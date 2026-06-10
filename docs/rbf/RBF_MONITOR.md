# RBF Monitor — Dashboard Iced Standalone

**Crate:** `crates/rbf_monitor/`
**Binario:** `cargo run -p rbf_monitor`
**Última actualización:** 2026-06-10

---

## Qué es

App de escritorio independiente para monitorear el sistema RBF v2 en tiempo real. Construida en Rust/iced — cero latencia, sin proceso separado, sin Next.js.

Conecta directamente a Binance Futures WebSocket y REST. No requiere que el proceso `monitor` esté corriendo.

---

## Arquitectura

```
Binance fstream.binance.com
  └── WS kline stream  →  live ticks (actualiza vela en construcción)

Binance fapi.binance.com
  └── REST /klines      →  1500 barras históricas al arrancar / al cambiar temporalidad

Supabase (opcional)
  └── rbf_signals       →  historial de señales RBF (stats, tabla, vetos)
```

El `monitor` de Railway sigue siendo la fuente de señales — las escribe en Supabase. El `rbf_monitor` las lee desde ahí.

---

## Estructura de archivos

```
crates/rbf_monitor/
├── Cargo.toml
└── src/
    ├── main.rs     — estado de la app, update, layout de 4 paneles
    ├── data.rs     — tipos: Bar, RbfSignal, Timeframe, colores del tema
    ├── ws.rs       — subscription iced al kline stream de Binance (auto-reconecta)
    ├── api.rs      — fetch_bars() desde Binance REST, fetch_signals() desde Supabase
    └── chart.rs    — canvas OHLC con canvas::Cache para redraws en tiempo real
```

---

## Layout

```
┌─ Navbar ─────────────────────────────────────────────────────────────────┐
│  RBF Monitor — BTCUSDT.P  105,420.1  [1m][3m][5m][15m][30m][1h][4h][1d] ● live │
├─────────────────────────────────────┬────────────────────────────────────┤
│                                     │  Stats                             │
│                                     │  ──────────────────────────        │
│         Chart OHLC (75%)            │  Win rate     52.3%                │
│         canvas::Cache               │  Avg R        +0.18R               │
│         1500 velas                  │  Señales      47                   │
│         vela viva en tiempo real    │  Abiertas     2                    │
│                                     ├────────────────────────────────────┤
│                                     │  Señales (últimas 14)              │
│                                     │  L  105420  Asia  4/7  open        │
│                                     │  S  104890  EU   3/7   +1.82R      │
│                                     ├────────────────────────────────────┤
│                                     │  Vetos                             │
│                                     │  wall_target        12             │
│                                     │  hvn_target          8             │
└─────────────────────────────────────┴────────────────────────────────────┘
```

---

## Estilo de velas

Idéntico a `draw_candle_dp` en `src/chart/kline.rs`:

| Elemento | Valor |
|---|---|
| Bull color | `#26a69a` |
| Bear color | `#ef5350` |
| Mecha      | `fill_rectangle` de `candle_width / 4` de ancho |
| Cuerpo     | `fill_rectangle` de `candle_width` de ancho |
| Vela viva  | mismos colores, alpha 0.65 |

---

## Temporalidades

Selector en el navbar. Al cambiar:
1. Se limpian las barras actuales
2. Se cargan 1500 velas de Binance REST con el nuevo interval
3. La subscription WS cambia automáticamente al nuevo stream

| Botón | Interval Binance |
|---|---|
| 1m  | `btcusdt@kline_1m`  |
| 3m  | `btcusdt@kline_3m`  |
| 5m  | `btcusdt@kline_5m`  |
| 15m | `btcusdt@kline_15m` |
| 30m | `btcusdt@kline_30m` |
| 1h  | `btcusdt@kline_1h`  |
| 4h  | `btcusdt@kline_4h`  |
| 1d  | `btcusdt@kline_1d`  |

---

## Actualizaciones en tiempo real

El canvas usa `canvas::Cache`. En cada mensaje de Binance que modifica el chart:

```rust
self.chart_cache.clear();  // fuerza redraw en el próximo frame
```

Sin esto, iced cachea la geometría y no redibuja aunque cambien los datos.

---

## Variables de entorno

| Variable | Por defecto | Uso |
|---|---|---|
| `SYMBOL` | `BTCUSDT` | Par a monitorear |
| `SUPABASE_URL` | — | Si está, carga señales al arrancar |
| `SUPABASE_KEY` | — | Requerida si SUPABASE_URL está presente |

---

## Correr

```powershell
# Mínimo (solo chart, sin señales)
cargo run -p rbf_monitor

# Con historial de señales
$env:SUPABASE_URL = "https://xxxx.supabase.co"
$env:SUPABASE_KEY = "eyJ..."
cargo run -p rbf_monitor

# Con símbolo distinto
$env:SYMBOL = "ETHUSDT"
cargo run -p rbf_monitor
```

---

## Calibraciones en `crates/monitor/src/main.rs` (2026-06-10)

El `monitor` de Railway (`crates/monitor/src/main.rs`) incorpora lógica de calibración por símbolo:

### regime_hist_25

Cada `BarState` mantiene un `VecDeque<Regime>` de las últimas 25 barras M1:
```rust
regime_hist_25: VecDeque<data::strategy::types::Regime>
```
Se actualiza después de calcular `effective_regime` en cada cierre de barra.

### expansion_bars_recent

Antes de construir `RbfGateContext`, cuenta barras Expansion en la ventana de 25:
```rust
let expansion_bars_recent: u8 = if symbol == "SOLUSDT" || symbol == "XRPUSDT" {
    0  // bypass — correlación invertida / muestra insuficiente
} else {
    self.regime_hist_25.iter()
        .filter(|&&r| r == Regime::Expansion)
        .count().min(25) as u8
};
```

### Config por símbolo

Se clona el config antes de llamar al detector, modificando `expansion_max_bars` por símbolo:
```rust
let mut rbf_cfg = cfg.range_breakout.clone();
rbf_cfg.expansion_max_bars = match symbol {
    "SOLUSDT" | "XRPUSDT" => None,
    _                      => Some(3),
};
```

---

## Pendiente (segunda fase)

- Señales en tiempo real desde el monitor (actualmente solo historial de Supabase)
- Marcadores de señales sobre las velas del chart
- Panel de confluencia flags breakdown
- cum_delta threshold filter por símbolo (pendiente n≥25 Shorts por símbolo)
