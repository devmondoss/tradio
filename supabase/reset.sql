-- ============================================================
-- FlowSurface — Reset completo de la base de datos
-- Ejecutar en Supabase SQL Editor cuando los datos viejos
-- ya no son válidos o el schema cambió.
--
-- ADVERTENCIA: borra TODOS los datos. No hay vuelta atrás.
-- ============================================================

-- ============================================================
-- 1. LIMPIAR (orden: vistas → hijos con FK → padres)
-- ============================================================

DROP VIEW  IF EXISTS v_signals_with_outcomes;

DROP TABLE IF EXISTS lab_outcomes;   -- ya no se usa (paralelo Subdimi no trackea outcomes)
DROP TABLE IF EXISTS lab_signals;
DROP TABLE IF EXISTS signal_outcomes;
DROP TABLE IF EXISTS intrabar_outcomes;
DROP TABLE IF EXISTS deployed_params;
DROP TABLE IF EXISTS calibration_log;
DROP TABLE IF EXISTS shadow_signals;
DROP TABLE IF EXISTS institutional_snapshots;
DROP TABLE IF EXISTS regime_history;

-- ============================================================
-- 2. TABLAS
-- ============================================================

-- Señales generadas por los detectores (write_signal)
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

    -- Niveles estructurales (para calibrar find_structural_target)
    hvn_levels_above        DOUBLE PRECISION[],
    hvn_levels_below        DOUBLE PRECISION[],
    nearest_wall_above      DOUBLE PRECISION,
    nearest_wall_below      DOUBLE PRECISION,
    swing_high_20           DOUBLE PRECISION,
    swing_low_20            DOUBLE PRECISION,

    -- Subdimi methodology context (columnas individuales para análisis estadístico)
    finish_action           BOOLEAN,
    unfinish_action         BOOLEAN,
    big_trade               BOOLEAN,
    stacked_imbalance       TEXT,
    vp_open_bias            TEXT,
    auction_state           TEXT,
    htf_weekly_location     TEXT,
    htf_monthly_location    TEXT,
    delta_velocity          DOUBLE PRECISION,

    -- Playbook reasoning Fase 1 (observador, no gate)
    reasoning_version       TEXT,
    primary_playbook        TEXT,
    secondary_playbooks     JSONB,
    reasoning_tags          JSONB,
    reasoning_confidence    DOUBLE PRECISION,
    reasoning_completeness  DOUBLE PRECISION,

    -- DRR — contexto del rango intradía
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

    subdomi_ctx             JSONB
);

-- Trades cerrados del paper trader (write_trade)
-- Una señal con partial exit genera DOS rows con el mismo signal_id:
--   close_reason='TP1_PARTIAL' (is_partial=TRUE)  + cierre final (is_partial=FALSE).
CREATE TABLE signal_outcomes (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id           UUID NOT NULL REFERENCES shadow_signals(id) ON DELETE CASCADE,
    resolved_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    timestamp_ms        BIGINT NOT NULL,

    close_reason        TEXT NOT NULL,   -- STOP_HIT | TARGET_HIT | TTL_EXPIRED | INVALIDATED | TP1_PARTIAL | TRAILING_HIT
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

    is_partial          BOOLEAN          NOT NULL DEFAULT FALSE,
    partial_fraction    DOUBLE PRECISION NOT NULL DEFAULT 0.0,  -- 0.5 = TP1, 0.0 = cierre total

    -- Precios futuros (patch_horizon los rellena en diferido)
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

-- Outcomes intrabar del OutcomeTracker (write_outcome)
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

-- Snapshots institucionales cada 5 minutos
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

-- Historial de cambios de régimen (write_regime_change)
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

-- Historial de calibraciones del pipeline Python
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

-- Señales del Subdimi Parallel Observer (los 6 detectores Subdimi corriendo en paralelo a DRR)
-- maturity = 'SubdimiParallel' para distinguirlas de señales históricas del Lab
CREATE TABLE lab_signals (
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

-- Parámetros activos por régimen (leídos por el monitor Rust)
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
-- 3. ÍNDICES
-- ============================================================

CREATE INDEX idx_signals_strategy_time  ON shadow_signals(strategy, timestamp_ms DESC);
CREATE INDEX idx_signals_action_time    ON shadow_signals(action, timestamp_ms DESC);
CREATE INDEX idx_signals_regime         ON shadow_signals(regime_combined, strategy);
CREATE INDEX idx_signals_institutional  ON shadow_signals(short_liq_usd_5m, funding_percentile_30d, ls_divergence);
CREATE INDEX idx_signals_playbook       ON shadow_signals(primary_playbook, reasoning_confidence, timestamp_ms DESC);

CREATE INDEX idx_outcomes_signal        ON signal_outcomes(signal_id);
CREATE INDEX idx_outcomes_reason        ON signal_outcomes(close_reason, r_multiple);
CREATE INDEX idx_outcomes_partial       ON signal_outcomes(signal_id, is_partial);

CREATE INDEX idx_intrabar_strategy_time ON intrabar_outcomes(strategy_id, signal_ms DESC);
CREATE INDEX idx_intrabar_source        ON intrabar_outcomes(source, signal_ms DESC);

CREATE INDEX idx_snapshots_time         ON institutional_snapshots(timestamp_ms DESC);
CREATE INDEX idx_regime_history_time    ON regime_history(timestamp_ms DESC);
CREATE INDEX idx_calibration_regime     ON calibration_log(regime, calibrated_at DESC);

CREATE INDEX idx_signals_range_location ON shadow_signals(range_location, strategy, timestamp_ms DESC);
CREATE INDEX idx_signals_session        ON shadow_signals(session_name, session_phase, strategy, timestamp_ms DESC);
CREATE INDEX idx_signals_absorption     ON shadow_signals(absorption_count, entry_type, strategy);
CREATE INDEX idx_signals_dow_hour       ON shadow_signals(day_of_week, hour_utc, strategy);
CREATE INDEX idx_signals_playbook       ON shadow_signals(primary_playbook, reasoning_confidence, timestamp_ms DESC);

CREATE INDEX idx_lab_signals_strategy   ON lab_signals(strategy_id, timestamp_ms DESC);
CREATE INDEX idx_lab_signals_status     ON lab_signals(status, maturity);

-- ============================================================
-- 4. VISTA ANALÍTICA
-- ============================================================

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

    -- Contexto institucional
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

    -- DRR range context
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

    -- Outcome del trade
    o.close_reason,
    o.r_multiple,
    o.pnl_net_usd,
    o.duration_ms,
    o.mfe_r,
    o.mae_r,
    o.is_partial,
    o.partial_fraction,

    -- Horizontes futuros
    o.r_5m,
    o.r_15m,
    o.r_30m,
    o.r_1h,

    -- Booleans de resultado (para filtros rápidos en Python)
    CASE WHEN o.close_reason = 'TARGET_HIT'  THEN TRUE ELSE FALSE END AS hit_target,
    CASE WHEN o.close_reason = 'STOP_HIT'    THEN TRUE ELSE FALSE END AS hit_stop,
    CASE WHEN o.close_reason = 'TTL_EXPIRED' THEN TRUE ELSE FALSE END AS expired,
    CASE WHEN o.close_reason = 'INVALIDATED' THEN TRUE ELSE FALSE END AS invalidated,
    CASE WHEN o.close_reason = 'TP1_PARTIAL' THEN TRUE ELSE FALSE END AS tp1_partial,

    -- Buckets para segmentación
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
