-- ============================================================
-- FlowSurface — Migración DRR completa
-- Ejecutar en Supabase SQL Editor UNA VEZ sobre la DB existente.
-- No borra datos. Agrega columnas y recrea la vista.
-- Fecha: 2026-05-29
-- ============================================================

-- ============================================================
-- BLOQUE DRR (ya estaban, completar si faltan)
-- ============================================================
ALTER TABLE shadow_signals
    ADD COLUMN IF NOT EXISTS range_high         DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS range_low          DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS range_mid          DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS range_poc          DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS range_size_atr     DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS range_location     TEXT,
    ADD COLUMN IF NOT EXISTS range_touches_high INTEGER,
    ADD COLUMN IF NOT EXISTS range_touches_low  INTEGER,
    ADD COLUMN IF NOT EXISTS range_sweep_low    BOOLEAN,
    ADD COLUMN IF NOT EXISTS range_sweep_high   BOOLEAN;

-- ============================================================
-- BLOQUE 1: Tiempo y sesión
-- ============================================================
ALTER TABLE shadow_signals
    ADD COLUMN IF NOT EXISTS session_name                TEXT,
    ADD COLUMN IF NOT EXISTS session_phase               TEXT,
    ADD COLUMN IF NOT EXISTS hour_utc                    SMALLINT,
    ADD COLUMN IF NOT EXISTS day_of_week                 SMALLINT,
    ADD COLUMN IF NOT EXISTS minutes_since_session_open  SMALLINT;

-- ============================================================
-- BLOQUE 2: Calidad del rango
-- ============================================================
ALTER TABLE shadow_signals
    ADD COLUMN IF NOT EXISTS range_midline_slope  DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS range_bars_inside    INTEGER,
    ADD COLUMN IF NOT EXISTS range_second_test    BOOLEAN,
    ADD COLUMN IF NOT EXISTS range_vs_value_area  TEXT;

-- ============================================================
-- BLOQUE 3: Calidad de absorción
-- ============================================================
ALTER TABLE shadow_signals
    ADD COLUMN IF NOT EXISTS absorption_count   SMALLINT,
    ADD COLUMN IF NOT EXISTS entry_type         TEXT,
    ADD COLUMN IF NOT EXISTS sweep_depth_atr    DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS delta_at_extreme   DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS bar_volume         DOUBLE PRECISION;

-- ============================================================
-- BLOQUE 4: Contexto de precio y estructura
-- ============================================================
ALTER TABLE shadow_signals
    ADD COLUMN IF NOT EXISTS value_location             TEXT,
    ADD COLUMN IF NOT EXISTS price_vs_vwap              TEXT,
    ADD COLUMN IF NOT EXISTS price_vs_avwap_bos         TEXT,
    ADD COLUMN IF NOT EXISTS naked_poc_in_target_path   BOOLEAN,
    ADD COLUMN IF NOT EXISTS hvn_between_entry_target   BOOLEAN,
    ADD COLUMN IF NOT EXISTS fast_slope_at_entry        DOUBLE PRECISION;

-- ============================================================
-- BLOQUE 5: Contexto institucional compacto
-- ============================================================
ALTER TABLE shadow_signals
    ADD COLUMN IF NOT EXISTS oi_direction               TEXT,
    ADD COLUMN IF NOT EXISTS cvd_divergence_persistence SMALLINT,
    ADD COLUMN IF NOT EXISTS vpin                       DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS funding_velocity           DOUBLE PRECISION;

-- ============================================================
-- BLOQUE 6: Calidad del trade
-- ============================================================
ALTER TABLE shadow_signals
    ADD COLUMN IF NOT EXISTS rr_actual                  DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS distance_to_target_atr     DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS distance_to_stop_atr       DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS obstacle_hvn_count         SMALLINT,
    ADD COLUMN IF NOT EXISTS nearest_naked_poc_dist_atr DOUBLE PRECISION;

-- ============================================================
-- Columnas Playbook Reasoning (sesión 2026-05-24)
-- ============================================================
ALTER TABLE shadow_signals
    ADD COLUMN IF NOT EXISTS reasoning_version      TEXT,
    ADD COLUMN IF NOT EXISTS primary_playbook       TEXT,
    ADD COLUMN IF NOT EXISTS secondary_playbooks    JSONB,
    ADD COLUMN IF NOT EXISTS reasoning_tags         JSONB,
    ADD COLUMN IF NOT EXISTS reasoning_confidence   DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS reasoning_completeness DOUBLE PRECISION;

-- ============================================================
-- Índices nuevos para análisis de patrones DRR
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_signals_playbook
    ON shadow_signals(primary_playbook, reasoning_confidence, timestamp_ms DESC);

CREATE INDEX IF NOT EXISTS idx_signals_range_location
    ON shadow_signals(range_location, strategy, timestamp_ms DESC);

CREATE INDEX IF NOT EXISTS idx_signals_session
    ON shadow_signals(session_name, session_phase, strategy, timestamp_ms DESC);

CREATE INDEX IF NOT EXISTS idx_signals_absorption
    ON shadow_signals(absorption_count, entry_type, strategy);

CREATE INDEX IF NOT EXISTS idx_signals_dow_hour
    ON shadow_signals(day_of_week, hour_utc, strategy);

-- ============================================================
-- Recrear la vista con TODAS las columnas nuevas
-- ============================================================
DROP VIEW IF EXISTS v_signals_with_outcomes;

CREATE VIEW v_signals_with_outcomes AS
SELECT
    s.id,
    s.timestamp_ms,
    TO_TIMESTAMP(s.timestamp_ms / 1000.0) AS signal_time,
    s.strategy,
    s.side,
    s.regime_combined,
    s.regime_slow,
    s.regime_fast,
    s.score,
    s.entry_price,
    s.stop_price,
    s.target_price,
    s.atr,
    s.spread_bps,

    -- Bloque 1: Tiempo / sesión
    s.session_name,
    s.session_phase,
    s.hour_utc,
    s.day_of_week,
    s.minutes_since_session_open,

    -- Bloque 2: Calidad del rango
    s.range_high,
    s.range_low,
    s.range_mid,
    s.range_poc,
    s.range_size_atr,
    s.range_location,
    s.range_touches_high,
    s.range_touches_low,
    s.range_sweep_low,
    s.range_sweep_high,
    s.range_midline_slope,
    s.range_bars_inside,
    s.range_second_test,
    s.range_vs_value_area,

    -- Bloque 3: Absorción / trigger
    s.absorption_count,
    s.entry_type,
    s.sweep_depth_atr,
    s.delta_at_extreme,
    s.bar_volume,

    -- Bloque 4: Contexto de precio
    s.value_location,
    s.price_vs_vwap,
    s.price_vs_avwap_bos,
    s.naked_poc_in_target_path,
    s.hvn_between_entry_target,
    s.fast_slope_at_entry,

    -- Bloque 5: Institucional compacto
    s.oi_direction,
    s.cvd_divergence_persistence,
    s.vpin,
    s.funding_velocity,

    -- Bloque 6: Calidad del trade
    s.rr_actual,
    s.distance_to_target_atr,
    s.distance_to_stop_atr,
    s.obstacle_hvn_count,
    s.nearest_naked_poc_dist_atr,

    -- Contexto Subdimi base
    s.cvd_slope,
    s.delta_velocity,
    s.finish_action,
    s.unfinish_action,
    s.big_trade,
    s.stacked_imbalance,
    s.vp_open_bias,
    s.auction_state,
    s.htf_weekly_location,
    s.htf_monthly_location,
    s.reasoning_version,
    s.primary_playbook,
    s.reasoning_confidence,
    s.reasoning_completeness,

    -- Institucional completo
    s.short_liq_usd_5m,
    s.long_liq_usd_5m,
    s.cascade_active,
    s.top_traders_long_pct,
    s.retail_long_pct,
    s.ls_divergence,
    s.oi_change_30m_pct,
    s.oi_trend,
    s.funding_current,
    s.funding_regime,
    s.taker_imbalance,

    -- Outcome del trade
    o.close_reason,
    o.r_multiple,
    o.pnl_net_usd,
    o.duration_ms,
    o.mfe_r,
    o.mae_r,
    o.is_partial,
    o.partial_fraction,
    o.r_5m,
    o.r_15m,
    o.r_30m,
    o.r_1h,

    -- Booleans de resultado
    CASE WHEN o.close_reason = 'TARGET_HIT'  THEN TRUE ELSE FALSE END AS hit_target,
    CASE WHEN o.close_reason = 'STOP_HIT'    THEN TRUE ELSE FALSE END AS hit_stop,
    CASE WHEN o.close_reason = 'TTL_EXPIRED' THEN TRUE ELSE FALSE END AS expired,
    CASE WHEN o.close_reason = 'INVALIDATED' THEN TRUE ELSE FALSE END AS invalidated,
    CASE WHEN o.close_reason = 'TP1_PARTIAL' THEN TRUE ELSE FALSE END AS tp1_partial,

    -- Buckets de segmentación para análisis de patrones
    CASE
        WHEN s.range_size_atr < 1.5 THEN 'tight'
        WHEN s.range_size_atr < 2.5 THEN 'normal'
        ELSE 'wide'
    END AS range_size_bucket,

    CASE
        WHEN s.absorption_count >= 4 THEN 'strong'
        WHEN s.absorption_count >= 2 THEN 'medium'
        WHEN s.absorption_count = 1  THEN 'weak'
        ELSE 'none'
    END AS absorption_quality,

    CASE
        WHEN s.range_midline_slope IS NOT NULL AND ABS(s.range_midline_slope) < 0.03 THEN 'flat'
        WHEN s.range_midline_slope IS NOT NULL AND ABS(s.range_midline_slope) < 0.08 THEN 'slight_drift'
        ELSE 'trending'
    END AS range_quality,

    CASE
        WHEN s.rr_actual >= 2.5 THEN 'high_rr'
        WHEN s.rr_actual >= 1.5 THEN 'normal_rr'
        ELSE 'low_rr'
    END AS rr_bucket,

    CASE
        WHEN s.short_liq_usd_5m > 1000000 THEN '>$1M'
        WHEN s.short_liq_usd_5m > 500000  THEN '$500K-$1M'
        ELSE '<$500K'
    END AS liq_bucket,

    CASE
        WHEN s.funding_percentile_30d > 85 THEN 'extreme'
        WHEN s.funding_percentile_30d > 65 THEN 'elevated'
        WHEN s.funding_percentile_30d > 35 THEN 'neutral'
        ELSE 'depressed'
    END AS funding_bucket

FROM shadow_signals s
LEFT JOIN signal_outcomes o ON s.id = o.signal_id
WHERE s.action = 'ShadowSignal';
