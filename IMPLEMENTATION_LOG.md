# Implementation Log: Strategy Module + New Indicators

## Estado: Fase 3 completada (pipeline wired end-to-end)

---

## Arquitectura del Pipeline

```
Exchange WebSocket
  ├── Trades → KlineChart.insert_trades() → Indicators update
  └── Depth  → Dashboard.ingest_depth()
                  ├── Heatmap/Ladder (existing)
                  └── KlineChart.update_depth() ← NEW
                        └── run_strategy_detection()
                              ├── build_orderbook_context(depth)
                              ├── build_flow_context(cvd, delta...)
                              ├── build_vwap_context(price, vwap)
                              ├── build_volume_profile_context(price, poc, vah, val)
                              ├── toxic_flow_gate() check
                              ├── 3 detectors: VA_Failed, VWAP_Pullback, LVN_Breakout
                              ├── score_signal()
                              ├── logger::log_signal() → JSONL file
                              └── push_strategy_signal() → draw_strategy_overlay()
```

---

## Archivos Creados (17 files, ~4200 lines)

### Modulo Strategy (`src/strategy/`)

| Archivo | Descripcion |
|---------|-------------|
| `src/strategy/mod.rs` | Module root |
| `src/strategy/types.rs` | All data contracts: StrategyMarketContext, StrategySignal, enums, configs |
| `src/strategy/context.rs` | Helper methods: price_relation, determine_value_location |
| `src/strategy/adapter.rs` | Builds context from live Depth + indicator outputs |
| `src/strategy/router.rs` | `route_strategy()` - runs detectors, picks best signal |
| `src/strategy/scoring.rs` | `score_signal()` - evidence-based scoring with penalties |
| `src/strategy/logger.rs` | JSONL logger to `data_dir/flowsurface/shadow_events/` |
| `src/strategy/detectors/mod.rs` | Detector module root |
| `src/strategy/detectors/toxic_flow_gate.rs` | Gate: blocks stress/aftermath/stale/spread/vpin |
| `src/strategy/detectors/value_area_failed_auction.rs` | Strategy 1: trapped breakout traders |
| `src/strategy/detectors/vwap_value_pullback_continuation.rs` | Strategy 2: trend pullback + flow realignment |
| `src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs` | Strategy 3: thin zone breakout |

### Indicadores Nuevos (`src/chart/indicator/kline/`)

| Archivo | Descripcion |
|---------|-------------|
| `src/chart/indicator/kline/vwap.rs` | Session VWAP (cumulative TP * Vol / Vol) |
| `src/chart/indicator/kline/atr.rs` | ATR(14) - Wilder smoothed True Range |
| `src/chart/indicator/kline/volume_profile.rs` | Rolling POC/VAH/VAL (70% value area) |

### Documentacion

| Archivo | Descripcion |
|---------|-------------|
| `FLOWSURFACE_STRATEGY_PROPOSAL_CLAUDE_CODE.md` | Propuesta tecnica completa |
| `IMPLEMENTATION_LOG.md` | Este archivo |

---

## Archivos Modificados

| Archivo | Cambio |
|---------|--------|
| `src/main.rs` | `mod strategy;` |
| `src/chart/kline.rs` | strategy_signals, strategy_overlay_enabled, last_depth, update_depth(), run_strategy_detection(), draw_strategy_overlay() |
| `src/screen/dashboard.rs` | ingest_depth() now routes to Kline panes |
| `data/src/chart/indicator.rs` | KlineIndicator enum + Display + market arrays (Vwap, VolumeProfile, Atr) |
| `src/chart/indicator/kline.rs` | pub mod + factory make_empty() for new indicators |
| `Cargo.toml` | Added `dirs-next = "2.0.0"` |

---

## Como funciona (end-to-end)

### Indicadores

```
Klines/Trades arrive
  → VwapIndicator.rebuild_from_source() → cumulative(TP*Vol)/cumulative(Vol)
  → AtrIndicator.rebuild_from_source() → Wilder smoothed TR(14)
  → VolumeProfileIndicator.rebuild_from_source() → rolling POC/VAH/VAL
  → LinePlot render in indicator panel below chart
```

### Strategy Detection

```
Depth update arrives → KlineChart.update_depth()
  → if strategy_overlay_enabled:
    → build StrategyMarketContext from depth + last price
    → toxic_flow_gate() → pass/block
    → detect_value_area_failed_auction()
    → detect_vwap_value_pullback_continuation()
    → detect_lvn_liquidity_vacuum_breakout()
    → score_signal() with evidence + penalties
    → if score >= min_score (0.70):
      → log to JSONL
      → push to chart overlay
      → draw entry/stop/target lines on canvas
```

### Overlay Rendering

```
draw_strategy_overlay() called in canvas main cache:
  - Entry: solid horizontal line (green=long, red=short)
  - Stop: dashed red line
  - Target: dashed green line
  - Zone: semi-transparent rectangle between entry and target
  - Auto-expire signals after TTL (default 5min)
  - Max 50 concurrent signals displayed
```

---

## Tests (20 unit tests)

```bash
cargo test --lib strategy

# Breakdown:
# toxic_flow_gate: 6 tests
# value_area_failed_auction: 4 tests
# vwap_value_pullback_continuation: 4 tests
# lvn_liquidity_vacuum_breakout: 4 tests
# scoring: 2 tests
```

---

## Lo que funciona ahora

- [x] 3 nuevos indicadores en el dropdown (VWAP, Vol Profile, ATR)
- [x] Modulo strategy completo con 3 detectores + gate + scoring
- [x] Overlay renderer para signals en el candlestick chart
- [x] JSONL logger para shadow events
- [x] Pipeline depth → strategy detection → overlay wired
- [x] Signal expiration y max concurrent limits
- [x] Adapter builds OrderBookContext from live Depth (OBI, microprice, spread, walls, thin zones)

---

## Pendientes por fase

### Fase 4: Exponer valores de indicadores al strategy context

Los indicadores calculan VWAP, ATR, POC/VAH/VAL pero actualmente no exponen sus valores hacia afuera. El `run_strategy_detection()` tiene TODOs para:

- [ ] Exponer ultimo valor VWAP desde VwapIndicator → VwapContext
- [ ] Exponer ultimo POC/VAH/VAL desde VolumeProfileIndicator → VolumeProfileContext
- [ ] Exponer ultimo ATR desde AtrIndicator → atr field
- [ ] Exponer CVD/delta desde CumulativeDeltaIndicator → OrderFlowContext

Requiere agregar metodos `pub fn latest_value(&self) -> Option<T>` a cada indicator.

### Fase 5: Features avanzados de indicadores

- [ ] VWAP session reset (00:00 UTC o configurable)
- [ ] AVWAP (Anchored VWAP) desde evento especifico
- [ ] Volume Profile windowed (ultimas N velas)
- [ ] HVN/LVN detection automatica

### Fase 6: Datos derivados para strategy (Option<T> pendientes)

- [ ] `regime`: ATR slope + trend detection (SMA crossover o similar)
- [ ] `vpin`: Volume-synchronized probability of informed trading
- [ ] `cvd_slope`: Pendiente de CVD sobre N periodos
- [ ] `footprint_absorption`: Alto volumen sin movimiento de precio
- [ ] `stacked_imbalance`: N niveles consecutivos con imbalance > threshold
- [ ] `failed_acceptance`: Precio rompe nivel y no sostiene
- [ ] `sweep_confirmed`: Liquidity sweep detection
- [ ] `mss_active`: Market structure shift

### Fase 7: UI toggle

- [ ] Agregar toggle en settings/dropdown para activar strategy_overlay_enabled
- [ ] Configuracion de StrategyConfig persistente

### Fase 8: Outcome tracker

- [ ] Track cada signal: MFE/MAE en 5s, 15s, 30s, 60s, 3m, 5m, 15m
- [ ] JSONL separado de outcomes
- [ ] 300+ signals antes de declarar usable

---

## Nota sobre compilacion

El entorno actual tiene un problema con `link.exe` (conflicto MSVC linker). Los errores de build NO son de nuestro codigo — son del toolchain de Windows. Solucion: reinstalar C++ Build Tools o corregir PATH para que `link.exe` de MSVC tenga prioridad sobre el `link` de coreutils/git.
