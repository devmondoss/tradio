# Bugs corregidos

Todos los bugs listados aquí han sido resueltos. Ver el commit correspondiente para el diff exacto.

---

## 1. OI — loop infinito de ERROR spam

**Síntoma:**
```
ERROR -- Failed to request OpenInterest(...): Request overlaps with an existing request
```
Spam cada ~1 segundo indefinidamente.

**Causa raíz:** `FetchUpdate::Error` no incluía `req_id`. Cuando un fetch de OI fallaba (HTTP 400 de Binance), el sistema no podía llamar `mark_failed(req_id)` en el `RequestHandler`. La request quedaba en estado `Pending` para siempre. Cada tick siguiente intentaba pedir el mismo rango → `Pending` → `Overlaps` → ERROR → repeat.

**Fix:**
- `FetchUpdate::Error` ahora incluye `req_id: Option<Uuid>`
- Los 3 sites de error actualizados: OI y klines pasan su `req_id`, trades pasa `None`
- Nuevo mensaje `Message::FetchFailed { pane_id, req_id, error }` en el dashboard
- Handler llama `state.mark_fetch_failed(req_id, error)` → `chart.mark_request_failed()` → `request_handler.mark_failed()`

**Archivos:** `src/connector/fetcher.rs`, `src/screen/dashboard.rs`, `src/screen/dashboard/pane.rs`, `src/chart/kline.rs`

---

## 2. MultiSplit — panic por mismatch de splits

**Síntoma:** Crash al arrancar cuando el layout guardado tenía más splits que paneles activos.

**Causa raíz:** `MultiSplit::new` aceptaba `&Vec<f32>` y asumía `splits.len() == panels.len() - 1`. El estado persistido quedaba desincronizado al eliminar un indicador.

**Fix:**
- `splits: &'a Vec<f32>` → `splits: &'a [f32]`
- Truncar el slice en el call site: `&state.layout.splits[..expected_splits.min(splits.len())]`
- Eliminada la aserción estricta

**Archivos:** `src/widget/multi_split.rs`, `src/chart.rs`

---

## 3. Error toasts en scroll/zoom

**Síntoma:** 4+ toasts "Fetch error: Request was rejected" en cada scroll o zoom.

**Causa raíz:** Scroll/zoom lanza requests de datos. Si el rango solapa una request pendiente o Binance rechaza el rango (sin historial), el error subía como notificación visible.

**Fix:** En `Message::FetchFailed`: si `error.contains("rejected") || error.contains("overlaps")` → log DEBUG solamente, sin toast. Errores reales siguen mostrando notificación.

**Archivo:** `src/screen/dashboard.rs`

---

## 4. Panic de lyon_path — strategy overlay (NaN en coordenadas)

**Síntoma:** `assertion failed: p.y.is_finite()` en `lyon_path-1.0.16/src/path.rs:812`

**Causa raíz:** `draw_strategy_overlay` calculaba `entry_y, stop_y, target_y` con `price_to_y` y los pasaba a `Path::line` sin verificar que fueran finitos. Con chart sin datos → NaN.

**Fix:**
```rust
if !entry_y.is_finite() || !stop_y.is_finite() || !target_y.is_finite() {
    continue;
}
```

**Archivo:** `src/chart/kline.rs` (función `draw_strategy_overlay`)

---

## 5. Panic de lyon_path — overlays de indicadores

**Síntoma:** Mismo panic en el sistema de overlays.

**Causa raíz:** Solo se verificaba `y.is_finite()` pero no `x.is_finite()`. `interval_to_x(key)` podía ser NaN en casos extremos.

**Fix:** Guards `!x.is_finite()` añadidos en `draw_indicator_overlays` para bandas y líneas.

**Archivo:** `src/chart/kline.rs` (función `draw_indicator_overlays`)

---

## 6. Panic de lyon_path — crosshair en panel vacío (RESUELTO)

**Síntoma:** Crash ~8s después del launch, al cargar metadata de tickers. Backtrace apuntaba a `plot.rs:361`.

**Causa raíz:** En el crosshair del panel de indicadores, `snap_ratio = (rounded - highest) / (lowest - highest)`. Cuando el panel estaba vacío, `highest == lowest` → división por cero → NaN → `Path::line(NaN, ...)` → panic de lyon.

**Fix:**
```rust
let range = lowest - highest;
if range.abs() > f32::EPSILON {
    let snap_ratio = (rounded - highest) / range;
    let hy = snap_ratio * bounds.height;
    if hy.is_finite() {
        frame.stroke(&Path::line(...), dashed);
    }
}
let vx = snap_ratio * bounds.width;
if vx.is_finite() {
    frame.stroke(&Path::line(...), dashed);
}
```

**Archivo:** `src/chart/indicator/plot.rs` (función de crosshair)

---

## 7. Panic de lyon_path — LinePlot y BarPlot

**Síntoma:** Mismo `p.y.is_finite()` en dibujo de polilíneas y barras.

**Fix en `line.rs`:**
```rust
// En el loop de polilínea:
if !sx.is_finite() || !sy.is_finite() { prev = None; return; }
// En el loop de puntos:
if !sx.is_finite() || !sy.is_finite() { return; }
```

**Fix en `bar.rs`:**
```rust
if !y_base.is_finite() { return; }
if !center_x.is_finite() { return; }
if !y_total.is_finite() { return; }
if !y_overlay.is_finite() { return; }
```

**Archivos:** `src/chart/indicator/plot/line.rs`, `src/chart/indicator/plot/bar.rs`

---

## 8. ATR — error de deref en pattern binding

**Causa raíz:** `for (i, (&time, dp))` con `UnixMs` newtype causaba doble deref.

**Fix:** `for (i, (time, dp))` + `result.insert(*time, ...)` → `result.insert(time, ...)`

**Archivo:** `src/chart/indicator/kline/atr.rs`

---

## 9. Scoring — ambigüedad de tipo float

**Fix:** `let mut score = 0.0` → `let mut score: f64 = 0.0`

**Archivo:** `src/strategy/scoring.rs`

---

## 10. OI Delta — fetch collision con OpenInterest

**Síntoma:** OI Delta tenía su propio `fetch_range` usando `FetchRange::OpenInterest`, que colisionaba con el fetch del indicador `OpenInterest`.

**Fix:** Eliminado `fetch_range` de `OiDeltaIndicator`. Los datos llegan vía `on_open_interest`, que ahora es llamado para ambos indicadores en `insert_open_interest`:

```rust
for key in [KlineIndicator::OpenInterest, KlineIndicator::OiDelta] {
    if let Some(indi) = self.indicators[key].as_mut() {
        indi.on_open_interest(oi_data);
    }
}
```

**Archivo:** `src/chart/kline.rs`

---

## 11. OI Delta — vacío al activar tardíamente

**Síntoma:** Si el usuario activa OI Delta después de que los datos de OI ya fueron cargados, el indicador aparece vacío (no recibe el fetch inicial).

**Fix:** En `toggle_indicator`, cuando se activa `KlineIndicator::OiDelta`, se hace bootstrap con datos existentes del indicador `OpenInterest`:

```rust
if indicator == KlineIndicator::OiDelta {
    if let Some(oi_indi) = self.indicators[KlineIndicator::OpenInterest].as_ref() {
        if let Some(existing) = oi_indi.oi_snapshot() {
            box_indi.on_open_interest(&existing);
        }
    }
}
```

**Archivo:** `src/chart/kline.rs` (función `toggle_indicator`)

---

## 12. Binance COIN-M — spam "Missing contract size"

**Síntoma:** Cada 5 minutos, mensajes de error para contratos como `BTCUSD_260626`.

**Causa raíz:** Los contratos de delivery de Binance COIN-M (formato `SYMBOL_YYMMDD`) pasaban por el pipeline de perps, pero no tenían contract size configurado.

**Fix:** Filtrar antes del lookup de contract size los símbolos con sufijo de 6 dígitos numéricos:

```rust
if market == MarketKind::InversePerps {
    if let Some(pos) = symbol.rfind('_') {
        let suffix = &symbol[pos + 1..];
        if suffix.len() == 6 && suffix.bytes().all(|b| b.is_ascii_digit()) {
            continue;
        }
    }
}
```

**Archivo:** `exchange/src/adapter/hub/binance/fetch.rs`

---

## 13. LiquidationHunt — target filter ignoraba `cfg.min_rr`

**Síntoma:** Si `min_rr` se tuneaba por encima de 1.5 desde `deployed_params` en Supabase, las señales de LiquidationHunt podían dispararse con R:R por debajo del mínimo configurado.

**Causa raíz:** El filtro de candidatos de target usaba `1.5 * atr` hardcodeado en lugar de `cfg.min_rr * atr`. El detector ignoraba el campo configurable.

```rust
// ANTES — hardcodeado, no respeta config tuneable:
.filter(|&t| t.is_finite() && t > entry + 1.5 * atr)

// AHORA — usa el R:R mínimo configurado:
.filter(|&t| t.is_finite() && t > entry + cfg.min_rr * atr)
```

**Commit:** `95e9908`
**Archivo:** `data/src/strategy/detectors/liquidation_hunt.rs`

---

## 14. Swing20 off-by-one — 19 barras en lugar de 20

**Síntoma:** Los niveles de swing usados como targets en `find_structural_target` miraban 19 barras históricas en lugar de las 20 que indica el nombre de la constante `SWING20`.

**Causa raíz:** El range `highs[n - SWING20..n - 1]` con `SWING20 = 20` produce 19 elementos (índices `n-20` a `n-2` inclusive). El guard `n >= SWING20` era también insuficiente.

```rust
// ANTES — 19 barras, guard insuficiente:
if n >= SWING20 {
    let sh = highs[n - SWING20..n - 1]  // solo 19 barras

// AHORA — 20 barras exactas, excluyendo la barra actual:
if n >= SWING20 + 1 {
    let sh = highs[n - SWING20 - 1..n - 1]  // 20 barras
```

**Commit:** `95e9908`
**Archivo:** `crates/monitor/src/main.rs`

---

## 15. SmartMoneyDivergence — evidencia LONG usaba lógica del branch SHORT

**Síntoma:** En señales LONG de SMD, la evidencia `"oi_accumulation_on_dip"` se añadía cuando OI subía **y** delta era negativo — flujo vendedor durante una señal compradora. Esto era incorrecto y contaminaba el análisis de outcomes.

**Causa raíz:** La variable `oi_by_delta_long` (nombre engañoso) se computaba con `delta < -0.20 * atr` y se reutilizaba en ambos branches. Para el LONG branch, la condición correcta es `delta > +0.20 * atr` (confirmación directa de acumulación).

```rust
// ANTES — misma variable en ambos branches:
let oi_by_delta_long = oi_delta > 0.0 && delta < -0.20 * atr;  // incorrecto para LONG

// AHORA — variables separadas por semántica:
let oi_rising_on_negative_delta = oi_delta > 0.0 && delta < -0.20 * atr;  // SHORT branch
let oi_rising_on_positive_delta  = oi_delta > 0.0 && delta > +0.20 * atr;  // LONG branch
```

**Commit:** `95e9908`
**Archivo:** `data/src/strategy/detectors/smart_money_divergence.rs`
