# Flujo de Datos: Exchange → Chart

## Tipos core del exchange

**Archivo:** `exchange/src/lib.rs`

```rust
Ticker {
    bytes: [u8; 28],          // Símbolo ("BTCUSDT"), zero-padded
    exchange: Exchange,
    display_bytes: [u8; 28],  // Display alternativo (Hyperliquid spot)
}

TickerInfo {
    ticker: Ticker,
    min_ticksize: MinTicksize,         // Precisión de precio (e.g. 0.01)
    min_qty: MinQtySize,               // Cantidad mínima tradeable
    contract_size: Option<ContractSize>, // Para perps inversos (e.g. 100 USD/contrato)
}

Trade {
    time: UnixMs,
    is_sell: bool,
    price: Price,   // Wrapper sobre i64 en atomic units (×10^-8)
    qty: Qty,
}

Kline {
    time: UnixMs,
    open: Price, high: Price, low: Price, close: Price,
    volume: Volume {
        TotalOnly(Qty),        // Sin split direccional
        BuySell(Qty, Qty),     // (buy_qty, sell_qty)
    }
}

OpenInterest {
    time: UnixMs,
    value: f32,
}
```

**Depth (`exchange/src/depth.rs`):**
```rust
Depth {
    bids: BTreeMap<Price, Qty>,   // Ordenado descendente
    asks: BTreeMap<Price, Qty>,   // Ordenado ascendente
}
```

---

## Units (atomic integers)

`Price` y `Qty` son newtypes sobre `i64` con "atomic units":
- 1 atomic unit = 10^-8 de la unidad real
- Evitan errores de punto flotante en comparaciones y sumas
- Conversión: `price.to_f64() = price.0 as f64 * 1e-8`

`PriceStep` determina la precisión visible (decimal places) para cada ticker.

---

## WebSocket → App (flujo de eventos)

```
Exchange WebSocket
    → exchange adapter (Binance/Bybit/etc.)
        → parsea JSON → convierte a tipos internos
        → envía por channel tokio mpsc
    → AdapterHandles.kline_stream() / trade_stream() / depth_stream()
        → retorna BoxStream<'static, Event>
    → Flowsurface::subscription() registra el stream
    → iced despacha Event como Message::MarketWsEvent(event)
```

**Event enum (`exchange/src/adapter.rs`):**
```rust
Event {
    Connected(Exchange),
    Disconnected(Exchange, String),
    KlineReceived(StreamKind, Kline),
    DepthReceived(StreamKind, UnixMs, Depth),
    TradesReceived(StreamKind, UnixMs, Box<[Trade]>),
}
```

**StreamKind** identifica qué suscripción produjo el evento:
```rust
StreamKind {
    Kline { ticker_info, timeframe },
    Depth { ticker_info, depth_aggr, push_freq },
    Trades { ticker_info },
}
```

---

## Adapters por exchange

Cada exchange tiene su handler en `exchange/src/adapter/hub/`:
- Mantiene una tarea tokio con conexión WebSocket
- Reconexión automática en desconexiones
- Limitación de rate en fetches HTTP
- Parseo de formato específico del exchange → tipos internos

**AdapterHandles (`exchange/src/adapter/client.rs`):**
```rust
AdapterHandles::spawn_all(config)
    → crea un handle por exchange
    → cada handle tiene kline_stream(), trade_stream(), depth_stream()
    → retornan BoxStream clonables
```

---

## HTTP Fetch de datos históricos

**RequestHandler (`src/connector/fetcher.rs`):**

Evita requests duplicadas y repetidas:

```rust
RequestHandler {
    requests: FxHashMap<Uuid, FetchRequest {
        fetch_type: FetchRange,
        status: Pending | Completed(timestamp) | Failed(String),
    }>,
}
```

**Flujo de un fetch:**
1. Chart detecta que el viewport tiene espacio sin datos
2. `request_fetch(FetchRange::Kline(start_ms, end_ms))`
3. RequestHandler verifica:
   - Mismo rango ya Pending → `Err(Overlaps)` (se ignora)
   - Completado hace <30s → `Err(Failed)` (cooldown)
   - Completado hace >30s → retry permitido
4. `kline_fetch_task()` → Task tokio → adapter HTTP request
5. `FetchUpdate::Data(klines)` → Dashboard distribuye al pane correcto
6. `chart.insert_hist_klines()` → inserta en TimeSeries
7. `RequestHandler.mark_completed(req_id)`

**FetchRange enum:**
```rust
FetchRange {
    Kline(UnixMs, UnixMs),
    OpenInterest(UnixMs, UnixMs),
    Trades(UnixMs, UnixMs),
}
```

**FetchUpdate enum (resultado de una task de fetch):**
```rust
FetchUpdate {
    Data { pane_id, klines: Vec<Kline> },
    Error { pane_id, req_id: Option<Uuid>, error: String },
}
```

Los errores con `"rejected"` o `"overlaps"` se suprimen como toast (solo log DEBUG). Los errores reales muestran notificación.

---

## Agregación de datos

### PlotData (contenedor principal)

**Archivo:** `data/src/chart.rs`

```rust
PlotData<D: DataPoint> {
    TimeBased(TimeSeries<D>),    // Charts basados en tiempo
    TickBased(TickAggr),         // Charts basados en número de trades
}
```

### TimeSeries

**Archivo:** `data/src/aggr/time.rs`

```rust
TimeSeries<D> {
    interval: Timeframe,           // M1, M3, M5, M15, M30, H1, H2, H4, H8, H12, D1, W1
    datapoints: BTreeMap<UnixMs, D>,
}
```

Métodos clave:
- `latest_timestamp()` → última vela
- `price_scale(num_candles)` → rango de precios visible
- `min_max_price_in_range(start, end)` → bounding box

### TickAggr

**Archivo:** `data/src/aggr/ticks.rs`

```rust
TickAggr {
    tick_count: TickCount,
    datapoints: Vec<(KlineDataPoint, u64)>,  // (datos, índice)
}
```

Agrupa N trades en una vela. Se usa en charts de footprint con basis de tick.

### KlineDataPoint

**Archivo:** `data/src/chart/kline.rs`

```rust
KlineDataPoint {
    kline: Kline,              // OHLCV básico
    footprint: KlineTrades,    // HashMap<Price, GroupedTrades> para el footprint
}
```

**KlineTrades:**
```rust
KlineTrades {
    trades: FxHashMap<Price, GroupedTrades {
        buy_qty: Qty,
        sell_qty: Qty,
    }>,
    poc: Option<PointOfControl>,   // Precio con mayor volumen en el footprint
}
```

### BasisSeries

Proyección ligera para indicadores. Solo almacena el valor calculado, no el OHLCV completo:

```rust
BasisSeries<T> {
    Time(BTreeMap<UnixMs, T>),    // Clave = timestamp
    Tick(BTreeMap<u64, T>),       // Clave = índice de tick
}
```

Usado por todos los indicadores (VWAP, ATR, CVD, etc.) para guardar sus series calculadas.

---

## Flujo end-to-end completo: usuario abre BTCUSDT kline

```
1. Sidebar.on_ticker_selected()
   → Message::Dashboard { event: Pane::SetContent(Candles) }

2. Dashboard.update()
   → pane::State::set_content_and_streams()
   → Content::new_kline(ticker_info, basis=Time(M15), ...)
   → generate streams: [Trades{...}, Kline{timeframe: M15}]

3. AdapterHandles
   → Binance WS subscribe /btcusdt@kline_15m + /btcusdt@aggTrade

4. WS recibe Kline
   → Event::KlineReceived(stream_kind, kline)
   → Flowsurface::update → Message::MarketWsEvent
   → Dashboard.update_latest_klines()
   → chart.insert_latest_kline(kline)
   → TimeSeries.insert() → cache.clear()

5. Frame loop (Tick)
   → chart.view() → canvas draw()
   → renderiza velas, indicadores, strategy overlay

6. Usuario hace scroll (viewport se extiende)
   → KlineChart detecta gap de datos
   → request_fetch(FetchRange::Kline(t0, t1))
   → kline_fetch_task() → HTTP GET binance/klines
   → FetchUpdate::Data → insert_hist_klines()
   → cache.clear() → redraw

7. Al cerrar
   → save_state_to_disk()
   → JSON con layout completo, streams, settings, ventanas
```

---

## Timeframes disponibles por exchange

No todos los exchanges tienen todos los timeframes. Cada `TickerInfo` tiene asociados los timeframes soportados. El UI filtra el dropdown según disponibilidad.

Timeframes estándar: M1, M3, M5, M15, M30, H1, H2, H4, H8, H12, D1, W1

OI (Open Interest) solo disponible en perps, y solo en ciertos timeframes (M5–H4, excluye M1, M3, H2).
