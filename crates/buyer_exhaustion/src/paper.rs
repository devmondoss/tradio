//! Paper trader para Buyer Exhaustion.
//!
//! Opera en barras M1. En cada cierre verifica si el precio tocó stop o target.
//! Gestión de riesgo:
//!   - Stop = range_high (inválido si precio recupera el rango)
//!   - Target = T1: range_low − range_height (medida proyectada)
//!   - Time stop: si a TIME_STOP_BARS la posición está en pérdida, cierra al close
//!   - Daily risk cap: pausa al llegar a day_loss_limit o day_profit_cap

use crate::signal::{BeClosedTrade, BeExitReason, BuyerExhaustionSignal};

// ── Posición activa ───────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
struct BePosition {
    entry_price:  f64,
    stop_price:   f64,
    target_price: f64,
    entry_ms:     i64,
    bars_held:    u32,
    supabase_id:  Option<String>,
}

// ── Paper trader ──────────────────────────────────────────────────────────────

pub struct BePaperTrader {
    active: Option<BePosition>,

    // ── Risk cap diario ─────────────────────────────────────────────────────
    pub day_r:           f64,
    pub day_loss_limit:  f64,
    pub day_profit_cap:  f64,
    last_bar_day:        u32,
}

impl BePaperTrader {
    pub fn new() -> Self {
        Self {
            active:          None,
            day_r:           0.0,
            day_loss_limit: -3.0,
            day_profit_cap:  6.0,
            last_bar_day:    0,
        }
    }

    pub fn has_position(&self) -> bool {
        self.active.is_some()
    }

    pub fn entry_ms(&self) -> Option<i64> {
        self.active.as_ref().map(|p| p.entry_ms)
    }

    pub fn is_daily_limit_hit(&self) -> bool {
        self.day_r <= self.day_loss_limit || self.day_r >= self.day_profit_cap
    }

    /// Asigna el UUID de Supabase cuando la escritura async devuelve el ID.
    pub fn set_supabase_id(&mut self, id: String) {
        if let Some(pos) = &mut self.active {
            pos.supabase_id = Some(id);
        }
    }

    /// Abre una nueva posición Short desde una señal BE.
    pub fn open(&mut self, sig: &BuyerExhaustionSignal) {
        self.active = Some(BePosition {
            entry_price:  sig.entry_price,
            stop_price:   sig.stop_price,
            target_price: sig.target_price,
            entry_ms:     sig.timestamp_ms,
            bars_held:    0,
            supabase_id:  None,
        });
    }

    /// Restaura una posición persistida en Supabase tras reinicio del proceso.
    pub fn restore(
        &mut self,
        signal_id:    String,
        entry_price:  f64,
        stop_price:   f64,
        target_price: f64,
        entry_ms:     i64,
    ) {
        self.active = Some(BePosition {
            entry_price,
            stop_price,
            target_price,
            entry_ms,
            bars_held:  0,
            supabase_id: Some(signal_id),
        });
    }

    /// Llamar en cada cierre de barra M1.
    pub fn on_bar_close(
        &mut self,
        high:           f64,
        low:            f64,
        close:          f64,
        bar_ms:         i64,
        time_stop_bars: u32,
    ) -> Option<BeClosedTrade> {
        self.maybe_reset_day(bar_ms);

        let pos = self.active.as_mut()?;
        pos.bars_held += 1;

        let risk = (pos.stop_price - pos.entry_price).abs();
        if risk < 1e-10 {
            return None;
        }

        // Short: stop encima de entry, target debajo de entry
        let stop_hit   = high >= pos.stop_price;
        let target_hit = low  <= pos.target_price;

        // Si ambos ocurren en la misma barra, asumimos el peor caso (stop)
        let reason = if stop_hit {
            BeExitReason::Stop
        } else if target_hit {
            BeExitReason::Target
        } else {
            // Time stop: posición en pérdida tras N barras
            if pos.bars_held >= time_stop_bars {
                let pnl = pos.entry_price - close; // Short: positivo si cayó
                if pnl < 0.0 {
                    println!(
                        "[be_paper] time_stop bar={} pnl={:.3}R",
                        pos.bars_held, pnl / risk
                    );
                    return self.close_at(close, bar_ms, BeExitReason::TimeStop);
                }
            }
            return None;
        };

        let exit_price = match reason {
            BeExitReason::Target => pos.target_price,
            _                    => pos.stop_price,
        };

        self.close_at(exit_price, bar_ms, reason)
    }

    fn close_at(
        &mut self,
        exit_price: f64,
        bar_ms:     i64,
        reason:     BeExitReason,
    ) -> Option<BeClosedTrade> {
        let pos = self.active.as_ref()?;
        let risk     = (pos.stop_price - pos.entry_price).abs();
        let pnl      = pos.entry_price - exit_price; // Short
        let result_r = if risk > 1e-10 { pnl / risk } else { 0.0 };

        let trade = BeClosedTrade {
            entry_price: pos.entry_price,
            exit_price,
            result_r,
            exit_reason: reason,
            entry_ms:    pos.entry_ms,
            exit_ms:     bar_ms,
            bars_held:   pos.bars_held,
            supabase_id: pos.supabase_id.clone(),
        };

        self.active  = None;
        self.day_r  += result_r;
        Some(trade)
    }

    /// Cierre forzado al fin de sesión o cuando daily limit se alcanza con
    /// posición abierta.
    pub fn close_session(&mut self, price: f64, bar_ms: i64) -> Option<BeClosedTrade> {
        self.close_at(price, bar_ms, BeExitReason::SessionEnd)
    }

    /// Resetea day_r si el timestamp pertenece a un nuevo día UTC.
    fn maybe_reset_day(&mut self, bar_ms: i64) {
        let day = (bar_ms / 86_400_000) as u32;
        if day != self.last_bar_day {
            self.day_r        = 0.0;
            self.last_bar_day = day;
        }
    }
}
