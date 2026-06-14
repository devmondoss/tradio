/// Micro-window capture for DRR calibration.
///
/// Each 5-minute candle is divided into 20 slots of 15s. At bar close,
/// 5 slots are selected based on the anchor mode and shape features are
/// computed. Every candle emits one row to `micro_windows` in Supabase,
/// regardless of whether a signal fired.
///
/// # Size units
/// All volume fields (`size`, `buy_vol`, `sell_vol`) are in **base units**
/// (BTC for BTCUSDT). This matches CVD, footprint, and `bar_buy_vol` in the
/// monitor. `absorption_proxy` and `late_surge_ratio` depend on this — do not
/// mix with USD volume.
use serde::Serialize;
use serde_json::{Value, json};

// ── Config ────────────────────────────────────────────────────────────────────

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum WindowAnchor {
    CandleOpen,
    CandleClose,
    Trigger,
}

impl WindowAnchor {
    /// Stable string representation for Supabase — independent of Rust variant names.
    pub fn to_db_str(self) -> &'static str {
        match self {
            WindowAnchor::CandleOpen => "candle_open",
            WindowAnchor::CandleClose => "candle_close",
            WindowAnchor::Trigger => "trigger",
        }
    }
}

#[derive(Clone, Debug)]
pub struct MicroWindowConfig {
    pub bucket_count: usize, // 5
    pub bucket_secs: u64,    // 15
    pub anchor: WindowAnchor,
    pub candle_secs: u64,          // 300 for 5 min
    pub capture_all_candles: bool, // true → emit even if no signal fired
}

impl Default for MicroWindowConfig {
    fn default() -> Self {
        Self {
            bucket_count: 5,
            bucket_secs: 15,
            anchor: WindowAnchor::CandleClose,
            candle_secs: 300,
            capture_all_candles: true,
        }
    }
}

impl MicroWindowConfig {
    fn slot_ms(&self) -> u64 {
        self.bucket_secs * 1000
    }
    fn total_slots(&self) -> usize {
        (self.candle_secs / self.bucket_secs) as usize // 20
    }
}

// ── Input trade (simple struct, mapped from exchange::Trade in main.rs) ───────

/// Lightweight trade record fed into the buffer.
/// Constructed in main.rs from the exchange `Trade` type.
pub struct MicroTrade {
    /// Unix timestamp milliseconds (trade.time.as_u64() as i64).
    pub ts_ms: i64,
    pub price: f64,
    /// Volume in base units (BTC for BTCUSDT) — same as CVD / footprint.
    pub size: f64,
    /// True = taker buyer (buy aggressor). `!trade.is_sell`.
    pub is_buy: bool,
    /// Currently always false until per-trade big-trade detection is added.
    /// The bar-level `big_trade_bullish/bearish` is computed after close.
    pub is_big: bool,
    /// Liquidation USD for this trade. 0.0 — liquidations arrive on a
    /// separate @forceOrder stream and are not available per-trade.
    pub liq_usd: f64,
}

// ── 15-second slot ───────────────────────────────────────────────────────────

#[derive(Clone, Debug, Default)]
struct Slot {
    buy_vol: f64,
    sell_vol: f64,
    trades: i64,
    big_vol: f64,
    max_trade: f64,
    liq_usd: f64,
    p_open: f64,
    p_close: f64,
    hi: f64,
    lo: f64,
    seen: bool,
}

impl Slot {
    fn add(&mut self, t: &MicroTrade) {
        if !self.seen {
            self.p_open = t.price;
            self.hi = t.price;
            self.lo = t.price;
            self.seen = true;
        }
        self.p_close = t.price;
        if t.price > self.hi {
            self.hi = t.price;
        }
        if t.price < self.lo {
            self.lo = t.price;
        }
        if t.is_buy {
            self.buy_vol += t.size;
        } else {
            self.sell_vol += t.size;
        }
        self.trades += 1;
        if t.is_big {
            self.big_vol += t.size;
        }
        if t.size > self.max_trade {
            self.max_trade = t.size;
        }
        self.liq_usd += t.liq_usd;
    }
    fn vol(&self) -> f64 {
        self.buy_vol + self.sell_vol
    }
    fn delta(&self) -> f64 {
        self.buy_vol - self.sell_vol
    }
}

// ── Buffer per candle ─────────────────────────────────────────────────────────

pub struct CandleMicroBuffer {
    cfg: MicroWindowConfig,
    candle_open_ms: i64,
    slots: Vec<Slot>,
    trigger_ms: Option<i64>,
}

impl CandleMicroBuffer {
    pub fn new(cfg: MicroWindowConfig, candle_open_ms: i64) -> Self {
        let n = cfg.total_slots();
        Self {
            cfg,
            candle_open_ms,
            slots: vec![Slot::default(); n],
            trigger_ms: None,
        }
    }

    /// Reset at the start of each new candle.
    pub fn reset(&mut self, candle_open_ms: i64) {
        self.candle_open_ms = candle_open_ms;
        self.trigger_ms = None;
        for s in &mut self.slots {
            *s = Slot::default();
        }
    }

    /// Call once per inbound trade from the WS stream.
    pub fn on_trade(&mut self, t: &MicroTrade) {
        let off = (t.ts_ms - self.candle_open_ms).max(0) as u64;
        let idx = (off / self.cfg.slot_ms()) as usize;
        if idx < self.slots.len() {
            self.slots[idx].add(t);
        }
    }

    /// Call when the strategy router emits a signal on this candle.
    pub fn mark_trigger(&mut self, ts_ms: i64) {
        self.trigger_ms = Some(ts_ms);
    }

    fn window_slot_range(&self) -> (usize, usize) {
        let n = self.cfg.bucket_count;
        let total = self.slots.len();
        match self.cfg.anchor {
            WindowAnchor::CandleOpen => (0, n.min(total)),
            WindowAnchor::CandleClose => (total.saturating_sub(n), total),
            WindowAnchor::Trigger => match self.trigger_ms {
                Some(tms) => {
                    let off = (tms - self.candle_open_ms).max(0) as u64;
                    let ti = (off / self.cfg.slot_ms()) as usize;
                    let lead = n / 2; // 2 slots before trigger for n=5
                    let start = ti.saturating_sub(lead);
                    let end = (start + n).min(total);
                    let start = end.saturating_sub(n);
                    (start, end)
                }
                None => (total.saturating_sub(n), total), // fall back to close
            },
        }
    }

    /// Build the row at bar close. `ctx` injects range/zone context that the
    /// buffer doesn't track itself.
    pub fn build_row(&self, ctx: &MicroCtx, exchange: &str, symbol: &str) -> MicroWindowRow {
        let (s0, s1) = self.window_slot_range();
        let win: Vec<&Slot> = self.slots[s0..s1].iter().collect();
        let n = win.len().max(1);

        let deltas: Vec<f64> = win.iter().map(|s| s.delta()).collect();
        let vols: Vec<f64> = win.iter().map(|s| s.vol()).collect();

        let win_vol_total: f64 = vols.iter().sum();
        let win_delta_total: f64 = deltas.iter().sum();
        let win_trades_total: i64 = win.iter().map(|s| s.trades).sum();
        let win_liq_total: f64 = win.iter().map(|s| s.liq_usd).sum();
        let win_big_vol: f64 = win.iter().map(|s| s.big_vol).sum();
        let win_max_trade: f64 = win.iter().map(|s| s.max_trade).fold(0.0, f64::max);
        let buy_vol: f64 = win.iter().map(|s| s.buy_vol).sum();
        let aggressor_ratio = if win_vol_total > 0.0 {
            buy_vol / win_vol_total
        } else {
            0.5
        };

        // ── shape features ───────────────────────────────────────────────────
        let delta_slope = linreg_slope(&deltas);
        let dstd = stdev(&deltas);
        let delta_slope_norm = if dstd > 0.0 { delta_slope / dstd } else { 0.0 };
        let half = n / 2;
        let first_half: f64 = deltas[..half.max(1)].iter().sum();
        let second_half: f64 = deltas[half..].iter().sum();
        let delta_accel = second_half - first_half;

        let (delta_flip, delta_flip_bucket) = detect_flip(&deltas);
        let monotonic_delta = deltas.iter().all(|d| *d >= 0.0) || deltas.iter().all(|d| *d <= 0.0);

        let vol_peak_bucket = argmax(&vols) as i16;
        let vol_mean = win_vol_total / n as f64;
        let late_surge_ratio = if vol_mean > 0.0 {
            vols[n - 1] / vol_mean
        } else {
            0.0
        };
        let vol_trajectory = classify_trajectory(&vols);

        let p_open_win = win.first().map(|s| s.p_open).unwrap_or(0.0);
        let p_close_win = win.last().map(|s| s.p_close).unwrap_or(0.0);
        let price_net = p_close_win - p_open_win;
        let path_raw: f64 = win.iter().map(|s| (s.p_close - s.p_open).abs()).sum();
        let price_path_eff = if path_raw > 0.0 {
            price_net.abs() / path_raw
        } else {
            0.0
        };
        let hi = win.iter().map(|s| s.hi).fold(f64::MIN, f64::max);
        let lo = win.iter().map(|s| s.lo).fold(f64::MAX, f64::min);
        let atr = ctx.atr.max(1e-9);
        let micro_range_atr = (hi - lo) / atr;
        // absorption_proxy: volume relative to price movement — high = buyers/sellers
        // absorbed aggression without letting price move (classic Subdimi absorption signal)
        let absorption_proxy = win_vol_total / (price_net.abs() / atr + 1e-6);

        // ── DRR-specific ─────────────────────────────────────────────────────
        let (reclaimed, reclaim_bucket) = ctx.detect_reclaim(&win);
        let sweep_depth_atr = ctx.sweep_depth_atr(lo, hi);

        // ── JSONB bucket detail (escape hatch for future analysis) ────────────
        let buckets = json!(
            win.iter()
                .enumerate()
                .map(|(i, s)| json!({
                    "i":         i,
                    "buy_vol":   s.buy_vol,  "sell_vol": s.sell_vol, "delta": s.delta(),
                    "vol":       s.vol(),    "trades":   s.trades,
                    "big_vol":   s.big_vol,  "max_trade": s.max_trade, "liq_usd": s.liq_usd,
                    "p_open":    s.p_open,   "p_close": s.p_close,  "hi": s.hi, "lo": s.lo,
                }))
                .collect::<Vec<_>>()
        );

        let slot_ms = self.cfg.slot_ms() as i64;
        let window_start_ms = self.candle_open_ms + s0 as i64 * slot_ms;
        let window_end_ms = self.candle_open_ms + s1 as i64 * slot_ms;
        let candle_close_ms = self.candle_open_ms + (self.cfg.candle_secs * 1000) as i64;

        MicroWindowRow {
            exchange: exchange.to_string(),
            symbol: symbol.to_string(),
            timeframe: "5m".to_string(),
            candle_open_ms: self.candle_open_ms,
            candle_close_ms,
            anchor: self.cfg.anchor.to_db_str().to_string(),
            window_start_ms,
            window_end_ms,
            bucket_count: self.cfg.bucket_count as i16,
            bucket_secs: self.cfg.bucket_secs as i16,
            in_drr_zone: ctx.in_drr_zone,
            range_location: ctx.range_location_str(),
            range_high: ctx.range_high,
            range_low: ctx.range_low,
            range_mid: ctx.range_mid,
            atr: ctx.atr,
            win_vol_total,
            win_delta_total,
            win_trades_total: win_trades_total as i32,
            win_cvd_net: win_delta_total,
            win_liq_total,
            win_big_vol,
            win_max_trade,
            aggressor_ratio,
            delta_b: pad5(&deltas),
            vol_b: pad5(&vols),
            delta_slope,
            delta_slope_norm,
            delta_accel,
            delta_flip,
            delta_flip_bucket,
            monotonic_delta,
            vol_peak_bucket,
            vol_trajectory,
            late_surge_ratio,
            price_net,
            price_path_eff,
            micro_range_atr,
            absorption_proxy,
            reclaimed,
            reclaim_bucket,
            sweep_depth_atr,
            buckets,
        }
    }
}

// ── Context injected from StrategyMarketContext ───────────────────────────────

use crate::detectors::range_detector::RangeLocation;

pub struct MicroCtx {
    pub atr: f64,
    pub range_loc: Option<RangeLocation>,
    pub range_high: Option<f64>,
    pub range_low: Option<f64>,
    pub range_mid: Option<f64>,
    pub in_drr_zone: Option<bool>,
}

impl MicroCtx {
    /// Stable string for each RangeLocation variant — decoupled from Rust names.
    /// Any future rename of the enum must update this match, not silently break.
    pub fn range_location_str(&self) -> Option<String> {
        self.range_loc.map(|loc| {
            match loc {
                RangeLocation::NearHigh => "near_high",
                RangeLocation::NearLow => "near_low",
                RangeLocation::NoTrade => "mid",
                RangeLocation::Inside => "inside",
                RangeLocation::OutsideHigh => "outside_high",
                RangeLocation::OutsideLow => "outside_low",
                RangeLocation::Unknown => "unknown",
            }
            .to_string()
        })
    }

    /// Detect whether the price exited the range and returned within the window.
    fn detect_reclaim(&self, win: &[&Slot]) -> (Option<bool>, Option<i16>) {
        let (Some(hi), Some(lo)) = (self.range_high, self.range_low) else {
            return (None, None);
        };
        let mut went_out = false;
        for (i, s) in win.iter().enumerate() {
            if s.lo < lo || s.hi > hi {
                went_out = true;
            }
            if went_out && s.p_close <= hi && s.p_close >= lo {
                return (Some(true), Some(i as i16));
            }
        }
        (Some(false), None)
    }

    fn sweep_depth_atr(&self, win_lo: f64, win_hi: f64) -> Option<f64> {
        let atr = self.atr.max(1e-9);
        match self.range_loc {
            Some(RangeLocation::NearLow) | Some(RangeLocation::OutsideLow) => {
                self.range_low.map(|rl| (rl - win_lo).max(0.0) / atr)
            }
            Some(RangeLocation::NearHigh) | Some(RangeLocation::OutsideHigh) => {
                self.range_high.map(|rh| (win_hi - rh).max(0.0) / atr)
            }
            _ => None,
        }
    }
}

// ── Output row ────────────────────────────────────────────────────────────────

#[derive(Debug, Serialize)]
pub struct MicroWindowRow {
    pub exchange: String,
    pub symbol: String,
    pub timeframe: String,
    pub candle_open_ms: i64,
    pub candle_close_ms: i64,
    pub anchor: String,
    pub window_start_ms: i64,
    pub window_end_ms: i64,
    pub bucket_count: i16,
    pub bucket_secs: i16,
    pub in_drr_zone: Option<bool>,
    pub range_location: Option<String>,
    pub range_high: Option<f64>,
    pub range_low: Option<f64>,
    pub range_mid: Option<f64>,
    pub atr: f64,
    pub win_vol_total: f64,
    pub win_delta_total: f64,
    pub win_trades_total: i32,
    pub win_cvd_net: f64,
    pub win_liq_total: f64,
    pub win_big_vol: f64,
    pub win_max_trade: f64,
    pub aggressor_ratio: f64,
    /// Per-bucket delta, base units. Maps to delta_b0..delta_b4 in writer.
    pub delta_b: [f64; 5],
    /// Per-bucket volume, base units. Maps to vol_b0..vol_b4 in writer.
    pub vol_b: [f64; 5],
    pub delta_slope: f64,
    pub delta_slope_norm: f64,
    pub delta_accel: f64,
    pub delta_flip: bool,
    pub delta_flip_bucket: Option<i16>,
    pub monotonic_delta: bool,
    pub vol_peak_bucket: i16,
    pub vol_trajectory: String,
    pub late_surge_ratio: f64,
    pub price_net: f64,
    pub price_path_eff: f64,
    pub micro_range_atr: f64,
    pub absorption_proxy: f64,
    pub reclaimed: Option<bool>,
    pub reclaim_bucket: Option<i16>,
    pub sweep_depth_atr: Option<f64>,
    pub buckets: Value,
}

// ── Numeric helpers ───────────────────────────────────────────────────────────

fn linreg_slope(y: &[f64]) -> f64 {
    let n = y.len() as f64;
    if n < 2.0 {
        return 0.0;
    }
    let xbar = (n - 1.0) / 2.0;
    let ybar = y.iter().sum::<f64>() / n;
    let (mut num, mut den) = (0.0f64, 0.0f64);
    for (i, &v) in y.iter().enumerate() {
        let dx = i as f64 - xbar;
        num += dx * (v - ybar);
        den += dx * dx;
    }
    if den == 0.0 { 0.0 } else { num / den }
}

fn stdev(y: &[f64]) -> f64 {
    let n = y.len() as f64;
    if n < 2.0 {
        return 0.0;
    }
    let m = y.iter().sum::<f64>() / n;
    (y.iter().map(|v| (v - m).powi(2)).sum::<f64>() / n).sqrt()
}

fn argmax(y: &[f64]) -> usize {
    y.iter()
        .enumerate()
        .max_by(|(_, a), (_, b)| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal))
        .map(|(i, _)| i)
        .unwrap_or(0)
}

fn detect_flip(deltas: &[f64]) -> (bool, Option<i16>) {
    if deltas.is_empty() {
        return (false, None);
    }
    let s0 = deltas[0].signum();
    for (i, d) in deltas.iter().enumerate().skip(1) {
        if d.signum() != s0 && d.abs() > 0.0 {
            return (true, Some(i as i16));
        }
    }
    (false, None)
}

/// Classify the volume trajectory shape across buckets.
/// These strings are stable DB values — update carefully.
fn classify_trajectory(vols: &[f64]) -> String {
    let n = vols.len();
    if n < 3 {
        return "flat".into();
    }
    let total: f64 = vols.iter().sum();
    let mean = total / n as f64;
    let spread = stdev(vols);
    if mean > 0.0 && spread / mean < 0.25 {
        return "flat".into();
    }
    let center = vols[n / 2];
    let ends = (vols[0] + vols[n - 1]) / 2.0;
    if center < ends * 0.6 {
        return "u_shape".into();
    }
    match argmax(vols) {
        0 | 1 => "front".into(),
        i if i >= n - 2 => "back".into(),
        _ => "mid".into(),
    }
}

fn pad5(v: &[f64]) -> [f64; 5] {
    let mut a = [0.0f64; 5];
    for (i, &x) in v.iter().take(5).enumerate() {
        a[i] = x;
    }
    a
}
