use std::collections::VecDeque;

use super::types::{OiHistSnapshot, OiTrend, OiTrendDir};

const MAX_SAMPLES: usize = 30; // 150 minutes at 5-minute intervals
const SAMPLES_30M: usize = 6; // 30 minutes
const SLOPE_WINDOW: usize = 5;
const ZSCORE_WINDOW: usize = 20; // ~100 min rolling window for delta z-score

pub struct OiTracker {
    history: VecDeque<OiHistSnapshot>,
    /// Rolling bar-to-bar OI deltas (USD) for z-score computation.
    deltas: VecDeque<f64>,
}

impl OiTracker {
    pub fn new() -> Self {
        Self {
            history: VecDeque::new(),
            deltas: VecDeque::with_capacity(ZSCORE_WINDOW + 1),
        }
    }

    pub fn push(&mut self, snap: OiHistSnapshot) {
        if let Some(prev) = self.history.back() {
            let delta = snap.open_interest_usd - prev.open_interest_usd;
            self.deltas.push_back(delta);
            if self.deltas.len() > ZSCORE_WINDOW {
                self.deltas.pop_front();
            }
        }
        self.history.push_back(snap);
        if self.history.len() > MAX_SAMPLES {
            self.history.pop_front();
        }
    }

    /// Z-score of the most recent OI delta vs the rolling window.
    /// None until at least 3 deltas are available.
    pub fn delta_zscore(&self) -> Option<f64> {
        let n = self.deltas.len();
        if n < 3 {
            return None;
        }
        let last = *self.deltas.back()?;
        let mean = self.deltas.iter().sum::<f64>() / n as f64;
        let variance = self.deltas.iter().map(|d| (d - mean).powi(2)).sum::<f64>() / n as f64;
        let std = variance.sqrt();
        if std < 1.0 {
            return None; // avoid division by near-zero when OI is flat
        }
        Some((last - mean) / std)
    }

    pub fn snapshot(&self) -> OiTrend {
        let n = self.history.len();
        if n == 0 {
            return OiTrend::default();
        }

        let current = self.history.back().unwrap().open_interest_usd;

        let change_30m = if n >= SAMPLES_30M {
            let older = self.history[n - SAMPLES_30M].open_interest_usd;
            if older > 0.0 {
                (current - older) / older * 100.0
            } else {
                0.0
            }
        } else {
            0.0
        };

        let slope_5bar = if n >= SLOPE_WINDOW {
            let vals: Vec<f64> = self
                .history
                .iter()
                .rev()
                .take(SLOPE_WINDOW)
                .map(|s| s.open_interest_usd)
                .collect::<Vec<_>>()
                .into_iter()
                .rev()
                .collect();
            ols_slope(&vals).unwrap_or(0.0) / current.max(1.0)
        } else {
            0.0
        };

        let trend = match change_30m {
            c if c > 1.0 => OiTrendDir::AccumulatingFast,
            c if c > 0.3 => OiTrendDir::Accumulating,
            c if c < -1.0 => OiTrendDir::DecreasingFast,
            c if c < -0.3 => OiTrendDir::Decreasing,
            _ => OiTrendDir::Flat,
        };

        OiTrend {
            current,
            change_30m,
            slope_5bar,
            trend,
        }
    }
}

fn ols_slope(vals: &[f64]) -> Option<f64> {
    let n = vals.len() as f64;
    if n < 2.0 {
        return None;
    }
    let sum_x: f64 = (0..vals.len()).map(|i| i as f64).sum();
    let sum_y: f64 = vals.iter().sum();
    let sum_xy: f64 = vals.iter().enumerate().map(|(i, y)| i as f64 * y).sum();
    let sum_x2: f64 = (0..vals.len()).map(|i| (i * i) as f64).sum();
    let denom = n * sum_x2 - sum_x * sum_x;
    if denom.abs() < 1e-10 {
        return None;
    }
    Some((n * sum_xy - sum_x * sum_y) / denom)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn snap(oi: f64) -> OiHistSnapshot {
        OiHistSnapshot {
            timestamp_ms: 0,
            open_interest_usd: oi,
        }
    }

    #[test]
    fn accumulating_fast_when_oi_up_1pct_30m() {
        let mut t = OiTracker::new();
        // 6 samples over 30m: go from 1_000_000 to 1_015_000 (+1.5%)
        for i in 0..6 {
            t.push(snap(1_000_000.0 + i as f64 * 3_000.0));
        }
        let oi = t.snapshot();
        assert_eq!(oi.trend, OiTrendDir::AccumulatingFast);
    }

    #[test]
    fn flat_when_oi_stable() {
        let mut t = OiTracker::new();
        for _ in 0..6 {
            t.push(snap(1_000_000.0));
        }
        let oi = t.snapshot();
        assert_eq!(oi.trend, OiTrendDir::Flat);
    }

    #[test]
    fn decreasing_when_oi_falls() {
        let mut t = OiTracker::new();
        for i in 0..6 {
            t.push(snap(1_000_000.0 - i as f64 * 2_000.0));
        }
        let oi = t.snapshot();
        assert!(matches!(
            oi.trend,
            OiTrendDir::Decreasing | OiTrendDir::DecreasingFast
        ));
    }
}
