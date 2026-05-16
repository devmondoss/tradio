# Plan de Implementación — Capa de Datos Institucionales + Estrategias Nuevas
## Para Claude Code — FlowSurface Fork

**Repo base:** https://github.com/flowsurface-rs/flowsurface  
**Exchanges activos:** Binance, Bybit, Hyperliquid, OKX, MEXC  
**Modo:** Shadow only — cero ejecución real, cero API keys de trading  
**Fecha:** Mayo 2026

---

## Contexto para Claude Code

Este proyecto es un fork del repo público `flowsurface-rs/flowsurface` — una plataforma
nativa de charting en Rust con soporte multi-exchange. Ya tiene:

- Crate `exchange/` con adaptadores por exchange (WebSocket + REST)
- Crate `data/` con agregación de ticks, CVD, footprint, volume profile
- Crate `src/` con GUI en Iced + rendering de charts
- Soporte para Binance, Bybit, Hyperliquid, OKX, MEXC
- Streams activos: klines, trades, depth, markPrice

Lo que vamos a construir encima es una **capa de datos institucionales** (datos que el 95%
de traders retail no usa) más **3 detectores de estrategia nuevos** que explotan esos datos.

**Regla absoluta:** shadow only. Detectar, visualizar, loguear. Sin órdenes, sin API keys privadas.

---

## Resumen de qué vamos a construir

```
FASE 1 — Datos nuevos (exchange crate)
  ↓
FASE 2 — Agregación y features derivados (data crate)  
  ↓
FASE 3 — 3 detectores de estrategia nuevos (data/strategy crate)
  ↓
FASE 4 — Overlay visual en el chart (src crate)
  ↓
FASE 5 — Logger JSONL + outcome tracker
```

---

## FASE 1 — Nuevos streams de datos

### 1.1 Datos que hay que agregar al crate `exchange/`

Estos son datos que Binance ya expone públicamente y que no estamos consumiendo.
Solo Binance para esta fase — los otros exchanges se agregan después donde haya equivalente.

#### A. WebSocket nuevo: Liquidaciones en tiempo real

**Stream:** `btcusdt@forceOrder` (por símbolo) y `!forceOrder@arr` (todo el mercado)  
**Ruta:** `wss://fstream.binance.com/stream?streams=btcusdt@forceOrder`  
**Frecuencia:** Por evento — solo llega cuando hay una liquidación  
**Payload relevante:**

```json
{
  "o": {
    "s": "BTCUSDT",
    "S": "SELL",        // side de la liquidación (SELL = long liquidado)
    "q": "0.014",       // cantidad liquidada en BTC
    "p": "9910",        // precio al que se liquidó
    "ap": "9910",       // precio promedio de ejecución
    "X": "FILLED",      // estado
    "l": "0.014",       // cantidad ejecutada
    "T": 1591086046402  // timestamp
  }
}
```

**Tipo Rust a crear en `exchange/src/binance/stream_types.rs`:**

```rust
#[derive(Debug, Clone, Deserialize)]
pub struct LiquidationEvent {
    pub symbol: String,
    pub side: String,           // "BUY" = short liquidado, "SELL" = long liquidado
    pub quantity: f64,
    pub price: f64,
    pub avg_price: f64,
    pub status: String,
    pub timestamp_ms: i64,
}

#[derive(Debug, Clone)]
pub struct LiquidationBar {
    pub timestamp_ms: i64,
    pub long_liquidations_usd: f64,   // suma de longs liquidados en ventana
    pub short_liquidations_usd: f64,  // suma de shorts liquidados en ventana
    pub total_usd: f64,
    pub dominant_side: LiqSide,       // qué lado fue más liquidado
}

pub enum LiqSide { Longs, Shorts, Neutral }
```

---

#### B. REST polling nuevo: Taker Buy/Sell Volume Ratio

**Endpoint:** `GET /futures/data/takerlongshortRatio`  
**Params:** `symbol=BTCUSDT&period=5m&limit=10`  
**Frecuencia:** Fetch cada 1 minuto (el endpoint actualiza cada 5min)  
**Rate limit:** Peso 1 — sin problema  
**Qué retorna:**

```json
[
  {
    "buySellRatio": "1.2342",   // ratio taker buy / taker sell
    "buyVol": "387.5",          // volumen comprador en período
    "sellVol": "313.8",         // volumen vendedor en período  
    "timestamp": 1703001600000
  }
]
```

**Tipo Rust:**

```rust
#[derive(Debug, Clone)]
pub struct TakerRatioSnapshot {
    pub timestamp_ms: i64,
    pub buy_sell_ratio: f64,
    pub buy_vol: f64,
    pub sell_vol: f64,
    // derivado:
    pub taker_imbalance: f64,  // (buy - sell) / (buy + sell), rango -1.0 a +1.0
}
```

---

#### C. REST polling nuevo: Long/Short Ratios institucionales

Tres endpoints distintos — cada uno mide una cosa diferente:

**1. Top Trader Account Ratio** (qué porcentaje de las cuentas top están long vs short)
```
GET /futures/data/topLongShortAccountRatio
params: symbol=BTCUSDT&period=5m&limit=10
```

**2. Top Trader Position Ratio** (qué porcentaje de las *posiciones* top están long vs short — más preciso)
```
GET /futures/data/topLongShortPositionRatio  
params: symbol=BTCUSDT&period=5m&limit=10
```

**3. Global Account Ratio** (todo el mercado, incluye retail)
```
GET /futures/data/globalLongShortAccountRatio
params: symbol=BTCUSDT&period=5m&limit=10
```

**Frecuencia:** Fetch cada 5 minutos — el endpoint actualiza cada 5min  
**Tipo Rust unificado:**

```rust
#[derive(Debug, Clone)]
pub struct LongShortSnapshot {
    pub timestamp_ms: i64,
    pub long_ratio: f64,     // e.g. 0.6234 = 62.34% long
    pub short_ratio: f64,    // e.g. 0.3766 = 37.66% short
    pub ls_ratio: f64,       // long/short ratio directo
    pub source: LsSource,
}

pub enum LsSource {
    TopTraderAccount,
    TopTraderPosition,  // este es el más útil
    GlobalAccount,
}
```

---

#### D. REST polling nuevo: Open Interest histórico

**Endpoint:** `GET /futures/data/openInterestHist`  
**Params:** `symbol=BTCUSDT&period=5m&limit=30` (últimas 2.5 horas)  
**Frecuencia:** Fetch cada 5 minutos  
**Por qué es mejor que el OI actual:** Podés ver si el OI lleva 2 horas acumulando
(momentum real) o si el spike es de los últimos 5 minutos (posiblemente noise).

```rust
#[derive(Debug, Clone)]
pub struct OiHistSnapshot {
    pub timestamp_ms: i64,
    pub open_interest_btc: f64,
    pub open_interest_usd: f64,
}

// Derivado de la serie histórica:
#[derive(Debug, Clone)]
pub struct OiTrend {
    pub current: f64,
    pub change_30m: f64,     // % cambio últimos 30min
    pub change_2h: f64,      // % cambio últimas 2h
    pub slope_5bar: f64,     // OLS slope de los últimos 5 valores
    pub trend: OiTrendDir,
}

pub enum OiTrendDir {
    AccumulatingFast,   // +>1% en 30min
    Accumulating,       // +0.3% a +1% en 30min
    Flat,
    Decreasing,
    DecreasingFast,
}
```

---

#### E. REST polling nuevo: Funding Rate histórico

**Endpoint:** `GET /fapi/v1/fundingRate`  
**Params:** `symbol=BTCUSDT&limit=10` (últimos 10 períodos = últimas ~80 horas)  
**Frecuencia:** Fetch cada hora — cambia cada 8h  
**Para qué:** Comparar el funding actual vs su contexto histórico reciente.
0.05% puede ser normal o extremo dependiendo de los últimos 5 días.

```rust
#[derive(Debug, Clone)]
pub struct FundingRateHistory {
    pub rates: Vec<FundingRateSample>,
    // derivado:
    pub current: f64,
    pub avg_7d: f64,
    pub percentile_current: f64,   // 0-100, qué tan extremo es vs historia
    pub regime: FundingRegime,
}

pub enum FundingRegime {
    ExtremeLong,    // > percentil 90 positivo
    ElevatedLong,   // > percentil 70 positivo
    Neutral,
    ElevatedShort,  // > percentil 70 negativo
    ExtremeShort,   // > percentil 90 negativo
}
```

---

### 1.2 Estructura de archivos a crear/modificar en `exchange/`

```
exchange/
  src/
    binance/
      stream_types.rs          ← MODIFICAR: agregar LiquidationEvent
      ws_handlers.rs           ← MODIFICAR: agregar handler forceOrder stream
      rest/
        market_data.rs         ← MODIFICAR: agregar los 5 endpoints nuevos
        types.rs               ← MODIFICAR: agregar tipos de response
    
    // Los tipos compartidos van en data/ para que sean exchange-agnósticos
```

---

## FASE 2 — Agregación y features derivados

### 2.1 Nuevo módulo: `data/src/institutional/`

Este módulo toma los datos crudos de la Fase 1 y produce features listos para los detectores.

```
data/src/institutional/
  mod.rs
  liquidation_tracker.rs    ← agrega liquidaciones en ventanas de tiempo
  ls_ratio_tracker.rs       ← mantiene historia de long/short ratios
  oi_tracker.rs             ← calcula tendencia de OI
  funding_tracker.rs        ← calcula régimen de funding con contexto histórico
  context_builder.rs        ← ensambla InstitutionalContext para los detectores
```

#### `liquidation_tracker.rs`

```rust
pub struct LiquidationTracker {
    // Buffer de los últimos N eventos
    events: VecDeque<LiquidationEvent>,
    window_ms: i64,  // default: 5 minutos
}

impl LiquidationTracker {
    /// Agrega un evento de liquidación
    pub fn push(&mut self, event: LiquidationEvent) { ... }
    
    /// Purga eventos fuera de la ventana de tiempo
    pub fn prune(&mut self, now_ms: i64) { ... }
    
    /// Features derivados para detectores
    pub fn snapshot(&self, now_ms: i64) -> LiquidationSnapshot {
        LiquidationSnapshot {
            long_liq_usd_5m: ...,    // USD de longs liquidados en 5 min
            short_liq_usd_5m: ...,   // USD de shorts liquidados en 5 min
            total_usd_5m: ...,
            dominant_side: ...,
            cascade_detected: ...,   // true si > umbral en < 60s
            last_event_ms: ...,
        }
    }
}

pub struct LiquidationSnapshot {
    pub long_liq_usd_5m: f64,
    pub short_liq_usd_5m: f64,
    pub total_usd_5m: f64,
    pub dominant_side: LiqSide,
    pub cascade_detected: bool,    // > $5M en 60s = cascada
    pub last_event_ms: Option<i64>,
}
```

#### `ls_ratio_tracker.rs`

```rust
pub struct LsRatioTracker {
    top_position: VecDeque<LongShortSnapshot>,  // últimas 6 muestras (30 min)
    global: VecDeque<LongShortSnapshot>,
}

impl LsRatioTracker {
    pub fn snapshot(&self) -> LsRatioContext {
        LsRatioContext {
            top_traders_long_pct: ...,      // % de posiciones top traders long
            retail_long_pct: ...,           // % de cuentas global long
            smart_money_divergence: ...,    // top_traders vs retail divergen?
            divergence_signal: DivergenceSignal,
        }
    }
}

pub enum DivergenceSignal {
    // Top traders short, retail long = setup squeeze alcista
    SmartShortRetailLong,
    // Top traders long, retail short = setup squeeze bajista
    SmartLongRetailShort,  
    // Todos alineados = momentum genuino
    Aligned,
    // Sin divergencia clara
    Neutral,
}
```

#### `context_builder.rs`

Ensambla todo en un único struct que consumen los detectores:

```rust
#[derive(Debug, Clone)]
pub struct InstitutionalContext {
    pub timestamp_ms: i64,
    
    // Liquidaciones
    pub liquidations: LiquidationSnapshot,
    
    // Long/Short ratios
    pub ls_ratio: LsRatioContext,
    
    // Open Interest
    pub oi_trend: OiTrend,
    
    // Taker ratio (reemplaza al taker_imbalance calculado localmente)
    pub taker_ratio: Option<TakerRatioSnapshot>,
    
    // Funding con contexto histórico
    pub funding: FundingRateHistory,
    
    // Calidad del dato
    pub quality: DataQuality,
    pub staleness_ms: i64,  // cuánto tiene el dato más viejo
}
```

---

## FASE 3 — Los 3 detectores nuevos

Estos van en `data/src/strategy/detectors/` junto a los existentes.

---

### Detector 4: `LiquidationHunt`

**Concepto:** Detectar cuando una concentración de stops va a ser barrida y
posicionarse a favor del barrido — no contra.

**Hipótesis:**
```
Precio se acerca a zona de stops conocida (orderbook wall + LVN)
→ OI sube (nuevas posiciones agresivas)
→ CVD confirma dirección
→ Liquidaciones empiezan (primeras del lado contrario)
→ El barrido está en curso
→ Entry en dirección del barrido, target = siguiente nivel de liquidez
```

**Condiciones LONG (barrido de shorts):**
```
DATOS INSTITUCIONALES:
- liquidations.short_liq_usd_5m > umbral_inicio (ej. $500K en 5min)
- liquidations.dominant_side == Shorts
- oi_trend == Accumulating o AccumulatingFast (nuevas posiciones entrando)
- taker_ratio.taker_imbalance > +0.15 (compradores agresivos dominando)

DATOS TÉCNICOS:
- CVD slope positivo últimas 3 barras
- Precio rompiendo LVN o zona delgada arriba
- No hay HVN inmediata que bloquee el target

GATES:
- funding.regime != ExtremeLong (no entrar long si ya están todos long)
- ls_ratio.smart_money_divergence != SmartShortRetailLong
  (si los top traders están en contra, no entrar)
```

**Pseudocódigo Rust:**

```rust
pub fn detect_liquidation_hunt(
    ctx: &StrategyMarketContext,
    inst: &InstitutionalContext,
    cfg: &StrategyConfig,
) -> Option<StrategySignal> {
    // Gate institucional duro
    if matches!(inst.funding.regime, FundingRegime::ExtremeLong) 
        && inst.ls_ratio.divergence_signal == DivergenceSignal::SmartShortRetailLong {
        return None; // smart money en contra + funding extremo = no entrar long
    }

    let px = ctx.price;
    let atr = ctx.atr.filter(|&a| a > 1.0)?;

    // LONG — barrido de shorts
    let liq_confirms_long = 
        inst.liquidations.short_liq_usd_5m > cfg.liq_hunt_min_usd &&
        matches!(inst.liquidations.dominant_side, LiqSide::Shorts);

    let momentum_confirms =
        inst.oi_trend.slope_5bar > 0.0 &&
        inst.taker_ratio.as_ref()
            .map(|t| t.taker_imbalance > 0.15)
            .unwrap_or(false) &&
        ctx.flow.cvd_slope.unwrap_or(0.0) > 0.0;

    let path_clear = ctx.orderbook.thin_zone_above;

    if liq_confirms_long && momentum_confirms && path_clear {
        let entry = px;
        let stop = entry - 1.0 * atr;

        // Target = siguiente HVN o wall
        let mut targets = ctx.volume_profile.hvn_nearby.clone();
        if let Some(vah) = ctx.volume_profile.vah { targets.push(vah); }
        targets.extend(&ctx.orderbook.walls_above);
        
        let target = targets.iter()
            .filter(|&&t| t.is_finite() && t > entry + 1.5 * atr)
            .copied()
            .min_by(|a, b| a.partial_cmp(b).unwrap_or(Equal))?;

        if target > entry && stop < entry {
            return Some(StrategySignal {
                action: StrategyAction::ShadowSignal,
                strategy_id: Some(StrategyId::LiquidationHunt),
                side: Some(Side::Long),
                entry_price: Some(entry),
                stop_price: Some(stop),
                target_price: Some(target),
                score: 0.0, // se calcula en score_signal
                evidence: vec![
                    Evidence::ShortLiquidationsCascade,
                    Evidence::OiAccumulating,
                    Evidence::TakerImbalanceBullish,
                    Evidence::CvdAligned,
                    Evidence::ThinZoneAbove,
                ],
                // ...
            });
        }
    }

    // SHORT — barrido de longs (simétrico)
    // ... 

    None
}
```

**Score base:** 0.0 — se calcula en `score_signal` con los evidence items  
**Stop:** 1.0× ATR  
**Target:** Siguiente HVN o wall, mínimo 1.5× ATR de distancia  
**TTL:** 10 minutos (más corto — las liquidaciones se resuelven rápido)

---

### Detector 5: `FundingExhaustionReversal`

**Concepto:** Cuando el funding rate está en extremos históricos, las posiciones
apalancadas se vuelven insostenibles. Los longs (o shorts) empiezan a cerrar
no por precio sino por costo de carry — eso crea momentum orgánico de reversión.

**Hipótesis:**
```
Funding en percentil >90% de los últimos 30 días
→ Long/short ratio global confirma sobrecarga de un lado
→ Top traders ya están posicionando en la dirección contraria
→ OI empieza a bajar (posiciones cerrando)
→ CVD se debilita
→ Reversión inminente
```

**Condiciones SHORT (funding extremo positivo = demasiados longs):**
```
FUNDING (condición principal):
- funding.regime == ExtremeLong (percentil > 85% histórico)
- funding.current > +0.06% (absoluto, no relativo)

CONFIRMACIÓN INSTITUCIONAL:
- ls_ratio.top_traders_long_pct < 0.50 (top traders ya están saliendo)
- ls_ratio.retail_long_pct > 0.65 (retail todavía long = fuel para reversión)
- oi_trend == Decreasing o DecreasingFast (posiciones cerrando)

CONFIRMACIÓN TÉCNICA:
- CVD slope <= 0 (flujo no confirma precio)
- Precio cerca de VAH o resistencia conocida
- taker_ratio.buy_sell_ratio bajando (menos agresión compradora)

GATE:
- Spread dentro de límites
- No hay cascade de liquidaciones shorts activa
  (si están liquidando shorts agresivamente, el squeeze puede continuar)
```

**Pseudocódigo Rust:**

```rust
pub fn detect_funding_exhaustion(
    ctx: &StrategyMarketContext,
    inst: &InstitutionalContext,
    cfg: &StrategyConfig,
) -> Option<StrategySignal> {
    let px = ctx.price;
    let atr = ctx.atr.filter(|&a| a > 1.0)?;

    // SHORT — funding extremo positivo
    let funding_extreme_long =
        matches!(inst.funding.regime, FundingRegime::ExtremeLong) &&
        inst.funding.current > cfg.funding_extreme_threshold; // default 0.0006

    let institutional_diverging =
        inst.ls_ratio.top_traders_long_pct < 0.52 &&  // smart money saliendo
        inst.ls_ratio.retail_long_pct > 0.62;          // retail todavía adentro

    let oi_weakening =
        matches!(inst.oi_trend.trend, OiTrendDir::Decreasing | OiTrendDir::DecreasingFast);

    let flow_weakening =
        ctx.flow.cvd_slope.unwrap_or(0.0) <= 0.0 &&
        inst.taker_ratio.as_ref()
            .map(|t| t.buy_sell_ratio < 1.0)
            .unwrap_or(false);

    // Gate: no entrar si hay cascade de liquidaciones longs activa
    // (eso significa que el movimiento ya está en curso, tarde para entrar)
    if inst.liquidations.long_liq_usd_5m > cfg.liq_cascade_threshold {
        return None;
    }

    if funding_extreme_long && institutional_diverging && oi_weakening && flow_weakening {
        let entry = px;
        let stop = f64::max(
            ctx.volume_profile.vah.unwrap_or(px + atr),
            entry + 0.75 * atr
        );
        let target = ctx.volume_profile.val
            .unwrap_or(entry - 2.0 * atr);

        if target < entry && stop > entry {
            return Some(StrategySignal {
                action: StrategyAction::ShadowSignal,
                strategy_id: Some(StrategyId::FundingExhaustionReversal),
                side: Some(Side::Short),
                entry_price: Some(entry),
                stop_price: Some(stop),
                target_price: Some(target),
                score: 0.0,
                evidence: vec![
                    Evidence::FundingExtremePositive,
                    Evidence::TopTradersExiting,
                    Evidence::RetailStillLong,
                    Evidence::OiDecreasing,
                    Evidence::CvdWeakening,
                ],
                // ...
            });
        }
    }

    // LONG — funding extremo negativo (simétrico)
    // ...

    None
}
```

**Por qué este es especial:** Es el único detector donde la señal principal viene de datos
que el mercado no puede "engañar" fácilmente — el costo real de mantener una posición
apalancada. Papers académicos con significancia estadística lo respaldan directamente.

---

### Detector 6: `SmartMoneyDivergence`

**Concepto:** Cuando los top traders de Binance (las cuentas con mayor capital y
presumiblemente mejor información) están posicionados en una dirección mientras
el retail (cuentas pequeñas) está en la dirección contraria, los top traders
históricamente tienen razón. La divergencia es el setup.

**Hipótesis:**
```
Top traders posición ratio: 65% short
Global account ratio: 70% long (retail)
→ Divergencia significativa: smart money vs retail
→ Confirmación técnica de que el precio está toppeando
→ Entry en dirección de smart money
→ Target = donde el retail va a capitular
```

**Condiciones SHORT (smart money short, retail long):**
```
DIVERGENCIA (condición principal):
- ls_ratio.top_traders_long_pct < 0.45 (top traders principalmente short)
- ls_ratio.retail_long_pct > 0.60 (retail principalmente long)
- divergencia = retail_long - top_traders_long > 0.20 (20% de diferencia)

CONFIRMACIÓN ADICIONAL:
- funding.regime == ElevatedLong o ExtremeLong (retail pagando carry)
- oi_trend == Flat o Decreasing (no hay acumulación nueva — el move está maduro)
- CVD mostrando debilidad (no confirmando precio si está en highs)

CONFIRMACIÓN TÉCNICA:
- Precio en zona de resistencia (HVN, VAH, wall)
- Taker ratio bajando en las últimas 2-3 muestras

GATE:
- No hay cascade activa (esperar a que se calme antes de entrar)
- Spread dentro de límites
```

**Nota sobre timing:** Este es el detector más lento — la divergencia puede durar horas
antes de resolverse. El TTL debería ser más largo (20-30 minutos) y el stop más amplio (1.5× ATR).

**Pseudocódigo Rust:**

```rust
pub fn detect_smart_money_divergence(
    ctx: &StrategyMarketContext,
    inst: &InstitutionalContext,
    cfg: &StrategyConfig,
) -> Option<StrategySignal> {
    let px = ctx.price;
    let atr = ctx.atr.filter(|&a| a > 1.0)?;

    let top_long = inst.ls_ratio.top_traders_long_pct;
    let retail_long = inst.ls_ratio.retail_long_pct;
    let divergence = retail_long - top_long; // positivo = retail más long que smart

    // SHORT — smart money short, retail long
    let strong_divergence_short = 
        top_long < cfg.smart_short_threshold &&      // default 0.45
        retail_long > cfg.retail_long_threshold &&   // default 0.60
        divergence > cfg.min_divergence;             // default 0.18

    // Confirmar con funding (retail pagando caro por estar long)
    let funding_confirms =
        matches!(inst.funding.regime, FundingRegime::ElevatedLong | FundingRegime::ExtremeLong);

    // Confirmar con OI (el move está maduro, no hay acumulación nueva)
    let oi_confirms =
        !matches!(inst.oi_trend.trend, OiTrendDir::AccumulatingFast);

    // Confirmar con técnico (precio en zona de resistencia)
    let technical_confirms =
        ctx.volume_profile.vah.map(|vah| (px - vah).abs() < 0.5 * atr).unwrap_or(false) ||
        ctx.orderbook.walls_above.iter().any(|&w| (w - px).abs() < 0.3 * atr);

    if strong_divergence_short && funding_confirms && oi_confirms && technical_confirms {
        let entry = px;
        let stop = entry + 1.5 * atr;  // stop más amplio — setup más lento
        let target = ctx.volume_profile.val
            .unwrap_or(entry - 3.0 * atr);

        if target < entry && stop > entry {
            return Some(StrategySignal {
                action: StrategyAction::ShadowSignal,
                strategy_id: Some(StrategyId::SmartMoneyDivergence),
                side: Some(Side::Short),
                entry_price: Some(entry),
                stop_price: Some(stop),
                target_price: Some(target),
                score: 0.0,
                ttl_ms: 20 * 60 * 1000,  // 20 minutos, más largo
                evidence: vec![
                    Evidence::SmartMoneyShort,
                    Evidence::RetailLongExtreme,
                    Evidence::FundingElevated,
                    Evidence::OiMature,
                    Evidence::PriceAtResistance,
                ],
                // ...
            });
        }
    }

    // LONG — smart money long, retail short (simétrico)
    // ...

    None
}
```

---

## FASE 4 — Overlay visual en el chart

### 4.1 Nuevo panel: `Institutional Flow`

Agregar un sub-panel opcional debajo del candlestick chart (al mismo nivel que Volume/OI/CVD):

```
Candlestick Chart
─────────────────────────────────
Volume  [barras]
─────────────────────────────────
Institutional Flow  ← NUEVO
  Liquidaciones (barras rojas/verdes)
  Long/Short ratio (línea)
  Funding regime (color de fondo)
─────────────────────────────────
Open Interest  [existente]
```

### 4.2 Overlay de señales institucionales

Las señales de los 3 detectores nuevos se muestran con marcadores distintos
a los de los detectores existentes:

```
Detectores existentes:  triángulo sólido ▲▼
LiquidationHunt:        icono de relámpago ⚡ (urgente, corto plazo)
FundingExhaustion:      icono de funding $ (macro, más lento)  
SmartMoneyDivergence:   icono de ballena 🐋 (institucional)
```

Tooltip al hover:

```
┌──────────────────────────────────────────┐
│  LIQUIDATION_HUNT | LONG               ⚡ │
│  Score: 0.84                             │
│  Entry: 100,000 | Stop: 99,600           │
│  Target: 101,200                         │
│                                          │
│  Institucional:                          │
│   • Short liquidations: $2.4M en 5min   │
│   • OI: +0.8% en 30min                  │
│   • Taker imbalance: +0.31               │
│   • Top traders: 61% long                │
└──────────────────────────────────────────┘
```

---

## FASE 5 — Logger y outcome tracker

### 5.1 JSONL extendido

El formato del log agrega campos institucionales:

```json
{
  "action": "ShadowSignal",
  "strategy": "LIQUIDATION_HUNT",
  "side": "LONG",
  "regime": "TrendUp_Expansion",
  "entry_price": 100000.0,
  "stop_price": 99600.0,
  "target_price": 101200.0,
  "score": 0.84,
  "ttl_ms": 600000,
  "evidence": ["short_liquidations_cascade", "oi_accumulating", "taker_imbalance_bullish"],
  "institutional": {
    "short_liq_usd_5m": 2400000,
    "long_liq_usd_5m": 180000,
    "top_traders_long_pct": 0.61,
    "retail_long_pct": 0.54,
    "funding_current": 0.00042,
    "funding_regime": "ElevatedLong",
    "oi_change_30m": 0.008,
    "taker_buy_sell_ratio": 1.31
  },
  "context": {
    "price": 100000.0,
    "vwap_session": 99950.0,
    "poc": 99500.0,
    "vah": 100100.0,
    "val": 99000.0,
    "cvd_slope": 0.45,
    "atr": 400.0
  },
  "timestamp_ms": 1710000000000
}
```

### 5.2 Archivos de log separados

```
data/shadow_events/
  shadow_signals.jsonl        ← ShadowSignal (trades tomados)
  shadow_rejected.jsonl       ← Wait con score bajo (calibración threshold)
  shadow_blocked.jsonl        ← Blocked con reason (calibración gate)
  shadow_institutional.jsonl  ← Snapshot institucional cada 5min (para análisis)
```

El `shadow_institutional.jsonl` guarda el estado del contexto institucional
periódicamente, no solo cuando hay señal — así podés correlacionar en el
análisis offline qué valores de funding/LS ratio precedieron movimientos.

---

## FASE 6 — Corrección de bugs antes de implementar (no saltear)

Estos bugs deben corregirse en los detectores existentes antes de agregar los nuevos.
Hacerlos ahora evita que los datos institucionales se construyan sobre lógica rota.

| # | Archivo | Bug | Fix |
|---|---------|-----|-----|
| 1 | `value_area_failed_auction.rs` | `taker_imbalance < 0.25` no filtra nada | Cambiar a `< 0.10` para short, `> -0.10` para long. O eliminar y dejar el dato oficial de `takerlongshortRatio` (Fase 1B) hacer esa función |
| 2 | `vwap_value_pullback_continuation.rs` | `BelowVal` en long = contradicción | Cambiar a `InValue` solo |
| 3 | `lvn_liquidity_vacuum_breakout.rs` | `partial_cmp().unwrap()` puede panic | Agregar `.filter(|x| x.is_finite())` antes del sort |
| 4 | `router.rs` | ATR = 0 no protegido | Guard `ctx.atr.filter(|&a| a > 1.0)?` en el router |
| 5 | `types.rs` | `StrategySignal` no tiene `regime` | Agregar campo `regime: Regime` |
| 6 | `logger.rs` | Solo loguea `ShadowSignal` | Loguear también `Blocked` y `Wait` en archivos separados |

---

## Estructura de archivos — resumen completo

```
exchange/
  src/
    binance/
      stream_types.rs       ← agregar LiquidationEvent
      ws_handlers.rs        ← suscribir forceOrder stream  
      rest/
        market_data.rs      ← agregar 5 endpoints nuevos
        rest_types.rs       ← agregar tipos de response

data/
  src/
    institutional/          ← NUEVO MÓDULO
      mod.rs
      liquidation_tracker.rs
      ls_ratio_tracker.rs
      oi_tracker.rs
      funding_tracker.rs
      context_builder.rs    ← ensambla InstitutionalContext
    strategy/
      types.rs              ← agregar Evidence nuevos, StrategyId nuevos, regime field
      context.rs            ← agregar InstitutionalContext al StrategyMarketContext
      scoring.rs            ← agregar scoring para evidences institucionales
      logger.rs             ← 4 archivos JSONL separados
      detectors/
        mod.rs
        // existentes (con bugs corregidos):
        lvn_liquidity_vacuum_breakout.rs
        vwap_value_pullback_continuation.rs
        value_area_failed_auction.rs
        // NUEVOS:
        liquidation_hunt.rs
        funding_exhaustion_reversal.rs
        smart_money_divergence.rs

src/
  chart/
    indicator/
      kline/
        institutional_flow.rs  ← nuevo sub-panel
    strategy_overlay.rs        ← marcadores nuevos para detectores institucionales
```

---

## Configuración — nuevos campos en `StrategyConfig`

```rust
pub struct StrategyConfig {
    // ... campos existentes ...

    // Liquidation Hunt
    pub liq_hunt_min_usd: f64,          // default: 500_000.0 ($500K en 5min)
    pub liq_cascade_threshold: f64,     // default: 5_000_000.0 ($5M = cascada)
    pub liq_ttl_ms: i64,                // default: 600_000 (10 min)

    // Funding Exhaustion
    pub funding_extreme_threshold: f64, // default: 0.0006 (0.06% por período)
    pub funding_elevated_threshold: f64,// default: 0.0003 (0.03%)
    pub funding_ttl_ms: i64,            // default: 1_800_000 (30 min)

    // Smart Money Divergence
    pub smart_short_threshold: f64,     // default: 0.45 (top traders < 45% long)
    pub retail_long_threshold: f64,     // default: 0.60 (retail > 60% long)
    pub min_divergence: f64,            // default: 0.18 (18% de diferencia)
    pub smd_ttl_ms: i64,                // default: 1_200_000 (20 min)
}
```

---

## Tests mínimos por detector nuevo

### LiquidationHunt
```
- genera LONG cuando short_liq > threshold + oi acumulando + thin_zone_above
- genera SHORT cuando long_liq > threshold + oi acumulando + thin_zone_below
- rechaza si funding == ExtremeLong y divergence == SmartShortRetailLong (gate)
- rechaza si cascade activa (long_liq muy alto, timing incorrecto)
- rechaza si no hay thin_zone en dirección del trade
- rechaza si target < 1.5 ATR de distancia
```

### FundingExhaustionReversal
```
- genera SHORT cuando funding == ExtremeLong + top traders saliendo + oi decreasing
- genera LONG cuando funding == ExtremeShort + top traders saliendo + oi decreasing
- rechaza si cascade de liquidaciones activa en mismo lado
- rechaza si funding es ElevatedLong pero no ExtremeLong
- rechaza si oi sigue acumulando (setup no maduro)
- score correcto con y sin confirmación de CVD
```

### SmartMoneyDivergence
```
- genera SHORT cuando divergencia > 18% + funding elevated + precio en resistencia
- genera LONG cuando divergencia < -18% + funding depressed + precio en soporte
- rechaza si divergencia es 15% (debajo del umbral)
- rechaza si top traders y retail alineados (no hay divergencia)
- TTL es 20min, no el default de 5min
- stop es 1.5 ATR, no 1.0 ATR
```

### LiquidationTracker (unit tests de agregación)
```
- suma correcta de long_liq_usd en ventana de 5min
- purge correcto de eventos fuera de la ventana
- cascade_detected = true cuando > $5M en 60s
- cascade_detected = false cuando distribuido en 5min
- dominant_side correcto cuando un lado domina 70%+
```

---

## Orden de implementación recomendado

```
Día 1-2: FASE 1 + FASE 6
  - Corregir los 6 bugs existentes
  - Agregar LiquidationEvent al WebSocket de Binance
  - Agregar los 5 endpoints REST nuevos
  - Verificar que los datos llegan correctamente (log crudo)

Día 3-4: FASE 2
  - Implementar los 4 trackers en data/institutional/
  - Implementar context_builder
  - Escribir tests unitarios de los trackers
  - Verificar que InstitutionalContext se construye con datos reales

Día 5-6: FASE 3
  - Implementar LiquidationHunt
  - Implementar FundingExhaustionReversal  
  - Implementar SmartMoneyDivergence
  - Tests unitarios de cada detector
  - Integrar en route_strategy()

Día 7: FASE 4 + FASE 5
  - Sub-panel Institutional Flow en el chart
  - Marcadores nuevos en overlay
  - Logger JSONL extendido con campos institucionales
  - 4 archivos de log separados

Día 8+: Validación
  - Correr shadow trading con los 6 detectores activos
  - Verificar que los datos institucionales llegan con la frecuencia correcta
  - Verificar que los detectores nuevos generan señales en condiciones correctas
  - No declarar ningún detector "usable" hasta 300+ señales por detector
```

---

## Rate limits — verificación

Todos los nuevos endpoints REST están dentro de los límites seguros:

| Endpoint | Peso | Frecuencia fetch | Requests/hora | Límite Binance |
|----------|------|-----------------|---------------|----------------|
| `takerlongshortRatio` | 1 | cada 1min | 60 | 2400/min ✅ |
| `topLongShortPositionRatio` | 1 | cada 5min | 12 | 2400/min ✅ |
| `topLongShortAccountRatio` | 1 | cada 5min | 12 | 2400/min ✅ |
| `globalLongShortAccountRatio` | 1 | cada 5min | 12 | 2400/min ✅ |
| `openInterestHist` | 1 | cada 5min | 12 | 2400/min ✅ |
| `fundingRate` hist | 1 | cada 60min | 1 | 2400/min ✅ |

El WebSocket `forceOrder` no consume rate limit — es push, no pull.

---

## Lo que NO hacer en esta implementación

```
- No agregar API keys de trading
- No implementar ejecución de órdenes
- No implementar position management real
- No operar con capital real hasta 300+ trades shadow validados por detector
- No agregar Bybit/Hyperliquid/OKX equivalentes en esta fase
  (hacerlo después de validar que los datos de Binance tienen edge real)
- No implementar ML sobre estos datos todavía
  (primero medir si los features tienen señal manual)
```

---

## Entregable esperado de Claude Code

Al terminar esta implementación:

1. Lista de archivos creados y modificados
2. Confirmación de que `cargo build --release` pasa sin errores
3. Confirmación de que los tests nuevos pasan
4. Descripción de cómo suscribirse al stream `forceOrder` en el contexto del exchange adapter existente
5. Descripción de cómo `InstitutionalContext` fluye desde los trackers hasta los detectores
6. Sample del JSONL generado con campos institucionales reales

---

*Construido sobre: flowsurface-rs/flowsurface (GPLv3)*  
*Modo: Shadow trading — zero ejecución real*  
*Stack: Rust + Iced + Binance USDM Futures API*
