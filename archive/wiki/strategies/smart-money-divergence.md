# Detector: SmartMoneyDivergence

Archivo: `data/src/strategy/detectors/smart_money_divergence.rs`  
Requiere: `InstitutionalContext`

---

## Concepto

Los **top traders** (top 20 por volumen, reportados por Binance como "Global L/S Ratio — Top Traders") históricamente tienen mejor desempeño que el retail. Cuando hay una divergencia grande entre la posición de los top traders y el retail, el mercado tiende a moverse en la dirección de los top traders.

El setup más fuerte es: **smart money short, retail long** — el retail está atrapado en la dirección equivocada y eventualmente sus stops se activarán.

---

## Condiciones de activación

### SHORT — smart money short, retail long

```
# Divergencia fuerte en L/S ratios
1. inst.ls_ratio.top_traders_long_pct < cfg.smart_short_threshold (0.45)
   ← top traders predominantemente short (menos del 45% están long)
2. inst.ls_ratio.retail_long_pct > cfg.retail_long_threshold (0.60)
   ← retail predominantemente long (más del 60%)
3. divergence = retail_long - top_traders_long > cfg.min_divergence (0.18)
   ← mínimo 18% de divergencia

# Contexto de mercado que confirma
4. inst.funding.regime ∈ {ElevatedLong, ExtremeLong}
   ← funding elevado = retail pagando por sus longs = costo de carry

5. inst.oi_trend.trend != AccumulatingFast
   ← movimiento maduro, no acumulación nueva (que indicaría breakout real)

# Precio en zona de resistencia
6. |px - vah| < 0.5×ATR               ← precio cerca del VAH (resistencia natural)
   OR alguna wall_above dentro de 0.3×ATR

# Flujo débil
7. flow.cvd_slope ≤ 0                  ← CVD no confirmando el precio (debilidad)

# Gate: no durante cascade activa (demasiado ruido)
8. !inst.liquidations.cascade_detected
```

**Entry**: precio actual  
**Stop**: `entry + 1.5×ATR` — stop amplio; la divergencia puede tardar en resolverse  
**Target**: VAL — soporte del value area donde el smart money probablemente está cubierto  
**TTL**: `smd_ttl_ms` (20 min)

### LONG — smart money long, retail short

```
1. inst.ls_ratio.top_traders_long_pct > (1 - smart_short_threshold) = 0.55
2. inst.ls_ratio.retail_long_pct < (1 - retail_long_threshold) = 0.40
3. (-divergence) > cfg.min_divergence  ← mismo umbral pero inverso

4. inst.funding.regime ∈ {ElevatedShort, ExtremeShort}
5. !AccumulatingFast

6. |px - val| < 0.5×ATR               ← precio cerca del VAL (soporte natural)
   OR alguna wall_below dentro de 0.3×ATR

7. flow.cvd_slope ≥ 0                  ← CVD recuperándose

8. inst.ls_ratio.divergence_signal ∈ {SmartLongRetailShort, Neutral}
   ← confirmación adicional del signal de divergencia

9. !inst.liquidations.cascade_detected
```

**Entry**: precio actual  
**Stop**: `entry - 1.5×ATR`  
**Target**: VAH

---

## Evidencia emitida

**Short**: `["smart_money_short", "retail_long_extreme", "funding_elevated", "oi_mature", "price_at_resistance", "cvd_weak"]`  
**Long**: `["smart_money_long", "retail_short_extreme", "funding_negative_elevated", "oi_mature", "price_at_support", "cvd_recovering"]`

## Invalidación semántica

**Short**: smart money flips long (ratios cambian), funding normaliza, precio rompe la resistencia  
**Long**: smart money flips short, funding normaliza, precio rompe soporte

---

## Parámetros calibrables

| Parámetro | Default | Descripción |
|-----------|---------|-------------|
| `smart_short_threshold` | 0.45 | Top traders long pct por debajo = predominantly short |
| `retail_long_threshold` | 0.60 | Retail long pct por encima = predominantly long |
| `min_divergence` | 0.18 | Diferencia mínima entre retail y smart |
| `smd_ttl_ms` | 20 min | Ventana media — la divergencia se resuelve gradualmente |

---

## Por qué el stop es 1.5×ATR

Las divergencias de L/S ratio pueden persistir varios bars antes de resolverse. Un stop de 1×ATR sería demasiado ajustado y produciría muchos STOP_HIT seguidos de movimientos a favor. El 1.5×ATR permite que el mercado procese ruido antes de invalidar la tesis.

Este parámetro tiene alta influencia en el MAE — analizar `mae_r` en los datos históricos para validar si 1.5 es apropiado o si 1.0/2.0 funciona mejor en el régimen actual.

---

## Diferencia con FundingExhaustion

| | SmartMoneyDivergence | FundingExhaustion |
|--|---------------------|-------------------|
| Trigger principal | Divergencia L/S ratio | Funding extremo |
| Funding | Elevado (confirmación) | Extremo (condición necesaria) |
| OI | Maduro (no AccumulatingFast) | Decreciendo activamente |
| Frecuencia | Más frecuente | Menos frecuente, mayor convicción |
| TTL | 20 min | 30 min |
