# RBF Backtest — 14 días M1 (May 23 – Jun 6, 2026)

**Fecha de ejecución:** 2026-06-07  
**Script:** `scripts/rbf_backtest.py --extended --days 14`  
**Símbolo:** BTCUSDT perpetual (Binance FAPI)

> **Nota (2026-06-10):** Este backtest es la referencia pre-live. El sistema ya tiene
> **72 trades live** en 5 símbolos (BTC/ETH/BNB/SOL/XRP). Resultados live y calibraciones
> aplicadas en [RBF_CALIBRACION_POR_ACTIVO.md](RBF_CALIBRACION_POR_ACTIVO.md).
> Cambios implementados post-backtest: expansion_max_bars filter + trailing direction-aware.


---

## 1. Fuentes de datos

| Período | Fuente OHLCV | Fuente microestructura | Cobertura |
|---|---|---|---|
| May 23 – Jun 1 | Binance REST `/fapi/v1/klines` | ❌ ninguna | Solo cvd_slope + vwap_bias (computados internamente) |
| Jun 1 – Jun 5 | Binance REST | `scalping_bars` (Supabase) | obi_l5, absorption + cvd_slope interno |
| Jun 5 – Jun 6 | `btc_bars` (Supabase) | `btc_bars` (Supabase) | Completa: obi, stacked_imb, absorption, thin, vpin, oi_momentum, vwap |

**Total barras M1:** 20,160 (14 días)  
**Barras con microestructura:** 7,163 (1,568 de btc_bars + 5,599 de scalping_bars)  
**Barras sin microestructura:** 12,997 (primeros ~9 días)

---

## 2. Parámetros del detector

Idénticos a `config/strategy.toml` en producción:

| Parámetro | Valor |
|---|---|
| Ventanas de rango | 15, 20, 30, 45, 60 barras |
| Range min/max | 0.08% – 0.55% del precio |
| VR mínimo | ≥ 2.0× |
| dz gate | 0.5 – 3.0 (alineado con dirección) |
| CVD slope gate | activo (dirección debe confirmar) |
| Stop | 0.25% |
| Target SHORT | 0.50% |
| Target LONG | 0.45% |
| RR mínimo | 1.5 |
| Cooldown | 60 barras M1 |
| Warmup | 110 barras M1 |
| Sessions operativas | London (08–13 UTC), LondonNyOverlap (13–17 UTC) |
| min_confluence_score | 1 (shadow mode) |
| Veto Long/Bear | score < 5 |
| Veto wall_target | desactivado |
| Veto HVN | activo (requiere HVN entre entry y target, primera mitad del recorrido) |
| Veto VPIN | > 0.65 |

**Limitaciones del backtest vs sistema live:**
- HVN veto: `btc_bars` no tiene niveles de Volume Profile → desactivado en el backtest
- Flag 5 LVN: solo usa `thin_zone`, no `lvn_nearby` (mismo motivo)
- Los días May 23 – Jun 1 no tienen obi, stacked_imb, absorption, thin_zone ni oi_momentum → esos flags siempre 0 para ese período

---

## 3. Señales generadas

| Métrica | Valor |
|---|---|
| Total señales | 56 |
| Tradeables (sin veto, score ≥ 1) | 42 |
| Vetadas | 14 |
| Cerradas (TARGET o STOP) | 38 |
| Abiertas (no resolvieron en 4h) | 4 |

**Detalle de vetos:**

| Motivo | N |
|---|---|
| score < min (score=0, sin veto explícito) | 13 |
| vpin_toxic | 1 |

---

## 4. Resultados generales (n=38 cerradas)

| Métrica | Valor |
|---|---|
| Win rate | 39.5% (15W / 23L) |
| Avg R | +0.163 |
| Avg barras hasta exit | 37.6 min |

---

## 5. Por sesión

| Sesión | n | WR% | Avg R |
|---|---|---|---|
| London | 23 | **43.5%** | **+0.278** |
| LondonNyOverlap | 15 | 33.3% | -0.013 |

London claramente superior. Overlap casi break-even.

---

## 6. Por dirección

| Dirección | n | WR% | Avg R |
|---|---|---|---|
| Short | 25 | **44.0%** | **+0.320** |
| Long | 13 | 30.8% | -0.138 |

Shorts tienen el doble de edge que Longs en este período.

---

## 7. Por régimen macro

| Régimen | n | WR% | Avg R |
|---|---|---|---|
| TrendDown | 26 | 38.5% | +0.154 |
| TrendUp | 12 | 41.7% | +0.183 |

Diferencia mínima — sin conclusión con este n.

---

## 8. Por score de confluencia

| Score | n | WR% | Avg R | Nota |
|---|---|---|---|---|
| 0 | — | — | — | vetadas, no tradeables |
| 1 | 18 | **50.0%** | **+0.467** | |
| 2 | 12 | 33.3% | +0.000 | |
| 3 | 3 | 33.3% | +0.000 | |
| 4 | 5 | 20.0% | -0.440 | |

**El gradiente es inverso al esperado.** Score alto correlaciona con peor performance.

### ⚠️ Advertencia de interpretación

Este resultado NO indica que los flags de confluencia sean contraproducentes. Hay un sesgo de período severo:

- **Scores 0–1**: casi exclusivamente de May 23 – Jun 1 (mercado con tendencia clara, BTC en movimiento)
- **Scores 3–4**: exclusivamente de Jun 1–6, cuando hay microestructura de scalping_bars/btc_bars disponible
- Jun 3–6 fue un período lateral de fin de semana crypto — condiciones sistemáticamente desfavorables para cualquier breakout
- En otras palabras: **estás comparando distintos períodos de mercado, no distintos niveles de calidad de señal**

Conclusión correcta: **los datos son insuficientes para evaluar el sistema de scoring**. Con n=38 distribuidos en períodos de cobertura heterogénea, cualquier comparación por score es inválida.

---

## 9. Análisis de flags

Para señales cerradas. Los flags solo tienen datos reales en Jun 1–6 (35% de las señales).

| Flag | En winners | En losers | Diferencia | Interpretación |
|---|---|---|---|---|
| vwap_bias | 86.7% | 87.0% | -0.3pp | No discrimina — está en casi todas |
| cvd_slope | 33.3% | **60.9%** | **-27.5pp** | Aparece más en losers |
| absorption | 0.0% | 17.4% | -17.4pp | Solo Jun 1–6, n pequeño |
| obi | 6.7% | 21.7% | -15.1pp | Solo Jun 1–6, n pequeño |
| stacked_imbalance | 13.3% | 8.7% | +4.6pp | Solo Jun 6, n=3 |
| lvn_thin | 13.3% | 8.7% | +4.6pp | Solo Jun 6, n=3 |
| oi_momentum | 6.7% | 0.0% | +6.7pp | Solo Jun 6, n=1 |

### ⚠️ cvd_slope en más losers que winners

Podría indicar:
1. El flag es un proxy de sobreextensión/momentum extremo → mercados que ya corrieron mucho y revierten
2. Sesgo de período: las señales con cvd_slope activo son mayormente del 26-31 de mayo, que fue un período con más volatilidad y reversiones
3. El threshold de ±15 USD/barra podría ser demasiado bajo (deja entrar demasiadas señales)

**No ajustar nada aún. Se necesitan al menos 50 señales con cobertura homogénea de microestructura.**

---

## 10. Señal por señal (todas)

| Timestamp | Dir | Sesión | Régimen | Score | VR | Rng% | CVD | Flags | Exit | R |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-05-24 09:10 | Long | London | TrendUp | 1 | 2.69 | 0.107 | +102 | vwap_bias | TARGET | +1.80 |
| 2026-05-24 12:11 | Short | London | TrendUp | 0 | 2.07 | 0.159 | -68 | — | TARGET [V] | +2.00 |
| 2026-05-24 13:28 | Short | LonNyOv | TrendDown | 1 | 2.80 | 0.188 | -127 | vwap_bias | TARGET | +2.00 |
| 2026-05-24 15:52 | Short | LonNyOv | TrendDown | 1 | 3.01 | 0.112 | -88 | vwap_bias | STOP | -1.00 |
| 2026-05-25 08:54 | Long | London | TrendUp | 1 | 3.22 | 0.124 | +236 | vwap_bias | OPEN | — |
| 2026-05-25 11:52 | Short | London | TrendUp | 0 | 5.54 | 0.144 | -64 | — | STOP [V] | -1.00 |
| 2026-05-25 13:19 | Short | LonNyOv | TrendDown | 1 | 2.29 | 0.193 | -54 | vwap_bias | STOP | -1.00 |
| 2026-05-25 16:13 | Short | LonNyOv | TrendUp | 1 | 2.54 | 0.119 | -248 | cvd_slope | STOP | -1.00 |
| 2026-05-26 09:01 | Short | London | TrendDown | 1 | 2.10 | 0.152 | -173 | vwap_bias | STOP | -1.00 |
| 2026-05-26 13:30 | Short | LonNyOv | TrendDown | 2 | 4.14 | 0.412 | -718 | cvd_slope, vwap_bias | STOP | -1.00 |
| 2026-05-27 08:26 | Long | London | TrendUp | 1 | 3.80 | 0.130 | +62 | vwap_bias | STOP | -1.00 |
| 2026-05-27 09:38 | Long | London | TrendUp | 1 | 2.64 | 0.184 | +212 | vwap_bias | STOP | -1.00 |
| 2026-05-27 11:30 | Short | London | TrendDown | 0 | 3.23 | 0.144 | -37 | — | STOP [V] | -1.00 |
| 2026-05-27 13:09 | Short | LonNyOv | TrendDown | 2 | 2.88 | 0.355 | -463 | cvd_slope, vwap_bias | TARGET | +2.00 |
| 2026-05-27 14:27 | Short | LonNyOv | TrendDown | 2 | 3.66 | 0.399 | -212 | cvd_slope, vwap_bias | STOP | -1.00 |
| 2026-05-27 15:30 | Long | LonNyOv | TrendDown | 1 | 2.83 | 0.436 | +810 | cvd_slope | STOP | -1.00 |
| 2026-05-28 08:21 | Short | London | TrendDown | 1 | 2.26 | 0.142 | -17 | vwap_bias | STOP | -1.00 |
| 2026-05-28 09:36 | Long | London | TrendDown | 0 | 2.25 | 0.117 | +206 | — | STOP [V] | -1.00 |
| 2026-05-28 10:51 | Short | London | TrendDown | 1 | 3.73 | 0.235 | -218 | vwap_bias | TARGET | +2.00 |
| 2026-05-28 12:21 | Short | London | TrendDown | 2 | 3.41 | 0.230 | -278 | cvd_slope, vwap_bias | STOP | -1.00 |
| 2026-05-28 13:21 | Short | LonNyOv | TrendDown | 2 | 3.05 | 0.343 | -222 | cvd_slope, vwap_bias | TARGET | +2.00 |
| 2026-05-28 16:24 | Long | LonNyOv | TrendUp | 1 | 2.20 | 0.531 | +439 | cvd_slope | TARGET | +1.80 |
| 2026-05-29 11:31 | Short | London | TrendDown | 1 | 3.42 | 0.213 | -66 | vwap_bias | TARGET | +2.00 |
| 2026-05-29 12:33 | Short | London | TrendDown | 1 | 4.44 | 0.134 | -76 | vwap_bias | TARGET | +2.00 |
| 2026-05-29 14:51 | Long | LonNyOv | TrendDown | 0 | 4.15 | 0.444 | +242 | — | TARGET [V] | +1.80 |
| 2026-05-30 09:50 | Long | London | TrendUp | 1 | 2.73 | 0.099 | +168 | vwap_bias | OPEN | — |
| 2026-05-30 12:20 | Long | London | TrendUp | 1 | 2.21 | 0.086 | +48 | vwap_bias | TARGET | +1.80 |
| 2026-05-30 14:03 | Long | LonNyOv | TrendUp | 2 | 4.15 | 0.162 | +341 | cvd_slope, vwap_bias | OPEN | — |
| 2026-05-31 08:04 | Long | London | TrendDown | 0 | 3.32 | 0.117 | +14 | — | STOP [V] | -1.00 |
| 2026-05-31 09:41 | Long | London | TrendUp | 0 | 2.40 | 0.100 | +19 | — | STOP [V] | -1.00 |
| 2026-05-31 12:17 | Long | London | TrendUp | 0 | 2.17 | 0.140 | +53 | — | STOP [V] | -1.00 |
| 2026-05-31 13:20 | Long | LonNyOv | TrendUp | 0 | 4.72 | 0.143 | +37 | — | STOP [V] | -1.00 |
| 2026-05-31 16:14 | Short | LonNyOv | TrendDown | 1 | 2.12 | 0.141 | -33 | vwap_bias | OPEN | — |
| 2026-06-01 10:03 | Short | London | TrendDown | 2 | 3.65 | 0.246 | -208 | cvd_slope, vwap_bias | TARGET | +2.00 |
| 2026-06-01 11:32 | Short | London | TrendDown | 1 | 3.42 | 0.146 | -157 | vwap_bias | TARGET | +2.00 |
| 2026-06-01 13:55 | Short | LonNyOv | TrendDown | 2 | 5.04 | 0.428 | -802 | cvd_slope, vwap_bias | STOP | -1.00 |
| 2026-06-02 08:49 | Short | London | TrendDown | 1 | 2.09 | 0.367 | -222 | vwap_bias | TARGET | +2.00 |
| 2026-06-02 10:11 | Long | London | TrendDown | 3 | 2.85 | 0.489 | +616 | cvd_slope, obi, absorption | STOP | -1.00 |
| 2026-06-02 12:18 | Short | London | TrendDown | 2 | 3.95 | 0.419 | -476 | cvd_slope, vwap_bias | STOP | -1.00 |
| 2026-06-02 14:01 | Short | LonNyOv | TrendDown | 2 | 3.29 | 0.361 | -854 | cvd_slope, vwap_bias | TARGET | +2.00 |
| 2026-06-03 08:32 | Long | London | TrendUp | 4 | 2.23 | 0.204 | +611 | cvd_slope, obi, absorption, vwap_bias | STOP | -1.00 |
| 2026-06-03 09:48 | Short | London | TrendDown | 2 | 3.69 | 0.208 | -1571 | cvd_slope, vwap_bias | STOP | -1.00 |
| 2026-06-03 11:36 | Short | London | TrendUp | 0 | 3.33 | 0.333 | -126 | — | TARGET [V] | +2.00 |
| 2026-06-03 13:00 | Long | LonNyOv | TrendUp | 4 | 2.05 | 0.328 | +181 | cvd_slope, obi, absorption, vwap_bias | STOP | -1.00 |
| 2026-06-03 16:01 | Short | LonNyOv | TrendDown | 2 | 3.41 | 0.493 | -1677 | cvd_slope, vwap_bias | STOP | -1.00 |
| 2026-06-04 12:48 | Long | London | TrendUp | 4 | 2.43 | 0.549 | +781 | cvd_slope, obi, absorption, vwap_bias | STOP | -1.00 |
| 2026-06-04 16:30 | Short | LonNyOv | TrendDown | 0 | 2.35 | 0.397 | -88 | — | STOP [V] | -1.00 |
| 2026-06-05 08:55 | Short | London | TrendDown | 2 | 2.62 | 0.534 | -450 | cvd_slope, vwap_bias | STOP | -1.00 |
| 2026-06-05 10:44 | Long | London | TrendDown | 1 | 2.12 | 0.453 | +177 | vwap_bias | STOP | -1.00 |
| 2026-06-05 13:20 | Long | LonNyOv | TrendDown | 0 | 2.04 | 0.472 | +223 | — | STOP [V] | -1.00 |
| 2026-06-06 08:27 | Long | London | TrendUp | 4 | 2.32 | 0.288 | +179 | stacked_imb, lvn_thin, vwap_bias, oi_momentum | TARGET | +1.80 |
| 2026-06-06 09:47 | Short | London | TrendUp | 3 | 4.01 | 0.425 | -67 | obi, stacked_imb, lvn_thin | TARGET | +2.00 |
| 2026-06-06 11:02 | Long | London | TrendDown | 0 | 2.82 | 0.496 | +319 | — | vpin_toxic [V] | — |
| 2026-06-06 12:03 | Long | London | TrendUp | 3 | 3.06 | 0.378 | +201 | stacked_imb, lvn_thin, vwap_bias | STOP | -1.00 |
| 2026-06-06 13:30 | Short | LonNyOv | TrendDown | 0 | 2.60 | 0.316 | -117 | — | STOP [V] | -1.00 |
| 2026-06-06 15:02 | Short | LonNyOv | TrendDown | 4 | 5.03 | 0.401 | -1 | obi, stacked_imb, lvn_thin, vwap_bias | STOP | -1.00 |

*[V] = vetada, no tradeable. Outcome simulado para referencia.*

---

## 11. Señales con microestructura completa (Jun 6 únicamente, n=5)

Son las únicas señales donde todos los 7 flags estaban disponibles. Demasiado pequeño para conclusiones pero es la referencia del sistema live:

| Timestamp | Dir | Score | Flags activos | Exit | R |
|---|---|---|---|---|---|
| 08:27 | Long | 4 | stacked_imb, lvn_thin, vwap_bias, oi_momentum | TARGET | +1.80 |
| 09:47 | Short | 3 | obi, stacked_imb, lvn_thin | TARGET | +2.00 |
| 11:02 | Long | 0 | — | vpin_toxic [V] | — |
| 12:03 | Long | 3 | stacked_imb, lvn_thin, vwap_bias | STOP | -1.00 |
| 15:02 | Short | 4 | obi, stacked_imb, lvn_thin, vwap_bias | STOP | -1.00 |

Jun 6: 2W / 2L, 50% WR, avgR +0.45 con solo datos del fin de semana lateral. En línea con el live.

---

## 12. Conclusiones y próximos pasos

### Lo que sabemos con confianza

1. **Short > Long** en este período (May 23 – Jun 6). El mercado BTC tuvo bias bajista dominante. No generalizable hasta tener más datos en contexto alcista.

2. **London > LondonNyOverlap** — consistente con el backtest original del plan (30 días). London tiene mejor edge, Overlap más ruidoso.

3. **El sistema genera señales a una frecuencia razonable** — ~4 señales/día operativo. Cooldown de 60 min funciona.

4. **El edge base (sin scoring) es positivo pero pequeño** — avgR +0.163 con WR 39.5%. El RR de 2:1 permite ser rentable con WR > 33%.

### Lo que NO se puede concluir todavía

1. **El sistema de scoring** — el gradiente inverso es un artefacto de períodos con distinta cobertura de microestructura. Inválido para calibración.

2. **Utilidad de flags individuales** — la heterogeneidad de datos contamina cualquier comparación.

3. **Performance en mercado alcista** — toda la muestra tiene bias bajista.

### Qué se necesita para calibrar

| Umbral | Para qué | Estado |
|---|---|---|
| 30 señales en btc_bars | Primera lectura de score vs outcome con datos homogéneos | ✅ Completado |
| 50 señales en btc_bars | Queries 1–4 del plan de calibración válidas | ✅ Completado (72 trades) |
| 100 señales en btc_bars | Fijar `min_confluence_score` real | En progreso |

### Anomalía investigada (2026-06-10)

**Score 4 tiene el peor outcome (WR=17%, −11.15R en 72 trades live)** — confirmado. La causa raíz
es que score alto = múltiples flags alineados = movimiento ya en Expansion avanzada = entrada tardía.
El filtro `expansion_max_bars=3` implementado aborda esto directamente.

**cvd_slope aparece más en losers que en winners** — sigue vigente como hipótesis. Cuando se
confirme con n≥50 homogéneos, revisar si `cvd_slope_gate` necesita ajuste de threshold (>15).

### Calibraciones aplicadas post-backtest (2026-06-10)

1. `expansion_max_bars = Some(3)` para BTC/ETH/BNB — bloquea señales cuando expansion_bars_recent > 3
2. `TRAIL_ACTIVATE_R_SHORT = 1.75` (antes 1.5) — 4 trailing Shorts cedieron avg 0.97R/trade

---

*Próxima revisión: cuando cada símbolo alcance n=25 Shorts cerrados para calibración per-símbolo completa.*
