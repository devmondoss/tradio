# Sweep & Reclaim Long — Backtest 7.5 días (Jun 5–12, 2026)

*Generado: 2026-06-12*

> Backtest aislado del setup Sweep & Reclaim Long. Script: `apps/rbf-review/api/sweep_backtest.py`.
> Este setup opera **independiente** del RBF Short — cooldown propio, rama de detección separada.
> El bug que bloqueaba el disparo en live (`allow_long` gateando `sweep_reclaim_long_enabled`)
> fue corregido el 2026-06-12. Los trades aquí son **simulados** sobre barras históricas reales.

---

## Parámetros

| Parámetro | Valor |
|-----------|-------|
| Capital | $500 |
| Riesgo/trade | $10 (2%) |
| VR mínimo | ≥ 1.5× |
| Riesgo máximo del wick | ≤ 0.3% (`sweep_max_risk_frac`) |
| Target | 2R desde close |
| Trail activate | 1.90R |
| Trail ATR K | 1.2× |
| Cooldown | 60 barras |
| Sesiones | London, LondonNyOverlap, NewYork |
| Símbolos activos | BTC, BNB, SOL |
| Símbolos excluidos | ETH (WR=22%), XRP (WR=25%) |

### Condiciones de disparo

```
low < range_low          → wick barró por debajo del rango
close > range_low        → precio recuperó dentro del rango
bar_delta < 0            → barra vendedora (selling fue absorbido)
obi_l5 > 0              → soporte bid al cierre
VR >= 1.5
sweep_risk / close <= 0.003
```

---

## Resultados globales

| Métrica | Valor |
|---------|-------|
| Trades totales | **41** |
| Wins / Losses | 19 / 22 |
| Win Rate | **46.3%** |
| Total R | **+21.12R** |
| Avg R/trade | **+0.515R** |
| Equity final | **$711.24 (+42.2%)** |

### Por exit reason

| Reason | n | Avg R | Total R |
|--------|---|-------|---------|
| TRAILING_STOP | 19 | +2.27R | +43.12R |
| STOP_LOSS | 22 | -1.00R | -22.00R |

El trailing lock a 1.90R funciona: todos los trailing exits garantizan mínimo +1.90R.
Trade #7 (BTC London) alcanzó +5.46R — movimiento extendido desde wick muy pequeño (0.018%).

---

## Por símbolo

| Símbolo | n | WR | Total R | Avg R | Nota |
|---------|---|----|---------|-------|------|
| BTCUSDT | 20 | **55%** | **+17.53R** | **+0.876R** | Motor principal del edge |
| BNBUSDT | 16 | 37.5% | +2.80R | +0.175R | WR bajo — revisar gate cum_delta |
| SOLUSDT | 5 | 40% | +0.80R | +0.160R | Pocos trades — `sweep_max_risk_frac` puede estar filtrando wicks de SOL |

BTC concentra el edge: 11 wins de 20, incluyendo los outliers más grandes (+5.46R, +3.51R, +2.35R).

---

## Por sesión

| Sesión | n | WR | Total R | Avg R |
|--------|---|----|---------|-------|
| LondonNyOverlap | 6 | **83%** | **+8.50R** | **+1.42R** |
| London | 16 | 43.8% | +7.86R | +0.491R |
| NewYork | 19 | 36.8% | +4.76R | +0.251R |

LondonNyOverlap tiene el mejor WR pero n=6 — no calibrar filtros con este dato.
NewYork domina en volumen (19 trades) pero con el menor edge (WR=37%).

---

## Trade por trade

| # | Símbolo | Sesión | VR | Riesgo% | Reason | R |
|---|---------|--------|----|---------|--------|---|
| 1 | BTCUSDT | London | 2.11 | 0.055% | TRAILING_STOP | +1.90 |
| 2 | BTCUSDT | LondonNyOverlap | 3.19 | 0.049% | TRAILING_STOP | +1.90 |
| 3 | BNBUSDT | NewYork | 1.92 | 0.056% | TRAILING_STOP | +1.90 |
| 4 | BTCUSDT | NewYork | 2.58 | 0.120% | STOP_LOSS | -1.00 |
| 5 | BTCUSDT | NewYork | 1.64 | 0.036% | STOP_LOSS | -1.00 |
| 6 | BNBUSDT | London | 1.73 | 0.105% | STOP_LOSS | -1.00 |
| 7 | BTCUSDT | London | 2.40 | **0.018%** | TRAILING_STOP | **+5.46** |
| 8 | BTCUSDT | London | 6.47 | 0.127% | STOP_LOSS | -1.00 |
| 9 | BTCUSDT | NewYork | 5.09 | 0.058% | TRAILING_STOP | +1.90 |
| 10 | BNBUSDT | London | 2.06 | 0.039% | TRAILING_STOP | +1.90 |
| 11 | SOLUSDT | London | 1.60 | 0.091% | STOP_LOSS | -1.00 |
| 12 | BNBUSDT | LondonNyOverlap | 3.72 | 0.055% | TRAILING_STOP | +1.90 |
| 13 | BTCUSDT | LondonNyOverlap | 3.70 | 0.046% | TRAILING_STOP | +1.90 |
| 14 | BNBUSDT | LondonNyOverlap | 1.56 | 0.038% | STOP_LOSS | -1.00 |
| 15 | BTCUSDT | NewYork | 1.55 | 0.108% | STOP_LOSS | -1.00 |
| 16 | BNBUSDT | NewYork | 4.26 | 0.097% | STOP_LOSS | -1.00 |
| 17 | BTCUSDT | NewYork | 7.54 | 0.048% | STOP_LOSS | -1.00 |
| 18 | BNBUSDT | London | 2.24 | 0.101% | STOP_LOSS | -1.00 |
| 19 | BTCUSDT | London | 1.54 | 0.015% | STOP_LOSS | -1.00 |
| 20 | SOLUSDT | London | 5.84 | 0.075% | TRAILING_STOP | +1.90 |
| 21 | BTCUSDT | London | 8.77 | 0.067% | TRAILING_STOP | +1.90 |
| 22 | BNBUSDT | London | 2.23 | 0.131% | TRAILING_STOP | +1.90 |
| 23 | BTCUSDT | LondonNyOverlap | 2.20 | 0.052% | TRAILING_STOP | +1.90 |
| 24 | BNBUSDT | LondonNyOverlap | 2.17 | 0.068% | TRAILING_STOP | +1.90 |
| 25 | BNBUSDT | NewYork | 2.17 | 0.037% | TRAILING_STOP | **+3.30** |
| 26 | BTCUSDT | NewYork | 3.02 | 0.009% | TRAILING_STOP | +1.90 |
| 27 | BNBUSDT | NewYork | 1.91 | 0.017% | STOP_LOSS | -1.00 |
| 28 | BNBUSDT | London | 1.63 | 0.102% | STOP_LOSS | -1.00 |
| 29 | BTCUSDT | London | 1.75 | 0.155% | STOP_LOSS | -1.00 |
| 30 | SOLUSDT | London | 1.51 | 0.079% | STOP_LOSS | -1.00 |
| 31 | BNBUSDT | London | 10.74 | 0.142% | STOP_LOSS | -1.00 |
| 32 | BTCUSDT | London | 2.30 | 0.036% | TRAILING_STOP | +1.90 |
| 33 | BNBUSDT | NewYork | 1.56 | 0.049% | STOP_LOSS | -1.00 |
| 34 | BTCUSDT | NewYork | 5.00 | 0.204% | STOP_LOSS | -1.00 |
| 35 | SOLUSDT | NewYork | 2.85 | 0.111% | STOP_LOSS | -1.00 |
| 36 | BNBUSDT | NewYork | 2.59 | 0.048% | STOP_LOSS | -1.00 |
| 37 | BTCUSDT | NewYork | 3.50 | 0.040% | STOP_LOSS | -1.00 |
| 38 | BTCUSDT | NewYork | 1.71 | 0.032% | TRAILING_STOP | **+2.35** |
| 39 | BTCUSDT | NewYork | 3.02 | **0.006%** | TRAILING_STOP | **+3.51** |
| 40 | SOLUSDT | NewYork | 1.90 | 0.045% | TRAILING_STOP | +1.90 |
| 41 | BNBUSDT | NewYork | 1.73 | 0.051% | STOP_LOSS | -1.00 |

---

## Observaciones

### BTC concentra el edge
BTC tiene WR=55% y +17.53R de los 21.12R totales. Los outliers más grandes son todos BTC
(+5.46R, +3.51R, +2.35R). Wicks muy pequeños en BTC (0.006%–0.018%) con trailing extendido
son la fuente principal de R en este setup.

### BNB con WR bajo (37.5%)
BNB tiene 10 losses de 16 trades. El gate `cum_delta_25b >= -500` aplica en los Shorts pero
no está implementado en el sweep. Candidato a añadir: si BNB ya acumuló mucho delta vendedor
antes del sweep, el reclaim puede ser un dead-cat bounce.

### SOL con pocos trades (n=5)
`SWEEP_MAX_RISK_PCT = 0.003` (0.3%) probablemente filtra la mayoría de wicks de SOL por ser
más volátil. Revisar si subir a 0.005 genera más trades sin destruir el edge.

### VR muy alto en losses
Trades #8 (VR=6.47), #17 (VR=7.54), #21 (VR=8.77 — win), #31 (VR=10.74).
VR extremo en sweep puede indicar evento de liquidación real, no absorción.
Candidato a gate: `VR <= 5.0` (similar al VR_MAX del short breakout).

---

## Próximos pasos

| Trabajo | Criterio | Estado |
|---------|----------|--------|
| Datos live reales del sweep | Bug fix desplegado 2026-06-12 | Acumulando |
| Gate cum_delta para BNB sweep | Calibrar con n=15+ trades live | Pendiente |
| VR máximo sweep | ¿VR>5 tiene peor WR? Probar con n actual | Pendiente |
| Umbral risk_frac SOL | Probar 0.005 vs 0.003 con n actual | Pendiente |
| n mínimo para calibrar | 25 trades live por símbolo | Pendiente |

---

*Re-ejecutar cuando haya ≥7 días nuevos de datos live del sweep.*
