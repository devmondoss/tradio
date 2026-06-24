-- Liquidaciones en vivo (forward-only — Bybit no publica histórico).
-- Capturadas por live/liquidation_collector.py vía WS allLiquidation.{symbol}.
create table if not exists liquidity_liquidations (
  id          bigserial primary key,
  ts_ms       bigint            not null,   -- timestamp del evento (ms)
  symbol      text              not null,   -- BTCUSDT / ETHUSDT / SOLUSDT
  side        text              not null,   -- S del feed: lado del ORDEN que liquidó.
                                            -- "Sell" => se liquidó un LONG (venta forzada)
                                            -- "Buy"  => se liquidó un SHORT (compra forzada)
  price       double precision  not null,
  qty         double precision  not null,
  usd         double precision  not null,   -- price * qty
  inserted_at timestamptz       default now()
);
create index if not exists idx_liq_sym_ts on liquidity_liquidations (symbol, ts_ms);

-- Vista de cascadas agregadas por minuto (para enchufar al feature lab más adelante).
create or replace view liquidity_liq_1m as
select
  symbol,
  (ts_ms / 60000) * 60000                       as min_ms,
  sum(usd)                                       as usd_total,
  sum(usd) filter (where side = 'Sell')          as usd_longs_liq,   -- longs reventados
  sum(usd) filter (where side = 'Buy')           as usd_shorts_liq,  -- shorts reventados
  count(*)                                       as n
from liquidity_liquidations
group by symbol, (ts_ms / 60000) * 60000;
