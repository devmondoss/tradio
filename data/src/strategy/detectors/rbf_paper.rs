//! Paper trader para el detector Range Breakout Flow.
//!
//! Opera en barras M1: en cada cierre chequea si el precio tocó stop o target.
//! Cuando cierra, emite un `RbfClosedTrade` que el monitor persiste en Supabase
//! actualizando la fila original de `rbf_signals`.

use super::range_breakout_flow::{RbfDirection, RbfSignal};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RbfExitReason {
    Target,
    Stop,
    SessionEnd,
}

impl RbfExitReason {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Target     => "TARGET",
            Self::Stop       => "STOP",
            Self::SessionEnd => "SESSION_END",
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
    direction:   RbfDirection,
    entry_price: f64,
    stop_price:  f64,
    target_price: f64,
    entry_ms:    i64,
    supabase_id: Option<String>,
}

pub struct RbfPaperTrader {
    active: Option<ActivePosition>,
}

impl RbfPaperTrader {
    pub fn new() -> Self {
        Self { active: None }
    }

    pub fn has_position(&self) -> bool {
        self.active.is_some()
    }

    /// Abre una nueva posición cuando el detector emite una señal.
    /// `supabase_id` se asigna más tarde via `set_supabase_id` cuando llega el UUID.
    pub fn open(&mut self, sig: &RbfSignal) {
        self.active = Some(ActivePosition {
            direction:    sig.direction,
            entry_price:  sig.entry_price,
            stop_price:   sig.stop_price,
            target_price: sig.target_price,
            entry_ms:     sig.timestamp_ms,
            supabase_id:  None,
        });
    }

    /// Asigna el UUID de Supabase una vez que la escritura async devuelve el ID.
    pub fn set_supabase_id(&mut self, id: String) {
        if let Some(pos) = &mut self.active {
            pos.supabase_id = Some(id);
        }
    }

    /// Llamar en cada cierre de barra M1.
    /// Retorna `Some(RbfClosedTrade)` si la posición cerró, `None` si sigue abierta.
    pub fn on_bar_close(&mut self, high: f64, low: f64, bar_ms: i64) -> Option<RbfClosedTrade> {
        let pos = self.active.as_ref()?;

        let (stop_hit, target_hit) = match pos.direction {
            RbfDirection::Short => (
                high >= pos.stop_price,   // stop = por encima de entrada
                low  <= pos.target_price, // target = por debajo de entrada
            ),
            RbfDirection::Long => (
                low  <= pos.stop_price,   // stop = por debajo de entrada
                high >= pos.target_price, // target = por encima de entrada
            ),
        };

        // Si ambos tocan en la misma barra asumimos el peor caso (stop)
        let reason = if stop_hit {
            RbfExitReason::Stop
        } else if target_hit {
            RbfExitReason::Target
        } else {
            return None;
        };

        let exit_price = match reason {
            RbfExitReason::Target => pos.target_price,
            _                     => pos.stop_price,
        };

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

        self.active = None;
        Some(trade)
    }

    /// Cierre forzado al fin de sesión.
    pub fn close_session(&mut self, price: f64, bar_ms: i64) -> Option<RbfClosedTrade> {
        let pos = self.active.as_ref()?;
        let risk = (pos.entry_price - pos.stop_price).abs();
        let pnl  = match pos.direction {
            RbfDirection::Short => pos.entry_price - price,
            RbfDirection::Long  => price - pos.entry_price,
        };
        let result_r = if risk > 1e-10 { pnl / risk } else { 0.0 };

        let trade = RbfClosedTrade {
            direction:   pos.direction,
            entry_price: pos.entry_price,
            exit_price:  price,
            result_r,
            exit_reason: RbfExitReason::SessionEnd,
            entry_ms:    pos.entry_ms,
            exit_ms:     bar_ms,
            supabase_id: pos.supabase_id.clone(),
        };

        self.active = None;
        Some(trade)
    }
}
