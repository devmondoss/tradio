# Fases de Implementación — Flowsurface

Documento de referencia de todo lo implementado en el proyecto, organizado en 4 fases mayores.
Actualizado: 2026-05-14.

---

## FASE 1 — Strategy Module (shadow-only detection)

**Objetivo:** Sistema de detección microestructural que visualiza señales en el chart sin ejecutar órdenes reales. Mide MFE/MAE por detector para calibrar con datos antes de arriesgar capital.

### Sub-fases completadas

| Sub-fase | Descripción | Commit |
|----------|-------------|--------|
| 1 | 3 detectores base + tipos de dominio | c15bf0b |
| 2 | Overlay rendering de señales en kline chart | 1bca2ad |
| 3 | Wired a depth updates (datos en tiempo real) | 0f41046 |
| 4 | Valores reales de indicadores al contexto | afbe52d |
| 5 | CVD slope, HVN/LVN nearby, AVWAP break-of-structure | b3ff7b5 |
| 6 | Regime via OLS, failed acceptance, footprint absorption | 6fadc8c |
| 7 | UI toggle — botón ⭐ en toolbar del kline chart | 2714b96 |
| 8 | OutcomeTracker MFE/MAE → paper_trades.jsonl | d0cfc12 |

### Detectores implementados

**LvnLiquidityVacuumBreakout** — detecta rotura de zonas de bajo volumen (LVN) con confirmación de CVD, alineación de delta y regime favorable.

**VwapMomentumPullback** — pullback a VWAP con momentum de CVD alineado, spread bajo y presencia de zona de valor nearby.

**ValueAreaFailedAuction** — precio entra al área de valor (VAH/VAL) y es rechazado, señalando falla de subasta como potencial reversión.

### Arquitectura de archivos

```
src/strategy/
├── types.rs          — StrategyMarketContext, StrategySignal, Regime, OrderFlowContext
├── adapter.rs        — build_context(), build_order_flow(), derive_regime()
├── router.rs         — dispatcher de detección, toxic_flow_gate, scoring
├── scoring.rs        — scoring compuesto 0.0–1.0
├── tracker.rs        — OutcomeTracker, seguimiento MFE/MAE por (strategy_id, side)
├── logger.rs         — escritura a JSONL (paper_trades, strategy_signals, contradictions)
└── detectors/
    ├── lvn_breakout.rs
    ├── vwap_pullback.rs
    └── value_area.rs
```

### Datos producidos

Guardados en `%APPDATA%\flowsurface\shadow_events\`:

- `paper_trades.jsonl` — trades cerrados con outcome (net_pnl, close_reason, mfe, mae, contexto completo)
- `strategy_signals.jsonl` — señales emitidas (score, detector, timestamp)
- `contradictions.jsonl` — cuando dos detectores señalan en direcciones opuestas al mismo tiempo

### Gates de calidad (en router.rs)

- **VPIN gate**: descarta señales con VPIN > 0.75 (flujo tóxico)
- **Spread gate**: descarta con spread > 2.5 bps
- **Score mínimo**: descarta si score compuesto < 0.60
- **Max concurrent positions**: 1 posición por símbolo a la vez

---

## FASE 2 — Indicadores visuales y overlays en chart

**Objetivo:** Agregar contexto visual directamente sobre el canvas del chart de velas: VWAP con bandas, Volume Profile, OI Delta, Key Levels diarios/semanales, y Session Lines con resaltado por mercado.

### 2.1 VWAP + bandas de volatilidad

**Archivo:** `src/chart/indicator/kline/vwap.rs` y overlay en `src/chart/kline.rs`

- VWAP anchoreado por sesión UTC diaria (reset a las 00:00 UTC)
- Bandas ±1σ y ±2σ calculadas sobre desviación típica running
- Struct `VwapPoint { price, upper1, lower1, upper2, lower2 }`
- Método `visible_points(earliest, latest)` para recortar al rango visible
- Colores: línea central `rgba(0.20, 0.75, 1.0, 0.95)`, banda ±1σ `rgba(0.0, 0.55, 1.0, 0.09)`, banda ±2σ `rgba(0.0, 0.55, 1.0, 0.05)`

### 2.2 Volume Profile en overlay

**Archivo:** `src/chart/indicator/kline/volume_profile.rs`

- Histograma de 150 bins, ventana de 300 velas
- Calcula POC (mayor volumen), VAH/VAL (70% del volumen = Value Area), HVN/LVN
- `ProfileBar` struct con campos `price, buy_vol, sell_vol, is_poc, is_hvn, is_lvn`
- Renderizado como barras horizontales en el 14% derecho del canvas
- Colores diferenciados: POC dorado, VAH/VAL verde, HVN azul, LVN rojo

### 2.3 OI Delta

**Archivo:** `src/chart/indicator/kline/oi_delta.rs`

- `KlineIndicator::OiDelta` — nuevo tipo de indicador en panel separado debajo del chart
- Calcula `OI[n] - OI[n-1]` para mostrar cambio de open interest por vela
- Barras verdes = OI aumentando (nuevas posiciones), rojas = OI cayendo (cierre de posiciones)
- Solo disponible para perps (futuros perpetuos); se deshabilita para spot

### 2.4 Key Levels (PDH/PDL/DO/WO)

**Archivo:** `src/chart/kline.rs` — `draw_key_levels()` + `compute_key_levels()`

- **PDH** (Previous Day High) — máximo del día anterior
- **PDL** (Previous Day Low) — mínimo del día anterior
- **DO** (Daily Open) — apertura de hoy a 00:00 UTC
- **WO** (Weekly Open) — apertura del lunes de esta semana a 00:00 UTC
- Líneas horizontales punteadas que cruzan todo el ancho del chart
- **Tooltip en hover**: al acercar el cursor a la zona derecha (últimos 60px) y estar a ≤10px de una línea, aparece un cuadro con el nombre completo y descripción del nivel
- Controlado por `Config::show_key_levels` (toggle en settings modal)

### 2.5 Session Lines / Session Rectangles

**Archivo:** `src/chart/kline.rs` — `draw_session_lines()`

- Rectángulos semitransparentes coloreados por sesión de mercado:
  - **Asia** (00:00–08:00 UTC) — periwinkle pastel `rgba(0.72, 0.82, 1.00, 0.08)`
  - **London** (08:00–13:00 UTC) — durazno pastel `rgba(1.00, 0.82, 0.68, 0.08)`
  - **New York** (13:00–22:00 UTC) — menta pastel `rgba(0.68, 0.95, 0.78, 0.08)`
- Borde izquierdo sólido en la apertura de cada sesión (alpha 0.35)
- Etiqueta de texto en la esquina superior izquierda de cada rectángulo
- Solo se dibuja para timeframes ≤ 4h (en timeframes más largos no es significativo)
- Sesiones que salen del rango visible se recortan correctamente
- Controlado por `Config::show_session_lines` (toggle en settings modal)

### 2.6 Settings modal con toggles

**Archivos:** `data/src/chart/kline.rs`, `src/chart/kline.rs`, `src/screen/dashboard/pane.rs`, `src/modal/pane/settings.rs`

- `data::chart::kline::Config { show_key_levels: bool, show_session_lines: bool }` — serializable, defaults `true`
- Campo `config: Config` agregado a `KlineChart` struct
- Dos eventos nuevos en `pane::Event`: `ToggleKeyLevels`, `ToggleSessionLines`
- Settings modal (ícono ⚙ en la toolbar) para `KlineChartKind::Candles` ahora muestra dos checkboxes en vez del placeholder "WIP..."

### Arquitectura del overlay rendering

En `src/chart/kline.rs`, función `draw_indicator_overlays()`:

1. Para cada indicador overlay (Vwap, VolumeProfile): dibuja bandas → línea central → niveles horizontales → histograma
2. `draw_key_levels()` llamada después, condicional a `config.show_key_levels`
3. `draw_session_lines()` llamada después, condicional a `config.show_session_lines`
4. Tooltip de key levels en la capa crosshair (se redibuja en cada movimiento del cursor)

**Guarda NaN en todo punto de canvas** — lyon_path hace `assert!(p.y.is_finite())` internamente.

---

## FASE 3 — Infraestructura y corrección de build

**Objetivo:** Hacer que el proyecto compile y corra correctamente en Windows con el toolchain GNU (x86_64-pc-windows-gnu) usando WinLibs POSIX.UCRT MinGW.

### 3.1 Problema de linker: nanosleep64

**Síntoma:** `cargo test --workspace` fallaba con `undefined reference to nanosleep64`.

**Causa:** `aws-lc-sys` se compiló con WinLibs gcc (headers POSIX.UCRT que tienen `nanosleep64`), pero el sysroot MinGW bundleado con Rust no tiene esa función.

**Solución** (`exchange/src/lib.rs`):
```rust
#[cfg(all(windows, target_arch = "x86_64"))]
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nanosleep64(_rqtp: *const u8, _rmtp: *mut u8) -> i32 {
    0
}
```
Stub que satisface al linker. La función nunca se llama en runtime (es código de backoff de threading, nunca activo en tests).

### 3.2 Problema de runtime: CryptoProvider panic

**Síntoma:** La app crasheaba al intentar conectar con los exchanges. `rustls 0.23` requiere instalar explícitamente el crypto provider antes de llamar `ClientConfig::builder()`.

**Solución** (`exchange/src/adapter/connect.rs`):
```rust
fn tls_connector() -> Result<TlsConnector, AdapterError> {
    let _ = aws_lc_rs::default_provider().install_default();
    // ...
}
```

### 3.3 run.bat mejorado

**Archivo:** `run.bat`

- Agrega WinLibs bin al PATH antes de compilar (evita que cargo use el linker del sysroot Rust)
- Acepta argumentos: `.\run.bat --release` funciona para builds de release
- `RUST_BACKTRACE=1` siempre activo

### 3.4 Actualización de dependencias TLS

`tokio-rustls` bumpeado de `0.24.1` a `0.26.4` (commit 17e2412). Esta versión introduce `aws-lc-sys` como crypto backend, que es la raíz de los problemas del linker. Se mantuvo la versión nueva (mejor mantenimiento de seguridad) y se resolvió el linker con el stub.

---

## FASE 4 — Análisis de outcomes (scripts/analyze_outcomes.py)

**Objetivo:** Script de diagnóstico que lee los JSONL producidos por el motor de paper trading y responde preguntas de calibración concretas sobre qué funciona y por qué.

### Cómo ejecutar

```bash
python scripts/analyze_outcomes.py              # datos reales desde %APPDATA%
python scripts/analyze_outcomes.py C:\ruta\dir  # directorio alternativo
python scripts/analyze_outcomes.py --test       # datos sintéticos (verificación)
```

### D1 — Métricas base (Bloques 1–6)

| Bloque | Pregunta |
|--------|----------|
| 1 | ¿El sistema gana o pierde? Equity curve, drawdown máximo, PnL total |
| 2 | ¿Qué detector funciona? Win rate, R-múltiplo, distribución TARGET/STOP/TTL por detector |
| 3 | ¿El scoring discrimina? Histograma de scores emitidos + rendimiento por bucket desde trades |
| 4 | ¿Cuánto cuestan los costos? Fees, funding, slippage — cuánto se comen del bruto |
| 5 | ¿Distribución MFE/MAE? Excursiones en R-múltiplos; ratio MFE/MAE |
| 6 | ¿Cuántas contradicciones? Por símbolo, por par de detectores, por resolución |

### D2 — Análisis condicional (Bloques 7–11)

| Bloque | Pregunta |
|--------|----------|
| 7 | ¿Los detectores funcionan en algunos regímenes pero no en otros? Detector × Regime |
| 8 | ¿El score discrimina realmente? Buckets finos 0.60–1.00 + Pearson r si N≥30 |
| 9A | ¿El VPIN en el momento de la señal predice el resultado? Limpio / Neutro / Tóxico |
| 9B | ¿El spread en el momento de la señal importa? Tight (<1.0) / Medio / Wide (>1.5 bps) |
| 9C | ¿Funciona mejor cuando delta está alineado con el side? Alineado vs no alineado |
| 10 | ¿Los detectores cierran por las razones correctas? Interpretación textual `[ok]` / `[!]` / `[~]` |
| 11 | ¿El MFE llega cerca del target? MFE-R vs Target-R por detector, candidato a trailing stop |

### Criterio "muestra chica"

- En D1 (Bloque 2): N < 10 se marca con `[muestra chica]`
- En D2 (Bloques 7–11): N < 5 se marca con `[muestra chica]`
- Cualquier análisis con muestra chica se muestra pero no se debe usar para tomar decisiones

### Campos esperados en paper_trades.jsonl

| Campo | Tipo | Fuente |
|-------|------|--------|
| `strategy_id` | string | nombre del detector |
| `regime` | string | Compression, Expansion, TrendUp, TrendDown, Chop, Unknown |
| `score` | float 0–1 | scoring compuesto del router |
| `vpin` | float 0–1 | VPIN en el momento de la señal |
| `spread_bps` | float | spread en bps en el momento de la señal |
| `side` | string | Long / Short |
| `delta` | float | delta acumulado en el momento de la señal (opcional) |
| `close_reason` | string | TARGET_HIT / STOP_HIT / TTL_EXPIRED |
| `entry_price`, `stop_price`, `target_price` | float | precios del trade |
| `size` | float | tamaño en contratos/coins |
| `net_pnl` | float | PnL neto en USD |
| `net_pnl_pct` | float | net_pnl / capital_inicial × 100 |
| `mfe`, `mae` | float | excursiones en unidades de precio (no R, el script convierte) |
| `closed_at_ms` | u64 | timestamp de cierre en Unix ms |

---

## Estado actual del proyecto

### Completado

- [x] Strategy module completo (8 sub-fases, 3 detectores, paper trading, outcome tracker)
- [x] VWAP con bandas ±1σ/±2σ como overlay en chart
- [x] Volume Profile (POC/VAH/VAL/HVN/LVN) como overlay en chart
- [x] OI Delta como panel indicador
- [x] Key Levels (PDH/PDL/DO/WO) con tooltip explicativo al hover
- [x] Session Lines como rectángulos pasteles (Asia/London/NY) con etiquetas
- [x] Settings modal con toggles para Key Levels y Session Lines
- [x] Build funcional en Windows GNU (nanosleep64 stub + CryptoProvider fix)
- [x] analyze_outcomes.py D1 — métricas base (bloques 1–6)
- [x] analyze_outcomes.py D2 — análisis condicional (bloques 7–11)

### Pendiente prioritario

- [ ] Funding rate panel (endpoint REST por exchange)
- [ ] OI z-score (rolling, dentro del indicador OI existente)
- [ ] Manual AVWAP anchor (clic en chart → timestamp → ancla)
- [ ] Session VWAPs separados (Asia / London / NY)
- [ ] Regime mejorado (EMA 21/55 crosses, ATR squeeze)
- [ ] Simulación intrabar (actualmente cierra al cierre de vela, no tick-by-tick)

---

## Referencia de archivos por fase

| Fase | Archivos principales |
|------|---------------------|
| 1 — Strategy | `src/strategy/*.rs`, `src/chart/kline.rs` (run_strategy_detection) |
| 2 — Overlays | `src/chart/indicator/kline/*.rs`, `src/chart/kline.rs` (draw_*) |
| 2 — Settings | `data/src/chart/kline.rs`, `src/modal/pane/settings.rs`, `src/screen/dashboard/pane.rs` |
| 3 — Build | `exchange/src/lib.rs`, `exchange/src/adapter/connect.rs`, `run.bat` |
| 4 — Analysis | `scripts/analyze_outcomes.py` |

## Docs relacionados

- `docs/DRR_PRESENTE_Y_FUTURO.md` — referencia canonica del estado live actual de DRR
- `docs/ARQUITECTURA.md` — arquitectura general del proyecto
- `docs/CHARTS.md` — sistema de charts y rendering
- `docs/RENDERING.md` — pipeline de rendering canvas
- `docs/DATOS.md` — fuentes de datos y adaptadores de exchange
- `docs/BUILD.md` — setup de compilación y toolchain
- `docs/BUGS_Y_FIXES.md` — historial de bugs y cómo se resolvieron
- `docs/README.md` — indice canonico de documentacion vigente e historica
- `docs/DETECTOR_SOURCE.md` — fuentes y referencias de los detectores
