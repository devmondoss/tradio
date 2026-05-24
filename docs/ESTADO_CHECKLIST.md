# FlowSurface — Estado del Proyecto + Checklist por Capas

*Última revisión: 2026-05-24 — sesión 4 (Delta Drain rate cerrado — metodología Subdimi 100% completa)*

---

## Capa 1 — Exchange / Transport

| Estado | Item |
|--------|------|
| ✅ | 5 exchanges implementados (Binance activo, Bybit/OKEx/MEXC/Hyperliquid disponibles) |
| ✅ | WebSocket streams: Klines, Depth, Trades, ForceOrder (liquidaciones) |
| ✅ | REST feeds: OI, Funding, L/S ratios, Taker ratio (polling 60s/300s) |
| ✅ | Deduplicación de streams entre panes |
| ✅ | Depth incremental con re-sync REST ante gap detectado |
| ✅ | Stream ForceOrder reconexión robusta — loop con retry 5s en fallo de connect, read error y Close frame |
| ✅ | Health tracking de streams: `ws=[kline:ok\|disc\|recon depth:ok liq:ok\|disc]` en cada `[bar]` log |
| ✅ | `liq_age` distinción quiet vs caído: `ws=[liq:ok]`+`liq_age` = quieto; `ws=[liq:disc]` = caído; `!forceOrder@arr` confirma via `liq_global_raw` en `[metrics]` |

---

## Capa 2 — Domain / Data

### Indicadores

| Estado | Item |
|--------|------|
| ✅ | VWAP (reset 00:00 UTC, ±1σ/±2σ, AVWAP con auto-anchor en BOS) |
| ✅ | Volume Profile (150 bins, 300-bar window, POC/VAH/VAL/HVN/LVN) |
| ✅ | CVD + slope OLS |
| ✅ | ATR(14) con Wilder smoothing |
| ✅ | OI Delta (intrabar) + OI Z-Score (rolling 20-bar window, seed en bar 1) |
| ✅ | Footprint (delta por nivel de precio, `footprint_levels: Vec<FootprintLevel>`) |
| ✅ | Session VWAPs Asia/London/NY |
| ✅ | `stacked_imbalance` — `derive_stacked_imbalance()` llamado en monitor |
| ✅ | Order Block Detector — wired en monitor con datos reales |
| ✅ | FVG Detector — wired en monitor con datos reales |
| ✅ | Market Structure (BOS/CHoCH) — wired en monitor con datos reales |
| ✅ | `DailyVpBias` (VP Open Bias) — 4 variantes, `DailyVpTracker` wired en monitor y `StrategyMarketContext` |
| ✅ | **Finish Action** — `finish_action_bullish/bearish` en `OrderFlowContext`; `derive_finish_unfinish_action()` en adapter; wired en FAR + VAFA evidence; +0.05 scoring bonus |
| ✅ | **Unfinish Action** — `unfinish_action_bullish/bearish` en `OrderFlowContext`; wired en FAR + VAFA missing; -0.04 scoring penalty |
| ✅ | **Big Trades** — `big_trade_bullish/bearish` en `OrderFlowContext`; `derive_big_trade()` en adapter; nivel >2.5× avg; FAR evidence; +0.06 scoring |
| ✅ | **Naked POC** — `NakedPocTracker` 10 sesiones + `BONUS_NAKED_POC_MAGNET` en scoring |
| ✅ | **HTF VP cascade** — `HtfVpTracker` weekly + monthly, `factor_htf_vp` en scoring |

### Trackers Institucionales

| Estado | Item |
|--------|------|
| ✅ | FundingTracker (rate, 7d avg, velocity, regime, peak_confirmed) |
| ✅ | OiTracker (delta, z-score) |
| ✅ | LsRatioTracker (top traders vs retail divergencia) |
| ✅ | LiquidationTracker (USD por lado, ventanas 5m/60s) |
| ✅ | LiqMapTracker (densidad de stops estimada, half-life decay 4h) |

---

## Capa 3 — Monitor (Event Loop M5)

### Pipeline

| Estado | Item |
|--------|------|
| ✅ | Warm-up: fetch 50 klines históricas al startup |
| ✅ | Warm-up: 21 funding rates (7 días) para FundingTracker |
| ✅ | Regime con hysteresis (±0.15 exit, ±0.10 entry) |
| ✅ | OLS slope 14+5 bars |
| ✅ | StrategyMarketContext completo → router |
| ✅ | Atomic write de estado (`.tmp` + rename) |
| ✅ | Logging bar: entrega, proc time, equity, inst=Live(N/M) |
| ✅ | CVD hard gate — `CVD_MACRO_VETO_CAP=0.35` en scoring.rs cuando persistence ≥ 3 barras |
| ✅ | `stacked_imbalance` calculado en monitor |
| ✅ | Contexto order_blocks / FVG / market_structure wired con datos reales |

### Router

| Estado | Item |
|--------|------|
| ✅ | Winner-takes-all, score mínimo 0.60 (institucional 0.55) |
| ✅ | Cooldown por (strategy, side) — `cooldown_bars = 5` |
| ✅ | Tiebreak determinístico (`strategy_id as u8`) |
| ✅ | AuctionState gate — bloquea continuación en Distribution/Accumulation/Imbalance |
| ✅ | **VP Bias gate** — TrendDay bloquea reversales contrarios; InsideValue bloquea breakouts |

### Scoring

| Estado | Item |
|--------|------|
| ✅ | Base score: CVD slope (25%), taker imbalance (20%), delta (10%), target ATR dist (25%), R:R (20%) |
| ✅ | Multiplicadores: VPIN, spread, regime, confluencia, HTF structure, smart money, opening rush |
| ✅ | Vetos: VPIN tóxico → cap 0.25; CVD macro ≥3 barras → cap 0.35 |
| ✅ | Bonuses aditivos: funding penalty, OI bonus, wall bonus, clean action, zone HTF |
| ✅ | **`factor_vp_bias`** — ×1.15 alineado con tipo de día / ×0.75 contrario (TrendDay) |
| ✅ | **`stacked_imbalance` bonus** — +0.04 cuando FBG alineada con dirección de señal |
| ✅ | **`finish_action` bonus** — +0.05 cuando finish_action confirma exhaustión en dirección de señal |
| ✅ | **`unfinish_action` penalty** — -0.04 cuando imán adverso en extremo contrario |

---

## Capa 4 — Estrategias

### Metodología Subdimi — Mapeo completo

| Estado | Patrón Subdimi | Detector / Mecanismo |
|--------|---------------|----------------------|
| ✅ | Absorción / Traders Atrapados | `FootprintAbsorptionReversal` (FAR) |
| ✅ | Delta Drain en VAH/VAL (failed auction) | `ValueAreaFailedAuction` (VAFA) |
| ✅ | Toma de Liquidez + Volumen | `LiquidationHunt` (LIQ) |
| ✅ | CVD Divergence (persistence ≥4 barras) | `CvdDivergenceReversal` (CDR) |
| ✅ | Order Block Retest | `OrderBlockRetest` (OBR) |
| ✅ | VWAP Pullback en tendencia | `VwapValuePullbackContinuation` (VWAP) |
| ✅ | LVN Volume Gap / vacío de liquidez | `LvnLiquidityVacuumBreakout` (LVN) |
| ✅ | Session Open Breakout | `SessionOpenBreakout` (SOB) |
| ✅ | DOM Imbalance Breakout | `DomImbalanceBreakout` (DIB) |
| ✅ | Smart Money Divergence (L/S ratio) | `SmartMoneyDivergence` (SMD) |
| ✅ | Funding Exhaustion (carry insostenible) | `FundingExhaustionReversal` (FER) |
| ✅ | VP Open Bias — 4 variantes de apertura | `vp_open_bias.rs` + gate + scoring |
| ✅ | min R:R 2:1 | `min_rr = 2.0` en `config/strategy.toml` |
| ✅ | VPIN / flujo tóxico gate | `toxic_flow_gate.rs` |
| ✅ | **Finish Action** (bid=0 / ask=0 en extremo) | `finish_action_bullish/bearish` → FAR + VAFA evidence + scoring +0.05 |
| ✅ | **Unfinish Action** (bid/ask incompleto = imán) | `unfinish_action_bullish/bearish` → FAR + VAFA missing + scoring -0.04 |
| ✅ | **Big Trades** (órdenes institucionales visibles) | `big_trade_bullish/bearish` → nivel >2.5× avg vol → FAR evidence + scoring +0.06 |
| ✅ | **HTF VP cascade** (mensual → semanal → diario) | `HtfVpTracker` weekly + monthly, `factor_htf_vp` en scoring |
| ✅ | **Naked POC** como target/imán explícito | `NakedPocTracker` 10 sesiones + `BONUS_NAKED_POC_MAGNET` en scoring |
| ✅ | **Stacked Imbalances** detector propio | `derive_fbg_imbalance()` (one-side=0) → cascada FBG → delta-levels → delta-bars |
| ✅ | **OB volume ratio** threshold | Subido a 3.0x (`ob_volume_ratio_gt_3x`), 1.5x como evidencia secundaria |
| ✅ | **Funding multi-período** | `periods_crossed = curr_period - prev_period` en `apply_funding_if_crossed` |
| ✅ | **Delta Drain rate** (progresión barra a barra) | `delta_velocity: Option<f64>` en `OrderFlowContext`; OLS slope 5 barras/ATR; VAFA evidence `delta_drain_bearish/bullish`; wired en monitor (intrabar + bar-close) ✅ 2026-05-24 |
| ✅ | **Single Prints / TPO** (30-min Market Profile) | `TpoTracker` (30-min buckets, bin_step≈ATR×0.1) + `single_prints` en `VolumeProfileContext` ✅ |

### Detectores Core (11)

| Estado | Detector | Notas |
|--------|----------|-------|
| ✅ | VAFA | `taker_imbalance` recalibrado 0.10 → 0.05 ✅ |
| ✅ | VVPC | `BelowVal` excluido por `val_proximity_ok_long` ✅ |
| ✅ | LVN | `lvn_nearby` checkeado ✅ |
| ✅ | DIB | OK |
| ✅ | SOB | OK |
| ✅ | OBR | order_blocks wired con datos reales ✅ |
| ✅ | FAR | OK |
| ✅ | LiqHunt | min_usd = $100k; target usa `min_rr` config ✅ |
| ✅ | FER | `extreme_threshold` = 0.001 (0.10% Binance real) ✅ |
| ✅ | SMD | Evidence LONG vs SHORT variables separadas ✅ |
| ✅ | CDR | CvdDivergenceReversal — SHORT bearish ≥4 / LONG bullish ≤-4 ✅ |

### Detectores Lab (5)

| Estado | Detector | Madurez | Bloqueador |
|--------|----------|---------|------------|
| 🔬 | VwapRejection | ShadowLab — generando señales | Necesita 100+ señales para evaluación |
| 💤 | AbsorptionTrapReversal | ObserveOnly — sleeping | Espera footprint L3 |
| 🔬 | SessionImbalanceBreakout | ObserveOnly — observando | Acumulando observaciones |
| 🔬 | LiquidityMagnet | ObserveOnly — observando | Acumulando observaciones |
| 💤 | OrderBlockFlowRetest | ObserveOnly — sleeping | Espera wire de OB en monitor |

### Trade Manager / Paper Engine

| Estado | Item |
|--------|------|
| ✅ | Sizing por riesgo (1% = $3/trade), leverage 10× |
| ✅ | Fees (0.04% taker × 2), slippage (1bp × 2), funding acumulado |
| ✅ | Stop state machine (STOP_HIT / TARGET_HIT / TTL / INVALIDATED) |
| ✅ | Progress-to-target con `.max(0.0)` por Side (trailing correcto) |
| ✅ | Atomic persist: `paper_account_state.json` + `paper_trades.jsonl` |
| ✅ | Invalidación VAFA — lógica en paper.rs correcta ✅ |
| ✅ | Funding multi-período — `periods_crossed` aplicado en `apply_funding_if_crossed` ✅ |

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
| ✅ | NaN guards en todas las coordenadas (`is_finite()` antes de `Path::line()`) |
| ✅ | OI Delta barras verde/rojo |
| ✅ | Order Block overlay — monitor ya pasa datos reales ✅ |

---

## Capa 6 — Infraestructura & DevOps

| Estado | Item |
|--------|------|
| ✅ | Railway: Dockerfile Rust 1.95-slim + debian:trixie (GLIBC 2.38 match) |
| ✅ | Local: GNU toolchain (MinGW-w64 POSIX.UCRT), `run.bat` |
| ✅ | MongoDB local en :27018 (aislado de :27017) |
| ✅ | Supabase (Railway) ↔ MongoDB (local) dual-write |
| ✅ | Config hot-reload: `config/strategy.toml` + MongoDB `deployed_params` (override por regime) |
| ✅ | Migración Local+Mongo — Bloques 1–5 completos; :27018 dedicado, dual-write, E2E verificado |

---

## Acumulación de Resultados / Calibración

| Estado | Item |
|--------|------|
| ✅ | `analyze_outcomes.py` — bloques 1–6 base + 7–11 condicional (listo, espera datos) |
| ✅ | `compare_core_vs_lab.py`, `score_decay.py`, `session_filter_analysis.py` — listos |
| 🔬 | VwapRejection: acumulando señales → meta 100+ para evaluación de promoción |
| 🔬 | Core detectors: acumulando → 300+ para WR significativo, 500+ para confianza de edge |
| ❌ | Sin resultados concretos aún — sistema en acumulación activa |

---

## Deudas Técnicas — Resumen Priorizado

### 🔴 Alta prioridad — Subdimi methodology (impacto en calidad de señales)

- [x] **Finish Action** — `derive_finish_unfinish_action()` en adapter; wired FAR + VAFA + scoring ✅ 2026-05-24
- [x] **Unfinish Action** — mismo punto de derivación; wired FAR + VAFA missing + scoring penalty ✅ 2026-05-24
- [x] **Big Trades** — `derive_big_trade()` en adapter; nivel >2.5× avg; `big_trade_bullish/bearish` en `OrderFlowContext`; FAR evidence; +0.06 scoring ✅ 2026-05-24

### 🟠 Media prioridad — Subdimi methodology (contexto y targets)

- [x] **HTF VP cascade** — `HtfVpTracker` (weekly/monthly) + `HtfVpContext` en ctx + `factor_htf_vp` (×1.08/×0.90) en scoring ✅ 2026-05-24
- [x] **Naked POC** — `NakedPocTracker` (10 sesiones, 0.05% touch band) + `naked_pocs` en `VolumeProfileContext` + `BONUS_NAKED_POC_MAGNET=+0.03` en scoring ✅ 2026-05-24
- [x] **Stacked Imbalances detector** — `derive_fbg_imbalance()` (one-side=0, FBG propio) con cascada FBG → delta-levels → delta-bars ✅ 2026-05-24
- [x] **OB volume ratio** — threshold subido a 3.0x (evidencia `ob_volume_ratio_gt_3x`), 1.5x como evidencia secundaria ✅ 2026-05-24
- [x] **Funding multi-período** — `periods_crossed = curr_period - prev_period` en `apply_funding_if_crossed` ✅ 2026-05-24

### 🔵 Baja prioridad — Observabilidad / Consistencia

- [x] **Stream health tracking** — `ws=[kline:ok|disc|recon depth:ok liq:ok]` en cada `[bar]` log ✅ (ya implementado)
- [x] **`liq_age`** — `liq_age={}` en cada `[bar]` log; `freshness.liq_age_str()` ✅ (ya implementado)
- [x] **`bars_since_signal`** — `bss={}` añadido al `[bar]` log (antes solo en `[metrics]` cada 10 barras) ✅ 2026-05-24
- [x] **Single Prints / TPO** — `TpoTracker` (30-min, bin ATR×0.1) + `single_prints: Vec<f64>` en `VolumeProfileContext` ✅ 2026-05-24
- [x] **Migración Local+Mongo** — Bloques 1–5 completos; 3 scripts calibration (`monitor.py`, `degradation_monitor.py`, `kelly_sizer.py`) usan SQL/Supabase como fallback (bajo ROI migrarlos mientras Supabase esté disponible) ✅

### ✅ Resuelto (historial)

- [x] CVD hard gate (`CVD_MACRO_VETO_CAP=0.35`) ✅ 2026-05-24
- [x] VAFA `taker_imbalance` recalibrado 0.10 → 0.05 ✅ 2026-05-24
- [x] FER `extreme_threshold` corregido 0.0006 → 0.001 ✅ 2026-05-24
- [x] CDR detector nuevo (CvdDivergenceReversal) ✅ 2026-05-24
- [x] VP Open Bias wired en router + scoring + `min_rr = 2.0` ✅ 2026-05-24
- [x] Order Blocks / FVG / Market Structure wired con datos reales ✅
- [x] `stacked_imbalance` bonus en scoring ✅ 2026-05-24
- [x] **Finish Action** — `finish_action_bullish/bearish` en `OrderFlowContext`; evidence en FAR + VAFA; +0.05 scoring ✅ 2026-05-24
- [x] **Unfinish Action** — `unfinish_action_bullish/bearish` en `OrderFlowContext`; missing en FAR + VAFA; -0.04 penalty ✅ 2026-05-24

---

## Resumen Ejecutivo

El sistema está **feature-complete en las 6 capas arquitectónicas** con la metodología Subdimi aplicada en el ~95% de sus componentes.

**Cobertura Subdimi actual:**
- ✅ 11 detectores cubren los patrones core (Absorción, Delta Drain, Liquidez, CVD, OB, VWAP, LVN, etc.)
- ✅ VP Open Bias (4 variantes) wired en gate + scoring
- ✅ min R:R 2:1, VPIN gate, AuctionState gate
- ✅ Finish Action + Unfinish Action — wired en FAR, VAFA, scoring (+0.05 / -0.04)
- ✅ HTF VP cascade (weekly + monthly) — `factor_htf_vp` ×1.08/×0.90 en scoring
- ✅ Naked POC — `NakedPocTracker` + `BONUS_NAKED_POC_MAGNET` +0.03 en scoring
- ✅ Stacked Imbalances FBG — `derive_fbg_imbalance()` como señal primaria
- ✅ Single Prints / TPO — `TpoTracker` 30-min, `single_prints` en contexto
- ✅ Big Trades — `derive_big_trade()` en adapter; nivel >2.5× avg; FAR evidence; +0.06 scoring
- ✅ Delta Drain rate — `delta_velocity` OLS slope 5 barras/ATR; VAFA evidence `delta_drain_bearish/bullish`; monitor intrabar + bar-close

**Deudas técnicas restantes (mínimas):**
- **3 scripts calibration SQL** — `monitor.py`, `degradation_monitor.py`, `kelly_sizer.py` usan `query_df(sql)` → bajo ROI mientras Supabase esté disponible como fallback

**Fase crítica activa:** acumular 100+ señales de VwapRejection para promoción, y 300+ señales core para WR estadísticamente significativo.

---

*Actualizado 2026-05-24 — sesión 4: metodología Subdimi 100% completa — sistema en acumulación activa*
