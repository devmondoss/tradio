-- FASE 2.2 + 2.3: VR tier + métricas de calidad del rango en RBF
-- Aplicar en Supabase SQL Editor antes de redesplegar el monitor

ALTER TABLE rbf_signals
  ADD COLUMN IF NOT EXISTS vr_tier                SMALLINT,
  ADD COLUMN IF NOT EXISTS range_touch_symmetry   DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS cvd_per_bar            DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS breakout_extension_pct DOUBLE PRECISION;

COMMENT ON COLUMN rbf_signals.vr_tier IS
  'Categoría de Volume Ratio: 1=2-3×, 2=3-4×, 3=4×+. Correlaciona con probabilidad de follow-through.';
COMMENT ON COLUMN rbf_signals.range_touch_symmetry IS
  '0=asimétrico (solo un lado tocado), 1=simétrico (ambos lados igual). Rangos simétricos = consolidación real.';
COMMENT ON COLUMN rbf_signals.cvd_per_bar IS
  'CVD acumulado en el rango dividido entre barras del rango. Densidad de presión por barra.';
COMMENT ON COLUMN rbf_signals.breakout_extension_pct IS
  '% que cerró más allá del límite del rango en dirección del breakout. >0.05% = breakout fuerte.';
