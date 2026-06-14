# HTF Shorts System — Documentación Completa

**Última actualización:** 2026-06-14  
**Estado:** Baseline v3 activo (5 símbolos), reglas congeladas — pendiente walk-forward  
**Baseline v3 (14 días, 5 símbolos):** n=133, WR=58.6%, AvgR=+0.548R, $500→$2,012 (+302%)

---

## 1. Concepto

Sistema de shorts que combina tres capas de confirmación:
- **Filtro macro D1**: solo operar en contexto bajista o neutral (precio < EMA20 diaria)
- **Señales M1 mineadas**: patrones detectados por análisis MFE/MAE sobre 9,000+ barras reales
- **Stop estructural H1**: H1_high + 0.3×ATR_H1 con límite máximo 0.75% (el edge existe solo aquí)
- **Exit inteligente**: CVD exhaustion + OBI flip, o target fijo 2.5R

**Nombre real de la estrategia:** "Structural Scalping" / "HTF-anchored LTF scalping" — M1 entry signals con H1 structural stops y D1 trend filter.

El problema que resuelve: los sistemas M1 con stops ajustados (~0.11%) son destruidos por fees (0.07% RT = 0.62R/trade). Con stop H1 estructural < 0.75% el fee es 0.05–0.10R — viable.

---

## 2. Arquitectura

```
D1 EMA20 filter
    └── precio < EMA20×0.995 → bear/neutral → permitir shorts
    └── precio > EMA20×1.005 → bull → bloquear todo

Sesión filter
    └── OffHours / Asia → BLOQUEADO siempre
    └── London / LondonNyOverlap / NewYork → PERMITIDO

M1 signal detector (por símbolo, sesiones específicas por símbolo)
    └── BTC:  London+NY  → shoot+london, shoot+ask+obi
    └── ETH:  London+NY  → ny+oi+eq, ask+london+exp, ask+vpin+ny
    └── SOL:  London+NY  → ny+vr4+oi, ny+vr4+eq, eq+london+exp
    └── BNB:  solo NY    → eq+ny+oi, oi+ny
    └── XRP:  solo NY    → eq+ny+oi, ask+ny, oi+ny

Stop placement
    └── stop = H1_high + 0.3 × ATR_H1
    └── filtro estricto: 0.30% < stop_pct < 0.75%  ← crítico, aquí vive el edge

Exit logic
    └── TAKE_PROFIT:     precio cae a entry - 2.5×risk
    └── CVD_EXHAUSTION:  cvd_slope > 0 por 5 barras consecutivas
                         Y obi_fast > 0.15
                         Y en profit >= 1.0R
    └── STOP_LOSS:       precio sube a H1_high + 0.3×ATR
    └── EXPIRED:         1200 barras M1 sin resolución (~20h)
```

---

## 3. Parámetros actuales (v3)

| Parámetro | Valor | Notas |
|-----------|-------|-------|
| Capital inicial | $500 | — |
| Risk por trade | 2% compounding | — |
| Fee RT | 0.07% | maker entry + taker exit |
| MIN_STOP_PCT | 0.30% | — |
| MAX_STOP_PCT | 0.75% | **Crítico — edge colapsa arriba de aquí** |
| Target R | 2.5R fijo | score≥3 usa 3.5R (diferencia mínima con n=12) |
| CVD_FLIP_BARS | 5 | barras M1 consecutivas con cvd_slope>0 |
| OBI_FLIP_THR | 0.15 | umbral obi_fast para confirmar CVD exit |
| MIN_PROFIT_CVD | 1.0R | no cierra por CVD si profit < 1.0R |
| Cooldown M1 | 30 barras | ~30 min entre señales por símbolo |
| Forward max | 1200 barras | ~20h máximo por trade |
| BLOCKED_HOURS_UTC | {} | vacío — sin bloqueo por hora |
| Símbolos | BTC, ETH, SOL, BNB, XRP | — |

---

## 4. Confluence Score (0–6)

Puntos acumulables por trade. No filtra señales, es informativo + pesa el target:

| Factor | Condición | Puntos |
|--------|-----------|--------|
| Stacked imbalance bajista | `stacked_imb == 'Bearish'` | +1 |
| Sin thin zone arriba | `thin_above == False` | +1 |
| Bar delta negativo | `bar_delta < -50` | +1 |
| OBI L5 negativo | `obi_l5 < -0.2` | +1 |
| Delta Z-score negativo | `dz < -0.5` | +1 |
| OI momentum alineado | `oi_momentum == True` | +1 |

**Distribución observada (14 días, 5 símbolos):**

| Score | n | WR% | AvgR |
|-------|---|-----|------|
| 0 | 15 | 46.7% | +0.20R |
| 1 | 41 | 58.5% | +0.57R |
| 2 | 28 | 60.7% | +0.59R |
| **3** | **12** | **83.3%** | **+1.24R** |
| 4 | 1 | 0.0% | -1.11R |

Score=3+ es el setup de más alta convicción. Con n=12 aún insuficiente para filtrar, pero el patrón es claro.

---

## 5. Detectores por Símbolo

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
if not is_ny: return False              # London bloqueado explícitamente
if eq_hi and oi_true:                   → 'bnb:eq+ny+oi'         # WR=80% n=5
if oi_true:                             → 'bnb:oi+ny'            # WR=56.5% n=23
```

### XRP (**solo NY** — London WR=26-39% en todos los patrones)
```python
if not is_ny: return False              # London bloqueado explícitamente
if eq_hi and oi_true:                   → 'xrp:eq+ny+oi'         # WR=66.7% n=3
if abs_ask:                             → 'xrp:ask+ny'           # WR=57.1% n=7
if oi_true:                             → 'xrp:oi+ny'            # WR=57.1% n=7
```

---

## 6. Exit: CVD Exhaustion

El sistema no usa trailing stop (precio hace mecha y para al trade). Usa exhaustion de orderflow:

```python
CVD_FLIP_BARS  = 5     # barras M1 consecutivas con cvd_slope > 0
OBI_FLIP_THR   = 0.15  # obi_fast debe superar este umbral
MIN_PROFIT_CVD = 1.0   # solo cierra si estamos >= 1R en profit
```

**Auditoría de CVD exit (14 días, n=35 CVD trades):**

| Escenario post-exit | n | % |
|---------------------|---|---|
| Precio rebotó/lateral (salida correcta) | 18 | 51% |
| Precio continuó >0.5R (salimos pronto) | 17 | 49% |

- AvgR capturado: +1.24R
- AvgR adicional disponible en 30 barras post-exit: +0.64R
- **Conclusión: no tocar.** Calibrar más barras o min_profit convierte winners en SL. Net negativo en las 3 variantes probadas (A: bars=8, B: minR=1.5R, C: ambos).

---

## 7. Resultados por versión

### Baseline v3 — 2026-06-14 (14 días, 5 símbolos, sin bloqueo de horas)

| Símbolo | n | WR% | AvgR | Total R |
|---------|---|-----|------|---------|
| BTC | 63 | 55.6% | +0.439R | +27.66R |
| ETH | 14 | **78.6%** | **+0.902R** | +12.63R |
| SOL | 21 | 57.1% | +0.715R | +15.03R |
| BNB | 23 | 56.5% | +0.393R | +9.03R |
| XRP | 12 | 58.3% | +0.708R | +8.50R |
| **TOTAL** | **133** | **58.6%** | **+0.548R** | **+72.84R** |

**Equity: $500 → $2,012 (+302% en 14 días)**

### v2 — 2026-06-13 (8 días, 3 símbolos, horas bloqueadas)

n=63, WR=71.4%, AvgR=+0.904R, $500→$1,437 (+187%)

### v1 — 2026-06-12 (8 días, 3 símbolos)

n=137, WR=52.6%, AvgR=+0.343R, $500→$1,173 (+134%)

---

## 8. Minería de Patrones

**Script:** `apps/rbf-review/api/pattern_mine.py`

Metodología:
1. Fetch todas las barras M1 disponibles por símbolo
2. Para cada barra extraer flags booleanos (cvd_neg, obi_strong, vr_high, oi_true, etc.)
3. Para cada combinación de 1, 2 y 3 flags: medir MFE y MAE en horizonte 60 barras M1
4. WR = % barras donde MFE > MAE — el short habría ganado
5. Ordenar por WR desc, filtrar n >= 4

**Calibración BNB/XRP (htf_bnb_xrp_calibrate.py — 2026-06-14):**

Escaneo exhaustivo de 18 patrones candidatos sobre datos reales. Hallazgo: London es inviable para BNB y XRP (WR 26–42% en todos los patrones). Solo NY con OI alineado tiene edge.

---

## 9. Análisis de Distribución

**Script:** `apps/rbf-review/api/htf_analysis.py`

Hallazgo crítico de stop_pct:

```
stop < 0.75%    WR=65.8%  AvgR=+0.732R  ← TODO el edge aquí
0.75–1.50%      WR=38.3%  AvgR=-0.141R  ← edge negativo
stop > 1.50%    WR=12.5%  AvgR=-0.650R  ← destruye capital
```

---

## 10. Minería de Patrones II — 2026-06-14

**Scripts creados:**
- `htf_loss_autopsy.py` — autopsia individual de cada pérdida
- `htf_winner_dna.py` — ADN del trade ganador (big winners vs losers)
- `htf_cvd_exit_audit.py` — auditoría CVD exit: ¿salimos bien o pronto?
- `htf_cvd_calibrate.py` — comparación 3 variantes de calibración CVD

**Hallazgos:**

### Pérdidas (n=38 losers sobre 96 trades)
- thin_above y thin_below **no discriminan** — presentes por igual en winners y losers (82% vs 81%)
- Score 0 tiene SL rate=32%; score 1 tiene SL rate=21%
- No hay hora única que concentre las pérdidas de forma estadísticamente significativa

### ADN de los Big Winners (n=24 trades ≥2R)
- **OI_MOMENTUM**: +33pp de edge — 54% de big winners vs 21% de losers tienen oi_momentum=True
- **VWAP**: precio bajo el VWAP en entry tiene WR=68-75% vs sobre el VWAP WR=57%
- **LondonNyOverlap**: 50% de big winners ocurren aquí con solo 35 de 133 trades totales
- Score de confluence no discrimina bien entre winners y losers (AvgR similar en todos los buckets antes de añadir oi_momentum)

### CVD Exit (calibración)
- 3 variantes probadas: ninguna mejora el equity neto
- A (bars=5→8): -$150 vs baseline
- B (minR=1.0→1.5R): -$10 vs baseline
- C (ambos): -$60 vs baseline
- **Conclusión: CVD exit está bien calibrado, no tocar**

---

## 11. Decisiones Tomadas

| Decisión | Razón |
|----------|-------|
| Sin bloqueo de horas | Sobreajuste con <100 trades; mercado variable |
| CVD bars=5, minR=1.0R | Las 3 alternativas probadas empeoran el resultado neto |
| BNB/XRP solo NY | Datos de calibración: London WR=26-42% todos los patrones |
| Score como info, no filtro | Filtrar score≥2 reduce n de 133 a ~41 — menos PnL total |
| Target score≥3 → 3.5R | Diferencia de +$2.5 vs baseline — irrelevante con n=12 |
| oi_momentum en score | Discriminador más fuerte big winners vs losers (+33pp edge) |

---

## 12. Pendiente

### Pendiente inmediato
- [ ] Ejecutar migración SQL `migrations/vp_levels_bars.sql` en Supabase
- [ ] Verificar que monitor guarda vp_poc, vp_vah, vp_val, vp_lvn_below por barra

### Walk-forward (~2026-07-05)
- [ ] Correr `shorts_htf_backtest.py --days 30` sobre datos post-2026-06-13
- [ ] Criterio pass: WR ≥ 55% y AvgR ≥ +0.30R en datos nuevos
- [ ] Si pasa: evaluar añadir al sistema live

### Con 200+ trades (30+ días)
- [ ] Calibrar VP floor exit usando VAL como target (WR=81-87% cuando VAL en zona 1-2.5R)
- [ ] Calibrar VWAP como filtro de entry (precio bajo VWAP → WR=68-75%, sobre VWAP → WR=57%)
- [ ] Score≥3 como mínimo de entrada si n≥30 en ese bucket
- [ ] Revisar BNB/XRP con más datos — patrones ny+oi son prometedores pero n=12-23

### Investigación pendiente
- [ ] **Entrada tarde**: el sistema entra cuando el move ya empezó. ¿Hay señal M1 más temprana que anticipe la estructura H1 antes de que forme?
- [ ] **LondonNyOverlap**: concentra el 50% de big winners — ¿qué la diferencia de London pura?
- [ ] Revisar `btc:shoot+ask+obi` en más datos (n=2 en backtest original, insuficiente)
- [ ] ETH tiene WR=78.6% — analizar si hay más patrones ETH que se están perdiendo

---

## 13. Archivos

| Archivo | Descripción |
|---------|-------------|
| `apps/rbf-review/api/shorts_htf_backtest.py` | Script principal — 5 símbolos, baseline v3 |
| `apps/rbf-review/api/pattern_mine.py` | Minería MFE/MAE original |
| `apps/rbf-review/api/htf_analysis.py` | Análisis distribución stop_pct/horas |
| `apps/rbf-review/api/htf_bnb_xrp_calibrate.py` | Calibración BNB/XRP desde cero |
| `apps/rbf-review/api/htf_loss_autopsy.py` | Autopsia individual de pérdidas |
| `apps/rbf-review/api/htf_winner_dna.py` | ADN big winners vs losers |
| `apps/rbf-review/api/htf_cvd_exit_audit.py` | Auditoría CVD exit: left on table |
| `apps/rbf-review/api/htf_cvd_calibrate.py` | Comparación variantes CVD calibration |
| `apps/rbf-review/api/htf_experiments.py` | Experimentos OBI/CVD/Delta |
| `apps/rbf-review/api/htf_confluence.py` | Análisis confluence score |
| `apps/rbf-review/api/htf_vp_target.py` | VP como target (POC/VAL/LVN) |
| `migrations/vp_levels_bars.sql` | SQL para columnas VP en Supabase |
| `apps/rbf-review/src/views/HTFModuleView.tsx` | Módulo UI independiente |
| `docs/htf/HTF_SHORTS_SISTEMA.md` | Este documento |

---

## 14. Evolución del Sistema

1. **Microscalping M1** → fees destruyen el edge (stop 0.11%)
2. **HTF H1→M1 con trailing** → trailing cerraba prematuramente en mecha
3. **Target swing_low H1** → targets de $2,000 en BTC, inalcanzables
4. **Target fijo 2.5R** → correcto, pero stop placement incorrecto
5. **Detección M1 + stop swing_high_10barras** → WR=39%
6. **Detección M1 mineada + stop H1 estructural** → n=159, WR=50%, +125%
7. **v1: poda de patrones débiles** → n=137, WR=52.6%, +134%
8. **v2: análisis distribución + calibración** → n=63, WR=71.4%, +187%
9. **v3: sin bloqueo horas + BNB/XRP calibrados + oi_momentum en score** → n=133, WR=58.6%, $2,012 ✓

---

## 15. Advertencia de Overfitting

Los patrones de BTC/ETH/SOL fueron minados y backtestados sobre los **mismos datos**. BNB/XRP fueron calibrados sobre los mismos 14 días.

**Reglas congeladas:** 2026-06-14  
**No modificar** `detect_m1_signal()` ni parámetros hasta walk-forward.  
**Fecha objetivo walk-forward:** ~2026-07-05  
**Criterio pass:** WR ≥ 55% y AvgR ≥ +0.30R sobre datos nuevos
