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

        let regime = match (current, percentile) {
            (r, p) if r > 0.0 && p > 85.0 => FundingRegime::ExtremeLong,
            (r, p) if r > 0.0 && p > 65.0 => FundingRegime::ElevatedLong,
            (r, p) if r < 0.0 && p < 15.0 => FundingRegime::ExtremeShort,
            (r, p) if r < 0.0 && p < 35.0 => FundingRegime::ElevatedShort,
            _ => FundingRegime::Neutral,
        };

        FundingContext { current, avg, regime }
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
