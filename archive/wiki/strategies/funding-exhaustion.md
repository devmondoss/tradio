# Detector: FundingExhaustionReversal

Archivo: `data/src/strategy/detectors/funding_exhaustion_reversal.rs`  
Requiere: `InstitutionalContext`

---

## Concepto

El **funding rate** de los perpetuos es el mecanismo de equilibrio entre el precio del perp y el precio spot. Cuando muchos traders tienen posiciones en la misma dirección, el funding se vuelve extremo: los longs pagan a los shorts (funding positivo extremo) o viceversa.

Un funding extremo sostenido es insostenible. Los traders que pagan funding eventualmente cierran sus posiciones (por el costo de carry o por stops), lo que hace revertir el precio. Este detector identifica el momento en que el **smart money ya está saliendo** (top traders reduciendo exposición) mientras el **retail sigue atrapado** — la señal más fuerte de reversión inminente.

---

## Condiciones de activación

### SHORT — funding extremo positivo (demasiados longs)

```
1. inst.funding.regime == ExtremeLong         ← funding en régimen extremo
2. inst.funding.current > cfg.funding_extreme_threshold (0.0006 = 0.06%)

3. inst.ls_ratio.top_traders_long_pct < 0.52  ← smart money ya no está long
4. inst.ls_ratio.retail_long_pct > 0.62       ← retail sigue long (atrapado)

5. inst.oi_trend.trend ∈ {Decreasing, DecreasingFast}  ← OI cae = posiciones cerrándose

6. flow.cvd_slope ≤ 0                         ← CVD debilitándose
7. inst.taker_ratio.buy_sell_ratio < 1.0      ← más takers vendiendo que comprando

# Gate: no entrar si ya hay cascade de longs (el movimiento ya pasó)
8. inst.liquidations.long_liq_usd_5m < cfg.liq_cascade_threshold
```

**Entry**: precio actual  
**Stop**: `max(VAH, entry + 0.75×ATR)` — sobre el VAH como resistencia natural  
**Target**: VAL — el soporte del value area  
**TTL**: `funding_ttl_ms` (30 min — la reversión de funding es más lenta que una cascade de liq)

### LONG — funding extremo negativo (demasiados shorts)

```
1. inst.funding.regime == ExtremeShort
2. inst.funding.current < -cfg.funding_extreme_threshold

3. inst.ls_ratio.top_traders_long_pct > 0.52  ← smart money largo
4. inst.ls_ratio.retail_long_pct < 0.38       ← retail short (atrapado)

5. inst.oi_trend.trend ∈ {Decreasing, DecreasingFast}
6. flow.cvd_slope ≥ 0
7. inst.taker_ratio.buy_sell_ratio > 1.0

8. inst.liquidations.short_liq_usd_5m < cfg.liq_cascade_threshold
```

**Entry**: precio actual  
**Stop**: `min(VAL, entry - 0.75×ATR)`  
**Target**: VAH

---

## Evidencia emitida

**Short**: `["funding_extreme_positive", "top_traders_exiting_long", "retail_still_long", "oi_decreasing", "cvd_weakening"]`  
**Long**: `["funding_extreme_negative", "top_traders_exiting_short", "retail_still_short", "oi_decreasing", "cvd_recovering"]`

## Invalidación semántica

**Short**: funding cae por debajo del threshold, OI retoma acumulación, CVD se vuelve fuertemente positivo  
**Long**: funding sube por encima del threshold, OI retoma acumulación

---

## Parámetros calibrables

| Parámetro | Default | Descripción |
|-----------|---------|-------------|
| `funding_extreme_threshold` | 0.0006 (0.06%) | Threshold de activación del régimen extremo |
| `funding_ttl_ms` | 30 min | Ventana más larga que otros detectores |
| `liq_cascade_threshold` | $5M | Si ya hay cascade activa = demasiado tarde |

---

## Por qué el TTL es largo (30 min)

A diferencia de las liquidaciones (que se resuelven en minutos), el costo de funding actúa gradualmente. Los traders con posiciones perdedoras tardan más en capitular. La reversión puede tardar 1-3 horas desde el punto de máximo funding extremo, así que un TTL de 30 min da tiempo para que el setup se desarrolle.

---

## Diferencia con SmartMoneyDivergence

Ambos detectores usan L/S ratios, pero el énfasis es diferente:
- **FundingExhaustion**: el trigger es el funding extremo. Los L/S ratios son confirmación de que smart money ya está reduciendo exposición.
- **SmartMoneyDivergence**: el trigger es la divergencia entre retail y smart money. El funding es confirmación adicional pero no tiene que ser extremo — puede ser simplemente elevado.

En la práctica, FundingExhaustion genera señales menos frecuentes (funding extremo es raro) pero de mayor convicción.
