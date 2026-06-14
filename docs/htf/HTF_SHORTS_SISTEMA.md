# HTF Shorts System — Documentación Completa

**Fecha:** 2026-06-13  
**Estado:** Calibrado v2 (2026-06-13), reglas congeladas — pendiente walk-forward  
**Resultados v2:** n=63, WR=71.4%, AvgR=+0.904R, $500→$1,437 (+187% en 8 días)  
**Resultados v1:** n=137, WR=52.6%, AvgR=+0.343R, $500→$1,173 (+134% en 8 días)

---

## 1. Concepto

Sistema de shorts que combina:
- **Filtro macro D1**: solo operar en contexto bajista o neutral (precio < EMA20 diaria)
- **Señales M1 mineadas**: patrones detectados por análisis MFE/MAE sobre 9,000+ barras reales
- **Stop estructural H1**: H1_high + 0.3×ATR_H1 con límite máximo 0.75% (el edge existe solo aquí)
- **Filtro horario**: excluir horas UTC con WR histórico < 35% (10–13h y 17h)
- **Exit inteligente**: CVD exhaustion + OBI flip, o target fijo 2.5R

El problema que resuelve: los sistemas M1 con stops ajustados (~0.11%) son destruidos por fees (0.07% RT = 0.62R/trade). Con stop H1 estructural < 0.75% el fee es 0.05–0.10R — viable.

---

## 2. Arquitectura

```
D1 EMA20 filter
    └── precio < EMA20×0.995 → bear/neutral → permitir shorts
    └── precio > EMA20×1.005 → bull → bloquear todo

Filtro horario UTC
    └── BLOQUEADO: 10h, 11h, 12h, 13h, 17h (transición London→NY + cierre NY)
    └── PERMITIDO: 08–09h (London), 14–16h (NY core), 19–21h (post-NY)

M1 signal detector (por símbolo, por sesión)
    └── BTC: shoot_star+London, shoot+ask+obi
    └── ETH: ny+oi+eq_hi, ask+london+exp, ask+vpin+ny
    └── SOL: ny+vr4+oi, ny+vr4+eq, eq+london+exp

Stop placement
    └── H1 candle que contiene la barra M1 de señal
    └── stop = H1_high + 0.3 × ATR_H1
    └── filtro estricto: 0.30% < stop_pct < 0.75%  ← crítico

Exit logic
    └── TAKE_PROFIT:     precio cae a entry - 2.5×risk
    └── CVD_EXHAUSTION:  cvd_slope > 0 por 5 barras consecutivas
                         Y obi_fast > 0.15
                         Y en profit >= 1.0R
    └── STOP_LOSS:       precio sube a H1_high + 0.3×ATR
    └── EXPIRED:         1200 barras M1 sin resolución (~20h)
```

---

## 3. Minería de Patrones

**Script:** `apps/rbf-review/api/pattern_mine.py`

Metodología:
1. Fetch todas las barras M1 disponibles (~9,000 BTC, ~8,100 ETH, ~7,900 SOL)
2. Para cada barra, extraer 21 flags booleanos (cvd_neg, obi_strong, vr_high, oi_true, etc.)
3. Para cada combinación de 1, 2 y 3 flags: medir MFE y MAE en horizonte 60 barras M1 (~1h)
4. Calcular WR = % barras donde MFE > MAE (el short habría ganado)
5. Ordenar por WR desc, filtrar n >= 4

**Top patrones descubiertos (horizonte 60min M1):**

| Símbolo | Patrón | n mining | WR mining | Edge |
|---------|--------|----------|-----------|------|
| BTC | shoot_star + abs_ask + obif_neg | 15 | 80% | +0.467 |
| BTC | shoot_star + london | 22 | 77% | +0.485 |
| ETH | abs_ask + vpin>0.6 + ny | 6 | 83% | +0.423 |
| ETH | ny + oi_true + eq_hi | 17 | 76% | +0.342 |
| SOL | ny + vr>4 + oi_true | 7 | 100% | +0.954 |
| SOL | ny + vr>4 + eq_hi | 9 | 100% | +0.923 |

**Patrones descartados (minería + análisis de distribución):**
- `btc:vr4+obi+ny` → WR=37.5% AvgR=+0.045R en backtest — ruido
- `sol:ny+vr2+oi` → WR=40.0% AvgR=+0.048R en backtest — ruido
- `btc:vr4+obi+london` → WR=33% n=3
- `eth:ny+oi+exp` → WR=33%
- BNB completo → 0 wins en 8 días

---

## 4. Detectores por Símbolo (v2 — calibrados)

### BTC (London + NY, horas permitidas)
```python
if is_shoot and abs_ask and obif < 0:    → 'btc:shoot+ask+obi'   # WR=80% mining
if is_shoot and is_london:               → 'btc:shoot+london'    # WR=77.1% n=35 backtest v2
# btc:vr4+obi+ny ELIMINADO — WR=37.5% AvgR=+0.045R
```

### ETH
```python
if is_ny and oi_true and eq_hi:          → 'eth:ny+oi+eq'        # WR=71.4% n=7 backtest v2
if abs_ask and is_london and is_exp:     → 'eth:ask+london+exp'  # WR=80.0% n=5 backtest v2
if abs_ask and vpin > 0.6 and is_ny:    → 'eth:ask+vpin+ny'     # WR=83% mining (poco n)
```

### SOL
```python
if is_ny and vr > 4.0 and oi_true:      → 'sol:ny+vr4+oi'       # WR=66.7% n=3 backtest v2
if is_ny and vr > 4.0 and eq_hi:        → 'sol:ny+vr4+eq'       # WR=66.7% n=3 backtest v2
if eq_hi and is_london and is_exp:      → 'sol:eq+london+exp'   # WR=66.7% n=6 backtest v2
# sol:ny+vr2+oi ELIMINADO — WR=40% AvgR=+0.048R
```

---

## 5. Exit: CVD Exhaustion

El sistema no usa trailing stop — usa exhaustion de orderflow:

```python
CVD_FLIP_BARS  = 5     # barras M1 consecutivas con cvd_slope > 0
OBI_FLIP_THR   = 0.15  # obi_fast debe superar este umbral
MIN_PROFIT_CVD = 1.0   # solo cierra si estamos >= 1R en profit
```

**Lógica:** cuando los vendedores se agotan (CVD deja de caer 5 min consecutivos) y los compradores regresan (OBI > 0.15), cerrar con la ganancia actual.

**Distribución de exits v2 (n=63):**

| Exit | n | % | Avg R |
|------|---|---|-------|
| CVD_EXHAUSTION | 28 | 46% | +1.179R |
| TAKE_PROFIT | 17 | 28% | +2.364R |
| STOP_LOSS | 16 | 26% | -1.129R |

CVD ahora es el exit dominante — el sistema sale inteligentemente antes del TP en casi la mitad de los trades ganadores.

---

## 6. Parámetros (v2)

| Parámetro | v1 | v2 | Razón del cambio |
|-----------|----|----|-----------------|
| MAX_STOP_PCT | 2.50% | **0.75%** | Edge solo existe en stop < 0.75% |
| Horas bloqueadas | ninguna | **10,11,12,13,17 UTC** | WR<35% en esas horas |
| btc:vr4+obi+ny | activo | **eliminado** | WR=37.5% AvgR=+0.045R |
| sol:ny+vr2+oi | activo | **eliminado** | WR=40% AvgR=+0.048R |
| Capital inicial | $500 | $500 | — |
| Risk por trade | 2% compounding | 2% compounding | — |
| Fee RT | 0.07% | 0.07% | — |
| MIN_STOP_PCT | 0.30% | 0.30% | — |
| Target | 2.5R fijo | 2.5R fijo | — |
| CVD bars | 5 | 5 | — |
| OBI threshold | 0.15 | 0.15 | — |
| Min profit CVD | 1.0R | 1.0R | — |
| Cooldown M1 | 30 barras | 30 barras | — |
| Forward max | 1200 barras | 1200 barras | — |
| Símbolos | BTC, ETH, SOL | BTC, ETH, SOL | — |

---

## 7. Resultados del Backtest

**Período:** 8.1 días (Jun 5–13, 2026) · Contexto: bear D1 en los tres símbolos

### v2 (parámetros calibrados) — actual

| Métrica | Valor |
|---------|-------|
| n total | 63 |
| Win Rate | **71.4%** |
| Avg R | **+0.904R** |
| Total R | +56.95R |
| Equity | $500 → $1,437 (+187%) |
| SL rate | 26% |
| CVD exit rate | 46% |
| Wins avg R | +1.597R |
| Losses avg R | -1.129R |

### Performance por hora UTC (v2)

| Hora | Sesión | n | WR% | AvgR |
|------|--------|---|-----|------|
| 08:00 | London | 7 | 71.4% | +0.574R |
| 09:00 | London | 11 | 63.6% | +0.595R |
| 14:00 | NY | 11 | 72.7% | +0.673R |
| 15:00 | NY | 7 | **85.7%** | **+1.725R** |
| 16:00 | NY | 10 | **90.0%** | **+1.535R** |
| 19:00 | Post-NY | 7 | 71.4% | +0.812R |

### Performance por símbolo (v2)

| Símbolo | n | WR% | AvgR | Total R |
|---------|---|-----|------|---------|
| BTC | 37 | 75.7% | +0.892R | +33.08R |
| ETH | 12 | 75.0% | +0.847R | +10.16R |
| SOL | 12 | 66.7% | +0.998R | +11.98R |

### v1 vs v2 — comparación

| Métrica | v1 | v2 | Delta |
|---------|----|----|-------|
| n | 137 | 63 | -54% |
| WR | 52.6% | 71.4% | +18.8pp |
| AvgR | +0.343R | +0.904R | +2.6× |
| SL rate | 47% | 26% | -21pp |
| Equity | $1,173 | $1,437 | +22% |

---

## 8. Análisis de Distribución (htf_analysis.py)

**Script:** `apps/rbf-review/api/htf_analysis.py`

Hallazgo crítico del análisis de distribución de stop_pct:

```
stop < 0.75%    n=79   WR=65.8%  AvgR=+0.732R  ← TODO el edge estaba aquí
0.75–1.50%      n=47   WR=38.3%  AvgR=-0.141R  ← edge negativo
stop > 1.50%    n=8    WR=12.5%  AvgR=-0.650R  ← destruye capital
```

El edge del sistema existe **exclusivamente** con stop < 0.75%. Por encima de ese umbral el precio tiene suficiente espacio para generar ruido antes de moverse a favor, y la relación señal/ruido colapsa.

---

## 9. Advertencia de Overfitting

Los resultados (v1 y v2) fueron obtenidos sobre los **mismos 8 días** usados para minar los patrones. Los números son una hipótesis, no una validación.

**Reglas congeladas:** 2026-06-13  
**Protocolo walk-forward:** NO modificar `detect_m1_signal()` ni parámetros hasta correr backtest sobre datos post-2026-06-13.  
**Fecha objetivo:** ~2026-07-05 con `--days 30`  
**Criterio pass:** WR ≥ 55% y AvgR ≥ +0.30R en datos nuevos  

El n=63 de v2 es más pequeño que v1 — los intervalos de confianza son amplios. WR=71% con n=63 tiene IC95% aproximado de ±11pp (60%–82%).

---

## 10. Evolución del Sistema

1. **Microscalping M1** → fees destruyen el edge (stop 0.11%)
2. **HTF H1→M1 con trailing** → trailing cerraba prematuramente
3. **Target swing_low H1** → targets de $2,000 en BTC, inalcanzables
4. **Target fijo 2.5R** → correcto, pero stop placement incorrecto
5. **Detección M1 + stop swing_high_10barras** → WR=39%
6. **Detección M1 mineada + stop H1 estructural** → n=159, WR=50%, +125%
7. **Poda de patrones débiles (v1)** → n=137, WR=52.6%, +134%
8. **Análisis distribución + calibración (v2)** → n=63, WR=71.4%, +187% ✓

---

## 11. Archivos

| Archivo | Descripción |
|---------|-------------|
| `apps/rbf-review/api/shorts_htf_backtest.py` | Script principal del backtest |
| `apps/rbf-review/api/pattern_mine.py` | Script de minería de patrones MFE/MAE |
| `apps/rbf-review/api/htf_analysis.py` | Análisis de distribución: stop_pct, horas, patrones |
| `apps/rbf-review/src/views/HTFModuleView.tsx` | Módulo UI independiente |
| `apps/rbf-review/src/views/BacktestView.tsx` | Strategy `'shorts'` con metadata |
| `apps/rbf-review/vite.config.ts` | Ruta `/api/backtest/shorts` → script |
| `docs/htf/HTF_SHORTS_SISTEMA.md` | Este documento |

---

## 12. Ventanas Operativas y Gates Live

### Mapa de sesiones UTC (diario)

| Hora UTC | Sesión | Estado HTF | Patrones activos |
|---|---|---|---|
| 00:00–07:59 | Asia / OffHours | **BLOQUEADO** — sesión excluida | ninguno |
| **08:00–09:59** | **London** | **ACTIVO** | BTC: `shoot+london`, `shoot+ask+obi` · ETH: `ask+london+exp` · SOL: `eq+london+exp` |
| 10:00–13:59 | London / Overlap | **BLOQUEADO** — horas 10h,11h,12h,13h excluidas | ninguno |
| **14:00–16:59** | **NY core** | **ACTIVO** ← mejor ventana | BTC: `shoot+ask+obi` · ETH: `ny+oi+eq`, `ask+vpin+ny` · SOL: `ny+vr4+oi`, `ny+vr4+eq` |
| 17:00–17:59 | NY / Cierre | **BLOQUEADO** — hora 17h excluida | ninguno |
| 18:00–18:59 | Post-NY | zona gris — sin patrones con suficiente n aún | — |
| **19:00–20:59** | **Post-NY** | **ACTIVO** (n bajo, pendiente más datos) | patrones London/NY |
| 21:00+ | OffHours | **BLOQUEADO** | ninguno |

**Tiempo de espera desde OffHours nocturno hasta primera señal posible: ~6h** (08:00 UTC)

### Gates que bloquean señales dentro de ventana activa

Estos filtros se evalúan en orden. El primero que falla descarta la barra sin evaluar el siguiente.

| Prioridad | Gate | Condición de bloqueo | Frecuencia |
|---|---|---|---|
| 1 | **D1 trend** | precio > EMA20×1.005 (contexto bull D1) | Bloquea TODO mientras el mercado suba |
| 2 | **H1 ATR** | `h1_atr() == 0` — sin H1 completas aún | Solo al arrancar (seed o primera hora live) |
| 3 | **Stop PCT** | stop < 0.30% o stop > 0.75% | El más frecuente — depende de volatilidad H1 |
| 4 | **Sesión/hora** | OffHours, Asia, o hora en {10,11,12,13,17} | Estructura fija diaria |
| 5 | **Trade activo** | posición abierta → no evalúa nueva señal | Durante la duración del trade |
| 6 | **Cooldown** | < 30 barras M1 desde última señal | ~30 min post-señal |
| 7 | **Símbolo** | BNB, XRP u otro → `detect_signal` retorna None | Permanente para símbolos no soportados |

### Síntomas por gate en los logs

```
# Gate 1 — D1 bull bloqueando
[htf] D1 seeded 25 closes, EMA20=Some("65432.10")
→ sin ningún log [htf] SIGNAL durante semanas

# Gate 2 — H1 sin datos (warm-up)
[htf] H1 seeded 0 candles   ← seed falló o monitor recién arrancó
→ bloquea durante la primera hora live hasta que se complete una H1

# Gate 3 — stop_pct fuera de rango (log normal)
→ silencio; no hay log explícito por este filtro

# Símbolo no soportado
→ no aparece ningún log [htf] nunca
```

### Símbolos soportados (v2)

| Símbolo | Sesiones | Patrones |
|---|---|---|
| BTCUSDT | London + NY | `shoot+london`, `shoot+ask+obi` |
| ETHUSDT | London + NY | `ny+oi+eq`, `ask+london+exp`, `ask+vpin+ny` |
| SOLUSDT | London + NY | `ny+vr4+oi`, `ny+vr4+eq`, `eq+london+exp` |
| BNBUSDT | — | **No soportado** (0 wins en 8 días de backtest) |
| XRPUSDT | — | **No soportado** (pendiente 60+ días de datos) |

---

## 13. Próximos pasos

- [ ] Walk-forward ~2026-07-05: correr backtest sobre datos post-2026-06-13
- [ ] Con más datos: analizar si 18:00–21:00 UTC tiene edge real (n muy bajo ahora)
- [ ] Evaluar CVD_FLIP_BARS por símbolo: ETH/SOL pueden necesitar menos barras
- [ ] Revisar `btc:shoot+ask+obi` con más datos (n=2 en v2, insuficiente)
- [ ] Revisar comportamiento con D1 bull (filtro debería bloquear todo)
- [ ] Evaluar añadir XRP con minería específica (60+ días de datos mínimo)
- [ ] Implementar live signal detection basada en los mismos detectores M1
