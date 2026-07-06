# Detector: LvnLiquidityVacuumBreakout

Archivo: `data/src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs`

---

## Concepto

Un **Low Volume Node (LVN)** es una zona de precio donde históricamente se ha operado poco volumen. Cuando el precio alcanza un LVN, hay muy pocos órdenes de mercado en esa zona — el precio la atraviesa rápidamente (vacuum) hacia el siguiente High Volume Node (HVN) o nivel de soporte/resistencia.

El detector no busca el LVN directamente, sino que busca la **thin zone** en el orderbook: zona de poca liquidez que indica que el precio puede moverse rápidamente si supera ese nivel. La thin zone del book correlaciona con los LVN del Volume Profile.

El setup requiere que el flujo ya esté confirmando el movimiento antes de entrar — no es un breakout anticipatorio sino una confirmación.

---

## Condiciones de activación

### LONG — thin zone arriba, flujo alcista

```
1. ob.thin_zone_above == true          ← poca liquidez arriba del precio actual
2. price_vs_vwap ∈ {Above, At}        ← precio sobre la VWAP (soporte dinámico)
3. vp.value_location ∈ {InValue, AboveVah}  ← dentro o sobre el value area
4. flow.delta > 0                      ← delta de la barra positivo
5. flow.cvd_slope > 0                  ← tendencia del CVD alcista
6. flow.stacked_imbalance ∈ {Bullish, None, Unknown}
7. flow.taker_imbalance.abs() < 0.90  ← no sobrecomprado extremo
8. ob.spread_bps ≤ max_spread_bps
9. ob.microprice ≥ px                  ← libro sesgado hacia arriba
10. !flow.ask_wall_nearby              ← no hay pared de asks bloqueando el camino
11. basis_ok(flow.basis, true)
```

**Entry**: precio actual  
**Stop**: `max(min(VWAP_session, entry - 0.75×ATR), entry - 1.5×ATR)` — usa VWAP como referencia, capado en 1.5×ATR  
**Target**: primer HVN o VAH por encima del precio actual  
**TTL**: `default_ttl_ms`

### SHORT — thin zone abajo, flujo bajista

```
1. ob.thin_zone_below == true
2. price_vs_vwap ∈ {Below, At}
3. vp.value_location ∈ {InValue, BelowVal}
4. flow.delta < 0
5. flow.cvd_slope < 0
6. flow.stacked_imbalance ∈ {Bearish, None, Unknown}
7. flow.taker_imbalance.abs() < 0.90
8-9. (mismos)
10. !flow.bid_wall_nearby             ← no hay pared de bids bloqueando el camino
```

**Entry**: precio actual  
**Stop**: `min(max(VWAP_session, entry + 0.75×ATR), entry + 1.5×ATR)`  
**Target**: primer HVN o VAL por debajo del precio actual

---

## Lógica de target

```rust
let mut candidates = vp.hvn_nearby.clone();
candidates.push(vp.vah or vp.val);

// Para longs: nearest_above(candidates, px) — el más cercano que esté arriba
// Para shorts: nearest_below(candidates, px) — el más cercano que esté abajo
```

El target debe ser al menos 1.0×ATR de distancia (R:R mínimo de 1.0). Si no hay HVN/VAH alcanzable → `None` → no señal.

---

## Evidencia emitida

**Long**: `["thin_zone_above", "vwap_reclaim_or_above", "positive_delta", "cvd_positive", "target_next_HVN_or_VAH"]`  
**Short**: `["thin_zone_below", "vwap_loss_or_below", "negative_delta", "cvd_negative", "target_next_HVN_or_VAL"]`

## Invalidación semántica

**Long**: pérdida del VWAP o CVD se vuelve negativo → la liquidez que soportaba el movimiento se agotó  
**Short**: precio recupera el VWAP o CVD se vuelve positivo

---

## Régimen favorito

**TrendUp** y **Expansion** para longs (factor_regime = ×1.15). El scorer también premia fuertemente la confluencia de niveles de VP cerca del target.

---

## Gates críticos

- **ask_wall_nearby (long) / bid_wall_nearby (short)**: si hay una pared de órdenes inmediatamente en la dirección del breakout, bloquea la señal — la pared es resistencia activa que puede frenar el movimiento hacia la thin zone.
- **R:R mínimo 1.0**: si el primer HVN está demasiado cerca del precio para el riesgo calculado → sin señal.
