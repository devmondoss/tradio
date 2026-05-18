# Fix 02 — OrderBlockDetector: peso controlado + validación empírica

> El OrderBlockDetector es el módulo más nuevo del sistema y el más subjetivo
> algorítmicamente. Este fix establece el protocolo para introducirlo con peso
> bajo, medir su contribución real y calibrarlo con datos del OutcomeTracker.

---

## Problema concreto

Un OB mal calibrado genera dos tipos de error:

```
Error tipo 1 — Falso positivo:
  Impulso débil detectado como OB válido
  → señal en zona que no tiene soporte institucional real
  → score inflado artificialmente → entrada mala

Error tipo 2 — Over-reliance:
  OB correcto pero con peso alto en scoring
  → sistema entra aunque otros indicadores estén débiles
  → OB solo no es suficiente para una entrada
```

---

## Criterios de validez de un Order Block — definición precisa

Para que un OB sea considerado válido por el detector, debe cumplir **todos**:

### OB Alcista (Bullish OB)
```
1. Es la última vela BAJISTA antes de un impulso alcista
2. El impulso alcista posterior rompe al menos 2 swing highs previos
   (confirma que hay participación institucional real)
3. El OB aún no ha sido "mitigado" — el precio no ha vuelto
   a cruzar el 50% del rango de esa vela desde el lado bajista
4. El OB está dentro de una zona de Discount (PriceZone < 50%)
   o en un nivel de soporte estructural (HVN, VAL)
5. El volumen de la vela OB es > 1.2× el volumen promedio de las
   últimas 20 velas (confirma participación real)
```

### OB Bajista (Bearish OB)
```
1. Es la última vela ALCISTA antes de un impulso bajista
2. El impulso bajista posterior rompe al menos 2 swing lows previos
3. No mitigado (precio no cruzó 50% del OB desde el lado alcista)
4. Dentro de zona Premium (PriceZone > 50%) o resistencia estructural
5. Volumen > 1.2× promedio de las últimas 20 velas
```

### Estado de mitigación
```rust
pub enum OBStatus {
    /// OB válido, precio no ha vuelto
    Active,
    /// Precio tocó el OB pero no cerró vela dentro — sigue activo
    Tested,
    /// Precio cerró vela dentro del OB — considerado mitigado
    Mitigated,
    /// Precio cerró vela completamente al otro lado — invalidado
    Invalidated,
}
```

---

## Peso inicial en scoring — protocolo de introducción

### Fase A: observación (primeras 2 semanas)
Peso en scoring = **0.0** — el OB se detecta y logea pero **no afecta ninguna decisión**.

```rust
// StrategyProfile para MarketPure en Fase A
MarketPureProfile {
    cvd_slope_weight:    0.25,
    taker_imb_weight:    0.20,
    delta_weight:        0.10,
    target_dist_weight:  0.25,
    rr_weight:           0.20,
    ob_weight:           0.00,  // ← Fase A: solo observación
    fvg_weight:          0.00,  // ← ídem FVG
}
```

En los logs, agregar:
```json
{
  "event": "ob_detected",
  "ob_type": "Bullish",
  "ob_price_high": 77100.0,
  "ob_price_low":  76980.0,
  "ob_volume_ratio": 1.45,
  "swings_broken": 3,
  "price_zone": "Discount",
  "strategy_signal_nearby": "VAFA",
  "signal_outcome": "target_hit",   ← se llena en retrospectiva
  "ob_contributed": false            ← Fase A: no contribuyó
}
```

### Fase B: peso bajo (semanas 3-4)
Si en Fase A el 60%+ de los OBs detectados cerca de señales ganadoras
corresponden a señales que terminaron en `target_hit` o `trailing_hit`:

```rust
ob_weight: 0.08,  // 8% del score total
fvg_weight: 0.07, // 7% del score total
// Reducir target_dist en 0.05 para compensar
target_dist_weight: 0.20,
```

### Fase C: peso estándar (mes 2+)
Si en Fase B el OB mejora el win rate de las señales donde aparece en > 5%:

```rust
ob_weight:  0.12,
fvg_weight: 0.10,
target_dist_weight: 0.18,
cvd_slope_weight:   0.22,
```

---

## Métricas de validación — qué medir en OutcomeTracker

Agregar a `strategy_outcomes.jsonl`:

```json
{
  "signal_id": "uuid",
  "ob_present": true,
  "ob_type": "Bullish",
  "ob_distance_pct": 0.12,
  "ob_status_at_entry": "Active",
  "fvg_present": false,
  "ob_plus_fvg_confluence": false,
  "realized_r": 2.3,
  "close_reason": "TrailingHit"
}
```

### Queries de análisis (SQL / jsonl filter)

```sql
-- Win rate con OB presente vs sin OB
SELECT
  ob_present,
  COUNT(*) as total,
  AVG(CASE WHEN realized_r > 0 THEN 1.0 ELSE 0.0 END) as win_rate,
  AVG(realized_r) as avg_r
FROM outcomes
GROUP BY ob_present;

-- Distribución de R por tipo de OB
SELECT ob_type, AVG(realized_r), COUNT(*)
FROM outcomes
WHERE ob_present = true
GROUP BY ob_type;

-- Confluencia OB + FVG
SELECT
  ob_plus_fvg_confluence,
  AVG(realized_r),
  COUNT(*)
FROM outcomes
GROUP BY ob_plus_fvg_confluence;
```

### Criterio de promoción de Fase A → B → C

| Condición | Acción |
|---|---|
| OB presente en señal + win_rate >= 0.60 con n >= 20 | Promover a Fase B |
| OB presente mejora avg_r en >= +0.3R vs sin OB | Promover a Fase C |
| OB presente + win_rate < 0.45 con n >= 20 | Revisar criterios de validez |
| Falso positivo rate > 40% | Endurecer criterio de swings_broken (de 2 a 3) |

---

## Interacción con estrategias existentes

### VAFA
```
OB alcista en VAL → confluencia alta → score bonus
OB bajista en VAH → confluencia alta → score bonus
OB en zona contraria al trade → penalización leve (−0.05)
```

### VWAP Pullback
```
Pullback que retorna a VWAP + OB activo en mismo nivel → máxima confluencia
Peso: OB confirma el pullback tiene soporte institucional real
```

### LVN
```
OB al inicio de una zona LVN → confirma que el vacío tiene origen institucional
OB ausente en LVN → breakout puede ser menos confiable
```

### SMD
```
Divergencia smart money en zona de OB bajista → setup de mayor calidad
SMD sin OB → señal válida pero de menor convicción
```

---

## Estructura del detector — `order_block.rs`

```rust
#[derive(Debug, Clone)]
pub struct OrderBlock {
    pub ob_type: OBType,        // Bullish / Bearish
    pub high: f64,
    pub low: f64,
    pub mid: f64,               // (high + low) / 2 — nivel de mitigación
    pub volume_ratio: f64,      // volumen OB / avg volumen 20 velas
    pub swings_broken: u32,     // cuántos swings rompió el impulso posterior
    pub status: OBStatus,       // Active / Tested / Mitigated / Invalidated
    pub bar_formed: u64,        // bar_index donde se formó
    pub price_zone: PriceZone,  // Discount / Equilibrium / Premium
}

impl OrderBlock {
    /// Actualiza el estado del OB según el precio actual
    pub fn update_status(&mut self, close: f64, low: f64, high: f64) {
        match self.ob_type {
            OBType::Bullish => {
                if close < self.mid {
                    self.status = OBStatus::Mitigated;
                } else if low <= self.high && low >= self.low {
                    self.status = OBStatus::Tested;
                }
                if close < self.low {
                    self.status = OBStatus::Invalidated;
                }
            }
            OBType::Bearish => {
                if close > self.mid {
                    self.status = OBStatus::Mitigated;
                } else if high >= self.low && high <= self.high {
                    self.status = OBStatus::Tested;
                }
                if close > self.high {
                    self.status = OBStatus::Invalidated;
                }
            }
        }
    }

    /// Distancia porcentual del precio actual al centro del OB
    pub fn distance_pct(&self, price: f64) -> f64 {
        ((price - self.mid) / self.mid).abs()
    }

    /// True si el precio está dentro del rango del OB
    pub fn price_inside(&self, price: f64) -> bool {
        price >= self.low && price <= self.high
    }
}
```

---

## Orden de implementación

| Paso | Acción |
|---|---|
| 1 | Definir criterios exactos de validez (swings, volumen, zona) en `order_block.rs` |
| 2 | Implementar `update_status()` — mitigación por cierre de vela, no intrabar |
| 3 | Agregar campos OB al log de `strategy_outcomes.jsonl` |
| 4 | Fase A: peso 0.0 — solo observación durante 2 semanas |
| 5 | Correr análisis de validación con queries sobre outcomes |
| 6 | Fase B si criterios se cumplen: peso 0.08 |
| 7 | Fase C si mejora measurable: peso 0.12 |
