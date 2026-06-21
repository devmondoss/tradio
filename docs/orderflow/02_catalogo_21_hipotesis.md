# Catálogo de Hipótesis de Orderflow — Especificación Detallada (21 hipótesis)

> **Naturaleza de este documento.** Cada hipótesis viene de contenido educativo de traders
> individuales (transcripciones de YouTube/webinars), no de literatura académica revisada por
> pares. La sección "Lógica que argumenta el creador" expone el RAZONAMIENTO del trader de
> origen — es su justificación, no un hecho demostrado. Ninguna hipótesis de este documento
> está validada contra el dataset BTCUSDT perp. La validación es un paso posterior, separado,
> que requiere backtest con walk-forward (ver metodología ya aplicada a la Hipótesis 1 en el
> proyecto). Este documento existe para que la implementación de cada regla sea inequívoca,
> no para argumentar que las reglas son correctas.

> **Convención de mapeo de columnas**: todas las referencias a columnas usan los nombres
> exactos de `btcusdt_perp_m1.parquet` (o el timeframe que corresponda: m5/m15/h1/h4) tal
> como está documentado en el README del dataset. Donde la columna no existe y hace falta
> derivarla, se marca explícitamente como **[DERIVAR]**. Donde la hipótesis requiere datos
> que el dataset NO tiene (ej. heatmap DOM completo, liquidaciones históricas), se marca
> como **[NO DISPONIBLE — requiere capa de datos no presente en este dataset]**.

> **Fee de referencia**: 11 bps round-trip taker, 11bps→~4bps si se ejecuta maker
> (asumir taker salvo que se indique lo contrario, según contexto ya establecido del proyecto).

---

## Índice

**Prioridad alta**
1. [4 Variantes de Apertura vs. Perfil del Día Anterior](#h1)
2. [Delta Range Reversal v3 (sistema de score)](#h2)
3. [Filtro: Delta Liderando en Contra](#h3)
4. [Unfinished Action como Red Flag](#h4)
5. [Order Block con 6 Condiciones + Entrada en Cluster](#h5)

**Prioridad media**
6. [Big Trades: Fade en Balance vs. Continuación en Ruptura](#h6)
7. [LVN = Entrada, HVN/POC = Target](#h7)
8. [Merge de Perfiles de Volumen Contiguos](#h8)
9. [POC Desplazándose como Detector de Fuerza de Tendencia](#h9)

**Prioridad baja**
10. [Velas de Volumen como Confirmación Cruzada](#h10)
11. [Confluencia Discrecional Multi-Nivel](#h11)
12. [Delta Absorbido Intrabar (max−close)](#h12)

**Segunda tanda (creadores en inglés, variados)**
13. [LVN + Defensa de Orden Pasiva con Validación de Fill](#h13)
14. [Niveles Psicológicos Redondos (80/20)](#h14)
15. [Repair Candle como Gatillo de Re-entrada](#h15)
16. [Bid Refill Creciente como Señal de Convicción](#h16)
17. [Salida Dinámica por Tape Weakness](#h17)
18. [Jerarquía Macro→Secundaria→Microestructura](#h18)
19. [Daily Candle Sentiment Continuation](#h19)
20. [Caja Estadística de Pullback Histórico](#h20)
21. [B-Shape en Footprint + Bids Sostenidos](#h21)

---
<a name="h1"></a>
## Hipótesis 1: 4 Variantes de Apertura vs. Perfil del Día Anterior

**Fuente**: "Metodología para futuros, índices y oro" + "Revelo mi ESTRATEGIA de Perfil de
Volumen" (mismo creador, Dimy/Subdimi).

**Estado**: ya tiene spec completo independiente en `01_opening_range_4_variants.md`. Resumen
aquí por completitud del catálogo; ver ese archivo para el detalle completo de 11 secciones.

### Lógica que argumenta el creador
El mercado pasa ~80% del tiempo en "balance" (rango) y ~20% en "imbalance" (tendencia). La
posición de la apertura del día respecto al área de valor (POC/VAH/VAL) del día anterior
indica si hoy probablemente seguirá en rango (apertura dentro del área de valor previa) o si
hay presión direccional incipiente (apertura fuera de ella). El razonamiento es de tipo
"contexto de subasta": si ayer el mercado encontró un precio "justo" y hoy abre dentro de
ese rango de precios ya aceptados, lo más probable es que el precio siga oscilando ahí hasta
que aparezca una razón nueva (volumen, noticia) para salir. Si abre afuera, ya hay evidencia
de que algo cambió.

### Condiciones (resumen — ver doc completo para casos borde)
```
SI val_ayer <= open_hoy <= vah_ayer:
    VARIANTE 1 (rango): operar reversión en VAH/VAL hacia POC, luego rotación opuesta
SI vah_ayer < open_hoy <= high_ayer:
    VARIANTE 2 (direccional alcista leve): esperar retroceso a POC, entrar ahí, target = open_hoy
SI low_ayer <= open_hoy < val_ayer:
    VARIANTE 3 (direccional bajista leve): simétrico a Variante 2
SI open_hoy > high_ayer O open_hoy < low_ayer:
    VARIANTE 4: regla de entrada no especificada en la fuente — NO IMPLEMENTAR sin definición
```

### Pseudocódigo
```python
def classify_opening_variant(open_today, poc_prev, vah_prev, val_prev, high_prev, low_prev):
    if val_prev <= open_today <= vah_prev:
        return "V1_RANGE"
    elif vah_prev < open_today <= high_prev:
        return "V2_BULLISH_LEAN"
    elif low_prev <= open_today < val_prev:
        return "V3_BEARISH_LEAN"
    elif open_today > high_prev or open_today < low_prev:
        return "V4_OUT_OF_RANGE"  # sin regla de entrada definida, excluir del backtest v1
```

### Columnas necesarias
`vp_poc`, `vp_vah`, `vp_val` (congeladas al día anterior, ver doc completo sección 6),
`prev_day_high`, `prev_day_low`, `close` (para derivar `daily_open`).

### Ejemplo numérico
Día anterior: POC=$104,200, VAH=$105,800, VAL=$102,900, high=$106,500, low=$101,800.
Apertura de hoy: $105,100 → cae entre VAL y VAH → **Variante 1 (rango)**.
Bias: esperar toque de VAH ($105,800) → entrada short → target_1=POC ($104,200) →
target_2=VAL ($102,900) si rota completo.

**Ver `01_opening_range_4_variants.md` para: definición exacta de "apertura", congelado de
variables intra-día, modos de stop loss, filtros opcionales (delta en contra, unfinished
action), y las 4 decisiones pendientes documentadas en la sección 11 de ese archivo.**

---
<a name="h2"></a>
## Hipótesis 2: Delta Range Reversal v3 (sistema de score)

**Fuente**: documento "Delta_Range_Reversal_v3_Unificado.md" — a diferencia de los demás,
este NO es transcripción de video sino una especificación ya formalizada por su autor (no
identificado en el documento, posiblemente el propio usuario o un colaborador suyo), con su
propia arquitectura de score y advertencia explícita de walk-forward.

### Lógica que argumenta el creador
En un rango intradía, los extremos (máximo/mínimo del rango) son "zonas de decisión": si el
precio llega ahí con volumen y delta agresivo pero NO logra romper ni aceptar fuera del
rango, ese flujo agresivo queda "atrapado" — los traders que apostaron a la ruptura están
del lado equivocado. El precio entonces rota de vuelta hacia el POC/mid/VWAP o el extremo
opuesto, porque esos traders atrapados eventualmente cierran posición (lo que empuja el
precio en la dirección contraria a su apuesta original). La regla central: **"no operamos el
medio, operamos los extremos"**.

A diferencia de casi todo lo demás en este catálogo, este documento exige NORMALIZACIÓN
explícita de cada variable contra su propia media móvil reciente (no usa umbrales absolutos),
lo cual es metodológicamente más defendible — evita que un umbral fijo se vuelva obsoleto
cuando cambia el régimen de volatilidad.

### Las 4 variables maestras (todas normalizadas contra SMA de ventana N=50 en M5, N=150 en M1)

```
VR  (Volume Ratio)   = volumen_vela / SMA(volumen, N)
DZ  (Delta Z-score)  = (delta_vela − media(delta, N)) / stdev(delta, N)
AS  (Absorption)     = ver fórmula abajo
LI  (Liquidity Imb.) = (liq_resting_bid − liq_resting_ask) / (bid + ask)
                        [NO DISPONIBLE — requiere heatmap DOM completo, dataset solo
                         tiene 6 días de ob500 crudo, no 365. Esta variable debe
                         excluirse o sustituirse por obi10_mean/obi20_mean del M1
                         agregado, con el caveat de que el README advierte que esa
                         agregación "licúa la microestructura"]
```

### Fórmula de Absorption Score (la pieza más específica de esta hipótesis)
```
desplazamiento = (cierre − apertura) / rango_vela          # rango ∈ [−1, +1]
AS_long  = max(−DZ, 0) × (1 − max(desplazamiento, 0))
AS_short = max( DZ, 0) × (1 − max(−desplazamiento, 0))
```
Interpretación: `AS_long` es alto cuando hay venta agresiva fuerte (DZ muy negativo) que NO
logró bajar el precio (desplazamiento no fue hacia abajo) — vendedores atrapados, sesgo long.

### Función de intensidad (usada para escalar cada señal de 0 a 1)
```python
def intensidad(x, umbral_min, umbral_fuerte):
    return clamp((x - umbral_min) / (umbral_fuerte - umbral_min), 0, 1)
```
Una señal que apenas pasa el umbral mínimo aporta poco peso; una que lo supera con holgura
aporta el peso completo de esa variable al score.

### Arquitectura completa: Fases 1-5

```
Fase 1 — Detección de rango (parámetros calibrados sugeridos por la fuente):
  min_bars_inside_range: 20
  min_touches_total: 3 (mínimo 1 touch por lado)
  min_range_size_atr: 0.8   (rango debe medir al menos 0.8×ATR para pagar el riesgo)
  max_range_size_atr: 3.5   (más que eso, ya no es "rango operable intradía")
  middle_zone_pct: 0.35     (zona muerta = 35%-65% del rango, no operar ahí)

Detección de régimen (rango vs tendencia) — voto mayoritario de 3 señales:
  votos_tendencia = (value_area_se_desplaza) + (precio_sostenido_un_lado_VWAP)
                   + (pendiente_CVD_fuerte)
  regimen = TENDENCIA si votos_tendencia >= 2, sino RANGO
  → Esta estrategia SOLO opera en RANGO.

Fase 2 — Zonas operativas: range_high, range_low, desviación/reclaim del extremo,
  cluster de liquidez cercano al extremo, POC absorbido en mecha. NO operar en middle zone.

Fase 3 — Liquidaciones y Open Interest [NO DISPONIBLE liquidaciones — Bybit no las publica
  históricamente. Tu dataset SÍ tiene OI en oi_5m.parquet, así que esta parte parcial sí
  es implementable]:
  oi_change_pct = (OI_actual - OI_hace_M_velas) / OI_hace_M_velas
  oi_lookback_bars: 6
  oi_drop_on_sweep_min: -0.015   (OI baja >=1.5% en la barrida = cierre forzado, bueno para fade)
  oi_rise_after_reclaim_min: 0.010  (OI sube >=1% tras reclaim = nuevas posiciones a favor)

  Regla dura: liquidaciones sin absorción = NO trade. [Sin datos de liquidaciones, esta regla
  dura no puede aplicarse tal cual — sustituir por: caída fuerte de OI sin absorción = NO trade]

Fase 4 — Footprint/Delta/CVD/Big Trades en el extremo: cálculo del score (ver abajo).

Fase 5 — Gestión del trade (ver sección de gestión).
```

### Cálculo del score — ejemplo dado por la fuente
```
| Señal          | Valor medido | Intensidad | Peso × Intensidad |
|----------------|--------------|------------|--------------------|
| Volumen (VR)   | 3.1          | 0.55       | 10 × 0.55 = 5.5    |
| CVD diverg.    | 2 swings     | 0.50       | 8 × 0.50 = 4.0     |
| Big trades     | 12×          | 0.13       | 6 × 0.13 = 0.8     |
| Liquidaciones  | 5×           | 0.40       | 6 × 0.40 = 2.4     |  [no disponible]
| OI confirma    | 1.8%         | 0.40       | 4 × 0.40 = 1.6     |
| LI heatmap     | 0.42         | 0.35       | 2 × 0.35 = 0.7     |  [no disponible/sustituir]
| SCORE TOTAL    |              |            | 60.3               |
```
Los "pesos" (10, 8, 6, 6, 4, 2) son parámetros de partida sugeridos por la fuente, no
derivados de ningún backtest — deben tratarse como hiperparámetros a calibrar/validar
en IS, nunca fijarse a priori sin prueba.

### Tramos de decisión y tamaño de posición
```yaml
score < 55:        no_trade
score 55-69:        "scout", tamaño = 0.5x base
score 70-84:        "estándar", tamaño = 1.0x base
score >= 85:        "alta convicción", tamaño = 1.5x base (tope)

critical_red_flag_override: true   # si hay red flag crítica, NO trade aunque score>=85
```

### Red flags críticas (cualquiera de estas anula el trade sin importar el score)
```
- strong_close_outside_range
- acceptance_outside_range
- delta_continuation_breakout
- cvd_breakout_confirmation
- volume_expansion_breakout
- no_reclaim
- middle_zone
- rr_below_minimum
- high_impact_news_window
```

### Gestión del trade
```yaml
min_rr_overall: 1.5          # gate duro: si no hay 1.5R hasta TP2, no se entra
partial_at_tp1: 0.5          # cerrar 50% en POC/mid
move_stop_to_breakeven_after_tp1: true
partial_at_tp2: 0.3
trailing_remainder: true     # 20% restante con trailing por delta/CVD

trailing_logic:
  exit_if_delta_reverses_strong: true
  exit_if_no_new_extreme_in_bars: 3
  exit_if_opposite_big_trade_at_zone: true

session_discipline:
  max_trades_per_session: 3
  reduce_size_after_win: true
  stop_after_consecutive_losses: 2
  daily_target_pct: 1.0
  daily_stop_pct: 0.6
  risk_pct_per_trade: 1.0
```

### Pseudocódigo completo (de la fuente, adaptado)
```python
def detect_delta_range_reversal(market, cfg):
    rng = detect_intraday_range(market, cfg)
    if not rng.valid or detect_regime(market) != "RANGE":
        return None
    if price_in_middle_zone(market.price, rng):
        return None

    for side, near_extreme, absb, breakout in [
        ("long",  near_range_low,  detect_sell_absorption_at_low,  detect_real_breakout_down),
        ("short", near_range_high, detect_buy_absorption_at_high, detect_real_breakout_up),
    ]:
        if not near_extreme(market.price, rng):
            continue
        if breakout(market, rng):          # gate duro: si rompió de verdad, no es reversal
            return None
        if not absb(market, rng):
            continue
        trigger = has_trigger(market, rng, side)   # flip / reclaim / retest
        if not trigger:
            continue
        score, breakdown = compute_score(market, rng, side, cfg)
        if score < cfg.score_scout or has_critical_red_flag(market, rng, side):
            return None
        if not risk_reward_valid(market, rng, side, cfg.min_rr):
            return None
        size_mult = size_from_score(score, cfg)
        return build_order(side, market, rng, score, breakdown, size_mult)
    return None
```

### Columnas necesarias del dataset
`vr` (ya existe como Volume Ratio), `dz` (ya existe como Delta Z-score), `cvd`, `cvd_div`,
`cvd_slope`, `regime` (para filtrar a no-Chop/rango — aunque la definición de "rango" de
esta hipótesis es más estricta: rango operable de 0.8-3.5 ATR, no simplemente `regime=="Chop"`),
`atr14`, `vwap`, `max_trade`/`big_trade_bearish`/`big_trade_bullish`, `oi_5m.parquet`
(merge requerido), `fp_*` (footprint, para absorción en el extremo).

### ⚠️ Caveats de implementación
1. Sin datos de liquidaciones, la "regla dura" de Fase 3 debe adaptarse o eliminarse — no
   inventar un proxy sin avisar que es una sustitución, no el original.
2. `LI` (liquidity imbalance del heatmap) no existe para los 365 días — sustituir por
   `obi10_mean`/`obi20_mean` con el caveat explícito del README sobre licuación de M1.
3. Los pesos del score (10/8/6/6/4/2) son arbitrarios de la fuente — el backtest debe
   probar sensibilidad a estos pesos, no asumirlos correctos.
4. Esta es la hipótesis con mayor superficie de parámetros del catálogo — mayor riesgo de
   overfitting si se calibran todos simultáneamente en vez de probarlos con walk-forward
   estricto (igual rigor que ya se aplicó al resto del proyecto).

---
<a name="h3"></a>
## Hipótesis 3: Filtro — Delta Liderando en Contra

**Fuente**: "Patrones de entrada con ORDER FLOW" (Dimy/Subdimi).

### Lógica que argumenta el creador
No es una señal de entrada, es una regla de **abstención**. Si el precio llega a una zona de
interés (un nivel que el trader ya identificó por otro motivo: HTF, perfil, OB) y el delta
sigue empujando con fuerza en la dirección CONTRARIA al sesgo que se quería tomar, eso indica
que el mercado todavía no está "listo" para revertir ahí — operar de todas formas es "pararse
frente a un tren que viene embalado". La métrica de éxito de esta regla no es "cuánto gana
cuando se cumple", sino "cuántos stops evita" cuando NO se cumple y por eso no se entra.

### Condición
```
SEA bias = dirección que el sistema principal (cualquier otro setup) quiere tomar
SEA dz_reciente = delta z-score promedio de las últimas N velas (parámetro, default N=3)

SI bias == "long" Y dz_reciente <= -umbral:
    NO OPERAR (delta sigue liderando bajista en contra del long)
SI bias == "short" Y dz_reciente >= +umbral:
    NO OPERAR (delta sigue liderando alcista en contra del short)
```
`umbral` es un parámetro a calibrar; la fuente no da un número, sugerir punto de partida
1.5 (mismo orden de magnitud que `delta_expansion_dz` de la Hipótesis 2) y testear sensibilidad.

### Pseudocódigo
```python
def delta_against_filter(df_recent_n_bars, bias, dz_threshold=1.5):
    """
    df_recent_n_bars: las últimas N filas hasta la barra de entrada candidata.
    bias: "long" o "short" — el sesgo que el sistema principal quiere tomar.
    Retorna True si debe ABSTENERSE de operar.
    """
    dz_avg = df_recent_n_bars["dz"].mean()
    if bias == "long" and dz_avg <= -dz_threshold:
        return True   # abstenerse
    if bias == "short" and dz_avg >= dz_threshold:
        return True   # abstenerse
    return False
```

### Columnas necesarias
`dz` (ya existe en el dataset, Delta Z-score).

### Ejemplo numérico
Setup principal (ej. Hipótesis 1, Variante 1) da señal LONG en VAL. Pero las últimas 3 velas
M15 antes de la entrada candidata tienen `dz` promedio de -2.1 (venta agresiva fuerte y
sostenida). Con umbral 1.5: -2.1 <= -1.5 → **abstenerse**, no tomar el long aunque el setup
de nivel esté técnicamente cumplido.

### Cómo se usa (no es standalone)
Esta hipótesis NO genera trades por sí sola — es un **filtro de veto** que se aplica sobre
cualquier otra hipótesis de este catálogo que tenga un `bias` definido antes de la entrada.
En el backtest, debe probarse como capa opcional (ON/OFF) sobre cada hipótesis principal,
comparando métricas con y sin el filtro activo.

---
<a name="h4"></a>
## Hipótesis 4: Unfinished Action como Red Flag

**Fuente**: "Patrones de entrada con ORDER FLOW" (Dimy/Subdimi).

### Lógica que argumenta el creador
"Finish action" (acción terminada) se refiere a que un nivel de precio fue completamente
"limpiado" en términos de interés de trading — nadie más quiere operar ahí. "Unfinished
action" es lo opuesto: el nivel deja la sensación de que "falta algo", como si el mercado no
terminó de procesar ese precio. La fuente lo describe como un "imán para el precio" — si la
primera reacción en un nivel deja un unfinished, lo más probable es que el precio vuelva a
visitar ese nivel antes de continuar en la dirección original (aunque haya dado una reacción
inicial). Operar la PRIMERA reacción en un nivel con unfinished es entonces más arriesgado
que esperar a que el nivel se revisite y la segunda reacción no deje unfinished.

### Condición
```
SEA primera_reaccion = la vela (o pequeña secuencia de velas) donde el precio toca
                        el nivel de interés y empieza a alejarse

SI fp_unfinished_hi == True (si el toque fue desde abajo, dejando un máximo sin terminar)
   O fp_unfinished_lo == True (si el toque fue desde arriba, dejando un mínimo sin terminar)
   EN la vela de primera_reaccion:
    → RED FLAG: no operar esta primera reacción
    → marcar el nivel como "pendiente de revisita"
    → SOLO operar si el nivel es revisitado y la nueva reacción NO deja unfinished
```

### Pseudocódigo
```python
def unfinished_action_filter(bar_at_level, side):
    """
    bar_at_level: fila del dataset correspondiente a la vela de toque del nivel.
    side: "long" (toque desde abajo, mirando fp_unfinished_lo) o
          "short" (toque desde arriba, mirando fp_unfinished_hi)
    Retorna True si debe abstenerse (red flag) en esta reacción.
    """
    if side == "long" and bar_at_level["fp_unfinished_lo"]:
        return True
    if side == "short" and bar_at_level["fp_unfinished_hi"]:
        return True
    return False

def wait_for_clean_revisit(df, level_price, side, lookback_bars=50):
    """
    Busca la siguiente vez que el precio revisita level_price (dentro de una tolerancia)
    y verifica que esa segunda reacción NO tenga unfinished. Retorna el índice de esa
    vela limpia, o None si no ocurre dentro de lookback_bars.
    """
    # implementación: iterar hacia adelante desde la vela de primera reacción,
    # detectar cuándo el precio vuelve a tocar level_price (± tolerancia en ATR),
    # y chequear unfinished_action_filter en esa nueva vela.
    pass  # placeholder de estructura, implementar con la lógica de tolerancia del proyecto
```

### Columnas necesarias
`fp_unfinished_hi`, `fp_unfinished_lo` (ya existen en el dataset).

### Ejemplo numérico
Precio toca VAH ($105,800) por primera vez, vela cierra con `fp_unfinished_hi=True` (el
máximo de esa vela "no terminó" su distribución de volumen) → no operar el short ahí. Tres
horas después el precio vuelve a tocar $105,800-806, esta vez `fp_unfinished_hi=False` →
esta sí es una entrada válida según esta regla.

### Cómo se usa
Igual que la Hipótesis 3, es un **filtro/gate**, no una estrategia standalone. Se aplica
sobre la vela de entrada de cualquier hipótesis de este catálogo que opere en un nivel
específico (Hipótesis 1, 5, 6, 7, 13, etc.).

---
<a name="h5"></a>
## Hipótesis 5: Order Block con 6 Condiciones + Entrada en Cluster

**Fuente**: "Como identificar los mejores Order Blocks para operar con ORDERFLOW" (Dimy/Subdimi).

### Lógica que argumenta el creador
Un Order Block (concepto ICT) es, según la definición que cita la fuente (atribuida a
Michael Huddleston/ICT): la última vela bajista antes de un movimiento alcista (bullish OB) o
la última vela alcista antes de un movimiento bajista (bearish OB). El problema que identifica
el creador: la mayoría de traders SMC/ICT no distinguen un OB "real" (con traders realmente
atrapados dentro) de uno falso, porque solo miran la vela, no el volumen dentro de ella. La
propuesta es filtrar los OB candidatos con 6 condiciones de acción de precio, y luego, dentro
del OB ya validado, usar el footprint para encontrar el **cluster de mayor volumen** (no el
rango completo de la vela) como zona de entrada real y para definir un stop más ajustado.

### Las 6 condiciones de validación (todas deben cumplirse para considerar el OB válido)
```
1. Debe formar un swing high o swing low (ej. vía indicador de fractales / William Fractal)
2. Debe haberse generado en una zona de soporte/resistencia importante previa
3. Confirmación: el precio cierra por debajo (bearish OB) o por encima (bullish OB) del
   50% del rango del OB (nivel fibonacci 0.5 del high al low de la vela del OB)
4. Idealmente, cambio de estructura (BOS/CHoCH) en un timeframe menor a partir del
   movimiento que sale del OB
5. Idealmente, el movimiento que sale del OB genera imbalances/displacement (impulso con
   FVGs), señal de que el movimiento se está acelerando
6. Idealmente, el OB coincide con un nivel de retroceso 61.8% (fibonacci) o una zona OTE
   (Optimal Trade Entry, definida en ICT como la zona 62%-79% de retroceso)
```
Nota de la fuente: las condiciones 1-3 son obligatorias ("válido"); 4-6 son "idealmente"
(refuerzan la confianza pero no son estrictamente requeridas según el ejemplo que da el
propio creador, donde muestra un caso con las 6 cumplidas que de todas formas falla).

### Por qué puede fallar igual (ejemplo explícito de la fuente)
La fuente muestra un caso con las 6 condiciones cumplidas donde el precio rompe el OB sin
resistencia (stop loss directo). Al revisar el footprint, el volumen real estaba concentrado
en una zona específica dentro de la vela grande del OB, no distribuido en todo su rango — el
precio rompió porque la entrada se tomó en el rango completo del OB, no en el cluster real
de volumen.

### Modelo de entrada 1: zona de mayor volumen
```
1. Validar las 6 condiciones (al menos las 3 obligatorias)
2. Dentro de la vela (o velas) del OB, identificar la "pancita" del perfil de volumen
   (zona donde se concentra más volumen, no el rango completo de la vela)
3. Orden límite en esa zona de mayor volumen (no en el extremo del OB)
4. Stop por debajo/encima del rango completo del OB
```

### Modelo de entrada 2: patrón de vela envolvente en LTF dentro del OB (más agresivo)
```
1. Identificar zona de mayor volumen dentro del OB (igual que modelo 1)
2. Bajar a un timeframe menor (LTF) y buscar un patrón de vela envolvente (engulfing)
   que deje el volumen en la parte del rango opuesta a la dirección esperada
   (ej. para un bullish OB, buscar una envolvente que deje volumen en la parte baja)
3. Entrar al cierre de esa vela envolvente, O esperar un retest de esa zona de volumen
4. Stop por debajo del "bloque de órdenes fuerte" (el cluster específico), NO de la vela
   completa — esto da un R:R más ajustado y favorable
```

### Caso de múltiples Order Blocks superpuestos
```
SI hay 2+ OBs candidatos en la misma zona general:
    1. Combinar el volumen de ambas velas (no elegir uno u otro a priori)
    2. Marcar la zona de mayor volumen combinado (desde el inicio del volumen fuerte de
       la primera vela hasta el final del volumen fuerte de la vela siguiente)
    3. Usar esa zona combinada como zona de entrada
```

### Pseudocódigo
```python
def validate_order_block(df, ob_candidate_idx, direction):
    """
    direction: "bullish" o "bearish"
    Retorna dict con las 6 condiciones evaluadas (bool) y si pasa el mínimo (1-3 obligatorias).
    """
    bar = df.iloc[ob_candidate_idx]
    cond = {}

    # Condición 1: swing point (requiere lógica de fractales, no está directo en el dataset)
    cond["c1_swing_point"] = check_fractal_swing(df, ob_candidate_idx, direction)
    # [DERIVAR] — el dataset tiene swing_high_50/swing_low_50 que pueden aproximar esto,
    # pero no son fractales estrictos de N barras; documentar la sustitución si se usa.

    # Condición 2: zona de S/R previa importante
    cond["c2_prior_sr_zone"] = check_prior_support_resistance(df, ob_candidate_idx)
    # [DERIVAR] — usar near_pdh, near_weekly_high, equal_high/low, swing_high_50/low_50
    # como proxies de "zona importante previa".

    # Condición 3: cierre por debajo/encima del 50% del rango de la vela del OB
    ob_high, ob_low = bar["high"], bar["low"]
    ob_mid = (ob_high + ob_low) / 2
    next_bar = df.iloc[ob_candidate_idx + 1]  # vela de confirmación
    if direction == "bearish":
        cond["c3_close_below_50"] = next_bar["close"] < ob_mid
    else:
        cond["c3_close_above_50"] = next_bar["close"] > ob_mid

    # Condición 4 (idealmente): BOS/CHoCH en LTF
    cond["c4_structure_shift"] = check_h1_choch_bos(df, ob_candidate_idx, direction)
    # mapear a h1_choch_bear/h1_choch_bull/h1_bos_bear/h1_bos_bull del dataset

    # Condición 5 (idealmente): displacement con FVG
    cond["c5_displacement_fvg"] = bar.get("displacement_bear", False) or \
                                    df.iloc[ob_candidate_idx:ob_candidate_idx+5]["bearish_fvg_active"].any()

    # Condición 6 (idealmente): OTE/61.8 en la misma zona
    cond["c6_ote_confluence"] = bar.get("ote_62", False) or bar.get("fib_ote", False)

    obligatorias_ok = cond["c1_swing_point"] and cond["c2_prior_sr_zone"] and \
                       cond.get("c3_close_below_50", cond.get("c3_close_above_50"))
    return cond, obligatorias_ok


def find_volume_cluster_zone(df, ob_candidate_idx):
    """
    [NO DISPONIBLE en M1 agregado con precisión completa] — el dataset tiene fp_poc
    (Point of Control de la vela) como aproximación del "cluster de mayor volumen",
    pero no la distribución completa intra-vela por nivel de precio (eso requeriría
    la capa raw_trades o ob500, no presente para los 365 días).
    Aproximación viable: usar fp_poc como proxy del centro del cluster, y el rango de
    fp_n_levels alrededor de él como proxy del ancho del cluster — esto es una
    SUSTITUCIÓN, no el dato original, debe documentarse así en cualquier backtest.
    """
    bar = df.iloc[ob_candidate_idx]
    return bar["fp_poc"]  # aproximación, no el perfil completo
```

### Columnas necesarias
`swing_high_50`, `swing_low_50` (proxy de condición 1), `near_pdh`, `near_weekly_high`,
`equal_high`, `equal_low` (proxy de condición 2), `h1_choch_bear`, `h1_choch_bull`,
`h1_bos_bear`, `h1_bos_bull` (condición 4), `displacement_bear`, `bearish_fvg_active`,
`near_bearish_fvg` (condición 5), `ote_62`, `ote_79`, `fib_ote`, `fib_ote_london`,
`ote_rejection` (condición 6), `fp_poc` (proxy de cluster de volumen — ver caveat arriba).

### Ejemplo numérico
Vela candidata a bearish OB: high=$106,200, low=$105,900, close=$106,050 (alcista, último
impulso antes de la caída). Mid = $106,050. Vela siguiente cierra en $105,200 (< mid) →
condición 3 ✓. `swing_high_50=True` en esa vela → condición 1 ✓ (aproximado). `near_weekly_high
=True` → condición 2 ✓. → OB válido con las 3 obligatorias. `fp_poc` de esa vela = $106,000 →
zona de entrada short en $106,000 (no en $106,200, el extremo de la vela), con stop por
encima del high completo de la vela ($106,200) en el modelo conservador, o por encima del
cluster específico en el modelo agresivo.

### ⚠️ Caveat principal
La condición de "cluster de mayor volumen dentro de la vela" es la pieza más específica y
valiosa de esta hipótesis, pero el dataset M1 agregado **no tiene la resolución necesaria**
para reconstruirla con fidelidad — solo `fp_poc` como aproximación de un solo punto, no la
distribución completa. Para testear esto correctamente haría falta la capa `raw_trades` o
reconstruir un mini-footprint desde ahí, trabajo adicional no trivial.

---
<a name="h6"></a>
## Hipótesis 6: Big Trades — Fade en Balance vs. Continuación en Ruptura

**Fuente**: "Cómo Utilizo los BIG TRADES en el ORDERFLOW para operar con las INSTITUCIONES"
(Dimy/Subdimi), apoyado en Auction Market Theory (AMT).

### Lógica que argumenta el creador
La misma señal cruda (big trade, una orden de tamaño grande ejecutada a mercado) tiene
significado OPUESTO según el contexto de régimen:

- **En balance (rango)**: los big trades que aparecen en los extremos del rango suelen ser
  absorbidos (el precio no sigue esa dirección) → es señal de fade (operar en contra del
  big trade).
- **En ruptura de balance (la transición de rango a tendencia)**: los big trades que
  aparecen justo en el momento de la ruptura son "movimiento iniciativo" — traders/instituciones
  empujando activamente para encontrar un nuevo precio justo → es señal de continuación
  (operar a favor del big trade), con el stop justo "detrás" de esos big trades (si el
  precio vuelve a ese nivel, el movimiento se invalida).

El criterio para distinguir cuál de los dos casos aplica es Auction Market Theory: si el
mercado está "buscando un balance" (dentro de un rango ya establecido), favorecer fade. Si
está "rompiendo" ese balance (saliendo de la zona de valor), favorecer continuación.

### Condición
```
SEA regime_local = "BALANCE" si el precio está dentro de un rango ya identificado
                    (ej. vp_vah/vp_val recientes, o regime != "Expansion"/"TrendUp"/"TrendDown")
SEA regime_local = "RUPTURA" si el precio acaba de salir de ese rango
                    (cruce reciente de vp_vah o vp_val, o regime == "Expansion")

SI regime_local == "BALANCE" Y aparece big_trade en extremo del rango:
    → señal FADE (operar en contra del big trade)
SI regime_local == "RUPTURA" Y aparece big_trade en la dirección de la ruptura:
    → señal CONTINUACIÓN (operar a favor del big trade)
    stop = justo detrás del nivel donde aparecieron los big trades de ruptura
```

### Pseudocódigo
```python
def classify_big_trade_context(df, idx, lookback_bars=20):
    """
    Determina si estamos en balance o en ruptura reciente, y si hay big trade activo.
    """
    bar = df.iloc[idx]
    recent = df.iloc[max(0, idx - lookback_bars):idx]

    in_balance = bar["regime"] not in ["Expansion", "TrendUp", "TrendDown"]
    # ruptura reciente: regime cambió de no-Expansion a Expansion/Trend en los últimos N bars
    recent_breakout = (recent["regime"].isin(["Expansion","TrendUp","TrendDown"]).any()
                        and not in_balance)

    has_big_trade = bar["big_trade_bullish"] or bar["big_trade_bearish"]

    if in_balance and has_big_trade:
        # ¿está en extremo del rango? usar obi/vp_vah/vp_val como proxy de "extremo"
        at_extreme = (bar["close"] >= bar["vp_vah"] * 0.998 or
                      bar["close"] <= bar["vp_val"] * 1.002)
        if at_extreme:
            return "FADE", "bearish" if bar["big_trade_bullish"] else "bullish"
            # fade: si fue compra grande sin éxito -> sesgo bajista, y viceversa

    if recent_breakout and has_big_trade:
        direction = "bullish" if bar["big_trade_bullish"] else "bearish"
        return "CONTINUATION", direction

    return None, None
```

### Columnas necesarias
`big_trade_bullish`, `big_trade_bearish`, `max_trade`, `regime`, `vp_vah`, `vp_val`.

### Ejemplo numérico
Precio dentro de un rango establecido ($102k-$106k), `regime="Chop"`. Aparece `big_trade_bullish
=True` cerca de $105,900 (extremo alto del rango, cerca de `vp_vah=$105,800`). Como estamos en
BALANCE y el big trade está en el extremo → señal FADE: sesgo bajista (operar short, esperando
que esa compra grande sea absorbida y el precio rote de vuelta al rango).

Contraste: precio rompe $106,000 con `regime` cambiando a "Expansion", aparece `big_trade_bullish
=True` en $106,300 (en la dirección de la ruptura, no en un extremo de rango previo) → señal
CONTINUACIÓN: sesgo alcista, stop por debajo de $106,300 (si vuelve ahí, invalida la ruptura).

### ⚠️ Caveat
La distinción "balance vs ruptura" es la pieza más subjetiva de esta hipótesis — la fuente no
da una regla cuantitativa exacta de cuándo algo deja de ser "balance" y pasa a ser "ruptura
confirmada". La aproximación con `regime` del dataset es razonable pero es una sustitución,
no una traducción literal de la fuente.

---
<a name="h7"></a>
## Hipótesis 7: LVN = Entrada, HVN/POC = Target

**Fuente**: "Guía Completa del Perfil de Volumen en Español 2026" (Dimy/Subdimi).

### Lógica que argumenta el creador
Una Low Volume Node (LVN) es una zona de precio donde poco volumen se negoció — el precio
"atravesó rápido" esa zona porque pocos participantes operaron ahí. La fuente argumenta que,
precisamente por eso, cuando el precio entra ahí de nuevo, suele "entrar gente que algo sabe"
(traders informados aprovechando la falta de competencia de órdenes) y reaccionar fuerte. Por
eso las LVN son buenas zonas de ENTRADA. Las High Volume Nodes (HVN, especialmente el POC,
el nivel de mayor volumen) son zonas donde "hay mucho acuerdo de precio justo" — el precio
tiende a frenar/consolidar ahí, por eso son mejores como zona de TARGET (toma de ganancias),
no de entrada nueva.

### Condición
```
ENTRADA: precio se aproxima a una LVN identificada (zona de bajo volumen dentro de un
         perfil de volumen ya trazado)
TARGET:  el siguiente HVN/POC en la dirección del trade
```

### Pseudocódigo
```python
def find_lvn_entry_and_hvn_target(df, idx, direction, window_bars=200):
    """
    Aproximación con las columnas disponibles del dataset.
    direction: "long" o "short"
    """
    recent = df.iloc[max(0, idx - window_bars):idx]
    vp_lvn = recent["vp_lvn_below"].iloc[-1]  # LVN ya identificado por el dataset
    vp_poc_target = recent["vp_poc"].iloc[-1]  # POC como target

    bar = df.iloc[idx]
    near_lvn = abs(bar["close"] - vp_lvn) / bar["close"] < 0.003  # tolerancia 0.3%, parámetro

    if near_lvn:
        return {
            "entry_zone": vp_lvn,
            "target": vp_poc_target,
            "direction": direction
        }
    return None
```

### Columnas necesarias
`vp_lvn_below` (ya existe), `vp_poc` (ya existe). Nota: el dataset solo trae `vp_lvn_below`
(LVN por debajo del precio actual) — no hay columna equivalente para LVN por encima; si se
necesita LVN al alza, hay que derivarla o limitar esta hipótesis a setups de reversión desde
abajo.

### Ejemplo numérico
Precio cae hacia $103,200, que coincide con `vp_lvn_below=$103,150` (zona de bajo volumen
identificada en el perfil reciente). Entrada long en esa zona, target = `vp_poc=$104,800`
(el nivel de mayor volumen más cercano por encima).

---
<a name="h8"></a>
## Hipótesis 8: Merge de Perfiles de Volumen Contiguos

**Fuente**: "Guía Completa del Perfil de Volumen en Español 2026" (Dimy/Subdimi).

### Lógica que argumenta el creador
Cuando el perfil de volumen de un día (o sesión) se solapa o está contenido dentro del
perfil del día anterior, tratarlos como dos zonas de valor separadas pierde información —
"están diciendo lo mismo". Fusionar ambos perfiles (sumar el volumen de ambos períodos y
recalcular un único POC/VAH/VAL combinado) da un nivel "más certero" porque representa
acumulación de interés en esa zona durante más tiempo, no solo un día.

### Condición
```
SI el rango de precio del perfil de hoy (vp_vah_hoy a vp_val_hoy) se solapa
   significativamente con el rango del perfil de ayer (vp_vah_ayer a vp_val_ayer)
   (ej. solapamiento > 50% del rango menor):
    → fusionar: recalcular POC/VAH/VAL combinados usando el volumen de ambos períodos
    → usar el perfil fusionado, no los dos perfiles separados, para definir niveles
```

### Pseudocódigo
```python
def should_merge_profiles(vah_today, val_today, vah_prev, val_prev, overlap_threshold=0.5):
    range_today = vah_today - val_today
    range_prev = vah_prev - val_prev
    overlap_low = max(val_today, val_prev)
    overlap_high = min(vah_today, vah_prev)
    overlap = max(0, overlap_high - overlap_low)
    smaller_range = min(range_today, range_prev)
    if smaller_range <= 0:
        return False
    return (overlap / smaller_range) >= overlap_threshold

def merge_volume_profiles(volume_by_price_today, volume_by_price_prev):
    """
    [NO DISPONIBLE directamente — el dataset solo tiene vp_poc/vp_vah/vp_val ya calculados,
    no la distribución de volumen por nivel de precio necesaria para recalcular un POC
    fusionado correctamente. Esta función requeriría la capa raw_trades o ob_1s para
    reconstruir el perfil completo de cada día y luego sumarlos.]
    raise NotImplementedError("Requiere distribución de volumen por precio, no solo POC/VAH/VAL")
```

### Columnas necesarias
`vp_poc`, `vp_vah`, `vp_val` por día — pero el **merge real requiere la distribución
completa de volumen por nivel de precio**, que no está en el M1 agregado. Una aproximación
burda (promediar los POC de los días que se solapan, ponderado por volumen total del día)
es posible pero es una sustitución notablemente más débil que el merge real que describe
la fuente.

### Ejemplo numérico
Día D-1: POC=$104,500, VAH=$106,000, VAL=$103,000. Día D-2: POC=$104,700, VAH=$105,800,
VAL=$103,200. Solapamiento de rangos: prácticamente total → fusionar. Aproximación con
promedio ponderado por volumen total de cada día (si `volume` diario está disponible):
POC fusionado ≈ promedio ponderado de 104,500 y 104,700.

### ⚠️ Caveat
Esta es, junto con la Hipótesis 5 (cluster de volumen) la hipótesis con **menor fidelidad
posible** dado el dataset actual — el merge real de perfiles de volumen necesita la
distribución completa, no solo 3 puntos resumen (POC/VAH/VAL) por día.

---
<a name="h9"></a>
## Hipótesis 9: POC Desplazándose como Detector de Fuerza de Tendencia

**Fuente**: "Perfil de Volumen: Aprende a Leer el Contexto con Áreas de Valor" (Dimy/Subdimi).

### Lógica que argumenta el creador
En vez de usar un indicador técnico (pendiente de EMA, ATR) para definir si una tendencia
está "intacta", esta hipótesis propone mirar la secuencia de POCs de períodos consecutivos
(semana a semana, o día a día). Si cada nuevo período establece su POC más alto que el
anterior, la tendencia alcista está intacta (el "valor justo" se sigue desplazando hacia
arriba, evidencia de demanda sostenida). El primer período donde el POC se establece MÁS
BAJO que el anterior es la primera señal de debilidad — no significa que la tendencia ya
cambió, pero es una alerta temprana antes de que el precio mismo confirme un cambio de
estructura.

### Condición
```
SEA poc_serie = [poc_periodo_1, poc_periodo_2, ..., poc_periodo_N]  (orden cronológico,
                 por ejemplo POC semanal de cada semana)

SI poc_periodo_N > poc_periodo_(N-1):
    tendencia_alcista_intacta = True
SI poc_periodo_N <= poc_periodo_(N-1)  Y  poc_periodo_(N-1) > poc_periodo_(N-2):
    → primera señal de debilidad (el POC dejó de subir tras venir subiendo)
    → no implica reversión confirmada, solo alerta
```

### Pseudocódigo
```python
def detect_poc_weakness(poc_series, lookback=3):
    """
    poc_series: lista de POCs por período (ej. semanal), orden cronológico.
    Retorna "BULLISH_INTACT", "BEARISH_INTACT", "FIRST_WEAKNESS_SIGNAL", o "NEUTRAL"
    """
    if len(poc_series) < lookback + 1:
        return "NEUTRAL"  # datos insuficientes

    recent = poc_series[-lookback-1:]
    diffs = [recent[i+1] - recent[i] for i in range(len(recent)-1)]

    was_rising = all(d > 0 for d in diffs[:-1])  # todos los anteriores subían
    last_failed = diffs[-1] <= 0                  # el último no subió

    if was_rising and not last_failed:
        return "BULLISH_INTACT"
    if was_rising and last_failed:
        return "FIRST_WEAKNESS_SIGNAL"
    # simétrico para bajista (invertir signos)
    was_falling = all(d < 0 for d in diffs[:-1])
    last_failed_down = diffs[-1] >= 0
    if was_falling and not last_failed_down:
        return "BEARISH_INTACT"
    if was_falling and last_failed_down:
        return "FIRST_WEAKNESS_SIGNAL"
    return "NEUTRAL"
```

### Columnas necesarias
`vp_poc` agregado por período (día/semana — requiere resampleo desde M1/H1, tomando el
último `vp_poc` válido de cada período, igual lógica de congelado que en la Hipótesis 1).

### Ejemplo numérico
POC semanal: semana 1 = $98,000, semana 2 = $101,500, semana 3 = $104,200, semana 4 =
$103,800. Las semanas 1→2→3 venían subiendo (`was_rising=True`), semana 4 no superó a la 3
(`last_failed=True`) → **FIRST_WEAKNESS_SIGNAL** en la semana 4, aunque el precio en sí
podría no haber roto ninguna estructura todavía.

### Cómo se usa
Esta hipótesis es un **filtro de contexto/régimen**, similar en función a `regime` del
dataset pero con lógica distinta (desplazamiento de valor vs. pendiente de EMA/ATR). Útil
para combinar con otras hipótesis de este catálogo como condición de sesgo direccional, no
como señal de entrada standalone.

---
<a name="h10"></a>
## Hipótesis 10: Velas de Volumen como Confirmación Cruzada

**Fuente**: "Velas de Volumen - Cómo Uso el Order Flow para Mis Entradas" (Dimy/Subdimi).

### Lógica que argumenta el creador
Las velas de tiempo (M1, M5, etc.) cierran por transcurso de tiempo fijo. Las "velas de
volumen" (tipo Renko/Range/Trend Reversal bars) cierran cuando se acumula determinada
cantidad de volumen, sin importar cuánto tiempo tarde — pueden tardar 10 minutos o 1 minuto
según la actividad del mercado. La fuente argumenta que un cambio de estructura confirmado
en velas de volumen representa "interés real" (volumen empujando el cambio), mientras que
uno en velas de tiempo puede ser solo coincidencia temporal. La estrategia es usar AMBAS en
conjunto: a veces el cambio de estructura aparece primero en velas de tiempo (entrada
temprana, confirmar con volumen después) y a veces aparece primero en velas de volumen
(confirmación adelantada de lo que las velas de tiempo mostrarán después). Configuraciones
sugeridas por la fuente: "44-26" para cripto, "9-6-6-4" como alternativa más tardía/confirmada,
"3-1" para índices (mercados con mucho más volumen, necesitan umbral más bajo para no llegar
tarde).

### Condición
```
SI cambio_estructura(velas_tiempo) Y NO cambio_estructura(velas_volumen):
    → señal de tiempo sin confirmar, considerar entrada arriesgada o esperar confirmación,
      o salir/no agregar más si las velas de volumen nunca confirman
SI cambio_estructura(velas_tiempo) Y cambio_estructura(velas_volumen):
    → ambas confirman: entrada de alta probabilidad
SI cambio_estructura(velas_volumen) ANTES que velas_tiempo:
    → señal de volumen adelanta la entrada, puede usarse como entrada anticipada
```

### Pseudocódigo (conceptual — requiere construcción previa de la capa de velas de volumen)
```python
def build_volume_bars(raw_trades_df, volume_threshold):
    """
    Construye velas de volumen desde tick data (raw_trades). Cada vela acumula trades
    hasta alcanzar volume_threshold, luego cierra y abre la siguiente.
    """
    bars = []
    current_bar = {"open": None, "high": -float("inf"), "low": float("inf"),
                    "close": None, "volume": 0, "start_ts": None}
    for _, trade in raw_trades_df.iterrows():
        if current_bar["open"] is None:
            current_bar["open"] = trade["price"]
            current_bar["start_ts"] = trade["ts_ms"]
        current_bar["high"] = max(current_bar["high"], trade["price"])
        current_bar["low"] = min(current_bar["low"], trade["price"])
        current_bar["close"] = trade["price"]
        current_bar["volume"] += trade["size"]
        if current_bar["volume"] >= volume_threshold:
            bars.append(current_bar.copy())
            current_bar = {"open": None, "high": -float("inf"), "low": float("inf"),
                            "close": None, "volume": 0, "start_ts": None}
    return pd.DataFrame(bars)

def cross_confirm_structure_change(time_bars_signal, volume_bars_signal, lookback_window):
    """
    Compara si ambos tipos de barras confirman un cambio de estructura dentro de la
    misma ventana temporal. Requiere alinear timestamps entre ambas series.
    """
    pass  # estructura de comparación, implementación depende de cómo se defina
          # "cambio de estructura" formalmente (BOS/CHoCH ya existen para time bars
          # en el dataset: h1_bos_bear, h1_choch_bear, etc., pero NO existen para
          # velas de volumen, que habría que calcular desde cero)
```

### Columnas necesarias
**[NO DISPONIBLE en el dataset procesado actual]**. Requiere construir la capa de velas de
volumen desde `raw_trades/YYYY-MM-DD.parquet` (365 archivos diarios, 2.3GB total según el
inventario original), que no está descargada/subida en este proyecto. Es trabajo de
construcción de datos antes de poder testear esta hipótesis, no solo de backtest.

### Ejemplo numérico
Configuración "44-26" para cripto (el significado exacto de estos dos números no se explica
del todo en la fuente — probablemente un par de umbrales de volumen para detectar reversal
en ambas direcciones, o dos parámetros de un indicador específico de la plataforma Exocharts
que usa el creador). **Esto debe aclararse con el creador o por prueba empírica antes de
implementar — no asumir el significado.**

### ⚠️ Caveat
Esta es la hipótesis de mayor costo de implementación de todo el catálogo: no solo hay que
testear una regla, hay que construir una capa de datos completamente nueva primero. Evaluar
si vale la pena el esfuerzo antes de las demás hipótesis más baratas de probar.

---
<a name="h11"></a>
## Hipótesis 11: Confluencia Discrecional Multi-Nivel

**Fuente**: "Como formular tu propia estrategia" (Dimy/Subdimi).

### Lógica que argumenta el creador
No es una regla única sino una filosofía: cuantos más "tipos" de herramientas distintas
(fibonacci, naked POC, área de valor, líneas de canal, armónicos) coincidan en el mismo
nivel de precio, mayor la "calidad" del setup. El creador da ejemplos de sus propios trades
donde 2-4 herramientas distintas coinciden en la misma zona y argumenta que esos son sus
mejores trades históricos. Explícitamente dice que "no hay absolutos" y que cada trader debe
encontrar su propia combinación favorita mediante journaling personal.

### Por qué esto es difícil de operacionalizar como regla binaria
A diferencia de las demás hipótesis del catálogo, esta no especifica:
- Qué conjunto fijo de herramientas usar (el creador menciona fibonacci, naked POC, área de
  valor, canales, armónicos, sin una lista cerrada)
- Cuántas deben coincidir como mínimo
- Qué tan cerca en precio cuenta como "coincidencia" (tolerancia)
- Si todas las herramientas pesan igual o algunas más que otras

### Aproximación mínima operacionalizable (supuesto a validar, no literal de la fuente)
```python
def count_confluences(bar, tolerance_pct=0.003):
    """
    Cuenta cuántos niveles HTF distintos del dataset coinciden dentro de una tolerancia
    alrededor del precio actual. Esto es una operacionalización mínima y arbitraria de
    la idea de "confluencia" — NO es una traducción literal de la fuente, que es
    deliberadamente discrecional y no cuantificada.
    """
    price = bar["close"]
    levels = {
        "prev_day_high": bar["prev_day_high"],
        "prev_day_low": bar["prev_day_low"],
        "weekly_high": bar["weekly_high"],
        "weekly_low": bar["weekly_low"],
        "vp_poc": bar["vp_poc"],
        "vp_vah": bar["vp_vah"],
        "vp_val": bar["vp_val"],
        "asian_high": bar["asian_high"],
        "asian_low": bar["asian_low"],
        "equal_high": bar["equal_high"] if bar.get("equal_high") else None,
        "equal_low": bar["equal_low"] if bar.get("equal_low") else None,
    }
    count = 0
    matched = []
    for name, level in levels.items():
        if level is not None and abs(price - level) / price <= tolerance_pct:
            count += 1
            matched.append(name)
    return count, matched
```

### Columnas necesarias
Todas las columnas de nivel de precio HTF ya listadas en secciones anteriores del proyecto
(`prev_day_high/low`, `weekly_high/low`, `vp_poc/vah/val`, `asian_high/low`, `equal_high/low`).

### Ejemplo numérico
Precio en $104,800. `vp_poc=$104,750` (diff 0.05%), `weekly_low=$104,900` (diff 0.10%),
`prev_day_low=$104,820` (diff 0.02%) — con tolerancia 0.3%, las 3 caen dentro → 3 confluencias
detectadas en esa zona.

### ⚠️ Caveat — la más importante de esta hipótesis
Cualquier umbral de "cuántas confluencias son suficientes" y "qué tolerancia de precio cuenta"
es **inventado para fines de operacionalización**, no viene de la fuente. Esta hipótesis tiene
el mayor riesgo de todo el catálogo de terminar siendo un ejercicio de overfitting de
parámetros sin respaldo conceptual claro — tratarla con el mayor escepticismo del catálogo.

---
<a name="h12"></a>
## Hipótesis 12: Delta Absorbido Intrabar (max−close)

**Fuente**: "Traders Atrapados - Cómo Identificarlos con Order Flow" (Dimy/Subdimi).

### Lógica que argumenta el creador
El delta final de una vela (close) puede ocultar lo que pasó DURANTE la vela. El ejemplo
concreto de la fuente: una vela con delta negativo máximo intrabar de -26 millones, pero que
CIERRA en -18 millones — la diferencia (8 millones) es delta negativo que fue "absorbido"
por órdenes límite de compra durante el transcurso de la vela, no se ve reflejado en el delta
de cierre porque entremedio aparecieron compras agresivas que lo compensaron parcialmente.
Comparar este "delta absorbido" (max - close) contra el delta absorbido de velas recientes da
una medida de cuán inusual es ese evento — si es mucho mayor a lo normal, es señal más fuerte
de traders atrapados que mirar solo el delta de cierre.

### Condición
```
delta_absorbido = |delta_max_intrabar| − |delta_close|
SI delta_absorbido >> delta_absorbido_tipico_reciente (ej. z-score alto vs. ventana N):
    → señal más fuerte de absorción que el delta de cierre solo
```

### Pseudocódigo
```python
def compute_intrabar_absorbed_delta(tick_data_for_bar):
    """
    Requiere acceso a datos tick-a-tick dentro de la vela (no solo el delta final agregado).
    """
    cum_delta = 0
    min_delta = 0
    max_delta = 0
    for trade in tick_data_for_bar:
        cum_delta += trade["size"] if trade["side"] == "Buy" else -trade["size"]
        min_delta = min(min_delta, cum_delta)
        max_delta = max(max_delta, cum_delta)
    close_delta = cum_delta
    absorbed_negative = abs(min_delta) - abs(close_delta) if min_delta < 0 else 0
    absorbed_positive = abs(max_delta) - abs(close_delta) if max_delta > 0 else 0
    return absorbed_negative, absorbed_positive
```

### Columnas necesarias
**[NO DISPONIBLE en el dataset M1 agregado]**. La capa M1 solo tiene `delta` (valor final
de cierre por vela), no el mínimo/máximo intrabar acumulado. Esto requiere reconstruir desde
`raw_trades` igual que la Hipótesis 10 — mismo costo de construcción de datos.

### Ejemplo numérico (el de la fuente)
Vela con delta máximo negativo intrabar = -26,000,000, delta de cierre = -18,000,000 →
delta absorbido = 26M - 18M = **8,000,000** de delta negativo "absorbido" por compradores
pasivos durante la vela — información que el delta de cierre (-18M) solo no comunica.

### ⚠️ Caveat
Mismo problema de costo que la Hipótesis 10: requiere construir una columna nueva desde
datos tick-a-tick no presentes en el dataset procesado actual. Evaluar si vale la pena junto
con la Hipótesis 10, ya que ambas comparten el mismo prerrequisito de datos.

---
<a name="h13"></a>
## Hipótesis 13: LVN + Defensa de Orden Pasiva con Validación de Fill

**Fuente**: "How To Trade Real Fair Value Gaps with Extreme Accuracy" — Carmine Rosato.

### Lógica que argumenta el creador
Variante mucho más exigente que la Hipótesis 7. El proceso de 4-5 pasos:
1. Identificar un nivel de soporte/resistencia/demanda/oferta previo (un swing significativo).
2. Esperar a que el precio **regrese** a ese nivel (no operar el primer toque).
3. **Validación crítica**: en el retest, confirmar que hay una orden pasiva grande siendo
   efectivamente "rellenada" (filled) — se observa como una ráfaga de volumen agresivo en
   contra del nivel (ej. mucha venta a mercado) que NO logra mover el precio porque está
   siendo absorbida por una orden de compra pasiva grande. El argumento explícito del
   creador: "no se puede falsificar cuando se rellena así" — a diferencia de simplemente
   mirar el tamaño de las órdenes en el libro (que pueden ser spoofing/fake), ver el volumen
   EJECUTÁNDOSE contra esa orden es prueba de que es real, porque un trade completado
   requiere que ambas partes (agresor y pasivo) hayan participado de verdad.
4. Stop loss por debajo de los mínimos donde ocurrió esa absorción.
5. Target = el siguiente LVN en la dirección del trade (la idea de la Hipótesis 7, pero acá
   el creador la enfatiza con el agregado de "si el mercado recupera esa área de bajo
   volumen, ahí es donde se obtiene una continuación muy grande" — sugiere mantener
   parciales para approach más agresivo si el precio valida superando el LVN).

### Condición (idealizada — requiere datos no disponibles en M1, ver caveat)
```
1. nivel_interes = swing significativo previo (soporte/resistencia)
2. SI precio retest nivel_interes:
3.     SI volumen_agresivo_en_contra(nivel_interes) ES grande
           Y precio NO se mueve proporcionalmente (queda "pegado" al nivel)
           Y el tamaño ejecutado contra la orden pasiva es consistente entre múltiples
             ráfagas (no solo un trade aislado):
4.         validación_orden_pasiva = True
5.         ENTRAR en dirección de defensa del nivel
6.         stop = mínimo/máximo de esa zona de absorción
7.         target = siguiente LVN en la dirección del trade
```

### Pseudocódigo (aproximación con datos M1, con caveat fuerte de fidelidad)
```python
def carmine_lvn_defense_setup(df, idx, level_price, direction, window_bars=5):
    """
    Aproximación con columnas M1 agregadas. La validación "real" de Carmine requiere
    ver ejecuciones tick-a-tick contra una orden pasiva específica (nivel L2/DOM),
    que el dataset M1 no tiene. Esta es una sustitución burda usando abs_ask/abs_bid
    (columnas de absorción ya presentes en el dataset) como proxy.
    """
    window = df.iloc[idx:idx+window_bars]
    price_at_level = abs(window["close"] - level_price) / level_price < 0.003

    if direction == "long":
        absorption_signal = window["abs_bid"].any()  # absorción de venta en el bid
    else:
        absorption_signal = window["abs_ask"].any()  # absorción de compra en el ask

    if price_at_level.any() and absorption_signal:
        return True
    return False
```

### Columnas necesarias
`abs_bid`, `abs_ask` (ya existen como proxy aproximado — el dataset las describe como
"absorción de venta/compra detectada"), `vp_lvn_below`, swing levels existentes
(`swing_high_50`, `swing_low_50`, `equal_high`, `equal_low`).

### ⚠️ Caveat — el más importante de esta hipótesis
La pieza central del argumento de Carmine ("ver el volumen ejecutándose contra la orden
pasiva, no solo que aparezca en el libro") es **microestructura de nivel L2/tick**, y tu
propio README ya advierte: *"OBI/microprice agregados a M1 licúan la microestructura (un
sweep dura ~15s). Para señal micro real, usar la capa ob_1s / raw_trades directamente, no
el agregado M1."* Las columnas `abs_bid`/`abs_ask` del M1 son la mejor aproximación
disponible sin tocar capas más finas, pero son una sustitución importante, no el dato
original que describe Carmine.

---
<a name="h14"></a>
## Hipótesis 14: Niveles Psicológicos Redondos (80/20)

**Fuente**: "La estrategia de Prop Firm mejor pagada — 70% Win Rate" — trader "Okala".

### Lógica que argumenta el creador
Específico para Nasdaq futures, observación empírica del propio creador tras años de
screen-time: los precios terminados en "80" y "20" (de cada centena, ej. 25,680 / 25,620)
actúan como niveles psicológicos de reacción frecuente — no por una razón fundamental, sino
porque suficientes participantes del mercado (humanos y posiblemente algoritmos calibrados
en esos niveles) colocan órdenes ahí, generando una profecía auto-cumplida de reacción. El
creador es explícito en que esto es "reversion trading" — funciona mejor en mercado "choppy"
(lateral), no en tendencia fuerte. Regla mecánica de gestión: SL fijo de 10 puntos siempre
(no basado en ATR ni estructura), TP1 fijo de 15 puntos, mover a breakeven tras TP1, dejar
correr el resto con trailing discrecional.

### Condición
```
SEA nivel = múltiplo cercano de 80 o 20 dentro de cada centena del precio actual
            (ej. para BTC, el equivalente podría ser múltiplos de $X00 — necesita
            recalibración, NO es directamente portable de Nasdaq a BTC sin ajuste)

SI precio toca nivel Y da una reacción INMEDIATA (no espera, la fuente es explícita en
   que debe ser instantánea):
    ENTRAR en la dirección de la reacción
    SL = 10 puntos (en unidades de Nasdaq; para BTC, recalibrar a unidad equivalente,
         posiblemente en términos de ATR o % de precio, NO directamente "10 dólares")
    TP1 = 15 puntos, breakeven tras TP1
    TP2 = más profundo, según contexto de estructura (la fuente usa "previous cross
          section" — niveles previos no testeados — como target secundario)
```

### Pseudocódigo
```python
def find_round_number_level(price, round_to=100, sub_levels=[20, 80]):
    """
    Encuentra el nivel redondo más cercano según la convención 80/20.
    Para BTC, round_to y sub_levels deben recalibrarse empíricamente —
    NO asumir que los mismos números de Nasdaq aplican a BTC.
    """
    base = (price // round_to) * round_to
    candidates = [base + s for s in sub_levels] + [base - round_to + s for s in sub_levels]
    return min(candidates, key=lambda x: abs(x - price))

def instant_reaction_check(df, idx, level, direction, max_bars_to_react=1):
    """
    La fuente exige reacción INMEDIATA al toque, no esperar varias velas.
    """
    bar = df.iloc[idx]
    touched = abs(bar["low"] - level) / level < 0.001 or abs(bar["high"] - level) / level < 0.001
    if not touched:
        return False
    next_bar = df.iloc[idx + 1] if idx + 1 < len(df) else None
    if next_bar is None:
        return False
    if direction == "long":
        return next_bar["close"] > bar["close"]
    else:
        return next_bar["close"] < bar["close"]
```

### Columnas necesarias
Solo `close`, `high`, `low` — no requiere ninguna columna de orderflow especial. Es la
hipótesis más simple de implementar técnicamente de todo el catálogo, aunque la
recalibración de "80/20" a BTC es una decisión de diseño abierta.

### Ejemplo numérico
BTC cotizando en $104,920. Si se define el equivalente como múltiplos de $100 con
sub-niveles en .80/.20 de cada $100 (ej. $104,980 / $104,920) — esto es una hipótesis de
recalibración a probar empíricamente, no algo que la fuente garantice que funcione igual
en cripto.

### ⚠️ Caveat
Esta hipótesis es 100% específica de microestructura de Nasdaq futures (tick size, comportamiento
de algoritmos de market makers calibrados a esos instrumentos). **No hay garantía conceptual
de que BTC perpetuo tenga el mismo comportamiento en niveles redondos** — sería necesario un
escaneo univariate dedicado (similar al ya hecho en este proyecto) sobre distintas
convenciones de "nivel redondo" para BTC (¿múltiplos de $100? ¿$500? ¿$1000?) antes de asumir
que esta hipótesis tiene siquiera sentido en este mercado.

---
<a name="h15"></a>
## Hipótesis 15: Repair Candle como Gatillo de Re-entrada

**Fuente**: misma transcripción que Hipótesis 14 — trader "Okala".

### Lógica que argumenta el creador
Una "repair candle" es una vela que no deja mecha (o deja una mecha mínima) al recuperar un
nivel — el argumento es que eso indica que "se recogieron todas las órdenes que no pudieron
participar en el precio anterior" (liquidez que quedó sin ejecutar en el movimiento previo).
Cuando el precio vuelve a esa zona de "reparación" después, es una oportunidad de re-entrada
en la misma dirección del trade original, porque la zona ya demostró ser un punto de
interés institucional.

### Condición
```
SEA repair_candle = vela donde el cuerpo cubre prácticamente todo el rango
                     (mecha mínima, ej. < 10% del rango total de la vela — parámetro
                     a calibrar, la fuente no da un número exacto)

SI aparece repair_candle EN dirección del bias:
    marcar esa zona como "punto de reparación"
SI el precio retest esa zona de reparación posteriormente:
    → señal de re-entrada en la dirección original
```

### Pseudocódigo
```python
def is_repair_candle(bar, max_wick_pct=0.10):
    total_range = bar["high"] - bar["low"]
    if total_range <= 0:
        return False
    body = abs(bar["close"] - bar["open"])
    wick_total = total_range - body
    return (wick_total / total_range) <= max_wick_pct

def find_repair_reentry(df, idx, repair_zone_price, direction, tolerance_pct=0.002):
    bar = df.iloc[idx]
    near_repair_zone = abs(bar["close"] - repair_zone_price) / repair_zone_price <= tolerance_pct
    return near_repair_zone
```

### Columnas necesarias
Solo `open`, `high`, `low`, `close` — no requiere columnas de orderflow especiales, aunque
podría reforzarse opcionalmente con `delta`/`dz` de esa vela para confirmar direccionalidad.

### Ejemplo numérico
Vela con open=$104,000, close=$104,480, high=$104,500, low=$103,980. Rango total=$520,
cuerpo=$480, mecha total=$40 → wick_pct=7.7% < 10% → **repair candle válida**. Si el precio
retest la zona ~$104,250 (mitad del cuerpo) más tarde, esa es la zona de re-entrada.

---
<a name="h16"></a>
## Hipótesis 16: Bid Refill Creciente como Señal de Convicción

**Fuente**: "LIVE Trading with a $1M Order Flow Trader" — Jay Ortani (acciones NVDA/TSLA,
no crypto, pero lógica portable).

### Lógica que argumenta el creador
No basta con detectar UN big trade aislado como señal de absorción. El argumento específico
de Jay Ortani: cuando una orden pasiva grande se "rellena" (refill) repetidamente en el
MISMO nivel de precio, y el tamaño de los refills sucesivos va CRECIENDO (ej. su ejemplo:
primer refill de 12,000 acciones, segundo refill de 37,000 acciones en el mismo nivel), eso
es evidencia mucho más fuerte de comprador/vendedor institucional comprometido que un solo
trade grande aislado — sugiere que el participante grande está "aumentando su posición" en
lugar de simplemente haber colocado una orden una vez.

### Condición
```
SEA refills_en_nivel = lista de tamaños de big trades sucesivos en el mismo nivel de precio
                        (dentro de una tolerancia), ordenados cronológicamente

SI len(refills_en_nivel) >= 2  Y  refills_en_nivel[-1] > refills_en_nivel[-2]:
    → señal de convicción CRECIENTE en ese nivel (más fuerte que un solo big trade)
```

### Pseudocódigo (aproximación — el dataset no tiene tamaño exacto de órdenes pasivas
### rellenadas, solo flags de big trade direccional)
```python
def detect_growing_refill(df, level_price, lookback_bars=30, tolerance_pct=0.002):
    """
    Aproximación: usar max_trade (tamaño del mayor trade de cada vela) en velas
    sucesivas que toquen el mismo nivel, y verificar si la secuencia es creciente.
    Esto es un proxy de "refill creciente", no la medición exacta de Jay Ortani
    (que usa Time & Sales / DOM con tamaños de orden específicos, no disponible
    en el dataset M1).
    """
    recent = df[abs(df["close"] - level_price) / level_price <= tolerance_pct].tail(lookback_bars)
    if len(recent) < 2:
        return False
    sizes = recent["max_trade"].tolist()
    return sizes[-1] > sizes[-2] > 0
```

### Columnas necesarias
`max_trade` (proxy aproximado de tamaño de big trade por vela), `big_trade_bullish`,
`big_trade_bearish`.

### Ejemplo numérico
Nivel $104,200 tocado 3 veces en las últimas 30 velas. `max_trade` en esos toques: primero
8.2 BTC, segundo 15.6 BTC, tercero 24.1 BTC — secuencia creciente → señal de convicción
creciente en esa zona.

### ⚠️ Caveat
El dataset no diferencia "refill de la misma orden pasiva" de "trades grandes distintos por
coincidencia" — la aproximación con `max_trade` por vela es un proxy débil del concepto
original, que en acciones usa Time & Sales con identificación de niveles de precio exactos
en el DOM (Depth of Market), información de microestructura que el dataset BTC perp no tiene
con esa granularidad fuera de la capa cruda de 6 días de `ob500`.

---
<a name="h17"></a>
## Hipótesis 17: Salida Dinámica por Tape Weakness

**Fuente**: "LIVE Trading with a $1M Order Flow Trader" — Jay Ortani.

### Lógica que argumenta el creador
En vez de salir por un take-profit fijo predefinido, el creador mantiene la posición mientras
el ratio de trades agresivos a favor se sostenga (su ejemplo concreto: "quiero ver 80% verde,
20% rojo" en el time & sales/tape), y cierra en cuanto ese ratio se revierte de forma
significativa — no espera a que toque un nivel de precio fijo. El argumento: el flujo de
órdenes en tiempo real da una salida más temprana y precisa que un nivel estático, porque
capta el momento exacto en que el "combustible" direccional se agota, no después.

### Condición
```
SEA ratio_agresion_reciente = volumen_agresor_a_favor / volumen_agresor_total
                               (ventana móvil corta, ej. últimas N velas, parámetro)

MIENTRAS ratio_agresion_reciente >= umbral_sostenido (ej. 0.65, "65% a favor"):
    MANTENER posición

SI ratio_agresion_reciente cae por debajo de umbral_salida (ej. 0.45):
    CERRAR posición (señal de debilidad del tape, independiente de si tocó TP)
```

### Pseudocódigo
```python
def tape_weakness_exit_signal(df, idx, direction, window_bars=5,
                                threshold_hold=0.65, threshold_exit=0.45):
    """
    direction: "long" (favor = buy_vol) o "short" (favor = sell_vol)
    """
    window = df.iloc[max(0, idx - window_bars):idx + 1]
    if direction == "long":
        favor_vol = window["buy_vol"].sum()
    else:
        favor_vol = window["sell_vol"].sum()
    total_vol = window["buy_vol"].sum() + window["sell_vol"].sum()
    if total_vol == 0:
        return None
    ratio = favor_vol / total_vol

    if ratio < threshold_exit:
        return "EXIT_WEAKNESS"
    elif ratio >= threshold_hold:
        return "HOLD_STRONG"
    return "NEUTRAL"
```

### Columnas necesarias
`buy_vol`, `sell_vol` (ya existen en el dataset).

### Ejemplo numérico
Posición long abierta. Últimas 5 velas: buy_vol total=420, sell_vol total=95 → ratio=0.815
→ HOLD_STRONG (mantener). 10 velas después: buy_vol=180, sell_vol=240 → ratio=0.43 → cae
debajo de 0.45 → EXIT_WEAKNESS, cerrar posición independiente de si el TP original fue
alcanzado.

### Cómo se usa
Esta es una regla de **gestión de salida**, no de entrada — debe combinarse con cualquier
hipótesis de entrada del catálogo como alternativa o complemento al TP/SL fijo (comparar en
backtest: salida fija vs. salida dinámica por tape, sobre el mismo conjunto de entradas).

---
<a name="h18"></a>
## Hipótesis 18: Jerarquía Macro→Secundaria→Microestructura

**Fuente**: "Pass Prop Firms Using This ICT & Orderflow Futures Trading Strategy" — Abraham
Pérez, citando a Charles Dow (Dow Theory).

### Lógica que argumenta el creador
Tres niveles de análisis de estructura, de mayor a menor escala:
1. **Macroestructura**: la tendencia/ciclo de mayor escala — define el sesgo direccional
   general (¿el mercado está en tendencia alcista, bajista, o rango en el timeframe más alto
   que se está considerando?).
2. **Estructura secundaria**: la estructura que se forma DENTRO de los impulsos y
   retrocesos de la macroestructura — esto es lo que el creador usa para CONFIRMAR si el
   sesgo de la macroestructura ya está mostrando debilidad antes de operar en contra de ella.
3. **Microestructura**: el detalle de orderflow/footprint en el momento exacto de entrada.

La regla operativa central: no entrar en una posición contraria a la macroestructura solo
porque el precio tocó un extremo (tomó liquidez) — primero hay que ver que la estructura
SECUNDARIA (dentro de ese impulso/retroceso) muestre signos de debilidad, recién ahí buscar
la entrada de microestructura.

### Condición (conceptual, requiere jerarquía de timeframes)
```
NIVEL 1 (macro, ej. H4/D1): determinar tendencia general usando estructura HTF existente
         (h4_bearish, h4_bos_bear, etc. ya están en el dataset)

NIVEL 2 (secundaria, ej. H1): dentro del impulso/retroceso actual de NIVEL 1, buscar
         evidencia de debilidad (CHoCH en H1: h1_choch_bear/h1_choch_bull ya existen)

NIVEL 3 (micro, ej. M1/M5): solo si NIVEL 1 Y NIVEL 2 alinean, buscar el trigger de
         entrada de footprint/delta en el timeframe de ejecución

SI macro Y secundaria no alinean:
    NO OPERAR (esperar a que la estructura secundaria confirme antes de actuar)
```

### Pseudocódigo
```python
def hierarchical_structure_check(bar):
    """
    Usa columnas H4/H1 ya existentes en el dataset como proxies directos de los
    niveles macro y secundario de esta hipótesis — mapeo casi literal, a diferencia
    de otras hipótesis de este catálogo que requieren mayor aproximación.
    """
    macro_bearish = bar["h4_bearish"] and bar["h4_bos_bear"]
    macro_bullish = not bar["h4_bearish"]  # aproximación simétrica

    secondary_weakness_bear = bar["h1_choch_bear"]  # debilidad dentro de tendencia alcista
    secondary_weakness_bull = bar["h1_choch_bull"]  # debilidad dentro de tendencia bajista

    if macro_bullish and secondary_weakness_bull:
        return "ALIGNED_FOR_SHORT_SETUP"  # macro alcista pero secundaria muestra debilidad
    if macro_bearish and secondary_weakness_bear:
        return "ALIGNED_FOR_LONG_SETUP"
    return "NOT_ALIGNED"  # no operar, falta confirmación de estructura secundaria
```

### Columnas necesarias
`h4_bearish`, `h4_bos_bear`, `h1_choch_bear`, `h1_choch_bull`, `h1_bos_bear`, `h1_bos_bull`
— todas ya existen en el dataset, mapeo directo. **Nota de contexto del proyecto**: el
propio inventario del dataset ya advirtió que estas columnas H1/H4 tuvieron un bug de
lookahead corregido recientemente, y que "el edge desapareció" tras la corrección — esta
hipótesis depende fuertemente de columnas que ya mostraron ser frágiles en el escaneo
univariate hecho anteriormente en este proyecto (ver `h1_fvg_bear`, que dio resultado
inestable entre folds).

### Ejemplo numérico
H4: `h4_bearish=False` (macro alcista). H1: `h1_choch_bull` (cambio de carácter bajista
detectado dentro del impulso alcista actual, es decir primera señal de debilidad dentro de
la tendencia) → ALIGNED_FOR_SHORT_SETUP — recién acá se busca el trigger de microestructura
en M1/M5 para una entrada short táctica, no antes.

---
<a name="h19"></a>
## Hipótesis 19: Daily Candle Sentiment Continuation

**Fuente**: "PRO Trader Reveals Super Simple Trading Strategy" — Rajan D.

### Lógica que argumenta el creador
Enfoque deliberadamente simple, sin footprint/microestructura: el sentimiento de la vela
DIARIA de ayer predice el sesgo de hoy. Si ayer fue una vela alcista (close > open en el
diario), el creador busca longs hoy ~80% del tiempo, salvo que esté operando una estrategia
de mean-reversion explícitamente anidada DENTRO de una tendencia mayor (no como estrategia
default). El argumento es de "continuidad de sentimiento": el comportamiento de los
participantes que cerraron el día previo en una dirección tiende a continuar al día
siguiente, salvo evidencia fuerte en contra.

### Tensión filosófica explícita con la Hipótesis 1
Esta hipótesis es conceptualmente opuesta en su sesgo por defecto a la Hipótesis 1
(que busca mean-reversion dentro del área de valor del día anterior en su Variante 1, la
más común dado que ~80% del tiempo el mercado está en rango según esa misma fuente). Ambas
no pueden ser ciertas como regla general al mismo tiempo en el mismo contexto: tratarla como
tensión real a resolver con datos, no asumir que ambas funcionan.

### Condicion
```
SEA sentimiento_ayer = "alcista" SI close_diario_ayer > open_diario_ayer, sino "bajista"

POR DEFECTO (80% del tiempo segun la fuente):
    bias_hoy = mismo signo que sentimiento_ayer (continuacion)

EXCEPCION (mean-reversion, solo si esta anidada en tendencia mayor):
    SI hay evidencia de tendencia HTF mayor Y el precio retrocede dentro de ella:
        bias_hoy puede ser contrario al sentimiento de ayer, pero a FAVOR de la
        tendencia HTF mayor
```

### Pseudocodigo
```python
def daily_sentiment_continuation(df, current_date, htf_trend=None):
    prev_day = df[df["date"] == current_date - pd.Timedelta(days=1)]
    if prev_day.empty:
        return None
    daily_open = prev_day["open"].iloc[0]
    daily_close = prev_day["close"].iloc[-1]
    sentiment_yesterday = "bullish" if daily_close > daily_open else "bearish"
    if htf_trend is None or htf_trend == sentiment_yesterday:
        return sentiment_yesterday
    else:
        return htf_trend
```

### Columnas necesarias
`open`, `close` agregados a nivel diario, opcionalmente `regime` o columnas H4.

### Ejemplo numerico
Vela diaria de ayer: open=$103,000, close=$105,200 (alcista) -> bias_hoy="bullish" por
defecto, salvo evidencia HTF contraria fuerte.

---

## Hipotesis 20: Caja Estadistica de Pullback Historico

**Fuente**: misma transcripcion que Hipotesis 19 - Rajan D.

### Logica que argumenta el creador
En vez de un nivel de Fibonacci fijo, medir estadisticamente el rango historico real de
retrocesos del activo (ejemplo de la fuente: S&P 500 retrocedio historicamente 7%-17%) y
usar ese rango especifico del activo como "caja" de zona de agotamiento esperado.

### Pseudocodigo
```python
def compute_historical_pullback_distribution(df, swing_detection_window=50):
    # usar swing_high_50/swing_low_50 para identificar impulsos y medir profundidad
    # de retroceso de cada uno como % del impulso previo
    pass

def is_pullback_in_historical_box(current_pullback_pct, historical_distribution,
                                    pct_low=25, pct_high=75):
    box_low = np.percentile(historical_distribution, pct_low)
    box_high = np.percentile(historical_distribution, pct_high)
    return box_low <= current_pullback_pct <= box_high
```

### Columnas necesarias
`swing_high_50`, `swing_low_50`, `close`.

### Caveat
Requiere calculo previo de la distribucion real de pullbacks de BTC en este dataset -
no se puede portar el 7%-17% del S&P directamente.

---

## Hipotesis 21: B-Shape en Footprint + Bids Sostenidos

**Fuente**: "TRADING EN VIVO - Como Gano Mas de $40,000 Operando con OrderFlow" - Carmine
Rosato (mismo creador que Hipotesis 13).

### Logica que argumenta el creador
Forma de "B" en el footprint: alto volumen en zona media de consolidacion + lift posterior,
mas confirmacion de bids sostenidos (no desaparecen) en el heatmap mientras el precio
rebota repetidamente desde ahi.

### Pseudocodigo (aproximacion)
```python
def detect_b_shape_pattern(df, idx, lookback_bars=15, min_defenses=2, tolerance_pct=0.002):
    window = df.iloc[max(0, idx - lookback_bars):idx]
    candidate_level = window["fp_poc"].mode().iloc[0] if not window["fp_poc"].mode().empty else None
    if candidate_level is None:
        return False
    touches = window[abs(window["low"] - candidate_level) / candidate_level <= tolerance_pct]
    defended = touches[touches["close"] > candidate_level]
    if len(defended) >= min_defenses:
        next_bar = df.iloc[idx] if idx < len(df) else None
        if next_bar is not None and next_bar["close"] > window["close"].iloc[-1]:
            return True
    return False
```

### Columnas necesarias
`fp_poc`, `low`, `close`. Componente de bids sostenidos en heatmap: NO DISPONIBLE para
los 365 dias (solo 6 dias de ob500 crudo en el dataset original).

### Caveat
Misma limitacion que Hipotesis 13: el argumento central depende de liquidez pasiva
persistente en heatmap, no reconstruible con el dataset M1 completo.

---

## Resumen de viabilidad de implementacion (las 21 hipotesis)

| # | Hipotesis | Viabilidad con dataset actual | Requiere datos no disponibles |
|---|---|---|---|
| 1 | 4 Variantes de Apertura | Alta | No |
| 2 | Delta Range Reversal v3 | Media-Alta | Parcial (liquidaciones, heatmap LI) |
| 3 | Delta Liderando en Contra (filtro) | Alta | No |
| 4 | Unfinished Action (filtro) | Alta | No |
| 5 | Order Block 6 Condiciones + Cluster | Media | Parcial (cluster de volumen exacto) |
| 6 | Big Trades Fade/Continuacion | Media-Alta | No (pero "ruptura" es aproximada) |
| 7 | LVN Entrada / HVN Target | Alta | No |
| 8 | Merge de Perfiles | Baja | Si (distribucion completa de volumen) |
| 9 | POC Desplazandose (regimen) | Alta | No |
| 10 | Velas de Volumen | Muy baja | Si (raw_trades, no construido aun) |
| 11 | Confluencia Discrecional | Media (alto riesgo overfitting) | No, pero mal definida |
| 12 | Delta Absorbido Intrabar | Muy baja | Si (tick data dentro de la vela) |
| 13 | LVN + Defensa Orden Pasiva | Media (aproximada) | Parcial (fill real, L2) |
| 14 | Niveles 80/20 | Alta (tecnicamente simple) | No, pero recalibracion a BTC abierta |
| 15 | Repair Candle | Alta | No |
| 16 | Bid Refill Creciente | Media (aproximada) | Parcial (tamano exacto de refills) |
| 17 | Salida por Tape Weakness | Alta | No |
| 18 | Jerarquia Macro/Secundaria/Micro | Alta (mapeo directo) | No, pero columnas ya fragiles |
| 19 | Daily Sentiment Continuation | Alta | No |
| 20 | Caja Estadistica de Pullback | Media (requiere calculo previo) | No |
| 21 | B-Shape + Bids Sostenidos | Media (aproximada) | Parcial (heatmap real) |

**Las mas listas para testear de inmediato sin trabajo de datos adicional**: 1, 3, 4, 7, 9,
14, 15, 17, 19.
**Las que requieren mas trabajo de construccion de datos antes de testear**: 10, 12 (raw_trades),
8 (distribucion completa de volumen), 13/16/21 (capas tick/L2 para validacion fina).
