# Flowsurface — inkamaia19's fork

[![Made with iced](https://iced.rs/badge.svg)](https://github.com/iced-rs/iced)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](./LICENSE)

A personal fork of [flowsurface-rs/flowsurface](https://github.com/flowsurface-rs/flowsurface) — a native desktop charting application for crypto markets built with Rust and iced.

This fork extends the upstream project with a full suite of microstructural indicators and a live strategy detection engine running on Railway.

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
| Relative Volume | Sub-panel | Current candle volume / mean of last 50 candles; directional coloring |
| ATR | Sub-panel | Average True Range, 14-period Wilder smoothing |
| Open Interest | Sub-panel | Perpetuals OI fetched from exchange REST API |
| OI Delta | Sub-panel | Candle-by-candle OI change (green = new longs/shorts, red = deleveraging) |

---

### Strategy engine (live on Railway)

As of 2026-06-02 the monitor runs two engines on M1 BTCUSDT bars:

#### Scalping S1/S2/S3

Three orderflow strategies (circuit breaker currently active):

| Strategy | Logic |
|----------|-------|
| S1 OBI Maker | Order book imbalance bias → post-only maker entry |
| S2 Absorption | Delta Z-score + volume ratio at range extreme → reversal |
| S3 CVD Divergence | Price swing without CVD confirmation → fade |

Sessions: London (07:00–10:00 UTC), NY (13:00–17:00 UTC). Stop: ~0.25%, Target: ~0.5–0.7%.

#### RangeBreakoutFlow (pending deploy)

Validated on 30-day M1 backtest (43,200 bars, n=914 signals with aligned CVD):

> A consolidation range (0.08–0.55% of price, 15–60 M1 bars) where CVD accumulates pressure in one direction, followed by a close outside the range with VR ≥ 2×, produces positive edge.

| Config | n | Hold | Win rate | Exp/trade |
|--------|---|------|---------|----------|
| SHORT breakdown + CVD bearish + VR≥2 | 187 | +60min | 13.9% | +0.061% |
| LONG breakout + CVD bullish (counter-trend) | 196 | +30min | 12.8% | +0.093% |

**Pipeline per M1 bar close:**

```
closed M1 bar
  → Scalping engine (S1/S2/S3)
      ├─ OBI, micro-price, CVD, DZ, VR, absorption scores
      ├─ Session filter (London / NY only)
      ├─ Circuit breaker (3 consecutive losses)
      └─ write scalping_bars (always) + scalping_signals (on fire)

  → RangeBreakoutFlow
      ├─ Rolling 15–60 bar range detection (0.08–0.55% size)
      ├─ CVD accumulation direction during range
      ├─ Breakout bar: VR ≥ 2×, close outside range
      ├─ EMA480 macro regime filter
      └─ write rbf_signals (on fire)
```

---

### Supabase data (Railway dual-write)

| Table | Description |
|-------|-------------|
| `scalping_bars` | One row per M1 bar — DZ, VR, CVD, OBI, signal_fired, blocked_by |
| `scalping_signals` | S1/S2/S3 signals with full orderflow context |
| `scalping_trades` | Closed paper trades with PnL, MFE, MAE |
| `rbf_signals` | RangeBreakoutFlow signals (pending migration) |
| `regime_history` | Regime change log |
| `micro_windows` | 5×15s micro-dynamic buckets per M5 candle |

---

## Building on Windows

This project requires the **GNU toolchain** (not MSVC).

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

See [`docs/BUILD.md`](docs/BUILD.md) for full details.

---

## Documentation

Start with [`docs/README.md`](docs/README.md).

| File | Contents |
|------|----------|
| [`docs/DRR_PRESENTE_Y_FUTURO.md`](docs/DRR_PRESENTE_Y_FUTURO.md) | Canonical current/future state — read first every session |
| [`docs/ESTADO_CHECKLIST.md`](docs/ESTADO_CHECKLIST.md) | Technical checklist by system layer |
| [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md) | App architecture: Elm model, component hierarchy, message flow |
| [`docs/DATOS.md`](docs/DATOS.md) | Data flow: exchange types, WebSocket → chart, HTTP fetch |
| [`docs/CHARTS.md`](docs/CHARTS.md) | All chart types: Kline, Heatmap, Footprint, DOM, Time&Sales |
| [`docs/INDICATORS.md`](docs/INDICATORS.md) | All indicators: formulas, structs, constants, trait methods |
| [`docs/BUILD.md`](docs/BUILD.md) | Build setup, toolchain, MinGW, PowerShell recipe |
| [`docs/BUGS_Y_FIXES.md`](docs/BUGS_Y_FIXES.md) | All resolved bugs with root cause and fix |

---

## License

GPL v3 — same as the upstream project.

Upstream: [flowsurface-rs/flowsurface](https://github.com/flowsurface-rs/flowsurface)
