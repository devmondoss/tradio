use std::collections::VecDeque;

use super::types::{LiqSide, LiquidationEvent, LiquidationSnapshot};

const WINDOW_MS: i64 = 5 * 60 * 1_000; // 5 minutes
const CASCADE_WINDOW_MS: i64 = 60_000; // 60 seconds
const CASCADE_THRESHOLD_USD: f64 = 5_000_000.0; // $5M in 60s
const BAR_HISTORY: usize = 20; // rolling window for z-score (20 bars = 100 min at M5)

pub struct LiquidationTracker {
    events: VecDeque<LiquidationEvent>,
    /// Total USD liquidated per bar-end snapshot — used for z-score normalization.
    bar_totals: VecDeque<f64>,
}

impl LiquidationTracker {
    pub fn new() -> Self {
        Self {
            events: VecDeque::new(),
            bar_totals: VecDeque::with_capacity(BAR_HISTORY + 1),
        }
    }

    /// Pre-seed bar_totals with historical baseline values so z-score is available
    /// from bar 1 on startup. Binance does not expose historical liquidation data via
    /// REST, so we use a realistic quiet-market distribution (M5 BTC, ~$20K–$500K range).
    /// Real data displaces seed values over time as bars accumulate.
    pub fn seed_bar_history(&mut self) {
        // 20 samples spanning quiet ($20K), moderate ($100K), and one active spike ($500K).
        // Mean ≈ $78K, std ≈ $105K → z-score for a $5M crash ≈ 46, for $500K bar ≈ 4.0.
        const SEED: [f64; 20] = [
            30_000.0, 45_000.0, 20_000.0, 80_000.0, 35_000.0, 60_000.0, 25_000.0, 150_000.0,
            40_000.0, 30_000.0, 55_000.0, 70_000.0, 28_000.0, 90_000.0, 35_000.0, 500_000.0,
            45_000.0, 30_000.0, 65_000.0, 40_000.0,
        ];
        for v in SEED {
            self.bar_totals.push_back(v);
        }
    }

    /// Record the total USD liquidated at bar close. Call once per bar after `snapshot()`.
    pub fn record_bar_total(&mut self, total_usd: f64) {
        self.bar_totals.push_back(total_usd);
        if self.bar_totals.len() > BAR_HISTORY {
            self.bar_totals.pop_front();
        }
    }

    /// Z-score of `value` vs the bar_totals rolling distribution. None until ≥3 samples.
    fn zscore(&self, value: f64) -> Option<f64> {
        let n = self.bar_totals.len();
        if n < 3 {
            return None;
        }
        let mean = self.bar_totals.iter().sum::<f64>() / n as f64;
        let variance = self
            .bar_totals
            .iter()
            .map(|x| (x - mean).powi(2))
            .sum::<f64>()
            / n as f64;
        let std = variance.sqrt();
        if std < 1.0 {
            return None;
        }
        Some((value - mean) / std)
    }

    pub fn push(&mut self, event: LiquidationEvent) {
        self.events.push_back(event);
    }

    /// Remove events older than the 5-minute window.
    pub fn prune(&mut self, now_ms: i64) {
        let cutoff = now_ms - WINDOW_MS;
        while self
            .events
            .front()
            .map(|e| e.timestamp_ms < cutoff)
            .unwrap_or(false)
        {
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
            total_zscore: self.zscore(total),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ev(ts: i64, side: LiqSide, usd: f64) -> LiquidationEvent {
        LiquidationEvent {
            timestamp_ms: ts,
            side,
            quantity_usd: usd,
        }
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
            t.push(ev(
                now - (i as i64 + 1) * 70_000,
                LiqSide::Longs,
                1_500_000.0,
            ));
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
