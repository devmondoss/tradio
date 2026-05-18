# Fix 01 — Router Cooldown por estrategia

> El router actualmente elige la mejor señal del ciclo sin memoria de señales recientes.
> Si una estrategia falla, puede volver a emitir en el mismo nivel de precio velas después.
> Este fix agrega un cooldown de **5 velas** por estrategia después de cualquier señal emitida.

---

## Problema concreto

```
Vela 08:30 → VAFA emite señal LONG en 76,950 → stop hit → equity -5.39
Vela 08:35 → VAFA vuelve a evaluar el mismo nivel → puede emitir de nuevo
Vela 08:40 → ídem
```

El router no sabe que VAFA acaba de fallar. Sin cooldown, la misma estrategia
puede martillar el mismo setup fallido 3-4 veces seguidas.

---

## Solución

Agregar un `CooldownRegistry` en `router.rs` que rastrea, por estrategia:
- Cuándo emitió su última señal (`last_signal_bar`)
- Cuántas velas han pasado desde entonces
- Si está en cooldown o disponible

**Cooldown = 5 velas** desde la última señal emitida, independientemente del resultado.

---

## Archivos a modificar

```
src/
└── strategy/
    ├── router.rs          ← principal — agregar CooldownRegistry
    └── cooldown.rs        ← NUEVO — tipos y lógica de cooldown
```

---

## Tipos — `cooldown.rs`

```rust
use std::collections::HashMap;
use crate::types::StrategyId;

/// Estado de cooldown para una estrategia
#[derive(Debug, Clone)]
pub struct CooldownEntry {
    /// Número de vela (bar_index) donde se emitió la última señal
    pub last_signal_bar: u64,
    /// Resultado de esa señal (para logging y análisis)
    pub last_signal_side: SignalSide,
    /// Precio donde se emitió (para detectar mismo nivel)
    pub last_signal_price: f64,
}

#[derive(Debug, Clone, PartialEq)]
pub enum SignalSide {
    Long,
    Short,
}

/// Registro central de cooldowns — uno por estrategia
pub struct CooldownRegistry {
    entries: HashMap<StrategyId, CooldownEntry>,
    /// Velas de cooldown post-señal (default: 5)
    cooldown_bars: u64,
}

impl CooldownRegistry {
    pub fn new(cooldown_bars: u64) -> Self {
        Self {
            entries: HashMap::new(),
            cooldown_bars,
        }
    }

    /// Registra que una estrategia emitió una señal en esta vela
    pub fn register_signal(
        &mut self,
        strategy_id: StrategyId,
        bar_index: u64,
        side: SignalSide,
        price: f64,
    ) {
        self.entries.insert(strategy_id, CooldownEntry {
            last_signal_bar: bar_index,
            last_signal_side: side,
            last_signal_price: price,
        });
    }

    /// Retorna true si la estrategia está disponible (no en cooldown)
    pub fn is_available(&self, strategy_id: &StrategyId, current_bar: u64) -> bool {
        match self.entries.get(strategy_id) {
            None => true, // nunca emitió — disponible
            Some(entry) => {
                current_bar >= entry.last_signal_bar + self.cooldown_bars
            }
        }
    }

    /// Velas restantes de cooldown (0 = disponible)
    pub fn bars_remaining(&self, strategy_id: &StrategyId, current_bar: u64) -> u64 {
        match self.entries.get(strategy_id) {
            None => 0,
            Some(entry) => {
                let elapsed = current_bar.saturating_sub(entry.last_signal_bar);
                self.cooldown_bars.saturating_sub(elapsed)
            }
        }
    }

    /// Estado de todos los cooldowns — para logging/GUI
    pub fn status(&self, current_bar: u64) -> Vec<CooldownStatus> {
        self.entries.iter().map(|(id, entry)| {
            let remaining = self.bars_remaining(id, current_bar);
            CooldownStatus {
                strategy_id: id.clone(),
                available: remaining == 0,
                bars_remaining: remaining,
                last_price: entry.last_signal_price,
                last_side: entry.last_signal_side.clone(),
            }
        }).collect()
    }
}

#[derive(Debug)]
pub struct CooldownStatus {
    pub strategy_id: StrategyId,
    pub available: bool,
    pub bars_remaining: u64,
    pub last_price: f64,
    pub last_side: SignalSide,
}
```

---

## Cambios en `router.rs`

```rust
use crate::strategy::cooldown::{CooldownRegistry, SignalSide};

pub struct StrategyRouter {
    strategies: Vec<Box<dyn Strategy>>,
    cooldown: CooldownRegistry,   // ← NUEVO
    bar_index: u64,               // ← NUEVO — contador de velas
}

impl StrategyRouter {
    pub fn new(strategies: Vec<Box<dyn Strategy>>) -> Self {
        Self {
            strategies,
            cooldown: CooldownRegistry::new(5), // 5 velas de cooldown
            bar_index: 0,
        }
    }

    pub fn evaluate(&mut self, ctx: &MarketContext) -> Option<Signal> {
        self.bar_index += 1;
        let current_bar = self.bar_index;

        // Recolecta candidatos de todas las estrategias
        let mut candidates: Vec<Signal> = self.strategies
            .iter()
            .filter_map(|strategy| {

                // ── NUEVO: filtrar estrategias en cooldown ──
                if !self.cooldown.is_available(&strategy.id(), current_bar) {
                    let remaining = self.cooldown.bars_remaining(
                        &strategy.id(), current_bar
                    );
                    log::debug!(
                        "[router] {} en cooldown — {} velas restantes",
                        strategy.id(), remaining
                    );
                    return None;
                }

                strategy.evaluate(ctx)
            })
            .filter(|s| s.score >= ctx.config.min_score)
            .collect();

        if candidates.is_empty() {
            return None;
        }

        // Elige la señal con mayor score
        candidates.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap());
        let best = candidates.remove(0);

        // ── NUEVO: registra el cooldown para la estrategia ganadora ──
        self.cooldown.register_signal(
            best.strategy_id.clone(),
            current_bar,
            match best.side {
                Side::Long  => SignalSide::Long,
                Side::Short => SignalSide::Short,
            },
            best.entry_price,
        );

        log::info!(
            "[router] Señal emitida: {} {} @ {:.2} score={:.3} — cooldown activado por 5 velas",
            best.strategy_id, best.side, best.entry_price, best.score
        );

        Some(best)
    }
}
```

---

## Qué cambia en el comportamiento

```
ANTES:
Vela 1: VAFA emite LONG → stop hit
Vela 2: VAFA disponible → puede emitir de nuevo ✗
Vela 3: VAFA disponible → puede emitir de nuevo ✗

DESPUÉS:
Vela 1: VAFA emite LONG → cooldown activado (bar_index = 100)
Vela 2: VAFA bloqueada — 4 velas restantes
Vela 3: VAFA bloqueada — 3 velas restantes
Vela 4: VAFA bloqueada — 2 velas restantes
Vela 5: VAFA bloqueada — 1 vela restante
Vela 6: VAFA disponible → puede evaluar de nuevo ✓  (bar_index = 106)
```

---

## Logging a agregar

Cada vez que el cooldown bloquea una estrategia, loguear a `strategy_outcomes.jsonl`:

```json
{
  "event": "cooldown_block",
  "strategy_id": "VAFA",
  "bar_index": 102,
  "bars_remaining": 3,
  "would_have_emitted": true,
  "score_if_available": 0.71
}
```

El campo `would_have_emitted` + `score_if_available` permite analizar retrospectivamente
si el cooldown bloqueó señales buenas o malas — útil para calibrar el número de velas.

---

## Consideración: cooldown solo para la estrategia ganadora

El cooldown se aplica **solo a la estrategia que ganó el slot** (emitió la señal),
no a todas las que evaluaron en ese ciclo. Si VAFA ganó pero LVN también tenía
un candidato válido con score menor, LVN **no** entra en cooldown — solo VAFA.

Esto evita penalizar estrategias que no emitieron pero estaban disponibles.

---

## Tests recomendados

```rust
#[test]
fn test_cooldown_bloquea_5_velas() {
    let mut registry = CooldownRegistry::new(5);
    registry.register_signal("VAFA", 100, SignalSide::Long, 76950.0);

    assert!(!registry.is_available(&"VAFA", 101)); // bloqueado
    assert!(!registry.is_available(&"VAFA", 104)); // bloqueado
    assert!(registry.is_available(&"VAFA", 105));  // disponible
}

#[test]
fn test_cooldown_no_afecta_otras_estrategias() {
    let mut registry = CooldownRegistry::new(5);
    registry.register_signal("VAFA", 100, SignalSide::Long, 76950.0);

    assert!(registry.is_available(&"LVN", 101));   // LVN no bloqueado
    assert!(registry.is_available(&"SMD", 101));   // SMD no bloqueado
}

#[test]
fn test_cooldown_se_resetea_despues_de_nueva_señal() {
    let mut registry = CooldownRegistry::new(5);
    registry.register_signal("VAFA", 100, SignalSide::Long, 76950.0);
    // Pasaron 5 velas — disponible
    assert!(registry.is_available(&"VAFA", 105));
    // Emite de nuevo
    registry.register_signal("VAFA", 105, SignalSide::Short, 77100.0);
    // Bloqueado de nuevo
    assert!(!registry.is_available(&"VAFA", 106));
}
```

---

## Orden de implementación

| Paso | Acción |
|---|---|
| 1 | Crear `src/strategy/cooldown.rs` con `CooldownRegistry` |
| 2 | Agregar `bar_index: u64` al `StrategyRouter` |
| 3 | Agregar `cooldown: CooldownRegistry` al `StrategyRouter` |
| 4 | Filtrar estrategias en cooldown antes de `evaluate()` |
| 5 | Registrar cooldown después de elegir la señal ganadora |
| 6 | Agregar logging de `cooldown_block` a outcomes |
| 7 | Correr tests unitarios |
| 8 | Verificar en paper que el número de señales por sesión es razonable |
