use super::types::{
    HorizonOutcome, LabOutcome, LabSignal, LabStrategyId, OutcomeStatus, StrategyRuntimeStatus,
};
use crate::strategy::types::Side;

struct PendingOutcome {
    outcome: LabOutcome,
    risk: f64,
    bars_elapsed: u32,
    ttl_bars: u32,
    done: bool,
}

impl PendingOutcome {
    fn r_at(&self, price: f64) -> f64 {
        if self.risk < 1e-10 {
            return 0.0;
        }
        match self.outcome.side {
            Side::Long => (price - self.outcome.entry_price) / self.risk,
            Side::Short => (self.outcome.entry_price - price) / self.risk,
        }
    }
}

/// Tracks open LabSignals across bar closes, resolving outcomes by horizon.
///
/// Call `push` when a ShadowSignal is emitted, then `on_bar` at each M5 close.
/// Returns completed LabOutcomes ready to write to Supabase.
pub struct LabTracker {
    pending: Vec<PendingOutcome>,
    ttl_bars: u32,
}

impl LabTracker {
    pub fn new(ttl_bars: u32) -> Self {
        Self {
            pending: Vec::new(),
            ttl_bars,
        }
    }

    /// Registers a new signal for outcome tracking.
    /// Silently ignores signals without entry/stop/target (Asleep, Observed, Blocked).
    pub fn push(&mut self, signal: &LabSignal) {
        if signal.status != StrategyRuntimeStatus::ShadowSignal {
            return;
        }
        let (Some(entry), Some(target), Some(stop), Some(side)) =
            (signal.entry_price, signal.target, signal.stop, signal.side)
        else {
            return;
        };
        let risk = (entry - stop).abs();
        if risk < 1e-10 {
            return;
        }

        self.pending.push(PendingOutcome {
            outcome: LabOutcome {
                signal_id: signal.signal_id,
                strategy_id: signal.strategy_id,
                entry_price: entry,
                target,
                stop,
                side,
                outcome_30s: None,
                outcome_1m: None,
                outcome_3m: None,
                outcome_5m: None,
                outcome_15m: None,
                outcome_ttl: None,
                mfe: None,
                mae: None,
                final_status: None,
            },
            risk,
            bars_elapsed: 0,
            ttl_bars: self.ttl_bars,
            done: false,
        });
    }

    /// Called at each M5 bar close. Returns outcomes that finished this bar.
    pub fn on_bar(&mut self, high: f64, low: f64, close: f64) -> Vec<LabOutcome> {
        for p in self.pending.iter_mut().filter(|p| !p.done) {
            p.bars_elapsed += 1;

            // MFE/MAE use bar extremes to bound the true excursion
            let r_high = p.r_at(high);
            let r_low = p.r_at(low);
            let r_close = p.r_at(close);

            let bar_max_r = r_high.max(r_low);
            let bar_min_r = r_high.min(r_low);

            p.outcome.mfe = Some(p.outcome.mfe.unwrap_or(f64::NEG_INFINITY).max(bar_max_r));
            p.outcome.mae = Some(p.outcome.mae.unwrap_or(f64::INFINITY).min(bar_min_r));

            // Bar-level horizons (sub-bar 30s/1m/3m need intrabar — stay None here)
            if p.bars_elapsed == 1 {
                p.outcome.outcome_5m = Some(HorizonOutcome {
                    price_at_horizon: close,
                    r_achieved: r_close,
                    direction_correct: r_close > 0.0,
                });
            }
            if p.bars_elapsed == 3 {
                p.outcome.outcome_15m = Some(HorizonOutcome {
                    price_at_horizon: close,
                    r_achieved: r_close,
                    direction_correct: r_close > 0.0,
                });
            }

            // Resolve using bar extremes (conservative: stop wins if both touch same bar)
            let stop_hit = match p.outcome.side {
                Side::Long => low <= p.outcome.stop,
                Side::Short => high >= p.outcome.stop,
            };
            let target_hit = match p.outcome.side {
                Side::Long => high >= p.outcome.target,
                Side::Short => low <= p.outcome.target,
            };

            if stop_hit {
                p.outcome.final_status = Some(OutcomeStatus::StopHit);
                p.done = true;
            } else if target_hit {
                p.outcome.final_status = Some(OutcomeStatus::TargetHit);
                p.done = true;
            } else if p.bars_elapsed >= p.ttl_bars {
                p.outcome.outcome_ttl = Some(HorizonOutcome {
                    price_at_horizon: close,
                    r_achieved: r_close,
                    direction_correct: r_close > 0.0,
                });
                p.outcome.final_status = Some(OutcomeStatus::TtlExpired {
                    exit_price: close,
                    r_achieved: r_close,
                });
                p.done = true;
            }
        }

        let mut completed = Vec::new();
        self.pending.retain(|p| {
            if p.done {
                completed.push(p.outcome.clone());
                false
            } else {
                true
            }
        });
        completed
    }

    /// Returns which strategy IDs currently have open signals being tracked.
    pub fn open_strategy_ids(&self) -> impl Iterator<Item = LabStrategyId> + '_ {
        self.pending
            .iter()
            .filter(|p| !p.done)
            .map(|p| p.outcome.strategy_id)
    }

    pub fn open_count(&self) -> usize {
        self.pending.iter().filter(|p| !p.done).count()
    }
}
