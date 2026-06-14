# MTF System — Documentación Completa

**Última actualización:** 2026-06-14 (v2)  
**Estado:** Baseline v3 activo — Shorts (5 símbolos) + Longs (ETH/SOL) — reglas congeladas, pendiente walk-forward  
**Shorts v3 (14 días, 5 símbolos):** n=133+, WR≈58%, AvgR≈+0.55R, $500→$2,012 (+302%)  
**Longs baseline (8 días, ETH/SOL):** n=125, WR=56.0%, AvgR=+0.526R, $500→$1,748 (+250%)

---

## 1. Concepto

Sistema que combina tres capas de confirmación en ambas direcciones:

- **Filtro macro (Shorts: D1 EMA20 / Longs: H4 EMA20)**: contexto de tendencia
- **Señales M1 mineadas**: patrones detectados por análisis MFE/MAE sobre 9,000+ barras reales
- **Stop estructural H1**: H1_high/low + 0.3×ATR_H1, límite máximo 0.75% ← el edge vive aquí
- **Exit inteligente**: CVD exhaustion + OBI flip, o target fijo 2.5R

El problema que resuelve: sistemas M1 con stops ajustados (~0.11%) son destruidos por fees (0.07% RT = 0.62R/trade). Con stop H1 estructural < 0.75% el fee es 0.05–0.10R — viable.

**Tabla Supabase:** `mtf_trades` (campo `direction='Short'/'Long'`)  
**Trade recovery:** posiciones abiertas se restauran desde Supabase al arrancar el monitor (sobrevive redeploys de Railway)

---

## 2. Arquitectura General

```
Filtro macro
    Shorts: D1 EMA20 — precio < EMA20×0.995 → bear/neutral → permitir
    Longs:  H4 EMA20 — precio > EMA20×0.995 → bull/neutral → permitir

Sesión filter (ambas direcciones)
    OffHours / Asia → BLOQUEADO
    London / LondonNyOverlap / NewYork → PERMITIDO

M1 signal detector (ver §5 / §6)

Stop placement
    Shorts: stop = H1_high + 0.3×ATR_H1
    Longs:  stop = H1_low  - 0.3×ATR_H1
    Filtro estricto: 0.30% < stop_pct < 0.75%  ← crítico

Exit logic (simétrico para ambas direcciones)
    TAKE_PROFIT:    precio alcanza 2.5×risk en dirección correcta
    CVD_EXHAUSTION: ver §7
    STOP_LOSS:      precio toca stop estructural
    EXPIRED:        1200 barras M1 sin resolución (~20h)
```

---

## 3. Parámetros (compartidos)

| Parámetro | Valor | Notas |
|-----------|-------|-------|
| Capital inicial | $500 | — |
| Risk por trade | 2% compounding | — |
| Fee RT | 0.07% | maker entry + taker exit |
| MIN_STOP_PCT | 0.30% | — |
| MAX_STOP_PCT | 0.75% | **Crítico — edge colapsa arriba de aquí** |
| Target R | 2.5R fijo | score≥3 usa 3.5R (diferencia mínima con n=12) |
| CVD_FLIP_BARS | 5 | barras M1 consecutivas en dirección contraria |
| OBI_FLIP_THR | 0.15 | umbral abs(obi_fast) para confirmar CVD exit |
| MIN_PROFIT_CVD | 1.0R | no cierra por CVD si profit < 1.0R |
| Cooldown M1 | 30 barras | ~30 min entre señales por símbolo |
| Forward max | 1200 barras | ~20h máximo por trade |

---

## 4. Columnas de microestructura snapshot

Capturadas en entrada y guardadas en `mtf_trades`:

| Columna | Tipo | Descripción |
|---------|------|-------------|
| `direction` | TEXT | `'Short'` o `'Long'` |
| `obi_entry` | FLOAT8 | OBI fast en el momento de entrada |
| `cvd_slope_entry` | FLOAT8 | Slope del CVD M1 en entrada |
| `dz_score` | FLOAT8 | Delta z-score en entrada |
| `stacked_imb` | TEXT | `'Bearish'`, `'Bullish'` o vacío |
| `equal_low` / `equal_high` | BOOLEAN | Equal low/high H1 detectado |

**Migration pendiente:** `migrations/mtf_microstructure_cols.sql`
- Añade las columnas anteriores a `mtf_trades`
- Consolida longs en misma tabla con `direction` (ejecuta `DROP TABLE IF EXISTS mtf_long_trades`)

---

## 5. Detectores — MTF Shorts (BTC/ETH/SOL/BNB/XRP)

Filtro D1: precio < EMA20×0.995

### BTC (London + NY)
```python
if is_shoot and abs_ask and obif < 0:    → 'btc:shoot+ask+obi'
if is_shoot and is_london:               → 'btc:shoot+london'
```

### ETH (London + NY)
```python
if is_ny and oi_true and eq_hi:          → 'eth:ny+oi+eq'       # WR=78.6% n=14
if abs_ask and is_london and is_exp:     → 'eth:ask+london+exp'
if abs_ask and vpin > 0.6 and is_ny:    → 'eth:ask+vpin+ny'
```

### SOL (London + NY)
```python
if is_ny and vr > 4.0 and oi_true:      → 'sol:ny+vr4+oi'
if is_ny and vr > 4.0 and eq_hi:        → 'sol:ny+vr4+eq'
if eq_hi and is_london and is_exp:      → 'sol:eq+london+exp'
```

### BNB (**solo NY** — London WR=30-42% en todos los patrones)
```python
if not is_ny: return False
if eq_hi and oi_true:                   → 'bnb:eq+ny+oi'         # WR=80% n=5
if oi_true:                             → 'bnb:oi+ny'            # WR=56.5% n=23
```

### XRP (**solo NY** — London WR=26-39% en todos los patrones)
```python
if not is_ny: return False
if eq_hi and oi_true:                   → 'xrp:eq+ny+oi'         # WR=66.7% n=3
if abs_ask:                             → 'xrp:ask+ny'           # WR=57.1% n=7
if oi_true:                             → 'xrp:oi+ny'            # WR=57.1% n=7
```

---

## 6. Detectores — MTF Longs (ETH/SOL)

Filtro H4: precio > EMA20×0.995 en H4 (más sensible que D1, captura recuperaciones intraday)  
Stop: H1_low - 0.3×ATR_H1  
**BTC/BNB/XRP: sin edge en longs** (todos los patrones negativos en minería)

### ETH (London + NY)
```python
if stacked_bull and is_london:          → 'eth:stacked_bull+london'
if stacked_bull and is_ny:              → 'eth:stacked_bull+ny'
if is_hammer and dz_buy:                → 'eth:hammer+dz_buy'
if eq_low and is_london and is_exp:     → 'eth:eq_low+london+exp'
if oi_true and is_ny:                   → 'eth:oi+ny'
```

### SOL (London + NY)
```python
if is_hammer and is_london:             → 'sol:hammer+london'
if stacked_bull and is_london:          → 'sol:stacked_bull+london'
if is_hammer and obi_pos:               → 'sol:hammer+obi_pos'
if oi_true and is_london:               → 'sol:oi+london'
if eq_low and is_london:                → 'sol:eq_low+london'
```

**Backtest baseline longs (8 días, ETH+SOL):**

| Símbolo | n | WR% | AvgR | Total R |
|---------|---|-----|------|---------|
| ETH | ~65 | ~54% | +0.5R | — |
| SOL | ~60 | ~58% | +0.55R | — |
| **TOTAL** | **125** | **56.0%** | **+0.526R** | — |

**Equity: $500 → $1,748 (+250% en 8 días)**

---

## 7. Exit: CVD Exhaustion

```python
# Shorts: busca reversión alcista (compradores retomando)
CVD_FLIP_BARS  = 5     # barras M1 con cvd_slope > 0 (comprando)
OBI_FLIP_THR   = 0.15  # obi_fast > +0.15

# Longs: busca reversión bajista (vendedores retomando)
CVD_FLIP_BARS  = 5     # barras M1 con cvd_slope < 0 (vendiendo)
OBI_FLIP_THR   = 0.15  # obi_fast < -0.15

MIN_PROFIT_CVD = 1.0   # solo cierra si profit >= 1R (ambas direcciones)
```

**Auditoría de CVD exit (14 días, n=35 CVD trades en shorts):**

| Escenario post-exit | n | % |
|---------------------|---|---|
| Precio rebotó/lateral (salida correcta) | 18 | 51% |
| Precio continuó >0.5R (salimos pronto) | 17 | 49% |

- AvgR capturado: +1.24R; adicional disponible: +0.64R
- **Conclusión: no tocar.** Las 3 variantes calibradas (bars=8, minR=1.5R, ambos) empeoran el equity neto.

---

## 8. Confluence Score — Shorts (0–6)

Puntos informativos, no filtran señales. Usan snapshot de microestructura en entrada:

| Factor | Condición | Puntos |
|--------|-----------|--------|
| Stacked imbalance bajista | `stacked_imb == 'Bearish'` | +1 |
| Sin thin zone arriba | `thin_above == False` | +1 |
| Bar delta negativo | `bar_delta < -50` | +1 |
| OBI L5 negativo | `obi_l5 < -0.2` | +1 |
| Delta Z-score negativo | `dz < -0.5` | +1 |
| OI momentum alineado | `oi_momentum == True` | +1 |

**Distribución observada (14 días):**

| Score | n | WR% | AvgR |
|-------|---|-----|------|
| 0 | 15 | 46.7% | +0.20R |
| 1 | 41 | 58.5% | +0.57R |
| 2 | 28 | 60.7% | +0.59R |
| **3** | **12** | **83.3%** | **+1.24R** |
| 4 | 1 | 0.0% | -1.11R |

Score=3+ es el setup de más alta convicción (n=12, insuficiente para filtrar aún).

---

## 9. Resultados — MTF Shorts por versión

### Baseline v3 — 2026-06-14 (14 días, 5 símbolos)

| Símbolo | n | WR% | AvgR | Total R |
|---------|---|-----|------|---------|
| BTC | 63 | 55.6% | +0.439R | +27.66R |
| ETH | 14 | **78.6%** | **+0.902R** | +12.63R |
| SOL | 21 | 57.1% | +0.715R | +15.03R |
| BNB | 23 | 56.5% | +0.393R | +9.03R |
| XRP | 12 | 58.3% | +0.708R | +8.50R |
| **TOTAL** | **133** | **58.6%** | **+0.548R** | **+72.84R** |

**Equity: $500 → $2,012 (+302% en 14 días)**

### v2 — 2026-06-13 (8 días, BTC/ETH/SOL, horas bloqueadas)
n=63, WR=71.4%, AvgR=+0.904R, $500→$1,437 (+187%)

### v1 — 2026-06-12 (8 días, BTC/ETH/SOL)
n=137, WR=52.6%, AvgR=+0.343R, $500→$1,173 (+134%)

---

## 10. Análisis de distribución

Hallazgo crítico de stop_pct (aplica a ambas direcciones):

```
stop < 0.75%    WR=65.8%  AvgR=+0.732R  ← TODO el edge aquí
0.75–1.50%      WR=38.3%  AvgR=-0.141R  ← edge negativo
stop > 1.50%    WR=12.5%  AvgR=-0.650R  ← destruye capital
```

---

## 11. Minería de Patrones

**Scripts:**
- `apps/rbf-review/api/mtf_analysis.py` — análisis stop_pct/distribución
- `apps/rbf-review/api/mtf_bnb_xrp_calibrate.py` — calibración BNB/XRP (shorts)
- `apps/rbf-review/api/mtf_longs_mine.py` — minería MFE/MAE para longs ETH/SOL
- `apps/rbf-review/api/mtf_loss_autopsy.py` — autopsia losers
- `apps/rbf-review/api/mtf_winner_dna.py` — ADN big winners vs losers

**Metodología:** fetch barras M1 → extraer flags booleanos → medir MFE/MAE en horizonte 60 barras → WR = % donde MFE > MAE → filtrar n ≥ 4.

**Hallazgos (shorts, 14 días):**
- thin_above/thin_below **no discriminan** — igual presencia en winners y losers
- OI_MOMENTUM: +33pp de edge en big winners (54% vs 21% en losers)
- VWAP: precio bajo VWAP en entry → WR=68-75% vs sobre VWAP → WR=57%
- LondonNyOverlap: 50% de big winners con solo 35/133 trades totales
- BNB/XRP: London inviable en longs y shorts — solo NY+OI tiene edge

---

## 12. Decisiones

| Decisión | Razón |
|----------|-------|
| Sin bloqueo de horas | Sobreajuste con <100 trades |
| CVD bars=5, minR=1.0R | Las 3 alternativas probadas empeoran el neto |
| BNB/XRP solo en shorts NY | London WR=26-42% en todos los patrones |
| BTC/BNB/XRP sin longs | Edge negativo en minería |
| Score como info, no filtro | Filtrar score≥2 → n=41, menos PnL total |
| Longs filtro H4 (no D1) | H4 captura recuperaciones intraday; D1 demasiado lento |
| Una sola tabla `mtf_trades` con `direction` | Simplifica queries, longs y shorts comparten estructura |

---

## 13. Trade Recovery (redeploy-safe)

### Problema
El monitor corre en Railway. En cada redeploy el proceso muere y toda la memoria volátil desaparece, incluyendo `active_trade`. Un trade que estaba a 0.1R del TP podía perderse completamente.

### Solución implementada (2026-06-14)

**Flujo al arrancar el monitor por símbolo:**

```
1. sb.load_mtf_active(symbol, "Short")
   → SELECT * FROM mtf_trades WHERE is_open=true AND symbol=X AND direction='Short'
   → devuelve Option<RestoredHtfTrade>

2. mtf_state.restore_active_trade(pos.entry, pos.stop, pos.target, ...)
   → reconstruye ActiveTrade { entry, stop, risk, target, fee_r, signal, bars_in_trade: 0 }

3. warm_up_history(bars_m1_cached)
   → replay de barras históricas desde el arranque
   → si SL/TP fue alcanzado durante la caída, se cierra correctamente
   → si sigue abierto, queda en active_trade listo para la siguiente barra live
```

Aplica igual para Short y Long (ETHUSDT/SOLUSDT). Código en `crates/monitor/src/main.rs` (bloque después del restore de BE).

**Struct restaurado:**
```rust
pub struct RestoredHtfTrade {
    pub entry_at: String, pub ts_ms: i64, pub sig: String,
    pub session: String, pub trend: String,
    pub entry: f64, pub stop: f64, pub target: f64, pub stop_pct: f64,
    pub obi_entry: f64, pub cvd_slope_entry: Option<f64>,
    pub dz_score: f64, pub stacked_imb: String, pub equal_low: bool,
}
```

---

## 13b. Pendiente

### Migraciones Supabase
- [x] `migrations/mtf_microstructure_cols.sql` — **EJECUTADA** 2026-06-14: añadió `direction`, `obi_entry`, `cvd_slope_entry`, `dz_score`, `stacked_imb`, `equal_low` a `mtf_trades` + dropó tabla antigua
- [x] `migrations/htf_to_mtf_rename.sql` — **EJECUTADA** 2026-06-14: `htf_trades` → `mtf_trades`
- [ ] `migrations/vp_levels_bars.sql` — columnas VP (vp_poc, vp_vah, vp_val, vp_lvn_below) en tablas de barras

### Walk-forward (~2026-07-05)
- [ ] `shorts_mtf_backtest.py --days 30` sobre datos post-2026-06-13 (shorts baseline frozen)
- [ ] `mtf_longs_backtest.py --days 30` sobre datos post-2026-06-14 (longs baseline frozen)
- [ ] Criterio pass: WR ≥ 55% y AvgR ≥ +0.30R (ambas direcciones por separado)
- [ ] Si falla: recalibrar patrones (no agregar filtros ad-hoc)

### Con 200+ trades (30+ días)
- [ ] VWAP como filtro de entry (WR=68-75% bajo VWAP vs 57% sobre)
- [ ] Score≥3 como mínimo si n≥30 en ese bucket
- [ ] VP floor exit usando VAL como target (WR=81-87% cuando VAL en zona 1-2.5R)
- [ ] Más patrones ETH longs (WR=78.6% en shorts — ¿igual potencial en longs?)
- [ ] BNB/XRP longs con más datos (actualmente sin edge)

### Investigación
- [ ] **LondonNyOverlap**: 50% de big winners — ¿qué la diferencia de London pura?
- [ ] **Entrada anticipada**: señal M1 que anticipe estructura H1 antes de que forme
- [ ] `btc:shoot+ask+obi` en más datos (n=2, insuficiente)
- [ ] Longs BTC: revisar si con más datos emerge edge (n<10 en todos los patrones actuales)
- [ ] **H4 filter en shorts** — validar en walk-forward (ver §17)

---

## 14. Archivos

| Archivo | Descripción |
|---------|-------------|
| `data/src/strategy/detectors/mtf_shorts_detector.rs` | Detector shorts + `restore_active_trade()` |
| `data/src/strategy/detectors/mtf_longs_detector.rs` | Detector longs ETH/SOL + `restore_active_trade()` |
| `crates/monitor/src/supabase_writer.rs` | `load_mtf_active()` + `RestoredHtfTrade` + `write_mtf_long_trade()` → `mtf_trades` |
| `crates/monitor/src/main.rs` | Bloque de recovery en startup por símbolo (Short + Long) |
| `apps/rbf-review/api/shorts_mtf_backtest.py` | Backtest shorts — 5 símbolos |
| `apps/rbf-review/api/mtf_longs_backtest.py` | Backtest longs — ETH/SOL |
| `apps/rbf-review/api/mtf_longs_mine.py` | Minería MFE/MAE longs |
| `apps/rbf-review/api/mtf_bnb_xrp_calibrate.py` | Calibración BNB/XRP |
| `apps/rbf-review/api/mtf_loss_autopsy.py` | Autopsia losers |
| `apps/rbf-review/api/mtf_winner_dna.py` | ADN big winners |
| `apps/rbf-review/api/mtf_cvd_exit_audit.py` | Auditoría CVD exit |
| `apps/rbf-review/api/mtf_cvd_calibrate.py` | Comparación variantes CVD |
| `apps/rbf-review/api/mtf_confluence.py` | Análisis confluence score |
| `apps/rbf-review/api/mtf_analysis.py` | Análisis stop_pct/distribución |
| `apps/rbf-review/api/mtf_vp_target.py` | VP como target (POC/VAL/LVN) |
| `apps/rbf-review/api/mtf_experiments.py` | Experimentos OBI/CVD/Delta |
| `scripts/mtf_audit.py` | Breakdown por dirección×sesión, símbolo, patrón, hora UTC, exit reason |
| `apps/rbf-review/src/views/MTFModuleView.tsx` | UI del módulo — métricas Short/Long separadas + selector backtest |
| `apps/rbf-review/vite.config.ts` | Middleware `/api/backtest/mtf_combined` (corre ambos scripts en paralelo) |
| `migrations/mtf_microstructure_cols.sql` | **EJECUTADA** 2026-06-14 — microestructura + consolidación tabla |
| `migrations/vp_levels_bars.sql` | **PENDIENTE** — VP en tablas de barras |
| `docs/mtf/MTF_SHORTS_SISTEMA.md` | Este documento |
| `docs/MTF_STRATEGY_RULES.md` | Reglas exactas congeladas — referencia para walk-forward |

---

## 15. UI — MTFModuleView

### Header stats row
Muestra métricas separadas por dirección + equity combinada:

```
Short  n | WR | Avg R | Total  │  Long  n | WR | Avg R | Total  │  Equity
```

- Color de WR: verde ≥55%, amarillo 45–55%, rojo <45%
- En modo **live**: filtra `mtf_trades` por `direction` desde Supabase
- En modo **backtest**: filtra `btTrades` por `dir='Short'/'Long'` (recibidos via `onTrades` prop de `BacktestView`)

### Selector de backtest
Tres opciones: **Combined / Shorts / Longs**

- Combined: `vite.config.ts` corre ambos scripts Python en paralelo (`Promise.all`), fusiona trades ordenados por timestamp, recalcula stats
- Shorts: `shorts_mtf_backtest.py` directo
- Longs: `mtf_longs_backtest.py` directo

### Audit script
`scripts/mtf_audit.py` extrae `mtf_trades` desde Supabase (REST API directa, sin SDK) y genera breakdown en consola:

- **Por dirección × sesión** — cuántas señales por contexto
- **Por símbolo** — distribución de trades
- **Por patrón** — WR/AvgR por sig key
- **Por hora UTC** — flujo horario de señales
- **Por exit reason** — TP / SL / CVD / EXPIRED
- **Por stop_pct bucket** — confirma que el edge vive en <0.75%
- **Microestructura** — OBI/CVD/DZ en winners vs losers

---

## 16. Evolución del Sistema

1. **Microscalping M1** → fees destruyen el edge (stop 0.11%)
2. **MTF H1→M1 con trailing** → trailing cerraba prematuramente en mecha
3. **Target swing_low H1** → targets de $2,000 en BTC, inalcanzables
4. **Target fijo 2.5R** → correcto, pero stop placement incorrecto
5. **Detección M1 + stop swing_high_10barras** → WR=39%
6. **Detección M1 mineada + stop H1 estructural** → n=159, WR=50%, +125%
7. **v1: poda de patrones débiles** → n=137, WR=52.6%, +134%
8. **v2: análisis distribución + calibración** → n=63, WR=71.4%, +187%
9. **v3 shorts: sin bloqueo horas + BNB/XRP calibrados + oi_momentum** → n=133, WR=58.6%, $2,012 ✓
10. **Longs: detector espejo ETH/SOL con H4 EMA20** → n=125, WR=56.0%, $1,748 ✓
11. **Microestructura snapshot**: `obi_entry`, `cvd_slope_entry`, `dz_score`, `stacked_imb`, `equal_low` guardados por trade
12. **Unificación tabla**: longs ahora escriben a `mtf_trades` con `direction='Long'` (antes: tabla `mtf_long_trades` inexistente → pérdida silenciosa de todos los longs)
13. **Trade recovery redeploy-safe**: `load_mtf_active()` + `restore_active_trade()` → open trades sobreviven reinicios de Railway
14. **UI métricas separadas**: MTFModuleView header muestra Short | Long independientemente; selector Combined/Shorts/Longs en tab backtest
15. **Funding regime filter (shorts)**: bloquea entrada en `ExtremeLong` y `ElevatedShort` — +$375 PnL en 14d in-sample (ver §18)
16. **Warmup recovery completo**: warm_up_history ahora pasa barras históricas por `mtf_state`/`mtf_longs_state` post-restart; cierra TP/SL perdidos durante downtime (ver §19)
17. **Chart fix TradeChart**: trades OPEN proyectan box hasta `entry + 20h`; visible range sincronizado con el box para que `timeToCoordinate` funcione correctamente

---

## 16. Advertencia de Overfitting

Los patrones de BTC/ETH/SOL fueron minados y backtestados sobre los **mismos datos**. BNB/XRP (shorts) y ETH/SOL (longs) fueron calibrados sobre los mismos días.

**Reglas congeladas:** 2026-06-14  
**No modificar** detectores ni parámetros hasta walk-forward.  
**Fecha objetivo walk-forward:** ~2026-07-05  
**Criterio pass:** WR ≥ 55% y AvgR ≥ +0.30R sobre datos nuevos (ambas direcciones)

---

## 18. Funding Regime Filter — Shorts (2026-06-14)

### Concepto

"Gamma environment": el régimen de funding rate como variable de contexto de mercado. No es vol implícita de opciones, sino el régimen de funding de perps como proxy de posicionamiento institucional.

### Lógica de bloqueo para shorts

| Régimen | Por qué bloquear |
|---------|-----------------|
| `ExtremeLong` | Arbitrageurs compran spot para cobrar funding → sostienen precio arriba → shorts contra el flujo |
| `ElevatedShort` | Shorts pagando mucho → squeeze inminente → precio sube → shorts en peligro |
| `ElevatedLong` | Permiten — WR positivo |
| `Neutral` | Permiten — base |
| `ExtremeShort` | Permiten — precio ya bajó, shorts bien posicionados |

### Para longs: sin filtro
El espejo exacto no aplica: incluso en el "peor" régimen para longs (`Neutral`, WR=47.8%), el AvgR sigue siendo positivo (+0.34R). Quitar trades positivos empeora el PnL absoluto.

### Resultados backtest in-sample (14 días)

| Variante | n | WR% | AvgR | PnL |
|----------|---|-----|------|-----|
| Base | 133 | 58.6% | +0.548R | $1,512 |
| + Filtro funding (bloquea ExtremeLong+ElevatedShort) | ~95 | ~65% | +0.71R | **$1,887 (+$375)** |
| + OI Declining | — | +WR | — | peor PnL (quita trades +0.15R avg) |

**Decisión**: solo filtro funding en shorts. OI Declining descartado.

### Implementación

`data/src/strategy/detectors/mtf_shorts_detector.rs`, step 3b en `on_bar_close()`:
```rust
// Paso 3b — DESPUÉS del active trade check (step 1), antes de signal detection (step 4)
if matches!(ctx.funding_regime.as_str(), "ExtremeLong" | "ElevatedShort") {
    return None;
}
```

`MtfBarContext` tiene campo `funding_regime: String`. El monitor lo puebla en `main.rs`:
```rust
let htf_funding_regime = ctx.institutional
    .as_ref()
    .map(|inst| format!("{:?}", inst.funding.regime))
    .unwrap_or_else(|| "Neutral".into());
```

El warmup usa `funding_regime: "Neutral".into()` → nunca bloquea recovery de trades ya abiertos.

---

## 19. Bug: Warmup Recovery MTF (detectado y fixeado 2026-06-14)

### El bug

`warm_up_history` alimentaba barras históricas post-restart a `rbf_paper` y `be_paper` para detectar SL/TP golpeados durante el downtime, pero **nunca llamaba `mtf_state.on_bar_close()`** con esas barras.

Consecuencia: si el monitor se reiniciaba (Railway rolling restart, nuevo deploy) y el TP/SL de un trade MTF se golpeaba durante los minutos/horas de downtime, el trade quedaba stuck OPEN en Supabase para siempre.

**Caso real (2026-06-14):**
- Trade BTC Short abrió 12:36 UTC, entry=64334, TP=63704.9
- TP golpeado a las 17:56 UTC (low=63650 en M1)
- Monitor se reinició entre medio → warmup viejo no procesó `mtf_state` → trade quedó OPEN
- Se detectó y se parchó manualmente en Supabase

### Fix 1: warmup procesa mtf_state

`crates/monitor/src/main.rs` — dentro del loop de `warm_up_history`, después del bloque `be_paper`:

```rust
if state.mtf_state.has_active_trade() {
    if let Some(entry_ms) = state.mtf_state.active_entry_ms() {
        if open_ms > entry_ms {
            // contexto minimal — cvd_slope=None suprime CVD exhaustion,
            // solo evalúa TP/SL/EXPIRED
            let warmup_ctx = MtfBarContext { high, low, close, ts_ms: open_ms,
                cvd_slope: None, funding_regime: "Neutral".into(), ... };
            if let Some(closed) = state.mtf_state.on_bar_close(&warmup_ctx) {
                // PATCH Supabase con el cierre
            }
        }
    }
}
// idem para mtf_longs_state
```

Nuevos métodos públicos en `MtfShortsState` y `MtfLongsState`:
- `has_active_trade() -> bool`
- `active_entry_ms() -> Option<i64>`

### Fix 2: warmup dinámico (barras desde la entrada)

**Antes**: `warm_up_history(150)` — siempre 150 barras = 2.5h de cobertura.

**Problema**: si el downtime dura más de 2.5h, el TP/SL sigue sin detectarse.

**Fix**: calcular cuántas barras han pasado desde la entrada del trade más antiguo:

```rust
let warmup_limit = if earliest_entry_ms < i64::MAX {
    let bars_since_entry = ((now_ms - earliest_entry_ms) / (tf_min * 60_000)) as usize;
    (bars_since_entry + 20).clamp(150, 1500)  // mínimo 150, máximo 1500 (25h = FORWARD_MAX)
} else {
    150
};
warm_up_history(&mut state, &symbol_str, tf_min, warmup_limit).await;
```

Log al arrancar:
```
[warmup] posición restaurada hace ~380 barras → cargando 400 barras
```

**Cobertura garantizada**: cualquier restart de Railway (segundos a minutos) queda dentro de las barras cargadas. Un downtime de hasta 25h (máximo de vida de un trade) queda cubierto.

---

## 20. Chart: Trades OPEN en TradeChart (2026-06-14)

### Bug

El box del trade OPEN se dibujaba solo hasta "ahora" porque:

1. **`durationMin` mal usado**: en trades abiertos, `durationMin` = barras transcurridas desde la entrada (no la duración total proyectada). Usar `exitTs = entry + durationMin * 60` hacía que el box terminara en "now".

2. **`visibleRange` no extendido**: la corrección de `exitTs = entry + 20h` no bastaba porque `chart.timeScale().setVisibleRange({ to: nowSec + 10bars })` limitaba el rango visible. `timeToCoordinate(entry + 20h)` devolvía `null` → el box no se renderizaba más allá del borde derecho.

### Fix

```typescript
// 1. exitTs correcto para trades abiertos
const FORWARD_MAX_SEC = 20 * 3600
let exitTs = t.isOpen ? t.ts + FORWARD_MAX_SEC : t.ts + 90 * 60

// 2. visibleRange sincronizado con el box
const FORWARD_MAX_S = 20 * 3600
const closedAtSec = trade.isOpen
  ? trade.ts + FORWARD_MAX_S   // proyectar horizonte completo
  : /* fecha real de cierre */

// visTo alcanza entry+20h para que timeToCoordinate tenga coordenadas válidas
const visTo = trade.isOpen
  ? (trade.ts + FORWARD_MAX_S + rightPadBars * barSec) as Time
  : (closedAtSec + rightPadBars * barSec) as Time

// 3. fetch solo hasta "now" (barras futuras no existen)
const fetchEndSec = trade.isOpen ? nowSec : closedAtSec
```

El chart muestra candles hasta "now" y espacio vacío a la derecha hasta `entry + 20h`, igual que TradingView — el box siempre alcanza las zonas de TP y SL.

---

## 17. Experimento: H4 vs D1 como filtro macro en Shorts (2026-06-14)

### Pregunta
¿Usar H4 EMA20 en vez de D1 EMA20 como filtro de tendencia en shorts produce mejores resultados?

### Metodología
- Script: `apps/rbf-review/api/mtf_shorts_backtest.py --days 14` (baseline D1)
- Mismo script con `--h4-filter` (H4 alternativo)
- Mismos patrones M1, mismo stop H1, mismo capital
- Datos: 5 símbolos, ~9-10 días activos (desde STARTS hardcodeados ~2026-06-05)

### Resultados

| Filtro | n | WR% | AvgR | TotalR | Equity |
|--------|---|-----|------|--------|--------|
| **D1 EMA20 (baseline)** | 153 | 52.3% | +0.359R | +55.0R | $1,399 |
| **H4 EMA20 (alternativa)** | 55 | 78.2% | +1.138R | +62.6R | $1,691 |

### Desglose por símbolo

| Símbolo | D1: n / WR / AvgR | H4: n / WR / AvgR |
|---------|-------------------|-------------------|
| BTC | 69 / 50.7% / +0.30R | 30 / 76.7% / +1.02R |
| ETH | 17 / 70.6% / +0.66R | 3 / 100% / +2.04R |
| SOL | 26 / 50.0% / +0.49R | 5 / 100% / +2.20R |
| BNB | 26 / 50.0% / +0.21R | 7 / 71.4% / +0.55R |
| XRP | 15 / 46.7% / +0.33R | 10 / 70.0% / +1.09R |

### Desglose por sesión

| Sesión | D1: n / WR / AvgR | H4: n / WR / AvgR |
|--------|-------------------|-------------------|
| London | 52 / 44.2% / +0.11R | 18 / 77.8% / +1.01R |
| LondonNyOverlap | 43 / 60.5% / +0.67R | 13 / 84.6% / +1.44R |
| NewYork | 58 / 53.4% / +0.35R | 24 / 75.0% / +1.07R |

### Trades que separan los dos filtros

**D1 permite, H4 bloquea (n=102):** WR=40.2%, AvgR≈0.00R, TotalR=-0.3R
- Son los trades donde el H4 ya está en "bull" pero el D1 aún dice "bear/neutral"
- XRP especialmente malo: n=5, WR=0%, AvgR=-1.19R
- BTC: n=42, WR=35.7%, AvgR=-0.12R — destruye el edge

**H4 permite, D1 bloquea (n=4):** WR=100%, AvgR=+1.82R, TotalR=+7.3R
- Son trades en zona H4 bear donde D1 ya cambió a bull
- Muestra que H4 captura oportunidades que D1 ciega (n muy pequeño)

### Interpretación

El H4 como filtro es **mecánicamente más restrictivo** para shorts: solo permite entrar cuando el H4 (las últimas 4 horas) confirma tendencia bajista. El D1 permite shorts incluso cuando el H4 ya está subiendo — esos son exactamente los trades con WR=40% y AvgR≈0.

El H4 en este período fue un **pre-filtro de momentum de corto plazo**: en las sesiones donde el H4 estaba en "bear" o "neutral", el precio tenía mayor continuidad bajista intraday.

### ⚠️ Advertencia crítica de sobreajuste

El salto de WR es extremo (+26pp) con n muy reducido (55 vs 153 trades). Las señales de alarma:
- ETH y SOL tienen n=3 y n=5 respectivamente en H4 — insuficiente para cualquier conclusión
- El período de 14 días tiene régimen de mercado específico; H4 puede haberse sincronizado con él por azar
- Los resultados D1 en este backtest (WR=52.3%) ya difieren del baseline documentado (WR=58.6%) — sugiere que los datos son ligeramente distintos

**No cambiar el filtro ahora.** El baseline está congelado para el walk-forward del ~2026-07-05.

### Plan para walk-forward

Correr **ambas versiones en paralelo** sobre datos post-2026-06-14:
```
python mtf_shorts_backtest.py --days 30            # D1 baseline
python mtf_shorts_backtest.py --days 30 --h4-filter # H4 alternativa
```
Si H4 mantiene WR ≥ 58% con n ≥ 50 trades nuevos → candidato a reemplazar D1.
Si la ventaja desaparece → D1 sigue siendo el filtro correcto.
