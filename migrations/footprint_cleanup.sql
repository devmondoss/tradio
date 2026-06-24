-- Cleanup del footprint — mantiene solo las últimas N barras por símbolo.
-- El monitor solo restaura 200 barras al arrancar (load_footprint), así que
-- guardar más es desperdicio. Sin cleanup, la tabla crece ~110 MB/año (3 símbolos
-- × 96 barras/día × ~1 KB). Con cleanup queda en ~1 MB constante (300 × 3 ≈ 900 filas).
--
-- Uso manual: correr en el SQL Editor cuando se quiera (es idempotente).
-- Automatizable con pg_cron (ver abajo).

-- ── Borrar todo lo que NO esté en las últimas 300 barras de cada símbolo ──
DELETE FROM liquidity_paper_footprint t
USING (
    SELECT ts_ms, symbol, tf,
           row_number() OVER (PARTITION BY symbol, tf ORDER BY ts_ms DESC) AS rn
    FROM liquidity_paper_footprint
) ranked
WHERE t.ts_ms = ranked.ts_ms
  AND t.symbol = ranked.symbol
  AND t.tf = ranked.tf
  AND ranked.rn > 300;

-- ── (opcional) Automatizar cada hora con pg_cron ──────────────────────────────
-- Requiere la extensión pg_cron habilitada en Supabase (Database → Extensions).
-- Descomentar para programar:
--
-- select cron.schedule(
--   'footprint-cleanup',
--   '0 * * * *',   -- cada hora en punto
--   $$
--     DELETE FROM liquidity_paper_footprint t
--     USING (
--       SELECT ts_ms, symbol, tf,
--              row_number() OVER (PARTITION BY symbol, tf ORDER BY ts_ms DESC) AS rn
--       FROM liquidity_paper_footprint
--     ) ranked
--     WHERE t.ts_ms = ranked.ts_ms AND t.symbol = ranked.symbol
--       AND t.tf = ranked.tf AND ranked.rn > 300;
--   $$
-- );
