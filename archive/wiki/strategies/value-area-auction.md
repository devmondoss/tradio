# Detector: ValueAreaFailedAuction

Archivo: `data/src/strategy/detectors/value_area_failed_auction.rs`

---

## Concepto

El mercado intenta expandir el rango aceptado de precios más allá del Value Area (el 70% de volumen donde opera la mayoría del tiempo). Si el precio intenta salir por encima del VAH (o por debajo del VAL) pero el flujo de órdenes no confirma el movimiento, se produce un **failed auction**: el mercado rechaza los nuevos precios y regresa hacia el POC.

Este es un setup de reversión a la media — el mercado siempre tiende a volver hacia el precio justo (POC) cuando una extensión falla.

---

## Condiciones de activación

### SHORT — failed auction por encima del VAH

```
1. px < vah                          ← precio bajó de vuelta al value area
2. px > vah - 0.5 × ATR             ← sigue cerca del borde (no está deep inside)
3. flow.failed_acceptance == true    ← el footprint confirmó rechazo
4. flow.delta < 0                    ← delta de la barra alineado SHORT
5. flow.footprint_absorption == Ask  ← los ask sellers están absorbiendo
6. flow.cvd_slope ≤ 0               ← CVD no confirma el breakout
7. flow.taker_imbalance < 0.10      ← flujo neutral-a-bajista (no excesivamente alcista)
8. ob.spread_bps ≤ max_spread_bps   ← spread manejable
9. !ob.thin_zone_above              ← hay liquidez arriba (si no hay, el breakout podría continuar)
10. basis_ok(flow.basis, false)     ← basis no extremo en contra del short
11. R:R ≥ 1.5                       ← gate mínimo de calidad
```

**Entry**: precio actual  
**Stop**: `max(VAH + 0.25×ATR, px + 0.5×ATR)` — sobre el área de rechazo  
**Target**: POC — el precio de mayor volumen actúa como imán  
**TTL**: `default_ttl_ms` (5 min por defecto)

### LONG — failed auction por debajo del VAL

Simétrico al short:
```
1. px > val                          ← precio subió de vuelta al value area
2. px < val + 0.5 × ATR             ← sigue cerca del borde
3. flow.failed_acceptance == true
4. flow.delta > 0                    ← delta alineado LONG
5. flow.footprint_absorption == Bid
6. flow.cvd_slope ≥ 0
7. flow.taker_imbalance > -0.10
8-10. (mismos checks)
```

**Entry**: precio actual  
**Stop**: `min(VAL - 0.25×ATR, px - 0.5×ATR)`  
**Target**: POC  
**TTL**: `default_ttl_ms`

---

## Evidencia emitida

**Short**: `["failed_acceptance_above_VAH", "ask_absorption", "delta_aligned_short", "target_POC"]`  
**Long**: `["failed_acceptance_below_VAL", "bid_absorption", "delta_aligned_long", "target_POC"]`

## Invalidación semántica

**Short**: si el precio vuelve a cruzar por encima del VAH → el failed auction fue falso  
**Long**: si el precio vuelve a cruzar por debajo del VAL

---

## Régimen favorito

**Chop y TrendDown** para shorts, **Chop y TrendUp** para longs. En Expansion el scorer penaliza si el trade va contra la tendencia (factor_regime = 0.70).

---

## Parámetros calibrables

| Parámetro | Default | Efecto |
|-----------|---------|--------|
| `min_score` | 0.60 | Umbral de activación general |
| `max_spread_bps` | 2.0 | Filtra spreads anchos |
| `default_ttl_ms` | 5 min | Duración máxima del trade |

---

## Lógica anti-ruido incorporada

- **Proximity gate (fix 2)**: precio debe estar dentro de 0.5×ATR del borde del Value Area. Evita entradas tardías cuando el precio ya está deep inside.
- **Delta gate (fix 3)**: delta de la barra debe estar alineado con el side. Antes de este fix, el detector generaba señales cortas con delta positivo — contradicción con el scorer.
- **R:R gate (fix 1)**: mínimo 1.5:1. Elimina setups donde el POC está demasiado cerca del precio para justificar el riesgo.
- **thin_zone gate**: si hay zona thin en la dirección del breakout rechazado, puede ser que el mercado sí esté buscando esa dirección — se bloquea la señal contraria.
