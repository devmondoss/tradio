-- ============================================================================
-- MIGRACIÓN: Sistema de Scalping — 3 estrategias order flow
-- Ejecutar DESPUÉS de schema.sql y migration_drr.sql
--
-- Tablas nuevas:
--   scalping_signals  — señales detectadas por S1/S2/S3 (paper trading)
--   scalping_trades   — trades cerrados con resultado
-- ============================================================================

-- ── scalping_signals ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS scalping_signals (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    timestamp_ms    BIGINT NOT NULL,

    -- Identificación
    strategy        TEXT NOT NULL,      -- 'S1_OBI' | 'S2_ABSORPTION' | 'S3_DIVERGENCE'
    side            TEXT NOT NULL,      -- 'Long' | 'Short'
    entry_type      TEXT,               -- 'aggressive' | 'conservative' | 'reclaim' | 'divergence' | 'post_only'
    session         TEXT,               -- 'London' | 'NewYork' | 'LondonNyOverlap'

    -- Precios
    entry_price     DOUBLE PRECISION,
    stop_price      DOUBLE PRECISION,
    tp1_price       DOUBLE PRECISION,
    tp2_price       DOUBLE PRECISION,
    rr_planned      DOUBLE PRECISION,
    conviction_score DOUBLE PRECISION,

    -- Contexto en entrada
    obi_at_entry    DOUBLE PRECISION,
    obi_ema_fast    DOUBLE PRECISION,
    dz_at_entry     DOUBLE PRECISION,
    vr_at_entry     DOUBLE PRECISION,
    cvd_at_entry    DOUBLE PRECISION,
    spread_ticks    INTEGER,
    atr             DOUBLE PRECISION,
    regime          TEXT,               -- 'Range' | 'Trend'
    liq_ratio       DOUBLE PRECISION,

    -- Contexto de rango (para S2)
    range_high      DOUBLE PRECISION,
    range_low       DOUBLE PRECISION,
    range_mid       DOUBLE PRECISION,
    range_location  TEXT,               -- 'NearHigh' | 'NearLow' | 'Inside' | 'NoTrade'

    -- CVD / divergencia (para S3)
    divergence_strength DOUBLE PRECISION,
    divergence_bars_ago INTEGER,

    -- Evidencia
    evidence        TEXT[],

    -- Estado del trade (se actualiza cuando cierra)
    status          TEXT NOT NULL DEFAULT 'OPEN',   -- 'OPEN' | 'CLOSED'
    exit_price      DOUBLE PRECISION,
    exit_reason     TEXT,
    pnl_net         DOUBLE PRECISION,
    result_r        DOUBLE PRECISION,
    duration_ms     BIGINT,
    closed_at       TIMESTAMPTZ
);

-- ── scalping_trades (tabla plana de trades cerrados para análisis) ────────────

CREATE TABLE IF NOT EXISTS scalping_trades (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    trade_id        TEXT NOT NULL UNIQUE,

    -- Identificación
    strategy        TEXT NOT NULL,
    side            TEXT NOT NULL,
    session         TEXT,
    entry_type      TEXT,

    -- Precios
    entry_price     DOUBLE PRECISION NOT NULL,
    stop_price      DOUBLE PRECISION,
    tp1_price       DOUBLE PRECISION,
    tp2_price       DOUBLE PRECISION,
    exit_price      DOUBLE PRECISION NOT NULL,
    rr_planned      DOUBLE PRECISION,

    -- Contexto
    obi_at_entry    DOUBLE PRECISION,
    dz_at_entry     DOUBLE PRECISION,
    vr_at_entry     DOUBLE PRECISION,
    cvd_at_entry    DOUBLE PRECISION,
    conviction_score DOUBLE PRECISION,
    spread_ticks    INTEGER,

    -- Resultado
    exit_reason     TEXT NOT NULL,
    pnl_gross       DOUBLE PRECISION,
    pnl_net         DOUBLE PRECISION NOT NULL,
    result_r        DOUBLE PRECISION,
    duration_ms     BIGINT,
    entry_ms        BIGINT,
    exit_ms         BIGINT,

    -- MFE / MAE
    mfe             DOUBLE PRECISION,
    mae             DOUBLE PRECISION
);

-- ── Índices ───────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_scalping_signals_ts
    ON scalping_signals (timestamp_ms DESC);

CREATE INDEX IF NOT EXISTS idx_scalping_signals_strategy
    ON scalping_signals (strategy, side);

CREATE INDEX IF NOT EXISTS idx_scalping_signals_status
    ON scalping_signals (status);

CREATE INDEX IF NOT EXISTS idx_scalping_trades_strategy
    ON scalping_trades (strategy, side);

CREATE INDEX IF NOT EXISTS idx_scalping_trades_exit_reason
    ON scalping_trades (exit_reason);

-- ── Vista de análisis rápido ──────────────────────────────────────────────────

CREATE OR REPLACE VIEW v_scalping_summary AS
SELECT
    strategy,
    side,
    COUNT(*) AS n_trades,
    COUNT(*) FILTER (WHERE pnl_net > 0) AS wins,
    ROUND(COUNT(*) FILTER (WHERE pnl_net > 0) * 100.0 / NULLIF(COUNT(*), 0), 1) AS win_rate_pct,
    ROUND(AVG(pnl_net)::NUMERIC, 4)   AS avg_pnl_net,
    ROUND(SUM(pnl_net)::NUMERIC, 4)   AS total_pnl_net,
    ROUND(AVG(result_r)::NUMERIC, 3)  AS avg_r,
    ROUND(AVG(conviction_score)::NUMERIC, 1) AS avg_score,
    ROUND(AVG(duration_ms / 1000.0)::NUMERIC, 0) AS avg_duration_secs
FROM scalping_trades
GROUP BY strategy, side
ORDER BY strategy, side;

-- ── Vista por tipo de entrada (para S2/S3 principalmente) ────────────────────

CREATE OR REPLACE VIEW v_scalping_by_entry_type AS
SELECT
    strategy,
    entry_type,
    COUNT(*) AS n,
    ROUND(COUNT(*) FILTER (WHERE pnl_net > 0) * 100.0 / NULLIF(COUNT(*), 0), 1) AS win_rate_pct,
    ROUND(AVG(result_r)::NUMERIC, 3) AS avg_r,
    ROUND(SUM(pnl_net)::NUMERIC, 4)  AS total_pnl_net
FROM scalping_trades
GROUP BY strategy, entry_type
ORDER BY strategy, entry_type;
