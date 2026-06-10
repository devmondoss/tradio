-- FASE 1.1: Absorption Score continuo en rbf_signals
-- Aplicar en Supabase SQL Editor antes de redesplegar el monitor

ALTER TABLE rbf_signals
  ADD COLUMN IF NOT EXISTS absorption_score  DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS bar_displacement  DOUBLE PRECISION;

COMMENT ON COLUMN rbf_signals.absorption_score IS
  'Intensidad del delta direccional normalizada [0,1]. max(dz_dir,0)/3. Alto = fuerte presión delta alineada con el breakout.';
COMMENT ON COLUMN rbf_signals.bar_displacement IS
  'Ratio cuerpo/rango de la vela de ruptura [0,1]. 0 = pin bar (absorbido), 1 = marubozu (engulfing).';
