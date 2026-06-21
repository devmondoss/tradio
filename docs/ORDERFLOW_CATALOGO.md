# Catálogo COMPLETO de Orderflow — BTC Perpetual (Bybit)

> Actualizado: 2026-06-18 — incorpora barrido IS/OOS + auditoría de gaps.
>
> Fuentes crudas oficiales: **tick trades** (`public.bybit.com/trading/`) y **order book L2**
> (`quote-saver.bycsi.com/orderbook/` — bycsi.com es CDN de Bybit; ob500/ob200 = 200-500 niveles,
> snapshot+delta ~200ms). La sección D requiere API REST de Bybit. La sección Liq requiere colector live.
>
> Estado: ✅ tenemos y validado | ⚠️ tenemos sin validar | 🔨 construyendo | 🆕 podemos construir | 🔌 falta fuente

---

## VEREDICTO — qué tenemos vs qué falta

El catálogo cubre bien:
```
trades ejecutados, delta/CVD, footprint, volume profile,
tape intensity, big trades, L2 básico, absorción, sweep/reclaim
```

Lo que falta o está débil:
```
1. OI / OI delta / OI como régimen
2. Funding como régimen (no solo dato)
3. Mark price, index price, premium/basis
4. Liquidaciones reales históricas y live
5. Event labeling — no solo columnas M1
6. L2 como gestión/salida (no entrada)
7. Simulación realista: slippage, latencia, fills
8. Regímenes explícitos (trend/range/volatile por fuente externa)
9. Validación cruzada: mes, sesión, volatilidad, régimen
10. Setup attribution (qué subtipo paga el edge)
11. Feature decay (¿el edge se degrada?)
12. No-trade engine (vetos explícitos)
13. Target quality (obstáculo entre entry y TP)
14. Lado long como espejo de validación
```

---

## A. DESDE TICK TRADES (footprint / tape reading)

### Tenemos y validados ✅
- `delta`, `cvd`, `cvd_slope`, `dz` (delta z-score), `cvd_div`, `cvd_consec_pos/neg`
- `vr` (volume ratio), `vpin` (toxicidad), `buy_vol`, `sell_vol`
- `big_trade_bearish/bullish`, `stacked_imb` (proxy por barra)
- `n_trades` — intensidad de tape ✅ **tier de sizing** (IS +0.215 / OOS +0.402, ~50% vol)
- `minus_ticks`, `plus_ticks` — tickDirection (agresor nivel tick) ✅ **gate** (2º salto del edge)

### Construyendo 🔨 (`_footprint.py`)
- `fp_poc` — POC de la barra (precio con más volumen DENTRO de la barra)
- `fp_buy_imb` / `fp_sell_imb` — imbalances diagonales (bid ≥ 3× ask del nivel inferior)
- `fp_stack_buy` / `fp_stack_sell` — imbalances apilados (3+ niveles consecutivos)
- `fp_unfinished_high/low` — auctions sin terminar
- `fp_delta_top/bot`, `fp_max/min_lvl_delta` — delta por nivel, extremos

### Podemos construir 🆕 (desde los mismos ticks)
- **Value area de la barra** (rango del 70% del volumen intrabar)
- **Delta excursion** (CVD máx/mín DENTRO de la barra → agotamiento real)
- **Single prints** — niveles con volumen ínfimo = movimiento rápido sin aceptación
- **Trade size histogram** — distribución de tamaños; ballenas vs retail
- **Aggressive ratio** — % de volumen que cruzó el spread (taker agresivo)
- **Effort vs result** — volumen alto + rango chico = absorción; volumen bajo + rango grande = sin resistencia
- **Delta divergence en swings** — precio HH pero delta LH
- **CVD por sesión** (reset diario/sesión)
- **Trade intensity / speed of tape** — trades por segundo (aceleración del flujo)

---

## B. DESDE ORDER BOOK L2 (profundidad)

### Tenemos ✅
- `obi5_mean`, `obi10_mean`, `obi20_mean`, `obi10_min/max`, `obi_range`
- `near5_ask/bid`, `max_ask5/bid5`, `ask_wall`, `bid_wall`, `thin_above/below`, `spread_mean`

### IMPORTANTE: El L2 no filtra entradas — sirve para gestión/salidas

> Resultado del barrido 2026-06-18: OBI/walls suben en IS pero se invierten OOS.
> **No construir OBI profundo como gate de entrada.** Reenfocarlo a salidas y gestión.

Úsalo para:
```
¿dónde reduzco?              ¿hay pared antes del target?
¿dónde muevo stop?           ¿el libro se vacía tras el sweep?
¿hay liquidez que frena?     ¿el target tiene camino limpio?
```

### Features de gestión/salida (construir) 🆕
```
wall_before_tp1              depth_to_target
wall_before_tp2              liquidity_gap_to_target
book_thins_after_entry       book_refills_against_position
spread_expands_after_entry   microprice_against_position
exit_pressure_score          target_obstacle_score
```

### Nota sobre completitud del libro
Bybit no incluye órdenes RPI en mensajes/API de orderbook — el L2 es la mejor vista pública,
no una radiografía completa. Modelar esa incertidumbre en el slippage model.

### Podemos construir (valor bajo como entrada; evaluar para gestión) 🆕
- **OBI profundo** — niveles 50/100/200 (tenemos ob500/ob200; hoy solo usamos 5/10/20)
- **Microprice** — mid ponderado por imbalance (mejor que mid simple)
- **Weighted OBI** — OBI ponderado por distancia al mid
- **Book slope / gradiente de liquidez** — qué tan rápido se adelgaza
- **Order Flow Imbalance (OFI)** — altas vs cancelaciones por nivel (del stream de deltas)
- **Liquidity voids / gaps** — huecos = movimiento rápido
- **Replenishment / queue dynamics** — ¿el libro se rellena tras un sweep?

---

## C. VOLUME PROFILE (composite rolling, parcial)

### Tenemos y validados ✅
- `vp_poc`, `vp_vah`, `vp_val`, `vp_lvn_below`, `above_poc`, `val_near`, `body_below_poc`
- `body_below_poc` ✅ **tier de calidad más robusto** — IS +0.360 / OOS +0.380 (IS≈OOS, ~30% vol)

### Estado de mercado por VP (construir) 🆕
Convertir el VP en **estado de subasta**, no solo columnas aisladas:
```
auction_state          → balance / imbalance / trend
balance_state          → dentro o fuera de value area
value_acceptance_up    → mercado acepta precios más altos
value_acceptance_down  → mercado acepta precios más bajos
failed_auction_high    → intento de breakout que falló arriba
failed_auction_low     → intento de breakdown que falló abajo
poc_migration_up/down  → POC se desplaza = aceptación direccional
value_overlap_prev_session
value_expansion / value_compression
```

### Target quality — obstáculos entre entry y TP 🆕
No preguntar `¿OBI es negativo?`. Preguntar `¿hay obstáculo entre entry y TP1?`:
```
distance_to_vwap               distance_to_lvn
distance_to_vp_poc             distance_to_hvn
distance_to_naked_poc          distance_to_session_low
distance_to_prev_day_low       volume_between_entry_target
liquidity_between_entry_target wall_between_entry_target
```

### Podemos construir 🆕
- **HVN nodes** (High Volume Nodes) — imanes / soporte-resistencia de aceptación
- **LVN above** (solo tenemos below) — zonas de rechazo arriba
- **Naked / virgin POC** — POC no testeado = imán de precio
- **Developing value area** — VA construyéndose intradía (no solo rolling 300b)
- **Profile shape** — P-shape (short cover), b-shape (long liq), D (balance), bimodal
- **Initial balance** — rango de la primera hora (referencia del día)
- **Composite profile** — perfil multi-día (semanal/mensual)
- **VWAP bands** — VWAP ± desviaciones estándar

---

## D. DERIVADOS — OI, FUNDING, PREMIUM, BASIS (PRIORIDAD 1, no opcional)

> Esta es la frontera real. Sin esto el sistema ve agresión pero no sabe si viene de
> nuevos participantes o de gente siendo liquidada. Pequeño detalle.

### Open Interest y OI delta 🔌

Bybit tiene endpoint oficial para OI en contratos USDT/USDC/inversos. Nota oficial:
puede haber latencia en volatilidad extrema — útil, pero no perfecto.

```
open_interest              oi_delta
oi_delta_z                 oi_change_pct
oi_slope                   oi_rising_with_price      → nuevo posicionamiento
oi_rising_against_price    → short squeeze potencial
oi_drop_on_sweep           → liquidación, no posicionamiento
oi_rise_after_reclaim      → confirmación de reclaim real
```

### Funding Rate 🔌

Bybit tiene endpoint histórico de funding rate; cada símbolo puede tener intervalo diferente.

```
funding_rate               funding_zscore
funding_extreme_long       → sesgo crowded long = riesgo reversión
funding_extreme_short      → sesgo crowded short = riesgo squeeze
funding_flip               → cambio de sesgo de mercado
funding_regime             → positivo / negativo / extremo
```

### Mark price, index price, premium, basis 🔌

El ticker público de Bybit incluye `fundingRate`, `openInterest`, `openInterestValue`, `basisRate`.

```
mark_price                 index_price
premium                    premium_zscore
basis_rate                 basis_zscore
mark_vs_last_distance      index_vs_last_distance
```

### Por qué importa
Separa:
```
nuevo posicionamiento      vs    cierre de posiciones
vs liquidación             vs    fake move por perp premium
```

---

## E. LIQUIDACIONES REALES Y CLUSTERS (alta prioridad)

Bybit tiene stream oficial **All Liquidation** (USDT/USDC/inversos), push cada 500ms.
Problema: solo sirve para coleccionar desde ahora. Histórico completo = Tardis (pago).

### Features 🔌
```
liq_long_usd               liq_short_usd
liq_delta                  liq_ratio
liq_cluster_high           liq_cluster_low
liq_cluster_near_sweep     liq_cascade_score
liq_after_reclaim          liq_before_reversal
distance_to_liq_cluster
```

### Setup que desbloquea
```
sweep low + liquidaciones long + vendedores tardíos atrapados → long
sweep high + liquidaciones short + compradores tardíos atrapados → short
```

---

## F. SETUPS COMPUESTOS Y EVENT BUILDER

### Setups construibles con features actuales
- **Absorción en VAH/VAL** — volumen alto que aguanta el nivel clave
- **Sweep + reclaim** — barre liquidez y revierte
- **Delta divergence en swing** — precio extremo sin confirmación de delta
- **Imbalance fill** — precio regresa a llenar imbalances apilados
- **Exhaustion** — pico de volumen en extremo + delta agotado + reversión

### Event Builder (construir) 🆕
Orderflow real ocurre como **evento**, no como vela bonita empaquetada. Un sweep puede
durar 15 segundos o 3 velas — forzarlo a M1 lo licúa estadísticamente.

```
event_builder.py
```

Eventos a construir:
```
sweep_event                 reclaim_event
absorption_event            big_trade_cluster_event
delta_extreme_event         tape_burst_event
failed_breakout_event       liquidation_cluster_event
poc_rejection_event         value_acceptance_event
```

Cada evento debe tener:
```
start_ts / end_ts / duration_ms
price_start / price_end
max_adverse_excursion / max_favorable_excursion
delta_total / volume_total / big_trade_total
book_state_before / book_state_after
outcome_5m / outcome_15m
```

---

## G. ARQUITECTURA DE ROLES (entrada / sizing / salida)

No mezclar roles. Cuando se mezcla todo como "filtro de entrada", el sistema se comporta
como Frankenstein en pandas.

| Rol | Feature | Descripción |
|---|---|---|
| **Gate** | D1 EMA, tickDirection (minus>plus) | Permite o bloquea la entrada |
| **Tier** | body_below_poc, n_trades | Mejora calidad / define sizing |
| **Veto** | fp_absorb_buy | Evita el short malo |
| **Exit** | L2 walls, spread, microprice | Gestión de trade abierto |
| **Regime** | OI, funding, premium | Contexto macro del momento |
| **Target** | VP/VWAP/LVN, distancia limpia | Dónde salir con lógica |

---

## H. NO-TRADE ENGINE (vetos explícitos)

Tener veto `absorb_buy` es un buen start. Faltan vetos sistemáticos:

```python
# Vetos a implementar
middle_of_value_area           # sin sesgo direccional
rr_too_low                     # RR < 1.5 hasta target
target_blocked_by_wall         # pared antes del TP
spread_above_threshold         # bid/ask demasiado ancho
low_liquidity_session          # Asia / off-hours
post_news_volatility_spike     # después de evento macro
overextended_after_move        # precio muy lejos del POC
cvd_against_short              # CVD fuerte positivo
oi_against_short               # OI subiendo con precio (longs entrando)
funding_extreme_against_short  # funding muy negativo = crowded short
```

> El edge puede mejorar más evitando basura que agregando señales.

---

## I. SLIPPAGE / FILL MODEL

Tener `FEE_RT=0.0011` no es suficiente. Entre "datos disponibles" y "yo habría sido
llenado exactamente ahí" hay un abismo donde viven muchos backtests muertos.

### Features / simulación 🆕
```
spread_at_entry            spread_at_exit
spread_zscore              slippage_estimated
entry_fill_probability     exit_fill_probability
queue_position_proxy       market_impact_proxy
latency_ms_assumption
```

### Backtest en 3 variantes
```
optimista  → mid/close (como ahora)
realista   → bid/ask + spread
pesimista  → spread + slippage dinámico por tape speed
```

Si solo gana en optimista, no hay sistema. Hay fan fiction con columnas.

---

## J. SETUP ATTRIBUTION

El sistema actual tiene un promedio global `OOS AvgR +0.277`. Pero no sabemos qué subtipo
paga la fiesta. Etiquetar cada trade:

```
setup_type         → sweep_reclaim / range_rotation / breakdown_retest / value_acceptance / failed_vwap_reclaim
setup_subtype
entry_reason_primary
entry_reason_secondary
veto_reason
target_type
```

Permite matar subtipos inútiles sin matar el sistema entero. Cirugía, no motosierra.

---

## K. VALIDACIÓN DE ROBUSTEZ — cruce por régimen y sesión

OOS general está bien, pero hay que partirlo por:

```
performance_by_session           (London/NY/Overlap/Asia)
performance_by_hour              (hora UTC)
performance_by_weekday
performance_by_volatility_regime (ATR alto/bajo)
performance_by_value_state       (balance/imbalance/trend)
performance_by_oi_state          (OI subiendo/bajando)
performance_by_funding_state     (positivo/negativo/extremo)
performance_by_spread_regime
```

Un sistema puede verse estable en total y estar sostenido por tres horas específicas.

### Feature decay 🆕
```
rolling_avgR_30d               rolling_hit_rate_30d
rolling_trade_count_30d        feature_importance_by_month
gate_pass_rate_by_month        drawdown_by_month
```

¿`body_below_poc` sigue funcionando en todos los trimestres o fue un regalo de régimen?
Si una feature solo funcionó en un régimen bajista, es una mascota temporal, no una ley.

---

## L. ESTUDIO DEL LADO LONG (espejo de validación)

El sistema está orientado al short. No operar long necesariamente — pero estudiar el espejo
da información sobre si el edge explota una asimetría real de BTC perp o solo un sesgo:

```
body_above_poc            absorb_sell
plus_ticks_gt_minus_ticks sell_absorption_veto
long_setup_score          short_setup_score
```

Si el lado long falla simétricamente, el edge no es "vela + nivel". Es asimetría estructural
de BTC perp. Eso es mucho más interesante que "soy bearish porque el gráfico se ve feo".

---

## RESULTADOS DEL BARRIDO (validado 2026-06-18, futuros perp 17 meses, IS/OOS)

> Script: `backtest/funnel_orderflow.py`. Fee futuros 0.11% RT.
> IS hasta 2026-03-01, OOS desde ahí. Dataset `btcusdt_perp_m1.parquet` (105 cols).

### Evolución del edge
| Sistema | IS AvgR | OOS AvgR | TotalR | Meses negativos |
|---|---|---|---|---|
| Original (sin filtros) | +0.015 | +0.148 | +64 | muchos |
| + régimen D1 EMA thr=1.000 | +0.104 | +0.247 | +99 | — |
| + agresión (minus>plus ticks) | +0.181 | +0.256 | +110 | — |
| **+ veto absorb_buy** | **+0.198** | **+0.277** | **+111** | **3 de 18** |

### Veredicto por paso del embudo
| Paso | Feature | Veredicto |
|---|---|---|
| 0. Régimen | D1 EMA thr=1.000 | ✅ **gate** (mayor salto) |
| 1-2. Valor/Nivel | `body_below_poc` | ✅ **tier de calidad**: IS +0.360 / OOS +0.380 (~30% vol) |
| 3. Agresión | minus_ticks > plus_ticks | ✅ **gate** (2º salto) |
| 4. Atrapada | `fp_absorb_buy` (veto) | ✅ **veto casi gratis** (+AvgR IS y OOS, −6% vol) |
| 5. Veto liquidez | OBI / ask_wall / bid_wall | ✗ **no generaliza** (se invierte OOS) |
| 6. Tape acelera | `n_trades` | ✅ tier de calidad: IS +0.215 / OOS +0.402 (~50% vol) |
| 7. Espacio limpio | OBI negativo | ✗ **se invierte OOS** (IS +0.240 → OOS +0.052) |

### Hallazgos clave
1. **OB L2 a la ENTRADA no es predictivo.** OBI/walls suben IS, se invierten OOS. No vale OB profundo para entradas. Puede servir para gestión/salidas.
2. **`body_below_poc`** — mejor señal de calidad, IS≈OOS. El mercado premia aceptación bajo valor.
3. **`n_trades`** — intensidad de tape confirma como tier de sizing.
4. **No combinar tiers**: `body_below_poc` + `n_trades` juntos colapsa a IS +0.069 (overfit). Usar por separado.
5. **Footprint** — el único fp_ útil fue el **veto absorb_buy**. Imbalances/stacks no discriminan.

---

## PRIORIZACIÓN REAL

### P0 — ya mismo
```
codificar sistema refinado en mtf_basics.py (régimen + agresión + veto absorb_buy)
paridad Python/Rust
test de costos pesimista (slippage realista)
performance por mes/sesión/hora (robustez)
setup attribution (etiquetar subtipos)
```

### P1 — frontera útil (derivados)
```
open_interest + OI delta
funding_rate + funding_regime
premium / basis
mark-index-last divergence
liquidation live collector (desde ahora)
```

### P2 — mejorar gestión/salidas
```
L2 para salidas (wall_before_tp, depth_to_target)
spread dinámico / trailing basado en L2
microprice contra posición
liquidity gap hacia target
```

### P3 — investigación
```
event_builder.py (sweep/reclaim/absorption como eventos)
auction_state (VP como estado de subasta)
target_quality (obstacles between entry and TP)
long mirror study
feature decay (rolling metrics por mes)
```

### P4 — no tocar todavía
```
spoofing / layering
iceberg detection
OBI profundo como gate de entrada
OFI como gate de entrada
stacks/imbalances exóticos
más variantes de score de entrada
```

---

## Qué NO falta

Tu propio barrido ya lo dijo — no ignorar los resultados propios:
```
OB L2 no generaliza en entrada          → probado, descartado
imbalances/stacks no discriminan        → probado, descartado
combinar tiers colapsa                  → probado, descartado
spoofing/iceberg/microprice entrada     → no testear todavía
más variantes de score                  → no añade edge
```

---

## Archivos clave

| Archivo | Contenido |
|---|---|
| `backtest/mtf_basics.py` | Short backtest (core — pendiente integrar 3 filtros) |
| `backtest/funnel_orderflow.py` | Barrido IS/OOS del embudo — fuente de los resultados |
| `backtest/build_futures_dataset.py` | Pipeline descarga + enrich perp |
| `backtest/_footprint.py` | Footprint proto |
| `crates/ob_parser/` | Parser OB paralelo Rust |
| `data/bybit-perp/processed/btcusdt_perp_m1.parquet` | Dataset 767K barras, 105 cols |
| `docs/mtf/MTF_WORKLOG_2026-06-18.md` | Log completo de la sesión de auditoría |
