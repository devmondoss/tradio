/// Volume Profile Open Bias — classifies the daily session open vs previous session's value area.
///
/// Based on Subdimi/Supreme Trading methodology:
///   1. Open Inside VA → range day; trade extremes (VAL/VAH)
///   2. Open Outside VA but Inside Price Action range → acceptance trade toward POC
///   3. Open Completely Outside (gap) → trend day in direction of gap
///   4. Gap Open then Accepts Inside VA → fade the gap (reversal)
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum DailyVpBias {
    /// Open inside previous VA → range day; trade VAH/VAL as extremes
    InsideValue,
    /// Open outside VA but within prev session price action → trend toward POC
    OutsideVaInsidePa,
    /// Open completely outside prev price action (true gap) → trend in gap direction
    TrendDay,
    /// Gap open, then price accepts back inside VA → fade (reversal)
    FadeGap,
    /// Not enough data to classify
    Unknown,
}

/// Snapshot of previous session's VP for open classification.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PrevSessionVp {
    pub poc: f64,
    pub vah: f64,
    pub val: f64,
    /// Previous session high (for price action range)
    pub session_high: f64,
    /// Previous session low (for price action range)
    pub session_low: f64,
}

/// Daily VP open classification result.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DailyVpContext {
    pub bias: DailyVpBias,
    pub prev_poc: f64,
    pub prev_vah: f64,
    pub prev_val: f64,
    /// Open price used for classification
    pub session_open: f64,
}

impl DailyVpContext {
    /// Returns `true` if the bias supports a directional move toward `poc` from above (short-biased day)
    pub fn bias_supports_short(&self) -> bool {
        matches!(
            self.bias,
            DailyVpBias::OutsideVaInsidePa | DailyVpBias::FadeGap
        ) && self.session_open > self.prev_vah
    }

    /// Returns `true` if the bias supports a directional move toward `poc` from below (long-biased day)
    pub fn bias_supports_long(&self) -> bool {
        matches!(
            self.bias,
            DailyVpBias::OutsideVaInsidePa | DailyVpBias::FadeGap
        ) && self.session_open < self.prev_val
    }

    /// Returns `true` if this is a range day (trade extremes, not breakouts)
    pub fn is_range_day(&self) -> bool {
        matches!(self.bias, DailyVpBias::InsideValue)
    }

    /// Returns `true` if this is a trend day (follow the breakout direction)
    pub fn is_trend_day(&self) -> bool {
        matches!(self.bias, DailyVpBias::TrendDay)
    }

    /// True when TrendDay opened above prev VAH — bullish trend day
    pub fn trend_day_is_up(&self) -> bool {
        matches!(self.bias, DailyVpBias::TrendDay) && self.session_open > self.prev_vah
    }

    /// True when TrendDay opened below prev VAL — bearish trend day
    pub fn trend_day_is_down(&self) -> bool {
        matches!(self.bias, DailyVpBias::TrendDay) && self.session_open < self.prev_val
    }
}

/// Classify the daily open vs the previous session's volume profile.
///
/// `open_price`: first trade price of the new session.
/// `prev`: previous session's VP snapshot.
/// `accepted_inside`: whether, after opening outside, price has re-entered the VA
///   (used to detect FadeGap). Pass `false` at open, `true` if first few bars re-enter.
pub fn classify_open(
    open_price: f64,
    prev: &PrevSessionVp,
    accepted_inside: bool,
) -> DailyVpContext {
    let bias = if open_price >= prev.val && open_price <= prev.vah {
        // Open within value area
        DailyVpBias::InsideValue
    } else if open_price > prev.session_low && open_price < prev.session_high {
        // Open outside VA but within prev session price range
        if accepted_inside {
            DailyVpBias::FadeGap
        } else {
            DailyVpBias::OutsideVaInsidePa
        }
    } else {
        // True gap — open completely outside prev session range
        if accepted_inside {
            DailyVpBias::FadeGap
        } else {
            DailyVpBias::TrendDay
        }
    };

    DailyVpContext {
        bias,
        prev_poc: prev.poc,
        prev_vah: prev.vah,
        prev_val: prev.val,
        session_open: open_price,
    }
}

/// Tracker that accumulates the current day's VP extremes and detects session transitions.
#[derive(Debug, Default)]
pub struct DailyVpTracker {
    /// Current day index (bar_ms / 86_400_000)
    current_day: i64,
    /// Current day's running high/low
    day_high: f64,
    day_low: f64,
    /// Current day's VP snapshot (updated each bar)
    current_poc: Option<f64>,
    current_vah: Option<f64>,
    current_val: Option<f64>,
    /// Previous day's complete snapshot
    prev_session: Option<PrevSessionVp>,
    /// Current open bias (computed once per session open)
    current_bias: Option<DailyVpContext>,
    /// Whether we've detected acceptance inside VA after a gap open
    gap_acceptance_checked: bool,
}

impl DailyVpTracker {
    pub fn new() -> Self {
        Self {
            current_day: -1,
            day_high: f64::NEG_INFINITY,
            day_low: f64::INFINITY,
            ..Default::default()
        }
    }

    /// Update with the current bar's data. Returns the current bias if available.
    pub fn update(
        &mut self,
        bar_ms: i64,
        open: f64,
        high: f64,
        low: f64,
        poc: Option<f64>,
        vah: Option<f64>,
        val: Option<f64>,
    ) -> Option<&DailyVpContext> {
        let day = bar_ms / 86_400_000;

        if day != self.current_day {
            // Day transition: save current as prev, reset
            if self.current_day >= 0 {
                if let (Some(p), Some(h), Some(l)) =
                    (self.current_poc, Some(self.day_high), Some(self.day_low))
                {
                    if h.is_finite() && l.is_finite() {
                        self.prev_session = Some(PrevSessionVp {
                            poc: p,
                            vah: self.current_vah.unwrap_or(p),
                            val: self.current_val.unwrap_or(p),
                            session_high: h,
                            session_low: l,
                        });
                    }
                }
            }
            self.current_day = day;
            self.day_high = high;
            self.day_low = low;
            self.current_poc = poc;
            self.current_vah = vah;
            self.current_val = val;
            self.gap_acceptance_checked = false;

            // Classify the open for the new day
            if let Some(ref prev) = self.prev_session {
                self.current_bias = Some(classify_open(open, prev, false));
            } else {
                self.current_bias = None;
            }
        } else {
            // Same day: accumulate high/low, update VP
            self.day_high = self.day_high.max(high);
            self.day_low = self.day_low.min(low);
            self.current_poc = poc;
            self.current_vah = vah;
            self.current_val = val;

            // Check for gap acceptance: if we opened outside VA but price has re-entered
            if !self.gap_acceptance_checked {
                if let (Some(bias_ctx), Some(prev)) = (&mut self.current_bias, &self.prev_session) {
                    if matches!(
                        bias_ctx.bias,
                        DailyVpBias::OutsideVaInsidePa | DailyVpBias::TrendDay
                    ) {
                        let accepted = open >= prev.val && open <= prev.vah
                            || (low <= prev.vah && high >= prev.val);
                        if accepted {
                            *bias_ctx = classify_open(bias_ctx.session_open, prev, true);
                            self.gap_acceptance_checked = true;
                        }
                    }
                }
            }
        }

        self.current_bias.as_ref()
    }

    pub fn current_bias(&self) -> Option<&DailyVpContext> {
        self.current_bias.as_ref()
    }
}

// ── Naked POC tracker ────────────────────────────────────────────────────────

/// Tracks Point of Control levels from previous sessions that price hasn't revisited.
///
/// A "naked POC" is a Subdimi magnet: price tends to return to unfinished business.
/// When price crosses within `touch_band` of the level it is considered "touched" and removed.
#[derive(Debug, Default)]
pub struct NakedPocTracker {
    /// (session_day, poc_price) — oldest first.
    pocs: Vec<(i64, f64)>,
    /// Current session day to avoid adding the active session's POC twice.
    current_day: i64,
    /// Max naked POCs to keep (drop oldest beyond this).
    max_pocs: usize,
}

impl NakedPocTracker {
    pub fn new(max_pocs: usize) -> Self {
        Self {
            pocs: Vec::new(),
            current_day: -1,
            max_pocs,
        }
    }

    /// Call on every bar close.
    ///
    /// `day_ms`: bar timestamp in ms.
    /// `new_poc`: POC just computed for the current session (added on day transition).
    /// `price`: current bar close — used to expire touched POCs.
    /// `touch_band`: fraction of price to consider a "touch" (e.g. 0.0005 = 0.05%).
    ///
    /// Returns the list of active naked POC prices (untouched, oldest first).
    pub fn update(
        &mut self,
        day_ms: i64,
        new_poc: Option<f64>,
        price: f64,
        touch_band: f64,
    ) -> &[(i64, f64)] {
        let day = day_ms / 86_400_000;

        // On day transition: commit the previous session's POC to the naked list.
        if day != self.current_day && self.current_day >= 0 {
            if let Some(poc) = new_poc {
                if poc.is_finite() && poc > 0.0 {
                    self.pocs.push((self.current_day, poc));
                    // Keep only last `max_pocs` sessions.
                    if self.pocs.len() > self.max_pocs {
                        self.pocs.remove(0);
                    }
                }
            }
        }
        self.current_day = day;

        // Remove POCs that price has revisited this bar.
        let band = price * touch_band;
        self.pocs.retain(|(_, poc)| (price - poc).abs() > band);

        &self.pocs
    }

    /// Returns naked POC prices (no session tag), newest first.
    pub fn naked_poc_prices(&self) -> Vec<f64> {
        self.pocs.iter().rev().map(|(_, p)| *p).collect()
    }
}

// ── HTF VP cascade ────────────────────────────────────────────────────────────

/// Price location relative to an HTF value area.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum HtfValueLocation {
    /// Price above HTF VAH → bullish structure
    AboveVah,
    /// Price below HTF VAL → bearish structure
    BelowVal,
    /// Price inside HTF value area → no directional bias from this timeframe
    InValue,
    /// Not enough data
    Unknown,
}

/// Context from a single HTF (weekly or monthly) volume profile.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HtfVpLevel {
    pub poc: f64,
    pub vah: f64,
    pub val: f64,
    pub location: HtfValueLocation,
}

/// Combined weekly + monthly VP context for top-down bias.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HtfVpContext {
    pub weekly: Option<HtfVpLevel>,
    pub monthly: Option<HtfVpLevel>,
}

impl HtfVpContext {
    /// Returns a bias multiplier for scoring: 1.0 neutral, >1.0 aligned, <1.0 against.
    /// Long signals: bullish if weekly AboveVah and/or monthly AboveVah.
    /// Short signals: bullish if weekly BelowVal and/or monthly BelowVal.
    pub fn bias_factor(&self, is_long: bool) -> f64 {
        let mut aligned = 0u8;
        let mut against = 0u8;
        let total_levels = self.weekly.is_some() as u8 + self.monthly.is_some() as u8;
        if total_levels == 0 {
            return 1.0;
        }
        for lvl in [&self.weekly, &self.monthly]
            .iter()
            .filter_map(|x| x.as_ref())
        {
            match lvl.location {
                HtfValueLocation::AboveVah if is_long => aligned += 1,
                HtfValueLocation::BelowVal if !is_long => aligned += 1,
                HtfValueLocation::AboveVah | HtfValueLocation::BelowVal => against += 1,
                _ => {}
            }
        }
        if aligned > 0 && against == 0 {
            1.08 // both HTF levels aligned
        } else if aligned > 0 {
            1.03 // mixed but net positive
        } else if against > 0 {
            0.90 // trading against HTF structure
        } else {
            1.0
        }
    }
}

/// Generic tracker for a higher-timeframe VP window (week or month).
///
/// `period_ms`: length of one period in ms (7 × 86_400_000 for weekly, ~30 days for monthly).
#[derive(Debug)]
pub struct HtfVpTracker {
    period_ms: i64,
    current_period: i64,
    period_high: f64,
    period_low: f64,
    /// Running VP: Vec<(price_bin, cumulative_volume)> — simplified 50-bin histogram.
    bins: Vec<(f64, f64)>,
    bin_step: f64,
    /// Computed levels for the current (open) period.
    current: Option<HtfVpLevel>,
    /// Completed previous period's levels.
    prev: Option<HtfVpLevel>,
}

impl HtfVpTracker {
    pub fn new_weekly() -> Self {
        Self::new(7 * 86_400_000)
    }

    pub fn new_monthly() -> Self {
        // Approximate 30-day month. We use calendar month via period = bar_ms / (30*86400000).
        Self::new(30 * 86_400_000)
    }

    fn new(period_ms: i64) -> Self {
        Self {
            period_ms,
            current_period: -1,
            period_high: f64::NEG_INFINITY,
            period_low: f64::INFINITY,
            bins: Vec::new(),
            bin_step: 0.0,
            current: None,
            prev: None,
        }
    }

    /// Update with bar data. Returns the current HTF VP level if available.
    pub fn update(
        &mut self,
        bar_ms: i64,
        high: f64,
        low: f64,
        _close: f64,
        volume: f64,
        price: f64,
    ) -> Option<&HtfVpLevel> {
        let period = bar_ms / self.period_ms;

        if period != self.current_period {
            // Save current as prev before resetting
            self.prev = self.current.take();
            self.current_period = period;
            self.period_high = high;
            self.period_low = low;
            self.bins.clear();
            self.bin_step = 0.0;
        } else {
            self.period_high = self.period_high.max(high);
            self.period_low = self.period_low.min(low);
        }

        // Add volume to bins
        const N_BINS: usize = 50;
        let range = self.period_high - self.period_low;
        if range > 0.0 {
            if self.bin_step == 0.0 || self.bins.is_empty() {
                self.bin_step = range / N_BINS as f64;
                self.bins = (0..N_BINS)
                    .map(|i| {
                        (
                            self.period_low + i as f64 * self.bin_step + self.bin_step * 0.5,
                            0.0,
                        )
                    })
                    .collect();
            }
            // Distribute bar volume proportionally across [low, high]
            let bar_range = (high - low).max(self.bin_step * 0.1);
            for (bin_price, bin_vol) in &mut self.bins {
                // Volume per bin proportional to overlap with [low, high]
                let overlap_low = low.max(*bin_price - self.bin_step * 0.5);
                let overlap_high = high.min(*bin_price + self.bin_step * 0.5);
                if overlap_high > overlap_low {
                    *bin_vol += volume * (overlap_high - overlap_low) / bar_range;
                }
            }

            // Recompute POC, VAH, VAL from bins
            if let Some(level) = Self::compute_levels(&self.bins, self.bin_step, price) {
                self.current = Some(level);
            }
        }

        self.current.as_ref()
    }

    fn compute_levels(bins: &[(f64, f64)], _bin_step: f64, price: f64) -> Option<HtfVpLevel> {
        if bins.is_empty() {
            return None;
        }

        // POC = max volume bin
        let (poc_idx, _) = bins
            .iter()
            .enumerate()
            .max_by(|(_, (_, va)), (_, (_, vb))| {
                va.partial_cmp(vb).unwrap_or(std::cmp::Ordering::Equal)
            })?;
        let poc = bins[poc_idx].0;

        // Total volume
        let total_vol: f64 = bins.iter().map(|(_, v)| v).sum();
        if total_vol <= 0.0 {
            return None;
        }

        // VAH/VAL: 70% of volume centered on POC
        let target = total_vol * 0.70;
        let mut included = bins[poc_idx].1;
        let mut lo_idx = poc_idx;
        let mut hi_idx = poc_idx;

        while included < target && (lo_idx > 0 || hi_idx < bins.len() - 1) {
            let expand_lo = lo_idx > 0
                && (hi_idx >= bins.len() - 1
                    || bins[lo_idx - 1].1 >= bins[(hi_idx + 1).min(bins.len() - 1)].1);
            if expand_lo {
                lo_idx -= 1;
                included += bins[lo_idx].1;
            } else if hi_idx < bins.len() - 1 {
                hi_idx += 1;
                included += bins[hi_idx].1;
            } else {
                break;
            }
        }

        let val = bins[lo_idx].0;
        let vah = bins[hi_idx].0;

        let location = if price > vah {
            HtfValueLocation::AboveVah
        } else if price < val {
            HtfValueLocation::BelowVal
        } else {
            HtfValueLocation::InValue
        };

        Some(HtfVpLevel {
            poc,
            vah,
            val,
            location,
        })
    }

    pub fn current_level(&self) -> Option<&HtfVpLevel> {
        self.current.as_ref()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn prev_vp() -> PrevSessionVp {
        PrevSessionVp {
            poc: 100_000.0,
            vah: 100_500.0,
            val: 99_500.0,
            session_high: 101_000.0,
            session_low: 99_000.0,
        }
    }

    #[test]
    fn inside_va_classified_correctly() {
        let ctx = classify_open(100_200.0, &prev_vp(), false);
        assert_eq!(ctx.bias, DailyVpBias::InsideValue);
    }

    #[test]
    fn outside_va_inside_pa_classified() {
        let ctx = classify_open(100_700.0, &prev_vp(), false); // above VAH but below session_high
        assert_eq!(ctx.bias, DailyVpBias::OutsideVaInsidePa);
    }

    #[test]
    fn true_gap_classified_as_trend_day() {
        let ctx = classify_open(101_500.0, &prev_vp(), false); // above session_high
        assert_eq!(ctx.bias, DailyVpBias::TrendDay);
    }

    #[test]
    fn gap_then_acceptance_classified_fade() {
        let ctx = classify_open(101_500.0, &prev_vp(), true);
        assert_eq!(ctx.bias, DailyVpBias::FadeGap);
    }

    #[test]
    fn tracker_transitions_day_and_stores_prev() {
        let mut t = DailyVpTracker::new();
        let day1_ms: i64 = 0; // day 0
        let day2_ms: i64 = 86_400_000; // day 1

        // Day 1 bars
        t.update(
            day1_ms,
            100_000.0,
            101_000.0,
            99_000.0,
            Some(100_000.0),
            Some(100_500.0),
            Some(99_500.0),
        );
        // Day 2 open — should classify vs day 1
        let bias = t.update(
            day2_ms,
            100_700.0,
            101_000.0,
            100_500.0,
            Some(100_300.0),
            Some(101_000.0),
            Some(100_000.0),
        );
        assert!(bias.is_some());
        let b = bias.unwrap();
        // 100_700 is above VAH (100_500) but below session_high (101_000) → OutsideVaInsidePa
        assert_eq!(b.bias, DailyVpBias::OutsideVaInsidePa);
    }
}
