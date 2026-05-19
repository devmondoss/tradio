# FlowSurface — Ingesta de Datos

> Referencia completa de todas las fuentes de datos externas: WebSockets, REST APIs, contratos de datos, structs de configuración Rust y esquema de escritura a Supabase.
> Última actualización: 2026-05-19 (rev 2 — sección 13: raw vs derivado con fórmulas)

---

## Tabla de Contenidos

1. [Visión General](#1-visión-general)
2. [Variables de Entorno](#2-variables-de-entorno)
3. [WebSockets — Tiempo Real](#3-websockets--tiempo-real)
4. [REST APIs — Datos de Mercado](#4-rest-apis--datos-de-mercado)
5. [REST APIs — Datos Institucionales](#5-rest-apis--datos-institucionales)
6. [REST APIs — Histórico (Startup)](#6-rest-apis--histórico-startup)
13. [Raw vs Derivado — Fórmulas completas](#13-raw-vs-derivado--fórmulas-completas)
7. [Supabase — Config y Escritura](#7-supabase--config-y-escritura)
8. [Adaptadores disponibles vs activos](#8-adaptadores-disponibles-vs-activos)
9. [Frecuencias y Frescura](#9-frecuencias-y-frescura)
10. [Structs internos — Contratos de datos en Rust](#10-structs-internos--contratos-de-datos-en-rust)
11. [StrategyConfig — Valores por defecto y calibración](#11-strategyconfig--valores-por-defecto-y-calibración)
12. [ConfigLoader — Carga desde Supabase](#12-configloader--carga-desde-supabase)

---

## 1. Visión General

```
┌─────────────────────────────────────────────────────────────────┐
│                        FUENTES EXTERNAS                         │
│                                                                 │
│  Binance Futures WebSocket                                      │
│  ├── @kline_5m        → barras OHLCV + taker volume            │
│  ├── @depth@100ms     → libro de órdenes L2 incremental        │
│  ├── @aggTrade        → trades individuales (footprint/CVD)    │
│  └── @forceOrder      → liquidaciones en tiempo real           │
│                                                                 │
│  Binance Futures REST (polling)                                 │
│  ├── /premiumIndex    → funding rate actual        (60s)        │
│  ├── /openInterest    → OI actual                  (5m)         │
│  ├── /topLongShort... → ratio top traders          (5m)         │
│  ├── /globalLongShort → ratio retail               (5m)         │
│  └── /takerlongshort  → ratio taker buy/sell       (5m)         │
│                                                                 │
│  Binance Spot REST                                              │
│  └── /ticker/price    → precio spot                (30s)        │
│                                                                 │
│  Supabase REST                                                  │
│  ├── deployed_params  → config de estrategia (READ, 300s)      │
│  ├── shadow_signals   → señales Core         (WRITE)           │
│  ├── signal_outcomes  → outcomes Core        (WRITE)           │
│  ├── regime_history   → cambios de regime    (WRITE)           │
│  ├── lab_signals      → señales Lab          (WRITE)           │
│  └── lab_outcomes     → outcomes Lab         (WRITE)           │
└─────────────────────────────────────────────────────────────────┘
```

**Exchange activo**: Binance LinearPerps (`BTCUSDT` por defecto).
**Exchanges con adaptador pero sin activar**: Bybit, Hyperliquid, MEXC, OKEx.
**Autenticación Binance**: Todos los endpoints usados son públicos (sin API key).

---

## 2. Variables de Entorno

| Variable | Defecto | Descripción | Leída en |
|----------|---------|-------------|----------|
| `SYMBOL` | `BTCUSDT` | Par de trading | `main.rs` |
| `TIMEFRAME_MIN` | `5` | Intervalo de barras en minutos (1/3/5/15/30/60) | `main.rs` |
| `SUPABASE_URL` | — | URL del proyecto Supabase (e.g. `https://xyz.supabase.co`) | `config_loader.rs`, `supabase_writer.rs` |
| `SUPABASE_KEY` | — | API key (anon o service_role) | `config_loader.rs`, `supabase_writer.rs` |
| `LAB_ENABLED` | `false` | Activa el Strategy Lab | `data/src/strategy/lab/types.rs` |
| `RUST_BACKTRACE` | `1` | Backtrace en panics (Railway) | `run.bat` |

Si `SUPABASE_URL` o `SUPABASE_KEY` no están definidas, el sistema arranca en modo local silencioso: usa `StrategyConfig::default()` y no escribe señales.

---

## 3. WebSockets — Tiempo Real

### 3.1 Kline Stream

| Campo | Valor |
|-------|-------|
| **URL** | `wss://fstream.binance.com/market/stream?streams={symbol}@kline_{interval}` |
| **Ejemplo** | `wss://fstream.binance.com/market/stream?streams=btcusdt@kline_5m` |
| **Protocolo** | WebSocket + TLS (fastwebsockets) |
| **Autenticación** | Pública |
| **Archivo** | `exchange/src/adapter/hub/binance/stream.rs` |

**Contrato JSON (campo `k`):**

```json
{
  "t": 1234567890000,
  "o": "67000.00",
  "h": "67500.00",
  "l": "66800.00",
  "c": "67200.00",
  "v": "123.456",
  "V": "80.123",
  "i": "5m",
  "x": true
}
```

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `t` | i64 | open_time (ms) |
| `o` | f64 str | open |
| `h` | f64 str | high |
| `l` | f64 str | low |
| `c` | f64 str | close |
| `v` | f64 str | volume total |
| `V` | f64 str | taker_buy_base_asset_volume |
| `i` | string | interval ("5m") |
| `x` | bool | is_closed — `true` = barra finalizada |

**Procesamiento**: Solo se procesa cuando `x = true`. `V` calcula `taker_imbalance = (V - (v-V)) / v`. La barra cerrada dispara todo el pipeline de estrategias.

---

### 3.2 Order Book Stream (Depth Incremental)

| Campo | Valor |
|-------|-------|
| **URL** | `wss://fstream.binance.com/public/stream?streams={symbol}@depth@100ms` |
| **Ejemplo** | `wss://fstream.binance.com/public/stream?streams=btcusdt@depth@100ms` |
| **Frecuencia push** | 100ms |
| **Autenticación** | Pública |
| **Archivo** | `exchange/src/adapter/hub/binance/stream.rs` |

**Contrato JSON (perps):**

```json
{
  "T": 1234567890123,
  "U": 1000,
  "u": 1050,
  "pu": 999,
  "b": [["67000.00", "5.500"], ["66990.00", "2.100"]],
  "a": [["67010.00", "3.200"], ["67020.00", "1.800"]]
}
```

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `T` | i64 | timestamp ms |
| `U` | i64 | first_update_id |
| `u` | i64 | final_update_id |
| `pu` | i64 | prev_final_update_id (para validar secuencia) |
| `b` | `[[str,str]]` | bids: [precio, qty] |
| `a` | `[[str,str]]` | asks: [precio, qty] |

**Procesamiento**: El sistema mantiene un L2 local. Cuando hay gap de secuencia (`U != pu+1`) se hace re-sync con REST `/depth` (snapshot). Calcula `obi_l5/l10/l20`, `microprice`, `spread_bps`, `walls_above/below`, `thin_zone_above/below`.

---

### 3.3 AggTrade Stream

| Campo | Valor |
|-------|-------|
| **URL** | `wss://fstream.binance.com/market/stream?streams={symbol}@aggTrade` |
| **Ejemplo** | `wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade` |
| **Frecuencia push** | Cada trade agregado (near real-time) |
| **Autenticación** | Pública |
| **Archivo** | `exchange/src/adapter/hub/binance/stream.rs` |

**Contrato JSON:**

```json
{
  "T": 1234567890000,
  "p": "67200.50",
  "q": "0.015",
  "m": false
}
```

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `T` | i64 | trade time ms |
| `p` | f64 str | price |
| `q` | f64 str | quantity (BTC) |
| `m` | bool | `false` = taker buy, `true` = taker sell |

**Procesamiento**: Trades se acumulan en `KlineTrades` (footprint acumulador, `FxHashMap<Price, GroupedTrades>`) por precio dentro de la barra actual. Al cerrar barra → `Vec<FootprintLevel>`. También alimenta CVD, delta, VPIN y stacked_imbalance.

---

### 3.4 Force Order Stream (Liquidaciones)

| Campo | Valor |
|-------|-------|
| **URL** | `wss://fstream.binance.com:443/ws/{symbol}@forceOrder` |
| **Ejemplo** | `wss://fstream.binance.com:443/ws/btcusdt@forceOrder` |
| **Frecuencia push** | Por evento (real-time) |
| **Autenticación** | Pública |
| **Reconexión** | Automática con retry de 5s |
| **Archivo** | `crates/monitor/src/main.rs` — `spawn_force_order_stream()` |

**Contrato JSON (campo `o`):**

```json
{
  "S": "SELL",
  "z": "0.500",
  "ap": "67150.00",
  "T": 1234567890000
}
```

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `S` | string | `"SELL"` = longs liquidados, `"BUY"` = shorts liquidados |
| `z` | f64 str | quantity (contratos BTC) |
| `ap` | f64 str | average fill price |
| `T` | i64 | timestamp ms |

**Procesamiento**: Alimenta `LiquidationTracker`. Acumula USD liquidados (`qty × ap`) en ventanas de 5m y 60s por side. Usado por `LiquidationHunt` (gate mínimo `liq_hunt_min_usd = 500k USD`) y anti-cascade (`< liq_cascade_threshold = 5M USD` en 60s).

---

## 4. REST APIs — Datos de Mercado

### 4.1 Precio Spot

```
GET https://api.binance.com/api/v3/ticker/price?symbol={SYMBOL}
Frecuencia: cada 30s
```

**Respuesta:**
```json
{ "price": "67200.50" }
```

**Uso**: Precio spot de referencia. Calcula `basis = (perp/spot - 1) × 100` en `OrderFlowContext`.

---

### 4.2 Depth Snapshot (Re-sync L2)

```
GET https://fapi.binance.com/fapi/v1/depth?symbol={SYMBOL}&limit=1000
Frecuencia: on-demand cuando hay gap de secuencia en el WS
Weight: 20 (Binance rate limit)
```

**Respuesta:**
```json
{
  "lastUpdateId": 1050,
  "T": 1234567890000,
  "bids": [["67000.00", "5.500"]],
  "asks": [["67010.00", "3.200"]]
}
```

---

### 4.3 Exchange Info (Metadata)

```
GET https://fapi.binance.com/fapi/v1/exchangeInfo
Frecuencia: una vez al inicializar el adaptador
Weight: 1
```

**Campos extraídos de `symbols[]`:**

| Campo | Uso |
|-------|-----|
| `symbol` | Identificación del par |
| `contractType` | Filtro: solo `PERPETUAL` |
| `quoteAsset` | Filtro: solo `USDT` / `USD` |
| `status` | Filtro: `TRADING` o `HALT` |
| `filters[PRICE_FILTER].tickSize` | Precisión de precio |
| `filters[LOT_SIZE].minQty` | Tamaño mínimo de orden |
| `contractSize` | Tamaño del contrato |

---

### 4.4 Klines Históricas (Startup warm-up)

```
GET https://fapi.binance.com/fapi/v1/klines?symbol={SYMBOL}&interval={INTERVAL}&limit={LIMIT}
Frecuencia: una vez al arrancar
```

**Respuesta** (array de arrays, posiciones):

```
[0]  open_time (ms)
[1]  open (str)
[2]  high (str)
[3]  low (str)
[4]  close (str)
[5]  volume (str)
[6]  close_time (ms)
[7]  quote_asset_volume (str)
[8]  number_of_trades (int)
[9]  taker_buy_base_asset_volume (str)    ← usado
[10] taker_buy_quote_asset_volume (str)
[11] ignore
```

**Uso**: Precalienta `VolumeProfile` (300 velas), `VwapIndicator`, `ATR`, `MarketStructureTracker`, `OiTracker` y `FundingTracker`.

---

## 5. REST APIs — Datos Institucionales

Todos públicos en `fapi.binance.com`. Ninguno requiere API key.

### 5.1 Funding Rate Actual

```
GET https://fapi.binance.com/fapi/v1/premiumIndex?symbol={SYMBOL}
Frecuencia: cada 60s
Archivo: main.rs — fetch_premium_index()
```

**Respuesta:**
```json
{
  "lastFundingRate": "0.00010000",
  "markPrice": "67200.50"
}
```

**Procesamiento**: `FundingTracker.push(rate)`. Calcula:
- `regime`: `Neutral` | `ElevatedLong` | `ExtremeLong` | `ElevatedShort` | `ExtremeShort`
- `velocity`: cambio de tasa por muestra
- `peak_confirmed`: reversión detectada
- Umbral extremo: `funding_extreme_threshold = 0.0006` (0.06%)

---

### 5.2 Funding Rate Histórico (Startup)

```
GET https://fapi.binance.com/fapi/v1/fundingRate?symbol={SYMBOL}&limit=21
Frecuencia: una vez al arrancar
```

**Respuesta:**
```json
[
  { "fundingRate": "0.00010000", "fundingTime": 1234567890000 }
]
```

**Uso**: Precalienta `FundingTracker` con 21 muestras (~7 días a intervalos de 8h) para tener `avg` y `velocity` desde el primer tick en vivo.

---

### 5.3 Open Interest

```
GET https://fapi.binance.com/fapi/v1/openInterest?symbol={SYMBOL}
Frecuencia: cada 5m
Archivo: main.rs — fetch_open_interest()
```

**Respuesta:**
```json
{ "openInterest": "45123.456" }
```

**Procesamiento**: `OiTracker.push(oi_value)`. Calcula:
- `oi_delta` = OI actual − OI anterior
- `oi_momentum_aligned`: precio↑ + OI↑ (longs frescos) o precio↓ + OI↑ (shorts frescos)
- `delta_zscore()`: z-score rolling sobre ventana de 20 deltas — detecta spikes anómalos (> +2.0 o < -2.0)

---

### 5.4 Top Trader Long/Short Ratio

```
GET https://fapi.binance.com/futures/data/topLongShortPositionRatio
    ?symbol={SYMBOL}&period=5m&limit=1
Frecuencia: cada 5m
Archivo: main.rs — fetch_top_trader_ls()
```

**Respuesta:**
```json
[{
  "longAccount": "0.5500",
  "shortAccount": "0.4500",
  "longShortRatio": "1.2222",
  "timestamp": 1234567890000
}]
```

**Procesamiento**: `LsRatioTracker.push(source=TopTraderPosition, long_pct=0.55)`.
Interpretación para `SmartMoneyDivergence`:
- `top_traders_long% < 0.45` → smart money short (`smart_short_threshold`)
- `top_traders_long% > 0.55` → smart money long

---

### 5.5 Global Long/Short Ratio (Proxy Retail)

```
GET https://fapi.binance.com/futures/data/globalLongShortAccountRatio
    ?symbol={SYMBOL}&period=5m&limit=1
Frecuencia: cada 5m
Archivo: main.rs — fetch_global_ls()
```

**Respuesta**: mismos campos que §5.4.

**Procesamiento**: `LsRatioTracker.push(source=GlobalAccount, long_pct=...)`.
Representa al retail. Divergencia con top traders > `min_divergence = 0.10` → `SmartMoneyDivergence`.

---

### 5.6 Taker Buy/Sell Ratio

```
GET https://fapi.binance.com/futures/data/takerlongshortRatio
    ?symbol={SYMBOL}&period=5m&limit=1
Frecuencia: cada 5m
Archivo: main.rs — fetch_taker_ratio()
```

**Respuesta:**
```json
[{
  "buySellRatio": "1.1500",
  "buyVol": "1234.56",
  "sellVol": "1073.53",
  "timestamp": 1234567890000
}]
```

**Procesamiento**: `TakerRatioSnapshot`. Complementa el `taker_imbalance` tick-a-tick del WS con una ventana de 5m de contexto macro.

---

## 6. REST APIs — Histórico (Startup)

Resumen de todas las llamadas que ocurren una sola vez al arrancar:

| Endpoint | Función | Precalienta |
|----------|---------|-------------|
| `/fapi/v1/klines?limit=~400` | Warm-up barras históricas | VP, VWAP, ATR, MarketStructure |
| `/fapi/v1/fundingRate?limit=21` | Historial funding | FundingTracker (avg, velocity) |
| `/fapi/v1/exchangeInfo` | Metadata par | tick_size, min_qty, contract_size |
| `/fapi/v1/depth?limit=1000` | Snapshot L2 inicial | OrderBook local (antes del WS) |

---

## 7. Supabase — Config y Escritura

### 7.1 Lectura — Config de Estrategia (`deployed_params`)

```
GET {SUPABASE_URL}/rest/v1/deployed_params
    ?regime=eq.{REGIME}
    &strategy=is.null
    &is_active=eq.true
    &order=updated_at.desc
    &limit=1
Headers:
  apikey: {SUPABASE_KEY}
  Authorization: Bearer {SUPABASE_KEY}
  Accept: application/json

Frecuencia: cada 300s o en cada cambio de regime
Fallback: StrategyConfig::default() si falla o env vars ausentes
Archivo: crates/monitor/src/config_loader.rs
```

**Campos leídos de `params` (JSONB):**

| Campo | Tipo | Defecto |
|-------|------|---------|
| `max_spread_bps` | f64 | 2.0 |
| `max_vpin` | f64 | 0.75 |
| `min_score` | f64 | 0.60 |
| `default_ttl_ms` | i64 | 15_000_000 (250 min) |
| `min_rr` | f64 | 1.5 |
| `max_rr_m5` | f64 | 8.0 |
| `liq_hunt_min_usd` | f64 | 25_000.0 |
| `liq_cascade_threshold` | f64 | 5_000_000.0 |
| `liq_ttl_ms` | i64 | 600_000 (10 min) |
| `funding_extreme_threshold` | f64 | 0.0006 |
| `funding_ttl_ms` | i64 | 1_800_000 (30 min) |
| `fer_top_long_min` | f64 | 0.46 |
| `fer_retail_long_max` | f64 | 0.58 |
| `smart_short_threshold` | f64 | 0.45 |
| `retail_long_threshold` | f64 | 0.60 |
| `min_divergence` | f64 | 0.10 |
| `smd_ttl_ms` | i64 | 1_200_000 (20 min) |
| `min_score_institutional` | f64 | 0.55 |

---

### 7.2 Escritura — Tablas de salida

Todas las escrituras son fire-and-forget con `tokio::spawn`. Errores van a stderr.

```
POST {SUPABASE_URL}/rest/v1/{tabla}
Headers:
  apikey: {SUPABASE_KEY}
  Authorization: Bearer {SUPABASE_KEY}
  Content-Type: application/json
  Prefer: return=minimal        (señales/outcomes)
  Prefer: return=representation (lab_signals — necesita el id retornado)
```

| Tabla | Disparador | Campos clave |
|-------|-----------|--------------|
| `shadow_signals` | Señal Core emitida | strategy, side, entry, stop, target, score, snapshot |
| `signal_outcomes` | Trade Core cerrado | signal_id, final_r, final_status, mfe, mae |
| `regime_history` | Cambio de regime | regime, atr, timestamp_ms |
| `lab_signals` | Cada barra M5 × 5 detectores | **id** (UUID Rust), strategy_id, status, maturity, snapshot (JSONB) |
| `lab_outcomes` | Outcome Lab resuelto | signal_id (FK → lab_signals.id), outcome_5m/15m/ttl, mfe, mae, final_status |

**Nota FK**: `lab_signals` recibe `"id": signal.signal_id` en el body para que Supabase use el UUID de Rust como PK — garantiza que `lab_outcomes.signal_id` referencie correctamente la fila.

---

## 8. Adaptadores disponibles vs activos

El sistema usa un trait `ExchangeAdapter` implementado para múltiples exchanges. Solo Binance está activo:

| Exchange | Tipo | Estado | Directorio |
|----------|------|--------|------------|
| **Binance** LinearPerps | `Venue::Binance + MarketKind::LinearPerps` | ✅ Activo (Railway) | `exchange/src/adapter/hub/binance/` |
| Bybit | LinearPerps | ✅ Implementado | `exchange/src/adapter/hub/bybit/` |
| Hyperliquid | Perps | ✅ Implementado | `exchange/src/adapter/hub/hyperliquid/` |
| MEXC | LinearPerps | ✅ Implementado | `exchange/src/adapter/hub/mexc/` |
| OKEx | LinearPerps | ✅ Implementado | `exchange/src/adapter/hub/okex/` |

Para activar otro exchange: cambiar `Venue` + `MarketKind` en `main.rs`. Todos exponen la misma interfaz de traits: `kline_stream()`, `depth_stream()`, `trade_stream()`, `fetch_depth_snapshot()`, `fetch_ticker_metadata()`.

---

## 9. Frecuencias y Frescura

| Fuente | Frecuencia | Stale threshold | Flag de salud |
|--------|-----------|-----------------|---------------|
| Kline WS | Continuo (barra cada `TIMEFRAME_MIN`) | — | `StreamHealth::{Ok,Reconnecting,Disc}` |
| Depth WS | 100ms push | — | `StreamHealth` |
| AggTrade WS | Continuo por trade | — | — |
| ForceOrder WS | Por evento | — | `liq_stream_ok: bool` |
| Funding rate REST | 60s | 120s | `funding_tick_at: Option<Instant>` |
| Spot price REST | 30s | — | — |
| Open Interest REST | 5m | 600s | `oi_fetched_at: Option<Instant>` |
| Top Trader L/S REST | 5m | 600s | `ls_fetched_at: Option<Instant>` |
| Global L/S REST | 5m | 600s | (mismo que arriba) |
| Taker ratio REST | 5m | 300s | `taker_fetched_at: Option<Instant>` |
| Config Supabase | 300s o cambio regime | — | silencioso (fallback a default) |

Cuando edad > stale threshold → `DataQuality::Stale` → `BlockReason::DataQuality` en los detectores que requieren ese dato.

---

## 10. Structs internos — Contratos de datos en Rust

Estos son los tipos que circulan por todo el sistema de estrategias. Definidos en `data/src/strategy/types.rs`.

### Enums de estado

```rust
// Régimen de mercado derivado de ATR + estructura
pub enum Regime {
    TrendUp,
    TrendDown,
    Chop,
    Compression,
    Expansion,
    Stress,
    Aftermath,
    Unknown,
}

// Calidad del dato en cada contexto
pub enum DataQuality {
    Live,
    Fallback,
    Degraded,
    Stale,
    Missing,
}

// Absorción detectada en footprint
pub enum AbsorptionSide { Bid, Ask, None, Unknown }

// Imbalance acumulado en N barras consecutivas
pub enum ImbalanceSide { Bullish, Bearish, None, Unknown }
```

### FootprintLevel

```rust
// Delta real por nivel de precio dentro de la barra actual.
// Vec<FootprintLevel> se construye al cerrar barra desde KlineTrades.
pub struct FootprintLevel {
    pub price: f64,
    pub buy_volume: f64,
    pub sell_volume: f64,
    pub delta: f64,              // buy_volume - sell_volume
}
```

### VolumeProfileContext

```rust
// Histograma 150 bins sobre ventana de 300 velas.
pub struct VolumeProfileContext {
    pub poc: Option<f64>,             // Point of Control
    pub vah: Option<f64>,             // Value Area High (70% del volumen)
    pub val: Option<f64>,             // Value Area Low
    pub hvn_nearby: Vec<f64>,         // High Volume Nodes cercanos al precio
    pub lvn_nearby: Vec<f64>,         // Low Volume Nodes cercanos al precio
    pub value_location: ValueLocation, // AboveVah | BelowVal | InValue | Unknown
    pub quality: DataQuality,
}
```

### VwapContext

```rust
// VWAP con reset 00:00 UTC, ±1σ/±2σ bands.
pub struct VwapContext {
    pub vwap_session: Option<f64>,
    pub avwap_bos: Option<f64>,        // AVWAP anclado al último BOS
    pub avwap_event: Option<f64>,      // AVWAP anclado a evento manual
    pub price_vs_vwap: PriceRelation,  // Above | Below | At | Unknown
    pub price_vs_avwap_bos: PriceRelation,
    pub price_vs_avwap_event: PriceRelation,
    pub quality: DataQuality,
}
```

### OrderFlowContext

```rust
// Todo lo derivado de trades WS + kline taker volume.
pub struct OrderFlowContext {
    pub cvd: Option<f64>,              // Cumulative Volume Delta
    pub cvd_slope: Option<f64>,        // Pendiente CVD últimas N barras
    pub delta: Option<f64>,            // Delta de la barra actual
    pub taker_imbalance: Option<f64>,  // (buy_vol - sell_vol) / total_vol
    pub buy_volume: Option<f64>,
    pub sell_volume: Option<f64>,
    pub vpin: Option<f64>,             // Volume-synchronized PIN (0..1)
    pub cvd_divergence: Option<CvdDivergence>,
    pub footprint_absorption: AbsorptionSide,
    pub stacked_imbalance: ImbalanceSide,  // 3+ barras alineadas
    pub failed_acceptance: bool,       // Intento de breakout fallido
    pub sweep_confirmed: bool,         // Wick cruzó swing pero cerró dentro
    pub mss_active: bool,              // Cierre actual superó swing H/L previo (BOS)
    pub quality: DataQuality,
    pub funding_rate: Option<f64>,     // Decimal: 0.0001 = 1 bp
    pub basis: Option<f64>,            // (perp/spot - 1) × 100 (%)
    pub oi_delta: Option<f64>,         // OI actual - OI anterior (contratos)
    pub oi_momentum_aligned: Option<bool>,
    pub bid_wall_nearby: bool,         // Pared bids dentro de 1×ATR debajo
    pub ask_wall_nearby: bool,         // Pared asks dentro de 1×ATR encima
    pub price_action_clean: bool,      // ≤2 reversiones en últimas 5 velas
    pub fast_slope: Option<f64>,       // Pendiente precio normalizada por ATR (5 barras)
    pub footprint_levels: Vec<FootprintLevel>,
    pub oi_delta_zscore: Option<f64>,  // Z-score rolling ventana 20 OI deltas
}
```

### OrderBookContext

```rust
// Derivado del L2 local mantenido por el depth stream.
pub struct OrderBookContext {
    pub obi_l5: Option<f64>,       // (bid_vol_5 - ask_vol_5) / (bid_vol_5 + ask_vol_5)
    pub obi_l10: Option<f64>,      // mismo para 10 niveles
    pub obi_l20: Option<f64>,      // mismo para 20 niveles
    pub microprice: Option<f64>,   // precio ponderado por tamaño top libro
    pub spread_bps: Option<f64>,   // (ask1 - bid1) / mid × 10_000
    pub walls_above: Vec<f64>,     // niveles con qty >= umbral (resistencias)
    pub walls_below: Vec<f64>,     // niveles con qty >= umbral (soportes)
    pub thin_zone_above: bool,     // pocas órdenes encima — precio puede moverse rápido
    pub thin_zone_below: bool,
    pub quality: DataQuality,
    pub spoof: Option<SpoofContext>, // None hasta que SpoofDetector esté activo
}
```

### InstitutionalContext

```rust
// Datos institucionales. None hasta que complete el primer ciclo REST.
pub struct InstitutionalContext {
    pub liquidations: LiquidationContext,
    pub ls_ratio: LsRatioContext,
    pub oi: OiContext,
    pub funding: FundingContext,
    pub liq_map: LiqMapSnapshot,
}

pub struct LiquidationContext {
    pub long_usd_5m: f64,     // USD en longs liquidados últimos 5m
    pub short_usd_5m: f64,
    pub long_usd_60s: f64,    // USD en longs liquidados últimos 60s
    pub short_usd_60s: f64,
    pub total_usd_5m: f64,
}

pub struct LsRatioContext {
    pub top_traders_long_pct: Option<f64>,   // 0..1
    pub retail_long_pct: Option<f64>,
    pub divergence: Option<f64>,             // retail - top_traders
}

pub struct OiContext {
    pub current: f64,
    pub delta: f64,
    pub momentum_aligned: Option<bool>,
    pub delta_zscore: Option<f64>,
}

pub struct FundingContext {
    pub rate: f64,
    pub avg: f64,
    pub regime: FundingRegime,     // Neutral | ElevatedLong | ExtremeLong | ElevatedShort | ExtremeShort
    pub velocity: f64,
    pub peak_confirmed: bool,
}

// Mapa estimado de stops (estimación heurística, no feed directo)
pub struct LiqMapSnapshot {
    pub density_above: f64,         // densidad de stops estimados encima
    pub density_below: f64,
    pub primary_target_above: Option<f64>,  // precio con mayor concentración encima
    pub primary_target_below: Option<f64>,
    pub confidence: f64,            // 0..1
}
```

### StrategyMarketContext (contexto completo por barra)

```rust
// Struct raíz que se construye en cada barra M5 cerrada y se pasa
// a todos los detectores Core y Lab.
pub struct StrategyMarketContext {
    pub symbol: String,
    pub timestamp_ms: i64,
    pub price: f64,
    pub regime: Regime,
    pub atr: Option<f64>,
    pub volume_profile: VolumeProfileContext,
    pub vwap: VwapContext,
    pub flow: OrderFlowContext,
    pub orderbook: OrderBookContext,
    pub institutional: Option<InstitutionalContext>,
    pub swing_high_20: Option<f64>,      // max de últimas 20 barras (exc. actual)
    pub swing_low_20: Option<f64>,       // min de últimas 20 barras
    pub market_structure: Option<MarketStructureContext>,  // BOS/CHoCH, HTF bias
    pub session: Option<SessionContext>,   // Asia/London/NY/Overlap + fase
    pub order_blocks: Option<OrderBlockContext>,  // OBs activos (100 barras)
    pub fvg: Option<FvgContext>,          // Fair Value Gaps activos
    pub leverage: f64,                   // paper leverage (para calibración)
    pub prev_obi_l5: Option<f64>,        // OBI L5 de la barra anterior (DIB gate)
}
```

---

## 11. StrategyConfig — Valores por defecto y calibración

```rust
// data/src/strategy/types.rs
impl Default for StrategyConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            max_spread_bps: 2.0,         // bloqueo total si spread > 2 bps
            max_vpin: 0.75,              // flujo tóxico si VPIN > 0.75
            min_score: 0.60,             // PLACEHOLDER — calibrar con Fase 4
            default_ttl_ms: 15_000_000, // 250 minutos
            min_rr: 1.5,
            max_rr_m5: 8.0,

            // LiquidationHunt
            liq_hunt_min_usd: 25_000.0,          // mínimo USD liquidados en 5m
            liq_cascade_threshold: 5_000_000.0,  // > 5M en 60s = cascade (no entrar)
            liq_ttl_ms: 600_000,                 // 10 minutos

            // FundingExhaustionReversal
            funding_extreme_threshold: 0.0006,   // 0.06% = extremo
            funding_ttl_ms: 1_800_000,           // 30 minutos
            fer_top_long_min: 0.46,              // top traders long% mínimo para FER Long
            fer_retail_long_max: 0.58,           // retail long% máximo para FER Long

            // SmartMoneyDivergence
            smart_short_threshold: 0.45,         // top traders long% < 45% → smart short
            retail_long_threshold: 0.60,         // retail long% > 60% → retail long
            min_divergence: 0.10,                // divergencia mínima (10 pp)
            smd_ttl_ms: 1_200_000,              // 20 minutos

            session_filter_enabled: false,
            min_score_institutional: 0.55,
            htf_scoring_enabled: false,
            spoof_gate_enabled: false,
            cooldown_bars: 5,                    // barras de enfriamiento por (strategy, side)
        }
    }
}
```

Los valores de `deployed_params` en Supabase sobreescriben estos defaults en producción. Los campos `session_filter_enabled`, `htf_scoring_enabled`, `spoof_gate_enabled` y `cooldown_bars` no se leen de Supabase — solo cambian por redeploy.

---

## 12. ConfigLoader — Carga desde Supabase

```rust
// crates/monitor/src/config_loader.rs

const RELOAD_INTERVAL: Duration = Duration::from_secs(300);

pub struct ConfigLoader {
    supabase_url: Option<String>,
    supabase_key: Option<String>,
    client: reqwest::Client,
    pub current_regime: String,
    pub last_reload: Option<Instant>,
}

// Lógica de recarga: se dispara cuando cambia el regime O han pasado 300s
pub fn should_reload(&self, new_regime: &str) -> bool {
    let regime_changed = new_regime != self.current_regime;
    let stale = self.last_reload
        .map(|t| t.elapsed() >= RELOAD_INTERVAL)
        .unwrap_or(true);
    regime_changed || stale
}

// Query a Supabase:
// GET /rest/v1/deployed_params
//     ?regime=eq.{regime}
//     &strategy=is.null
//     &is_active=eq.true
//     &order=updated_at.desc
//     &limit=1
//
// Mapeo de campos JSON → StrategyConfig:
fn parse_config_from_json(params: &Value) -> StrategyConfig {
    let f = |key: &str, default: f64| params.get(key)
        .and_then(|v| v.as_f64()).unwrap_or(default);
    let i = |key: &str, default: i64| params.get(key)
        .and_then(|v| v.as_i64()).unwrap_or(default);

    StrategyConfig {
        enabled: true,
        max_spread_bps:            f("max_spread_bps",            2.0),
        max_vpin:                  f("max_vpin",                  0.75),
        min_score:                 f("min_score",                 0.60),
        default_ttl_ms:            i("default_ttl_ms",            15_000_000),
        liq_hunt_min_usd:          f("liq_hunt_min_usd",          25_000.0),
        liq_cascade_threshold:     f("liq_cascade_threshold",     5_000_000.0),
        liq_ttl_ms:                i("liq_ttl_ms",                600_000),
        funding_extreme_threshold: f("funding_extreme_threshold", 0.0006),
        funding_ttl_ms:            i("funding_ttl_ms",            1_800_000),
        fer_top_long_min:          f("fer_top_long_min",          0.46),
        fer_retail_long_max:       f("fer_retail_long_max",       0.58),
        smart_short_threshold:     f("smart_short_threshold",     0.45),
        retail_long_threshold:     f("retail_long_threshold",     0.60),
        min_divergence:            f("min_divergence",            0.10),
        smd_ttl_ms:                i("smd_ttl_ms",                1_200_000),
        min_rr:                    f("min_rr",                    1.5),
        max_rr_m5:                 f("max_rr_m5",                 8.0),
        min_score_institutional:   f("min_score_institutional",   0.55),
        // No se leen de Supabase (solo por redeploy):
        session_filter_enabled: false,
        htf_scoring_enabled: false,
        spoof_gate_enabled: false,
        cooldown_bars: 5,
    }
}
```

---

---

## 13. Raw vs Derivado — Fórmulas completas

Esta sección distingue qué campos vienen directamente de una API externa (raw) y cuáles se calculan internamente (derivados), con la fórmula o lógica exacta de cada uno.

**Leyenda**: 🟢 Raw (viene de API) | 🔵 Derivado (calculado internamente) | 🟡 Semi-derivado (raw transformado)

---

### 13.1 OrderFlowContext — Raw vs Derivado

| Campo | Origen | Fórmula / Fuente |
|-------|--------|-----------------|
| `buy_volume` | 🟢 Raw | `k.V` del kline WS (`taker_buy_base_asset_volume`) |
| `sell_volume` | 🟡 Semi | `k.v - k.V` (volumen total − taker buy) |
| `delta` | 🔵 Derivado | `buy_volume - sell_volume` por barra |
| `taker_imbalance` | 🔵 Derivado | `(buy_vol - sell_vol) / (buy_vol + sell_vol)` → rango `[-1, +1]` |
| `cvd` | 🔵 Derivado | Suma acumulada de `delta` barra a barra (no se resetea) |
| `cvd_slope` | 🔵 Derivado | OLS slope del CVD sobre ventana de N barras, normalizado por ATR |
| `fast_slope` | 🔵 Derivado | OLS slope de últimas **5** barras de precio, normalizado por ATR |
| `funding_rate` | 🟢 Raw | `lastFundingRate` de `/premiumIndex` (REST 60s) |
| `basis` | 🔵 Derivado | `(precio_perp / precio_spot - 1) × 100` — requiere ambos feeds |
| `oi_delta` | 🔵 Derivado | `OI_actual - OI_anterior` (contratos) — de polling REST 5m |
| `oi_momentum_aligned` | 🔵 Derivado | `true` si (precio↑ ∧ OI↑) ∨ (precio↓ ∧ OI↑ con short momentum) |
| `oi_delta_zscore` | 🔵 Derivado | `(delta_actual - media_20) / std_20` — ventana rolling de 20 OI deltas |
| `vpin` | 🔵 Derivado | Volume-synchronized PIN: `abs(buy_vol - sell_vol) / total_vol` rolling |
| `footprint_levels` | 🔵 Derivado | Acumulación de trades WS (`aggTrade`) por nivel de precio en la barra actual → `Vec<FootprintLevel>` al cerrar barra |
| `footprint_absorption` | 🔵 Derivado | Detectado desde `footprint_levels`: delta negativo en zona VAL (bid absorption) o positivo en VAH (ask absorption) con CVD slope opuesto |
| `stacked_imbalance` | 🔵 Derivado | 3+ barras consecutivas con mismo signo de delta (oldest-first, rev-scan) |
| `failed_acceptance` | 🔵 Derivado | Wick cruzó VAH/VAL en las últimas 3 barras pero el cierre más reciente volvió al value area |
| `sweep_confirmed` | 🔵 Derivado | Alguna de las últimas 3 barras: wick cruzó swing H/L previo pero cerró dentro del rango |
| `mss_active` | 🔵 Derivado | Cierre actual > prior_swing_high ∨ cierre actual < prior_swing_low (ventana N-1 barras) |
| `cvd_divergence` | 🔵 Derivado | Precio hizo HH pero CVD slope < −0.5 (BearishAbsorption) ∨ precio hizo LL pero CVD slope > +0.5 (BullishAbsorption) — ventana 10 barras |
| `bid_wall_nearby` | 🔵 Derivado | Alguna wall en `walls_below` dentro de `1×ATR` del precio |
| `ask_wall_nearby` | 🔵 Derivado | Alguna wall en `walls_above` dentro de `1×ATR` del precio |
| `price_action_clean` | 🔵 Derivado | Reversiones en últimas 5 velas `≤ 2` (conteo de cambios de signo en movimientos consecutivos) |

---

### 13.2 OrderBookContext — Raw vs Derivado

| Campo | Origen | Fórmula / Fuente |
|-------|--------|-----------------|
| Bids/asks L2 local | 🟢 Raw | Depth WS `@depth@100ms` (actualizaciones incrementales) + REST `/depth` (snapshot inicial y re-syncs) |
| `spread_bps` | 🔵 Derivado | `(ask1 - bid1) / mid × 10_000` donde `mid = (bid1 + ask1) / 2` |
| `microprice` | 🔵 Derivado | `(bid1_price × ask1_qty + ask1_price × bid1_qty) / (bid1_qty + ask1_qty)` — precio ponderado por tamaño del top del libro |
| `obi_l5` | 🔵 Derivado | `(sum_bid_qty_5 - sum_ask_qty_5) / (sum_bid_qty_5 + sum_ask_qty_5)` — rango `[-1, +1]` |
| `obi_l10` | 🔵 Derivado | Mismo para 10 niveles |
| `obi_l20` | 🔵 Derivado | Mismo para 20 niveles |
| `walls_above` | 🔵 Derivado | Niveles ask en top 30 con qty > `5× promedio_qty_top20` |
| `walls_below` | 🔵 Derivado | Niveles bid en top 30 con qty > `5× promedio_qty_top20` |
| `thin_zone_above` | 🔵 Derivado | ≥3 de los top 10 niveles ask con qty < `0.3× promedio_qty_top10` |
| `thin_zone_below` | 🔵 Derivado | ≥3 de los top 10 niveles bid con qty < `0.3× promedio_qty_top10` |

---

### 13.3 VolumeProfileContext — Raw vs Derivado

| Campo | Origen | Fórmula / Fuente |
|-------|--------|-----------------|
| OHLCV per barra | 🟢 Raw | Kline WS `@kline_5m` + histórico REST `/klines` |
| `poc` | 🔵 Derivado | Nivel de precio con mayor volumen acumulado — histograma 150 bins sobre 300 barras |
| `vah` | 🔵 Derivado | Límite superior del value area (70% del volumen total) — expand desde POC hacia arriba |
| `val` | 🔵 Derivado | Límite inferior del value area (70% del volumen total) — expand desde POC hacia abajo |
| `hvn_nearby` | 🔵 Derivado | Bins con volumen > umbral local — múltiples picos en el histograma |
| `lvn_nearby` | 🔵 Derivado | Bins con volumen < umbral local — valles en el histograma |
| `value_location` | 🔵 Derivado | `AboveVah` si precio > VAH, `BelowVal` si precio < VAL, `InValue` si dentro |

---

### 13.4 VwapContext — Raw vs Derivado

| Campo | Origen | Fórmula / Fuente |
|-------|--------|-----------------|
| `vwap_session` | 🔵 Derivado | `sum(precio_típico × volumen) / sum(volumen)` — reset a 00:00 UTC. Precio típico = `(H + L + C) / 3` |
| `avwap_bos` | 🔵 Derivado | VWAP anclado desde la barra del último BOS detectado por `MarketStructureTracker` |
| `price_vs_vwap` | 🔵 Derivado | Comparación simple: `Above` / `Below` / `At` |

---

### 13.5 Regime — Raw vs Derivado

El regime es completamente derivado — ningún campo viene de API.

```
Inputs: recent_closes[] (últimas 14 barras), recent_highs[], recent_lows[], ATR, prev_regime

Cálculo:
  slow_slope = OLS(recent_closes, 14 barras) / ATR
  fast_slope = OLS(recent_closes[-5:], 5 barras) / ATR
  range_atr  = (max(closes) - min(closes)) / ATR

Clasificación base:
  range_atr < 0.8                        → Compression
  range_atr > 4.0 AND |slow| > 0.10     → Expansion
  slow > 0.10 OR fast > 0.25            → TrendUp
  slow < -0.10 OR fast < -0.25          → TrendDown
  else                                  → Chop

Extensiones (sobre la base):
  last_bar_range > 2× avg_bar_range
    AND |slow| > 0.15                   → Stress
  prev_regime ∈ {Stress, Aftermath}
    AND last_bar_range < 1.5× avg
    AND |slow| < 0.10                   → Aftermath

Histéresis (evita flipping rápido TrendUp↔Chop):
  En TrendUp: permanece si slow > 0.05 OR fast > 0.15
  En TrendDown: permanece si slow < -0.05 OR fast < -0.15
```

---

### 13.6 InstitutionalContext — Raw vs Derivado

| Campo | Origen | Fórmula / Fuente |
|-------|--------|-----------------|
| `liquidations.long_usd_5m` | 🟡 Semi | Eventos WS `@forceOrder` con `S=SELL`: `sum(qty × ap)` en ventana deslizante 5m |
| `liquidations.short_usd_5m` | 🟡 Semi | Eventos `@forceOrder` con `S=BUY`: `sum(qty × ap)` en ventana 5m |
| `liquidations.long_usd_60s` | 🟡 Semi | Mismo, ventana 60s |
| `liquidations.short_usd_60s` | 🟡 Semi | Mismo, ventana 60s |
| `ls_ratio.top_traders_long_pct` | 🟢 Raw | `longAccount` de `/topLongShortPositionRatio` (REST 5m) |
| `ls_ratio.retail_long_pct` | 🟢 Raw | `longAccount` de `/globalLongShortAccountRatio` (REST 5m) |
| `ls_ratio.divergence` | 🔵 Derivado | `retail_long_pct - top_traders_long_pct` |
| `oi.current` | 🟢 Raw | `openInterest` de `/openInterest` (REST 5m) |
| `oi.delta` | 🔵 Derivado | `oi_actual - oi_anterior` (contratos) |
| `oi.momentum_aligned` | 🔵 Derivado | `true` si (precio↑ ∧ OI↑) = longs frescos, o (precio↓ ∧ OI↑) = shorts frescos |
| `oi.delta_zscore` | 🔵 Derivado | `(delta - mean(deltas[-20:])) / std(deltas[-20:])` |
| `funding.rate` | 🟢 Raw | `lastFundingRate` de `/premiumIndex` (REST 60s) |
| `funding.avg` | 🔵 Derivado | Media de las últimas N tasas acumuladas en `FundingTracker` |
| `funding.velocity` | 🔵 Derivado | `rate_actual - rate_anterior` (tendencia de cambio) |
| `funding.regime` | 🔵 Derivado | `abs(rate) > 0.0006` → Extreme; `abs(rate) > 0.0003` → Elevated; else Neutral. Signo determina Long/Short |
| `funding.peak_confirmed` | 🔵 Derivado | Velocity cambió de signo mientras regime era Extreme (reversión detectada) |
| `liq_map.density_above` | 🔵 Derivado | Estimación heurística: stops de shorts sobre swing highs, densidad proporcional al OI, decaimiento half-life 4h |
| `liq_map.density_below` | 🔵 Derivado | Stops de longs bajo swing lows — misma metodología |
| `liq_map.primary_target_above/below` | 🔵 Derivado | Precio con mayor densidad estimada en cada lado |
| `liq_map.confidence` | 🔵 Derivado | Función de la antigüedad del OI y del número de swing points disponibles |

---

### 13.7 swing_high_20 / swing_low_20 — Derivado

```
swing_high_20 = max(highs[0..n-1])   // excluye la barra actual
swing_low_20  = min(lows[0..n-1])    // excluye la barra actual
Ventana: últimas 20 barras (o las disponibles si < 20)
Archivo: adapter.rs — derive_swing_highs_lows()
```

---

### 13.8 Resumen visual: pipeline de transformación

```
Binance WS / REST
        │
        ▼
┌───────────────────────────────┐
│        DATOS RAW              │
│  kline: O,H,L,C,V,taker_V    │
│  depth: bids[], asks[]        │
│  aggTrade: price,qty,m        │
│  forceOrder: S,z,ap,T         │
│  premiumIndex: fundingRate    │
│  openInterest: OI             │
│  topLongShort: longAccount    │
│  globalLongShort: longAccount │
└──────────────┬────────────────┘
               │
               ▼
┌───────────────────────────────┐
│      TRANSFORMACIÓN L1        │  (un solo paso, sin acumulación)
│  delta = buy_vol - sell_vol   │
│  taker_imbalance = Δ/total    │
│  spread_bps = (a-b)/mid×10k  │
│  microprice = weighted mid    │
│  obi_l5/10/20 = (B-A)/(B+A)  │
│  liq_usd = qty × ap           │
│  oi_delta = OI[n] - OI[n-1]  │
└──────────────┬────────────────┘
               │
               ▼
┌───────────────────────────────┐
│      TRANSFORMACIÓN L2        │  (requiere historia acumulada)
│  cvd = Σ delta                │
│  cvd_slope = OLS(cvd, N)/ATR  │
│  fast_slope = OLS(close,5)/ATR│
│  vpin = rolling abs(Δ)/total  │
│  vwap = Σ(típico×vol)/Σvol    │
│  poc/vah/val = histograma 300 │
│  regime = OLS + range + stress│
│  funding_regime = threshold   │
│  oi_zscore = z-score rolling  │
│  walls/thin = umbral ×avg_qty │
└──────────────┬────────────────┘
               │
               ▼
┌───────────────────────────────┐
│      TRANSFORMACIÓN L3        │  (cross-field, múltiples inputs)
│  failed_acceptance = vah/val  │
│    + breach_window + cierre   │
│  sweep_confirmed = swing H/L  │
│    + wick + cierre            │
│  mss_active = close vs prior  │
│    swing_high / swing_low     │
│  stacked_imbalance = 3+ barras│
│    mismo signo delta          │
│  footprint_absorption = Σdelta│
│    en zona ±0.35×ATR de val/vah│
│  cvd_divergence = HH/LL vs    │
│    slope (10 barras)          │
│  liq_map = OI + swings + decay│
└──────────────┬────────────────┘
               │
               ▼
     StrategyMarketContext
     (input de los detectores)
```

---

*Documento de referencia del codebase. Actualizar cuando cambien URLs, frecuencias de polling, structs de datos, esquema de Supabase o fórmulas de derivación.*
