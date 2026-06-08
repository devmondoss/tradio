# FlowSurface — Estado del Proyecto + Checklist por Capas

*Última revisión: 2026-05-29 — sesión 6 (UI local con DRR range overlay + HUD panel, DB completamente migrada, sistema en acumulación activa)*

---

## Capa 1 — Exchange / Transport

| Estado | Item |
|--------|------|
| ✅ | 5 exchanges implementados (Binance activo, Bybit/OKEx/MEXC/Hyperliquid disponibles) |
| ✅ | WebSocket streams: Klines, Depth, Trades, ForceOrder (liquidaciones) |
| ✅ | REST feeds: OI, Funding, L/S ratios, Taker ratio (polling 60s/300s) |
| ✅ | Deduplicación de streams entre panes |
| ✅ | Depth incremental con re-sync REST ante gap detectado |
| ✅ | Stream ForceOrder reconexión robusta — loop con retry 5s |
| ✅ | Health tracking de streams en cada `[bar]` log |
| ✅ | `liq_age` distinción quiet vs caído |

---

## Capa 2 — Domain / Data

### Indicadores

| Estado | Item |
|--------|------|
| ✅ | VWAP (reset 00:00 UTC, ±1σ/±2σ, AVWAP BOS) |
| ✅ | Volume Profile (150 bins, 300-bar window, POC/VAH/VAL/HVN/LVN) |
| ✅ | CVD + slope OLS |
| ✅ | ATR(14) Wilder smoothing |
| ✅ | OI Delta intrabar + OI Z-Score rolling 20-bar |
| ✅ | Footprint (delta por nivel, `footprint_levels: Vec<FootprintLevel>`) |
| ✅ | Session VWAPs Asia/London/NY |
| ✅ | `stacked_imbalance` via `derive_stacked_imbalance()` |
| ✅ | Order Block Detector — wired en monitor con datos reales |
| ✅ | FVG Detector — wired en monitor |
| ✅ | Market Structure (BOS/CHoCH) — wired en monitor |
| ✅ | `DailyVpBias` (VP Open Bias) — 4 variantes, wired en monitor |
| ✅ | Finish Action / Unfinish Action — derivados del footprint |
| ✅ | Big Trades — nivel >2.5× avg, `big_trade_bullish/bearish` |
| ✅ | Naked POC — `NakedPocTracker` 10 sesiones |
| ✅ | HTF VP cascade — `HtfVpTracker` weekly + monthly |
| ✅ | **RangeDetector** — rango intradía de price action (80 barras, slope/size/touches) ✅ 2026-05-29 |

### Trackers Institucionales

| Estado | Item |
|--------|------|
| ✅ | FundingTracker (rate, 7d avg, velocity, regime, peak_confirmed) |
| ✅ | OiTracker (delta, z-score) |
| ✅ | LsRatioTracker (top traders vs retail) |
| ✅ | LiquidationTracker (USD por lado, ventanas 5m/60s) |
| ✅ | LiqMapTracker (densidad de stops estimada, half-life decay 4h) |

---

## Capa 3 — Monitor (Event Loop M5)

### Pipeline

| Estado | Item |
|--------|------|
| ✅ | Warm-up: fetch 50 klines históricas al startup |
| ✅ | Warm-up: 21 funding rates (7 días) para FundingTracker |
| ✅ | Warm-up: inicializa `current_candle_open_ms` + `micro_buffer` ✅ 2026-05-29 |
| ✅ | Regime con hysteresis (±0.15 exit, ±0.10 entry) |
| ✅ | OLS slope 14+5 bars |
| ✅ | StrategyMarketContext completo → router |
| ✅ | Atomic write de estado (`.tmp` + rename) |
| ✅ | CVD hard gate — `CVD_MACRO_VETO_CAP=0.35` |
| ✅ | **`CandleMicroBuffer`** — acumula trades por slots de 15s, build_row() en bar close ✅ 2026-05-29 |

### Router

| Estado | Item |
|--------|------|
| ✅ | Winner-takes-all, score mínimo 0.60 |
| ✅ | Cooldown por (strategy, side) — `cooldown_bars = 5` |
| ✅ | Tiebreak determinístico |
| ✅ | AuctionState gate |
| ✅ | VP Bias gate — TrendDay bloquea reversales contrarios |
| ✅ | **DRR-only mode** — `subdimi_detector_allowed()` solo permite `DeltaRangeReversal` ✅ 2026-05-29 |

### Scoring

| Estado | Item |
|--------|------|
| ✅ | Base score: CVD slope (25%), taker imbalance (20%), delta (10%), target ATR dist (25%), R:R (20%) |
| ✅ | Multiplicadores: VPIN, spread, regime, confluencia, HTF structure, smart money |
| ✅ | Vetos: VPIN tóxico → cap 0.25; CVD macro ≥3 barras → cap 0.35 |
| ✅ | `factor_vp_bias` — ×1.15 alineado / ×0.75 contrario |
| ✅ | `stacked_imbalance` bonus +0.04 |
| ✅ | `finish_action` bonus +0.05 |
| ✅ | `unfinish_action` penalty -0.04 |

---

## Capa 4 — Estrategias

### Arquitectura actual (2026-05-29)

```
Core (router winner-takes-all)
  └── DeltaRangeReversal (DRR) — único detector live
       VAFA, FAR, LVN, CDR, OBR, LIQ, VWAP, DIB, SOB, FER, SMD
       → SUBDIMI_ONLY_DISABLED (en código, no compiten)

Subdimi Parallel (observación sin paper trading)
  └── VAFA, FAR, LVN, CDR, OBR, LIQ — 6 detectores en paralelo
       → lab_signals (maturity = 'SubdimiParallel')
       → sin paper trades, sin outcome tracking

Lab
  └── Eliminado ✅ 2026-05-29
```

### DeltaRangeReversal (DRR) — estrategia única Live

| Estado | Componente |
|--------|-----------|
| ✅ | **RangeDetector** — `data/src/detectors/range_detector.rs` — ventana 80 barras, 5 slots seleccionados por anchor, validación slope/size/touches ✅ 2026-05-29 |
| ✅ | **DRR detector** — `data/src/strategy/detectors/delta_range_reversal.rs` — 2 setups (LONG en NearLow/OutsideLow, SHORT en NearHigh/OutsideHigh), no-trade zone 35-65% bloqueada ✅ 2026-05-29 |
| ✅ | **RangeContext** en `StrategyMarketContext` — range_high/low/mid, touches, sweep, breakout ✅ 2026-05-29 |
| ✅ | **`sweep_low_depth` / `sweep_high_depth`** — profundidad del sweep en RangeContext ✅ 2026-05-29 |
| ✅ | **Router DRR-only** — único detector en `subdimi_detector_allowed()` ✅ 2026-05-29 |
| ✅ | **PlaybookReasoning** actualizado con variante `DeltaRangeReversal` ✅ 2026-05-29 |
| ✅ | **Cooldown** actualizado — key 11 para DRR ✅ 2026-05-29 |
| ✅ | **RangeDetector wired** en monitor (on_trade, on_bar_close, warmup) + kline.rs (UI) ✅ 2026-05-29 |
| 🔬 | DRR acumulando señales — meta 100+ para primera evaluación |

### Subdimi Parallel (ex-Lab)

| Estado | Componente |
|--------|-----------|
| ✅ | `data/src/strategy/subdimi_parallel.rs` — `run_subdimi_parallel()` corre 6 detectores sin winner-takes-all ✅ 2026-05-29 |
| ✅ | Señales van a `lab_signals` con `maturity = 'SubdimiParallel'` ✅ 2026-05-29 |
| ✅ | Lab module eliminado (10 archivos borrados) ✅ 2026-05-29 |

### Detectores en código (no compiten en router live)

| Detector | Estado en código | Motivo de bloqueo |
|----------|-----------------|-------------------|
| VAFA | ✅ implementado | `SUBDIMI_ONLY_DISABLED` |
| FAR | ✅ implementado | `SUBDIMI_ONLY_DISABLED` |
| LVN | ✅ implementado | `SUBDIMI_ONLY_DISABLED` |
| CDR | ✅ implementado | `SUBDIMI_ONLY_DISABLED` |
| OBR | ✅ implementado | `SUBDIMI_ONLY_DISABLED` |
| LIQ | ✅ implementado | `SUBDIMI_ONLY_DISABLED` |
| VWAP | ✅ implementado | `SUBDIMI_ONLY_DISABLED` |
| DIB | ✅ implementado | `SUBDIMI_ONLY_DISABLED` |
| SOB | ✅ implementado | `SUBDIMI_ONLY_DISABLED` |
| FER | ✅ implementado | `SUBDIMI_ONLY_DISABLED` |
| SMD | ✅ implementado | `SUBDIMI_ONLY_DISABLED` |

### Trade Manager / Paper Engine

| Estado | Item |
|--------|------|
| ✅ | Sizing por riesgo (1% = $3/trade), leverage 10× |
| ✅ | Fees (0.04% taker × 2), slippage (1bp × 2), funding acumulado |
| ✅ | Stop state machine (STOP_HIT / TARGET_HIT / TTL / INVALIDATED) |
| ✅ | Progress-to-target con `.max(0.0)` por Side |
| ✅ | Atomic persist: `paper_account_state.json` + `paper_trades.jsonl` |
| ✅ | Funding multi-período — `periods_crossed` aplicado |

---

## Capa 4.5 — Captura de Datos para Calibración

### Schema Supabase — 30 campos nuevos en `shadow_signals` (2026-05-29)

| Bloque | Campos | Pregunta que responde |
|--------|--------|----------------------|
| **1 — Tiempo/sesión** | `session_name`, `session_phase`, `hour_utc`, `day_of_week`, `minutes_since_session_open` | ¿Cuándo tiene mejor edge DRR? |
| **2 — Calidad del rango** | `range_midline_slope`, `range_bars_inside`, `range_second_test`, `range_vs_value_area` | ¿Qué tipo de rango funciona? |
| **3 — Absorción** | `absorption_count`, `entry_type`, `sweep_depth_atr`, `delta_at_extreme`, `bar_volume` | ¿Cuántas señales simultáneas mejoran el edge? |
| **4 — Precio/estructura** | `value_location`, `price_vs_vwap`, `price_vs_avwap_bos`, `naked_poc_in_target_path`, `hvn_between_entry_target`, `fast_slope_at_entry` | ¿El contexto estructural importa? |
| **5 — Institucional** | `oi_direction`, `cvd_divergence_persistence`, `vpin`, `funding_velocity` | ¿El dinero grande acompaña? |
| **6 — Calidad del trade** | `rr_actual`, `distance_to_target_atr`, `distance_to_stop_atr`, `obstacle_hvn_count`, `nearest_naked_poc_dist_atr` | ¿El setup predice si llega al target? |

### Micro-ventana (2026-05-29)

| Estado | Item |
|--------|------|
| ✅ | **`CandleMicroBuffer`** — 20 slots × 15s por vela M5, selección de 5 según anchor ✅ 2026-05-29 |
| ✅ | **Shape features** — `late_surge_ratio`, `delta_slope_norm`, `delta_flip_bucket`, `vol_trajectory`, `absorption_proxy`, `delta_accel`, `monotonic_delta`, `price_path_eff` ✅ 2026-05-29 |
| ✅ | **DRR context** — `reclaimed`, `reclaim_bucket`, `sweep_depth_atr` calculados por vela ✅ 2026-05-29 |
| ✅ | **`micro_windows` table** en Supabase — 288 rows/día, JOIN por `candle_open_ms` con `shadow_signals.timestamp_ms` ✅ 2026-05-29 |
| ✅ | **Wiring completo** — `on_trade()` → acumula, `on_bar_close()` → mark_trigger + build_row + reset ✅ 2026-05-29 |
| ✅ | **`v_micro_with_outcomes`** — vista que une micro_windows + shadow_signals + signal_outcomes ✅ 2026-05-29 |

### Migración SQL

| Estado | Archivo |
|--------|---------|
| ✅ | `supabase/migration_drr.sql` — 30 columnas DRR + reasoning + 5 índices + vista actualizada ✅ ejecutado 2026-05-29 |
| ✅ | Migracion `micro_windows` — tabla `micro_windows` + vista `v_micro_with_outcomes` ejecutadas en Supabase SQL Editor ✅ 2026-05-29 |
| ✅ | `v_micro_with_outcomes` recreada con `DROP + CREATE` — incluye session_name, absorption_count, entry_type ✅ 2026-05-29 |
| ✅ | DB limpieza completa — datos pre-DRR borrados (13 señales viejas), sistema arranca limpio |
| ✅ | `micro_windows` acumulando — verificado 2 rows a los 23 min del primer deploy |

---

## Capa 5 — UI (iced)

### Chart / Canvas

| Estado | Item |
|--------|------|
| ✅ | Panes: Kline (Candles/Footprint), Heatmap, ShaderHeatmap, DOM, T&S, Comparison |
| ✅ | Overlay: VWAP + bands, Volume Profile histogram |
| ✅ | Key Levels (PDH/PDL/DO/WO) con tooltip hover |
| ✅ | Session Lines (Asia/London/NY rectángulos pasteles) |
| ✅ | Strategy overlay: Entry/Stop/Target con shaded zone, TTL expiration filter |
| ✅ | NaN guards en todas las coordenadas |
| ✅ | OI Delta barras verde/rojo |
| ✅ | Order Block overlay |
| ✅ | **RangeDetector wired en UI** — `bootstrap_detectors()` + `on_kline_closed()` ✅ 2026-05-29 |
| ✅ | **`draw_drr_range()`** — Range High/Low/Mid líneas dashed naranjas, no-trade zone sombreada (35-65%), triángulos de sweep, labels RH/RL/T ✅ 2026-05-29 |
| ✅ | **`draw_drr_hud()`** — Panel HUD top-right: Regime (coloreado), Session/Phase, VPBias, AuctionState, Range state, Sweep, Absorb N/5 [F.B.C.X.S], CVD slope ✅ 2026-05-29 |
| ✅ | **`DrrHudState`** — Snapshot del ctx en cada bar close para el panel HUD ✅ 2026-05-29 |

---

## Capa 6 — Infraestructura & DevOps

| Estado | Item |
|--------|------|
| ✅ | Railway: Dockerfile Rust 1.95-slim + debian:trixie |
| ✅ | `#![recursion_limit = "512"]` — fix build error con json! de 133 campos ✅ 2026-05-29 |
| ✅ | Local: GNU toolchain (MinGW-w64 POSIX.UCRT), `run.bat` |
| ✅ | Supabase (Railway) dual-write |
| ✅ | Config hot-reload: `config/strategy.toml` + MongoDB |
| ✅ | **DB limpieza** — todos los datos pre-DRR borrados 2026-05-29 — datos limpios desde deploy |

---

## Acumulación de Datos / Calibración

| Estado | Item |
|--------|------|
| 🔬 | DRR: acumulando señales — meta **100+** para primera evaluación de edge |
| 🔬 | Subdimi Parallel: acumulando en `lab_signals` — para comparación contra DRR |
| 🔬 | Micro-windows: acumulando — **288 rows/día** — útil con ≥150 señales cerradas con R |
| ❌ | Sin resultados DRR aún — sistema en acumulación activa desde 2026-05-29 |

### Queries de primer análisis (cuando haya ≥50 señales cerradas)

```sql
-- ¿Qué sesión tiene mejor edge?
SELECT session_name, session_phase,
       COUNT(*) FILTER (WHERE o.close_reason = 'TARGET_HIT') AS wins,
       COUNT(*) AS total
FROM shadow_signals s LEFT JOIN signal_outcomes o ON o.signal_id = s.id
WHERE s.strategy = 'DeltaRangeReversal'
GROUP BY session_name, session_phase ORDER BY wins::float/NULLIF(total,0) DESC;

-- ¿Absorción alta vs baja — diferencia de win rate?
SELECT absorption_count, COUNT(*) AS n,
       AVG(o.r_multiple) AS avg_r
FROM shadow_signals s LEFT JOIN signal_outcomes o ON o.signal_id = s.id
WHERE s.strategy = 'DeltaRangeReversal' AND o.is_partial = FALSE
GROUP BY absorption_count ORDER BY absorption_count;

-- ¿Sweep reclaim vs near_extreme?
SELECT entry_type, AVG(o.r_multiple) AS avg_r, COUNT(*) AS n
FROM shadow_signals s LEFT JOIN signal_outcomes o ON o.signal_id = s.id
WHERE s.strategy = 'DeltaRangeReversal' AND o.is_partial = FALSE
GROUP BY entry_type;

-- ¿Velas back-loaded en zona DRR tienen mejor win rate?
SELECT surge_bucket, outcome_bucket, COUNT(*) AS n
FROM v_micro_with_outcomes
WHERE in_drr_zone = TRUE AND side IS NOT NULL
GROUP BY surge_bucket, outcome_bucket ORDER BY surge_bucket;
```

---

## Resumen Ejecutivo — 2026-05-29

El sistema está en **producción activa con DRR como estrategia única**. La infraestructura de captura está completa — cada vela emite datos de micro-dinámica y cada señal DRR registra 30 campos contextuales. El objetivo ahora es **acumular 100+ señales cerradas con R** para la primera evaluación estadística de edge.

**Lo único que falta no es código — es tiempo de mercado.**

| Milestone | Condición |
|-----------|-----------|
| Primera evaluación de edge | 100+ señales DRR cerradas con R |
| Calibración de parámetros del rango | 50+ señales por session_name |
| Análisis micro-ventana | 150+ señales + micro_windows con R |
| Decisión de continuar DRR-only o restaurar paralelo al Core | 300+ señales DRR |

---

*Actualizado 2026-05-29 — sesión 5: DRR live, micro-ventana conectada, DB limpia, sistema en acumulación activa*
