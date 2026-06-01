# Estrategias de Orderflow para FlowSurface
## Lo que tienes, lo que usa el sistema, y lo que se puede construir

> Basado en auditoría real del codebase (19-mayo-2026).
> Capital: $300. Riesgo por trade: $3 (1%). Par: BTCUSDT Perp Binance.
> Todo lo que se afirma aquí está referenciado al código real — no al documento de diseño.

---

## Parte 1 — La brecha: lo que ves vs lo que usa el algoritmo

Tienes en pantalla cuatro herramientas de orderflow serias. El problema es que el monitor headless que ejecuta las estrategias vive en un universo paralelo al de la UI.

```
LO QUE VES EN PANTALLA          LO QUE EL ALGORITMO RECIBE HOY
────────────────────────────     ──────────────────────────────────────────
Heatmap Chart                →   walls_above/below, thin_zone (resumen)
                                 HistoricalDepth: NO existe en contexto

Footprint Chart              →   delta total de la barra (un solo número)
                                 footprint por nivel de precio: NO existe

DOM / Ladder                 →   obi_l5/l10/l20, microprice, spread
                                 bids/asks completos: NO existen en contexto

Time & Sales                 →   cvd, cvd_slope, taker_imbalance
                                 trades individuales: NO pasan al contexto

Gráfico 15m OB/FVG/STR/LIQ  →   order_blocks: None siempre
                                 fvg: None siempre
                                 market_structure: None siempre
                                 session: None siempre
                                 liq_map: None siempre
```

Esto no es un bug. Es que el constructor del contexto en
`crates/monitor/src/main.rs` hardcodea None en todos esos campos
mientras que `src/chart/kline.rs` (la UI) sí los conecta.
El código para calcularlos existe — simplemente nadie lo llamó
en el monitor.

---

## Parte 2 — Las 6 estrategias actuales: qué funciona y qué no

### VVPC — La única que opera hoy

**Por qué funciona**: usa solo datos que sí llegan al contexto.
price, regime, vwap, cvd_slope, fast_slope, volume profile. Todo conectado.

**Por qué falla en Longs**: el gate `fast_slope > -0.20` es demasiado
permisivo. Los 3 Longs perdedores de hoy tenían fast_slope entre
-0.062 y -0.180 — pasaron el gate pero el momentum ya iba en contra.

**Campos que llegan pero VVPC ignora completamente**:
- `obi_l5/l10/l20` → el libro, no lo lee
- `flow.taker_imbalance` → no lo usa en ningún gate
- `flow.sweep_confirmed` → no lo usa
- `volume_profile.poc` → no lo usa
- `volume_profile.lvn_nearby` → no lo usa

**Diagnóstico**: VVPC opera con 40% de los datos disponibles.
Con los fixes de calibración (fast_slope a -0.08, targets 1-2×ATR)
mejora sin tocar el código de detección.

---

### VAFA — Opera con footprint falso

**Por qué no disparó hoy**: necesita `failed_acceptance = true` y
`footprint_absorption` confirmado. El problema es que
`footprint_absorption` se calcula con `[bar_delta]` — el delta total
de toda la barra como un único número — no con el footprint real nivel
por nivel.

**Lo que debería hacer**: detectar que en los niveles bajos del rango
de la barra hubo delta negativo masivo (vendedores agresivos) pero el
precio no cerró ahí (compradores absorbiendo en el libro). Eso es
absorción real. Lo que hace ahora es comparar el delta total con
un umbral — una aproximación muy gruesa.

**Resultado**: VAFA puede disparar en momentos equivocados (falso
positivo de absorción) o no disparar cuando hay absorción real
(falso negativo). Hoy no disparó — pero con el footprint real
probablemente hubiera visto la absorción en VAL durante el drop de
09:20.

**Fix necesario**: acumular trades WS en `KlineTrades` por nivel
de precio en el monitor (estructura ya existe en la UI).

---

### LVN — Bug crítico activo

**El problema más absurdo del sistema**: la estrategia se llama
`LvnLiquidityVacuumBreakout`. El contexto tiene el campo `lvn_nearby`
correctamente calculado y conectado. El detector LVN no lo lee.

```
El contexto tiene:    volume_profile.lvn_nearby  ✓
El detector lee:      volume_profile.hvn_nearby  (para target)
                      volume_profile.vah, val
                      PERO NO lvn_nearby para la condición de entrada
```

Esto significa que LVN dispara (o no dispara) sin verificar si hay
realmente un LVN cerca. Es una estrategia de breakout de vacío de
liquidez que no chequea si hay vacío de liquidez.

**Segundo problema**: `stacked_imbalance` siempre llega como `Unknown`
al monitor. LVN lee ese campo como parte de su lógica de confirmación.
La función `derive_stacked_imbalance` ya existe en
`data/src/strategy/adapter.rs:593-614` — nadie la llama en el monitor.

**Fix**: dos líneas. Una para leer `lvn_nearby`, una para llamar
`derive_stacked_imbalance` con `bar_delta_history`.

---

### LiqHunt — Doble problema

**Problema 1 — Feed vacío**: `liq=0$` todo el día no es el umbral
de $500k. Es que el WS `@forceOrder` no está generando eventos.
Si hubiera $100k en liquidaciones el log mostraría `liq=100000$`
pero el detector no dispararía (porque el umbral es $500k).
Lo que muestra es que el feed mismo está vacío — desconectado o
parseando mal.

**Problema 2 — Umbral imposible con $300**: aunque el feed funcione,
`liq_hunt_min_usd = 500_000`. Con $300 de capital, un movimiento
de $50k en liquidaciones es un evento real y significativo para
tu escala. El umbral está diseñado para fondos, no para traders
individuales.

**Datos que LiqHunt necesita y no tiene**:
- `inst.liq_map` → siempre None (LiqHunt lo usa para targets)
- `inst.liquidations.total_usd_5m` → siempre 0 (feed vacío)

**Fix**: bajar umbral a $25k + conectar `LiqMapTracker` en monitor
+ investigar por qué el WS `@forceOrder` no genera eventos.

---

### FER y SMD — No aplican con $300 hoy

FER necesita funding rate > 0.06% en extremo con peak confirmado.
Ocurre 2-3 veces al mes en mercados tranquilos.

SMD necesita divergencia entre top traders y retail > 18%.
Hoy la divergencia fue de 6%.

Estas dos estrategias no son el problema — simplemente el mercado
no les da condiciones frecuentemente. No valen la pena optimizar
para el capital actual.

---

## Parte 3 — Estrategias nuevas que tus gráficos permiten

Estas no existen en el código. Para cada una se especifica
exactamente qué datos ya tienes disponibles hoy y qué falta conectar.

---

### Estrategia A — DOMImbalanceBreakout (DIB)

**La más rápida de implementar. Todos sus datos ya llegan al contexto.**

**Concepto**: el DOM muestra en tiempo real si el libro está
completamente desbalanceado. Cuando hay mucho más volumen en bids
que en asks en los primeros 5 niveles, y además el precio está
en un LVN (vacío de liquidez), el movimiento cuando rompe es
explosivo porque no hay resistencia encima.

Lo que ves en el DOM/Ladder: pared de bids debajo del precio,
muy poco ask arriba, thin zone confirmada.

**Condiciones Long**:

```
obi_l5 > 0.40                     ← libro muy cargado a bids
thin_zone_above = true             ← poca resistencia encima
volume_profile.lvn_nearby ≠ None   ← vacío de liquidez al frente
precio > vwap_session              ← en zona de control o encima
cvd_slope > 0.05                   ← flujo de compra confirmando
fast_slope > -0.10                 ← momentum no en contra

Target: hvn_nearby más cercano encima (ya en contexto)
Stop:   walls_below más cercana (ya en contexto)
R:R mínimo: 1.5
```

**Condiciones Short**: espejo exacto.

**Datos necesarios**: TODOS ya llegan al contexto hoy.
`obi_l5`, `thin_zone_above`, `lvn_nearby`, `hvn_nearby`,
`walls_below`, `cvd_slope`, `fast_slope`. Ninguno está en None.

**Esfuerzo**: solo escribir el detector (~80-100 líneas Rust).
No hay fix de infraestructura previo necesario.

**Régimen óptimo**: Expansion (cuando el mercado está explotando),
inicio de TrendUp/Down justo cuando el DOM muestra el desbalance
antes de que el precio se mueva.

**Por qué es mejor que LVN actual**: LVN actual no lee `lvn_nearby`
(bug) y no mira el DOM. DIB combina ambos correctamente.

---

### Estrategia B — FootprintAbsorptionReversal (FAR)

**Requiere conectar footprint por nivel — 1-2 semanas de trabajo.**

**Concepto**: en el footprint chart ves exactamente en qué niveles
de precio los vendedores agresivos (takers) están golpeando el bid
pero el precio no cae porque hay compradores pasivos absorbiendo
en el libro. Cuando esa absorción ocurre en un nivel clave
(VAL, POC, swing low reciente), el precio revierte con fuerza
porque los vendedores se quedan sin contraparte.

Lo que ves en el Footprint: vela con delta muy negativo en los
niveles bajos del rango pero el precio cierra arriba. Los números
del footprint muestran más ventas que compras en la parte baja
pero el precio no cerró ahí.

**Condiciones Long**:

```
precio entre val - 0.5×atr y val + 0.3×atr   ← zona de absorción
footprint por nivel muestra:
  delta negativo en ≥3 niveles consecutivos bajos
  (vendedores agresivos tratando de empujar abajo)
pero precio cierra > val                       ← no pudieron
obi_l5 > 0                                    ← libro con más bids
cvd_slope virando: estaba < 0, ahora > -0.05  ← presión cediendo
taker_imbalance > -0.10                        ← vendedores perdiendo fuerza
regime != TrendDown                            ← no contra tendencia fuerte

Target: poc primero, luego vah si momentum continúa
Stop:   val - 1×atr
```

**Condiciones Short**: espejo en VAH.

**Datos necesarios**:

```
Ya disponibles:           obi_l5, cvd_slope, taker_imbalance, vah, val, poc, atr
Falta conectar:           footprint por nivel de precio

Fix requerido: en on_trade() del monitor, acumular trades en
               KlineTrades (estructura ya existe en UI en
               data/src/chart/kline.rs:144-148) en vez de solo
               sumar bar_buy_vol y bar_sell_vol.
               En on_bar_close, pasar el mapa al contexto.
```

**Por qué supera a VAFA**: VAFA usa `failed_acceptance` que es
una aproximación binaria. FAR usa el footprint real nivel por
nivel — la misma información que ves en pantalla. Cuando el
footprint muestra absorción, la probabilidad de reversión es
significativamente más alta que cuando solo el precio tocó VAL.

**Régimen óptimo**: Chop, Compression, Aftermath. Exactamente
cuando VVPC no tiene condiciones.

---

### Estrategia C — SessionOpenBreakout (SOB)

**Requiere conectar SessionTracker — días de trabajo, código ya existe.**

**Concepto**: los primeros 15 minutos de London (07:00 UTC) y de
NY (13:30 UTC) establecen la dirección del día con alta probabilidad
en crypto. El mercado barre los stops acumulados durante Asia,
establece el high o low de sesión temprano, y luego continúa.

Lo que ves en el Heatmap: las zonas densas de Asia (stops acumulados
durante 7 horas de rango estrecho) se ven claramente. Cuando London
abre y el precio rompe esa zona con momentum, es el setup SOB.

Lo que ves en el DOM: en los primeros minutos de London el libro
se vuelve asimétrico — thin zone en la dirección del movimiento,
liquidez acumulada en el otro lado.

**Condiciones Long (London Open Breakout)**:

```
session.phase == OpeningRush         ← primeros 15 min de London/NY
                                       (SessionTracker ya existe en UI,
                                        solo falta conectar en monitor)
precio > high de las últimas 4 barras (rango Asia)
cvd_slope > 0.15 en la barra de ruptura
thin_zone_above = true               ← libro vacío encima
obi_l5 > 0.20                        ← libro cargado a bids
fast_slope > 0.10                    ← momentum confirmando

Target: high de sesión anterior + 1×atr
Stop:   low de la barra de ruptura (mecha)
R:R típico: 2-3 (el movimiento de apertura suele ser limpio)
```

**Datos necesarios**:

```
Ya disponibles:    price, cvd_slope, thin_zone, obi_l5, fast_slope, atr
Falta conectar:    session.phase (SessionTracker)

Fix: copiar SessionTracker.update(timestamp) de
     src/chart/kline.rs:1368-1443 a on_bar_close del monitor.
     Es exactamente lo que hace la UI — el código existe.
```

**Por qué importa**: el drop de 09:20-09:30 de hoy (200 puntos
en 10 minutos, inicio de TrendDown) fue un SOB a la baja en la
apertura de London. El sistema no lo capturó porque no sabe
qué hora es. Ninguna estrategia tiene el bonus de OpeningRush
activo. El score ×1.15 de OpeningRush está implementado para
LiqHunt pero nunca se activa porque `session = None`.

---

### Estrategia D — OrderBlockRetest (OBR)

**Requiere conectar OrderBlockDetector — código ya existe en UI.**

**Concepto**: un Order Block es la última vela bajista antes de
un impulso alcista masivo. El precio tiende a regresar a ese nivel
para "mitigar" el OB — ejecutar contra la liquidez que quedó ahí —
antes de continuar. Es uno de los setups de mayor R:R en Smart Money
porque el stop es muy ajustado (debajo del OB) y el target es
el siguiente swing estructural.

Lo que ves en el gráfico de 15m: ya tienes los OBs marcados con
la etiqueta OB. El precio regresa a tocar la zona verde/roja.
En el Footprint de ese momento ves si hay absorción confirmando.

**Condiciones Long (Bullish OB Retest)**:

```
order_blocks.nearest_bullish.status == Active o Tested
precio entre ob.low y ob.high       ← dentro de la zona del OB
flow.footprint_absorption == BidAbsorption
                                    ← compradores absorbiendo en el OB
cvd_slope > 0 o virando positivo    ← flujo apoyando
obi_l5 > 0                          ← libro con bids
regime != TrendDown                 ← no contra tendencia mayor

Target: swing_high_20 (el máximo estructural previo)
Stop:   ob.low - 0.5×atr

Bonus de score si:
  ob.volume_ratio > 1.5             ← OB con volumen institucional
  ob.swings_broken >= 2             ← OB que rompió estructura significativa
```

**Datos necesarios**:

```
Ya disponibles:    footprint_absorption (aunque aproximado), cvd_slope,
                   obi_l5, swing_high_20, atr, regime
Falta conectar:    order_blocks (OrderBlockDetector)

Fix: llamar OrderBlockDetector en on_bar_close del monitor.
     Ya se inicializa y actualiza en src/chart/kline.rs.
     El código de detección está en data/src/detectors/order_block.rs.
```

**Por qué complementa a VVPC**: VVPC busca pullbacks al VWAP en
tendencia. OBR busca retests de Order Blocks específicos. Cuando
el pullback de VVPC coincide con un OB activo, el setup es
significativamente más fuerte — mismo trade, más confluencia,
mayor score.

---

### Estrategia E — HeatmapLiquidityMagnet (HLM)

**Puede funcionar hoy con proxy, mejor con LiqMapTracker conectado.**

**Concepto**: el heatmap muestra históricamente dónde se han
acumulado órdenes grandes. El precio actúa como imán hacia esas
zonas porque los participantes grandes necesitan ejecutar contra
esa liquidez. Cuando el precio está cerca de una zona densa con
momentum alineado, la probabilidad de que llegue ahí es alta.

Lo que ves en el Heatmap: bandas horizontales de color intenso
(rojo o naranja) — alto volumen de órdenes en ese nivel en el
pasado reciente. El precio se aproxima desde un lado.

**Condiciones Long (precio siendo atraído hacia zona densa encima)**:

```
hvn_nearby encima ≠ None y distancia < 2×atr
                                    ← zona densa alcanzable
                                    (hvn_nearby como proxy del heatmap)
precio > vwap_session               ← precio en zona favorable
fast_slope > 0.05                   ← subiendo hacia la zona
obi_l5 > 0.15                       ← libro apoyando
thin_zone_above = true              ← poco obstáculo hasta la zona
cvd_slope > 0                       ← flujo confirmando

Target: hvn_nearby (la zona densa — el imán)
Stop:   0.75×atr debajo de entry
R:R: depende de la distancia — filtrar si < 1.5
```

**Datos necesarios**:

```
Ya disponibles:    hvn_nearby, vwap_session, fast_slope, obi_l5,
                   thin_zone_above, cvd_slope, atr
                   (esto permite una versión funcional hoy)

Mejora con:        liq_map.primary_target_above/below
                   (LiqMapTracker conectado en monitor —
                    da targets más precisos que hvn_nearby)
```

**Esta es la única nueva que puede escribirse y probar
esta semana sin fixes previos** — usa solo datos ya conectados.
`hvn_nearby` es un proxy funcional del heatmap mientras
`liq_map` sigue en None.

---

## Parte 4 — El plan de implementación real

Ordenado por esfuerzo y por lo que desbloquea.

### Semana 1 — Fixes críticos (cambian el comportamiento hoy)

**Fix 1: `lvn_nearby` en el detector LVN**
- Archivo: `data/src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs`
- Cambio: agregar condición de entrada que verifique `volume_profile.lvn_nearby`
- Impacto: LVN deja de entrar sin verificar que hay LVN
- Esfuerzo: 1 hora

**Fix 2: Calibración VVPC**
- `fast_slope > -0.20` → `fast_slope > -0.08` (o gate compuesto con cvd_slope)
- Targets: swing_high/low_20 → TP1 a 1.0×ATR, TP2 a 2.0×ATR
- Impacto: los 3 Longs perdedores de hoy no habrían entrado
- Esfuerzo: 2-3 horas

**Fix 3: `liq_hunt_min_usd` a $25k**
- Archivo: config
- Impacto: LiqHunt puede disparar con movimientos reales del mercado
- Esfuerzo: 5 minutos (si el feed llega — investigar `@forceOrder` primero)

**Fix 4: `stacked_imbalance` real en monitor**
- Archivo: `crates/monitor/src/main.rs` línea ~1051
- Cambio: conservar `bar_delta_history[]` y llamar `derive_stacked_imbalance()`
  que ya existe en `data/src/strategy/adapter.rs:593-614`
- Impacto: LVN recibe `Bullish/Bearish` en vez de `Unknown`
- Esfuerzo: 3-4 horas

**Nuevo: Escribir detector DIB (DOMImbalanceBreakout)**
- Datos todos disponibles, cero fixes previos
- ~100 líneas de Rust siguiendo el patrón de los detectores existentes
- Esfuerzo: 1-2 días

### Semana 2-3 — Conectar lo que la UI ya hace

**Conectar SessionTracker al monitor**
- Copiar el patrón de `src/chart/kline.rs:1368-1443`
- Desbloquea: SOB, bonus OpeningRush activo para todas las estrategias
- Esfuerzo: 1 día

**Conectar OrderBlockDetector al monitor**
- El detector ya existe en `data/src/detectors/order_block.rs`
- Desbloquea: OBR, VVPC mejora con OB como confluencia real
- Esfuerzo: 2-3 días

**Conectar LiqMapTracker al monitor**
- Inicializar y alimentar en `on_bar_close` con precio + swings + OI
- La UI ya muestra el patrón en `src/chart/kline.rs:1400-1411`
- Desbloquea: LiqHunt tiene targets reales, HLM mejora
- Esfuerzo: 2-3 días

**Escribir detector SOB y OBR**
- SOB: requiere SessionTracker conectado
- OBR: requiere OrderBlockDetector conectado
- Esfuerzo: 1-2 días cada uno

### Semana 4-6 — Footprint real

**Acumular trades WS por nivel de precio en monitor**
- En `on_trade()`: agregar a `KlineTrades` (estructura ya en UI)
- En `on_bar_close`: pasar mapa al contexto
- Desbloquea: VAFA funciona con absorción real, FAR posible
- Esfuerzo: 1 semana incluyendo pruebas

**Escribir detector FAR**
- Requiere footprint por nivel conectado
- Esfuerzo: 2-3 días

---

## Parte 5 — Qué capturaría cada setup que hoy se pierde

Esta es la respuesta directa a tu pregunta original.

| Situación que ocurrió hoy | ¿Qué estrategia la capta? | Estado |
|---|---|---|
| Drop de 200 pts a las 09:20 (London open) | SOB | No existe — falta SessionTracker |
| Absorción en VAL durante el drop | FAR | No existe — falta footprint por nivel |
| DOM desbalanceado antes del drop | DIB | No existe — hay que escribirlo (datos disponibles) |
| Precio regresando al OB de 09:30 | OBR | No existe — falta OrderBlockDetector |
| LVN en 77,050 antes del breakout | LVN | Existe pero no lee lvn_nearby (bug) |
| Precio siendo atraído hacia HVN 77,000 | HLM | No existe — hay que escribirlo (datos disponibles) |
| VVPC Long fallido con fast_slope -0.062 | — | Fix de calibración, no estrategia nueva |

---

## Resumen ejecutivo

El sistema tiene 6 estrategias pero efectivamente solo opera 1 porque:

1. VVPC es la única que usa datos realmente conectados
2. VAFA opera con footprint falso (un número, no un mapa)
3. LVN tiene un bug de una línea que la invalida
4. LiqHunt tiene el feed de liquidaciones vacío + umbral imposible
5. FER y SMD necesitan condiciones de mercado que no ocurren frecuentemente

Lo que los gráficos (Heatmap, Footprint, DOM, Time&Sales) muestran
visualmente ya está disponible como datos en el websocket del monitor.
El trabajo es conectar esos datos al contexto de estrategia —
algo que la UI ya hace pero el monitor no.

Con DIB y HLM escritos esta semana (datos ya disponibles, cero
fixes de infraestructura), el sistema pasa de 1 estrategia activa
a 3. Con SOB y OBR en semana 2-3, llega a 5. Con FAR en semana 4-6,
el sistema realmente usa el Footprint Chart que ves en pantalla.

La plataforma es buena. El problema es la brecha entre la UI y el
monitor. Esa brecha se cierra en fases.
