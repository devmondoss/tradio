# Detector: VwapValuePullbackContinuation

Archivo: `data/src/strategy/detectors/vwap_value_pullback_continuation.rs`

---

## Concepto

En mercados con tendencia, el precio se mueve en impulsos y correcciones. Durante una corrección, el precio regresa al **Value Area** (zona de equilibrio donde opera el 70% del volumen) y toca la **VWAP** — el precio promedio ponderado por volumen de la sesión. Si el flujo se realinea con la tendencia al llegar a este nivel, es una oportunidad de continuación.

El detector usa preferentemente **AVWAP-BOS** (VWAP anclado al Break of Structure) si está disponible, fallback a la VWAP de sesión. El AVWAP-BOS es más preciso porque comienza desde el momento en que la estructura de mercado cambió.

---

## Condiciones de activación

### LONG — pullback en tendencia alcista

```
1. regime ∈ {TrendUp, Expansion}
2. price_vs_avwap_bos ∈ {Above, At}    ← sobre el AVWAP-BOS (o VWAP si no hay BOS)
   fallback: price_vs_vwap ∈ {Above, At}
3. vp.value_location == InValue        ← precio dentro del value area (pullback)
   (BelowVal excluido — sería breakdown, no pullback)
4. flow.cvd_slope ≥ 0                  ← tendencia del CVD alcista
5. flow.delta > 0                      ← delta de la barra positivo (flujo realineado)
6. flow.cvd > -200                     ← CVD acumulado no fuertemente negativo
   (gate anti-ruido: un solo bar positivo no puede compensar una sesión de sell-side)
7. !flow.failed_acceptance             ← sin rechazo activo
8. ob.spread_bps ≤ max_spread_bps
9. ob.microprice ≥ px × 0.9998        ← libro no sesgado a la venta
10. basis_ok(flow.basis, true)
```

**Entry**: precio actual  
**Stop**: `min(VAL, entry - 0.75×ATR)` — bajo el extremo del value area  
**Target**: VAH — el techo del value area  
**TTL**: `default_ttl_ms`

### SHORT — pullback en tendencia bajista

```
1. regime ∈ {TrendDown, Expansion}
2. price_vs_avwap_bos ∈ {Below, At}
   fallback: price_vs_vwap ∈ {Below, At}
3. vp.value_location == InValue
   (AboveVah excluido — sería breakout, no pullback)
4. flow.cvd_slope ≤ 0
5. flow.delta < 0
6. flow.cvd < 200                      ← CVD acumulado no fuertemente positivo
7-10. (mismos)
```

**Entry**: precio actual  
**Stop**: `max(VAH, entry + 0.75×ATR)`  
**Target**: VAL

---

## Anchor logic (AVWAP-BOS vs VWAP)

```rust
// Si existe AVWAP-BOS (price_vs_avwap_bos != Unknown) → usar esa relación
// Si no → usar price_vs_vwap
// El label se registra en evidence para análisis posterior
```

La evidencia incluye `"anchor_avwap_bos"` o `"anchor_vwap_session"` para poder analizar cuál anchor funciona mejor.

---

## Evidencia emitida

**Long**: `["trend_up", "anchor_avwap_bos|anchor_vwap_session", "pullback_into_value", "positive_delta_reentry", "cvd_aligned"]`  
**Short**: `["trend_down", "anchor_avwap_bos|anchor_vwap_session", "pullback_into_value", "negative_delta_reentry", "cvd_aligned"]`

## Invalidación semántica

**Long**: pérdida del VAL o AVWAP-BOS → el pullback se convirtió en breakdown  
**Short**: recuperación del VAH o AVWAP-BOS

---

## Régimen favorito

**TrendUp** para longs (factor_regime = ×1.15), **TrendDown** para shorts (×1.15). El setup en **Chop** recibe factor neutro pero el scorer puede rechazarlo por bajo score base.

---

## Gate CVD acumulado

El gate `flow.cvd > -200` (long) y `flow.cvd < 200` (short) fue añadido después de observar un false trigger donde el CVD acumulado era -471 (sesión completa de sell-side) pero un solo bar positivo activaba el long. Este gate previene que la realineación de una sola vela sobrescriba el contexto acumulado de todo el día.

---

## Parámetros calibrables

| Parámetro | Efecto en este detector |
|-----------|------------------------|
| `min_score` | Umbral de activación |
| `max_spread_bps` | Filtra spreads |
| `default_ttl_ms` | Duración del trade |

No hay parámetros específicos de VwapPullback — su sensibilidad se controla via el score (cvd_slope, taker_imbalance, delta alineados con el trend aportan más puntos al score base).
