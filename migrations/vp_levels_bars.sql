-- Volume Profile levels per bar: POC, VAH, VAL, LVN below price
-- Computed by Rust monitor from 300-bar session VP and saved alongside each M1 bar.
-- Apply in Supabase SQL Editor before redeploying the monitor.

ALTER TABLE btc_bars ADD COLUMN IF NOT EXISTS vp_poc       FLOAT8;
ALTER TABLE btc_bars ADD COLUMN IF NOT EXISTS vp_vah       FLOAT8;
ALTER TABLE btc_bars ADD COLUMN IF NOT EXISTS vp_val       FLOAT8;
ALTER TABLE btc_bars ADD COLUMN IF NOT EXISTS vp_lvn_below FLOAT8;

ALTER TABLE eth_bars ADD COLUMN IF NOT EXISTS vp_poc       FLOAT8;
ALTER TABLE eth_bars ADD COLUMN IF NOT EXISTS vp_vah       FLOAT8;
ALTER TABLE eth_bars ADD COLUMN IF NOT EXISTS vp_val       FLOAT8;
ALTER TABLE eth_bars ADD COLUMN IF NOT EXISTS vp_lvn_below FLOAT8;

ALTER TABLE sol_bars ADD COLUMN IF NOT EXISTS vp_poc       FLOAT8;
ALTER TABLE sol_bars ADD COLUMN IF NOT EXISTS vp_vah       FLOAT8;
ALTER TABLE sol_bars ADD COLUMN IF NOT EXISTS vp_val       FLOAT8;
ALTER TABLE sol_bars ADD COLUMN IF NOT EXISTS vp_lvn_below FLOAT8;

ALTER TABLE bnb_bars ADD COLUMN IF NOT EXISTS vp_poc       FLOAT8;
ALTER TABLE bnb_bars ADD COLUMN IF NOT EXISTS vp_vah       FLOAT8;
ALTER TABLE bnb_bars ADD COLUMN IF NOT EXISTS vp_val       FLOAT8;
ALTER TABLE bnb_bars ADD COLUMN IF NOT EXISTS vp_lvn_below FLOAT8;

ALTER TABLE xrp_bars ADD COLUMN IF NOT EXISTS vp_poc       FLOAT8;
ALTER TABLE xrp_bars ADD COLUMN IF NOT EXISTS vp_vah       FLOAT8;
ALTER TABLE xrp_bars ADD COLUMN IF NOT EXISTS vp_val       FLOAT8;
ALTER TABLE xrp_bars ADD COLUMN IF NOT EXISTS vp_lvn_below FLOAT8;
