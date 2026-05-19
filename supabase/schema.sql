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
    leverage                DOUBLE PRECISION
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

    s.short_liq_usd_5m,
    s.long_liq_usd_5m,
    s.cascade_active,
    s.top_traders_long_pct,
    s.retail_long_pct,
    s.ls_divergence,
    s.divergence_signal,
    s.oi_change_30m_pct,
    s.oi_trend,
    s.funding_current,
    s.funding_regime,
    s.funding_percentile_30d,
    s.taker_imbalance,
    s.cvd_slope,

    o.close_reason,
    o.r_multiple,
    o.pnl_net_usd,
    o.duration_ms,
    o.mfe_r,
    o.mae_r,
    o.r_5m,
    o.r_15m,
    o.r_30m,
    o.r_1h,
    o.is_partial,
    o.partial_fraction,

    CASE WHEN o.close_reason = 'TARGET_HIT'   THEN TRUE ELSE FALSE END AS hit_target,
    CASE WHEN o.close_reason = 'STOP_HIT'     THEN TRUE ELSE FALSE END AS hit_stop,
    CASE WHEN o.close_reason = 'TTL_EXPIRED'  THEN TRUE ELSE FALSE END AS expired,
    CASE WHEN o.close_reason = 'INVALIDATED'  THEN TRUE ELSE FALSE END AS invalidated,
    CASE WHEN o.close_reason = 'TP1_PARTIAL'  THEN TRUE ELSE FALSE END AS tp1_partial,

    CASE
        WHEN s.short_liq_usd_5m > 3000000 THEN '>$3M'
        WHEN s.short_liq_usd_5m > 2000000 THEN '$2M-$3M'
        WHEN s.short_liq_usd_5m > 1000000 THEN '$1M-$2M'
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
