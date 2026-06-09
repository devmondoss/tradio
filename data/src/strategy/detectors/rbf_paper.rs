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
    DailyLimitHit,
}

impl RbfExitReason {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Target         => "TARGET",
            Self::Stop           => "STOP",
            Self::SessionEnd     => "SESSION_END",
            Self::DailyLimitHit  => "DAILY_LIMIT",
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

    /// Restaura una posición abierta persistida en Supabase (cross-deploy survival).
    /// Llamar durante el startup, después del warmup, si Supabase devuelve is_active=true.
    pub fn restore(
        &mut self,
        signal_id:    String,
        direction:    RbfDirection,
        entry_price:  f64,
        stop_price:   f64,
        target_price: f64,
        entry_ms:     i64,
    ) {
        self.active = Some(ActivePosition {
            direction,
            entry_price,
            stop_price,
            target_price,
            entry_ms,
            supabase_id: Some(signal_id),
        });
    }

    /// Llamar en cada cierre de barra M1.
    /// Retorna `Some(RbfClosedTrade)` si la posición cerró, `None` si sigue abierta.
    pub fn on_bar_close(&mut self, high: f64, low: f64, bar_ms: i64) -> Option<RbfClosedTrade> {
        self.maybe_reset_day(bar_ms);

        let pos = self.active.as_ref()?;

        let (stop_hit, target_hit) = match pos.direction {
            RbfDirection::Short => (
                high >= pos.stop_price,
                low  <= pos.target_price,
            ),
            RbfDirection::Long => (
                low  <= pos.stop_price,
                high >= pos.target_price,
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

        self.active  = None;
        self.day_r  += result_r;
        Some(trade)
    }

    /// Cierre forzado al fin de sesión (o cuando daily limit se alcanza con posición abierta).
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

        self.active  = None;
        self.day_r  += result_r;
        Some(trade)
    }
}
