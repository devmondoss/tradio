# Implementación de Estrategias de Scalping Order Flow — BTCUSDT Perpetual
**Versión:** 1.0 | **Exchange:** Binance Futures / Bybit Linear | **Capital base:** $50 USDT
**Stack:** Rust (data feed) + Python (signal engine) | **Sesiones:** London 07:00–10:00 UTC · NY 13:00–17:00 UTC

> **Regla absoluta:** Todas las entradas son post-only (maker). Cualquier taker es emergencia.
> Break-even maker→maker con BNB: ~$36 por lote (0.001 BTC). Sin esta restricción, no hay edge.

---

## Índice

1. [Parámetros globales y restricciones de capital](#1-parámetros-globales-y-restricciones-de-capital)
2. [Arquitectura técnica (Rust + Python)](#2-arquitectura-técnica-rust--python)
3. [Estrategia 1 — OBI Micro-Price Maker Reversion](#3-estrategia-1--obi-micro-price-maker-reversion)
4. [Estrategia 2 — Absorción / Trapped Traders Reversal](#4-estrategia-2--absorción--trapped-traders-reversal)
5. [Estrategia 3 — Delta / CVD Divergence Fade](#5-estrategia-3--delta--cvd-divergence-fade)
6. [Filtros de contexto compartidos](#6-filtros-de-contexto-compartidos)
7. [Gestión de riesgo unificada](#7-gestión-de-riesgo-unificada)
8. [Journal y métricas de validación](#8-journal-y-métricas-de-validación)
9. [Red flags globales](#9-red-flags-globales)
10. [Roadmap de implementación por etapas](#10-roadmap-de-implementación-por-etapas)

---

## 1. Parámetros globales y restricciones de capital

### Especificaciones del contrato

| Parámetro | Binance USDT-M | Bybit Linear |
|---|---|---|
| Tick size | $0.10 | $0.10 |
| Lot size (qty step) | 0.001 BTC | 0.001 BTC |
| Min notional | 100 USDT | 5 USDT |
| Min qty | 0.001 BTC | 0.001 BTC |
| Maker fee (VIP0) | 0.020% | 0.020% |
| Taker fee (VIP0) | 0.050% | 0.055% |
| Maker fee con BNB | 0.018% | — |

> **Nota crítica $15:** Con BTC ≈ $100,000, un lote mínimo = $100 notional. Con $15 necesitás ≥7x solo para abrir una posición, sin buffer para gestión. Recomendación: paper trading con $15, live con mínimo $50.

### Fees y break-even por round-trip (0.001 BTC, BTC=$100k)

```
Maker→Maker (Binance con BNB):  0.018% + 0.018% = 0.036% ≈ $36  (~360 ticks)
Maker→Maker (sin BNB):          0.020% + 0.020% = 0.040% ≈ $40  (~400 ticks)
Maker→Taker:                    0.020% + 0.050% = 0.070% ≈ $70  (~700 ticks)
Taker→Taker:                    0.050% + 0.050% = 0.100% ≈ $100 (~1000 ticks)
```

**Implicación directa:** Cada scalp necesita capturar al menos 360–400 ticks de movimiento favorable neto de fees solo para break-even. Cualquier estrategia con taker entries empieza con un handicap de $60–$100.

### Parámetros de riesgo globales

```yaml
account_risk:
  capital_live: 50.0          # USDT mínimo recomendado
  risk_per_trade_pct: 1.0     # 1% del capital por trade = $0.50 en $50
  max_risk_per_trade_pct: 2.0 # nunca superar
  daily_loss_limit_pct: 3.0   # stop trading el día
  daily_target_pct: 1.5       # objetivo diario (no forzar si se alcanza)
  max_trades_per_session: 5   # calidad sobre cantidad
  max_consecutive_losses: 3   # parar y revisar

position_sizing:
  lot_size: 0.001            # BTC — único tamaño disponible a este capital
  leverage_range: [5, 10]    # efectivo; nunca superar 10x
  leverage_preferred: 7      # balance entre margen y distancia de liquidación

sessions:
  london:  "07:00-10:00 UTC"
  new_york: "13:00-17:00 UTC"
  avoid:   "21:00-06:00 UTC"  # thin book, spoofing-prone
  funding_window_buffer: 60  # segundos — flatten posiciones antes del funding
```

---

## 2. Arquitectura técnica (Rust + Python)

### Streams de datos requeridos

**Binance Futures WebSocket (wss://fstream.binance.com):**

```
btcusdt@depth@0ms        → L2 orderbook (incremental, 0ms delay)
btcusdt@aggTrade         → trades agregados (precio, qty, is_buyer_maker)
btcusdt@forceOrder       → liquidaciones (throttled 1/s desde abril 2021)
btcusdt@markPrice@1s     → mark price + predicted funding rate
btcusdt@openInterest     → open interest (vía REST cada 6s, no WS)
```

**Bybit V5 WebSocket (wss://stream.bybit.com/v5/public/linear):**

```
orderbook.50.BTCUSDT     → L2 50 niveles
publicTrade.BTCUSDT      → trades públicos
allLiquidation.BTCUSDT   → liquidaciones
tickers.BTCUSDT          → funding rate, OI, mark price
```

### Capa Rust — responsabilidades

```rust
// Pseudocódigo de estructura de datos core

struct L2Book {
    bids: BTreeMap<i64, f64>,  // precio en ticks → qty
    asks: BTreeMap<i64, f64>,
    seq: u64,                  // para validación de secuencia
}

impl L2Book {
    fn obi(&self, levels: usize) -> f64 {
        let bid_qty: f64 = self.bids.iter().rev().take(levels).map(|(_, q)| q).sum();
        let ask_qty: f64 = self.asks.iter().take(levels).map(|(_, q)| q).sum();
        if bid_qty + ask_qty == 0.0 { return 0.5; }
        bid_qty / (bid_qty + ask_qty)
    }

    fn micro_price(&self) -> f64 {
        let best_bid = *self.bids.keys().rev().next().unwrap() as f64 * TICK;
        let best_ask = *self.asks.keys().next().unwrap() as f64 * TICK;
        let obi = self.obi(1);
        best_ask * obi + best_bid * (1.0 - obi)
    }

    fn spread_ticks(&self) -> i64 {
        let best_ask = *self.asks.keys().next().unwrap();
        let best_bid = *self.bids.keys().rev().next().unwrap();
        best_ask - best_bid
    }
}

struct AggTradeState {
    cvd: f64,                        // cumulative volume delta
    delta_window: VecDeque<f64>,     // ventana rolling para DZ
    volume_window: VecDeque<f64>,    // ventana rolling para VR
    obi_ema: f64,                    // EMA del OBI
}

// Evento normalizado que va a Python vía IPC
struct NormalizedEvent {
    ts_ns: u64,
    event_type: EventType,   // Trade | BookUpdate | Liquidation | FundingUpdate
    price: f64,
    qty: f64,
    side: Side,              // Buy | Sell
    is_aggressor: bool,
    obi: f64,
    micro_price: f64,
    cvd: f64,
    dz: f64,                 // delta z-score
    vr: f64,                 // volume ratio
}
```

**Tareas críticas de Rust:**
- Mantener L2 book completo con `BTreeMap<price_tick, qty>`
- Fetch REST snapshot al conectar (exchanges no envían snapshot inicial en WS)
- Validar sequence numbers de Binance (`U`, `u`, `pu`) y reiniciar en gap
- Calcular OBI, micro-price, CVD, DZ, VR en hot loop por cada evento
- Serializar a IPC (shared memory ring buffer o Unix socket) con msgpack/flatbuffers
- Timestampear en recepción local (no confiar en exchange timestamp)

### Capa Python — responsabilidades

```python
# Signal engine skeleton con asyncio

import asyncio
import numpy as np
from collections import deque
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

class Side(Enum):
    LONG = "long"
    SHORT = "short"

@dataclass
class MarketState:
    # OBI / Micro-price
    obi: float = 0.5
    obi_ema_fast: float = 0.5    # EMA 10 eventos
    obi_ema_slow: float = 0.5    # EMA 60 eventos
    micro_price: float = 0.0
    spread_ticks: int = 1

    # Delta / CVD
    cvd: float = 0.0
    cvd_history: deque = field(default_factory=lambda: deque(maxlen=500))
    dz: float = 0.0              # delta z-score (calculado en Rust)
    vr: float = 1.0              # volume ratio

    # VWAP
    vwap: float = 0.0
    vwap_std: float = 0.0
    vwap_z: float = 0.0

    # Sesión
    session_open_price: float = 0.0
    bars_m1: deque = field(default_factory=lambda: deque(maxlen=200))
    bars_m5: deque = field(default_factory=lambda: deque(maxlen=100))

    # Estado de trading
    position: Optional[float] = None  # None = flat
    entry_price: float = 0.0
    active_orders: dict = field(default_factory=dict)

    # Liquidaciones y OI
    liq_volume_1s: float = 0.0
    liq_sma: float = 0.0
    oi_current: float = 0.0
    oi_prev: float = 0.0

    # Funding
    funding_rate: float = 0.0
    funding_percentile: float = 0.5
    next_funding_ts: int = 0

class RiskManager:
    def __init__(self, capital: float, risk_pct: float = 0.01):
        self.capital = capital
        self.risk_pct = risk_pct
        self.daily_loss = 0.0
        self.daily_trades = 0
        self.consecutive_losses = 0

    def can_trade(self, daily_loss_limit: float = 0.03) -> bool:
        if self.daily_loss / self.capital >= daily_loss_limit:
            return False
        if self.daily_trades >= 5:
            return False
        if self.consecutive_losses >= 3:
            return False
        return True

    def position_size(self) -> float:
        return 0.001  # lot size fijo con este capital

    def update_pnl(self, pnl: float):
        self.daily_loss += min(0, pnl)
        self.daily_trades += 1
        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
```

---

## 3. Estrategia 1 — OBI Micro-Price Maker Reversion

**Tipo:** Market making híbrido (mean-reversion sesgada por orderbook)
**Validación:** **Sí** — `hftbacktest` BTCUSDT/Binance con datos reales
**Win rate:** 55–65% de round-trips completados favorablemente
**R:R:** ~1:1 a 1:1.5
**Sharpe interno hftbacktest:** ~3.0–5.4 (2025, rebate-dependiente, decayendo)
**Timeframe:** Tick / sub-segundo para señal; hold de segundos a ~2 minutos
**Entrada:** Post-only siempre

### 3.1 Mecanismo

Explota la relación casi-lineal entre el imbalance del orderbook y los cambios de precio en el corto plazo (Cont-Kukanov-Stoikov 2014). El micro-price —mid ajustado por la proporción del imbalance— predice mejor el precio futuro inmediato que el mid-price simple. El bot cotiza bid y ask post-only sesgando sus quotes hacia donde el OBI indica que el precio se va a mover, capturando spread maker mientras la señal está activa.

**Intuición:** Si hay mucho más volumen en el bid que en el ask (OBI > 0.5), el precio probablemente suba. El bot sube su cotización de bid y ask proporcionalmente, por lo que si es llenado en el ask vende más caro, y si es llenado en el bid compra en un nivel que ya tiene sesgo alcista.

### 3.2 Indicadores y variables

| Variable | Definición | Fuente |
|---|---|---|
| `OBI` | `Σ bid_qty[:N] / (Σ bid_qty[:N] + Σ ask_qty[:N])`, N=5 | L2 book, Rust |
| `micro_price` | `best_ask × OBI + best_bid × (1 - OBI)` | L2 book, Rust |
| `obi_ema_fast` | EMA(OBI, α=0.1) ~10 eventos | Python |
| `obi_ema_slow` | EMA(OBI, α=0.017) ~60 eventos | Python |
| `vol_estimate` | Rango high-low últimos 60s normalizado | Python |
| `spread_ticks` | `best_ask_tick - best_bid_tick` | L2 book, Rust |
| `half_spread` | `c1 × max(vol_estimate, 1 tick)` | Python (tunable) |
| `skew` | `κ × (OBI_ema_fast - 0.5)` | Python (tunable) |
| `cvd_window` | CVD rolling últimas 50 operaciones | Rust/Python |
| `position` | Inventario actual en BTC | Python |
| `gamma` | Aversión al riesgo de inventario | Config |

**Constantes iniciales (calibrar con backtest):**
```python
C1 = 2.0      # multiplicador del half-spread (en ticks)
KAPPA = 4.0   # sensibilidad del skew al OBI
GAMMA = 0.1   # aversión al riesgo de inventario (Avellaneda-Stoikov)
N_LEVELS = 5  # niveles del libro para OBI
OBI_THRESHOLD_LONG  = 0.60   # OBI mínimo para sesgar longs
OBI_THRESHOLD_SHORT = 0.40   # OBI máximo para sesgar shorts
MAX_INVENTORY = 0.002  # BTC — máximo 2 lotes antes de pausar
```

### 3.3 Condiciones de entrada (cotización activa)

El sistema cotiza cuando **todas** las siguientes condiciones se cumplen:

```python
def should_quote(state: MarketState, risk: RiskManager) -> bool:
    # 1. Sesión activa
    if not in_active_session():
        return False

    # 2. Spread aceptable (book no está thin)
    if state.spread_ticks > 2:
        return False

    # 3. No hay evento de alta volatilidad en curso
    if state.vr > 5.0:  # volumen 5x la media = evento, pausar
        return False

    # 4. No hay funding en los próximos 60 segundos
    if seconds_to_funding(state.next_funding_ts) < 60:
        return False

    # 5. Inventario dentro de límites
    if abs(state.position or 0) >= MAX_INVENTORY:
        return False

    # 6. Risk manager permite
    if not risk.can_trade():
        return False

    # 7. Señal OBI tiene convicción mínima
    obi_deviation = abs(state.obi_ema_fast - 0.5)
    if obi_deviation < 0.05:  # señal muy neutral, no cotizar
        return False

    return True
```

### 3.4 Cálculo de quotes

```python
def calculate_quotes(state: MarketState) -> dict:
    obi = state.obi_ema_fast
    micro = state.micro_price

    # Half-spread dinámico basado en volatilidad
    half = C1 * max(state.vol_estimate, 1) * 0.10  # en USD

    # Skew por OBI (mueve ambos quotes en la dirección del imbalance)
    skew = KAPPA * (obi - 0.5) * 0.10  # en USD

    # Skew adicional por inventario (reduce exposición si estamos cargados)
    inventory_skew = -GAMMA * (state.position or 0) * state.vol_estimate

    # Quotes finales
    bid_px = round_to_tick(micro - half + skew + inventory_skew)
    ask_px = round_to_tick(micro + half + skew + inventory_skew)

    return {
        "bid": bid_px,
        "ask": ask_px,
        "qty": 0.001,
        "long_bias": obi > OBI_THRESHOLD_LONG and (state.cvd > 0),
        "short_bias": obi < OBI_THRESHOLD_SHORT and (state.cvd < 0),
    }

def round_to_tick(price: float, tick: float = 0.10) -> float:
    return round(round(price / tick) * tick, 2)
```

### 3.5 Gestión de posición y salida

```python
def manage_position(state: MarketState, entry_px: float, side: Side) -> dict:
    # Target mínimo: break-even + 1 tick
    be_move = 0.040 * entry_px  # 0.040% de break-even maker→maker
    target_pct = max(be_move * 1.2, 4 * 0.10)  # al menos 4 ticks sobre BE

    if side == Side.LONG:
        tp = round_to_tick(entry_px + target_pct)
        sl = round_to_tick(entry_px - target_pct * 0.8)  # RR ~1.2:1
    else:
        tp = round_to_tick(entry_px - target_pct)
        sl = round_to_tick(entry_px + target_pct * 0.8)

    return {"tp": tp, "sl": sl, "qty": 0.001}
```

**Salidas adicionales (time-stop y señal-reversal):**

```python
def should_exit_position(state: MarketState, entry_time: float,
                          entry_side: Side) -> tuple[bool, str]:
    time_elapsed = current_time() - entry_time

    # Time-stop: 2 minutos máximo en posición para scalp
    if time_elapsed > 120:
        return True, "time_stop"

    # Señal se revirtió: OBI flipeó en contra
    if entry_side == Side.LONG and state.obi_ema_fast < 0.45:
        return True, "obi_reversal"
    if entry_side == Side.SHORT and state.obi_ema_fast > 0.55:
        return True, "obi_reversal"

    # CVD en contra sostenido
    if entry_side == Side.LONG and state.dz < -1.5:
        return True, "delta_adverse"
    if entry_side == Side.SHORT and state.dz > 1.5:
        return True, "delta_adverse"

    # Spread se ensanchó (adverse selection en curso)
    if state.spread_ticks > 3:
        return True, "spread_widening"

    return False, ""
```

### 3.6 Red flags — cancelar cotización inmediatamente

```python
RED_FLAGS_S1 = [
    lambda s: s.spread_ticks > 2,                    # book thin
    lambda s: s.vr > 5.0,                            # evento de vol
    lambda s: seconds_to_funding(s.next_funding_ts) < 60,
    lambda s: abs(s.position or 0) >= MAX_INVENTORY,
    lambda s: high_impact_news_in_window(),           # ±10 min de macro
    lambda s: s.liq_volume_1s > 3 * s.liq_sma,      # cascade en curso
]
```

### 3.7 Pseudocódigo completo del loop

```python
async def strategy_1_loop(feed, exchange):
    state = MarketState()
    risk = RiskManager(capital=50.0)

    async for event in feed:
        update_state(state, event)

        if state.position is not None:
            # Gestionar posición abierta
            should_exit, reason = should_exit_position(state, entry_time, entry_side)
            if should_exit:
                await exchange.cancel_all_orders()
                await exchange.close_position_post_only(reason)
                risk.update_pnl(calculate_pnl(state))

        elif should_quote(state, risk):
            quotes = calculate_quotes(state)
            await exchange.update_quotes_post_only(
                bid=quotes["bid"], ask=quotes["ask"], qty=quotes["qty"]
            )
```

### 3.8 Nota sobre alpha decayente

El Sharpe interno de `hftbacktest` cayó de ~10.8 (mayo 2023) a ~3.0 (julio 2025). El return per-trade bajó de 0.0139% a 0.0044%. La estrategia sigue siendo positiva pero el edge se está comprimiendo. **Revalidar mensualmente con datos frescos de Tardis. Si el return per-trade cae por debajo de las fees (0.036%), pausar hasta recalibrar.**

---

## 4. Estrategia 2 — Absorción / Trapped Traders Reversal

**Tipo:** Mean-reversion pura basada en orderflow
**Validación:** Parcial (mecanismo bien documentado; win rates de fuentes practitioner)
**Win rate:** 55–65%
**R:R:** 1:1.5 – 1:2
**Timeframe:** M1 footprint / tick para entrada; M5 para nivel de referencia
**Entrada:** Post-only en retest del POC

### 4.1 Mecanismo

Cuando un gran número de órdenes de mercado (agresoras) intentan romper un nivel pero encuentran suficientes órdenes límite pasivas que absorben el impacto, el precio falla en desplazarse. Los agresores quedan atrapados en posición perdedora. Cuando el precio reclama el nivel, esos traders deben cerrar forzosamente (stop loss), impulsando el movimiento en la dirección contraria.

**En crypto (BTC específicamente):** El precio se mueve en contra del delta agresor más frecuentemente que en índices. Delta negativo fuerte en soporte suele ser una trampa para vendedores. Esta es la mecánica contraria a índices como ES/NQ.

### 4.2 Indicadores y variables

| Variable | Definición | Fuente |
|---|---|---|
| `delta` | `Σ(buy_qty - sell_qty)` por vela (aggTrades) | Rust |
| `DZ` | `(delta - mean(delta, N)) / std(delta, N)`, N=50 (M5) o N=150 (M1) | Python |
| `VR` | `vol_vela / SMA(vol, N)` | Python |
| `POC_vela` | Precio con mayor volumen dentro de la vela | Python (footprint) |
| `poc_wick_ratio` | `(POC_vela - low_vela) / (high_vela - low_vela)` para longs | Python |
| `AS_long` | `max(-DZ, 0) × (1 - max(desplazamiento, 0))` | Python |
| `AS_short` | `max(DZ, 0) × (1 - max(-desplazamiento, 0))` | Python |
| `desplazamiento` | `(close - open) / (high - low)` — entre -1 y +1 | Python |
| `absorption_wall` | Volumen en bid/ask que no se mueve durante el spike | L2 book, Rust |
| `liq_ratio` | `liq_volume_en_extremo / SMA(liq_volume, 50)` | Rust |
| `oi_change_pct` | `(OI_actual - OI_6_velas_atrás) / OI_6_velas_atrás` | REST poll |
| `range_high`, `range_low` | Extremos del rango identificado | Python |
| `range_poc` | POC del perfil de volumen del rango completo | Python |

**Ventanas de normalización:**
```python
N_M5 = 50    # velas M5 para DZ y VR
N_M1 = 150   # velas M1 para DZ y VR
```

### 4.3 Condiciones de entrada — Setup LONG (Absorción bajista fallida)

**Fase 1 — Contexto (gates binarios, todos deben pasar):**

```python
def context_gates_long(state: MarketState, regime: str) -> bool:
    gates = [
        regime == "RANGE",                          # solo operar en rango
        in_active_session(),                         # Londres o NY
        not high_impact_news_in_window(minutes=10),  # sin macro ±10 min
        state.spread_ticks <= 2,                     # book sano
        not in_middle_zone(state),                   # NO en 35%-65% del rango
        price_near_range_low(state, tolerance_atr=0.25),  # cerca del low
    ]
    return all(gates)

def in_middle_zone(state: MarketState) -> bool:
    rng = state.range_high - state.range_low
    mid_low  = state.range_low  + rng * 0.35
    mid_high = state.range_low  + rng * 0.65
    return mid_low <= state.current_price <= mid_high
```

**Fase 2 — Señal de absorción en la vela de trigger:**

```python
def absorption_long_signal(candle: Candle, state: MarketState) -> tuple[bool, float]:
    """
    Retorna (señal_válida, absorption_score)
    """
    dz  = candle.dz    # calculado en Rust con ventana normalizada
    vr  = candle.vr
    disp = (candle.close - candle.open) / max(candle.high - candle.low, 0.01)

    # Condición 1: Delta negativo fuerte (vendedores agresivos entraron)
    cond_delta_neg = dz <= -1.5

    # Condición 2: Volumen significativamente por encima de la media
    cond_volume = vr >= 2.0

    # Condición 3: Vela NO cerró por debajo del nivel (sin aceptación)
    cond_no_acceptance = candle.close >= state.range_low

    # Condición 4: Precio cerró en contra del delta (absorción)
    cond_price_vs_delta = candle.close > candle.open  # vela cerró alcista pese a delta negativo

    # Condición 5: POC de la vela quedó en la mecha inferior
    poc_wick_ratio = (candle.poc - candle.low) / max(candle.high - candle.low, 0.01)
    cond_poc_wick = poc_wick_ratio < 0.35  # POC en el tercio inferior de la vela

    # Score de absorción (Absorption Score)
    as_long = max(-dz, 0) * (1 - max(disp, 0))

    # Gate duro: si el precio cerró fuera y aceptó afuera = ruptura real
    if candle.close < state.range_low and not reclaimed_in_same_candle(candle, state):
        return False, 0.0  # RUPTURA REAL — no fadear

    # Todas las condiciones core deben cumplirse
    core_conditions = (
        cond_delta_neg and
        cond_volume and
        cond_no_acceptance and
        cond_price_vs_delta
    )

    # Bonus si el POC quedó en la mecha
    if not core_conditions:
        return False, 0.0

    # Score final
    score = as_long
    if cond_poc_wick:
        score *= 1.3  # boost si el POC confirma
    if candle.liq_ratio >= 3.0:
        score *= 1.2  # boost si hubo liquidaciones

    return True, score
```

**Fase 3 — Trigger de entrada (al menos uno requerido):**

```python
def get_entry_trigger_long(state: MarketState,
                            absorption_candle: Candle) -> tuple[str, float]:
    """
    Retorna (tipo_entrada, precio_entrada)
    - "aggressive": retest del POC de la vela de absorción
    - "conservative": cierre confirmado de vela siguiente con delta positivo
    - "reclaim": precio reclamó el range low
    - None si no hay trigger
    """
    current = state.current_price
    poc = absorption_candle.poc

    # Entrada agresiva: precio retestea el POC de la absorción
    if abs(current - poc) <= 2 * TICK_SIZE:
        return "aggressive", poc + TICK_SIZE  # post-only 1 tick por encima del POC

    # Entrada conservadora: vela siguiente cerró positiva con delta flip
    next_candle = state.bars_m1[-1] if state.bars_m1 else None
    if (next_candle and
        next_candle.close > next_candle.open and
        next_candle.dz > 0.5 and
        next_candle.close > poc):
        return "conservative", next_candle.close + TICK_SIZE

    # Reclaim del range low
    if current > state.range_low and prev_price_was_below(state, state.range_low):
        return "reclaim", current + TICK_SIZE

    return None, 0.0
```

### 4.4 Condiciones de entrada — Setup SHORT (Absorción alcista fallida)

Espejo exacto del long:

```python
def absorption_short_signal(candle: Candle, state: MarketState) -> tuple[bool, float]:
    dz  = candle.dz
    vr  = candle.vr
    disp = (candle.close - candle.open) / max(candle.high - candle.low, 0.01)

    cond_delta_pos = dz >= 1.5                      # compradores agresivos entraron
    cond_volume    = vr >= 2.0
    cond_no_accept = candle.close <= state.range_high
    cond_price_vs_delta = candle.close < candle.open  # vela bajista pese a delta positivo
    poc_wick_ratio = (candle.high - candle.poc) / max(candle.high - candle.low, 0.01)
    cond_poc_wick  = poc_wick_ratio < 0.35          # POC en tercio superior

    as_short = max(dz, 0) * (1 - max(-disp, 0))

    if candle.close > state.range_high:
        return False, 0.0  # RUPTURA REAL alcista

    core = cond_delta_pos and cond_volume and cond_no_accept and cond_price_vs_delta
    if not core:
        return False, 0.0

    score = as_short
    if cond_poc_wick: score *= 1.3
    if candle.liq_ratio >= 3.0: score *= 1.2

    return True, score
```

### 4.5 Scoring de convicción

```python
def calculate_conviction_score(absorption_score: float,
                                 candle: Candle,
                                 state: MarketState,
                                 side: Side) -> float:
    """
    Score 0-100 basado en intensidad de cada señal.
    Umbral mínimo para operar: 55 (scout), 70 (estándar), 85 (alta convicción)
    """

    def intensity(value, min_thresh, strong_thresh):
        return min(max((value - min_thresh) / (strong_thresh - min_thresh), 0), 1)

    score = 0.0

    # Absorción (peso 22)
    score += 22 * intensity(absorption_score, 1.5, 3.0)

    # Delta Z-score (peso 12)
    score += 12 * intensity(abs(candle.dz), 1.5, 2.5)

    # Volume Ratio (peso 10)
    score += 10 * intensity(candle.vr, 2.0, 4.0)

    # POC en mecha (peso 12)
    poc_wick = poc_wick_ratio(candle, side)
    score += 12 * intensity(poc_wick, 0.5, 0.9)

    # Proximidad al extremo (peso 18)
    dist_atr = distance_to_extreme_in_atr(state, side)
    proximity = intensity(0.25 - dist_atr, 0, 0.20)  # invertido: más cerca = más intensidad
    score += 18 * max(proximity, 0)

    # CVD divergencia (peso 8)
    cvd_swings = count_cvd_divergence_swings(state, side, lookback=20)
    score += 8 * intensity(cvd_swings, 1, 3)

    # Big trades atrapados (peso 6)
    if candle.big_trade_present and big_trades_in_wrong_direction(candle, side):
        score += 6 * intensity(candle.big_trade_size_ratio, 10, 25)

    # Liquidaciones crypto (peso 6)
    score += 6 * intensity(state.liq_ratio, 3.0, 8.0)

    # Open Interest confirma (peso 4)
    oi_change = abs(state.oi_change_pct)
    score += 4 * intensity(oi_change, 0.010, 0.030)

    return round(score, 1)

# Decisión de tamaño por score:
# < 55  → No trade
# 55-69 → Scout: 0.5x (mínimo = 1 lote, pero convicción baja = reducir frecuencia)
# 70-84 → Estándar: 1.0x (1 lote = 0.001 BTC)
# ≥ 85  → Alta convicción: 1.5x (pero con $50 esto sigue siendo 1 lote)
```

### 4.6 Stop Loss, Take Profit y gestión

```python
def calculate_sl_tp(entry_px: float, side: Side,
                     absorption_candle: Candle,
                     state: MarketState) -> dict:
    tick = 0.10

    if side == Side.LONG:
        # SL: debajo del low de la absorción (donde los atrapados tienen razón)
        sl_primary = absorption_candle.low - 0.25 * state.atr
        sl_max     = state.range_low - 3 * tick  # nunca más allá del range low

        # TP1: POC del rango (70-80% probabilidad de llegar)
        tp1 = state.range_poc

        # TP2: Range High (rotación completa)
        tp2 = state.range_high

        # Gestión: mover SL a BE después de TP1
        be_price = entry_px

    else:  # SHORT
        sl_primary = absorption_candle.high + 0.25 * state.atr
        sl_max     = state.range_high + 3 * tick

        tp1 = state.range_poc
        tp2 = state.range_low
        be_price = entry_px

    # Gate de RR: mínimo 1.5:1 para operar
    rr = abs(tp1 - entry_px) / abs(entry_px - sl_primary)
    if rr < 1.5:
        return {"valid": False, "reason": "rr_below_minimum"}

    return {
        "valid": True,
        "sl": sl_primary,
        "sl_max": sl_max,
        "tp1": tp1,
        "tp2": tp2,
        "be": be_price,
        "rr": rr,
        "tp1_close_pct": 0.50,   # cerrar 50% en TP1
        "move_sl_after_tp1": True
    }
```

### 4.7 Red flags — invalidación inmediata

```python
RED_FLAGS_S2_LONG = [
    # Ruptura real: precio cerró afuera y aceptó afuera (2+ velas)
    lambda s, c: c.close < s.range_low and next_candle_accepts_below(s),

    # CVD rompe con el precio (no divergencia sino confirmación bajista)
    lambda s, c: s.cvd < min(s.cvd_history) * 0.95,

    # Delta continúa negativo DESPUÉS del supuesto reclaim
    lambda s, c: c.dz < -1.5 and c.close < s.entry_price,

    # Volumen se expande en dirección del breakout
    lambda s, c: c.vr > 4.0 and c.close < s.range_low,

    # No reclamó el nivel en 3 velas
    lambda s, c: s.bars_since_absorption >= 3 and not reclaimed(s),

    # Cascade de liquidaciones activo
    lambda s, c: s.liq_ratio > 8.0 and liq_direction == "long",  # liquidando longs
]
```

---

## 5. Estrategia 3 — Delta / CVD Divergence Fade

**Tipo:** Mean-reversion por divergencia de flujo
**Validación:** Parcial (~60% WR citado en literatura practitioner)
**Win rate:** ~60% (no peer-reviewed, tratar como hipótesis)
**R:R:** ~1:2
**Timeframe:** M1 para entrada, M5 para estructura de swings
**Entrada:** Post-only en bid/ask actual al detectar divergencia

### 5.1 Mecanismo

Cuando el precio hace un nuevo máximo/mínimo local pero el CVD no confirma (divergencia), el movimiento carece de soporte de agresores institucionales. Los participantes que empujaron el precio no tienen respaldo del flujo de órdenes, por lo que el movimiento es poco sostenible. Variante de "liquidity sweep": el precio barre un nivel trampa, el delta colapsa, y el fade captura el retroceso.

**Diferencia clave con la Estrategia 2:** La estrategia 2 requiere absorción en una vela (señal dentro de la vela). La estrategia 3 requiere divergencia entre swings (señal entre velas consecutivas). Son complementarias: la 2 es más precisa en el nivel, la 3 es más rápida de detectar.

### 5.2 Indicadores y variables

| Variable | Definición | Fuente |
|---|---|---|
| `CVD` | `Σ(buy_qty - sell_qty)` acumulado desde el inicio de la sesión | Rust |
| `cvd_swing_high` | Máximo local del CVD en ventana de N velas | Python |
| `cvd_swing_low` | Mínimo local del CVD en ventana de N velas | Python |
| `price_swing_high` | Máximo local del precio en ventana de N velas | Python |
| `price_swing_low` | Mínimo local del precio en ventana de N velas | Python |
| `divergence_lookback` | Número de velas para comparar swings (default: 20 en M1) | Config |
| `DZ` | Delta Z-score normalizado | Rust |
| `VR` | Volume Ratio | Rust |
| `obi_confirm` | OBI confirma dirección del fade | L2 book |
| `cvd_slope` | Pendiente del CVD en últimas N velas | Python |

**Regla estricta de divergencias válidas (evitar falsos positivos):**

```python
# DIVERGENCIA BAJISTA VÁLIDA:
#   precio.high >= prev_price.high    (precio igual o mayor)
#   CVD.high < prev_CVD.high         (CVD lower-high)
# Cualquier otra combinación NO es divergencia válida.

# DIVERGENCIA ALCISTA VÁLIDA:
#   precio.low <= prev_price.low     (precio igual o menor)
#   CVD.low > prev_CVD.low          (CVD higher-low)
```

### 5.3 Detección de swings y divergencia

```python
from scipy.signal import argrelextrema
import numpy as np

class CVDDivergenceDetector:
    def __init__(self, lookback: int = 20, order: int = 3):
        self.lookback = lookback
        self.order = order  # mínimo de velas a cada lado para ser swing

    def detect(self, prices: np.ndarray,
               cvd: np.ndarray) -> dict:
        """
        Analiza los últimos `lookback` valores de precio y CVD.
        Retorna diccionario con tipo de divergencia y metadatos.
        """
        if len(prices) < self.lookback:
            return {"divergence": None}

        prices = prices[-self.lookback:]
        cvd    = cvd[-self.lookback:]

        # Encontrar swings locales
        highs_idx = argrelextrema(prices, np.greater, order=self.order)[0]
        lows_idx  = argrelextrema(prices, np.less,    order=self.order)[0]

        result = {"divergence": None, "strength": 0.0, "bars_ago": 0}

        # DIVERGENCIA BAJISTA: precio higher-high pero CVD lower-high
        if len(highs_idx) >= 2:
            last_h  = highs_idx[-1]
            prev_h  = highs_idx[-2]
            price_higher_high = prices[last_h] >= prices[prev_h]
            cvd_lower_high    = cvd[last_h]    <  cvd[prev_h]

            if price_higher_high and cvd_lower_high:
                gap_price = prices[last_h] - prices[prev_h]
                gap_cvd   = cvd[prev_h]    - cvd[last_h]  # positivo
                result = {
                    "divergence": "bearish",
                    "price_level": prices[last_h],
                    "cvd_at_high": cvd[last_h],
                    "gap_price": gap_price,
                    "gap_cvd": gap_cvd,
                    "strength": gap_cvd / max(abs(cvd).mean(), 1),
                    "bars_ago": self.lookback - 1 - last_h,
                }

        # DIVERGENCIA ALCISTA: precio lower-low pero CVD higher-low
        if len(lows_idx) >= 2:
            last_l  = lows_idx[-1]
            prev_l  = lows_idx[-2]
            price_lower_low = prices[last_l] <= prices[prev_l]
            cvd_higher_low  = cvd[last_l]    >  cvd[prev_l]

            if price_lower_low and cvd_higher_low:
                gap_price = prices[prev_l] - prices[last_l]
                gap_cvd   = cvd[last_l]    - cvd[prev_l]  # positivo
                # Solo sobreescribir si es más fuerte que la bajista
                strength = gap_cvd / max(abs(cvd).mean(), 1)
                if strength > result.get("strength", 0):
                    result = {
                        "divergence": "bullish",
                        "price_level": prices[last_l],
                        "cvd_at_low": cvd[last_l],
                        "gap_price": gap_price,
                        "gap_cvd": gap_cvd,
                        "strength": strength,
                        "bars_ago": self.lookback - 1 - last_l,
                    }

        return result
```

### 5.4 Condiciones de entrada — Setup SHORT (divergencia bajista)

```python
def should_enter_short_divergence(state: MarketState,
                                   div: dict) -> tuple[bool, str]:
    if div["divergence"] != "bearish":
        return False, ""

    conditions = {
        # Divergencia reciente (< 5 velas)
        "div_recent":        div["bars_ago"] <= 5,

        # Fuerza mínima de la divergencia
        "div_strength":      div["strength"] >= 0.3,

        # DZ actual confirma debilitamiento comprador
        "delta_weak":        state.dz < 0.5,  # delta ya no es fuerte positivo

        # OBI confirma (libro pesado en asks)
        "obi_confirm":       state.obi_ema_fast < 0.52,

        # Sesión activa
        "session":           in_active_session(),

        # Spread ok
        "spread":            state.spread_ticks <= 2,

        # Sin eventos de vol
        "no_vol_event":      state.vr <= 4.0,

        # Sin funding próximo
        "no_funding":        seconds_to_funding(state.next_funding_ts) >= 60,

        # Sin posición abierta
        "flat":              state.position is None,

        # Risk manager ok
        "risk":              risk_manager.can_trade(),
    }

    # Todos los gates deben pasar
    if not all(conditions.values()):
        failed = [k for k, v in conditions.items() if not v]
        return False, f"failed_gates: {failed}"

    return True, "divergence_short"

def get_short_entry_price(state: MarketState, div: dict) -> float:
    # Entrar con post-only en el best_bid (para ser filled como maker)
    # Si el precio está justo en el nivel de divergencia, usar ask - 1 tick
    if abs(state.current_price - div["price_level"]) <= 3 * TICK_SIZE:
        return state.best_bid  # cotizar en bid para ser maker en corto
    else:
        return state.best_bid
```

**Setup LONG (divergencia alcista)** — espejo exacto: `div["divergence"] == "bullish"`, `state.dz > -0.5`, `state.obi_ema_fast > 0.48`, entrar en `best_ask`.

### 5.5 Stop Loss, Take Profit

```python
def sl_tp_divergence(entry_px: float, side: Side,
                      div: dict, state: MarketState) -> dict:
    tick = 0.10

    if side == Side.SHORT:
        # SL: por encima del máximo de la divergencia + buffer
        sl = div["price_level"] + state.atr * 0.3
        # TP1: swing anterior (el low que viene del swing high)
        tp1 = find_prev_swing_low(state)
        # TP2: POC del rango o VWAP
        tp2 = min(state.range_poc, state.vwap)

    else:  # LONG
        sl  = div["price_level"] - state.atr * 0.3
        tp1 = find_prev_swing_high(state)
        tp2 = max(state.range_poc, state.vwap)

    rr = abs(tp1 - entry_px) / abs(entry_px - sl)
    if rr < 1.5:
        return {"valid": False, "reason": "rr_below_minimum"}

    return {
        "valid": True,
        "sl": round_to_tick(sl),
        "tp1": round_to_tick(tp1),
        "tp2": round_to_tick(tp2),
        "rr": round(rr, 2),
        "tp1_close_pct": 0.50,  # cerrar 50% en TP1
        "trail_remainder": True  # traillear el 50% restante
    }
```

### 5.6 Gestión de trailing en TP2

```python
def trail_stop(current_px: float, side: Side, entry_px: float,
               state: MarketState) -> float:
    """
    Trailing stop basado en delta y CVD para el 50% restante tras TP1.
    """
    if side == Side.SHORT:
        # Subir SL si delta se torna positivo sostenido (compradores entrando)
        if state.dz > 1.0:
            return current_px + 3 * TICK_SIZE  # SL agresivo
        # Subir SL si CVD diverge (fuerza compradora reapareciendo)
        if cvd_recovering_at_level(state):
            return current_px + 5 * TICK_SIZE
        # SL normal: 3 velas sin nuevo mínimo
        return state.bars_m1[-3].low + TICK_SIZE if len(state.bars_m1) >= 3 else current_px + 10 * TICK_SIZE

    else:  # LONG
        if state.dz < -1.0:
            return current_px - 3 * TICK_SIZE
        if cvd_weakening_at_level(state):
            return current_px - 5 * TICK_SIZE
        return state.bars_m1[-3].high - TICK_SIZE if len(state.bars_m1) >= 3 else current_px - 10 * TICK_SIZE
```

### 5.7 Red flags

```python
RED_FLAGS_S3_BEARISH = [
    # Delta re-expande positivo con fuerza DESPUÉS de la señal
    lambda s: s.dz > 2.0 and current_price_rising(s),

    # CVD hace nuevo máximo (divergencia invalidada)
    lambda s: s.cvd > max(s.cvd_history),

    # OBI se vuelca al alza fuertemente
    lambda s: s.obi_ema_fast > 0.65,

    # Evento macro de alto impacto
    lambda s: high_impact_news_in_window(minutes=10),

    # Funding payment en los próximos 60s
    lambda s: seconds_to_funding(s.next_funding_ts) < 60,

    # La divergencia era demasiado antigua (precio se movió mucho desde entonces)
    lambda s: bars_since_divergence(s) > 8,
]
```

---

## 6. Filtros de contexto compartidos

Estos filtros aplican a las **tres estrategias**. Son la primera capa de evaluación antes de calcular señales específicas.

### 6.1 Detección de régimen (rango vs tendencia)

```python
def detect_regime(state: MarketState) -> str:
    """
    Voto mayoritario de 3 señales independientes.
    Retorna "RANGE" o "TREND"
    """
    votes_trend = 0

    # Señal 1: Value Area se desplaza consistentemente
    if value_area_shifting_direction(state, bars=5):
        votes_trend += 1

    # Señal 2: Precio sostenido en un lado del VWAP (>70% de velas)
    if price_sustained_above_vwap(state, pct=0.70) or \
       price_sustained_below_vwap(state, pct=0.70):
        votes_trend += 1

    # Señal 3: Pendiente del CVD fuerte (r² > 0.7 sobre últimas 20 velas)
    if cvd_slope_significant(state, bars=20, r2_threshold=0.7):
        votes_trend += 1

    return "TREND" if votes_trend >= 2 else "RANGE"
```

> **Nota:** Las estrategias 1 (OBI maker) y 3 (divergencia) pueden operar en tendencia con cautela. La estrategia 2 (absorción) **solo opera en rango**.

### 6.2 Sesiones operativas y dead zone

```python
from datetime import datetime, timezone, time

SESSION_WINDOWS = [
    (time(7, 0), time(10, 0)),    # London
    (time(13, 0), time(17, 0)),   # New York
]

DEAD_ZONE = (time(21, 0), time(6, 0))  # Evitar: thin book, spoofing

FUNDING_TIMES_UTC = [time(0, 0), time(8, 0), time(16, 0)]  # Binance 8h
FUNDING_BUFFER_SECS = 60

def in_active_session() -> bool:
    now = datetime.now(timezone.utc).time()
    return any(start <= now <= end for start, end in SESSION_WINDOWS)

def seconds_to_funding(state: MarketState) -> int:
    now_ts = int(datetime.now(timezone.utc).timestamp())
    return max(0, state.next_funding_ts - now_ts)
```

### 6.3 Filtro de noticias macro

```python
# Integrar con Binance markPrice que incluye next funding, y con
# un calendario económico externo (Forex Factory API o similar)

HIGH_IMPACT_EVENTS = ["FOMC", "NFP", "CPI", "PPI", "GDP", "Fed Speech"]
NEWS_BUFFER_MINUTES = 10

def high_impact_news_in_window(minutes: int = 10) -> bool:
    # Consultar calendario económico cacheado (actualizar cada hora)
    upcoming = get_upcoming_events(horizon_minutes=minutes)
    return any(e["impact"] == "high" for e in upcoming)
```

### 6.4 Filtro de liquidez del libro

```python
def book_is_liquid(state: MarketState,
                   min_depth_usd: float = 5000,
                   max_spread_ticks: int = 2) -> bool:
    """
    Verifica que el libro tiene suficiente profundidad y spread sano.
    """
    # Spread
    if state.spread_ticks > max_spread_ticks:
        return False

    # Profundidad en los 5 mejores niveles
    bid_depth = sum_book_depth(state, side="bid", levels=5)
    ask_depth = sum_book_depth(state, side="ask", levels=5)
    total_depth_usd = (bid_depth + ask_depth) * state.current_price

    if total_depth_usd < min_depth_usd:
        return False

    return True
```

### 6.5 Filtro de funding

```python
def funding_context(state: MarketState) -> dict:
    """
    Evalúa el contexto de funding para sesgar dirección o pausar.
    """
    pct = state.funding_percentile  # percentil histórico 0-1

    return {
        "bias_short": pct > 0.95,    # funding extremadamente positivo = longs overcrowded
        "bias_long":  pct < 0.05,    # funding extremadamente negativo = shorts overcrowded
        "neutral":    0.05 <= pct <= 0.95,
        "flatten_now": seconds_to_funding(state) < 60,
    }
```

---

## 7. Gestión de riesgo unificada

### 7.1 Position sizing y kelly

Con $50 de capital y lote mínimo de $100 notional, no hay granularidad de sizing. La gestión de riesgo se hace principalmente a través de la **selección de trades** (score mínimo), no del tamaño de posición.

```python
class PositionSizer:
    """
    A este nivel de capital, el sizing es binario: 1 lote o no entrar.
    El foco está en la selección de trades (score), no en el tamaño.
    """

    def get_size(self, conviction_score: float) -> float:
        if conviction_score < 55:
            return 0.0   # No trade
        return 0.001     # 1 lote siempre (tamaño mínimo)

    def kelly_fraction(self, win_rate: float, avg_win: float,
                       avg_loss: float) -> float:
        """
        Fracción de Kelly teórica. NUNCA usar full Kelly.
        Con datos escasos, usar quarter-Kelly como techo.
        """
        b = avg_win / avg_loss  # odds
        p = win_rate
        q = 1 - p
        full_kelly = (b * p - q) / b
        return min(full_kelly * 0.25, 0.02)  # quarter-Kelly, cap 2%
```

### 7.2 Circuit breakers

```python
class CircuitBreaker:
    def __init__(self, capital: float):
        self.capital = capital
        self.daily_loss = 0.0
        self.daily_trades = 0
        self.consecutive_losses = 0
        self.session_start_equity = capital

    def check(self) -> tuple[bool, str]:
        """Retorna (puede_operar, razón_si_no)"""

        # Pérdida diaria > 3%
        if self.daily_loss / self.capital > 0.03:
            return False, "daily_loss_limit_3pct"

        # Más de 5 trades en la sesión
        if self.daily_trades >= 5:
            return False, "max_trades_per_session_5"

        # 3 pérdidas consecutivas
        if self.consecutive_losses >= 3:
            return False, "consecutive_losses_3"

        # Equity < 70% del capital inicial (max drawdown general)
        current_equity = self.capital - self.daily_loss
        if current_equity < self.capital * 0.70:
            return False, "max_drawdown_30pct"

        return True, ""

    def update(self, pnl: float):
        self.daily_trades += 1
        if pnl < 0:
            self.daily_loss += abs(pnl)
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
```

### 7.3 Rutina pre-sesión

```python
def pre_session_checklist(state: MarketState) -> dict:
    """
    Ejecutar al inicio de cada sesión (London 06:55 UTC, NY 12:55 UTC).
    """
    funding = funding_context(state)
    regime  = detect_regime(state)
    news    = get_upcoming_events(horizon_minutes=30)

    return {
        "regime": regime,
        "funding_bias": funding,
        "high_impact_news_30m": [e for e in news if e["impact"] == "high"],
        "book_liquid": book_is_liquid(state),
        "session_valid": in_active_session(),

        # Estrategias habilitadas para esta sesión
        "s1_enabled": True,                    # OBI maker siempre disponible
        "s2_enabled": regime == "RANGE",       # absorción solo en rango
        "s3_enabled": True,                    # divergencia en rango y tendencia

        # Bias direccional
        "direction_bias": (
            "short" if funding["bias_short"] else
            "long"  if funding["bias_long"]  else
            "neutral"
        ),
    }
```

---

## 8. Journal y métricas de validación

### 8.1 Campos por trade

```python
@dataclass
class TradeRecord:
    # Identificación
    trade_id: str
    timestamp: int
    strategy: str               # "S1_OBI", "S2_ABSORPTION", "S3_DIVERGENCE"
    session: str                # "LONDON" | "NY"
    side: str                   # "LONG" | "SHORT"

    # Contexto
    regime: str                 # "RANGE" | "TREND"
    funding_percentile: float
    session_bias: str

    # Señales al momento de entrada
    obi_at_entry: float
    dz_at_entry: float
    vr_at_entry: float
    cvd_at_entry: float
    absorption_score: float     # S2 solamente
    conviction_score: float     # S2 solamente
    divergence_strength: float  # S3 solamente
    liq_ratio: float
    oi_change_pct: float
    spread_ticks: int

    # Ejecución
    entry_price: float
    entry_type: str             # "aggressive" | "conservative" | "reclaim"
    stop_price: float
    tp1_price: float
    tp2_price: float
    rr_planned: float

    # Resultado
    exit_price: float
    exit_reason: str            # "tp1" | "tp2" | "stop" | "time_stop" | "manual" | "obi_reversal" etc
    pnl_gross: float            # antes de fees
    pnl_net: float              # después de fees
    result_r: float             # resultado en unidades de R
    duration_seconds: int

    # Metadatos
    maker_fill: bool            # fue maker o tuvo que ser taker
    slippage_ticks: int
```

### 8.2 Métricas de validación mínimas

```python
VALIDATION_THRESHOLDS = {
    # Métricas para considerar que la estrategia tiene edge real
    "min_trades_to_validate": 100,      # mínimo para estadística confiable

    # Por estrategia
    "S1_OBI": {
        "min_win_rate": 0.52,
        "min_profit_factor": 1.1,
        "max_drawdown_r": 5.0,
        "min_net_return_per_trade_pct": 0.036,  # > fee de break-even
    },
    "S2_ABSORPTION": {
        "min_win_rate": 0.53,
        "min_profit_factor": 1.2,
        "max_drawdown_r": 6.0,
        "min_rr_realized": 1.3,
    },
    "S3_DIVERGENCE": {
        "min_win_rate": 0.54,
        "min_profit_factor": 1.3,
        "max_drawdown_r": 5.0,
        "min_rr_realized": 1.5,
    },

    # Si no se cumplen después de 100+ trades → pausar y recalibrar
    "alpha_decay_alert": {
        "rolling_20_net_return_below_zero": True,
        "win_rate_rolling_20_below": 0.45,
    }
}

def compute_metrics(trades: list[TradeRecord]) -> dict:
    if len(trades) < 10:
        return {"insufficient_data": True}

    pnls = [t.pnl_net for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    return {
        "n_trades": len(trades),
        "win_rate": len(wins) / len(trades),
        "profit_factor": sum(wins) / abs(sum(losses)) if losses else float("inf"),
        "avg_win": np.mean(wins) if wins else 0,
        "avg_loss": np.mean(losses) if losses else 0,
        "avg_rr_realized": np.mean([t.result_r for t in trades]),
        "max_drawdown_r": compute_max_drawdown_r(trades),
        "sharpe": compute_sharpe(pnls),
        "maker_fill_rate": np.mean([t.maker_fill for t in trades]),
        "avg_net_return_pct": np.mean([t.pnl_net / (t.entry_price * 0.001) for t in trades]),
    }
```

---

## 9. Red flags globales

Estas condiciones **detienen cualquier actividad** de las tres estrategias:

```python
GLOBAL_RED_FLAGS = {
    "liquidation_cascade": lambda s: (
        s.liq_ratio > 8.0 and
        abs(s.current_price - s.prev_price_1m) / s.current_price > 0.005
    ),

    "extreme_volatility": lambda s: s.vr > 8.0,  # 8x volumen normal

    "book_collapse": lambda s: (
        s.spread_ticks > 5 or
        total_book_depth_usd(s, levels=3) < 2000
    ),

    "oi_collapse": lambda s: (
        s.oi_change_pct < -0.05  # OI cayó 5%+ en 6 velas = desapalancamiento masivo
    ),

    "funding_settlement": lambda s: seconds_to_funding(s) < 60,

    "high_impact_news": lambda s: high_impact_news_in_window(minutes=10),

    "outside_session": lambda s: not in_active_session(),

    "circuit_breaker": lambda s: not circuit_breaker.check()[0],

    "depegging_event": lambda s: (
        # Como el USDE depegging de Oct 2025 (bajó a $0.65)
        abs(s.usde_price - 1.0) > 0.05 if hasattr(s, "usde_price") else False
    ),
}

def any_global_red_flag(state: MarketState) -> tuple[bool, str]:
    for name, check in GLOBAL_RED_FLAGS.items():
        try:
            if check(state):
                return True, name
        except Exception:
            pass
    return False, ""
```

---

## 10. Roadmap de implementación por etapas

### Stage 0 — Backtest con datos reales (antes de arriesgar capital)

**Objetivo:** Validar que las señales tienen edge positivo neto de fees.

```bash
# 1. Descargar datos históricos de Binance vía Tardis (1-3 meses)
pip install tardis-dev
python -c "
from tardis_dev import datasets
datasets.download('binance-futures', ['incremental_book_L2', 'trades', 
                  'liquidations', 'open_interest'], 
                  ['BTCUSDT'], '2025-01-01', '2025-03-31')
"

# 2. Convertir a formato hftbacktest
# hftbacktest provee utilidades para Tardis → su formato nativo

# 3. Correr backtest con fees reales (0.018% maker Binance con BNB)
# Threshold para proceder: net return por trade > 0.036% Y Sharpe > 1
```

**Criterios para avanzar a Stage 1:**
- S1 (OBI): net return per trade > fees (>0.036%), Sharpe > 1.0
- S2 (Absorción): win rate > 53%, RR realizado > 1.3, profit factor > 1.2
- S3 (Divergencia): win rate > 52%, RR realizado > 1.5

### Stage 1 — Paper trading en testnet (2-4 semanas)

```
Exchange:      Binance Futures Testnet (testnet.binancefuture.com)
Capital:       $50 virtual
Órdenes:       Solo post-only siempre
Estrategias:   Las 3 en paralelo
Trades:        Mínimo 50 por estrategia
Métricas clave: Maker fill rate > 60%, net return per trade > 0, win rate > 52%
```

### Stage 2 — Live con capital mínimo ($50)

```
Capital:       $50 USDT mínimo
Lote:          0.001 BTC (único disponible)
Leverage:      5-10x efectivo
Estrategias:   Empezar solo con S2 (más mecánica, menos HFT-dependiente)
               Agregar S1 y S3 después de 50 trades S2 positivos
Sesiones:      Solo London + NY
Max trades:    5 por sesión
Stop diario:   3% ($1.50 en $50)
Target diario: 1.5% ($0.75 en $50)
```

### Stage 3 — Optimización y escala

Una vez con 100+ trades documentados y métricas validando:
- Agregar Estrategia 6 (funding rate) como filtro de sesión
- Agregar Estrategia 9 (OI divergence) como confirmación de S2 y S3
- Evaluar Bybit como segunda venue para arbitrage de fees
- Considerar SOL/USDT o ETH/USDT para diversificar (recalibrar todos los parámetros)
- Escalar capital gradualmente: $50 → $100 → $250 → $500 solo si métricas sostienen

### Checklist de recalibración mensual

```
□ ¿El net return per trade de S1 todavía supera las fees? (alpha decayente)
□ ¿Win rate de S2 sostenido > 53% en últimas 50 trades?
□ ¿Maker fill rate > 60%? (si baja, quotes son muy agresivos)
□ ¿Profit factor de S3 > 1.2?
□ ¿Max drawdown mensual < 10% del capital?
□ ¿Alguna estrategia tiene 20 trades negativos consecutivos? → Pausar
□ ¿Cambió la fee structure del exchange?
□ ¿Cambió el min notional del par?
```

---

## Referencias y recursos

**Código y backtesting:**
- `hftbacktest` (nkaz001): https://github.com/nkaz001/hftbacktest — OBI tutorial BTCUSDT incluido
- `tardis-dev`: https://tardis.dev — datos históricos tick-level, Binance Futures + Bybit

**Literatura académica:**
- Cont, Kukanov, Stoikov (2014) — "The Price Impact of Order Book Events" — base teórica de OBI
- Stoikov (2017) SSRN 2970694 — micro-price como predictor superior al mid-price
- Silantyev (2019) *Digital Finance* 1:191–218 — OFI en crypto (XBTUSD), R² de 40.5% a 10s
- Gould & Bonart (2015) arXiv:1512.03492 — queue imbalance predictivo

**Implementación:**
- `python-binance`: https://github.com/sammchardy/python-binance
- `pybit` (Bybit V5): https://github.com/bybit-exchange/pybit
- `ccxt`: https://github.com/ccxt/ccxt — normalización multi-exchange

**Datos en vivo:**
- Binance Futures WS docs: https://binance-docs.github.io/apidocs/futures/en/
- Bybit V5 WS docs: https://bybit-exchange.github.io/docs/v5/websocket/public/orderbook

---

*Documento generado para research y backtesting. No es consejo financiero. Validar todas las condiciones con datos propios antes de operar capital real.*
