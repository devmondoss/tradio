-- Add obi_min_intrabar and obi_max_intrabar to all bar tables
-- obi_min_intrabar: lowest OBI-L5 sampled during the M1 bar (peak selling pressure)
-- obi_max_intrabar: highest OBI-L5 sampled during the M1 bar (peak buying pressure)
-- Sampled every 10s → 6 samples per bar → captures intrabar DOM context lost in bar-close snapshot

ALTER TABLE btc_bars ADD COLUMN IF NOT EXISTS obi_min_intrabar FLOAT8;
ALTER TABLE btc_bars ADD COLUMN IF NOT EXISTS obi_max_intrabar FLOAT8;

ALTER TABLE eth_bars ADD COLUMN IF NOT EXISTS obi_min_intrabar FLOAT8;
ALTER TABLE eth_bars ADD COLUMN IF NOT EXISTS obi_max_intrabar FLOAT8;

ALTER TABLE bnb_bars ADD COLUMN IF NOT EXISTS obi_min_intrabar FLOAT8;
ALTER TABLE bnb_bars ADD COLUMN IF NOT EXISTS obi_max_intrabar FLOAT8;

ALTER TABLE sol_bars ADD COLUMN IF NOT EXISTS obi_min_intrabar FLOAT8;
ALTER TABLE sol_bars ADD COLUMN IF NOT EXISTS obi_max_intrabar FLOAT8;

ALTER TABLE xrp_bars ADD COLUMN IF NOT EXISTS obi_min_intrabar FLOAT8;
ALTER TABLE xrp_bars ADD COLUMN IF NOT EXISTS obi_max_intrabar FLOAT8;
