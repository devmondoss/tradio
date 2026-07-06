# Detector: LiquidationHunt

Archivo: `data/src/strategy/detectors/liquidation_hunt.rs`  
Requiere: `InstitutionalContext` (liquidation tracker activo)

---

## Concepto

Cuando posiciones apalancadas son liquidadas por fuerza (margin call), el exchange vende/compra agresivamente al mercado. Si las liquidaciones son de un solo lado (ej: shorts siendo liquidados), el precio se mueve violentamente en esa dirección, arrastrando a más stops y creando un efecto cascada.

Este detector identifica el momento **durante o inmediatamente después** de una cascade de liquidaciones de un solo lado, cuando el movimiento tiene momentum pero no ha llegado a un extreme de cascade del lado opuesto (lo que indicaría que la caza de stops ya pasó). Entra a favor de la dirección de la liquidación, apuntando hacia el próximo HVN o pared del orderbook.

---

## Condiciones de activación

### LONG — barrido de shorts

```
# Liquidaciones confirman la dirección
1. inst.liquidations.short_liq_usd_5m > cfg.liq_hunt_min_usd (default $500K)
2. inst.liquidations.dominant_side == LiqSide::Shorts

# Momentum activo
3. inst.oi_trend.slope_5bar > 0          ← OI creciendo (nuevos longs entrando)
4. inst.taker_ratio.taker_imbalance > 0.15 ← takers comprando agresivamente
5. flow.cvd_slope > 0                     ← CVD positivo

# Camino libre
6. ob.thin_zone_above == true             ← poca resistencia en el camino

# Gates de no-entrada
7. funding.regime != ExtremeLong          ← si funding está extremo y smart money short, 
   OR ls_ratio.divergence != SmartShortRetailLong  ← el mercado ya podría revertir
8. inst.liquidations.long_liq_usd_5m < cfg.liq_cascade_threshold ($5M)
   ← si hay cascade de longs paralela, el movimiento ya pasó
```

**Entry**: precio actual  
**Stop**: `entry - 1.0×ATR` — stop amplio para absorber la volatilidad post-cascade  
**Target**: primer HVN, VAH o wall arriba que esté > `1.5×ATR` de la entry  
**TTL**: `liq_ttl_ms` (10 min por defecto — la ventana de oportunidad es corta)

### SHORT — barrido de longs

Simétrico: `long_liq_usd_5m > liq_hunt_min_usd`, `dominant_side == Longs`, `slope_5bar < 0`, `taker_imbalance < -0.15`, `thin_zone_below`.

---

## Lógica de target

```rust
let candidates = vp.hvn_nearby + [vp.vah] + ob.walls_above;
let target = candidates
    .filter(|t| t > entry + 1.5 * atr)   // mínimo 1.5×ATR de distancia
    .min();  // el más cercano que cumpla el mínimo
```

Si no hay candidato a más de 1.5×ATR → sin señal. Esto filtra setups donde el próximo nivel de resistencia está demasiado cerca para el riesgo tomado.

---

## Evidencia emitida

**Long**: `["short_liquidations_cascade", "oi_slope_positive", "taker_imbalance_bullish", "cvd_slope_positive", "thin_zone_above"]`  
**Short**: `["long_liquidations_cascade", "oi_slope_negative", "taker_imbalance_bearish", "cvd_slope_negative", "thin_zone_below"]`

---

## Parámetros calibrables

| Parámetro | Default | Descripción |
|-----------|---------|-------------|
| `liq_hunt_min_usd` | $500K | USD liquidados en 5min para confirmar hunt |
| `liq_cascade_threshold` | $5M | USD en 5min del lado opuesto = cascade activa (tarde para entrar) |
| `liq_ttl_ms` | 10 min | TTL corto — la ventana de oportunidad es limitada |

---

## Por qué el TTL es corto (10 min)

Las liquidaciones en cascade se resuelven rápidamente. Si el precio no se mueve en la dirección esperada en los próximos 2 bars (5min cada uno), la tesis está inválida — otros market makers ya absorbieron el flujo. Un TTL largo mantiene posiciones abiertas cuando el contexto ya cambió.

---

## Gates de seguridad

- **Cascade del lado opuesto**: si `long_liq_usd_5m > $5M` cuando estamos considerando un long (short siendo liquidados), significa que hay una cascade masiva de longs en paralelo — el mercado está en crisis y es demasiado peligroso entrar.
- **Funding extremo + smart money divergencia**: si el funding está extremo en la dirección que queremos entrar Y los top traders están posicionados en contra, el mercado puede revertir brutalmente. Doble señal de alerta = no entrar.
