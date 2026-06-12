-- Buyer Exhaustion strategy — tabla de señales y outcomes
-- Patrón: rango CVD+ + giro CVD + breakout a la baja con volumen expandido.
-- Columnas de outcome (exit_*) se rellenan cuando el paper trader cierra la posición.

CREATE TABLE IF NOT EXISTS be_signals (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Identificación
    symbol       TEXT NOT NULL,
    session      TEXT NOT NULL,
    timestamp_ms BIGINT NOT NULL,

    -- Niveles del trade
    entry_price  DOUBLE PRECISION NOT NULL,
    stop_price   DOUBLE PRECISION NOT NULL,
    target_price DOUBLE PRECISION NOT NULL,
    rr           DOUBLE PRECISION NOT NULL,

    -- Evidencia del rango
    range_high   DOUBLE PRECISION NOT NULL,
    range_low    DOUBLE PRECISION NOT NULL,
    range_pct    DOUBLE PRECISION NOT NULL,
    range_bars   INTEGER NOT NULL,

    -- Evidencia CVD
    range_cvd       DOUBLE PRECISION NOT NULL,   -- positivo (compradores atrapados)
    pre_cvd_flip    DOUBLE PRECISION NOT NULL,   -- negativo (giro de sellers)
    cvd_flip_ratio  DOUBLE PRECISION NOT NULL,   -- fuerza del giro (0–1+)

    -- Evidencia del breakout
    vr_at_breakout  DOUBLE PRECISION NOT NULL,
    breakout_delta  DOUBLE PRECISION NOT NULL,   -- negativo

    -- Outcome (rellena cuando el paper trader cierra)
    active       BOOLEAN NOT NULL DEFAULT true,
    closed_at    TIMESTAMPTZ,
    exit_price   DOUBLE PRECISION,
    exit_reason  TEXT,          -- TARGET / STOP / TIME_STOP / SESSION_END / DAILY_LIMIT
    result_r     DOUBLE PRECISION,
    bars_held    INTEGER
);

-- Índices para queries de análisis
CREATE INDEX IF NOT EXISTS be_signals_symbol_idx    ON be_signals (symbol);
CREATE INDEX IF NOT EXISTS be_signals_session_idx   ON be_signals (session);
CREATE INDEX IF NOT EXISTS be_signals_active_idx    ON be_signals (active) WHERE active = true;
CREATE INDEX IF NOT EXISTS be_signals_timestamp_idx ON be_signals (timestamp_ms DESC);
CREATE INDEX IF NOT EXISTS be_signals_result_idx    ON be_signals (result_r) WHERE result_r IS NOT NULL;
