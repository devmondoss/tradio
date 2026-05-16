use std::collections::VecDeque;

use super::types::{DivergenceSignal, LongShortSnapshot, LsRatioContext, LsSource};

const MAX_SAMPLES: usize = 6; // 30 minutes at 5-minute intervals

pub struct LsRatioTracker {
    top_position: VecDeque<LongShortSnapshot>,
    global: VecDeque<LongShortSnapshot>,
}

impl LsRatioTracker {
    pub fn new() -> Self {
        Self {
            top_position: VecDeque::new(),
            global: VecDeque::new(),
        }
    }

    pub fn push(&mut self, snap: LongShortSnapshot) {
        let queue = match snap.source {
            LsSource::TopTraderPosition => &mut self.top_position,
            LsSource::GlobalAccount => &mut self.global,
        };
        queue.push_back(snap);
        if queue.len() > MAX_SAMPLES {
            queue.pop_front();
        }
    }

    pub fn snapshot(&self) -> LsRatioContext {
        let top_long = self
            .top_position
            .back()
            .map(|s| s.long_ratio)
            .unwrap_or(0.5);
        let retail_long = self.global.back().map(|s| s.long_ratio).unwrap_or(0.5);

        let divergence = retail_long - top_long;
        let divergence_signal = if divergence > 0.15 {
            // Retail more long than smart money
            DivergenceSignal::SmartShortRetailLong
        } else if divergence < -0.15 {
            // Smart money more long than retail
            DivergenceSignal::SmartLongRetailShort
        } else if (top_long - 0.5).abs() < 0.10 && (retail_long - 0.5).abs() < 0.10 {
            DivergenceSignal::Neutral
        } else {
            DivergenceSignal::Aligned
        };

        LsRatioContext {
            top_traders_long_pct: top_long,
            retail_long_pct: retail_long,
            divergence_signal,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn snap(source: LsSource, long_ratio: f64) -> LongShortSnapshot {
        LongShortSnapshot {
            timestamp_ms: 0,
            long_ratio,
            short_ratio: 1.0 - long_ratio,
            ls_ratio: long_ratio / (1.0 - long_ratio),
            source,
        }
    }

    #[test]
    fn detects_smart_short_retail_long() {
        let mut t = LsRatioTracker::new();
        t.push(snap(LsSource::TopTraderPosition, 0.38)); // smart money 38% long
        t.push(snap(LsSource::GlobalAccount, 0.65));     // retail 65% long
        let ctx = t.snapshot();
        assert_eq!(ctx.divergence_signal, DivergenceSignal::SmartShortRetailLong);
    }

    #[test]
    fn detects_smart_long_retail_short() {
        let mut t = LsRatioTracker::new();
        t.push(snap(LsSource::TopTraderPosition, 0.70));
        t.push(snap(LsSource::GlobalAccount, 0.40));
        let ctx = t.snapshot();
        assert_eq!(ctx.divergence_signal, DivergenceSignal::SmartLongRetailShort);
    }

    #[test]
    fn neutral_when_no_data() {
        let t = LsRatioTracker::new();
        let ctx = t.snapshot();
        assert_eq!(ctx.divergence_signal, DivergenceSignal::Neutral);
    }
}
