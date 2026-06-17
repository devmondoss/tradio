-- Trades paper live para MTF Spot.
-- Separado de mtf_trades para no mezclar Bybit spot con Binance/Bybit futures.

CREATE TABLE IF NOT EXISTS mtf_spot_trades (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ DEFAULT now(),
    symbol          TEXT NOT NULL,
    venue           TEXT NOT NULL DEFAULT 'bybit',
    market_type     TEXT NOT NULL DEFAULT 'spot',
    strategy        TEXT NOT NULL,
    sig             TEXT NOT NULL,
    session         TEXT,
    direction       TEXT NOT NULL,
    level           TEXT,
    entry           FLOAT8,
    stop            FLOAT8,
    target          FLOAT8,
    stop_pct        FLOAT8,
    is_open         BOOLEAN DEFAULT TRUE,
    result_r        FLOAT8,
    gross_r         FLOAT8,
    fee_r           FLOAT8,
    reason          TEXT,
    exit_price      FLOAT8,
    duration_bars   INT,
    mfe_r           FLOAT8,
    mae_r           FLOAT8,
    entry_at        TIMESTAMPTZ,
    closed_at       TIMESTAMPTZ,
    wick_pct        FLOAT8,
    obi_entry       FLOAT8,
    delta_entry     FLOAT8,
    cvd_slope_entry FLOAT8
);

CREATE INDEX IF NOT EXISTS idx_mtf_spot_trades_symbol
    ON mtf_spot_trades (symbol);

CREATE INDEX IF NOT EXISTS idx_mtf_spot_trades_entry_at
    ON mtf_spot_trades (entry_at DESC);

CREATE INDEX IF NOT EXISTS idx_mtf_spot_trades_is_open
    ON mtf_spot_trades (is_open);

CREATE INDEX IF NOT EXISTS idx_mtf_spot_trades_strategy
    ON mtf_spot_trades (strategy);
