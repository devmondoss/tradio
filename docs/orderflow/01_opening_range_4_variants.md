# Estrategia: 4 Variantes de Apertura vs. Perfil del Día Anterior

> **Estado: hipótesis no testeada.** Origen: contenido educativo de un trader individual
> (transcripciones de YouTube), sin backtest público verificable. Este documento especifica
> la lógica EXACTA para poder implementarla y testearla sin ambigüedad. No implica que la
> estrategia funcione — eso lo determina el backtest, no este documento.

---

## 0. Resumen en una frase

La posición de la apertura del período actual (día/semana/mes) respecto al perfil de volumen
del período anterior (POC, VAH, VAL) determina si el contexto es de "rango" o "direccional",
y eso define dónde buscar entradas y dónde poner los targets.

---

## 1. Inputs requeridos (mapeo a columnas del dataset)

| Concepto del spec | Columna en dataset | Notas |
|---|---|---|
| POC del día anterior | `vp_poc` | **CRÍTICO**: hay que verificar si esta columna se recalcula intra-día (rolling) o es fija por sesión. Si es rolling, hay que tomar el valor congelado al cierre de la sesión anterior, NO el valor en la fila actual. Ver sección 6. |
| VAH del día anterior | `vp_vah` | Mismo caveat que POC. |
| VAL del día anterior | `vp_val` | Mismo caveat que POC. |
| Máximo del día anterior | `prev_day_high` | Ya viene pre-calculado y causal según el README del dataset. Usar este, no recalcular. |
| Mínimo del día anterior | `prev_day_low` | Igual que arriba. |
| Apertura del día actual | Ver sección 2 | NO existe una columna directa; hay que derivarla. |
| Precio actual | `close` (de la vela en curso) | Para evaluar dónde está el precio en cada instante. |
| Timestamp | `ts_ms` / `dt` | Para identificar el inicio de cada día. |

**Acción previa obligatoria antes de implementar**: correr un `.describe()` y graficar
`vp_poc`/`vp_vah`/`vp_val` a lo largo de un solo día para confirmar si son valores fijos
(un solo valor por día, repetido en todas las filas de ese día) o si cambian fila a fila
dentro del mismo día. Esto determina si la sección 6 (congelado) es necesaria o no.

---

## 2. Definición de "apertura del día" (`daily_open`)

```
daily_open = close de la PRIMERA vela M1 (o M5/M15, según el timeframe de trabajo)
             cuyo timestamp en UTC cae en 00:00:00 del día calendario.
```

**Ambigüedad a resolver antes de implementar**: el trader de origen es argentino y probablemente
piensa en términos de "apertura de Nueva York" (13:30 UTC aprox, o 14:30 en horario de verano US)
para activos tradicionales, pero también menciona "daily open" genérico. Para BTCUSDT perpetuo,
que cotiza 24/7 sin apertura de mercado tradicional, la convención más razonable y defendible es:

```
daily_open = close a las 00:00:00 UTC
```

Esto debe quedar como parámetro configurable (`daily_open_hour_utc`), NO hardcodeado, para poder
testear sensibilidad a esta elección. Valores a probar: `00:00 UTC`, `13:30 UTC` (NY).

---

## 3. Definición del perfil del "día anterior" (`prev_session_profile`)

```
prev_session_profile = {
    poc: vp_poc congelado al cierre del día calendario previo,
    vah: vp_vah congelado al cierre del día calendario previo,
    val: vp_val congelado al cierre del día calendario previo,
    high: prev_day_high (tal como viene en el dataset, ya es causal y representa el día previo),
    low: prev_day_low  (ídem)
}
```

**Regla de causalidad NO NEGOCIABLE**: `prev_session_profile` se calcula usando ÚNICAMENTE datos
hasta el cierre del día calendario anterior (23:59:59 UTC del día D-1). Ningún valor de este
profile puede incorporar información de la sesión del día D (el día que se está evaluando).
Si `vp_poc`/`vp_vah`/`vp_val` son rolling/intra-día, hay que tomar el snapshot de esos valores
en la ÚLTIMA fila del día D-1, no recalcularlos.

---

## 4. Las 4 variantes — definición exacta

Sea `O` = `daily_open` del día D, y `{poc, vah, val, high, low}` = `prev_session_profile` del día D-1.

### Variante 1: Apertura DENTRO del área de valor previa
```
condición:  val <= O <= vah
contexto:   RANGO / BALANCE
```

**Regla de entrada (sub-variante 1a — primera reacción):**
- Si el precio toca `vah` desde adentro → bias SHORT
  - target_1 = `poc`
  - target_2 = `val` (rotación completa)
- Si el precio toca `val` desde adentro → bias LONG
  - target_1 = `poc`
  - target_2 = `vah` (rotación completa)
- stop_loss = ver sección 5 (dos modos: local vs. máximo)

**Regla de entrada (sub-variante 1b — reacción en POC tras fallar TP2):**
- Si tras tocar `vah`/`val` el precio reacciona en `poc` y rebota (no completa la rotación a
  `val`/`vah` opuesto), se puede tomar una SEGUNDA entrada en la dirección original, esta vez
  apuntando a la rotación completa (`val` o `vah` opuesto), no solo al POC.
- Trigger de "reacción en POC": el precio toca `poc` y la siguiente vela (o las siguientes N
  velas, parámetro `poc_reaction_confirm_bars`, default=1) cierra alejándose del POC en la
  dirección original del trade.

### Variante 2: Apertura ENTRE el máximo del día anterior y el VAH
```
condición:  vah < O <= high
contexto:   DIRECCIONAL ALCISTA (leve)
```

**Regla de entrada:**
- Esperar a que el precio retroceda y entre al área de valor previa (cruce `vah` hacia abajo).
- Entrada en `poc` (no es target, es ZONA DE ENTRADA en esta variante — diferencia clave vs.
  variante 1).
- target_1 = `daily_open` (el open del día actual, `O`)
- target_2 (opcional, si target_1 se alcanza con fuerza) = extensión más allá de `O`, sin nivel
  fijo predefinido en el material fuente — dejar como "trailing" o no definir target_2 explícito.
- bias = LONG

### Variante 3: Apertura ENTRE el VAL y el mínimo del día anterior (simétrico a Variante 2)
```
condición:  low <= O < val
contexto:   DIRECCIONAL BAJISTA (leve)
```

**Regla de entrada:**
- Esperar a que el precio retroceda y entre al área de valor previa (cruce `val` hacia arriba).
- Entrada en `poc`.
- target_1 = `daily_open` (`O`)
- bias = SHORT

### Variante 4: Apertura FUERA del rango del día anterior (por encima de `high` o por debajo de `low`)
```
condición:  O > high   O   O < low
contexto:   DIRECCIONAL FUERTE / posible gap o continuación de tendencia
```

**Regla de entrada**: el material fuente NO especifica una regla de entrada explícita y
verificable para este caso — se menciona de forma genérica ("buscar balances históricos más
lejanos"). **NO IMPLEMENTAR esta variante con una regla concreta sin antes pedir aclaración**;
marcarla como "fuera de alcance v1" y excluir esos días del backtest, no inventar una regla.

---

## 5. Stop loss — dos modos (deben testearse ambos, no elegir uno a priori)

```
modo "local":
    stop_loss = mínimo/máximo local de la vela o las N velas previas a la entrada
                (parámetro: local_stop_lookback_bars, default=3)
    → mejor R:R esperado, pero el material fuente advierte menor win rate

modo "máximo" (conservador):
    stop_loss = prev_day_high (si es entrada SHORT) o prev_day_low (si es entrada LONG)
    → peor R:R esperado, pero mayor win rate según el material fuente
```

**Implementación obligatoria**: correr el backtest con AMBOS modos por separado y comparar.
No asumir cuál es mejor — esa es justamente una de las preguntas que el backtest debe responder.

---

## 6. Congelado de variables intra-día (crítico para evitar lookahead)

Si al verificar la sección 1 se confirma que `vp_poc`/`vp_vah`/`vp_val` son rolling/intra-día
(cambian fila a fila), el pipeline de construcción de features DEBE:

1. Para cada fila del día D, el `prev_session_profile` usado es el de D-1, **fijo durante
   todo el día D** (no debe cambiar entre las 00:00 y las 23:59 del día D).
2. Implementación sugerida en pandas:
   ```python
   df['date'] = df['dt'].dt.date
   daily_close_profile = df.groupby('date')[['vp_poc','vp_vah','vp_val']].last()
   daily_close_profile_shifted = daily_close_profile.shift(1)  # el de AYER
   df = df.merge(daily_close_profile_shifted, on='date', suffixes=('','_prevday'))
   ```
3. **Test de sanity obligatorio post-implementación**: verificar que para CADA fila del día D,
   `vp_poc_prevday` (etc.) es idéntico al valor que tenía `vp_poc` en la última fila del día D-1.
   Si no coincide, hay un bug de alineación temporal — no continuar hasta resolverlo.

---

## 7. Entry trigger — definición de "tocar" un nivel

Ambigüedad del material fuente: "tocar" un nivel no especifica si es:
- (a) el `low`/`high` de la vela cruza el nivel (mecha toca), o
- (b) el `close` de la vela cruza el nivel (cierre confirma).

**Decisión por defecto para v1 (debe documentarse como supuesto, no como hecho)**:
usar (a) para definir que el precio "llegó" a la zona, pero exigir una vela de **rechazo o
absorción** en esa zona antes de entrar (no entrar al primer toque mecánico). Definición de
vela de rechazo: cierre de la vela en el 50% más alejado del nivel respecto a su rango total
(ej. para un toque de `vah` desde abajo con bias short, exigir que el cierre de esa vela quede
en la mitad inferior del rango de la vela).

Esto es una interpretación razonable pero NO viene literal del material fuente — flag explícito
para que quien implemente lo revise y decida si lo simplifica a (b) (más simple, menos supuestos).

---

## 8. Horizonte de validez de la señal

El material fuente no especifica cuánto tiempo permanece válida la variante calculada al inicio
del día. Supuesto a usar (parametrizable): **la variante calculada con el `daily_open` rige
durante todo el día calendario D**, hasta las 23:59:59 UTC. Si no se activa ningún trigger
de entrada durante ese día, el día se cuenta como "sin señal", no como pérdida ni ganancia.

---

## 9. Filtros adicionales mencionados en el material fuente (aplicar como capas opcionales,
## testear con y sin cada uno por separado)

- **Filtro de sesión**: el material fuente menciona operar preferentemente en sesión de
  Nueva York (13:00–17:00 UTC aprox). Para BTCUSDT 24/7 esto es opcional, testear ON/OFF.
- **Filtro "delta liderando en contra"**: si al llegar al nivel de entrada el `delta` de las
  últimas N velas (parámetro, default N=3) sigue siendo fuerte en la dirección CONTRARIA al
  bias de la variante, NO ENTRAR. "Fuerte" se define como `dz` (delta z-score, ya en el
  dataset) > umbral configurable (default 1.5, incluir como parámetro a calibrar en IS,
  nunca en OOS).
- **Filtro "unfinished action"**: si `fp_unfinished_hi` o `fp_unfinished_lo` (según el lado
  del rechazo) está activo en la vela de toque del nivel, NO ENTRAR en esa primera reacción
  — esperar a que el nivel sea revisitado y la siguiente reacción no muestre unfinished.

---

## 10. Métricas de evaluación obligatorias (consistentes con el resto de la investigación)

- Retorno neto de fee (11 bps round-trip taker) por trade, en bps.
- Win rate, avg R, total trades, frecuencia (trades/mes).
- **Walk-forward con folds temporales** (mínimo 4 folds en IS), igual metodología que el resto
  de este proyecto — ver `combo_scan_*` scripts ya construidos como referencia de estructura.
- Reportar conteo de **fechas únicas** en los trades resultantes, no solo n de trades — ya
  detectamos en este proyecto que setups de baja frecuencia pueden mostrar pocos eventos de
  mercado independientes disfrazados de muestra grande.
- Variantes 1, 2 y 3 deben evaluarse POR SEPARADO antes de combinarse — no asumir que las
  tres funcionan igual de bien.
- Probar sensibilidad a: `daily_open_hour_utc` (00:00 vs 13:30 UTC), modo de stop loss
  (local vs máximo), trigger de entrada (mecha vs cierre, sección 7).

---

## 11. Lo que este documento NO resuelve (decisiones pendientes antes de codear)

1. Confirmar si `vp_poc`/`vp_vah`/`vp_val` son fijos o rolling intra-día (sección 1/6).
2. Definir regla de entrada para Variante 4 (sección 4) — actualmente fuera de alcance.
3. Decidir entre trigger de mecha vs. cierre (sección 7) — hay un supuesto por defecto pero
   no es del material fuente original.
4. Validar si aplica igual a M15, H1, H4, o si el período "día anterior" debe ajustarse según
   el timeframe de trabajo (ej. para H4, ¿el "perfil anterior" es el día calendario previo o
   la sesión H4 anterior?).
