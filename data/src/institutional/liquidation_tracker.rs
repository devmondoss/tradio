use std::collections::VecDeque;

use super::types::{LiqSide, LiquidationEvent, LiquidationSnapshot};

const WINDOW_MS: i64 = 5 * 60 * 1_000; // 5 minutes
const CASCADE_WINDOW_MS: i64 = 60_000;  // 60 seconds
const CASCADE_THRESHOLD_USD: f64 = 5_000_000.0; // $5M in 60s

pub struct LiquidationTracker {
    events: VecDeque<LiquidationEvent>,
}

impl LiquidationTracker {
    pub fn new() -> Self {
        Self {
            events: VecDeque::new(),
        }
    }

    pub fn push(&mut self, event: LiquidationEvent) {
        self.events.push_back(event);
    }

    /// Remove events older than the 5-minute window.
    pub fn prune(&mut self, now_ms: i64) {
        let cutoff = now_ms - WINDOW_MS;
        while self.events.front().map(|e| e.timestamp_ms < cutoff).unwrap_or(false) {
            self.events.pop_front();
        }
    }

    pub fn snapshot(&self, now_ms: i64) -> LiquidationSnapshot {
        let cutoff_5m = now_ms - WINDOW_MS;
        let cutoff_cascade = now_ms - CASCADE_WINDOW_MS;

        let mut long_usd = 0.0_f64;
        let mut short_usd = 0.0_f64;
        let mut cascade_usd = 0.0_f64;
        let mut last_ms: Option<i64> = None;

        for ev in &self.events {
            if ev.timestamp_ms >= cutoff_5m {
                match ev.side {
                    LiqSide::Longs => long_usd += ev.quantity_usd,
                    LiqSide::Shorts => short_usd += ev.quantity_usd,
                    LiqSide::Neutral => {}
                }
                if last_ms.map(|m| ev.timestamp_ms > m).unwrap_or(true) {
                    last_ms = Some(ev.timestamp_ms);
                }
            }
            if ev.timestamp_ms >= cutoff_cascade {
                cascade_usd += ev.quantity_usd;
            }
        }

        let total = long_usd + short_usd;
        let dominant_side = if total < 1.0 {
            LiqSide::Neutral
        } else if long_usd / total > 0.60 {
            LiqSide::Longs
        } else if short_usd / total > 0.60 {
            LiqSide::Shorts
        } else {
            LiqSide::Neutral
        };

        LiquidationSnapshot {
            long_liq_usd_5m: long_usd,
            short_liq_usd_5m: short_usd,
            total_usd_5m: total,
            dominant_side,
            cascade_detected: cascade_usd > CASCADE_THRESHOLD_USD,
            last_event_ms: last_ms,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ev(ts: i64, side: LiqSide, usd: f64) -> LiquidationEvent {
        LiquidationEvent { timestamp_ms: ts, side, quantity_usd: usd }
    }

    #[test]
    fn sums_5m_window_correctly() {
        let mut t = LiquidationTracker::new();
        let now = 10 * 60 * 1_000_i64; // 10 min
        t.push(ev(now - 4 * 60_000, LiqSide::Shorts, 1_000_000.0));
        t.push(ev(now - 2 * 60_000, LiqSide::Shorts, 500_000.0));
        t.push(ev(now - 6 * 60_000, LiqSide::Longs, 999_000.0)); // outside 5min
        let snap = t.snapshot(now);
        assert!((snap.short_liq_usd_5m - 1_500_000.0).abs() < 1.0);
        assert!(snap.long_liq_usd_5m < 1.0);
    }

    #[test]
    fn prune_removes_old_events() {
        let mut t = LiquidationTracker::new();
        let now = 10 * 60 * 1_000_i64;
        t.push(ev(now - 6 * 60_000, LiqSide::Shorts, 2_000_000.0));
        t.push(ev(now - 1 * 60_000, LiqSide::Shorts, 500_000.0));
        t.prune(now);
        // After prune, only the recent event should remain
        let snap = t.snapshot(now);
        assert!((snap.short_liq_usd_5m - 500_000.0).abs() < 1.0);
    }

    #[test]
    fn cascade_detected_when_5m_in_60s() {
        let mut t = LiquidationTracker::new();
        let now = 10 * 60 * 1_000_i64;
        t.push(ev(now - 30_000, LiqSide::Longs, 3_000_000.0));
        t.push(ev(now - 20_000, LiqSide::Longs, 2_500_000.0));
        let snap = t.snapshot(now);
        assert!(snap.cascade_detected);
    }

    #[test]
    fn cascade_not_detected_when_spread_over_5m() {
        let mut t = LiquidationTracker::new();
        let now = 10 * 60 * 1_000_i64;
        // $6M but spread over 5 minutes — only $1.5M in any 60s window
        for i in 0..4 {
            t.push(ev(now - (i as i64 + 1) * 70_000, LiqSide::Longs, 1_500_000.0));
        }
        let snap = t.snapshot(now);
        assert!(!snap.cascade_detected);
    }

    #[test]
    fn dominant_side_correct() {
        let mut t = LiquidationTracker::new();
        let now = 60_000_i64;
        t.push(ev(now - 10_000, LiqSide::Shorts, 2_000_000.0));
        t.push(ev(now - 20_000, LiqSide::Longs, 500_000.0));
        let snap = t.snapshot(now);
        assert_eq!(snap.dominant_side, LiqSide::Shorts);
    }
}
