# Referencia: Data Crate

Crate: `data/` (`flowsurface-data`)

El crate `data` es la capa de estado compartida entre el frontend Iced y los subsistemas de chart. Contiene la persistencia de configuración, los tipos de datos de chart, el sistema de agregación de trades, y el modelo de layout (qué panes están abiertos y con qué configuración).

---

## Estructura

```
data/src/
  lib.rs              ← exports, data_path(), cleanup_old_market_data()
  config/             ← persistencia de preferencias del usuario
  layout/             ← modelo de layout: Dashboard, Pane (árbol de splits)
  chart/              ← tipos de datos de charts y agregación
  aggr/               ← agregación time-based y tick-based
  panel/              ← datos para Ladder y TimeAndSales
  stream.rs           ← tipos de stream persistidos
  tickers_table.rs    ← estado de la tabla de tickers
  institutional/      ← trackers institucionales (ver institutional.md)
  strategy/           ← sistema de detección (ver strategy.md)
```

---

## Persistencia (`data_path`)

```rust
pub fn data_path(path_name: Option<&str>) -> PathBuf
```

Directorio base de datos del usuario:
- Si `FLOWSURFACE_DATA_PATH` está definida → usa esa ruta
- Si no → `{dirs_next::data_dir()}/flowsurface` (ej: `%APPDATA%\flowsurface` en Windows)

`SAVED_STATE_PATH = "saved-state.json"` — archivo principal de estado.

**`write_json_to_file(json, file_name)`** — escribe a `data_path/file_name`, crea directorios si no existen.

**`read_from_file(file_name) → Result<State>`** — deserializa. Si el JSON está corrupto, renombra el archivo a `*_old.json` (backup automático) y devuelve error.

**`cleanup_old_market_data()`** — elimina archivos `.zip` de aggTrades de Binance que tengan más de 4 días. Paths: `market_data/binance/data/futures/um|cm/daily/aggTrades/`.

---

## Config (`data/src/config/`)

### `State`

```rust
struct State {
    layout_manager: Layouts,
    selected_theme: Theme,
    custom_theme: Option<Theme>,
    main_window: Option<WindowSpec>,
    timezone: UserTimezone,
    sidebar: Sidebar,
    scale_factor: ScaleFactor,
    audio_cfg: AudioStream,
    trade_fetch_enabled: bool,
    size_in_quote_ccy: exchange::SizeUnit,
    proxy_cfg: Option<exchange::proxy::Proxy>,
}
```

Es el struct raíz que se serializa a `saved-state.json`. Usa `#[serde(default)]` en todos los campos para tolerar versiones antiguas del archivo.

### `ScaleFactor`

Wrapper de `f32` para el factor de escala de la UI. Rango: `[MIN_SCALE=0.8, MAX_SCALE=1.5]`. Valor por defecto: `1.0`.

### `Layouts`

```rust
struct Layouts {
    layouts: Vec<Layout>,
    active_layout: Option<String>,
}
```

Lista de layouts guardados. Cada `Layout` tiene nombre y un `Dashboard`.

### `UserTimezone`

Zona horaria seleccionada por el usuario. Afecta el eje X de todos los charts.

### `Sidebar`

Configuración de la sidebar: qué tickers están fijados, orden, etc.

### `Theme`

Colores del tema. Puede ser uno de los temas predefinidos o un tema custom editado via `ThemeEditor`.

---

## Layout (`data/src/layout/`)

### `Dashboard`

```rust
struct Dashboard {
    pane: Pane,              // árbol de splits del layout activo
    popout: Vec<(Pane, WindowSpec)>,  // ventanas flotantes
}
```

### `Pane` — árbol de splits

```rust
enum Pane {
    Split { axis: Axis, ratio: f32, a: Box<Pane>, b: Box<Pane> },
    Starter { link_group: Option<LinkGroup> },
    HeatmapChart { layout, studies, stream_type, settings, indicators, link_group },
    ShaderHeatmap { studies, stream_type, settings, indicators, link_group },
    KlineChart { layout, kind, stream_type, settings, indicators, link_group },
    ComparisonChart { stream_type, settings, link_group },
    TimeAndSales { stream_type, settings, link_group },
    Ladder { stream_type, settings, link_group },
    StrategyMonitor { link_group },
}
```

`Pane` es un árbol recursivo: `Split` divide en dos sub-panes `a` y `b` con un ratio. Las hojas son los tipos de contenido.

**Tipos de pane:**

| Variante | Descripción |
|---------|-------------|
| `Starter` | Pane vacío (placeholder al abrir un layout nuevo) |
| `HeatmapChart` | Heatmap canvas (versión legacy sin WGPU) |
| `ShaderHeatmap` | Heatmap WGPU con shaders propios |
| `KlineChart` | Chart de velas + indicadores |
| `ComparisonChart` | Comparación multi-ticker |
| `TimeAndSales` | Tape de trades en tiempo real |
| `Ladder` | Order Ladder — orderbook L2 visual |
| `StrategyMonitor` | Pane de monitoreo del paper trading |

**`LinkGroup`** — permite sincronizar ticker/timeframe entre panes que comparten el mismo grupo.

**`Axis`** — `Horizontal` o `Vertical` — orientación del split.

### `WindowSpec`

Posición y tamaño de una ventana flotante (popout). Almacenada junto al `Pane` para restaurar la posición al cargar el layout.

---

## Stream persistence (`data/src/stream.rs`)

### `PersistStreamKind`

```rust
enum PersistStreamKind {
    Kline { ticker: Ticker, timeframe: Timeframe },
    Depth(PersistDepth),
    Trades { ticker: Ticker },
    DepthAndTrades(PersistDepth),  // deprecated — se convierte en Depth + Trades al cargar
}
```

Versión serializable de `exchange::adapter::StreamKind`. Los panes guardan `Vec<PersistStreamKind>` para restaurar sus subscripciones al cargar el layout.

### `PersistDepth`

```rust
struct PersistDepth {
    ticker: Ticker,
    depth_aggr: exchange::adapter::StreamTicksize,
    push_freq: PushFrequency,
}
```

`into_stream_kinds(resolver)` — convierte a `StreamKind` runtime usando una función que resuelve `Ticker → TickerInfo`. Si el ticker no existe en el exchange, devuelve error y el pane muestra estado `Loading` mientras re-fetcha el `TickerInfo`.

---

## Chart data (`data/src/chart/`)

### `Basis`

```rust
enum Basis {
    Time(exchange::Timeframe),  // una vela = N milisegundos
    Tick(aggr::TickCount),      // una vela = N trades
}
```

Define el eje X del chart. Todos los indicadores y el renderizador respetan el `Basis` activo.

### `PlotData<D>`

```rust
enum PlotData<D: DataPoint> {
    TimeBased(TimeSeries<D>),
    TickBased(TickAggr),
}
```

La fuente de datos de un chart. `TimeSeries` para charts time-based, `TickAggr` para tick charts. Implementa `visible_price_range`, `map_basis_series` y otros métodos que abstraen la diferencia entre los dos modos.

### `KlineDataPoint`

```rust
struct KlineDataPoint {
    kline: Kline,
    footprint: KlineTrades,
}
```

Un datapoint del kline chart: la vela (OHLCV) + el footprint (trades agrupados por nivel de precio). El footprint activa el modo **cluster/footprint chart** que muestra el volumen por precio dentro de la vela.

- `poc_price()` — precio del Point of Control dentro del footprint
- `set_poc_status(NPoc)` — marca el POC como naked (no re-visitado)
- `max_cluster_qty(ClusterKind, high, low)` — máximo volumen en un nivel para escalar la visualización

### `HeatmapDataPoint`

Datapoint del heatmap: trades agrupados por precio + buy/sell total del bucket de tiempo. Usa `Box<[GroupedTrade]>` (slice en heap) en lugar de `Vec` para minimizar allocations — el heatmap tiene miles de datapoints activos.

`CLEANUP_THRESHOLD = 4800` — cuando el heatmap acumula más de este número de datapoints, limpia los más viejos.

### `KlineIndicator` / `HeatmapIndicator`

```rust
enum KlineIndicator {
    Volume, CumulativeDelta, OpenInterest, OiDelta,
    Vwap, VolumeProfile, Atr, RelativeVolume,
}

enum HeatmapIndicator { Volume }
```

Trait `Indicator`:
- `for_market(MarketKind) → &'static [Self]` — lista de indicadores disponibles para el tipo de mercado

`KlineIndicator::FOR_SPOT` — 6 indicadores (sin OI ni OiDelta, que son solo perps)  
`KlineIndicator::FOR_PERPS` — 8 indicadores (todos)

---

## Agregación (`data/src/aggr/`)

### `TimeSeries<D>` (`aggr/time.rs`)

```rust
struct TimeSeries<D: DataPoint> {
    pub datapoints: BTreeMap<UnixMs, D>,
    pub interval: Timeframe,
    pub tick_size: PriceStep,
}
```

Serie temporal indexada por timestamp. `BTreeMap` permite rangos eficientes para el renderizador (solo pinta los datapoints visibles en la ventana actual).

Métodos clave:
- `price_scale(lookback)` → `(high, low)` — rango de precios de las últimas N velas
- `ingest_trades_bucket(rounded_t, trades, step)` — agrega trades al bucket correcto
- `check_kline_integrity(earliest, latest)` → lista de timestamps faltantes — detecta gaps en klines históricas
- `align_down_to_phase(time, phase, interval)` — alinea un timestamp al inicio del bucket

### `DataPoint` trait

```rust
trait DataPoint {
    fn add_trade(&mut self, trade: &Trade, step: PriceStep);
    fn clear_trades(&mut self);
    fn last_trade_time(&self) -> Option<UnixMs>;
    fn first_trade_time(&self) -> Option<UnixMs>;
    fn last_price(&self) -> Price;
    fn kline(&self) -> Option<&Kline>;
    fn value_high(&self) -> Price;
    fn value_low(&self) -> Price;
}
```

Implementado por `KlineDataPoint` y `HeatmapDataPoint`.

### `TickAggr` (`aggr/ticks.rs`)

Agrega trades por número de trades (tick charts). Cada bucket se cierra cuando acumula `TickCount` trades. `TickAccumulation` es el bucket en construcción:

```rust
struct TickAccumulation {
    tick_count: usize,
    kline: Kline,
    footprint: KlineTrades,
}
```

`is_full(interval)` → `tick_count >= interval.0` — cierra el bucket y crea uno nuevo.

---

## Panel data (`data/src/panel/`)

### Ladder (`panel/ladder.rs`)

Datos del Order Ladder — orderbook L2 visual con trades superpuestos.

**Constantes:**
```
TRADE_RETENTION_MS = 8 * 60_000  // trades visibles por 8 minutos
CHASE_MIN_VISIBLE_OPACITY = 0.15 // opacidad mínima del chase tracker
```

**`Config`:**
```rust
struct Config {
    show_spread: bool,
    show_chase_tracker: bool,  // tracker del precio de entrada
    trade_retention: Duration, // default 8 min
}
```

El Ladder mantiene una `BTreeMap<Price, [Qty; 2]>` (bid/ask por precio) actualizada en tiempo real con cada evento `Depth`.

### TimeAndSales (`panel/timeandsales.rs`)

Tape de trades. Mantiene una `VecDeque<Trade>` con las últimas N transacciones, con filtros de tamaño mínimo configurables por el usuario.

---

## Exports públicos de `data`

```rust
pub use audio::AudioStream;
pub use config::ScaleFactor;
pub use config::sidebar::{self, Sidebar};
pub use config::state::{Layouts, State};
pub use config::theme::Theme;
pub use config::timezone::UserTimezone;
pub use layout::{Dashboard, Layout, Pane};
```

Estos tipos son usados directamente por el frontend (`src/`) sin importar submódulos.
