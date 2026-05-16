use serde::{Deserialize, Serialize};

use crate::strategy::types::DataQuality;

// ── Liquidation ──────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum LiqSide {
    Longs,
    Shorts,
    Neutral,
}

#[derive(Debug, Clone)]
pub struct LiquidationEvent {
    pub timestamp_ms: i64,
    pub side: LiqSide,
    /// Notional value in USD.
    pub quantity_usd: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LiquidationSnapshot {
    pub long_liq_usd_5m: f64,
    pub short_liq_usd_5m: f64,
    pub total_usd_5m: f64,
    pub dominant_side: LiqSide,
    /// True when > $5M liquidated in any 60-second window within the last 5 minutes.
    pub cascade_detected: bool,
    pub last_event_ms: Option<i64>,
}

impl Default for LiquidationSnapshot {
    fn default() -> Self {
        Self {
            long_liq_usd_5m: 0.0,
            short_liq_usd_5m: 0.0,
            total_usd_5m: 0.0,
            dominant_side: LiqSide::Neutral,
            cascade_detected: false,
            last_event_ms: None,
        }
    }
}

// ── Long/Short Ratios ────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum LsSource {
    TopTraderPosition,
    GlobalAccount,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LongShortSnapshot {
    pub timestamp_ms: i64,
    pub long_ratio: f64,
    pub short_ratio: f64,
    /// long / short direct ratio.
    pub ls_ratio: f64,
    pub source: LsSource,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum DivergenceSignal {
    /// Top traders short, retail long → potential long squeeze.
    SmartShortRetailLong,
    /// Top traders long, retail short → potential short squeeze.
    SmartLongRetailShort,
    /// Both sides aligned → genuine momentum.
    Aligned,
    Neutral,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LsRatioContext {
    pub top_traders_long_pct: f64,
    pub retail_long_pct: f64,
    pub divergence_signal: DivergenceSignal,
}

impl Default for LsRatioContext {
    fn default() -> Self {
        Self {
            top_traders_long_pct: 0.5,
            retail_long_pct: 0.5,
            divergence_signal: DivergenceSignal::Neutral,
        }
    }
}

// ── Open Interest ────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OiHistSnapshot {
    pub timestamp_ms: i64,
    pub open_interest_usd: f64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum OiTrendDir {
    AccumulatingFast,
    Accumulating,
    Flat,
    Decreasing,
    DecreasingFast,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OiTrend {
    pub current: f64,
    /// % change over the last 30 minutes.
    pub change_30m: f64,
    /// OLS slope of the last 5 OI samples (normalized by current OI).
    pub slope_5bar: f64,
    pub trend: OiTrendDir,
}

impl Default for OiTrend {
    fn default() -> Self {
        Self {
            current: 0.0,
            change_30m: 0.0,
            slope_5bar: 0.0,
            trend: OiTrendDir::Flat,
        }
    }
}

// ── Taker Ratio ──────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TakerRatioSnapshot {
    pub timestamp_ms: i64,
    pub buy_sell_ratio: f64,
    /// (buy - sell) / (buy + sell), range -1.0 to +1.0.
    pub taker_imbalance: f64,
}

// ── Funding ──────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FundingRateSample {
    pub timestamp_ms: i64,
    pub rate: f64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum FundingRegime {
    /// > 85th percentile of recent history (positive).
    ExtremeLong,
    /// > 65th percentile (positive).
    ElevatedLong,
    Neutral,
    ElevatedShort,
    ExtremeShort,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FundingContext {
    pub current: f64,
    pub avg: f64,
    pub regime: FundingRegime,
}

impl Default for FundingContext {
    fn default() -> Self {
        Self {
            current: 0.0,
            avg: 0.0,
            regime: FundingRegime::Neutral,
        }
    }
}

// ── Combined InstitutionalContext ─────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InstitutionalContext {
    pub timestamp_ms: i64,
    pub liquidations: LiquidationSnapshot,
    pub ls_ratio: LsRatioContext,
    pub oi_trend: OiTrend,
    pub taker_ratio: Option<TakerRatioSnapshot>,
    pub funding: FundingContext,
    pub quality: DataQuality,
}
