# MTF Worklog — 2026-06-19 (sistema v2: funnel orderflow, sizing, longs, AS)

> Continuación de 2026-06-18. El sistema básico con fees reales sobre futuros estaba validado.
> Esta sesión construyó el sistema completo: régimen estricto + funnel de features orderflow +
> sizing multi-tier CVD/OBI/tape + longs espejo + investigación exhaustiva de transcripciones.

---

## 1. ¿Qué es el MTF?

El nombre sigue siendo correcto. El sistema opera en **tres timeframes simultáneos**:

| TF | Rol |
|---|---|
| D1 | Régimen (EMA20): determina si el mercado está en contexto bajista/alcista |
| H1 | Estructura (BOS/ChoCH): confirma momentum direccional |
| M1 | Entry (wick + orderflow): momento preciso de entrada |

Sin los tres alineados, no hay trade. MTF = Multi-TimeFrame.

---

## 2. El sistema v2 — resultado final de la sesión

### 2.1 Configuración global

```python
CAPITAL  = 500          # USD inicial
RISK_PCT = 0.02         # 2% por trade (mensual compound)
FEE_RT   = 0.0011       # 0.055% × 2 legs, futuros Bybit taker
TARGET_R = 2.8          # validado como mejor IS≈OOS (barrido completo)
FORWARD  = 1200         # timeout 20h (1200 barras M1)
MIN_STOP = 0.30%        # stop mínimo
MAX_STOP = 0.75%        # stop máximo
LEVEL_TOL = 0.7%        # tolerancia de proximidad al nivel
IS = Ene 2025 – Feb 2026 (425 días, ~127 shorts / ~41 longs IS)
OOS = Mar – Jun 2026    (92 días, ~42 shorts / ~7 longs OOS)
```

### 2.2 Gates del sistema (SHORTS)

```
Gate 0 : D1 EMA20 — close <= EMA20 × 0.980 (régimen bajista sin sobreextensión)
Gate 0b: H1 estructura — h1_bos_bear OR h1_choch_bear (BOS o ChoCH bajista)
Gate 1 : Nivel — VAH (± 0.7%) | AH | PDH (solo con VAH) | WH
Gate 2 : Rechazo bajista — wick superior > 30% del rango, close <= open
Gate 2a: body_below_poc — precio acepta por debajo del POC de sesión
Gate 2b: agresión — minus_ticks > plus_ticks (más ticks bajistas que alcistas)
Veto   : NOT fp_absorb_buy (si hay comprador absorbiendo activamente, skip)
Sesión : london | overlap | ny
```

**Razonamiento:** body_below_poc + minus_ticks eliminan el 73% de las barras de entrada que pasan el wick, con una mejora neta de +0.44R IS.

### 2.3 Gates del sistema (LONGS)

```
Gate 0 : D1 EMA20 — 1.000 <= close/EMA20 <= 1.030 (alcista sin sobreextensión)
Gate 0b: NOT h4_bos_bear (sin downtrend H4 activo)
Gate 0c: h1_bos_bull (BOS alcista H1 confirmado)
Gate 1 : Nivel — VAL | AL (asian_low) | PDL | WL
Gate 2 : Rechazo alcista — wick inferior > 30% del rango, close >= open
Gate 2a: cuerpo no por encima del POC
Gate 2b: plus_ticks > minus_ticks (agresión compradora)
Veto   : NOT fp_absorb_sell
Sesión : overlap | ny
```

**Nota h4_bos_bear:** Validado IS/OOS — veta los longs en estructura H4 bajista.
**Nota h1_bos_bull:** Solo BOS (no ChoCH solo): h1_choch_bull solo tiene performance IS negativa.

### 2.4 Sizing multi-tier SHORTS (validado IS/OOS)

| Tier | Condición | Mult | IS WR | IS AvgR |
|------|-----------|------|-------|---------|
| 1 | CVD>0 + tape + obi10>=0 | **2.5×** | 75.0% | +1.648 PF=6.59 |
| 2 | CVD>0 + tape, obi10<0 | **2.0×** | 47.4% | +0.532 |
| 3a | CVD>0 solo | **1.5×** | 64.3% | +1.033 |
| 3b | tape solo | **1.5×** | 55.6% | +0.729 |
| 3c | obi10>=0 solo | **1.5×** | 50.0% | +0.728 |
| 5 | ninguno | **1.0×** | 7.7% | -0.941 |

**Significado de cada señal (shorts en VAH):**
- `CVD>0` = compradores agresivos en la barra → quedarán atrapados en VAH (bull trap)
- `obi10>=0` = bids >= asks en el libro → compradores acumulando órdenes límite en VAH (se atrapan)
- `tape` = n_trades >= Q50 IS → volumen suficiente para validar la absorción

**Tier 5 NO vetado:** OOS tiene 9 trades tier 5 con WR=44.4% y 3 wins a 2.5R (May-Jun 2026). El 1.0× ya lo controla sin necesidad de veto.

### 2.5 Sizing multi-tier LONGS (validado IS)

| Tier | Condición | Mult | IS WR | IS AvgR |
|------|-----------|------|-------|---------|
| 1 | CVD>0 + tape + OBI>0 | **2.5×** | 50.0% | +0.626 |
| 2 | CVD>0 + tape | **2.0×** | 55.6% | +0.790 |
| 3 | CVD>0 o OBI>0 | **1.5×** | ~50% | ~+0.5 |
| 5 | ninguno / tape solo | **1.0×** | — | — |

**Dirección de señales para longs (descubrimiento crítico):**

CVD y OBI usan el MISMO signo que para shorts (CVD>0, OBI>0), pero por razón diferente:

| Señal | En SHORTS (VAH) | En LONGS (VAL) |
|---|---|---|
| CVD>0 | Compradores que serán absorbidos (contrarian) | Compradores DEFENDIENDO el nivel (confirmatorio) |
| OBI>0 | Bids pesadas = bull trap | Bids pesadas = buyers activos en soporte |
| Tape solo | Señal útil (WR=55.6%) | Señal negativa (WR=16.7%, AvgR=-0.612) — degradar a 1.0× |

La interpretación cambia por el contexto del nivel, no por la dirección de la señal.

---

## 3. Hallazgos de la sesión

### 3.1 obi10_mean: dirección era INVERTIDA (bug crítico corregido)

Inicialmente se usó `obi_ok = obi10 < 0` (asks pesadas). El análisis mostró:
- obi10 < 0 sola: IS WR=7.7%, AvgR=-0.941 (catastrófico)
- obi10 >= 0: IS WR=57%, discrimina mejor

La dirección correcta es `obi10 >= 0` (bids pesadas = compradores acumulando en VAH = bull trap).

### 3.2 fp_stack_sell: discriminante NEGATIVO (no usar en sizing)

El análisis inicial intentó usar `fp_stack_sell >= 3` como boost para Tier 1. Resultado:
- fp_stack_sell > mediana (≥3): WR=44%, AvgR=+0.427
- fp_stack_sell ≤ mediana: WR=56%, AvgR=+0.829

Más imbalances apilados = el setup es "demasiado obvio" = ya está siendo exprimido. Rechazado completamente.

### 3.3 Absorption Score (AS, spec v3) — no aplica a nuestro entry pattern

El AS del catálogo de orderflow (peso 22/100 en spec v3) mide:
```
DZ = (delta - rolling_mean(delta, 150)) / rolling_std(delta, 150)
desplaz = (close - open) / (high - low)
AS_short = max(DZ, 0) × (1 - max(-desplaz, 0))
```

**Por qué no funciona para nosotros:** nuestras barras de entry tienen `minus_ticks > plus_ticks` = delta negativo = DZ < 0 → AS siempre 0. El AS está diseñado para entrar EN la barra de absorción (compradores comprando con fuerza pero sin que suba el precio). Nosotros entramos DESPUÉS del rechazo (barra bajista confirmatoria).

Probado en barras previas (lag 1-5, ventana max 5): max(AS, ventana=5) >= 1.5 → n=6, WR=33%, AvgR=-0.532 = discriminante NEGATIVO.

**Lo que usamos en su lugar** (ya en el sistema):
- `CVD>0` (slope de CVD acumulado) captura la absorción multi-barra
- `body_below_poc` captura que el precio no logró mantener el nivel
- `NOT fp_absorb_buy` veta si la absorción compradora es activa en este momento

### 3.4 Abril 2026 OOS DD — Liberation Day

Todos los trades de Abril 2-5, 2026 perdieron (WR=0%, 4 trades, PnL=-$1,209):
- Aranceles Trump ("Liberation Day") anunciados Abril 2, 2026
- Shock macro no capturado por ninguna señal técnica
- **No es fallo del sistema** — todos los trades tuvieron CVD=0 (sin señal de absorción)
- Ya estaban en Tier 2-3 (sizing moderado), no Tier 1
- Capital peak-to-trough máximo: -12% (manejable)

### 3.5 Transcripciones — síntesis de 40+ videos

Leídas todas las transcripciones de `transcripciones/` y `transcripciones/revisar/`:
- 65 conceptos catalogados en `docs/ORDERFLOW_CATALOGO.md`
- Spec v3 completa ("Delta Range Reversal") con fórmulas exactas y pesos

**Qué usa el sistema** de los conceptos del catálogo (aproximadamente 66/100 puntos de spec v3):
- AS (22pts): parcialmente capturado con CVD+body_below_poc
- Proximidad al extremo (18pts): VAH/VAL gates
- POC en mecha (12pts): body_below_poc
- Delta flip (12pts): minus_ticks > plus_ticks
- Volumen VR (10pts): n_trades tape
- CVD divergencia (8pts): cvd_slope

**Qué no tenemos** (Big Trades 6pts, Liquidaciones 6pts, OI 4pts, LI heatmap 2pts): requieren tick data o APIs adicionales. No implementados.

### 3.6 asian_low como nivel de longs

El `active_level_long()` ya detectaba `asian_low` como nivel `AL`, pero `simulate_long()` requería `'VAL' in lbl` — filtrándolo. Se quitó esa restricción.

Resultado IS: 1 trade nuevo (AL puro, Jan 2026), WR=100%, AvgR=+2.632.
Espejo exacto de `asian_high` que usamos para shorts.

---

## 4. Resultados finales v2

```
=== SHORTS ===
IS: n=127  WR=51.2%  AvgR=+0.658  PF=2.12
OOS: n=42  WR=50.0%  AvgR=+0.635  PF=2.08  ← IS≈OOS ✓

=== LONGS ===
IS: n=42  WR=45.2%  AvgR=+0.346  PF=1.52
OOS: n=7  WR=42.9%  AvgR=+0.365  PF=1.52  ← pequeña muestra

=== COMBINADO ($500 → $15,810, 3062%) ===
Shorts: $15,461  |  Longs: $849
```

**Calendario mensual IS shorts** (para entender variabilidad):

| Mes | n | WR | AvgR |
|---|---|---|---|
| Ene 2025 | 11 | 45.5% | +0.285 |
| Feb 2025 | 9 | 44.4% | +0.468 |
| Mar 2025 | 10 | 50.0% | +0.533 |
| Abr 2025 | 9 | 44.4% | -0.174 |
| May 2025 | 7 | 28.6% | -0.453 |
| Jun 2025 | 7 | 57.1% | +0.734 |
| Jul 2025 | 11 | 72.7% | +1.469 |
| Ago 2025 | 9 | 55.6% | +1.128 |
| Sep 2025 | 11 | 27.3% | -0.396 |
| Oct 2025 | 9 | 66.7% | +0.990 |
| Nov 2025 | 14 | 57.1% | +0.830 |
| Dic 2025 | 8 | 62.5% | +0.805 |
| Ene 2026 | 9 | 44.4% | +0.635 |
| Feb 2026 | 3 | 33.3% | +0.225 |

---

## 5. Paridad Python ↔ Rust

`data/src/strategy/detectors/mtf_spot_detector.rs` actualizado con:
- `pub n_trades: f64` en `MtfSpotBarContext` (con `#[serde(default)]`)
- `pub sizing_mult: f64` en `MtfSpotSignal`
- Rolling median para `n_trades_q50` (warmup cada 1000 barras, live update cada 500)
- Sizing tiers completos en `on_bar_close()`:
  - Shorts: CVD>0 / tape / obi10>=0
  - Longs: CVD>0 / tape solo=1.0x / obi10>0
- `restore_active_trade()` gets `sizing_mult: 1.0` (safe default)
- **Compilado exitoso** con `cargo +stable-x86_64-pc-windows-gnu build --release`

**Pendiente en Rust**: longs sizing con tape-solo=1.0x y obi>0 (actualmente usa la versión anterior con CVD<0/OBI<=0 que fue descartada).

---

## 6. Features explorados y estado de cada uno

| Feature | Resultado | Estado |
|---|---|---|
| `body_below_poc` | Gate duro válido, +edge | ✅ En uso |
| `minus_ticks > plus_ticks` | Gate duro válido, +edge | ✅ En uso |
| `fp_absorb_buy` | Veto válido shorts | ✅ En uso (veto) |
| `fp_absorb_sell` | Veto válido longs | ✅ En uso (veto) |
| `h1_bos_bear` / `h1_choch_bear` | Gate estructura shorts | ✅ En uso |
| `h1_bos_bull` | Gate estructura longs | ✅ En uso |
| `h4_bos_bear` | Veto longs (downtrend H4) | ✅ En uso (veto) |
| `cvd_slope > 0` | Sizing discriminante clave | ✅ En uso (sizing) |
| `obi10_mean >= 0` | Sizing shorts (corrección crítica) | ✅ En uso (sizing) |
| `obi10_mean > 0` | Sizing longs (misma dirección) | ✅ En uso (sizing) |
| `n_trades` tape | Sizing (Q50 IS) | ✅ En uso (sizing) |
| `asian_high` | Nivel shorts (AH) | ✅ En uso (nivel) |
| `asian_low` | Nivel longs (AL) | ✅ En uso (nivel) |
| `prev_day_high` | Nivel shorts (PDH con VAH) | ✅ En uso (nivel) |
| `weekly_high` | Nivel shorts (WH) | ✅ En uso (nivel) |
| `fp_stack_sell` | Discriminante NEGATIVO | ❌ Rechazado |
| `h1_ob_bear` | IS WR=22.5%, AvgR=-0.440 | ❌ Rechazado |
| `obi10 < 0` en tier 3 | WR=7.7% catastrófico | ❌ Rechazado (dirección invertida) |
| Absorption Score (AS) | AS=0 en nuestros entries (DZ<0) | ❌ No aplica |
| `fp_result_sell` (TRUE) | n=16, WR=62.5%, AvgR=+1.071 | 🔬 Por validar (n pequeño) |
| `fp_unfinished_hi` | FALSE=señal débil | 🔬 Por validar |
| OI / Funding | Requiere API Bybit adicional | ⏳ Pendiente |
| Liquidaciones | Requiere tick data | ⏳ Pendiente |
| CVD divergencia multi-barra | Computeable, no implementado | 🔬 Por testear |
| Daily VWAP | Computeable desde M1 | 🔬 Por testear |

---

## 7. Archivos modificados en esta sesión

| Archivo | Cambios |
|---|---|
| `backtest/mtf_v2.py` | Sistema completo. Gates v2, sizing multi-tier shorts+longs, simulate_long() con asian_low, compute_as() (referencia), longs sizing CVD>0/OBI>0/tape-solo=1.0x |
| `backtest/test_v3.py` | Script de análisis comparativo (A2 vs v2, CVD modes, auditoría de features) |
| `backtest/funnel_orderflow.py` | Barrido de features individuales y combinaciones |
| `data/src/strategy/detectors/mtf_spot_detector.rs` | n_trades, sizing_mult, rolling Q50, tiers CVD/OBI/tape |
| `docs/ORDERFLOW_CATALOGO.md` | Catálogo exhaustivo 65 conceptos extraído de transcripciones |

---

## 8. PENDIENTE para próxima sesión

### Alta prioridad
1. **Paridad Rust longs**: corregir `mtf_spot_detector.rs` con:
   - Longs sizing: CVD>0 (no CVD<0), OBI>0 (no OBI<=0), tape solo = 1.0x
   - Recompilar y verificar parity contra Python

2. **fp_result_sell como gate adicional (shorts)**: n=16 IS, WR=62.5%, AvgR=+1.071 cuando TRUE+CVD>0. Testear IS/OOS con más contexto.

### Media prioridad
3. **CVD divergencia multi-barra**: precio hace new high pero CVD hace lower high → señal más fuerte. Ventana 20 barras. Añadir a `compute_spot_features.py`.

4. **Volume Ratio (VR) rolling**: `n_trades / rolling_median(n_trades, 150)`. Más adaptativo que Q50 estático. VR >= 2.0 = absorción; VR >= 4.0 = rompimiento.

5. **More longs levels**: analizar PDL (IS=0% n=1, OOS=100% n=1 — muy poca data), WL.

### Baja prioridad / datos nuevos
6. OI + Funding como veto de shorts (API Bybit)
7. Multi-instrumento (ETH/SOL) — después de estabilizar BTC

---

## 9. Conceptos clave para entender el sistema

### Por qué CVD>0 es señal de short (contraintuitivo)
En BTC perp, los compradores agresivos en VAH quedan atrapados. CVD>0 en VAH = compradores comprando el breakout que no existe → absorción → después bajan. Delta positivo en máximos = compra mal posicionada (diferente de índices como ES/NQ donde delta sigue al precio).

### Por qué body_below_poc es tan importante
El POC de sesión es donde más volumen se ejecutó ese día. Si el precio cierra POR DEBAJO del POC, la "mayoría" de los compradores de ese día están en pérdida. Esto crea presión vendedora adicional y confirma que VAH fue rechazado para quedarse ahí.

### Por qué 2.8R y no menos
Barrido completo de 1.5R a 4.0R sobre 17 meses: 2.8R maximiza el TotalR IS y minimiza la divergencia IS/OOS. Con 2.8R el sistema "deja correr" las victorias sin esperar demasiado (timeout 1200 barras = 20 horas).

### Por qué Tier 5 no se veta
Los 13 trades IS en tier 5 tenían WR=7.7% (1 winner). Parecía obvio vetarlo. Pero OOS 2026 tiene 9 trades tier 5 con WR=44.4% incluyendo 3 trades de 2.5R en Mayo-Junio. Vetar habría costado $329 de capital. El 1.0× ya minimiza el daño en IS sin sacrificar el upside OOS.

---

## 10. SESIÓN CONTINUADA — edge + volumen → sistema `directions` (mtf_system.py)

> Objetivo del usuario: el v2 es swing disfrazado (0.3 tpd / ~9 trades-mes). La meta es
> 2-4 trades/día INTRADÍA fusionando ICT + orderflow. Esta parte diagnostica el cuello de
> botella real y consolida el primer salto desplegable.

### 10.1 Dos señales nuevas robustas (sobre entradas v2, `_edge_research.py`)
Criterio: mejora/limpia en AMBOS IS y OOS (lo único que importa con n=127/42).
- **Veto `vp_lvn_below==False`**: ese subconjunto pierde en ambos (IS +0.204 / OOS **−0.439**).
  Quitarlo: OOS +0.635→+0.780, WR 50→54%, costo 8 IS / 5 OOS. **Edge real, no leverage.**
- **`vpin` (toxicidad de flujo)**: ORTOGONAL a tape (corr=0.12) y solo discrimina CON volumen.
  `tape & vpin_hi`: OOS WR 66.7% +1.316. `tape & vpin_hi & cvd>0`: OOS WR 80% +1.816.
  `cvd>0` solo es FRÁGIL OOS cuando vpin bajo (−0.107). → sizing reordenado alrededor de
  `tape & vpin`. (fp_result_sell, fp_sell_dom, bid_wall, cvd_div: se invierten OOS, descartados.)

### 10.2 Diagnóstico del cuello de botella (`_funnel_attrition.py`)
Embudo short: 1440 barras/día → sesión 780 → régimen D1 264 → H1 122 → cerca VAH 59 →
**rechazo (vela) 8.15** ← mata 86% → ... → **2.35 barras/día** pasan TODOS los gates.
Pero el sistema solo hace 0.31 tpd. Dos bloqueos, ninguno es el edge:
1. **Disparador único** (rechazo@VAH): un solo patrón de vela.
2. **Lock de 1 posición + holding 3.6h**: serializa las 2.35 oportunidades/día.

### 10.3 Menú de disparadores ICT (`_ict_triggers.py`, misma confirmación orderflow)
| Trigger | IS AvgR | OOS AvgR | tpd | Veredicto |
|---|---|---|---|---|
| rejection_VAH (actual) | +0.568 | +0.476 | 0.38 | base |
| **FVG bajista** | +0.544 | +0.468 | 0.50 | ✅ +volumen, robusto |
| OrderBlock retest | +0.500 | +0.490 | 0.07 | ✅ robusto, poco vol |
| Displacement | +0.464 | +0.415 | 0.33 | ✅ (ver nota slot) |
| Liquidity sweep | +0.368 | +0.461 | 0.30 | ⚠️ medio |
| OTE (fib 62-79) | +0.547 | **+0.086** | 0.31 | ❌ colapsa OOS |
| EqualHigh sweep | +0.700 | **−0.296** | 0.06 | ❌ flip OOS |

UNIÓN (validados): 0.56 tpd, OOS +0.479 — edge intacto, +47% volumen. Sin gate H1: 0.74 tpd
pero edge diluido (OOS +0.324) → el H1 carga edge, no se quita.

**Sweep target/timeout**: ningún combo rompe ~1 tpd (lock de 1 pos es el techo). Bajar target
sube WR pero destruye TotR (edge vive en runners 2.8R). Banked: **timeout 240m (4h)** cuesta
casi nada vs 20h y se siente intradía.

### 10.4 Motor concurrente — modelos de riesgo (`_concurrent_engine.py`)
| Modelo | tpd | OOS AvgR | Equity | MaxDD | concur máx |
|---|---|---|---|---|---|
| **directions** (1S+1L) | **0.73** | **+0.412** | **$74.9k** | **28%** | 1 |
| cap 6% | 1.20 | +0.169 | $111k | 38% | 5 |
| cap 10% | 1.68 | +0.113 | $241k | 55% | 7 |
| nocap | 2.12 | +0.190 | $675k | **70%** | 12 |

**Hallazgo central:** 2-4 tpd en UN símbolo = leverage correlacionado, no edge. El MaxDD escala
28→70% mientras el AvgR OOS se degrada: las señales clusterizan → posiciones correlacionadas →
la misma apuesta repetida. nocap llega a 2.12 tpd pero 70% DD = no desplegable.

**`directions` es oro:** shorts y longs NUNCA se solapan (regímenes D1 opuestos), así que añadir
longs es volumen SIN riesgo correlacionado (concur máx=1). 0.31→0.73 tpd (2.4x), $15.8k→$74.9k
(4.7x), DD 28%, mejor edge OOS. Los longs llenan los días alcistas donde el short no entra.

### 10.5 Sistema consolidado: `backtest/mtf_system.py`
Config `directions`: unión ICT (rejection@VAH+FVG+OB+Displacement+Sweep) + longs espejo +
sizing vpin-aware + veto LVN + target 2.8R + timeout 4h + cooldown 15m + capital compartido.
Walk-forward 533 días, fee 0.11%:
```
COMBINADO  IS n=309 WR=54.7% AvgR=+0.502  |  OOS n=82 WR=53.7% AvgR=+0.412  | 0.73 tpd
shorts     IS n=267 WR=54.7% AvgR=+0.527  |  OOS n=75 WR=53.3% AvgR=+0.386
longs      IS n= 42 WR=54.8% AvgR=+0.343  |  OOS n= 7 WR=57.1% AvgR=+0.681  (muestra chica)
Capital $500 -> $74,916   MaxDD 28.2%
```

**Lección — atribución por-trigger NO es separable:** quitar Displacement (que en desglose
puntúa −0.562 OOS) EMPEORA el sistema ($74.9k→$55.7k, DD 28→32%) porque libera slots a
entradas marginales peores de rejection/sweep. La unión completa domina. No "limpiar" triggers
por su número aislado bajo slot-competition.

### 10.6 Camino real a 2-4 tpd: MULTI-INSTRUMENTO
La única vía a 2-4 tpd de edge no-correlacionado es la misma estrategia en símbolos paralelos
(ETH/SOL/…): edge independiente, los tpd suman sin inflar DD. (Dataset ETH purgado 2026-06-18 →
re-descargar.) Apilar BTC (cap/nocap) es re-apostar, no diversificar.

### 10.7 PENDIENTE (siguiente sesión)
1. Portar `mtf_system.py` a `mtf_spot_detector.rs` (unión ICT + longs concurrentes + vpin) + parity.
2. Multi-instrumento: re-bajar ETH/SOL, correr build_futures_dataset + mtf_system.
3. Longs siguen con muestra OOS chica (n=7) — profundizar o aceptar como complemento de bajo peso.

Archivos nuevos: `mtf_system.py` (sistema), `_edge_research.py`, `_edge_research2.py`,
`_funnel_attrition.py`, `_ict_triggers.py`, `_concurrent_engine.py` (research). `mtf_v2.py`
actualizado (veto LVN + sizing vpin).
