//! Near-miss / intent logger.
//!
//! Fires on every bar close (same trigger as signal detection). Logs setups that
//! were "forming" but didn't fully trigger, so you can review what conditions
//! were met and what blocked the entry.
//!
//! Output: %APPDATA%\flowsurface\logs\near_misses.jsonl
//! Each line is a JSON object. Open with any text editor or `python scripts/analyze_outcomes.py`.

use super::types::*;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::PathBuf;

/// Returns the dedicated logs directory, creating it if needed.
pub fn logs_dir() -> PathBuf {
    let base = dirs_next::data_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("flowsurface")
        .join("logs");
    let _ = fs::create_dir_all(&base);
    base
}

// ──────────────────────────────────────────────────────────────────────────────
// Public types
// ──────────────────────────────────────────────────────────────────────────────

#[derive(serde::Serialize)]
pub struct NearMiss {
    pub timestamp_ms: i64,
    pub symbol: String,
    pub detector: &'static str,
    pub side: &'static str,
    pub price: f64,
    pub regime: String,
    pub atr: Option<f64>,
    /// Conditions that passed
    pub met: Vec<&'static str>,
    /// Conditions that failed (what blocked the entry)
    pub blocked: Vec<&'static str>,
    /// How many conditions passed out of the total evaluated
    pub score_pct: u8,
    // Key context values for post-analysis
    pub vah: Option<f64>,
    pub val: Option<f64>,
    pub poc: Option<f64>,
    pub delta: Option<f64>,
    pub cvd_slope: Option<f64>,
    pub vpin: Option<f64>,
    pub spread_bps: Option<f64>,
    pub failed_acceptance: bool,
}

// ──────────────────────────────────────────────────────────────────────────────
// Evaluation — ValueAreaFailedAuction
// ──────────────────────────────────────────────────────────────────────────────

/// Evaluates all VAFA conditions individually and returns NearMiss entries for
/// sides where at least the core location condition (price near VA edge) is met.
/// Returns 0, 1, or 2 entries (one per side that is "forming").
pub fn evaluate_vafa(ctx: &StrategyMarketContext, cfg: &StrategyConfig) -> Vec<NearMiss> {
    let px = ctx.price;
    let atr = ctx.atr.unwrap_or(0.0);
    let vp = &ctx.volume_profile;
    let flow = &ctx.flow;
    let ob = &ctx.orderbook;

    let (Some(vah), Some(val), Some(poc)) = (vp.vah, vp.val, vp.poc) else {
        return vec![];
    };

    // VPIN gate (toxic flow) — if triggered, note it but still evaluate
    let vpin_ok = flow.vpin.unwrap_or(0.0) <= cfg.max_vpin;

    let mut results = vec![];

    // ── SHORT side ──
    {
        let near_vah = px < vah && (atr <= 0.0 || px > vah - 0.5 * atr);
        // Only log if price is near the edge — otherwise it's not even forming
        if near_vah || flow.failed_acceptance {
            let mut met = vec![];
            let mut blocked = vec![];

            let check = |cond: bool, label_ok: &'static str, label_fail: &'static str,
                         met: &mut Vec<&'static str>, blocked: &mut Vec<&'static str>| {
                if cond { met.push(label_ok) } else { blocked.push(label_fail) }
            };

            check(near_vah,                                          "price_near_vah",         "price_far_from_vah",      &mut met, &mut blocked);
            check(flow.failed_acceptance,                            "failed_acceptance",       "no_failed_acceptance",    &mut met, &mut blocked);
            check(flow.delta.unwrap_or(0.0) < 0.0,                  "delta_aligned_short",     "delta_not_aligned",       &mut met, &mut blocked);
            check(flow.footprint_absorption == AbsorptionSide::Ask,  "ask_absorption",          "no_ask_absorption",       &mut met, &mut blocked);
            check(flow.cvd_slope.unwrap_or(0.0) <= 0.0,             "cvd_slope_ok",            "cvd_slope_bullish",       &mut met, &mut blocked);
            check(flow.taker_imbalance.unwrap_or(0.0) < 0.25,       "taker_imbalance_ok",      "taker_imbalance_high",    &mut met, &mut blocked);
            check(ob.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps, "spread_ok",           "spread_too_wide",         &mut met, &mut blocked);
            check(!ob.thin_zone_above,                               "no_thin_zone_above",      "thin_zone_above",         &mut met, &mut blocked);
            check(vpin_ok,                                           "vpin_ok",                 "vpin_toxic",              &mut met, &mut blocked);

            // R:R gate — only meaningful if other location conditions are met
            if near_vah && flow.failed_acceptance {
                let entry = px;
                let stop = f64::max(vah + 0.25 * atr, px + 0.5 * atr);
                let target = poc;
                let risk = (stop - entry).abs();
                let reward = (target - entry).abs();
                let rr_ok = target < entry && stop > entry && risk > 1e-10 && reward / risk >= 1.5;
                check(rr_ok, "rr_ok", "rr_insufficient", &mut met, &mut blocked);
            }

            let total = met.len() + blocked.len();
            let score_pct = if total > 0 { (met.len() * 100 / total) as u8 } else { 0 };

            results.push(NearMiss {
                timestamp_ms: ctx.timestamp_ms,
                symbol: ctx.symbol.clone(),
                detector: "ValueAreaFailedAuction",
                side: "Short",
                price: px,
                regime: format!("{:?}", ctx.regime),
                atr: ctx.atr,
                met,
                blocked,
                score_pct,
                vah: Some(vah),
                val: Some(val),
                poc: Some(poc),
                delta: flow.delta,
                cvd_slope: flow.cvd_slope,
                vpin: flow.vpin,
                spread_bps: ob.spread_bps,
                failed_acceptance: flow.failed_acceptance,
            });
        }
    }

    // ── LONG side ──
    {
        let near_val = px > val && (atr <= 0.0 || px < val + 0.5 * atr);
        if near_val || flow.failed_acceptance {
            let mut met = vec![];
            let mut blocked = vec![];

            let check = |cond: bool, label_ok: &'static str, label_fail: &'static str,
                         met: &mut Vec<&'static str>, blocked: &mut Vec<&'static str>| {
                if cond { met.push(label_ok) } else { blocked.push(label_fail) }
            };

            check(near_val,                                          "price_near_val",          "price_far_from_val",      &mut met, &mut blocked);
            check(flow.failed_acceptance,                            "failed_acceptance",       "no_failed_acceptance",    &mut met, &mut blocked);
            check(flow.delta.unwrap_or(0.0) > 0.0,                  "delta_aligned_long",      "delta_not_aligned",       &mut met, &mut blocked);
            check(flow.footprint_absorption == AbsorptionSide::Bid,  "bid_absorption",          "no_bid_absorption",       &mut met, &mut blocked);
            check(flow.cvd_slope.unwrap_or(0.0) >= 0.0,             "cvd_slope_ok",            "cvd_slope_bearish",       &mut met, &mut blocked);
            check(flow.taker_imbalance.unwrap_or(0.0) > -0.25,      "taker_imbalance_ok",      "taker_imbalance_low",     &mut met, &mut blocked);
            check(ob.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps, "spread_ok",           "spread_too_wide",         &mut met, &mut blocked);
            check(!ob.thin_zone_below,                               "no_thin_zone_below",      "thin_zone_below",         &mut met, &mut blocked);
            check(vpin_ok,                                           "vpin_ok",                 "vpin_toxic",              &mut met, &mut blocked);

            if near_val && flow.failed_acceptance {
                let entry = px;
                let stop = f64::min(val - 0.25 * atr, px - 0.5 * atr);
                let target = poc;
                let risk = (stop - entry).abs();
                let reward = (target - entry).abs();
                let rr_ok = target > entry && stop < entry && risk > 1e-10 && reward / risk >= 1.5;
                check(rr_ok, "rr_ok", "rr_insufficient", &mut met, &mut blocked);
            }

            let total = met.len() + blocked.len();
            let score_pct = if total > 0 { (met.len() * 100 / total) as u8 } else { 0 };

            results.push(NearMiss {
                timestamp_ms: ctx.timestamp_ms,
                symbol: ctx.symbol.clone(),
                detector: "ValueAreaFailedAuction",
                side: "Long",
                price: px,
                regime: format!("{:?}", ctx.regime),
                atr: ctx.atr,
                met,
                blocked,
                score_pct,
                vah: Some(vah),
                val: Some(val),
                poc: Some(poc),
                delta: flow.delta,
                cvd_slope: flow.cvd_slope,
                vpin: flow.vpin,
                spread_bps: ob.spread_bps,
                failed_acceptance: flow.failed_acceptance,
            });
        }
    }

    results
}

// ──────────────────────────────────────────────────────────────────────────────
// Writer
// ──────────────────────────────────────────────────────────────────────────────

/// Logs all near-misses for this bar to logs/near_misses.jsonl.
/// Only writes entries where at least one core condition is met (score_pct > 0)
/// and the setup is NOT a full signal (to avoid duplicate noise).
pub fn log_near_misses(ctx: &StrategyMarketContext, cfg: &StrategyConfig, signal_fired: bool) {
    let candidates = evaluate_vafa(ctx, cfg);
    if candidates.is_empty() {
        return;
    }

    let path = logs_dir().join("near_misses.jsonl");
    let Ok(mut file) = OpenOptions::new().create(true).append(true).open(&path) else {
        return;
    };

    for nm in candidates {
        // Skip full signals (already logged in strategy_signals.jsonl)
        if signal_fired && nm.score_pct == 100 {
            continue;
        }
        // Skip if nothing is forming at all
        if nm.score_pct == 0 && !nm.failed_acceptance {
            continue;
        }
        if let Ok(json) = serde_json::to_string(&nm) {
            let _ = writeln!(file, "{json}");
        }
    }
}
