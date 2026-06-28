-- Trades REALES del ejecutor de liquidity (demo/live). Tabla separada de las de paper.
-- Idempotente: renombra la vieja (liquidity_testnet_trades) si existe, o crea la nueva.
-- Correr en el SQL editor de Supabase.

alter table if exists public.liquidity_testnet_trades rename to liquidity_exec_trades;

create table if not exists public.liquidity_exec_trades (
    id              bigint generated always as identity primary key,
    created_at      timestamptz not null default now(),
    symbol          text not null,
    tf              text,
    kind            text,          -- poc_ob | poc_def | poc_def_short
    side            text,          -- long | short
    gestion         text,          -- fade | trail
    vol_regime      text,          -- high | low
    regime          text,          -- chop | trend
    entry           double precision,
    stop            double precision,
    target          double precision,
    exit_price      double precision,
    pnl_usdt        double precision,   -- P&L realizado del exchange (closed-pnl)
    result_r        double precision,   -- R = (exit-entry)/riesgo, signo por lado
    win             boolean,
    time_to_fill_s  bigint,             -- segundos desde colocar la PostOnly hasta el fill REAL
    atr             double precision,
    opened_at       timestamptz,
    closed_at       timestamptz
);

create index if not exists idx_liq_exec_symbol_created
    on public.liquidity_exec_trades (symbol, created_at desc);
