-- =====================================================================
-- migration_micro_windows.sql
-- Captura de micro-ventana por vela (5 sub-buckets de 15s = 75s).
-- No destructivo: CREATE TABLE IF NOT EXISTS + CREATE OR REPLACE VIEW.
-- Ejecutar en el SQL Editor de Supabase. Luego redeploy del monitor.
-- =====================================================================

CREATE TABLE IF NOT EXISTS micro_windows (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- ---- identidad / join key con shadow_signals ----
    exchange        TEXT    NOT NULL,
    symbol          TEXT    NOT NULL,
    timeframe       TEXT    NOT NULL DEFAULT '5m',
    candle_open_ms  BIGINT  NOT NULL,
    candle_close_ms BIGINT  NOT NULL,

    -- ---- config de la ventana ----
    anchor          TEXT    NOT NULL,          -- 'candle_close' | 'candle_open' | 'trigger'
    window_start_ms BIGINT  NOT NULL,
    window_end_ms   BIGINT  NOT NULL,
    bucket_count    SMALLINT NOT NULL DEFAULT 5,
    bucket_secs     SMALLINT NOT NULL DEFAULT 15,

    -- ---- contexto (para filtrar el dataset completo hacia zona DRR) ----
    in_drr_zone     BOOLEAN,                   -- precio cerca de un extremo del rango
    range_location  TEXT,                      -- 'near_low' | 'near_high' | 'outside_low' | 'outside_high' | 'mid' | NULL
    range_high      DOUBLE PRECISION,
    range_low       DOUBLE PRECISION,
    range_mid       DOUBLE PRECISION,
    atr             DOUBLE PRECISION,          -- para normalizar todo lo que sea precio

    -- ---- agregados de toda la ventana ----
    win_vol_total       DOUBLE PRECISION,
    win_delta_total     DOUBLE PRECISION,      -- suma de (buy_agresor - sell_agresor)
    win_trades_total    INTEGER,
    win_cvd_net         DOUBLE PRECISION,      -- cambio de CVD sobre la ventana (= win_delta_total)
    win_liq_total       DOUBLE PRECISION,      -- liquidaciones USD en la ventana (crypto)
    win_big_vol         DOUBLE PRECISION,      -- volumen de big trades
    win_max_trade       DOUBLE PRECISION,      -- mayor trade individual
    aggressor_ratio     DOUBLE PRECISION,      -- buy_vol / vol_total  (0..1)

    -- ---- delta por bucket (flatten de los 2 mas usados para SQL rapido) ----
    delta_b0 DOUBLE PRECISION, delta_b1 DOUBLE PRECISION, delta_b2 DOUBLE PRECISION,
    delta_b3 DOUBLE PRECISION, delta_b4 DOUBLE PRECISION,
    vol_b0   DOUBLE PRECISION, vol_b1   DOUBLE PRECISION, vol_b2   DOUBLE PRECISION,
    vol_b3   DOUBLE PRECISION, vol_b4   DOUBLE PRECISION,

    -- ---- FEATURES DE FORMA (el oro para subir win rate) ----
    delta_slope         DOUBLE PRECISION,      -- regresion lineal de los deltas por bucket
    delta_slope_norm    DOUBLE PRECISION,      -- slope / stdev(delta buckets)
    delta_accel         DOUBLE PRECISION,      -- 2do tramo - 1er tramo (aceleracion)
    delta_flip          BOOLEAN,               -- cambio de signo de delta dentro de la ventana
    delta_flip_bucket   SMALLINT,              -- en que bucket ocurrio el flip (0..4)
    monotonic_delta     BOOLEAN,               -- todos los buckets mismo signo

    vol_peak_bucket     SMALLINT,              -- bucket con mas volumen (0..4)
    vol_trajectory      TEXT,                  -- 'front' | 'mid' | 'back' | 'flat' | 'u_shape'
    late_surge_ratio    DOUBLE PRECISION,      -- vol_b4 / media(vol buckets)  -- tu insight de los seg finales

    price_net           DOUBLE PRECISION,      -- close ventana - open ventana
    price_path_eff      DOUBLE PRECISION,      -- |price_net| / suma(|movimiento por bucket|)  (0..1)
    micro_range_atr     DOUBLE PRECISION,      -- (hi-lo de la ventana) / atr
    absorption_proxy    DOUBLE PRECISION,      -- win_vol_total / (|price_net|/atr + eps)

    -- ---- especifico DRR (solo cuando hay rango) ----
    reclaimed           BOOLEAN,               -- el precio volvio dentro del rango tras salir
    reclaim_bucket      SMALLINT,              -- en que bucket reclamo (0..4)
    sweep_depth_atr     DOUBLE PRECISION,      -- profundidad del barrido del extremo / atr

    -- ---- escape hatch: detalle completo por bucket ----
    buckets         JSONB                      -- array de 5 objetos con todos los campos crudos
);

-- indices para analisis y join
CREATE INDEX IF NOT EXISTS idx_mw_symbol_candle  ON micro_windows (symbol, candle_open_ms);
CREATE INDEX IF NOT EXISTS idx_mw_zone           ON micro_windows (in_drr_zone, range_location);
CREATE INDEX IF NOT EXISTS idx_mw_traj           ON micro_windows (vol_trajectory);
CREATE INDEX IF NOT EXISTS idx_mw_created        ON micro_windows (created_at);

-- =====================================================================
-- Vista: une la micro-ventana con la señal/resultado de la misma vela.
-- Asume que shadow_signals tiene: symbol, candle_open_ms (o timestamp_ms
-- mapeable a la vela), side, score, outcome/result_r.
-- Ajusta los nombres de columnas de shadow_signals a tu esquema real.
-- =====================================================================
CREATE OR REPLACE VIEW v_micro_with_outcomes AS
SELECT
    mw.*,
    ss.side,
    ss.score,
    ss.entry_type,
    ss.absorption_count,
    ss.session_name,
    ss.session_phase,
    ss.result_r,                         -- resultado en R (NULL si aun abierta / sin trade)
    CASE WHEN ss.result_r IS NULL THEN 'no_signal'
         WHEN ss.result_r > 0      THEN 'win'
         ELSE 'loss' END AS outcome_bucket,
    -- buckets de analisis derivados de la micro-ventana
    CASE
        WHEN mw.late_surge_ratio >= 1.6 THEN 'back_loaded'
        WHEN mw.late_surge_ratio <= 0.6 THEN 'front_loaded'
        ELSE 'even' END AS surge_bucket,
    CASE
        WHEN mw.delta_slope_norm >=  0.5 THEN 'accelerating_up'
        WHEN mw.delta_slope_norm <= -0.5 THEN 'accelerating_down'
        ELSE 'flat' END AS delta_shape_bucket
FROM micro_windows mw
LEFT JOIN shadow_signals ss
       ON ss.symbol = mw.symbol
      AND ss.candle_open_ms = mw.candle_open_ms;

-- =====================================================================
-- Ejemplos de preguntas de calibracion que ya puedes responder:
--
-- 1) ¿Las velas "back_loaded" (volumen concentrado en los ult. 15s)
--    en zona near_low tienen mejor win rate en longs?
--   SELECT surge_bucket, COUNT(*) FILTER (WHERE outcome_bucket='win')::float
--          / NULLIF(COUNT(*) FILTER (WHERE outcome_bucket IN ('win','loss')),0) AS wr
--   FROM v_micro_with_outcomes
--   WHERE side='long' AND range_location='near_low'
--   GROUP BY surge_bucket;
--
-- 2) ¿El delta acelerando a favor en la ventana predice el reclaim?
--   SELECT delta_shape_bucket, AVG(result_r) FROM v_micro_with_outcomes
--   WHERE reclaimed = true GROUP BY delta_shape_bucket;
-- =====================================================================
