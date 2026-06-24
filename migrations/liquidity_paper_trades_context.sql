-- Observabilidad completa por trade: contexto de entrada, gestión y salida.
-- Permite auto-explicar y BACKTESTEAR cada paper trade (replay contra el tape).
-- Correr en Supabase ANTES de desplegar el monitor con estos campos.
alter table liquidity_paper_trades
  add column if not exists fp_source         text,              -- "tick" (footprint real) | "ohlcv" (midpoint)
  add column if not exists tp1               double precision,  -- nivel del parcial
  add column if not exists atr               double precision,  -- ATR al entrar
  add column if not exists atr_median        double precision,  -- mediana ATR(500) → atr/atr_median = fuerza filtro vol
  add column if not exists take_partial      boolean,
  add column if not exists placed_ts         bigint,            -- ms cuando se colocó la orden
  add column if not exists time_to_fill_s    double precision,  -- opened_at - placed_ts (segundos)
  add column if not exists filled1           boolean,           -- ¿tocó el parcial TP1?
  add column if not exists realized_r        double precision,  -- R bancado del parcial
  add column if not exists fee_r             double precision,  -- fees en R (bruto = result_r + fee_r)
  add column if not exists mfe_r             double precision,  -- máx excursión a favor (R)
  add column if not exists mae_r             double precision,  -- máx excursión en contra (R)
  add column if not exists bar_delta_at_exit double precision;  -- order-flow al salir
