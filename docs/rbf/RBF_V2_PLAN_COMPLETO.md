# RBF v2 — Plan Completo: Implementación, Calibración y Expectativas

**Fecha:** 2026-06-04
**Versión anterior:** RBF v1 (detector base: rango + CVD + VR)
**Esta versión:** RBF v2 (sistema de confluencia por capas de order flow)
**Estado al escribir este doc:** RBF v1 deployado en Railway, 4 señales live (día 1), pendiente migración DB v3

---

## 1. Qué es RBF v2 y por qué existe

RBF v1 pregunta: *¿hay breakout con CVD alineado y volumen?*
RBF v2 pregunta: *¿cuántas capas independientes del mercado confirman que este breakout es real?*

La diferencia no es cosmética. El único stop del día 1 (Long 10:11, régimen Bear) pasó todos los filtros de v1 sin problema. En v2, ese trade hubiera tenido score 0 de 7 y no habría disparado. La mejora central es pasar de 3 filtros en serie a un sistema de evidencia acumulada donde múltiples fuentes de order flow votan juntas.

### Problema central que resuelve

| Problema en v1 | Cómo lo resuelve v2 |
|---|---|
| CVD total puede ser un spike de 1 barra | CVD slope OLS verifica presión sostenida barra a barra |
| No verifica el libro en el momento del breakout | OBI L5 confirma que el libro ya empuja en la misma dirección |
| No detecta acumulación silenciosa dentro del rango | Absorción footprint y stacked imbalance detectan distribución institucional |
| No verifica si el target es alcanzable | LVN + thin zone confirman camino libre; HVN bloquea la señal |
| Long contra Bear con cualquier CVD positivo dispara | Long en Bear requiere score ≥ 5 (máxima confluencia) |
| Todas las señales valen igual | Score determina tamaño de posición (1×, 1.5×, 2×) |
| Sin datos de qué combinación funciona mejor | `confluence_score` + `confluence_flags` grabados en cada señal |

---

## 2. Arquitectura v2 — las tres capas

### Capa 0: Requisitos duros (sin cambio respecto a v1)

Los tres requisitos originales permanecen. Sin ellos no hay señal, independientemente del score.

- Rango válido: 0.08%–0.55% del precio, 15–60 barras M1
- CVD alineado: `cvd_in_range < 0` para SHORT · `cvd_in_range > 0` para LONG
- Breakout confirmado: cierre fuera del rango con VR ≥ 2×

### Capa 1: Sistema de puntos de confluencia (nuevo)

Cada condición que se cumple suma +1 punto. La señal dispara si `confluence_score ≥ min_confluence_score` (configurable en `strategy.toml`, valor inicial: 1 para shadow mode, después 3).

| Flag | Condición SHORT | Condición LONG | Fuente en código |
|---|---|---|---|
| CVD slope sostenido | `cvd_slope < −15` | `cvd_slope > +15` | `cvd_slope` en scalping_bars |
| OBI L5 alineado | `obi_l5 < −0.15` | `obi_l5 > +0.15` | `obi_l5` en OrderBookContext |
| Stacked imbalance | `stacked_imbalance = true` (últimas 3 barras del rango) | idem | `stacked_imbalance` de aggTrades |
| Absorción footprint | `absorption_short = true` (últimas 5 barras del rango) | `absorption_long = true` | `footprint_absorption` |
| LVN / thin zone | LVN dentro de 0.5× ATR del nivel roto O `thin_zone_below = true` | idem | `lvn_nearby` + `thin_zone_above` |
| VWAP bias | `price_vs_vwap = Below` | `price_vs_vwap = Above` | `VwapContext` |
| OI momentum | `oi_momentum_aligned = true` (precio↓ + OI↑) | idem | `InstitutionalContext` |

### Capa 2: Vetos duros (nuevo)

Cancelan la señal independientemente del score. Sin excepciones.

| Veto | Condición | Razón |
|---|---|---|
| Wall en dirección del target | `ask_wall_nearby = true` (LONG) · `bid_wall_nearby = true` (SHORT) | El target no es alcanzable si hay pared a 1× ATR |
| HVN antes del target | HVN dentro del 50% del camino entry→target | El nodo va a frenar el movimiento antes de llegar |
| VPIN tóxico | `vpin > 0.65` | Flujo tóxico activo, selección adversa garantizada |
| Long contra Bear sin máxima confluencia | `direction = Long` + `macro_regime = Bear` + `score < 5` | El backtest confirmó que es el peor setup. Requiere 5/7 fuentes confirmando |

### Capa 3: Sizing por convicción (nuevo)

Una vez que la señal dispara, el tamaño es proporcional al score.

| Score | Nivel | Multiplicador de posición |
|---|---|---|
| 0–2 | No dispara | — |
| 3 | Convicción base | 1× (tamaño estándar) |
| 4 | Alta convicción | 1.5× |
| 5–7 | Máxima convicción | 2× |

*Nota: el sizing diferencial se activa solo cuando el análisis de datos confirme gradiente score→avg_r. En shadow mode, todas las señales son 1×.*

---

## 3. Lógica de cada flag — por qué funciona (o debería)

### CVD slope — lógica sólida ✓

Presión sostenida barra a barra significa que en cada minuto del rango los vendedores ganaron el intercambio de órdenes. No es un evento aislado. Si el precio no cayó pese a esa presión, algo lo estaba absorbiendo. Cuando ese algo se agote, el movimiento es brusco. Causalidad clara, verificable.

**Riesgo de solapamiento:** puede correlacionar con stacked imbalance (ambos miden presión direccional en M1). Verificar con datos si son redundantes.

### OBI L5 — lógica académicamente validada ✓

Los market makers en los primeros 5 niveles del libro están posicionados hacia la dirección del breakout. OBI bajista en SHORT = los proveedores de liquidez están vendiendo, no comprando. Validado en Cont-Kukanov-Stoikov 2014 (R² ~65% en equities), adaptado a crypto en Silantyev 2019.

**Condición de validez:** spread < 2 bps. Con libro thin el OBI es ruidoso y puede ser extremo sin significar nada.

### Stacked imbalance — lógica válida, verificar solapamiento ⚠

3 barras M1 consecutivas con mismo signo de delta = acumulación real, no ruido. Dentro de un rango es la firma de posicionamiento institucional silencioso. Válido.

**Riesgo:** si cvd_slope es negativo sostenido, casi garantizado que haya stacked imbalance. Pueden medir lo mismo. La query de correlación entre flags lo resolverá con datos.

### Absorción footprint — señal de mayor calidad ✓

Delta negativo en zona VAH con CVD slope opuesto = alguien grande está vendiendo en cada barra alcista sin dejar caer el precio. Wyckoff Phase B/C cuantificado. Cuando la demanda que ese vendedor absorbía se agota, el precio cae sin soporte. Causalidad institucional bien documentada.

**Condición de validez:** requiere 2+ toques del extremo con absorción creciente. Un solo toque puede ser ruido.

### LVN / thin zone — lógica sólida ✓

Un LVN es una zona donde históricamente se negoció poco volumen. No hay gente con posición allí que defienda su precio. El precio se mueve rápido a través de LVNs porque no hay contrapartes naturales. La thin zone del libro confirma lo mismo en tiempo real.

**Veto asociado:** HVN antes del target es el caso opuesto y es más importante bloquearlo que sumar puntos por LVN.

### VWAP bias — lógica sólida ✓

El VWAP de sesión es el precio de referencia de los algoritmos institucionales. Un breakout bajista cuando el precio ya está bajo VWAP tiene doble confirmación: el precio ya falló en mantenerse sobre el benchmark y ahora rompe el rango. Los institucionales que deben vender al VWAP o mejor no tienen urgencia de comprar.

**Condición de validez:** VWAP debe tener ≥ 60 min de datos y no haber sido cruzado más de 4 veces en la sesión. Si el precio oscila alrededor del VWAP constantemente, el VWAP ya no es un nivel relevante.

### OI momentum — lógica válida pero lag alto ⚠

Precio↓ + OI↑ = shorts frescos entrando, no longs cerrando. Es posición nueva con convicción. La lógica es correcta.

**Problema real:** el polling es cada 5 minutos. El breakout ocurre en 60 segundos. El OI que lees puede ser de hace 4 minutos. Es demasiado lento para confirmar un evento M1. Usar como contexto de fondo (¿el OI de la sesión apoya la dirección?) más que como confirmador del breakout específico.

**Decisión pendiente:** si el análisis de datos muestra que este flag no diferencia winners de losers, eliminarlo del score y dejarlo solo como campo informativo.

---

## 4. Implementación — cambios exactos en el código

### 4.1 `data/src/strategy/detectors/range_breakout_flow.rs`

Añadir la función `score_confluence()` que recibe el `StrategyMarketContext` completo y devuelve `(score: u8, flags: Vec<ConfluenceFlag>, veto: Option<VetoReason>)`.

```rust
#[derive(Debug, Clone)]
pub enum ConfluenceFlag {
    CvdSlopeSostenido,
    ObiAlineado,
    StackedImbalance,
    AbsorcionFootprint,
    LvnOThinZone,
    VwapBias,
    OiMomentum,
}

#[derive(Debug, Clone)]
pub enum VetoReason {
    WallEnDireccionTarget,
    HvnAntesDelTarget,
    VpinToxico,
    LongContraBearSinMaxConfluencia,
}

pub fn score_confluence(
    ctx: &StrategyMarketContext,
    direction: Side,
) -> (u8, Vec<ConfluenceFlag>, Option<VetoReason>) {
    // --- VETOS primero (cancelan todo) ---
    if direction == Side::Long {
        if let Some(ob) = &ctx.order_book {
            if ob.ask_wall_nearby { return (0, vec![], Some(VetoReason::WallEnDireccionTarget)); }
        }
    }
    if direction == Side::Short {
        if let Some(ob) = &ctx.order_book {
            if ob.bid_wall_nearby { return (0, vec![], Some(VetoReason::WallEnDireccionTarget)); }
        }
    }
    if let Some(vp) = &ctx.volume_profile {
        if hvn_between_entry_and_target(vp, ctx, direction) {
            return (0, vec![], Some(VetoReason::HvnAntesDelTarget));
        }
    }
    if let Some(of) = &ctx.order_flow {
        if of.vpin.unwrap_or(0.0) > 0.65 {
            return (0, vec![], Some(VetoReason::VpinToxico));
        }
    }

    // --- PUNTOS ---
    let mut score: u8 = 0;
    let mut flags: Vec<ConfluenceFlag> = vec![];

    // CVD slope
    if let Some(of) = &ctx.order_flow {
        let slope = of.cvd_slope.unwrap_or(0.0);
        let alineado = match direction {
            Side::Short => slope < -15.0,
            Side::Long  => slope > 15.0,
        };
        if alineado { score += 1; flags.push(ConfluenceFlag::CvdSlopeSostenido); }
    }

    // OBI L5
    if let Some(ob) = &ctx.order_book {
        let alineado = match direction {
            Side::Short => ob.obi_l5 < -0.15,
            Side::Long  => ob.obi_l5 > 0.15,
        };
        if alineado { score += 1; flags.push(ConfluenceFlag::ObiAlineado); }
    }

    // Stacked imbalance
    if let Some(of) = &ctx.order_flow {
        if of.stacked_imbalance { score += 1; flags.push(ConfluenceFlag::StackedImbalance); }
    }

    // Absorción footprint
    if let Some(of) = &ctx.order_flow {
        let absorcion = match direction {
            Side::Short => of.absorption_short,
            Side::Long  => of.absorption_long,
        };
        if absorcion { score += 1; flags.push(ConfluenceFlag::AbsorcionFootprint); }
    }

    // LVN / thin zone
    if let Some(vp) = &ctx.volume_profile {
        let lvn_ok = lvn_near_breakout_level(vp, ctx);
        if lvn_ok { score += 1; flags.push(ConfluenceFlag::LvnOThinZone); }
    }
    if let Some(ob) = &ctx.order_book {
        let thin = match direction {
            Side::Short => ob.thin_zone_below,
            Side::Long  => ob.thin_zone_above,
        };
        if thin && !flags.contains(&ConfluenceFlag::LvnOThinZone) {
            score += 1; flags.push(ConfluenceFlag::LvnOThinZone);
        }
    }

    // VWAP bias
    if let Some(vwap) = &ctx.vwap {
        let alineado = match direction {
            Side::Short => vwap.price_vs_vwap == PriceVsVwap::Below,
            Side::Long  => vwap.price_vs_vwap == PriceVsVwap::Above,
        };
        if alineado { score += 1; flags.push(ConfluenceFlag::VwapBias); }
    }

    // OI momentum
    if let Some(inst) = &ctx.institutional {
        if inst.oi.momentum_aligned { score += 1; flags.push(ConfluenceFlag::OiMomentum); }
    }

    // Veto especial: Long contra Bear sin max confluencia
    if direction == Side::Long
        && ctx.regime == Regime::Bear
        && score < 5
    {
        return (0, flags, Some(VetoReason::LongContraBearSinMaxConfluencia));
    }

    (score, flags, None)
}
```

En `detect()`, después de validar los requisitos duros, llamar `score_confluence()` y:
- Si hay `veto` → `return None` (con log del motivo)
- Si `score < min_confluence_score` → `return None`
- Si dispara → incluir `score` y `flags` en el `RbfSignal`

### 4.2 `config/strategy.toml` — sección `[range_breakout]`

```toml
[range_breakout]
# ... parámetros existentes ...

# v2 — sistema de confluencia
min_confluence_score = 1        # SHADOW MODE: 1 para grabar todo
                                # PRODUCCIÓN: subir a 3 cuando haya 50+ señales analizadas
cvd_slope_threshold = 15.0      # |cvd_slope| mínimo para el flag (normalizado por ATR)
obi_threshold = 0.15            # |obi_l5| mínimo para el flag
bear_long_min_score = 5         # score mínimo para Long en régimen Bear
```

### 4.3 `supabase/migration_rbf_v3.sql` — 4 campos nuevos

```sql
ALTER TABLE rbf_signals
  ADD COLUMN IF NOT EXISTS confluence_score  smallint,
  ADD COLUMN IF NOT EXISTS confluence_flags  text[],
  ADD COLUMN IF NOT EXISTS obi_at_breakout   float4,
  ADD COLUMN IF NOT EXISTS veto_reason       text;

-- Índice para análisis de score vs outcome
CREATE INDEX IF NOT EXISTS idx_rbf_confluence_score
  ON rbf_signals (confluence_score)
  WHERE exit_reason IS NOT NULL;

-- Vista actualizada
CREATE OR REPLACE VIEW v_rbf_summary AS
SELECT
  session,
  direction,
  macro_regime,
  confluence_score,
  COUNT(*)                                        AS total,
  COUNT(*) FILTER (WHERE result_r >= 1.0)         AS wins,
  COUNT(*) FILTER (WHERE result_r < 0)            AS losses,
  ROUND(AVG(result_r)::NUMERIC, 3)                AS avg_r,
  ROUND(
    COUNT(*) FILTER (WHERE result_r >= 1.0)::numeric
    / NULLIF(COUNT(*), 0) * 100, 1
  )                                               AS win_pct
FROM rbf_signals
WHERE exit_reason IS NOT NULL
GROUP BY session, direction, macro_regime, confluence_score
ORDER BY session, confluence_score DESC;
```

### 4.4 `crates/monitor/src/supabase_writer.rs` — `write_rbf_signal()`

Añadir los 4 campos nuevos al JSON de inserción:

```rust
"confluence_score": signal.confluence_score,
"confluence_flags": signal.confluence_flags,   // Vec<String>
"obi_at_breakout":  signal.obi_at_breakout,
"veto_reason":      signal.veto_reason,        // Option<String>
```

### 4.5 `data/src/strategy/detectors/range_breakout_flow.rs` — struct `RbfSignal`

```rust
pub struct RbfSignal {
    // ... campos existentes ...
    pub confluence_score: u8,
    pub confluence_flags: Vec<String>,   // serialización de ConfluenceFlag
    pub obi_at_breakout:  Option<f32>,
    pub veto_reason:      Option<String>,
}
```

### 4.6 Orden de ejecución del deploy

1. Correr `supabase/migration_rbf_v3.sql` en Supabase SQL Editor
2. Verificar que la vista `v_rbf_summary` se creó correctamente
3. `git add -A && git commit -m "feat: RBF v2 confluence scoring system"`
4. `git push` → Railway build automático
5. Verificar en Railway logs que las señales incluyen `confluence_score` en el JSON
6. Verificar en Supabase que los campos se están llenando

---

## 5. Plan de calibración — cómo saber si los umbrales son correctos

### El principio fundamental

Los umbrales actuales (cvd_slope < −15, obi_l5 < −0.15, score ≥ 3) son hipótesis razonadas, no datos. La única forma de saber si son correctos es registrar TODAS las señales con su score real y su outcome real, y luego mirar si el umbral estuvo bien puesto.

**Por eso el shadow mode con `min_confluence_score = 1` es obligatorio.** Si despliegas con score ≥ 3 desde el día 1, nunca sabrás cómo le fue a las señales con score 1 o 2 y no podrás calibrar el umbral.

### Fase 1 — Shadow mode (semanas 1–3)

**Objetivo:** acumular 50+ señales cerradas con `confluence_score` y `result_r` registrados.

Configuración:
- `min_confluence_score = 1` → dispara toda señal que pase los requisitos duros
- Paper trading (sin dinero real)
- Grabar `confluence_score`, `confluence_flags`, `obi_at_breakout`, `veto_reason` en cada señal

Lo que NO hacer durante esta fase:
- No ajustar umbrales basándose en las primeras 10-20 señales
- No subir `min_confluence_score` hasta tener el análisis de fase 2

### Fase 2 — Primera calibración (con 50+ señales cerradas)

**Query 1: ¿CVD slope realmente predice mejor outcome?**

```sql
SELECT
  CASE
    WHEN cvd_slope < -15  THEN 'slope_fuerte'
    WHEN cvd_slope < 0    THEN 'slope_debil'
    ELSE                       'slope_positivo'
  END AS grupo,
  COUNT(*) AS n,
  ROUND(AVG(result_r)::numeric, 3) AS avg_r,
  COUNT(*) FILTER (WHERE result_r >= 1.0) * 100
    / NULLIF(COUNT(*), 0) AS win_pct
FROM rbf_signals
WHERE exit_reason IS NOT NULL AND direction = 'Short'
GROUP BY 1 ORDER BY avg_r DESC;
```

Resultado esperado si el flag es válido: `avg_r(slope_fuerte) > avg_r(slope_debil)`.
Si no hay diferencia → el umbral −15 no aporta nada, recalibrar o eliminar.

**Query 2: ¿OBI importa?**

```sql
SELECT
  CASE
    WHEN obi_at_breakout < -0.15 THEN 'obi_bajista'
    WHEN obi_at_breakout < 0.0   THEN 'obi_neutro'
    ELSE                              'obi_alcista'
  END AS grupo,
  COUNT(*) AS n,
  ROUND(AVG(result_r)::numeric, 3) AS avg_r
FROM rbf_signals
WHERE exit_reason IS NOT NULL
GROUP BY 1 ORDER BY avg_r DESC;
```

**Query 3: ¿El score total predice el outcome? — la prueba maestra**

```sql
SELECT
  confluence_score,
  COUNT(*) AS n,
  ROUND(AVG(result_r)::numeric, 3) AS avg_r,
  COUNT(*) FILTER (WHERE result_r >= 1.0) * 100
    / NULLIF(COUNT(*), 0) AS win_pct
FROM rbf_signals
WHERE exit_reason IS NOT NULL AND confluence_score IS NOT NULL
GROUP BY 1 ORDER BY 1;
```

Lo que buscas ver: avg_r sube monótonamente con el score. Score 1 → negativo. Score 4 → positivo claro. Si no hay gradiente, el sistema de puntos no funciona y hay que replantear qué se está midiendo.

**Query 4: ¿Qué combinación de flags predice mejor?**

```sql
SELECT
  confluence_flags,
  COUNT(*) AS n,
  ROUND(AVG(result_r)::numeric, 3) AS avg_r
FROM rbf_signals
WHERE exit_reason IS NOT NULL
  AND array_length(confluence_flags, 1) >= 2
GROUP BY 1
HAVING COUNT(*) >= 5
ORDER BY avg_r DESC
LIMIT 15;
```

**Query 5: ¿Los vetos bloquearon correctamente?**

```sql
-- Reconstruir retroactivamente señales que habrían disparado en v1
-- pero fueron vetadas en v2, y ver cómo les hubiera ido
SELECT
  veto_reason,
  COUNT(*) AS n_vetadas,
  -- outcome hipotético: si hubieran sido señales normales de v1
  -- no lo podemos saber exactamente, pero podemos ver el contexto
  ROUND(AVG(range_pct)::numeric, 3) AS avg_range_pct,
  ROUND(AVG(vr_at_breakout)::numeric, 2) AS avg_vr
FROM rbf_signals
WHERE veto_reason IS NOT NULL
GROUP BY 1;
```

### Fase 3 — Fijación de umbrales reales (con 100+ señales)

**Test de overfitting obligatorio:** dividir las señales 70/30 temporalmente.
- Primeras 70 señales: encontrar umbrales óptimos
- Últimas 30 señales: verificar que esos umbrales funcionan en datos no vistos

**Regla de robustez:** si cambiar el umbral ±30% no cambia materialmente el avg_r, el umbral es robusto. Si solo funciona exactamente en el valor calibrado ±10%, es overfitting.

Con este análisis:
1. Fijar `min_confluence_score` real en el `strategy.toml`
2. Decidir qué flags eliminar (OI momentum si el lag lo invalida, stacked imbalance si es redundante con cvd_slope)
3. Activar sizing diferencial (1×, 1.5×, 2×) si el gradiente score→avg_r es robusto

### Fase 4 — Validación de vetos (con 200+ señales)

Con 200+ señales, cruzar señales vetadas contra el contexto para ver si los vetos bloquearon principalmente losers o también winners.

Si "wall en dirección target" bloqueó principalmente señales que habrían perdido → veto válido.
Si bloqueó winners → el umbral del veto es demasiado agresivo, recalibrar.

---

## 6. Queries de monitoreo diario

Después de cada sesión, estas queries dan una lectura rápida del estado:

```sql
-- Estado del día actual
SELECT
  timestamp_ms,
  direction,
  session,
  macro_regime,
  confluence_score,
  confluence_flags,
  vr_at_breakout,
  result_r,
  exit_reason,
  veto_reason
FROM rbf_signals
WHERE timestamp_ms > extract(epoch from now()-interval '24 hours')*1000
ORDER BY timestamp_ms DESC;

-- Distribución de scores (¿está generando señales de alta convicción?)
SELECT
  confluence_score,
  COUNT(*) AS total,
  COUNT(*) FILTER (WHERE exit_reason IS NOT NULL) AS cerradas,
  ROUND(AVG(result_r) FILTER (WHERE exit_reason IS NOT NULL)::numeric, 3) AS avg_r
FROM rbf_signals
WHERE timestamp_ms > extract(epoch from now()-interval '7 days')*1000
GROUP BY 1 ORDER BY 1;

-- Flags más frecuentes esta semana
SELECT
  UNNEST(confluence_flags) AS flag,
  COUNT(*) AS apariciones,
  ROUND(AVG(result_r) FILTER (WHERE exit_reason IS NOT NULL)::numeric, 3) AS avg_r_cuando_presente
FROM rbf_signals
WHERE timestamp_ms > extract(epoch from now()-interval '7 days')*1000
GROUP BY 1 ORDER BY apariciones DESC;

-- Vetos de la semana
SELECT veto_reason, COUNT(*) AS n
FROM rbf_signals
WHERE veto_reason IS NOT NULL
  AND timestamp_ms > extract(epoch from now()-interval '7 days')*1000
GROUP BY 1 ORDER BY n DESC;
```

---

## 7. Lo que esperamos de v2 — expectativas honestas

### Qué debería mejorar

**Reducción de fakeouts.** El Long 10:11 (el único stop de día 1) no habría disparado. Los breakouts con CVD correcto pero sin soporte de OBI, sin absorción visible y con sesgo VWAP en contra tienen score bajo y no disparan o disparan con tamaño mínimo.

**Calidad sobre cantidad.** RBF v1 generó ~30 señales en el backtest de 30 días. v2 en shadow mode generará el mismo número (grabando todo), pero cuando suba el umbral a score ≥ 3, probablemente baje a 10–15 señales al mes. Eso es intencionado.

**Información accionable.** Con `confluence_score` y `confluence_flags` grabados en cada señal, en 4 semanas sabrás qué combinación de order flow predice mejor el outcome en BTC específicamente. Eso no existe publicado para este activo y estas condiciones — lo estás construyendo desde datos reales.

**Sizing dinámico.** Cuando el análisis confirme el gradiente, los trades de 5+ puntos se pueden hacer el doble de grandes. En lugar de arriesgar 1% en todo trade, arriesgas 1% en los normales y 2% cuando 5 fuentes independientes están de acuerdo.

### Qué NO debería esperarse

**No va a tener win rate del 70%.** El backtest de v1 mostró 13–31% dependiendo del setup. v2 filtra los peores setups, lo cual debería subir el win rate del subconjunto que dispara, pero no esperar un salto a 60%+ sin datos que lo confirmen.

**No elimina todos los stops.** Los vetos bloquean fakeouts estructuralmente identificables. Los fakeouts de noticias macro (CPI, NFP) donde el VR es extremo pero el movimiento se revierte son difíciles de filtrar sin un feed de calendario económico.

**El shadow mode no genera PnL real.** Las primeras 3–6 semanas son de acumulación de datos, no de ganancias. Es la inversión en información necesaria para que el sistema funcione correctamente después.

### Milestones con criterios de éxito claros

| Señales cerradas | Qué analizar | Decisión a tomar |
|---|---|---|
| 30 | Primera lectura de distribución de scores | ¿El sistema genera scores ≥ 3 con frecuencia suficiente? |
| 50 | Queries 1–4 de calibración | ¿Hay gradiente score→avg_r? ¿Qué flags correlacionan con outcomes? |
| 70/30 split | Test de overfitting | ¿Los umbrales encontrados en las 70 primeras funcionan en las 30 últimas? |
| 100 | Fijación de `min_confluence_score` real | Activar umbral calibrado. Considerar sizing diferencial. |
| 200 | Validación de vetos | ¿Los vetos bloquearon principalmente losers? |
| 200+ win rate > 55% sostenido | Decisión de escalar | Aumentar tamaño base de posición |

### Señal de alerta temprana — cuándo replantear

Si con 50 señales el análisis muestra que avg_r es igual o peor en score alto vs score bajo, el sistema de puntos no funciona como filtro y hay que replantear desde la selección de flags. No ajustar parámetros en ese caso — replantar la hipótesis.

Si los vetos están bloqueando más del 40% de las señales que pasarían en v1, algún veto está siendo demasiado agresivo. Verificar cuál con los datos y relajar su condición.

---

## 8. Archivos modificados — resumen

| Archivo | Cambio |
|---|---|
| `data/src/strategy/detectors/range_breakout_flow.rs` | Añadir `score_confluence()`, enum `ConfluenceFlag`, enum `VetoReason`, campos en `RbfSignal` |
| `config/strategy.toml` | Añadir `min_confluence_score`, `cvd_slope_threshold`, `obi_threshold`, `bear_long_min_score` |
| `supabase/migration_rbf_v3.sql` | ADD COLUMN `confluence_score`, `confluence_flags`, `obi_at_breakout`, `veto_reason` + vista actualizada |
| `crates/monitor/src/supabase_writer.rs` | Añadir 4 campos al JSON de `write_rbf_signal()` |
| `data/src/strategy/config_file.rs` | Parsear los 4 campos nuevos de `[range_breakout]` en `RangeBreakoutSection` |

---

## 9. Checklist de deploy — en orden

- [ ] Correr `supabase/migration_rbf_v3.sql` en Supabase SQL Editor
- [ ] Verificar vista `v_rbf_summary` se creó con `confluence_score` en GROUP BY
- [ ] Implementar `score_confluence()` en `range_breakout_flow.rs`
- [ ] Añadir enums `ConfluenceFlag` y `VetoReason`
- [ ] Añadir campos a `RbfSignal` struct
- [ ] Actualizar `config_file.rs` para parsear nuevos campos de toml
- [ ] Actualizar `write_rbf_signal()` en `supabase_writer.rs`
- [ ] Actualizar `strategy.toml` con `min_confluence_score = 1` (shadow mode)
- [ ] `cargo test --workspace` → verde
- [ ] `git push` → Railway build verde
- [ ] Verificar primera señal en Supabase tiene `confluence_score` y `confluence_flags` no nulos
- [ ] Verificar que señales vetadas tienen `veto_reason` y `result_r = null`

---

*Documento generado en sesión 2026-06-04. Próxima revisión: cuando haya 50+ señales cerradas con `confluence_score` registrado.*
