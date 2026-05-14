# Tipos de Chart

## 1. Kline Chart (Candlestick / Footprint)

**Archivo:** `src/chart/kline.rs`

El chart más complejo del proyecto. Soporta dos modos (`KlineChartKind`):
- `Candles` — velas japonesas tradicionales
- `Footprint { clusters, scaling, studies }` — velas con distribución de volumen por nivel de precio

**Ver `docs/RENDERING.md`** para el sistema de coordenadas, cache, scroll/zoom e indicadores.

**Indicadores disponibles:** ver `docs/INDICATORS.md`.

**Strategy overlay:** ver `docs/STRATEGY.md`.

---

## 2. Heatmap Chart (Historical DOM)

**Archivo:** `src/chart/heatmap.rs`

Visualiza el orderbook L2 como una serie temporal: el eje X es tiempo, el eje Y es precio, y la intensidad del color representa el tamaño de la orden en cada nivel.

### Struct principal

```rust
HeatmapChart {
    chart: ViewState,
    trades: TimeSeries<HeatmapDataPoint>,
    indicators: EnumMap<HeatmapIndicator, Option<IndicatorData>>,
    pause_buffer: Vec<(UnixMs, Box<[Trade]>, Depth)>,  // buffer durante zoom/scroll
    heatmap: HistoricalDepth,              // Estado del depth histórico
    visual_config: Config,                 // QtyScale, ProfileKind
    study_configurator: study::Configurator<HeatmapStudy>,
}
```

### HistoricalDepth

**Archivo:** `data/src/chart/heatmap.rs`

Mantiene una ventana de ~300 snapshots de depth. En cada update de WebSocket:
1. Aplica el delta de depth (price levels añadidos/eliminados/modificados)
2. Guarda el snapshot completo para ese timestamp
3. Renderiza como histograma: columnas de tiempo × bins de precio

### Colores de rendering

- Bids (órdenes de compra): rojo, intensidad proporcional a qty
- Asks (órdenes de venta): verde, intensidad proporcional a qty
- Midprice (punto medio bid/ask): línea amarilla

### ProfileKind (selector de visualización)

```rust
ProfileKind {
    TimeProfile,    // Tiempo que el precio pasó en cada nivel
    VolumeProfile,  // Volumen acumulado por nivel (trades ejecutados)
    CVD,            // Buy-sell divergence coloreado por nivel
}
```

### Indicadores del Heatmap

`HeatmapIndicator` — disponibles como paneles debajo del chart:
- `Volume` — barras de volumen por candle del heatmap
- `CVD` — cumulative delta sobre el mismo eje temporal

### Studies del Heatmap

Estudios configurables para el heatmap (naked POC, value area, etc.).

---

## 3. ShaderHeatmap (GPU Heatmap)

**Archivo:** `src/widget/chart/heatmap/`

Versión del Heatmap renderizada en la GPU con wgpu (WebGPU). Más eficiente para datasets grandes.

### Arquitectura

```
HeatmapShader
├── widget.rs          — iced widget, maneja eventos, pasa a scene
└── scene/
    ├── mod.rs         — Scene struct (state del renderer)
    ├── camera.rs      — Cámara 3D (pan, zoom)
    ├── cell.rs        — Celda del grid (color, posición)
    ├── grid.rs        — Grid de celdas
    └── pipeline.rs    — Pipelines wgpu (circle glyphs, rectangle glyphs)
```

La escena usa coordenadas 3D con cámara configurable, permitiendo pan/zoom más fluido que el canvas 2D. Las celdas del grid se pasan como instancias al shader GPU.

### Cuándo usarlo vs Heatmap normal

- `ShaderHeatmap`: Para timeframes cortos (M1/M5) con mucha actividad de orderbook donde el heatmap 2D puede degradar el framerate.
- `Heatmap`: Para timeframes mayores o cuando se necesitan los indicadores de panel (Volume, CVD).

---

## 4. Footprint Chart

El Footprint es un modo del `KlineChart` (`KlineChartKind::Footprint`), no un chart separado.

### Qué muestra

Cada vela se expande para mostrar la distribución de trades ejecutados por nivel de precio:
- Columna izquierda (ask side): volumen de sellers en ese nivel
- Columna derecha (bid side): volumen de buyers en ese nivel
- Imbalance: si una columna supera N× a la otra (configurable), se resalta

### Structs

```rust
KlineTrades {
    trades: FxHashMap<Price, GroupedTrades {
        buy_qty: Qty,
        sell_qty: Qty,
    }>,
    poc: Option<PointOfControl>,    // Nivel con mayor volumen dentro de la vela
}
```

### Imbalance

Ratio configurable (por defecto ~3:1). Si `buy_qty / sell_qty > ratio` → imbalance bid (verde). Si `sell_qty / buy_qty > ratio` → imbalance ask (rojo). Los imbalances se resaltan visualmente.

### ClusterKind

```rust
ClusterKind {
    BidAsk,         // Muestra buy_qty vs sell_qty por nivel
    DeltaProfile,   // Muestra delta = buy - sell por nivel (coloreado)
    VolumeProfile,  // Muestra volumen total por nivel (sin split)
}
```

### Naked POC

El POC (Point of Control) de una vela que no ha sido "testeado" por el precio en velas posteriores. Se muestra como línea horizontal que persiste hasta que el precio lo cruza.

### Datos requeridos

El Footprint requiere trades con split buy/sell. Si el exchange no provee datos direccionales (ej. MEXC en perps), el footprint aparece vacío. Binance y Bybit en LinearPerps sí proveen dirección.

---

## 5. DOM / Ladder

**Archivo:** `src/screen/dashboard/panel/ladder.rs`

Muestra el orderbook L2 en tiempo real como una "escalera" de precios con sus cantidades.

### Struct

```rust
Ladder {
    ticker_info: TickerInfo,
    config: Config,
    cache: canvas::Cache,
    orderbook: [GroupedDepth; 2],      // [bids, asks]
    trades: TradeStore,                 // Últimas trades ejecutadas por nivel de precio
    chase_tracker: [ChaseTracker; 2],   // Detecta cuando el best bid/ask "persigue" al precio
    scroll_px: f32,
}
```

### Layout de columnas

```
| Sell Orders | Sell Trades | Price Level | Buy Trades | Buy Orders |
```

- **Sell/Buy Orders**: cantidad de órdenes límite pendientes en ese precio (del orderbook)
- **Sell/Buy Trades**: volumen ejecutado en ese precio (de los trades recibidos)
- **Price Level**: precio, resaltado si es el best bid/ask

### GroupedDepth

Agrega múltiples price levels en un solo nivel (tick multiplier configurable). Por ejemplo, con `tick_multiply = 5` y ticksize `0.01`, cada fila del ladder representa un rango de `0.05`.

### ChaseTracker

Detecta cuando el best bid o best ask se mueve agresivamente en una dirección ("persigue" el precio). Se usa para alertas visuales de momentum.

### Actualización

El Ladder recibe depth updates vía `dashboard.ingest_depth()` → `ladder.update_depth(depth)`. Los trades llegan vía `ingest_trades()` → `ladder.update_trades(trades)`.

---

## 6. Time & Sales

**Archivo:** `src/screen/dashboard/panel/timeandsales.rs`

Feed de trades ejecutados en tiempo real, ordenados por tiempo descendente.

### Columnas

| Columna | Descripción |
|---------|-------------|
| Precio | Precio de ejecución |
| Qty | Cantidad del trade |
| Dirección | Compra (verde) / Venta (rojo) |
| Tiempo | Timestamp (HH:MM:SS) |

### Comportamiento

- Nuevos trades aparecen arriba
- Scrollable manualmente
- `reset_scroll()` vuelve al top (más reciente) cuando llegan nuevos trades si el usuario no ha hecho scroll
- Límite configurable de trades almacenados en memoria

### Filtro de tamaño

Algunos exchanges permiten filtrar trades por tamaño mínimo. Trades pequeños pueden suprimirse para reducir el ruido.

---

## 7. Comparison Chart

**Archivo:** `src/chart/comparison.rs`

Gráfica de líneas multi-activo normalizada para comparar el rendimiento relativo de varios tickers.

### Struct

```rust
ComparisonChart {
    series: Vec<Series {
        points: Vec<(u64, f32)>,    // (timestamp, normalized_price)
        color: Color,
        name: String,               // Ticker display name
    }>,
    timeframe: Timeframe,
    zoom: Zoom,
    pan: f32,
    request_handler: FxHashMap<TickerInfo, RequestHandler>,
    series_editor: TickerSeriesEditor,    // UI para añadir/quitar series
}
```

### Normalización

Cada serie se normaliza al 100% en el primer punto visible. Esto permite comparar el rendimiento relativo sin importar el precio absoluto de cada activo.

```
normalized_price = (current_price / first_price) * 100
```

### Límites

- Máximo ~5000 puntos por serie
- Máximo 5 series simultáneas (recomendado)

### Interacción

- `TickerSeriesEditor`: modal para buscar y añadir nuevos tickers a la comparación
- Cada serie tiene su color asignado automáticamente o configurable
- Zoom/pan con rueda del ratón y arrastre

---

## 8. Pane Starter

Estado inicial de un pane vacío. Muestra un selector de tipo de chart con descripción de cada opción. El usuario elige y el pane se inicializa con el chart correspondiente.

---

## Resumen de datos requeridos por chart

| Chart | WebSocket Feeds | HTTP Fetch | Restricciones |
|-------|----------------|------------|---------------|
| Kline (Candles) | Klines + Trades (opcional) | Klines históricos | — |
| Kline (Footprint) | Klines + Trades | Klines + Trades históricos | Requiere trades con dirección |
| Heatmap | Depth + Trades | — | — |
| ShaderHeatmap | Depth + Trades | — | — |
| Ladder | Depth + Trades | — | — |
| Time & Sales | Trades | — | — |
| Comparison | Klines | Klines históricos (multi-ticker) | — |
