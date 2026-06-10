-- FASE 4: Score continuo + sizing dinámico
-- Aplicar en Supabase SQL Editor antes de redesplegar el monitor

ALTER TABLE rbf_signals
  ADD COLUMN IF NOT EXISTS signal_score_v2  DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS sizing_multiplier DOUBLE PRECISION;

COMMENT ON COLUMN rbf_signals.signal_score_v2 IS
  'Score experimental 0-1 combinando absorption, vr_tier, breakout_extension, h4_aligned, confluence. Validar vs outcomes antes de usar en sizing.';
COMMENT ON COLUMN rbf_signals.sizing_multiplier IS
  'Multiplicador sugerido: 0.5x(<0.3), 1.0x(0.3-0.5), 1.5x(0.5-0.7), 2.0x(>=0.7). Experimental.';

ALTER TABLE amd_signals
  ADD COLUMN IF NOT EXISTS signal_score_v2  DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS sizing_multiplier DOUBLE PRECISION;

COMMENT ON COLUMN amd_signals.signal_score_v2 IS
  'Score experimental 0-1 combinando quality_score, delta_dz, kill_zone, bars_to_entry, h4_aligned. Validar vs outcomes.';
COMMENT ON COLUMN amd_signals.sizing_multiplier IS
  'Multiplicador sugerido 0.5x-2.0x según signal_score_v2. Experimental hasta n>=30.';
