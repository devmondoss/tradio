# Archivo MTF — docs históricos (NO son el estado vigente)

> Archivado 2026-06-19. **Fuente de verdad vigente: [`../../mtf/MTF_SISTEMA.md`](../../mtf/MTF_SISTEMA.md)**
> (sistema futuros Bybit perp, BTCUSDT único, M1).

Estos documentos describen dos eras previas del sistema MTF que ya **no** reflejan cómo
opera ni qué edge tiene. Se conservan por su razonamiento de research, no como spec activa.
Si contradicen `MTF_SISTEMA.md`, manda `MTF_SISTEMA.md`.

## Era 1 — multi-símbolo Binance linear (06-12 → 06-15)

Cinco símbolos (BTC/ETH/SOL/BNB/XRP), filtros `oi_momentum` / `funding_regime`, EXP8 regime
filter, tabla `mtf_trades`. Backtests sobre ventanas de 8-14 días recogidas live (muestra chica,
mismo-dato in/out-of-sample). Los WR de 58-78% son in-sample sobre n<200 — no walk-forward.

- `MTF_STRATEGY_RULES.md` — reglas "congeladas" multi-símbolo (era 1)
- `MTF_H4_SHORTS_MINING.md` — mining H4 multi-símbolo
- `MTF_RAILWAY_SERVICES.md` — servicios Railway (monitor-futures Binance + spot)

## Era 2 — BTC SPOT Bybit (06-16 → 06-18)

Migración a un solo símbolo en SPOT. Specs Shorts v5 / Longs v1, "score sizing", capital
$119K/$78K. **Invalidado después por:** (a) bug de fees (el edge estaba inflado, ver worklog
06-18); (b) a fee spot real (0.20%) el sistema pierde → se pivotó a **futuros**. El "score
sizing" resultó ser apalancamiento, no edge (`MTF_SPOT_SIZING_AUDIT.md`).

- `MTF_SPOT_SHORTS_SPEC.md` / `MTF_SPOT_LONGS_SPEC.md` — specs spot
- `MTF_SPOT_SIZING_AUDIT.md` — auditoría que mostró que el sizing es leverage
- `MTF_SPOT_EDGE_REALITY_Y_PLAN.md` — sesión que descubrió el bug de fees y pivotó a futuros
- `MTF_COMPLETION_STATUS.md` / `MTF_PAPER_LIVE_MONITOR.md` — preparación paper live spot

> Nota crítica posterior (06-19): el modelo `directions` que dio "$74.9K" en futuros tenía
> **lookahead intra-hora** en las features H1/H4. Corregido a causal → el edge honesto es fino
> y no desplegable en BTC solo. Ver `MTF_SISTEMA.md` y el worklog `MTF_WORKLOG_2026-06-19_lookahead.md`.
