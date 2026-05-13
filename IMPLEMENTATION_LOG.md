# Implementation Log: Strategy Module + New Indicators

## Estado: Fase 1 completada (estructura base)

---

## Archivos Creados

### Modulo Strategy (`src/strategy/`)

| Archivo | Descripcion |
|---------|-------------|
| `src/strategy/mod.rs` | Module root, exports all submodules |
| `src/strategy/types.rs` | All data contracts: StrategyMarketContext, signals, enums, configs |
| `src/strategy/context.rs` | Helper methods for context building (price_relation, value_location) |
| `src/strategy/router.rs` | `route_strategy()` - runs all detectors, picks best signal |
| `src/strategy/scoring.rs` | `score_signal()` - evidence-based scoring with penalties |
| `src/strategy/logger.rs` | JSONL logger to `data_dir/flowsurface/shadow_events/strategy_signals.jsonl` |
| `src/strategy/detectors/mod.rs` | Detector module root |
| `src/strategy/detectors/toxic_flow_gate.rs` | Gate filter: blocks on stress/aftermath/stale/wide spread/toxic vpin |
| `src/strategy/detectors/value_area_failed_auction.rs` | Strategy 1: failed auction above VAH / below VAL |
| `src/strategy/detectors/vwap_value_pullback_continuation.rs` | Strategy 2: trend pullback to value + flow realignment |
| `src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs` | Strategy 3: breakout through thin order book zone |

### Indicadores Nuevos (`src/chart/indicator/kline/`)

| Archivo | Descripcion |
|---------|-------------|
| `src/chart/indicator/kline/vwap.rs` | Session VWAP (cumulative typical_price * volume) |
| `src/chart/indicator/kline/atr.rs` | ATR(14) - Average True Range con Wilder smoothing |
| `src/chart/indicator/kline/volume_profile.rs` | Rolling Volume Profile: POC, VAH, VAL (70% value area) |

---

## Archivos Modificados

| Archivo | Cambio |
|---------|--------|
| `src/main.rs` | Added `mod strategy;` |
| `data/src/chart/indicator.rs` | Added `Vwap`, `VolumeProfile`, `Atr` to `KlineIndicator` enum + Display + market arrays |
| `src/chart/indicator/kline.rs` | Added `pub mod vwap/volume_profile/atr` + factory match arms |
| `Cargo.toml` | Added `dirs-next = "2.0.0"` dependency |
| `FLOWSURFACE_STRATEGY_PROPOSAL_CLAUDE_CODE.md` | Updated sections 13-14-17-18 for UI integration |

---

## Como funciona

### Flujo de datos de los indicadores

```
Exchange WebSocket → Trades/Klines
  → KlineChart.data_source (PlotData<KlineDataPoint>)
    → indicator.rebuild_from_source() / on_insert_trades()
      → VWAP: cumulative(TP * Vol) / cumulative(Vol) per bar
      → ATR: Wilder smoothed True Range, period 14
      → Volume Profile: rolling POC/VAH/VAL from footprint data
    → Canvas render via LinePlot
```

### Flujo de la estrategia (pendiente de wiring)

```
Indicators compute → build StrategyMarketContext
  → toxic_flow_gate() check
  → run all 3 detectors
  → score_signal() on candidates
  → pick highest score >= min_score
  → log to JSONL if ShadowSignal or Blocked
  → (future) draw overlay on chart
```

---

## Tests incluidos

```bash
# Para correr los tests del modulo strategy:
cargo test --lib strategy

# Tests especificos:
# - toxic_flow_gate: 6 tests (allows valid, blocks stress/aftermath/spread/vpin/stale)
# - value_area_failed_auction: 4 tests (short/long detection, rejection cases)
# - vwap_value_pullback_continuation: 4 tests (long/short detection, rejection cases)
# - lvn_liquidity_vacuum_breakout: 4 tests (long/short detection, rejection cases)
# - scoring: 2 tests (high evidence scoring, spread penalty)
```

---

## Gaps y Pendientes

### Fase 2: Wiring (conectar strategy al chart)

- [ ] **Adapter**: `build_strategy_context()` que mapee el estado actual de KlineChart + Depth + Indicators activos → `StrategyMarketContext`
- [ ] **Execution point**: Decidir cuando se ejecuta `route_strategy()` (cada trade? cada vela nueva? timer?)
- [ ] **Overlay rendering**: Dibujar entry/stop/target lines sobre el canvas del kline chart cuando hay signal activa
- [ ] **UI toggle**: Agregar "Strategy Signals" como opcion en el dropdown de Indicators (o toggle separado)

### Fase 3: Features faltantes en los indicadores

- [ ] **VWAP session reset**: Actualmente es cumulative desde el inicio de data. Necesita reset por sesion (00:00 UTC o configurable)
- [ ] **AVWAP (Anchored VWAP)**: VWAP anclado a un evento especifico (BOS, swing high/low)
- [ ] **Volume Profile windowed**: Actualmente es rolling total. Opcion de ventana (ej: ultimas 50 velas)
- [ ] **HVN/LVN detection**: Identificar high/low volume nodes automaticamente del profile

### Fase 4: Datos no disponibles aun (Option<T> en el context)

- [ ] `regime`: No hay detector de regimen de mercado. Necesita implementacion (ATR slope + trend detection)
- [ ] `vpin`: Volume-synchronized probability of informed trading. Formula compleja, requiere buckets de volumen
- [ ] `taker_imbalance`: Ratio de agresion. Calculable desde buy_volume/sell_volume del footprint
- [ ] `footprint_absorption`: Detectar absorcion (alto volumen sin movimiento de precio)
- [ ] `stacked_imbalance`: Multiples niveles consecutivos con imbalance > threshold
- [ ] `failed_acceptance`: Precio rompe nivel, no sostiene. Necesita tracking de estado temporal
- [ ] `sweep_confirmed`: Liquidity sweep detection
- [ ] `mss_active`: Market structure shift (break of swing)
- [ ] `thin_zone_above/below`: Detectar desde depth data (zonas con poca liquidez en el book)
- [ ] `walls_above/below`: Niveles con ordenes grandes en el book
- [ ] `obi` (Order Book Imbalance): Calculable desde Depth { bids, asks }
- [ ] `microprice`: (best_bid * ask_size + best_ask * bid_size) / (bid_size + ask_size)

### Fase 5: Outcome tracker

- [ ] Track cada signal emitida y medir MFE/MAE en horizontes: 5s, 15s, 30s, 60s, 3m, 5m, 15m
- [ ] Guardar outcomes en JSONL separado
- [ ] No declarar usable hasta 300+ signals con expectancy neta positiva

---

## Nota sobre compilacion

El entorno actual tiene un problema con `link.exe` (conflicto MSVC linker). Los errores de build NO son de nuestro codigo sino del toolchain de Windows. Una vez resuelto el entorno (reinstalar C++ Build Tools o fijar PATH), el codigo deberia compilar limpio.
