# Orderflow Algorítmico Rentable en BTC/ETH Perpetuos (M5/M15): Diagnóstico, Edge Documentado y Roadmap de Implementación

## TL;DR
- El único orderflow setup con edge estadístico **académicamente documentado** sobre BTC perpetuos es el de **Order Flow Imbalance (OFI) a horizonte corto** (Cont-Kukanov-Stoikov 2014; replicado en BTC por Silantyev 2018 con R²≈40.5% a 10s y R²≈7.1% a 1s); los setups discrecionales tipo "LVN rebalance + footprint absorption" del framework orderflowmafia/dariusfxtrading carecen de evidencia publicada de win-rate y deben tratarse como hipótesis a backtestear, no como verdades.
- Para 1–3 señales/día en M5, la combinación con mayor relación señal/ruido respaldada por la literatura es: **estado de imbalance (precio fuera del Value Area previo) → reentry/rotación hacia POC anclado**, gateado por (a) VPIN/CDF<0.9 (Easley-López de Prado-O'Hara 2012) para evitar flujo tóxico, (b) ventana 13:00–16:00 UTC (Amberdata identifica 13:00 UTC como uno de los picos diarios de volatilidad BTC y Kaiko 2025 reporta que ">55% del volumen BTC-USD ahora ocurre durante horas US, vs 39% en 2020"), y (c) funding rate no en extremo direccional.
- El sistema actual falla en detectar acumulación/distribución previa porque mide *confirmación reactiva* (BOS, CHoCH, FVG) pero no mide los tres predictores documentados: **OFI acumulado multi-nivel, CVD-vs-price divergence persistente, y absorption ratio en LVN**. La prioridad de construcción debe ser un *Auction State Classifier* (Balance/Imbalance/Acumulación/Distribución) que sirva de gate común a todos los detectores existentes.

## Key Findings

### 1. Qué setups tienen edge realmente documentado

| Setup | Evidencia empírica | Métricas documentadas | Fuente |
|---|---|---|---|
| **OFI → short-horizon price** | Sí (peer-reviewed) | R² ≈ 65% (equity, 50 stocks NYSE); R²≈40.5% (BTC a 10s), R²≈7.1% (BTC a 1s) | Cont-Kukanov-Stoikov, *Journal of Financial Econometrics* 12(1):47–88 (2014); Silantyev 2018 (BitMEX XBTUSD) |
| **OFI cross-sectional crypto (daily)** | Sí (peer-reviewed) | Long–short mean 1.83% (t=2.82), alpha 1.72% (t=2.71), Sharpe 1.93 | Anastasopoulos, Gradojevic, Liu, Maynard & Tsiakas (2026), "Order flow and cryptocurrency returns," *Journal of Financial Markets* article 101047 |
| **Queue imbalance / microprice** | Sí | Predice one-tick-ahead mid-price; microprice de Stoikov supera mid-price como estimador | Stoikov 2018; Cartea, Donnelly & Jaimungal 2015 |
| **VPIN como detector de toxic flow** | Sí, pero **contestado** | Threshold canónico CDF(VPIN)=0.9; alcanzó 0.9 una hora antes del Flash Crash. **Rebuttal Andersen-Bondarenko (2014)**: el valor de VPIN una hora antes del crash "was surpassed on 71 (189) preceding days, constituting 11.7% (31.2%) of the pre-crash sample" (TR-VPIN vs BVC-VPIN). En BTC, valores 0.85+ correlacionan con crashes (Heusser 2013, MtGox abril 2013). | Easley, López de Prado & O'Hara, *Review of Financial Studies* 25(5):1457–1493 (2012); Andersen & Bondarenko, *Journal of Financial Markets* 17(1):1–46 (2014); Kitvanitphasu et al., *Research in International Business and Finance* (2026) |
| **Mean reversion intradía BTC tras movimientos grandes** | Sí | Autocorrelación negativa significativa de retornos a 1h, 2h, 4h; reversiones más fuertes tras movimientos mayores; explicado parcialmente por liquidation cascades | De Nicola, *Ledger Journal* Vol. 6 (2021) 58–80, DOI 10.5195/LEDGER.2021.213 |
| **Initial Balance breakout (RTH single-side)** | Sí, en equities/futures | Edgeful indicator page reporta "on NQ, single breaks happen 80% of the time in the New York session"; NinjaTrader/Edgeful joint analysis (Medium, ene 2025) reportó 82.17% en ventana 08/07/24–02/06/25 | Edgeful research; btcLeft IB Statistics (TradingView, 2.800+ días ES/NQ) |
| **Order imbalance Granger-causes BTC returns hasta lag 7d** | Sí (peer-reviewed) | Significancia al 5% hasta 7-day lag; sample Bitstamp 04/2013–01/2023 | "Nowcasting bitcoin's crash risk with order imbalance," *Review of Quantitative Finance and Accounting* (2023) |
| **Stacked imbalances (footprint)** | **No (sin peer-review)** | Cifras de 70–80% WR son marketing/scripts sin metodología publicada | TradingView script "whitebronco"; FuturesHive (sin source) |
| **LVN rebalance + footprint absorption** | **No (sin peer-review)** | Cero literatura académica; framework popularizado por traders discrecionales | Tradingriot, Trader-Dale, TradeZella |
| **Failed auction** | Conceptualmente derivado de AMT | Sin estadísticas publicadas; mecánicamente sólido | Steidlmayer/Dalton; Vtrender |
| **Liquidation cluster reversal** | **No directamente cuantificado** | No existe estadística publicada por CoinGlass/Kaiko/Glassnode con % de mean-reversion tras cascade; sólo evidencia indirecta vía De Nicola 2021 | — |

**Conclusión decisiva:** la mayoría de "setups de orderflow" que circulan en el espacio crypto-Instagram (incluyendo el framework de orderflowmafia/dariusfxtrading) tienen **fundamento teórico válido pero cero validación estadística publicada**. Esto no significa que no funcionen — significa que el operador debe demostrarlo en su propio backtest con la rigurosidad que la literatura exige.

### 2. Cómo definir Balance vs Imbalance algorítmicamente

La definición correcta y consensuada (Steidlmayer/Dalton + Cont-Kukanov-Stoikov) es **multi-variable**, no un solo indicador:

**Estado BALANCE:**
- Precio dentro del Value Area de la sesión previa (VAH–VAL, 70% del volumen).
- Volume Profile shape D-shaped o p/b simétricas (no skewed).
- |ΔCVD| / Volumen < umbral (e.g. 0.15) sostenido en ventana rolling.
- OFI acumulado (suma signed queue changes en 5–15 min) oscilando cerca de 0.
- VPIN/CDF < 0.7 (flow no tóxico).
- Open Interest estable (Δ%OI < ±2% por hora).

**Estado IMBALANCE:**
- Precio acepta fuera del Value Area previo (≥2 cierres M5 fuera + retest holds).
- Volume Profile skewed (POC migrando direccionalmente en >3 candles).
- OFI acumulado con sign consistente (linear relation Cont et al.).
- CVD haciendo HH/LL alineado con precio (no divergencia).
- VPIN/CDF subiendo hacia 0.7–0.9 (informed flow).
- OI subiendo en dirección del move (nuevas posiciones, no short cover).

**Estado ACUMULACIÓN/DISTRIBUCIÓN (lo que el sistema actual no detecta):**
- Precio rotando lateralmente en rango ≥ 1× ATR durante ≥ 30 min.
- CVD haciendo HH (acumulación) o LL (distribución) mientras precio plano → **divergencia CVD-precio sostenida**.
- Absorption prints: barras footprint con volumen >2σ del rolling mean pero rango < 0.5×ATR.
- OBI L5/L10 persistentemente skewed hacia el lado "smart" (compradores absorbiendo en lows, etc.).
- OI subiendo sin desplazamiento de precio (nuevas posiciones netas, no rotación).
- VPIN/CDF subiendo gradualmente sin spike (información asimétrica creciendo).

**Esto es lo que el sistema actual debe construir prioritariamente:** un *Auction State Classifier* corriendo en background que cada N segundos emita {Balance, Up-Imbalance, Down-Imbalance, Accumulation, Distribution} con un score 0–100. Sin este gate, los detectores de FVG/OB/BOS van a disparar señales en contexto equivocado.

### 3. Setups concretos con mejor relación señal/ruido para M5

Ranking pragmático combinando literatura + experiencia documentada por mesas profesionales:

**Tier A — Edge documentado o mecánicamente sólido:**

1. **OFI Mean-Reversion en M5 dentro de Balance**
   - Trigger: OFI normalizado en ventana 5 min < –2σ (presión vendedora extrema) **mientras** VPIN/CDF < 0.8 y precio cerca de VAL.
   - Entry: limit en microprice o mejor.
   - Stop: bajo VAL + 0.5×ATR(M5).
   - Target: POC.
   - Edge: linealidad OFI→price (Cont 2014); reversión intradía en BTC (De Nicola 2021).

2. **Value Area Reentry (Failed Auction) + Footprint absorption**
   - Trigger: precio rompe VAH/VAL previo, no logra ≥2 cierres M5 de aceptación, vuelve a entrar al VA con absorption visible (volumen >2σ + delta flip).
   - Entry: en re-entry con confirmación de CVD reverso.
   - Stop: más allá del extremo de la failed auction.
   - Target: POC (primer target), opuesto de VA (segundo).
   - Edge: Auction Market Theory + reversión empírica BTC (De Nicola 2021).

3. **Liquidation Sweep + CVD Divergence**
   - Trigger: spike de liquidaciones outlier (z-score >2σ rolling, no monto absoluto) en una dirección **+** CVD haciendo HL/LH opuesto al precio **+** OI cayendo (capitulación, no continuación).
   - Entry: cierre M5 reverso a la dirección de las liquidaciones.
   - Stop: extremo del wick de liquidación.
   - Target: punto de origen del move (mid-range del rally/dump previo).
   - Edge: reversión post-liquidación documentada vía autocorrelación negativa 1–4h (De Nicola 2021).

**Tier B — Mecánicamente válidos pero sin estadística pública:**

4. **LVN Rebalance + Absorption** (framework orderflowmafia)
   - Solo válido **si** el LVN está en el flanco "balanceado" del perfil (entre dos HVNs significativos).
   - Requiere: footprint con absorption (delta flip, volumen >2σ, range comprimido).
   - El edge no está documentado — tratar como hipótesis, backtestear primero.

5. **Stacked Imbalance Continuation**
   - 3+ niveles consecutivos con ratio bid/ask ≥3:1 al final de una M5 bar en dirección de la estructura mayor (BOS confirmado).
   - Solo en sesión activa, no en lunch (16:00–19:00 UTC).

**Tier C — Evitar como triggers, usar solo como contexto:**
- FVG aislados sin orderflow confirm
- Order Blocks puros sin volumen
- BOS/CHoCH sin delta confirmation

### 4. Filtros de contexto que mejoran el WR

Basado en evidencia empírica:

**Sesión (impacto alto):**
- BTC volatilidad y volumen pico en **13:00–16:00 UTC** (overlap London-NY). Amberdata blog "Trading Between Hours – Volatility Dispersion Across Multiple Regions" (datos BTC/USDT Binance 2018–oct 2023) identifica: *"Notably high volatility hours were 00:00, 12:00, 13:00, 14:00, and 17:00 UTC, with four of the five peaks being around US trading hours."* Kaiko Research, "Bitcoin Booms in Low-Risk Environment" (2025) cuantifica el shift estructural: *"As of 2025, more than 55% of all BTC-USD trading now occurs during U.S. hours, up from just 39% in 2020... A quarter of all BTC-USD volume is now clustered around the U.S. market open and close."* **Recomendación**: 80% de señales activas debe estar en 12:00–21:00 UTC.
- Evitar **22:00–04:00 UTC** (Asia thin liquidity): los setups de absorption dan más falsos positivos por low-tick statistical insignificance.

**Funding rate (impacto medio):**
- Funding > +0.05% / 8h: filtrar contra-longs (riesgo cascada).
- Funding > +0.10% / 8h: bias a shorts; "fade longs" históricamente coincide con corrections 10–30% durante períodos especulativos (Bitget research).
- Funding < –0.05%: bias a longs (short squeeze risk).
- Funding neutral (±0.01%): condiciones más limpias para mean-reversion setups.

**Open Interest (impacto medio):**
- OI ↑ + price ↑ = continuación: NO operar mean-reversion contra-trend.
- OI ↑ + price flat near resistance = short build-up → squeeze probability sube; ideal para failed auction long.
- OI ↓ + price ↓ = capitulación: ideal para reversión long (post-cascade).
- Cambio de OI >5% en 1h sin precio = acumulación/distribución silente.

**Régimen de volatilidad:**
- ATR(M5) percentil <30%: priorizar mean-reversion, evitar breakouts.
- ATR(M5) percentil >70%: priorizar momentum/continuation, evitar fades.
- VPIN/CDF >0.8 sostenido: pausar mean-reversion completamente (régimen trending, López de Prado).

**Eventos macro:** evitar la primera hora tras NFP, CPI, FOMC — la IB se distorsiona y los extension targets pierden valor.

### 5. Qué está haciendo mal el sistema actual

Del briefing: "falla en detectar distribuciones/acumulaciones previas al movimiento". Diagnóstico:

**Problema raíz:** el sistema tiene detectores de **confirmación reactiva** (BOS, CHoCH, FVG, OB) pero no tiene un **clasificador del estado del auction**. Esto causa:

1. **Señales en mid-balance**: el sistema dispara en estructuras menores dentro del Value Area, donde por definición no debe haber edge direccional (es ruido).
2. **No detecta absorción previa**: porque mide delta puntual por barra, no *absorption ratio* (volumen vs displacement) en ventana rolling.
3. **CVD divergence subutilizada**: tener CVD calculado no sirve si no se mide *persistencia* de divergencia (>N candles M5).
4. **OI sin contexto direccional**: tener OI no sirve si no se decompone en ΔOI(rising_price) vs ΔOI(falling_price) para distinguir build-up de unwind.
5. **Liquidaciones sin filtro de magnitud relativa**: las liquidaciones solo son signal cuando son outliers vs su rolling distribution (z-score).
6. **VPIN no calculado**: tener trades en tiempo real sin VPIN/CDF es subutilizar una variable que separa toxic flow de uninformed flow.
7. **Microprice no usado para entry**: con OBI L5/L10/L20 disponibles, no usar microprice (Stoikov) para entry preciso deja edge en la mesa.

### 6. Implementación algorítmica del setup "LVN rebalance" (framework orderflowmafia)

Pseudocódigo defensivo (recordando que el setup no tiene estadística pública, requiere backtest):

```
PRE-MARKET (cada inicio sesión 13:00 UTC):
  1. Build Volume Profile sesión previa → POC, VAH, VAL, HVNs, LVNs.
  2. Marcar LVNs entre dos HVNs significativos (descartar LVNs en extremos).
  3. Para cada LVN, calcular "bisagra rule": precio_actual > LVN → bias LONG; < LVN → bias SHORT.

EJECUCIÓN M5 (cada barra):
  Condición setup_LVN_long:
    a) price toca/penetra LVN desde arriba (max 0.5×ATR de penetración)
    b) footprint absorption: en M5 actual o anterior, max delta negativo en bottom 30% del bar AND total_volume > 2σ rolling mean(20)
    c) OBI L5 > +0.2 sostenido >30s al test del LVN (compradores en book)
    d) CVD M5 NO está haciendo LL (CVD ≥ low previo)
    e) Auction State NO es Down-Imbalance (es Balance o Accumulation)
    f) VPIN/CDF < 0.85
    g) Funding < +0.08% (no overcrowded long)
    h) Sesión: 12:00–21:00 UTC

  Entry: limit en microprice o LVN ± 0.25×ATR(M5)
  Stop: max(LVN low - 0.5×ATR, último wick low) — esto coincide con "below LVN with footprint confirmation"
  Target T1: POC (target principal del framework)
  Target T2: VAH (si momentum continua post-T1)
  R:R esperado mínimo: 2.0; si no llega, no operar.
```

### 7. Lo que distingue un setup rentable de uno no rentable (síntesis evidencia)

1. **Contexto > Patrón**: la misma señal (e.g. stacked imbalance) cambia de carácter radicalmente con la localización (soporte vs resistance, balance vs imbalance). La contextualización por Auction State es el filtro #1.
2. **Multi-confluencia, no indicador único**: literatura es clara — OFI solo da Sharpe pobre como signal aislado (Markwick "Order Flow Imbalance – A High Frequency Trading Signal," dm13450.github.io, feb 2022: *"the Sharpe ratio of said strategy is poor and that overall, using it as a trading signal on its own will not have you retiring to the Bahamas"*); el edge aparece con OFI + estructura + filtro de volatilidad/sesión.
3. **Confirmación de aceptación, no de break**: failed auctions tienen WR mayor que breakouts naive — Edgeful reporta que en NQ NY session, 80% son single-side breaks, así que fadear el segundo break (failed auction direccional) tiene asimetría.
4. **Targets a niveles auction-genuinos**: POC, VAH/VAL, HVNs. No múltiplos arbitrarios de ATR.
5. **Stop basado en invalidación estructural**, no en %: si la idea era "compradores defienden LVN", el stop debe estar bajo el LVN + buffer footprint, no a -0.5%.
6. **Filtro de toxicidad**: VPIN/CDF >0.9 → pausar mean-reversion (literatura López de Prado).
7. **Régimen detector**: cuando VPIN está sostenido >0.6 por horas, régimen trending; pausar fades.

## Details

### Evidencia académica destacada (con números verificables)

- **Cont, Kukanov & Stoikov (2014), *Journal of Financial Econometrics* 12(1):47-88**: OFI tiene relación lineal con price change de slope inversamente proporcional a market depth; estable cross-stock y cross-time. R² ≈ 65% promedio en sus 50 stocks NYSE. Es la pieza fundamental de cualquier sistema de orderflow algorítmico.

- **Silantyev (2018), "Order Flow Analysis of Cryptocurrency Markets," Medium**: replicación del modelo Cont-Kukanov-Stoikov sobre BitMEX XBTUSD perpetual. *"R-squared of 1-second OFI linear model fit is 7.1%. When k is set to 10 seconds, the linear model has a much better fit — R² = 40.5%. The linear relationship starts to resemble the one Cont et al (2014) observe."* **Implicación para M5 (300s)**: a horizontes largos el OFI puro pierde poder predictivo lineal; necesita combinarse con estructura.

- **Anastasopoulos, Gradojevic, Liu, Maynard & Tsiakas (2026), "Order flow and cryptocurrency returns," *Journal of Financial Markets* article 101047**: estrategia long-short cross-sectional basada en world order flow: *"the long–short mean is 1.83% (t-stat = 2.82), the alpha is 1.72% (t-stat = 2.71), and the Sharpe ratio is 1.93."* Esto es a frecuencia diaria, no M5, pero valida que OFI tiene edge persistente en crypto.

- **Easley, López de Prado & O'Hara (2012), *Review of Financial Studies* 25(5):1457-1493**: VPIN; threshold canónico CDF=0.9. CDF(VPIN) alcanzó 0.9 más de una hora antes del Flash Crash (2010-05-06).
  - **Rebuttal importante**: Andersen & Bondarenko (2014), *Journal of Financial Markets* 17(1):1-46, "VPIN and the Flash Crash": *"This value was surpassed on 71 (189) preceding days, constituting 11.7% (31.2%) of the pre-crash sample"* (TR-VPIN vs BVC-VPIN respectivamente). **Lección**: VPIN es signal de stress, no oracle.

- **Heusser (2013), "Order Flow Toxicity of the Bitcoin April Crash," jheusser.github.io**: VPIN en BTC durante el crash de abril 2013 (MtGox): *"During the 15 and 17 April VPIN is above 85% indicating high levels of toxicity — similarly to the levels of toxicity after the flash crash. As the price recovered over the following days VPIN declined to around 63%."*

- **De Nicola (2021), *Ledger Journal* Vol. 6 (2021) 58–80, DOI 10.5195/LEDGER.2021.213**: *"Our most interesting finding is the unusual presence of significant negative first-order autocorrelation of returns calculated on medium-frequency timeframes, such as one, two and four hours, signaling the presence of systematic mean reversion. It is also found that larger price movements lead to stronger reversals, in percentage terms... We explain the findings mainly through (i) investor and trader overreaction, (ii) excess volatility and (iii) cascading liquidations due to excessive use of leverage by market participants."* Este es el sustento empírico más sólido para los setups de mean-reversion intra-day en M5/M15.

- **"Nowcasting bitcoin's crash risk with order imbalance" (2023), *Review of Quantitative Finance and Accounting***: order imbalance Granger-causes returns hasta 7-day lag en BTC (sample Bitstamp 04/2013–01/2023).

- **Kitvanitphasu, Kyaw, Likitapiwat & Treepongkaruna (2026), "Bitcoin wild moves: Evidence from order flow toxicity and price jumps," *Research in International Business and Finance* vol. 81**: *"VPIN significantly predicts future price jumps, with positive serial correlation observed in both VPIN and jump size, suggesting persistent asymmetric information and momentum effects."*

- **Edgeful initial balance research**: *"on NQ, single breaks happen 80% of the time in the New York session"*; NinjaTrader/Edgeful joint analysis (Medium, ene 2025) reportó 82.17% en ventana 6-meses 08/07/24–02/06/25.

### Limitaciones honestas

- **No existe ningún backtest peer-reviewed del setup específico "LVN rebalance + footprint absorption → POC target"**. Los números de R:R 3.2/6.75/12.46 que reporta el trader de Instagram son **anécdotas cherry-picked, no estadísticas**. Asumir que el setup tiene edge sin backtestearlo en tu propia data es un error.
- Las cifras de "70–80% WR en absorption" que circulan en TradingView/marketing **carecen de metodología publicada**; tratarlas como hipótesis.
- VPIN sufre de la crítica Andersen-Bondarenko: úsalo como **gate de toxicidad**, no como trigger directo.
- El edge de OFI cae rápidamente con el horizonte: lo que funciona a 1–10s no necesariamente sobrevive a 5 min sin layers adicionales (estructura, contexto, filtros).
- No existe estadística publicada por CoinGlass/Kaiko/Glassnode con un % específico de mean-reversion tras liquidation cascades; la evidencia más sólida es indirecta vía De Nicola (2021).

## Recommendations

### Roadmap prioritizado (orden de impacto / dificultad)

**Fase 1 — Construir el Auction State Classifier (2–3 semanas, IMPACTO MÁXIMO)**

Construir un módulo que cada barra M5 (con refresh cada 30s sub-barra) emita:
```
{
  state: "balance" | "up_imbalance" | "down_imbalance" | "accumulation" | "distribution",
  confidence: 0-100,
  vars: { price_vs_VA, profile_skew, cvd_divergence_persistence, ofi_sign_persistence, vpin_cdf, oi_delta }
}
```
Esto es el **gate común** que filtrará TODOS los detectores existentes. Backtest: el WR de FVG/OB/BOS debería subir significativamente al filtrar solo cuando estado coincide.

**Fase 2 — Métricas faltantes (1–2 semanas)**

Implementar:
- **VPIN con CDF rolling** (Easley-López de Prado parametrization).
- **OFI multi-level** (no solo L1; los papers muestran ganancia monotónica hasta ~L4–L5 con saturación).
- **Absorption ratio** = volumen / |price displacement| rolling z-score.
- **CVD divergence persistence**: número de barras consecutivas con CVD direction ≠ price direction.
- **OI directional decomposition**: ΔOI cuando price↑ vs price↓ en ventana rolling.
- **Microprice** (Stoikov) para entry preciso.
- **Liquidation z-score**: liquidations vs rolling distribution, no valor absoluto.

**Fase 3 — Detectores de setup propios (3–4 semanas)**

En orden de prioridad por edge documentado:
1. **VA Reentry + Failed Auction** (más sólido teóricamente, AMT clásica).
2. **Liquidation Sweep + CVD Divergence + OI drop** (reversión post-cascade — alineado con De Nicola 2021).
3. **OFI Mean-Reversion en Balance** (literatura Cont).
4. **LVN Rebalance** (framework orderflowmafia, requiere backtest dedicado).

Cada detector debe registrar: trigger time, estado del auction classifier, filtros activos, outcome (PnL en R), context (sesión, funding, OI). Esto construye la base de datos para WR/expectancy reales.

**Fase 4 — Backtesting riguroso (continuo)**

- Mínimo 6 meses de M5 BTC perp + ETH perp.
- Walk-forward, no in-sample.
- Métricas: WR, expectancy en R, Sharpe, max DD, profit factor, distribución de R por contexto (sesión, funding bucket, regime).
- Threshold de go-live: Sharpe walk-forward >1.5, WR consistente entre regímenes, expectancy >0.4R por trade.

### Benchmarks que cambian la recomendación

- Si en backtest el LVN-rebalance no llega a WR >50% con R:R 2.0 → **descartar** el framework e ir solo con setups Tier A.
- Si Auction State Classifier no mejora el WR de detectores existentes en >10pp → revisar definición de estados.
- Si VPIN gating no reduce DD en >20% → reparametrizar (bucket size, CDF window).
- Si más del 30% de las señales caen fuera de 12:00–21:00 UTC → forzar filtro de sesión, no opcional.
- Si la Sharpe ratio del módulo OFI puro (sin layers) es <0.5 → confirma el hallazgo de Markwick (2022) y obliga a usar OFI solo como componente confluente, nunca como trigger único.

### Quick wins inmediatos (esta semana)

1. **Filtro horario duro**: rechazar todas las señales fuera de 12:00–21:00 UTC. Dada la concentración Kaiko (>55% del volumen BTC-USD en horas US) y Amberdata (picos de volatilidad en 12:00–14:00 UTC), se espera mejora inmediata de WR.
2. **Filtro funding extremo**: si |funding| > 0.08% / 8h, solo trades en dirección contraria al funding crowded (mean-reversion bias).
3. **Filtro VPIN crude**: aunque sea aproximado (bucket size = ATR diaria/50), pausar mean-reversion cuando VPIN/CDF rolling 1h > 0.85.
4. **Log de "context at signal"**: empezar a registrar para cada señal disparada: hora UTC, funding, OI delta 1h, ATR percentile, distance a POC/VAH/VAL. Sin esto no se puede aprender.

## Caveats

- **Edge en orderflow degrada con frecuencia**: el R² de OFI cae de ~40% a 10s (Silantyev) a probable <10% a 5min. M5 no es donde más limpio se ve el edge; M15 puede ser peor o mejor según ruido. Considera implementar también un layer sub-minuto que feedee al M5 (e.g., microprice para entry exacto dentro de la barra M5 que dispara la señal).
- **Crypto perpetuos tienen problemas únicos**: wash trading en algunos exchanges, fragmentación de liquidez, manipulación específica del cripto-broker. Validar OFI/CVD usando trades en venues con buen reporte (Binance, Bybit, Hyperliquid según Lehalle, Mounjid & Rosenbaum, "Fragmentation, Price Formation and Cross-Impact in Bitcoin Markets," *Applied Mathematical Finance* 2022, que reporta que su imbalance measure outperforms the classical one in 11 out of 14 cases en Bybit BTC perpetual a 500ms).
- **Los números de R:R 3.2/6.75/12.46 del trader de referencia no son métricas estadísticas** — son ejemplos cherry-picked. Cualquier framework discrecional puede mostrar trades ganadores; el edge se demuestra con expectancy positiva en cientos de trades, no con tres screenshots.
- **El framework "balance/imbalance" es válido conceptualmente pero ambiguo operativamente** — distintos traders lo implementan diferente. La implementación algorítmica debe ser explícita y testeable, no copiar verbatim un framework discrecional.
- **VPIN tiene crítica académica** (Andersen-Bondarenko 2014): no es predictor causal del Flash Crash. Úsalo como condición de pausa, no como trigger.
- **Sin VPIN/CDF nativo en tu stack, una aproximación tipo CDF rolling sobre delta absoluto / volumen sirve como proxy**, pero no es lo mismo.
- **Los HFT papers reportan R² altos a horizontes <10s donde latencia importa**: en M5 el competitor más relevante no son HFT firms, sino traders sistemáticos al mismo horizonte; el edge defendible para ti es la combinación contexto + estructura + orderflow, no la velocidad pura.
- **Past performance no es indicativo de resultados futuros** — particularmente en crypto donde el régimen cambia con cambios regulatorios, llegada/salida de market makers institucionales, y ETFs spot (el shift documentado por Kaiko de 39% → 55% volumen en horas US entre 2020 y 2025 es exactamente este tipo de régimen-change estructural que invalida backtests antiguos).