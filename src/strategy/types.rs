use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Side {
    Long,
    Short,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum StrategyId {
    ValueAreaFailedAuction,
    VwapValuePullbackContinuation,
    LvnLiquidityVacuumBreakout,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum StrategyAction {
    Wait,
    ShadowSignal,
    Blocked,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Regime {
    TrendUp,
    TrendDown,
    Chop,
    Compression,
    Expansion,
    Stress,
    Aftermath,
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum DataQuality {
    Live,
    Fallback,
    Degraded,
    Stale,
    Missing,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ValueLocation {
    AboveVah,
    BelowVal,
    InValue,
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum PriceRelation {
    Above,
    Below,
    At,
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum AbsorptionSide {
    Bid,
    Ask,
    None,
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ImbalanceSide {
    Bullish,
    Bearish,
    None,
    Unknown,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VolumeProfileContext {
    pub poc: Option<f64>,
    pub vah: Option<f64>,
    pub val: Option<f64>,
    pub hvn_nearby: Vec<f64>,
    pub lvn_nearby: Vec<f64>,
    pub value_location: ValueLocation,
    pub quality: DataQuality,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VwapContext {
    pub vwap_session: Option<f64>,
    pub avwap_bos: Option<f64>,
    pub avwap_event: Option<f64>,
    pub price_vs_vwap: PriceRelation,
    pub price_vs_avwap_bos: PriceRelation,
    pub price_vs_avwap_event: PriceRelation,
    pub quality: DataQuality,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrderFlowContext {
    pub cvd: Option<f64>,
    pub cvd_slope: Option<f64>,
    pub delta: Option<f64>,
    pub taker_imbalance: Option<f64>,
    pub buy_volume: Option<f64>,
    pub sell_volume: Option<f64>,
    pub vpin: Option<f64>,
    pub footprint_absorption: AbsorptionSide,
    pub stacked_imbalance: ImbalanceSide,
    pub failed_acceptance: bool,
    pub sweep_confirmed: bool,
    pub mss_active: bool,
    pub quality: DataQuality,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrderBookContext {
    pub obi_l5: Option<f64>,
    pub obi_l10: Option<f64>,
    pub obi_l20: Option<f64>,
    pub microprice: Option<f64>,
    pub spread_bps: Option<f64>,
    pub walls_above: Vec<f64>,
    pub walls_below: Vec<f64>,
    pub thin_zone_above: bool,
    pub thin_zone_below: bool,
    pub quality: DataQuality,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StrategyMarketContext {
    pub symbol: String,
    pub timestamp_ms: i64,
    pub price: f64,
    pub regime: Regime,
    pub atr: Option<f64>,
    pub volume_profile: VolumeProfileContext,
    pub vwap: VwapContext,
    pub flow: OrderFlowContext,
    pub orderbook: OrderBookContext,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StrategySignal {
    pub action: StrategyAction,
    pub strategy_id: Option<StrategyId>,
    pub side: Option<Side>,
    pub entry_price: Option<f64>,
    pub stop_price: Option<f64>,
    pub target_price: Option<f64>,
    pub score: f64,
    pub ttl_ms: i64,
    pub evidence: Vec<String>,
    pub missing: Vec<String>,
    pub invalidation: Vec<String>,
    pub created_at_ms: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StrategyConfig {
    pub enabled: bool,
    pub max_spread_bps: f64,
    pub max_vpin: f64,
    pub min_score: f64,
    pub default_ttl_ms: i64,
}

impl Default for StrategyConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            max_spread_bps: 2.0,
            max_vpin: 0.75,
            min_score: 0.70,
            default_ttl_ms: 5 * 60 * 1000,
        }
    }
}
