# AMD + Order Flow — Estrategia Completa

**Accumulation · Manipulation · Distribution**  
*ICT Smart Money Concepts + Order Flow Confirmation*  
*Última revisión: 2026-06-07*

---

## Qué es y por qué funciona

El patrón AMD describe cómo los participantes institucionales mueven el mercado en tres fases secuenciales antes de un movimiento real. No es teoría — es la mecánica exacta de cómo se construye liquidez antes de un impulso.

**La hipótesis central:**  
Antes de mover el precio en una dirección, los institucionales necesitan dos cosas: (1) liquidez para llenar sus órdenes de tamaño sin mover el mercado en su contra, y (2) stops ajenos para usar como combustible del movimiento real. El patrón AMD es exactamente ese proceso visible en los datos de order flow.

**Por qué el orden importa:**  
Un sistema que entra en el breakout del rango (como RBF en su forma básica) es víctima de la fase de Manipulación — entra justo cuando el precio está haciendo un movimiento falso para barrer stops. AMD entra **después** del spike falso, cuando el precio revierte con convicción real.

---

## Las tres fases en detalle

### Fase 1 — Accumulation (Acumulación)

El precio consolida en un rango estrecho. Aparentemente no pasa nada. En realidad los institucionales están construyendo posiciones gradualmente sin revelar la dirección.

**Lo que ves en el precio:**
- Rango lateral de 15 a 40 barras M1
- Highs y lows similares, sin breakout claro
- Sin tendencia definida — choppy

**Lo que ves en order flow:**
- `cvd_in_range` cerca de cero o levemente sesgado — los compradores y vendedores se equilibran artificialmente
- `obi_l5` oscilando alrededor de cero, sin sesgo sostenido
- `vr` bajo (1.0× a 1.5×) — volumen normal, sin convicción aparente
- `vpin` bajo — no hay flujo tóxico todavía
- `stacked_imbalance` ausente — no hay barras consecutivas con mismo delta

**La trampa para traders minoristas:**  
El range parece igual a cientos de otros rangos que no llevan a nada. La mayoría espera el breakout para entrar. Los institucionales ya están dentro.

---

### Fase 2 — Manipulation (Manipulación)

El precio hace un movimiento brusco en una dirección — típicamente en la dirección del mínimo resistencia (donde hay más stops acumulados). El spike barre los stops de los traders que entraron anticipando la acumulación y atrae a nuevos traders en la dirección equivocada.

**Lo que ves en el precio:**
- Spike agresivo en una dirección (1 a 5 barras M1)
- Cierre fuera del rango de acumulación
- Parece un breakout legítimo — por eso es efectivo

**Lo que ves en order flow — la firma de manipulación:**
- `cvd` hace un spike en la dirección del precio... pero menos de lo esperado para un movimiento real. O directamente va en contra del precio (price up, CVD down = vendedores dominando el movimiento alcista)
- `obi_l5` NO confirma el spike — el libro no apoya el movimiento
- `vpin` sube bruscamente — flujo tóxico activo = institucionales ejecutando órdenes grandes disfrazadas
- `liq_ratio` sube — liquidaciones disparadas en el lado incorrecto
- `LiqMapSnapshot.primary_target` señalaba exactamente esa zona — era el cluster de stops predicho
- `absorption` activo — alguien absorbe cada trade que va en la dirección del spike

**El momento más importante:**  
Cuando el CVD diverge del precio durante el spike (precio sube, CVD baja o viceversa), eso es la firma institucional. El precio está siendo empujado artificialmente. Ese es el momento de preparar la entrada en la dirección opuesta.

---

### Fase 3 — Distribution (Distribución)

El precio revierte con violencia desde el extremo del spike. El movimiento real comienza. Los stops boneados se convierten en combustible (se ejecutan como market orders en la dirección del nuevo movimiento). El volumen explota.

**Lo que ves en el precio:**
- Reversión agresiva desde el extremo del spike
- El precio atraviesa el rango de acumulación completo
- Movimiento sostenido, barras grandes consecutivas
- Targets de 1.5× a 2.5× el tamaño del spike de manipulación

**Lo que ves en order flow — la firma de distribución:**
- `cvd_slope` revierte agresivamente en la nueva dirección — presión real y sostenida
- `obi_l5` se alinea con el movimiento — el libro confirma
- `vr` explota ≥ 3× — el volumen real entra en la dirección correcta
- `stacked_imbalance` bearish/bullish activo — 3+ barras consecutivas con mismo sesgo de delta
- `oi_momentum` true — posiciones nuevas abriéndose, no cierres

**El entry:**  
No en el primer tick de reversión — esperás confirmación. La primera barra que cierra en la nueva dirección con CVD alineado y OBI confirmando es el entry. Stop debajo/encima del extremo del spike. Target en el siguiente nivel estructural significativo (HVN, OB, FVG, Naked POC).

---

## Confirmaciones de Order Flow por fase

| Indicador | Acumulación | Manipulación (señal) | Distribución (entry) |
|-----------|-------------|---------------------|----------------------|
| `cvd_in_range` | ~0, neutral | spike vs precio (divergencia) | alineado y creciendo |
| `obi_l5` | ~0, oscilando | NO confirma el spike | confirma la dirección |
| `vr` | 1.0–1.5× | 2–4× (spike falso) | ≥ 3× (movimiento real) |
| `vpin` | bajo < 0.4 | sube > 0.65 | puede bajar o sostenerse |
| `stacked_imb` | ausente | puede aparecer en dirección del spike | activo en nueva dirección |
| `absorption` | ausente | activo — absorben el spike | puede estar activo al inicio |
| `liq_ratio` | normal | pico — stops boneados | alto — combustible del movimiento |
| `LiqMap` | señala zona de stops | precio llegó a esa zona | precio alejándose de ella |
| `oi_momentum` | neutro | puede ser false | true — posiciones nuevas |

---

## Ejemplos reales — Jun 5 y Jun 6, 2026

Los siguientes 8 ejemplos son trades reales capturados en BTCUSDT Perpetual M1 en Binance entre el 5 y 6 de junio de 2026. Todos muestran el patrón AMD con RR entre 2.22 y 2.25, targets de ~$898–$909.

---

### Ejemplo 1 — Jun 5, 13:15 UTC (LondonNyOverlap)

**Contexto:** Apertura de NYSE. BTC en ~$61,918.

**Fase Acumulación (12:30–13:10):**  
Rango lateral comprimido, ~20 barras M1. CVD neutral, OBI oscilando sin sesgo. VR promedio 1.2×.

**Fase Manipulación (13:10–13:15):**  
Spike alcista rápido hacia $61,949 (máximo del día). Barre stops de posiciones SHORT acumuladas arriba del rango. El CVD no confirma el movimiento — el libro absorbía cada compra. VPIN subió indicando flujo tóxico.

**Fase Distribución (13:15+):**  
Reversión agresiva SHORT desde el máximo. CVD revirtió negativamente con fuerza. Entrada en el primer cierre bajista post-spike.

**Resultado:**
```
Entry:  ~$61,792
Stop:   $40,474 / $404.73 (0.65%) → ~$62,197
Target: $90,896 / $908.95 (1.46%) → ~$60,883
RR:     2.25
PnL:    +$908.95 (Qty: 0.618)
```

---

### Ejemplo 2 — Jun 5, 18:31 UTC (NewYork)

**Contexto:** NY tarde. BTC en ~$60,085–$60,270. Sesión fuera de London.

**Fase Acumulación (15:30–18:00):**  
Rebote desde mínimos, consolidación lateral entre $59,800–$60,270. Estructura de rango clara visible en el precio.

**Fase Manipulación (17:30–18:00):**  
Impulso alcista falso hacia ~$60,270. Barre stops SHORT del rebote. CVD diverge del precio.

**Fase Distribución (18:00–18:31):**  
Colapso SHORT. El precio rompe el soporte del rango y continúa bajando.

**Resultado:**
```
Entry:  ~$60,214
Stop:   $404.73 (0.67%) 
Target: $908.95 (1.50%)
RR:     2.25
PnL:    +$908.95 (Qty: 0.618)
```

---

### Ejemplo 3 — Jun 5, 19:36 UTC (NewYork)

**Contexto:** NY. BTC en ~$59,736. Mercado bajista continuado.

**Dinámica invertida — Distribución alcista:**  
Este ejemplo muestra el patrón en LONG. La manipulación fue un spike bajista que llevó el precio a ~$59,100 (mínimo del período), barriendo stops LONG acumulados abajo del rango. El CVD divergió — el precio bajó pero los compradores absorbían cada tick.

**Fase Distribución LONG (19:00+):**  
Reversión alcista agresiva desde el mínimo. Target en $89,839 / +$898.38 (1.52%).

**Resultado:**
```
Entry:  ~$59,646
Stop:   $404.73 (0.68%) — debajo del mínimo del spike
Target: $898.38 (1.52%) → $89,839
RR:     2.22
PnL:    +$898.38 (Qty: 0.618)
```

---

### Ejemplo 4 — Jun 5, 20:21 UTC (NewYork)

**Contexto:** Continuación del movimiento. BTC en ~$61,005.

**Estructura multi-fase visible:**  
Este ejemplo es particularmente claro — se ven dos rangos de acumulación separados con sus respectivos spikes de manipulación (uno bajista, uno alcista) antes de la distribución final.

**Resultado:**
```
Entry:  ~$60,832
Stop:   $404.73 (0.67%)
Target: $898.38 (1.49%)
RR:     2.22
PnL:    +$898.38 (Qty: 0.618)
```

---

### Ejemplo 5 — Jun 5, 23:11 UTC (Asia temprana)

**Contexto:** Fuera de sesiones operativas de London/Overlap. BTC ~$61,375. Este ejemplo es crucial — demuestra que el patrón AMD ocurre en cualquier sesión, no solo London.

**Acumulación (20:30–22:30):**  
Rebote post-mínimos, consolidación lateral clara en el precio. ~2 horas de rango.

**Manipulación (22:30):**  
Spike alcista corto que barre stops SHORT encima del rango. Duración: 2–3 barras.

**Distribución (22:30–23:11):**  
SHORT agresivo. Target en $90,896 alcanzado.

**Resultado:**
```
Target: $908.95 (1.47%) → $90,896
Stop:   $404.73 (0.65%)
RR:     2.25
PnL:    +$908.95 (Qty: 0.618)
```

---

### Ejemplo 6 — Jun 6, 04:22 UTC (Asia)

**Contexto:** Madrugada, sesión Asia. BTC ~$59,987. Demostración de que el patrón AMD es 24/7.

**Estructura:**  
Acumulación larga (01:30–03:30), manipulación alcista falsa hacia ~$60,094, distribución SHORT hacia target.

**Resultado:**
```
Target: $908.95 (1.50%) → $90,896
Stop:   $404.73 (0.67%)
RR:     2.25
PnL:    +$908.95 (Qty: 0.618)
```

---

### Ejemplo 7 — Jun 6, 05:18 UTC (Asia / pre-London)

**Contexto:** Pre-apertura London. BTC ~$59,973. El movimiento más rápido del período.

**Manipulación extremadamente corta:**  
Spike bajista de 1–2 barras a ~$59,585 (mínimo visible). Barre stops LONG acumulados.

**Distribución LONG explosiva:**  
Reversión alcista inmediata y vertical. Target de +$898.38 alcanzado en pocas barras — el movimiento más limpio del período.

**Resultado:**
```
Target: $898.38 (1.50%) → $89,839
Stop:   $404.73 (0.67%)
RR:     2.22
PnL:    +$898.38 (Qty: 0.618)
```

---

### Ejemplo 8 — Jun 6, 10:01 UTC (London)

**Contexto:** Sesión London activa. BTC ~$60,880.

**El único ejemplo dentro de la sesión operativa de RBF.**  
La acumulación ocurrió entre 06:30–09:00. El spike de manipulación alcanzó ~$60,880 (máximo visible). La distribución SHORT llevó al target completo.

**Resultado:**
```
Target: $908.95 (1.48%) → $90,896
Stop:   $404.73 (0.66%)
RR:     2.25
PnL:    +$908.95 (Qty: 0.618)
```

---

## Resumen de los 8 ejemplos

| # | Hora UTC | Sesión | Dir | Duración AMD | RR | PnL |
|---|----------|--------|-----|-------------|-----|-----|
| 1 | Jun 5 13:15 | LonNyOverlap | SHORT | ~45 min | 2.25 | +$908 |
| 2 | Jun 5 18:31 | NewYork | SHORT | ~3h | 2.25 | +$908 |
| 3 | Jun 5 19:36 | NewYork | LONG | ~1.5h | 2.22 | +$898 |
| 4 | Jun 5 20:21 | NewYork | SHORT | ~2h | 2.22 | +$898 |
| 5 | Jun 5 23:11 | Asia | SHORT | ~2h | 2.25 | +$908 |
| 6 | Jun 6 04:22 | Asia | SHORT | ~2.5h | 2.25 | +$908 |
| 7 | Jun 6 05:18 | Asia | LONG | ~45 min | 2.22 | +$898 |
| 8 | Jun 6 10:01 | London | SHORT | ~3h | 2.25 | +$908 |

**Total 2 días: 8 trades × ~$905 promedio = +$7,240 bruto**  
**Con $50 capital × 15× = $750 nocional: +$908 por trade en el instrumento, NO sobre tu capital.**

> ⚠️ Nota importante: los $908 de PnL son sobre una posición de 0.618 BTC (~$37,000 nocional). Con $750 nocional (tu caso), el PnL por trade es ~$13.50 target / ~$6.00 stop. Las imágenes muestran la estrategia en su forma completa, no escalada a $50.

---

## Diferencias clave entre AMD y RBF

| Dimensión | RBF | AMD + Order Flow |
|-----------|-----|-----------------|
| Entry timing | En el breakout del rango | Después del spike de manipulación |
| Lo que detecta | Continuación de breakout | Reversión del spike falso |
| Sesiones | London + Overlap | Cualquier sesión (24/7) |
| Señal principal | CVD alineado con breakout | CVD divergiendo del spike |
| Stop placement | 0.25% fijo | Detrás del extremo del spike |
| Target | 0.50% fijo | Nivel estructural siguiente (HVN, OB, FVG) |
| Frecuencia | ~2–3/día | Potencialmente 4–8/día |
| RR típico | 2:1 | 2.2–2.5:1 |
| Vulnerable a | Fakeouts, HVNs en el camino | Spikes falsos consecutivos |
| Usa LiqMap | No | Core — predice la manipulación |

---

## Cómo construir el detector AMD en el sistema

### Lógica de detección secuencial

El detector necesita mantener estado en tres fases. No puede ser stateless como un detector de breakout — necesita "recordar" que está en fase de acumulación para reconocer la manipulación.

```
Estado: IDLE → ACCUMULATING → MANIPULATION_DETECTED → DISTRIBUTION_ENTRY
```

**IDLE → ACCUMULATING:**
```
condición:
  range_pct entre 0.06% y 0.45% (más estrecho que RBF)
  barras_en_rango >= 15
  cvd_in_range.abs() < umbral_neutral (CVD equilibrado)
  obi_l5.abs() < 0.12 (libro equilibrado)
  vr_promedio < 1.6 (volumen bajo)
```

**ACCUMULATING → MANIPULATION_DETECTED:**
```
condición (spike de manipulación):
  cierre fuera del rango con vr >= 2.0×
  Y CUALQUIERA de:
    cvd_spike_vs_precio: precio sube pero cvd_slope < 0 (divergencia)
    obi no confirma: precio sube pero obi_l5 < 0
    vpin > 0.65 (flujo tóxico)
    liq_ratio pico: ratio > media + 2σ
    LiqMap: precio llegó a primary_target_above/below

registrar: spike_high o spike_low (el extremo de la manipulación)
registrar: spike_direction (la dirección falsa)
```

**MANIPULATION_DETECTED → DISTRIBUTION_ENTRY (el entry real):**
```
condición (reversión confirmada):
  primera barra que cierra en dirección OPUESTA al spike
  Y cvd_slope en dirección opuesta al spike
  Y obi_l5 confirma la nueva dirección
  Y vr >= 2.5× (volumen real en la nueva dirección)

entry: cierre de esa barra
stop: spike_extreme + buffer (0.05–0.10%)
target: siguiente nivel estructural (LVN, Naked POC, OB, FVG)
  → si no hay nivel claro: entry ± 1.0% (2× el stop)
```

### Datos disponibles que ya tenés

Todos los inputs necesarios para el detector AMD ya están en `StrategyMarketContext`:

| Input AMD | Fuente en el sistema |
|-----------|---------------------|
| CVD divergencia | `ctx.order_flow.cvd_slope` + `bar_delta` |
| OBI no confirma | `ctx.order_book.obi_l5` |
| VPIN tóxico | `ctx.institutional.vpin` |
| Spike de liquidaciones | `ctx.liquidations.liq_ratio` |
| LiqMap zona predicha | `ctx.liq_map.primary_target_above/below` |
| Absorción del spike | `ctx.order_flow.absorption_long/short` |
| Target dinámico | `ctx.volume_profile.lvn_nearby` + `NakedPocTracker` |
| OB en zona de acumulación | `ctx.order_blocks` |

No hay que conectar nada nuevo. El detector AMD es código nuevo que lee datos que ya existen.

---

## Parámetros iniciales sugeridos

```toml
[amd_detector]
# Acumulación
accum_range_min_pct    = 0.06
accum_range_max_pct    = 0.45
accum_min_bars         = 15
accum_max_bars         = 50
accum_cvd_neutral_usd  = 200.0    # |cvd_in_range| < 200 USD para considerar equilibrado
accum_obi_neutral      = 0.12     # |obi_l5| < 0.12

# Manipulación
manip_min_vr           = 2.0
manip_cvd_diverge_pct  = 0.30     # CVD en dirección opuesta >= 30% del esperado
manip_vpin_threshold   = 0.60
manip_liq_zscore       = 2.0      # liq_ratio > media + 2σ

# Entry (distribución)
dist_min_vr            = 2.5
dist_cvd_confirm_slope = 10.0     # cvd_slope en nueva dirección >= 10 USD/barra
dist_obi_confirm       = 0.10

# Gestión del trade
stop_buffer_pct        = 0.08     # Detrás del extremo del spike
target_min_rr          = 2.0
cooldown_bars          = 45       # Más corto que RBF — el patrón puede repetirse

# Sesiones
sessions_active        = ["Asia", "London", "LondonNyOverlap", "NewYork"]  # 24/7
min_confluence_score   = 1        # Shadow mode inicial
```

---

## Milestones de calibración

| Condición | Acción |
|-----------|--------|
| Sistema construido | Activar en shadow mode, todas las sesiones |
| 20 señales cerradas | ¿El estado MANIPULATION_DETECTED se activa correctamente? ¿Falsos positivos? |
| 50 señales cerradas | ¿El WR del entry post-manipulación > 50%? ¿Cuántas manipulaciones falsas? |
| 100 señales cerradas | Calibrar `manip_cvd_diverge_pct` y `manip_vpin_threshold` con datos reales |
| WR > 55% en 50+ señales | Activar sizing diferencial por score de confluencia |

---

## Lo que hace único a este sistema

La mayoría de traders que usan AMD lo detectan **visualmente** y tarde. Ven el spike en el chart y deciden en segundos si fue manipulación o breakout real.

Este sistema lo detecta **en tiempo real con datos cuantitativos**:

1. `LiqMapSnapshot` predice **antes** del spike dónde van a barrer stops — la manipulación está anticipada
2. La divergencia CVD/precio se mide automáticamente barra a barra
3. El VPIN sube durante la manipulación con microsegundos de adelanto visible en los datos
4. El entry en la distribución tiene 5 confirmaciones simultáneas (CVD, OBI, VR, absorción, OI)

No hay juicio subjetivo. No hay "parece que fue falso". Son números.

---

*Próximo paso: diseño del struct `AmdSignal` y la máquina de estados `AmdDetectorState` en Rust.*
