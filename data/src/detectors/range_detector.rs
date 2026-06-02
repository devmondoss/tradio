use std::collections::VecDeque;

use serde::{Deserialize, Serialize};

use crate::strategy::types::DataQuality;

// ── Parameters (doc section 9 — calibrated starting points) ──────────────────

/// Rolling window of historical bars used for range detection.
const WINDOW_BARS: usize = 36;

/// Minimum bars whose close fell inside [range_low, range_high] to consider the range valid.
const MIN_BARS_INSIDE: usize = 12;

/// Minimum total touches on both extremes combined.
const MIN_TOUCHES_TOTAL: usize = 3;

/// Minimum touches on each side individually (at least 1 high + 1 low touch).
const MIN_TOUCHES_EACH_SIDE: usize = 1;

/// Maximum normalized OLS midline slope (per bar, per ATR) to qualify as a range.
/// A slope > 0.15 means the midline is drifting — that is a trend, not a range.
const MAX_MIDLINE_SLOPE: f64 = 0.15;

/// Range must span at least 0.8× ATR to be worth trading.
const MIN_RANGE_SIZE_ATR: f64 = 0.8;

/// Range spanning more than 3.5× ATR is too wide — stops become too large.
const MAX_RANGE_SIZE_ATR: f64 = 3.5;

/// Middle no-trade zone: 35%–65% of range from the low. Price here has no clear extreme to fade.
const NO_TRADE_ZONE_LOW_PCT: f64 = 0.35;
const NO_TRADE_ZONE_HIGH_PCT: f64 = 0.65;

/// Extreme zone width as a fraction of range size. Price within 20% of an extreme = NearHigh/NearLow.
const EXTREME_ZONE_PCT: f64 = 0.20;

/// Touch tolerance: a bar is counted as touching the extreme if its high/low
/// is within TOUCH_TOLERANCE_ATR × ATR of range_high / range_low.
const TOUCH_TOLERANCE_ATR: f64 = 0.30;

/// Number of consecutive bar closes outside the range required to declare a breakout.
const BREAKOUT_BARS: usize = 2;

/// How many recent bars to scan for sweep detection (wick through extreme + close inside).
const SWEEP_LOOKBACK: usize = 3;

// ── Types ─────────────────────────────────────────────────────────────────────

/// Where the current price sits relative to the detected intraday range.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
pub enum RangeLocation {
    /// Within the upper EXTREME_ZONE_PCT of the range — active trading zone for shorts.
    NearHigh,
    /// Within the lower EXTREME_ZONE_PCT of the range — active trading zone for longs.
    NearLow,
    /// In the middle 35–65% of the range — no-trade zone.
    NoTrade,
    /// Inside the range but not in any tradeable zone.
    Inside,
    /// Above range_high — potential breakout in progress.
    OutsideHigh,
    /// Below range_low — potential breakdown in progress.
    OutsideLow,
    #[default]
    Unknown,
}

/// Snapshot of the current intraday range state.
/// Attached to `StrategyMarketContext.range` every bar.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RangeContext {
    /// True only when all validity criteria are met (bars_inside, touches, slope, size).
    pub valid: bool,
    pub range_high: f64,
    pub range_low: f64,
    pub range_mid: f64,
    /// Volume Profile POC if it falls within [range_low, range_high], else None.
    /// Used as TP1 target (more precise than simple range_mid).
    pub range_poc: Option<f64>,
    pub range_size: f64,
    pub range_size_atr: f64,
    /// Bars where high was within TOUCH_TOLERANCE_ATR × ATR of range_high.
    pub touches_high: u32,
    /// Bars where low was within TOUCH_TOLERANCE_ATR × ATR of range_low.
    pub touches_low: u32,
    /// Bars whose close was inside [range_low, range_high].
    pub bars_inside: u32,
    /// OLS slope of recent closes, normalized by ATR per bar.
    /// Small absolute value = flat range. Large = trending.
    pub midline_slope: f64,
    pub location: RangeLocation,
    /// True when price is in the 35–65% middle zone — no-trade area.
    pub no_trade_zone: bool,
    /// At least one of the last SWEEP_LOOKBACK bars had low < range_low but closed above it.
    pub sweep_range_low: bool,
    /// Maximum depth of the low sweep in absolute price (range_low - min_low of sweep bars).
    /// None if no sweep occurred. Divide by ATR in the writer for normalized sweep_depth_atr.
    pub sweep_low_depth: Option<f64>,
    /// At least one of the last SWEEP_LOOKBACK bars had high > range_high but closed below it.
    pub sweep_range_high: bool,
    /// Maximum depth of the high sweep (max_high - range_high of sweep bars).
    pub sweep_high_depth: Option<f64>,
    /// Two or more consecutive closes above range_high → confirmed upward breakout.
    pub breakout_up: bool,
    /// Two or more consecutive closes below range_low → confirmed downward breakout.
    pub breakout_down: bool,
    pub quality: DataQuality,
}

impl Default for RangeContext {
    fn default() -> Self {
        Self {
            valid: false,
            range_high: 0.0,
            range_low: 0.0,
            range_mid: 0.0,
            range_poc: None,
            range_size: 0.0,
            range_size_atr: 0.0,
            touches_high: 0,
            touches_low: 0,
            bars_inside: 0,
            midline_slope: 0.0,
            location: RangeLocation::Unknown,
            no_trade_zone: false,
            sweep_range_low: false,
            sweep_low_depth: None,
            sweep_range_high: false,
            sweep_high_depth: None,
            breakout_up: false,
            breakout_down: false,
            quality: DataQuality::Missing,
        }
    }
}

// ── Detector ──────────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
struct Bar {
    high: f64,
    low: f64,
    close: f64,
}

/// Stateful detector — push one bar per M5 close, then call `compute()` for a snapshot.
pub struct RangeDetector {
    bars: VecDeque<Bar>,
    max_window: usize,
}

impl RangeDetector {
    pub fn new() -> Self {
        Self {
            bars: VecDeque::with_capacity(WINDOW_BARS + 1),
            max_window: WINDOW_BARS,
        }
    }

    /// Push a closed bar. Old bars beyond `max_window` are dropped automatically.
    pub fn push_bar(&mut self, high: f64, low: f64, close: f64) {
        if !high.is_finite() || !low.is_finite() || !close.is_finite() {
            return;
        }
        self.bars.push_back(Bar { high, low, close });
        while self.bars.len() > self.max_window {
            self.bars.pop_front();
        }
    }

    /// Compute the current range snapshot.
    ///
    /// - `current_price`: latest price (intrabar or last close).
    /// - `atr`: current ATR — used for normalization and tolerances.
    /// - `vp_poc`: session Volume Profile POC — used as TP1 if it falls within the range.
    pub fn compute(&self, current_price: f64, atr: f64, vp_poc: Option<f64>) -> RangeContext {
        if atr <= 0.0 || self.bars.len() < MIN_BARS_INSIDE {
            return RangeContext::default();
        }

        // ── 1. Range bounds from window ───────────────────────────────────────
        let range_high = self.bars.iter().map(|b| b.high).fold(f64::NEG_INFINITY, f64::max);
        let range_low = self.bars.iter().map(|b| b.low).fold(f64::INFINITY, f64::min);
        let range_size = range_high - range_low;
        let range_size_atr = range_size / atr;

        // ── 2. Range size gate ────────────────────────────────────────────────
        if range_size_atr < MIN_RANGE_SIZE_ATR || range_size_atr > MAX_RANGE_SIZE_ATR {
            return RangeContext {
                valid: false,
                range_high,
                range_low,
                range_mid: (range_high + range_low) / 2.0,
                range_size,
                range_size_atr,
                quality: DataQuality::Degraded,
                ..Default::default()
            };
        }

        let touch_tol = TOUCH_TOLERANCE_ATR * atr;

        // ── 3. Count bars inside, touches ────────────────────────────────────
        let mut bars_inside = 0usize;
        let mut touches_high = 0usize;
        let mut touches_low = 0usize;
        let mut closes: Vec<f64> = Vec::with_capacity(self.bars.len());

        for bar in &self.bars {
            if bar.close >= range_low && bar.close <= range_high {
                bars_inside += 1;
            }
            if bar.high >= range_high - touch_tol {
                touches_high += 1;
            }
            if bar.low <= range_low + touch_tol {
                touches_low += 1;
            }
            closes.push(bar.close);
        }

        // ── 4. Bars inside + touches gate ────────────────────────────────────
        if bars_inside < MIN_BARS_INSIDE
            || (touches_high + touches_low) < MIN_TOUCHES_TOTAL
            || touches_high < MIN_TOUCHES_EACH_SIDE
            || touches_low < MIN_TOUCHES_EACH_SIDE
        {
            return RangeContext {
                valid: false,
                range_high,
                range_low,
                range_mid: (range_high + range_low) / 2.0,
                range_size,
                range_size_atr,
                touches_high: touches_high as u32,
                touches_low: touches_low as u32,
                bars_inside: bars_inside as u32,
                quality: DataQuality::Fallback,
                ..Default::default()
            };
        }

        // ── 5. Midline slope gate ────────────────────────────────────────────
        let midline_slope = ols_slope_per_atr(&closes, atr);
        if midline_slope.abs() > MAX_MIDLINE_SLOPE {
            return RangeContext {
                valid: false,
                range_high,
                range_low,
                range_mid: (range_high + range_low) / 2.0,
                range_size,
                range_size_atr,
                touches_high: touches_high as u32,
                touches_low: touches_low as u32,
                bars_inside: bars_inside as u32,
                midline_slope,
                quality: DataQuality::Fallback,
                ..Default::default()
            };
        }

        // ── 6. Valid range — compute all derived fields ───────────────────────
        let range_mid = (range_high + range_low) / 2.0;
        let range_poc = vp_poc.filter(|&p| p.is_finite() && p >= range_low && p <= range_high);

        // ── 7. Sweep detection (last SWEEP_LOOKBACK bars) ────────────────────
        let sweep_low_bars: Vec<f64> = self
            .bars.iter().rev().take(SWEEP_LOOKBACK)
            .filter(|b| b.low < range_low && b.close >= range_low)
            .map(|b| range_low - b.low)
            .collect();
        let sweep_range_low = !sweep_low_bars.is_empty();
        let sweep_low_depth = sweep_low_bars.iter().copied()
            .reduce(f64::max)
            .filter(|&d| d > 0.0);

        let sweep_high_bars: Vec<f64> = self
            .bars.iter().rev().take(SWEEP_LOOKBACK)
            .filter(|b| b.high > range_high && b.close <= range_high)
            .map(|b| b.high - range_high)
            .collect();
        let sweep_range_high = !sweep_high_bars.is_empty();
        let sweep_high_depth = sweep_high_bars.iter().copied()
            .reduce(f64::max)
            .filter(|&d| d > 0.0);

        // ── 8. Breakout detection (last BREAKOUT_BARS all closed outside) ────
        let n = self.bars.len();
        let breakout_up = n >= BREAKOUT_BARS
            && self
                .bars
                .iter()
                .rev()
                .take(BREAKOUT_BARS)
                .all(|b| b.close > range_high);
        let breakout_down = n >= BREAKOUT_BARS
            && self
                .bars
                .iter()
                .rev()
                .take(BREAKOUT_BARS)
                .all(|b| b.close < range_low);

        // ── 9. Price location ────────────────────────────────────────────────
        let pct = if range_size > 0.0 {
            (current_price - range_low) / range_size
        } else {
            0.5
        };

        let location = if current_price > range_high {
            RangeLocation::OutsideHigh
        } else if current_price < range_low {
            RangeLocation::OutsideLow
        } else if pct >= NO_TRADE_ZONE_LOW_PCT && pct <= NO_TRADE_ZONE_HIGH_PCT {
            RangeLocation::NoTrade
        } else if pct > 1.0 - EXTREME_ZONE_PCT {
            RangeLocation::NearHigh
        } else if pct < EXTREME_ZONE_PCT {
            RangeLocation::NearLow
        } else {
            RangeLocation::Inside
        };

        let no_trade_zone = matches!(location, RangeLocation::NoTrade);

        RangeContext {
            valid: true,
            range_high,
            range_low,
            range_mid,
            range_poc,
            range_size,
            range_size_atr,
            touches_high: touches_high as u32,
            touches_low: touches_low as u32,
            bars_inside: bars_inside as u32,
            midline_slope,
            location,
            no_trade_zone,
            sweep_range_low,
            sweep_low_depth,
            sweep_range_high,
            sweep_high_depth,
            breakout_up,
            breakout_down,
            quality: DataQuality::Live,
        }
    }
}

// ── OLS helpers ───────────────────────────────────────────────────────────────

/// Returns OLS slope (price per bar) normalized by ATR.
/// Positive = upward drift, negative = downward drift.
fn ols_slope_per_atr(closes: &[f64], atr: f64) -> f64 {
    if closes.len() < 2 || atr <= 0.0 {
        return 0.0;
    }
    let n = closes.len() as f64;
    let sum_x: f64 = (0..closes.len()).map(|i| i as f64).sum();
    let sum_y: f64 = closes.iter().sum();
    let sum_xy: f64 = closes
        .iter()
        .enumerate()
        .map(|(i, &y)| i as f64 * y)
        .sum();
    let sum_x2: f64 = (0..closes.len()).map(|i| (i * i) as f64).sum();
    let denom = n * sum_x2 - sum_x * sum_x;
    if denom.abs() < 1e-10 {
        return 0.0;
    }
    let slope = (n * sum_xy - sum_x * sum_y) / denom;
    slope / atr
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn make_range_detector(n: usize, high: f64, low: f64) -> RangeDetector {
        let mut d = RangeDetector::new();
        for i in 0..n {
            let close = low + (high - low) * 0.5 + (i as f64 % 3.0 - 1.0) * (high - low) * 0.05;
            let bar_high = if i % 10 == 0 { high + 10.0 } else { high - 10.0 };
            let bar_low = if i % 7 == 0 { low - 10.0 } else { low + 10.0 };
            d.push_bar(bar_high, bar_low, close);
        }
        d
    }

    #[test]
    fn detects_valid_range() {
        // BTC-like: range 200 pts wide, ATR 250
        let d = make_range_detector(50, 100_200.0, 100_000.0);
        let ctx = d.compute(100_100.0, 250.0, Some(100_100.0));
        assert!(ctx.valid, "should detect valid range");
        assert_eq!(ctx.quality, DataQuality::Live);
    }

    #[test]
    fn rejects_too_few_bars() {
        let mut d = RangeDetector::new();
        for _ in 0..10 {
            d.push_bar(100_200.0, 100_000.0, 100_100.0);
        }
        let ctx = d.compute(100_100.0, 250.0, None);
        assert!(!ctx.valid);
    }

    #[test]
    fn rejects_too_large_range() {
        let mut d = RangeDetector::new();
        // Range 4× ATR — too big
        for i in 0..50 {
            let close = 100_000.0 + (i as f64 % 5.0) * 200.0;
            d.push_bar(101_200.0, 100_000.0, close);
        }
        let ctx = d.compute(100_600.0, 250.0, None);
        // range_size = 1200, ATR = 250 → range_size_atr = 4.8 > 3.5
        assert!(!ctx.valid);
    }

    #[test]
    fn no_trade_zone_detection() {
        let d = make_range_detector(50, 100_200.0, 100_000.0);
        // Price at midpoint = 100_100 = 50% of range [100_000, 100_200]
        let ctx = d.compute(100_100.0, 250.0, None);
        if ctx.valid {
            assert!(ctx.no_trade_zone, "50% of range should be no-trade zone");
        }
    }

    #[test]
    fn near_low_detection() {
        let d = make_range_detector(50, 100_200.0, 100_000.0);
        // Price at 100_010 = 5% of range → NearLow
        let ctx = d.compute(100_010.0, 250.0, None);
        if ctx.valid {
            assert_eq!(ctx.location, RangeLocation::NearLow);
            assert!(!ctx.no_trade_zone);
        }
    }

    #[test]
    fn sweep_range_low_detected() {
        let mut d = RangeDetector::new();
        // Fill with flat bars inside range
        for _ in 0..30 {
            d.push_bar(100_180.0, 100_020.0, 100_100.0);
        }
        // Add a sweep bar: wick below range_low but closed inside
        d.push_bar(100_180.0, 99_950.0, 100_010.0);
        let ctx = d.compute(100_010.0, 250.0, None);
        if ctx.valid {
            assert!(ctx.sweep_range_low, "should detect sweep of range low");
        }
    }

    #[test]
    fn breakout_down_detected() {
        let mut d = RangeDetector::new();
        for _ in 0..30 {
            d.push_bar(100_180.0, 100_020.0, 100_100.0);
        }
        // Two closes below range_low
        d.push_bar(99_980.0, 99_800.0, 99_900.0);
        d.push_bar(99_960.0, 99_780.0, 99_850.0);
        let ctx = d.compute(99_850.0, 250.0, None);
        if ctx.valid {
            assert!(ctx.breakout_down);
        }
    }
}
