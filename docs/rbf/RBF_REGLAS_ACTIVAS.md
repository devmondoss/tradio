# RBF — Reglas Activas por Símbolo

*Última actualización: 2026-06-11 — OBI intrabar 10s · spread gate · OI covering gate · multi-depth confluence*

Este documento es la referencia única de todas las reglas vigentes en el sistema RBF.
Cada regla tiene su fuente: backtest histórico, análisis live, o pendiente de datos.

---

## Reglas globales (todos los símbolos)

| Regla | Valor | Fuente |
|-------|-------|--------|
| Sesiones activas | London · LondonNyOverlap · NewYork | backtest 30d |
| Dirección | **Solo Shorts** — Longs desactivados | live: WR Long = 14% en 72 trades |
| Tamaño del rango | 0.08% – 0.55% del precio | backtest |
| Ventanas probadas | 15 · 20 · 30 · 45 · 60 barras M1 | backtest |
| CVD en rango | Debe ser negativo para Short | lógica fundamental |
| VWAP gate | close ≤ VWAP + 0.3% | backtest: >0.5% bajo → WR=6.5% |
| min_rr | 1.5 | parámetro base |
| min_confluence_score | 1 (shadow mode) | pendiente calibración |
| **score ≠ 4 gate** | **score=4 → no opera** (se registra en Supabase) | live n=11: WR=9%, total=−10.68R |
| Cooldown | 60 barras desde última señal | operativo |
| Time stop | 30 barras M1 si en pérdida | calibrado |
| Trailing Short | activa en **1.75R**, distancia 1.2×ATR | live: 4 trailing cases cedían 0.97R/trade |
| Trailing Long | activa en **1.5R**, distancia 1.2×ATR | (Longs desactivados, pero si se reactivan) |
| **Pre-CVD gate** | últimas 5 barras del rango: si sum(bar_delta) > 0 → skip | live n=7: WR=0%, avg=−1.38R |
| **London CVD gate** | sesión London + cvd_in_range > 200 → skip | fakeouts con compradores activos en rango |
| **Spread gate** | spread_bps > 5 → veto `spread_wide` | mercado ilíquido = breakout de noise |
| **OI covering gate** | Short + oi_delta_pct < −10% → veto `oi_covering` | longs cerrando = presión compradora al entrar |

---

## Modo post-breakout (señal normal)

El precio ya cerró fuera del rango con volumen alto.

| Regla | Valor | Fuente |
|-------|-------|--------|
| VR mínimo | ≥ 3.0× | backtest: <3× WR cae a 18% |
| Breakout extension | ≥ 0.1% más allá del nivel | backtest: <0.1% WR=18.8% vs >0.1% WR=44.4% |
| Entry | close de la barra de breakout | — |
| Stop | range_high (estructural) | — |
| Target Short | entry − 2.0 × risk | RR = 2.0 |
| pct_done promedio | ~54% del move consumido al entrar | análisis entry lag, 72 trades |

---

## Modo pre-breakout (entrada anticipada)

Activado **2026-06-10**. Entra en el borde del rango antes del VR≥3×.
El precio aún no ha roto — se anticipa el breakdown.

| Regla | Valor | Fuente |
|-------|-------|--------|
| VR en la barra | ≥ 1.5× (subiendo, pero sin breakout) | diseño |
| Zona de entrada | close ≤ range_low × 1.001 (dentro del 0.1% del piso) | diseño |
| CVD en rango | negativo (igual que post-breakout) | lógica fundamental |
| `oi_mom_bars_recent` | ≤ 3 barras con OI alineado en las últimas 25 | análisis: WR=58% PnL=+4.5R |
| `expansion_bars_recent` | aplica el mismo filtro por símbolo que post-breakout | análisis |
| Cooldown pre-señal | 60 barras desde la última señal pre-breakout | operativo |
| Entry | close de la barra (en el borde del rango) | — |
| Stop | range_high (estructural, igual que post-breakout) | — |
| Target Short | entry − 3.0 × risk | RR = 3.0 (captura el move completo) |
| `is_pre_breakout` | `true` en Supabase | diferenciador |
| `bars_held` | guardado al cerrar | para calibrar time stop específico |

**Por qué 3R y no 2R:** en post-breakout el entry consume ~54% del move, queda 2R para el target.
En pre-breakout el entry es en `pct_done≈0%` — el move total desde range_low promedia 4.63R,
de los cuales 2.63R eran "pre-entry" y 2R "post-entry". Con entry en el borde, el target natural es 3R+.

---

## Sistema de confluence score (actualizado 2026-06-11)

Score máximo: **11 puntos** (era 9 antes del 2026-06-11). Cada flag suma +1:

| Flag | Condición (Short) | Activo |
|------|------------------|--------|
| `cvd_slope` | CVD slope negativo | ✅ |
| `stacked_imbalance` | footprint: imbalance bajista apilado | ✅ |
| `absorption` | absorción de asks | ✅ |
| `lvn_nearby` | LVN (zona de poco volumen) cerca del precio | ✅ |
| `vpin` | VPIN alto (informed trading) | ✅ |
| `oi_momentum` | OI momentum bajista | ✅ |
| `htf_h1_aligned` | tendencia H1 bajista | ✅ |
| `obi_aligned` | obi_l5 < −0.10 | ✅ |
| `liq_ratio` | ratio liquidaciones bajista | ✅ |
| **`obi_multi_depth`** | **obi_l10 < −0.05 AND obi_l20 < −0.03** | ✅ nuevo 2026-06-11 |
| **`obi_intrabar_mean`** | **media de muestras 10s en la barra < −0.05** | ✅ nuevo 2026-06-11 |

### Vetos globales (bloquean el trade sin importar el score)

| Veto | Condición | Motivo |
|------|-----------|--------|
| `spread_wide` | spread_bps > 5 | mercado ilíquido = breakout de noise |
| `oi_covering` | Short + oi_delta_pct < −10% | longs cerrando = presión compradora al entrar |
| `vwap_gate` | precio muy por encima del VWAP | — |

---

## Calibración por símbolo

### BTCUSDT

| Regla | Valor | Estado | Fuente |
|-------|-------|--------|--------|
| `expansion_max_bars` | **1** | ✅ activo | análisis 72 trades: ≤1→WR=54.5% vs ≤3→48.8% |
| cum_delta gate Short | `> +200` candidato | ⏳ n<25 | losses cum_delta = +377 (compradores activos) |
| Trailing Short | 1.75R | ✅ activo | 2 trailing cases cediendo ~1.16R/trade |
| pre_breakout_oi_max | 3 | ✅ activo | análisis global |

**Insight BTC:** pérdidas tienen cum_delta positivo (+377) — compradores agresivos al entrar short. Wins tienen delta apenas negativo (-127) con absorción real.

---

### ETHUSDT

| Regla | Valor | Estado | Fuente |
|-------|-------|--------|--------|
| `expansion_max_bars` | **1** | ✅ activo | losses 4.8 barras vs wins 1.3 |
| London | **desactivado** | ✅ activo | WR=14%, −4R en 7/11 trades live |
| `cvd_in_range_min_short` | −700 | ✅ activo | wins CVD −418 vs losses −982 |
| `obi_gate` | 0.10 | ✅ activo | wins OBI=−0.094 = ask pressure real |
| cum_delta Short | no discrimina | ✖ no aplica | −1468 wins vs −1320 losses |
| Trailing Short | 1.75R | ✅ activo | calibración por dirección |
| pre_breakout_oi_max | 3 | ✅ activo | análisis global |

**Insight ETH:** único símbolo donde cum_delta no discrimina. La señal útil es OBI negativo (ask pressure). London tiene WR=14% → desactivado por completo.

---

### BNBUSDT

| Regla | Valor | Estado | Fuente |
|-------|-------|--------|--------|
| `expansion_max_bars` | **1** | ✅ activo | losses 7.8 barras vs wins 3.6 — mayor gap |
| cum_delta gate Short | `< −500` candidato | ⏳ n<25 | wins avg −78 vs losses −1357 |
| Trailing Short | 1.75R | ✅ activo | peores trailing cases: +0.90R y +0.80R |
| oi_mom veto | `> 6` candidato | ⏳ n<25 | losses 9.1 barras vs wins 4.3 |
| trailing threshold | posible 1.85R | ⏳ pendiente | BNB tuvo los peores trailing exits |
| pre_breakout_oi_max | 3 | ✅ activo | análisis global |

**Insight BNB:** delta casi neutro en wins (-78) vs muy negativo en losses (-1357). Cuando BNB acumuló mucho selling el move está terminado.

---

### SOLUSDT

| Regla | Valor | Estado | Fuente |
|-------|-------|--------|--------|
| `expansion_max_bars` | **None (bypass)** | ✅ activo | correlación INVERTIDA: wins 5.2 > losses 4.2 |
| `expansion_bars_recent` | se pasa 0 | ✅ activo | bypass para que el filtro nunca active |
| Trailing Short | 1.75R | ✅ activo | calibración por dirección |
| cum_delta gate Short | `> −20,000` candidato | ⏳ n<25 | wins avg −9,702 vs losses −43,138 |
| oi_mom (invertido) | no aplica aún | ⏳ n<25 | wins 8.2 barras vs losses 5.0 — también invertido |
| pre_breakout_oi_max | 3 | ✅ activo | análisis global |

**Insight SOL:** dos señales invertidas respecto al resto (expansion Y oi_mom). Revisar cuando n≥25.

---

### XRPUSDT

| Regla | Valor | Estado | Fuente |
|-------|-------|--------|--------|
| `expansion_max_bars` | **None (bypass)** | ✅ activo | muestra insuficiente |
| `expansion_bars_recent` | se pasa 0 | ✅ activo | bypass hasta n≥15 |
| Trailing Short | 1.75R | ✅ activo | calibración por dirección |
| Todo lo demás | pendiente | ⏳ n<10 | sin datos suficientes |

---

## Datos que se recolectan para futuras calibraciones

### En `rbf_signals` (por trade)

| Campo | Para qué sirve |
|-------|----------------|
| `is_pre_breakout` | separar el análisis pre vs post |
| `bars_held` | calibrar time stop específico para pre-breakout por símbolo |
| `obi_at_entry` | OBI L5 snapshot al entrar |
| `obi_at_breakout` | OBI L5 en la barra de breakout |
| `oi_delta_pct` | % de cambio en OI — detecta longs cubriendo |
| `confluence_flags` | array con qué flags se activaron |
| `confluence_score` | score 0–11 |
| `veto_reason` | razón de veto si aplica (`spread_wide`, `oi_covering`, etc.) |

### En `*_bars` (por barra M1 de cada símbolo)

| Campo | Para qué sirve |
|-------|----------------|
| `obi_l5` | OBI 5 niveles al cierre |
| `obi_l10` | OBI 10 niveles al cierre (nuevo 2026-06-11) |
| `obi_l20` | OBI 20 niveles al cierre (nuevo 2026-06-11) |
| `obi_fast` / `obi_slow` | EMA 5-bar y 20-bar del OBI |
| `spread_ticks` | spread en ticks en el cierre |

### En `obi_10s` (nueva tabla 2026-06-11)

Muestras a 10 segundos de OBI durante cada barra M1. ~6 muestras/minuto por símbolo.

| Campo | Contenido |
|-------|-----------|
| `ts_ms` | timestamp de la muestra |
| `symbol` | par |
| `obi_l5` / `obi_l10` / `obi_l20` | OBI en los 3 niveles |
| `spread_bps` | spread en bps en esa muestra |

Sirve para: detectar "OBI spike al cierre vs presión sostenida", analizar lag del OBI respecto al breakout, calibrar el gate de `obi_intrabar_mean`.

---

## Roadmap de calibraciones pendientes

| Filtro | Símbolo | Condición | Hipótesis |
|--------|---------|-----------|-----------|
| cum_delta threshold Short | BTC | n≥25 Shorts | losses cum_delta > +200 = fakeout |
| cum_delta threshold Short | BNB | n≥25 Shorts | < −500 discrimina wins (−78) de losses (−1357) |
| cum_delta threshold Short | SOL | n≥25 Shorts | > −20,000 discrimina wins (−9k) de losses (−43k) |
| obi_multi_depth gate | todos | n≥30 | requiere al menos un flag OBI para operar |
| obi_intrabar_mean análisis | todos | n≥30 + `obi_10s` acumulada | ¿presión sostenida predice mejor? |
| oi_mom_veto | BNB | n≥25 Shorts | > 6 barras OI = move maduro |
| trailing threshold | BNB | n≥20 trailing cases | posible 1.85R si gap sigue |
| time stop pre-breakout | todos | n≥20 pre-breakout trades | `bars_held` + `exit_reason` define cutoff |
| expansion inversión | SOL | investigación | revisar ATR del régimen |
| pre-breakout oi_max | SOL/XRP | n≥15 pre-breakout | puede ser distinto de BTC/ETH/BNB |
| Asia session | todos | ~mid-julio 2026 | acumular n≥30 antes de evaluar |

---

## Decisión: cuándo activar un filtro pendiente

La regla es la misma para todos: **calibrar con datos, nunca quitar señales.**

Un filtro se activa cuando:
1. La muestra del símbolo supera el mínimo de la tabla anterior
2. La diferencia win/loss es estadísticamente clara (>2× de diferencia)
3. El WR en el subconjunto filtrado supera el WR global del símbolo

Un filtro NO se activa si:
- El n es pequeño aunque el dato "parezca claro"
- La diferencia puede ser ruido (diferencia <1.5×)
- El símbolo tiene comportamiento invertido respecto al patrón global (→ bypass)
