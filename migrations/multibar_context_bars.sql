-- Multi-bar context features for mining — consecutive CVD direction, prev delta, compression
-- These give the signal bar its narrative context: what happened in the bars BEFORE it.
-- Apply in Supabase SQL Editor after vp_levels_bars.sql.

ALTER TABLE btc_bars ADD COLUMN IF NOT EXISTS cvd_consec_neg    INT2;
ALTER TABLE btc_bars ADD COLUMN IF NOT EXISTS cvd_consec_pos    INT2;
ALTER TABLE btc_bars ADD COLUMN IF NOT EXISTS prev_bar_delta    FLOAT8;
ALTER TABLE btc_bars ADD COLUMN IF NOT EXISTS bars_since_low_vr INT2;

ALTER TABLE eth_bars ADD COLUMN IF NOT EXISTS cvd_consec_neg    INT2;
ALTER TABLE eth_bars ADD COLUMN IF NOT EXISTS cvd_consec_pos    INT2;
ALTER TABLE eth_bars ADD COLUMN IF NOT EXISTS prev_bar_delta    FLOAT8;
ALTER TABLE eth_bars ADD COLUMN IF NOT EXISTS bars_since_low_vr INT2;

ALTER TABLE sol_bars ADD COLUMN IF NOT EXISTS cvd_consec_neg    INT2;
ALTER TABLE sol_bars ADD COLUMN IF NOT EXISTS cvd_consec_pos    INT2;
ALTER TABLE sol_bars ADD COLUMN IF NOT EXISTS prev_bar_delta    FLOAT8;
ALTER TABLE sol_bars ADD COLUMN IF NOT EXISTS bars_since_low_vr INT2;

ALTER TABLE bnb_bars ADD COLUMN IF NOT EXISTS cvd_consec_neg    INT2;
ALTER TABLE bnb_bars ADD COLUMN IF NOT EXISTS cvd_consec_pos    INT2;
ALTER TABLE bnb_bars ADD COLUMN IF NOT EXISTS prev_bar_delta    FLOAT8;
ALTER TABLE bnb_bars ADD COLUMN IF NOT EXISTS bars_since_low_vr INT2;

ALTER TABLE xrp_bars ADD COLUMN IF NOT EXISTS cvd_consec_neg    INT2;
ALTER TABLE xrp_bars ADD COLUMN IF NOT EXISTS cvd_consec_pos    INT2;
ALTER TABLE xrp_bars ADD COLUMN IF NOT EXISTS prev_bar_delta    FLOAT8;
ALTER TABLE xrp_bars ADD COLUMN IF NOT EXISTS bars_since_low_vr INT2;
