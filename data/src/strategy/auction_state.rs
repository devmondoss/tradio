use serde::{Deserialize, Serialize};

use crate::strategy::types::{ValueLocation, StrategyMarketContext};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum AuctionState {
    /// Price within VA, balanced flow, OI stable — no directional edge.
    Balance,
    /// Price accepted outside VA upward, OFI/CVD aligned bullish.
    UpImbalance,
    /// Price accepted outside VA downward, OFI/CVD aligned bearish.
    DownImbalance,
    /// Price lateral, CVD making higher highs — informed buyers absorbing silently.
    Accumulation,
    /// Price lateral, CVD making lower lows — informed sellers distributing silently.
    Distribution,
    /// Insufficient data to classify.
    Unknown,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuctionStateContext {
    pub state: AuctionState,
    /// Confidence 0–100. Higher = more variables agree.
    pub confidence: u8,
}

impl Default for AuctionStateContext {
    fn default() -> Self {
        Self { state: AuctionState::Unknown, confidence: 0 }
    }
}

/// Classify the current auction state using available context variables.
/// Uses a simple vote: each confirming variable adds to a score bucket.
pub fn classify(ctx: &StrategyMarketContext) -> AuctionStateContext {
    let flow = &ctx.flow;
    let vp = &ctx.volume_profile;

    // ── Location signals ──────────────────────────────────────────────────────
    let above_va = matches!(vp.value_location, ValueLocation::AboveVah);
    let below_va = matches!(vp.value_location, ValueLocation::BelowVal);
    let in_va = matches!(vp.value_location, ValueLocation::InValue);

    // ── Flow directional signals ──────────────────────────────────────────────
    let cvd_up = flow.cvd_slope.map(|s| s > 0.05).unwrap_or(false);
    let cvd_down = flow.cvd_slope.map(|s| s < -0.05).unwrap_or(false);
    let delta_up = flow.delta.map(|d| d > 0.0).unwrap_or(false);
    let delta_down = flow.delta.map(|d| d < 0.0).unwrap_or(false);
    let taker_up = flow.taker_imbalance.map(|t| t > 0.10).unwrap_or(false);
    let taker_down = flow.taker_imbalance.map(|t| t < -0.10).unwrap_or(false);

    // ── OI expansion ─────────────────────────────────────────────────────────
    let oi_expanding = flow.oi_momentum_aligned.unwrap_or(false);

    // ── CVD divergence persistence (key for Accumulation/Distribution) ────────
    // Positive = bearish divergence (price up, CVD not confirming).
    // Negative = bullish divergence (price down, CVD not confirming).
    let div_bars = flow.cvd_divergence_persistence.unwrap_or(0);
    let bearish_divergence = div_bars >= 3; // 3+ bars of price up / CVD flat or down
    let bullish_divergence = div_bars <= -3; // 3+ bars of price down / CVD flat or up

    // ── VPIN toxicity ─────────────────────────────────────────────────────────
    let low_toxicity = flow.vpin_cdf.map(|c| c < 0.70).unwrap_or(true);

    // ── Scoring buckets ───────────────────────────────────────────────────────
    let mut up_imbalance_score: u8 = 0;
    let mut down_imbalance_score: u8 = 0;
    let mut accumulation_score: u8 = 0;
    let mut distribution_score: u8 = 0;
    let mut balance_score: u8 = 0;

    // UpImbalance: price outside VA upward + flow confirms
    if above_va { up_imbalance_score += 30; }
    if cvd_up { up_imbalance_score += 20; }
    if delta_up { up_imbalance_score += 15; }
    if taker_up { up_imbalance_score += 15; }
    if oi_expanding { up_imbalance_score += 20; }

    // DownImbalance: price outside VA downward + flow confirms
    if below_va { down_imbalance_score += 30; }
    if cvd_down { down_imbalance_score += 20; }
    if delta_down { down_imbalance_score += 15; }
    if taker_down { down_imbalance_score += 15; }
    if oi_expanding { down_imbalance_score += 20; }

    // Distribution: price lateral in/near VA + CVD falling persistently
    if in_va || above_va { distribution_score += 10; }
    if bearish_divergence { distribution_score += 50; }
    if cvd_down { distribution_score += 20; }
    if delta_down { distribution_score += 20; }

    // Accumulation: price lateral in/near VA + CVD rising persistently
    if in_va || below_va { accumulation_score += 10; }
    if bullish_divergence { accumulation_score += 50; }
    if cvd_up { accumulation_score += 20; }
    if delta_up { accumulation_score += 20; }

    // Balance: in VA, low toxicity, no strong flow signal
    if in_va { balance_score += 30; }
    if low_toxicity { balance_score += 20; }
    if !cvd_up && !cvd_down { balance_score += 25; }
    if !delta_up && !delta_down { balance_score += 25; }

    // ── Pick winner ───────────────────────────────────────────────────────────
    let scores = [
        (AuctionState::UpImbalance, up_imbalance_score),
        (AuctionState::DownImbalance, down_imbalance_score),
        (AuctionState::Distribution, distribution_score),
        (AuctionState::Accumulation, accumulation_score),
        (AuctionState::Balance, balance_score),
    ];

    let (state, raw_score) = scores
        .iter()
        .max_by_key(|(_, s)| s)
        .copied()
        .unwrap_or((AuctionState::Unknown, 0));

    // Require at least 30 points to claim a state; below that is Unknown
    if raw_score < 30 {
        return AuctionStateContext { state: AuctionState::Unknown, confidence: 0 };
    }

    // Normalize confidence: raw_score capped at 100
    let confidence = raw_score.min(100);

    AuctionStateContext { state, confidence }
}
