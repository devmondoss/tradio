-- SC3 paper trades + persistencia de posición abierta
-- Ejecutar en Supabase SQL Editor (Dashboard → SQL)

-- ── Tabla principal de trades ─────────────────────────────────────────────────
create table if not exists sc3_paper_trades (
  id         bigserial primary key,
  created_at timestamptz default now(),

  symbol     text    not null,
  side       text    not null check (side in ('long','short')),
  tag        text,               -- nivel VP (val/vah/poc/pdh/pdl/wh/wl/swh/swl)
  htf_filter text,               -- qué filtro HTF disparó: h1 | h4 | h1_h4 | vr3 | h1_vr3…
  entry      numeric not null,
  stop       numeric not null,
  tp         numeric not null,
  exit_px    numeric not null,
  r          numeric not null,   -- resultado en R (neto con fee estimado)
  result_r   numeric,            -- alias consistente con liquidity_paper_trades
  win        boolean,
  reason     text    not null check (reason in ('stop','target','timeout')),

  vr         numeric,            -- volume ratio en la barra de señal
  atr        numeric,            -- ATR en la barra de señal

  ts_open       bigint not null, -- ts_ms barra de señal (Unix ms)
  fill_ts_ms    bigint,          -- ts_ms real del fill (0 = desconocido)
  ts_close      bigint not null, -- ts_ms barra de cierre (Unix ms)
  time_to_fill_s integer         -- segundos de placed→fill
);

-- Añadir columnas si ya existía la tabla (idempotente)
alter table sc3_paper_trades add column if not exists htf_filter    text;
alter table sc3_paper_trades add column if not exists result_r      numeric;
alter table sc3_paper_trades add column if not exists win           boolean;
alter table sc3_paper_trades add column if not exists fill_ts_ms    bigint;
alter table sc3_paper_trades add column if not exists time_to_fill_s integer;

-- Índices para UI queries
create index if not exists sc3_paper_trades_symbol_ts on sc3_paper_trades(symbol, ts_open);
create index if not exists sc3_paper_trades_ts_close  on sc3_paper_trades(ts_close);

-- RLS
alter table sc3_paper_trades enable row level security;
drop policy if exists "allow read"           on sc3_paper_trades;
drop policy if exists "allow insert service" on sc3_paper_trades;
create policy "allow read"           on sc3_paper_trades for select using (true);
create policy "allow insert service" on sc3_paper_trades for insert with check (true);

-- ── Posición abierta (sobrevive redeploys) ────────────────────────────────────
create table if not exists sc3_open_pos (
  id         bigserial primary key,
  created_at timestamptz default now(),

  symbol       text    not null unique,  -- PK lógica: 1 fila por símbolo
  side         text    not null,
  entry        numeric not null,
  stop         numeric not null,
  tp           numeric not null,
  tag          text,
  htf_filter   text,
  vr           numeric,
  atr          numeric,
  ts_open      bigint,                   -- ts barra de señal
  placed_ts    bigint,                   -- ts colocación orden
  fill_ts_ms   bigint                    -- ts real del fill
);

alter table sc3_open_pos enable row level security;
drop policy if exists "allow all service" on sc3_open_pos;
create policy "allow all service" on sc3_open_pos for all using (true) with check (true);

-- ── Añadir filter_version a liquidity_paper_trades ────────────────────────────
-- (separar v1 base de v2 H1+dist+IFVG, deployado 2026-06-29)
alter table liquidity_paper_trades add column if not exists filter_version text default 'v1_base';

-- Marcar trades existentes como v1
update liquidity_paper_trades set filter_version = 'v1_base' where filter_version is null;
