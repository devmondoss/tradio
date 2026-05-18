# Fix 03 — LiqMapTracker: validación antes de usar como target

> El LiqMapTracker estima dónde están los stops acumulados que aún no fueron
> activados. Su precisión depende directamente de la calidad de datos OI por
> nivel de precio del exchange. Antes de que LiqHunt y SMD dependan de él
> para colocar targets, hay que validar su precisión con datos reales.

---

## Qué hace el LiqMapTracker (recap)

```
Input:  OI por nivel de precio + leverage promedio estimado + precio actual
Output: liq_density_above  → concentración de stops LONG por encima del precio
        liq_density_below  → concentración de stops SHORT por debajo del precio
        liq_target_above   → precio donde hay máxima densidad sobre el precio
        liq_target_below   → precio donde hay máxima densidad bajo el precio
```

El precio es atraído hacia estas zonas porque los institucionales saben dónde
están los stops y los barren para obtener liquidez antes de revertir.

---

## El problema: márgenes de error no conocidos

Los datos de OI por nivel de precio tienen 3 fuentes de error:

```
1. Resolución del exchange
   Binance y Bybit reportan OI en buckets de precio (no tick a tick)
   → la densidad real puede estar ±0.5% del nivel reportado

2. Leverage promedio estimado
   El leverage real de cada posición no es público
   → se estima con leverage promedio del exchange (sesgado hacia retail)
   → las posiciones de institucionales pueden tener leverage diferente

3. Stops vs liquidaciones
   No todo OI concentrado = stops en ese nivel
   Algunos son posiciones de largo plazo sin stop cercano
   → puede haber sobreestimación de la "imán" en ciertos niveles
```

---

## Protocolo de validación en 3 etapas

### Etapa 1: modo observación pasiva (2 semanas)

El LiqMapTracker corre y genera `liq_target_above` / `liq_target_below`
pero **no se usa para colocar targets en ningún trade**.

En cambio, se logea junto al resultado real del precio:

```json
{
  "event": "liq_map_prediction",
  "bar_index": 1042,
  "price_at_prediction": 76950.0,
  "liq_target_above": 77340.0,
  "liq_density_above": 0.78,
  "liq_target_below": 76540.0,
  "liq_density_below": 0.45,
  "price_5_bars_later":  77290.0,
  "price_10_bars_later": 77380.0,
  "price_20_bars_later": 77100.0,
  "target_hit_above_5":  false,
  "target_hit_above_10": true,
  "target_hit_above_20": true,
  "miss_pct": 0.22        ← distancia entre liq_target y precio máximo alcanzado
}
```

### Etapa 2: análisis de precisión

Con al menos **50 predicciones** logeadas, calcular:

```sql
-- Precisión del liq_target en diferentes horizontes
SELECT
  AVG(CASE WHEN target_hit_above_10 THEN 1.0 ELSE 0.0 END) as hit_rate_10_bars,
  AVG(CASE WHEN target_hit_above_20 THEN 1.0 ELSE 0.0 END) as hit_rate_20_bars,
  AVG(miss_pct) as avg_miss_pct,
  PERCENTILE(miss_pct, 0.80) as p80_miss_pct
FROM liq_map_predictions
WHERE liq_density_above > 0.60;  -- solo densidades altas

-- Precisión por rango de densidad
SELECT
  CASE
    WHEN liq_density_above >= 0.75 THEN 'high'
    WHEN liq_density_above >= 0.50 THEN 'medium'
    ELSE 'low'
  END as density_tier,
  AVG(CASE WHEN target_hit_above_10 THEN 1.0 ELSE 0.0 END) as hit_rate,
  COUNT(*) as n
FROM liq_map_predictions
GROUP BY density_tier;
```

### Criterios de aprobación para usar en targets

| Métrica | Umbral mínimo | Acción si no se cumple |
|---|---|---|
| `hit_rate_10_bars` con `density >= 0.75` | >= 0.55 | Ajustar resolución de buckets |
| `avg_miss_pct` | <= 0.50% | Agregar margen de tolerancia al target |
| n de predicciones | >= 50 | Continuar observando |
| Correlación densidad → hit_rate | Positiva | Si no correlaciona, revisar fuente OI |

---

## Integración gradual en estrategias

### Fase A: solo como contexto (no target)

```rust
// En LiqHunt y SMD — Fase A
// liq_map disponible como información pero no define el target
pub struct SignalContext {
    pub liq_density_in_direction: f64,  // informativo
    pub liq_target: Option<f64>,        // calculado pero no usado
    pub target: f64,                    // ← sigue siendo VP/OB/FVG
}
```

Agregar al scoring como **bonus leve** cuando la densidad confirma la dirección:

```rust
// Si liq_density en dirección del trade > 0.70 → score bonus pequeño
let liq_confirmation = if ctx.liq_density_in_direction > 0.70 { 0.05 } else { 0.0 };
score += liq_confirmation;
```

### Fase B: como target alternativo (si aprobó validación)

```rust
// TargetSelector — Fase B
// liq_target compite con OB/FVG/HVN como candidato a target

let target = TargetSelector::select(
    entry, &side, &vp, ob, fvg,
    Some(&liq_map),  // ← ahora participa en la selección
    atr
);
```

La prioridad en `TargetSelector`:
```
1. liq_target (si densidad >= 0.75 Y validación aprobó)
2. HVN del VP
3. OB activo
4. FVG unfilled
5. VAH / VAL
```

### Fase C: target principal para LiqHunt

Una vez validado con >= 100 predicciones y hit_rate >= 0.60:

```rust
// LiqHunt específicamente — el target ES la zona de liquidez
// porque la estrategia está diseñada para capturar ese move
pub fn select_liqhunt_target(
    liq_map: &LiqMapData,
    side: &Side,
) -> Option<f64> {
    let (density, target) = match side {
        Side::Long  => (liq_map.density_above, liq_map.target_above),
        Side::Short => (liq_map.density_below, liq_map.target_below),
    };

    // Solo usar si la densidad es alta
    if density >= 0.70 {
        Some(target)
    } else {
        None  // fallback a VP/OB
    }
}
```

---

## Tolerancia de target — ajuste por `avg_miss_pct`

Si la validación muestra `avg_miss_pct = 0.30%`, agregar ese margen al target:

```rust
let raw_target = liq_map.target_above;
let tolerance  = raw_target * 0.003;  // 0.30% de margen

// Para longs: target un poco antes (más conservador)
let adjusted_target = raw_target - tolerance;
```

Esto evita que el precio llegue a 0.25% del target y revierta sin que el trade cierre.

---

## Archivos a modificar / crear

```
data/src/institutional/
└── liq_map_tracker.rs      ← agregar campos de validación al output

src/paper/
└── outcome_tracker.rs      ← agregar liq_map_prediction logging

src/strategy/
└── target_selector.rs      ← integrar liq_map en Fase B/C
```

---

## Output completo del LiqMapTracker después del fix

```rust
pub struct LiqMapData {
    // Datos principales
    pub density_above: f64,        // 0.0 → 1.0
    pub density_below: f64,
    pub target_above: f64,         // precio de máxima densidad sobre precio actual
    pub target_below: f64,

    // Metadatos de confianza — NUEVOS
    pub confidence: LiqMapConfidence,
    pub data_source: LiqMapSource,
    pub bucket_resolution_pct: f64, // granularidad de los datos del exchange
    pub last_updated_bar: u64,
}

pub enum LiqMapConfidence {
    High,    // density >= 0.75 + validación aprobada
    Medium,  // density 0.50–0.75
    Low,     // density < 0.50 — solo informativo
}

pub enum LiqMapSource {
    BinanceOI,
    BybitOI,
    Estimated,  // calculado sin datos directos
}
```

---

## Orden de implementación

| Paso | Acción |
|---|---|
| 1 | Agregar logging de `liq_map_prediction` al OutcomeTracker |
| 2 | Agregar campos `price_N_bars_later` y `target_hit_*` al log |
| 3 | Correr 2 semanas en observación pasiva |
| 4 | Correr queries de validación con >= 50 predicciones |
| 5 | Si aprueba: agregar como bonus en scoring (Fase A) |
| 6 | Si aprueba con >= 100 predicciones: integrar en TargetSelector (Fase B) |
| 7 | Para LiqHunt específicamente: Fase C cuando hit_rate >= 0.60 |
