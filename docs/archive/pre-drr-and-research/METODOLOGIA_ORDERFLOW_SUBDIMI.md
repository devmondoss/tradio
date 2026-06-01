# Metodología Order Flow Subdimi — Traducida a FlowSurface

> Documento de análisis del sistema: cómo la metodología de Subdimi/Supreme Trading
> se implementa concretamente en los detectores de FlowSurface.
>
> Fuentes primarias: 18 markdown en `orderflow/`, código Rust en `data/src/strategy/detectors/`,
> `data/src/strategy/vp_open_bias.rs`, y `config/strategy.toml`.

---

## Índice

1. [Fundamentos teóricos](#1-fundamentos-teóricos)
2. [Los 5 patrones core](#2-los-5-patrones-core)
3. [VP Open Bias — Las 4 variantes de apertura](#3-vp-open-bias--las-4-variantes-de-apertura)
4. [Tabla de mapeo: Patrones → Detectores](#4-tabla-de-mapeo-patrones--detectores)
5. [Descripción completa de cada detector](#5-descripción-completa-de-cada-detector)
6. [Implementaciones directas vs pendientes](#6-implementaciones-directas-vs-pendientes)
7. [Comparación de parámetros](#7-comparación-de-parámetros)

---

## 1. Fundamentos teóricos

### 1.1 Action Market Theory (AMT)

El marco conceptual base de toda la metodología. El mercado es una subasta continua:

- El mercado alterna entre **balance** (rango, eficiencia, fair value encontrado) e **imbalance** (tendencia, ineficiencia, búsqueda de nuevo valor).
- Se estima que **80% del tiempo el mercado está en balance**, 20% en imbalance.
- El precio siempre busca el **fair value** — la distribución de Gauss donde más volumen se negoció.

Las 5 reglas AMT de Subdimi:
1. Si el precio acepta dentro del Value Area → visitará el otro extremo (VAH↔VAL)
2. El precio oscila entre VAH y VAL mientras esté en balance
3. Si rompe el balance → busca el balance previo en esa dirección
4. Si el precio llega al POC y lo rompe → invalida la rotación, continúa
5. Si hay construcción de volumen bajo uno de los extremos → breakout inminente

### 1.2 Volume Profile — Estructura base

```
Value Area High (VAH) ── 85% percentil de volumen
Point of Control (POC) ── precio donde más volumen se negoció
Value Area Low (VAL) ── 15% percentil de volumen
```

**Value Area = 68-70% del volumen total** (1 desviación estándar).

Cascada de temporalidades para construir bias:
- **Mensual** → contexto macro (¿estamos por encima/debajo del POC mensual?)
- **Semanal** → bias de la semana (¿POC semanal sube/baja?)
- **Diario** → tipo de día (4 variantes de VP Open Bias)
- **Sesión** → zonas de acción intraday

**HVN (High Volume Node)**: cluster de alto volumen → zona de soporte/resistencia, TP natural.  
**LVN (Low Volume Node)**: zona de bajo volumen → el precio la atraviesa rápido (vacío de liquidez), stop-less zone.  
**Naked POC**: POC previo sin retestear → imán de precio.

### 1.3 El trío de fuerza: Volumen + Delta + OI

Subdimi usa tres métricas juntas para medir la "fuerza" de un movimiento:

| Combinación | Interpretación |
|---|---|
| Volumen↑ + OI↑ + Delta positivo | Fuerza alcista genuina (nuevas posiciones long) |
| Volumen↑ + OI↑ + Delta negativo | Fuerza bajista genuina (nuevas posiciones short) |
| Volumen↑ + OI↓ | Liquidaciones (stop-losses), no nuevas posiciones |
| Volumen↓ | Movimiento sin convicción, no seguir |

**Delta** = compras − ventas (market orders). Positivo = netos compradores.  
**CVD (Cumulative Delta)** = delta acumulado. Diverge del precio cuando hay absorción institucional.  
**OI (Open Interest)** = contratos abiertos. OI cae en sweep = liquidaciones de stops, no conviction.

---

## 2. Los 5 patrones core

### 2.1 Absorción / Traders Atrapados

**Concepto Subdimi:**
> "Los institucionales absorben las órdenes agresivas con órdenes límite grandes. El precio no se mueve a pesar del volumen. Los que entraron agresivamente quedan atrapados."

**Lógica completa:**
1. Precio llega a zona de VAL/VAH (extremo del Value Area)
2. Se forma una vela o mechazo que penetra el nivel
3. El delta de esa vela es extremadamente negativo (muchas ventas absorbidas) en zona de VAL  
   O extremadamente positivo (muchas compras absorbidas) en zona de VAH
4. El precio **cierra de vuelta dentro** del Value Area (los agresivos quedan atrapados)
5. Se visualiza en Footprint: muchos contratos en bid (compras absorbidas) sin movimiento de precio

**Señales de confirmación:**
- 3+ niveles consecutivos con delta negativo (absorción en VAL) o positivo (absorción en VAH)
- OBI_L5 positivo (books compradores en VAL)
- CVD slope no muy negativo (no hay más vendedores agresivos)

**Big Trades component:** Las "burbujitas" (Big Trades) aparecen en la zona de absorción — se posicionan contra los atrapados.

**Implementación FlowSurface:** `footprint_absorption_reversal.rs`

```rust
// LONG en VAL: 3 niveles consecutivos delta < 0 dentro de [VAL-0.5ATR, VAL+0.3ATR]
let long_levels = footprint_levels atrapados en zona VAL
let long_setup = long_zone
    && has_delta_run(&long_levels, want_negative=true, min_run=3)
    && obi_l5 > 0.0          // libro comprador
    && cvd_slope > -0.05     // vendedores fading
    && taker_imbalance > -0.10
    && !TrendDown && !Stress
    && spread_bps <= max_spread_bps
```

Stop: `VAL − ATR`. Target: POC → VAH.

---

### 2.2 Delta Drain

**Concepto Subdimi:**
> "El delta va perdiendo fuerza en cada empuje del precio hacia la misma dirección. En cada nuevo high el delta es menor. Los compradores están perdiendo convicción."

**Lógica completa:**
1. Precio en zona de resistencia (VAH o HVN)
2. Se forman 2-3 velas alcistas consecutivas pero el delta decrece en cada una
3. El delta se "drena" — cada empuje tiene menos fuerza compradora
4. Entrada: confirmación de flip de delta + fakeout de precio action
5. Target: POC o VAL

**Diferencia con Absorción:** En el Delta Drain el precio SÍ avanza pero con delta menguante. En Absorción el precio NO avanza a pesar del volumen.

**Red flags:** Si el delta sube junto con el precio → no es Delta Drain, es momentum genuino.

**Implementación FlowSurface:** Parcialmente en `value_area_failed_auction.rs`  
El detector VAFA captura el caso de precio que intenta establecerse sobre VAH pero el delta confirma fracaso (`failed_acceptance = true`, `delta < 0` para SHORT).

---

### 2.3 Liquidity Sweep + Volume

**Concepto Subdimi:**
> "Las instituciones barren los stops que están acumulados por encima de los máximos o por debajo de los mínimos. El precio va a buscar esa liquidez, la consume, y revierte."

**Lógica completa:**
1. Existen stops acumulados sobre un máximo reciente (longs atrapados) o bajo un mínimo (shorts atrapados)
2. El precio hace un barrido (sweep) rompiendo ese nivel
3. **CRÍTICO**: debe haber volumen elevado en el sweep — si no hay volumen, no hay liquidaciones reales
4. El OI **desciende** en el sweep = son liquidaciones (stops ejecutados), no nuevas posiciones
5. El precio revierte rápidamente de vuelta al rango

**Señales que invalidan:**
- Sweep sin volumen significativo → falso break, no hay liquidez que capturar
- OI que sube en el sweep → son nuevas posiciones, no liquidaciones, el movimiento continúa

**Implementación FlowSurface:** `liquidation_hunt.rs`

```rust
// LONG — barrido de shorts detectado:
let liq_confirms_long =
    dominant_side == LiqSide::Shorts    // shorts siendo liquidados
    && (total_zscore > 1.5              // outlier estadístico vs distribución rolling
        || short_liq_usd_5m > 100_000)  // fallback: $100k+ en 5 min

let momentum_long =
    oi_trend.slope_5bar > 0.0           // nuevas posiciones long entrando
    && taker_imbalance > 0.15           // compradores agresivos
    && cvd_slope > 0.0                  // CVD confirmando

let path_clear_long = ob.thin_zone_above  // sin resistencia inmediata

// Gates:
// - funding extremo + smart money short → no entrar long
// - cascade ya activa ($5M+ en 60s) → tarde para entrar
```

---

### 2.4 Finish Action / Unfinish Action

**Concepto Subdimi:**
> "Finish Action es cuando el bid o el ask llega a cero en el extremo del precio. No quedan más traders interesados. El precio se convierte en imán de reversión."
>
> "Unfinish Action es lo opuesto — queda un bid o ask sin completar, el precio lo irá a buscar antes de regresar."

**Lógica completa:**

**Finish Action (reversal magnet):**
- En Footprint: el bid llega a 0 en el precio más bajo de la vela → nadie más quiere vender aquí
- O el ask llega a 0 en el precio más alto → nadie más quiere comprar aquí
- Señal: reversión inminente, el precio "ya no tiene interés" en ese nivel
- Entrada: siguiente vela que abra en dirección contraria

**Unfinish Action (continuation magnet):**
- En Footprint: queda volumen sin ejecutar (bid o ask incompleto)
- Ese volumen pendiente "atrae" al precio de vuelta antes de continuar
- El precio regresará a completar esa orden antes de moverse

**Implementación FlowSurface:** **NO implementado directamente.**  
El campo `footprint_levels: Vec<FootprintLevel>` en `OrderFlowContext` contiene `buy_volume` y `sell_volume` por precio, pero la detección de "bid=0" o "ask=0" en el extremo no está implementada en ningún detector actual. Esta es una **brecha pendiente**.

---

### 2.5 CVD Divergence

**Concepto Subdimi:**
> "El precio hace un Higher High pero el CVD no lo confirma (o va bajando). Esto indica que los institucionales están absorbiendo las compras. La reversión es inminente."
>
> Válido solo cuando: en divergencia bullish el precio hace Higher Lows (no HH); en divergencia bearish el precio hace Lower Highs.

**Lógica completa:**

**Bearish Divergence (SHORT):**
- Precio hace nuevos máximos o se sostiene en zona alta
- CVD se aplana o decrece → las compras agresivas están siendo absorbidas por vendedores grandes
- Señal: reversión hacia abajo
- Condición de validez: precio debe estar cerca de VAH (dentro de 1 ATR)

**Bullish Divergence (LONG):**
- Precio hace nuevos mínimos o se sostiene en zona baja
- CVD se aplana o sube → las ventas agresivas están siendo absorbidas por compradores grandes
- Señal: reversión hacia arriba
- Condición de validez: precio debe estar cerca de VAL (dentro de 1 ATR)

**Implementación FlowSurface:** `cvd_divergence_reversal.rs`

```rust
// La divergencia se mide con cvd_divergence_persistence (i32):
// +4 o más → bearish divergence activa (precio↑ pero CVD no confirma) → SHORT
// -4 o menos → bullish divergence activa (precio↓ pero CVD no confirma) → LONG

if persist >= 4 {
    // Bloquear en TrendUp / Expansion (divergencia puede ser absorción de fake breakout)
    // Precio debe estar cerca de VAH (px >= vah - ATR && px <= vah + 1.5*ATR)
    // Entry = px, stop = vah + 0.5*ATR, target = POC
    // R:R mínimo = cfg.min_rr (1.5)
}
```

---

## 3. VP Open Bias — Las 4 variantes de apertura

Metodología central de Subdimi para clasificar el tipo de día antes de operar.

**Fuente de datos:** Perfil de volumen de la sesión previa (POC, VAH, VAL, high/low de sesión).

### 3.1 InsideValue — Día de Rango

**Condición:** El precio de apertura está dentro del Value Area previo (VAL ≤ open ≤ VAH).

**Interpretación:** El mercado está de acuerdo con el precio justo previo. No hay nueva información. Será un día de rango donde el precio oscilará entre extremos.

**Plan de trading:**
- Target principal: POC de la sesión previa
- Si llega al POC, rotar al extremo opuesto (VAH o VAL)
- Stop: más allá del high/low del día anterior
- No buscar breakouts — esperar rotaciones internas

**Implementación FlowSurface:**
```rust
// vp_open_bias.rs
DailyVpBias::InsideValue  // cuando val <= open <= vah
is_range_day() → true
```

---

### 3.2 OutsideVaInsidePa — Aceptación hacia POC

**Condición:** El precio abre fuera del Value Area pero dentro del rango de precios de la sesión anterior (entre session_low y session_high).

**Interpretación:** El mercado abre con sesgo direccional pero dentro de territorio conocido. Se espera que el precio "acepte" hacia el POC previo — los traders del día anterior que no cerraron posiciones presionan hacia el fair value.

**Plan de trading (SHORT cuando open > VAH):**
- Entry: en zona de apertura con señal de rechazo
- Target 1: POC del día anterior
- Target 2: apertura del día
- Stop: por encima del máximo del día anterior

**Plan de trading (LONG cuando open < VAL):**
- Espejo del anterior hacia arriba

**Implementación FlowSurface:**
```rust
DailyVpBias::OutsideVaInsidePa
bias_supports_short() → true cuando session_open > prev_vah
bias_supports_long()  → true cuando session_open < prev_val
```

---

### 3.3 TrendDay — Día Tendencial

**Condición:** El precio abre completamente fuera del rango de precios de la sesión anterior (gap real por encima del session_high o por debajo del session_low).

**Interpretación:** Nueva información significativa. El mercado está en imbalance puro. No esperar rotación — seguir la tendencia del gap.

**Plan de trading:**
- Buscar mini rangos de consolidación para entrada en continuación
- Target: balances históricos (VP de semanas/meses previos)
- No fade el movimiento
- Stop: solo si el precio acepta de vuelta dentro del rango previo (→ cambia a FadeGap)

**Implementación FlowSurface:**
```rust
DailyVpBias::TrendDay  // cuando open > session_high o open < session_low
is_trend_day() → true
```

---

### 3.4 FadeGap — Fade del Gap

**Condición:** El precio abrió fuera del Value Area o del rango de la sesión anterior, pero en los primeros minutos/velas **acepta de vuelta dentro** del Value Area.

**Interpretación:** El gap fue rechazado. Los traders institucionales empujaron el precio de vuelta a fair value. La dirección del gap fue un fakeout.

**Plan de trading:**
- Fade la dirección del gap original
- Target: POC, luego extremo opuesto del VA
- Stop: si el precio vuelve a salir en la dirección del gap original

**Implementación FlowSurface:**
```rust
DailyVpBias::FadeGap  // cuando opened outside + accepted_inside = true

// DailyVpTracker detecta la aceptación automáticamente:
// Si bias era OutsideVaInsidePa o TrendDay Y el precio re-entra en el VA
// → reclasifica a FadeGap
let accepted = low <= vah && high >= val;
if accepted { *bias_ctx = classify_open(session_open, prev, true); }
```

---

## 4. Tabla de mapeo: Patrones → Detectores

| Patrón Subdimi | Detector FlowSurface | Estado |
|---|---|---|
| Absorción / Traders Atrapados | `FootprintAbsorptionReversal` | ✅ Implementado |
| Delta Drain en VAH/VAL | `ValueAreaFailedAuction` | ✅ Implementado (parcial) |
| Liquidity Sweep + Volume | `LiquidationHunt` | ✅ Implementado |
| CVD Divergence | `CvdDivergenceReversal` | ✅ Implementado |
| VP Open Bias (4 variantes) | `vp_open_bias.rs` + `DailyVpTracker` | ✅ Implementado |
| LVN Volume Gap | `LvnLiquidityVacuumBreakout` | ✅ Implementado |
| VWAP Pullback en tendencia | `VwapValuePullbackContinuation` | ✅ Implementado |
| Order Block Retest | `OrderBlockRetest` | ✅ Implementado |
| Session Open Breakout | `SessionOpenBreakout` | ✅ Implementado |
| Smart Money Divergence (LS Ratio) | `SmartMoneyDivergence` | ✅ Implementado |
| Funding Exhaustion (carry insostenible) | `FundingExhaustionReversal` | ✅ Implementado |
| **Finish Action (bid/ask = 0)** | — | ❌ Pendiente |
| **Big Trades como stops dinámicos** | — | ❌ Pendiente (parcial via OrderBlock) |
| **Single Prints (TPO)** | — | ❌ Pendiente |
| **Stacked Imbalances (FBG detection)** | `stacked_imbalance` field | ⚠️ Campo exists, no detector propio |
| **Delta Profile (clusters)** | — | ❌ Pendiente |
| Toxic Flow Gate (VPIN) | `toxic_flow_gate.rs` | ✅ Implementado |
| DOM Imbalance | `DomImbalanceBreakout` | ✅ Implementado |

---

## 5. Descripción completa de cada detector

### 5.1 `toxic_flow_gate` — Guardián de flujo tóxico

Evaluado una sola vez en el router antes de llamar a cualquier detector. Es el filtro global.

**Bloquea cuando:**
- Régimen `Stress` o `Aftermath` (mercado en pánico/post-pánico)
- Flow quality, Volume Profile quality u OrderBook quality != `DataQuality::Live`
- Spread > `max_spread_bps` (2.0 bps por defecto)
- VPIN > `max_vpin` (0.75 por defecto) — flujo tóxico detectado
- Spoof detectado (si `spoof_gate_enabled = true`)

**Relación con Subdimi:** Corresponde a los "red flags" que menciona: spread excesivo = mercado no participable, VPIN alto = hay traders mejor informados dominando el flujo.

---

### 5.2 `FootprintAbsorptionReversal` — Absorción en extremos del VA

**Detecta:** Patrón de Absorción / Traders Atrapados en VAL (LONG) o VAH (SHORT).

**LONG (absorción en VAL):**
- Zona: `px >= VAL − 0.5*ATR && px <= VAL + 0.3*ATR && px > VAL`
- Footprint: ≥ 3 niveles consecutivos con delta < 0 en esa zona (ventas siendo absorbidas)
- OBI_L5 > 0 (libro comprador activo)
- CVD slope > −0.05 (vendedores perdiendo fuerza)
- Taker imbalance > −0.10
- Régimen: no TrendDown, no Stress
- Stop: `VAL − ATR` | Target: POC → VAH

**SHORT (absorción en VAH):** espejo simétrico con delta positivo.

**Notas de implementación:**
- `has_delta_run()` ordena los niveles por precio y busca racha de ≥ 3 niveles
- La función `rr_ok()` valida R:R ≥ `min_rr` antes de emitir

---

### 5.3 `ValueAreaFailedAuction` (VAFA) — Subasta fallida

**Detecta:** Intento de breakout sobre VAH o bajo VAL que fracasa — el precio no logra establecerse fuera del VA.

**SHORT (fallo sobre VAH):**
- Precio justo por debajo de VAH (dentro de 0.5 ATR)
- `failed_acceptance = true` (el precio intentó establecerse sobre VAH y regresó)
- Delta < 0 (momentum alineado con SHORT)
- `footprint_absorption == Ask` (vendedores absorbiendo compradores en la zona)
- CVD slope ≤ 0 && taker_imbalance < 0.05
- No `thin_zone_above` (si hay zona vacía arriba, podría ser breakout real)
- Stop: `max(VAH + 0.25*ATR, px + 0.5*ATR)` | Target: POC
- R:R ≥ 1.5 obligatorio

**Régimen Chop** requiere gates más estrictos: CVD slope < −0.20 && taker_imbalance < −0.15 && delta/ATR < −0.35

**Relación con Subdimi:** Captura el "Delta Drain" cuando el precio llega a VAH con delta menguante y `failed_acceptance` marcado.

---

### 5.4 `LiquidationHunt` — Caza de liquidaciones

**Detecta:** Barrido de liquidaciones (sweep + volume) con momentum confirmando reversión.

**LONG (barrido de shorts):**
- `dominant_side == LiqSide::Shorts` → se están liquidando cortos
- Magnitud: z-score > 1.5 (outlier estadístico) OR `short_liq_usd_5m > 100_000`
- `oi_trend.slope_5bar > 0` → nuevas posiciones long (no solo liquidaciones)
- `taker_imbalance > 0.15` → compradores agresivos
- `cvd_slope > 0` → CVD confirmando
- `thin_zone_above` → camino libre (sin resistencia inmediata)
- Gate funding: no entrar long si funding extremo + smart money short
- Gate cascade: `long_liq_usd_5m < 5_000_000` (si ya es cascada, tarde para entrar)

**Targets:** candidatos = `vp.hvn_nearby` + `vp.vah` + `ob.walls_above` → el más cercano que cumpla `entry + min_rr * ATR`.

**LiqMap (logging):** Cuando `liq_map` está disponible, se agrega evidencia `liq_target_above` y `high_liq_density_above` — sin cambio de lógica de gate, solo logging informativo.

---

### 5.5 `CvdDivergenceReversal` — Divergencia CVD

**Detecta:** Divergencia persistente entre precio y CVD ≥ 4 barras.

**SHORT (divergencia bearish):**
- `cvd_divergence_persistence >= 4`
- Precio cerca de VAH (dentro de 1 ATR)
- Régimen: no TrendUp, no Expansion
- Stop: `VAH + 0.5*ATR` | Target: POC

**LONG (divergencia bullish):**
- `cvd_divergence_persistence <= −4`
- Precio cerca de VAL (dentro de 1 ATR)
- Régimen: no TrendDown
- Stop: `VAL − 0.5*ATR` | Target: POC

**Evidencia extra:** Si `stacked_imbalance == Bearish/Bullish`, `vpin < 0.35`, o régimen confirma → se agrega a `evidence[]`.

---

### 5.6 `LvnLiquidityVacuumBreakout` — Vacío de liquidez en LVN

**Detecta:** El patrón de "Volume Gap" / Low Volume Node que Subdimi usa para scalping en tendencia.

> "Cuando el precio se mueve rápido, ahí fue donde se posicionaron los traders grandes. Al retestear esa zona, no hay contrapartida → se mueve rápido de nuevo."

**LONG:**
- `thin_zone_above = true` (zona vacía de liquidez arriba)
- LVN nearby (dentro de 1 ATR)
- `price_vs_vwap == Above` (por encima del VWAP de sesión)
- `value_location == InValue || AboveVah`
- Delta > 0, CVD slope > 0
- No `ask_wall_nearby` (stop si hay resistencia inmediata)
- Stop: anclado en VWAP de sesión o `entry − 0.75*ATR`, máximo `entry − 1.5*ATR`
- Target: siguiente HVN o VAH

---

### 5.7 `VwapValuePullbackContinuation` — Continuación de tendencia

**Detecta:** Retroceso a la zona de valor (lower 40% del VA para longs, upper 40% para shorts) con realineación de flow.

**LONG (en TrendUp/Expansion):**
- Precio en lower 40% del Value Range `(px − VAL) / (VAH − VAL) ≤ 0.40`
- Price > AVWAP-BOS o > VWAP sesión
- `value_location == InValue`
- Delta > 0, CVD slope ≥ 0, taker_imbalance > 0
- `fast_slope`: −0.05 < fs < 0.50 (ni demasiado débil ni agotamiento)
- `slow_slope > 0.12` (tendencia firme)
- `funding_ok_long`: bloquea si FundingRegime es ElevatedLong o ExtremeLong
- Stop: `max(VAL, entry − ATR)` | Target: HVN → VAH → swing_high_20

**SHORT (en TrendDown/Expansion):**
- Precio en upper 40% del Value Range
- Price < AVWAP-BOS
- `fast_slope ≤ −0.10` (momentum bajista confirmado)
- `slow_slope < −0.12`
- Bloquea si hay `BullishAbsorption` CVD divergence

---

### 5.8 `OrderBlockRetest` — Retesteo de Order Block

**Detecta:** Precio regresando a un Order Block activo con absorción confirmada.

> Subdimi: "Un buen OB tiene volumen 3-10x la media, delta atrapado, y fuerte delta iniciativo opuesto después."

**LONG (bullish OB):**
- `nearest_bullish.is_some()` && `ob.price_inside(px)`
- `ob.status == Active || Tested`
- `footprint_absorption == Bid` (compradores absorbiendo en el OB)
- CVD slope ≥ 0
- OBI_L5 > 0
- Régimen: no TrendDown, no Stress, no Aftermath
- Stop: `ob.low − 0.5*ATR` | Target: `swing_high_20`
- Evidencia extra si `ob.volume_ratio > 1.5` o `ob.swings_broken >= 2`

---

### 5.9 `SessionOpenBreakout` — Breakout de apertura de sesión

**Detecta:** Breakout de sesión en la fase `OpeningRush` de London, LondonNyOverlap, o NewYork.

**LONG:**
- `session.phase == OpeningRush`
- `px > swing_high_20`
- CVD slope > 0.15
- `thin_zone_above` (sin resistencia)
- OBI_L5 > 0.20
- `fast_slope > 0.10`
- Stop: `entry − 0.5*ATR` | Target: `swing_high_20 + ATR`

**Relación con Subdimi:** Subdimi no opera la primera vela de las 9:30 pero usa la apertura de NY para buscar setups. Los primeros 30 minutos son de alto riesgo; el detector exige `OpeningRush` (no los primeros minutos exactos).

---

### 5.10 `SmartMoneyDivergence` — Divergencia smart money vs retail

**Detecta:** Divergencia entre posicionamiento de smart money (top traders) y retail (L/S ratio).

> Subdimi usa los Big Trades para identificar quién está mejor posicionado. Este detector usa datos de L/S ratio como proxy institucional.

**SHORT:**
- `top_traders_long_pct < 0.45` (smart money predominantemente short)
- `retail_long_pct > 0.60` (retail predominantemente long)
- Divergencia `retail − top > min_divergence` (0.18 por defecto)
- `funding.regime == ElevatedLong || ExtremeLong`
- OI maduro (no AccumulatingFast)
- Precio en resistencia (cerca de VAH ± 0.5*ATR o cerca de wall_above)
- CVD slope ≤ 0
- Gate: cascade no activa
- Stop: `entry + 1.5*ATR` | Target: VAL

---

### 5.11 `FundingExhaustionReversal` — Agotamiento por funding extremo

**Detecta:** El costo de carry es insostenible — demasiados longs/shorts que comenzarán a cerrar.

**SHORT (funding extremo positivo):**
- `funding.regime == ExtremeLong && funding.current > 0.001` (0.10%)
- `top_traders_long_pct < 0.52 && retail_long_pct > 0.62`
- OI en tendencia decreciente (Decreasing o DecreasingFast)
- CVD slope ≤ 0 && `buy_sell_ratio < 1.0`
- `long_liq_usd_5m < 5_000_000` (no cascade activa)
- Stop: `max(VAH, entry + 0.75*ATR)` | Target: VAL

Evidencia extra: `funding_velocity < 0` (funding retrocediendo), `funding_peak_confirmed`.

---

### 5.12 `DomImbalanceBreakout` — Imbalance en DOM

Detecta imbalances extremos en el orderbook (DOM) como señal de breakout.

---

## 6. Implementaciones directas vs pendientes

### Implementados directamente (metodología Subdimi 1:1)

| Patrón | Detector | Fidelidad |
|---|---|---|
| VP Open Bias (4 variantes) | `vp_open_bias.rs` | Alta — lógica idéntica a la descrita por Subdimi |
| Absorción / Traders Atrapados | `FootprintAbsorptionReversal` | Alta — footprint levels + delta run + VAL/VAH zones |
| CVD Divergence | `CvdDivergenceReversal` | Alta — persistence threshold, regimes, VAH/VAL proximity |
| Liquidity Sweep + Volume | `LiquidationHunt` | Alta — z-score, taker imbalance, OI slope, thin zone |
| Volume Gap / LVN | `LvnLiquidityVacuumBreakout` | Alta — LVN nearby + thin zone + flow confirmation |
| Delta Drain (failed VAH) | `ValueAreaFailedAuction` | Media — captura failed_acceptance pero no mide delta_drain rate |

### Implementados como extensión (no mencionados explícitamente por Subdimi pero alineados)

| Patrón | Detector | Relación |
|---|---|---|
| VWAP pullback | `VwapValuePullbackContinuation` | Subdimi usa VWAP para scalping; este detector lo sistematiza |
| Order Block | `OrderBlockRetest` | Subdimi tiene metodología OB completa; detector simplifica a 6 condiciones |
| Session Open | `SessionOpenBreakout` | Subdimi opera NY open; detector formaliza la condición OpeningRush |
| Smart Money | `SmartMoneyDivergence` | Proxy de Big Trades via L/S ratio |
| Funding exhaustion | `FundingExhaustionReversal` | Subdimi menciona funding como señal secundaria |

### Pendientes (brechas identificadas)

| Patrón | Descripción | Complejidad |
|---|---|---|
| **Finish Action** | Bid=0 o Ask=0 en extremo del footprint → imán de reversión | Media — requiere footprint.bid/ask volumen por precio |
| **Unfinish Action** | Bid o Ask incompleto → imán de continuación | Media |
| **Single Prints (TPO)** | 30-min interval con single letter = imbalance verdadero | Alta — requiere Market Profile (TPO) computation |
| **Stacked Imbalances (FBG)** | 300%+ diferencia en 3+ niveles consecutivos | Baja — field existe, falta detector propio |
| **Delta Profile clusters** | Dónde se concentra el delta en un rango (distribución) | Alta — requiere delta histogram por rango |
| **Big Trades como stops dinámicos** | Mover stop debajo/encima de la última "burbuja" grande | Media — requiere rastreo de big trades por precio |
| **Naked POC detection** | POC de sesiones previas sin retestear | Media — requiere historial de VPs previos |

---

## 7. Comparación de parámetros

### Umbrales descritos por Subdimi vs configuración FlowSurface

| Parámetro | Valor Subdimi | FlowSurface (`config/strategy.toml`) | Notas |
|---|---|---|---|
| **Liquidaciones mínimas para confirmar sweep** | "debe haber volumen significativo" (no cuantifica) | `liq_hunt.min_usd = 100_000` | Subido de 25k a 100k — el original filtraba ruido normal |
| **Threshold de cascada** | "tarde para entrar" — no cuantifica | `liq_hunt.cascade_threshold = 5_000_000` | $5M en 60s = cascada activa |
| **CVD divergence persistence** | "divergencia sostenida" (no menciona barras exactas) | `PERSIST_THRESHOLD = 4` barras | Estimado razonable para M5 BTC |
| **Stacked imbalances %** | 300% diferencia mínima entre bid/ask | Campo `stacked_imbalance` (sin threshold explícito en config) | Threshold hardcodeado en el procesador de data |
| **OB volume ratio** | "3-10x la media" | `ob.volume_ratio > 1.5` para evidencia extra | FlowSurface es más permisivo |
| **OB swings broken** | "precio debe romper estructura después del OB" | `ob.swings_broken >= 2` | Aproximación de estructura via swings |
| **R:R mínimo** | "siempre busco 2:1 mínimo" | `min_rr = 1.5` | FlowSurface ligeramente más permisivo |
| **VPIN gate** | "no operar cuando hay flujo tóxico" | `max_vpin = 0.75` | Umbral moderado |
| **Funding extremo** | "arriba de 0.1% es peligroso" | `funding_extreme_threshold = 0.001` (0.10%) | Coincide exacto |
| **Smart money short threshold** | "top traders < 45-50% long" | `smart_short_threshold = 0.45` | Coincide con lo descrito |
| **Retail long threshold** | "retail > 60% long" | `retail_long_threshold = 0.60` | Coincide |
| **Divergencia mínima retail vs smart** | "diferencia significativa" | `min_divergence = 0.10` (antes 0.18) | Ajustado en calibración |
| **Spread máximo** | "no operar con spread ancho" | `max_spread_bps = 2.0` | 2 bps en BTC ≈ 2 ticks |
| **TTL señal default** | N/A (discrecional) | `default_ttl_min = 250` minutos | Paper trading solamente |
| **Cooldown entre señales** | N/A | `cooldown_bars = 5` velas | Previene señales duplicadas |
| **Score mínimo** | N/A | `min_score = 0.60` / `min_score_institutional = 0.55` | Sistema de scoring propio |

### Parámetros de scalping (Subdimi) — no directamente en FlowSurface

Subdimi usa M1/M5 para ejecución con M30 de contexto. FlowSurface actualmente opera en M5 como temporalidad principal. Los parámetros de gestión (stop trailing, tamaño de posición, target parciales) son responsabilidad del módulo de ejecución/paper trading, no del detector.

---

## Apéndice: Flujo de señal en FlowSurface

```
StrategyMarketContext + InstitutionalContext
            │
            ▼
    toxic_flow_gate()     ← Bloqueo global (VPIN, spread, quality, régimen)
            │
            ▼  (si pasa)
    AuctionState gate     ← Bloqueo por estado de subasta (si activo)
            │
            ▼
    Cooldown check        ← ¿Pasaron ≥ 5 barras desde la última señal?
            │
            ▼
    Detectores (paralelo):
    ┌──────────────────────────────────────────┐
    │ VwapValuePullbackContinuation            │ ← Tendencia + pullback
    │ LvnLiquidityVacuumBreakout              │ ← Volume gap breakout
    │ ValueAreaFailedAuction                   │ ← Failed auction VAH/VAL
    │ FootprintAbsorptionReversal              │ ← Absorción en extremos
    │ CvdDivergenceReversal                    │ ← Divergencia CVD
    │ OrderBlockRetest                         │ ← OB retest
    │ DomImbalanceBreakout                     │ ← DOM imbalance
    │ SessionOpenBreakout                      │ ← Opening rush
    │ LiquidationHunt (inst)                   │ ← Sweep + volume
    │ SmartMoneyDivergence (inst)              │ ← LS ratio divergence
    │ FundingExhaustionReversal (inst)         │ ← Funding carry
    └──────────────────────────────────────────┘
            │
            ▼
    Scoring (evidence weights × HTF multiplier)
            │
            ▼
    Score ≥ min_score?
            │ Sí
            ▼
    ShadowSignal emitida (paper trading)
            │
            ▼
    OutcomeTracker → análisis posterior
```

Todos los detectores emiten `StrategyAction::ShadowSignal` — ninguna señal ejecuta operaciones reales. El sistema es paper trading hasta que se tenga estadística de ≥ 100 señales por detector.
