-- FASE 2.4 + 2.5: Kill Zones + Spike quality metrics en AMD
-- Aplicar en Supabase SQL Editor antes de redesplegar el monitor

ALTER TABLE amd_signals
  ADD COLUMN IF NOT EXISTS is_kill_zone        BOOLEAN,
  ADD COLUMN IF NOT EXISTS kill_zone_name      TEXT,
  ADD COLUMN IF NOT EXISTS bars_to_entry       INTEGER,
  ADD COLUMN IF NOT EXISTS spike_extension_pct DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS range_spike_ratio   DOUBLE PRECISION;

COMMENT ON COLUMN amd_signals.is_kill_zone IS
  'True si la señal ocurrió durante London (07-09 UTC) o NY (13:30-15:30 UTC). Kill zones = mayor probabilidad AMD.';
COMMENT ON COLUMN amd_signals.kill_zone_name IS
  '"London", "NewYork" o "" si fuera de kill zone.';
COMMENT ON COLUMN amd_signals.bars_to_entry IS
  'Barras entre el spike y la barra de distribución/entry. 1=inmediatamente siguiente (setup más limpio).';
COMMENT ON COLUMN amd_signals.spike_extension_pct IS
  '% que el spike extremo superó el límite del rango de acumulación. Mayor extensión = stop sweep más agresivo.';
COMMENT ON COLUMN amd_signals.range_spike_ratio IS
  '|cvd_in_range| / spike_extension_pct. Ratio CVD-por-extensión. Mayor = más absorción institucional por unidad de precio.';
