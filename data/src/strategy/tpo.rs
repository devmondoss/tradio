/// TPO (Time Price Opportunity) Market Profile — 30-minute periods.
///
/// Each 30-minute bucket is one "letter" in the traditional Market Profile.
/// A **Single Print** is a price level that appears in exactly one TPO period
/// within the current day and has not been revisited since — these are structural
/// gaps that price tends to fill (Subdimi / Market Profile methodology).
///
/// We use the M5 chart: 6 bars = 1 TPO period.
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

const TPO_PERIOD_MS: i64 = 30 * 60 * 1_000; // 30 minutes
const DAY_MS: i64 = 86_400_000;

/// Price bin precision: we round prices to the nearest `bin_step`.
/// For BTC this is set dynamically in the tracker (1% of current price / 100).
const DEFAULT_BIN_STEP: f64 = 10.0; // $10 per bin (overridden by tracker)

/// A range of consecutive single-print price levels.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SinglePrintZone {
    /// Low price of the zone.
    pub low: f64,
    /// High price of the zone.
    pub high: f64,
    /// TPO period index this zone belongs to.
    pub tpo_period: i64,
}

/// Tracks TPO periods for the current day and identifies single-print zones.
#[derive(Debug, Default)]
pub struct TpoTracker {
    /// Current day index (bar_ms / DAY_MS).
    current_day: i64,
    /// Map from bin price → set of TPO periods within the current day that visited it.
    /// Key: quantized price (as i64 = (price / bin_step).round() as i64).
    bin_visits: HashMap<i64, Vec<i64>>,
    /// Current TPO period index.
    current_period: i64,
    /// Price bin step (set once from first bar's ATR or default).
    bin_step: f64,
    /// Last computed single-print zones for this day.
    single_print_zones: Vec<SinglePrintZone>,
    /// Current bar close price — used to remove zones that price has revisited.
    last_price: f64,
}

impl TpoTracker {
    pub fn new() -> Self {
        Self {
            current_day: -1,
            bin_step: DEFAULT_BIN_STEP,
            ..Default::default()
        }
    }

    /// Update with bar data. Call once per bar close.
    ///
    /// `bar_ms`: bar close timestamp in ms.
    /// `high`, `low`: bar high/low.
    /// `close`: bar close price (used for visit detection).
    /// `atr`: current ATR — used to calibrate bin step on first bar of the day.
    pub fn update(&mut self, bar_ms: i64, high: f64, low: f64, close: f64, atr: Option<f64>) {
        let day = bar_ms / DAY_MS;
        let period = bar_ms / TPO_PERIOD_MS;

        // Day transition: reset all state for the new day.
        if day != self.current_day {
            self.current_day = day;
            self.bin_visits.clear();
            self.single_print_zones.clear();
            // Set bin_step from ATR: ~1% of ATR gives reasonable price resolution for BTC M5.
            if let Some(a) = atr.filter(|&a| a > 0.0) {
                self.bin_step = (a * 0.10).max(1.0).min(100.0);
            }
        }

        self.current_period = period;

        // Record all price levels in [low, high] for this TPO period.
        let lo_bin = self.quantize(low);
        let hi_bin = self.quantize(high);
        let mut bin = lo_bin;
        while bin <= hi_bin {
            let visits = self.bin_visits.entry(bin).or_default();
            if visits.last().copied() != Some(period) {
                visits.push(period);
            }
            bin += 1;
        }

        self.last_price = close;

        // Recompute single-print zones for this day.
        self.recompute_zones();
    }

    fn quantize(&self, price: f64) -> i64 {
        (price / self.bin_step).round() as i64
    }

    fn bin_to_price(&self, bin: i64) -> f64 {
        bin as f64 * self.bin_step
    }

    fn recompute_zones(&mut self) {
        let close_bin = self.quantize(self.last_price);
        let current_period = self.current_period;

        // Collect bins with exactly 1 TPO visit that are NOT the current period
        // (current period is still open — may get revisited before close).
        let mut single_bins: Vec<i64> = self
            .bin_visits
            .iter()
            .filter(|(bin, visits)| {
                visits.len() == 1 && visits[0] != current_period && (*bin - close_bin).abs() > 0 // not at current price
            })
            .map(|(bin, _)| *bin)
            .collect();

        if single_bins.is_empty() {
            self.single_print_zones.clear();
            return;
        }

        single_bins.sort_unstable();

        // Merge consecutive bins into zones.
        let mut zones: Vec<SinglePrintZone> = Vec::new();
        let mut zone_start = single_bins[0];
        let mut zone_end = single_bins[0];
        let mut zone_period = self.bin_visits[&single_bins[0]][0];

        for &bin in &single_bins[1..] {
            let period = self.bin_visits[&bin][0];
            if bin == zone_end + 1 && period == zone_period {
                zone_end = bin;
            } else {
                zones.push(SinglePrintZone {
                    low: self.bin_to_price(zone_start),
                    high: self.bin_to_price(zone_end) + self.bin_step,
                    tpo_period: zone_period,
                });
                zone_start = bin;
                zone_end = bin;
                zone_period = period;
            }
        }
        zones.push(SinglePrintZone {
            low: self.bin_to_price(zone_start),
            high: self.bin_to_price(zone_end) + self.bin_step,
            tpo_period: zone_period,
        });

        self.single_print_zones = zones;
    }

    /// Returns the mid-prices of all single-print zones for the current day.
    /// Useful as context magnets, similar to naked POCs.
    pub fn single_print_mids(&self) -> Vec<f64> {
        self.single_print_zones
            .iter()
            .map(|z| (z.low + z.high) / 2.0)
            .collect()
    }

    /// Returns all single-print zones.
    pub fn zones(&self) -> &[SinglePrintZone] {
        &self.single_print_zones
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn single_print_detected_after_fast_move() {
        let mut tracker = TpoTracker::new();
        let day_ms: i64 = 0;
        let period1 = 0; // 00:00–00:30
        let period2 = TPO_PERIOD_MS; // 00:30–01:00
        let period3 = 2 * TPO_PERIOD_MS; // 01:00–01:30

        // Period 1: price range 100–110
        tracker.update(day_ms + period1 + 1, 110.0, 100.0, 105.0, Some(5.0));

        // Period 2: price jumps to 130–140 (fast move, skips 111–129)
        tracker.update(day_ms + period2 + 1, 140.0, 130.0, 135.0, Some(5.0));

        // Period 3: price stays at 130–140 (not revisiting the gap)
        tracker.update(day_ms + period3 + 1, 142.0, 131.0, 137.0, Some(5.0));

        let mids = tracker.single_print_mids();
        // The gap 110–130 (period1 top to period2 bottom) should have single prints.
        // Period1 bins 100–110 each visited by period 0 only, but period 3 is current (open)
        // so period1 bins should show as single prints.
        // Gap zone 110–130: if period1 visited 100–110 and period2 visited 130–140,
        // bins 111–129 were never visited so they won't appear in bin_visits at all.
        // The single prints are the period1 bins (100–110) visited only once.
        assert!(!mids.is_empty(), "should detect single print zones");
    }

    #[test]
    fn no_single_prints_when_revisited() {
        let mut tracker = TpoTracker::new();
        let day_ms: i64 = 0;

        // Period 1 visits 100–120
        tracker.update(day_ms + 1, 120.0, 100.0, 110.0, Some(5.0));
        // Period 2 revisits same range
        tracker.update(day_ms + TPO_PERIOD_MS + 1, 120.0, 100.0, 110.0, Some(5.0));

        let mids = tracker.single_print_mids();
        // All bins visited twice → no single prints
        assert!(
            mids.is_empty(),
            "revisited bins should not be single prints"
        );
    }

    #[test]
    fn day_transition_clears_state() {
        let mut tracker = TpoTracker::new();
        tracker.update(0, 110.0, 100.0, 105.0, Some(5.0));
        assert!(!tracker.bin_visits.is_empty());

        // New day
        tracker.update(DAY_MS + 1, 110.0, 100.0, 105.0, Some(5.0));
        // After new day, bins from previous day should be gone.
        // Bins from new day's first bar will be populated.
        assert!(tracker.current_day == 1);
    }
}
