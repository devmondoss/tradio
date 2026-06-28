-- Enriquecer el registro del ejecutor para análisis/backtest post-trade completo.
-- Autocontenido + idempotente. Correr en el SQL editor de Supabase.

-- 0) tabla de posición abierta del ejecutor (crear si no existe, con todos los campos)
create table if not exists public.liquidity_exec_open_pos (
    symbol        text primary key,
    tf            text,
    side          text,
    kind          text,
    gestion       text,
    vol_regime    text,
    regime        text,
    fp_source     text,
    price         double precision,
    stop          double precision,
    tp1           double precision,
    tp            double precision,
    atr           double precision,
    atr_median    double precision,
    take_partial  boolean,
    entry         double precision,
    fill_ts       bigint,
    ttf_s         bigint,
    exits_armed   boolean,
    seen_hi       double precision,
    seen_lo       double precision,
    bar_ts        bigint,
    updated_at    timestamptz not null default now()
);

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
