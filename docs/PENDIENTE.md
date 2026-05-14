# Trabajo pendiente

Las fases 1–8 del strategy module están completadas. Lo que sigue es refinamiento y nuevas features.

---

## Mejoras a indicadores existentes

### Volume Profile

- **Visible Range Volume Profile (VRVP):** El perfil usa una ventana fija de 300 velas. Lo correcto es calcular el perfil del rango visible en pantalla, adaptándose al zoom. Requiere pasar `earliest/latest` al cálculo del histograma en lugar de pre-computarlo en `rebuild_from_source`.

- **HVN/LVN markers en el eje de precio:** Marcar las zonas de alto/bajo volumen como líneas o marcadores en el scale lateral.

- **Sesión completa vs ventana fija:** Opción para calcular el perfil desde el inicio de la sesión del día en lugar de las últimas 300 velas.

### VWAP

- **AVWAP manual:** VWAP anclado a un punto específico elegido por el usuario (requiere UI de selección en el chart con clic + drag).

- **Sesiones:** VWAP separado por sesión Asia / London / NY.
  | Sesión | Horario UTC |
  |--------|-------------|
  | Asia | 00:00–08:00 |
  | London | 08:00–16:00 |
  | New York | 13:00–21:00 |

### CVD

- **Divergencias automáticas:** Detectar cuando precio hace HH/LL pero CVD no confirma:
  - Precio HH + CVD LH → absorción compradora (bearish)
  - Precio LL + CVD HL → absorción vendedora (bullish)
- **Session reset:** Resetear CVD acumulado en cada sesión diaria.

### OI

- **OI z-score:** Desviación del OI actual vs su media histórica (N velas).
- **Large OI change markers:** Markers visuales en el chart cuando OI Delta supera N desviaciones estándar.

---

## Nuevos indicadores

| Indicador | Descripción | Prioridad |
|-----------|-------------|-----------|
| Funding rate | Tasa de financiamiento cada 8h (solo perps) | Media |
| Premium/basis | Precio perp vs spot | Media |
| Relative volume | Volumen actual vs media del mismo periodo en días anteriores | Alta |
| Liquidation zones | Estimación de niveles de stop-hunt y liquidaciones en cascada | Baja |

---

## Sesiones y niveles clave

Dibujar separadores de sesión en el chart principal como líneas verticales opcionales.

Niveles horizontales opcionales:
- Previous Day High/Low
- Previous Week High/Low
- Weekly Open
- Daily Open
- Monthly Open

---

## Strategy module — refinamientos

### VPIN (Volume-Synchronized Probability of Informed Trading)

Cálculo bucket-based para rellenar `flow.vpin`. Actualmente siempre `None`.

**Fórmula simplificada:**
- Dividir el volumen en buckets de tamaño fijo
- En cada bucket: `VPIN = |buy_vol − sell_vol| / total_vol`
- VPIN corriente = media móvil de los últimos 50 buckets

### Stacked imbalance

Detectar columnas verticales de imbalance en el footprint (`flow.stacked_imbalance`).
- Imbalance: ratio buy/sell en un nivel de precio supera un umbral (e.g., >3:1)
- Stacked: N niveles consecutivos con imbalance del mismo lado

### Regime mejorado

El regime actual usa OLS sobre closes. Mejoras posibles:
- Incorporar ATR histórico para Compression/Expansion más precisos
- Usar EMAs cruzadas (21/55) como confirmación de TrendUp/TrendDown
- `Stress`: volatilidad intradiaria muy alta relativa a ATR

### CVD divergencia en scoring

Añadir al scoring puntos por divergencias CVD/precio detectadas automáticamente.

---

## Outcome tracker — análisis

Los datos acumulados en `strategy_outcomes.jsonl` permiten:

1. **Win rate por detector:** % de señales que alcanzaron target vs stop
2. **MAE/MFE ratio:** distribución para optimizar el stop/target placement
3. **Decay del score:** correlación entre score en el momento de la señal y outcome real
4. **Filtros de sesión:** qué sesiones producen mejores resultados por detector

Script de análisis pendiente: `scripts/analyze_outcomes.py` (no existe aún).

---

## Layout sugerido

```
[55%] Precio + VWAP + AVWAP + Volume Profile + niveles (PDH/PDL, sesiones)
[10%] Volume + relative volume
[13%] CVD + divergencias marcadas
[13%] Open Interest + OI Delta
[ 9%] ATR / volatilidad
```

---

## Capa de interpretación (objetivo)

El módulo de estrategia actual detecta setups técnicos. La capa siguiente es interpretación semántica:

```
Regime:      trend / range / chop / expansion / compression
Flow:        buyers aggressive / sellers aggressive / absorption
Positioning: new longs / new shorts / short covering / deleveraging
Location:    above VWAP / below VWAP / at POC / at LVN / at VAH
Risk:        ATR high / ATR low / high volatility / squeeze
```

Esto permitiría un "market narrator" que genere texto descriptivo del estado del mercado en cada depth update.
