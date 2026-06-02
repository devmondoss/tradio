# Auditoría de Lógica — FlowSurface Trading System
## Verificación completa de correctness antes de agregar nada nuevo

> **Documento historico.** Auditoria previa a fixes y a la decision DRR-only. Usar solo para entender bugs pasados; el estado actual esta en [DRR_PRESENTE_Y_FUTURO.md](../../DRR_PRESENTE_Y_FUTURO.md).

**Fecha:** Mayo 2026  
**Fuentes:** STRATEGY_COMPLETE_DOCUMENTATION.md + FLOWSURFACE_STRATEGY_PROPOSAL_CLAUDE_CODE.md + logs Railway 00:35-05:06 UTC Mayo 17  
**Metodología:** Análisis estático de pseudocódigo + evidencia de logs en producción  
**Sin acceso a:** Código Rust real — estos hallazgos son sobre la lógica documentada y lo observable desde los logs

---

## Resumen ejecutivo

**Estado general: FUNCIONAL PERO CON PROBLEMAS CRÍTICOS**

El sistema está corriendo, procesando barras, y midiendo correctamente los datos de mercado.
Sin embargo hay **4 bugs que hacen imposible generar señales válidas**, **3 problemas lógicos
en los detectores**, y **2 problemas de infraestructura** confirmados por los logs.

```
Bugs críticos (bloquean señales):       4
Bugs de lógica en detectores:           3  
Inconsistencias entre documentos:       4
Problemas confirmados por logs:         5
Problemas de infraestructura:           2
```

---

## PARTE 1 — Problemas confirmados por los logs

Estos no son hipótesis — los logs los demuestran directamente.

---

### LOG-1 — CRÍTICO: Sistema ciego durante ~20 minutos en cada deploy

**Evidencia:**
```
00:40 Bar 1: missing=["ATR_NOT_READY"]   regime=Unknown
00:45 Bar 2: missing=["NO_VALID_SETUP"]  regime=Unknown
00:50 Bar 3: missing=["NO_VALID_SETUP"]  regime=Unknown  
00:55 Bar 4: missing=["NO_VALID_SETUP"]  regime=Unknown
01:00 Bar 5: regime=TrendDown  ← primer régimen válido
```

El sistema arranca sin historia. Las primeras 4 barras (20 minutos) no pueden
generar ninguna señal porque:

- ATR requiere mínimo N barras para calcularse (bar 1 → `ATR_NOT_READY`)
- OLS slope (régimen) requiere 14 barras para slow y 5 para fast (barras 1-4 → `Unknown`)
- Volume profile requiere historia de trades para tener POC/VAH/VAL significativos
- VWAP es correcto desde barra 1 pero sin ATR los detectores no pueden calcular stops

**Impacto real en los logs del 17 de mayo:**

El colapso del precio ocurrió exactamente durante estas barras ciegas:

```
00:50 Bar 3:  cvd=-80.9   → vendedores aparecen
00:55 Bar 4:  cvd=-174.6  → aceleran
01:00 Bar 5:  cvd=-625.8  → cascada  ← primer régimen válido
```

El momento más claro del período fue invisible para el sistema porque estaba en warm-up.

**Solución:**

```rust
// Al startup, antes de procesar el primer bar live:
async fn warm_up(binance: &BinanceClient, symbol: &str) -> BarState {
    // Fetch últimas 50 barras M5 vía REST
    let klines = binance.get_klines(symbol, "5m", 50).await?;
    
    let mut state = BarState::default();
    for kline in klines {
        state.process_historical_bar(&kline);
        // Calcula ATR, OLS slope, volume profile, VWAP
        // sin emitir señales (es historia, no live)
    }
    
    log::info!(
        "Warm-up: {} bars, regime={:?}, atr={:.0}, poc={:.0}",
        50, state.regime, state.atr, state.poc
    );
    
    state
}
```

Con 50 barras históricas (4.2h de historia), el primer bar live ya tiene:
ATR calculado, OLS slow y fast listos, volume profile con datos reales,
y VWAP del día desde medianoche UTC.

---

### LOG-2 — CRÍTICO: Régimen fluctúa en barras consecutivas

**Evidencia:**
```
01:00  regime=TrendDown  (slow=-0.430, fast=-0.430)
...
02:20  regime=TrendUp    (slow=-0.165, fast=+0.421)  ← 1 sola barra
02:25  regime=TrendDown  (slow=-0.126, fast=+0.174)  ← vuelve
02:30  regime=Chop       (slow=-0.076, fast=+0.094)
02:50  regime=TrendUp    (slow=+0.120, fast=+0.064)
```

El régimen cambia 4 veces en 30 minutos. Eso tiene consecuencias:

**Primero:** `[config] No calibrated params for regime 'TrendUp'` aparece
repetidamente porque el sistema intenta cargar parámetros para un régimen que
dura una sola barra. Los parámetros calibrados por régimen son inútiles si el
régimen cambia cada 5 minutos.

**Segundo:** Los detectores tienen condiciones como:
```rust
slow_regime == TrendUp  // para LvnBreakout long
slow_regime == TrendDown // para VwapPullback short
```

Si el slow OLS slope de 14 barras cambia de signo en una sola barra, el detector
que estaba bloqueado por régimen incorrecto de repente se activa — y se bloquea
de nuevo en la siguiente barra. Esto crea ventanas de señal aleatorias de una barra.

**Raíz del problema:**

El slow OLS de 14 barras tiene umbral de ±0.10 para clasificar TrendUp/Down.
Con precio en rango estrecho ($77,700-$78,200 durante horas), el slope oscila
alrededor de cero y cruza el threshold repetidamente.

**Solución — Hysteresis en el régimen:**

```rust
pub struct RegimeDetector {
    current_regime: Regime,
    // Umbrales con hysteresis: más fácil entrar que salir
}

impl RegimeDetector {
    pub fn update(&mut self, slow_slope: f64) -> Regime {
        self.current_regime = match self.current_regime {
            Regime::TrendUp => {
                // Solo salir de TrendUp si slope < -0.15 (más negativo que -0.10)
                if slow_slope < -0.15 { Regime::TrendDown }
                else if slow_slope.abs() < 0.08 { Regime::Chop }
                else { Regime::TrendUp }
            }
            Regime::TrendDown => {
                // Solo salir de TrendDown si slope > +0.15
                if slow_slope > 0.15 { Regime::TrendUp }
                else if slow_slope.abs() < 0.08 { Regime::Chop }
                else { Regime::TrendDown }
            }
            Regime::Chop => {
                // Entrar en TrendUp/Down con umbral más alto
                if slow_slope > 0.15 { Regime::TrendUp }
                else if slow_slope < -0.15 { Regime::TrendDown }
                else { Regime::Chop }
            }
            Regime::Unknown => {
                if slow_slope > 0.10 { Regime::TrendUp }
                else if slow_slope < -0.10 { Regime::TrendDown }
                else { Regime::Chop }
            }
        };
        self.current_regime
    }
}
```

Con hysteresis de ±0.15 para salir vs ±0.10 para entrar, el régimen
es significativamente más estable en mercados de chop.

---

### LOG-3 — ALTO: CVD acumulado negativo persistente en régimen TrendUp

**Evidencia:**
```
02:50 regime=TrendUp  cvd=-1,383  close=77,868
03:00 regime=TrendUp  cvd=-1,279  close=77,856
03:05 regime=TrendUp  cvd=-1,165  close=77,943
...
04:45 regime=TrendUp  cvd=-486    close=78,144  (mejorando)
05:05 regime=TrendUp  cvd=-462    close=78,155
```

Durante 2+ horas de TrendUp, el CVD fue consistentemente negativo (-1,383 a -462).
Eso significa que los vendedores agresivos dominaron el flujo incluso mientras
el precio subía.

**Esto describe exactamente un short squeeze / cierre de posiciones:**

```
OI delta negativo (-92, -65, -11, -68...) + precio subiendo = shorts covering
CVD negativo = más volumen en bid side (presión vendedora)
Pero precio sube = los shorts cubriéndose mueven el precio aunque el taker flow es vendedor
```

**Problema:** `VwapPullbackContinuation` LONG requiere:
```rust
cvd_slope > 0.0  // compradores entrando
```

Pero con CVD acumulado de -1,383, incluso si el slope de las últimas 5 barras
es levemente positivo, el contexto macro del CVD del día es bajista.

El documento dice:
```rust
// CVD absoluto confirma dirección (ej. CVD > -200 para longs)
if cvd_absolute > -200 { score += bonus }
```

Ese filtro de CVD absoluto > -200 debería estar **bloqueando** la señal LONG
cuando el CVD acumulado es -1,383. Si no está implementado como gate (solo como
modifier de score), puede que el score baje pero no se bloquee la señal.

**Verificar:** ¿`cvd_absolute` es un gate duro o solo un modifier de score?
La documentación es ambigua — lo muestra en el scoring pero no como condición
de `return None`.

---

### LOG-4 — ALTO: Datos institucionales no visibles en logs

**Evidencia:**

Cada bar log muestra:
```
[bar] ts=... close=... regime=... funding=0.4646 basis=-0.048% oi_delta=0 cvd=20.4 ob=live action=Wait
```

Los campos institucionales **nuevos** (short_liq_usd_5m, top_traders_long_pct,
funding_percentile_30d, taker_buy_sell_ratio) no aparecen en ningún bar log.

Dos posibilidades:

**A) Los datos institucionales están llegando pero no se loguean.**
En este caso los detectores institucionales corren normalmente pero no
podemos verificarlo desde los logs.

**B) Los datos institucionales NO están llegando (inst=None).**
En este caso los detectores institucionales nunca corren porque el router
tiene:
```rust
if ctx.institutional.is_some() {
    // solo entonces llamar LiquidationHunt, FundingExhaustion, SmartMoneyDivergence
}
```

**Cómo verificar:**

Agregar al bar log:
```rust
log::error!(
    "[bar] ... inst={} liq_short={:.0}K liq_long={:.0}K \
     top_traders={:.2} retail={:.2} fund_pct={:.0}",
    if ctx.institutional.is_some() { "live" } else { "null" },
    inst.map(|i| i.liquidations.short_liq_usd_5m / 1000.0).unwrap_or(0.0),
    inst.map(|i| i.liquidations.long_liq_usd_5m / 1000.0).unwrap_or(0.0),
    inst.map(|i| i.ls_ratio.top_traders_long_pct).unwrap_or(0.0),
    inst.map(|i| i.ls_ratio.retail_long_pct).unwrap_or(0.0),
    inst.map(|i| i.funding.funding_percentile_30d).unwrap_or(0.0),
);
```

Si el log muestra `inst=null` → el pipeline institucional no está conectado
al monitor y los 3 detectores nuevos nunca corren.

---

### LOG-5 — MEDIO: `NO_VALID_SETUP` como único missing sin breakdown

**Evidencia:**

Todas las barras (excepto bar 1) muestran:
```
missing=["NO_VALID_SETUP"]
```

Este es el mensaje cuando ningún detector genera señal. No dice **por qué**
ningún detector generó señal — si fue porque el régimen no cumple, porque
ATR no está, porque CVD no confirma, o porque las condiciones de LVN/VWAP
no se cumplen.

**Sin breakdown del `missing`, no podemos diagnosticar qué está fallando.**

La diferencia entre:
```
missing=["REGIME_NOT_FAVORABLE"]     ← régimen incorrecto para todos
missing=["NO_LVN_NEARBY"]            ← LvnBreakout sin LVN cerca
missing=["CVD_AGAINST"]              ← CVD en contra
missing=["DISTANCE_TO_VWAP_TOO_FAR"] ← VwapPullback sin precio cerca de VWAP
missing=["NO_VAH_BREACH"]            ← FailedAuction sin breach reciente
```

es la diferencia entre saber qué arreglar y adivinar.

**Solución:** Cambiar el logging para que cada detector reporte su razón
de rechazo específica cuando no genera señal.

---

## PARTE 2 — Bugs de lógica en los detectores

Estos vienen del análisis de los documentos, confirmados o amplificados por los logs.

---

### BUG-1 — CRÍTICO: `taker_imbalance` thresholds que no discriminan

**Detector:** `ValueAreaFailedAuction`  
**Severidad:** CRÍTICO — el filtro de flujo no filtra nada

**Código en documentación:**
```rust
// SHORT failed auction — condition actual
let short_flow =
    flow.cvd_slope.unwrap_or(0.0) <= 0.0 &&
    flow.taker_imbalance.unwrap_or(0.0) < 0.25;  // ← casi siempre true

// LONG failed auction — condition actual  
let long_flow =
    flow.cvd_slope.unwrap_or(0.0) >= 0.0 &&
    flow.taker_imbalance.unwrap_or(0.0) > -0.25; // ← casi siempre true
```

**Por qué es un bug:**

`taker_imbalance` está definido en el rango (-1.0, +1.0) donde:
- +1.0 = todos los trades en ask (compradores agresivos puros)
- -1.0 = todos los trades en bid (vendedores agresivos puros)
- 0.0 = equilibrio

La condición `< 0.25` para short es verdadera cuando el imbalance está entre
(-1.0, +0.25) — es decir, el 87.5% teórico del rango total. En la práctica,
durante mercado normal el imbalance oscila entre -0.3 y +0.3, lo que significa
que esta condición es verdadera prácticamente siempre.

**Lo que debería hacer para una failed auction SHORT:**

Detectar que los compradores NO pueden sostener el precio arriba de VAH.
El taker imbalance debería ser **neutro o bajando**, no simplemente "menor a 0.25".

```rust
// SHORT — corregido: flujo neutro o vendedor
let short_flow =
    flow.cvd_slope.unwrap_or(0.0) <= 0.0 &&
    flow.taker_imbalance.unwrap_or(1.0) < 0.05; // neutro a vendedor

// LONG — corregido: flujo neutro o comprador
let long_flow =
    flow.cvd_slope.unwrap_or(0.0) >= 0.0 &&
    flow.taker_imbalance.unwrap_or(-1.0) > -0.05; // neutro a comprador
```

Alternativamente, y más elegante: reemplazar este cálculo local con el dato
oficial de Binance (`takerlongshortRatio`) que ya tenemos en la capa institucional.
Es más preciso y elimina la necesidad de calcularlo nosotros.

---

### BUG-2 — CRÍTICO: `ValueLocation::BelowVal` en VWAP Pullback LONG

**Detector:** `VwapPullbackContinuation`  
**Severidad:** CRÍTICO — contradicción lógica que activa señales en breakdowns

**Código en propuesta:**
```rust
let long_context =
    matches!(ctx.regime, Regime::TrendUp | Regime::Expansion) &&
    matches!(vw.price_vs_avwap_bos, PriceRelation::Above | PriceRelation::At) &&
    matches!(vp.value_location, ValueLocation::InValue | ValueLocation::BelowVal); // ← ERROR
```

**Por qué es un bug:**

`BelowVal` significa que el precio está **debajo del Value Area Low** — el soporte
inferior del área de valor. En TrendUp, si el precio perdió el VAL, no está en
pullback dentro de tendencia: está en breakdown del soporte que define la tendencia.

Ejemplo concreto con los datos del 17 de mayo:
```
VAL hipotético: $77,900
Precio en 01:00: $77,915 → InValue (correcto)
Precio en 01:45: $77,755 → BelowVal (INCORRECTO — precio rompió soporte)
```

Con el código actual, si el régimen flippeara a TrendUp durante la caída
(como pasó brevemente a las 02:20), el detector podría generar señal LONG
cuando el precio estaba debajo de VAL — exactamente el peor momento para entrar long.

**Corrección:**
```rust
let long_context =
    matches!(ctx.regime, Regime::TrendUp | Regime::Expansion) &&
    matches!(vw.price_vs_avwap_bos, PriceRelation::Above | PriceRelation::At) &&
    matches!(vp.value_location, ValueLocation::InValue); // solo dentro de valor
```

**Simétrico para SHORT:**
```rust
// Actual (mismo problema):
matches!(vp.value_location, ValueLocation::InValue | ValueLocation::AboveVah)

// Corregido:
matches!(vp.value_location, ValueLocation::InValue)
```

---

### BUG-3 — ALTO: CVD absoluto como modifier en vez de gate duro

**Afecta:** `VwapPullback` principalmente  
**Severidad:** ALTO — permite señales con flujo macro en contra

**Del documento (sección Detector 2):**
```rust
// CVD absoluto > -200 (para longs) — flujo del día no está vendedor
if cvd_absolute > -200 { bonus += algo }
```

**Evidencia de los logs:**

Durante horas de TrendUp (02:50-05:05), el CVD absoluto fue:
```
02:50: cvd = -1,383
03:00: cvd = -1,279
...
04:45: cvd = -486
05:05: cvd = -462
```

Si el sistema intentara generar señal LONG de VwapPullback durante este período,
el CVD de -1,383 debería **bloquear** la señal, no solo penalizar el score en -0.10.

Con CVD de -1,383 y una penalización de -0.10 en el score, el score base
podría ser 0.75 y quedar en 0.65 — que **pasa el threshold de 0.60**.
El sistema entraría long mientras el flujo macro del día grita "vendedor".

**La distinción crítica:**

```rust
// INCORRECTO — CVD como modifier (lo que parece estar implementado):
if cvd_absolute > -200 { score += 0.10; }  // bono si CVD ok
// → Con CVD=-1,383, el score simplemente no recibe el bono
// → La señal puede igualmente pasar el threshold

// CORRECTO — CVD como gate duro:
if side == Side::Long && cvd_absolute < -300 {
    return None;  // CVD macro demasiado negativo, no entrar long
}
if side == Side::Short && cvd_absolute > 300 {
    return None;  // CVD macro demasiado positivo, no entrar short
}
```

**El umbral exacto necesita calibración**, pero el concepto es claro:
hay un nivel de CVD acumulado donde entrar en esa dirección es entrar
contra el flujo macro del día, y eso debería ser un veto, no una penalización.

---

### BUG-4 — ALTO: ATR = 0 no protegido en cálculo de stops

**Afecta:** Todos los detectores  
**Severidad:** ALTO — produce stops en precio de entry (riesgo cero, R:R incoherente)

**Evidencia en logs:**

Bar 1 muestra `missing=["ATR_NOT_READY"]` — el guard existe para la primera barra.
Pero ¿qué pasa si ATR existe pero es muy pequeño (mercado sin volatilidad)?

```rust
// En ValueAreaFailedAuction SHORT:
let stop = f64::max(vah + 0.25 * atr, px + 0.5 * atr);
// Si atr = 0 → stop = f64::max(vah, px)
// Si px < vah (condición del setup) → stop = vah
// Riesgo entre entry y stop = vah - px → puede ser $0 si px ≈ vah
// El check stop > entry pasa con stop = vah y entry < vah
// → Señal emitida con riesgo potencialmente $0

// En LvnBreakout LONG:
let stop = entry - 0.75 * atr;
// Si atr = 0 → stop = entry
// → Stop en el mismo precio que entry → riesgo $0
```

**Solución unificada en el router:**

```rust
// Gate en router — antes de llamar cualquier detector:
let atr = ctx.atr.filter(|&a| a > 50.0)?;
// $50 de ATR en M5 para BTC es el mínimo razonable
// ATR < $50 indica datos degenerados o mercado congelado
// Retorna None si no hay ATR válido → acción = Wait missing=["ATR_INVALID"]
```

Esto evita duplicar el guard en cada detector.

---

### BUG-5 — MEDIO: `partial_cmp().unwrap()` puede panic con NaN

**Detector:** `LvnBreakout` — helpers `nearest_above` / `nearest_below`  
**Severidad:** MEDIO — crash en producción con datos degenerados

**Del documento (propuesta):**
```rust
fn nearest_above(levels: &[f64], price: f64) -> Option<f64> {
    levels
        .iter()
        .copied()
        .filter(|x| *x > price)
        .min_by(|a, b| a.partial_cmp(b).unwrap())  // ← panic si hay NaN
}
```

`partial_cmp` retorna `None` cuando compara `NaN` con cualquier valor.
`.unwrap()` sobre `None` → panic en producción.

Los `hvn_levels` y `lvn_levels` vienen del volume profile calculado sobre
ticks de WebSocket. Un tick corrupto, un gap de datos durante reconexión,
o un valor de volumen 0 mal clasificado pueden introducir NaN.

**Corrección:**
```rust
fn nearest_above(levels: &[f64], price: f64) -> Option<f64> {
    levels
        .iter()
        .copied()
        .filter(|x| x.is_finite() && *x > price)  // ← filtrar NaN/Inf primero
        .min_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal))
}

fn nearest_below(levels: &[f64], price: f64) -> Option<f64> {
    levels
        .iter()
        .copied()
        .filter(|x| x.is_finite() && *x < price)  // ← filtrar NaN/Inf primero
        .max_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal))
}
```

---

### BUG-6 — MEDIO: `InvalidationCondition` en `ValueAreaFailedAuction` — lógica invertida

**Detector:** `ValueAreaFailedAuction`  
**Severidad:** MEDIO — invalida trades que no deberían invalidarse

**Del documento:**
```rust
"ValueAreaFailedAuction" => {
    // Precio vuelve a entrar al value area
    current_close > pos.entry_val && current_close < pos.entry_vah
}
```

**El problema:**

Para un SHORT de failed auction, la setup es:
- Entry: precio arriba de VAH, esperando que baje
- Stop: arriba del high del failed auction
- Target: POC

La condición de invalidación dice: "si precio entra al value area, salir".
Pero si el trade es SHORT y el precio está bajando desde arriba de VAH hacia
el value area, entrar al value area es el precio **moviéndose a favor del trade**,
no invalidándolo.

Lo que debería invalidar el SHORT es que el precio **vuelva a subir por encima
de VAH** (el mercado aceptó el precio arriba del VA después de todo).

Lo que debería invalidar el LONG (precio abajo de VAL esperando subir) es que
el precio **caiga más debajo de VAL** (el mercado aceptó el precio abajo del VA).

**Corrección:**
```rust
"ValueAreaFailedAuction" => {
    match pos.side {
        Side::Short => {
            // SHORT invalida si precio VUELVE arriba de VAH
            // (el mercado aceptó el precio alto — el failed auction fue falso)
            current_close > pos.entry_vah
        }
        Side::Long => {
            // LONG invalida si precio CAE más abajo de VAL  
            // (el mercado aceptó el precio bajo — el failed auction fue falso)
            current_close < pos.entry_val
        }
    }
}
```

---

## PARTE 3 — Inconsistencias entre documentos

Los dos documentos (DOCUMENTATION v2 y PROPOSAL) describen versiones diferentes
del sistema. Estas inconsistencias indican qué está implementado en producción
vs. qué es el diseño propuesto.

---

### INCONSISTENCIA-1: Scoring architecture — por componentes vs. por strings

**En DOCUMENTATION (lo que está en producción):**
```rust
// Scoring directo por componentes:
if distance_to_lvn < 0.1 * atr { score += 0.25; }
if cvd_slope_aligned { score += 0.20; }
```

**En PROPOSAL (el nuevo diseño):**
```rust
// Scoring por string matching:
let has = |s: &str| signal.evidence.iter().any(|e| e == s);
if has("target_POC") { score += 0.20; }
if has("ask_absorption") { score += 0.20; }
```

**El problema del diseño de PROPOSAL:**

Si un detector cambia el string `"cvd_not_confirming_breakout"` a `"cvd_divergence"`,
el scoring silenciosamente deja de contar ese componente. El score baja sin ningún
error ni warning. En shadow trading esto significa analizar datos de señales con
scores incorrectos.

**Recomendación:** Usar el approach de DOCUMENTATION (scoring por componentes
numéricos directos) como base, y usar evidences tipados (enum) solo para el log,
no para el scoring.

---

### INCONSISTENCIA-2: Umbral MIN_SCORE

**En DOCUMENTATION:** `MIN_SCORE = 0.60`

**En PROPOSAL:** `min_score = 0.70`

La diferencia de 0.10 es significativa. Con MIN_SCORE=0.60 pasan muchas más
señales marginales. Con 0.70 el sistema es más selectivo.

**¿Cuál está en producción?**

Los logs muestran `score=0.000` en todos los casos porque no hay señales.
No podemos determinar el threshold activo desde los logs actuales.

**Acción:** Verificar el valor de `MIN_SCORE` en el código Rust y decidir
deliberadamente cuál usar. Documentar el valor activo.

---

### INCONSISTENCIA-3: Funding rate thresholds

**En DOCUMENTATION:**
```rust
Side::Long if rate > 0.0006 => -0.20,
Side::Long if rate > 0.0003 => -0.10,
```

**En logs reales (Mayo 17):**
```
Bar 1-54: funding = 0.4646, 0.4622, 0.4523... (¡valores en porcentaje!)
```

Los logs muestran funding como `0.4646` — que si es en porcentaje significa
0.4646% por período de 8h, que es altísimo (normal es 0.01-0.05%).

**Pero** si el código compara con `0.0006` (en decimal, = 0.06%), y los datos
llegan como `0.4646` (también en decimal, = 46.46%), entonces el funding
**siempre está por encima del threshold** y la penalización de -0.20 se aplica
a todas las señales long.

**Hipótesis:** Los datos de funding de Binance llegan como porcentaje (0.01 = 1%
o 0.01 = 0.01%?) y el código los compara en unidades incorrectas.

Verificar la documentación de la API de Binance:

El campo `r` en el stream `markPrice` es el funding rate en decimal donde
`0.0001` = 0.01% (1 basis point). Un funding normal es 0.0001 a 0.0005.

Si los logs muestran `funding=0.4646`, eso sería 46.46% por período — absurdo.
Más probable: el valor se está multiplicando por 1000 o 100 antes de loguear,
o hay una conversión incorrecta en el parsing.

**Esta puede ser la razón por la que el sistema no genera señales:**
si el funding siempre parece extremo, todas las señales long reciben -0.20
y potencialmente caen bajo el threshold.

**Acción urgente:** Verificar las unidades del funding rate en el código.
Comparar `funding=0.4646` en logs vs. el valor real de Binance para ese momento.
Si Binance muestra 0.01%, el código debería loguear `0.0001`, no `0.4646`.

---

### INCONSISTENCIA-4: Invalidation en ValueAreaFailedAuction

**En DOCUMENTATION:**
```rust
"ValueAreaFailedAuction" => {
    // Precio vuelve a entrar al value area
    current_close > pos.entry_val && current_close < pos.entry_vah
}
```

**Problema lógico ya documentado en BUG-6 arriba.** La lógica es incorrecta
para el SHORT — invalida cuando el precio se mueve A FAVOR del trade.

Esto está en DOCUMENTATION (v2), no solo en la PROPOSAL. Está en producción.

---

## PARTE 4 — Verificación del cálculo matemático de PnL

Revisión del paper trading engine contra la documentación.

---

### PNL-1 — CORRECTO: Fórmula básica

```rust
let pnl_gross = (exit_price - entry_price) * position_size * side_multiplier;
```

Para LONG: `side_multiplier = +1.0` → precio sube = ganancia ✓  
Para SHORT: `side_multiplier = -1.0` → precio baja = ganancia ✓

---

### PNL-2 — CORRECTO: Fees en ambos lados

```rust
let entry_fee = entry_price * position_size * fee_rate;  // 0.04%
let exit_fee = exit_price * position_size * fee_rate;    // 0.04%
```

Fee en entry Y en exit — correcto para taker fees. ✓

---

### PNL-3 — CORRECTO: Slippage

```rust
let entry_slippage = entry_price * position_size * slippage_rate;  // 0.02%
let exit_slippage = exit_price * position_size * slippage_rate;
```

Slippage en ambos lados — conservador y correcto. ✓

---

### PNL-4 — PROBLEMA POTENCIAL: Funding binario

```rust
let funding_cost = if position_crossed_funding {
    position_size * entry_price * funding_rate
} else {
    0.0
};
```

**Problema:** Un trade que dura 16h cruza 2 períodos de funding, pero el código
carga solo 1. Un trade que dura 7h59m no cruza ningún período, pero si
el próximo funding es en 1 minuto después del entry, cruza 1 período —
el código puede no detectar esto correctamente dependiendo de cómo se
implementa `position_crossed_funding`.

**Verificar:** ¿`position_crossed_funding` cuenta el número de períodos de
funding (00:00, 08:00, 16:00 UTC) que transcurren entre `entry_time` y
`exit_time`? Si solo es un boolean, puede perder múltiples períodos.

**Corrección:**
```rust
fn count_funding_periods(entry_ms: i64, exit_ms: i64) -> i64 {
    // Funding periods en UTC: 00:00, 08:00, 16:00
    let funding_interval_ms = 8 * 60 * 60 * 1000; // 8 horas en ms
    let entry_period = entry_ms / funding_interval_ms;
    let exit_period = exit_ms / funding_interval_ms;
    exit_period - entry_period
}

let funding_cost = count_funding_periods(pos.entry_ms, exit_ms) as f64
    * position_size * entry_price * funding_rate;
```

---

### PNL-5 — CORRECTO: Fórmula neta

```rust
let pnl_net = pnl_gross - entry_fee - exit_fee - entry_slippage - exit_slippage - funding_cost;
```

El orden de operaciones y el signo son correctos. ✓

---

### PNL-6 — CORRECTO: R:R matemática

Con stops de 1× ATR y targets de 3× ATR:
```
Win rate breakeven = 1 / (1 + 3) = 25%
```

Con fees de ~0.12% total (entry + exit + slippage ambos lados):
En BTC a $78,000, con ATR de $400:
```
Fees = $78,000 × 0.001 × 2 × 0.0004 = ~$0.06/contrato → negligible
Slippage = $78,000 × 0.001 × 2 × 0.0002 = ~$0.03 → negligible
```

Con 1 BTC de position size a $78,000:
```
Stop loss (1R): $400 × 1 BTC = $400
Target (3R):    $1,200 × 1 BTC = $1,200
Fees total:     ~$62.40 (entry + exit en $78,000)
Breakeven real: ($400 + $62.40) / ($1,200 - $62.40) = ~40.6%
```

La documentación dice "~30% win rate para breakeven real" — está subestimando
el impacto de fees con position sizing de 1 BTC. Con 1 BTC a $78,000,
las fees son $62 de un riesgo de $400, que es 15.5% del riesgo por trade.

Esto no es un bug — es que el fee impact es significativo con position sizes grandes.
Con 0.01 BTC el impacto es proporcional y el breakeven se acerca más al 30%.

---

## PARTE 5 — Lista de acciones priorizadas

### Hacer AHORA (antes de confiar en ningún dato del sistema)

| # | Problema | Archivo | Acción |
|---|---------|---------|--------|
| 1 | **Unidades de funding rate** (INCONSISTENCIA-3) | `exchange/binance/ws_handlers.rs` | Verificar si funding llega como decimal (0.0001) o porcentaje (0.01). Comparar log vs Binance UI |
| 2 | **Datos institucionales null** (LOG-4) | `monitor/src/main.rs` | Agregar `inst=live\|null` al bar log. Si es null, el pipeline institucional no funciona |
| 3 | **Breakdown del NO_VALID_SETUP** (LOG-5) | Cada detector | Hacer que cada detector loguee su razón de rechazo cuando no genera señal |
| 4 | **Warm-up histórico** (LOG-1) | `monitor/src/main.rs` | Fetch 50 barras M5 al startup antes del primer bar live |

### Hacer PRONTO (antes de tomar señales como válidas)

| # | Problema | Archivo | Acción |
|---|---------|---------|--------|
| 5 | **taker_imbalance thresholds** (BUG-1) | `value_area_failed_auction.rs` | Cambiar `< 0.25` a `< 0.05` y `> -0.25` a `> -0.05` |
| 6 | **BelowVal en VWAP LONG** (BUG-2) | `vwap_value_pullback_continuation.rs` | Eliminar `BelowVal` de la condición long, `AboveVah` del short |
| 7 | **CVD absoluto como gate** (BUG-3) | `router.rs` o cada detector | Convertir CVD absoluto de modifier a gate duro con `return None` |
| 8 | **Hysteresis de régimen** (LOG-2) | `data/src/strategy/context.rs` | Implementar umbral asimétrico: ±0.15 para salir, ±0.10 para entrar |
| 9 | **InvalidationCondition VAFA** (BUG-6) | `paper.rs` | Corregir lógica — SHORT invalida si precio sube sobre VAH, no si entra al VA |
| 10 | **partial_cmp panic** (BUG-5) | `lvn_liquidity_vacuum_breakout.rs` | Agregar `.filter(|x| x.is_finite())` antes del sort |

### Hacer ANTES de escalar

| # | Problema | Archivo | Acción |
|---|---------|---------|--------|
| 11 | **Funding binario vs períodos** (PNL-4) | `paper.rs` | Cambiar boolean `crossed_funding` a contador de períodos |
| 12 | **MIN_SCORE ambiguo** (INCONSISTENCIA-2) | `types.rs` | Decidir 0.60 o 0.70, documentar y unificar |
| 13 | **ATR gate unificado** (BUG-4) | `router.rs` | Centralizar guard de ATR mínimo antes de llamar detectores |

---

## PARTE 6 — El hallazgo más urgente a verificar hoy

De todos los problemas encontrados, el más urgente es la **INCONSISTENCIA-3
(unidades de funding rate)** porque si está presente, explica directamente
por qué el sistema no genera señales:

```
Funding en logs: 0.4646
Threshold en código: 0.0006 (para penalizar)

Si funding = 0.4646 (en cualquier unidad):
  0.4646 > 0.0006 → SIEMPRE aplica -0.20 a todos los longs
  
Con penalización permanente de -0.20:
  Score máximo de VwapPullback = 0.30 + 0.25 + 0.20 + 0.15 + 0.10 = 1.00
  Score máximo con funding penalty = 1.00 - 0.20 = 0.80
  
Pero si hay otros modifiers negativos, el score puede caer bajo MIN_SCORE.
```

Si el funding real de Binance en esas horas era 0.01%-0.05% (normal), pero
el sistema lo interpreta como 0.4646% (46× más alto de lo real), entonces:
- Todas las señales long reciben -0.20 de penalización innecesaria
- El sistema puede estar rechazando señales válidas constantemente

**Verificar en 5 minutos:**

```bash
# Buscar el funding rate de Binance en ese momento vía API
curl "https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=5"

# Comparar el valor retornado con lo que aparece en los logs
# Si Binance retorna 0.0001 y los logs muestran 0.4646 → hay conversión incorrecta
```

---

*Auditoría basada en análisis estático de documentación + evidencia directa de logs de Railway.*  
*Para completar esta auditoría se necesita acceso al código fuente Rust.*  
*Prioridad máxima: verificar unidades de funding rate y estado del pipeline institucional.*
