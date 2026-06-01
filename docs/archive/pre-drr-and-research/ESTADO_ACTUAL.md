# Estado Actual del Sistema — FlowSurface Monitor

**Última actualización:** Mayo 2026

> **🆕 2026-05-20**: Migración a operación **local con MongoDB**. La UI ahora corre el pipeline completo de detección + persistencia + config dinámica por régimen — paridad funcional con el monitor de Railway, más visualización en tiempo real. Doc canónica: [LOCAL_MONGO_SETUP.md](LOCAL_MONGO_SETUP.md).

Este documento describe el estado real y funcional del sistema después de todos los fixes y mejoras aplicados. Es la referencia autoritativa para entender qué hace cada componente y cómo está configurado en producción.

---

## 1. Infraestructura de Deployment

### Docker (Railway)

```dockerfile
FROM rust:1.95-slim AS builder          # Debian trixie → GLIBC 2.38
RUN apt-get install -y pkg-config libssl-dev
COPY . .
RUN cargo build --release -p monitor

FROM debian:trixie-slim                  # mismo GLIBC 2.38 — match obligatorio
RUN apt-get install -y ca-certificates
COPY --from=builder /app/target/release/monitor /usr/local/bin/monitor
ENV SYMBOL=BTCUSDT
ENV TIMEFRAME_MIN=5
CMD ["monitor"]
```

**Regla crítica:** builder y runtime deben ser la misma versión de Debian. `rust:1.95-slim` = trixie = GLIBC 2.38. Si se cambia el builder hay que cambiar el runtime en consecuencia.

### Toolchain local (Windows)

```
Toolchain: stable-x86_64-pc-windows-gnu
Linker: MinGW-w64 (winget: BrechtSanders.WinLibs.POSIX.UCRT)
Run: ./run.bat (incluye PATH de MinGW + RUST_BACKTRACE=1)
```

---

## 2. Monitor — Arquitectura del Event Loop

El binario `monitor` (`crates/monitor/src/main.rs`) corre en Railway y conecta a Binance USDM Futures via WebSocket.

### Flujo por bar-close (M5)

```
WebSocket kline event
  → on_bar_close()
      ├─ [1] Recupera UUID del write_signal del bar anterior (oneshot channel)
      ├─ [2] Calcula delivery_lag_ms = now_ms - bar_close_ms
      ├─ [3] Actualiza VWAP, CVD, Volume Profile, OI
      ├─ [4] Calcula ATR (14 períodos)
      ├─ [5] Calcula regime con hysteresis
      ├─ [6] Construye StrategyMarketContext
      ├─ [7] route_strategy() → StrategySignal
      ├─ [8] paper_account.on_bar_close() (funding → cierres → apertura)
      ├─ [9] Dispara write_signal en tokio::spawn (off hot path)
      └─ [10] Log: bar close, signal, paper state, metrics
```

### Latencia observada en producción

```
delivery_avg = 400–800ms   ← Binance WebSocket (unavoidable, not our code)
proc_avg     < 7ms         ← Rust compute (confirmed via [perf] instrumentation)
proc_max     < 15ms        ← spike ocasional por volume profile recalc
```

La latencia de entrega de Binance es normal e inherente al protocolo — no es un bug.

### Optimizaciones de performance aplicadas


| Commit    | Optimización                                             | Impacto                                |
| --------- | -------------------------------------------------------- | -------------------------------------- |
| `09e0d7e` | `write_signal` movido a tokio::spawn con oneshot channel | Elimina await de Supabase del hot path |
| `be6a0bf` | Config reload movido a mpsc channel                      | Elimina fetch HTTP del hot path        |
| `8a4f30d` | AVWAP-BOS: O(n²) → O(n) con prefix max/min arrays        | Elimina loop nested por bar-close      |


### Historical warm-up

Al arrancar, el monitor fetchea 50 klines históricas de `fapi.binance.com/fapi/v1/klines` para primar:

- VWAP acumuladores
- ATR (14 períodos necesita 14+ barras)
- Regime inicial
- CVD inicial

Sin warm-up las primeras señales tienen ATR=None y se bloquean en el ATR guard del router.

---

## 3. Strategy Module

### Arquitectura

```
data/src/strategy/
├── types.rs          — structs, enums de dominio (StrategyConfig, StrategySignal, etc.)
├── adapter.rs        — build_context(), derive_regime(), derive_regime_with_hysteresis()
├── router.rs         — dispatch a detectores + toxic_flow_gate
├── scoring.rs        — score 0.0–1.0 sobre la señal cruda
├── paper.rs          — motor de paper trading completo
├── tracker.rs        — OutcomeTracker MFE/MAE (legacy)
├── logger.rs         — escribe señales a JSONL
└── detectors/
    ├── vwap_value_pullback_continuation.rs  ← detector principal, más fixes
    ├── value_area_failed_auction.rs
    ├── lvn_liquidity_vacuum_breakout.rs
    ├── liquidation_hunt.rs               — requiere ctx.institutional
    ├── funding_exhaustion_reversal.rs    — requiere ctx.institutional
    ├── smart_money_divergence.rs         — requiere ctx.institutional
    └── toxic_flow_gate.rs               — gate pre-detección
```

### StrategyConfig — valores actuales

```rust
enabled:                   false  // se activa via Supabase o env
max_spread_bps:            2.0
max_vpin:                  0.75
min_score:                 0.60
default_ttl_ms:            15_000_000   // 250 minutos (50 barras M5)
min_rr:                    1.5          // R:R mínimo para emitir señal
max_rr_m5:                 8.0          // R:R máximo razonable en M5
liq_hunt_min_usd:          500_000.0
liq_cascade_threshold:     5_000_000.0
liq_ttl_ms:                600_000      // 10 min
funding_extreme_threshold: 0.0006
funding_ttl_ms:            1_800_000    // 30 min
smart_short_threshold:     0.45
retail_long_threshold:     0.60
min_divergence:            0.18
smd_ttl_ms:                1_200_000    // 20 min
```

### Regime con hysteresis

```rust
// Thresholds de entrada (más exigentes)
TrendUp:   slope > +0.10 (slow 20 barras) || slope > +0.25 (fast 5 barras)
TrendDown: slope < -0.10 || slope < -0.25

// Thresholds de salida (más permisivos — evita flip-flop)
TrendUp sale si:   slow < 0.05 && fast < 0.15
TrendDown sale si: slow > -0.05 && fast > -0.15
```

---

## 4. VwapValuePullbackContinuation — Fixes Completos

Este es el detector que más señales genera. Tenía 4 bugs críticos corregidos en `ccb5f99`.

### Bug A — Stop invertido (el más crítico)

```rust
// ANTES (incorrecto): min() elige el más LEJANO
let stop = f64::min(val, entry - 0.75 * atr);
// → en producción: stop a $153 de distancia, R:R 0.20:1

// AHORA (correcto): max() elige el más CERCANO
let stop = f64::max(val, entry - 1.0 * atr);
// → stop al nivel más cercano entre VAL y 1×ATR
```

**Lógica:** `max(val, entry - atr)` devuelve el precio más alto entre los dos, que es el más cercano al entry para un long. Si VAL está dentro de 1×ATR, usa VAL como nivel estructural. Si VAL está muy lejos, usa ATR como floor del riesgo.

**Simétrico para SHORT:** `min(vah, entry + 1.0 * atr)` — usa el precio más bajo (más cercano).

### Bug B — Target fijo (sin R:R mínimo)

```rust
// ANTES: target = vah (siempre, sin verificar distancia)
// → en producción: reward $31 con risk $153 → R:R 0.20

// AHORA: find_structural_target() con R:R garantizado
fn find_structural_target(entry, risk, side, vp, ob, atr, cfg) -> Option<f64> {
    // Candidatos en orden de prioridad:
    // 1. HVNs en dirección del trade dentro de [min_reward, max_reward]
    // 2. VAH/VAL si está en rango
    // 3. Walls del orderbook en rango
    // 4. Fallback: entry ± 3×ATR (si cumple min_rr)
    // Retorna None si ningún candidato cumple → señal suprimida
}
```

### Bug C — Gate de riesgo mínimo

```rust
let risk = entry - stop;  // long
if risk < 10.0 || stop >= entry {
    return None;  // stop degenerado
}
```

### Bug D — TTL hardcodeado

```
ANTES: ttl_ms: 300_000   (5 minutos = 1 barra M5)
AHORA: ttl_ms: cfg.default_ttl_ms   (250 minutos = 50 barras)
```

### Resultado con datos reales de producción

```
Señal 08:00 UTC (entry=$78,137, ATR=$43.8, VAL=$77,984, VAH=$78,168)

  ANTES:  stop=$77,984  risk=$153  target=$78,168  reward=$31   R:R=0.20
  AHORA:  stop=$78,093  risk=$44   target=$78,268  reward=$131  R:R=2.98
           (ATR floor)              (fallback 3×ATR)
```

---

## 5. Paper Trader — Estado y Configuración

### Configuración actual (producción)

```
PAPER_INITIAL_CAPITAL = $300     ← capital real de arranque
PAPER_LEVERAGE        = 10×      ← simula Binance USDM Futures
PAPER_RISK_PCT        = 1%       ← $3 por trade
PAPER_MAX_POSITIONS   = 1
PAPER_SLIPPAGE_BPS    = 1        ← 1bp por lado
PAPER_TAKER_FEE       = 0.04%   ← fee de Binance taker
PAPER_FUNDING_RATE    = 0.01%   ← cada 8h
```

**Poder de compra:** $300 × 10 = $3,000

### Sizing — lógica correcta

```rust
risk_amount = balance × risk_pct          // $300 × 0.01 = $3
raw_risk_per_unit = |entry - stop|        // distancia al stop
min_risk_per_unit = entry × risk_pct / leverage  // floor escalado con leverage
                  = $104,000 × 0.01 / 10 = $104

risk_per_unit = max(raw, min)             // nunca < $104 para BTC@$104k
size = risk_amount / risk_per_unit        // $3 / $250 = 0.012 BTC
notional = size × entry                  // 0.012 × $104,000 = $1,248
margin = notional / leverage             // $1,248 / 10 = $124.80 bloqueado
```

El floor `entry × risk_pct / leverage` garantiza que `notional ≤ max_notional` matemáticamente, eliminando el warning de cap en condiciones normales.

### Trade ejemplo (BTC@$104k, ATR=$250)

```
Signal:   entry=$104,000  stop=$103,750  target=$104,800  R:R=3.2:1

Sizing:
  size           = 0.012 BTC
  notional       = $1,248
  margin usado   = $124.80  (41.6% del capital)
  cash libre     = $175.20
  entry fee      = $0.50

WIN — target a $104,800:
  gross PnL  = 0.012 × $800     = +$9.60
  fees       = 2 × 0.04% × $1,248 = $1.00
  net PnL    = +$8.60            = +2.87% del balance
  balance    → $308.60

LOSS — stop a $103,750:
  gross PnL  = 0.012 × (-$250)  = -$3.00
  fees       = $1.00
  net PnL    = -$4.00            = -1.33% del balance
  balance    → $296.00

Net R:R post-fees = $8.60 / $4.00 = 2.15:1
Break-even win rate = 4.00 / (8.60 + 4.00) = 31.7%
```

### Contabilidad completa

```
Apertura:
  balance -= margin                      // cash bloqueado como colateral
  fees_paid += notional × taker_fee      // fee de entrada (deferred al cierre)

Cada 8h (funding):
  Long paga:  funding_paid += notional × funding_rate
  Short cobra: funding_paid -= notional × funding_rate
  (no toca balance hasta cierre)

Cierre:
  exit_price = nivel (stop/target/close) ± slippage
  gross_pnl = size × (exit - entry) para Long
  exit_fee = size × exit_price × taker_fee
  net_pnl = gross_pnl - fees_paid_total - funding_paid
  balance += margin + net_pnl           // devuelve colateral + resultado
```

### Persistencia

```
logs/paper_account_state.json   — estado completo (write atómico via .tmp + rename)
logs/paper_trades.jsonl         — trades cerrados (append)
logs/contradictions.jsonl       — señales opuestas ignoradas (append)
```

Restauración automática al reiniciar: si el JSON existe, restaura balance, posiciones y trades. Si está corrupto, arranca con cuenta nueva sin crashear.

---

## 6. Router y Scoring

### Flujo del router

```rust
route_strategy(ctx, cfg):
  1. if !cfg.enabled → Wait
  2. if atr < $1.0  → Wait (ATR_NOT_READY)
  3. toxic_flow_gate(ctx, cfg) → si falla → Blocked
  4. run detectors:
       VAFA, LVN, VWAP → siempre
       LIQ, FER, SMD   → solo si ctx.institutional.is_some()
  5. score todos los candidatos
  6. best = max by score
  7. if best.score >= cfg.min_score → ShadowSignal
     else → Wait con LOW_SCORE + evidence del mejor candidato (para calibración)
```

### Rejection tags en logs

Cuando un detector no genera señal, el router loguea la razón:

```
VAFA:SKIP, LVN:SKIP, VWAP:SKIP  — detector no encontró setup
LIQ:SKIP, FER:SKIP, SMD:SKIP    — detector institucional no encontró setup
INST:NULL                        — ctx.institutional no disponible
LOW_SCORE                        — hay candidato pero score < min_score
ATR_NOT_READY                    — ATR < $1 o no calculado aún
```

---

## 7. Supabase — Schema de Datos

### Tablas activas


| Tabla             | Descripción                                                    |
| ----------------- | -------------------------------------------------------------- |
| `shadow_signals`  | Una fila por señal emitida (ShadowSignal o Blocked)            |
| `paper_trades`    | Una fila por trade cerrado con PnL completo                    |
| `deployed_params` | Configuración calibrada por regime (cargada por config_loader) |


### Campos de shadow_signals

```sql
id uuid, created_at timestamptz,
symbol text, strategy_id text, action text, side text, regime text,
entry_price float8, stop_price float8, target_price float8,
score float8, ttl_ms int8,
evidence text[], missing text[], invalidation text[],
-- contexto de mercado al momento de la señal:
price float8, atr float8, vwap float8, cvd float8, delta float8,
vpin float8, spread_bps float8, poc float8, vah float8, val float8,
value_location text, funding_rate float8, oi_delta float8,
-- paper trade linkado (si existe):
paper_trade_id uuid
```

### Config loader

`config_loader.rs` fetchea `deployed_params` al cambiar de regime o cada 15 minutos. Si Supabase no responde, cae silenciosamente a `StrategyConfig::default()` con `enabled: true`. Los campos `min_rr` y `max_rr_m5` son tuneables desde Supabase sin redeploy.

---

## 8. Métricas en Logs

### Separación stdout / stderr

Los logs operacionales van a **stdout** (`println!`) y los errores reales a **stderr** (`eprintln!`). En Railway esto separa el noise operacional de las alertas reales.

| stdout (`println!`)                   | stderr (`eprintln!`)                    |
|---------------------------------------|-----------------------------------------|
| `[bar]`, `[metrics]`, `[intrabar]`    | `[WARN]` (stream stale, sin datos)      |
| `[outcome]`, `[warmup]`, boot msgs    | `[fetch] failed`, `[liq] WS connect failed` |
| `[liq] connected`, `[kline] connected`| `[kline] DISCONNECTED`, `[liq] parse_failure` |

### Formato del [bar] log

```
[bar] ts=1747500000000 close=104000.00 regime=TrendUp
  slow=0.012 fast=0.008 funding=0.0001 basis=0.020% oi_delta=120
  vwap=103850.00 cvd=1240.0 ob=live
  inst=Live(4/5) ls_top=49.9%/55.6% liq=0$ liq_age=never
  ws=[kline:Ok depth:Ok trades:ok liq:Ok]
  action=ShadowSignal score=0.720 delivery=423ms proc=4ms equity=300.00
  missing=[] skip=[]
```

### Formato del [metrics] log (cada 10 bars y cada 60s)

```
[metrics] bars=12 signals=0 liq_events=0 liq_raw=47 ws_reconnects=0 |
  kline_ticks=73 trades=1840 depth_updates=245 |
  bar_latency_avg=430ms max=812ms |
  depth_age=120ms trade_age=95ms |
  inst=4/5 liq_age=never liq_connected=5m30s ls_age=4m12s taker_age=2m01s oi_age=3m45s funding_age=0m12s |
  ws=[kline:Ok depth:Ok trades:ok liq:Ok]
```

**Diagnóstico liquidaciones:**
- `liq_raw=0` + `liq_connected` reciente → mercado quieto, normal
- `liq_raw > 0` + `liq_events=0` → buscar `[liq] parse_failure:` en logs
- `liq_connected=never` → WS nunca estableció conexión TLS

---

## 9. Pendientes Críticos

### Para primera calibración (necesita ≥50 trades cerrados)

El script `scripts/analyze_outcomes.py` está implementado pero necesita datos reales. Con 0 trades cerrados en Supabase actualmente, la calibración no puede ejecutarse.

Los trades se cierran por: `STOP_HIT`, `TARGET_HIT`, `TTL_EXPIRED`, `INVALIDATED`.

Con los fixes de hoy (stop correcto, target estructural, TTL 250min), los trades deberían cerrar por `STOP_HIT` o `TARGET_HIT` en vez de todos por `TTL_EXPIRED`.

### Pendientes de código


| Prioridad | Item                                                                | Archivo                                         |
| --------- | ------------------------------------------------------------------- | ----------------------------------------------- |
| ~~Alta~~  | ~~`mss_active` y `sweep_confirmed` hardcodeados — RESUELTO~~        | `main.rs:1121`, `src/chart/kline.rs:1427`       |
| ~~Alta~~  | ~~`write_trade` Supabase no llamado — RESUELTO~~                    | `main.rs` — oneshot UUID + write_trade          |
| ~~Media~~ | ~~Lab outcomes/signals ignorados — RESUELTO~~                       | `main.rs` — `completed_outcomes` + Supabase     |
| ~~Baja~~  | ~~`write_regime_change` no llamado — RESUELTO~~                     | `main.rs` — bloque regime change                |
| ~~Alta~~  | ~~`progress_to_target()` con `.abs()` activaba trailing con precio adverso — RESUELTO~~ | `trade_manager.rs` — `.max(0.0)` por Side |
| ~~Alta~~  | ~~`max_by` tie-break no determinístico en router — RESUELTO~~       | `router.rs` — `strategy_id as u8` secundario   |
| ~~Media~~ | ~~`range == 0.0` → NaN/Infinity en scale.rs — RESUELTO~~            | `src/chart/scale.rs` — early return guard       |
| ~~Media~~ | ~~`is_sell` case-sensitive en Bybit parser — RESUELTO~~             | `bybit/stream.rs` — `eq_ignore_ascii_case`      |
| ~~Baja~~  | ~~Escritura de estado no atómica (corrupción en crash) — RESUELTO~~ | `data/src/lib.rs` — `.tmp` + rename             |
| ~~Baja~~  | ~~Dockerfile sin fail-fast si SUPABASE_URL/KEY ausentes — RESUELTO~~ | `Dockerfile` — check en CMD                  |
| ~~Media~~ | ~~Swing high/low en `find_structural_target` — RESUELTO~~           | `vwap_value_pullback_continuation.rs` líneas 112-114, 363-366 |
| ~~Media~~ | ~~LiqHunt target hardcodeado 1.5×ATR ignora `cfg.min_rr` — RESUELTO~~ | `liquidation_hunt.rs` — `cfg.min_rr * atr`  |
| ~~Baja~~  | ~~Swing20 off-by-one (19 barras, no 20) — RESUELTO~~                | `main.rs` — guard `n >= 21`, range `[n-21..n-1]` |
| ~~Baja~~  | ~~SMD evidencia LONG incorrecta (`oi_by_delta_long` reutilizado) — RESUELTO~~ | `smart_money_divergence.rs` — variables separadas por side |
| ~~Alta~~  | ~~`liq_age=never`: sin distinción entre mercado quieto y stream roto — RESUELTO~~ | `main.rs` — `liq_raw_messages` + `liq_ws_connected_at` + log parse_failure |
| ~~Media~~ | ~~Todos los logs en stderr → Railway marca operacionales como errores — RESUELTO~~ | `main.rs` — `println!` para operacionales, `eprintln!` para errores |
| Media     | HVN levels en Supabase para análisis offline                        | Schema migration                                |
| Baja      | `cargo-audit` en CI                                                 | `.github/workflows/`                            |
| Baja      | `write_outcome` / `patch_horizon` intrabar outcomes (no integrados) | `supabase_writer.rs`                            |


### Pendientes de indicadores

Todos implementados (verificado 2026-05-21).

| Item                      | Archivo                                              | Estado |
| ------------------------- | ---------------------------------------------------- | ------ |
| ~~Funding rate panel~~    | `src/chart/indicator/kline/funding_rate.rs`          | DONE   |
| ~~OI z-score~~            | `data/src/institutional/oi_tracker.rs` → `delta_zscore()` | DONE   |
| ~~AVWAP manual~~          | `src/chart.rs` — `PlacingAvwapAnchor` → `SetAvwapAnchor` | DONE   |
| ~~Session VWAPs~~         | `src/chart/indicator/kline/vwap.rs` → `compute_session_vwap()` | DONE   |


---

## 10. Commits de Esta Sesión


| Commit    | Descripción                                                            |
| --------- | ---------------------------------------------------------------------- |
| —         | fix(monitor): liq_raw counter + stdout/stderr separation |
| `daee74e` | Warm-up histórico, regime hysteresis, inst logging, detector breakdown |
| `09e0d7e` | `write_signal` off hot path (oneshot channel)                          |
| `be6a0bf` | Config reload off hot path (mpsc channel)                              |
| `8a4f30d` | AVWAP-BOS O(n²) → O(n)                                                 |
| `29535bf` | Timing por sección en bar-close log                                    |
| `9a906c1` | Separación delivery lag vs processing time en métricas                 |
| `ccb5f99` | Fix VWAP: stop correcto, target estructural, TTL, sizing               |
| `aff54d6` | Paper: leverage=10×                                                    |
| `6fda4e2` | Paper: capital $300                                                    |
| `4916be6` | config_loader: campos min_rr y max_rr_m5 (fix build Railway)           |
| `88b651e` | Dockerfile: builder rust:1.95-slim                                     |
| `eac7221` | Dockerfile: runtime debian:trixie-slim (fix GLIBC 2.38)                |
| —         | fix: progress_to_target, router tie-break, scale NaN, Bybit is_sell, atomic write, Dockerfile fail-fast, Supabase partial index |


