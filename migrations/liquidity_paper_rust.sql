-- Columnas adicionales para el port Rust (regime, gestion, system).
-- Correr en Supabase SQL Editor antes del primer deploy Rust.

ALTER TABLE liquidity_paper_trades
    ADD COLUMN IF NOT EXISTS regime  TEXT,
    ADD COLUMN IF NOT EXISTS gestion TEXT,
    ADD COLUMN IF NOT EXISTS system  TEXT;

ALTER TABLE liquidity_paper_events
    ADD COLUMN IF NOT EXISTS regime  TEXT,
    ADD COLUMN IF NOT EXISTS gestion TEXT;

-- fill_ratio_view actualizada: incluye regime y gestion como dimensiones de segmentación
CREATE OR REPLACE VIEW liquidity_paper_fill_ratio AS
SELECT
    symbol, tf, vol_regime, regime, gestion,
    count(*) FILTER (WHERE event_type='place') AS placed,
    count(*) FILTER (WHERE event_type='fill')  AS filled,
    round(count(*) FILTER (WHERE event_type='fill')::numeric
          / nullif(count(*) FILTER (WHERE event_type='place'), 0), 4) AS fill_ratio
FROM liquidity_paper_events
GROUP BY symbol, tf, vol_regime, regime, gestion;
