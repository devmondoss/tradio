# Sistema de Estrategias — FlowSurface

> Documento de referencia completo. Cubre arquitectura, módulos, condiciones, scoring, gates y estado de implementación.
> Última actualización: 2026-05-18 (rev 2 — swing pivots, stacked_imbalance, MSS/sweep, Regime::Stress/Aftermath)

---

## Tabla de Contenidos

1. [Visión General — Detección en 4 Dimensiones](#1-visión-general--detección-en-4-dimensiones)
2. [MACRO — Las 6 Estrategias](#2-macro--las-6-estrategias)
3. [MICRO — Módulos de Soporte](#3-micro--módulos-de-soporte)
4. [El Router — Pipeline Completo](#4-el-router--pipeline-completo)
5. [Scoring y Weighting](#5-scoring-y-weighting)
6. [Sistema de Gates y Control de Calidad](#6-sistema-de-gates-y-control-de-calidad)
7. [Sesión y Tiempo](#7-sesión-y-tiempo)
8. [Datos Institucionales](#8-datos-institucionales)
9. [Order Book — Snapshot del Libro](#9-order-book--snapshot-del-libro)
10. [Flujo de Órdenes — Order Flow](#10-flujo-de-órdenes--order-flow)
11. [Patrones ICT / Smart Money — Estructura de Precio](#11-patrones-ict--smart-money--estructura-de-precio)
12. [Configuración — StrategyConfig](#12-configuración--strategyconfig)
13. [Estado de Implementación y Gaps](#13-estado-de-implementación-y-gaps)

---

## 1. Visión General — Detección en 4 Dimensiones

El sistema lee el mercado en **4 dimensiones simultáneas** antes de emitir cualquier señal. Ninguna dimensión por sí sola genera una señal; todas alimentan el scoring de forma combinada.

```
┌─────────────────────────────────────────────────────────────────────┐
│                    MERCADO (OHLCV + L2 + REST)                      │
└──────────────┬────────────┬──────────────┬───────────────┬──────────┘
               │            │              │               │
    ┌──────────▼──┐  ┌──────▼──────┐  ┌───▼──────┐  ┌────▼───────────┐
    │ ESTRUCTURA  │  │   FLUJO     │  │  LIBRO   │  │ INSTITUCIONAL  │
    │  (HTF/SMC)  │  │ (CVD/VPIN)  │  │  (OBI)   │  │ (OI/Funding/LS)│
    └──────────┬──┘  └──────┬──────┘  └───┬──────┘  └────┬───────────┘
               │            │              │               │
               └────────────┴──────────────┴───────────────┘
                                    │
                         ┌──────────▼──────────┐
                         │   StrategyRouter    │
                         │  (6 estrategias)    │
                         └──────────┬──────────┘
                                    │
                         ┌──────────▼──────────┐
                         │  Scoring + Gates    │
                         │  Cooldown + Session │
                         └──────────┬──────────┘
                                    │
                         ┌──────────▼──────────┐
                         │  ShadowSignal / Wait│
                         └─────────────────────┘
```

### Las 4 Dimensiones


| #   | Dimensión         | Qué mide                                                                               | Fuente principal                                              |
| --- | ----------------- | -------------------------------------------------------------------------------------- | ------------------------------------------------------------- |
| 1   | **Estructura**    | Dónde estamos en el mercado grande (HTF BOS/CHoCH, swing highs/lows, premium/discount) | `MarketStructureTracker`, `OrderBlockDetector`, `FvgDetector` |
| 2   | **Flujo**         | Qué está pasando en tiempo real (CVD, taker imbalance, delta, VPIN)                    | `OrderFlowContext`, tick trades, footprint                    |
| 3   | **Libro**         | Snapshot del libro de órdenes (OBI, paredes, spread, spoofing)                         | `OrderBookContext`, L2 data                                   |
| 4   | **Institucional** | Lo que hacen los grandes (OI, funding, L/S ratios, liquidaciones)                      | REST Binance/Bybit, `InstitutionalContext`                    |


---

## 2. MACRO — Las 6 Estrategias

Las estrategias **deciden** si hay señal. Cada una combina condiciones de las 4 dimensiones y produce una `StrategySignal` con acción `ShadowSignal` o `Wait`.

> **Modo actual**: Solo `ShadowSignal` — paper trading, sin ejecución real.

---

### 2.1 ValueAreaFailedAuction (VAFA)

**Concepto**: Precio intenta escapar del área de valor (VAH o VAL del perfil de volumen), falla y regresa — señal de reversión al POC o al extremo opuesto.

**Condiciones Long** (intento fallido por debajo de VAL):

- Precio actual < VAL y > VAL - 1.5×ATR
- `failed_acceptance = true` (precio intentó establecerse fuera pero regresó)
- CVD slope > 0 (compradores absorbiendo)
- Taker imbalance > 0 (más compras agresivas)
- OBI l5 > 0 (libro favorable)

**Condiciones Short** (intento fallido por encima de VAH):

- Precio actual > VAH y < VAH + 1.5×ATR
- `failed_acceptance = true`
- CVD slope < 0
- Taker imbalance < 0
- OBI l5 < 0

**Gates adicionales en Chop** (más estrictos):

- Short: `cvd_slope < -0.20 && taker_imbalance < -0.15 && delta/atr < -0.35`
- Long: `cvd_slope > 0.20 && taker_imbalance > 0.15 && delta/atr > 0.35`

**Target**: POC si está al otro lado, sino swing estructural.

**Archivo**: `data/src/strategy/detectors/value_area_failed_auction.rs`

---

### 2.2 VwapValuePullbackContinuation (VVPC)

**Concepto**: En tendencia, precio hace pullback a VWAP o al área de valor y retoma dirección — entrada de continuación con el momentum.

**Condiciones Long**:

- Regime: `TrendUp`
- Precio entre VAL y VWAP (zona de valor)
- Precio > AVWAP de último BOS (estructura sana)
- CVD slope > 0.10
- `fast_slope > -0.20` (gate: si fast_slope < -0.20 el impulso se opone al long)
- No `sweep_confirmed` reciente opuesto
- OBI favorable (bid > ask)

**Condiciones Short**:

- Regime: `TrendDown`
- Precio entre VWAP y VAH
- Precio < AVWAP de último BOS
- CVD slope < -0.10
- `fast_slope < 0.20` (gate: si fast_slope > +0.20 el impulso se opone al short)
- OBI favorable (ask > bid)

**Target**: Extremo del rango diario o swing estructural.

**Archivo**: `data/src/strategy/detectors/vwap_value_pullback_continuation.rs`

---

### 2.3 LvnLiquidityVacuumBreakout (LVN)

**Concepto**: Precio rompe zona de Low Volume Node (LVN) — vacío de liquidez que actúa como catapulta. Precio tiende a moverse rápido hasta el siguiente HVN.

**Condiciones**:

- `lvn_nearby` no vacío (perfil de volumen detecta LVN cercano)
- Precio cerrando barra dentro o justo al otro lado del LVN
- CVD slope confirma dirección del breakout
- Volumen de ruptura > promedio
- Siguiente HVN como target natural

**Target**: Siguiente HVN en la dirección del breakout.

**Archivo**: `data/src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs`

---

### 2.4 LiquidationHunt (LiqHunt)

**Concepto**: Movimiento brusco diseñado para cazar stops. Precio barre un nivel obvio (swing high/low), liquida posiciones, y revierte. Entrada después de confirmación del reversal.

**Condiciones**:

- `sweep_confirmed = true` (precio superó swing y regresó)
- Liquidaciones recientes >= `liq_hunt_min_usd` (500k USD default) en la dirección del sweep
- No cascade: liquidaciones en 60s < `liq_cascade_threshold` (5M USD — si es cascade, ya pasó)
- `mss_active = true` (Market Structure Shift post-sweep)
- LiqMap: nivel de densidad en la dirección del target confirma que el pool fue cazado

**Bonus de sesión**: `SessionPhase::OpeningRush` → ×1.15 al score final.

**Target**: LiqMap `primary_target_above/below` o swing estructural opuesto.

**Archivo**: `data/src/strategy/detectors/liquidation_hunt.rs`

---

### 2.5 FundingExhaustionReversal (FER)

**Concepto**: Funding rate en extremo (long o short muy cargado), posicionamiento de top traders apoya la tesis, precio muestra señales de agotamiento — reversal inminente.

**Condiciones Long** (funding extremadamente negativo, shorts agotados):

- `funding.regime == Extreme` con tasa < -threshold (-0.06%)
- `peak_confirmed = true` (tocó extremo y retreated ≥10%)
- Top traders long% >= `fer_top_long_min` (46%)
- Retail long% <= `fer_retail_long_max` (58%)
- OBI favorable o thin zone en ask (resistencia delgada)

**Condiciones Short** (funding extremadamente positivo, longs agotados):

- `funding.regime == Extreme` con tasa > +threshold
- `peak_confirmed = true`
- Top traders long% <= 54% (no overwhelmingly long)
- Retail long% >= 42%

**Evidencia adicional**: `funding_velocity_retreating`, `ask_side_thin/bid_side_thin`

**Nota**: `peak_confirmed = false` se registra en `missing` pero no bloquea — es señal de advertencia.

**Archivo**: `data/src/strategy/detectors/funding_exhaustion_reversal.rs`

---

### 2.6 SmartMoneyDivergence (SMD)

**Concepto**: Top traders (smart money) y retail apuntan en direcciones opuestas. Seguir al smart money cuando la divergencia es suficientemente grande.

**Condiciones Short** (smart money short, retail long):

- Top traders long% < `smart_short_threshold` (45%)
- Retail long% > `retail_long_threshold` (60%)
- Divergencia (retail_long - top_traders_long) > `min_divergence` (18%)
- CVD slope < 0 confirma presión vendedora
- OI delta opcional: `oi_delta > 0 && delta < -0.20×ATR` → evidencia `oi_accumulation_on_dip`

**Condiciones Long**: Inversa — smart money predominantemente long, retail short.

**Evidencia adicional**: OB en zona de divergencia, LiqMap target en dirección de la tesis.

**Archivo**: `data/src/strategy/detectors/smart_money_divergence.rs`

---

### Resumen Comparativo


| Estrategia | Perfil        | Régimen óptimo   | TTL     | Frecuencia esperada |
| ---------- | ------------- | ---------------- | ------- | ------------------- |
| VAFA       | MarketPure    | Chop/Range       | 250 min | Alta                |
| VVPC       | MarketPure    | TrendUp/Down     | 250 min | Media               |
| LVN        | MarketPure    | Expansion        | 250 min | Media-baja          |
| LiqHunt    | Institutional | Cualquiera       | 10 min  | Baja                |
| FER        | Institutional | Stress/Aftermath | 30 min  | Muy baja            |
| SMD        | Institutional | Cualquiera       | 20 min  | Baja                |

### Clasificación de Régimen

El régimen se calcula con `derive_regime_with_stress()` en cada cierre de vela (kline.rs). La función ejecuta la detección en este orden de prioridad:

1. **Stress**: Último rango (high-low) > 2× promedio de barras anteriores **Y** |slow_slope| > 0.15 → mercado en spike de volatilidad con dirección
2. **Aftermath**: Régimen anterior era Stress **Y** rango < 1.5× promedio **Y** |slow_slope| < 0.10 → volatilidad normalizando, precio digiriendo
3. **Compression**: Rango del precio en ventana / ATR < 0.8 → acumulación / coil
4. **Expansion**: Rango / ATR > 4.0 Y |slow_slope| > 0.10 → movimiento explosivo en curso
5. **TrendUp**: slow > 0.10 **o** fast > 0.25
6. **TrendDown**: slow < -0.10 **o** fast < -0.25
7. **Chop**: ninguno de los anteriores

Para TrendUp/Down se aplica **histéresis**: una vez en tendencia, el régimen se mantiene hasta que slow salga de ±0.05 **y** fast de ±0.15 (exit thresholds más bajos que entry). Evita flip-flop en mercados choppy.

`last_regime_enum` se persiste en `KlineChart` para que Aftermath pueda detectarse en el bar siguiente al Stress.


---

## 3. MICRO — Módulos de Soporte

Los módulos **alimentan** a las estrategias. No emiten señales directamente — producen contexto que los detectores consumen.

---

### 3.1 OrderBlockDetector

**Qué es**: Detecta zonas de acumulación institucional (última vela bearish antes de impulso alcista y viceversa).

**Cómo funciona**:

1. Ventana deslizante de barras OHLCV
2. Para cada barra: ¿es bearish + seguida de N barras alcistas que rompen su high? → **Bullish OB**
3. Para cada barra: ¿es bullish + seguida de N barras bajistas que rompen su low? → **Bearish OB**

**Campos del OrderBlock**:

```
high, low, mid=(high+low)/2   — zona del OB
status                        — Active / Tested / Mitigated / Invalidated
volume_ratio                  — vol_ob / avg_vol_20 (>1.0 = más significativo)
swings_broken                 — cuántos swings previos rompió el impulso
timestamp_ms                  — cuándo se formó
```

**Estados del OB** (se actualiza con cada cierre de vela):

- `Active`: Precio no ha llegado al OB
- `Tested`: Low (bullish) o high (bearish) tocó la zona pero no cerró dentro
- `Mitigated`: Cierre de vela cruzó el 50% del rango (mid)
- `Invalidated`: Cierre de vela completamente al otro lado

**Output** (`OrderBlockContext`): `bullish_obs`, `bearish_obs`, `nearest_bullish`, `nearest_bearish`

**Archivo**: `data/src/detectors/order_block.rs`

---

### 3.2 FvgDetector

**Qué es**: Detecta Fair Value Gaps — gaps de precio entre la vela N-2 y N que la vela N-1 no cubre. Zonas de desequilibrio que el precio tiende a rellenar.

**Tipos**:

- **Bullish FVG**: `high[n-2] < low[n]` (vela alcista deja gap por arriba)
- **Bearish FVG**: `low[n-2] > high[n]` (vela bajista deja gap por abajo)

**Output** (`FvgContext`): Lista de FVGs activos ordenados por proximidad al precio.

**Archivo**: `data/src/detectors/fvg.rs`

---

### 3.3 SpoofDetector

**Qué es**: Detecta spoofing en L2 — órdenes grandes que aparecen y desaparecen antes de ejecutarse, manipulando la percepción de presión compradora/vendedora.

**Estado actual**: Wired en `OrderBookContext.spoof` pero `spoof_gate_enabled = false` (requiere L2 tick data).

**Output** (`SpoofContext`): `spoof_detected`, `spoof_side`, `confidence`

**Archivo**: `data/src/detectors/spoof.rs`

---

### 3.4 MarketStructureTracker

**Qué es**: Analiza la estructura de precio en HTF (Higher Time Frame). Detecta Break of Structure (BOS) y Change of Character (CHoCH).

**Conceptos clave**:

- **BOS** (Break of Structure): Nuevo high más alto en uptrend / nuevo low más bajo en downtrend — continuación
- **CHoCH** (Change of Character): Uptrend rompe el último swing low — posible cambio de tendencia
- **Premium zone**: Precio por encima del 50% del último swing range — zona cara para longs
- **Discount zone**: Precio por debajo del 50% — zona barata para longs

**Output** (`MarketStructureContext`): `htf_bias` (bullish/bearish/neutral), `last_bos_price`, `in_premium`, `in_discount`

**Archivo**: `data/src/structure/`

---

### 3.5 SessionTracker

**Qué es**: Clasifica el tiempo actual en sesión de trading y fase dentro de la sesión.

**Sesiones** (UTC):

- **Asia**: 00:00–07:00
- **London**: 07:00–12:00
- **LondonNyOverlap**: 12:00–16:00
- **NY**: 13:30–20:00 (aproximado)
- **Off**: Fuera de sesiones principales

**Fases** dentro de sesión:

- `OpeningRush`: Primeros 15 minutos de London/NY/Overlap — máxima volatilidad
- `Open`: Minutos 16–30
- `Mid`: Minutos 31–60
- `Close`: Últimos 30 minutos antes del cierre

**Archivo**: `data/src/session/session_tracker.rs`

---

### 3.6 FundingTracker

**Qué es**: Acumula histórico de funding rate del perp y calcula métricas derivadas.

**Métricas**:

- `current`: Funding rate más reciente
- `avg`: Promedio móvil de las últimas N muestras
- `regime`: `Normal / Elevated / Extreme` según umbral configurable
- `velocity`: Cambio entre muestra[n-1] y muestra[n-3] — indica aceleración/deceleración
- `peak_confirmed`: Si tocó extremo y retreated ≥10% — señal de agotamiento

**Archivo**: `data/src/institutional/funding_tracker.rs`

---

### 3.7 LiqMapTracker

**Qué es**: Construye un mapa estimado de dónde se acumulan stops, usando swings + OI histórico como proxy.

**Metodología**:

- Stops de shorts se acumulan **encima** de swing highs
- Stops de longs se acumulan **debajo** de swing lows
- El OI en el momento del swing = proxy de densidad (más OI = más posiciones = más stops)
- Decaimiento temporal: half-life de 4 horas

**Output** (`LiqMapSnapshot`):

```
density_above / density_below   — niveles con densidad 0.0–1.0
primary_target_above/below       — nivel de mayor densidad
confidence                       — High(≥0.75) / Medium(0.50-0.74) / Low(<0.50)
data_source                      — BinanceOI / BybitOI / Estimated
```

**Archivo**: `data/src/institutional/liq_map_tracker.rs`

---

### 3.8 LiquidationTracker

**Qué es**: Acumula eventos de liquidación recibidos del websocket y calcula USD liquidados en ventanas de tiempo.

**Output**: USD liquidados en últimos 5 min, 60s; side (long/short).

**Archivo**: `data/src/institutional/liquidation_tracker.rs`

---

### 3.9 OiTracker

**Qué es**: Acumula Open Interest y calcula delta (cambio de OI) y tendencia.

**Métricas**:

- `oi_delta`: `OI[n] - OI[n-1]` — si sube mientras precio sube = longs entrando
- `oi_momentum_aligned`: Precio y OI en la misma dirección

**Archivo**: `data/src/institutional/oi_tracker.rs`

---

### 3.10 LsRatioTracker

**Qué es**: Acumula ratios Long/Short de top traders y retail de Binance/Bybit.

**Output**: `top_traders_long_pct`, `retail_long_pct` para FER y SMD.

**Archivo**: `data/src/institutional/ls_ratio_tracker.rs`

---

### 3.11 SmartMoneyScore (compute)

**Qué es**: Función que combina todos los datos institucionales en un score unificado −1.0..+1.0.

- `+1.0`: Toda la evidencia institucional apunta a alcista
- `-1.0`: Toda la evidencia apunta a bajista
- `0.0`: Neutral / sin datos

Usado como multiplicador de scoring: `×1.15` si alineado, `×0.70` si opuesto.

**Archivo**: `data/src/institutional/smart_money_score.rs`

---

### 3.12 VolumeProfile (Indicador)

**Qué es**: Histograma de 150 bins sobre ventana de 300 velas. Identifica zonas de alto y bajo volumen.

**Output**: `poc` (Point of Control), `vah`, `val`, `hvn_nearby[]`, `lvn_nearby[]`

**Archivo**: `data/src/indicators/volume_profile.rs`

---

### 3.13 VwapIndicator

**Qué es**: VWAP con reset diario a las 00:00 UTC, ±1σ y ±2σ bands. También AVWAP anclado a eventos (BOS, liquidación).

**Output**: `vwap_session`, `avwap_bos`, `avwap_event`

**Archivo**: `data/src/indicators/vwap.rs`

---

### 3.14 CooldownRegistry

**Qué es**: Control de cooldown entre señales por barras (no por tiempo). Evita señales back-to-back en el mismo nivel mientras el trade sigue activo.

**Funcionamiento**:

- Por cada estrategia + side + entry_price registra la barra de la última señal
- `is_available(strategy_id, current_bar)`: true si `current_bar - last_bar >= cooldown_bars`
- Default: 5 barras

**Archivo**: `data/src/strategy/cooldown.rs`

---

## 4. El Router — Pipeline Completo

El `StrategyRouter` en `data/src/strategy/mod.rs` orquesta todo el pipeline de detección.

```
Bar cerrada (OHLCV completo)
         │
         ▼
1. Actualizar contexto base (VolumeProfile, VWAP, OrderFlow, OrderBook)
         │
         ▼
2. Actualizar módulos MICRO
   ├── SessionTracker.update(timestamp)
   ├── MarketStructureTracker.push_bar(OHLCV)
   ├── OrderBlockDetector.push_bar(OHLCV + volume)
   ├── FvgDetector.push_bar(OHLCV)
   ├── FundingTracker.push(rate, timestamp)
   ├── LiqMapTracker.update(price, swing_h, swing_l, oi)
   ├── LiquidationTracker.push(event)
   └── OiTracker.push(oi_value)
         │
         ▼
3. Construir StrategyMarketContext (snapshot completo del mercado)
         │
         ▼
4. Pre-gates globales
   ├── spread_bps > max_spread_bps? → bloquear todas
   ├── vpin > max_vpin? → penalizar score (FACTOR_VPIN_TOXIC)
   └── spoof_gate_enabled + spoof_detected en dirección? → bloquear
         │
         ▼
5. Evaluar las 6 estrategias en paralelo
   ├── VAFA.evaluate(ctx, config)
   ├── VVPC.evaluate(ctx, config)
   ├── LVN.evaluate(ctx, config)
   ├── LiqHunt.evaluate(ctx, config)
   ├── FER.evaluate(ctx, config)
   └── SMD.evaluate(ctx, config)
         │
         ▼
6. Para cada señal con action != Wait:
   ├── Calcular score final (ver Sección 5)
   ├── Verificar score >= min_score
   ├── Verificar R:R dentro de [min_rr, max_rr_m5]
   ├── Verificar cooldown (CooldownRegistry.is_available)
   └── Si pasa todos: emitir ShadowSignal + registrar en cooldown
         │
         ▼
7. Retornar Vec<StrategySignal> para logging / outcome tracker
```

---

## 5. Scoring y Weighting

El score final de cada señal es el producto de varios factores. Cada factor puede penalizar o bonificar el score base.

### 5.1 Score Base

Para estrategias `MarketPure` (VAFA, VVPC, LVN):


| Factor              | Peso | Qué mide                               |
| ------------------- | ---- | -------------------------------------- |
| `W_CVD_SLOPE`       | 0.25 | Momentum CVD en dirección de la señal  |
| `W_TAKER_IMBALANCE` | 0.20 | Agresión de takers en dirección        |
| `W_DELTA_ALIGNED`   | 0.10 | Delta de la vela alineado              |
| `W_TARGET_ATR_DIST` | 0.25 | Target a distancia razonable (1–5×ATR) |
| `W_RR`              | 0.20 | Risk:Reward >= min_rr                  |


Para estrategias `Institutional` (LiqHunt, FER, SMD):


| Factor                   | Peso | Qué mide                               |
| ------------------------ | ---- | -------------------------------------- |
| `W_CVD_SLOPE_INST`       | 0.15 | CVD momentum                           |
| `W_TAKER_IMBALANCE_INST` | 0.10 | Presión takers                         |
| `W_DELTA_ALIGNED_INST`   | 0.05 | Delta                                  |
| `W_TARGET_ATR_DIST_INST` | 0.20 | Distancia target                       |
| `W_RR_INST`              | 0.15 | R:R                                    |
| `W_INSTITUTIONAL`        | 0.35 | Score institucional (OI, funding, L/S) |


### 5.2 Multiplicadores (aplicados en cascada)

```
score_final = score_base
            × factor_vpin
            × factor_spread
            × factor_regime
            × factor_confluencia
            × factor_htf_structure
            × factor_smart_money
            × factor_opening_rush
```


| Multiplicador          | Valor favorable      | Valor penalización      | Condición                         |
| ---------------------- | -------------------- | ----------------------- | --------------------------------- |
| `factor_vpin`          | ×1.20 (VPIN < 0.30)  | ×0.35 (VPIN > 0.75)     | Toxicidad del flujo               |
| `factor_spread`        | ×1.0                 | ×0.80                   | Spread > 1.5 bps                  |
| `factor_regime`        | ×1.10                | ×0.60                   | Regime compatible vs incompatible |
| `factor_confluencia`   | +5% por OB activo    | —                       | OB en zona de señal               |
| `factor_htf_structure` | ×1.20 (con HTF bias) | ×0.70 (contra HTF bias) | `htf_scoring_enabled = true`      |
| `factor_smart_money`   | ×1.15 (SMS alineado) | ×0.70 (SMS opuesto)     | SmartMoneyScore                   |
| `factor_opening_rush`  | ×1.15 (LiqHunt)      | ×1.0                    | Solo LiqHunt en OpeningRush       |


### 5.3 Veto absoluto

Si el score después de todos los multiplicadores cae por debajo de `VETO_SCORE_CAP` (0.25), la señal se descarta independientemente de las condiciones.

---

## 6. Sistema de Gates y Control de Calidad

Los gates son condiciones que **bloquean** la señal antes de que llegue al scoring, o la marcan como degradada.

### 6.1 Gates Globales (aplican a todas las estrategias)


| Gate         | Condición de bloqueo                                          | Efecto                         |
| ------------ | ------------------------------------------------------------- | ------------------------------ |
| Spread gate  | `spread_bps > max_spread_bps` (2.0)                           | Bloqueo total                  |
| Data quality | `quality == Stale` o `quality == Missing`                     | `StrategyAction::Blocked`      |
| VPIN toxic   | `vpin > max_vpin` (0.75)                                      | Penalización ×0.35, no bloqueo |
| Spoof gate   | `spoof_detected` en dirección de señal + `spoof_gate_enabled` | Bloqueo                        |


### 6.2 Gates por Estrategia

**VAFA en Chop**:

```
Short: cvd_slope < -0.20 AND taker_imbalance < -0.15 AND delta/atr < -0.35
Long:  cvd_slope > 0.20  AND taker_imbalance > 0.15  AND delta/atr > 0.35
```

**LiqHunt — Anti-cascade**:

```
liq_usd_60s < liq_cascade_threshold (5M USD)
```

Si el cascade ya ocurrió, la oportunidad pasó.

**FER — Peak confirmation**:

```
peak_confirmed = false → registrado en signal.missing (warning, no bloqueo)
```

### 6.3 Cooldown Gate (CooldownRegistry)

Después de emitir una señal para estrategia X + side Y, esa combinación queda bloqueada por N barras (default 5). Previene señales redundantes mientras el trade hipotético sigue activo.

### 6.4 Session Filter (opcional)

Si `session_filter_enabled = true`, las estrategias MarketPure solo operan en sesiones con liquidez suficiente (London, NY, Overlap). Las estrategias Institutional operan en cualquier sesión.

### 6.5 R:R Gate

```
rr = (target - entry) / (entry - stop)
rr >= min_rr (1.5)   → OK
rr > max_rr_m5 (8.0) → target irreal, filtrado
```

---

## 7. Sesión y Tiempo

### Sesiones de Trading


| Sesión          | UTC         | Actividad | Estrategias favorecidas          |
| --------------- | ----------- | --------- | -------------------------------- |
| Asia            | 00:00–07:00 | Baja      | Ninguna con session_filter       |
| London          | 07:00–12:00 | Alta      | VAFA, VVPC, LiqHunt              |
| LondonNyOverlap | 12:00–16:00 | Muy alta  | LiqHunt (+15%), todos            |
| NY              | 13:30–20:00 | Alta      | VAFA, VVPC, LiqHunt              |
| Off             | —           | Muy baja  | Solo institutional si habilitado |


### Fases dentro de Sesión


| Fase          | Minutos desde apertura | Características                                           |
| ------------- | ---------------------- | --------------------------------------------------------- |
| `OpeningRush` | 0–15                   | Máxima volatilidad, cacería de stops, LiqHunt bonus ×1.15 |
| `Open`        | 16–30                  | Establecimiento de dirección del día                      |
| `Mid`         | 31–60                  | Continuación o consolidación                              |
| `Close`       | Últimos 30 min         | Reducción de posiciones, thin book                        |


---

## 8. Datos Institucionales

### 8.1 InstitutionalContext (struct)

```rust
pub struct InstitutionalContext {
    pub liquidations: LiquidationContext,  // USD liquidados en 5m/60s
    pub ls_ratio: LsRatioContext,          // top traders vs retail long%
    pub oi: OiContext,                     // OI actual + delta + tendencia
    pub funding: FundingContext,           // rate + avg + regime + velocity + peak_confirmed
    pub liq_map: LiqMapSnapshot,           // mapa de stops estimado
}
```

### 8.2 FundingContext


| Campo            | Descripción                                  |
| ---------------- | -------------------------------------------- |
| `current`        | Funding rate actual (decimal: 0.0001 = 1 bp) |
| `avg`            | Promedio móvil                               |
| `regime`         | `Normal` / `Elevated` / `Extreme`            |
| `velocity`       | `rate[n-1] - rate[n-3]` — aceleración        |
| `peak_confirmed` | Tocó extremo y retreated ≥10%                |


**Umbrales FER**: `abs(rate) > 0.0006` (0.06%) = Extreme.

### 8.3 L/S Ratios — Interpretación


| top_traders_long% | retail_long% | Interpretación                                 |
| ----------------- | ------------ | ---------------------------------------------- |
| < 45%             | > 60%        | Smart money short, retail long → **SMD Short** |
| > 55%             | < 40%        | Smart money long, retail short → **SMD Long**  |
| 45–55%            | 40–60%       | Neutral / sin divergencia clara                |


### 8.4 OI Delta — Interpretación


| precio | OI  | Interpretación                     |
| ------ | --- | ---------------------------------- |
| ↑      | ↑   | Longs entrando (bullish momentum)  |
| ↓      | ↑   | Shorts entrando (bearish momentum) |
| ↑      | ↓   | Longs cerrando (posible techo)     |
| ↓      | ↓   | Shorts cerrando (posible suelo)    |


---

## 9. Order Book — Snapshot del Libro

### OrderBookContext (struct)


| Campo             | Descripción                                                  |
| ----------------- | ------------------------------------------------------------ |
| `obi_l5`          | Order Book Imbalance nivel 5 (−1 a +1, positivo = bid > ask) |
| `obi_l10`         | OBI nivel 10                                                 |
| `obi_l20`         | OBI nivel 20                                                 |
| `microprice`      | Precio ponderado por tamaño en el top del libro              |
| `spread_bps`      | Spread bid-ask en basis points                               |
| `walls_above`     | Precios con órdenes >= umbral por encima                     |
| `walls_below`     | Precios con órdenes >= umbral por debajo                     |
| `thin_zone_above` | Pocos órdenes encima — precio puede moverse rápido           |
| `thin_zone_below` | Pocos órdenes abajo                                          |
| `spoof`           | `SpoofContext` opcional (activo si `spoof_gate_enabled`)     |


### Uso en Estrategias

- **VAFA/VVPC**: `obi_l5 > 0` para long, `< 0` para short — confirmación del libro
- **FER**: `thin_zone_above/below` como evidencia de resistencia ligera en target
- **LiqHunt**: `walls_above/below` — los stops cazados están justo más allá de las paredes
- **Gates**: `bid_wall_nearby` / `ask_wall_nearby` en `OrderFlowContext` (derivados del libro)

---

## 10. Flujo de Órdenes — Order Flow

### OrderFlowContext (campos clave)


| Campo                      | Descripción                                                        |
| -------------------------- | ------------------------------------------------------------------ |
| `cvd`                      | Cumulative Volume Delta — compras − ventas acumuladas              |
| `cvd_slope`                | Pendiente del CVD en las últimas barras                            |
| `delta`                    | Delta de la vela actual                                            |
| `taker_imbalance`          | (buy_volume − sell_volume) / total_volume                          |
| `buy_volume / sell_volume` | Volumen de compras y ventas separado                               |
| `vpin`                     | Volume-synchronized Probability of Informed Trading (0–1)          |
| `cvd_divergence`           | `BearishAbsorption` o `BullishAbsorption`                          |
| `footprint_absorption`     | Absorción detectada en el footprint (Bid/Ask/None)                 |
| `stacked_imbalance`        | 3+ barras consecutivas en la misma dirección delta (Bullish/Bearish/None) — derivado de `recent_deltas` |
| `failed_acceptance`        | Precio intentó establecerse fuera del rango y regresó              |
| `sweep_confirmed`          | Wick intrabar cruzó swing extremo en las últimas 3 barras pero cerró dentro — derivado por `derive_mss_and_sweep()` |
| `mss_active`               | Cierre actual superó el swing high/low previo (break de estructura) — derivado por `derive_mss_and_sweep()` |
| `funding_rate`             | Funding rate del perp (también en InstitutionalContext)            |
| `basis`                    | Basis perp-spot en %                                               |
| `oi_delta`                 | OI delta acumulado del período                                     |
| `oi_momentum_aligned`      | Precio y OI en la misma dirección                                  |
| `bid_wall_nearby`          | Pared de bids dentro de 1×ATR por debajo                           |
| `ask_wall_nearby`          | Pared de asks dentro de 1×ATR por encima                           |
| `price_action_clean`       | Últimas 5 velas con ≤2 reversiones                                 |
| `fast_slope`               | Pendiente rápida del precio normalizada por ATR (últimas 5 barras) |


### VPIN — Interpretación


| VPIN      | Interpretación                       | Factor scoring |
| --------- | ------------------------------------ | -------------- |
| < 0.30    | Flujo limpio, mercado ordenado       | ×1.20          |
| 0.30–0.75 | Normal                               | ×1.0           |
| > 0.75    | Flujo tóxico, información asimétrica | ×0.35          |


---

## 11. Patrones ICT / Smart Money — Estructura de Precio

### 11.1 Order Blocks (OBs)

Zonas de acumulación/distribución institucional. Ver Sección 3.1 para detalles.

**Usos en estrategias**:

- VAFA: OB activo cerca del VAL/VAH como confluencia (evidencia `bullish/bearish_ob_at_zone`)
- SMD: OB en zona de divergencia (`bearish/bullish_ob_at_divergence`)
- LiqHunt: OB como zona de soporte post-sweep

### 11.2 Fair Value Gaps (FVGs)

Desequilibrios de precio que el mercado tiende a rellenar. Ver Sección 3.2.

**Interpretación**: FVG bullish activo → magneto de precio hacia abajo (relleno) o soporte.

### 11.3 Break of Structure (BOS) y Change of Character (CHoCH)


| Patrón        | Significado                | Sesgo resultante            |
| ------------- | -------------------------- | --------------------------- |
| BOS alcista   | Nuevo HH en uptrend        | Bullish (continuación)      |
| BOS bajista   | Nuevo LL en downtrend      | Bearish (continuación)      |
| CHoCH bajista | Uptrend rompe swing low    | Posible reversión a bajista |
| CHoCH alcista | Downtrend rompe swing high | Posible reversión a alcista |


### 11.4 Premium / Discount

Basado en el rango del último swing (high − low):

- **Premium** (> 50%): Zona cara — shorts favorecidos, longs en desventaja
- **Equilibrium** (≈ 50%): Zona de valor justo — dirección indefinida
- **Discount** (< 50%): Zona barata — longs favorecidos

Bonus de scoring: +5% si long en discount, +5% si short en premium.

### 11.5 Swing Highs/Lows Estructurales

El sistema trackea `swing_high_20` y `swing_low_20` calculados por `derive_swing_highs_lows()` en `adapter.rs`:

- **Método**: max(highs[0..n-1]) y min(lows[0..n-1]) sobre las últimas 20 barras, excluyendo la barra actual
- **Usos**:
  - Seleccionar targets estructurales en `find_structural_target` (VVPC, VAFA)
  - Base para `derive_mss_and_sweep()` — MSS y sweep se computan contra estos extremos
  - Alimentar al LiqMapTracker
- **En kline.rs**: Highs/lows se extraen a 20 barras (antes eran 5). Esto también expande la ventana de `derive_failed_acceptance_and_absorption` y `derive_stacked_imbalance` al mismo tamaño.

---

## 12. Configuración — StrategyConfig

```rust
StrategyConfig {
    // Globales
    enabled: bool,                     // Master switch
    max_spread_bps: 2.0,               // Spread máximo para operar
    max_vpin: 0.75,                    // VPIN que activa penalización toxic
    min_score: 0.60,                   // Score mínimo para emitir señal (MarketPure)
    min_score_institutional: 0.55,     // Score mínimo para estrategias institucionales
    default_ttl_ms: 250*60*1000,       // TTL de señal: 250 minutos
    min_rr: 1.5,                       // R:R mínimo aceptable
    max_rr_m5: 8.0,                    // R:R máximo (filtra targets irreales)
    cooldown_bars: 5,                  // Barras entre señales del mismo id

    // Feature flags
    session_filter_enabled: false,     // Activar filtro por sesión
    htf_scoring_enabled: false,        // Activar multiplicador HTF (true en producción)
    spoof_gate_enabled: false,         // Activar gate de spoofing (requiere L2)

    // LiquidationHunt
    liq_hunt_min_usd: 500_000.0,       // Mínimo USD liquidados para confirmar hunt
    liq_cascade_threshold: 5_000_000.0,// USD en 60s que indica cascade (ya tarde)
    liq_ttl_ms: 10*60*1000,            // TTL señal LiqHunt: 10 min

    // FundingExhaustionReversal
    funding_extreme_threshold: 0.0006, // 0.06% = Extreme
    funding_ttl_ms: 30*60*1000,        // TTL señal FER: 30 min
    fer_top_long_min: 0.46,            // Top traders mínimo long% para FER Long
    fer_retail_long_max: 0.58,         // Retail máximo long% para FER Long

    // SmartMoneyDivergence
    smart_short_threshold: 0.45,       // Top traders < 45% long = predominantemente short
    retail_long_threshold: 0.60,       // Retail > 60% long = retail sobreposicionado long
    min_divergence: 0.18,              // Diferencia mínima retail_long - top_traders_long
    smd_ttl_ms: 20*60*1000,            // TTL señal SMD: 20 min
}
```

**Nota sobre min_score**: El valor 0.60 es un placeholder. El umbral real se calibra en Fase D analizando la distribución histórica de scores. No optimizar antes de tener datos reales.

---

## 13. Estado de Implementación y Gaps

### 13.1 Estado Actual (completado)


| Módulo                            | Estado     | Notas                                                                 |
| --------------------------------- | ---------- | --------------------------------------------------------------------- |
| OrderBlockDetector                | ✅ Completo | volume_ratio, swings_broken, mid threshold                            |
| FvgDetector                       | ✅ Completo | Bullish/Bearish FVGs                                                  |
| MarketStructureTracker            | ✅ Completo | BOS/CHoCH, HTF bias, premium/discount                                 |
| SessionTracker                    | ✅ Completo | 4 fases incluyendo OpeningRush                                        |
| FundingTracker                    | ✅ Completo | velocity + peak_confirmed                                             |
| LiqMapTracker                     | ✅ Completo | confidence + data_source                                              |
| LiquidationTracker                | ✅ Completo |                                                                       |
| OiTracker                         | ✅ Completo |                                                                       |
| LsRatioTracker                    | ✅ Completo |                                                                       |
| SmartMoneyScore                   | ✅ Completo |                                                                       |
| CooldownRegistry                  | ✅ Completo | Bar-based cooldown                                                    |
| VAFA                              | ✅ Completo | Con chop gates estrictos                                              |
| VVPC                              | ✅ Completo | fast_slope gate ±0.20 activo para long y short                        |
| LVN                               | ✅ Completo |                                                                       |
| LiqHunt                           | ✅ Completo | LiqMap evidence                                                       |
| FER                               | ✅ Completo | velocity/peak evidencia                                               |
| SMD                               | ✅ Completo | OI delta + OB confluence                                              |
| SpoofDetector                     | ⚠️ Wired   | Requiere L2 tick data para activar                                    |
| Outcome Tracker                   | ✅ Completo | MFE/MAE/RR intrabar + bar-close                                       |
| `swing_high_20` / `swing_low_20`  | ✅ Completo | `derive_swing_highs_lows()` — max/min 20 barras excluyendo actual     |
| `mss_active` / `sweep_confirmed`  | ✅ Completo | `derive_mss_and_sweep()` — ya no hardcodeado a false                  |
| `stacked_imbalance`               | ✅ Completo | `derive_stacked_imbalance()` — 3+ barras consecutivas mismo delta     |
| `Regime::Stress` / `Aftermath`    | ✅ Completo | `derive_regime_with_stress()` — ATR spike + slope / normalización     |


### 13.2 Gaps Pendientes (Fase B+)


| Item                               | Prioridad | Bloqueo                          | Notas                                   |
| ---------------------------------- | --------- | -------------------------------- | --------------------------------------- |
| **Institutional data en GUI**      | Alta      | Connector wiring                 | `institutional = None` en kline.rs      |
| **CVD multi-timeframe (SMD)**      | Media     | Pipeline de velas 1H separado    | Requiere tracker de candles HTF         |
| **AVWAP multi-anchor manual**      | Media     | Lógica UI (clic en canvas)       | Capturar coord X → timestamp → anclar   |
| **Session VWAPs**                  | Media     | Lógica en indicador              | Asia 00-08 / London 08-16 / NY 13-21 UTC|
| **Funding rate panel**             | Media     | Endpoint REST por exchange       | Nuevo `KlineIndicator::FundingRate`     |
| **OI z-score + spike markers**     | Baja      | Sin fetch adicional              | Dentro de `OpenInterestIndicator`       |
| **SpoofDetector activo**           | Media     | L2 tick data feed                | spoof_gate_enabled = false              |
| **OB Fase B**                      | Baja      | 20+ señales con ≥60% correlación | Promover weight a 0.08                  |
| **LiqMap Fase B**                  | Baja      | 50+ predicciones hit_rate ≥0.55  | Integrar como target alternativo        |
| **Funding velocity en producción** | Media     | Connector histórico              | FundingTracker calcula, falta alimentar |
| **min_score calibración**          | Alta      | Dataset de señales reales        | Fase D — no optimizar antes             |
| **stacked_imbalance en monitor**   | Baja      | Delta history buffer             | Monitor pasa `Unknown`; mejorar cuando haya buffer de deltas por barra |


### 13.3 Flags de Producción


| Flag                     | Valor actual | Cuándo cambiar      |
| ------------------------ | ------------ | ------------------- |
| `enabled`                | false        | Al activar en papel |
| `htf_scoring_enabled`    | true         | Ya activo           |
| `session_filter_enabled` | true         | Ya activo           |
| `spoof_gate_enabled`     | false        | Al tener L2 data    |
| `min_score`              | 0.60         | Calibrar en Fase D  |


---

## Apéndice — Flujo de Datos en kline.rs

```
WebSocket tick
     │
     ▼
KlineChart::on_tick(price, volume, timestamp)
     ├── Actualizar OHLCV de la vela en curso
     ├── Actualizar footprint (delta intrabar)
     └── Si barra cerrada:
             ├── update_indicators() → VolumeProfile, VWAP, OI Delta
             ├── update_detectors()  → OB, FVG, Structure, Session, Funding
             ├── Extraer últimas 20 barras: closes, highs, lows, deltas (oldest-first)
             ├── derive_regime_with_stress(closes, highs, lows, atr, last_regime_enum)
             │       → Stress / Aftermath / TrendUp / TrendDown / Chop / Compression / Expansion
             ├── derive_stacked_imbalance(recent_deltas)  → ImbalanceSide
             ├── derive_mss_and_sweep(highs, lows, closes) → (mss_active, sweep_confirmed)
             ├── derive_swing_highs_lows(highs, lows)      → (swing_high_20, swing_low_20)
             ├── build_context()     → StrategyMarketContext
             ├── run_strategy_detection()
             │       ├── bar_index += 1
             │       ├── router.evaluate(ctx)
             │       └── cooldown checks + signal registration
             ├── self.last_regime_enum = ctx.regime  (persiste para Aftermath)
             └── update_chart_state() → render triggers
```

---

*Documento de referencia del codebase. Actualizado tras cada sprint de implementación.*