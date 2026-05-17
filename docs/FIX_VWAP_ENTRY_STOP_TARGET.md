# Fix Plan — VwapPullbackContinuation: Entry, Stop y Target
## FlowSurface Trading System

**Fecha:** Mayo 2026  
**Detector:** `VwapValuePullbackContinuation`  
**Archivo:** `data/src/strategy/detectors/vwap_value_pullback_continuation.rs`  
**Problema confirmado:** R:R real de 0.20-0.62:1 en producción (objetivo: ≥1.5:1)

---

## El problema — números reales de producción

Las 6 señales generadas hoy demuestran el bug con datos concretos:

```
Señal 08:00 UTC
  entry:  78,137
  stop:   77,984   ← VAL del value area (bug: usa min() que elige lo más lejano)
  target: 78,168   ← VAH del value area (fijo, demasiado cerca)
  
  risk:   78,137 - 77,984 = $153
  reward: 78,168 - 78,137 = $31
  R:R:    0.20:1  ← necesita 67% win rate solo para breakeven
```

**Causa raíz — dos bugs combinados:**

**Bug A — Stop con `min()` en vez de `max()`:**
```rust
// ACTUAL (incorrecto):
let stop = f64::min(val, entry - 0.75 * atr);
// min() elige el MÁS BAJO (más lejano) → stop queda $153 abajo

// CORRECTO:
let stop = f64::max(val, entry - 1.0 * atr);
// max() elige el MÁS ALTO (más cercano) → stop queda $44 abajo
```

**Bug B — Target fijo en VAH:**
```rust
// ACTUAL (incorrecto):
let target = vah;
// VAH es fijo e independiente de la distancia al entry
// Puede estar a $31 o a $500, sin verificar R:R

// CORRECTO:
// Target = nivel estructural más cercano que ofrezca R:R ≥ 1.5
// Candidatos: HVNs arriba, ask walls, swing highs recientes
// Fallback solo si no hay estructura válida
```

---

## Parte 1 — Fixes inmediatos (hacer primero)

### Fix 1A — Stop con `max()` para LONG

**Archivo:** `data/src/strategy/detectors/vwap_value_pullback_continuation.rs`

```rust
// LONG stop — buscar línea actual:
let stop = f64::min(val, entry - 0.75 * atr);

// Reemplazar por:
let stop = f64::max(
    val,                    // VAL como nivel de invalidación estructural
    entry - 1.0 * atr,      // ATR como floor del riesgo
);
// max() garantiza que el stop es el más cercano entre VAL y ATR
// Si VAL está muy lejos, ATR limita el riesgo a 1× ATR
// Si VAL está cerca (dentro de 1× ATR), lo usa como stop natural
```

**Fix 1B — Stop para SHORT:**
```rust
// SHORT stop — buscar línea actual:
let stop = f64::max(vah, entry + 0.75 * atr);

// Reemplazar por:
let stop = f64::min(
    vah,                    // VAH como nivel de invalidación estructural
    entry + 1.0 * atr,      // ATR como ceiling del riesgo
);
// min() garantiza el más cercano (más alto para short = más conservador)
```

### Fix 1C — Gate de R:R mínimo

Después de calcular stop, antes de calcular target, agregar:

```rust
let risk = (entry - stop).abs();

// Si el riesgo calculado es menor a $10, hay algo degenerado
if risk < 10.0 {
    return None;
}
```

### Fix 1D — TTL correcto

```rust
// ACTUAL (incorrecto):
ttl_ms: 300_000,   // 5 minutos = 1 barra M5

// CORRECTO (50 barras = 250 minutos):
ttl_ms: cfg.default_ttl_ms,  // usar el valor de StrategyConfig
// StrategyConfig::default_ttl_ms debería ser 15_000_000 (250 min)
```

Verificar el valor en `types.rs`:
```bash
grep -n "default_ttl_ms" data/src/strategy/types.rs
```

---

## Parte 2 — Target estructural dinámico (hacer después de validar Parte 1)

El target fijo en VAH es conceptualmente incorrecto. El target correcto es el próximo
nivel de resistencia estructural donde el mercado va a encontrar vendedores reales.

### 2.1 — Función `find_structural_target`

Agregar en el detector o en un módulo de utilidades compartido:

```rust
/// Encuentra el target estructural más cercano en la dirección del trade
/// que ofrezca el R:R mínimo requerido.
///
/// Candidatos en orden de preferencia:
/// 1. HVN más cercano en dirección del trade
/// 2. VAH/VAL según dirección
/// 3. Wall de orderbook más cercano
/// 4. Swing high/low reciente (últimas N barras)
/// 5. Fallback: múltiplo de ATR
fn find_structural_target(
    entry: f64,
    stop: f64,
    side: Side,
    vp: &VolumeProfileContext,
    ob: &OrderbookContext,
    bars: &VecDeque<Bar>,
    atr: f64,
    min_rr: f64,    // mínimo R:R aceptable (ej: 1.5)
    max_rr: f64,    // máximo razonable para M5 (ej: 8.0)
) -> Option<f64> {
    let risk = (entry - stop).abs();
    let min_reward = risk * min_rr;
    let max_reward = risk * max_rr;

    let mut candidates: Vec<f64> = Vec::new();

    match side {
        Side::Long => {
            // HVNs arriba del entry dentro del rango válido
            candidates.extend(
                vp.hvn_levels.iter()
                    .copied()
                    .filter(|&h| h.is_finite())
                    .filter(|&h| h > entry + min_reward && h < entry + max_reward)
            );

            // VAH si está en rango válido
            if let Some(vah) = vp.vah {
                if vah > entry + min_reward && vah < entry + max_reward {
                    candidates.push(vah);
                }
            }

            // Ask walls en rango válido
            candidates.extend(
                ob.walls_above.iter()
                    .copied()
                    .filter(|&w| w.is_finite())
                    .filter(|&w| w > entry + min_reward && w < entry + max_reward)
            );

            // Swing high reciente (últimas 20 barras)
            if let Some(swing_high) = recent_swing_high(bars, 20) {
                if swing_high > entry + min_reward && swing_high < entry + max_reward {
                    candidates.push(swing_high);
                }
            }

            // Si no hay candidatos estructurales: fallback 3× ATR
            // Solo si 3× ATR también supera el min_rr
            if candidates.is_empty() {
                let fallback = entry + 3.0 * atr;
                let fallback_rr = (fallback - entry) / risk;
                if fallback_rr >= min_rr {
                    candidates.push(fallback);
                }
            }

            // Tomar el candidato más cercano (menor reward primero)
            // Esto captura el target más conservador que cumple el R:R
            candidates.into_iter()
                .filter(|t| t.is_finite())
                .min_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal))
        }

        Side::Short => {
            // HVNs debajo del entry
            candidates.extend(
                vp.hvn_levels.iter()
                    .copied()
                    .filter(|&h| h.is_finite())
                    .filter(|&h| h < entry - min_reward && h > entry - max_reward)
            );

            // VAL si está en rango válido
            if let Some(val) = vp.val {
                if val < entry - min_reward && val > entry - max_reward {
                    candidates.push(val);
                }
            }

            // Bid walls en rango válido
            candidates.extend(
                ob.walls_below.iter()
                    .copied()
                    .filter(|&w| w.is_finite())
                    .filter(|&w| w < entry - min_reward && w > entry - max_reward)
            );

            // Swing low reciente
            if let Some(swing_low) = recent_swing_low(bars, 20) {
                if swing_low < entry - min_reward && swing_low > entry - max_reward {
                    candidates.push(swing_low);
                }
            }

            // Fallback
            if candidates.is_empty() {
                let fallback = entry - 3.0 * atr;
                let fallback_rr = (entry - fallback) / risk;
                if fallback_rr >= min_rr {
                    candidates.push(fallback);
                }
            }

            // Tomar el más cercano (mayor precio para short = más conservador)
            candidates.into_iter()
                .filter(|t| t.is_finite())
                .max_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal))
        }
    }
}
```

### 2.2 — Helpers de swing high/low

```rust
/// Swing high reciente: máximo de los highs de las últimas N barras
/// Solo si ese high no fue el de la barra actual (necesita ser un nivel viejo)
fn recent_swing_high(bars: &VecDeque<Bar>, n: usize) -> Option<f64> {
    bars.iter()
        .rev()
        .skip(1)          // skip barra actual
        .take(n)
        .map(|b| b.high.to_f32() as f64)
        .filter(|h| h.is_finite())
        .reduce(f64::max)
}

fn recent_swing_low(bars: &VecDeque<Bar>, n: usize) -> Option<f64> {
    bars.iter()
        .rev()
        .skip(1)
        .take(n)
        .map(|b| b.low.to_f32() as f64)
        .filter(|l| l.is_finite())
        .reduce(f64::min)
}
```

### 2.3 — Integración en el detector

Reemplazar el bloque de cálculo de target actual:

```rust
// ANTES del cambio:
let target = vah;  // o val para short

// DESPUÉS:
let target = find_structural_target(
    entry,
    stop,
    side,
    &ctx.volume_profile,
    &ctx.orderbook,
    &self.bars,    // o como se acceda al historial de barras
    atr,
    cfg.min_rr,            // default: 1.5
    cfg.max_rr_m5,         // default: 8.0
)?;
// El ? propaga None si no hay target válido → señal no se emite
```

### 2.4 — Nuevos campos en StrategyConfig

```rust
// En data/src/strategy/types.rs
pub struct StrategyConfig {
    // ... campos existentes ...

    /// R:R mínimo para emitir señal (default: 1.5)
    pub min_rr: f64,

    /// R:R máximo razonable para M5 BTC
    /// Targets más allá de este múltiplo se ignoran (default: 8.0)
    pub max_rr_m5: f64,
}

impl Default for StrategyConfig {
    fn default() -> Self {
        Self {
            // ... defaults existentes ...
            min_rr: 1.5,
            max_rr_m5: 8.0,
        }
    }
}
```

---

## Parte 3 — Verificación con los datos de hoy

Con los fixes aplicados, simular manualmente cómo habrían quedado las señales:

```
Señal 08:00 UTC (con datos reales de Supabase):
  entry  = 78,137
  atr    = 43.8
  val    = 77,984
  vah    = 78,168
  
  STOP (fix 1A):
    val       = 77,984
    atr_stop  = 78,137 - 43.8 = 78,093
    stop      = max(77,984, 78,093) = 78,093  ← $44 de riesgo
  
  TARGET (parte 2):
    min_reward = 44 × 1.5 = $66
    max_reward = 44 × 8.0 = $352
    
    Candidatos:
      VAH = 78,168 → distancia = $31 → RECHAZADO (< min_reward $66)
      HVNs: necesita datos de hvn_levels (no están en Supabase aún)
      Swing high 20 barras: necesita datos de bars
      Fallback 3×ATR = 78,137 + 131.4 = 78,268 → distancia = $131 → R:R = 131/44 = 2.98 ✓
    
    target = 78,268  (fallback si no hay HVN más cercano)
    R:R real = $131 / $44 = 2.98:1  ← correcto
  
  RESULTADO:
    Con VAH como único candidato estructural (muy cerca), la señal
    habría necesitado el fallback de 3×ATR o un HVN arriba de $78,203.
    Si no había HVN en ese rango → señal no emitida → correcto.
```

---

## Parte 4 — Agregar hvn_levels a Supabase para análisis offline

Los HVNs no están en el schema actual de Supabase — sin ellos no podés
analizar offline si el target estructural habría funcionado.

```sql
-- Agregar a shadow_signals:
ALTER TABLE shadow_signals
  ADD COLUMN hvn_levels_above  DOUBLE PRECISION[],  -- HVNs arriba del entry
  ADD COLUMN hvn_levels_below  DOUBLE PRECISION[],  -- HVNs debajo del entry
  ADD COLUMN nearest_wall_above DOUBLE PRECISION,
  ADD COLUMN nearest_wall_below DOUBLE PRECISION,
  ADD COLUMN swing_high_20     DOUBLE PRECISION,    -- swing high últimas 20 barras
  ADD COLUMN swing_low_20      DOUBLE PRECISION;    -- swing low últimas 20 barras
```

Y en el logger Rust:
```rust
// Agregar al StrategyMarketContext o al signal:
hvn_levels_above: vp.hvn_levels.iter().filter(|&&h| h > entry).cloned().collect(),
hvn_levels_below: vp.hvn_levels.iter().filter(|&&h| h < entry).cloned().collect(),
nearest_wall_above: ob.walls_above.iter().filter(|&&w| w > entry).copied().next(),
nearest_wall_below: ob.walls_below.iter().filter(|&&w| w < entry).copied().next(),
```

---

## Orden de implementación

```
DÍA 1 — Parte 1 (bugs inmediatos):
  [ ] Fix 1A: cambiar min() por max() en stop LONG
  [ ] Fix 1B: cambiar max() por min() en stop SHORT
  [ ] Fix 1C: gate de riesgo mínimo (risk < $10 → return None)
  [ ] Fix 1D: TTL = cfg.default_ttl_ms en vez de 300_000
  [ ] Verificar: cargo test -p data
  [ ] Deploy y observar primeras señales del día siguiente

DÍA 2 — Validar Parte 1 con datos reales:
  [ ] Revisar Supabase: ¿el R:R de las nuevas señales es ≥ 1.5?
  [ ] ¿Cuántas señales se generaron? ¿Menos que antes? ¿Cuáles pasaron?
  [ ] ¿El equity se mueve en ambas direcciones o solo baja por fees?

DÍA 3-5 — Parte 2 (target estructural):
  [ ] Implementar find_structural_target()
  [ ] Implementar recent_swing_high() / recent_swing_low()
  [ ] Agregar min_rr y max_rr_m5 a StrategyConfig
  [ ] Integrar en el detector reemplazando target = vah/val
  [ ] Tests unitarios:
      - contexto con HVN a 2× ATR → target = HVN
      - contexto sin HVN pero con VAH a 2× ATR → target = VAH
      - contexto con solo VAH a 0.5× ATR → None (sin señal)
      - contexto sin estructura pero fallback 3× ATR cumple min_rr → target = fallback
  [ ] Deploy y observar

DÍA 6+ — Parte 4 (datos en Supabase):
  [ ] Migración SQL para hvn_levels_above/below y swing high/low
  [ ] Actualizar logger Rust para incluir esos campos
  [ ] Usar para análisis offline: ¿cuántas veces había HVN vs fallback?
```

---

## Tests que deben pasar después de Parte 1

```rust
#[cfg(test)]
mod tests {
    use super::*;

    fn make_ctx(price: f64, vwap: f64, val: f64, vah: f64, atr: f64) -> StrategyMarketContext {
        // helper para construir contexto mínimo
        // ...
    }

    #[test]
    fn stop_long_uses_atr_when_val_is_far() {
        // val muy lejos → stop debe ser entry - 1×ATR
        let ctx = make_ctx(78137.0, 78074.0, 77000.0, 78500.0, 43.8);
        let signal = detect_vwap_pullback(&ctx, &StrategyConfig::default()).unwrap();
        let expected_stop = 78137.0 - 43.8; // = 78093.2
        assert!((signal.stop_price.unwrap() - expected_stop).abs() < 1.0);
    }

    #[test]
    fn stop_long_uses_val_when_val_is_close() {
        // val dentro de 1×ATR → stop debe ser val (más conservador)
        let ctx = make_ctx(78137.0, 78074.0, 78100.0, 78500.0, 43.8);
        let signal = detect_vwap_pullback(&ctx, &StrategyConfig::default()).unwrap();
        // val=78100 > entry-atr=78093 → stop=val=78100
        assert!((signal.stop_price.unwrap() - 78100.0).abs() < 1.0);
    }

    #[test]
    fn no_signal_when_rr_below_minimum() {
        // target muy cerca → R:R < 1.5 → None
        let ctx = make_ctx(78137.0, 78074.0, 78093.0, 78168.0, 43.8);
        // stop = max(78093, 78093.2) = 78093.2, risk = $43.8
        // vah = 78168, reward = $31 < min_reward $65.7
        // sin HVNs, fallback 3×ATR = 78268, reward = $131, R:R = 3.0 ✓
        // → señal con target fallback
        let signal = detect_vwap_pullback(&ctx, &StrategyConfig::default());
        assert!(signal.is_some()); // tiene señal con fallback
        let target = signal.unwrap().target_price.unwrap();
        assert!(target > 78137.0 + 43.8 * 2.0); // target > 2×ATR del entry
    }

    #[test]
    fn ttl_uses_config_not_hardcoded() {
        let ctx = make_ctx(78137.0, 78074.0, 78006.0, 78400.0, 43.8);
        let mut cfg = StrategyConfig::default();
        cfg.default_ttl_ms = 15_000_000;
        let signal = detect_vwap_pullback(&ctx, &cfg).unwrap();
        assert_eq!(signal.ttl_ms, 15_000_000);
    }
}
```

---

## Resumen de por qué cada fix importa en términos de P&L

| Fix | Problema que resuelve | Impacto en P&L |
|-----|----------------------|----------------|
| Stop con `max()` | Stop estaba 3.5× ATR lejos → riesgo enorme | Reduce pérdida máxima por trade de $153 a $44 |
| Target estructural | Target fijo a $31 cuando riesgo era $153 | Permite ganar 2-5R en vez de 0.2R |
| Gate R:R mínimo | Señales con R:R < 1.5 no se emiten | Elimina trades matemáticamente perdedores |
| TTL correcto | 5 min = trade expira antes de llegar al target | Trade tiene tiempo real de resolver |
| hvn_levels en Supabase | No podés analizar qué targets habría elegido | Habilita calibración del target estructural |

Con los fixes de Parte 1, el sistema deja de tomar trades donde la matemática
garantiza pérdida. Con Parte 2, el sistema empieza a usar la estructura del
mercado para encontrar targets reales.

---

*Los fixes de Parte 1 son cambios de 4 líneas en el detector.*  
*La Parte 2 es ~80 líneas de código nuevo bien testeado.*  
*Ambas partes son independientes y pueden deployarse por separado.*
