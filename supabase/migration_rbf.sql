-- Range Breakout Flow signals table
-- Señales del detector RangeBreakoutFlow (validado en backtest 30 días M1)
-- Ejecutar en Supabase SQL Editor

CREATE TABLE IF NOT EXISTS rbf_signals (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    timestamp_ms    BIGINT NOT NULL,

    -- Señal
    direction       TEXT NOT NULL,   -- 'Short' | 'Long'
    session         TEXT,            -- 'London' | 'LondonNyOverlap' | 'NewYork'
    entry_price     DOUBLE PRECISION NOT NULL,
    stop_price      DOUBLE PRECISION NOT NULL,
    target_price    DOUBLE PRECISION NOT NULL,
    rr              DOUBLE PRECISION,

    -- Contexto del rango
    range_high      DOUBLE PRECISION,
    range_low       DOUBLE PRECISION,
    range_pct       DOUBLE PRECISION,  -- tamaño del rango como % del precio
    range_bars      INTEGER,           -- barras M1 del rango

    -- Orderflow
    cvd_in_range    DOUBLE PRECISION,  -- CVD acumulado durante el rango
    vr_at_breakout  DOUBLE PRECISION,  -- volumen relativo en la barra de breakout

    -- Régimen
    macro_regime    TEXT,   -- 'Bull' | 'Bear' | 'BullPullback' | 'BearPullback'

    -- Outcome (se rellena después)
    status          TEXT NOT NULL DEFAULT 'OPEN',
    exit_price      DOUBLE PRECISION,
    exit_reason     TEXT,
    pnl_pct         DOUBLE PRECISION,
    result_r        DOUBLE PRECISION,
    closed_at       TIMESTAMPTZ,

    -- Debug
    evidence        TEXT[]
);

-- Índices para consultas de análisis
CREATE INDEX IF NOT EXISTS rbf_signals_ts        ON rbf_signals (timestamp_ms DESC);
CREATE INDEX IF NOT EXISTS rbf_signals_direction ON rbf_signals (direction, session);
CREATE INDEX IF NOT EXISTS rbf_signals_status    ON rbf_signals (status);

-- Vista resumen por dirección y sesión
CREATE OR REPLACE VIEW v_rbf_summary AS
SELECT
    direction,
    session,
    macro_regime,
    COUNT(*)                                               AS total,
    COUNT(*) FILTER (WHERE status = 'OPEN')                AS open,
    COUNT(*) FILTER (WHERE result_r >= 1.0)                AS wins,
    COUNT(*) FILTER (WHERE result_r < 0)                   AS losses,
    ROUND(AVG(result_r)::NUMERIC, 3)                       AS avg_r,
    ROUND(AVG(range_pct)::NUMERIC, 4)                      AS avg_range_pct,
    ROUND(AVG(vr_at_breakout)::NUMERIC, 2)                 AS avg_vr,
    ROUND(AVG(cvd_in_range)::NUMERIC, 1)                   AS avg_cvd
FROM rbf_signals
GROUP BY direction, session, macro_regime
ORDER BY direction, session;
