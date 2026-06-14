-- Tabla para trades del sistema HTF Longs (espejo alcista de htf_trades)
-- Patrones mineados 2026-06-14: ETH/SOL con filtro H4 EMA20

CREATE TABLE IF NOT EXISTS htf_long_trades (
    id            BIGSERIAL PRIMARY KEY,
    created_at    TIMESTAMPTZ DEFAULT now(),
    symbol        TEXT        NOT NULL,
    sig           TEXT        NOT NULL,
    session       TEXT,
    h4_trend      TEXT,
    entry         FLOAT8,
    stop          FLOAT8,
    target        FLOAT8,
    stop_pct      FLOAT8,
    is_open       BOOLEAN     DEFAULT TRUE,
    result_r      FLOAT8,
    gross_r       FLOAT8,
    fee_r         FLOAT8,
    reason        TEXT,
    exit_price    FLOAT8,
    duration_bars INT,
    entry_at      TIMESTAMPTZ,
    closed_at     TIMESTAMPTZ
);

-- Índices para consultas rápidas desde el dashboard
CREATE INDEX IF NOT EXISTS idx_htf_long_trades_symbol    ON htf_long_trades (symbol);
CREATE INDEX IF NOT EXISTS idx_htf_long_trades_entry_at  ON htf_long_trades (entry_at DESC);
CREATE INDEX IF NOT EXISTS idx_htf_long_trades_is_open   ON htf_long_trades (is_open);
