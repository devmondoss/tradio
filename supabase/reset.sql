-- ============================================================
-- FlowSurface — Reset completo de la base de datos
-- Ejecutar en Supabase SQL Editor cuando Railway redeploya
-- y los datos viejos ya no son válidos.
--
-- ADVERTENCIA: borra TODOS los datos. No hay vuelta atrás.
-- ============================================================

-- 1. Vista analítica
DROP VIEW IF EXISTS v_signals_with_outcomes;

-- 2. Tablas con FK primero (hijos antes que padres)
DROP TABLE IF EXISTS signal_outcomes;
DROP TABLE IF EXISTS intrabar_outcomes;
DROP TABLE IF EXISTS deployed_params;
DROP TABLE IF EXISTS calibration_log;
DROP TABLE IF EXISTS shadow_signals;
DROP TABLE IF EXISTS institutional_snapshots;
DROP TABLE IF EXISTS regime_history;

-- ============================================================
-- RECREAR TABLAS
-- ============================================================

CREATE TABLE shadow_signals (
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
    short_liq_usd_5m        DOUBLE PRECISION,
    long_liq_usd_5m         DOUBLE PRECISION,
    total_liq_usd_5m        DOUBLE PRECISION,
    cascade_active          BOOLEAN,
    liq_dominant_side       TEXT,
    top_traders_long_pct    DOUBLE PRECISION,
    retail_long_pct         DOUBLE PRECISION,
    ls_divergence           DOUBLE PRECISION,
    divergence_signal       TEXT,
    oi_current_btc          DOUBLE PRECISION,
    oi_change_30m_pct       DOUBLE PRECISION,
    oi_change_2h_pct        DOUBLE PRECISION,
    oi_trend                TEXT,
    funding_current         DOUBLE PRECISION,
    funding_regime          TEXT,
    funding_percentile_30d  DOUBLE PRECISION,
    funding_avg_7d          DOUBLE PRECISION,
    taker_buy_sell_ratio    DOUBLE PRECISION,
    taker_imbalance         DOUBLE PRECISION,
    -- Campos adicionales del writer actual
    hvn_levels_above        DOUBLE PRECISION[],
    hvn_levels_below        DOUBLE PRECISION[],
    nearest_wall_above      DOUBLE PRECISION,
    nearest_wall_below      DOUBLE PRECISION,
    swing_high_20           DOUBLE PRECISION,
    swing_low_20            DOUBLE PRECISION
);

CREATE TABLE signal_outcomes (
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

-- Outcomes intrabar del outcome tracker (write_outcome en supabase_writer.rs)
CREATE TABLE intrabar_outcomes (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    signal_ms   BIGINT NOT NULL,
    source      TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    side        TEXT NOT NULL,
    entry_px    DOUBLE PRECISION NOT NULL,
    stop_px     DOUBLE PRECISION NOT NULL,
    target_px   DOUBLE PRECISION NOT NULL,
    risk        DOUBLE PRECISION,
    mfe         DOUBLE PRECISION,
    mae         DOUBLE PRECISION,
    rr_at_1m    DOUBLE PRECISION,
    rr_at_3m    DOUBLE PRECISION,
    rr_at_5m    DOUBLE PRECISION,
    hit_target  BOOLEAN,
    hit_stop    BOOLEAN,
    age_ms      BIGINT
);

CREATE TABLE institutional_snapshots (
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

CREATE TABLE regime_history (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    timestamp_ms    BIGINT NOT NULL,
    regime_slow     TEXT NOT NULL,
    regime_fast     TEXT NOT NULL,
    regime_combined TEXT NOT NULL,
    duration_ms     BIGINT,
    price_at_change DOUBLE PRECISION
);

CREATE TABLE calibration_log (
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

CREATE TABLE deployed_params (
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

CREATE INDEX idx_signals_strategy_time   ON shadow_signals(strategy, timestamp_ms DESC);
CREATE INDEX idx_signals_action_time     ON shadow_signals(action, timestamp_ms DESC);
CREATE INDEX idx_signals_regime          ON shadow_signals(regime_combined, strategy);
CREATE INDEX idx_signals_institutional   ON shadow_signals(short_liq_usd_5m, funding_percentile_30d, ls_divergence);
CREATE INDEX idx_outcomes_signal         ON signal_outcomes(signal_id);
CREATE INDEX idx_outcomes_reason         ON signal_outcomes(close_reason, r_multiple);
CREATE INDEX idx_intrabar_strategy_time  ON intrabar_outcomes(strategy_id, signal_ms DESC);
CREATE INDEX idx_intrabar_source         ON intrabar_outcomes(source, signal_ms DESC);
CREATE INDEX idx_snapshots_time          ON institutional_snapshots(timestamp_ms DESC);
CREATE INDEX idx_regime_history_time     ON regime_history(timestamp_ms DESC);
CREATE INDEX idx_calibration_regime      ON calibration_log(regime, calibrated_at DESC);

-- ============================================================
-- VISTA ANALÍTICA
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
    CASE WHEN o.close_reason = 'TARGET'      THEN TRUE ELSE FALSE END AS hit_target,
    CASE WHEN o.close_reason = 'STOP'        THEN TRUE ELSE FALSE END AS hit_stop,
    CASE WHEN o.close_reason = 'EXPIRED'     THEN TRUE ELSE FALSE END AS expired,
    CASE WHEN o.close_reason = 'INVALIDATED' THEN TRUE ELSE FALSE END AS invalidated,
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
