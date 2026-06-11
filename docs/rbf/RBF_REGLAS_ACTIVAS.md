# RBF — Reglas Activas por Símbolo

*Última actualización: 2026-06-10 — post calibración v2 + modo pre-breakout activado*

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
| Cooldown | 60 barras desde última señal | operativo |
| Time stop | 30 barras M1 si en pérdida | calibrado |
| Trailing Short | activa en **1.75R**, distancia 1.2×ATR | live: 4 trailing cases cedían 0.97R/trade |
| Trailing Long | activa en **1.5R**, distancia 1.2×ATR | (Longs desactivados, pero si se reactivan) |

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

## Calibración por símbolo

### BTCUSDT

| Regla | Valor | Estado | Fuente |
|-------|-------|--------|--------|
| `expansion_max_bars` | **3** | ✅ activo | losses tenían 7.2 barras vs 4.0 wins |
| Trailing Short | 1.75R | ✅ activo | 2 trailing cases cediendo ~1.16R/trade |
| cum_delta gate Short | pendiente | ⏳ n<25 | losses cum_delta = +377 (compradores activos) |
| pre_breakout_oi_max | 3 | ✅ activo | análisis global |

**Insight BTC:** las pérdidas tienen cum_delta positivo (+377) — compradores agresivos cuando entras short. Las wins tienen OBI positivo + delta apenas negativo → absorción real, no presión vendedora directa.

---

### ETHUSDT

| Regla | Valor | Estado | Fuente |
|-------|-------|--------|--------|
| `expansion_max_bars` | **3** | ✅ activo | losses 4.8 barras vs wins 1.3 |
| Trailing Short | 1.75R | ✅ activo | calibración por dirección |
| obi_gate | pendiente | ⏳ n<20 | ETH invierte el OBI: wins OBI=-0.094 (ask pressure) |
| cum_delta Short | no discrimina | ✖ no aplica | -1468 wins vs -1320 losses — sin diferencia |
| pre_breakout_oi_max | 3 | ✅ activo | análisis global |

**Insight ETH:** único símbolo donde cum_delta no discrimina. La señal útil es OBI negativo (ask pressure real). Pendiente calibrar `obi_gate` direction-aware cuando n≥20.

---

### BNBUSDT

| Regla | Valor | Estado | Fuente |
|-------|-------|--------|--------|
| `expansion_max_bars` | **3** | ✅ activo | losses 7.8 barras vs wins 3.6 — mayor gap de todos |
| Trailing Short | 1.75R | ✅ activo | peores trailing cases: +0.90R y +0.80R vs target 2R |
| cum_delta gate Short | `> −500` candidato | ⏳ n<25 | wins avg −78 vs losses −1357 — gap de 1279 |
| oi_mom veto | `> 6` candidato | ⏳ n<25 | losses 9.1 barras vs wins 4.3 |
| trailing threshold | posible 1.85R | ⏳ pendiente | BNB tuvo los peores trailing exits |
| pre_breakout_oi_max | 3 | ✅ activo | análisis global |

**Insight BNB:** el patrón más claro de todos. Delta casi neutro en wins (-78) vs muy negativo en losses (-1357). Cuando BNB ya acumuló mucho selling el move está terminado. El filtro expansion tiene el mayor impacto aquí.

---

### SOLUSDT

| Regla | Valor | Estado | Fuente |
|-------|-------|--------|--------|
| `expansion_max_bars` | **None (bypass)** | ✅ activo | correlación INVERTIDA: wins 5.2 > losses 4.2 |
| `expansion_bars_recent` | se pasa 0 | ✅ activo | bypass para que el filtro nunca active |
| Trailing Short | 1.75R | ✅ activo | calibración por dirección |
| cum_delta gate Short | `> −20,000` candidato | ⏳ n<25 | wins avg −9,702 vs losses −43,138 |
| oi_mom (invertido) | no aplica aún | ⏳ n<25 | wins 8.2 barras vs losses 5.0 — también invertido |
| pre_breakout_oi_max | 3 | ✅ activo | análisis global (puede que SOL también tenga inversión) |

**Insight SOL:** dos señales están invertidas respecto al resto (expansion Y oi_mom). SOL tiene movimientos más explosivos — puede que la ventana de 25 barras capture un régimen diferente al del breakout real. Revisar cuando n≥25.

---

### XRPUSDT

| Regla | Valor | Estado | Fuente |
|-------|-------|--------|--------|
| `expansion_max_bars` | **None (bypass)** | ✅ activo | muestra insuficiente (n=1) |
| `expansion_bars_recent` | se pasa 0 | ✅ activo | bypass hasta n≥15 |
| Trailing Short | 1.75R | ✅ activo | calibración por dirección |
| Todo lo demás | pendiente | ⏳ n=1 | sin datos |

---

## Datos que se recolectan para futuras calibraciones

Cada trade cerrado guarda en Supabase:

| Campo | Para qué sirve |
|-------|----------------|
| `is_pre_breakout` | separar el análisis pre vs post |
| `bars_held` | calibrar time stop específico para pre-breakout por símbolo |
| `expansion_bars_recent` | ya en `confluence_flags` vía `RbfGateContext` |
| `oi_mom_bars_recent` | guardado en gate, futuro análisis por símbolo |

**Query de análisis pre-breakout cuando n≥20:**
```sql
SELECT symbol, bars_held, exit_reason, result_r
FROM rbf_signals
WHERE is_pre_breakout = true
ORDER BY bars_held;
```

---

## Roadmap de calibraciones pendientes

| Filtro | Símbolo | Condición para calibrar | Hipótesis |
|--------|---------|------------------------|-----------|
| cum_delta threshold Short | BTC | n≥25 Shorts | losses cum_delta>0 = fakeout con compradores |
| cum_delta threshold Short | BNB | n≥25 Shorts | >-500 discrimina wins (-78) de losses (-1357) |
| cum_delta threshold Short | SOL | n≥25 Shorts | >-20,000 discrimina wins (-9k) de losses (-43k) |
| obi_gate direction-aware | ETH | n≥20 Shorts | wins OBI<-0.05 = ask pressure real |
| oi_mom_veto | BNB | n≥25 Shorts | >6 barras OI = move maduro |
| trailing threshold | BNB | n≥20 trailing cases | posible 1.85R si gap sigue |
| time stop pre-breakout | todos | n≥20 pre-breakout trades | bars_held + exit_reason define cutoff óptimo |
| expansion inversión | SOL | investigación | revisar si ATR del régimen está calibrado igual |
| pre-breakout oi_max por símbolo | SOL/XRP | n≥15 pre-breakout | puede que gate sea distinto que BTC/ETH/BNB |

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
