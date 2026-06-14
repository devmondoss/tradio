use std::fmt::{self, Debug, Display};

use enum_map::Enum;
use exchange::adapter::MarketKind;
use serde::{Deserialize, Serialize};

pub trait Indicator: PartialEq + Display + 'static {
    fn for_market(market: MarketKind) -> &'static [Self]
    where
        Self: Sized;
}

#[derive(Debug, Clone, Copy, PartialEq, Deserialize, Serialize, Eq, Enum)]
pub enum KlineIndicator {
    Volume,
    CumulativeDelta,
    OpenInterest,
    OiDelta,
    OiZScore,
    FundingRate,
    Vwap,
    VolumeProfile,
    Atr,
    RelativeVolume,
    // ── New order flow indicators ──────────────────────────────────────────
    /// Tape activity — normalized trade count per bar with anomaly detection.
    SpeedOfTape,
    /// Large-trade delta — cumulative delta of trades above adaptive size threshold.
    CvdLarge,
    /// Market pressure — exponential-decay smoothed taker imbalance [-100, +100].
    MarketPressure,
}

impl Indicator for KlineIndicator {
    fn for_market(market: MarketKind) -> &'static [Self] {
        match market {
            MarketKind::Spot => &Self::FOR_SPOT,
            MarketKind::LinearPerps | MarketKind::InversePerps => &Self::FOR_PERPS,
        }
    }
}

impl KlineIndicator {
    // Indicator togglers on UI menus depend on these arrays.
    // Every variant needs to be in either SPOT, PERPS or both.
    /// Indicators that can be used with spot market tickers
    const FOR_SPOT: [KlineIndicator; 9] = [
        KlineIndicator::Volume,
        KlineIndicator::RelativeVolume,
        KlineIndicator::CumulativeDelta,
        KlineIndicator::Vwap,
        KlineIndicator::VolumeProfile,
        KlineIndicator::Atr,
        KlineIndicator::SpeedOfTape,
        KlineIndicator::CvdLarge,
        KlineIndicator::MarketPressure,
    ];
    /// Indicators that can be used with perpetual swap market tickers
    const FOR_PERPS: [KlineIndicator; 13] = [
        KlineIndicator::Volume,
        KlineIndicator::RelativeVolume,
        KlineIndicator::CumulativeDelta,
        KlineIndicator::OpenInterest,
        KlineIndicator::OiDelta,
        KlineIndicator::OiZScore,
        KlineIndicator::FundingRate,
        KlineIndicator::Vwap,
        KlineIndicator::VolumeProfile,
        KlineIndicator::Atr,
        KlineIndicator::SpeedOfTape,
        KlineIndicator::CvdLarge,
        KlineIndicator::MarketPressure,
    ];
}

impl Display for KlineIndicator {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        match self {
            KlineIndicator::Volume => write!(f, "Volume"),
            KlineIndicator::CumulativeDelta => write!(f, "CVD"),
            KlineIndicator::OpenInterest => write!(f, "Open Interest"),
            KlineIndicator::OiDelta => write!(f, "OI Delta"),
            KlineIndicator::OiZScore => write!(f, "OI Z-Score"),
            KlineIndicator::FundingRate => write!(f, "Funding Rate"),
            KlineIndicator::Vwap => write!(f, "VWAP"),
            KlineIndicator::VolumeProfile => write!(f, "Vol Profile"),
            KlineIndicator::Atr => write!(f, "ATR"),
            KlineIndicator::RelativeVolume => write!(f, "Rel Volume"),
            KlineIndicator::SpeedOfTape => write!(f, "Speed of Tape"),
            KlineIndicator::CvdLarge => write!(f, "CVD Large"),
            KlineIndicator::MarketPressure => write!(f, "Market Pressure"),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Deserialize, Serialize, Eq, Enum)]
pub enum HeatmapIndicator {
    Volume,
}

impl Indicator for HeatmapIndicator {
    fn for_market(market: MarketKind) -> &'static [Self] {
        match market {
            MarketKind::Spot => &Self::FOR_SPOT,
            MarketKind::LinearPerps | MarketKind::InversePerps => &Self::FOR_PERPS,
        }
    }
}

impl HeatmapIndicator {
    // Indicator togglers on UI menus depend on these arrays.
    // Every variant needs to be in either SPOT, PERPS or both.
    /// Indicators that can be used with spot market tickers
    const FOR_SPOT: [HeatmapIndicator; 1] = [HeatmapIndicator::Volume];
    /// Indicators that can be used with perpetual swap market tickers
    const FOR_PERPS: [HeatmapIndicator; 1] = [HeatmapIndicator::Volume];
}

impl Display for HeatmapIndicator {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        match self {
            HeatmapIndicator::Volume => write!(f, "Volume"),
        }
    }
}

#[derive(Debug, Clone, Copy)]
/// Temporary workaround,
/// represents any indicator type in the UI
pub enum UiIndicator {
    Heatmap(HeatmapIndicator),
    Kline(KlineIndicator),
}

impl From<KlineIndicator> for UiIndicator {
    fn from(k: KlineIndicator) -> Self {
        UiIndicator::Kline(k)
    }
}

impl From<HeatmapIndicator> for UiIndicator {
    fn from(h: HeatmapIndicator) -> Self {
        UiIndicator::Heatmap(h)
    }
}
