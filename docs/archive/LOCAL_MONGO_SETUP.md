# Setup Local + MongoDB

**Fecha de escritura**: 2026-05-20  
**Estado actual (2026-05-29)**: El monitor de producción corre en **Railway + Supabase** (headless, 24/7). MongoDB local es para desarrollo: permite correr el binario `flowsurface` (UI) con detección activa sin depender de Railway. Ambos modos usan el mismo crate `data/`.

---

## 1. ¿Qué cambió respecto a Railway?

El sistema que corría 24/7 headless en Railway con Supabase ahora corre **dentro de la UI** local con **MongoDB local**, conservando lógica de detección idéntica (crate compartido `data/`).

```
ANTES (Railway, headless)                       AHORA (local, con UI)
─────────────────────────                       ─────────────────────
Railway servidor 24/7                           Tu PC
└─ monitor (Rust headless)                      ├─ MongoDB localhost:27018
   ├─ Binance WebSocket                         └─ Flowsurface UI (iced)
   ├─ 8 detectores @ bar close                     ├─ Binance WebSocket   (mismo)
   ├─ ESCRIBE → Supabase shadow_signals             ├─ 8 detectores @ bar close   (mismo código)
   ├─ ESCRIBE → Supabase signal_outcomes            ├─ ESCRIBE → Mongo shadow_signals
   └─ LEE     ← Supabase deployed_params             ├─ ESCRIBE → Mongo signal_outcomes
                                                      ├─ LEE     ← Mongo deployed_params
                                                      └─ ⭐ Visualización en charts (overlay)
```

| Aspecto | Railway | Local |
|---|---|---|
| Lógica detección | crate `data/` | **Mismo** crate `data/` |
| Persistencia | Supabase cloud | **MongoDB localhost** |
| Config por régimen | Supabase | **MongoDB** |
| Scripts Python | Supabase | **MongoDB** (Supabase fallback opcional) |
| Visualización | Solo logs | **Charts + overlays + paper PnL en vivo** ⭐ |
| Uptime | 24/7 servidor | Mientras la UI esté abierta |
| Costo | Railway + Supabase | Cero (todo local) |

El binario [`crates/monitor`](../crates/monitor/) sigue existiendo y aún apunta a Supabase — se dejó intacto por si quieres volver a desplegar en Railway en paralelo. La UI no lo necesita.

---

## 2. Stack instalado en local

| Componente | Versión | Cómo se levanta |
|---|---|---|
| Rust toolchain GNU | 1.95 | Ver [BUILD.md](BUILD.md) |
| MongoDB | 8.x | **Instancia dedicada** en puerto **27018** (no la default `:27017`), datapath aislado en `%LOCALAPPDATA%\flowsurface\mongo-data`. Arrancar con [`start-mongo.bat`](../start-mongo.bat) desde la raíz del repo. Aislado de cualquier otra herramienta que use el `:27017` por defecto |
| Python | 3.13 | `pymongo>=4.0` (ya en `calibration/requirements.txt`) |
| Compass (GUI Mongo) | opcional | para inspeccionar colecciones |

Variables de entorno (todas con default razonable):

| Variable | Default | Uso |
|---|---|---|
| `MONGODB_URI` | `mongodb://localhost:27018` | conexión al mongod dedicado de flowsurface |
| `MONGODB_DB` | `flowsurface` | base a usar |
| `ANALYTICS_BACKEND` | `mongo` | scripts Python: `mongo` o `supabase` |
| `FLOWSURFACE_FORCE_STRATEGY` | — | fuerza overlay aunque la config persistida tenga `false` |
| `SUPABASE_URL` / `SUPABASE_KEY` | — | opcionales; si los seteas, los scripts de seed/deploy escriben también a Supabase en paralelo |

---

## 3. Arranque en frío (paso a paso)

```powershell
# 1. Arrancar el mongod dedicado de flowsurface (puerto 27018, aislado)
.\start-mongo.bat       # deja esta ventana abierta; cerrarla detiene mongod

# 2. Verificar que respondió
python -c "from pymongo import MongoClient; print(MongoClient('mongodb://localhost:27018').server_info()['version'])"

# 3. Sembrar parámetros conservadores por régimen (4 docs en `deployed_params`)
python scripts\seed_deployed_params.py

# 4. En otra ventana: compilar y correr la UI
.\run.bat
```

Lo que debes ver en la consola en los primeros ~10 segundos:

```
[mongo] writer connected → mongodb://localhost:27018 / db=flowsurface
[mongo-cfg] loader connected → mongodb://localhost:27018 / db=flowsurface
```

Si no aparecen, ver [§9 Troubleshooting](#9-troubleshooting).

---

## 4. Las tres piezas que hubo que enchufar (y por qué)

El detector vivía en la UI desde antes, pero estaba apagado por defaults conservadores y nunca había sido validado end-to-end con un usuario real. La migración a local requirió tres ajustes para que `run_strategy_detection` realmente corriera en cada cierre de barra.

### Fix #1 — `strategy_overlay_enabled` arranca en `true`

[`data/src/chart/kline.rs`](../data/src/chart/kline.rs) — el flag de overlay defaultea ahora a `true` (antes `false`). Sin esto, la detección estaba gated en [`src/chart/kline.rs:535`](../src/chart/kline.rs#L535) y nunca ejecutaba `run_strategy_detection`.

Override por env var: `FLOWSURFACE_FORCE_STRATEGY=1` bypasea el gate aunque tu layout persistido tenga el flag explícito en `false`.

### Fix #2 — Stream `Depth` automático para el kline pane con overlay

El detector hace `let Some(depth) = &self.last_depth else { return; }` en [`src/chart/kline.rs:1201`](../src/chart/kline.rs#L1201). Pero un kline pane típico se suscribe solo a `[Kline, Trades]` — el `Depth` vive en el pane Heatmap. Resultado: `last_depth = None` y la detección abortaba en su primera línea.

Solución: [`State::ensure_strategy_depth_stream()`](../src/screen/dashboard/pane.rs) añade un `StreamKind::Depth` al pane kline si el overlay está activo y aún no lo tiene. Se invoca:
- al togglear el overlay,
- en el `tick` del dashboard (auto-cura tras arranque),
- desde el path de `ToggleLinkedStrategyOverlay` cuando se vincula via Strategy Monitor.

Es idempotente — una vez añadido no vuelve a disparar.

### Fix #3 — Indicadores requeridos se instancian automáticamente

`run_strategy_detection` necesita los indicadores `ATR`, `VWAP`, `VolumeProfile`, `CumulativeDelta`, `Volume` para construir el `StrategyMarketContext`. Sin ATR, cada barra se rechaza con `ATR_NOT_READY`. La función `toggle_strategy_overlay()` declaraba la constante `STRATEGY_INDICATORS` pero **retornaba `vec![]`** (feature incompleto).

Solución: [`KlineChart::ensure_strategy_indicators()`](../src/chart/kline.rs) instancia los faltantes sin tocar el flag del overlay. Llamada:
- desde `toggle_strategy_overlay()` cuando se enciende,
- desde el `tick` del dashboard (auto-cura para layouts persistidos con overlay=true pero lista de indicadores vacía).

Tras este fix, las razones de rechazo en `strategy_rejected.jsonl` pasaron de `ATR_NOT_READY` (1 razón) a las específicas de cada detector (`VAFA:SESSION_INVALID`, `LVN:SESSION_INVALID`, etc.) — prueba de que los 8 detectores evalúan barras completas con contexto.

---

## 5. Capas de configuración (fuente única de verdad)

Antes de la migración los 25 parámetros de `StrategyConfig` estaban hardcodeados en `impl Default` y divergían entre UI y monitor. Ahora hay tres capas, de menor a mayor precedencia:

```
1. impl Default en types.rs           ← fallback de emergencia (si el TOML falta)
2. config/strategy.toml                ← FUENTE BASE canónica (editar AQUÍ)
3. Mongo `deployed_params` por régimen ← override de calibración
```

- **TOML**: [`config/strategy.toml`](../config/strategy.toml), versionado en git, agrupado por estrategia con comentarios. Cargado vía `StrategyConfig::load()` en [`data/src/strategy/config_file.rs`](../data/src/strategy/config_file.rs).
- **Mongo override**: la UI llama `mongo_handles().loader.request_reload(regime)` cuando cambia el régimen. El loader corre en un thread dedicado y publica el `StrategyConfig` actual en un `Arc<RwLock<_>>` que la UI lee sin bloquearse.

Tests automatizados de la integración: [`data/src/strategy/config_file.rs::tests`](../data/src/strategy/config_file.rs).

---

## 6. Esquema MongoDB (`db=flowsurface`)

| Colección | Quién escribe | Quién lee | Ciclo de vida |
|---|---|---|---|
| `deployed_params` | scripts Python (seed / param_deployer) | UI loader, monitor (si Railway activo) | 1 doc activo por régimen |
| `shadow_signals` | UI (`MongoWriter::write_signal`) | scripts Python análisis | Append-only |
| `signal_outcomes` | UI (`MongoWriter::write_trade`) con FK al signal `_id` | scripts Python análisis | Append-only |
| `lab_signals` / `lab_outcomes` | (no escrito desde Rust aún — feature pendiente) | scripts Python lab | — |

### `deployed_params` (formato esperado por el loader Rust)

```json
{
  "regime": "TrendUp",
  "strategy": null,
  "is_active": true,
  "params": {
    "min_rr": 1.8,
    "min_score": 0.70,
    "max_spread_bps": 2.0,
    "liq_hunt_min_usd": 25000.0,
    "cooldown_bars": 5
  },
  "calibration_id": "seed-conservative-2026-05-20",
  "updated_at": "ISODate(...)"
}
```

Solo los campos presentes en `params` sobreescriben el TOML base — todo lo demás conserva los valores del archivo. Ver `apply_overrides()` en [`data/src/strategy/mongo_config_loader.rs`](../data/src/strategy/mongo_config_loader.rs).

### `shadow_signals` (un doc por señal con `action ≠ Wait`)

`_id` es un `ObjectId` generado **en la UI** (no esperamos round-trip de Mongo) y se preserva en `pending_signal_oid` para enlazar el trade futuro.

Campos principales: `symbol`, `strategy`, `side`, `regime_combined`, `action`, `entry_price`, `stop_price`, `target_price`, `score`, `ttl_ms`, `evidence[]`, `missing[]`, más todo el contexto técnico (VWAP, POC/VAH/VAL, CVD, OBI, etc.) y el institucional (liquidaciones, L/S, OI, funding). Ver `build_signal_doc` en [`data/src/strategy/mongo_writer.rs`](../data/src/strategy/mongo_writer.rs).

### `signal_outcomes` (un doc por trade cerrado)

FK: `signal_id` apunta al `_id` de `shadow_signals`. Calcula `r_multiple`, `mfe_r`, `mae_r` a partir del trade. Permite multi-leg (TP1_PARTIAL + segundo cierre) que se agregan en el adapter de análisis.

---

## 7. Arquitectura del writer/loader Rust

**Reto**: la UI corre sobre `iced` (no async como el monitor tokio).

**Solución**: thread dedicado con runtime tokio `current_thread` + canal `mpsc`. La UI hace `tx.send()` y sigue, sin esperar. Errores se loguean a `stderr`, nunca rompen la UI.

```rust
// src/strategy/mod.rs
static MONGO: OnceLock<MongoHandles> = OnceLock::new();
pub fn mongo_handles() -> &'static MongoHandles { ... }  // perezoso, una sola conexión por sesión
```

Componentes:
- **[`MongoWriter`](../data/src/strategy/mongo_writer.rs)**: thread `mongo-writer`, escribe a `shadow_signals` y `signal_outcomes`. API: `write_signal(...)`, `write_trade(...)`.
- **[`MongoConfigLoader`](../data/src/strategy/mongo_config_loader.rs)**: thread `mongo-config-loader`, lee `deployed_params` por régimen, publica `StrategyConfig` en `Arc<RwLock<_>>`. API: `current()`, `request_reload(regime)`.

Wireado en [`src/chart/kline.rs::run_strategy_detection`](../src/chart/kline.rs): mismo patrón que el monitor de Railway (snapshot del oid antes de `paper.on_bar_close`, iterar trades nuevos en `closed_trades[prev..]`, escribir signal después).

---

## 8. Pipeline Python adaptado

Todos los scripts que tocaban Supabase ahora hablan Mongo por default. Switcheable vía `ANALYTICS_BACKEND`.

| Script | Cambio |
|---|---|
| [`calibration/core/mongo_db.py`](../calibration/core/mongo_db.py) | **Nuevo** — helper `get_db()`, `deploy_params_to_mongo()`, y un cliente `mongo_client_compat()` con API estilo Supabase (`.table().select().eq().gte()`) para portar otros scripts con cambio mínimo |
| [`scripts/seed_deployed_params.py`](../scripts/seed_deployed_params.py) | Escribe a Mongo siempre; a Supabase solo si hay creds |
| [`calibration/calibration/param_deployer.py`](../calibration/calibration/param_deployer.py) | Dual write (Mongo es criterio de éxito; Supabase best-effort) |
| [`scripts/mongo_to_analyze.py`](../scripts/mongo_to_analyze.py) | **Nuevo** — espejo de `supabase_to_analyze.py` con `$lookup` agregation. Alimenta `analyze_outcomes.py` desde Mongo |
| [`calibration/core/db.py`](../calibration/core/db.py) | `query_signals()` ahora resuelve backend; Mongo usa `$lookup` shadow_signals × signal_outcomes con prefijo `o_` para campos del outcome |
| 4 scripts en [`scripts/analysis/`](../scripts/analysis/) | `get_client()` delega a `analytics_client()` — un cambio de 5 líneas por script |

**Pendiente menor**: 3 scripts en `calibration/` (`monitor.py`, `degradation_monitor.py`, `kelly_sizer.py`) usan `query_df(sql)` con SQL crudo sobre vistas Postgres. Su migración requiere portar cada query a aggregation pipeline. Mientras tanto, funcionan con `ANALYTICS_BACKEND=supabase` (si tienes DATABASE_URL). Si Supabase se desconecta del todo, hay que reescribirlos.

---

## 9. Troubleshooting

### "No veo `[mongo] writer connected` en consola"

`mongo_handles()` es perezoso — solo se invoca cuando `run_strategy_detection` corre. Si no ves la línea, la detección no está corriendo. Causas comunes:

1. **Overlay apagado**: por default ahora arranca encendido. Si tu layout persistido lo tiene explícito en `false`, setea `FLOWSURFACE_FORCE_STRATEGY=1` o activa la estrella ★ en el panel Strategy Monitor.
2. **Stream no resuelve**: si tu ticker (ej. BTCUSDT) no aparece en el `tickers_info` de Binance (típicamente transitorio ~9s mientras carga metadata), la persistencia de streams reintenta cada 2s. Espera ~15s.
3. **`mongod` caído**: `python -c "from pymongo import MongoClient; print(MongoClient().server_info())"`.

### "Todos los rechazos son `ATR_NOT_READY`"

Los indicadores no se instanciaron. El auto-heal en el tick del dashboard debería arreglarlo en ≤1 segundo. Si persiste, mira [`State::ensure_strategy_indicators`](../src/screen/dashboard/pane.rs) y confirma que `Content::Kline { chart: Some(c), .. }` matchea con tu pane.

### "Todos los rechazos son `*:SESSION_INVALID`"

Esperado: el filtro de sesión rechaza fuera de Asia/London/NY. Dos opciones:
- Esperar a sesión activa (señales empezarán a aprobar).
- Apagar el filtro: en [`config/strategy.toml`](../config/strategy.toml) → `session_filter_enabled = false` → reinicia UI.

### "`deployed_params` se vació entre sesiones"

Si ocurre, confirma que estás corriendo el `mongod` dedicado de flowsurface (`start-mongo.bat`, puerto **27018**) y no el default del sistema (`:27017`). El puerto 27017 puede compartirse con otras herramientas que hacen `dropDatabase`. La instancia dedicada usa datapath aislado en `%LOCALAPPDATA%\flowsurface\mongo-data` y persiste entre ejecuciones.

### "La UI compila pero no veo el chart"

No es el alcance de esta migración. Ver [BUILD.md](BUILD.md) — típicamente linker MinGW.

---

## 10. Limitaciones conocidas

| Limitación | Workaround |
|---|---|
| Necesita la UI abierta (no 24/7) | Mantén la PC encendida con la UI maximizada, o vuelve a Railway. El [crate `monitor`](../crates/monitor/) sigue funcional |
| ~~Vulnerable a drops externos~~ | **Resuelto**: mongod dedicado en `:27018` con datapath aislado en `%LOCALAPPDATA%\flowsurface\mongo-data` |
| 3 scripts de análisis (`monitor.py`, `degradation_monitor.py`, `kelly_sizer.py`) aún en Supabase | `ANALYTICS_BACKEND=supabase` para esos; pendiente portar SQL a aggregations |
| `lab_signals` / `lab_outcomes` no escrito desde la UI Rust | Feature Lab independiente; calibration scripts pueden poblarla |
| Indicadores se auto-añaden pero no se quitan al apagar overlay | No es regresión — el toggle off no los borra; quedan visibles. Borrarlos manualmente desde la UI si molesta |

---

## 11. Cómo volver a Railway si hace falta

La migración es **aditiva** — Railway/Supabase no se rompió:

1. El crate `monitor` y `Dockerfile` siguen apuntando a Supabase y compilan limpio.
2. Si los scripts (`seed`, `param_deployer`) tienen `SUPABASE_URL`/`KEY` en el entorno, escriben a **ambas** BDs en paralelo.
3. La UI ignora Supabase totalmente.

Para activar Railway:
- Re-deploy del Dockerfile (sin cambios necesarios).
- Setear vars en Railway: `SUPABASE_URL`, `SUPABASE_KEY`.
- La UI sigue local; el monitor de Railway llena Supabase; los scripts pueden leer cualquiera vía `ANALYTICS_BACKEND`.

---

## Apéndice: tests automatizados que validan la integración

| Test | Cubre |
|---|---|
| `cargo test --workspace` | 125 tests (lógica de detección, paper, regime, etc.) |
| `cargo test -p flowsurface-data --test mongo_smoke -- --ignored writer_inserts` | Conexión Mongo + insert/find/delete básicos |
| `cargo test -p flowsurface-data --test mongo_smoke -- --ignored loader_reads_seeded` | Loader lee `deployed_params` y aplica override |
| `cargo test -p flowsurface-data --test mongo_smoke -- --ignored end_to_end_signal_and_trade` | Pipeline completo: `write_signal` → `write_trade` → FK linkage |
| `cargo test -p flowsurface-data config_file` | TOML parsea y mapea bien, conversión TTL min→ms |

Los `--ignored` requieren `mongod` activo en localhost:27017.
