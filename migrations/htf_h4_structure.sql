-- FASE 2.6: HTF H4 estructura en rbf_signals y amd_signals
-- Aplicar en Supabase SQL Editor antes de redesplegar el monitor

ALTER TABLE rbf_signals
  ADD COLUMN IF NOT EXISTS htf_h4_trend   TEXT,
  ADD COLUMN IF NOT EXISTS htf_h4_aligned BOOLEAN;

COMMENT ON COLUMN rbf_signals.htf_h4_trend IS
  'Tendencia H4 al breakout: "Bull" (precio > EMA-240M1) o "Bear". NULL si aún en warmup (<240 barras).';
COMMENT ON COLUMN rbf_signals.htf_h4_aligned IS
  'True si el trade va en dirección del H4: Long+Bull o Short+Bear.';

ALTER TABLE amd_signals
  ADD COLUMN IF NOT EXISTS htf_h4_trend   TEXT,
  ADD COLUMN IF NOT EXISTS htf_h4_aligned BOOLEAN;

COMMENT ON COLUMN amd_signals.htf_h4_trend IS
  'Tendencia H4 al entry AMD: "Bull" o "Bear". NULL si aún en warmup (<240 barras M1).';
COMMENT ON COLUMN amd_signals.htf_h4_aligned IS
  'True si la reversión AMD va en dirección del H4 (Long+Bull o Short+Bear).';
