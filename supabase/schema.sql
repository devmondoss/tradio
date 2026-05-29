-- ============================================================
-- FlowSurface — Schema de Supabase
-- Ejecutar en Supabase SQL Editor (en orden)
-- ============================================================

-- ============================================================
-- TABLA PRINCIPAL: señales generadas por los detectores
-- ============================================================
CREATE TABLE IF NOT EXISTS shadow_signals (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    timestamp_ms            BIGINT NOT NULL,

    strategy                TEXT NOT NULL,
    side                    TEXT,
    regime_slow             TEXT,
    regime_fast             TEXT,
    regime_combined         TEXT,
    action                  TEXT NOT NULL,

    entry_price             DOUBLE PRECISION,
    stop_price              DOUBLE PRECISION,
    target_price            DOUBLE PRECISION,
    score                   DOUBLE PRECISION,
    ttl_ms                  BIGINT,

    block_reason            TEXT,
    reject_reason           TEXT,

    evidence                TEXT[],
    missing                 TEXT[],

    -- Contexto técnico
    price                   DOUBLE PRECISION NOT NULL,
    vwap_session            DOUBLE PRECISION,
    poc                     DOUBLE PRECISION,
    vah                     DOUBLE PRECISION,
    val                     DOUBLE PRECISION,
    cvd_slope               DOUBLE PRECISION,
    cvd_absolute            DOUBLE PRECISION,
    atr                     DOUBLE PRECISION,
    spread_bps              DOUBLE PRECISION,
    obi_l5                  DOUBLE PRECISION,
    microprice              DOUBLE PRECISION,

    -- Contexto institucional — Liquidaciones
    short_liq_usd_5m        DOUBLE PRECISION,
    long_liq_usd_5m         DOUBLE PRECISION,
    total_liq_usd_5m        DOUBLE PRECISION,
    cascade_active          BOOLEAN,
    liq_dominant_side       TEXT,

    -- Long/Short ratios
    top_traders_long_pct    DOUBLE PRECISION,
    retail_long_pct         DOUBLE PRECISION,
    ls_divergence           DOUBLE PRECISION,
    divergence_signal       TEXT,

    -- Open Interest
    oi_current_btc          DOUBLE PRECISION,
    oi_change_30m_pct       DOUBLE PRECISION,
    oi_change_2h_pct        DOUBLE PRECISION,
    oi_trend                TEXT,

    -- Funding
    funding_current         DOUBLE PRECISION,
    funding_regime          TEXT,
    funding_percentile_30d  DOUBLE PRECISION,
    funding_avg_7d          DOUBLE PRECISION,

    -- Taker
    taker_buy_sell_ratio    DOUBLE PRECISION,
    taker_imbalance         DOUBLE PRECISION,

    -- Paper trader config
    leverage                DOUBLE PRECISION,

    -- Subdimi methodology context (columnas individuales para análisis estadístico)
    finish_action           BOOLEAN,          -- exhaustión alineada con dirección de señal
    unfinish_action         BOOLEAN,          -- imán adverso opuesto a la señal (penaliza)
    big_trade               BOOLEAN,          -- orden institucional confirma dirección
    stacked_imbalance       TEXT,             -- 'Bullish' | 'Bearish' | 'None'
    vp_open_bias            TEXT,             -- 'InsideValue' | 'TrendDay' | 'OutsideVaInsidePa' | 'FadeGap' | 'Unknown'
    auction_state           TEXT,             -- 'Balance' | 'UpImbalance' | 'DownImbalance' | 'Accumulation' | 'Distribution' | 'Unknown'
    htf_weekly_location     TEXT,             -- 'AboveVah' | 'BelowVal' | 'InValue' | 'Unknown'
    htf_monthly_location    TEXT,             -- idem
    delta_velocity          DOUBLE PRECISION, -- OLS slope delta últimas 5 barras / ATR

    -- Playbook reasoning Fase 1 (observador, no gate)
    reasoning_version       TEXT,
    primary_playbook        TEXT,
    secondary_playbooks     JSONB,
    reasoning_tags          JSONB,
    reasoning_confidence    DOUBLE PRECISION,
    reasoning_completeness  DOUBLE PRECISION,

    -- DRR — contexto del rango intradía (price-action, no VP-based)
    range_high              DOUBLE PRECISION,
    range_low               DOUBLE PRECISION,
    range_mid               DOUBLE PRECISION,
    range_poc               DOUBLE PRECISION,
    range_size_atr          DOUBLE PRECISION,
    range_location          TEXT,
    range_touches_high      INTEGER,
    range_touches_low       INTEGER,
    range_sweep_low         BOOLEAN,
    range_sweep_high        BOOLEAN,

    -- Bloque 1: Tiempo y sesión
    session_name                TEXT,
    session_phase               TEXT,
    hour_utc                    SMALLINT,
    day_of_week                 SMALLINT,
    minutes_since_session_open  SMALLINT,

    -- Bloque 2: Calidad del rango
    range_midline_slope  DOUBLE PRECISION,
    range_bars_inside    INTEGER,
    range_second_test    BOOLEAN,
    range_vs_value_area  TEXT,

    -- Bloque 3: Calidad de absorción
    absorption_count   SMALLINT,
    entry_type         TEXT,
    sweep_depth_atr    DOUBLE PRECISION,
    delta_at_extreme   DOUBLE PRECISION,
    bar_volume         DOUBLE PRECISION,

    -- Bloque 4: Contexto de precio y estructura
    value_location             TEXT,
    price_vs_vwap              TEXT,
    price_vs_avwap_bos         TEXT,
    naked_poc_in_target_path   BOOLEAN,
    hvn_between_entry_target   BOOLEAN,
    fast_slope_at_entry        DOUBLE PRECISION,

    -- Bloque 5: Institucional compacto
    oi_direction               TEXT,
    cvd_divergence_persistence SMALLINT,
    vpin                       DOUBLE PRECISION,
    funding_velocity           DOUBLE PRECISION,

    -- Bloque 6: Calidad del trade
    rr_actual                  DOUBLE PRECISION,
    distance_to_target_atr     DOUBLE PRECISION,
    distance_to_stop_atr       DOUBLE PRECISION,
    obstacle_hvn_count         SMALLINT,
    nearest_naked_poc_dist_atr DOUBLE PRECISION,

    -- Contexto Subdimi adicional (blob)
    subdomi_ctx             JSONB
);

-- ============================================================
-- TABLA DE OUTCOMES
-- ============================================================
CREATE TABLE IF NOT EXISTS signal_outcomes (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id           UUID NOT NULL REFERENCES shadow_signals(id) ON DELETE CASCADE,
    resolved_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    timestamp_ms        BIGINT NOT NULL,

    close_reason        TEXT NOT NULL,
    close_price         DOUBLE PRECISION NOT NULL,
    duration_ms         BIGINT NOT NULL,
    r_multiple          DOUBLE PRECISION NOT NULL,

    pnl_gross_usd       DOUBLE PRECISION,
    pnl_net_usd         DOUBLE PRECISION,
    fee_entry_usd       DOUBLE PRECISION,
    fee_exit_usd        DOUBLE PRECISION,
    slippage_usd        DOUBLE PRECISION,
    funding_cost_usd    DOUBLE PRECISION,

    mfe_r               DOUBLE PRECISION,
    mae_r               DOUBLE PRECISION,

    -- Partial exit: TP1 (50% at 1.5R) generates is_partial=TRUE; final leg is_partial=FALSE.
    -- Both rows share the same signal_id. partial_fraction=0.5 for TP1, 0.0 for full close.
    is_partial          BOOLEAN NOT NULL DEFAULT FALSE,
    partial_fraction    DOUBLE PRECISION NOT NULL DEFAULT 0.0,

    price_5m            DOUBLE PRECISION,
    price_15m           DOUBLE PRECISION,
    price_30m           DOUBLE PRECISION,
    price_1h            DOUBLE PRECISION,
    price_4h            DOUBLE PRECISION,

    r_5m                DOUBLE PRECISION,
    r_15m               DOUBLE PRECISION,
    r_30m               DOUBLE PRECISION,
    r_1h                DOUBLE PRECISION,
    r_4h                DOUBLE PRECISION
);

-- ============================================================
-- TABLA DE SNAPSHOTS INSTITUCIONALES (cada 5 minutos)
-- ============================================================
CREATE TABLE IF NOT EXISTS institutional_snapshots (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    timestamp_ms            BIGINT NOT NULL,
    price                   DOUBLE PRECISION NOT NULL,

    short_liq_usd_5m        DOUBLE PRECISION,
    long_liq_usd_5m         DOUBLE PRECISION,
    cascade_active          BOOLEAN,

    top_traders_long_pct    DOUBLE PRECISION,
    retail_long_pct         DOUBLE PRECISION,
    ls_divergence           DOUBLE PRECISION,
    divergence_signal       TEXT,

    oi_btc                  DOUBLE PRECISION,
    oi_change_30m_pct       DOUBLE PRECISION,
    oi_trend                TEXT,

    funding_current         DOUBLE PRECISION,
    funding_regime          TEXT,
    funding_percentile      DOUBLE PRECISION,

    taker_ratio             DOUBLE PRECISION,
    taker_imbalance         DOUBLE PRECISION,

    regime_slow             TEXT,
    regime_fast             TEXT
);

-- ============================================================
-- TABLA DE HISTORIAL DE RÉGIMEN
-- ============================================================
CREATE TABLE IF NOT EXISTS regime_history (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    timestamp_ms    BIGINT NOT NULL,
    regime_slow     TEXT NOT NULL,
    regime_fast     TEXT NOT NULL,
    regime_combined TEXT NOT NULL,
    duration_ms     BIGINT,
    price_at_change DOUBLE PRECISION
);

-- ============================================================
-- TABLAS DEL STRATEGY LAB
-- ============================================================
CREATE TABLE IF NOT EXISTS lab_signals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    strategy_id     TEXT NOT NULL,
    status          TEXT NOT NULL,
    maturity        TEXT NOT NULL,
    timestamp_ms    BIGINT NOT NULL,
    action          TEXT,
    side            TEXT,
    entry_price     DOUBLE PRECISION,
    target          DOUBLE PRECISION,
    stop            DOUBLE PRECISION,
    rr              DOUBLE PRECISION,
    confidence      DOUBLE PRECISION,
    missing_data    TEXT[],
    block_reason    TEXT,
    snapshot        JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS lab_outcomes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    signal_id       UUID REFERENCES lab_signals(id) ON DELETE CASCADE,
    strategy_id     TEXT NOT NULL,
    entry_price     DOUBLE PRECISION NOT NULL,
    target          DOUBLE PRECISION NOT NULL,
    stop            DOUBLE PRECISION NOT NULL,
    side            TEXT NOT NULL,
    outcome_30s     JSONB,
    outcome_1m      JSONB,
    outcome_3m      JSONB,
    outcome_5m      JSONB,
    outcome_15m     JSONB,
    outcome_ttl     JSONB,
    mfe             DOUBLE PRECISION,
    mae             DOUBLE PRECISION,
    final_status    TEXT
);

-- ============================================================
-- TABLA DE CALIBRACIONES
-- ============================================================
CREATE TABLE IF NOT EXISTS calibration_log (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    calibrated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    trigger_reason      TEXT NOT NULL,
    regime              TEXT NOT NULL,
    strategy            TEXT,

    params              JSONB NOT NULL,

    train_expectancy    DOUBLE PRECISION,
    test_expectancy     DOUBLE PRECISION,
    overfit_gap         DOUBLE PRECISION,
    n_train_trades      INTEGER,
    n_test_trades       INTEGER,

    deflated_sharpe     DOUBLE PRECISION,
    edge_is_real        BOOLEAN,

    approved_for_deploy BOOLEAN NOT NULL DEFAULT FALSE,
    deploy_reason       TEXT,
    deployed_at         TIMESTAMPTZ
);

-- ============================================================
-- TABLA DE PARÁMETROS DESPLEGADOS
-- ============================================================
CREATE TABLE IF NOT EXISTS deployed_params (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    regime          TEXT NOT NULL,
    strategy        TEXT,
    params          JSONB NOT NULL,
    calibration_id  UUID REFERENCES calibration_log(id),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,

    UNIQUE(regime, strategy, is_active)
);

-- ============================================================
-- ÍNDICES
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_signals_strategy_time
    ON shadow_signals(strategy, timestamp_ms DESC);

CREATE INDEX IF NOT EXISTS idx_signals_action_time
    ON shadow_signals(action, timestamp_ms DESC);

CREATE INDEX IF NOT EXISTS idx_signals_regime
    ON shadow_signals(regime_combined, strategy);

CREATE INDEX IF NOT EXISTS idx_signals_institutional
    ON shadow_signals(short_liq_usd_5m, funding_percentile_30d, ls_divergence);

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

CREATE INDEX IF NOT EXISTS idx_outcomes_signal
    ON signal_outcomes(signal_id);

CREATE INDEX IF NOT EXISTS idx_outcomes_reason
    ON signal_outcomes(close_reason, r_multiple);

CREATE INDEX IF NOT EXISTS idx_snapshots_time
    ON institutional_snapshots(timestamp_ms DESC);

CREATE INDEX IF NOT EXISTS idx_regime_history_time
    ON regime_history(timestamp_ms DESC);

CREATE INDEX IF NOT EXISTS idx_calibration_regime
    ON calibration_log(regime, calibrated_at DESC);

-- Supports: WHERE approved_for_deploy = TRUE AND regime = ? AND calibrated_at > NOW() - interval
-- Used by MongoConfigLoader to fetch active deployed params per regime without full table scan.
CREATE INDEX IF NOT EXISTS idx_calibration_deploy_active
    ON calibration_log(approved_for_deploy, regime, calibrated_at DESC)
    WHERE approved_for_deploy = TRUE;

CREATE INDEX IF NOT EXISTS idx_lab_signals_strategy
    ON lab_signals(strategy_id, timestamp_ms DESC);

CREATE INDEX IF NOT EXISTS idx_lab_signals_status
    ON lab_signals(status, strategy_id);

CREATE INDEX IF NOT EXISTS idx_lab_outcomes_signal
    ON lab_outcomes(signal_id);

CREATE INDEX IF NOT EXISTS idx_lab_outcomes_strategy
    ON lab_outcomes(strategy_id, final_status);

-- ============================================================
-- VISTA ANALÍTICA PRINCIPAL
-- ============================================================
CREATE OR REPLACE VIEW v_signals_with_outcomes AS
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

    -- Institucional
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

    -- Outcome
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

    CASE WHEN o.close_reason = 'TARGET_HIT'  THEN TRUE ELSE FALSE END AS hit_target,
    CASE WHEN o.close_reason = 'STOP_HIT'    THEN TRUE ELSE FALSE END AS hit_stop,
    CASE WHEN o.close_reason = 'TTL_EXPIRED' THEN TRUE ELSE FALSE END AS expired,
    CASE WHEN o.close_reason = 'INVALIDATED' THEN TRUE ELSE FALSE END AS invalidated,
    CASE WHEN o.close_reason = 'TP1_PARTIAL' THEN TRUE ELSE FALSE END AS tp1_partial,

    -- Buckets de segmentación
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

-- ============================================================
-- MIGRATION: añadir columnas de partial exit a bases existentes
-- Ejecutar una sola vez en el SQL Editor de Supabase si la tabla ya existe.
-- En instalaciones nuevas el CREATE TABLE ya las incluye.
-- ============================================================
-- ALTER TABLE signal_outcomes
--     ADD COLUMN IF NOT EXISTS is_partial       BOOLEAN          NOT NULL DEFAULT FALSE,
--     ADD COLUMN IF NOT EXISTS partial_fraction DOUBLE PRECISION NOT NULL DEFAULT 0.0;

-- ============================================================
-- MIGRATION: columnas Subdimi methodology
-- Ejecutar una sola vez si la tabla shadow_signals ya existe.
-- En instalaciones nuevas el CREATE TABLE ya las incluye.
-- ============================================================
-- ALTER TABLE shadow_signals
--     ADD COLUMN IF NOT EXISTS finish_action        BOOLEAN,
--     ADD COLUMN IF NOT EXISTS unfinish_action      BOOLEAN,
--     ADD COLUMN IF NOT EXISTS big_trade            BOOLEAN,
--     ADD COLUMN IF NOT EXISTS stacked_imbalance    TEXT,
--     ADD COLUMN IF NOT EXISTS vp_open_bias         TEXT,
--     ADD COLUMN IF NOT EXISTS auction_state        TEXT,
--     ADD COLUMN IF NOT EXISTS htf_weekly_location  TEXT,
--     ADD COLUMN IF NOT EXISTS htf_monthly_location TEXT,
--     ADD COLUMN IF NOT EXISTS delta_velocity       DOUBLE PRECISION,
--     ADD COLUMN IF NOT EXISTS subdomi_ctx          JSONB;

-- ============================================================
-- MIGRATION: columnas DRR (DeltaRangeReversal)
-- Ejecutar UNA VEZ en el SQL Editor de Supabase si la tabla ya existe.
-- En instalaciones nuevas el CREATE TABLE ya las incluye.
-- ============================================================
-- ALTER TABLE shadow_signals
--     ADD COLUMN IF NOT EXISTS range_high         DOUBLE PRECISION,
--     ADD COLUMN IF NOT EXISTS range_low          DOUBLE PRECISION,
--     ADD COLUMN IF NOT EXISTS range_mid          DOUBLE PRECISION,
--     ADD COLUMN IF NOT EXISTS range_poc          DOUBLE PRECISION,
--     ADD COLUMN IF NOT EXISTS range_size_atr     DOUBLE PRECISION,
--     ADD COLUMN IF NOT EXISTS range_location     TEXT,
--     ADD COLUMN IF NOT EXISTS range_touches_high INTEGER,
--     ADD COLUMN IF NOT EXISTS range_touches_low  INTEGER,
--     ADD COLUMN IF NOT EXISTS range_sweep_low    BOOLEAN,
--     ADD COLUMN IF NOT EXISTS range_sweep_high   BOOLEAN;

-- MIGRATION: columnas Playbook Reasoning (sesión 2026-05-24)
-- Si se aplica esta migración, también recrear la vista v_signals_with_outcomes.
-- ============================================================
-- ALTER TABLE shadow_signals
--     ADD COLUMN IF NOT EXISTS reasoning_version      TEXT,
--     ADD COLUMN IF NOT EXISTS primary_playbook       TEXT,
--     ADD COLUMN IF NOT EXISTS secondary_playbooks    JSONB,
--     ADD COLUMN IF NOT EXISTS reasoning_tags         JSONB,
--     ADD COLUMN IF NOT EXISTS reasoning_confidence   DOUBLE PRECISION,
--     ADD COLUMN IF NOT EXISTS reasoning_completeness DOUBLE PRECISION;

-- ============================================================
-- TTL AUTOMÁTICO: borrar snapshots > 90 días
-- Habilitar pg_cron en Supabase Extensions primero
-- ============================================================
-- SELECT cron.schedule(
--     'cleanup-old-snapshots',
--     '0 3 * * *',
--     $$
--     DELETE FROM institutional_snapshots
--     WHERE created_at < NOW() - INTERVAL '90 days';
--     $$
-- );
