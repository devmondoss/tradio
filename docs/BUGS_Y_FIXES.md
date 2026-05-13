# Bugs corregidos

## 1. OI — loop infinito de ERROR spam

**Síntoma:**
```
ERROR -- Failed to request OpenInterest(...): Request overlaps with an existing request
```
Disparaba cada ~1 segundo indefinidamente.

**Causa raíz:** `FetchUpdate::Error` no incluía `req_id`. Cuando un fetch de OI fallaba (HTTP 400 de Binance), el sistema no podía llamar `mark_failed(req_id)` en el `RequestHandler`. La request quedaba en estado `Pending` para siempre. Cada tick siguiente intentaba pedir el mismo rango → `Pending` → `Overlaps` → ERROR → repeat.

**Fix:**
- Se añadió `req_id: Option<Uuid>` a `FetchUpdate::Error` en `src/connector/fetcher.rs`
- Los 3 sites que emiten error actualizados: fetch de OI y klines pasan su `req_id`, fetch de trades pasa `None`
- Nuevo mensaje `Message::FetchFailed { pane_id, req_id, error }` en el dashboard
- El handler llama `state.mark_fetch_failed(req_id, error)` → `chart.mark_request_failed()` → `request_handler.mark_failed()`
- Nuevos métodos: `KlineChart::mark_request_failed()`, `PaneState::mark_fetch_failed()`

**Archivos:** `src/connector/fetcher.rs`, `src/screen/dashboard.rs`, `src/screen/dashboard/pane.rs`, `src/chart/kline.rs`

---

## 2. MultiSplit — panic por mismatch de splits

**Síntoma:** Crash al arrancar cuando el layout guardado tenía más splits que paneles activos.

**Causa raíz:** `MultiSplit::new` aceptaba `&Vec<f32>` y asumía que `splits.len() == panels.len() - 1`. El estado persistido quedaba desincronizado al eliminar un indicador.

**Fix:**
- `splits: &'a Vec<f32>` → `splits: &'a [f32]`
- Truncar el slice en el call site: `&state.layout.splits[..expected_splits.min(splits.len())]`
- Eliminada la aserción estricta

**Archivos:** `src/widget/multi_split.rs`, `src/chart.rs`

---

## 3. Error toasts en scroll/zoom

**Síntoma:** 4+ toasts "Fetch error: Request was rejected" cada vez que el usuario hace scroll o zoom.

**Causa raíz:** Scroll/zoom lanza requests de datos. Si el rango solapa una request pendiente, o Binance rechaza el rango (sin historial tan atrás), el error subía como notificación visible al usuario.

**Fix:** En el handler de `Message::FetchFailed`: si `error.contains("rejected") || error.contains("overlaps")` → log DEBUG y no mostrar toast. Errores reales siguen mostrando notificación.

**Archivo:** `src/screen/dashboard.rs`

---

## 4. Panic de lyon_path — strategy overlay

**Síntoma:** `assertion failed: p.y.is_finite()` en `lyon_path-1.0.16/src/path.rs:812` al arrancar con strategy overlay activado.

**Causa raíz:** `draw_strategy_overlay` calculaba `entry_y, stop_y, target_y` con `price_to_y` y los pasaba directamente a `Path::line` sin verificar que fueran finitos. Con el chart sin datos, los valores podían ser NaN.

**Fix:**
```rust
if !entry_y.is_finite() || !stop_y.is_finite() || !target_y.is_finite() {
    continue;
}
```

**Archivo:** `src/chart/kline.rs`

---

## 5. Panic de lyon_path — overlay drawing (x sin guardar)

**Síntoma:** Mismo panic en el sistema de overlays de indicadores.

**Causa raíz:** Solo se verificaba `y.is_finite()` pero no `x.is_finite()`. El valor `x = interval_to_x(key)` podía ser NaN en casos extremos.

**Fix:** Añadido `!x.is_finite()` a todas las guardas en `draw_indicator_overlays`:
- Pase forward de bandas
- Pase reverse de bandas
- Loop de puntos de línea

**Archivo:** `src/chart/kline.rs`

---

## 6. Panic de lyon_path — PENDIENTE de resolver

**Síntoma:** El mismo `p.y.is_finite()` sigue ocurriendo al arrancar (~8s después del launch, cuando carga el metadata de tickers).

**Estado:** No identificado aún. `RUST_BACKTRACE=1` está activo en `run.bat`. El usuario necesita copiar el backtrace completo para localizar el call site exacto.

**Sospechosos principales:**
- `LinePlot::draw` en `src/chart/indicator/plot/line.rs:155` — usa `Path::line(Point::new(px, py), ...)` sin verificar `py.is_finite()`
- `BarPlot::draw` en `src/chart/indicator/plot/bar.rs` — misma situación

---

## 7. ATR — error de deref en pattern

**Fix:** `for (i, (&time, dp))` → `for (i, (time, dp))`, `result.insert(**time, ...)` → `result.insert(time, ...)`

**Archivo:** `src/chart/indicator/kline/atr.rs`

---

## 8. Scoring — ambigüedad de tipo float

**Fix:** `let mut score = 0.0` → `let mut score: f64 = 0.0`

**Archivo:** `src/strategy/scoring.rs`
