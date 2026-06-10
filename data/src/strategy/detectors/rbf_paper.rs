//! Paper trader para el detector Range Breakout Flow.
//!
//! Opera en barras M1: en cada cierre chequea si el precio tocó stop o target.
//! Cuando cierra, emite un `RbfClosedTrade` que el monitor persiste en Supabase
//! actualizando la fila original de `rbf_signals`.
//!
//! Gestión de posición activa:
//!   - Stop dinámico + trailing: se activa al llegar a TRAIL_ACTIVATE_R (1.5R),
//!     luego el stop sigue el extremo favorable con TRAIL_ATR_K × ATR de distancia.
//!   - Time stop: si a los TIME_STOP_BARS la posición está en pérdida, cierra
//!     al precio actual para evitar que los perdedores se extiendan.

use super::range_breakout_flow::{RbfDirection, RbfSignal};

/// R necesario para activar el trailing stop.
const TRAIL_ACTIVATE_R: f64 = 1.5;
/// Distancia del trailing en múltiplos de ATR.
const TRAIL_ATR_K: f64 = 1.2;
/// Barras máximas en una posición perdedora antes de cerrar.
const TIME_STOP_BARS: u32 = 15;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RbfExitReason {
    Target,
    Stop,
    TrailingStop,
    TimeStop,
    SessionEnd,
    DailyLimitHit,
}

impl RbfExitReason {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Target        => "TARGET",
            Self::Stop          => "STOP",
            Self::TrailingStop  => "TRAILING_STOP",
            Self::TimeStop      => "TIME_STOP",
            Self::SessionEnd    => "SESSION_END",
            Self::DailyLimitHit => "DAILY_LIMIT",
        }
    }
}

#[derive(Debug, Clone)]
pub struct RbfClosedTrade {
    pub direction:   RbfDirection,
    pub entry_price: f64,
    pub exit_price:  f64,
    pub result_r:    f64,       // R-múltiplo: positivo = ganancia
    pub exit_reason: RbfExitReason,
    pub entry_ms:    i64,
    pub exit_ms:     i64,
    /// UUID de la fila en rbf_signals para hacer el PATCH
    pub supabase_id: Option<String>,
}

#[derive(Debug, Clone)]
struct ActivePosition {
    direction:    RbfDirection,
    entry_price:  f64,
    stop_price:   f64,
    target_price: f64,
    entry_ms:     i64,
    supabase_id:  Option<String>,
    /// Barras transcurridas desde la apertura.
    bars_held:    u32,
    /// ATR en la barra de entrada (para trailing stop).
    atr_at_entry: f64,
    /// Extremo más favorable alcanzado (high para Long, low para Short).
    best_extreme: f64,
    /// True cuando el trailing stop ya fue activado (precio alcanzó TRAIL_ACTIVATE_R).
    trailing_active: bool,
}

pub struct RbfPaperTrader {
    active: Option<ActivePosition>,

    // ── Risk cap diario (Fabio: parar en pérdida Y en ganancia) ───────────────
    /// R acumulado en el día UTC actual.
    pub day_r: f64,
    /// Pérdida máxima diaria en R antes de pausar (default -3.0R).
    pub day_loss_limit: f64,
    /// Ganancia máxima diaria en R antes de pausar (default +6.0R).
    pub day_profit_cap: f64,
    /// Día UTC (epoch_ms / 86400000) de la última sesión — detecta cambio de día.
    last_bar_day: u32,
}

impl RbfPaperTrader {
    pub fn new() -> Self {
        Self {
            active:          None,
            day_r:           0.0,
            day_loss_limit:  -3.0,
            day_profit_cap:   6.0,
            last_bar_day:     0,
        }
    }

    pub fn has_position(&self) -> bool {
        self.active.is_some()
    }

    /// Devuelve true si el límite diario (pérdida o ganancia) ya fue alcanzado.
    pub fn is_daily_limit_hit(&self) -> bool {
        self.day_r <= self.day_loss_limit || self.day_r >= self.day_profit_cap
    }

    /// Reset automático si el timestamp pertenece a un nuevo día UTC.
    fn maybe_reset_day(&mut self, bar_ms: i64) {
        let day = (bar_ms / 86_400_000) as u32;
        if day != self.last_bar_day {
            self.day_r        = 0.0;
            self.last_bar_day = day;
        }
    }

    /// Abre una nueva posición cuando el detector emite una señal.
    pub fn open(&mut self, sig: &RbfSignal, atr: f64) {
        let best_extreme = match sig.direction {
            RbfDirection::Long  => sig.entry_price,
            RbfDirection::Short => sig.entry_price,
        };
        self.active = Some(ActivePosition {
            direction:       sig.direction,
            entry_price:     sig.entry_price,
            stop_price:      sig.stop_price,
            target_price:    sig.target_price,
            entry_ms:        sig.timestamp_ms,
            supabase_id:     None,
            bars_held:       0,
            atr_at_entry:    atr,
            best_extreme,
            trailing_active: false,
        });
    }

    /// Asigna el UUID de Supabase una vez que la escritura async devuelve el ID.
    pub fn set_supabase_id(&mut self, id: String) {
        if let Some(pos) = &mut self.active {
            pos.supabase_id = Some(id);
        }
    }

    /// Restaura una posición abierta persistida en Supabase (cross-deploy survival).
    pub fn restore(
        &mut self,
        signal_id:    String,
        direction:    RbfDirection,
        entry_price:  f64,
        stop_price:   f64,
        target_price: f64,
        entry_ms:     i64,
    ) {
        let best_extreme = entry_price;
        self.active = Some(ActivePosition {
            direction,
            entry_price,
            stop_price,
            target_price,
            entry_ms,
            supabase_id:     Some(signal_id),
            bars_held:       0,
            atr_at_entry:    0.0,  // unknown on restore; trailing disabled
            best_extreme,
            trailing_active: false,
        });
    }

    /// Llamar en cada cierre de barra M1.
    /// `close` se usa para time stop y trailing stop updates.
    /// `atr` actual — si 0.0, usa el ATR guardado al abrir.
    /// Retorna `Some(RbfClosedTrade)` si la posición cerró, `None` si sigue abierta.
    pub fn on_bar_close(
        &mut self,
        high:   f64,
        low:    f64,
        close:  f64,
        bar_ms: i64,
        atr:    f64,
    ) -> Option<RbfClosedTrade> {
        self.maybe_reset_day(bar_ms);

        let pos = self.active.as_mut()?;
        pos.bars_held += 1;

        let risk = (pos.entry_price - pos.stop_price).abs();
        if risk < 1e-10 {
            return None;
        }

        let effective_atr = if atr > 0.0 { atr } else { pos.atr_at_entry };

        // ── Actualizar extremo favorable y trailing stop ──────────────────────
        match pos.direction {
            RbfDirection::Long => {
                if high > pos.best_extreme { pos.best_extreme = high; }
                let fav_r = (pos.best_extreme - pos.entry_price) / risk;
                if fav_r >= TRAIL_ACTIVATE_R && !pos.trailing_active {
                    pos.trailing_active = true;
                    println!("[rbf_paper] trailing stop activado en {:.2}R", fav_r);
                }
                if pos.trailing_active && effective_atr > 0.0 {
                    let trail_stop = pos.best_extreme - TRAIL_ATR_K * effective_atr;
                    if trail_stop > pos.stop_price {
                        pos.stop_price = trail_stop;
                    }
                }
            }
            RbfDirection::Short => {
                if low < pos.best_extreme { pos.best_extreme = low; }
                let fav_r = (pos.entry_price - pos.best_extreme) / risk;
                if fav_r >= TRAIL_ACTIVATE_R && !pos.trailing_active {
                    pos.trailing_active = true;
                    println!("[rbf_paper] trailing stop activado en {:.2}R", fav_r);
                }
                if pos.trailing_active && effective_atr > 0.0 {
                    let trail_stop = pos.best_extreme + TRAIL_ATR_K * effective_atr;
                    if trail_stop < pos.stop_price {
                        pos.stop_price = trail_stop;
                    }
                }
            }
        }

        // ── Evaluar stop y target ─────────────────────────────────────────────
        let (stop_hit, target_hit) = match pos.direction {
            RbfDirection::Short => (high >= pos.stop_price, low  <= pos.target_price),
            RbfDirection::Long  => (low  <= pos.stop_price, high >= pos.target_price),
        };

        // Si ambos tocan en la misma barra asumimos el peor caso (stop)
        let reason = if stop_hit {
            if pos.trailing_active { RbfExitReason::TrailingStop } else { RbfExitReason::Stop }
        } else if target_hit {
            RbfExitReason::Target
        } else {
            // ── Time stop: bar 15+ y posición en pérdida → cierra al close ───
            if pos.bars_held >= TIME_STOP_BARS {
                let current_pnl = match pos.direction {
                    RbfDirection::Short => pos.entry_price - close,
                    RbfDirection::Long  => close - pos.entry_price,
                };
                if current_pnl < 0.0 {
                    println!(
                        "[rbf_paper] time stop bar {} pnl={:.3}R",
                        pos.bars_held, current_pnl / risk
                    );
                    return self.close_at(close, bar_ms, RbfExitReason::TimeStop);
                }
            }
            return None;
        };

        let exit_price = match reason {
            RbfExitReason::Target => pos.target_price,
            _                     => pos.stop_price,
        };

        self.close_at(exit_price, bar_ms, reason)
    }

    fn close_at(&mut self, exit_price: f64, bar_ms: i64, reason: RbfExitReason) -> Option<RbfClosedTrade> {
        let pos = self.active.as_ref()?;
        let risk = (pos.entry_price - pos.stop_price).abs();
        let pnl  = match pos.direction {
            RbfDirection::Short => pos.entry_price - exit_price,
            RbfDirection::Long  => exit_price - pos.entry_price,
        };
        let result_r = if risk > 1e-10 { pnl / risk } else { 0.0 };

        let trade = RbfClosedTrade {
            direction:   pos.direction,
            entry_price: pos.entry_price,
            exit_price,
            result_r,
            exit_reason: reason,
            entry_ms:    pos.entry_ms,
            exit_ms:     bar_ms,
            supabase_id: pos.supabase_id.clone(),
        };

        self.active  = None;
        self.day_r  += result_r;
        Some(trade)
    }

    /// Cierre forzado al fin de sesión (o cuando daily limit se alcanza con posición abierta).
    pub fn close_session(&mut self, price: f64, bar_ms: i64) -> Option<RbfClosedTrade> {
        self.close_at(price, bar_ms, RbfExitReason::SessionEnd)
    }
}
