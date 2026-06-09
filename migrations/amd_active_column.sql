-- Persistencia del paper trader AMD entre reinicios de Railway.
ALTER TABLE amd_signals ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_amd_signals_is_active
    ON amd_signals (symbol, is_active)
    WHERE is_active = TRUE;
