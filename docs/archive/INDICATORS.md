# Indicadores — Arquitectura y Referencia Completa

## Tipos de indicador

### Panel indicators
Renderizan en su propio panel debajo del chart principal usando `indicator_row` + `LinePlot` / `BarPlot`.

| Indicador | Plot | Mercado | Basis |
|-----------|------|---------|-------|
| Volume | BarPlot | Spot + Perps | Tiempo + Tick |
| CVD | LinePlot | Spot + Perps | Tiempo + Tick |
| Open Interest | LinePlot | Solo Perps | Solo Tiempo |
| OI Delta | BarPlot | Solo Perps | Solo Tiempo |
| ATR(14) | LinePlot | Spot + Perps | Tiempo + Tick |
| Relative Volume | BarPlot | Spot + Perps | Tiempo + Tick |

### Overlay indicators
Renderizan directamente en el canvas de velas. No generan panel. La función
`is_overlay_indicator(KlineIndicator) -> bool` en `src/chart/indicator/kline.rs` los distingue.

| Indicador | Overlay | Mercado |
|-----------|---------|---------|
| VWAP | Línea + bandas σ | Spot + Perps |
| Volume Profile | Histograma VRVP + niveles | Spot + Perps |
| Session lines | Verticales por sesión | Spot + Perps (≤4h) |
| Key levels | PDH, PDL, Daily Open, Weekly Open | Spot + Perps (tiempo) |

---

## Trait `KlineIndicatorImpl`

Definido en `src/chart/indicator/kline.rs`. Todos los métodos tienen implementación por defecto (no-op / None / vec vacío).

### Métodos de ciclo de vida

```rust
fn rebuild_from_source(&mut self, source: &PlotData<KlineDataPoint>)
fn on_insert_klines(&mut self, klines: &[Kline], source: &PlotData<KlineDataPoint>)
fn on_insert_trades(&mut self, trades: &[Trade], old_dp_len: usize, source: &PlotData<KlineDataPoint>)
fn on_ticksize_change(&mut self, source: &PlotData<KlineDataPoint>)
fn on_basis_change(&mut self, source: &PlotData<KlineDataPoint>)
fn on_open_interest(&mut self, pairs: &[exchange::OpenInterest])
```

### Métodos de rendering

```rust
fn element<'a>(&'a self, chart: &'a ViewState, visible_range: RangeInclusive<u64>) -> iced::Element<'a, Message>
fn overlay_line_points(&self, earliest: u64, latest: u64) -> Vec<(u64, f32)>
fn overlay_levels(&self) -> Vec<(f32, [f32; 4])>            // (precio, [r,g,b,a])
fn overlay_bands(&self, earliest: u64, latest: u64) -> Vec<Vec<(u64, f32, f32)>>
fn overlay_volume_profile(&self) -> &[ProfileBar]
fn overlay_volume_profile_max(&self) -> f64
```

### Métodos de datos para strategy

```rust
fn latest_vwap(&self) -> Option<f64>
fn latest_avwap_bos(&self) -> Option<f64>
fn latest_vol_profile_levels(&self) -> Option<(f64, f64, f64)>   // (poc, vah, val)
fn latest_hvn_lvn_nearby(&self, price: f64, atr: f64) -> (Vec<f64>, Vec<f64>)
fn latest_cvd(&self) -> Option<(f64, f64)>                        // (cumulative, delta)
fn latest_cvd_slope(&self) -> Option<f64>
fn latest_vpin(&self) -> Option<f64>                              // VPIN candle-level
fn latest_volume(&self) -> Option<(f64, f64)>                     // (buy, sell)
fn latest_atr(&self) -> Option<f64>
fn oi_snapshot(&self) -> Option<Vec<exchange::OpenInterest>>
fn update_visible_range(&mut self, earliest: u64, latest: u64, source: &PlotData<KlineDataPoint>)
```

### Métodos de disponibilidad

```rust
fn availability(&self, chart: &ViewState) -> IndicatorAvailability
fn fetch_range(&mut self, ctx: &FetchCtx) -> Option<FetchRange>
```

---

## BasisSeries\<T\>

Tipo de almacenamiento dual en `data/src/chart.rs`:

```rust
pub enum BasisSeries<T> {
    Time(BTreeMap<UnixMs, T>),
    Tick(BTreeMap<u64, T>),
}
```

- `Time`: clave = `UnixMs` (milisegundos epoch), orden cronológico ascendente
- `Tick`: clave = `u64` índice (0 = más antiguo), iterable en orden inverso para rendering

---

## VWAP

**Archivo:** `src/chart/indicator/kline/vwap.rs`

### Fórmula

```
precio_típico = (High + Low + Close) / 3
VWAP         = Σ(precio_típico × volumen) / Σ(volumen)
varianza     = Σ(precio_típico² × volumen) / Σ(volumen) − VWAP²
σ            = √max(varianza, 0)
banda_sup_1  = VWAP + σ
banda_inf_1  = VWAP − σ
banda_sup_2  = VWAP + 2σ
banda_inf_2  = VWAP − 2σ
```

### Session reset

Reinicia los acumuladores en cada cambio de día UTC: `timestamp_ms / 86_400_000`.
En charts de ticks no hay reset (sin referencia temporal fiable).

El **CVD acumulado** también aplica este reset: cada vez que cambia el día UTC, `cumulative` vuelve a cero. El delta por vela (`CumulativeDeltaPoint.delta`) no se ve afectado.

### AVWAP BOS (Anchored VWAP)

Ancla automática al swing-low más reciente en las últimas 50 velas.
Swing-low = vela cuyo low es estrictamente menor que el low de las velas anterior y siguiente.
Si no se encuentra pivot, ancla al inicio de la ventana de 50 velas.

```rust
fn compute_avwap_time(datapoints) -> Option<f32>
fn compute_avwap_tick(datapoints) -> Option<f32>
```

Expuesto vía `latest_avwap_bos() -> Option<f64>`.

### VwapPoint struct

```rust
pub struct VwapPoint {
    pub vwap: f32,
    pub upper_band1: f32,
    pub lower_band1: f32,
    pub upper_band2: f32,
    pub lower_band2: f32,
}
```

### Rendering

- Línea VWAP: `rgba(0.20, 0.75, 1.0, 0.95)`, 2px
- Banda ±1σ: `rgba(0.0, 0.55, 1.0, 0.09)`
- Banda ±2σ: `rgba(0.0, 0.55, 1.0, 0.05)`

---

## Volume Profile

**Archivo:** `src/chart/indicator/kline/volume_profile.rs`

### Constantes

```rust
const VALUE_AREA_PCT: f64  = 0.70;   // 70% del volumen total = value area
const HISTOGRAM_BINS: usize = 150;   // bins del histograma horizontal
const WINDOW: usize = 300;           // velas en la ventana base (no-VRVP)
```

### VRVP (Visible Range Volume Profile)

El histograma se recalcula automáticamente al rango visible en pantalla (scroll / zoom).
Cada frame de rendering, `invalidate()` en `KlineChart` llama `update_visible_range(earliest, latest, source)`.

El cálculo evita reconstruir el histograma si el rango no cambió:
```rust
if self.last_visible_range == Some((earliest, latest)) {
    return; // sin cambio
}
```

Para charts de tiempo: usa `ts.datapoints.range(UnixMs::new(earliest)..=UnixMs::new(latest))`.
Para charts de tick: filtra por `dp.kline.time.as_u64()` dentro del rango.

### Distribución de volumen (sin footprint trades)

5 niveles por vela con pesos:
- High: 10%
- 75% del rango: 15%
- Close: 50% (40% + 10% extra al final)
- 25% del rango: 15%
- Low: 10%

### Cálculo de niveles

1. Construye `BTreeMap<i64, f64>` de precio (en units) → volumen acumulado
2. **POC** = precio con mayor volumen
3. **Value Area**: expande desde POC tomando en cada paso el lado (arriba/abajo) con más volumen, hasta acumular el 70%
4. **VAH** = precio más alto del value area
5. **VAL** = precio más bajo del value area

### HVN/LVN nearby (para estrategia)

```rust
fn latest_hvn_lvn_nearby(&self, price: f64, atr: f64) -> (Vec<f64>, Vec<f64>)
```

Filtra `histogram` dentro de `price ± 3 × ATR`.
- **HVN** (High Volume Node): bins con volumen ≥ media + 0.5σ
- **LVN** (Low Volume Node): bins con volumen ≤ media − 0.5σ

### ProfileBar struct

```rust
pub struct ProfileBar {
    pub price: f32,   // precio en unidades flotantes (ya escalado 1e-8)
    pub volume: f64,
}
```

### VolumeProfilePoint struct

```rust
pub struct VolumeProfilePoint {
    pub poc: f32,
    pub vah: f32,
    pub val: f32,
}
```

### Rendering

Histograma en el 14% derecho del chart:
- POC bin: `rgba(1.0, 0.78, 0.05, 0.90)` — dorado
- HVN (>65% del max): `rgba(0.55, 0.36, 0.96, 0.60)` — púrpura
- LVN (<12% del max): `rgba(0.3, 0.65, 1.0, 0.18)` — azul tenue
- Normal: `rgba(0.45, 0.65, 0.95, 0.35)` — azul

Niveles horizontales punteados: POC dorado, VAH/VAL púrpura.
Value area como zona sombreada.

---

## CVD (Cumulative Volume Delta)

**Archivo:** `src/chart/indicator/kline/cumulative_delta.rs`

### Cálculo

```
delta[i]      = buy_volume[i] − sell_volume[i]
CVD[i]        = CVD[i−1] + delta[i]
```

- Si hay datos de footprint trades: usa la suma de `delta_qty()` por nivel de precio
- Si no: usa `buy_sell()` del volumen de la vela

### CumulativeDeltaPoint struct

```rust
pub struct CumulativeDeltaPoint {
    pub delta: Qty,       // delta de esta vela
    pub cumulative: Qty,  // suma acumulada desde el inicio
}
```

### CVD Slope (para estrategia)

```rust
fn latest_cvd_slope(&self) -> Option<f64>
```

OLS (regresión lineal) sobre los últimos 20 valores de CVD acumulado:
- `slope = (n·Σxy − Σx·Σy) / (n·Σx² − (Σx)²)`
- Retorna `None` si hay menos de 3 datapoints
- Positivo = CVD subiendo, negativo = CVD bajando

### VPIN (para estrategia)

```rust
fn latest_vpin(&self) -> Option<f64>
```

Aproximación candle-level de VPIN: `mean(|delta_i| / vol_i)` sobre las últimas 50 velas.
- `None` si `vpin == 0.0` (sin datos o todos cero)
- Valores altos (>0.65) indican flujo tóxico — el toxic flow gate los bloquea

### Disponibilidad

Requiere datos de volumen direccional (buy/sell split). Si no están disponibles, muestra mensaje "CVD requires directional trade-volume data".

---

## Volume

**Archivo:** `src/chart/indicator/kline/volume.rs`

Almacena `BasisSeries<Volume>`. El tipo `Volume` tiene:
- `.total()` — volumen total
- `.buy_sell()` → `Option<(Qty, Qty)>` — split buy/sell si disponible

Rendering con `BarClass::Overlay { overlay: buy − sell }`:
- Barra total: altura = volumen total
- Overlay: color verde/rojo según diferencia neta

---

## Open Interest

**Archivo:** `src/chart/indicator/kline/open_interest.rs`

Almacena `BTreeMap<UnixMs, f32>` con valores de OI absoluto.

### Disponibilidad

Solo perps, solo basis de tiempo. Timeframes disponibles determinados por el exchange.

### oi_snapshot()

```rust
fn oi_snapshot(&self) -> Option<Vec<exchange::OpenInterest>>
```

Expone los datos existentes para bootstrap del OI Delta cuando se activa tardíamente.

---

## OI Delta

**Archivo:** `src/chart/indicator/kline/oi_delta.rs`

### Cálculo

```
delta[i] = OI[i] − OI[i−1]
```

Almacena OI absoluto en `raw: BTreeMap<UnixMs, f32>` y deltas en `delta: BTreeMap<UnixMs, f32>`.
Recalcula completo en cada `on_open_interest`.

### Bootstrap

Cuando se activa el OI Delta con datos de OI ya cargados, `toggle_indicator` llama
`oi_snapshot()` del indicador OI y pasa los datos vía `on_open_interest`.

### Disponibilidad

Solo perps. Timeframes M5–H4 (excluye H2, M1, M3).

### Rendering

`BarPlot` con `BarClass::Overlay`:
- Barras verdes: delta positivo (nuevas posiciones / longs abriendo)
- Barras rojas: delta negativo (posiciones cerrando / longs liquidando)

### Interpretación

| Precio | OI Delta | Señal |
|--------|----------|-------|
| ↑ | + | Longs agresivos entrando (bullish) |
| ↓ | + | Shorts agresivos entrando (bearish) |
| ↑ | − | Short covering (rally poco fiable) |
| ↓ | − | Longs cerrando / deleveraging (bearish) |

---

## ATR (Average True Range)

**Archivo:** `src/chart/indicator/kline/atr.rs`

### Fórmula

```
TR[0]    = High − Low
TR[i]    = max(High−Low, |High−PrevClose|, |Low−PrevClose|)
ATR[1..14] = media simple de TR (calentamiento)
ATR[i≥14]  = (ATR[i−1] × 13 + TR[i]) / 14   (Wilder smoothing)
```

### Constante

```rust
const ATR_PERIOD: usize = 14;
```

---

## Relative Volume

**Archivo:** `src/chart/indicator/kline/relative_volume.rs`

### Cálculo

```
mean[i] = media de volumen total de las últimas LOOKBACK (20) velas (excluyendo la actual)
ratio[i] = volume[i] / mean[i]
```

Si la ventana está vacía (menos de 1 vela anterior), `mean = volume[i]` y `ratio = 1.0`.

### RelativeVolumePoint struct

```rust
pub struct RelativeVolumePoint {
    pub ratio: f32,      // volumen actual / media de los últimos 20 → 1.0 = promedio, 2.0 = doble
    pub direction: f32,  // +1.0 si buy > sell, -1.0 si sell > buy, 0.0 si desconocido
}
```

### Rendering

`BarPlot` con `BarClass::Overlay { overlay: ratio × direction }`:
- Barra principal: altura = ratio
- Verde si buy > sell, rojo si sell > buy, neutro si dirección desconocida

### Tooltip

`"Rel Vol: 1.84x"` — muestra el ratio con 2 decimales.

---

## Session lines y key levels

**Archivo:** `src/chart/kline.rs` (funciones libres)

### Session lines

Líneas verticales punteadas que marcan el inicio de cada sesión de trading. Solo se dibujan si `timeframe ≤ 4h`.

| Sesión | Hora UTC | Color |
|--------|----------|-------|
| Asia | 00:00 | `rgba(0.2, 0.5, 1.0, 0.20)` — azul |
| London | 08:00 | `rgba(0.2, 0.8, 0.3, 0.20)` — verde |
| New York | 13:00 | `rgba(1.0, 0.6, 0.1, 0.20)` — naranja |

`LineDash { segments: &[5.0, 5.0], offset: 0 }`

### Key levels

Líneas horizontales punteadas con etiqueta de texto. Solo para charts de tiempo (`PlotData::TimeBased`).

| Nivel | Color | Cálculo |
|-------|-------|---------|
| PDH (Previous Day High) | `rgba(0.7, 0.7, 0.7, 0.55)` | Máximo de velas del día anterior UTC |
| PDL (Previous Day Low) | `rgba(0.7, 0.7, 0.7, 0.55)` | Mínimo de velas del día anterior UTC |
| Daily Open | `rgba(0.2, 0.8, 0.3, 0.60)` | Close de la primera vela del día UTC actual |
| Weekly Open | `rgba(0.2, 0.5, 1.0, 0.60)` | Close de la primera vela desde el lunes UTC |

`LineDash { segments: &[3.0, 6.0], offset: 0 }`

**Cálculo del lunes:** `epoch_day_of_week = (current_day + 3) % 7` (día epoch 0 = jueves).

---

## Registro de indicadores en el enum

**Archivo:** `data/src/chart/indicator.rs`

```rust
pub enum KlineIndicator {
    Volume,           // Spot + Perps
    CumulativeDelta,  // Spot + Perps
    OpenInterest,     // Solo Perps
    OiDelta,          // Solo Perps
    Vwap,             // Spot + Perps (overlay)
    VolumeProfile,    // Spot + Perps (overlay)
    Atr,              // Spot + Perps
    RelativeVolume,   // Spot + Perps
}
```

Cada variante debe estar en `FOR_SPOT` y/o `FOR_PERPS` para aparecer en el menú de la UI.
Los overlays (`Vwap`, `VolumeProfile`) no generan panel separado.
