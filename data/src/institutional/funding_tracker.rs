use super::types::{FundingContext, FundingRateSample, FundingRegime};

const MAX_SAMPLES: usize = 21; // ~7 days at 8h intervals

pub struct FundingTracker {
    samples: Vec<FundingRateSample>,
}

impl FundingTracker {
    pub fn new() -> Self {
        Self { samples: Vec::new() }
    }

    pub fn load(&mut self, samples: Vec<FundingRateSample>) {
        let mut s = samples;
        s.sort_by_key(|r| r.timestamp_ms);
        s.truncate(MAX_SAMPLES);
        self.samples = s;
    }

    /// Appends a single new sample, keeping the window at MAX_SAMPLES.
    pub fn push(&mut self, sample: FundingRateSample) {
        self.samples.push(sample);
        self.samples.sort_by_key(|r| r.timestamp_ms);
        if self.samples.len() > MAX_SAMPLES {
            self.samples.drain(..self.samples.len() - MAX_SAMPLES);
        }
    }

    pub fn snapshot(&self) -> FundingContext {
        if self.samples.is_empty() {
            return FundingContext::default();
        }

        let current = self.samples.last().unwrap().rate;
        let avg = self.samples.iter().map(|s| s.rate).sum::<f64>() / self.samples.len() as f64;

        // Percentile: fraction of samples below current (simple rank-based)
        let n = self.samples.len() as f64;
        let rank = self.samples.iter().filter(|s| s.rate < current).count() as f64;
        let percentile = rank / n * 100.0;

        // Absolute floor: if current rate is ≥30% above rolling average, classify
        // as ElevatedLong regardless of percentile. Prevents the percentile window
        // from staying Neutral when the whole window shifts upward together.
        let above_avg = avg > 0.0 && current >= avg * 1.3;
        let regime = match (current, percentile) {
            (r, p) if r > 0.0 && p > 85.0 => FundingRegime::ExtremeLong,
            (r, p) if r > 0.0 && (p > 65.0 || above_avg) => {
                // Percentile gate OR absolute-vs-avg gate
                let _ = p;
                FundingRegime::ElevatedLong
            }
            (r, p) if r < 0.0 && p < 15.0 => FundingRegime::ExtremeShort,
            (r, p) if r < 0.0 && p < 35.0 => FundingRegime::ElevatedShort,
            _ => FundingRegime::Neutral,
        };

        // Velocity: change across the last 3 samples (most recent - 3rd-to-last).
        let n = self.samples.len();
        let velocity = if n >= 3 {
            self.samples[n - 1].rate - self.samples[n - 3].rate
        } else {
            0.0
        };

        // Peak confirmed: funding reached an extreme (positive or negative) in the last
        // 6 samples and has since retreated ≥ 10% of that peak value.
        let peak_confirmed = {
            let window = if n > 6 { &self.samples[n - 6..] } else { &self.samples[..] };
            let peak_pos = window.iter().map(|s| s.rate).fold(f64::NEG_INFINITY, f64::max);
            let peak_neg = window.iter().map(|s| s.rate).fold(f64::INFINITY, f64::min);
            let retreat_threshold = 0.10; // 10% retreat from peak
            let pos_peak_retreated = peak_pos > 0.0
                && current < peak_pos * (1.0 - retreat_threshold);
            let neg_peak_retreated = peak_neg < 0.0
                && current > peak_neg * (1.0 - retreat_threshold);
            pos_peak_retreated || neg_peak_retreated
        };

        FundingContext { current, avg, regime, velocity, peak_confirmed }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample(rate: f64) -> FundingRateSample {
        FundingRateSample { timestamp_ms: 0, rate }
    }

    #[test]
    fn extreme_long_when_high_percentile() {
        let mut t = FundingTracker::new();
        // 20 samples with one outlier at 0.001 (highest)
        let mut samples: Vec<_> = (0..20).map(|_| sample(0.0001)).collect();
        samples.push(sample(0.001)); // current (highest)
        t.load(samples);
        let ctx = t.snapshot();
        assert_eq!(ctx.regime, FundingRegime::ExtremeLong);
        assert!((ctx.current - 0.001).abs() < 1e-9);
    }

    #[test]
    fn elevated_long_when_30pct_above_avg_despite_low_percentile() {
        // Scenario: old bull-run window (10e-5) keeps avg high; recent recovery (3-4e-5)
        // then a new rise to 8e-5. Percentile = 57% (< 65%) but current = 1.33× avg.
        // The absolute-vs-avg gate should classify as ElevatedLong.
        let mut t = FundingTracker::new();
        let mut samples: Vec<_> = (0..8).map(|_| sample(0.00010)).collect(); // old high
        samples.extend((0..10).map(|_| sample(0.00003)));                     // recovery
        samples.extend((0..2).map(|_| sample(0.00004)));                      // rising
        samples.push(sample(0.00008));                                         // current
        // avg = (8×10e-5 + 10×3e-5 + 2×4e-5 + 8e-5) / 21 = 6e-5
        // current/avg = 8e-5/6e-5 = 1.33 > 1.3 → ElevatedLong despite percentile=57%
        t.load(samples);
        let ctx = t.snapshot();
        assert_eq!(ctx.regime, FundingRegime::ElevatedLong, "30%+ above avg should be ElevatedLong");
    }

    #[test]
    fn neutral_when_at_median() {
        let mut t = FundingTracker::new();
        let samples: Vec<_> = (0..10)
            .map(|i| sample(if i < 5 { -0.0001 } else { 0.0001 }))
            .collect();
        t.load(samples);
        let ctx = t.snapshot();
        assert_eq!(ctx.regime, FundingRegime::Neutral);
    }
}
