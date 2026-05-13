# Propuesta para Claude Code: Módulo de Estrategias Microestructurales en Flowsurface

**Proyecto:** flowsurface-rs/flowsurface  
**Objetivo:** agregar una capa de detección, visualización y logging de señales *shadow* basadas en VWAP/AVWAP, Volume Profile, Order Flow y Order Book.  
**Modo obligatorio inicial:** solo observación, overlay y registro. No ejecución real.  
**Estado:** propuesta para implementación local experimental.

---

## 0. Resumen ejecutivo

Queremos extender Flowsurface para probar estrategias microestructurales que un trader profesional leería mecánicamente en DOM, footprint, VWAP y Volume Profile, pero convertidas en reglas programables.

La prioridad no es “hacer trading automático” todavía. La prioridad es:

```text
market data
→ features microestructurales
→ detección de setups
→ overlay visual
→ shadow event
→ medición de outcome
→ evaluación estadística
```

No se debe implementar ejecución real, órdenes reales, API keys ni live trading en esta fase.  
Primero se valida si los setups tienen edge neto después de spread, fees, slippage y latencia. Qué idea revolucionaria: medir antes de apostar.

---

## 1. Contexto del repositorio

Flowsurface es una app nativa de escritorio en Rust orientada a crypto order flow. Ya tiene una base técnica ideal para esto:

- Heatmap / Historical DOM
- Footprint
- DOM / Ladder
- Time & Sales
- Order book L2
- Trades en vivo
- Aggregations locales
- UI en Rust con Iced
- Core de procesamiento local

La propuesta debe respetar esta arquitectura. No queremos convertir el repo en un Frankenstein con scripts Python pegados al render, porque bastante sufrió ya la industria del software con “prototipos que llegaron a producción”.

---

## 2. Decisión arquitectónica: Rust vs Python

### Veredicto

**Implementar el módulo principal en Rust.**  
**Usar Python solo fuera del hot path para análisis offline, calibración y notebooks.**

### Por qué Rust debe ser la línea principal

Rust es la opción correcta para:

- Ejecutar detectores en tiempo real.
- Mantener baja latencia.
- Integrarse con la UI actual.
- Compartir tipos con el resto del repo.
- Dibujar overlays en charts existentes.
- Evitar problemas de empaquetado cross-platform.
- Evitar runtime externo dentro de una app desktop.
- Mantener seguridad de tipos en una capa de señales donde los errores silenciosos cuestan caro.

Flowsurface ya está construido alrededor de Rust, Iced y procesamiento local. Si metemos Python en el hot path, habría que resolver:

- empaquetado de Python por plataforma;
- dependencia de entorno local;
- IPC o FFI;
- errores de versión;
- latencia;
- GIL;
- logs cruzados;
- debugging más difícil;
- distribución más frágil.

El costo no compensa para detectores simples basados en features que ya están disponibles en Rust.

### Para qué sí usar Python

Python puede usarse para:

- analizar archivos exportados;
- calibrar thresholds;
- generar reportes;
- hacer notebooks;
- revisar distribuciones;
- entrenar modelos futuros si algún día tiene sentido;
- comparar outcomes por régimen/sesión/setup.

Pero Python debe vivir fuera del loop principal:

```text
Flowsurface Rust app
→ export shadow_events.csv / parquet / jsonl
→ Python notebooks offline
→ thresholds/config revisados
→ volver a Rust
```

### Alternativa futura

Si más adelante se necesita compartir lógica, usar:

- PyO3/maturin para exponer módulos Rust a Python;
- no al revés;
- o IPC local si se requiere proceso separado.

Pero para esta fase: **no hace falta**. Menos ingeniería ceremonial, más validación.

---

## 3. Estrategias a implementar

Solo tres estrategias iniciales. No crear zoológico de señales.

```text
1. VALUE_AREA_FAILED_AUCTION
2. VWAP_VALUE_PULLBACK_CONTINUATION
3. LVN_LIQUIDITY_VACUUM_BREAKOUT
```

Filtro obligatorio:

```text
4. TOXIC_FLOW_GATE
```

Cada estrategia debe generar `StrategySignal` con evidencia, score, entry hipotético, stop hipotético, target hipotético y TTL.  
No debe mandar órdenes.

---

## 4. Contratos de datos

Crear un módulo nuevo:

```text
src/strategy/
  mod.rs
  types.rs
  context.rs
  router.rs
  scoring.rs
  logger.rs
  detectors/
    mod.rs
    toxic_flow_gate.rs
    value_area_failed_auction.rs
    vwap_value_pullback_continuation.rs
    lvn_liquidity_vacuum_breakout.rs
```

### 4.1 Enums base

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Side {
    Long,
    Short,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StrategyId {
    ValueAreaFailedAuction,
    VwapValuePullbackContinuation,
    LvnLiquidityVacuumBreakout,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StrategyAction {
    Wait,
    ShadowSignal,
    Blocked,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Regime {
    TrendUp,
    TrendDown,
    Chop,
    Compression,
    Expansion,
    Stress,
    Aftermath,
    Unknown,
}
```

### 4.2 Volume Profile contract

```rust
#[derive(Debug, Clone)]
pub struct VolumeProfileContext {
    pub poc: Option<f64>,
    pub vah: Option<f64>,
    pub val: Option<f64>,
    pub hvn_nearby: Vec<f64>,
    pub lvn_nearby: Vec<f64>,
    pub value_location: ValueLocation,
    pub quality: DataQuality,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ValueLocation {
    AboveVah,
    BelowVal,
    InValue,
    Unknown,
}
```

### 4.3 VWAP / AVWAP contract

```rust
#[derive(Debug, Clone)]
pub struct VwapContext {
    pub vwap_session: Option<f64>,
    pub avwap_bos: Option<f64>,
    pub avwap_event: Option<f64>,
    pub price_vs_vwap: PriceRelation,
    pub price_vs_avwap_bos: PriceRelation,
    pub price_vs_avwap_event: PriceRelation,
    pub quality: DataQuality,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PriceRelation {
    Above,
    Below,
    At,
    Unknown,
}
```

### 4.4 Order Flow contract

```rust
#[derive(Debug, Clone)]
pub struct OrderFlowContext {
    pub cvd: Option<f64>,
    pub cvd_slope: Option<f64>,
    pub delta: Option<f64>,
    pub taker_imbalance: Option<f64>,
    pub buy_volume: Option<f64>,
    pub sell_volume: Option<f64>,
    pub vpin: Option<f64>,

    pub footprint_absorption: AbsorptionSide,
    pub stacked_imbalance: ImbalanceSide,

    pub failed_acceptance: bool,
    pub sweep_confirmed: bool,
    pub mss_active: bool,

    pub quality: DataQuality,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AbsorptionSide {
    Bid,
    Ask,
    None,
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ImbalanceSide {
    Bullish,
    Bearish,
    None,
    Unknown,
}
```

### 4.5 Order Book contract

```rust
#[derive(Debug, Clone)]
pub struct OrderBookContext {
    pub obi_l5: Option<f64>,
    pub obi_l10: Option<f64>,
    pub obi_l20: Option<f64>,
    pub microprice: Option<f64>,
    pub spread_bps: Option<f64>,

    pub walls_above: Vec<f64>,
    pub walls_below: Vec<f64>,

    pub thin_zone_above: bool,
    pub thin_zone_below: bool,

    pub quality: DataQuality,
}
```

### 4.6 Market strategy context

```rust
#[derive(Debug, Clone)]
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

### 4.7 Strategy signal output

```rust
#[derive(Debug, Clone)]
pub struct StrategySignal {
    pub action: StrategyAction,
    pub strategy_id: Option<StrategyId>,
    pub side: Option<Side>,

    pub entry_price: Option<f64>,
    pub stop_price: Option<f64>,
    pub target_price: Option<f64>,

    pub score: f64,
    pub ttl_ms: i64,

    pub evidence: Vec<String>,
    pub missing: Vec<String>,
    pub invalidation: Vec<String>,

    pub created_at_ms: i64,
}
```

### 4.8 Data quality

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DataQuality {
    Live,
    Fallback,
    Degraded,
    Stale,
    Missing,
}
```

---

## 5. Filtro obligatorio: TOXIC_FLOW_GATE

Este gate bloquea señales cuando los datos o condiciones son peligrosos.

### Condiciones

```text
Bloquear si:
- regime == Stress
- regime == Aftermath
- flow.quality != Live
- volume_profile.quality != Live
- orderbook.quality != Live
- spread_bps > max_spread_bps
- vpin > max_vpin
```

### Config inicial

```rust
pub struct StrategyConfig {
    pub max_spread_bps: f64,
    pub max_vpin: f64,
    pub min_score: f64,
    pub default_ttl_ms: i64,
}

impl Default for StrategyConfig {
    fn default() -> Self {
        Self {
            max_spread_bps: 2.0,
            max_vpin: 0.75,
            min_score: 0.70,
            default_ttl_ms: 5 * 60 * 1000,
        }
    }
}
```

### Pseudocódigo Rust

```rust
pub fn toxic_flow_gate(ctx: &StrategyMarketContext, cfg: &StrategyConfig) -> Result<(), String> {
    if matches!(ctx.regime, Regime::Stress | Regime::Aftermath) {
        return Err("BLOCKED_REGIME".to_string());
    }

    if ctx.flow.quality != DataQuality::Live {
        return Err("FLOW_NOT_LIVE".to_string());
    }

    if ctx.volume_profile.quality != DataQuality::Live {
        return Err("VOLUME_PROFILE_NOT_LIVE".to_string());
    }

    if ctx.orderbook.quality != DataQuality::Live {
        return Err("ORDERBOOK_NOT_LIVE".to_string());
    }

    if let Some(spread) = ctx.orderbook.spread_bps {
        if spread > cfg.max_spread_bps {
            return Err("SPREAD_TOO_WIDE".to_string());
        }
    }

    if let Some(vpin) = ctx.flow.vpin {
        if vpin > cfg.max_vpin {
            return Err("VPIN_TOXIC".to_string());
        }
    }

    Ok(())
}
```

---

## 6. Estrategia 1: VALUE_AREA_FAILED_AUCTION

### Hipótesis

El precio intenta salir del área de valor, pero falla.  
Ese fallo atrapa traders de breakout/breakdown.  
El target natural es POC o el extremo opuesto del área de valor.

```text
Precio sale de value
→ no acepta fuera
→ order flow muestra agresión fallida
→ precio vuelve dentro de value
→ target = POC
```

### SHORT setup

```text
LOCATION:
- price rompe VAH previamente
- price vuelve debajo de VAH
- price pierde/rechaza VWAP o AVWAP

ORDER FLOW:
- buy delta alto durante ruptura
- precio no avanza proporcionalmente
- footprint_absorption == Ask
- CVD no confirma la ruptura
- failed_acceptance == true

ORDER BOOK:
- no thin_zone_above útil
- spread aceptable
- microprice deja de acompañar al alza

ENTRY:
- short al volver bajo VAH o perder VWAP/AVWAP

STOP:
- encima del high del failed auction
- o VAH + 0.25 ATR

TARGET:
- target 1 = POC
- target 2 = VAL si el flujo sigue vendedor
```

### LONG setup

```text
LOCATION:
- price rompe VAL previamente
- price vuelve encima de VAL
- price recupera VWAP o AVWAP

ORDER FLOW:
- sell delta alto durante ruptura
- precio no cae proporcionalmente
- footprint_absorption == Bid
- CVD no confirma el breakdown
- failed_acceptance == true

ORDER BOOK:
- no thin_zone_below útil
- spread aceptable
- microprice deja de acompañar a la baja

ENTRY:
- long al volver sobre VAL o recuperar VWAP/AVWAP

STOP:
- debajo del low del failed auction
- o VAL - 0.25 ATR

TARGET:
- target 1 = POC
- target 2 = VAH si el flujo sigue comprador
```

### Pseudocódigo Rust

```rust
pub fn detect_value_area_failed_auction(
    ctx: &StrategyMarketContext,
    cfg: &StrategyConfig,
) -> Option<StrategySignal> {
    toxic_flow_gate(ctx, cfg).ok()?;

    let px = ctx.price;
    let atr = ctx.atr.unwrap_or(0.0);
    let vp = &ctx.volume_profile;
    let flow = &ctx.flow;
    let ob = &ctx.orderbook;

    let vah = vp.vah?;
    let val = vp.val?;
    let poc = vp.poc?;

    // SHORT failed auction above VAH
    let short_location =
        px < vah &&
        flow.failed_acceptance &&
        flow.delta.unwrap_or(0.0) > 0.0 &&
        flow.footprint_absorption == AbsorptionSide::Ask;

    let short_flow =
        flow.cvd_slope.unwrap_or(0.0) <= 0.0 &&
        flow.taker_imbalance.unwrap_or(0.0) < 0.25;

    let short_book =
        ob.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps &&
        !ob.thin_zone_above;

    if short_location && short_flow && short_book {
        let entry = px;
        let stop = f64::max(vah + 0.25 * atr, px + 0.5 * atr);
        let target = poc;

        if target < entry && stop > entry {
            return Some(StrategySignal {
                action: StrategyAction::ShadowSignal,
                strategy_id: Some(StrategyId::ValueAreaFailedAuction),
                side: Some(Side::Short),
                entry_price: Some(entry),
                stop_price: Some(stop),
                target_price: Some(target),
                score: 0.0,
                ttl_ms: cfg.default_ttl_ms,
                evidence: vec![
                    "failed_acceptance_above_VAH".into(),
                    "ask_absorption".into(),
                    "cvd_not_confirming_breakout".into(),
                    "target_POC".into(),
                ],
                missing: vec![],
                invalidation: vec![
                    "price_reclaims_above_failed_auction_high".into(),
                    "vpin_becomes_toxic".into(),
                ],
                created_at_ms: ctx.timestamp_ms,
            });
        }
    }

    // LONG failed auction below VAL
    let long_location =
        px > val &&
        flow.failed_acceptance &&
        flow.delta.unwrap_or(0.0) < 0.0 &&
        flow.footprint_absorption == AbsorptionSide::Bid;

    let long_flow =
        flow.cvd_slope.unwrap_or(0.0) >= 0.0 &&
        flow.taker_imbalance.unwrap_or(0.0) > -0.25;

    let long_book =
        ob.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps &&
        !ob.thin_zone_below;

    if long_location && long_flow && long_book {
        let entry = px;
        let stop = f64::min(val - 0.25 * atr, px - 0.5 * atr);
        let target = poc;

        if target > entry && stop < entry {
            return Some(StrategySignal {
                action: StrategyAction::ShadowSignal,
                strategy_id: Some(StrategyId::ValueAreaFailedAuction),
                side: Some(Side::Long),
                entry_price: Some(entry),
                stop_price: Some(stop),
                target_price: Some(target),
                score: 0.0,
                ttl_ms: cfg.default_ttl_ms,
                evidence: vec![
                    "failed_acceptance_below_VAL".into(),
                    "bid_absorption".into(),
                    "cvd_not_confirming_breakdown".into(),
                    "target_POC".into(),
                ],
                missing: vec![],
                invalidation: vec![
                    "price_loses_below_failed_auction_low".into(),
                    "vpin_becomes_toxic".into(),
                ],
                created_at_ms: ctx.timestamp_ms,
            });
        }
    }

    None
}
```

---

## 7. Estrategia 2: VWAP_VALUE_PULLBACK_CONTINUATION

### Hipótesis

En tendencia, esperar pullback hacia VWAP/AVWAP + zona de valor.  
La entrada se habilita solo cuando el order flow confirma reentrada a favor.

```text
Tendencia limpia
→ pullback hacia VWAP/AVWAP o value
→ flow se alinea de nuevo
→ continuación
```

### LONG setup

```text
CONTEXT:
- regime == TrendUp o Expansion controlada
- price sobre AVWAP_BOS o recupera VWAP
- price cerca de VAL/POC o dentro de value
- pullback no rompe estructura

ORDER FLOW:
- CVD slope >= 0
- delta vuelve positivo
- no failed_acceptance bajista
- absorption Bid aceptable
- mss_active == true o flow aligned

ORDER BOOK:
- microprice >= price
- spread aceptable
- no wall fuerte inmediata arriba

ENTRY:
- long al reclaim de VWAP/AVWAP o reacción desde VAL/POC

STOP:
- debajo de AVWAP_BOS / VAL / swing local

TARGET:
- VAH
- BSL
- wall superior si aparece antes
```

### SHORT setup

```text
CONTEXT:
- regime == TrendDown
- price debajo de AVWAP_BOS o pierde VWAP
- price cerca de VAH/POC o dentro de value
- pullback no rompe estructura bajista

ORDER FLOW:
- CVD slope <= 0
- delta vuelve negativo
- no failed_acceptance alcista
- absorption Ask aceptable
- mss_active == true o flow aligned

ORDER BOOK:
- microprice <= price
- spread aceptable
- no wall fuerte inmediata abajo

ENTRY:
- short al perder VWAP/AVWAP o rechazo desde VAH/POC

STOP:
- encima de AVWAP_BOS / VAH / swing local

TARGET:
- VAL
- SSL
- wall inferior si aparece antes
```

### Pseudocódigo Rust

```rust
pub fn detect_vwap_value_pullback_continuation(
    ctx: &StrategyMarketContext,
    cfg: &StrategyConfig,
) -> Option<StrategySignal> {
    toxic_flow_gate(ctx, cfg).ok()?;

    let px = ctx.price;
    let atr = ctx.atr.unwrap_or(0.0);
    let vp = &ctx.volume_profile;
    let vw = &ctx.vwap;
    let flow = &ctx.flow;
    let ob = &ctx.orderbook;

    let vah = vp.vah?;
    let val = vp.val?;

    let long_context =
        matches!(ctx.regime, Regime::TrendUp | Regime::Expansion) &&
        matches!(vw.price_vs_avwap_bos, PriceRelation::Above | PriceRelation::At) &&
        matches!(vp.value_location, ValueLocation::InValue | ValueLocation::BelowVal);

    let long_flow =
        flow.cvd_slope.unwrap_or(0.0) >= 0.0 &&
        flow.delta.unwrap_or(0.0) > 0.0 &&
        flow.failed_acceptance == false;

    let long_book =
        ob.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps &&
        ob.microprice.map(|m| m >= px * 0.9998).unwrap_or(true);

    if long_context && long_flow && long_book {
        let entry = px;
        let stop = f64::min(val, entry - 0.75 * atr);
        let target = vah;

        if target > entry && stop < entry {
            return Some(StrategySignal {
                action: StrategyAction::ShadowSignal,
                strategy_id: Some(StrategyId::VwapValuePullbackContinuation),
                side: Some(Side::Long),
                entry_price: Some(entry),
                stop_price: Some(stop),
                target_price: Some(target),
                score: 0.0,
                ttl_ms: cfg.default_ttl_ms,
                evidence: vec![
                    "trend_up".into(),
                    "above_or_at_avwap_bos".into(),
                    "pullback_into_value".into(),
                    "positive_delta_reentry".into(),
                    "cvd_aligned".into(),
                ],
                missing: vec![],
                invalidation: vec![
                    "price_loses_VAL".into(),
                    "price_loses_AVWAP_BOS".into(),
                    "flow_turns_negative".into(),
                ],
                created_at_ms: ctx.timestamp_ms,
            });
        }
    }

    let short_context =
        matches!(ctx.regime, Regime::TrendDown) &&
        matches!(vw.price_vs_avwap_bos, PriceRelation::Below | PriceRelation::At) &&
        matches!(vp.value_location, ValueLocation::InValue | ValueLocation::AboveVah);

    let short_flow =
        flow.cvd_slope.unwrap_or(0.0) <= 0.0 &&
        flow.delta.unwrap_or(0.0) < 0.0 &&
        flow.failed_acceptance == false;

    let short_book =
        ob.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps &&
        ob.microprice.map(|m| m <= px * 1.0002).unwrap_or(true);

    if short_context && short_flow && short_book {
        let entry = px;
        let stop = f64::max(vah, entry + 0.75 * atr);
        let target = val;

        if target < entry && stop > entry {
            return Some(StrategySignal {
                action: StrategyAction::ShadowSignal,
                strategy_id: Some(StrategyId::VwapValuePullbackContinuation),
                side: Some(Side::Short),
                entry_price: Some(entry),
                stop_price: Some(stop),
                target_price: Some(target),
                score: 0.0,
                ttl_ms: cfg.default_ttl_ms,
                evidence: vec![
                    "trend_down".into(),
                    "below_or_at_avwap_bos".into(),
                    "pullback_into_value".into(),
                    "negative_delta_reentry".into(),
                    "cvd_aligned".into(),
                ],
                missing: vec![],
                invalidation: vec![
                    "price_reclaims_VAH".into(),
                    "price_reclaims_AVWAP_BOS".into(),
                    "flow_turns_positive".into(),
                ],
                created_at_ms: ctx.timestamp_ms,
            });
        }
    }

    None
}
```

---

## 8. Estrategia 3: LVN_LIQUIDITY_VACUUM_BREAKOUT

### Hipótesis

Una zona de bajo volumen o un tramo delgado del order book permite desplazamiento rápido si entra flujo agresivo real.

```text
Balance / compresión
→ ruptura por LVN o zona delgada
→ book muestra path limpio
→ order flow confirma
→ target = siguiente HVN / VAH / VAL / wall relevante
```

### LONG setup

```text
LOCATION:
- price cerca de LVN o borde de value
- price recupera VWAP o ya está sobre VWAP
- thin_zone_above == true

ORDER FLOW:
- delta > 0
- CVD slope > 0
- stacked_imbalance == Bullish
- taker_imbalance positivo pero no tóxico

ORDER BOOK:
- microprice >= price
- spread aceptable
- no wall fuerte antes del target

ENTRY:
- long tras aceptación encima del LVN / thin zone

STOP:
- debajo de LVN o VWAP

TARGET:
- siguiente HVN
- VAH
- wall superior
```

### SHORT setup

```text
LOCATION:
- price cerca de LVN o borde de value
- price pierde VWAP o ya está debajo de VWAP
- thin_zone_below == true

ORDER FLOW:
- delta < 0
- CVD slope < 0
- stacked_imbalance == Bearish
- taker_imbalance negativo pero no tóxico

ORDER BOOK:
- microprice <= price
- spread aceptable
- no wall fuerte antes del target

ENTRY:
- short tras aceptación debajo del LVN / thin zone

STOP:
- encima de LVN o VWAP

TARGET:
- siguiente HVN
- VAL
- wall inferior
```

### Helpers

```rust
fn nearest_above(levels: &[f64], price: f64) -> Option<f64> {
    levels
        .iter()
        .copied()
        .filter(|x| *x > price)
        .min_by(|a, b| a.partial_cmp(b).unwrap())
}

fn nearest_below(levels: &[f64], price: f64) -> Option<f64> {
    levels
        .iter()
        .copied()
        .filter(|x| *x < price)
        .max_by(|a, b| a.partial_cmp(b).unwrap())
}
```

### Pseudocódigo Rust

```rust
pub fn detect_lvn_liquidity_vacuum_breakout(
    ctx: &StrategyMarketContext,
    cfg: &StrategyConfig,
) -> Option<StrategySignal> {
    toxic_flow_gate(ctx, cfg).ok()?;

    let px = ctx.price;
    let atr = ctx.atr.unwrap_or(0.0);
    let vp = &ctx.volume_profile;
    let vw = &ctx.vwap;
    let flow = &ctx.flow;
    let ob = &ctx.orderbook;

    let long_location =
        ob.thin_zone_above &&
        matches!(vw.price_vs_vwap, PriceRelation::Above | PriceRelation::At) &&
        matches!(vp.value_location, ValueLocation::InValue | ValueLocation::AboveVah);

    let long_flow =
        flow.delta.unwrap_or(0.0) > 0.0 &&
        flow.cvd_slope.unwrap_or(0.0) > 0.0 &&
        matches!(flow.stacked_imbalance, ImbalanceSide::Bullish | ImbalanceSide::None | ImbalanceSide::Unknown) &&
        flow.taker_imbalance.unwrap_or(0.0).abs() < 0.90;

    let long_book =
        ob.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps &&
        ob.microprice.map(|m| m >= px).unwrap_or(true);

    if long_location && long_flow && long_book {
        let mut targets = vp.hvn_nearby.clone();
        if let Some(vah) = vp.vah {
            targets.push(vah);
        }

        if let Some(target) = nearest_above(&targets, px) {
            let entry = px;
            let stop_anchor = vw.vwap_session.unwrap_or(px);
            let stop = f64::min(stop_anchor, entry - 0.75 * atr);

            if target > entry && stop < entry {
                return Some(StrategySignal {
                    action: StrategyAction::ShadowSignal,
                    strategy_id: Some(StrategyId::LvnLiquidityVacuumBreakout),
                    side: Some(Side::Long),
                    entry_price: Some(entry),
                    stop_price: Some(stop),
                    target_price: Some(target),
                    score: 0.0,
                    ttl_ms: cfg.default_ttl_ms,
                    evidence: vec![
                        "thin_zone_above".into(),
                        "vwap_reclaim_or_above".into(),
                        "positive_delta".into(),
                        "cvd_positive".into(),
                        "target_next_HVN_or_VAH".into(),
                    ],
                    missing: vec![],
                    invalidation: vec![
                        "price_loses_VWAP".into(),
                        "cvd_turns_negative".into(),
                        "spread_expands".into(),
                    ],
                    created_at_ms: ctx.timestamp_ms,
                });
            }
        }
    }

    let short_location =
        ob.thin_zone_below &&
        matches!(vw.price_vs_vwap, PriceRelation::Below | PriceRelation::At) &&
        matches!(vp.value_location, ValueLocation::InValue | ValueLocation::BelowVal);

    let short_flow =
        flow.delta.unwrap_or(0.0) < 0.0 &&
        flow.cvd_slope.unwrap_or(0.0) < 0.0 &&
        matches!(flow.stacked_imbalance, ImbalanceSide::Bearish | ImbalanceSide::None | ImbalanceSide::Unknown) &&
        flow.taker_imbalance.unwrap_or(0.0).abs() < 0.90;

    let short_book =
        ob.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps &&
        ob.microprice.map(|m| m <= px).unwrap_or(true);

    if short_location && short_flow && short_book {
        let mut targets = vp.hvn_nearby.clone();
        if let Some(val) = vp.val {
            targets.push(val);
        }

        if let Some(target) = nearest_below(&targets, px) {
            let entry = px;
            let stop_anchor = vw.vwap_session.unwrap_or(px);
            let stop = f64::max(stop_anchor, entry + 0.75 * atr);

            if target < entry && stop > entry {
                return Some(StrategySignal {
                    action: StrategyAction::ShadowSignal,
                    strategy_id: Some(StrategyId::LvnLiquidityVacuumBreakout),
                    side: Some(Side::Short),
                    entry_price: Some(entry),
                    stop_price: Some(stop),
                    target_price: Some(target),
                    score: 0.0,
                    ttl_ms: cfg.default_ttl_ms,
                    evidence: vec![
                        "thin_zone_below".into(),
                        "vwap_loss_or_below".into(),
                        "negative_delta".into(),
                        "cvd_negative".into(),
                        "target_next_HVN_or_VAL".into(),
                    ],
                    missing: vec![],
                    invalidation: vec![
                        "price_reclaims_VWAP".into(),
                        "cvd_turns_positive".into(),
                        "spread_expands".into(),
                    ],
                    created_at_ms: ctx.timestamp_ms,
                });
            }
        }
    }

    None
}
```

---

## 9. Scoring

Implementar `score_signal`.

```rust
pub fn score_signal(ctx: &StrategyMarketContext, mut signal: StrategySignal) -> StrategySignal {
    let mut score = 0.0;

    let has = |s: &str| signal.evidence.iter().any(|e| e == s);

    // Location / target quality
    if has("target_POC") {
        score += 0.20;
    }
    if has("target_next_HVN_or_VAH") || has("target_next_HVN_or_VAL") {
        score += 0.20;
    }

    // VWAP / AVWAP confirmation
    if signal.evidence.iter().any(|e| e.contains("vwap") || e.contains("avwap")) {
        score += 0.15;
    }

    // Order flow
    if signal.evidence.iter().any(|e| e.contains("cvd")) {
        score += 0.20;
    }
    if has("ask_absorption") || has("bid_absorption") {
        score += 0.20;
    }
    if has("positive_delta") || has("negative_delta") {
        score += 0.15;
    }

    // Orderbook / path
    if has("thin_zone_above") || has("thin_zone_below") {
        score += 0.15;
    }

    // Penalizaciones
    if let Some(spread) = ctx.orderbook.spread_bps {
        if spread > 1.5 {
            score -= 0.15;
        }
    }

    if let Some(vpin) = ctx.flow.vpin {
        if vpin > 0.65 {
            score -= 0.20;
        }
    }

    signal.score = score.clamp(0.0, 1.0);
    signal
}
```

---

## 10. Router de estrategias

```rust
pub fn route_strategy(
    ctx: &StrategyMarketContext,
    cfg: &StrategyConfig,
) -> StrategySignal {
    if let Err(reason) = toxic_flow_gate(ctx, cfg) {
        return StrategySignal {
            action: StrategyAction::Blocked,
            strategy_id: None,
            side: None,
            entry_price: None,
            stop_price: None,
            target_price: None,
            score: 0.0,
            ttl_ms: 0,
            evidence: vec![],
            missing: vec![reason],
            invalidation: vec![],
            created_at_ms: ctx.timestamp_ms,
        };
    }

    let mut candidates = Vec::new();

    if let Some(s) = detect_value_area_failed_auction(ctx, cfg) {
        candidates.push(score_signal(ctx, s));
    }

    if let Some(s) = detect_lvn_liquidity_vacuum_breakout(ctx, cfg) {
        candidates.push(score_signal(ctx, s));
    }

    if let Some(s) = detect_vwap_value_pullback_continuation(ctx, cfg) {
        candidates.push(score_signal(ctx, s));
    }

    let best = candidates
        .into_iter()
        .max_by(|a, b| a.score.partial_cmp(&b.score).unwrap());

    match best {
        Some(signal) if signal.score >= cfg.min_score => signal,
        Some(signal) => StrategySignal {
            action: StrategyAction::Wait,
            strategy_id: signal.strategy_id,
            side: signal.side,
            entry_price: signal.entry_price,
            stop_price: signal.stop_price,
            target_price: signal.target_price,
            score: signal.score,
            ttl_ms: signal.ttl_ms,
            evidence: signal.evidence,
            missing: vec!["LOW_SCORE".into()],
            invalidation: signal.invalidation,
            created_at_ms: ctx.timestamp_ms,
        },
        None => StrategySignal {
            action: StrategyAction::Wait,
            strategy_id: None,
            side: None,
            entry_price: None,
            stop_price: None,
            target_price: None,
            score: 0.0,
            ttl_ms: 0,
            evidence: vec![],
            missing: vec!["NO_VALID_SETUP".into()],
            invalidation: vec![],
            created_at_ms: ctx.timestamp_ms,
        },
    }
}
```

---

## 11. Logging de eventos shadow

Crear archivo:

```text
data/shadow_events/strategy_signals.jsonl
```

Cada línea debe guardar:

```json
{
  "symbol": "BTCUSDT",
  "timestamp_ms": 1710000000000,
  "strategy": "VALUE_AREA_FAILED_AUCTION",
  "side": "SHORT",
  "entry_price": 100000.0,
  "stop_price": 100250.0,
  "target_price": 99500.0,
  "score": 0.82,
  "ttl_ms": 300000,
  "evidence": ["failed_acceptance_above_VAH", "ask_absorption"],
  "missing": [],
  "invalidation": ["price_reclaims_above_failed_auction_high"],
  "context": {
    "price": 100000.0,
    "vwap_session": 99950.0,
    "poc": 99500.0,
    "vah": 100100.0,
    "val": 99000.0,
    "cvd_slope": -0.2,
    "delta": 120.0,
    "vpin": 0.48,
    "spread_bps": 0.8,
    "obi_l5": -0.12
  }
}
```

---

## 12. Outcome tracker

Crear un tracker que evalúe cada señal shadow después de varios horizontes:

```text
5s, 15s, 30s, 60s, 3m, 5m, 15m
```

Campos mínimos:

```text
- mfe_bps
- mae_bps
- hit_target
- hit_stop
- time_to_target_ms
- time_to_stop_ms
- net_r_after_estimated_costs
- spread_at_entry
- spread_max_during_trade
```

No hay que declarar una estrategia “usable” hasta tener:

```text
- 300+ señales por estrategia
- expectancy neta positiva
- robustez por sesión
- robustez por régimen
- thresholds robustos ±20%
- resultado no dependiente de 2 o 3 outliers
```

Sí, esto es más aburrido que mirar una vela verde. Por eso funciona mejor.

---

## 13. Integración en UI: Indicadores + Overlay en Candlestick Chart

### Principio de diseño

No es un panel separado. Los cálculos y señales se integran **dentro del sistema de indicadores existente** del kline chart, al mismo nivel que Volume y Open Interest. El usuario los activa/desactiva desde el mismo dropdown de Indicators.

### 13.1 Nuevos indicadores seleccionables

Agregar al menú de Indicators (junto a Volume y Open Interest):

```text
Indicators
├── Volume                  ✓ (existente)
├── Open Interest           ✓ (existente)
├── Cumulative Delta        ✓ (existente)
├── VWAP                    ← NUEVO
├── Volume Profile (VAH/VAL/POC) ← NUEVO
├── ATR                     ← NUEVO
└── Strategy Signals        ← NUEVO (overlay)
```

Cada indicador nuevo sigue el mismo trait `Indicator` que ya usan Volume/OI/CVD. Se renderizan como sub-panels debajo del chart o como líneas sobre las velas según corresponda:

```text
VWAP          → línea sobre velas (session VWAP + bandas AVWAP)
Volume Profile → niveles horizontales: POC (sólido), VAH/VAL (punteado)
ATR           → sub-panel debajo del chart (línea)
Strategy      → overlay sobre velas (entry/stop/target)
```

### 13.2 Overlay de señales en el candlestick chart

Cuando "Strategy Signals" está activo, se dibujan directamente sobre las velas:

```text
ENTRY hipotético  → línea horizontal + marker (triángulo ▲/▼)
STOP hipotético   → línea punteada roja
TARGET hipotético → línea punteada verde
Zona activa       → rectángulo semitransparente entre entry y target
```

Colores:

```text
LONG signal:  green entry marker, green target, red stop
SHORT signal: red entry marker, red target, green stop
EXPIRED/INVALID: gray, se desvanece tras TTL
```

### 13.3 Tooltip de evidencia

Al hover sobre un marker de señal:

```text
┌─────────────────────────────────────┐
│ VALUE_AREA_FAILED_AUCTION | SHORT   │
│ Score: 0.82                         │
│ Entry: 100,000 | Stop: 100,250      │
│ Target: 99,500                      │
│                                     │
│ Evidence:                           │
│  • failed_acceptance_above_VAH      │
│  • ask_absorption                   │
│  • cvd_not_confirming_breakout      │
│                                     │
│ Invalidation:                       │
│  • price_reclaims_above_high        │
└─────────────────────────────────────┘
```

### 13.4 Operaciones shadow visibles en chart

Las operaciones shadow (entradas hipotéticas que se están trackeando) se muestran como:

```text
ACTIVA:     rectángulo coloreado desde entry hasta precio actual
HIT TARGET: rectángulo verde completo (entry → target)
HIT STOP:   rectángulo rojo completo (entry → stop)
EXPIRED:    rectángulo gris desvanecido
```

No saturar el chart. Máximo 3-5 señales visibles simultáneamente. Las más antiguas se comprimen a markers mínimos.

---

## 14. Integración técnica

### Paso 1: Módulo strategy (tipos y lógica pura)

Crear módulos sin tocar la lógica existente:

```text
src/strategy/
  mod.rs
  types.rs
  context.rs
  router.rs
  scoring.rs
  logger.rs
  detectors/
    mod.rs
    toxic_flow_gate.rs
    value_area_failed_auction.rs
    vwap_value_pullback_continuation.rs
    lvn_liquidity_vacuum_breakout.rs
```

### Paso 2: Indicadores nuevos (VWAP, VP levels, ATR)

Integrar en el sistema de indicadores existente (`src/chart/indicator/`):

```text
src/chart/indicator/kline/vwap.rs          ← VWAP session + AVWAP
src/chart/indicator/kline/volume_profile.rs ← VAH/VAL/POC/HVN/LVN levels
src/chart/indicator/kline/atr.rs           ← ATR calculation
```

Cada uno implementa el trait `Indicator` existente y se registra en el enum de indicadores disponibles para kline charts.

### Paso 3: Adapter de contexto

Crear adapter que construye `StrategyMarketContext` a partir del estado actual de los indicadores y el chart:

```rust
pub fn build_strategy_context(
    kline_state: &KlineChartState,
    depth: &Depth,
    indicators: &ActiveIndicators,
) -> Option<StrategyMarketContext> {
    // Map VWAP indicator output → VwapContext
    // Map Volume Profile indicator output → VolumeProfileContext
    // Map CVD/delta from existing indicator → OrderFlowContext
    // Map depth BTreeMap → OrderBookContext
    todo!()
}
```

### Paso 4: Ejecutar detectores

Ejecutar `route_strategy()` en cada tick/update cuando:
- Los indicadores requeridos están activos y tienen data
- El usuario tiene "Strategy Signals" habilitado

### Paso 5: Render del overlay

Implementar el dibujado de señales como parte del canvas rendering del kline chart. Se dibuja en una capa encima de las velas, usando el mismo sistema de coordenadas.

### Paso 6: Logger JSONL

Guardar cada `StrategySignal` en `data/shadow_events/strategy_signals.jsonl` para análisis offline posterior.

### Paso 7: Tests unitarios por detector

---

## 15. Tests mínimos

### Toxic gate

```text
- blocks STRESS
- blocks AFTERMATH
- blocks wide spread
- blocks vpin > 0.75
- blocks missing flow
- allows valid context
```

### VALUE_AREA_FAILED_AUCTION

```text
- detects short failed auction above VAH
- detects long failed auction below VAL
- rejects if CVD confirms breakout
- rejects if thin zone exists in breakout direction
- rejects if target invalid
```

### VWAP_VALUE_PULLBACK_CONTINUATION

```text
- detects long continuation in trend up
- detects short continuation in trend down
- rejects if price is on wrong side of AVWAP
- rejects if flow contradicts
- rejects if target invalid
```

### LVN_LIQUIDITY_VACUUM_BREAKOUT

```text
- detects long thin-zone breakout
- detects short thin-zone breakout
- rejects if no thin zone
- rejects if CVD contradicts
- rejects if no target above/below
```

---

## 16. No hacer en esta fase

```text
- No live execution
- No API keys
- No order placement
- No leverage
- No auto trading
- No Python inside the realtime UI loop
- No ML
- No strategy optimization until there are enough shadow events
- No “profitability claim” without outcomes
```

---

## 17. Prompt para Claude Code

Copiar y pegar:

```text
Estamos en el repositorio local de flowsurface.

Necesito implementar una capa experimental de detección de estrategias microestructurales integrada al UI como indicadores y overlay en el candlestick chart. Modo SHADOW ONLY: no ejecutar órdenes, no conectarse a cuentas, no usar API keys privadas. Detectar señales, pintarlas como overlay sobre las velas y guardarlas como eventos JSONL.

Objetivo técnico:
1. Crear indicadores nuevos (VWAP, Volume Profile levels, ATR) que se integren al dropdown de Indicators existente junto a Volume y Open Interest.
2. Crear módulo Rust `src/strategy/` con detectores, router, scoring y logger.
3. Renderizar señales como overlay sobre el kline chart (entry/stop/target markers).
4. Mostrar operaciones shadow activas directamente en el gráfico de velas.

Estrategias iniciales:
1. VALUE_AREA_FAILED_AUCTION
2. VWAP_VALUE_PULLBACK_CONTINUATION
3. LVN_LIQUIDITY_VACUUM_BREAKOUT

Filtro obligatorio:
- TOXIC_FLOW_GATE

Requisitos:
1. Analiza la estructura actual del repo y el sistema de indicadores (trait Indicator, cómo Volume/OI/CVD se registran y renderizan).
2. Implementa VWAP, Volume Profile (VAH/VAL/POC/HVN/LVN), y ATR como indicadores nuevos en `src/chart/indicator/kline/`.
3. Registra los nuevos indicadores en el enum/menú existente para que aparezcan en el dropdown.
4. Crea los tipos base del módulo strategy:
   - StrategyMarketContext, VolumeProfileContext, VwapContext
   - OrderFlowContext, OrderBookContext
   - StrategySignal, StrategyConfig
5. Implementa `toxic_flow_gate`.
6. Implementa los 3 detectores en archivos separados.
7. Implementa `route_strategy` y `score_signal`.
8. Implementa el overlay renderer: entry/stop/target como líneas y markers sobre el canvas del kline chart.
9. Implementa logger JSONL en `data/shadow_events/strategy_signals.jsonl`.
10. Agrega tests unitarios mínimos por detector.
11. Campos no disponibles aún → `Option<T>` con adapter pendiente documentado.
12. No Python en el hot path. No ejecución real. No credenciales.
13. Todo detrás de config toggle: `strategy_signals_enabled`.

Entregable:
- Lista de archivos creados/modificados.
- Explicación de cómo los indicadores se integran al dropdown.
- Explicación de cómo el overlay se renderiza sobre las velas.
- Comandos para testear.
- Gaps pendientes del adapter.
```

---

## 18. Decisión final

Arquitectura recomendada:

```text
Rust (dentro de la app, integrado al UI):
- Indicadores como ciudadanos de primera clase (VWAP, VP, ATR)
- Detectores de estrategia en tiempo real
- Overlay de señales sobre el candlestick chart
- Operaciones shadow visibles en el gráfico
- Scoring
- JSONL logger
- Tests

Python (offline, fuera de la app):
- Análisis de shadow_events.jsonl
- Calibración de thresholds
- Outcome analytics
- Reportes y notebooks
```

La línea correcta es Rust-first, UI-integrated.  
Los indicadores y señales son parte del chart, no un módulo lateral invisible.  
Python entra después, como laboratorio. No como órgano vital de la app.

