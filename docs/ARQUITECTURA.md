# Arquitectura General de Flowsurface

## Modelo de la app (Elm / iced daemon)

Flowsurface usa el patrón Elm de iced: estado inmutable + función `update` que produce el siguiente estado. No hay mutación directa — todo pasa por mensajes.

```
User Input / WebSocket Event
    → Message
    → Flowsurface::update(state, message) → (new_state, Command)
    → Flowsurface::view(state) → Element (UI tree)
    → iced renderiza el diff
```

---

## Struct principal: `Flowsurface`

Definida en `src/main.rs`. Implementa `iced::daemon` (no `iced::application` — soporta múltiples ventanas).

Campos relevantes:

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `main_window` | `Window` | Ventana principal |
| `sidebar` | `Sidebar` | Lista de tickers, búsqueda |
| `handles` | `AdapterHandles` | Conexiones a 5 exchanges |
| `layout_manager` | `LayoutManager` | Múltiples layouts (Uuid → Dashboard) |
| `theme_editor` | `ThemeEditor` | Editor de tema visual |
| `network` | `NetworkManager` | Config de proxy |
| `audio_stream` | `AudioStream` | Alertas de audio |
| `volume_size_unit` | `SizeUnit` | Quote vs Base currency |
| `ui_scale_factor` | `ScaleFactor` | Escala de UI (0.8–1.5×) |
| `timezone` | `UserTimezone` | UTC o local |
| `theme` | `Theme` | Tema activo |
| `notifications` | `Notifications` | Toast stack global |

---

## Jerarquía de componentes

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
│                   ├── Kline    → KlineChart
│                   ├── Heatmap  → HeatmapChart
│                   ├── ShaderHeatmap
│                   ├── TimeAndSales
│                   ├── Ladder
│                   └── Comparison → ComparisonChart
├── AdapterHandles                — WebSocket + HTTP por exchange
│   ├── BinanceHandle
│   ├── BybitHandle
│   ├── HyperliquidHandle
│   ├── MexcHandle
│   └── OkExHandle
└── Modal stack (layout_manager, theme_editor, network_manager)
```

---

## Message enum principal

```rust
Message {
    Sidebar(sidebar::Message),
    MarketWsEvent(exchange::Event),     // DepthReceived | TradesReceived | KlineReceived
    Dashboard { layout_id: Option<Uuid>, event: dashboard::Message },
    Tick(Instant),                       // Frame render loop
    WindowEvent { id: WindowId, event },
    // + handlers de proxy, theme, layout, audio, timezone
}
```

El routing de mensajes va de lo general a lo específico:
- `MarketWsEvent` → `Flowsurface::update` → identifica el layout/pane destinatario → `dashboard.update()`
- `Tick(now)` → cada pane actualiza su crosshair y solicita redraws necesarios

---

## Inicialización (`Flowsurface::new`)

1. `layout::load_saved_state()` — lee `{APPDATA}/flowsurface/saved-state.json`
2. `AdapterHandles::spawn_all()` — inicia conexiones WebSocket para 5 exchanges
3. `Sidebar::new()` — carga metadatos de tickers (fetch HTTP en background)
4. `load_layout()` — restaura panes y ventanas popup guardadas
5. Task batch: abre ventana principal + restaura layout + sidebar

---

## Pane lifecycle

```
1. Usuario selecciona ContentKind desde Starter
2. set_content_and_streams()
   ├── crea Content (ej. KlineChart)
   └── genera Vec<StreamKind> necesarios
3. Streams enviados a AdapterHandles → suscripción WebSocket
4. ResolvedStream::Waiting (hasta que TickerInfo se resuelva)
5. Al recibir MarketWsEvent:
   dashboard.ingest_depth/trades/klines()
   → distribuye datos al pane correcto por StreamKind
   → chart.insert_* → actualiza datos internos → cache.clear()
6. Próximo frame: draw() llama a los renderers
```

---

## Sistema de streams (deduplicación)

`UniqueStreams` asegura que no se abran suscripciones duplicadas:

```rust
UniqueStreams {
    streams: EnumMap<Exchange, FxHashMap<TickerInfo, FxHashSet<StreamKind>>>,
}
```

Límites por exchange:
- `MAX_KLINE_STREAMS_PER_STREAM = 100`
- `MAX_TRADE_TICKERS_PER_STREAM = 100`

Si dos panes piden el mismo stream (mismo exchange + ticker + timeframe), solo se abre una suscripción WebSocket y ambos panes reciben los datos.

---

## Persistencia

**Archivo:** `{APPDATA}\Roaming\flowsurface\saved-state.json`

Se guarda al cerrar la app (o en eventos de ventana). Contiene:

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

Cada pane serializa su `Content` con todos los streams, indicadores activos y settings visuales. Al restaurar, los streams van a `ResolvedStream::Waiting` hasta que TickerInfo llega del exchange.

---

## Múltiples ventanas (popout)

Iced daemon soporta múltiples ventanas nativas. Cada popout tiene su propio `pane_grid::State` independiente del main. El estado se guarda en `popout: HashMap<window::Id, (pane_grid::State, WindowSpec)>` dentro del Dashboard.

Los popouts se serializan y restauran junto con el layout.

---

## Exchanges soportados

| Exchange | Spot | Perps Lineares | Perps Inversos |
|----------|------|----------------|----------------|
| Binance | ✓ | ✓ | ✓ |
| Bybit | ✓ | ✓ | ✓ |
| Hyperliquid | ✓ | ✓ | ✓ |
| MEXC | ✓ | ✓ | — |
| OkEx | ✓ | ✓ | ✓ |
