-- liquidity_paper_footprint
-- Persiste el footprint (VP real por ticks) de cada barra M15.
-- Sobrevive reinicios de Railway: on_bar_close() inserta aquí,
-- el arranque restaura las últimas 200 filas al FootprintAccumulator.
--
-- Almacenamiento estimado:
--   ~300-600 bins activos/barra × ~16 bytes JSON ≈ 8-12 KB/barra
--   200 barras ≈ 2-3 MB (dentro del limite 500 MB de Supabase)
--
-- Ejecutar en Supabase SQL Editor (proyecto jubpovmsfvaqfnidozfh).

CREATE TABLE IF NOT EXISTS liquidity_paper_footprint (
  ts_ms   BIGINT        PRIMARY KEY,          -- timestamp inicio de barra (ms)
  symbol  TEXT          NOT NULL DEFAULT 'BTCUSDT',
  tf      TEXT          NOT NULL DEFAULT '15',
  poc     FLOAT8        NOT NULL,             -- precio de mayor volumen real
  delta   FLOAT8        NOT NULL,             -- buy_vol - sell_vol de toda la barra
  vol     FLOAT8        NOT NULL,             -- volumen total
  -- Arrays sparse: solo bins con volumen (NO se guarda total = buy+sell, se recalcula)
  prices  JSONB         NOT NULL,             -- [64000.0, 64005.0, ...]
  buy     JSONB         NOT NULL,             -- [1.23, 0.0, 4.56, ...]
  sell    JSONB         NOT NULL,             -- [0.88, 1.10, 2.34, ...]
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_fp_symbol_tf_ts
  ON liquidity_paper_footprint (symbol, tf, ts_ms DESC);

-- Vista: cuántas barras guardadas y cuánto espacio aproximado ocupan
CREATE OR REPLACE VIEW liquidity_paper_footprint_stats AS
SELECT
  symbol,
  tf,
  COUNT(*)                                               AS n_bars,
  MIN(to_timestamp(ts_ms / 1000))                        AS oldest_bar,
  MAX(to_timestamp(ts_ms / 1000))                        AS newest_bar,
  ROUND(SUM(pg_column_size(prices) +
            pg_column_size(buy) +
            pg_column_size(sell)) / 1024.0, 1)           AS kb_used
FROM liquidity_paper_footprint
GROUP BY symbol, tf;
