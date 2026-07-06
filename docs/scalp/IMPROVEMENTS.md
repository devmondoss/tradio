# sc3 — Mejoras / Research backlog (investigación 2026-06-28)

> Qué dice la literatura de orderflow/perps sobre mejorar fades de absorción, y qué nos FALTA.
> Priorizado por (valor esperado × datos ya disponibles).

## Lo que validó la investigación (vamos bien)
- **Fade de absorción = dominio del liquidity provider.** "Esperás un spike de venta agresiva que
  NO logra bajar el precio → una entidad puso bids pasivos absorbiendo → recuperación en V." Eso ES sc3.
- **Footprint (volumen ejecutado) > bookmap (libro resting)** para absorción: el libro se spoofea,
  el volumen ejecutado no. Confirma por qué nuestro test de bookmap falló y el footprint funciona.
- **VP fade en régimen balanceado + filtro ATR/rango** = el marco correcto. Coincide con lo nuestro.

## LO QUE NOS FALTA (priorizado)

### 1. ❌ Contexto OI + FUNDING — TESTEADO, NO ayuda (`_scalp_oifund.py`)
La literatura dice leer OI+funding+liquidaciones juntos para fadear cascadas. Lo testeamos en BTC
(335 trades con cobertura OI 2025-06→2026-06): **6 formulaciones (funding nivel/z/favorable, OI
nivel/z/cambio-1h) → todas Pearson ~0** (±0.05). La tesis de "fadear contra la multitud" se
INVIRTIÓ (funding-favorable +0.41 vs contra +0.52). Ningún filtro bate el ruido.
**Por qué:** el **footprint ya captura el flush directo** (volumen+delta de rechazo en el nivel =
el momento de liquidación, a alta resolución). OI/funding es un proxy GRUESO y lagging de lo mismo
→ redundante. La info ya está en el gatillo. (Confirma el prior: orderflow context = 0 impacto.)
No vale bajar OI/funding de ETH/SOL.

### 2. ❌ Guard de CORRELACIÓN BTC para alts — TESTEADO, NO ayuda (`_scalp_btcguard.py`)
Tesis: no fadear alts cuando BTC se mueve fuerte en contra. ETH+SOL, 1089 trades: BTC alineado al
fade (15m y 1h) → **Pearson ~0** (+0.001 / −0.021). Y los filtros EMPEORAN: los trades "con BTC en
contra" tienen avgR MÁS ALTO (+0.65/+0.73), descartarlos baja OOS (Δ −0.13 a −0.02). **Tesis
invertida.** Por qué: BTC cayendo + alt pega nivel con absorción = el CLÍMAX/capitulación = la mejor
V para fadear. El footprint ya ve la defensa real; el move de BTC ya está en cómo la alt llegó ahí.

### ⇒ META-LECCIÓN: el TRIGGER de footprint es auto-suficiente
Bookmap, OI/funding, correlación BTC → todos Pearson ~0. El nivel + absorción + filtro ATR ya
encodea la info relevante a alta resolución; los contextos externos son redundantes o ruido. Es
ROBUSTO (no depende de add-ons frágiles) pero también significa: **el techo de mejora por SEÑAL ya
se alcanzó.** La frontera que queda es EJECUCIÓN (fill real), no señal → paper.

### 3. Confirmación por DELTA-FLIP (timing de entrada)
Trader Dale y las guías de footprint: **NO entrar en la barra de absorción sola — esperar a que el
delta FLIPee** a favor (barra de confirmación) antes de entrar. Nuestro maker fillea en el retest
(ya espera algo), pero no exige delta-flip. **Tesis:** exigir que la barra siguiente confirme
(delta flip / cierre a favor) → menos fades falsos, ¿mejor avgR a costa de algo de fill?

### 4. Divergencia de delta MULTI-BARRA (trigger más fuerte)
"Precio hace nuevo extremo en 3+ barras consecutivas pero el delta va al revés" = la señal más
fuerte. Hoy usamos delta de UNA barra. **Tesis:** trigger de divergencia delta acumulada 3 barras.

### 5. Bias de VALUE AREA HTF (no fadear tendencias fuertes)
Regla de la literatura: fadear los bordes solo en "balanceado" (precio DENTRO del value area
diario); arriba del VAH favorecer longs (no shorts). sc3 fadea sin mirar el contexto diario.
**Tesis:** no fadear contra un precio claramente fuera del value-area diario (tendencia fuerte).

### 6. Salida dinámica (CVD/delta-reversal exit)
sc3 usa fade fija. El edge de liquidity tiene opción de salida por reversión de CVD. **Tesis:**
salir el runner si el delta/CVD se da vuelta post-parcial (capturar más del move bueno, cortar antes los malos).

## El que NO se resuelve con research
- **Fill real / selección adversa en vivo** — solo paper/testnet. Sigue siendo el #1 desconocido absoluto.

## Plan sugerido
Empezar por (1) OI+funding y (2) guard BTC — datos listos, alto valor, alineados con cómo los pros
fadean cascadas. Después (3) delta-flip y (5) HTF bias. Validar siempre con la regla dura (IS+OOS, 3 activos).

## Fuentes
- [Trader Dale — Absorption & Delta entry confirmation](https://www.trader-dale.com/order-flow-analysis-how-to-use-absorption-delta-to-confirm-trade-entry-13th-may-25/)
- [Finowings — Footprint, delta divergence, imbalance](https://www.finowings.com/Trading/order-flow-analysis-footprint-delta)
- [Bookmap — CVD divergence strategy](https://bookmap.com/blog/how-cumulative-volume-delta-transform-your-trading-strategy)
- [XT — Bitcoin futures microstructure: liquidation cascades, funding, OI](https://medium.com/@XT_com/bitcoin-futures-market-microstructure-liquidation-cascades-funding-regimes-and-open-interest-978b107b4889)
- [XT — Liquidation cascades altcoins: anticipate & profit](https://medium.com/@XT_com/liquidation-cascades-in-altcoin-futures-trading-how-advanced-traders-anticipate-and-profit-from-946b6b84a636)
- [ForkLog — Funding rate to anticipate reversals](https://forklog.com/en/the-funding-rate-how-it-helps-anticipate-price-reversals-in-bitcoin-and-ethereum/)
- [NPFinancials — Volume Profile POC/VAH/VAL setups](https://npfinancials.com.au/volume-profile/)
- [ForexTester — Fixed Range Volume Profile / regime bias](https://forextester.com/blog/fixed-range-volume-profile/)
