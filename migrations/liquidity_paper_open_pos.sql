-- Posiciones abiertas del PaperBook — persistencia para sobrevivir redeploys.
-- Sin esto, cada reinicio de Railway pierde las posiciones en RAM y nunca se
-- cierran (fills huérfanos en events, sin ClosedTrade). Ver crates/liquidity_monitor.
--
-- Estrategia de escritura: delete-all + insert del set vivo por (symbol, tf, system)
-- en cada bar_close. open_pos es chico (0-10 filas), así que es barato.

create table if not exists liquidity_paper_open_pos (
    id                 bigint generated always as identity primary key,
    symbol             text not null,
    tf                 text not null,
    system             text not null,
    -- ── Level embebido ──
    side               text not null,
    kind               text not null,
    price              double precision not null,
    lvl_stop           double precision not null,
    tp1                double precision,            -- nullable
    tp                 double precision not null,
    vol_regime         text not null,
    regime             text not null,
    gestion            text not null,
    atr                double precision not null,
    take_partial       boolean not null,
    fp_source          text not null,
    -- ── Estado OpenPos ──
    entry              double precision not null,
    fill_ts            bigint not null,
    bar_delta_at_fill  double precision not null,
    cur_stop           double precision not null,
    realized           double precision not null,
    rem                double precision not null,
    filled1            boolean not null,
    best_price         double precision not null,
    trail_stop         double precision not null,
    scale2_price       double precision not null,
    scale2_filled      boolean not null,
    effective_entry    double precision not null,
    updated_at         timestamptz default now()
);

create index if not exists idx_open_pos_lookup
    on liquidity_paper_open_pos (symbol, tf, system);
