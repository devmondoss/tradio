-- Una sola tabla htf_trades para shorts y longs (direction='Short'/'Long')
-- Ejecutar en Supabase SQL Editor

ALTER TABLE htf_trades
  ADD COLUMN IF NOT EXISTS direction       TEXT DEFAULT 'Short',
  ADD COLUMN IF NOT EXISTS obi_entry       FLOAT8,
  ADD COLUMN IF NOT EXISTS cvd_slope_entry FLOAT8,
  ADD COLUMN IF NOT EXISTS dz_score        FLOAT8,
  ADD COLUMN IF NOT EXISTS stacked_imb     TEXT,
  ADD COLUMN IF NOT EXISTS equal_low       BOOLEAN;

-- Borrar tabla duplicada (si ya se creó por error)
DROP TABLE IF EXISTS htf_long_trades;
