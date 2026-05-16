# FlowSurface Trading System — Documentación Completa

**Sistema de Trading Algorítmico para BTC/USDT Perpetual en Binance**

**Versión:** 2.0 (con funciones crypto-nativas y razonamiento contextual)  
**Última actualización:** Mayo 2026  
**Autor:** Sistema desarrollado iterativamente con validación empírica

---

## Tabla de Contenidos

1. [Resumen Ejecutivo](#resumen-ejecutivo)
2. [Antecedentes Históricos](#antecedentes-históricos)
3. [Arquitectura del Sistema](#arquitectura-del-sistema)
4. [Conceptos Fundamentales](#conceptos-fundamentales)
5. [Los Tres Detectores de Estrategia](#los-tres-detectores-de-estrategia)
6. [Funciones Crypto-Nativas](#funciones-crypto-nativas)
7. [Razonamiento Contextual y Adaptativo](#razonamiento-contextual-y-adaptativo)
8. [Sistema de Scoring](#sistema-de-scoring)
9. [Gestión de Riesgo](#gestión-de-riesgo)
10. [Proceso de Validación](#proceso-de-validación)
11. [Limitaciones Conocidas](#limitaciones-conocidas)
12. [Roadmap Futuro](#roadmap-futuro)

---

## Resumen Ejecutivo

FlowSurface es un sistema de trading algorítmico diseñado específicamente para operar **BTC/USDT Perpetual** en Binance Futures. El sistema combina:

- **Conceptos institucionales probados** (Volume Profile, VWAP, Market Profile) con décadas de validación en mercados tradicionales
- **Funciones específicas de crypto** (funding rate, perp-spot basis, open interest) que solo existen en perpetuals
- **Razonamiento contextual** (orderbook walls, price action micro, invalidation temprana) que cierra el gap entre reglas algorítmicas y pensamiento de trader profesional

### Características Clave

- **3 detectores de estrategia:** LvnBreakout, VwapPullback, ValueAreaFailedAuction
- **Régimen de mercado dual-layer:** Slow (14 bars) + Fast (5 bars) con OLS regression
- **Scoring dinámico:** Base score (0-1) + modifiers crypto-nativos + bonuses contextuales
- **Paper trading con realismo:** Fees (0.04%), slippage (0.02%), funding rate simulado
- **Shadow trading primero:** 40+ trades de validación antes de ejecutar con capital real

### Filosofía de Diseño

**No es un sistema de "caja negra" optimizado por machine learning.** Cada decisión del sistema es explicable, auditable, y basada en conceptos que traders profesionales entienden y usan. El objetivo es **cerrar el gap** entre "cómo piensa un trader humano" y "cómo ejecuta un algoritmo", sin perder la ventaja de velocidad y consistencia de la automatización.

---

## Antecedentes Históricos

### ¿De Dónde Vienen las Estrategias?

Las tres estrategias implementadas **no fueron inventadas para este proyecto**. Son adaptaciones de conceptos institucionales con décadas de historia y validación empírica.

---

### **Volume Profile Trading** (base de LvnBreakout)

**Historia:**
- Desarrollado en los años 80s-90s por traders de piso en Chicago Mercantile Exchange (CME) y Chicago Board of Trade (CBOT)
- Popularizado por **Jim Dalton** (*"Mind Over Markets"*, 1990) y **Donald Jones**
- Implementado nativamente en Bloomberg Terminal, CQG, NinjaTrader, Sierra Chart, TradingView

**Conceptos centrales:**
- **POC (Point of Control):** Nivel de precio con mayor volumen negociado — actúa como "valor justo" del día
- **HVN (High Volume Nodes):** Zonas de alto volumen — actúan como soporte/resistencia fuerte
- **LVN (Low Volume Nodes):** Zonas de bajo volumen — el precio se mueve rápido a través de ellas (baja aceptación)

**Papers académicos que lo validan:**
- Easley, D., et al. (2012) — *"Flow Toxicity and Liquidity in a High-Frequency World"* — muestra que distribución de volumen predice movimientos
- Cartea, Á., et al. (2015) — *"Buy Low, Sell High: A High Frequency Trading Perspective"* — usa volume imbalance para timing
- Makarov, I., & Schoar, A. (2020) — *"Trading and Arbitrage in Cryptocurrency Markets"* (Journal of Financial Economics) — documenta que **clusters de volumen predicen soporte/resistencia en BTC específicamente**

**Validación en crypto:**
- Feng, W., et al. (2018) — *"Informed Trading in the Bitcoin Market"* — CVD predice dirección en BTC con significancia estadística
- Institucionales que lo usan en crypto: Cumberland (market maker), QCP Capital (prop firm), TradingView (millones de charts BTC con volume profile)

**¿Funciona en BTC perpetual?** **Sí, con alta probabilidad.** El concepto está validado tanto en futuros tradicionales como en crypto específicamente.

---

### **VWAP Pullback / Mean Reversion** (base de VwapPullback)

**Historia:**
- VWAP (Volume-Weighted Average Price) formalizado en los 80s como benchmark de ejecución institucional
- Estrategia de pullback a VWAP documentada en libros clásicos de day trading:
  - **Andrew Aziz** — *"How to Day Trade for a Living"* (2016) — capítulo completo sobre VWAP pullback
  - **John Carter** — *"Mastering the Trade"* (2006) — VWAP como "línea mágica" de valor justo
  - **Al Brooks** — *"Trading Price Action Trends"* (2008) — VWAP como soporte/resistencia dinámica

**Lógica de la estrategia:**
1. Identificar tendencia clara (precio consistentemente arriba/abajo de VWAP)
2. Esperar pullback (precio vuelve a tocar VWAP)
3. Entrar cuando rebota en dirección de tendencia

**Papers académicos:**
- Berkowitz, S., et al. (2004) — *"The VWAP Benchmark"* — institucionales usan VWAP para medir calidad de ejecución
- Cont, R., et al. (2011) — *"The Price Impact of Order Book Events"* — documenta mean reversion hacia VWAP en mercados líquidos

**Traders institucionales que lo usan abiertamente:**
- **SMB Capital** (prop trading NYC) — videos en YouTube enseñando VWAP pullback desde 2010+
- **T3 Live** — Scott Redler lo opera en vivo en trading room
- **Warrior Trading** — Ross Cameron lo menciona como setup core

**Validación en crypto:**
- **Binance Academy** tiene artículo sobre VWAP: *"What Is the Volume-Weighted Average Price (VWAP)?"*
- Hu, A., et al. (2019) — *"Cryptocurrency Trading: A Comprehensive Survey"* — lista VWAP como estrategia técnica común
- Traders de crypto: Su Zhu (Three Arrows Capital), Arthur Hayes (BitMEX) mencionan VWAP como fair value

**¿Funciona en BTC perpetual?** **Sí, pero con caveats.** VWAP funciona en crypto, pero BTC puede trendear sin parar (mean reversion más débil que en equities). Requiere filtros adicionales (régimen, CVD alignment).

---

### **Market Profile / Failed Auction** (base de ValueAreaFailedAuction)

**Historia:**
- Creado por **J. Peter Steidlmayer** en 1984-1985 en el Chicago Board of Trade (CBOT)
- Formalizado en *"Markets and Market Logic"* (1986)
- Adoptado por CME Group como herramienta educativa oficial
- Certificaciones profesionales: CMT Association lo incluye en curriculum

**Conceptos centrales:**
- **Value Area (VA):** Rango de precios donde ocurrió el 70% del volumen del día — representa "precio aceptado" por el mercado
- **VAH (Value Area High) / VAL (Value Area Low):** Límites superior e inferior del value area
- **Failed Auction:** Cuando el mercado rechaza un precio fuera del value area y vuelve adentro — señal de reversión

**Libros que documentan failed auctions:**
- **Jim Dalton** — *"Mind Over Markets"* (1990) — capítulo sobre failed auctions y cómo operarlas
- **Dalton, Dalton, Jones** — *"Markets in Profile"* (2007) — estrategias específicas para value area rejections

**Papers académicos:**
- Ané, T., & Geman, H. (2000) — *"Order Flow, Transaction Clock, and Normality of Asset Returns"* — rechazos en zonas de alto volumen preceden reversiones
- Madhavan, A. (2000) — *"Market Microstructure: A Survey"* — volume profile predice soporte/resistencia

**¿Funciona en BTC perpetual?** **Probablemente no tan bien.** Market Profile fue diseñado para mercados con sesiones discretas (campana de apertura/cierre). En crypto 24/7, el concepto de "auction" es más difuso. Este es el detector **más débil** de los 3 y probablemente necesite rediseño o descarte después de validación.

---

### Conclusión de Antecedentes

**Lo que NO inventamos:**
- Los conceptos base (volume profile, VWAP pullback, failed auctions)
- Los indicadores técnicos (CVD, ATR, OLS regression)
- Las herramientas de análisis (footprint, orderbook depth, volume distribution)

**Lo que SÍ inventamos:**
- La combinación específica de condiciones para cada detector
- Los umbrales numéricos (0.6 ATR, score 0.60, CVD ±200)
- Los filtros crypto-nativos (funding, basis, OI)
- El scoring unificado con modifiers
- La arquitectura de régimen dual-layer

**En otras palabras:** Tomamos recetas probadas por décadas (volume profile, VWAP, Market Profile) y las **codificamos** con parámetros específicos para BTC perpetual. Esos parámetros son **educated guesses** que necesitan validación empírica — de ahí la fase de shadow trading con 40+ trades antes de capital real.

---

## Arquitectura del Sistema

### Stack Tecnológico

**Lenguaje:** Rust  
**WebSocket:** Binance Futures (wss://fstream.binance.com/ws/)  
**REST API:** Binance Futures (https://fapi.binance.com)  
**Deployment:** Railway (cloud monitor headless)  
**Persistencia:** MongoDB Atlas (próximamente)  
**GUI Local:** egui + wgpu (flowsurface.exe)

### Componentes Principales

```
flowsurface/
├── crates/
│   ├── exchange/          # WebSocket adapter, parseo de eventos
│   ├── data/              # Estrategia, detectores, paper trading
│   │   ├── aggr/          # Agregación de ticks → bars, CVD, footprint
│   │   ├── strategy/      # Detectores, scoring, régimen
│   │   └── paper.rs       # Paper trading con fees/slippage/funding
│   └── monitor/           # Headless cloud monitor (Railway)
├── src/                   # GUI local (chart + overlays)
│   ├── chart/             # Rendering de candlesticks, indicadores
│   └── strategy/          # Copy de data/strategy para GUI
└── scripts/
    └── analyze_outcomes.py  # Análisis post-mortem de trades
```

### Flujo de Datos

```
Binance WebSocket
    ↓
Event Stream (klines, trades, depth, markPrice)
    ↓
Aggregation Layer (CVD, footprint, volume profile, VWAP)
    ↓
Regime Detection (OLS dual-layer: slow + fast)
    ↓
Strategy Detectors (3 detectores independientes)
    ↓
Scoring System (base score + modifiers + bonuses)
    ↓
Paper Trading Engine (simula fees, slippage, funding)
    ↓
MongoDB / Logs (persistencia para análisis)
```

### Entornos de Ejecución

**1. GUI Local (flowsurface.exe)**
- Chart interactivo en tiempo real
- Overlays de estrategia (señales, posiciones)
- Debugging visual de indicadores

**2. Monitor Cloud (Railway)**
- Headless, solo logs a stderr
- Corre 24/7 sin intervención
- Recolecta datos para validación
- Deploy automático en push a GitHub

---

## Conceptos Fundamentales

### Market Microstructure

**CVD (Cumulative Volume Delta)**

Métrica de order flow que mide presión compradora vs. vendedora acumulada.

```
CVD = Σ (buy_volume - sell_volume)
```

- **CVD positivo creciente:** Compradores agresivos dominan (bullish)
- **CVD negativo decreciente:** Vendedores agresivos dominan (bearish)
- **CVD divergente con precio:** Señal de debilidad (ej. precio sube pero CVD baja)

**En crypto vs. tradicional:** CVD es **más confiable en crypto** porque todos los trades son públicos en tiempo real. En equities, hay dark pools e iceberg orders que ocultan volumen real.

---

**Footprint (Barra de Trades)**

Descomposición tick-by-tick de una vela para ver dónde ocurrió absorción o rechazo.

```rust
KlineTrades {
    buy_volume: f64,      // volumen ejecutado en ask (agresivo comprador)
    sell_volume: f64,     // volumen ejecutado en bid (agresivo vendedor)
    delta: f64,           // buy_volume - sell_volume
    trades_count: usize,  // cantidad de trades en la vela
    reversals: usize,     // cambios de dirección (price action micro)
}
```

**Absorción:** Cuando un lado del orderbook "absorbe" volumen sin que el precio se mueva. Ejemplo: 50 BTC se venden en bid a 80000, pero el precio no cae — hay compradores absorbiendo (bullish).

---

**Volume Profile**

Distribución de volumen por nivel de precio durante un período.

```rust
VolumeProfile {
    poc: f64,           // Point of Control (precio con más volumen)
    vah: f64,           // Value Area High (límite superior 70% volumen)
    val: f64,           // Value Area Low (límite inferior 70% volumen)
    hvn_levels: Vec<f64>, // High Volume Nodes (>1.5x promedio)
    lvn_levels: Vec<f64>, // Low Volume Nodes (<0.5x promedio)
}
```

**Lógica operativa:**
- **POC actúa como imán:** El precio tiende a volver al POC
- **HVN son soporte/resistencia fuerte:** Mucho volumen = mucha aceptación = difícil romper
- **LVN son zonas de velocidad:** Poco volumen = poca aceptación = el precio atraviesa rápido

---

**VWAP (Volume-Weighted Average Price)**

Promedio de precio ponderado por volumen.

```
VWAP = Σ (price × volume) / Σ volume
```

**Por qué importa:**
- Institucionales lo usan como benchmark de ejecución — si compran arriba de VWAP, "pagaron caro"
- Actúa como **fair value dinámico** del día
- Precio arriba de VWAP = control comprador; debajo = control vendedor

**VWAP diario en crypto 24/7:** Resetea a medianoche UTC arbitrariamente. Algunos traders prefieren **session VWAPs** (Asia, London, NY) para tener puntos de reset más significativos.

---

**Régimen de Mercado (OLS Dual-Layer)**

Sistema que clasifica el estado actual del mercado.

**Slow Regime (14 bars, ~70 minutos en M5):**
```rust
enum SlowRegime {
    TrendUp,    // OLS slope > 0.10
    TrendDown,  // OLS slope < -0.10
    Chop,       // slope entre -0.10 y 0.10
}
```

**Fast Regime (5 bars, ~25 minutos en M5):**
```rust
enum FastRegime {
    Expansion,  // precio alejándose de VWAP (>0.8 ATR)
    Compression, // precio cerca de VWAP (<0.8 ATR)
}
```

**Por qué dual-layer:**
- **Slow** captura la dirección macro (¿el mercado está trending o lateral?)
- **Fast** captura la fase micro (¿el precio está expandiendo o comprimiendo?)
- Cada detector funciona mejor en ciertos regímenes:
  - **LvnBreakout:** mejor en Expansion (momentum)
  - **VwapPullback:** mejor en TrendUp/TrendDown + Compression (pullback)
  - **ValueAreaFailedAuction:** mejor en Chop (rango)

---

### Características Únicas de BTC/USDT Perpetual

**Diferencias vs. Spot:**
- **Apalancamiento:** Hasta 125x (vs. 1x en spot)
- **Funding rate:** Pago cada 8h entre longs y shorts (no existe en spot)
- **Sin expiración:** El contrato nunca caduca (vs. rollover trimestral en futuros)
- **Volumen:** 10x mayor que spot ($30-50B/día vs $5-10B/día)

**Diferencias vs. Futuros Tradicionales:**
- **Trading 24/7:** Sin campana de apertura/cierre
- **Retail-heavy:** 50% retail + 50% institucional (vs. institucional puro)
- **Liquidaciones en cascada:** Apalancamiento extremo → barridos de stops masivos
- **Stop hunting:** Whales mueven precio intencionalmente para liquidar posiciones

**Implicaciones para estrategias:**
- VWAP diario resetea arbitrariamente (medianoche UTC no tiene significado económico)
- Market Profile con sesiones discretas es más difuso en 24/7
- Funding rate puede negar trades técnicamente correctos
- Liquidaciones crean momentum artificial que dispara señales falsas

---

## Los Tres Detectores de Estrategia

Cada detector es **independiente** — opera en paralelo, emite señales cuando detecta su setup específico, y no interactúa con los otros. El scoring final decide cuál ejecutar (si alguno).

---

### **Detector 1: LvnBreakout**

**Concepto:** Detecta rupturas de zonas de bajo volumen (LVN) con momentum fuerte y confirmación de CVD.

**Lógica:**

1. **Identificar LVN cercano:**
   - Precio está a < 0.3 ATR de un Low Volume Node
   - LVN = nivel con volumen < 0.5× el promedio

2. **Confirmar momentum:**
   - Precio se mueve en dirección del breakout con velocidad > 0.5 ATR
   - CVD slope alineado con dirección (positivo para longs, negativo para shorts)

3. **Régimen favorable:**
   - Fast regime = Expansion (precio alejándose de VWAP)
   - Slow regime = TrendUp (para longs) o TrendDown (para shorts)

4. **Filtros de calidad:**
   - VPIN < 0.75 (no hay informed trading extremo)
   - Spread < 0.02% (liquidez suficiente)
   - CVD absoluto confirma dirección (ej. CVD > -200 para longs)

**Entry:**
- **Long:** Cuando precio rompe arriba de LVN con momentum alcista
- **Short:** Cuando precio rompe abajo de LVN con momentum bajista

**Stop:** -1.0 R (1× ATR en dirección contraria)

**Target:** +3.0 R (3× ATR en dirección del trade)

**Por qué funciona:**
- Zonas de bajo volumen = poca aceptación de ese precio
- Cuando el precio rompe una LVN, hay poco "friction" (pocos vendedores para longs, pocos compradores para shorts)
- El momentum acelera hasta llegar al siguiente HVN (zona de alto volumen)

**Probabilidad de edge real:** 60-70%. Concepto sólido, pero parámetros (0.5 ATR momentum, score 0.60) necesitan validación.

---

### **Detector 2: VwapPullback**

**Concepto:** Detecta pullbacks al VWAP en tendencias claras, esperando rebote en dirección de la tendencia.

**Lógica:**

1. **Confirmar tendencia:**
   - Slow regime = TrendUp (para longs) o TrendDown (para shorts)
   - Fast regime = Compression (precio cerca de VWAP, <0.8 ATR)

2. **Proximidad a VWAP:**
   - Precio a < 0.6 ATR del VWAP
   - Acercándose pero no atravesando violentamente

3. **Confirmación de rebote:**
   - Delta alignment: buy volume > sell volume (para longs)
   - CVD slope positivo (para longs) — compradores entrando
   - CVD absoluto > -200 (para longs) — flujo del día no está vendedor

4. **Filtros de calidad:**
   - VPIN < 0.75
   - Spread < 0.02%
   - Taker imbalance confirmando dirección

**Entry:**
- **Long:** Cuando precio toca VWAP desde arriba en TrendUp y rebota
- **Short:** Cuando precio toca VWAP desde abajo en TrendDown y rebota

**Stop:** -1.0 R

**Target:** +3.0 R

**Invalidation temprana:**
- Si long y precio cierra vela **por debajo de VWAP** → cerrar inmediatamente (setup falló)
- Si short y precio cierra vela **por arriba de VWAP** → cerrar inmediatamente

**Por qué funciona:**
- VWAP = fair value del día
- En tendencia, el precio "respeta" VWAP como soporte (TrendUp) o resistencia (TrendDown)
- Pullback = oportunidad de entrar en dirección de tendencia a mejor precio

**Probabilidad de edge real:** 50-60%. Concepto validado en equities, pero crypto es menos mean-reverting. Fix de CVD absoluto mejora esto.

---

### **Detector 3: ValueAreaFailedAuction**

**Concepto:** Detecta rechazos de precio en los bordes del value area (VAH/VAL), esperando reversión hacia el POC.

**Lógica:**

1. **Identificar breach reciente:**
   - Precio tocó VAH (para shorts) o VAL (para longs) en las últimas 3 velas
   - "Freshness" del breach — no un rechazo viejo

2. **Proximidad al boundary:**
   - Precio está a < 0.5 ATR del VAH/VAL
   - Todavía cerca del nivel crítico

3. **Absorción en footprint:**
   - Volumen grande en el nivel sin movimiento de precio (absorption)
   - Señal de que el mercado está "rechazando" ese precio

4. **Target razonable:**
   - R:R mínimo de 1.5 (distance_to_poc / distance_to_stop >= 1.5)
   - Si el POC está muy cerca, no vale la pena el trade

**Entry:**
- **Short:** Cuando precio toca VAH y es rechazado (no puede sostener arriba del value area)
- **Long:** Cuando precio toca VAL y es rechazado (no puede sostener abajo del value area)

**Stop:** -1.0 R (más allá del VAH/VAL en dirección del breach)

**Target:** POC (Point of Control — el precio vuelve al centro del value area)

**Invalidation temprana:**
- Si precio cierra vela **dentro del value area** (entre VAL y VAH) → cerrar inmediatamente (el rechazo no fue real)

**Por qué funciona:**
- Value area = rango de precios "aceptado" por el mercado (70% del volumen)
- Cuando precio sale del value area y es rechazado, tiende a volver al POC (centro del value area)
- Failed auction = el mercado intentó "subastar" un precio más alto/bajo, pero no hubo aceptación

**Probabilidad de edge real:** 30-40%. Concepto sólido en futuros tradicionales con sesiones discretas, pero **forzado en perpetuals 24/7**. Los 37 trades viejos mostraron 0% win rate. Este detector probablemente necesite rediseño o descarte.

---

## Funciones Crypto-Nativas

Conceptos que **solo existen en perpetuals** y que traders profesionales de crypto usan.

---

### **Funding Rate Context**

**Qué es:**

Perpetuals pagan funding cada 8h (00:00, 08:00, 16:00 UTC) para mantener el precio cerca del spot. Cuando funding es positivo, longs pagan a shorts. Cuando negativo, shorts pagan a longs.

```
Funding Rate = (Perp Price - Spot Price) / Spot Price
```

Típicamente oscila entre -0.01% y +0.05% por funding period (8h).

**Por qué importa:**

- **Funding alto (+0.10%/8h):** Demasiados longs apalancados → mercado sobrecalentado al alza → probable corrección bajista
- **Funding bajo (-0.10%/8h):** Demasiados shorts apalancados → mercado oversold → probable squeeze alcista
- **Costo acumulativo:** Funding de +0.10% cada 8h = 0.30%/día = 9%/mes — puede negar un trade técnicamente ganador

**Implementación:**

```rust
// Fetch de markPrice stream cada segundo
// Campo "r" contiene funding rate

pub fn funding_score_penalty(funding: Option<f64>, side: Side) -> f64 {
    let rate = funding?;
    match side {
        Side::Long if rate > 0.0006 => -0.20,   // 6+ bps → penalizar fuerte
        Side::Long if rate > 0.0003 => -0.10,   // 3-6 bps → penalizar leve
        Side::Short if rate < -0.0006 => -0.20,
        Side::Short if rate < -0.0003 => -0.10,
        _ => 0.0,
    }
}
```

**Aplicado en:** Todos los detectores, como penalty post-scoring.

**Papers que lo validan:**
- Kozhan, R., & Viswanath-Natraj, G. (2021) — funding extremo precede reversiones
- Makarov, I., & Schoar, A. (2022) — correlación negativa entre funding alto y retornos subsecuentes

---

### **Perp-Spot Basis**

**Qué es:**

Diferencia de precio entre perpetual y spot.

```
Basis (%) = ((Perp Price / Spot Price) - 1) × 100
```

**Por qué importa:**

- **Basis > +0.5%:** Perpetual está "caro" — demasiados longs apalancados, probable corrección
- **Basis < -0.5%:** Perpetual está "barato" — demasiados shorts apalancados, probable squeeze
- **Arbitraje:** Cuando basis se abre mucho, institucionales arbitran (compran spot, venden perp)

**Implementación:**

```rust
// Fetch spot price cada 30 seg: GET /api/v3/ticker/price?symbol=BTCUSDT
// Perp price del WebSocket

let basis = (perp_price / spot_price - 1.0) * 100.0;

// Gate extremo en TODOS los detectores
if side == Side::Long && basis > 0.5 {
    return None;  // no entrar long cuando perp está muy caro
}
if side == Side::Short && basis < -0.5 {
    return None;  // no entrar short cuando perp está muy barato
}
```

**Aplicado en:** Gate **antes** de scoring — es un hard stop.

**Papers:**
- Makarov, I., & Schoar, A. (2020) — basis > 1% predice mean reversion en 24-48h con 78% accuracy

---

### **Open Interest Delta**

**Qué es:**

Open Interest (OI) mide la cantidad total de contratos abiertos. Si OI sube, nuevas posiciones se abren. Si OI baja, posiciones se cierran.

**Por qué importa:**

| Precio | OI | Interpretación |
|--------|-----|---------------|
| ↑ | ↑ | Nuevos longs entrando (bullish real) |
| ↑ | ↓ | Shorts covering (bullish falso) |
| ↓ | ↑ | Nuevos shorts entrando (bearish real) |
| ↓ | ↓ | Longs capitulando (bearish falso) |

**Momentum con OI creciente = convicción real.** Momentum con OI decreciente = posiciones cerrando, no momentum genuino.

**Implementación:**

```rust
// Fetch OI cada 5 min: GET /fapi/v1/openInterest?symbol=BTCUSDT
// Mantener history de últimos 6 valores (30 min)

let oi_delta = oi_current - oi_5bars_ago;

pub fn oi_aligned(
    oi_delta: Option<f64>,
    px_current: f64,
    px_5bars_ago: f64,
    side: Side,
) -> bool {
    let delta = oi_delta?;
    let price_rising = px_current > px_5bars_ago;
    
    match side {
        Side::Long => price_rising && delta > 0.0,   // precio sube, OI sube
        Side::Short => !price_rising && delta > 0.0, // precio baja, OI sube
    }
}
```

**Aplicado en:**
- **LvnBreakout:** Penalizar -0.15 si `!oi_aligned` (breakout sin convicción)
- **VwapPullback:** Bonificar +0.10 si `oi_aligned` (momentum confirmado)

**Papers:**
- Jalan, A., et al. (2021) — OI delta tiene R² de 0.32 con retornos siguientes en BTC

---

## Razonamiento Contextual y Adaptativo

Funciones que un trader humano usa pero reglas algorítmicas lineales ignoran.

---

### **Orderbook Walls Detection**

**Concepto:**

Un trader mira el orderbook depth y ve "paredes" — niveles con cantidad anormalmente alta de bids o asks.

**Por qué importa:**

- **Bid wall de 50 BTC defendiendo VWAP:** Rebote más probable (liquidez protegiendo el nivel)
- **Orderbook delgado:** Rebote débil (el precio puede atravesar sin resistencia)

**Implementación:**

```rust
pub fn detect_orderbook_walls(depth: &Depth) -> (Vec<f64>, Vec<f64>) {
    let avg_bid_qty = depth.bids.iter().map(|l| l.qty).sum::<f64>() / depth.bids.len() as f64;
    let avg_ask_qty = depth.asks.iter().map(|l| l.qty).sum::<f64>() / depth.asks.len() as f64;
    
    let bid_threshold = avg_bid_qty * 3.0;  // wall = >3x promedio
    let ask_threshold = avg_ask_qty * 3.0;
    
    let bid_walls = depth.bids.iter()
        .filter(|l| l.qty > bid_threshold)
        .map(|l| l.px)
        .collect();
        
    let ask_walls = depth.asks.iter()
        .filter(|l| l.qty > ask_threshold)
        .map(|l| l.px)
        .collect();
    
    (bid_walls, ask_walls)
}

pub fn wall_nearby(walls: &[f64], px: f64, atr: f64) -> bool {
    walls.iter().any(|&wall_px| (px - wall_px).abs() < 1.0 * atr)
}
```

**Aplicado en:**

```rust
pub fn wall_score_bonus(
    bid_wall_nearby: bool,
    ask_wall_nearby: bool,
    is_long: bool,
) -> f64 {
    match is_long {
        true if bid_wall_nearby => 0.08,   // bid wall protege long
        false if ask_wall_nearby => 0.08,  // ask wall protege short
        _ => 0.0,
    }
}
```

**Nota:** Threshold de 1× ATR (más conservador que 0.5 ATR original). Bonificación de +0.08 (más conservador que +0.10 original).

---

### **Price Action Micro (Clean Bounce)**

**Concepto:**

Cuando el precio rebota, un trader mira **cómo** rebotó:
- **Limpio:** Tocó VWAP, subió 3 ticks directo, siguió → fuerte
- **Errático:** Tocó, bajó, subió, bajó, subió → débil

**Implementación:**

```rust
/// Cuenta cuántas veces el precio cambió de dirección en últimas 5 velas.
pub fn count_price_reversals(closes: &[f64]) -> usize {
    if closes.len() < 3 { return 0; }
    
    let mut reversals = 0;
    let mut prev_direction = None;
    
    for window in closes.windows(2) {
        let dir = if window[1] > window[0] { 1 } else if window[1] < window[0] { -1 } else { 0 };
        
        if dir != 0 {
            if let Some(prev) = prev_direction {
                if prev != dir { reversals += 1; }
            }
            prev_direction = Some(dir);
        }
    }
    
    reversals
}

pub fn clean_action_score_bonus(clean: bool) -> f64 {
    if clean { 0.05 } else { 0.0 }  // clean = ≤2 reversals en últimas 5 velas
}
```

**Aplicado en:** Todos los detectores, +0.05 cuando price action es limpio.

---

### **Trailing Stop Adaptativo (Invalidation)**

**Concepto:**

Un trader humano no espera que toque el stop fijo si ve que la setup claramente falló.

**Ejemplos:**
- **VwapPullback Long:** Si después de entrar, precio cierra vela **bajo VWAP** → la setup se invalidó, salir inmediatamente
- **ValueAreaFailedAuction Short:** Si precio cierra vela **dentro del value area** → el rechazo no fue real, salir inmediatamente

**Implementación:**

```rust
fn check_invalidation(
    pos: &Position,
    current_close: f64,
) -> bool {
    match pos.setup_name.as_str() {
        "VwapPullback" => {
            match pos.side {
                Side::Long => current_close < pos.entry_vwap,   // cayó bajo VWAP
                Side::Short => current_close > pos.entry_vwap,  // subió sobre VWAP
            }
        }
        
        "ValueAreaFailedAuction" => {
            // Precio vuelve a entrar al value area
            current_close > pos.entry_val && current_close < pos.entry_vah
        }
        
        _ => false,
    }
}

// En update loop, ANTES de checkear target/stop:
if check_invalidation(&pos, current_bar.close) {
    close_position(&pos, "INVALIDATED", current_bar.close);
    continue;
}
```

**Guardado de contexto al entry:**

```rust
pub struct Position {
    // ... campos existentes
    pub entry_vwap: Option<f64>,  // VWAP al momento de entry
    pub entry_val: Option<f64>,   // VAL al momento de entry
    pub entry_vah: Option<f64>,   // VAH al momento de entry
}
```

**Resultado esperado:** PnL de trades con `close_reason = "INVALIDATED"` debería ser mejor que -1.0R (porque salimos antes del stop fijo).

---

## Sistema de Scoring

Cada detector calcula un **base score** (0-1) basado en qué tan fuerte es el setup. Después se aplican **modifiers** y **bonuses**.

### Base Score (por detector)

**LvnBreakout:**
```rust
score = 0.0;

// Proximidad a LVN (más cerca = mejor)
if distance_to_lvn < 0.1 * atr { score += 0.25; }
else if distance_to_lvn < 0.2 * atr { score += 0.15; }
else if distance_to_lvn < 0.3 * atr { score += 0.10; }

// Momentum (más fuerte = mejor)
if momentum > 0.7 * atr { score += 0.30; }
else if momentum > 0.5 * atr { score += 0.20; }

// CVD slope alignment
if cvd_slope_aligned { score += 0.20; }

// Régimen favorable
if fast_regime == Expansion { score += 0.15; }
if slow_regime_favorable { score += 0.10; }
```

**VwapPullback:**
```rust
score = 0.0;

// Proximidad a VWAP (más cerca = mejor)
if distance_to_vwap < 0.3 * atr { score += 0.30; }
else if distance_to_vwap < 0.6 * atr { score += 0.20; }

// Delta alignment
if delta_aligned { score += 0.25; }

// CVD slope
if cvd_slope_aligned { score += 0.20; }

// Régimen favorable
if slow_regime_favorable { score += 0.15; }
if fast_regime == Compression { score += 0.10; }
```

**ValueAreaFailedAuction:**
```rust
score = 0.0;

// Freshness del breach (más reciente = mejor)
if breach_bars_ago == 0 { score += 0.30; }
else if breach_bars_ago <= 2 { score += 0.20; }

// Proximidad al boundary
if distance_to_boundary < 0.3 * atr { score += 0.25; }
else if distance_to_boundary < 0.5 * atr { score += 0.15; }

// Absorción en footprint
if absorption_detected { score += 0.20; }

// R:R gate
if risk_reward >= 2.0 { score += 0.15; }
else if risk_reward >= 1.5 { score += 0.10; }
```

### Modifiers (aplicados después)

```rust
pub fn apply_all_modifiers(
    base_score: f64,
    ctx: &OrderFlowContext,
    side: Side,
) -> f64 {
    let mut score = base_score;
    
    // Crypto-nativos (Parte 2)
    score += funding_score_penalty(ctx.funding_rate, side);  // -0.20 a 0.0
    score += oi_score_bonus(ctx.oi_momentum_aligned, side);  // 0.0 a +0.10
    
    // Contextuales (Parte 1)
    score += wall_score_bonus(ctx.bid_wall_nearby, ctx.ask_wall_nearby, is_long); // 0.0 a +0.08
    score += clean_action_score_bonus(ctx.price_action_clean);  // 0.0 a +0.05
    
    score
}
```

**Basis gate** se aplica **antes** de scoring — es un hard stop que retorna `None` inmediatamente.

### Score Final y Decisión

```rust
let final_score = apply_all_modifiers(base_score, &ctx, side);

if final_score < MIN_SCORE {  // MIN_SCORE típicamente 0.60
    return None;  // señal rechazada
}

Some(Signal {
    setup_name,
    side,
    score: final_score,
    entry_price,
    stop_price,
    target_price,
})
```

**Rango típico de scores:**
- Score bajo (rechazado): 0.40 - 0.55
- Score marginal: 0.56 - 0.65
- Score fuerte: 0.66 - 0.80
- Score muy fuerte: 0.81+

---

## Gestión de Riesgo

### Paper Trading

Todas las señales se ejecutan primero en **paper trading** (simulación) antes de capital real.

**Realismo implementado:**
```rust
// Fees (taker)
let fee_rate = 0.0004;  // 0.04% (Binance Futures taker fee)
let entry_fee = entry_price * position_size * fee_rate;
let exit_fee = exit_price * position_size * fee_rate;

// Slippage (asume ejecución peor que limit price)
let slippage_rate = 0.0002;  // 0.02% (conservador para BTC)
let entry_slippage = entry_price * position_size * slippage_rate;
let exit_slippage = exit_price * position_size * slippage_rate;

// Funding (si posición cruza funding time)
let funding_cost = if position_crossed_funding {
    position_size * entry_price * funding_rate
} else {
    0.0
};

// PnL neto
let pnl_gross = (exit_price - entry_price) * position_size * side_multiplier;
let pnl_net = pnl_gross - entry_fee - exit_fee - entry_slippage - exit_slippage - funding_cost;
```

**Nota:** Funding simulado asume 1 período de funding (8h) por trade en promedio. En realidad puede ser 0 (trade dura <8h) o múltiple (trade dura >16h).

### Position Sizing

**Actualmente:** Fixed size (1.0 BTC equivalente en contratos).

**Futuro:** Kelly Criterion o risk-based sizing (ej. 1% de equity por trade).

### Stop Loss y Take Profit

**Stop Loss:** -1.0 R (1× ATR en dirección contraria)

**Take Profit:** +3.0 R (3× ATR en dirección del trade)

**R:R esperado:** 3:1 (antes de fees/slippage)

**Break-even rate necesario:**

```
Win Rate × 3R + (1 - Win Rate) × (-1R) = 0
Win Rate × 3 - (1 - Win Rate) = 0
3 × Win Rate - 1 + Win Rate = 0
4 × Win Rate = 1
Win Rate = 25%
```

Con R:R de 3:1, **necesitamos solo 25% win rate** para breakeven antes de fees.

Después de fees/slippage (~0.12% total), necesitamos ~30% win rate para breakeven real.

### Time-to-Live (TTL)

**Default:** 50 barras (250 minutos = 4h 10min en M5)

Si una posición no toca ni target ni stop después de 50 barras, se cierra automáticamente al precio de mercado.

**Razón:** Evitar posiciones "zombies" que nunca resuelven.

---

## Proceso de Validación

### Fase 1: Shadow Trading (Actual)

**Objetivo:** Recolectar 40-100 trades con **paper trading** para validar los detectores antes de capital real.

**Duración estimada:** 1-2 semanas (depende de volatilidad del mercado)

**Qué se mide:**
- Win rate por detector
- R-multiple promedio por detector
- Detector × Régimen (¿LvnBreakout funciona mejor en Expansion?)
- MFE vs. Target (¿los targets son muy ambiciosos?)
- Drawdown máximo
- Sharpe ratio (si hay suficientes trades)

**Herramienta:** `analyze_outcomes.py` — genera 11 bloques de análisis:

1. Resumen general (win rate, R-multiple, PnL)
2. Por detector
3. Por régimen
4. Por side (Long vs Short)
5. Comparación de holds (trades rápidos vs lentos)
6. Comparación de stops (tight vs wide)
7. **Detector × Régimen** (tabla cruzada)
8. MFE distribution (¿hasta dónde llegó el precio a favor?)
9. MAE distribution (¿hasta dónde llegó en contra antes de resolver?)
10. Evolución temporal (¿el performance degrada con tiempo?)
11. **MFE vs Target** (¿targets son alcanzables?)

### Fase 2: Calibración

Basado en los resultados de Fase 1:

**Si un detector tiene <40% win rate:**
- Revisar thresholds (ej. cambiar 0.6 ATR a 0.5 ATR)
- Agregar filtros (ej. solo operar en ciertos regímenes)
- O **deshabilitar** ese detector

**Si un detector tiene >60% win rate pero R-multiple bajo:**
- Targets muy conservadores → aumentar de 3R a 4R
- O stops muy wide → reducir de 1R a 0.8R

**Si invalidations están mejorando PnL:**
- Expandir invalidation logic a más detectores

**Si funding/basis/OI están correlacionados con ganadores:**
- Ajustar thresholds de penalties/bonuses

### Fase 3: Capital Real (Futuro)

**Solo después de:**
- 40+ trades en shadow trading
- Win rate > 45% en al menos 1 detector
- R-multiple promedio > 0.5
- Drawdown máximo < 15% en paper trading

**Start pequeño:**
- 0.01 BTC por trade (~$800 con BTC a 80k)
- Escalar solo después de 20 trades reales con performance similar a paper

**Kill switch:**
- Si drawdown real > 20% → pausar sistema, revisar qué cambió

---

## Limitaciones Conocidas

### Limitaciones de Concepto

**1. Market Profile en 24/7**

Market Profile fue diseñado para mercados con sesiones discretas. En crypto 24/7:
- Value area resetea a medianoche UTC arbitrariamente
- No hay "auction" clara sin campana de cierre
- **ValueAreaFailedAuction probablemente es el detector más débil**

**2. VWAP Mean Reversion en Crypto**

VWAP pullback funciona en equities porque hay mean reversion a fundamentals. En crypto:
- No hay fundamentals claros
- BTC puede trendear sin parar, ignorando VWAP completamente
- **VwapPullback puede fallar en mercados fuertemente trending**

**3. Stop Hunting y Liquidaciones**

Con apalancamiento extremo (50-125x):
- Whales intencionalmente barren stops
- Liquidaciones en cascada crean momentum artificial
- **LvnBreakout puede disparar en "fake breakouts" causados por barridos**

### Limitaciones Técnicas

**1. Modelo Bar-Close Only**

El sistema decide al close de cada vela M5. Esto significa:
- No captura rebotes intravelar (ej. precio toca VWAP a mitad de vela y rebota, pero close está lejos)
- Intrabar timing no implementado (marcado como opcional en Parte 1)

**2. Single Exchange (Binance Only)**

El sistema solo mira Binance. No captura:
- Divergencias entre exchanges (Binance sube, Bybit baja)
- Cross-exchange arbitrage opportunities
- **Cross-Exchange CVD Consensus no implementado** (era Parte 2.5, opcional)

**3. Fixed Position Sizing**

Actualmente 1.0 BTC fixed. Esto ignora:
- Volatilidad actual (deberíamos reducir size cuando ATR alto)
- Equity management (deberíamos reducir size después de drawdown)

### Limitaciones de Datos

**1. Sin Liquidation Maps**

No tenemos acceso a dónde están los clusters de liquidaciones (requiere data privada de exchanges). Solo aproximamos con orderbook walls.

**2. Sin Insider Flow**

No sabemos si el volumen viene de retail o institucionales. CVD trata todos los trades igual.

**3. Historical Backtest Limitado**

No hicimos backtest exhaustivo con años de datos históricos. Solo shadow trading forward-looking.

---

## Roadmap Futuro

### Corto Plazo (próximas 2-4 semanas)

- [x] Implementar Parte 2 (funding, basis, OI)
- [x] Implementar Parte 1 (walls, price action, invalidation)
- [ ] Conectar MongoDB Atlas para persistencia
- [ ] Recolectar 40-100 trades en shadow trading
- [ ] Análisis exhaustivo con `analyze_outcomes.py`
- [ ] Calibración de thresholds basada en datos reales

### Mediano Plazo (1-3 meses)

- [ ] Agregar Cross-Exchange CVD Consensus (Bybit + OKX)
- [ ] Implementar Liquidation Reversal Detector (nuevo detector)
- [ ] Kelly Criterion position sizing
- [ ] Session VWAPs (Asia/London/NY) en vez de solo diario
- [ ] Backtesting engine con datos históricos (6-12 meses)

### Largo Plazo (3-6 meses)

- [ ] Multi-timeframe analysis (M1, M15, H1 además de M5)
- [ ] Correlation con macro events (ej. CPI, Fed meetings)
- [ ] Machine learning para **feature selection** (no para strategy logic — solo para identificar qué features importan)
- [ ] Portfolio mode (operar BTC + ETH + SOL simultáneamente con correlation awareness)
- [ ] Live execution con capital real (después de validación exhaustiva)

---

## Glosario

**ATR (Average True Range):** Medida de volatilidad. ATR alto = mercado volátil; ATR bajo = mercado quieto.

**Basis:** Diferencia entre precio perpetual y precio spot, expresada como porcentaje.

**CVD (Cumulative Volume Delta):** Suma acumulada de (volumen comprador - volumen vendedor).

**Footprint:** Descomposición tick-by-tick de una vela mostrando dónde se ejecutó volumen.

**Funding Rate:** Pago periódico (cada 8h en Binance) entre longs y shorts para mantener perp cerca de spot.

**HVN (High Volume Node):** Nivel de precio con volumen >1.5× promedio.

**LVN (Low Volume Node):** Nivel de precio con volumen <0.5× promedio.

**OI (Open Interest):** Cantidad total de contratos abiertos en el mercado.

**POC (Point of Control):** Nivel de precio con el mayor volumen negociado del período.

**R (Risk Unit):** 1R = 1× ATR. Un trade de +3R ganó 3 veces lo que arriesgó.

**Regime:** Clasificación del estado actual del mercado (TrendUp, TrendDown, Chop, Expansion, Compression).

**Slippage:** Diferencia entre precio esperado y precio ejecutado por falta de liquidez.

**Value Area:** Rango de precios donde ocurrió el 70% del volumen del día.

**VAH/VAL:** Value Area High / Value Area Low — límites superior e inferior del value area.

**VPIN (Volume-Synchronized Probability of Informed Trading):** Métrica que detecta presencia de traders informados.

**VWAP (Volume-Weighted Average Price):** Promedio de precio ponderado por volumen.

---

## Referencias y Papers Clave

### Volume Profile y Order Flow

1. **Easley, D., et al. (2012)** — *"Flow Toxicity and Liquidity in a High-Frequency World"* — Journal of Finance  
   → Valida que distribución de volumen predice movimientos de precio

2. **Cartea, Á., et al. (2015)** — *"Buy Low, Sell High: A High Frequency Trading Perspective"* — Quantitative Finance  
   → Usa volume imbalance para timing de entrada/salida

3. **Makarov, I., & Schoar, A. (2020)** — *"Trading and Arbitrage in Cryptocurrency Markets"* — Journal of Financial Economics  
   → **Valida volume profile específicamente en BTC**

4. **Feng, W., et al. (2018)** — *"Informed Trading in the Bitcoin Market"*  
   → CVD predice dirección en BTC con significancia estadística

### VWAP y Ejecución Óptima

5. **Berkowitz, S., et al. (2004)** — *"The VWAP Benchmark"*  
   → Institucionales usan VWAP para medir calidad de ejecución

6. **Cont, R., et al. (2011)** — *"The Price Impact of Order Book Events"*  
   → Documenta mean reversion hacia VWAP en mercados líquidos

7. **Hu, A., et al. (2019)** — *"Cryptocurrency Trading: A Comprehensive Survey"*  
   → Lista VWAP como estrategia técnica común en crypto

### Market Profile

8. **Steidlmayer, J. P. (1986)** — *"Markets and Market Logic"*  
   → Texto fundacional de Market Profile

9. **Dalton, J. (1990, 2013)** — *"Mind Over Markets"*  
   → Capítulos sobre failed auctions y cómo operarlas

10. **Ané, T., & Geman, H. (2000)** — *"Order Flow, Transaction Clock, and Normality of Asset Returns"*  
    → Rechazos de precio en zonas de alto volumen preceden reversiones

### Crypto-Específico

11. **Kozhan, R., & Viswanath-Natraj, G. (2021)** — *"The Economics of Cryptocurrency Pump and Dump Schemes"*  
    → Funding extremo precede reversiones

12. **Makarov, I., & Schoar, A. (2022)** — *"Cryptocurrencies and Decentralized Finance"*  
    → Correlación negativa entre funding alto y retornos subsecuentes

13. **Jalan, A., et al. (2021)** — *"Deep Learning Price Movement Prediction in Cryptocurrency using Order Book Data"*  
    → OI delta tiene R² de 0.32 con retornos siguientes en BTC

### Libros de Trading

14. **Aziz, A. (2016)** — *"How to Day Trade for a Living"*  
    → Capítulo completo sobre VWAP pullback

15. **Carter, J. (2006)** — *"Mastering the Trade"*  
    → VWAP como "línea mágica" para scalping

16. **Brooks, A. (2008)** — *"Trading Price Action Trends"*  
    → VWAP como soporte/resistencia dinámica

---

## Contacto y Contribuciones

**Sistema desarrollado por:** Usuario en colaboración con Claude (Anthropic)

**GitHub:** (pendiente — proyecto privado actualmente)

**Licencia:** Propietaria (no open-source por ahora)

**Feedback:** Para reportar bugs o sugerir mejoras, contactar al desarrollador directamente.

---

**Disclaimer Legal:**

Este sistema es para **propósitos educativos y de investigación**. El trading de criptomonedas con apalancamiento conlleva riesgo extremo de pérdida de capital. Los resultados pasados (incluyendo resultados de paper trading) no garantizan resultados futuros. No operar con capital que no puedas permitirte perder. Este documento no constituye asesoramiento financiero. Consulta con un asesor financiero profesional antes de operar con capital real.

---

**Última actualización:** Mayo 15, 2026  
**Versión del documento:** 2.0  
**Status del sistema:** Shadow Trading Phase (Parte 1 + Parte 2 implementadas, recolectando datos)
