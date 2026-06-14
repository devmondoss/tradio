use super::amd_detector::{AmdDirection, AmdSignal};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AmdExitReason {
    Target,
    Stop,
    SessionEnd,
}

impl AmdExitReason {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Target => "TARGET",
            Self::Stop => "STOP",
            Self::SessionEnd => "SESSION_END",
        }
    }
}

#[derive(Debug, Clone)]
pub struct AmdClosedTrade {
    pub direction: AmdDirection,
    pub entry_price: f64,
    pub exit_price: f64,
    pub result_r: f64,
    pub exit_reason: AmdExitReason,
    pub entry_ms: i64,
    pub exit_ms: i64,
    pub supabase_id: Option<String>,
}

#[derive(Debug, Clone)]
struct AmdActivePosition {
    direction: AmdDirection,
    entry_price: f64,
    stop_price: f64,
    target_price: f64,
    entry_ms: i64,
    supabase_id: Option<String>,
}

pub struct AmdPaperTrader {
    active: Option<AmdActivePosition>,
}

impl AmdPaperTrader {
    pub fn new() -> Self {
        Self { active: None }
    }

    pub fn has_position(&self) -> bool {
        self.active.is_some()
    }

    pub fn open(&mut self, sig: &AmdSignal) {
        self.active = Some(AmdActivePosition {
            direction: sig.direction,
            entry_price: sig.entry_price,
            stop_price: sig.stop_price,
            target_price: sig.target_price,
            entry_ms: sig.timestamp_ms,
            supabase_id: None,
        });
    }

    pub fn set_supabase_id(&mut self, id: String) {
        if let Some(pos) = &mut self.active {
            pos.supabase_id = Some(id);
        }
    }

    /// Restaura posición persistida en Supabase al reiniciar el proceso.
    pub fn restore(
        &mut self,
        signal_id: String,
        direction: AmdDirection,
        entry_price: f64,
        stop_price: f64,
        target_price: f64,
        entry_ms: i64,
    ) {
        self.active = Some(AmdActivePosition {
            direction,
            entry_price,
            stop_price,
            target_price,
            entry_ms,
            supabase_id: Some(signal_id),
        });
    }

    pub fn on_bar_close(&mut self, high: f64, low: f64, bar_ms: i64) -> Option<AmdClosedTrade> {
        let pos = self.active.as_ref()?;

        let (stop_hit, target_hit) = match pos.direction {
            AmdDirection::Short => (high >= pos.stop_price, low <= pos.target_price),
            AmdDirection::Long => (low <= pos.stop_price, high >= pos.target_price),
        };

        let reason = if stop_hit {
            AmdExitReason::Stop
        } else if target_hit {
            AmdExitReason::Target
        } else {
            return None;
        };

        let exit_price = match reason {
            AmdExitReason::Target => pos.target_price,
            _ => pos.stop_price,
        };

        let risk = (pos.entry_price - pos.stop_price).abs();
        let pnl = match pos.direction {
            AmdDirection::Short => pos.entry_price - exit_price,
            AmdDirection::Long => exit_price - pos.entry_price,
        };
        let result_r = if risk > 1e-10 { pnl / risk } else { 0.0 };

        let trade = AmdClosedTrade {
            direction: pos.direction,
            entry_price: pos.entry_price,
            exit_price,
            result_r,
            exit_reason: reason,
            entry_ms: pos.entry_ms,
            exit_ms: bar_ms,
            supabase_id: pos.supabase_id.clone(),
        };

        self.active = None;
        Some(trade)
    }

    pub fn close_session(&mut self, price: f64, bar_ms: i64) -> Option<AmdClosedTrade> {
        let pos = self.active.as_ref()?;
        let risk = (pos.entry_price - pos.stop_price).abs();
        let pnl = match pos.direction {
            AmdDirection::Short => pos.entry_price - price,
            AmdDirection::Long => price - pos.entry_price,
        };
        let result_r = if risk > 1e-10 { pnl / risk } else { 0.0 };

        let trade = AmdClosedTrade {
            direction: pos.direction,
            entry_price: pos.entry_price,
            exit_price: price,
            result_r,
            exit_reason: AmdExitReason::SessionEnd,
            entry_ms: pos.entry_ms,
            exit_ms: bar_ms,
            supabase_id: pos.supabase_id.clone(),
        };

        self.active = None;
        Some(trade)
    }
}
