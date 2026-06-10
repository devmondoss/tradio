-- Renombrar htf_h4_* → htf_h1_* en rbf_signals y amd_signals
-- EMA cambió de 240 barras M1 (4H) a 60 barras M1 (1H)
-- Aplicar ANTES de redesplegar el monitor

ALTER TABLE rbf_signals
  RENAME COLUMN htf_h4_trend   TO htf_h1_trend;
ALTER TABLE rbf_signals
  RENAME COLUMN htf_h4_aligned TO htf_h1_aligned;

ALTER TABLE amd_signals
  RENAME COLUMN htf_h4_trend   TO htf_h1_trend;
ALTER TABLE amd_signals
  RENAME COLUMN htf_h4_aligned TO htf_h1_aligned;

COMMENT ON COLUMN rbf_signals.htf_h1_trend IS
  'Tendencia H1 al breakout: "Bull" (precio > EMA-60M1) o "Bear". NULL si warmup < 60 barras (≈1h desde arranque).';
COMMENT ON COLUMN rbf_signals.htf_h1_aligned IS
  'True si Long+Bull o Short+Bear. Alineamiento con tendencia de la hora anterior.';

COMMENT ON COLUMN amd_signals.htf_h1_trend IS
  'Tendencia H1 al entry AMD: "Bull" o "Bear". NULL si warmup < 60 barras M1.';
COMMENT ON COLUMN amd_signals.htf_h1_aligned IS
  'True si la reversión AMD va en dirección del H1 trend (Long+Bull o Short+Bear).';
