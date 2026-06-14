use serde::{Deserialize, Serialize};

use super::trade_state::{CloseReason, StopState, StructuralLevels, TradePhase};
use super::types::Side;

/// Breakeven stop offset — entry ± 8bps to clear round-trip fees before locking in.
/// Must be ≥ fees_pct in TradeConfig to guarantee positive net PnL at BE exit.
const BE_OFFSET: f64 = 0.0008;

#[derive(Debug, Clone, Copy)]
pub struct TradeConfig {
    pub risk_pct: f64,
    /// Target must be at least this many ATRs from entry (filters noise).
    pub min_atr_distance: f64,
    /// Target must not exceed this many ATRs (filters unreachable levels).
    pub max_atr_distance: f64,
    pub fees_pct: f64,
    /// TTL in bars — trade closes if no meaningful progress within this window.
    pub max_bars: u32,
}

impl Default for TradeConfig {
    fn default() -> Self {
        Self {
            risk_pct: 0.01,
            min_atr_distance: 0.5,
            max_atr_distance: 3.0,
            fees_pct: 0.0008,
            max_bars: 24,
        }
    }
}

/// Active trade with progressive stop management.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ActiveTrade {
    pub entry_price: f64,
    /// Current live stop — moves as trade progresses.
    pub stop_price: f64,
    /// Original stop at entry — used for realized_r denominator.
    pub stop_initial: f64,
    pub stop_state: StopState,
    pub phase: TradePhase,
    pub side: Side,
    pub levels: StructuralLevels,
    pub position_size: f64,
    pub entry_equity: f64,
    pub bars_open: u32,
    pub confirmed_swings: Vec<f64>,
    /// Max favorable excursion price (for MFE).
    pub max_favorable: f64,
    /// Max adverse excursion price (for MAE).
    pub max_adverse: f64,
}

impl ActiveTrade {
    /// Returns None if ATR distance or R:R filters are not met.
    pub fn open(
        entry: f64,
        stop: f64,
        levels: StructuralLevels,
        side: Side,
        equity: f64,
        config: TradeConfig,
    ) -> Option<Self> {
        if levels.atr_distance < config.min_atr_distance {
            return None;
        }
        if levels.atr_distance > config.max_atr_distance {
            return None;
        }
        let stop_dist = (entry - stop).abs();
        let target_dist = (levels.target - entry).abs();
        if stop_dist <= 0.0 || target_dist / stop_dist < 1.5 {
            return None;
        }
        let position_size = (equity * config.risk_pct) / stop_dist;
        Some(Self {
            entry_price: entry,
            stop_price: stop,
            stop_initial: stop,
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

    /// Processes one closed bar. Returns Some(reason) when the trade should close.
    pub fn on_bar_close(
        &mut self,
        high: f64,
        low: f64,
        close: f64,
        atr: f64,
        config: TradeConfig,
        invalidated: bool,
    ) -> Option<CloseReason> {
        self.bars_open += 1;

        match self.side {
            Side::Long => {
                if high > self.max_favorable {
                    self.max_favorable = high;
                }
                if low < self.max_adverse {
                    self.max_adverse = low;
                }
            }
            Side::Short => {
                if low < self.max_favorable {
                    self.max_favorable = low;
                }
                if high > self.max_adverse {
                    self.max_adverse = high;
                }
            }
        }

        if self.bars_open >= config.max_bars {
            return Some(CloseReason::TTLExpired);
        }

        // Stop checked before invalidation: when both fire in the same bar the stop
        // price caps the loss. Invalidation at bar_close would exit at a worse price.
        if self.stop_was_hit(low, high) {
            return Some(if matches!(self.phase, TradePhase::TargetExceeded) {
                CloseReason::TrailingHit
            } else {
                CloseReason::StopHit
            });
        }

        if invalidated {
            // Conditions broke but price hasn't hit the stop — behave like a disciplined
            // trader: if we're in profit, lock it in with a breakeven stop; if we're in
            // loss, let the original stop define the max risk rather than exiting early.
            // Only act when stop is still Original — BE/Trailing are already protective.
            let in_profit = match self.side {
                Side::Long => close > self.entry_price,
                Side::Short => close < self.entry_price,
            };
            if in_profit && matches!(self.stop_state, StopState::Original) {
                let be_price = match self.side {
                    Side::Long => self.entry_price * (1.0 + BE_OFFSET),
                    Side::Short => self.entry_price * (1.0 - BE_OFFSET),
                };
                self.stop_price = be_price;
                self.stop_state = StopState::BreakEven {
                    confirmed_level: close,
                };
                self.phase = TradePhase::Level1Confirmed;
            }
            // In loss or already protected: do nothing, stop handles the exit.
        }

        // Only close on TargetHit when trailing is not yet active.
        if self.target_was_hit(high, low) && !matches!(self.phase, TradePhase::TargetExceeded) {
            return Some(CloseReason::TargetHit);
        }

        if atr > 0.0 {
            self.update_stop_state(close, high, low, atr);
        }

        None
    }

    fn update_stop_state(&mut self, close: f64, high: f64, low: f64, atr: f64) {
        match self.stop_state.clone() {
            StopState::Original => {
                if let Some(intermediate) = self.levels.intermediate {
                    let confirmed = match self.side {
                        Side::Long => close > intermediate,
                        Side::Short => close < intermediate,
                    };
                    if confirmed {
                        let be_price = match self.side {
                            Side::Long => self.entry_price * (1.0 + BE_OFFSET),
                            Side::Short => self.entry_price * (1.0 - BE_OFFSET),
                        };
                        self.stop_price = be_price;
                        self.stop_state = StopState::BreakEven {
                            confirmed_level: intermediate,
                        };
                        self.phase = TradePhase::Level1Confirmed;
                    }
                } else if self.progress_to_target(close) > 0.60 {
                    let be_price = match self.side {
                        Side::Long => self.entry_price * (1.0 + BE_OFFSET),
                        Side::Short => self.entry_price * (1.0 - BE_OFFSET),
                    };
                    self.stop_price = be_price;
                    self.stop_state = StopState::BreakEven {
                        confirmed_level: close,
                    };
                }
            }
            StopState::BreakEven { .. } => {
                let target_exceeded = match self.side {
                    Side::Long => close > self.levels.target,
                    Side::Short => close < self.levels.target,
                };
                if target_exceeded {
                    let initial_trail = match self.side {
                        Side::Long => close - atr * 0.5,
                        Side::Short => close + atr * 0.5,
                    };
                    self.stop_price = initial_trail;
                    self.stop_state = StopState::TrailingStructural {
                        last_swing: initial_trail,
                    };
                    self.phase = TradePhase::TargetExceeded;
                }
            }
            StopState::TrailingStructural { last_swing } => {
                let new_swing = match self.side {
                    Side::Long => {
                        if low > last_swing {
                            low
                        } else {
                            last_swing
                        }
                    }
                    Side::Short => {
                        if high < last_swing {
                            high
                        } else {
                            last_swing
                        }
                    }
                };
                let should_update = match self.side {
                    Side::Long => new_swing > self.stop_price,
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

    pub fn stop_was_hit(&self, low: f64, high: f64) -> bool {
        match self.side {
            Side::Long => low <= self.stop_price,
            Side::Short => high >= self.stop_price,
        }
    }

    pub fn target_was_hit(&self, high: f64, low: f64) -> bool {
        match self.side {
            Side::Long => high >= self.levels.target,
            Side::Short => low <= self.levels.target,
        }
    }

    pub fn progress_to_target(&self, price: f64) -> f64 {
        let total = (self.levels.target - self.entry_price).abs();
        if total == 0.0 {
            return 0.0;
        }
        let traveled = match self.side {
            Side::Long => (price - self.entry_price).max(0.0),
            Side::Short => (self.entry_price - price).max(0.0),
        };
        (traveled / total).min(1.0)
    }

    /// R realized relative to the original stop distance.
    pub fn realized_r(&self, exit_price: f64) -> f64 {
        let risk = (self.entry_price - self.stop_initial).abs();
        if risk == 0.0 {
            return 0.0;
        }
        match self.side {
            Side::Long => (exit_price - self.entry_price) / risk,
            Side::Short => (self.entry_price - exit_price) / risk,
        }
    }
}
