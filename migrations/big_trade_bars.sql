-- Add big_trade_bearish and big_trade_bullish to all bar tables
-- big_trade_bearish: footprint level with volume >2.5x bar avg, sell-dominated, in upper half of bar (institutional seller in wick)
-- big_trade_bullish: footprint level with volume >2.5x bar avg, buy-dominated, in lower half of bar (institutional buyer in tail)

ALTER TABLE btc_bars ADD COLUMN IF NOT EXISTS big_trade_bearish BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE btc_bars ADD COLUMN IF NOT EXISTS big_trade_bullish BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE eth_bars ADD COLUMN IF NOT EXISTS big_trade_bearish BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE eth_bars ADD COLUMN IF NOT EXISTS big_trade_bullish BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE bnb_bars ADD COLUMN IF NOT EXISTS big_trade_bearish BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE bnb_bars ADD COLUMN IF NOT EXISTS big_trade_bullish BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE sol_bars ADD COLUMN IF NOT EXISTS big_trade_bearish BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE sol_bars ADD COLUMN IF NOT EXISTS big_trade_bullish BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE xrp_bars ADD COLUMN IF NOT EXISTS big_trade_bearish BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE xrp_bars ADD COLUMN IF NOT EXISTS big_trade_bullish BOOLEAN NOT NULL DEFAULT FALSE;
