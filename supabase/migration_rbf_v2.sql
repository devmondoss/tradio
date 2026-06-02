-- RBF signals v2: agregar 5 campos de contexto adicionales
-- Ejecutar en Supabase SQL Editor

ALTER TABLE rbf_signals
  ADD COLUMN IF NOT EXISTS range_touch_count  INTEGER,
  ADD COLUMN IF NOT EXISTS session_phase      TEXT,
  ADD COLUMN IF NOT EXISTS price_vs_vwap_pct  DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS funding_at_entry   DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS liq_ratio_pre      DOUBLE PRECISION;

-- Actualizar vista resumen para incluir session_phase
CREATE OR REPLACE VIEW v_rbf_summary AS
SELECT
    direction,
    session,
    session_phase,
    macro_regime,
    COUNT(*)                                               AS total,
    COUNT(*) FILTER (WHERE status = 'OPEN')                AS open,
    COUNT(*) FILTER (WHERE result_r >= 1.0)                AS wins,
    COUNT(*) FILTER (WHERE result_r < 0)                   AS losses,
    ROUND(AVG(result_r)::NUMERIC, 3)                       AS avg_r,
    ROUND(AVG(range_pct)::NUMERIC, 4)                      AS avg_range_pct,
    ROUND(AVG(vr_at_breakout)::NUMERIC, 2)                 AS avg_vr,
    ROUND(AVG(cvd_in_range)::NUMERIC, 1)                   AS avg_cvd,
    ROUND(AVG(range_touch_count)::NUMERIC, 1)              AS avg_touches,
    ROUND(AVG(price_vs_vwap_pct)::NUMERIC, 3)              AS avg_price_vs_vwap
FROM rbf_signals
GROUP BY direction, session, session_phase, macro_regime
ORDER BY direction, session;
