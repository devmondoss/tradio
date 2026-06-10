-- FASE 2.1: VP Open Variant — contexto del día en rbf_signals
-- Aplicar en Supabase SQL Editor antes de redesplegar el monitor

ALTER TABLE rbf_signals
  ADD COLUMN IF NOT EXISTS vp_open_bias TEXT;

COMMENT ON COLUMN rbf_signals.vp_open_bias IS
  'Tipo de día según apertura vs Value Area anterior: InsideValue (rango), OutsideVaInsidePa (tendencia al POC), TrendDay (gap real), FadeGap (gap + aceptación de vuelta). Útil para filtrar RBF: TrendDay favorece breakouts en dirección del gap.';
