# Arquitectura General de Flowsurface

**Última actualización:** 2026-05-29

---

## Visión de capas

El proyecto se divide en **5 capas** bien separadas que se comunican de forma unidireccional:

```
┌─────────────────────────────────────────────────────────┐
│  5. Infraestructura                                      │
│     Railway (Docker) · Supabase · MongoDB local          │
└─────────────────────────────────────────────────────────┘
         ↑ escribe            ↑ escribe
┌─────────────────┐  ┌────────────────────────────────────┐
│  3. Monitor     │  │  4. UI (iced app)                  │
│  crates/monitor │  │  src/ + flowsurface binary         │
└────────┬────────┘  └──────────────┬─────────────────────┘
         │ usa                       │ usa
         └──────────┬───────────────┘
                    ↓
┌─────────────────────────────────────────────────────────┐
│  2. Dominio / Data                                       │
│     data/ crate — indicadores + estrategia + persistencia│
└─────────────────────────────────────────────────────────┘
                    ↑ produce eventos
┌─────────────────────────────────────────────────────────┐
│  1. Transporte / Exchanges                               │
│     exchange/ crate — WebSocket + HTTP por exchange      │
└─────────────────────────────────────────────────────────┘
```

---

## Capa 1 — Transporte / exchange crate

**Path:** `exchange/`

Gestiona conexiones a 5 exchanges via WebSocket y REST:

| Exchange      | Spot | Perps Lineares | Perps Inversos |
|---------------|------|----------------|----------------|
| Binance       | ✓    | ✓              | ✓              |
| Bybit         | ✓    | ✓              | ✓              |
| Hyperliquid   | ✓    | ✓              | ✓              |
| MEXC          | ✓    | ✓              | —              |
| OkEx          | ✓    | ✓              | ✓              |

Produce tres tipos de eventos de mercado:
- `DepthReceived` — orderbook snapshot + deltas
- `TradesReceived` — trades individuales (taker side)
- `KlineReceived` — barras OHLCV cerradas

**Deduplicación de streams** (`UniqueStreams`):
```rust
UniqueStreams {
    streams: EnumMap<Exchange, FxHashMap<TickerInfo, FxHashSet<StreamKind>>>,
}
```
Dos panes que pidan el mismo stream comparten la misma suscripción WebSocket.
- `MAX_KLINE_STREAMS_PER_STREAM = 100`
- `MAX_TRADE_TICKERS_PER_STREAM = 100`

Esta capa no sabe nada de estrategia ni indicadores — solo transporta datos crudos.

---

## Capa 2 — Dominio / data crate

**Path:** `data/`  
**Crate:** `flowsurface-data`

El núcleo compartido. Tanto el monitor como la UI lo importan. Se divide en tres grandes módulos:

### 2a. Indicadores de mercado

| Módulo          | Descripción                                                |
|-----------------|------------------------------------------------------------|
| `vwap`          | VWAP de sesión (reset UTC diario), ±1σ/±2σ bands, AVWAP-BOS |
| `volume_profile`| Histograma 150 bins, POC/VAH/VAL/HVN/LVN, ventana 300 velas |
| `cvd`           | Cumulative Volume Delta acumulado desde open de sesión     |
| `atr`           | ATR 14 periodos para normalización de slopes y stops       |
| `oi`            | OI delta por barra (solo perps), z-score pendiente         |
| `footprint`     | Niveles de delta por precio intrabar                       |

### 2b. Institutional data

| Tracker                   | Fuente Binance FAPI          | Descripción                              |
|---------------------------|------------------------------|------------------------------------------|
| `FundingTracker`          | `premiumIndex`               | Tasa actual + percentil 21 muestras (7d) |
| `OiTracker`               | `openInterest`               | Tendencia OI + cambio 30m                |
| `LsRatioTracker`          | `globalLongShortAccountRatio`| Top traders vs retail L/S divergencia    |
| `LiquidationTracker`      | forceOrder WS                | USD liquidado por side, cascade detection|
| `LiqMapTracker`           | (calculado)                  | Mapa de liquidaciones estimado           |

### 2c. Strategy module

**Path:** `data/src/strategy/`

```
strategy/
├── types.rs              — StrategyConfig, StrategySignal, Regime, Side, enums
├── config_file.rs        — StrategyConfig::load() desde config/strategy.toml
├── adapter.rs            — build_context(), derive_regime(), derive_regime_with_hysteresis()
├── router.rs             — dispatch a detectores + toxic_flow_gate
├── scoring.rs            — score 0.0–1.0 sobre señal cruda
├── paper.rs              — motor paper trading completo
├── tracker.rs            — OutcomeTracker MFE/MAE
├── logger.rs             — escribe señales a JSONL
├── mongo_writer.rs       — escritura async a MongoDB (solo si MONGODB_URI presente)
├── mongo_config_loader.rs— config dinámica por régimen desde MongoDB
└── detectors/
    ├── vwap_value_pullback_continuation.rs  ← detector principal
    ├── value_area_failed_auction.rs
    ├── lvn_liquidity_vacuum_breakout.rs
    ├── dom_imbalance_breakout.rs
    ├── session_open_breakout.rs
    ├── order_block_retest.rs
    ├── footprint_absorption_reversal.rs
    ├── liquidation_hunt.rs          — requiere ctx.institutional
    ├── funding_exhaustion_reversal.rs — requiere ctx.institutional
    ├── smart_money_divergence.rs    — requiere ctx.institutional
    └── toxic_flow_gate.rs           — gate pre-detección
```

#### Regime con hysteresis

El régimen es la variable central que describe el estado del mercado:

```
Regimes: TrendUp | TrendDown | Chop | Expansion | Compression | Stress | Aftermath | Unknown
```

Se calcula con OLS sobre dos ventanas de los últimos cierres (REGIME_WINDOW=14 barras):
- **Slow layer** (14 barras, threshold ±0.10): tendencias establecidas
- **Fast layer** (últimas 5 barras, threshold ±0.25): impulsos recientes

Hysteresis previene flip-flop: salida de TrendUp requiere slow < 0.05 AND fast < 0.15.

**Corrección por divergencia de flujo:** si `regime=TrendUp` pero `price < VWAP` y `cvd_slope < 0`, el detector VWAP acepta shorts — "fake recovery" donde el precio hace mínimos ligeramente más altos en 14 barras pero el flujo acumulado es bajista.

#### Router

```rust
route_strategy(ctx, cfg):
  1. if !cfg.enabled → Wait
  2. if atr < $1.0   → Wait (ATR_NOT_READY)
  3. toxic_flow_gate → si falla → Blocked
  4. session filter  → descarta detectores fuera de horario válido
  5. run detectors:  todos los detectores pasan por subdimi_detector_allowed()
                     → solo DeltaRangeReversal puede emitir señal (DRR-only mode)
                     → VAFA, LVN, DIB, SOB, OBR, FAR, VWAP, LIQ, FER, SMD → SUBDIMI_ONLY_DISABLED
  6. score candidatos → best = max by score
  7. if best.score ≥ min_score → ShadowSignal
     else → Wait con LOW_SCORE
```

**Modo producción actual (2026-05-29):** `subdimi_detector_allowed()` solo permite `DeltaRangeReversal`. Los demás detectores están en el codebase pero no compiten. Subdimi Parallel corre los 6 detectores anteriores en paralelo sin winner-takes-all y escribe a `lab_signals`.

#### Paper Trader

```
capital: $300 · leverage: 10× · risk: 1%/trade · max_positions: 1
slippage: 1bp · taker_fee: 0.04% · funding: 0.01%/8h
```

Estado persistido en `logs/paper_account_state.json` (write atómico).

#### Persistencia dual (mongo_writer / supabase)

`MongoWriter` y `MongoConfigLoader` son **opcionales**:
- Si `MONGODB_URI` está en el entorno → conecta a MongoDB local
- Si no está (Railway) → noop silencioso, log: `writer disabled (Supabase only)`

Supabase siempre activo cuando `SUPABASE_URL` + `SUPABASE_KEY` están presentes.

---

## Capa 3 — Monitor

**Path:** `crates/monitor/`  
**Binario:** `monitor` — corre en Railway

### Flujo por bar-close (M5)

```
WebSocket kline event
  → on_bar_close()
      ├─ [1] Recupera UUID del write_signal anterior (oneshot channel)
      ├─ [2] Calcula delivery_lag_ms = now_ms - bar_close_ms
      ├─ [3] Actualiza VWAP, CVD, Volume Profile, OI
      ├─ [4] Calcula ATR (14 periodos)
      ├─ [5] derive_regime_with_hysteresis() + corrección de flujo
      ├─ [6] Construye StrategyMarketContext
      ├─ [7] route_strategy() → StrategySignal
      ├─ [8] paper_account.on_bar_close() (funding → cierres → apertura)
      ├─ [9] Dispara write_signal en tokio::spawn (off hot path)
      └─ [10] Log: bar close, signal, paper state, metrics
```

### Intrabar detection (modo ShadowEvent)

Además del bar-close, hay evaluaciones **intra-barra** cada vez que el precio se mueve ≥ 0.25×ATR o cada 60s (time_fallback). Hasta 6 evaluaciones por barra (eval=1/6 .. 6/6).

```
[intrabar] ts=... px=... regime=... fast_slope=... trigger=price_move eval=3/6
           action=ShadowSignal score=1.000 id=Some(FootprintAbsorptionReversal)
```

Configuración:
```
mode=ShadowEvent  allow_execution=false  write_events=true
price_move_atr_k=0.25  time_fallback_ms=60000  vwap_near_bps=8  min_rr=1.2
```

### Warm-up histórico

Al arrancar fetchea 50 klines históricas (`fapi.binance.com/fapi/v1/klines`) para primar VWAP, ATR, regime y CVD antes del primer bar live.

### Métricas de latencia (producción)

```
delivery_avg = 400–800ms   ← Binance WebSocket (inherente al protocolo)
proc_avg     < 7ms         ← Rust compute
proc_max     < 15ms        ← spike por volume profile recalc
```

### Optimizaciones aplicadas

| Commit    | Optimización                                              | Impacto                               |
|-----------|-----------------------------------------------------------|---------------------------------------|
| `09e0d7e` | `write_signal` en tokio::spawn con oneshot channel        | Elimina await Supabase del hot path   |
| `be6a0bf` | Config reload en mpsc channel                             | Elimina fetch HTTP del hot path       |
| `8a4f30d` | AVWAP-BOS: O(n²) → O(n) con prefix max/min arrays         | Elimina loop nested por bar-close     |

---

## Capa 4 — UI (iced app)

**Path:** `src/`  
**Binario:** `flowsurface`

### Modelo Elm (iced daemon)

```
User Input / WebSocket Event
    → Message
    → Flowsurface::update(state, message) → (new_state, Command)
    → Flowsurface::view(state) → Element (UI tree)
    → iced renderiza el diff
```

Usa `iced::daemon` (no `iced::application`) para soportar múltiples ventanas nativas.

### Struct principal `Flowsurface`

| Campo            | Tipo              | Descripción                              |
|------------------|-------------------|------------------------------------------|
| `main_window`    | `Window`          | Ventana principal                        |
| `sidebar`        | `Sidebar`         | Lista de tickers, búsqueda               |
| `handles`        | `AdapterHandles`  | Conexiones a 5 exchanges                 |
| `layout_manager` | `LayoutManager`   | Múltiples layouts (Uuid → Dashboard)     |
| `theme_editor`   | `ThemeEditor`     | Editor de tema visual                    |
| `network`        | `NetworkManager`  | Config de proxy                          |
| `audio_stream`   | `AudioStream`     | Alertas de audio                         |

### Jerarquía de componentes

```
Flowsurface (main.rs)
├── Sidebar                       — lista de tickers, búsqueda, metadatos
│   └── TickersTable
├── LayoutManager                 — Uuid → Dashboard (múltiples layouts)
│   └── Dashboard (dashboard.rs)
│       ├── pane_grid::State<pane::State>    — grid principal
│       └── popout: HashMap<window::Id, pane_grid::State<pane::State>>
│           └── pane::State (pane.rs)
│               └── Content (7 variantes)
│                   ├── Starter
│                   ├── Kline    → KlineChart + overlay indicators
│                   ├── Heatmap  → HeatmapChart
│                   ├── ShaderHeatmap
│                   ├── TimeAndSales
│                   ├── Ladder
│                   └── Comparison → ComparisonChart
├── AdapterHandles                — WebSocket + HTTP por exchange
└── Modal stack
```

### Overlay indicators (KlineChart)

Renderizados en canvas sobre el chart principal:

| Indicador       | Descripción                                          |
|-----------------|------------------------------------------------------|
| VWAP            | Sesión diaria + ±1σ / ±2σ bands                      |
| Volume Profile  | 150-bin histogram, POC/VAH/VAL/HVN/LVN               |
| OI Delta        | Barras verde/rojo, delta = OI[n]−OI[n−1] (solo perps)|
| Key Levels      | PDH/PDL/DO/WO con tooltip hover                      |
| Session Lines   | Asia/London/NY como rectángulos pasteles             |

### Message enum principal

```rust
Message {
    Sidebar(sidebar::Message),
    MarketWsEvent(exchange::Event),     // DepthReceived | TradesReceived | KlineReceived
    Dashboard { layout_id: Option<Uuid>, event: dashboard::Message },
    Tick(Instant),                       // Frame render loop
    WindowEvent { id: WindowId, event },
}
```

### Persistencia

**Archivo:** `{APPDATA}\Roaming\flowsurface\saved-state.json`

```
State {
  layout_manager: Layouts { layouts: Vec<Layout>, active_layout }
  selected_theme, custom_theme,
  main_window: WindowSpec { width, height, pos_x, pos_y },
  timezone, sidebar, scale_factor,
  audio_cfg, trade_fetch_enabled,
  size_in_quote_ccy, proxy_cfg
}
```

Cada pane serializa `Content` con streams, indicadores activos y settings visuales. Al restaurar, los streams van a `ResolvedStream::Waiting` hasta recibir TickerInfo del exchange.

### Pane lifecycle

```
1. Usuario selecciona ContentKind desde Starter
2. set_content_and_streams()
   ├── crea Content (ej. KlineChart)
   └── genera Vec<StreamKind> necesarios
3. Streams → AdapterHandles → suscripción WebSocket
4. ResolvedStream::Waiting (hasta TickerInfo)
5. Al recibir MarketWsEvent:
   dashboard.ingest_depth/trades/klines()
   → distribuye al pane por StreamKind
   → chart.insert_* → cache.clear()
6. Próximo frame: draw() llama renderers
```

---

## Capa 5 — Infraestructura

### Railway (producción)

```dockerfile
FROM rust:1.95-slim AS builder   # Debian trixie = GLIBC 2.38
RUN cargo build --release -p monitor
FROM debian:trixie-slim          # mismo GLIBC — match obligatorio
CMD ["monitor"]
```

**Variables de entorno requeridas en Railway:**

| Variable        | Descripción                            |
|-----------------|----------------------------------------|
| `SYMBOL`        | Ticker (ej. `BTCUSDT`)                 |
| `TIMEFRAME_MIN` | Timeframe en minutos (ej. `5`)         |
| `SUPABASE_URL`  | URL del proyecto Supabase              |
| `SUPABASE_KEY`  | Service role key de Supabase           |

`MONGODB_URI` no se setea en Railway → MongoDB queda deshabilitado automáticamente.

### Supabase (cloud storage)

Tablas activas — ver `ESTADO_CHECKLIST.md` para el schema completo:

| Tabla                    | Descripción                                                    |
|--------------------------|----------------------------------------------------------------|
| `shadow_signals`         | Señales DRR con 30 campos de contexto/calibración             |
| `signal_outcomes`        | Trades cerrados con R multiple, MFE, MAE                      |
| `lab_signals`            | Señales Subdimi Parallel (observación, sin paper trading)      |
| `micro_windows`          | Micro-dinámica por vela M5 — 288 rows/día                      |
| `regime_history`         | Cambios de régimen (timestamp, valor, ATR)                     |
| `institutional_snapshots`| Snapshots institucionales                                      |
| `deployed_params`        | Config activa por régimen                                      |
| `v_signals_with_outcomes`| Vista principal de análisis (shadow_signals + signal_outcomes) |
| `v_micro_with_outcomes`  | Vista micro_windows + shadow_signals + signal_outcomes         |

### Local (desarrollo)

```
Toolchain: stable-x86_64-pc-windows-gnu
Linker:    MinGW-w64 (winget: BrechtSanders.WinLibs.POSIX.UCRT)
Run:       ./run.bat  (incluye PATH MinGW + RUST_BACKTRACE=1)
MongoDB:   localhost:27018 (instancia dedicada, start-mongo.bat)
           Datapath: %LOCALAPPDATA%\flowsurface\mongo-data
```

Para activar MongoDB local se setea `MONGODB_URI=mongodb://localhost:27018` en el entorno.

---

## Separación Railway vs Local

| Aspecto              | Railway                          | Local                              |
|----------------------|----------------------------------|------------------------------------|
| Persistence          | Solo Supabase                    | MongoDB + Supabase (dual-write)    |
| Config loader        | Base config (strategy.toml)      | MongoDB deployed_params por régimen|
| `MONGODB_URI`        | No seteada → noop silencioso     | Seteada → conecta a :27018         |
| Binario              | `monitor` (headless)             | `flowsurface` (UI) o `monitor`     |
| Toolchain            | Docker rust:1.95-slim            | MinGW-w64 GNU Windows              |

---

## Workspace Cargo

```toml
[workspace]
members = ["data", "exchange", "crates/monitor"]

# flowsurface binary (UI) está en la raíz del workspace
```

- `flowsurface-exchange` — capa de transporte
- `flowsurface-data` — dominio compartido
- `monitor` — binario Railway (headless)
- `flowsurface` — binario UI (raíz)
