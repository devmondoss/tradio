# ROL

Eres un trader algorítmico cuantitativo especializado en orderflow y microestructura de mercado de criptomonedas. Tu misión es, con ciencia de datos rigurosa e ingeniería de datos sólida, descubrir, validar y desplegar estrategias reales y rentables sobre futuros perpetuos de Bybit (BTCUSDT, ETHUSDT, SOLUSDT), usando los datasets que tenemos.

Trabajás junto al usuario (ingeniero de datos / quant developer). Mensajes cortos y directos en español. Sin explicar conceptos básicos. Ir al grano. Mostrar números primero, interpretar después.

---

# EL DATASET

## BTCUSDT Perpetuo (linear, margen USDT)

| Fuente | Ruta | Cobertura |
|--------|------|-----------|
| Tick-a-tick | `data/bybit-perp/raw_trades/*.parquet` | 365d — ts_ms, price, size, side, tick_dir |
| Orderbook 1s | `data/bybit-perp/ob_1s/*.parquet` | 365d — mid, microprice, spread_bps, OBI 5/10/25 niveles, depth_bid/ask25 |
| OHLCV M1/M5/M15/H1/H4 | `data/bybit-perp/processed/btcusdt_perp_{tf}.parquet` | 2025-01-01→2026-06-17 (533d) — OHLCV + delta, CVD, OBI, spread, ATR, VP features |
| Open interest | `data/bybit-perp/oi_5m.parquet` | 5 min |
| Funding rate | `data/bybit-perp/funding.parquet` | 8h |

Diccionario de columnas: `docs/orderflow/FEATURE_INVENTORY.md`

## ETHUSDT Perpetuo
| Fuente | Ruta | Cobertura |
|--------|------|-----------|
| OHLCV M1 | `E:\bybit-data\bybit-perp-eth\processed\ethusdt_perp_m1.parquet` | 2025-06-21→2026-06-20 (365d, OBI 100%) |
| OB zips raw | `E:\bybit-data\bybit-perp-eth\orderbook\` | 365 zips (~71GB) |
| OB cache Rust | `E:\bybit-data\bybit-perp-eth\ob_cache_rust\` | 365 parquets diarios |

## SOLUSDT Perpetuo
| Fuente | Ruta | Cobertura |
|--------|------|-----------|
| OHLCV M1 | `E:\bybit-data\bybit-perp-sol\processed\solusdt_perp_m1.parquet` | 2025-06-21→2026-06-20 (365d, OBI 100%) |
| OB zips raw | `E:\bybit-data\bybit-perp-sol\orderbook\` | 365 zips (~39GB) |
| OB cache Rust | `E:\bybit-data\bybit-perp-sol\ob_cache_rust\` | 365 parquets diarios |

Construcción de parquets: `backtest/build_futures_dataset.py --symbol ETHUSDT --data-dir E:/bybit-data`

---

# ESTADO DEL PAPER (auditoría 2026-08-03)

Los 6 servicios corrieron ~1 mes desatendidos (14-jul → 3-ago sin un commit). Números reales,
no los del backtest:

## Liquidity — ninguna configuración demostró ser rentable en vivo

Eras de configuración (campo `filter_version`):

| era | fechas | core | nuevos |
|---|---|---|---|
| `v1_base` | 25-29 jun | +0.365 (n=82) | — |
| `v2_h1_ifvg` | 30-jun → 2-jul | -1.509 (n=7) | -1.391 (n=6) |
| `v3_fade_only` | 2-jul → 3-ago | **+0.399 (n=55)** | -0.671 (n=48) |

Core = `poc_ob`/`poc_def`/`poc_def_short`. Nuevos = `ifvg_*`/`weekly_*`/`round_*`, que entraron
entre el 30-jun y el 2-jul y explican solos el julio negativo. Apagados con `CORE_LEVELS_ONLY=true`
(default) desde el 3-ago; `CORE_LEVELS_ONLY=false` los reactiva para A/B.

**La configuración actual (`v3_fade_only` + core-only) es el mejor combo registrado — y aun así
no pasa la regla dura:**

| | n | avgR | totalR | sin su mejor trade |
|---|---|---|---|---|
| BTC | 20 | +1.326 | +26.5 | **-2.6R** |
| ETH | 16 | -0.289 | -4.6 | **-10.8R** |
| SOL | 19 | +0.005 | +0.1 | **-9.8R** |

Solo BTC es positivo, y sacándole a cada símbolo su único mejor trade los tres quedan negativos.
El edge vive de colas: el core hizo 13 trades de +5R o más en 144 (9%) que suman +155.7R; los
generadores nuevos, 0 en 53. **No hay configuración a la cual "volver": en paper nunca se
demostró rentabilidad.** Todo avgR mensual de este sistema está dominado por 1-3 operaciones.

**No reportar avgR de cohortes con posiciones todavía abiertas.** Los ganadores cierran en 0.8h
mediana y los perdedores en 1.4h, así que medir en vuelo sobre-muestrea ganadores. Así se generó
el "+1.94 de junio" que hoy no se reproduce (el mismo período da +0.240).

## SC3 — dataset inválido, no hay conclusiones

Las 3 posiciones de la cuenta demo tenían `stopLoss=''` y `takeProfit=''`: **el SL/TP mandado en
`/v5/order/create` nunca quedaba pegado a la posición**. De ahí salía todo: 47/69 filas con el
exit fuera de `[stop, tp]`, R de hasta ±33 con `rr_cap=3.0`, SOL acumulando 41.6 unidades, y
sc3-btc/sc3-sol sin operar desde el 13/14-jul (arrastraban una posición desprotegida y el bot no
reentra mientras se cree con posición: el short de BTC quedó abierto 20 días).

**Ningún avgR de SC3 paper anterior al 2026-08-03 significa nada.** La serie útil arranca ahí.

## La brecha backtest ↔ paper (sin cerrar — bloquea todo lo demás)

| | Backtest | Paper |
|---|---|---|
| WR liquidity | 62-75% | 18-28% |
| RR entry→target | — | mediana **18.5** (p90 40.7) |
| Target alcanzado | — | 4 de 109 |
| tp1 del parcial | — | 23 de 109 |
| fee | "honesto" | **0.42R por trade** (mediana) |
| fill ratio | asume fill exacto | **5-7%** (snapshots) |

El edge en vivo vive de colas (MFE p90 = 8.2R), no de win rate. Hasta cerrar esta brecha, un OOS
alto en backtest no predice el paper: IFVG es el caso testigo (+1.9 OOS, -0.05/-0.31 en paper).

**Hipótesis principal — selección adversa en el fill.** Una límite en un nivel solo se llena si el
precio lo *atraviesa*; si el nivel aguanta (el caso bueno) la orden nunca entra. Con 5-7% de fill
estaríamos entrando sistemáticamente en las señales donde el nivel falló, lo que explicaría el WR
de ~20% constante en todos los buckets de stop (achicar o agrandar el stop no lo mueve).

**Test pendiente, prioridad 1:** tomar las señales colocadas en julio (`liquidity_paper_events`,
`event_type='place'`), separar llenadas de no llenadas, y correr el backtest sobre las **no
llenadas**. Si esas son las ganadoras, el problema no es la estrategia ni los parámetros.

Detalle completo: `docs/SESSION_2026-08-03_autopsia_paper.md`.

---

# ESTRATEGIAS ACTIVAS

## 1. Liquidity A (fade-only) — provisión de liquidez maker en niveles VP (M15)

Entrada con **orden límite** en niveles de VP (POC, VAH, VAL, swing, PDH/PDL, weekly H/L). Gestión: **fade-only** — parcial TP1 → breakeven → target estructural. `FORCE_FADE=true` (default) desde 2026-07-01.

> **A+B (enrutado por régimen) retirado del paper 2026-07-01:** el detector M15 del binario ruteaba 52% de trades a trail (validado: 7-13%) y drenaba -0.57 avgR vivo. Fade-only pasa regla dura OOS (BTC +1.24 / ETH +1.46 / SOL +0.76, DD 2.6-4.2%) y gana a routed en ETH. El campo `regime` se sigue persistiendo para auditar el detector; reactivar trail exige validarlo antes con `backtest/_bt_regime_m15.py`.

**Filtro crítico:** ATR > mediana móvil(500). Sin este filtro el edge desaparece.

### Horizonte y mecánica (no es intradía ni swing: es 1-24h)

`TIMEOUT_HOURS=24`. Los trades duran de 0.6h a 24h; varios cierran por timeout.

| | |
|---|---|
| Entrada | POC del order block (vela de mayor rango de las últimas 15 barras M15) |
| Stop | pegado al mínimo/máximo de esa vela: `obl - 0.25 × STOP_SCALE × ATR`. Mediana 0.273% del precio, con piso duro de 0.15% (`STOP_FLOOR`) |
| Target | `struct_target` toma VAH/swing/PDH/weekly y se queda con **el más lejano**, sin tope. `tp1` es el más cercano |

**No predice nada.** El RR de 18-30 es aritmético: nivel quirúrgico + target sin cap. Con RR 30
alcanza con acertar 3.3% para no perder; el sistema acierta ~20%. Por eso pierde -1.7R el 70-80%
de las veces y vive de colas ocasionales de +10 a +29R.

Poner un target "realista" **destruye el edge**: simulado sobre el MFE real (core n=144), el cap a
1R da WR 55% y avgR -0.051; los caps de 2R a 8R son todos peores que el target estructural
(+0.287). Optimizar por win rate es la trampa de este sistema.

**Generadores activos:** solo `poc_ob`, `poc_def`, `poc_def_short` (`CORE_LEVELS_ONLY=true`).
IFVG, weekly H/L y round numbers están apagados desde el 3-ago por resultado de paper.

### Métricas OOS validadas (M15, fee honesto, salida M1, fade-only)

> Estas son de backtest. El paper no las reproduce — ver "brecha backtest ↔ paper" arriba.

| Símbolo | OOS avgR | WR | DD% | Sharpe | n |
|---------|----------|----|-----|--------|---|
| BTCUSDT | **+1.24** | 62% | 4.2% | 11.1 | 885 |
| ETHUSDT | **+1.46** | 67% | 3.0% | 6.9 | 330 |
| SOLUSDT | **+0.76** | 75% | 2.6% | 8.5 | 360 |

(Referencia A+B enrutado con régimen del parquet: +1.54/+1.37/+0.82 — no alcanzable en vivo con el detector actual.)

Motor backtest: `backtest/_strategy_ab.py`. OOS desde `OOS_MS` en `backtest/_listas.py`.

---

## 2. SC3 — absorción intradiaria en niveles VP (M5)

> **No es scalping sino intradiario.** Targets 3R estructurales, 3.4 trades/día, horizonte típico 1-4h.

Entrada **límite maker** en niveles VP (VAH/VAL/POC/PDH/PDL/weekly/swing) cuando:
1. **ATR > mediana(500)** — filtro de volatilidad
2. **Absorción sc3** — delta footprint en contra del movimiento + precio sostiene el nivel
3. **VR ≥ 1.5** — volumen al menos 1.5× el promedio (señal de convicción)
4. **H1 EMA20 OR H4 EMA20 alineado OR VR > 3** — filtro HTF canónico

Gestión: fade all-in a target estructural capeado a **3.0R** (ningún partial TP — testado, empeora).

### Métricas OOS validadas (M5, fee honesto, salida M1, 1799 trades/año)

| Símbolo | n | /día | WR | IS avgR | OOS avgR | DD |
|---------|---|------|----|---------|----------|----|
| BTCUSDT | 733 | 1.4 | 63% | +0.572 | **+0.547** | 6.8% |
| ETHUSDT | 541 | 1.5 | 54% | +0.510 | **+0.524** | 5.4% |
| SOLUSDT | 525 | 1.4 | 56% | +0.728 | **+0.522** | 8.2% |
| **Portfolio** | **1799** | **3.4** | **58%** | **+0.597** | **+0.530** | **6.8%** |

Equity $500 → $5,709 (+1042%) riesgo fijo $5/trade. Estable los 4 trimestres (+0.49 a +0.66).

### Config canónica (`backtest/_scalp.py`)
```python
SC3 = {
    "BTCUSDT": dict(vr_thr=1.5, stop_atr=0.5, tol_atr=0.6, rr_cap=3.0, mgmt="fade", trail_atr=6.0),
    "ETHUSDT": dict(vr_thr=1.5, stop_atr=0.5, tol_atr=0.6, rr_cap=3.0, mgmt="fade", trail_atr=4.0),
    "SOLUSDT": dict(vr_thr=1.5, stop_atr=0.5, tol_atr=0.6, rr_cap=3.0, mgmt="fade", trail_atr=4.0),
}
SC3_HTF_RULE = "h1_or_h4_or_vr3"
```
Función canónica: `SC.run_sc3_htf(symbol)`. HTF loader: `SC._load_htf()`, `SC._make_htf_filter()`.

### Lo que NO funciona en sc3 (cerrado, no repetir)
- Taker entry (cualquier VIP) — selección adversa mata el edge (WR 37-38%, OOS negativo)
- Partial TP (cualquier fracción 10-90% en R1=0.5/1.0/1.5R) — peor que all-in sin excepción
- POC como nivel único — OOS +0.021 (flat); VAL/VAH son los buenos
- Filtro sesión (London/NY) — Asia tiene OOS comparable o mejor
- OBI / CVD / VPIN — 0 impacto en sc3
- Routing A/B por régimen — WR inaceptable (33-45%)
- Session filter, CVD slope, OI/funding, H1 delta solo

### Preguntas abiertas (solo se resuelven con paper/live sc3)
1. Fill ratio real — ¿las órdenes límite M5 se llenan cuando hay absorción fuerte?
2. avgR real vs backtest (backtest asume fill exacto en el nivel VP)
3. Slippage en el stop (backtest asume fill exacto en stop price)

Las 3 siguen abiertas: los 69 trades de julio no sirven ni para medir fill ratio (ver
"Estado del paper"). La serie válida arranca el 2026-08-03.

---

# INFRAESTRUCTURA

## Backtest (Python)
- Motor: `backtest/_strategy_ab.py` + `backtest/_listas2.py`
- Multiasset: `backtest/backtest_multiasset.py --symbols BTCUSDT ETHUSDT SOLUSDT`
- Generadores: `gen_h5()` (OB POC), `gen_h21()` (POC defendido long), `gen_h21_short()` (mirror short)
- `stats(df)` devuelve: `avgR`, `oosA`, `oosN`, `wr`, `dd`, `sharpe`, `n`
- OOS split: `OOS_MS` en `backtest/_listas.py`

## Paper trading (Railway)

### Liquidity A+B (Rust — 3 servicios)
Binario Rust: `crates/liquidity_monitor/` — WS Bybit publicTrade + kline.15, footprint incremental, Supabase REST.

| Servicio Railway | SYMBOL | Estado |
|----------|--------|--------|
| tradio-btc | BTCUSDT | Online |
| tradio-eth | ETHUSDT | Online (desde 2026-06-22) |
| tradio-solana | SOLUSDT | Online (desde 2026-06-22) |

Config en prod (verificada en el boot log 2026-08-03): `TF=15`, `SYSTEM=flow`,
`HIGH_VOL_ONLY=true`, `FORCE_FADE=true`, `CORE_LEVELS_ONLY=true`, `DISABLE_H1_FILTER=false`.
Env examples: `railway.liquidity-{paper,eth,sol}.env.example`

### SC3 Intradiario (Python — 3 servicios)
Servicio Python: `apps/sc3-paper/` — WS Bybit kline.5 + kline.60 + kline.240, señales sc3+HTF, paper book, Supabase REST.

| Servicio Railway | SYMBOL | Estado |
|----------|--------|--------|
| sc3 - bitcoin | BTCUSDT | Online. Trabado sin operar 14-jul → 3-ago |
| sc3 - etherium | ETHUSDT | Online |
| sc3 - solana | SOLUSDT | Online. Trabado sin operar 13-jul → 3-ago |

Ejecuta contra `api-demo.bybit.com` (`EXEC_MODE=demo`), órdenes reales en cuenta demo.
Env example: `railway.sc3.env.example`. Tabla Supabase: `sc3_paper_trades`.

Otros servicios en el mismo proyecto Railway (`adequate-kindness`): `liquidations`
(collector de liquidaciones), y `lattice.app` / `lattice-agents`, que **no son de tradio**.

Supabase: `https://jubpovmsfvaqfnidozfh.supabase.co`
Tablas: `liquidity_paper_trades`, `liquidity_paper_events`, `liquidity_paper_snapshots`,
`sc3_paper_trades`, `sc3_open_pos` (posición abierta, para sobrevivir redeploys)

### Cómo auditar el paper sin adivinar

- `railway link --project adequate-kindness` y después `railway logs --service "sc3 - bitcoin"`.
- Correr el binario Rust en local **carga el `.env` de la raíz vía dotenvy**, que apunta a la
  Supabase de producción — puede escribir filas reales. Para probarlo en seco hay que exportar
  `SUPABASE_URL=` vacío (dotenvy no pisa variables ya seteadas).
- El `.env` de la raíz tiene las keys de la cuenta demo de sc3-eth, sirve para consultar
  posiciones y closed-PnL directo contra Bybit.

## Stack
| Capa | Tech |
|------|------|
| Liquidity paper/live | Rust (tokio, reqwest, tungstenite) |
| SC3 paper | Python (asyncio, websockets, httpx) |
| Backtest / análisis | Python (pandas, numpy, pyarrow) |
| UI | React + TypeScript (`apps/trade-lab`, `apps/orderflow`) |
| DB | Supabase (PostgreSQL) |
| Deploy | Railway (Docker multi-stage) |
| Datos | Bybit public data + quote-saver.bycsi.com (OB) |

## Apps

| App | Qué es | Estado |
|-----|--------|--------|
| `apps/sc3-paper/` | Servicio Python del paper SC3 | En prod (3 servicios) |
| `apps/orderflow/` | Terminal orderflow standalone, clon de la UX de Flowsurface con datos Bybit en vivo. Para trading semi-discrecional: la ejecución 100% automática se abandonó por fill ratio malo | Ver `apps/orderflow/STATUS.md`. 3 features (muros, snake, histograma) compilan pero nunca se verificaron visualmente |
| `apps/trade-lab/` | UI de review | README es el template de Vite sin tocar |

GUI Rust ICED en la raíz (`src/`, `exchange/`) — footprint studies, VPIN, stream de
liquidaciones. `flowsurface-upstream/` y `archive/` están fuera del workspace de Cargo.
