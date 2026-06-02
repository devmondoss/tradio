# Sistema de Estrategias — FlowSurface

> **Documento historico.** Describe la arquitectura Core/Lab multi-detector vigente antes de DRR-only. Para el estado actual y decisiones futuras usar [DRR_PRESENTE_Y_FUTURO.md](../../DRR_PRESENTE_Y_FUTURO.md).

> Documento de referencia completo. Cubre arquitectura, módulos, condiciones, scoring, gates y estado de implementación.
> Última actualización: 2026-05-21 (rev 5 — bugs de revisión estrategia corregidos, indicadores UI verificados como completos)

---

## Tabla de Contenidos

1. [Visión General — Arquitectura de dos carriles](#1-visión-general--arquitectura-de-dos-carriles)
2. [CORE — 10 Estrategias](#2-core--10-estrategias)
3. [LAB — 5 Hipótesis en Observación](#3-lab--5-hipótesis-en-observación)
4. [MICRO — Módulos de Soporte](#4-micro--módulos-de-soporte)
5. [El Router — Pipeline del Core](#5-el-router--pipeline-del-core)
6. [Scoring y Weighting](#6-scoring-y-weighting)
7. [Sistema de Gates y Control de Calidad](#7-sistema-de-gates-y-control-de-calidad)
8. [Sesión y Tiempo](#8-sesión-y-tiempo)
9. [Datos Institucionales](#9-datos-institucionales)
10. [Order Book — Snapshot del Libro](#10-order-book--snapshot-del-libro)
11. [Flujo de Órdenes — Order Flow](#11-flujo-de-órdenes--order-flow)
12. [Patrones ICT / Smart Money — Estructura de Precio](#12-patrones-ict--smart-money--estructura-de-precio)
13. [Configuración — StrategyConfig y LabConfig](#13-configuración--strategyconfig-y-labconfig)
14. [Ciclo de Madurez y Promoción Lab → Core](#14-ciclo-de-madurez-y-promoción-lab--core)
15. [Estado de Implementación](#15-estado-de-implementación)

---

## 1. Visión General — Arquitectura de dos carriles

El sistema opera en **dos carriles paralelos e independientes** sobre el mismo `StrategyMarketContext`. Comparten los datos pero nunca se tocan entre sí.

```
StrategyMarketContext (cada barra M5 cerrada)
          │
     ┌────┴────┐
     │         │
  CORE       LAB
  decide     observa
     │         │
  router     run_strategy_lab()
  winner-    todos evalúan
  takes-all  en paralelo
     │         │
shadow_signals  lab_signals
signal_outcomes lab_outcomes
(Supabase)      (Supabase)
```

### Reglas de separación — no negociables


| Acción                               | Core | Lab     |
| ------------------------------------ | ---- | ------- |
| Emitir ShadowSignal al router        | ✓    | ✗       |
| Escribir en paper_trades             | ✓    | ✗       |
| Bloquear otras estrategias           | ✓    | ✗       |
| Evaluar en paralelo sin bloqueo      | ✗    | ✓       |
| Modificar score del Core             | ✗    | ✗ nunca |
| Escribir en lab_signals/lab_outcomes | ✗    | ✓       |


### Las 4 Dimensiones de entrada


| #   | Dimensión         | Qué mide                                                          | Fuente                                                        |
| --- | ----------------- | ----------------------------------------------------------------- | ------------------------------------------------------------- |
| 1   | **Estructura**    | Dónde estamos en el mercado grande (BOS/CHoCH, swing pivots)      | `MarketStructureTracker`, `OrderBlockDetector`, `FvgDetector` |
| 2   | **Flujo**         | Qué pasa en tiempo real (CVD, taker imbalance, delta, footprint)  | `OrderFlowContext`, trades WS, `KlineTrades`                  |
| 3   | **Libro**         | Snapshot del libro (OBI, paredes, spread)                         | `OrderBookContext`, L2 data                                   |
| 4   | **Institucional** | Lo que hacen los grandes (OI, funding, L/S ratios, liquidaciones) | REST Binance, trackers institucionales                        |


---

## 2. CORE — 10 Estrategias

El Core evalúa 10 detectores en cada barra M5. El router toma el de mayor score si supera `min_score = 0.60`. Solo uno emite `ShadowSignal` por barra (winner-takes-all).

Los 7 primeros corren siempre. Los 3 institucionales solo cuando `ctx.institutional` tiene datos.

---

### 2.1 ValueAreaFailedAuction (VAFA)

**Concepto**: Precio intenta escapar del área de valor (VAH o VAL), falla y regresa — reversión al POC o extremo opuesto.

**Condiciones Long** (intento fallido bajo VAL):

- Precio < VAL y > VAL − 1.5×ATR
- `failed_acceptance = true`
- CVD slope > 0
- Taker imbalance > 0
- OBI l5 > 0

**Condiciones Short** (intento fallido sobre VAH):

- Precio > VAH y < VAH + 1.5×ATR
- `failed_acceptance = true`
- CVD slope < 0 / taker_imbalance < 0 / OBI l5 < 0

**Gates extra en Chop**: `cvd_slope > 0.20 && taker_imbalance > 0.15 && delta/atr > 0.35`

**Target**: POC si está al otro lado, sino swing estructural.
**Archivo**: `data/src/strategy/detectors/value_area_failed_auction.rs`

---

### 2.2 VwapValuePullbackContinuation (VVPC)

**Concepto**: En tendencia, precio hace pullback a VWAP o área de valor y retoma dirección.

**Condiciones Long**:

- Regime: `TrendUp`
- Precio entre VAL y VWAP
- CVD slope > 0.10
- `taker_imbalance > 0` ← gate nuevo
- `obi_l5 >= 0` ← gate nuevo
- `fast_slope > -0.08`
- AVWAP BOS: precio > avwap si disponible (no bloquea si None)

**Condiciones Short** (espejo):

- Regime: `TrendDown`
- `taker_imbalance < 0` + `obi_l5 <= 0` + `fast_slope < 0.08`

**Target**: `find_structural_target()` — HVNs, VAH/VAL, walls, fallback 3×ATR.
**Stop**: `max(val, entry − 1×atr)` para long. `min(vah, entry + 1×atr)` para short.
**Archivo**: `data/src/strategy/detectors/vwap_value_pullback_continuation.rs`

---

### 2.3 LvnLiquidityVacuumBreakout (LVN)

**Concepto**: Precio rompe un Low Volume Node — vacío de liquidez que actúa como catapulta hacia el siguiente HVN.

**Condiciones**:

- `lvn_nearby` no vacío (LVN dentro de 1×ATR)
- `thin_zone_above/below = true`
- CVD slope y stacked_imbalance confirman dirección
- Siguiente HVN como target

**Archivo**: `data/src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs`

---

### 2.4 DomImbalanceBreakout (DIB)

**Concepto**: Desequilibrio de libro de órdenes persistente en 2 barras consecutivas — continuación del desequilibrio.

**Condiciones Long**:

- `obi_l5 > 0.35` en barra actual
- `prev_obi_l5 > 0.10` en barra anterior ← persistencia (gate añadido)
- `obi_l10 > 0.20`
- `microprice > midprice`
- `thin_zone_above = true`

**Condiciones Short** (espejo):

- `obi_l5 < -0.35` + `prev_obi_l5 < -0.10`

**Archivo**: `data/src/strategy/detectors/dom_imbalance_breakout.rs`

---

### 2.5 SessionOpenBreakout (SOB)

**Concepto**: Breakout al inicio de sesión London/NY con volumen y flujo confirmatorio.

**Condiciones**:

- Sesión: London u NY
- Precio rompe rango de Asia/pre-sesión
- Volumen de ruptura > promedio
- CVD slope y taker_imbalance confirman dirección

**Archivo**: `data/src/strategy/detectors/session_open_breakout.rs`

---

### 2.6 OrderBlockRetest (OBR)

**Concepto**: Precio retestea un order block conocido con absorción confirmada por footprint.

**Condiciones Long**:

- `obs.nearest_bullish` activo (Active o Tested)
- `ob.price_inside(px)` — precio dentro del OB
- `footprint_absorption == AbsorptionSide::Bid`
- CVD slope ≥ 0 + OBI l5 > 0
- Regime no TrendDown/Stress/Aftermath

**Stop**: `ob.low − 0.5×ATR`
**Target**: `swing_high_20`

**Archivo**: `data/src/strategy/detectors/order_block_retest.rs`

---

### 2.7 FootprintAbsorptionReversal (FAR)

**Concepto**: 3+ niveles de precio consecutivos con delta negativo en zona VAL (o positivo en VAH), seguido de reversal — absorción real nivel a nivel.

**Condiciones Long** (absorción en VAL):

- `footprint_levels` no vacío
- Precio en zona `[val − 0.5×atr, val + 0.3×atr]`
- `has_delta_run(levels, want_positive=false, min_run=3)` — 3 niveles con delta < 0
- `obi_l5 > 0` + `cvd_slope > -0.05` + `taker_imbalance > -0.10`
- Regime no TrendDown/Stress

**Stop**: `val − atr`. **Target**: POC o VAH.

**Archivo**: `data/src/strategy/detectors/footprint_absorption_reversal.rs`

---

### 2.8 LiquidationHunt (LiqHunt) ⚠️ requiere datos institucionales

**Concepto**: Movimiento que barre stops, liquida posiciones y revierte. Entrada post-confirmación.

**Condiciones**:

- `sweep_confirmed = true`
- Liquidaciones ≥ 500k USD en dirección del sweep (60s window)
- No cascade (< 5M USD en 60s — si es cascade, ya pasó)
- `mss_active = true` (BOS post-sweep)
- LiqMap confirma que el pool fue cazado

**Bonus**: `SessionPhase::OpeningRush` → ×1.15 score.
**TTL**: 10 minutos.
**Archivo**: `data/src/strategy/detectors/liquidation_hunt.rs`

---

### 2.9 FundingExhaustionReversal (FER) ⚠️ requiere datos institucionales

**Concepto**: Funding rate en extremo absoluto + posicionamiento apoya tesis → reversal.

**Condiciones Long** (shorts agotados):

- `funding.regime == Extreme` con tasa < −0.06%
- `peak_confirmed = true`
- Top traders long% ≥ 46%

**Condiciones Short** (longs agotados):

- Funding extremo positivo + top traders long% ≤ 54%

**TTL**: 30 minutos.
**Archivo**: `data/src/strategy/detectors/funding_exhaustion_reversal.rs`

---

### 2.10 SmartMoneyDivergence (SMD) ⚠️ requiere datos institucionales

**Concepto**: Top traders y retail en direcciones opuestas — seguir al smart money.

**Condiciones Short** (smart short, retail long):

- Top traders long% < 45%
- Retail long% > 60%
- Divergencia > 18%
- CVD slope < 0

**TTL**: 20 minutos.
**Archivo**: `data/src/strategy/detectors/smart_money_divergence.rs`

---

### Resumen Core


| Estrategia | Perfil        | Régimen óptimo   | TTL     | Datos requeridos        |
| ---------- | ------------- | ---------------- | ------- | ----------------------- |
| VAFA       | MarketPure    | Chop/Range       | 250 min | Base                    |
| VVPC       | MarketPure    | TrendUp/Down     | 250 min | Base                    |
| LVN        | MarketPure    | Expansion        | 250 min | Base                    |
| DIB        | MarketPure    | Cualquiera       | 250 min | Base + prev_obi         |
| SOB        | MarketPure    | Sesión activa    | 250 min | Base + sesión           |
| OBR        | MarketPure    | TrendUp/Down     | 250 min | Base + order_blocks     |
| FAR        | MarketPure    | Chop/Range       | 250 min | Base + footprint_levels |
| LiqHunt    | Institutional | Cualquiera       | 10 min  | Institucional completo  |
| FER        | Institutional | Stress/Aftermath | 30 min  | Institucional completo  |
| SMD        | Institutional | Cualquiera       | 20 min  | Institucional completo  |


---

## 3. LAB — 5 Hipótesis en Observación

El Lab corre en paralelo sin afectar al Core. Cada barra M5 evalúa los 5 detectores independientemente. Los resultados se escriben en `lab_signals` y `lab_outcomes` (Supabase). **Nunca** toca `shadow_signals`, `paper_trades`, ni el router del Core.

### Ciclo de vida de una señal Lab

```
Asleep      — faltan datos mínimos (missing_data documenta el motivo)
Observed    — fenómeno parcial detectado, no alcanza para señal
ShadowSignal — setup completo, se escribe con entry/stop/target/rr
Blocked     — gate bloqueó (spread, datos, etc.)
```

### Estados de madurez

```
ObserveOnly    — acumula datos, no se evalúa para promoción todavía
ShadowLab      — genera ShadowSignals, candidata a evaluación de promoción
PaperCandidate — 100+ señales con RR ≥ 1.3 → simula con costos en papel separado
PaperPromoted  — 300+ señales con edge demostrado → puede entrar router secundario
CoreActive     — compite en router principal
```

---

### 3.1 VwapRejection

**Maturity**: `ShadowLab` ← única candidata activa a promoción.
**Concepto**: Precio extendido lejos del VWAP, momentum se agota, revierte hacia el VWAP.

**Condiciones Long** (precio muy bajo, agotamiento de sellers):

- Precio < `vwap_session − 0.5×ATR`
- Regime: TrendUp, Chop o Compression
- `delta > 0` + `cvd_slope > 0` + `taker_imbalance > 0` + `obi_l5 > 0`

**Condiciones Short** (precio muy alto, agotamiento de buyers): espejo.

**Stop**: `max(val, entry − 1×ATR)` para long.
**Target**: VWAP session.
**Confianza**: 0.7

**Archivo**: `data/src/strategy/lab/detectors/vwap_rejection.rs`

---

### 3.2 AbsorptionTrapReversal

**Maturity**: `ObserveOnly`
**Concepto**: Absorción de bid en VAL (o ask en VAH) detectada por `footprint_absorption` — precio está siendo sostenido contra la presión vendedora.

**Condiciones Long**:

- `flow.footprint_absorption == AbsorptionSide::Bid`
- `delta > 0` + `cvd_slope > 0`
- Precio ≤ VAL + 0.5×ATR
- Regime: TrendUp o Chop

**Archivo**: `data/src/strategy/lab/detectors/absorption_trap_reversal.rs`

---

### 3.3 SessionImbalanceBreakout

**Maturity**: `ObserveOnly`
**Concepto**: Al inicio de sesión (London/NY), precio rompe el value area con flujo fuerte y zona thin — breakout estructural.

**Condiciones Bull Break**:

- Precio > VAH
- `thin_zone_above = true`
- `cvd_slope > 0` + `obi_l5 > 0.20` + `taker_imbalance > 0`
- Regime: TrendUp o Expansion

**Archivo**: `data/src/strategy/lab/detectors/session_imbalance_breakout.rs`

---

### 3.4 LiquidityMagnet

**Maturity**: `ObserveOnly`
**Concepto**: Precio siendo atraído hacia zona HVN con momentum y libro alineados — "imán de liquidez".

**Condiciones Upward** (HVN encima):

- `hvn_above` dentro de 2×ATR
- Precio > VWAP
- `fast_slope > 0.05` + `obi_l5 > 0.15` + `cvd_slope > 0`
- `thin_zone_above = true`

**Target**: el HVN (el imán).

**Archivo**: `data/src/strategy/lab/detectors/liquidity_magnet.rs`

---

### 3.5 OrderBlockFlowRetest

**Maturity**: `ObserveOnly`
**Concepto**: Precio retestea un order block con flujo confirmatorio — misma tesis que Core OBR pero con condiciones de flujo más exigentes.

**Condiciones Long**:

- `obs.nearest_bullish` disponible
- Precio dentro de 0.5×ATR del `ob_mid`
- `delta > 0` + `cvd_slope > 0` + `taker_imbalance > 0`
- `failed_acceptance = false`

**Archivo**: `data/src/strategy/lab/detectors/order_block_flow_retest.rs`

---

### Resumen Lab


| Hipótesis                | Maturity    | Estado actual       | Candidata a promoción |
| ------------------------ | ----------- | ------------------- | --------------------- |
| VwapRejection            | ShadowLab   | ✅ Generando señales | Sí — evaluar con 100+ |
| AbsorptionTrapReversal   | ObserveOnly | ✅ Observando        | No todavía            |
| SessionImbalanceBreakout | ObserveOnly | ✅ Observando        | No todavía            |
| LiquidityMagnet          | ObserveOnly | ✅ Observando        | No todavía            |
| OrderBlockFlowRetest     | ObserveOnly | ✅ Observando        | No todavía            |


**Nota**: `positioning_expansion.rs` es una función auxiliar (`positioning_adjustment()`) — no corre como detector independiente. `LabStrategyId::PositioningExpansion` fue eliminado del enum.

---

## 4. MICRO — Módulos de Soporte

Los módulos **alimentan** a Core y Lab. No emiten señales — producen contexto.

### 4.1 OrderBlockDetector

Detecta zonas de acumulación institucional. Ventana de 100 barras. Campos: `high/low/mid`, `status` (Active/Tested/Mitigated/Invalidated), `volume_ratio`, `swings_broken`.
Output: `OrderBlockContext { nearest_bullish, nearest_bearish, bullish_obs, bearish_obs }`.
**Archivo**: `data/src/detectors/order_block.rs`

### 4.2 FvgDetector

Detecta Fair Value Gaps (desequilibrios de precio entre velas N-2 y N).
**Archivo**: `data/src/detectors/fvg.rs`

### 4.3 MarketStructureTracker

BOS/CHoCH, HTF bias, premium/discount. Ventana 100 barras.
**Archivo**: `data/src/structure/`

### 4.4 SessionTracker

Clasifica el timestamp en `TradingSession` (Asia/London/LondonNyOverlap/NY/Off) y `SessionPhase` (OpeningRush/Open/Mid/Close).
**Archivo**: `data/src/session/session_tracker.rs`

### 4.5 FundingTracker

Acumula funding rate. Métricas: `current`, `avg`, `regime` (Normal/Elevated/Extreme), `velocity`, `peak_confirmed`.
**Archivo**: `data/src/institutional/funding_tracker.rs`

### 4.6 OiTracker

Acumula Open Interest. Métricas: `oi_delta`, `oi_momentum_aligned`, `delta_zscore()` (z-score rolling de últimos 20 deltas — detecta spikes anómalos).
**Archivo**: `data/src/institutional/oi_tracker.rs`

### 4.7 LiqMapTracker

Mapa estimado de stops. Metodología: stops de shorts encima de swing highs, de longs debajo de swing lows, densidad proporcional al OI, decaimiento half-life 4h.
Output: `density_above/below`, `primary_target_above/below`, `confidence`.
**Archivo**: `data/src/institutional/liq_map_tracker.rs`

### 4.8 LiquidationTracker

Acumula liquidaciones del WS. Output: USD liquidados en 5m/60s por side.
**Archivo**: `data/src/institutional/liquidation_tracker.rs`

### 4.9 LsRatioTracker

Ratios L/S de top traders y retail (Binance/Bybit REST).
**Archivo**: `data/src/institutional/ls_ratio_tracker.rs`

### 4.10 SmartMoneyScore

Score unificado institucional −1.0..+1.0 combinando OI, funding y L/S ratios.
Usado como multiplicador: ×1.15 si alineado, ×0.70 si opuesto.
**Archivo**: `data/src/institutional/smart_money_score.rs`

### 4.11 VolumeProfile

Histograma 150 bins sobre 300 velas. Output: `poc`, `vah`, `val`, `hvn_nearby[]`, `lvn_nearby[]`.

### 4.12 VwapIndicator

VWAP con reset 00:00 UTC, ±1σ/±2σ bands. AVWAP anclado a BOS y eventos.

### 4.13 KlineTrades (Footprint Acumulador)

Acumula trades WS por precio dentro de la barra actual (`FxHashMap<Price, GroupedTrades>`). Se convierte a `Vec<FootprintLevel>` al cerrar barra. Se vacía al inicio de cada barra nueva.
**Archivo**: `data/src/chart/kline.rs`

### 4.14 SpoofDetector

Wired pero inactivo (`spoof_gate_enabled = false`). Requiere L2 tick data.
**Archivo**: `data/src/detectors/spoof.rs`

---

## 5. El Router — Pipeline del Core

```
Bar M5 cerrada
    │
    ▼
1. Pre-gates globales
   ├── spread_bps > 2.0 → bloquear todo
   ├── vpin > 0.75 → penalizar score ×0.35
   └── cfg.enabled = false → Wait
    │
    ▼
2. Evaluar los 10 detectores
   │  Siempre:
   ├── VAFA, LVN, DIB, SOB, OBR, FAR, VWAP
   │  Solo si ctx.institutional disponible:
   └── LiqHunt, FER, SMD
    │
    ▼
3. Para cada candidato con señal:
   ├── Calcular score (ver Sección 6)
   ├── score >= min_score (0.60)? → candidato
   ├── rr en [min_rr=1.5, max_rr=8.0]?
   └── cooldown disponible?
    │
    ▼
4. best = max(score) de candidatos válidos
   └── emitir ShadowSignal + registrar cooldown
```

En paralelo, **sin bloquear** al Core:

```
run_strategy_lab(ctx, cfg, lab_cfg)
    → [VwapRejection, AbsorptionTrap, SessionImbalance, LiqMagnet, OBFlowRetest]
    → cada señal → tokio::spawn → write_lab_signal (Supabase)
    → LabTracker.push() → tracking multi-horizonte
```

---

## 6. Scoring y Weighting

### Score Base (estrategias MarketPure)


| Factor              | Peso | Qué mide                               |
| ------------------- | ---- | -------------------------------------- |
| `W_CVD_SLOPE`       | 0.25 | Momentum CVD en dirección de la señal  |
| `W_TAKER_IMBALANCE` | 0.20 | Agresión de takers                     |
| `W_DELTA_ALIGNED`   | 0.10 | Delta de la vela alineado              |
| `W_TARGET_ATR_DIST` | 0.25 | Target a distancia razonable (1–5×ATR) |
| `W_RR`              | 0.20 | Risk:Reward >= min_rr                  |


### Score Base (estrategias Institutional)


| Factor                              | Peso |
| ----------------------------------- | ---- |
| CVD + Taker + Delta + Target + RR   | 0.65 |
| `W_INSTITUTIONAL` (SmartMoneyScore) | 0.35 |


### Multiplicadores en cascada


| Multiplicador          | Favorable | Penalización | Condición                         |
| ---------------------- | --------- | ------------ | --------------------------------- |
| `factor_vpin`          | ×1.20     | ×0.35        | VPIN < 0.30 / > 0.75              |
| `factor_spread`        | ×1.0      | ×0.80        | Spread > 1.5 bps                  |
| `factor_regime`        | ×1.10     | ×0.60        | Regime compatible vs incompatible |
| `factor_confluencia`   | +5%       | —            | OB activo en zona                 |
| `factor_htf_structure` | ×1.20     | ×0.70        | HTF bias alineado/opuesto         |
| `factor_smart_money`   | ×1.15     | ×0.70        | SmartMoneyScore alineado/opuesto  |
| `factor_opening_rush`  | ×1.15     | ×1.0         | Solo LiqHunt en OpeningRush       |


**Veto absoluto**: si score final < 0.25 → descartado.

---

## 7. Sistema de Gates y Control de Calidad

### Gates Globales


| Gate         | Condición                      | Efecto                |
| ------------ | ------------------------------ | --------------------- |
| Spread       | spread_bps > 2.0               | Bloqueo total         |
| Data quality | quality == Stale/Missing       | `Blocked`             |
| VPIN toxic   | vpin > 0.75                    | Penalización ×0.35    |
| Spoof        | spoof_detected (deshabilitado) | Bloqueo cuando activo |


### Gates por Estrategia

**VVPC**: `taker_imbalance > 0` + `obi_l5 >= 0` para long (y simétrico para short).

**DIB**: `prev_obi_l5 > 0.10` (barra anterior) para long — persistencia de 2 barras.

**VAFA en Chop**: gates más estrictos de `cvd_slope`, `taker_imbalance`, `delta/atr`.

**LiqHunt anti-cascade**: `liq_usd_60s < 5M USD` — si ya es cascade el momento pasó.

**FAR**: `footprint_levels` no vacío — sin datos de footprint no hay señal.

**OBR**: `ctx.order_blocks` no None — sin OBs detectados no evalúa.

### Cooldown Gate

5 barras de cooldown por `(strategy_id, side)` después de emitir señal.

### R:R Gate

`1.5 <= rr <= 8.0` — filtra stops degenerados y targets irreales.

---

## 8. Sesión y Tiempo


| Sesión          | UTC         | Estrategias favorecidas           |
| --------------- | ----------- | --------------------------------- |
| Asia            | 00:00–07:00 | Ninguna con session_filter activo |
| London          | 07:00–12:00 | VAFA, VVPC, SOB, LiqHunt          |
| LondonNyOverlap | 12:00–16:00 | Todos, LiqHunt +15%               |
| NY              | 13:30–20:00 | VAFA, VVPC, SOB, LiqHunt          |
| Off             | —           | Solo institucional si habilitado  |



| Fase        | Minutos    | Características                         |
| ----------- | ---------- | --------------------------------------- |
| OpeningRush | 0–15       | Máxima volatilidad, LiqHunt bonus ×1.15 |
| Open        | 16–30      | Establecimiento de dirección            |
| Mid         | 31–60      | Continuación o consolidación            |
| Close       | últimos 30 | Book thin, reducción de posiciones      |


---

## 9. Datos Institucionales

```rust
pub struct InstitutionalContext {
    pub liquidations: LiquidationContext,  // USD liquidados en 5m/60s
    pub ls_ratio: LsRatioContext,          // top traders vs retail long%
    pub oi: OiContext,                     // OI actual + delta + tendencia
    pub funding: FundingContext,           // rate + regime + velocity + peak_confirmed
    pub liq_map: LiqMapSnapshot,           // mapa de stops estimado
}
```

### OI Delta — Interpretación


| Precio | OI  | Interpretación                             |
| ------ | --- | ------------------------------------------ |
| ↑      | ↑   | Longs frescos entrando (bullish momentum)  |
| ↓      | ↑   | Shorts frescos entrando (bearish momentum) |
| ↑      | ↓   | Longs cerrando (posible techo)             |
| ↓      | ↓   | Shorts cerrando (posible suelo)            |


**OI z-score** (`delta_zscore()`): z-score del último delta vs media de los 20 deltas anteriores. `> +2.0` = spike anómalo de compra, `< -2.0` = spike anómalo de venta.

### L/S Ratios


| top_traders_long% | retail_long% | Interpretación                       |
| ----------------- | ------------ | ------------------------------------ |
| < 45%             | > 60%        | Smart short, retail long → SMD Short |
| > 55%             | < 40%        | Smart long, retail short → SMD Long  |


---

## 10. Order Book — Snapshot del Libro


| Campo                   | Descripción                                  |
| ----------------------- | -------------------------------------------- |
| `obi_l5`                | OBI nivel 5 (−1 a +1, positivo = bid > ask)  |
| `obi_l10`               | OBI nivel 10                                 |
| `microprice`            | Precio ponderado por tamaño en top del libro |
| `spread_bps`            | Spread bid-ask en basis points               |
| `walls_above/below`     | Precios con órdenes >= umbral                |
| `thin_zone_above/below` | Pocas órdenes — precio puede moverse rápido  |


---

## 11. Flujo de Órdenes — Order Flow


| Campo                  | Descripción                                                                      |
| ---------------------- | -------------------------------------------------------------------------------- |
| `cvd`                  | Cumulative Volume Delta                                                          |
| `cvd_slope`            | Pendiente del CVD en últimas barras                                              |
| `delta`                | Delta de la vela actual                                                          |
| `taker_imbalance`      | (buy_vol − sell_vol) / total_vol                                                 |
| `vpin`                 | Volume-synchronized Probability of Informed Trading                              |
| `footprint_levels`     | `Vec<FootprintLevel>` — delta real por nivel de precio de la barra actual        |
| `footprint_absorption` | Absorción detectada en footprint (Bid/Ask/None) — derivado de `footprint_levels` |
| `stacked_imbalance`    | 3+ barras consecutivas mismo delta (Bullish/Bearish/None)                        |
| `failed_acceptance`    | Precio intentó salir del rango y regresó                                         |
| `sweep_confirmed`      | Wick cruzó swing extremo pero cerró dentro                                       |
| `mss_active`           | Cierre actual superó swing high/low previo (BOS)                                 |
| `fast_slope`           | Pendiente rápida normalizada por ATR (5 barras)                                  |
| `oi_delta_zscore`      | Z-score del último OI delta vs ventana de 20                                     |


### VPIN


| VPIN      | Interpretación | Factor |
| --------- | -------------- | ------ |
| < 0.30    | Flujo limpio   | ×1.20  |
| 0.30–0.75 | Normal         | ×1.0   |
| > 0.75    | Flujo tóxico   | ×0.35  |


---

## 12. Patrones ICT / Smart Money — Estructura de Precio

### Order Blocks

Zonas de acumulación institucional. `status`: Active → Tested → Mitigated → Invalidated.
`volume_ratio > 1.5` = OB de alto volumen (más significativo). `swings_broken >= 2` = impulso fuerte.

### Fair Value Gaps

Desequilibrios entre velas N-2 y N. Magnetos de precio hacia el relleno.

### BOS / CHoCH


| Patrón        | Significado                | Sesgo                |
| ------------- | -------------------------- | -------------------- |
| BOS alcista   | Nuevo HH                   | Bullish continuación |
| BOS bajista   | Nuevo LL                   | Bearish continuación |
| CHoCH bajista | Uptrend rompe swing low    | Posible reversión    |
| CHoCH alcista | Downtrend rompe swing high | Posible reversión    |


### Premium / Discount

Basado en rango del último swing. Premium > 50%: shorts favorecidos. Discount < 50%: longs favorecidos. Bonus +5% en scoring.

### Swing High/Low

`swing_high_20` y `swing_low_20`: max/min de las últimas 20 barras excluyendo la actual. Usados como targets estructurales y base para MSS/sweep.

---

## 13. Configuración — StrategyConfig y LabConfig

### StrategyConfig

```rust
StrategyConfig {
    enabled: bool,               // Master switch Core
    max_spread_bps: 2.0,
    max_vpin: 0.75,
    min_score: 0.60,             // Score mínimo ShadowSignal
    min_rr: 1.5,
    max_rr_m5: 8.0,
    default_ttl_ms: 15_000_000, // 250 min
    cooldown_bars: 5,
    session_filter_enabled: true,
    htf_scoring_enabled: true,
    spoof_gate_enabled: false,
    liq_hunt_min_usd: 500_000.0,
    liq_cascade_threshold: 5_000_000.0,
    funding_extreme_threshold: 0.0006,
}
```

### LabConfig

```rust
LabConfig {
    enabled: bool,              // LAB_ENABLED env var
    session_gate_enabled: true,
    spread_gate_max_bps: 3.0,
    ttl_bars: 50,               // 250 min en M5
}
```

---

## 14. Ciclo de Madurez y Promoción Lab → Core


| Transición                     | Señales mínimas | Criterio cuantitativo                                   |
| ------------------------------ | --------------- | ------------------------------------------------------- |
| ObserveOnly → ShadowLab        | N/A             | Datos mínimos disponibles                               |
| ShadowLab → PaperCandidate     | **100+**        | RR promedio ≥ 1.3, MFE/MAE ratio > 1.5                  |
| PaperCandidate → PaperPromoted | **300+**        | Expectancy positiva, profit factor > 1.2, robustez ±20% |
| PaperPromoted → CoreActive     | **500+**        | Drawdown controlado, baja correlación negativa con Core |


**Próxima evaluación**: VwapRejection (ShadowLab) — correr scripts cuando tenga 100+ señales:

```bash
python scripts/analysis/analyze_lab_outcomes.py --strategy VwapRejection --days 14
python scripts/analysis/score_decay.py --strategy VwapRejection
python scripts/analysis/session_filter_analysis.py --strategy VwapRejection
```

---

## 15. Estado de Implementación

### Completo ✅


| Módulo                                             | Notas                                                                                    |
| -------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| VAFA, LVN, SOB, OBR, FAR, LiqHunt, FER, SMD        | Detectores Core completos                                                                |
| VVPC                                               | Con gates `taker_imbalance` + `obi_l5`                                                   |
| DIB                                                | Con persistencia `prev_obi_l5` (2 barras)                                                |
| OrderBlockDetector                                 | volume_ratio, swings_broken, 4 estados                                                   |
| FvgDetector, MarketStructureTracker                | Completos                                                                                |
| SessionTracker                                     | 4 sesiones + 4 fases                                                                     |
| FundingTracker, OiTracker, LsRatioTracker          | Completos                                                                                |
| OiTracker.delta_zscore()                           | z-score rolling ventana 20                                                               |
| LiqMapTracker, LiquidationTracker, SmartMoneyScore | Completos                                                                                |
| KlineTrades (footprint acumulador)                 | on_trade → footprint_levels en contexto                                                  |
| stacked_imbalance                                  | derive_stacked_imbalance() con fallback footprint                                        |
| mss_active / sweep_confirmed                       | derive_mss_and_sweep()                                                                   |
| swing_high/low_20                                  | `derive_confirmed_swings()` — pivot con 2-bar bilateral confirmation (ventana 25 barras) |
| Regime hysteresis + Stress/Aftermath               | derive_regime_with_stress()                                                              |
| Lab módulo completo                                | types, tracker, mod, 5 detectors                                                         |
| Lab writer en supabase_writer.rs                   | write_lab_signal + write_lab_outcome (FK corregida — `id` enviado en body)               |
| Lab runner + LabTracker en monitor                 | fire-and-forget, multi-horizonte                                                         |
| Scripts análisis Python                            | analyze_lab_outcomes, compare_core_vs_lab, score_decay, session_filter_analysis          |
| Supabase lab_signals + lab_outcomes                | reset.sql + schema.sql                                                                   |
| LabStrategyId enum limpio                          | 5 variants activos: VwapRejection + 4 ObserveOnly                                        |
| Funding rate panel (UI)                            | `src/chart/indicator/kline/funding_rate.rs` — fetch + render completo                    |
| AVWAP manual (click canvas)                        | `src/chart.rs` — `PlacingAvwapAnchor` → `SetAvwapAnchor` → `on_avwap_anchor_set()`       |
| Session VWAPs separados                            | `src/chart/indicator/kline/vwap.rs` — Asia/London/NY via `compute_session_vwap()`        |
| OI z-score                                         | `data/src/institutional/oi_tracker.rs` — `delta_zscore()` rolling 20 barras              |


### Bugs corregidos en revisión 2026-05-21


| Bug                                       | Archivo                     | Fix                                              |
| ----------------------------------------- | --------------------------- | ------------------------------------------------ |
| LiqHunt target filter hardcodeado 1.5×ATR | `liquidation_hunt.rs`       | `cfg.min_rr * atr` — respeta R:R configurable    |
| Swing20 off-by-one (19 barras)            | `main.rs`                   | guard `n >= 21`, range `[n-21..n-1]`             |
| SMD evidencia LONG con lógica SHORT       | `smart_money_divergence.rs` | variables separadas por side                     |
| progress_to_target usaba `.abs()`         | `trade_manager.rs`          | `.max(0.0)` por Side — corregido sesión anterior |
| max_by tie-break no determinístico        | `router.rs`                 | `strategy_id as u8` — corregido sesión anterior  |


### Fuera de scope actual ⏸️


| Item                  | Motivo                         |
| --------------------- | ------------------------------ |
| SpoofDetector activo  | Requiere L2 tick data          |
| min_score calibración | Requiere dataset real (Fase 4) |


---

## 16. Qué falta para monetizar

El sistema está técnicamente completo. Lo que falta no es código — es evidencia.


| Blocker                               | Por qué importa                                                                                                                                        | Estado                                      |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------- |
| **100+ señales con outcomes**         | Sin datos estadísticos no hay edge demostrable. Con < 100 señales cualquier win rate es ruido de muestra pequeña.                                      | ⏳ acumulando (LAB_ENABLED=true en Railway)  |
| **Swing engine real**                 | Los pivots actuales son max/min de 20 barras — heurístico. Un swing engine con N barras de confirmación a cada lado es más preciso para MSS y targets. | ⚠️ mejora de precisión, no blocker crítico  |
| **Regime calibrado con datos reales** | Stress/Aftermath existen como heurística ATR. Los thresholds exactos deben calibrarse con historial de señales y outcomes reales.                      | ⚠️ mejora de precisión                      |
| **Simulación intrabar real**          | Cuando stop y target tocan en la misma vela M5, el motor cierra en stop (conservador). Con tick replay se resolvería el orden real.                    | ⚠️ impacto menor en backtesting, no en live |


**El único blocker real es el primero.** Los otros tres son mejoras de precisión que se hacen *después* de tener datos, no antes.

Timeline estimado con LAB_ENABLED=true:

- **2 semanas** → primera evaluación de VwapRejection (100+ señales)
- **6–8 semanas** → suficientes datos para calibrar regime y min_score
- **3–4 meses** → 500+ señales para candidato CoreActive

---

*Documento de referencia del codebase. Actualizado tras cada sprint de implementación.*
