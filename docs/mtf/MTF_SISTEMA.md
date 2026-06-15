# MTF System — Documentación Completa

**Última actualización:** 2026-06-15 (v3 + EXP8 — regime filter activado en live)  
**Estado:** Baseline v3 activo + EXP8 Regime Filter — Shorts (5 símbolos) + Longs (ETH/SOL)  
**Shorts baseline (14 días, 5 símbolos):** n=180, WR=50.6%, AvgR=+0.274R, Equity=$1,237  
**Shorts + EXP8 (14 días, backtest):** n=168, WR=52.4%, AvgR=+0.336R, Equity=$1,432 ← activo en live  
**Longs baseline (8 días, ETH/SOL):** n=125, WR=56.0%, AvgR=+0.526R, $500→$1,748 (+250%)  
**Campos live acumulando:**
- Desde 2026-06-15: `big_trade_bearish/bullish`, `obi_min/max_intrabar` — re-mining BTC ~Jun 20
- Desde 2026-06-15: `vp_poc/vah/val/lvn_below`, `cvd_consec_neg/pos`, `prev_bar_delta`, `bars_since_low_vr` — minar ~Jun 20-25

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

Funding regime filter (shorts)
    ExtremeLong / ElevatedShort → BLOQUEADO (ver §18)

EXP8: Regime filter (shorts, activo 2026-06-15)
    TrendDown → BLOQUEADO (WR=44.4%, shorts persiguen caída establecida)
    TrendUp / Expansion / Chop → PERMITIDO (ver §24)

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
if bt_bear and oi:                       → 'btc:bt_bear+oi'        # (2026-06-15, prioridad A0)
if is_shoot and abs_ask and obif < 0:    → 'btc:shoot+ask+obi'
if is_shoot and is_london:               → 'btc:shoot+london'
```

- `bt_bear`: `big_trade_bearish == True` — footprint con volumen >2.5× media de la barra, dominado por sellers en la mitad superior (institucional vendiendo en la mecha)
- `oi`: `oi_momentum == True` (OI expandiéndose en dirección bajista)
- Patrón en fase de acumulación de datos (activo desde 2026-06-15). Sin resultados aún — pendiente re-mining en ~5 días.

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
| **EXP8 TrendDown bloqueado (activo)** | WR=44.4% peor regime; TrendUp WR=71.4% mejor (liquidity hunt). Backtest: -12 trades, +$195 equity |
| EXP7 VWAP Selectivo — no activado aún | Mejor WR/AvgR pero equity $1,214 < EXP8 $1,432; EXP8 retiene 93% de trades vs 59% de EXP7 |
| EXP9 Regime+VWAP — no activado | Mejor calidad (WR=57.4%) pero equity $1,158 < baseline; poda excesiva |

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
- [x] `migrations/big_trade_bars.sql` — **EJECUTADA** 2026-06-15: añadió `big_trade_bearish BOOLEAN DEFAULT FALSE` y `big_trade_bullish BOOLEAN DEFAULT FALSE` a `btc_bars`, `eth_bars`, `bnb_bars`, `sol_bars`, `xrp_bars`
- [x] `migrations/obi_intrabar_bars.sql` — **EJECUTADA** 2026-06-15: añadió `obi_min_intrabar FLOAT8` y `obi_max_intrabar FLOAT8` a todas las tablas de barras
- [x] `migrations/vp_levels_bars.sql` — **EJECUTADA** 2026-06-15: añadió `vp_poc`, `vp_vah`, `vp_val`, `vp_lvn_below FLOAT8` a todas las tablas de barras (el Rust ya escribía estos valores; las columnas faltaban y los datos se perdían silenciosamente)
- [x] `migrations/multibar_context_bars.sql` — **EJECUTADA** 2026-06-15: añadió `cvd_consec_neg INT2`, `cvd_consec_pos INT2`, `prev_bar_delta FLOAT8`, `bars_since_low_vr INT2` a todas las tablas de barras

### Walk-forward (~2026-07-05)
- [ ] `shorts_mtf_backtest.py --days 30` sobre datos post-2026-06-13 (shorts baseline frozen)
- [ ] `mtf_longs_backtest.py --days 30` sobre datos post-2026-06-14 (longs baseline frozen)
- [ ] Criterio pass: WR ≥ 55% y AvgR ≥ +0.30R (ambas direcciones por separado)
- [ ] Si falla: recalibrar patrones (no agregar filtros ad-hoc)

### Re-mining BTC (~2026-06-20)
- [ ] `btc_shorts_mine.py` con 5+ días de `big_trade_bearish` + `obi_min/max_intrabar` acumulados
- [ ] Evaluar WR/AvgR del patrón `btc:bt_bear+oi` con datos reales
- [ ] Evaluar si `obi_min_neg30` o `obi_range_wide` discriminan winners de losers
- [ ] Evaluar `cvd_consec_neg` (≥3 barras consecutivas) como feature adicional al patrón
- [ ] Evaluar `prev_bar_delta < 0` como pre-condición (barra anterior ya vendedora)
- [ ] Evaluar `bars_since_low_vr` 1-5 (breakout de compresión) vs >10 (momentum establecido)

### Mining VP (~2026-06-25, 7+ días de datos)
- [ ] `mtf_vp_target.py` con datos reales de `vp_poc/vah/val/lvn_below` en `btc_bars`
- [ ] Evaluar VAL como target dinámico cuando VAL está en zona 1.0–2.5R
- [ ] Evaluar LVN como target (zona de vacío — precio vuela sin resistencia)
- [ ] Comparar equity VAL-target vs baseline 2.5R fijo

### Con 200+ trades (30+ días)
- [ ] VWAP como filtro de entry (WR=68-75% bajo VWAP vs 57% sobre)
- [ ] Score≥3 como mínimo si n≥30 en ese bucket
- [ ] Más patrones ETH longs (WR=78.6% en shorts — ¿igual potencial en longs?)
- [ ] BNB/XRP longs con más datos (actualmente sin edge)
- [ ] VWAP rastro para **longs** (ver §22 — descartado para shorts; aplica mejor en tendencia alcista)

### Investigación
- [ ] **LondonNyOverlap**: 50% de big winners — ¿qué la diferencia de London pura?
- [ ] **Entrada anticipada**: señal M1 que anticipe estructura H1 antes de que forme
- [ ] `btc:shoot+ask+obi` en más datos (n=2, insuficiente)
- [ ] Longs BTC: revisar si con más datos emerge edge (n<10 en todos los patrones actuales)
- [ ] **H4 filter en shorts** — validar en walk-forward (ver §17)

### Descartado (no re-explorar sin nuevos datos)
- [x] VWAP rastro como target en shorts — descartado 2026-06-15 (ver §22)

---

## 14. Archivos

| Archivo | Descripción |
|---------|-------------|
| `data/src/strategy/detectors/mtf_shorts_detector.rs` | Detector shorts + `restore_active_trade()` + `MtfBarContext` |
| `data/src/strategy/detectors/mtf_longs_detector.rs` | Detector longs ETH/SOL + `restore_active_trade()` |
| `crates/monitor/src/supabase_writer.rs` | `load_mtf_active()` + `RestoredHtfTrade` + `write_rbf_bar()` con big_trade + obi_intrabar |
| `crates/monitor/src/main.rs` | Recovery startup + obi_min/max_intrabar cálculo + warmup 1500-cap fix |
| `apps/rbf-review/api/btc_shorts_v1.py` | Backtest live BTC shorts + patrón `btc:bt_bear+oi` |
| `apps/rbf-review/api/btc_shorts_mine.py` | Minería MFE/MAE BTC shorts + features big_trade/obi_intrabar |
| `apps/rbf-review/api/btc_vwap_rastro.py` | Backtest VWAP rastro como target — **descartado** (ver §22) |
| `apps/rbf-review/api/mtf_shorts_backtest.py` | Backtest shorts — 5 símbolos (flag `--experiment`) |
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
| `apps/rbf-review/api/mtf_experiments.py` | EXP1-EXP9 comparativo (--experiment flag) |
| `scripts/mtf_audit.py` | Breakdown por dirección×sesión, símbolo, patrón, hora UTC, exit reason |
| `apps/rbf-review/src/views/MTFModuleView.tsx` | UI del módulo — métricas Short/Long separadas + selector backtest |
| `apps/rbf-review/vite.config.ts` | Middleware `/api/backtest/mtf_combined` (corre ambos scripts en paralelo) |
| `migrations/mtf_microstructure_cols.sql` | **EJECUTADA** 2026-06-14 — microestructura + consolidación tabla |
| `migrations/big_trade_bars.sql` | **EJECUTADA** 2026-06-15 — big_trade_bearish/bullish en todas las tablas de barras |
| `migrations/obi_intrabar_bars.sql` | **EJECUTADA** 2026-06-15 — obi_min/max_intrabar en todas las tablas de barras |
| `migrations/vp_levels_bars.sql` | **PENDIENTE** — VP en tablas de barras |
| `docs/mtf/MTF_SISTEMA.md` | Este documento |
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
18. **big_trade_bearish/bullish en btc_bars** (2026-06-15): footprint institucional — seller dominante en mitad superior de la barra (>2.5× vol medio). `derive_big_trade()` ya existía en `adapter.rs`; ahora también se persiste en la tabla y se usa en minería BTC shorts
19. **obi_min/max_intrabar en btc_bars** (2026-06-15): 6 muestras de OBI-L5 cada 10s durante la vela M1. Captura el pico de presión vendedora durante la mecha, no solo al cierre. Resuelve la brecha de granularidad ms→M1 (ver §21)
20. **Warmup klines cap 1500** (2026-06-15): Binance FAPI max 1500 barras. `(limit + 1).min(1500)` — si la posición tiene >1500 barras de vida, el warmup carga hasta 1500 sin error de respuesta inesperada
21. **VWAP rastro investigado y descartado** (2026-06-15): London VWAP previo como target dinámico. Backtest 15d, n=52: solo 4 trades con rastro aplicable (7.7%), WR=25%, AvgR=-0.651R. Sin edge (ver §22)
23. **Análisis completo EXP1-EXP7** (2026-06-15): `mtf_winner_dna.py` + `mtf_loss_autopsy.py` + `mtf_experiments.py` sobre 14d/180 trades. Hallazgos: TrendDown WR=44.4%, TrendUp WR=71.4%, VWAP below WR=72%, LondonNyOverlap WR=60.9%. Solo EXP7 VWAP Selectivo pasa criterio (WR=57.9% +7.3pp, AvgR=+0.437R). Ver §24.
24. **EXP8 Regime Filter activado en live** (2026-06-15): bloquear TrendDown en `mtf_shorts_detector.rs` paso 3c. Backtest: -12 trades, equity $1,237→$1,432 (+$195, +15.8%). Commit `9721f98`. Railway redeploy automático.
25. **Arquitectura paralela confirmada** (2026-06-15): 5 símbolos corren en `tokio::spawn` independientes (paralelo). Filtros dentro de cada símbolo son lineales y fail-fast (D1→Funding→TrendDown→detect_signal). Diseño correcto.
26. **Expansión de campos acumulados en *_bars** (2026-06-15): se identificaron datos capturados pero no persistidos (VP levels) y datos faltantes (multi-bar context). Ejecutadas 2 migraciones + deploy:
    - `vp_poc/vah/val/lvn_below`: ya los calculaba el Rust, las columnas no existían → datos perdidos. Ahora persisten.
    - `cvd_consec_neg/pos`: barras consecutivas con CVD en la misma dirección — narrativa de momentum previo
    - `prev_bar_delta`: delta de la barra anterior al cierre actual — ¿la presión vendedora venía de antes?
    - `bars_since_low_vr`: barras desde la última compresión de volumen (vr < 0.7) — contexto de coil/spring

---

## 21. Granularidad — Big Trade + OBI Intrabar (2026-06-15)

### El problema

Los sistemas de orderflow como Flowsurface operan en milisegundos y muestran presión institucional a nivel de tick. Nuestro sistema trabaja con barras M1 — una barra de 60 segundos aplana toda la actividad intrabar en un único snapshot al cierre. Esto significa que un seller institucional que entra en los primeros 5 segundos de la vela y genera una mecha de -0.4% puede estar completamente invisible en el `obi_l5` al cierre de la barra.

### Solución implementada

**1. `big_trade_bearish` / `big_trade_bullish`** — footprint institucional al nivel de footprint de barra

| Campo | Definición |
|-------|-----------|
| `big_trade_bearish` | Vol de trades ask (sell-side) > 2.5× vol medio de la barra **Y** concentrado en la mitad superior del rango de la vela |
| `big_trade_bullish` | Vol de trades bid (buy-side) > 2.5× vol medio de la barra **Y** concentrado en la mitad inferior del rango |

`derive_big_trade()` ya existía en `data/src/strategy/adapter.rs:266`. A partir de 2026-06-15 se persiste en `btc_bars` (y otras tablas de barras) y se usa como feature en `btc_shorts_mine.py`.

**2. `obi_min_intrabar` / `obi_max_intrabar`** — pico de presión DOM durante la vela

El monitor captura OBI-L5 cada 10 segundos durante la vela M1 (`self.obi_intrabar: Vec<(i64, f32, f32, f32, f32)>` — 6 muestras/barra). Al cerrar la vela:

```rust
let min_l5 = self.obi_intrabar.iter().map(|s| s.1).fold(f32::MAX, f32::min) as f64;
let max_l5 = self.obi_intrabar.iter().map(|s| s.1).fold(f32::MIN, f32::max) as f64;
```

- `obi_min_intrabar`: pico de presión vendedora durante la barra (puede ser -0.40 cuando `obi_l5` al cierre es -0.10)
- `obi_max_intrabar`: pico de presión compradora durante la barra
- `obi_range = obi_max - obi_min`: amplitud total de la lucha DOM durante la vela

### Features derivados en minería

```python
'obi_min_neg20':  obi_min_intrabar < -0.20,  # presión vendedora moderada
'obi_min_neg30':  obi_min_intrabar < -0.30,  # presión vendedora fuerte
'obi_min_neg40':  obi_min_intrabar < -0.40,  # presión vendedora extrema
'obi_max_pos20':  obi_max_intrabar > +0.20,  # rebote comprador intrabar
'obi_range_wide': (obi_max - obi_min) > 0.30, # lucha DOM intensa
```

### Estado

Datos acumulándose desde 2026-06-15. Re-mining planificado en ~5 días (~2026-06-20) cuando haya suficientes barras con estos campos. El patrón `btc:bt_bear+oi` ya está activo en `btc_shorts_v1.py` como señal A0 (primera prioridad).

---

## 22. Investigación: VWAP Rastro como Target — Descartado (2026-06-15)

### Concepto explorado

El VWAP de cierre de la sesión London (07:00–12:00 UTC) del día anterior como nivel de "fair value institucional" que actúa como imán de precio para el día siguiente. Hipótesis: si el precio de entrada short está por encima del London VWAP previo, ese nivel sería un target más natural que el fijo 2.5R.

**Por qué tiene sentido teórico:**
- El VWAP es el precio promedio ponderado por volumen — donde realmente transaccionaron los institucionales
- Si el precio sube por encima del VWAP del día anterior, eventualmente vuelve a ese nivel (mean reversion)
- Usar el VWAP previo como target daría un target dinámico alineado con estructura institucional

### Metodología de backtest

Script: `apps/rbf-review/api/btc_vwap_rastro.py`

```python
# Reconstrucción del London VWAP desde btc_bars histórico
def build_london_vwap_map(m1):
    for bar in m1:
        utc_h = datetime.fromtimestamp(ts/1000, tz=utc).hour
        if 7 <= utc_h < 12:  # London: 07:00–12:00 UTC
            typical = (h + l + c) / 3
            sum_tv += typical * vol
            sum_vol += vol
    london_vwap_close = sum_tv / sum_vol  # valor al cerrar las 12:00 UTC
```

Comparación: mismo signal detector, mismos stops H1, mismo capital — solo el target varía.

**Condición de aplicación del rastro:**
```python
use_rastro = rastro is not None and fixed_tp < rastro < entry
# aplica si el VWAP previo está entre el entry y el target 2.5R
# (es decir, más cercano → más fácil de alcanzar)
```

### Resultados (15 días, n=52 BTC shorts)

| Escenario | n | WR% | AvgR | TotalR | Equity |
|-----------|---|-----|------|--------|--------|
| BASE (fijo 2.5R) | 52 | 63.5% | +0.771R | +40.10R | $901 |
| RASTRO (London VWAP previo) | 52 | 63.5% | +0.763R | +39.69R | $897 |

**Trades con rastro disponible:** 4/52 (7.7%)
- WR de esos 4: 25.0%
- AvgR de esos 4: -0.651R

**Trade-by-trade donde el rastro aplicó:**

| Fecha | Entry | Rastro | Gap | BASE | RASTRO |
|-------|-------|--------|-----|------|--------|
| 06-07 13:22 | 61,671 | 60,836 | 1.35% | -1.115R | -1.115R (=) |
| 06-08 09:31 | 63,348 | 62,472 | 1.38% | -1.116R | -1.116R (=) |
| 06-08 17:48 | 63,403 | 62,472 | 1.47% | -1.111R | -1.111R (=) |
| 06-14 16:03 | 64,023 | 63,806 | 0.34% | +1.149R | +0.738R (-) |

En los 3 primeros, el SL golpeó antes de que el precio llegara al rastro — sin diferencia. En el cuarto, el rastro cerró el trade prematuramente (+0.738R) cuando el fijo hubiera dado +1.149R.

### Por qué no funciona

En tendencia bajista (condición para short): el London VWAP del día anterior queda **por encima** del precio actual (el mercado ya bajó). Eso significa `rastro > entry` → condición `rastro < entry` no se cumple → no aplica. El rastro solo aplica en rangos laterales donde el VWAP previo está cerca del precio actual. Y en esos rangos, las señales de short ya tienden a fallar más.

### Decisión

**Descartado.** El concepto es teóricamente válido pero en la práctica BTC trending hace que el rastro sea inaplicable el 92.3% del tiempo. No se incorporará como filtro ni como target.

Para longs podría valer más la pena explorar (el VWAP previo quedaría por debajo del precio en tendencia alcista, actuando como soporte). Pendiente para cuando haya datos de longs suficientes (~2026-07-05).

---

## 23. Campos Acumulados en *_bars — Mapa Completo (2026-06-15)

### Qué está en DB y para qué sirve en minería

| Campo | Tipo | Desde | Qué captura | Feature en minería |
|-------|------|-------|-------------|-------------------|
| `obi_l5` | FLOAT8 | siempre | Order Book Imbalance 5 niveles al cierre | Presión DOM en entry |
| `obi_fast` | FLOAT8 | siempre | OBI EMA rápida (alpha=0.333) | Momentum DOM suavizado |
| `cvd_slope` | FLOAT8 | siempre | Pendiente del CVD M1 | Dirección del flujo neto |
| `bar_delta` | FLOAT8 | siempre | Delta neto de la barra (buy vol - sell vol) | Agresión neta en el bar |
| `dz` | FLOAT8 | siempre | Delta Z-score (delta vs su media) | ¿Es este delta anómalo? |
| `vr` | FLOAT8 | siempre | Volume ratio vs media 50 barras | ¿Expansión o compresión? |
| `oi_momentum` | BOOLEAN | siempre | OI expandiéndose en dirección bajista/alcista | Dinero nuevo entrando |
| `stacked_imb` | TEXT | siempre | Imbalances apilados Bullish/Bearish/None | Estructura de FVGs |
| `absorption` | TEXT | siempre | Absorción en Ask/Bid (footprint) | Institucional absorbiendo |
| `cvd_divergence` | TEXT | siempre | BearishAbsorption / BullishAbsorption | Divergencia CVD-precio |
| `regime` | TEXT | siempre | TrendDown/TrendUp/Ranging/etc | Contexto de mercado |
| `vwap` | FLOAT8 | siempre | VWAP de sesión diaria | Precio sobre/bajo fair value |
| `vpin` | FLOAT8 | siempre | Volume-synchronized PIN (toxicidad de flujo) | Flujo informado vs noise |
| `equal_high` | BOOLEAN | siempre | Equal high en ventana 50 barras | Pool de liquidez H1 |
| `equal_low` | BOOLEAN | siempre | Equal low en ventana 50 barras | Pool de liquidez H1 |
| `big_trade_bearish` | BOOLEAN | 2026-06-15 | Vol ask >2.5× media en mitad superior de rango | Seller institucional en mecha |
| `big_trade_bullish` | BOOLEAN | 2026-06-15 | Vol bid >2.5× media en mitad inferior de rango | Buyer institucional en mecha |
| `obi_min_intrabar` | FLOAT8 | 2026-06-15 | Pico mínimo de OBI-L5 durante la vela (6 muestras/min) | Presión vendedora máxima intrabar |
| `obi_max_intrabar` | FLOAT8 | 2026-06-15 | Pico máximo de OBI-L5 durante la vela | Presión compradora máxima intrabar |
| `vp_poc` | FLOAT8 | 2026-06-15 | Point of Control del VP 300 barras | Target dinámico / nivel de mayor volumen |
| `vp_vah` | FLOAT8 | 2026-06-15 | Value Area High (70% del volumen por encima) | Resistencia estructural de VP |
| `vp_val` | FLOAT8 | 2026-06-15 | Value Area Low (70% del volumen por debajo) | Soporte estructural / target natural shorts |
| `vp_lvn_below` | FLOAT8 | 2026-06-15 | LVN más cercano por debajo del precio | Zona de vacío — precio vuela sin resistencia |
| `cvd_consec_neg` | INT2 | 2026-06-15 | Barras consecutivas con cvd_slope < 0 | ¿Momentum vendedor sostenido o puntual? |
| `cvd_consec_pos` | INT2 | 2026-06-15 | Barras consecutivas con cvd_slope > 0 | Contexto comprador previo a señal short |
| `prev_bar_delta` | FLOAT8 | 2026-06-15 | bar_delta de la barra anterior | ¿La barra previa ya tenía presión vendedora? |
| `bars_since_low_vr` | INT2 | 2026-06-15 | Barras desde última barra de compresión (vr < 0.7) | Breakout de coil (1-5) vs momentum establecido (>10) |

### Features derivados en minería (calculados en Python, no en DB)

```python
# De obi_min/max_intrabar
'obi_min_neg20':    obi_min_intrabar < -0.20
'obi_min_neg30':    obi_min_intrabar < -0.30
'obi_range_wide':   (obi_max - obi_min) > 0.30

# De cvd_consec_neg
'cvd_momentum_3':   cvd_consec_neg >= 3   # momentum establecido
'cvd_momentum_5':   cvd_consec_neg >= 5   # momentum fuerte

# De prev_bar_delta
'prev_bear_delta':  prev_bar_delta < -50  # barra anterior bajista

# De bars_since_low_vr
'post_compression': 1 <= bars_since_low_vr <= 5   # breakout de coil
'open_momentum':    bars_since_low_vr > 10          # momentum establecido

# De vp_val
'val_in_range':     entry - vp_val < entry * 0.025  # VAL a <2.5% = target natural
```

### Calendario de mining

| Fecha | Qué minar | Campos nuevos disponibles |
|-------|-----------|--------------------------|
| ~2026-06-20 | Re-mining BTC shorts | `big_trade`, `obi_intrabar`, `cvd_consec`, `prev_bar_delta`, `bars_since_low_vr` |
| ~2026-06-25 | VP como target | `vp_poc`, `vp_val`, `vp_lvn_below` (7+ días para perfiles significativos) |
| ~2026-07-05 | Walk-forward completo | Todos los campos — validación out-of-sample |

---

## 24. Análisis Experimental EXP1–EXP9 (2026-06-15)

### Contexto

Con 9+ días de datos live acumulados (14d backtest window, n=180 shorts) se corrieron tres análisis para entender el edge:

1. **`mtf_winner_dna.py`** — ADN de big winners (≥2R) vs losers
2. **`mtf_loss_autopsy.py`** — autopsia hora-por-hora, símbolo, DZ score, OBI de los 87 SL hits
3. **`mtf_experiments.py`** — 9 experimentos de filtros adicionales comparados vs baseline

### Hallazgos clave del winner DNA

| Feature | Big Winners | Losers | Edge |
|---------|-------------|--------|------|
| `oi_momentum=True` | 42% | 20% | **+21pp** |
| `obi_l5 < -0.15` | 8% | 25% | -16pp (OBI negativo = más losers) |
| `regime=TrendDown` | 6% | 11% | -5.7pp |
| `vwap_dev < -0.1%` (bajo VWAP) | — | — | **WR=72%** en entry |

| Regime | n | WR | AvgR |
|--------|---|-----|------|
| TrendUp | 21 | **71.4%** | **+0.763R** |
| Chop | 11 | 63.6% | +0.708R |
| LondonNyOverlap | 46 | 60.9% | +0.648R |
| Expansion | 73 | 49.3% | +0.320R |
| **TrendDown** | **18** | **44.4%** | **+0.029R** |

**Insight ICT**: TrendUp es el mejor regime para shorts porque el precio subió rápido → equal highs + stops de compradores acumulados arriba → el short barre esa liquidez (liquidity hunt).

### Hallazgos de la autopsia de pérdidas

- XRP: 24 trades, 16 SL hits (66%) — el activo más débil del sistema
- `dz < -1.0` (venta muy fuerte en entry): WR=0% — entrar cuando los sellers ya dominan = perseguir
- `dz ≥ 0.5` (compradores activos en entry): WR=58.7% — el short entra justo cuando los buyers están subiendo precio (ICT liquidity hunt confirmado)
- Horas malas: 10h, 12-13h, 19-20h UTC — peor WR; hora pico: 15-16h UTC WR=73%

### Tabla completa de experimentos

| # | Experimento | n | WR | AvgR | Equity | Δn | ΔWR | ΔAvgR | Veredicto |
|---|-------------|---|-----|------|--------|-----|------|--------|-----------|
| — | Baseline | 180 | 50.6% | +0.274R | $1,237 | — | — | — | Base |
| 1 | OBI < -0.15 (global) | 53 | 52.8% | +0.209R | $612 | -127 | +2.2pp | -0.065R | ✗ excluye demasiado |
| 2 | CVD/sesión | 115 | 49.6% | +0.193R | $744 | -65 | -1.0pp | -0.081R | ✗ empeora |
| 3 | Delta Div | 22 | 36.4% | -0.426R | $412 | -158 | -14.2pp | -0.700R | ✗ destruye |
| 4 | VWAP Macro | 60 | 53.3% | +0.350R | $740 | -120 | +2.7pp | +0.076R | ✗ n muy bajo |
| 5 | Secundario | 138 | 50.7% | +0.285R | $1,032 | -42 | +0.1pp | +0.011R | ~ neutral |
| 6 | 3 Capas | 48 | 50.0% | +0.198R | $593 | -132 | -0.6pp | -0.076R | ✗ n muy bajo |
| **7** | **VWAP Selectivo** | **107** | **57.9%** | **+0.437R** | **$1,214** | -73 | **+7.3pp** | **+0.163R** | **✓ CANDIDATO** |
| **8** | **Regime Filter** | **168** | **52.4%** | **+0.336R** | **$1,432** | -12 | **+1.8pp** | **+0.062R** | **✓ ACTIVO** |
| 9 | Regime+VWAP | 101 | 57.4% | +0.439R | $1,158 | -79 | +6.8pp | +0.165R | ~ sobre-poda |

### Por qué EXP8 > EXP7 en práctica

EXP7 tiene mejor WR/AvgR pero produce menos equity porque descarta 73 trades (40%). EXP8 descarta solo 12 trades con alto impacto porque el filtro TrendDown es quirúrgico. La métrica objetivo es equity total, no WR máximo.

EXP7 queda pendiente para cuando haya más datos (n≥200) — puede ser que con más n el trade-off cambie.

### Scripts

```
apps/rbf-review/api/mtf_winner_dna.py      — ADN big winners
apps/rbf-review/api/mtf_loss_autopsy.py    — autopsia SL hits
apps/rbf-review/api/mtf_experiments.py     — EXP1-EXP9 comparativo
apps/rbf-review/api/mtf_shorts_backtest.py — backtest base (--experiment flag)
```

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
