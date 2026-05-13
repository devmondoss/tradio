# Indicadores — Arquitectura y Estado Actual

## Tipos de indicador

### Panel indicators (panel separado debajo del chart)
Renderizan en su propio espacio usando el sistema `indicator_row` + `LinePlot` / `BarPlot`.

| Indicador | Tipo | Mercado |
|-----------|------|---------|
| Volume | BarPlot | Spot + Perps |
| CVD | LinePlot | Spot + Perps |
| Open Interest | LinePlot | Perps |
| OI Delta | BarPlot | Perps |
| ATR | LinePlot | Spot + Perps |

### Overlay indicators (sobre el chart principal)
Renderizan directamente en el canvas de las velas. No generan panel separado.

| Indicador | Tipo | Mercado |
|-----------|------|---------|
| VWAP | Línea + bandas | Spot + Perps |
| Volume Profile | Histograma horizontal + niveles | Spot + Perps |

La función `is_overlay_indicator(KlineIndicator) -> bool` en `src/chart/indicator/kline.rs` distingue los dos tipos.

---

## VWAP

**Archivo:** `src/chart/indicator/kline/vwap.rs`

### Cálculo

- **Precio típico** = (High + Low + Close) / 3
- **VWAP** = Σ(precio_típico × volumen) / Σ(volumen)
- **Desviación estándar** = √(Σ(precio²×vol)/Σvol − VWAP²)
- **Bandas** = VWAP ± 1σ y VWAP ± 2σ

### Session reset

Reinicia en cada medianoche UTC. Para charts basados en tiempo: detecta cambio de día con `timestamp_ms / 86_400_000`. Para charts de ticks: acumula sin reset (no hay referencia temporal confiable).

### Rendering

- Línea VWAP: 2px, `rgba(0.20, 0.75, 1.0, 0.95)`
- Banda ±1σ: `rgba(0.0, 0.55, 1.0, 0.09)`
- Banda ±2σ: `rgba(0.0, 0.55, 1.0, 0.05)`

---

## Volume Profile

**Archivo:** `src/chart/indicator/kline/volume_profile.rs`

### Cálculo

**Ventana:** últimas 300 velas.

**Distribución de volumen por vela** (cuando no hay datos de trades individuales):
- 5 niveles representativos con pesos: high 10%, 75% del rango 15%, close 50%, 25% del rango 15%, low 10%

**Cuando hay datos de footprint:** volumen distribuido por precio real de cada trade.

**POC** (Point of Control): precio con más volumen acumulado.

**VAH/VAL** (Value Area High/Low): expansión desde el POC tomando el lado con más volumen hasta cubrir el 70% del volumen total.

**Histograma:** 150 bins de igual ancho entre precio mínimo y máximo del rango analizado.

### Rendering

- **Histograma** en el 14% derecho del chart:
  - POC: dorado `rgba(1.0, 0.78, 0.05, 0.90)`
  - HVN (>65% del max): púrpura `rgba(0.55, 0.36, 0.96, 0.60)`
  - LVN (<12% del max): azul tenue `rgba(0.3, 0.65, 1.0, 0.18)`
  - Normal: azul `rgba(0.45, 0.65, 0.95, 0.35)`
- **Niveles** como líneas punteadas horizontales: POC dorado, VAH/VAL púrpura
- **Value area** como zona sombreada entre VAH y VAL

---

## OI Delta

**Archivo:** `src/chart/indicator/kline/oi_delta.rs`

### Cálculo

`delta[i] = OI[i] - OI[i-1]`

Almacena el OI absoluto en `raw: BTreeMap<UnixMs, f32>` y los deltas calculados en `delta: BTreeMap<UnixMs, f32>`. Se recalcula completo en cada `on_open_interest`.

### Rendering

`BarPlot` con `BarClass::Overlay { overlay: delta }`:
- Barras verdes: delta positivo (nuevas posiciones entrando)
- Barras rojas: delta negativo (posiciones cerrando o liquidación)

### Disponibilidad

Solo perps. Timeframes M5–H4 (excluye H2). No disponible en charts de ticks.

### Interpretación

| Precio | OI | CVD | Señal |
|--------|-----|-----|-------|
| ↑ | ↑ | ↑ | Longs agresivos entrando |
| ↓ | ↑ | ↓ | Shorts agresivos entrando |
| ↑ | ↓ | — | Short covering |
| ↓ | ↓ | — | Longs cerrando / deleveraging |

---

## Trait KlineIndicatorImpl — métodos de overlay

Definidos en `src/chart/indicator/kline.rs` con implementaciones vacías por defecto:

```rust
fn overlay_line_points(&self, earliest: u64, latest: u64) -> Vec<(u64, f32)>
fn overlay_levels(&self) -> Vec<(f32, [f32; 4])>         // (precio, [r,g,b,a])
fn overlay_bands(&self, earliest: u64, latest: u64) -> Vec<Vec<(u64, f32, f32)>>
fn overlay_volume_profile(&self) -> &[ProfileBar]
fn overlay_volume_profile_max(&self) -> f64
```
