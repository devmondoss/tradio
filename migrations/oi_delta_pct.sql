-- FASE 1.2: OI delta % en señales RBF y AMD
-- Aplicar en Supabase SQL Editor antes de redesplegar el monitor

ALTER TABLE rbf_signals
  ADD COLUMN IF NOT EXISTS oi_delta_pct  DOUBLE PRECISION;

COMMENT ON COLUMN rbf_signals.oi_delta_pct IS
  '% cambio de Open Interest en la ventana reciente al momento del breakout. Positivo = expansión (nueva convicción); negativo = cierre de posiciones.';

ALTER TABLE amd_signals
  ADD COLUMN IF NOT EXISTS oi_delta_pct_at_spike  DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS oi_delta_pct_at_entry  DOUBLE PRECISION;

COMMENT ON COLUMN amd_signals.oi_delta_pct_at_spike IS
  '% cambio OI al momento del spike. Negativo en spike alcista = posiciones cerrando → manipulación confirmada.';
COMMENT ON COLUMN amd_signals.oi_delta_pct_at_entry IS
  '% cambio OI en la barra de distribución/entry. Positivo = expansión de nuevas posiciones en la dirección del trade.';
