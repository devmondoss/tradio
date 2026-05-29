-- =====================================================================
-- migration_micro_windows.sql
-- Captura de micro-ventana por vela (5 sub-buckets de 15s = 75s).
-- No destructivo: CREATE TABLE IF NOT EXISTS + CREATE OR REPLACE VIEW.
-- Ejecutar en el SQL Editor de Supabase. Luego redeploy del monitor.
-- =====================================================================

CREATE TABLE IF NOT EXISTS micro_windows (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- ---- identidad / join key ----
    exchange        TEXT    NOT NULL,
    symbol          TEXT    NOT NULL,
    timeframe       TEXT    NOT NULL DEFAULT '5m',
    candle_open_ms  BIGINT  NOT NULL,
    candle_close_ms BIGINT  NOT NULL,   -- shadow_signals.timestamp_ms = bar open time (= candle_open_ms)

    -- ---- config de la ventana ----
    anchor          TEXT    NOT NULL,   -- 'candle_close' | 'candle_open' | 'trigger'
    window_start_ms BIGINT  NOT NULL,
    window_end_ms   BIGINT  NOT NULL,
    bucket_count    SMALLINT NOT NULL DEFAULT 5,
    bucket_secs     SMALLINT NOT NULL DEFAULT 15,

    -- ---- contexto de zona DRR ----
    in_drr_zone     BOOLEAN,
    range_location  TEXT,               -- 'near_low' | 'near_high' | 'outside_low' | 'outside_high' | 'mid' | 'inside' | NULL
    range_high      DOUBLE PRECISION,
    range_low       DOUBLE PRECISION,
    range_mid       DOUBLE PRECISION,
    atr             DOUBLE PRECISION,

    -- ---- agregados de la ventana ----
    win_vol_total       DOUBLE PRECISION,
    win_delta_total     DOUBLE PRECISION,
    win_trades_total    INTEGER,
    win_cvd_net         DOUBLE PRECISION,
    win_liq_total       DOUBLE PRECISION,
    win_big_vol         DOUBLE PRECISION,
    win_max_trade       DOUBLE PRECISION,
    aggressor_ratio     DOUBLE PRECISION,

    -- ---- delta y vol por bucket (flat para SQL rápido) ----
    delta_b0 DOUBLE PRECISION, delta_b1 DOUBLE PRECISION, delta_b2 DOUBLE PRECISION,
    delta_b3 DOUBLE PRECISION, delta_b4 DOUBLE PRECISION,
    vol_b0   DOUBLE PRECISION, vol_b1   DOUBLE PRECISION, vol_b2   DOUBLE PRECISION,
    vol_b3   DOUBLE PRECISION, vol_b4   DOUBLE PRECISION,

    -- ---- FEATURES DE FORMA ----
    delta_slope         DOUBLE PRECISION,
    delta_slope_norm    DOUBLE PRECISION,
    delta_accel         DOUBLE PRECISION,
    delta_flip          BOOLEAN,
    delta_flip_bucket   SMALLINT,
    monotonic_delta     BOOLEAN,

    vol_peak_bucket     SMALLINT,
    vol_trajectory      TEXT,           -- 'front' | 'mid' | 'back' | 'flat' | 'u_shape'
    late_surge_ratio    DOUBLE PRECISION,

    price_net           DOUBLE PRECISION,
    price_path_eff      DOUBLE PRECISION,
    micro_range_atr     DOUBLE PRECISION,
    absorption_proxy    DOUBLE PRECISION,

    -- ---- específico DRR ----
    reclaimed           BOOLEAN,
    reclaim_bucket      SMALLINT,
    sweep_depth_atr     DOUBLE PRECISION,

    -- ---- detalle completo por bucket (escape hatch) ----
    buckets             JSONB
);

-- Índices
CREATE INDEX IF NOT EXISTS idx_mw_symbol_candle ON micro_windows (symbol, candle_open_ms);
CREATE INDEX IF NOT EXISTS idx_mw_zone          ON micro_windows (in_drr_zone, range_location);
CREATE INDEX IF NOT EXISTS idx_mw_traj          ON micro_windows (vol_trajectory);
CREATE INDEX IF NOT EXISTS idx_mw_created       ON micro_windows (created_at DESC);

-- =====================================================================
-- Vista: une micro-ventana con señal + outcome de la misma vela.
--
-- JOIN key:
--   shadow_signals.timestamp_ms  = la vela que cerró y disparó la señal
--   micro_windows.candle_close_ms = la misma vela
--   Ambos son el CIERRE de la vela M5.
--
-- result_r viene de signal_outcomes.r_multiple (no de shadow_signals).
-- LEFT JOIN doble: la señal puede estar abierta (sin outcome aún).
-- =====================================================================
-- shadow_signals NO tiene columna symbol — join solo por timestamp_ms.
-- Columnas entry_type/absorption_count/session_name/session_phase
-- solo existen después de correr migration_drr.sql.
CREATE OR REPLACE VIEW v_micro_with_outcomes AS
SELECT
    mw.*,
    ss.id            AS signal_id,
    ss.side,
    ss.score,
    ss.strategy,
    ss.entry_price,
    ss.stop_price,
    ss.target_price,
    ss.regime_combined,
    so.r_multiple    AS result_r,
    so.close_reason,
    so.pnl_net_usd,
    so.mfe_r,
    so.mae_r,
    so.duration_ms,
    CASE
        WHEN so.r_multiple IS NULL           THEN 'no_outcome_yet'
        WHEN so.close_reason = 'TP1_PARTIAL' THEN 'partial_win'
        WHEN so.r_multiple > 0               THEN 'win'
        ELSE 'loss'
    END AS outcome_bucket,
    CASE
        WHEN mw.late_surge_ratio >= 1.6 THEN 'back_loaded'
        WHEN mw.late_surge_ratio <= 0.6 THEN 'front_loaded'
        ELSE 'even'
    END AS surge_bucket,
    CASE
        WHEN mw.delta_slope_norm >=  0.5 THEN 'accelerating_up'
        WHEN mw.delta_slope_norm <= -0.5 THEN 'accelerating_down'
        ELSE 'flat'
    END AS delta_shape_bucket
FROM micro_windows mw
LEFT JOIN shadow_signals ss
       ON ss.timestamp_ms = mw.candle_open_ms
      AND ss.action       = 'ShadowSignal'
LEFT JOIN signal_outcomes so
       ON so.signal_id  = ss.id
      AND so.is_partial = FALSE;

-- =====================================================================
-- QUERY DE VERIFICACIÓN (ejecutar después de crear la vista):
--
-- Confirma que el join trae filas cuando hay señales en ese período.
-- Si result_r sigue NULL para señales cerradas = el join falla.
--
-- SELECT
--     mw.candle_close_ms,
--     mw.symbol,
--     mw.range_location,
--     mw.late_surge_ratio,
--     mw.absorption_proxy,
--     v.signal_id IS NOT NULL   AS has_signal,
--     v.result_r,
--     v.outcome_bucket,
--     v.close_reason
-- FROM v_micro_with_outcomes v
-- RIGHT JOIN micro_windows mw ON mw.id = v.id
-- ORDER BY mw.candle_close_ms DESC
-- LIMIT 20;
--
-- Filas esperadas:
--   has_signal = FALSE  → velas sin señal (mayoría, normal)
--   has_signal = TRUE   → velas con señal DRR
--   result_r NOT NULL   → señal ya cerrada con outcome
--   result_r NULL       → señal abierta o aún en TTL (normal al principio)
-- =====================================================================

-- =====================================================================
-- Nota sobre el modelo de datos:
-- shadow_signals.timestamp_ms → momento del cierre de la vela M5 que
--   disparó la señal (el router corre al cerrar cada barra).
-- micro_windows.candle_close_ms → exactamente lo mismo.
-- El join es directo y sin ambigüedad mientras el monitor escriba
-- ambos con el mismo timestamp de cierre de la vela.
-- =====================================================================
