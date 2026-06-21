-- Persistencia del paper-trading de la estrategia de PROVISIÓN DE LIQUIDEZ (BTCUSDT perp).
-- Valida el fill ratio maker en vivo (live/paper_liquidity.py, deploy en Railway).
-- Dos tablas: trades cerrados (calidad de fills) + snapshots periódicos (fill ratio en el tiempo).

-- 1) Trades cerrados (un row por posición que se cierra: stop / target / breakeven)
CREATE TABLE IF NOT EXISTS liquidity_paper_trades (
    id           BIGSERIAL PRIMARY KEY,
    created_at   TIMESTAMPTZ DEFAULT now(),
    symbol       TEXT NOT NULL,
    tf           TEXT NOT NULL,              -- timeframe de decisión (15)
    kind         TEXT NOT NULL,             -- poc_orderblock | poc_defendido
    side         TEXT NOT NULL,             -- long | short
    vol_regime   TEXT NOT NULL,             -- high | low (ATR vs mediana)
    entry        FLOAT8,
    stop         FLOAT8,
    target       FLOAT8,
    exit_price   FLOAT8,
    result_r     FLOAT8,                    -- R neto de fee maker
    win          BOOLEAN,
    reason       TEXT,                      -- target | stop | breakeven | timeout
    opened_at    TIMESTAMPTZ,
    closed_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_liq_trades_symbol   ON liquidity_paper_trades (symbol);
CREATE INDEX IF NOT EXISTS idx_liq_trades_closed   ON liquidity_paper_trades (closed_at DESC);
CREATE INDEX IF NOT EXISTS idx_liq_trades_regime   ON liquidity_paper_trades (vol_regime);

-- 2) Snapshots periódicos (cada cierre de vela): fill ratio y stats acumuladas por régimen
CREATE TABLE IF NOT EXISTS liquidity_paper_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ DEFAULT now(),
    at              TIMESTAMPTZ,
    symbol          TEXT NOT NULL,
    tf              TEXT NOT NULL,
    resting         INT,
    open_pos        INT,
    placed_high     INT,  filled_high     INT,  fill_ratio_high FLOAT8,
    closed_high     INT,  wr_high         FLOAT8, avg_r_high     FLOAT8,
    placed_low      INT,  filled_low      INT,  fill_ratio_low  FLOAT8,
    closed_low      INT,  wr_low          FLOAT8, avg_r_low      FLOAT8
);
CREATE INDEX IF NOT EXISTS idx_liq_snap_at ON liquidity_paper_snapshots (at DESC);

-- Vista rápida del estado más reciente (snapshot en-memoria, 'desde el último restart')
CREATE OR REPLACE VIEW liquidity_paper_latest AS
SELECT DISTINCT ON (symbol, tf) *
FROM liquidity_paper_snapshots
ORDER BY symbol, tf, at DESC;

-- 3) EVENTOS (fuente de verdad del fill ratio, INMUNE a restarts).
--    1 fila por cada límite colocado (place) y por cada llenado (fill).
CREATE TABLE IF NOT EXISTS liquidity_paper_events (
    id           BIGSERIAL PRIMARY KEY,
    created_at   TIMESTAMPTZ DEFAULT now(),
    at           TIMESTAMPTZ,
    symbol       TEXT NOT NULL,
    tf           TEXT NOT NULL,
    event_type   TEXT NOT NULL,        -- place | fill
    kind         TEXT,                 -- poc_orderblock | poc_defendido
    side         TEXT,                 -- long | short
    vol_regime   TEXT,                 -- high | low
    price        FLOAT8
);
CREATE INDEX IF NOT EXISTS idx_liq_events_at     ON liquidity_paper_events (at DESC);
CREATE INDEX IF NOT EXISTS idx_liq_events_type   ON liquidity_paper_events (event_type);
CREATE INDEX IF NOT EXISTS idx_liq_events_regime ON liquidity_paper_events (vol_regime);

-- Fill ratio REAL acumulado (desde eventos, sobrevive a restarts), por régimen de volatilidad
CREATE OR REPLACE VIEW liquidity_paper_fill_ratio AS
SELECT
    symbol, tf, vol_regime,
    count(*) FILTER (WHERE event_type='place') AS placed,
    count(*) FILTER (WHERE event_type='fill')  AS filled,
    round(count(*) FILTER (WHERE event_type='fill')::numeric
          / nullif(count(*) FILTER (WHERE event_type='place'), 0), 4) AS fill_ratio
FROM liquidity_paper_events
GROUP BY symbol, tf, vol_regime;
