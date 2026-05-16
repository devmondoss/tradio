# Referencia: Exchange Crate

Crate: `exchange/` (`flowsurface-exchange`)

El crate exchange es la capa de abstracción sobre las APIs de los exchanges. Provee tipos de datos unificados (Kline, Trade, Depth, TickerInfo) y adaptadores por exchange que normalizan las diferencias entre protocolos WebSocket y REST.

---

## Tipos principales (`exchange/src/lib.rs`)

### `Timeframe`

Enum con todos los timeframes soportados:

| Variante | Valor | Uso |
|---------|-------|-----|
| `MS100`–`MS1000` | 100ms–1000ms | Solo Heatmap (orderbook snapshot freq) |
| `M1`–`D1` | 1m–1d | Klines y Open Interest |

`Timeframe::KLINE` = [M1, M3, M5, M15, M30, H1, H2, H4, H12, D1]  
`Timeframe::HEATMAP` = [MS100, MS200, MS300, MS500, MS1000]

### `Ticker`

Identificador compacto de un instrumento. Almacenado como bytes fijos (max 28 chars ASCII) — sin heap allocation.

```rust
struct Ticker {
    bytes: [u8; 28],        // símbolo interno (ej: "BTCUSDT")
    exchange: Exchange,
    display_bytes: [u8; 28], // símbolo de display (ej: "HYPEUSDC" para Hyperliquid spot)
    has_display_symbol: bool,
}
```

`to_full_symbol_and_type()` → `(String, MarketKind)` — símbolo interno + tipo de mercado  
`display_symbol_and_type()` → versión formateada para UI (añade "USDT" a Hyperliquid Linear)

Serialización: `"BinanceLinear:BTCUSDT"` o `"HyperliquidLinear:@107|HYPEUSDC"` (con display symbol).

### `SerTicker`

Wrapper serializable de `(Exchange, Ticker)` para usar como clave en maps: `"BinanceLinear:BTCUSDT"`.

### `TickerInfo`

Info estática de un instrumento:
```rust
struct TickerInfo {
    ticker: Ticker,
    min_ticksize: MinTicksize,  // tamaño mínimo de tick de precio
    min_qty: MinQtySize,        // tamaño mínimo de orden
    contract_size: Option<ContractSize>,  // solo para contratos inversos
}
```

### `Kline`

```rust
struct Kline {
    time: UnixMs,
    open: Price, high: Price, low: Price, close: Price,
    volume: Volume,
    is_closed: bool,  // false = parcial (intrabar), true = cerrada
}
```

### `Volume`

```rust
enum Volume {
    TotalOnly(Qty),        // exchanges sin separación buy/sell
    BuySell(Qty, Qty),     // buy_vol, sell_vol — Binance, Bybit, etc.
}
```

`add_trade_qty(is_sell, qty)` — acumula trades en tiempo real.

### `Trade`

```rust
struct Trade {
    time: UnixMs,
    is_sell: bool,
    price: Price,
    qty: Qty,
}
```

### `TickerStats`

Stats de 24h: `mark_price`, `daily_price_chg` (%), `daily_volume` (USD).

### `OpenInterest`

```rust
struct OpenInterest { time: UnixMs, value: f32 }
```

### `TickMultiplier`

Agrupador de ticks para el orderbook: 1x, 2x, 5x, 10x, 25x, 50x, 100x, 200x, 500x.  
`multiply_step(base_step)` → step agrupado  
`unscale_step(scaled_step)` → paso original (para redondeo exacto)

### `PushFrequency`

Frecuencia de push del orderbook: `ServerDefault` o `Custom(Timeframe)`.

---

## Tipos de unidad (`exchange/src/unit/`)

### `Price` / `PriceStep`

Almacenados como enteros atómicos (i64) — sin punto flotante en la lógica de precio.  
`Price::from_f32(v)`, `Price::to_f64()`, `round_to_min_tick(min_ticksize)`

### `Qty`

Cantidad de contratos/monedas. `Qty::ZERO`, operadores aritméticos implementados.  
`SizeUnit` → `Base` (BTC) o `Quote` (USDT) — seleccionable en UI.

### `UnixMs`

Timestamp en milisegundos. `UnixMsRangeError` si el valor está fuera de rango.

---

## Exchanges soportados (`exchange/src/adapter/hub/`)

| Exchange | Mercados | Streams WebSocket | REST |
|----------|----------|-------------------|------|
| **Binance** | Spot, LinearPerps, InversePerps | klines, aggTrade, depth diff+snapshot | klines, OI, trades, depth, tickerStats |
| **Bybit** | LinearPerps, InversePerps | klines, trade, orderbook | klines, OI, trades, depth, tickerStats |
| **Hyperliquid** | LinearPerps (L2 perpetuals) | klines, trade, L2Book | klines, OI, trades, tickerStats |
| **MEXC** | Spot, LinearPerps | klines, trade, depth | klines, trades |
| **OKEx** | Spot, LinearPerps, InversePerps | candles, trade, books | klines, OI, trades, depth |

### Dominios WebSocket por exchange y mercado

**Binance**:
- Spot: `stream.binance.com`
- LinearPerps: `fstream.binance.com`
- InversePerps: `dstream.binance.com`

**Binance LinearPerps** usa dos rutas:
- `public/stream` — datos públicos generales
- `market/stream` — datos de mercado más frecuentes (trades, depth)

---

## Arquitectura del adaptador (`exchange/src/adapter/`)

### `FetchCommand<M>`

Enum de comandos que el hub despacha via `oneshot::channel`:

```
TickerMetadata { market_scope, reply }
TickerStats { market_scope, reply }
Klines { ticker, timeframe, range, reply }
OpenInterest { ticker, timeframe, range, reply }
DepthSnapshot { ticker, reply }
Trades { ticker, from_time, data_path, reply }
```

### `HttpHub<L>` (`adapter/hub.rs`)

Actor que recibe `FetchCommand` via `mpsc::channel` y los ejecuta con rate limiting.

### `RateLimiter` (`adapter/limiter.rs`)

Controla el ritmo de requests REST para no exceder los límites de cada exchange.

### `AdapterHandles`

Conjunto de handles a los adaptadores activos. El frontend mantiene uno por exchange conectado.

### Depth sync — Binance (`DepthSyncState`)

El orderbook de Binance se reconstruye con el patrón diff+snapshot:

```
WaitingSnapshot(rx)  ← bufferea diffs, espera snapshot REST
Synced { last_update_id }  ← aplica diffs incrementalmente
```

`MAX_PENDING_DEPTH_EVENTS = 512` — buffer de diffs mientras espera snapshot.

`ApplyDepthResult`:
- `Applied(update_id)` — diff aplicado correctamente
- `Skipped` — diff ya procesado (update_id viejo)
- `NeedsResync(reason)` — gap en los IDs → solicitar nuevo snapshot

### `StreamKind`

Tipos de stream que un pane puede suscribir:
- `Kline(Timeframe)`
- `Trades` (aggTrade/trade tick)
- `Depth(PushFrequency)` — orderbook L2
- `OpenInterest(Timeframe)`

### `MarketKind`

```rust
enum MarketKind { Spot, LinearPerps, InversePerps }
```

### `Event`

Eventos que fluyen del exchange al frontend:

```rust
enum Event {
    Kline(TickerInfo, Kline),
    Trades(TickerInfo, Vec<Trade>),
    Depth(TickerInfo, Depth),
    OpenInterest(TickerInfo, OpenInterest),
    TickerStats(TickerInfo, TickerStats),
    // ...
}
```

---

## Linker stub (`nanosleep64`)

```rust
#[cfg(all(windows, target_arch = "x86_64"))]
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nanosleep64(...) -> i32 { 0 }
```

`aws-lc-sys` (compilado con WinLibs POSIX.UCRT) referencia `nanosleep64` que no existe en el MinGW bundled de Rust. Este stub satisface el linker; nunca se llama en runtime.
