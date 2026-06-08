# Sistema de Rendering del KlineChart

## ViewState — el núcleo del rendering

`ViewState` contiene todo lo necesario para convertir datos de mercado a píxeles. Se comparte entre el chart principal y sus indicadores.

```rust
ViewState {
    bounds: Rectangle,          // Tamaño del canvas en píxeles
    scaling: f32,               // Factor de zoom (1.0 = default)
    translation: Vector,        // Offset de pan en píxeles
    cell_width: f32,            // Ancho de una vela en píxeles
    cell_height: f32,           // Altura de un "tick" de precio en píxeles
    // ...
}
```

---

## Conversiones de coordenadas

### Precio → Píxel Y

```
y = (highest_price - price) / price_step × cell_height + offset_y
```

- `highest_price`: precio máximo del viewport actual
- `price_step`: tamaño de un tick del ticker (ej. 0.01 para BTCUSDT en Binance)
- `cell_height`: píxeles por tick, determinado por zoom
- `offset_y`: margen superior del canvas

### Timestamp / Índice → Píxel X

```
x = (candle_index - first_visible_index) × cell_width + offset_x
```

- Para `TimeBased`: `candle_index = (timestamp - earliest_timestamp) / interval_ms`
- Para `TickBased`: índice secuencial del tick
- `cell_width`: píxeles por vela, determinado por zoom
- `offset_x`: margen izquierdo del canvas

Ambas funciones guardan NaN si los parámetros son inválidos. Todos los paths de rendering tienen guards `is_finite()` para evitar panics de lyon.

---

## Sistema de cache de iced

Iced usa un sistema de cache para evitar redibujar el canvas en cada frame cuando no hay cambios.

```rust
cache: iced::widget::canvas::Cache
```

**Ciclo de vida:**
1. `cache.draw(renderer, bounds, |frame| { ... })` — si el cache está válido, devuelve la geometría cacheada directamente sin llamar al closure
2. Si `cache.clear()` fue llamado desde el último frame → ejecuta el closure y guarda el resultado
3. El closure construye el `Frame`: paths, strokes, fills, texto

**Qué dispara `cache.clear()` (= `invalidate()`):**

| Evento | Resultado |
|--------|-----------|
| Nueva kline o trade recibido | Redibuja todo (datos cambiaron) |
| Scroll o zoom | Redibuja todo (viewport cambió) |
| Indicador activado/desactivado | Redibuja todo |
| Strategy signal nueva | Redibuja todo |
| Solo movimiento del mouse | Solo actualiza crosshair labels (cache principal intacto) |
| `Tick(now)` sin cambios | Redibuja solo el crosshair, no las velas |

El sistema tiene cachés separados para diferentes capas:
- Cache principal: velas, indicadores de panel
- Cache de crosshair: líneas de cursor + labels de precio/tiempo
- Cache de overlay: VWAP, Volume Profile, strategy signals

---

## Scroll y Zoom

### Zoom (rueda del ratón)

```rust
scaling = (scaling * factor).clamp(MIN_SCALING, MAX_SCALING)
cell_width  = BASE_CELL_WIDTH * scaling
cell_height = BASE_CELL_HEIGHT * scaling
```

El punto de ancla del zoom es la posición del cursor: el precio/tiempo bajo el cursor se mantiene fijo mientras el resto escala.

### Pan (arrastre o scroll horizontal)

Modifica `translation.x`. Se clampea para no desplazarse más allá del inicio de los datos disponibles ni demasiado a la derecha.

### Detección de viewport vacío

Después de cada scroll/zoom, el chart compara el timestamp más antiguo visible con el timestamp más antiguo en los datos:

```
if oldest_visible_timestamp < oldest_data_timestamp:
    request_fetch(FetchRange::Kline(
        oldest_visible_timestamp - look_ahead_buffer,
        oldest_data_timestamp
    ))
```

Esto dispara el fetch HTTP de datos históricos.

---

## Frame de rendering (kline)

El método `draw()` del canvas ejecuta en orden:

```
1. Background fill (tema actual)

2. Grid horizontal (líneas de precio)
   → cada N ticks de precio, línea punteada tenue

3. Velas principales
   → Para cada candle visible:
      - Cuerpo (open/close): rectángulo
      - Mecha (high/low): línea vertical
      - Color: verde si close > open, rojo si close < open

4. Overlay de indicadores
   → VWAP: línea + bandas sigma
   → Volume Profile: histograma VRVP (lado derecho)
   → Session lines: verticales punteadas
   → Key levels: horizontales punteadas con label
   → Strategy signals: entry/stop/target

5. Footprint (si está en modo Footprint)
   → Para cada vela visible, itera sus price levels
   → Renderiza buy_qty y sell_qty como texto o barras
   → Resalta imbalances

6. Crosshair
   → Línea horizontal en precio del cursor
   → Línea vertical en timestamp del cursor
   → Labels de precio y tiempo en los ejes

7. Panel indicators (sub-panels)
   → Cada indicador activo renderiza en su propio panel debajo
   → LinePlot (CVD, ATR, OI, RelVol) o BarPlot (Volume, OiDelta)
```

---

## Rendering de indicadores de panel (sub-panels)

Cada indicador de panel genera su propio `iced::Element` vía `element()`:

```rust
fn element<'a>(&'a self, chart: &'a ViewState, visible_range: RangeInclusive<u64>) -> iced::Element<'a, Message>
```

Internamente usa `LinePlot` o `BarPlot` del módulo `src/chart/indicator/plot/`.

**LinePlot (`plot/line.rs`):**
- Recibe `Vec<(x_pixel, y_pixel)>` pre-calculados
- Dibuja polilínea con stroke configurable
- Guard: si `!sx.is_finite() || !sy.is_finite()` → `prev = None`, no dibuja segmento

**BarPlot (`plot/bar.rs`):**
- Recibe `Vec<BarItem { x, y_base, y_total, y_overlay, color }>`
- Dibuja rectángulos con un "overlay" de color diferente (ej. dirección en Volume)
- Guard: verifica `is_finite()` en todos los valores antes de dibujar

---

## Rendering de overlays (sobre el canvas de velas)

Los overlay indicators (VWAP, Volume Profile) no tienen panel propio. En cambio exponen métodos que el canvas del kline chart consume directamente:

```rust
// Líneas (VWAP, AVWAP, etc.)
fn overlay_line_points(&self, earliest: u64, latest: u64) -> Vec<(u64, f32)>

// Niveles horizontales (POC, VAH, VAL, key levels)
fn overlay_levels(&self) -> Vec<(f32, [f32; 4])>   // (precio, [r,g,b,a])

// Bandas (σ bands del VWAP)
fn overlay_bands(&self, earliest, latest) -> Vec<Vec<(u64, f32, f32)>>  // Vec de bandas, cada banda = Vec<(t, upper, lower)>

// Histograma VRVP
fn overlay_volume_profile(&self) -> &[ProfileBar]
fn overlay_volume_profile_max(&self) -> f64
```

El histograma VRVP se dibuja en el 14% derecho del canvas:
- Barra de cada bin: ancho proporcional al volumen relativo al máximo del perfil
- Colores: dorado (POC), púrpura (HVN), azul tenue (LVN), azul normal

---

## Strategy overlay

**Función:** `draw_strategy_overlay()` en `src/chart/kline.rs`

Se llama como parte del frame de rendering cuando `strategy_overlay_enabled == true`.

Para cada `StrategySignal` en `strategy_signals`:

```
1. Convertir entry_price, stop_price, target_price → píxeles Y
2. Guard: if !entry_y.is_finite() || !stop_y.is_finite() || !target_y.is_finite() → continue
3. Zona semitransparente entre entry y target
4. Línea de entry: sólida, verde (long) o roja (short)
5. Línea de stop: punteada roja
6. Línea de target: punteada verde
7. Si TTL expiró → omitir de la lista (auto-expire)
```

Máximo 50 signals simultáneas. Las expiradas se filtran en `run_strategy_detection()`.

---

## Gestión de memoria y rendimiento

- Los datos históricos se conservan todos en `TimeSeries` (no hay purga automática)
- El rendering solo itera el rango visible: `visible_range = (earliest, latest)` en timestamps
- Los indicadores de overlay filtran sus datos al rango visible antes de calcular píxeles
- `update_visible_range()` en Volume Profile evita recalcular si el rango no cambió
- El Footprint solo renderiza las velas en viewport (iteración sobre rango visible)

Para charts con muchos datos históricos, el tiempo de redibujado es proporcional al número de velas visibles, no al total de datos almacenados.

---

## Fuente de verdad para el precio actual

El precio que se muestra en el label del eje Y del crosshair es el `close` de la última vela visible o el precio del último trade recibido, dependiendo de si hay trades activos en stream. El "latest price" se usa también para calcular el rango de precio del viewport cuando no hay datos suficientes.
