use super::types::*;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::PathBuf;

fn outcomes_dir() -> PathBuf {
    let base = dirs_next::data_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("flowsurface")
        .join("shadow_events");
    let _ = fs::create_dir_all(&base);
    base
}

#[derive(Debug)]
struct TrackedSignal {
    signal: StrategySignal,
    /// Highest price seen since the signal was opened
    highest: f64,
    /// Lowest price seen since the signal was opened
    lowest: f64,
}

impl TrackedSignal {
    fn new(signal: StrategySignal, open_price: f64) -> Self {
        Self {
            signal,
            highest: open_price,
            lowest: open_price,
        }
    }

    fn update_excursion(&mut self, price: f64) {
        if price > self.highest {
            self.highest = price;
        }
        if price < self.lowest {
            self.lowest = price;
        }
    }

    /// MFE in price units (favorable direction only).
    fn mfe(&self) -> f64 {
        let entry = self.signal.entry_price.unwrap_or(0.0);
        match self.signal.side {
            Some(Side::Long) => (self.highest - entry).max(0.0),
            Some(Side::Short) => (entry - self.lowest).max(0.0),
            None => 0.0,
        }
    }

    /// MAE in price units (adverse direction only).
    fn mae(&self) -> f64 {
        let entry = self.signal.entry_price.unwrap_or(0.0);
        match self.signal.side {
            Some(Side::Long) => (entry - self.lowest).max(0.0),
            Some(Side::Short) => (self.highest - entry).max(0.0),
            None => 0.0,
        }
    }

    /// Risk unit = |entry - stop|, used for R-multiples.
    fn risk_unit(&self) -> f64 {
        let entry = self.signal.entry_price.unwrap_or(0.0);
        let stop = self.signal.stop_price.unwrap_or(entry);
        (entry - stop).abs().max(1e-10)
    }

    fn close_reason(&self, now_ms: i64) -> Option<&'static str> {
        let stop = self.signal.stop_price;
        let target = self.signal.target_price;
        let expires_at = self.signal.created_at_ms + self.signal.ttl_ms;

        let stop_hit = stop.map(|s| match self.signal.side {
            Some(Side::Long) => self.lowest <= s,
            Some(Side::Short) => self.highest >= s,
            None => false,
        }).unwrap_or(false);

        let target_hit = target.map(|t| match self.signal.side {
            Some(Side::Long) => self.highest >= t,
            Some(Side::Short) => self.lowest <= t,
            None => false,
        }).unwrap_or(false);

        if target_hit {
            Some("TARGET_HIT")
        } else if stop_hit {
            Some("STOP_HIT")
        } else if now_ms >= expires_at {
            Some("TTL_EXPIRED")
        } else {
            None
        }
    }
}

/// Tracks open shadow signals and logs their MFE/MAE outcomes when they close.
pub struct OutcomeTracker {
    active: Vec<TrackedSignal>,
}

impl OutcomeTracker {
    pub fn new() -> Self {
        Self { active: Vec::new() }
    }

    /// Push a new signal. Deduplicates: only one active signal per (strategy_id, side) pair.
    pub fn push_signal(&mut self, signal: &StrategySignal, current_price: f64) {
        if signal.action != StrategyAction::ShadowSignal {
            return;
        }
        // Skip if same strategy+side already tracked
        let duplicate = self.active.iter().any(|t| {
            t.signal.strategy_id == signal.strategy_id && t.signal.side == signal.side
        });
        if duplicate {
            return;
        }
        self.active.push(TrackedSignal::new(signal.clone(), current_price));
    }

    /// Update all tracked signals with current price and timestamp.
    /// Closes signals that hit stop/target/TTL and logs their outcome.
    pub fn update(&mut self, price: f64, now_ms: i64) {
        let mut i = 0;
        while i < self.active.len() {
            self.active[i].update_excursion(price);
            if let Some(reason) = self.active[i].close_reason(now_ms) {
                let tracked = self.active.remove(i);
                log_outcome(&tracked, reason, now_ms);
            } else {
                i += 1;
            }
        }
    }
}

#[derive(serde::Serialize)]
struct OutcomeEntry {
    symbol: String,
    created_at_ms: i64,
    closed_at_ms: i64,
    strategy: Option<String>,
    side: Option<String>,
    entry_price: Option<f64>,
    stop_price: Option<f64>,
    target_price: Option<f64>,
    score: f64,
    mfe: f64,
    mae: f64,
    mfe_r: f64,
    mae_r: f64,
    outcome: String,
}

fn log_outcome(tracked: &TrackedSignal, outcome: &str, closed_at_ms: i64) {
    let risk = tracked.risk_unit();
    let entry = OutcomeEntry {
        symbol: tracked.signal.strategy_id
            .map(|id| format!("{id:?}"))
            .unwrap_or_default(),
        created_at_ms: tracked.signal.created_at_ms,
        closed_at_ms,
        strategy: tracked.signal.strategy_id.map(|id| format!("{id:?}")),
        side: tracked.signal.side.map(|s| format!("{s:?}")),
        entry_price: tracked.signal.entry_price,
        stop_price: tracked.signal.stop_price,
        target_price: tracked.signal.target_price,
        score: tracked.signal.score,
        mfe: tracked.mfe(),
        mae: tracked.mae(),
        mfe_r: tracked.mfe() / risk,
        mae_r: tracked.mae() / risk,
        outcome: outcome.to_string(),
    };

    let path = outcomes_dir().join("strategy_outcomes.jsonl");
    if let Ok(json) = serde_json::to_string(&entry) {
        if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(&path) {
            let _ = writeln!(file, "{json}");
        }
    }
}
