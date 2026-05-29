# DeltaRangeReversal + Micro-ventana — Bitácora de Implementación

*Fecha: 2026-05-29 (sesión completa)*

Este documento registra qué se cambió realmente en el código, qué quedó activo, y qué pasos operativos se ejecutaron para llevar el sistema al estado actual.

---

## Contexto — Por qué DRR

El router Subdimi-only (6 detectores) generó 13 señales en sus últimos días, 0 ganadores reales. El análisis mostró:

- LIQ disparaba señales back-to-back en el mismo nivel (76k, 3 señales en minutos)
- 4 LIQ Shorts a 77.3-77.4k en TrendDown → todas STOP_HIT cuando el mercado subió
- FAR: 1 ganador parcial de 3 señales
- Win rate efectivo: ~15%

Decisión: reemplazar todos los detectores por una única estrategia derivada directamente de Subdimi: **Delta Range Reversal con Absorción**. Una sola hipótesis, medible, calibrable.

---

## Arquitectura resultante

```
Core (router winner-takes-all)
  └── DeltaRangeReversal (DRR) — único detector

Subdimi Parallel (observación sin paper trading)
  └── VAFA, FAR, LVN, CDR, OBR, LIQ → lab_signals

Micro-ventana
  └── CandleMicroBuffer → micro_windows (288 rows/día)

Lab
  └── Eliminado
```

---

## 1. RangeDetector — nuevo módulo

**Archivo:** `data/src/detectors/range_detector.rs`

El módulo más nuevo del sistema. Detecta rangos intradía de price action (no VP-based).

**Funcionamiento:**
- Ventana rolling de 80 barras
- Finds Range High y Range Low por swings de highs/lows
- Valida: `bars_inside ≥ 20`, `touches_high ≥ 1`, `touches_low ≥ 1`, `total_touches ≥ 3`
- Slope OLS de los cierres normalizado por ATR — rechaza si `|slope| > 0.15` (trending, no ranging)
- Range size en ATR: válido entre 0.8 y 3.5

**Outputs clave:**
- `range_high`, `range_low`, `range_mid`, `range_poc` (VP POC si cae dentro del rango)
- `location: RangeLocation` — `NearHigh/NearLow/NoTrade/Inside/OutsideHigh/OutsideLow`
- `no_trade_zone` — True cuando precio está en el 35-65% del rango
- `sweep_range_low/high` — mecha bajo/sobre el extremo que cerró dentro (trigger principal)
- `sweep_low_depth/high_depth` — profundidad del sweep en ATR
- `breakout_down/up` — 2 cierres consecutivos fuera del rango = ruptura confirmada

**Wiring:** `data/src/detectors/mod.rs` → `main.rs` (struct, init, on_trade vía reset, on_bar_close) → `kline.rs` (bootstrap + bar close)

---

## 2. DeltaRangeReversal — detector

**Archivo:** `data/src/strategy/detectors/delta_range_reversal.rs`

Tesis: en un rango intradía, los extremos son zonas de decisión. Si el precio llega con agresión pero falla en aceptar fuera del rango, los agresivos quedan atrapados.

**LONG — Range Low Absorption:**
- Gate: `range.location == NearLow || OutsideLow` y `!range.breakout_down`
- Agresión vendedora: `delta < 0 || taker_imbalance < -0.05`
- Absorción (cualquiera): footprint bid absorption, big_trade_bearish, cvd bullish divergence, finish_action_bullish, sweep_range_low
- Trigger (cualquiera): sweep_range_low, sweep_confirmed, failed_acceptance, delta > 0
- Red flag: `cvd_slope < -0.25` (CVD confirmando ruptura) → no trade
- Stop: debajo del sweep low. Target: range_poc o range_mid

**SHORT — Range High Absorption:** espejo exacto.

**No-trade zone:** hardcodeada — el detector retorna `None` si `range.no_trade_zone == true`.

---

## 3. Router DRR-only

**Archivo:** `data/src/strategy/router.rs`

```rust
fn subdimi_detector_allowed(id: StrategyId) -> bool {
    matches!(id, StrategyId::DeltaRangeReversal)
}
```

Los otros 10 detectores retornan `SUBDIMI_ONLY_DISABLED` en el log. Están en código pero no compiten.

Se agregó:
- `DeltaRangeReversal` en `StrategyId` enum
- Key 11 en `cooldown.rs`
- Variante `"drr"` en `playbook_reasoning.rs` (detector_name + score de playbook)

---

## 4. Subdimi Parallel (ex-Lab)

**Archivo:** `data/src/strategy/subdimi_parallel.rs`

El Lab (5 detectores con lifecycle ObserveOnly/ShadowLab/PaperCandidate) fue eliminado.

Reemplazado por un runner simple:

```rust
pub fn run_subdimi_parallel(ctx, cfg) -> Vec<StrategySignal> {
    // corre: VAFA, FAR, LVN, CDR, OBR, LIQ — todos los que disparan
    // sin winner-takes-all, sin paper trading
}
```

Las señales van a `lab_signals` con `maturity = 'SubdimiParallel'`.
Útil para comparar: ¿qué habría señalado el sistema viejo en la misma vela donde DRR disparó?

---

## 5. 30 campos de calibración en shadow_signals

**Archivo:** `crates/monitor/src/supabase_writer.rs` — función `build_signal_row()`

Organizados en 6 bloques (computados antes del `json!` para no superar el recursion limit de Rust):

| Bloque | Variables clave |
|--------|----------------|
| Tiempo | `hour_utc`, `day_of_week`, `session_name`, `session_phase`, `minutes_since_session_open` |
| Rango | `range_midline_slope`, `range_bars_inside`, `range_second_test`, `range_vs_value_area` |
| Absorción | `absorption_count`, `entry_type` (`sweep_reclaim`/`near_extreme`), `sweep_depth_atr` |
| Precio | `value_location`, `price_vs_vwap`, `naked_poc_in_target_path`, `hvn_between_entry_target` |
| Institucional | `oi_direction`, `cvd_divergence_persistence`, `vpin`, `funding_velocity` |
| Trade | `rr_actual`, `distance_to_target_atr`, `obstacle_hvn_count`, `nearest_naked_poc_dist_atr` |

**Fix importante:** `#![recursion_limit = "512"]` en monitor — el macro `json!()` con 133 campos supera el límite por defecto de 128/256.

**Fix importante:** `TradingSession::Off` → `TradingSession::OffHours` (nombre real de la variante).

**Fix importante:** campos `self.vp.poc()` y `self.atr_wilder.current()` no existen en BarState → usar variables locales `poc` y `atr` ya en scope.

---

## 6. Micro-ventana (CandleMicroBuffer)

**Archivo:** `data/src/strategy/micro_window.rs`

Captura la micro-dinámica de cada vela M5. No es solo un snapshot — es la película.

**Diseño:**
- Buffer de 20 slots × 15s = toda la vela M5
- Al cerrar: selecciona 5 slots según `WindowAnchor` (CandleClose = últimos 75s por defecto)
- Emite una row a `micro_windows` por cada vela, con o sin señal

**Shape features clave:**
- `late_surge_ratio` = vol_b4 / mean(vol) — ¿el volumen se concentró en los últimos 15s?
- `delta_slope_norm` = slope OLS del delta por bucket / stdev — ¿el delta aceleró?
- `delta_flip_bucket` — ¿en qué bucket giró el delta?
- `vol_trajectory` — `front/mid/back/flat/u_shape`
- `absorption_proxy` = win_vol / (|price_net|/atr) — alto = compraron/vendieron sin mover precio

**DRR específico:**
- `reclaimed` / `reclaim_bucket` — ¿el precio salió del rango y volvió dentro?
- `sweep_depth_atr` — profundidad del barrido del extremo

**Decisiones de diseño:**
- `size` en base units (BTC) — consistente con CVD/footprint
- `is_big = false` — big trade detection no disponible per-trade (solo por barra)
- `liq_usd = 0.0` — liquidaciones vienen de @forceOrder stream, no per-trade
- Enums con métodos `to_db_str()` y `range_location_str()` explícitos — desacoplados de nombres de variantes Rust para estabilidad del dataset histórico

**Wiring:**
- `on_trade()` → `micro_buffer.on_trade(&MicroTrade{...})`
- `on_bar_close()` → si signal_fired: `mark_trigger(bar_ms)` → `build_row()` → Supabase
- Reset: `micro_buffer.reset(bar_ms + 300_000)` después de enviar

**JOIN con shadow_signals:**
```sql
ss.timestamp_ms = mw.candle_open_ms
-- bar.time = kline OPEN time = ctx.timestamp_ms = shadow_signals.timestamp_ms
```

---

## 7. Schema SQL — migraciones ejecutadas

### `supabase/migration_drr.sql`
- 10 columnas DRR en shadow_signals (range_high/low/mid/poc/size_atr/location/touches/sweep)
- 30 columnas de calibración (bloques 1-6)
- 6 columnas reasoning (de sesión 2026-05-24, pendientes)
- 5 índices nuevos (session, range_location, absorption, dow_hour, playbook)
- Vista `v_signals_with_outcomes` actualizada con todos los campos + buckets de segmentación

### `docs/migration_micro_windows.sql`
- Tabla `micro_windows` con 50+ columnas
- 4 índices
- Vista `v_micro_with_outcomes` — une micro_windows + shadow_signals + signal_outcomes
- Query de verificación incluida para confirmar que el JOIN trae filas

---

## 8. Limpieza de datos

Todos los datos pre-DRR (router Subdimi viejo, mayo 25-27) fueron borrados con:

```sql
DELETE FROM signal_outcomes;
DELETE FROM shadow_signals;
DELETE FROM lab_signals;
DELETE FROM regime_history;
DELETE FROM institutional_snapshots;
DELETE FROM intrabar_outcomes;
```

Sistema arranca en cero el 2026-05-29 con datos limpios.

---

## 9. Bugs de build resueltos en este proceso

| Error | Causa | Fix |
|-------|-------|-----|
| `recursion_limit reached` | `json!()` con 133 campos supera límite 256 | `#![recursion_limit = "512"]` |
| `unexpected end of macro invocation` | `let` bindings dentro de `json!({...})` como valor | Extraer `let rr = ...` antes del macro |
| `no field 'vp'`, `no field 'atr_wilder'` | Nombres de campo inventados en BarState | Usar variables locales `atr` y `poc` ya en scope |
| `TradingSession::Off` | Variante no existe — es `OffHours` | Corregir a `OffHours` |

---

## Estado operativo al finalizar

- Railway: deploy `55b8ed9` corriendo
- Supabase: DB limpia, tablas con schema DRR completo
- Monitor: DRR + Subdimi Parallel + Micro-ventana activos
- Primeros datos llegarán en la siguiente vela M5 cerrada después del deploy
