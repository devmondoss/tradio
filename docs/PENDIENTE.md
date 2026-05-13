# Trabajo pendiente

## Urgente

### Lyon panic al arrancar
Panic `assertion failed: p.y.is_finite()` ocurre ~8s después del launch.
- `RUST_BACKTRACE=1` ya está activo en `run.bat`
- Correr la app y copiar el backtrace completo
- Sospechosos: `LinePlot::draw` (line.rs:155) y `BarPlot::draw` — usan `Path::line` sin guard de `is_finite`

---

## Indicadores — mejoras planificadas

### Volume Profile
- **Visible Range Volume Profile (VRVP):** Actualmente usa una ventana fija de 300 velas. Lo correcto sería calcular el perfil del rango visible en pantalla, adaptándose al zoom. Requiere pasar el rango visible al cálculo del histograma en lugar de pre-computarlo.
- **HVN/LVN markers en el eje de precio:** Marcar zonas de alto/bajo volumen en el scale lateral

### VWAP
- **Anchored VWAP:** VWAP anclado a un swing específico (requiere UI para seleccionar el punto de anclaje)
- **VWAP de sesión Asia/London/NY:** Múltiples VWAPs simultáneos por sesión

### CVD
- **Divergencias automáticas:** Detectar cuando precio hace HH/LL pero CVD no confirma
  - Precio HH + CVD LH = absorción compradora (bearish)
  - Precio LL + CVD HL = absorción vendedora (bullish)
- **Session reset:** Resetear CVD acumulado en cada sesión diaria

### OI
- **OI z-score:** Desviación del OI actual respecto a su media histórica
- **Large OI change markers:** Markers visuales en el chart de velas cuando OI Delta supera N desviaciones estándar

### Nuevos indicadores discutidos
- **Funding rate** (para perps): tasa de financiamiento cada 8h
- **Premium/basis:** precio perp vs spot
- **Liquidation zones:** zonas estimadas de stop-hunt y liquidaciones en cascada
- **Relative volume:** volumen actual vs media del mismo periodo en días anteriores

---

## Sesiones

Dibujar separadores de sesión en el chart principal:

| Sesión | Horario UTC |
|--------|-------------|
| Asia | 00:00–08:00 |
| London | 08:00–16:00 |
| New York | 13:00–21:00 |

Además: Previous Day High/Low, Weekly Open, Daily Open — como líneas horizontales opcionales.

---

## Layout sugerido (del usuario)

```
[55%] Precio + VWAP + Volume Profile + niveles clave
[10%] Volume + relative volume
[13%] CVD + divergencias
[13%] Open Interest + OI Delta
[9%]  ATR / volatilidad
```

---

## Strategy module — fases pendientes

Ver `docs/STRATEGY.md` para el plan completo.

- **Fase 4:** Exponer valores actuales de VWAP/ATR/VolProfile al contexto de estrategia
- **Fase 5:** AVWAP, perfil de volumen por ventana, detección de HVN/LVN
- **Fase 6:** Datos derivados (regime, vpin, cvd_slope, absorción)
- **Fase 7:** Toggle de UI para el overlay de estrategia
- **Fase 8:** Outcome tracker (MFE/MAE por señal)

---

## Capa de interpretación (objetivo final)

Lo que falta no son más indicadores sino semántica operacional:

```
Regime:      trend / range / chop / expansion / compression
Flow:        buyers aggressive / sellers aggressive / absorption
Positioning: new longs / new shorts / covering / deleveraging
Location:    above VWAP / below VWAP / at POC / at LVN / at VAH
Risk:        ATR high / ATR low / high volatility / squeeze
```
