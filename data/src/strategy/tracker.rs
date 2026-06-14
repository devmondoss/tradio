use super::types::*;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::PathBuf;

fn outcomes_dir() -> PathBuf {
    let base = std::env::current_dir()
        .unwrap_or_else(|_| PathBuf::from("."))
        .join("logs");
    let _ = fs::create_dir_all(&base);
    base
}

#[derive(Debug)]
struct TrackedSignal {
    symbol: String,
    signal: StrategySignal,
    /// Highest price seen since the signal was opened
    highest: f64,
    /// Lowest price seen since the signal was opened
    lowest: f64,
    /// 1.5R price level computed at signal creation.
    tp1_price: Option<f64>,
    /// True once the 1.5R level has been touched.
    tp1_hit: bool,
}

impl TrackedSignal {
    fn new(symbol: &str, signal: StrategySignal, open_price: f64) -> Self {
        let tp1_price = match (signal.entry_price, signal.stop_price, signal.side) {
            (Some(entry), Some(stop), Some(Side::Long)) => Some(entry + 1.5 * (entry - stop).abs()),
            (Some(entry), Some(stop), Some(Side::Short)) => {
                Some(entry - 1.5 * (entry - stop).abs())
            }
            _ => None,
        };
        Self {
            symbol: symbol.to_string(),
            tp1_price,
            tp1_hit: false,
            signal,
            highest: open_price,
            lowest: open_price,
        }
    }

    fn update_excursion(&mut self, high: f64, low: f64) {
        if high > self.highest {
            self.highest = high;
        }
        if low < self.lowest {
            self.lowest = low;
        }
        if !self.tp1_hit {
            if let Some(tp1) = self.tp1_price {
                self.tp1_hit = match self.signal.side {
                    Some(Side::Long) => self.highest >= tp1,
                    Some(Side::Short) => self.lowest <= tp1,
                    None => false,
                };
            }
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

        let stop_hit = stop
            .map(|s| match self.signal.side {
                Some(Side::Long) => self.lowest <= s,
                Some(Side::Short) => self.highest >= s,
                None => false,
            })
            .unwrap_or(false);

        let target_hit = target
            .map(|t| match self.signal.side {
                Some(Side::Long) => self.highest >= t,
                Some(Side::Short) => self.lowest <= t,
                None => false,
            })
            .unwrap_or(false);

        // Conservative: if both hit in the same bar we cannot know the order — assume stop first.
        if stop_hit {
            Some("STOP_HIT")
        } else if target_hit {
            Some("TARGET_HIT")
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

impl Default for OutcomeTracker {
    fn default() -> Self {
        Self::new()
    }
}

impl OutcomeTracker {
    pub fn new() -> Self {
        Self { active: Vec::new() }
    }

    /// Push a new signal. Deduplicates: only one active signal per (strategy_id, side) pair.
    pub fn push_signal(&mut self, symbol: &str, signal: &StrategySignal, current_price: f64) {
        if signal.action != StrategyAction::ShadowSignal {
            return;
        }
        // Skip if same strategy+side already tracked
        let duplicate = self
            .active
            .iter()
            .any(|t| t.signal.strategy_id == signal.strategy_id && t.signal.side == signal.side);
        if duplicate {
            return;
        }
        self.active
            .push(TrackedSignal::new(symbol, signal.clone(), current_price));
    }

    /// Update all tracked signals with the bar's high/low and current timestamp.
    /// Closes signals that hit stop/target/TTL and logs their outcome.
    pub fn update(&mut self, high: f64, low: f64, now_ms: i64) {
        let mut i = 0;
        while i < self.active.len() {
            self.active[i].update_excursion(high, low);
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
    /// Whether price reached the 1.5R partial-exit level before final close.
    tp1_hit: bool,
    /// The 1.5R price level (None when stop was missing at signal creation).
    tp1_price: Option<f64>,
}

fn log_outcome(tracked: &TrackedSignal, outcome: &str, closed_at_ms: i64) {
    let risk = tracked.risk_unit();
    let entry = OutcomeEntry {
        symbol: tracked.symbol.clone(),
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
        tp1_hit: tracked.tp1_hit,
        tp1_price: tracked.tp1_price,
    };

    let path = outcomes_dir().join("strategy_outcomes.jsonl");
    if let Ok(json) = serde_json::to_string(&entry)
        && let Ok(mut file) = OpenOptions::new().create(true).append(true).open(&path)
    {
        let _ = writeln!(file, "{json}");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_signal(side: Side, entry: f64, stop: f64, target: f64, ttl_ms: i64) -> StrategySignal {
        StrategySignal {
            action: StrategyAction::ShadowSignal,
            strategy_id: Some(StrategyId::ValueAreaFailedAuction),
            side: Some(side),
            regime: Regime::Unknown,
            entry_price: Some(entry),
            stop_price: Some(stop),
            target_price: Some(target),
            score: 0.75,
            ttl_ms,
            evidence: vec![],
            missing: vec![],
            invalidation: vec![],
            created_at_ms: 1_000_000,
        }
    }

    #[test]
    fn hl_excursion_updates_correctly() {
        let sig = make_signal(Side::Long, 100.0, 95.0, 110.0, 300_000);
        let mut tracked = TrackedSignal::new("BTCUSDT", sig, 100.0);
        tracked.update_excursion(105.0, 98.0);
        tracked.update_excursion(102.0, 96.0);
        assert_eq!(tracked.highest, 105.0);
        assert_eq!(tracked.lowest, 96.0);
    }

    #[test]
    fn mfe_mae_long_signal() {
        let sig = make_signal(Side::Long, 100.0, 95.0, 110.0, 300_000);
        let mut tracked = TrackedSignal::new("BTCUSDT", sig, 100.0);
        tracked.update_excursion(108.0, 97.0);
        // MFE = highest - entry = 108 - 100 = 8
        assert!((tracked.mfe() - 8.0).abs() < 1e-9);
        // MAE = entry - lowest = 100 - 97 = 3
        assert!((tracked.mae() - 3.0).abs() < 1e-9);
    }

    #[test]
    fn mfe_mae_short_signal() {
        let sig = make_signal(Side::Short, 100.0, 105.0, 90.0, 300_000);
        let mut tracked = TrackedSignal::new("BTCUSDT", sig, 100.0);
        tracked.update_excursion(103.0, 92.0);
        // MFE = entry - lowest = 100 - 92 = 8
        assert!((tracked.mfe() - 8.0).abs() < 1e-9);
        // MAE = highest - entry = 103 - 100 = 3
        assert!((tracked.mae() - 3.0).abs() < 1e-9);
    }

    #[test]
    fn conservative_stop_before_target_same_bar() {
        // Long: bar low touches stop AND bar high touches target in same bar.
        // Should be STOP_HIT (conservative assumption).
        let sig = make_signal(Side::Long, 100.0, 95.0, 110.0, 300_000);
        let mut tracked = TrackedSignal::new("BTCUSDT", sig, 100.0);
        // Both stop (low=94) and target (high=111) hit this bar
        tracked.update_excursion(111.0, 94.0);
        assert_eq!(tracked.close_reason(1_100_000), Some("STOP_HIT"));
    }

    #[test]
    fn target_only_hit() {
        let sig = make_signal(Side::Long, 100.0, 95.0, 110.0, 300_000);
        let mut tracked = TrackedSignal::new("BTCUSDT", sig, 100.0);
        tracked.update_excursion(112.0, 98.0); // high hits target, low safe
        assert_eq!(tracked.close_reason(1_100_000), Some("TARGET_HIT"));
    }

    #[test]
    fn stop_only_hit() {
        let sig = make_signal(Side::Long, 100.0, 95.0, 110.0, 300_000);
        let mut tracked = TrackedSignal::new("BTCUSDT", sig, 100.0);
        tracked.update_excursion(104.0, 94.0); // low hits stop, high safe
        assert_eq!(tracked.close_reason(1_100_000), Some("STOP_HIT"));
    }

    #[test]
    fn ttl_expiry() {
        let sig = make_signal(Side::Long, 100.0, 95.0, 110.0, 60_000);
        let mut tracked = TrackedSignal::new("BTCUSDT", sig, 100.0);
        tracked.update_excursion(102.0, 99.0);
        // created_at=1_000_000, ttl=60_000, expires=1_060_000
        assert_eq!(tracked.close_reason(1_059_999), None);
        assert_eq!(tracked.close_reason(1_060_000), Some("TTL_EXPIRED"));
    }

    #[test]
    fn deduplication_by_strategy_side() {
        let mut tracker = OutcomeTracker::new();
        let sig = make_signal(Side::Long, 100.0, 95.0, 110.0, 300_000);
        tracker.push_signal("BTCUSDT", &sig, 100.0);
        tracker.push_signal("BTCUSDT", &sig, 101.0); // duplicate
        assert_eq!(tracker.active.len(), 1);
    }

    #[test]
    fn symbol_stored_correctly() {
        let mut tracker = OutcomeTracker::new();
        let sig = make_signal(Side::Long, 100.0, 95.0, 110.0, 300_000);
        tracker.push_signal("ETHUSDT", &sig, 100.0);
        assert_eq!(tracker.active[0].symbol, "ETHUSDT");
    }
}
