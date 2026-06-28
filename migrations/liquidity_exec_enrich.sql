-- Enriquecer el registro del ejecutor para análisis/backtest post-trade completo.
-- Correr en el SQL editor de Supabase.

-- 1) liquidity_exec_trades: campos ricos (paridad con paper + ejecución real)
alter table public.liquidity_exec_trades add column if not exists reason text;          -- stop|target|trail
alter table public.liquidity_exec_trades add column if not exists level  double precision; -- nivel intencional (slippage de entrada = entry - level)
alter table public.liquidity_exec_trades add column if not exists tp1    double precision;
alter table public.liquidity_exec_trades add column if not exists qty    double precision; -- tamaño de la posición
alter table public.liquidity_exec_trades add column if not exists mfe_r  double precision; -- máx a favor (R) durante la vida de la posición
alter table public.liquidity_exec_trades add column if not exists mae_r  double precision; -- máx en contra (R)
alter table public.liquidity_exec_trades add column if not exists fee_r  double precision; -- fee real en R (bruto - neto)
alter table public.liquidity_exec_trades add column if not exists bar_ts bigint;          -- ts de la barra-señal (link con liquidity_paper_trades.ts)

-- 2) liquidity_exec_open_pos: MFE/MAE persistidos para que sobrevivan un redeploy
alter table public.liquidity_exec_open_pos add column if not exists seen_hi double precision;
alter table public.liquidity_exec_open_pos add column if not exists seen_lo double precision;
alter table public.liquidity_exec_open_pos add column if not exists bar_ts  bigint;

-- 3) fill ratio REAL: snapshot por barra (placed vs filled acumulados)
create table if not exists public.liquidity_exec_snapshots (
    id          bigint generated always as identity primary key,
    at          timestamptz not null default now(),
    symbol      text,
    placed_cum  bigint,
    filled_cum  bigint,
    fill_ratio  double precision,
    open_pos    int
);
create index if not exists idx_liq_exec_snap on public.liquidity_exec_snapshots (symbol, at desc);
