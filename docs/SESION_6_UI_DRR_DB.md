# Sesión 6 — UI local DRR + DB migrada + sistema en producción

*Fecha: 2026-05-29 (continuación sesión 5)*

---

## Lo que se hizo en esta sesión

### 1. Diagnóstico del deploy anterior

Se verificó que el build de Railway fallaba con 3 errores después del commit de DRR:

| Error | Causa | Fix |
|-------|-------|-----|
| `recursion_limit reached` en `json!()` | 133 campos supera el límite 256 | `#![recursion_limit = "512"]` en main.rs |
| `unexpected end of macro invocation` en `build_parallel_signal_row` | `let` binding dentro del valor de `json!()` — serde_json trata `{` como objeto JSON | Extraer `let rr = ...` antes del macro |
| `no field 'vp'`, `no field 'atr_wilder'` en BarState | Nombres de campo inventados | Usar variables locales `poc` y `atr` ya en scope |
| `TradingSession::Off` | Variante inexistente — es `OffHours` | Corregir a `OffHours` |

Commit de fix: `4c9bfbd` → Railway buildeo verde.

---

### 2. Revisión de la DB antes del grand deploy

Se consultó Supabase y se encontraron datos del sistema Subdimi viejo (router anterior con FAR/LIQ):

**13 señales, 15 outcomes, win rate ~15%:**
- LIQ: 10 señales, 0 ganadores reales. 3 Longs back-to-back en 76k todos INVALIDATED. 4 Shorts en 77.4k todos STOP_HIT.
- FAR: 3 señales, 1 ganador parcial (+$0.43 neto).
- PnL total: ≈ −$35.52

Diagnóstico claro: LIQ disparaba señales duplicadas, el sistema no tenía disciplina de no-trade zone.

---

### 3. Limpieza total de la DB

Ejecutado en Supabase SQL Editor:

```sql
DELETE FROM signal_outcomes;
DELETE FROM shadow_signals;
DELETE FROM lab_signals;
DELETE FROM regime_history;
DELETE FROM institutional_snapshots;
DELETE FROM intrabar_outcomes;
```

Sistema arranca en cero el 2026-05-29. Todos los datos que entren de aquí en adelante son de DRR.

---

### 4. Migraciones SQL ejecutadas

**Problema:** `migration_micro_windows.sql` tenía la vista `v_micro_with_outcomes` con errores:
- `ss.symbol` — columna inexistente en `shadow_signals`
- `ss.entry_type`, `ss.session_name`, etc. — columnas que solo existen después de `migration_drr.sql`

**Fix:** Quitar `ss.symbol` del JOIN, simplificar la vista a columnas base, documentar el orden correcto de ejecución.

**Orden de ejecución (importante):**
1. `supabase/migration_drr.sql` primero — agrega 30 columnas a shadow_signals
2. `docs/migration_micro_windows.sql` segundo — crea micro_windows + vista

**Después:** `v_micro_with_outcomes` recreada con `DROP VIEW + CREATE VIEW` para incluir las columnas DRR (session_name, absorption_count, entry_type).

**Estado final de tablas:**
```
calibration_log        — vacía
deployed_params        — config activa
institutional_snapshots — acumulando
intrabar_outcomes      — vacía
lab_outcomes           — vacía (legacy, sin uso)
lab_signals            — Subdimi Parallel signals
micro_windows          — 288 rows/día ✅
regime_history         — acumulando
shadow_signals         — señales DRR con 30 campos
signal_outcomes        — trades cerrados
v_micro_with_outcomes  — vista completa
v_signals_with_outcomes — vista principal de análisis
```

---

### 5. Verificación del sistema en producción

A los 23 minutos del deploy:
- `regime_history`: 1 entrada `TrendUp` → monitor corriendo ✅
- `micro_windows`: 2 rows con datos reales:
  - vol_total=1447 BTC, late_surge_ratio=2.33 (back-loaded)
  - vol_total=351 BTC, late_surge_ratio=0.63 (front-loaded)
- `in_drr_zone = null` → normal, RangeDetector necesita 20+ barras (~100 min) para validar rango
- `shadow_signals` vacío → normal, DRR aún no disparó

---

### 6. Documentación actualizada

**Actualizado:** `docs/ESTADO_CHECKLIST.md` — estado real al 2026-05-29
**Creado:** `docs/DRR_IMPLEMENTACION.md` — bitácora técnica completa de la implementación DRR

---

### 7. UI local — DRR range overlay + HUD panel

Con todo el sistema en producción, se implementaron los overlays visuales para ver la lógica del backend directamente en el chart local.

**`DrrHudState`** — nuevo struct que captura un snapshot del ctx en cada bar close:
- regime, session_name, session_phase, vp_bias, auction_state
- range_valid, range_location, range_size_atr, touches_hi/lo, sweep_low/high
- absorption signals individuales (footprint, big_trade, cvd_divergence, finish_action, sweep)
- absorption_count (0-5), cvd_slope, vpin, delta_velocity

Poblado en `run_strategy_detection()` después de construir el ctx, sin costo adicional.

**`draw_drr_range()`** — overlay en el canvas del chart:

```
Range High  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─  [línea dashed naranja, 1.5px]
              █████ no-trade zone ████  [fondo amber sutil, 35-65%]
Range Mid   · · · · · · · · · · · ·   [línea punteada naranja, 0.35 alpha]
              █████ no-trade zone ████
Range Low   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─  ▲ [triángulo verde si sweep_range_low]
```

Labels: `RH 95000`, `RL 94500`, `T 3/4` (toques high/low).

**`draw_drr_hud()`** — panel de texto top-right (200px × ~120px, fondo semi-transparente):

```
Regime   TrendUp          ← verde/rojo/amarillo según estado
Session  London / Open
VPBias   InsideValue
Auction  Balance
Range    NearLow (1.8×ATR)
Sweep    ▲ sweep low
Absorb   3/5 [F.B.C.X.S]  ← F=footprint B=bigTrade C=cvdDiv X=finish S=sweep
CVD slp  -0.12
```

Color de absorción: verde si ≥4, amarillo si 2-3, naranja si 1, gris si 0.

**Ambos overlays se activan con el mismo toggle de strategy overlay** — no hay nuevo botón.

---

## Estado del sistema al cierre de la sesión

| Componente | Estado |
|---|---|
| Railway deploy | ✅ Corriendo (commit `52788b0`) |
| micro_windows | ✅ Acumulando 288 rows/día |
| shadow_signals | ✅ Schema completo, esperando primera señal DRR |
| UI local | ✅ Range overlay + HUD activos |
| DB | ✅ Todas las tablas completas y limpias |

**Primera señal DRR esperada:** cuando el RangeDetector valide un rango (100+ min de barras) y el precio llegue a un extremo con absorción confirmada. Puede ser hoy o mañana.

---

## Pendientes para sesiones futuras

| Prioridad | Item |
|---|---|
| 🔬 | Acumular 100+ señales DRR cerradas con R para primera evaluación |
| 🔵 | Wirear `CandleMicroBuffer` en la UI local para visualizar los 5 buckets en tiempo real |
| 🔵 | Query de win rate por sesión/absorción cuando haya ≥50 señales |
| 🔵 | Calibrar parámetros del RangeDetector con datos reales (slope threshold, size_atr range) |
