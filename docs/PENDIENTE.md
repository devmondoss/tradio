# Trabajo pendiente

Las fases 1–8 del strategy module están completadas. Lo que sigue es refinamiento y nuevas features.

---

## Completado (referencia histórica)

| Feature | Commit | Descripción |
|---------|--------|-------------|
| VWAP overlay | fase 4 | Session reset UTC, ±1σ/±2σ, AVWAP BOS |
| Volume Profile | fase 5 | 150-bin histogram, POC/VAH/VAL, HVN/LVN |
| CVD session reset | f725ee1 | CVD acumulado reinicia en cada día UTC |
| VPIN | f725ee1 | `mean(\|delta\|/vol)` sobre últimas 50 velas, alimenta toxic flow gate |
| CVD divergencias | f725ee1 | Precio HH + CVD slope negativo = BearishAbsorption, y viceversa |
| Relative Volume | f725ee1 | `current_vol / mean(últimas 20)`, barra verde/roja |
| Session lines | f725ee1 | Verticales punteadas Asia/London/NY, solo ≤4h |
| VRVP | ae5cee7 | Histograma de Volume Profile adapta al rango visible (scroll/zoom) |
| Key levels | a6da6c7 | PDH, PDL, Daily Open, Weekly Open — líneas horizontales con etiqueta |
| analyze_outcomes.py | 75b1f0c | Win rate, MFE/MAE, confianza por detector desde JSONL |

---

## Pendiente — indicadores

### Funding rate panel

**Qué es:** Tasa que pagan los longs a los shorts (o viceversa) cada 8h en contratos perp. Valor positivo = mercado sesgado long (longs sobrepagan). Valor negativo = mercado sesgado short.

**Para qué sirve:** Indicador de sentimiento extremo. Funding muy positivo en máximos = posible reversión. Complementa el orderflow para evitar entrar con el mercado sobreextendido en la dirección equivocada.

**Implementación:** Requiere endpoint REST dedicado por exchange (Binance: `/fapi/v1/fundingRate`, Bybit: `/v5/market/funding/history`). Nuevo `KlineIndicator::FundingRate`, panel de línea con cero como referencia y colores verde/rojo.

---

### OI z-score

**Qué es:** Desviación del OI actual respecto a su media histórica de N velas: `(OI − mean) / std`.

**Para qué sirve:** Normaliza el OI entre activos y épocas. Un z-score de +2 indica posicionamiento inusualmente alto — mercado cargado. Más útil que el delta crudo para comparar condiciones entre sesiones.

**Implementación:** Puede calcularse dentro de `OpenInterestIndicator` añadiendo una ventana rolling. No requiere fetch adicional.

---

### Large OI change markers

**Qué es:** Marcadores visuales (triángulos o puntos) en el chart de precios cuando el OI Delta supera N desviaciones estándar.

**Para qué sirve:** Señala eventos de posicionamiento masivo — momento en que grandes participantes abren o cierran posiciones. Útil para identificar el inicio de movimientos impulsivos.

**Implementación:** Derivado del OI Delta existente. Threshold configurable (p.ej. z-score > 2.0).

---

## Pendiente — VWAP

### AVWAP con anchor manual

**Qué es:** VWAP anclado a un punto específico elegido por el usuario en el chart (clic sobre una vela).

**Para qué sirve:** Permite anclar desde eventos clave — mínimo del día anterior, ruptura de rango, suelo de corrección. Más preciso que el AVWAP BOS automático porque el trader elige el punto semánticamente relevante.

**Implementación:** Requiere UI interactiva: capturar clic en el canvas, convertir coordenada X a timestamp o tick index, pasar el anchor al indicador VWAP. Es el cambio de mayor complejidad en UI de todos los pendientes.

---

### Session VWAPs

**Qué es:** Un VWAP separado por sesión (Asia, London, NY) en lugar de solo el diario.

**Para qué sirve:** Muestra dónde hizo valor cada sesión. Si el precio entra en NY por debajo del VWAP de London, es un contexto bajista para esa sesión. Permite razonar sobre quién está "ganando" entre sesiones.

**Implementación:** Dividir la lógica de reset del VWAP actual en 3 ventanas: 00:00–08:00, 08:00–16:00, 13:00–21:00 UTC (con solapamiento London/NY). Tres líneas separadas en el overlay.

---

## Pendiente — strategy module

### Stacked imbalance

**Qué es:** Detecta columnas verticales de imbalance en el footprint — N niveles consecutivos donde buy/sell supera un ratio umbral (p.ej. >3:1).

**Para qué sirve:** Stacked imbalances son zonas donde el mercado fue absorbido agresivamente. Actúan como soporte/resistencia en retesteos. El campo `flow.stacked_imbalance` siempre es `Unknown` actualmente.

**Implementación:** En `on_insert_trades` del `CumulativeDeltaIndicator` o en un indicador nuevo, iterar los levels del footprint buscando N imbalances consecutivos.

---

### Regime mejorado

**Qué es:** El regime actual usa solo OLS slope sobre closes normalizados por ATR.

**Mejoras:**
- **EMA crosses (21/55):** confirmación de TrendUp/TrendDown con menos ruido que el OLS
- **Volatility squeeze:** detectar Compression cuando ATR actual < ATR de las últimas 20 velas × 0.5
- **Stress:** volatilidad intradía muy alta relativa al ATR histórico

**Para qué sirve:** Reducir falsos `TrendUp`/`TrendDown` en mercados choppy y detectar compresiones antes del breakout.

---

## Pendiente — análisis de outcomes

Los datos en `strategy_outcomes.jsonl` permiten estos análisis (el script base existe en `scripts/analyze_outcomes.py`):

| Análisis | Descripción |
|----------|-------------|
| Win rate por sesión | ¿Qué sesión (Asia/London/NY) produce mejor resultado por detector? |
| Score decay | ¿Correlación entre score en el momento de la señal y el outcome real? |
| MAE/MFE distribution | Histograma para optimizar el stop/target placement |
| TTL optimization | ¿Cuántos TTL_EXPIRED habrían sido TARGET_HIT con más tiempo? |

Requieren acumulación de suficientes señales (~100+) para ser estadísticamente significativos.

---

## Layout objetivo

```
[55%] Precio + VWAP + AVWAP + Volume Profile VRVP + PDH/PDL/DO/WO + session lines
[10%] Volume + Relative Volume
[13%] CVD (con reset de sesión)
[13%] Open Interest + OI Delta (+ funding rate cuando esté listo)
[ 9%] ATR
```
