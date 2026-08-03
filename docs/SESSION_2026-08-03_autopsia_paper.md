# Sesión 2026-08-03 — autopsia del paper tras un mes desatendido

Último commit previo: `26571f6` (14-jul). Diecinueve días sin tocar el repo, con los 6 servicios
corriendo solos. Esta sesión no agregó estrategia: fue averiguar qué pasó y arreglar lo roto.

---

## 1. Liquidity — no falló el edge, falló lo que le agregamos

| Bloque | Junio (25-30) | Julio+ |
|---|---|---|
| Todo | n=88 · +0.240 · +21.1R | n=109 · **-0.172** · **-18.7R** |
| Core (`poc_ob`/`poc_def`/`poc_def_short`) | n=87 · **+0.256** · +22.3R | n=57 · **+0.334** · **+19.0R** |
| Nuevos (`ifvg_*`/`weekly_*`/`round_*`) | n=1 | n=52 · **-0.726** · **-37.8R** |

Desglose de los nuevos en julio: `weekly_h` -13.9R (n=9), `round_l` -12.7R (n=8), `round_h`
-7.2R (n=8), `ifvg_bear` -4.3R (n=14), `ifvg_bull` -0.5R (n=10), `weekly_l` +0.8R (n=3).

Core de julio por símbolo: BTC +1.178 (n=21) / SOL +0.005 (n=19) / ETH -0.341 (n=17).

Los 5 generadores entraron entre el 30-jun (`c8539db`, weekly + round numbers) y el 2-jul, sin
regla dura en paper. El core, mientras tanto, **mejoró** respecto a junio.

IFVG merece nota aparte: venía de backtest OOS +1.97/+1.91/+1.87 — el mejor combo que teníamos —
y en paper dio -0.05 / -0.31.

**Acción:** `CORE_LEVELS_ONLY=true` por default (commit `74f2cfd`), verificado en el boot log de
prod. `CORE_LEVELS_ONLY=false` los reactiva para A/B.

---

## 2. SC3 — el SL/TP nunca se pegaba a la posición

Síntoma que se veía en la tabla: 47 de 69 filas con el exit **fuera** de `[stop, tp]` de su
propia fila, R de hasta +33.3 y -15.3 bajo un `rr_cap` de 3.0, y 4 filas compartiendo el mismo
registro de closed-PnL.

Consultando la cuenta demo aparecieron 3 posiciones vivas, las tres con `stopLoss=''` y
`takeProfit=''`:

```
BTCUSDT Sell 0.043 @ 64831     SL='' TP=''
ETHUSDT Sell 1.74  @ 1864.72   SL='' TP=''
SOLUSDT Buy  41.6  @ 75.71     SL='' TP=''   upnl -88 USD
```

El `stopLoss`/`takeProfit` que se manda en `/v5/order/create` no queda registrado en la posición.
Todo lo demás se desprende de eso:

- nada cerraba la posición en su nivel → el exit caía donde fuera
- SOL acumuló 41.6 unidades entrando varias veces sin cerrar nunca
- **sc3-btc y sc3-sol dejaron de operar el 13/14-jul**: arrastraban una posición desprotegida y
  el bot no vuelve a entrar mientras se cree con posición. El short de BTC estuvo abierto 20 días.
  Eso es lo que se veía como "servicio online que no produce trades".

Los 69 trades de julio no sirven ni para medir fill ratio.

**Acciones:**
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

## 3. Lo que esta sesión NO resolvió

**La brecha backtest ↔ paper.** Es el bloqueante real:

| | Backtest | Paper |
|---|---|---|
| WR liquidity | 62-75% | 18-28% |
| RR entry→target | — | mediana 18.5 (p90 40.7) |
| Target alcanzado | — | 4 de 109 |
| tp1 del parcial | — | 23 de 109 |
| fee | "honesto" | 0.42R por trade |
| fill ratio | asume fill exacto | 5-7% |

El edge en vivo vive de colas (MFE p90 = 8.2R), no de win rate. Mientras el backtest reporte un
WR que el paper no reproduce ni de lejos, **un OOS alto no es evidencia para desplegar** — IFVG
es la prueba.

Siguiente paso concreto: correr el backtest sobre la ventana de julio y comparar RR y motivo de
salida trade a trade contra las filas del paper. Si el backtest no reproduce RR mediana 18.5, el
binario y el motor no están operando la misma estrategia y hay que encontrar dónde divergen.

---

## 4. La causa de fondo: nadie miraba

Ninguno de los dos problemas necesitaba un mes para detectarse. Dos servicios trabados 20 días y
un mes de generadores nuevos drenando R son cosas que un chequeo semanal de 5 minutos agarra.

No hay alerta de "servicio sin trades en N días", ni de "posición abierta hace más de X horas",
ni un resumen periódico del avgR por bloque. Hasta que exista algo así, el modo de fallo por
default de este sistema es degradarse en silencio.
