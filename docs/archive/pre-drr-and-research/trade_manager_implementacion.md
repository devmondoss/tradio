# TradeManager — Implementación de gestión dinámica de trades

> Desacopla el riesgo (1% fijo) del upside (ilimitado por estructura).
> Reemplaza el sistema actual de stop fijo + target fijo del `PaperAccount`.

---

## Resumen del cambio

| | Sistema actual | Sistema nuevo |
|---|---|---|
| **Stop** | Fijo en precio inicial | Progresivo: Original → BreakEven → Trailing |
| **Target** | `stop × R:R_fijo` | Nivel estructural real (HVN / OB / FVG / LiqMap) |
| **ATR** | Define el target | Filtra viabilidad del trade |
| **Trigger de cambio** | Tiempo o distancia % | Vela cerrada encima/debajo de nivel estructural |
| **Riesgo máximo** | 1% equity | 1% equity (no cambia) |
| **Upside** | Fijo por R:R | Sin techo — sigue estructura |

---

## Arquitectura

### Ubicación de archivos

```
src/
├── paper/
│   ├── paper_account.rs        ← consume TradeManager, ya existente
│   └── trade_manager.rs        ← NUEVO — lógica de gestión dinámica
├── strategy/
│   └── target_selector.rs      ← NUEVO — selecciona target estructural
└── types/
    └── trade_state.rs          ← NUEVO — tipos StopState, TradePhase
```

---

## Tipos base — `trade_state.rs`

```rust
/// Estado progresivo del stop en un trade activo
#[derive(Debug, Clone, PartialEq)]
pub enum StopState {
    /// Stop en precio original — riesgo máximo activo
    Original,
    /// Stop movido a break-even después de confirmar nivel estructural 1
    BreakEven { confirmed_level: f64 },
    /// Stop siguiendo swings confirmados en TF de ejecución
    TrailingStructural { last_swing: f64 },
}

/// Fase del trade según avance hacia el target
#[derive(Debug, Clone, PartialEq)]
pub enum TradePhase {
    /// Precio no ha llegado al primer nivel estructural
    Open,
    /// Precio confirmó nivel 1 — stop en break-even
    Level1Confirmed,
    /// Precio superó target base — trailing activo
    TargetExceeded,
    /// Trade cerrado
    Closed { reason: CloseReason },
}

#[derive(Debug, Clone, PartialEq)]
pub enum CloseReason {
    StopHit,
    TargetHit,
    TrailingHit,
    TTLExpired,
}

/// Dirección del trade
#[derive(Debug, Clone, PartialEq)]
pub enum Side {
    Long,
    Short,
}

/// Niveles estructurales relevantes para el trade
#[derive(Debug, Clone)]
pub struct StructuralLevels {
    /// Target principal — primer nivel estructural (HVN / OB / FVG / LiqMap)
    pub target: f64,
    /// Nivel intermedio opcional — para mover break-even
    pub intermediate: Option<f64>,
    /// Distancia en ATR al target (calculada al abrir)
    pub atr_distance: f64,
}
```

---

## TradeManager — `trade_manager.rs`

```rust
use crate::types::trade_state::*;

pub struct TradeConfig {
    /// Riesgo máximo por trade como fracción del equity (ej: 0.01 = 1%)
    pub risk_pct: f64,
    /// ATR mínimo que debe tener el trade para ser válido
    pub min_atr_distance: f64,    // default: 0.5
    /// ATR máximo — target demasiado lejos
    pub max_atr_distance: f64,    // default: 3.0
    /// Fees de entrada + salida combinados
    pub fees_pct: f64,            // default: 0.0008 (0.04% × 2)
    /// TTL máximo en velas si el precio no avanza
    pub max_bars: u32,            // default: 24
}

pub struct ActiveTrade {
    pub entry_price: f64,
    pub stop_price: f64,
    pub stop_state: StopState,
    pub phase: TradePhase,
    pub side: Side,
    pub levels: StructuralLevels,
    pub position_size: f64,
    pub entry_equity: f64,
    pub bars_open: u32,
    /// Swings confirmados mientras el trade está abierto
    pub confirmed_swings: Vec<f64>,
    /// Máximo favorable alcanzado (para MFE)
    pub max_favorable: f64,
    /// Máximo adverso alcanzado (para MAE)
    pub max_adverse: f64,
}

impl ActiveTrade {
    /// Abre un nuevo trade con gestión dinámica
    /// Retorna None si el trade no cumple los filtros de ATR
    pub fn open(
        entry: f64,
        stop: f64,
        levels: StructuralLevels,
        side: Side,
        equity: f64,
        config: &TradeConfig,
    ) -> Option<Self> {

        // Filtro 1: target demasiado cerca (ruido puede tocarlo)
        if levels.atr_distance < config.min_atr_distance {
            return None;
        }

        // Filtro 2: target demasiado lejos (no alcanzable en horizonte)
        if levels.atr_distance > config.max_atr_distance {
            return None;
        }

        // Filtro 3: R:R mínimo 1.5 antes de entrar
        let stop_dist = (entry - stop).abs();
        let target_dist = (levels.target - entry).abs();
        if target_dist / stop_dist < 1.5 {
            return None;
        }

        // Calcula tamaño de posición por riesgo fijo
        let risk_dollars = equity * config.risk_pct;
        let position_size = risk_dollars / stop_dist;

        Some(ActiveTrade {
            entry_price: entry,
            stop_price: stop,
            stop_state: StopState::Original,
            phase: TradePhase::Open,
            side,
            levels,
            position_size,
            entry_equity: equity,
            bars_open: 0,
            confirmed_swings: vec![],
            max_favorable: entry,
            max_adverse: entry,
        })
    }

    /// Procesa cada vela cerrada — actualiza stop y fase
    /// Retorna Some(CloseReason) si el trade debe cerrarse
    pub fn on_bar_close(
        &mut self,
        high: f64,
        low: f64,
        close: f64,
        atr: f64,
        config: &TradeConfig,
    ) -> Option<CloseReason> {

        self.bars_open += 1;

        // Actualiza MFE y MAE
        match self.side {
            Side::Long => {
                self.max_favorable = self.max_favorable.max(high);
                self.max_adverse = self.max_adverse.min(low);
            }
            Side::Short => {
                self.max_favorable = self.max_favorable.min(low);
                self.max_adverse = self.max_adverse.max(high);
            }
        }

        // TTL — cierra si lleva demasiadas velas sin avanzar
        if self.bars_open >= config.max_bars {
            return Some(CloseReason::TTLExpired);
        }

        // Verifica si el stop fue tocado intrabar
        if self.stop_was_hit(low, high) {
            return Some(CloseReason::StopHit);
        }

        // Verifica si el target fue tocado
        if self.target_was_hit(high, low) {
            // Si trailing está activo, no cerramos — actualizamos trailing
            if self.stop_state == StopState::TrailingStructural { .. } {
                // ver lógica de trailing abajo
            } else {
                return Some(CloseReason::TargetHit);
            }
        }

        // Escala el stop según estructura confirmada
        self.update_stop_state(close, high, low, atr);

        None
    }

    /// Lógica central: escalar StopState según estructura confirmada en vela cerrada
    fn update_stop_state(&mut self, close: f64, high: f64, low: f64, atr: f64) {
        match &self.stop_state.clone() {

            // Estado 1: stop original
            // Condición para escalar: precio CIERRA encima del nivel intermedio (long)
            // o debajo (short) — no intrabar, solo en cierre de vela
            StopState::Original => {
                if let Some(intermediate) = self.levels.intermediate {
                    let confirmed = match self.side {
                        Side::Long  => close > intermediate,
                        Side::Short => close < intermediate,
                    };
                    if confirmed {
                        // Mueve stop a break-even + fees
                        let be_price = match self.side {
                            Side::Long  => self.entry_price * (1.0 + 0.0008),
                            Side::Short => self.entry_price * (1.0 - 0.0008),
                        };
                        self.stop_price = be_price;
                        self.stop_state = StopState::BreakEven {
                            confirmed_level: intermediate,
                        };
                        self.phase = TradePhase::Level1Confirmed;
                    }
                }
                // Si no hay nivel intermedio, escalar directo cuando
                // el precio está a >60% del camino al target
                else {
                    let progress = self.progress_to_target(close);
                    if progress > 0.60 {
                        let be_price = match self.side {
                            Side::Long  => self.entry_price * 1.0008,
                            Side::Short => self.entry_price * 0.9992,
                        };
                        self.stop_price = be_price;
                        self.stop_state = StopState::BreakEven {
                            confirmed_level: close,
                        };
                    }
                }
            }

            // Estado 2: break-even activo
            // Condición para escalar a trailing: precio supera el target base
            StopState::BreakEven { .. } => {
                let target_exceeded = match self.side {
                    Side::Long  => close > self.levels.target,
                    Side::Short => close < self.levels.target,
                };
                if target_exceeded {
                    // Primer swing para trailing = precio actual - 0.5 ATR
                    let initial_trail = match self.side {
                        Side::Long  => close - atr * 0.5,
                        Side::Short => close + atr * 0.5,
                    };
                    self.stop_price = initial_trail;
                    self.stop_state = StopState::TrailingStructural {
                        last_swing: initial_trail,
                    };
                    self.phase = TradePhase::TargetExceeded;
                }
            }

            // Estado 3: trailing estructural
            // Actualiza stop al último swing confirmado en TF de ejecución
            StopState::TrailingStructural { last_swing } => {
                let new_swing = match self.side {
                    // Para longs: nuevo HL — si la vela cerró más alto que el swing previo
                    Side::Long => {
                        if low > *last_swing {
                            low  // nuevo swing low más alto
                        } else {
                            *last_swing  // mantiene el anterior
                        }
                    }
                    // Para shorts: nuevo LH
                    Side::Short => {
                        if high < *last_swing {
                            high
                        } else {
                            *last_swing
                        }
                    }
                };
                // Solo mueve el stop en la dirección favorable
                let should_update = match self.side {
                    Side::Long  => new_swing > self.stop_price,
                    Side::Short => new_swing < self.stop_price,
                };
                if should_update {
                    self.stop_price = new_swing;
                    self.stop_state = StopState::TrailingStructural {
                        last_swing: new_swing,
                    };
                    self.confirmed_swings.push(new_swing);
                }
            }
        }
    }

    fn stop_was_hit(&self, low: f64, high: f64) -> bool {
        match self.side {
            Side::Long  => low  <= self.stop_price,
            Side::Short => high >= self.stop_price,
        }
    }

    fn target_was_hit(&self, high: f64, low: f64) -> bool {
        match self.side {
            Side::Long  => high >= self.levels.target,
            Side::Short => low  <= self.levels.target,
        }
    }

    fn progress_to_target(&self, price: f64) -> f64 {
        let total = (self.levels.target - self.entry_price).abs();
        let done  = (price - self.entry_price).abs();
        if total == 0.0 { return 0.0; }
        (done / total).min(1.0)
    }

    /// R realizado al cerrar
    pub fn realized_r(&self, exit_price: f64) -> f64 {
        let risk = (self.entry_price - self.stop_price).abs();
        if risk == 0.0 { return 0.0; }
        match self.side {
            Side::Long  => (exit_price - self.entry_price) / risk,
            Side::Short => (self.entry_price - exit_price) / risk,
        }
    }

    /// PnL en dólares al cerrar
    pub fn pnl(&self, exit_price: f64) -> f64 {
        let raw = match self.side {
            Side::Long  => (exit_price - self.entry_price) * self.position_size,
            Side::Short => (self.entry_price - exit_price) * self.position_size,
        };
        raw - (exit_price * self.position_size * 0.0008)  // fees salida
    }
}
```

---

## TargetSelector — `target_selector.rs`

Selecciona el target estructural y el nivel intermedio para cada señal.

```rust
pub struct TargetSelector;

impl TargetSelector {
    /// Dado el precio de entrada, dirección y contexto de mercado,
    /// retorna los niveles estructurales para el trade
    pub fn select(
        entry: f64,
        side: &Side,
        vp: &VolumeProfileData,
        ob: Option<&OrderBlock>,
        fvg: Option<&FairValueGap>,
        liq_map: Option<&LiqMapData>,
        atr: f64,
    ) -> Option<StructuralLevels> {

        // Candidatos a target ordenados por prioridad
        // 1. Liq density (más magnético)
        // 2. HVN del volume profile
        // 3. OB no mitigado
        // 4. FVG unfilled
        // 5. VAH / VAL

        let mut candidates: Vec<f64> = vec![];

        // Liquidaciones pendientes
        if let Some(liq) = liq_map {
            match side {
                Side::Long  => candidates.push(liq.density_above_price),
                Side::Short => candidates.push(liq.density_below_price),
            }
        }

        // HVN del VP
        let hvn = match side {
            Side::Long  => vp.next_hvn_above(entry),
            Side::Short => vp.next_hvn_below(entry),
        };
        if let Some(h) = hvn { candidates.push(h); }

        // OB no mitigado
        if let Some(block) = ob {
            if !block.mitigated {
                candidates.push(block.zone_mid());
            }
        }

        // FVG unfilled
        if let Some(gap) = fvg {
            if gap.status == FVGStatus::Unfilled {
                candidates.push(gap.mid());
            }
        }

        // VAH / VAL
        match side {
            Side::Long  => candidates.push(vp.vah),
            Side::Short => candidates.push(vp.val),
        }

        // Filtra candidatos en dirección correcta
        let valid: Vec<f64> = candidates.into_iter().filter(|&c| {
            match side {
                Side::Long  => c > entry,
                Side::Short => c < entry,
            }
        }).collect();

        if valid.is_empty() { return None; }

        // Target = nivel válido más cercano con distancia > 0.5 ATR
        let target = valid.iter()
            .filter(|&&c| (c - entry).abs() > atr * 0.5)
            .min_by(|a, b| {
                let da = (a - entry).abs();
                let db = (b - entry).abs();
                da.partial_cmp(&db).unwrap()
            })
            .copied()?;

        // Nivel intermedio = primer nivel estructural entre entrada y target
        let intermediate = valid.iter()
            .filter(|&&c| {
                let in_range = match side {
                    Side::Long  => c > entry && c < target,
                    Side::Short => c < entry && c > target,
                };
                in_range && (c - entry).abs() > atr * 0.25
            })
            .min_by(|a, b| {
                let da = (a - entry).abs();
                let db = (b - entry).abs();
                da.partial_cmp(&db).unwrap()
            })
            .copied();

        let atr_distance = (target - entry).abs() / atr;

        Some(StructuralLevels {
            target,
            intermediate,
            atr_distance,
        })
    }
}
```

---

## Integración en `paper_account.rs`

### Cambios al abrir un trade

```rust
// ANTES
let target = entry + stop_dist * rr_ratio;
let trade = PaperTrade { entry, stop, target, ... };

// DESPUÉS
let levels = TargetSelector::select(
    entry, &side, &vp_data, ob.as_ref(), fvg.as_ref(), liq_map.as_ref(), atr
)?;

let trade = ActiveTrade::open(
    entry, stop, levels, side, self.equity, &self.config
)?;
```

### Cambios en el loop de velas

```rust
// ANTES — revisaba solo si precio tocó stop o target
fn on_kline(&mut self, k: &Kline) {
    if let Some(trade) = &self.active_trade {
        if k.low <= trade.stop  { self.close_trade(CloseReason::StopHit); }
        if k.high >= trade.target { self.close_trade(CloseReason::TargetHit); }
    }
}

// DESPUÉS — delega al TradeManager
fn on_kline(&mut self, k: &Kline) {
    if let Some(trade) = &mut self.active_trade {
        if let Some(reason) = trade.on_bar_close(
            k.high, k.low, k.close, self.current_atr, &self.config
        ) {
            let exit_price = self.exit_price_for(reason, k);
            let pnl = trade.pnl(exit_price);
            let r   = trade.realized_r(exit_price);
            self.equity += pnl;
            self.log_outcome(trade, exit_price, pnl, r, reason);
            self.active_trade = None;
        }
    }
}
```

### Precio de salida por razón de cierre

```rust
fn exit_price_for(&self, reason: CloseReason, k: &Kline) -> f64 {
    match reason {
        CloseReason::StopHit    => self.active_trade.as_ref().unwrap().stop_price,
        CloseReason::TargetHit  => self.active_trade.as_ref().unwrap().levels.target,
        CloseReason::TrailingHit => self.active_trade.as_ref().unwrap().stop_price,
        CloseReason::TTLExpired  => k.close,
    }
}
```

---

## Cambios en `OutcomeTracker`

El tracker necesita registrar el nuevo detalle de cómo cerró el trade:

```rust
// Agregar a strategy_outcomes.jsonl
{
    "signal_id": "...",
    "entry": 76950.0,
    "exit": 77340.0,
    "stop_initial": 76650.0,
    "stop_final": 77100.0,          // ← nuevo: stop donde cerró
    "stop_state_at_close": "TrailingStructural",  // ← nuevo
    "target_structural": 77400.0,   // ← nuevo: target que usamos
    "intermediate_level": 77100.0,  // ← nuevo
    "realized_r": 1.30,
    "mfe_r": 1.45,
    "mae_r": -0.20,
    "bars_open": 8,
    "close_reason": "TrailingHit",  // ← antes solo StopHit / TargetHit
    "phase_at_close": "TargetExceeded"  // ← nuevo
}
```

---

## Configuración recomendada inicial

```toml
# paper_account.toml o config.rs

[trade_manager]
risk_pct          = 0.01    # 1% equity por trade
min_atr_distance  = 0.5     # target mínimo a 0.5 ATR de la entrada
max_atr_distance  = 3.0     # target máximo a 3 ATR (descarta señales muy lejanas)
fees_pct          = 0.0008  # 0.04% entrada + 0.04% salida
max_bars          = 24      # TTL: 24 velas en TF de ejecución
```

---

## Flujo completo de un trade

```
SEÑAL GENERADA
     │
     ▼
TargetSelector.select()
  ├── Busca: liq_density → HVN → OB → FVG → VAH/VAL
  ├── Nivel intermedio (para break-even)
  └── Filtra por ATR mínimo/máximo
     │
     ▼
ActiveTrade::open()
  ├── Filtro R:R ≥ 1.5
  ├── Calcula position_size por riesgo 1%
  └── StopState = Original
     │
     ▼ (cada vela cerrada)
on_bar_close()
     │
     ├── ¿Stop hit intrabar?     → CloseReason::StopHit
     ├── ¿TTL expirado?          → CloseReason::TTLExpired
     │
     ├── StopState::Original
     │     └── close > intermediate? → BreakEven (stop = entrada + fees)
     │
     ├── StopState::BreakEven
     │     └── close > target?   → TrailingStructural (stop = target - 0.5 ATR)
     │
     └── StopState::TrailingStructural
           ├── nuevo swing > stop? → actualiza trailing
           └── stop hit?          → CloseReason::TrailingHit
```

---

## Orden de implementación sugerido

| Paso | Archivo | Qué hacer |
|---|---|---|
| 1 | `trade_state.rs` | Crear tipos: StopState, TradePhase, CloseReason, Side, StructuralLevels |
| 2 | `trade_manager.rs` | Implementar ActiveTrade::open() y on_bar_close() |
| 3 | `target_selector.rs` | Implementar TargetSelector::select() con VP + fallback ATR |
| 4 | `paper_account.rs` | Reemplazar lógica actual por llamadas a TradeManager |
| 5 | `outcome_tracker.rs` | Agregar campos nuevos al log (stop_state_at_close, target_structural, etc.) |
| 6 | Tests en paper | Correr con datos históricos y comparar equity curve |
| 7 | `target_selector.rs` | Agregar OB y FVG una vez que esos detectores estén implementados |
| 8 | `target_selector.rs` | Agregar LiqMap una vez que LiqMapTracker esté implementado |
