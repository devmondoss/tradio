# Autopsia del paper — 2026-08-03

Último commit previo: `26571f6` (14-jul). Diecinueve días sin tocar el repo, con los 6 servicios
corriendo solos. Esta sesión no agregó estrategia: fue averiguar qué pasó, arreglar lo roto y
medir qué sabemos realmente.

Datos: `liquidity_paper_trades` (198 filas, 25-jun → 3-ago, 0 reconstructed) y `sc3_paper_trades`
(69 filas, 3-jul → 31-jul).

**Conclusión corta:** ninguna de las dos estrategias demostró ser rentable en vivo. Liquidity ya
está en su mejor configuración registrada y aun así no pasa la regla dura. SC3 no tiene datos
válidos. El problema no es de configuración, es de evidencia.

---

## 1. Liquidity — las tres eras

El campo `filter_version` separa las configuraciones:

| era | fechas | core | nuevos |
|---|---|---|---|
| `v1_base` | 25-29 jun | +0.365 (n=82) | — |
| `v2_h1_ifvg` | 30-jun → 2-jul | -1.509 (n=7) | -1.391 (n=6) |
| `v3_fade_only` | 2-jul → 3-ago | **+0.399 (n=55)** | -0.671 (n=48) |

"Core" = `poc_ob`, `poc_def`, `poc_def_short`. "Nuevos" = `ifvg_*`, `weekly_*`, `round_*`.

### Los dos cambios de configuración fueron correctos

**Sacar el trail (1-jul, `3a8eba9`).** En `v1_base`, con el router de régimen activo:

| gestión | n | avgR | duración mediana |
|---|---|---|---|
| fade | 40 | **+1.220** | 0.8h |
| trail | 42 | **-0.450** | 1.4h |

El detector mandaba la mitad de los trades a trail y esa mitad perdía. Forzar fade subió el core
de +0.365 a +0.399.

**Apagar los generadores nuevos (3-ago, `74f2cfd`).** Dentro de `v3` conviven un core que hizo
+22.0R y 48 trades de generadores nuevos que hicieron -32.2R. El mes negativo era la suma de las
dos cosas, no una estrategia que dejó de funcionar.

### Julio, por generador

| generador | n | avgR | totalR | WR | mejor trade | grupo |
|---|---|---|---|---|---|---|
| `poc_ob` | 42 | +0.389 | +16.3 | 31% | **+29.1** | core |
| `poc_def` | 10 | +1.097 | +11.0 | 30% | **+10.9** | core |
| `weekly_l` | 3 | +0.259 | +0.8 | 33% | +3.8 | nuevo |
| `ifvg_bull` | 10 | -0.049 | -0.5 | 30% | +4.1 | nuevo |
| `ifvg_bear` | 14 | -0.307 | -4.3 | 29% | +3.1 | nuevo |
| `round_h` | 8 | -0.904 | -7.2 | 12% | +3.6 | nuevo |
| `poc_def_short` | 5 | -1.660 | -8.3 | 0% | -1.6 | core |
| `round_l` | 8 | -1.582 | -12.7 | 0% | -1.3 | nuevo |
| `weekly_h` | 9 | -1.540 | -13.9 | 0% | -1.3 | nuevo |

Todos tienen WR ~30% y todos pierden lo mismo cuando pierden (stop capeado ~-1.7R). **La
diferencia está entera en la columna del mejor trade.** El core produce colas; los nuevos no
produjeron ninguna.

En todo el período: el core hizo **13 trades de +5R o más en 144 (9%), que suman +155.7R**. Los
nuevos hicieron **0 en 53**. Si la tasa de colas fuera la misma, sacar cero en 53 trades tiene
probabilidad ≈0.7%. No es mala suerte — es que estos generadores no producen el tipo de trade
del que vive la estrategia.

Nota: `poc_def_short` es core y también se fue a -8.3R con 0% de aciertos en julio. El corte
core/nuevos no es una jerarquía moral, es dónde cayó la evidencia.

---

## 2. La corrección importante: todo esto vive de 3 trades

Durante la sesión reporté "el core hizo +19.0R en julio" como si fuera un resultado sólido.
**No lo es.**

| | total | sin su mejor trade |
|---|---|---|
| Core junio | +22.3R | **-7.1R** |
| Core julio | +19.0R | **-10.1R** |

Y la configuración actual (`v3_fade_only` + core-only), abierta por símbolo:

| | n | avgR | totalR | sin su mejor trade |
|---|---|---|---|---|
| BTC | 20 | +1.326 | +26.5 | **-2.6R** |
| ETH | 16 | -0.289 | -4.6 | **-10.8R** |
| SOL | 19 | +0.005 | +0.1 | **-9.8R** |

Solo BTC es positivo. Y si a cada símbolo le sacás su único mejor trade, **los tres quedan
negativos**. No pasa la regla dura del proyecto.

Lo que esto significa: no es que liquidity haya dejado de ser rentable y haya que revertir algo.
**En paper nunca demostró ser rentable.** La rentabilidad vive en el backtest. En vivo hay 55
trades bajo la config actual y el signo lo deciden 3 operaciones.

---

## 3. Cómo funciona realmente el sistema (anatomía del trade de +29R)

```
BTC long   entry 61936.77   stop 61843.86   target 64703.20   tp1 63118
           atr 167.61       duración 16.6h  mfe 41.2R  mae 0.939R
           exit 64703.20 (target) = +4.47% de movimiento = +29.13R
```

**Por qué el stop es minúsculo.** Para `poc_ob` long el código hace:

```rust
entry = obpoc                              // POC del order block
stop  = obl - 0.25 * STOP_SCALE * atr      // low del OB - 0.2 x ATR
```

El order block es la vela de mayor rango de las últimas 15 barras M15; `obpoc` es el precio de
mayor volumen *dentro* de esa vela y `obl` su mínimo. En este trade el POC estaba a 59 puntos del
mínimo de su propia vela (0.096%): el volumen se concentró justo abajo del todo. El stop calculado
era todavía más chico y chocó contra el piso: `61936.77 × 0.0015 = 92.905`, exactamente la
distancia entry→stop.

**El target no se predice.** `struct_target` junta cuatro niveles estructurales por encima de la
entrada (VAH, swing high, máximo del día previo, máximo semanal) y se queda con **el más lejano**.
El 64703.20 no fue un pronóstico: era el más alto de esos cuatro ese día. El `tp1` (el más
cercano) estaba a 12.7R.

**La consecuencia.** Nivel quirúrgico + target sin tope = RR 20-30 por construcción. Con RR 30
alcanza con acertar 3.3% de las veces para no perder. El sistema acierta ~20%. El edge es
aritmético, no predictivo — y por eso un mes bueno y uno malo se deciden por si cayó una cola
dentro de la ventana.

### El tamaño del stop no explica el WR

Hipótesis descartada en la sesión: creí que el piso de 0.15% estaba activo en casi todos los
trades. Falso — mediana real 0.273%, solo 13% pegados al piso.

| stop (% del precio) | n | avgR | WR |
|---|---|---|---|
| 0.15% (piso) | 25 | +0.514 | 20% |
| 0.15-0.25% | 65 | +0.261 | 20% |
| 0.25-0.50% | 52 | -0.414 | 15% |
| >0.50% | 55 | -0.108 | 27% |

**El WR es ~20% en todos los buckets.** Achicar o agrandar el stop no lo mueve. El problema no
está en dónde se pone el stop.

### Poner un target "realista" destruye el edge

Simulación sobre el MFE real de cada trade (si el precio llegó a X R, habría salido ahí), core n=144:

| target | avgR | WR |
|---|---|---|
| **estructural (actual)** | **+0.287** | 22% |
| cap 1R | -0.051 | **55%** |
| cap 2R | -0.100 | 38% |
| cap 3R | -0.175 | 28% |
| cap 5R | -0.088 | 25% |
| cap 8R | +0.074 | 23% |
| cap 10R | +0.192 | 23% |
| cap 15R | +0.355 | 22% |

Todo target razonable pierde plata. Y el cap a 1R da **55% de aciertos** y resultado negativo —
la trampa clásica de optimizar por win rate.

El cap a 15R supera al estructural en el agregado, pero no pasa la regla dura: BTC empeora
(+0.805 → +0.694), SOL mejora (+0.329 → +0.557), ETH sigue negativo. Con **12 observaciones de
cola** no hay nada que concluir.

### Los ganadores devuelven mucho

De los 12 trades que tocaron 10R+:

```
BTC  mfe 41.2R -> cerró +29.13R  (target)
BTC  mfe 31.4R -> cerró +29.41R  (target)
ETH  mfe 25.0R -> cerró  +8.84R  (trail)
SOL  mfe 24.5R -> cerró +18.88R  (timeout)
BTC  mfe 20.5R -> cerró  +6.31R  (trail)
BTC  mfe 19.7R -> cerró  +5.02R  (timeout)
BTC  mfe 17.4R -> cerró +11.04R  (target)
SOL  mfe 17.0R -> cerró  +1.52R  (breakeven)
SOL  mfe 14.7R -> cerró  +9.90R  (target)
BTC  mfe 14.1R -> cerró +10.94R  (timeout)
ETH  mfe 13.1R -> cerró  +3.94R  (breakeven)
SOL  mfe 11.5R -> cerró  -1.35R  (stop)
```

Uno llegó a +11.5R y cerró perdiendo. Es una avenida abierta, pero no accionable con n=12.

---

## 4. SC3 — el SL/TP nunca se pegaba a la posición

Síntoma en la tabla: **47 de 69 filas con el exit fuera de `[stop, tp]`** de su propia fila, R de
hasta +33.3 y -15.3 bajo un `rr_cap` de 3.0, y 4 filas compartiendo el mismo registro de
closed-PnL.

Consultando la cuenta demo aparecieron 3 posiciones vivas, las tres con `stopLoss=''` y
`takeProfit=''`:

```
BTCUSDT Sell 0.043 @ 64831     SL='' TP=''
ETHUSDT Sell 1.74  @ 1864.72   SL='' TP=''
SOLUSDT Buy  41.6  @ 75.71     SL='' TP=''   upnl -88 USD
```

El `stopLoss`/`takeProfit` mandado en `/v5/order/create` no queda registrado en la posición. Todo
lo demás se desprende de eso:

- nada cerraba la posición en su nivel — cerraba cuando el bot flipeaba, a cualquier precio
- SOL acumuló 41.6 unidades entrando varias veces sin cerrar nunca
- **sc3-btc y sc3-sol dejaron de operar el 13/14-jul**: arrastraban una posición desprotegida y el
  bot no reentra mientras se cree con posición. El short de BTC estuvo abierto 20 días. Eso es lo
  que se veía como "servicio Online que no produce trades"

**Ningún número de SC3 paper anterior al 2026-08-03 significa nada.** No hay una era buena y una
mala: hay 69 filas inservibles, que no alcanzan ni para medir fill ratio. No existe configuración
a la cual revertir porque nunca se midió ninguna. La única opción es acumular serie limpia.

### Fixes desplegados

- `27a027f` — el closed-PnL se matchea por ventana temporal (`updatedTime >= placed_ts - 60s`) +
  proximidad de `avgEntryPrice` al fill real, en vez de `items[0]`. La ventana sola no alcanza: el
  cierre de la posición anterior puede caer después de nuestro `placed_ts` (1 de 8 casos en el
  replay contra la cuenta demo). Sin match no se escribe fila. `reason` deja de ser
  "target si R>0" y sale de dónde cayó el exit.
- `3cb4958` — `ensure_tpsl()`: `/v5/position/trading-stop` después del fill y **relectura** de la
  posición para confirmar que quedó; si no, loguea `POSICIÓN DESPROTEGIDA`. También en el
  reconcile de boot, que es donde se arrastraba el caso de BTC.
- Las 3 posiciones se cerraron a mercado y se limpió `sc3_open_pos` antes del deploy. Los
  servicios con el código viejo alcanzaron a registrar ese cierre como 2 trades (ids 70/71):
  borrados, la tabla quedó en 69 filas.

---

## 5. La brecha backtest ↔ paper (sin cerrar — bloquea todo)

| | Backtest | Paper |
|---|---|---|
| WR liquidity | 62-75% | 18-28% |
| RR entry→target | — | mediana **18.5** (p10 5.1, p90 40.7) |
| RR hasta tp1 | — | mediana 5.4 |
| Target alcanzado | — | **4 de 109** |
| tp1 alcanzado | — | 23 de 109 |
| MFE | — | mediana 1.36R, p75 3.64R, p90 8.22R |
| fee | "honesto" | **0.42R por trade** (mediana) |
| fill ratio | asume fill exacto | **5-7%** (~26 de ~340 colocadas) |

IFVG es el caso testigo: backtest OOS +1.97/+1.91/+1.87 — el mejor combo que teníamos — y en
paper -0.049 / -0.307. **Mientras la brecha siga abierta, un OOS alto no es evidencia para
desplegar nada.**

### Hipótesis principal: selección adversa en el fill

Una orden límite en un nivel solo se llena si el precio llega y lo **atraviesa**. Si el nivel
aguanta —el caso bueno, el que la estrategia busca— el precio rebota antes y la orden nunca entra.
Con 5-7% de fill ratio estaríamos entrando sistemáticamente en las señales donde el nivel falló.
Eso explicaría el WR de ~20% constante en todos los buckets de stop.

El backtest, en cambio, asume que si la vela toca el nivel, entrás. Nunca paga ese peaje.

**Es una hipótesis, no un hecho. Test concreto:** tomar las señales que el binario colocó en julio
(`liquidity_paper_events`, `event_type='place'`), separar las que llenaron de las que no, y correr
el backtest sobre las **no llenadas**. Si esas son las ganadoras, queda demostrado y el problema
deja de ser de estrategia o de parámetros.

---

## 6. Lección de medición: el +1.94 de junio nunca existió

En junio se registró un avgR de **+1.94 (n=84)** para el paper limpio. El mismo período hoy da
**+0.240**. El número no se reproduce con ningún campo de la tabla (`result_r` +0.240,
`realized_r` +0.270). El mecanismo:

```
fade  (los ganadores):  duración mediana 0.8h
trail (los perdedores): duración mediana 1.4h

primeros 40 trades cerrados de v1: avgR +0.781
los 42 siguientes:                 avgR -0.032
```

Los ganadores cierran rápido, los perdedores tardan casi el doble. **Cualquier medición hecha con
el libro en vuelo sobre-muestrea ganadores rápidos** y da un número inflado que después se desinfla
solo.

**Regla para adelante:** no reportar avgR de una cohorte que todavía tiene posiciones abiertas.
Medir sobre cohortes cerradas completas, o al menos reportar cuántas quedan abiertas.

---

## 7. La causa de fondo: nadie miraba

Ninguno de los dos problemas necesitaba un mes para detectarse. Dos servicios trabados 20 días y
un mes de generadores nuevos drenando R son cosas que un chequeo semanal de 5 minutos agarra.

No hay alerta de "servicio sin trades en N días", ni de "posición abierta hace más de X horas",
ni un resumen periódico del avgR por bloque. Hasta que exista algo así, el modo de fallo por
default de este sistema es degradarse en silencio.

---

## 8. Estado al cierre y qué sigue

Los 6 servicios en `3cb4958`, cuenta demo flat, `sc3_open_pos` vacía.

| | Config | Estado |
|---|---|---|
| Liquidity | `v3_fade_only` + `CORE_LEVELS_ONLY=true` | Mejor combo registrado (+0.399), no pasa regla dura |
| SC3 | rr_cap 3.0 + HTF canónico, SL/TP verificado | Serie limpia arranca 2026-08-03 |

Lo que falta no es configuración — las dos estrategias están en su mejor versión conocida. Falta
evidencia:

1. **Muestra.** 55 trades con 3 colas no deciden nada. A 1-2 trades/día son meses.
2. **Saber si las colas existen en vivo** con la frecuencia que el backtest asume.

Prioridad 1: el test de selección adversa de la §5. Es el único que puede invalidar (o rescatar)
todo el resto del trabajo.
