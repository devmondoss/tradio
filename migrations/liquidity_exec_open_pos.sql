-- Estado de la posición abierta del ejecutor (1 fila por símbolo) para sobrevivir redeploys.
-- El ejecutor la guarda al abrir y la borra al cerrar; al arrancar la restaura y sigue gestionando.
create table if not exists public.liquidity_exec_open_pos (
    symbol        text primary key,
    tf            text,
    side          text,
    kind          text,
    gestion       text,
    vol_regime    text,
    regime        text,
    fp_source     text,
    price         double precision,   -- nivel
    stop          double precision,
    tp1           double precision,
    tp            double precision,
    atr           double precision,
    atr_median    double precision,
    take_partial  boolean,
    entry         double precision,   -- precio de fill real
    fill_ts       bigint,
    ttf_s         bigint,
    exits_armed   boolean,
    updated_at    timestamptz not null default now()
);
