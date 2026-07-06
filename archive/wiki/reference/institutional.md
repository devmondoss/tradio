# Referencia: Datos Institucionales

Crate: `data/src/institutional/`

El módulo institucional agrega datos de exchanges (Binance REST) sobre el comportamiento de traders grandes y el estado macro del mercado. Es la fuente de verdad para los tres detectores que requieren `InstitutionalContext`: `LiquidationHunt`, `FundingExhaustionReversal`, y `SmartMoneyDivergence`.

---

## Arquitectura

```
institutional/
  types.rs              ← todos los tipos y enums
  liquidation_tracker.rs ← ventana deslizante de liquidaciones
  ls_ratio_tracker.rs   ← snapshots L/S ratio (top traders + global)
  oi_tracker.rs         ← historial OI con OLS slope
  funding_tracker.rs    ← historial funding rate con percentil
```

Cada tracker es un struct independiente. El monitor los mantiene en su `BarState` y los actualiza al recibir datos del exchange.

---

## Tipos (`types.rs`)

### LiqSide

```rust
enum LiqSide { Longs, Shorts, Neutral }
```

Lado dominante de liquidaciones.

### LiquidationEvent

```rust
struct LiquidationEvent {
    timestamp_ms: i64,
    side: LiqSide,
    quantity_usd: f64,  // valor nocional en USD
}
```

Evento individual de liquidación. El monitor recibe estos eventos del stream de Binance (canal `forceOrder`).

### LiquidationSnapshot

```rust
struct LiquidationSnapshot {
    long_liq_usd_5m: f64,    // USD liquidados (longs) en últimos 5 min
    short_liq_usd_5m: f64,   // USD liquidados (shorts) en últimos 5 min
    total_usd_5m: f64,
    dominant_side: LiqSide,  // lado con > 60% del total
    cascade_detected: bool,  // true si > $5M en cualquier ventana de 60s
    last_event_ms: Option<i64>,
}
```

`cascade_detected` es el gate principal en detectores — indica que el movimiento de liquidación ya ocurrió y es tarde para entrar.

### LsSource

```rust
enum LsSource { TopTraderPosition, GlobalAccount }
```

Fuente del dato L/S:
- `TopTraderPosition` — top 20 traders por volumen (Binance "Top Trader L/S Ratio")
- `GlobalAccount` — ratio global de cuentas (incluye retail)

### LongShortSnapshot

```rust
struct LongShortSnapshot {
    timestamp_ms: i64,
    long_ratio: f64,   // fracción de posiciones long (0.0–1.0)
    short_ratio: f64,  // = 1.0 - long_ratio
    ls_ratio: f64,     // long / short (ratio directo)
    source: LsSource,
}
```

### DivergenceSignal

```rust
enum DivergenceSignal {
    SmartShortRetailLong,   // retail_long - top_long > 0.15 → squeeze potencial
    SmartLongRetailShort,   // top_long - retail_long > 0.15 → squeeze inverso
    Aligned,                // ambos lados en la misma dirección
    Neutral,                // sin datos o diferencia < 10%
}
```

### LsRatioContext

```rust
struct LsRatioContext {
    top_traders_long_pct: f64,  // default 0.5 (sin datos)
    retail_long_pct: f64,       // default 0.5
    divergence_signal: DivergenceSignal,
}
```

### OiTrendDir

```rust
enum OiTrendDir {
    AccumulatingFast,  // change_30m > +1.0%
    Accumulating,      // change_30m > +0.3%
    Flat,
    Decreasing,        // change_30m < -0.3%
    DecreasingFast,    // change_30m < -1.0%
}
```

### OiTrend

```rust
struct OiTrend {
    current: f64,      // OI actual en USD
    change_30m: f64,   // % de cambio en 30 minutos
    slope_5bar: f64,   // pendiente OLS de últimas 5 muestras, normalizada por OI actual
    trend: OiTrendDir,
}
```

`slope_5bar` es la pendiente OLS normalizada — permite comparar entre diferentes niveles de OI absoluto.

### TakerRatioSnapshot

```rust
struct TakerRatioSnapshot {
    timestamp_ms: i64,
    buy_sell_ratio: f64,   // buy_volume / sell_volume
    taker_imbalance: f64,  // (buy - sell) / (buy + sell) → [-1.0, +1.0]
}
```

`taker_imbalance > 0` = compradores agresivos. Usado en `FundingExhaustionReversal` y `LiquidationHunt`.

### FundingRegime

```rust
enum FundingRegime {
    ExtremeLong,   // > percentil 85 de historial reciente (positivo)
    ElevatedLong,  // > percentil 65
    Neutral,
    ElevatedShort, // < percentil 35 (negativo)
    ExtremeShort,  // < percentil 15
}
```

El régimen se calcula por **percentil** sobre el historial reciente, no por umbral fijo — se adapta a diferentes instrumentos.

### FundingContext

```rust
struct FundingContext {
    current: f64,       // tasa actual
    avg: f64,           // promedio del historial
    regime: FundingRegime,
}
```

### InstitutionalContext

```rust
struct InstitutionalContext {
    timestamp_ms: i64,
    liquidations: LiquidationSnapshot,
    ls_ratio: LsRatioContext,
    oi_trend: OiTrend,
    taker_ratio: Option<TakerRatioSnapshot>,  // None si no hay datos recientes
    funding: FundingContext,
    quality: DataQuality,  // de data/src/strategy/types.rs
}
```

El struct central que se pasa a los detectores institucionales. `taker_ratio` es `Option` porque Binance no siempre tiene datos actualizados en el momento exacto del bar close.

---

## Trackers

### LiquidationTracker

**Constantes:**
```
WINDOW_MS = 5 * 60 * 1000     // ventana de agregación: 5 min
CASCADE_WINDOW_MS = 60_000    // ventana de cascade: 60 s
CASCADE_THRESHOLD_USD = 5_000_000.0  // $5M en 60s = cascade
```

**Métodos:**
- `push(event)` — agrega un evento de liquidación al buffer
- `prune(now_ms)` — elimina eventos más viejos que 5 min (llama antes del snapshot en cada bar)
- `snapshot(now_ms) → LiquidationSnapshot` — calcula totales, dominant_side y cascade_detected

**Lógica de `dominant_side`:**
```
if total < $1       → Neutral
if long_usd/total > 0.60  → Longs
if short_usd/total > 0.60 → Shorts
else                → Neutral
```

Requiere más del 60% de un solo lado para declarar dominancia.

### LsRatioTracker

**Constantes:**
```
MAX_SAMPLES = 6  // 30 minutos a intervalos de 5 min
```

**Estructura interna:**
```rust
struct LsRatioTracker {
    top_position: VecDeque<LongShortSnapshot>,  // TopTraderPosition
    global: VecDeque<LongShortSnapshot>,        // GlobalAccount
}
```

Mantiene dos colas separadas: una para top traders, otra para global (retail).

**`snapshot() → LsRatioContext`:**
1. Toma el `long_ratio` más reciente de cada cola
2. Calcula `divergence = retail_long - top_long`
3. Asigna `DivergenceSignal`:
   - `divergence > 0.15` → `SmartShortRetailLong`
   - `divergence < -0.15` → `SmartLongRetailShort`
   - ambos cerca de 0.50 (< ±10%) → `Neutral`
   - else → `Aligned`

**Default sin datos:** ambos `long_ratio = 0.5`, signal = `Neutral`.

### OiTracker

**Constantes:**
```
MAX_SAMPLES = 30  // 150 minutos a intervalos de 5 min
SAMPLES_30M = 6   // ventana de 30 min
SLOPE_WINDOW = 5  // última 5 muestras para OLS
```

**`snapshot() → OiTrend`:**
1. `change_30m` = `(current - oi_30m_ago) / oi_30m_ago * 100`
2. `slope_5bar` = pendiente OLS de últimas 5 muestras, normalizada por OI actual
3. `trend` clasificado por `change_30m`:
   ```
   > +1.0% → AccumulatingFast
   > +0.3% → Accumulating
   < -0.3% → Decreasing
   < -1.0% → DecreasingFast
   else    → Flat
   ```

**OLS slope formula:**
```
slope = (n·Σxy - Σx·Σy) / (n·Σx² - (Σx)²)
```
Normalizada dividiendo por el OI actual — permite comparar entre instrumentos.

### FundingTracker

**Constantes:**
```
MAX_SAMPLES = 21  // ~7 días a intervalos de 8h
```

**`load(samples)`:** carga historial, ordena por timestamp, trunca a 21 muestras más recientes.

**`snapshot() → FundingContext`:**
1. `current` = última muestra
2. `avg` = promedio de todas las muestras
3. `percentile` = rank / n * 100 (fracción de muestras por debajo del current)
4. `regime`:
   ```
   current > 0 AND percentile > 85 → ExtremeLong
   current > 0 AND percentile > 65 → ElevatedLong
   current < 0 AND percentile < 15 → ExtremeShort
   current < 0 AND percentile < 35 → ElevatedShort
   else                            → Neutral
   ```

El régimen es **relativo al historial** — un funding de 0.01% puede ser `ExtremeLong` si el instrumento normalmente opera en 0.001%.

---

## Flujo de datos en el monitor

```
Binance REST (cada 5 min):
  GET /fapi/v1/ticker/bookTicker         → taker_ratio
  GET /futures/data/topLongShortPositionRatio → LsRatioTracker::push(TopTraderPosition)
  GET /futures/data/globalLongShortAccountRatio → LsRatioTracker::push(GlobalAccount)
  GET /fapi/v1/openInterest              → OiTracker::push
  GET /fapi/v1/fundingRate (historial)   → FundingTracker::load  (al arrancar)

Binance WS (tiempo real):
  forceOrder stream                      → LiquidationTracker::push
```

En cada bar close, el monitor llama `LiquidationTracker::prune(now)` y luego ensambla el `InstitutionalContext` desde los cuatro snapshots.

---

## Detectores que usan InstitutionalContext

| Detector | Liquidaciones | L/S Ratio | OI | Funding | Taker |
|----------|---------------|-----------|-----|---------|-------|
| `LiquidationHunt` | ✓ (trigger) | ✓ (gate) | ✓ (slope) | ✓ (gate) | ✓ (imbalance) |
| `FundingExhaustionReversal` | ✓ (cascade gate) | ✓ (confirmación) | ✓ (trend) | ✓ (trigger) | ✓ (ratio) |
| `SmartMoneyDivergence` | ✓ (cascade gate) | ✓ (trigger) | ✓ (AccumulatingFast gate) | ✓ (confirmación) | — |

Los tres detectores sin `InstitutionalContext` (`ValueAreaFailedAuction`, `LvnLiquidityVacuumBreakout`, `VwapValuePullbackContinuation`) solo usan `StrategyMarketContext` — datos del chart directamente disponibles sin REST adicional.
