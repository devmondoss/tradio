-- FASE 1.1: Delta Z-score continuo en amd_signals
-- Aplicar en Supabase SQL Editor antes de redesplegar el monitor

ALTER TABLE amd_signals
  ADD COLUMN IF NOT EXISTS delta_dz_at_spike  DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS delta_dz_at_entry  DOUBLE PRECISION;

COMMENT ON COLUMN amd_signals.delta_dz_at_spike IS
  'Rolling delta z-score (50 barras) de la barra del spike. Negativo para SpikeDir::Up = sellers dominando → manipulación confirmada.';
COMMENT ON COLUMN amd_signals.delta_dz_at_entry IS
  'Rolling delta z-score de la barra de distribución/entry. Positivo para Short = sellers activos en la barra de reversión.';
