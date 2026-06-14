# MTF Shorts H4 — Mining de Patrones
> Generado 2026-06-14 con `apps/rbf-review/api/mtf_h4_shorts_mine.py`
> Objetivo: encontrar señales M1 que mantengan WR alto bajo filtro H4 y aumenten la frecuencia vs el baseline H4 (n=55 en 14d)

---

## Contexto del experimento

El filtro H4 EMA20 en shorts tiene WR=78.2% y AvgR=+1.14R, pero solo 55 trades en 14 días (vs 153 con D1).
El problema: el H4 solo está en "bear/neutral" el **30% del tiempo** en este período (3,218 de 10,681 barras M1 en BTC).
La solución: minar patrones nuevos que exploten mejor esas ventanas H4-bear/neutral.

---

## Patrones candidatos con edge (WR≥55%, AvgR≥+0.30R, n≥4)

### ⚠️ Advertencia de in-sample

Todos los patrones abajo fueron minados sobre los **mismos 14 días** que el backtest.
El criterio de selección real para implementar es n≥10 con WR consistente por sesión.
Los patrones con WR=100% y n=4-6 son ruido estadístico, no edge real.

---

## BTCUSDT

### Patrones BASE (existentes, ahora bajo H4)
| Patron | n | WR% | AvgR | Nota |
|--------|---|-----|------|------|
| shoot+london | 27 | 77.8% | +1.00R | Sube de WR=55% (D1) a 77.8% (H4) |
| eq+london+exp | 8 | 87.5% | +1.04R | — |

### Patrones NUEVOS más sólidos (n≥15)
| Patron | n | WR% | AvgR | Desglose por sesión |
|--------|---|-----|------|---------------------|
| stk_bear+cvd | 51 | 66.7% | +0.65R | NY:23t 56.5% / London:17t 76.5% / Overlap:11t 72.7% |
| shoot+stk_bear | 45 | 71.1% | +0.87R | NY:20t 70% / London:15t 66.7% / Overlap:10t 80% |
| stk_bear+exp | 44 | 70.5% | +0.81R | NY:20t 65% / London:14t 85.7% / Overlap:10t 60% |
| stk_bear+obi | 58 | 60.3% | +0.61R | más n pero WR límite |
| shoot+obi_neg | 42 | 66.7% | +0.75R | NY:20t 65% / London:15t 66.7% / Overlap:7t 71.4% |
| shoot+cvd_neg | 36 | 72.2% | +0.75R | NY:16t 62.5% / London:11t 81.8% / Overlap:9t 77.8% |
| bear_delta+stk | 36 | 69.4% | +0.84R | NY:15t 60% / London:12t 75% / Overlap:9t 77.8% |
| stk_bear+oi | 38 | 68.4% | +0.82R | NY:17t 58.8% / London:13t 76.9% / Overlap:8t 75% |
| obif_neg+stk | 38 | 71.1% | +0.86R | NY:17t 64.7% / London:16t 75% / Overlap:5t 80% |
| oi+obi_neg | 35 | 71.4% | +0.90R | NY:17t 58.8% / London:13t 76.9% / Overlap:5t 100% |
| dz_sell+london | 25 | 76.0% | +0.94R | London:14t 71.4% / Overlap:11t 81.8% |
| stk_bear+london+oi | 21 | 76.2% | +0.93R | London:13t 76.9% / Overlap:8t 75% |
| obif_neg+london | 21 | 76.2% | +0.93R | London:16t 75% / Overlap:5t 80% |
| bear_delta+london | 21 | 81.0% | +1.00R | London:12t 75% / Overlap:9t 88.9% |
| oi+london | 21 | 81.0% | +0.97R | London:13t 76.9% / Overlap:8t 87.5% |
| stk+oi+obi | 30 | 73.3% | +1.00R | NY:15t 66.7% / London:12t 75% / Overlap:3t 100% |
| dz_sell_s+london | 18 | 88.9% | +1.22R | London:10t 90% / Overlap:8t 87.5% |
| shoot+london+oi | 13 | 92.3% | +1.48R | London:7t 85.7% / Overlap:6t 100% |

### Candidatos de alta convicción (n<15 pero WR muy alto)
| Patron | n | WR% | AvgR | Alerta |
|--------|---|-----|------|--------|
| stk+oi+obi | 30 | 73.3% | +1.00R | buena muestra |
| dz_sell_s+london | 18 | 88.9% | +1.22R | — |
| shoot+london+oi | 13 | 92.3% | +1.48R | — |
| eq+london+oi | 6 | 100% | +1.46R | n pequeño |
| ask+london | 6 | 100% | +1.69R | n pequeño |

---

## ETHUSDT

### Patrones BASE
| Patron | n | WR% | AvgR |
|--------|---|-----|------|
| ny+oi+eq (como base) | — | — | mineado originalmente |
| shoot+london | 12 | 75.0% | +1.30R | sube con H4 |
| eq+london+exp | 4 | 75.0% | +1.18R | sube con H4 |
| oi+ny | 10 | 80.0% | +1.16R | sube de 78.6% (D1) a 80% (H4) |

### Patrones NUEVOS más sólidos
| Patron | n | WR% | AvgR | Desglose |
|--------|---|-----|------|----------|
| bear_delta+ny | 15 | 93.3% | +1.60R | NY:15t 93.3% — fuerte en NY |
| obif_neg+stk | 18 | 83.3% | +1.20R | NY:10t 100% / London:5t 40% |
| stk_bear+exp | 18 | 83.3% | +1.18R | NY:10t 100% / Overlap:4t 75% |
| stk_bear+ny | 16 | 87.5% | +1.32R | NY:16t 87.5% |
| stk_bear+ny+oi | 9 | 88.9% | +1.01R | NY:9t 88.9% |
| stk_bear+cvd | 23 | 73.9% | +0.98R | NY:11t 90.9% / Overlap:7t 71.4% |
| bear_delta+stk | 27 | 70.4% | +0.90R | NY:14t 85.7% / Overlap:7t 71.4% |
| shoot+stk_bear | 19 | 84.2% | +1.33R | Overlap:7t 100% / NY:7t 100% |
| obif_neg+ny | 11 | 100.0% | +1.56R | NY:11t 100% — posible ruido |
| shoot+ny | 11 | 81.8% | +1.07R | NY:11t 81.8% |

**Nota ETH:** London tiene WR=40-50% en casi todos los patrones. ETH bajo H4 tiene edge principalmente en NY y LondonNyOverlap.

---

## SOLUSDT

### Patrones BASE
| Patron | n | WR% | AvgR |
|--------|---|-----|------|
| oi+ny | 11 | 90.9% | +1.88R | muy fuerte bajo H4 |

### Patrones NUEVOS más sólidos
| Patron | n | WR% | AvgR | Desglose |
|--------|---|-----|------|----------|
| stk_bear+ny | 17 | 94.1% | +1.84R | NY:17t 94.1% — el mejor patrón de toda la minería |
| bear_delta+ny | 15 | 93.3% | +1.81R | NY:15t 93.3% |
| bear_delta+stk | 27 | 74.1% | +1.27R | NY:15t 93.3% / London:7t 28.6% |
| stk_bear+cvd | 19 | 73.7% | +1.40R | NY:10t 90% / London:5t 40% |
| stk_bear+exp | 14 | 78.6% | +1.36R | NY:10t 90% / London:3t 33.3% |
| stk_bear+obi | 15 | 73.3% | +1.36R | NY:6t 100% / Overlap:5t 80% |
| bear_delta+oi | 11 | 72.7% | +1.32R | NY:8t 100% — fuerte |
| shoot+ny | 11 | 90.9% | +1.87R | NY:11t 90.9% |
| stk_bear+oi | 13 | 76.9% | +1.41R | NY:8t 100% / London:4t 25% |

**Nota SOL CRÍTICA:** London casi siempre tiene WR=0-33% en SOL. El edge de SOL bajo H4 está **exclusivamente en NY y LondonNyOverlap**.

---

## BNBUSDT

### Patrones BASE
| Patron | n | WR% | AvgR |
|--------|---|-----|------|
| oi+ny | 7 | 71.4% | +0.55R | sube levemente |
| shoot+london | 20 | 70.0% | +1.06R | Londres ahora tiene edge en BNB bajo H4 |

### Patrones NUEVOS más sólidos
| Patron | n | WR% | AvgR | Desglose |
|--------|---|-----|------|----------|
| obif_neg+london | 21 | 81.0% | +1.15R | London:16t 81.2% / Overlap:5t 80% |
| bear_delta+oi | 13 | 84.6% | +1.37R | London:7t 85.7% / Overlap:4t 100% |
| eq+stk_bear | 9 | 88.9% | +1.48R | NY:5t 80% / London:2t 100% |
| stk_bear+oi | 23 | 78.3% | +0.84R | London:11t 90.9% / NY:7t 71.4% |
| oi+cvd_neg | 18 | 72.2% | +0.84R | London:10t 80% / Overlap:5t 60% |
| dz_sell+london | 18 | 77.8% | +0.99R | London:11t 72.7% / Overlap:7t 85.7% |
| obif_neg+stk | 30 | 73.3% | +0.86R | London:15t 80% / NY:11t 63.6% |
| stk_bear+london+oi | 16 | 81.2% | +0.97R | London:11t 90.9% / Overlap:5t 60% |

**Nota BNB IMPORTANTE:** Bajo H4, BNB tiene edge también en London (lo contrario del baseline D1). El filtro H4 filtra los días en que el BNB London no funciona. `obif_neg+london` con n=21 WR=81% es el hallazgo más valioso de BNB.

---

## XRPUSDT

### Patrones BASE
| Patron | n | WR% | AvgR |
|--------|---|-----|------|
| ask+ny | 5 | 80.0% | +1.44R | sube bajo H4 |
| oi+ny | 7 | 57.1% | +0.61R | similar |
| shoot+london | 19 | 57.9% | +0.70R | marginal |

### Patrones NUEVOS más sólidos
| Patron | n | WR% | AvgR | Desglose |
|--------|---|-----|------|----------|
| ask+cvd | 10 | 80.0% | +1.42R | NY:5t 80% / Overlap:3t 100% |
| eq+london | 7 | 85.7% | +1.66R | London:5t 80% / Overlap:2t 100% |
| ask+stk_bear | 5 | 80.0% | +1.55R | NY:3t 66.7% |
| ask+exp | 6 | 83.3% | +1.37R | NY:3t 100% / Overlap:2t 100% |
| stk_bear+exp | 16 | 68.8% | +0.75R | NY:7t 85.7% / Overlap:5t 60% |
| bear_delta+ny | 19 | 57.9% | +0.63R | más n pero WR mínimo |

---

## Hallazgos transversales

### 1. London funciona en BNB bajo H4 (inversión del baseline D1)
El baseline D1 prohibía London para BNB (WR=30-42%). Bajo H4, `obif_neg+london` tiene WR=81% (n=21).
Explicación probable: el filtro H4 elimina los días en que BNB está en tendencia alcista intraday, que es exactamente cuando London fallaba.

### 2. SOL y ETH: London es tóxica bajo cualquier filtro
Patrón consistente: en SOL y ETH, casi todos los patrones London tienen WR=25-40%. El edge está en NY y LondonNyOverlap.
Excepción: ETH `shoot+london` con WR=75% (n=12), que sigue siendo el patrón London más robusto de ETH.

### 3. Patrones que funcionan en los 5 símbolos (universales)
- `stk_bear+ny` — BTC 63.3% / ETH 87.5% / SOL 94.1% / BNB 70.8% / XRP 64.3%
- `bear_delta+ny` — BTC 56.2% / ETH 93.3% / SOL 93.3% / BNB 58.8% / XRP 57.9%
- `shoot+stk_bear` — BTC 71.1% / ETH 84.2% / SOL 61.5% / BNB 62.1% / XRP 64.3%

Estos son los mejores candidatos para una implementación universal.

### 4. DZ strong (dz_sell_s = dz < -1.0) con London es consistente
`dz_sell_s+london`: BTC n=18 WR=88.9%, BNB n=13 WR=69.2%, ETH n=8 WR=62.5%
El DZ score fuerte filtra barras donde el CVD está muy por debajo de su media — señal de selling pressure institucional real.

---

## Patrones propuestos para implementar (set H4 v1)

Criterios de selección: n≥8, WR≥65%, edge en al menos 2 sesiones, mecanismo comprensible.

### BTC — nuevos a agregar
```python
if is_shoot and stk_bear:                      → 'btc:shoot+stk_bear'       # n=45, WR=71.1%
if stk_bear and oi_true and obif < 0:          → 'btc:stk+oi+obi'           # n=30, WR=73.3%
if oi_true and is_london:                       → 'btc:oi+london'            # n=21, WR=81.0%
if bear_delta and is_london:                    → 'btc:bear_delta+london'    # n=21, WR=81.0%
if dz_sell_strong and is_london:               → 'btc:dz_sell_s+london'     # n=18, WR=88.9%
if is_shoot and is_london and oi_true:          → 'btc:shoot+london+oi'      # n=13, WR=92.3%
```
Total nuevo BTC: +6 patrones; con los 2 del baseline = 8 patrones BTC.

### ETH — nuevos a agregar
```python
if stk_bear and is_ny:                          → 'eth:stk_bear+ny'          # n=16, WR=87.5%
if bear_delta < -50 and is_ny:                 → 'eth:bear_delta+ny'        # n=15, WR=93.3%
if stk_bear and is_exp:                         → 'eth:stk_bear+exp'         # n=18, WR=83.3%
if is_shoot and stk_bear:                       → 'eth:shoot+stk_bear'       # n=19, WR=84.2%
if stk_bear and oi_true and is_ny:             → 'eth:stk_bear+ny+oi'       # n=9, WR=88.9%
```
Total nuevo ETH: +5 patrones; con los 3 del baseline = 8 patrones ETH.

### SOL — nuevos a agregar
```python
if stk_bear and is_ny:                          → 'sol:stk_bear+ny'          # n=17, WR=94.1%
if bear_delta < -50 and is_ny:                 → 'sol:bear_delta+ny'        # n=15, WR=93.3%
if stk_bear and cvd_neg:                        → 'sol:stk_bear+cvd'         # n=19, WR=73.7%
if bear_delta < -50 and stk_bear:              → 'sol:bear_delta+stk'       # n=27, WR=74.1%
```
Total nuevo SOL: +4 patrones; con los 3 del baseline = 7 patrones SOL.

### BNB — nuevos a agregar + reabrir London
```python
if obif < -0.15 and is_london:                 → 'bnb:obif_neg+london'      # n=21, WR=81.0%
if bear_delta < -50 and oi_true:               → 'bnb:bear_delta+oi'        # n=13, WR=84.6%
if stk_bear and oi_true and is_london:         → 'bnb:stk_bear+london+oi'   # n=16, WR=81.2%
```
Total nuevo BNB: +3 patrones; con los 2 del baseline = 5 patrones BNB.
**OJO:** Con H4, Londres ya es válido para BNB. Quitar el bloqueo `if not is_ny`.

### XRP — nuevos a agregar
```python
if abs_ask and cvd_neg:                         → 'xrp:ask+cvd'              # n=10, WR=80.0%
if eq_hi and is_london:                         → 'xrp:eq+london'            # n=7, WR=85.7%
if stk_bear and is_exp:                         → 'xrp:stk_bear+exp'         # n=16, WR=68.8%
```
Total nuevo XRP: +3 patrones; con los 3 del baseline = 6 patrones XRP.

---

## Proyección de frecuencia

Con patrones actuales (baseline D1): n=153 en 14 días = ~10.9 trades/día  
Con H4 + baseline únicamente: n=55 en 14 días = ~3.9 trades/día  
Con H4 + patrones nuevos propuestos (estimado): ~100-130 trades en 14 días = ~7-9 trades/día  

La ganancia de frecuencia viene principalmente de:
1. Patrones con n alto en BTC (stk_bear+cvd n=51, stk_bear+obi n=58, shoot+stk_bear n=45)
2. Patrones que recuperan London para BNB (obif_neg+london n=21)
3. Nuevos patrones NY para ETH/SOL que no existían en baseline

---

## Próximos pasos

- [ ] Implementar set H4 v1 en `mtf_h4_shorts_backtest.py` con los patrones propuestos
- [ ] Correr backtest completo H4 v1 y comparar equity vs H4 baseline (n=55)
- [ ] Verificar que n total se acerca a 100+ con WR ≥ 65% y AvgR ≥ +0.50R
- [ ] Si pasa umbral → congelar como candidato para walk-forward vs D1 baseline
- [ ] Walk-forward ~2026-07-05: D1 baseline vs H4 v1 sobre datos nuevos

**No implementar en producción** hasta que el walk-forward valide ambos sistemas en datos out-of-sample.
