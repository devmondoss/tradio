-- FASE 1.3: CVD divergencia explícita en señales RBF y AMD
-- Aplicar en Supabase SQL Editor antes de redesplegar el monitor

ALTER TABLE rbf_signals
  ADD COLUMN IF NOT EXISTS cvd_divergence_bars  INTEGER;

COMMENT ON COLUMN rbf_signals.cvd_divergence_bars IS
  'Barras consecutivas de divergencia CVD-precio al breakout. Negativo=bullish div (buen LONG), positivo=bearish div (buen SHORT). |abs|>4 = señal fuerte.';

ALTER TABLE amd_signals
  ADD COLUMN IF NOT EXISTS cvd_divergence_bars  INTEGER;

COMMENT ON COLUMN amd_signals.cvd_divergence_bars IS
  'Barras de divergencia CVD-precio en el entry. Negativo=bullish div (confirma LONG reversal), positivo=bearish div (confirma SHORT reversal).';
