# Flowsurface — inkamaia19's fork

[![Made with iced](https://iced.rs/badge.svg)](https://github.com/iced-rs/iced)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](./LICENSE)

A personal fork of [flowsurface-rs/flowsurface](https://github.com/flowsurface-rs/flowsurface) — a native desktop charting application for crypto markets built with Rust and iced.

This fork extends the upstream project with a full suite of microstructural indicators and a shadow-only strategy detection engine for discretionary trade analysis.

---

## What this fork adds

### Indicators

| Indicator | Panel | Description |
|-----------|-------|-------------|
| VWAP | Overlay on kline | Session VWAP (UTC daily reset) with ±1σ / ±2σ bands and auto AVWAP BOS |
| Volume Profile (VRVP) | Overlay on kline | 150-bin histogram adapting to visible range; POC / VAH / VAL / HVN / LVN coloring |
| Session lines | Overlay on kline | Dashed vertical lines at Asia / London / NY session opens (≤4h timeframes) |
| Key levels | Overlay on kline | PDH, PDL, Daily Open, Weekly Open as labeled dashed horizontal lines |
| CVD (Cumulative Volume Delta) | Sub-panel | Cumulative delta with UTC session reset; OLS slope, VPIN, divergence detection |
| Relative Volume | Sub-panel | Current candle volume / mean of last 20 candles; directional coloring |
| ATR | Sub-panel | Average True Range, 14-period Wilder smoothing |
| Open Interest | Sub-panel | Perpetuals OI fetched from exchange REST API |
| OI Delta | Sub-panel | Candle-by-candle OI change (green = new longs/shorts, red = deleveraging) |

### Strategy detection engine (shadow-only)

An 8-phase microstructural analysis pipeline that detects high-probability setups and logs them for backreview — no orders are ever placed.

**3 detectors:**

| Detector | Logic summary |
|----------|---------------|
| `LvnLiquidityVacuumBreakout` | Price at LVN + aggressive CVD + trend regime → breakout continuation |
| `AbsorptionReversal` | Failed acceptance at VAH/VAL + divergent delta + absorption side → reversal |
| `TrendContinuationPullback` | Trend regime + pullback to VWAP/POC + CVD alignment → continuation entry |

**Pipeline per depth update:**

```
depth update
  → build StrategyMarketContext
      ├─ flow:    CVD slope, VPIN, stacked imbalance, absorption side
      ├─ vwap:    latest VWAP, bands, AVWAP BOS anchor
      ├─ vol_profile: POC, VAH, VAL, HVN list, LVN list
      ├─ regime:  OLS regression on last 20 closes, normalized by ATR
      └─ positioning: OI, OI delta, failed acceptance flag
  → toxic flow gate (skip if data insufficient)
  → run detectors → score signals (0.0–1.0)
  → shadow signals: render overlay + log to JSONL
  → OutcomeTracker: track MFE/MAE until target/stop/TTL
```

**Toggle:** the `⭐` button in any kline chart toolbar activates/deactivates the overlay.

### Outcome tracking

Every signal is tracked in real time until it closes. Results are written to:

```
%APPDATA%\Roaming\flowsurface\shadow_events\
├── strategy_signals.jsonl       — signal/block events at detection time
├── strategy_outcomes.jsonl      — legacy MFE/MAE tracker outcomes
├── paper_trades.jsonl           — closed paper-trading positions with PnL/costs
├── contradictions.jsonl         — ignored opposite-side signals per symbol
└── paper_account_state.json     — persisted paper account/open positions
```

Fields tracked per outcome: `mfe`, `mae`, `mfe_r`, `mae_r` (in price units and R-multiples).

---

## Original features (upstream)

- **Heatmap (Historical DOM):** L2 orderbook + trades as a time-series heatmap with configurable price grouping
- **Candlestick:** Time-based and tick-based kline charts
- **Footprint:** Trade clustering over candlesticks with imbalance and naked-POC studies
- **Time & Sales:** Scrollable live trade feed
- **DOM / Ladder:** L2 orderbook with grouped price levels
- **Comparison chart:** Normalized multi-asset line graph
- Exchange support: Binance, Bybit, Hyperliquid, OKX, MEXC
- Persistent layouts, customizable themes, pane linking, multi-monitor support

---

## Building on Windows

This project requires the **GNU toolchain** (not MSVC) on Windows because Git ships its own `link.exe` which breaks MSVC builds.

**Prerequisites:**

```powershell
# Install MinGW-w64
winget install BrechtSanders.WinLibs.POSIX.UCRT

# Set toolchain override (one-time, per project dir)
rustup override set stable-x86_64-pc-windows-gnu
```

**Run:**

```bat
./run.bat
```

`run.bat` sets the MinGW PATH, enables `RUST_BACKTRACE=1`, and calls `cargo run`.

See [`docs/BUILD.md`](docs/BUILD.md) for full details including the `cargo check` recipe.

---

## Documentation

| File | Contents |
|------|----------|
| [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md) | App architecture: Elm model, component hierarchy, message flow, persistence |
| [`docs/DATOS.md`](docs/DATOS.md) | Data flow: exchange types, WebSocket → chart, HTTP fetch, aggregation |
| [`docs/CHARTS.md`](docs/CHARTS.md) | All chart types: Kline, Heatmap, Footprint, DOM, Time&Sales, Comparison |
| [`docs/RENDERING.md`](docs/RENDERING.md) | KlineChart rendering: coordinates, cache, scroll/zoom, overlays |
| [`docs/INDICATORS.md`](docs/INDICATORS.md) | All indicators: formulas, structs, constants, trait methods |
| [`docs/STRATEGY.md`](docs/STRATEGY.md) | Full strategy pipeline: context, detectors, scoring, adapter, tracker |
| [`docs/STRATEGY_AUDIT.md`](docs/STRATEGY_AUDIT.md) | Current audit of strategy, paper trading, and remaining implementation gaps |
| [`docs/BUILD.md`](docs/BUILD.md) | Build setup, toolchain, MinGW, PowerShell recipe |
| [`docs/BUGS_Y_FIXES.md`](docs/BUGS_Y_FIXES.md) | All resolved bugs with root cause and fix |
| [`docs/PENDIENTE.md`](docs/PENDIENTE.md) | Planned improvements and future indicators |

---

## License

GPL v3 — same as the upstream project.

Upstream: [flowsurface-rs/flowsurface](https://github.com/flowsurface-rs/flowsurface)
