# Strategy Module — Referencia Completa

## Estado: todas las fases completadas ✅

| Fase | Descripción | Commit |
|------|-------------|--------|
| 1 | Strategy module + 3 detectores | c15bf0b |
| 2 | Overlay rendering en kline chart | 1bca2ad |
| 3 | Wired a depth updates | 0f41046 |
| 4 | Valores reales de indicadores al contexto | afbe52d |
| 5 | CVD slope, HVN/LVN nearby, AVWAP BOS | b3ff7b5 |
| 6 | Regime, failed acceptance, footprint absorption | 6fadc8c |
| 7 | UI toggle (botón ⭐ en toolbar) | 2714b96 |
| 8 | Outcome tracker MFE/MAE + JSONL | d0cfc12 |

---

## Arquitectura del módulo

```
src/strategy/
├── mod.rs                           — exports públicos
├── types.rs                         — todos los structs y enums de dominio
├── adapter.rs                       — funciones build_* y derive_*
├── router.rs                        — dispatcher de detectores
├── scoring.rs                       — scoring 0.0–1.0
├── tracker.rs                       — OutcomeTracker (MFE/MAE)
├── logger.rs                        — log de señales a JSONL
├── context.rs                       — helpers adicionales
└── detectors/
    ├── mod.rs
    ├── toxic_flow_gate.rs           — pre-filtro de calidad de datos
    ├── value_area_failed_auction.rs
    ├── vwap_value_pullback_continuation.rs
    └── lvn_liquidity_vacuum_breakout.rs
```

---

## Pipeline de detección

```
depth update
    → update_depth(&Depth)
    → run_strategy_detection()
        ├── extraer price, recent_candles de data_source
        ├── build_orderbook_context(depth)
        ├── build_vwap_context(price, vwap_session, avwap_bos)
        ├── build_volume_profile_context(price, poc, vah, val, hvn, lvn)
        ├── build_flow_context(cvd, cvd_slope, delta, buy_vol, sell_vol, failed_acceptance, absorption)
        ├── derive_regime(recent_closes, atr)
        ├── derive_failed_acceptance_and_absorption(highs, lows, closes, vah, val, delta, cvd_slope)
        ├── StrategyMarketContext { ... }
        ├── router::route_strategy(ctx, cfg)
        │       ├── toxic_flow_gate(ctx, cfg)
        │       ├── value_area_failed_auction::detect(ctx, cfg)
        │       ├── lvn_liquidity_vacuum_breakout::detect(ctx, cfg)
        │       ├── vwap_value_pullback_continuation::detect(ctx, cfg)
        │       └── score_signal + pick best
        ├── logger::log_signal(ctx, signal)
        ├── outcome_tracker.push_signal(signal, price)   [si ShadowSignal]
        └── outcome_tracker.update(price, now_ms)
```

---

## StrategyMarketContext

```rust
pub struct StrategyMarketContext {
    pub symbol: String,
    pub timestamp_ms: i64,
    pub price: f64,
    pub regime: Regime,
    pub atr: Option<f64>,
    pub volume_profile: VolumeProfileContext,
    pub vwap: VwapContext,
    pub flow: OrderFlowContext,
    pub orderbook: OrderBookContext,
}
```

### VolumeProfileContext

```rust
pub struct VolumeProfileContext {
    pub poc: Option<f64>,
    pub vah: Option<f64>,
    pub val: Option<f64>,
    pub hvn_nearby: Vec<f64>,    // HVNs dentro de ±3×ATR del precio actual
    pub lvn_nearby: Vec<f64>,    // LVNs dentro de ±3×ATR del precio actual
    pub value_location: ValueLocation,  // AboveVah | BelowVal | InValue | Unknown
    pub quality: DataQuality,
}
```

### VwapContext

```rust
pub struct VwapContext {
    pub vwap_session: Option<f64>,    // VWAP de sesión (reset UTC diario)
    pub avwap_bos: Option<f64>,       // AVWAP anclado al último swing-low (50 velas)
    pub avwap_event: Option<f64>,     // AVWAP a evento específico (no implementado)
    pub price_vs_vwap: PriceRelation,
    pub price_vs_avwap_bos: PriceRelation,
    pub price_vs_avwap_event: PriceRelation,
    pub quality: DataQuality,
}
```

### OrderFlowContext

```rust
pub struct OrderFlowContext {
    pub cvd: Option<f64>,
    pub cvd_slope: Option<f64>,        // OLS slope últimas 20 velas, positivo = CVD subiendo
    pub delta: Option<f64>,
    pub taker_imbalance: Option<f64>,  // (buy − sell) / (buy + sell), rango [−1, 1]
    pub buy_volume: Option<f64>,
    pub sell_volume: Option<f64>,
    pub vpin: Option<f64>,             // mean(|delta|/vol) sobre últimas 50 velas
    pub cvd_divergence: Option<CvdDivergence>, // divergencia precio/CVD detectada
    pub footprint_absorption: AbsorptionSide,  // Ask | Bid | None | Unknown
    pub stacked_imbalance: ImbalanceSide,       // siempre Unknown
    pub failed_acceptance: bool,
    pub sweep_confirmed: bool,
    pub mss_active: bool,
    pub quality: DataQuality,
}
```

### OrderBookContext

```rust
pub struct OrderBookContext {
    pub obi_l5: Option<f64>,       // Order Book Imbalance top 5 niveles
    pub obi_l10: Option<f64>,
    pub obi_l20: Option<f64>,
    pub microprice: Option<f64>,   // precio ponderado por tamaño bid/ask
    pub spread_bps: Option<f64>,   // spread en basis points
    pub walls_above: Vec<f64>,     // órdenes >5x la media en primeros 20 asks
    pub walls_below: Vec<f64>,
    pub thin_zone_above: bool,     // ≥3 de los primeros 10 asks con <30% del avg
    pub thin_zone_below: bool,
    pub quality: DataQuality,
}
```

---

## Enums de dominio

```rust
pub enum Regime {
    TrendUp,      // slope OLS > 0.15 ATR/vela
    TrendDown,    // slope OLS < -0.15 ATR/vela
    Chop,         // slope bajo, rango normal
    Compression,  // rango < 0.8 ATR en 20 velas
    Expansion,    // rango > 4 ATR y slope fuerte
    Stress,       // no usado actualmente
    Aftermath,    // no usado actualmente
    Unknown,      // insuficientes datos
}

pub enum DataQuality { Live, Fallback, Degraded, Stale, Missing }
pub enum ValueLocation { AboveVah, BelowVal, InValue, Unknown }
pub enum PriceRelation { Above, Below, At, Unknown }
pub enum AbsorptionSide { Bid, Ask, None, Unknown }
pub enum ImbalanceSide { Bullish, Bearish, None, Unknown }

pub enum CvdDivergence {
    BearishAbsorption,  // precio HH pero CVD slope bajando → absorción en máximos
    BullishAbsorption,  // precio LL pero CVD slope subiendo → absorción en mínimos
}
```

---

## Funciones de adapter.rs

### build_orderbook_context(depth: &Depth) -> OrderBookContext

Calcula OBI a 5/10/20 niveles, microprecio, spread en bps, walls (umbral 5× la media), zonas delgadas (≥3 de 10 niveles con <30% del promedio).

### build_vwap_context(price, vwap_session, avwap_bos) -> VwapContext

Calcula `price_vs_vwap` y `price_vs_avwap_bos` usando `price_relation()`:
- `|price − level| / level < 0.0002` → `At`
- `price > level` → `Above`
- `price < level` → `Below`

### build_volume_profile_context(price, poc, vah, val, hvn_nearby, lvn_nearby) -> VolumeProfileContext

Calcula `value_location` y asigna `quality = Live` cuando poc/vah/val están disponibles.

### build_flow_context(cvd, cvd_slope, delta, buy_vol, sell_vol, vpin, failed_acceptance, footprint_absorption, cvd_divergence) -> OrderFlowContext

Calcula `taker_imbalance = (buy − sell) / (buy + sell)`.
`quality = Live` si cvd o delta disponibles.

### derive_cvd_divergence(recent_highs, recent_lows, cvd_slope) -> Option\<CvdDivergence\>

Compara la primera mitad vs la segunda mitad de las últimas 10 velas:
- Precio HH (`last_max > first_max × 1.0001`) + `slope < −0.5` → `BearishAbsorption`
- Precio LL (`last_min < first_min × 0.9999`) + `slope > 0.5` → `BullishAbsorption`
- Requiere al menos 10 highs/lows y CVD slope disponible

### derive_regime(recent_closes: &[f64], atr: f64) -> Regime

OLS sobre los últimos 20 cierres. Slope normalizado por ATR:
- `slope_per_atr > 0.15` → TrendUp
- `slope_per_atr < -0.15` → TrendDown
- `range_atr < 0.8` → Compression
- `range_atr > 4.0 && |slope| > 0.15` → Expansion
- resto → Chop

### derive_failed_acceptance_and_absorption(highs, lows, closes, vah, val, delta, cvd_slope) -> (bool, AbsorptionSide)

Ventana de las últimas 5 velas:
- **Failed above VAH**: alguna vela rompió VAH pero el último cierre quedó por debajo
  - Ask absorption si `delta > 0 && cvd_slope ≤ 0` (compradores activos pero rechazados)
- **Failed below VAL**: alguna vela perforó VAL pero el último cierre quedó por encima
  - Bid absorption si `delta < 0 && cvd_slope ≥ 0` (vendedores activos pero absorbidos)

---

## Toxic Flow Gate

**Archivo:** `src/strategy/detectors/toxic_flow_gate.rs`

Pre-filtro que bloquea la detección cuando las condiciones no son válidas:

```
flow.quality != Live       → "FLOW_DATA_MISSING"
vp.quality != Live         → "VOLUME_PROFILE_MISSING"
spread_bps > max_spread    → "SPREAD_TOO_WIDE"
vpin > max_vpin            → "VPIN_TOXIC"  (si disponible)
```

Configuración por defecto (`StrategyConfig::default()`):
- `max_spread_bps: 2.0`
- `max_vpin: 0.75`
- `min_score: 0.70`
- `default_ttl_ms: 300_000` (5 minutos)

---

## Detectores

### VALUE_AREA_FAILED_AUCTION

**Cuándo dispara**: precio intentó romper fuera del value area pero regresó.

**Short** (rechazo sobre VAH):
- `failed_acceptance == true`
- `delta > 0` (compradores activos)
- `footprint_absorption == Ask` (vendedores absorbieron)
- `cvd_slope ≤ 0` (CVD no confirma el breakout)
- `taker_imbalance < 0.25`
- No hay thin zone arriba (no es un breakout real)
- Entry: precio actual | Stop: VAH + 0.25×ATR | Target: POC

**Long** (rechazo bajo VAL):
- Condiciones inversas
- Entry: precio actual | Stop: VAL − 0.25×ATR | Target: POC

Requiere: VWAP + VolumeProfile activos, con datos de CVD para `failed_acceptance`.

### LVN_LIQUIDITY_VACUUM_BREAKOUT

**Cuándo dispara**: zona delgada de orderbook con flujo confirmado.

**Long**:
- `ob.thin_zone_above == true`
- `price_vs_vwap == Above | At`
- `value_location == InValue | AboveVah`
- `delta > 0 && cvd_slope > 0`
- `taker_imbalance < 0.90`
- `microprice ≥ price`
- Target: HVN más cercano por encima (de `hvn_nearby`) o VAH

**Short**: condiciones inversas. Target: HVN más cercano por debajo o VAL.

**Este detector es el más fácil de disparar** — no requiere `regime`.
Requiere: VWAP + VolumeProfile + CVD + CVD slope activos.

### VWAP_VALUE_PULLBACK_CONTINUATION

**Cuándo dispara**: retroceso al value area durante una tendencia definida.

**Long**:
- `regime == TrendUp | Expansion`
- `price_vs_avwap_bos == Above | At`
- `value_location == InValue | BelowVal`
- `cvd_slope ≥ 0 && delta > 0`
- `failed_acceptance == false`
- Entry: precio | Stop: min(VAL, entry − 0.75×ATR) | Target: VAH

**Short**: `regime == TrendDown`, condiciones inversas.

Requiere: VWAP + VolumeProfile + CVD + regime no Unknown.

---

## Scoring (0.0–1.0)

**Archivo:** `src/strategy/scoring.rs`

Basado en evidencia del signal:

| Evidencia | Puntos |
|-----------|--------|
| target_POC | +0.20 |
| target_next_HVN_or_VAH/VAL | +0.20 |
| Menciona vwap o avwap | +0.15 |
| Menciona cvd | +0.20 |
| ask_absorption / bid_absorption | +0.20 |
| positive/negative_delta | +0.15 |
| thin_zone_above/below | +0.15 |
| spread_bps > 1.5 | −0.15 |
| vpin > 0.65 | −0.20 |

Score mínimo para ejecutar (modo shadow): `0.70`.

---

## Router

**Archivo:** `src/strategy/router.rs`

1. Si `cfg.enabled == false` → retorna `Wait`
2. Corre `toxic_flow_gate` → si falla → retorna `Blocked`
3. Corre los 3 detectores, recolecta candidatos con score
4. Selecciona el de mayor score
5. Si score < `min_score` → marca como `Wait` + añade "LOW_SCORE" a missing
6. Si no hay candidatos → retorna `Wait` + "NO_VALID_SETUP"

---

## Outcome Tracker

**Archivo:** `src/strategy/tracker.rs`

### OutcomeTracker

```rust
pub struct OutcomeTracker {
    active: Vec<TrackedSignal>,
}
```

**Métodos:**

```rust
pub fn push_signal(&mut self, signal: &StrategySignal, current_price: f64)
pub fn update(&mut self, price: f64, now_ms: i64)
```

**Deduplicación**: solo un signal activo por `(strategy_id, side)`. Si llega otro del mismo tipo mientras hay uno abierto, se ignora.

### Cálculo de MFE/MAE

```
MFE (Long)  = max(highest − entry, 0)
MAE (Long)  = max(entry − lowest, 0)
MFE (Short) = max(entry − lowest, 0)
MAE (Short) = max(highest − entry, 0)

MFE_R = MFE / |entry − stop|   (en R-multiples)
MAE_R = MAE / |entry − stop|
```

### Razones de cierre

| Outcome | Condición |
|---------|-----------|
| `TARGET_HIT` | Long: highest ≥ target_price; Short: lowest ≤ target_price |
| `STOP_HIT` | Long: lowest ≤ stop_price; Short: highest ≥ stop_price |
| `TTL_EXPIRED` | `now_ms ≥ created_at_ms + ttl_ms` |

### Formato de salida (strategy_outcomes.jsonl)

```json
{
  "symbol": "LvnLiquidityVacuumBreakout",
  "created_at_ms": 1715000000000,
  "closed_at_ms":  1715000300000,
  "strategy": "LvnLiquidityVacuumBreakout",
  "side": "Long",
  "entry_price": 100000.0,
  "stop_price": 99500.0,
  "target_price": 100500.0,
  "score": 0.85,
  "mfe": 320.0,
  "mae": 45.0,
  "mfe_r": 0.64,
  "mae_r": 0.09,
  "outcome": "TTL_EXPIRED"
}
```

---

## Logger de señales

**Archivo:** `src/strategy/logger.rs`

Guarda en `strategy_signals.jsonl` cada vez que una señal no es `Wait`. Incluye contexto completo de mercado en el momento de la señal.

```json
{
  "symbol": "BTCUSDT",
  "timestamp_ms": 1715000000000,
  "strategy": "LvnLiquidityVacuumBreakout",
  "side": "Long",
  "action": "ShadowSignal",
  "entry_price": 100000.0,
  "stop_price": 99500.0,
  "target_price": 100500.0,
  "score": 0.85,
  "ttl_ms": 300000,
  "evidence": ["thin_zone_above", "vwap_reclaim_or_above", "positive_delta", "cvd_positive", "target_next_HVN_or_VAH"],
  "missing": [],
  "invalidation": ["price_loses_VWAP", "cvd_turns_negative", "spread_expands"],
  "context": {
    "price": 100000.0,
    "vwap_session": 99800.0,
    "poc": 99500.0,
    "vah": 100300.0,
    "val": 99200.0,
    "cvd_slope": 0.45,
    "delta": 280.0,
    "vpin": null,
    "spread_bps": 0.8,
    "obi_l5": 0.12
  }
}
```

---

## UI Toggle

**Archivo:** `src/screen/dashboard/pane.rs`

Botón de estrella en la barra de controles (esquina superior derecha del pane), solo visible para kline charts:

- ⭐ (`Icon::StarFilled`) = strategy overlay activo
- ☆ (`Icon::Star`) = strategy overlay inactivo

El estado activo se enciende en el botón con `control_btn_style(strategy_active)`.

**Evento:** `Event::ToggleStrategyOverlay` → llama `chart.toggle_strategy_overlay()`.

**Comportamiento de `toggle_strategy_overlay()`:**
- Si activa: corre detección inmediata si hay depth disponible
- Si desactiva: limpia `strategy_signals`

---

## StrategyConfig

```rust
pub struct StrategyConfig {
    pub enabled: bool,
    pub max_spread_bps: f64,    // default: 2.0
    pub max_vpin: f64,          // default: 0.75
    pub min_score: f64,         // default: 0.70
    pub default_ttl_ms: i64,    // default: 300_000 (5 min)
}
```

En `run_strategy_detection`, se usa:
```rust
let cfg = StrategyConfig { enabled: true, ..Default::default() };
```

---

## Qué activa cada detector (checklist)

Para que **LvnLiquidityVacuumBreakout** dispare, activar:
- [x] VWAP (para `price_vs_vwap` y `vwap_session` como stop reference)
- [x] Volume Profile (para `value_location`, `hvn_nearby`, `vah/val`)
- [x] CVD (para `delta` y `cvd_slope`)
- El orderbook ya está siempre disponible

Para que **VwapValuePullbackContinuation** dispare, además:
- [x] Mercado en TrendUp o TrendDown (detectado automáticamente vía OLS)
- [x] AVWAP BOS debe estar disponible (requiere VWAP activo)

Para que **ValueAreaFailedAuction** dispare:
- [x] VolumeProfile activo
- [x] CVD activo (para `failed_acceptance` y `absorption`)
- [x] El precio debe haber roto brevemente fuera del value area
