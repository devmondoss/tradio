use crate::chart::{Basis, Message, ViewState};
use crate::connector::fetcher::FetchRange;

use data::chart::indicator::KlineIndicator;
use data::chart::kline::KlineDataPoint;
use data::chart::{BasisSeries, PlotData};
use exchange::adapter::Exchange;
use exchange::{Kline, Timeframe, Trade, UnixMs};

use super::plot::AnySeries;

pub mod atr;
pub mod cumulative_delta;
pub mod funding_rate;
pub mod oi_delta;
pub mod oi_zscore;
pub mod open_interest;
pub mod relative_volume;
pub mod volume;
pub mod volume_profile;
pub mod vwap;

/// UI adapter methods for converting domain `BasisSeries` into plot-ready series.
trait BasisSeriesExt<T> {
    fn as_plot_series(&self) -> AnySeries<'_, T>;
}

impl<T> BasisSeriesExt<T> for BasisSeries<T> {
    fn as_plot_series(&self) -> AnySeries<'_, T> {
        match self {
            BasisSeries::Time(data) => AnySeries::forward_unix_ms(data),
            BasisSeries::Tick(data) => AnySeries::reversed_u64(data),
        }
    }
}

#[allow(dead_code)]
#[derive(Debug, Clone, Default, PartialEq)]
pub enum IndicatorAvailability {
    /// Indicator can be rendered normally.
    #[default]
    Available,
    /// Availability cannot be determined yet (e.g. no datapoints loaded).
    Unknown,
    /// Indicator cannot be rendered for the current source/context.
    Unavailable(AvailabilityCause),
}

#[allow(dead_code)]
#[derive(Debug, Clone, PartialEq)]
pub enum AvailabilityCause {
    Exchange(Exchange),
    Timeframe(Timeframe),
    Basis(Basis),
    TradeData,
}

impl IndicatorAvailability {
    pub fn unavailable_message(&self, indicator: &str) -> Option<String> {
        match self {
            IndicatorAvailability::Available | IndicatorAvailability::Unknown => None,
            IndicatorAvailability::Unavailable(cause) => Some(match cause {
                AvailabilityCause::Exchange(exchange) => {
                    format!("{indicator} is not available for {exchange}.")
                }
                AvailabilityCause::Timeframe(timeframe) => {
                    format!("{indicator} is not available on {timeframe} timeframe.")
                }
                AvailabilityCause::Basis(Basis::Tick(_)) => {
                    format!("{indicator} is not available for tick charts.")
                }
                AvailabilityCause::Basis(basis) => {
                    format!("{indicator} is not available on {basis} basis.")
                }
                AvailabilityCause::TradeData => {
                    format!("{indicator} requires directional trade-volume data.")
                }
            }),
        }
    }
}

/// Whether this indicator renders as an overlay on the main chart canvas
/// rather than in its own separate panel below.
pub fn is_overlay_indicator(indicator: KlineIndicator) -> bool {
    matches!(
        indicator,
        KlineIndicator::Vwap | KlineIndicator::VolumeProfile
    )
}

pub trait KlineIndicatorImpl {
    /// Clear all caches for a full redraw
    fn clear_all_caches(&mut self);

    /// Clear caches related to crosshair only
    /// e.g. tooltips and scale labels for a partial redraw
    fn clear_crosshair_caches(&mut self);

    fn element<'a>(
        &'a self,
        chart: &'a ViewState,
        visible_range: std::ops::RangeInclusive<u64>,
    ) -> iced::Element<'a, Message>;

    /// Return price-level points for overlay drawing on the main chart.
    /// Each point is (interval_key, price_f32).
    fn overlay_line_points(&self, _earliest: u64, _latest: u64) -> Vec<(u64, f32)> {
        vec![]
    }

    /// Return additional named line series for overlay drawing (e.g. session VWAPs).
    /// Each entry is (points, rgba_color). Points are (interval_key, price_f32).
    fn overlay_extra_lines(&self, _earliest: u64, _latest: u64) -> Vec<(Vec<(u64, f32)>, [f32; 4])> {
        vec![]
    }

    /// Return horizontal price levels to draw on the main chart.
    /// Each entry is (price_f32, color_rgba).
    fn overlay_levels(&self) -> Vec<(f32, [f32; 4])> {
        vec![]
    }

    /// Return band data for shaded region overlays.
    /// Each entry is (interval_key, upper_f32, lower_f32).
    fn overlay_bands(&self, _earliest: u64, _latest: u64) -> Vec<Vec<(u64, f32, f32)>> {
        vec![]
    }

    /// Return pre-binned histogram bars for Volume Profile rendering.
    fn overlay_volume_profile(&self) -> &[volume_profile::ProfileBar] {
        &[]
    }

    /// Return the maximum volume bin value (for normalizing bar widths).
    fn overlay_volume_profile_max(&self) -> f64 {
        0.0
    }

    fn availability(&self, _chart: &ViewState) -> IndicatorAvailability {
        IndicatorAvailability::Available
    }

    fn unavailable_message(&self, chart: &ViewState, indicator: &str) -> Option<String> {
        self.availability(chart).unavailable_message(indicator)
    }

    /// Latest VWAP value for the current session.
    fn latest_vwap(&self) -> Option<f64> {
        None
    }

    /// Latest Volume Profile levels: (poc, vah, val).
    fn latest_vol_profile_levels(&self) -> Option<(f64, f64, f64)> {
        None
    }

    /// Latest CVD values: (cumulative, candle_delta).
    fn latest_cvd(&self) -> Option<(f64, f64)> {
        None
    }

    /// Latest candle volume: (buy, sell). None when buy/sell split unavailable.
    fn latest_volume(&self) -> Option<(f64, f64)> {
        None
    }

    /// Latest ATR(14) value.
    fn latest_atr(&self) -> Option<f64> {
        None
    }

    /// CVD linear-regression slope over last N candles (positive = rising CVD).
    fn latest_cvd_slope(&self) -> Option<f64> {
        None
    }

    /// Candle-level VPIN approximation: mean(|delta|/vol) over last 50 candles.
    fn latest_vpin(&self) -> Option<f64> {
        None
    }

    /// HVN and LVN price levels within `atr`-scaled distance of `price`.
    /// Returns (hvn_nearby, lvn_nearby).
    fn latest_hvn_lvn_nearby(&self, _price: f64, _atr: f64) -> (Vec<f64>, Vec<f64>) {
        (vec![], vec![])
    }

    /// Latest anchored-VWAP (BOS anchor = most recent swing pivot).
    fn latest_avwap_bos(&self) -> Option<f64> {
        None
    }

    /// Per-candle delta for the last `n` candles, oldest-first.
    /// Aligned with the same candle order used for recent_highs/recent_lows in kline.rs.
    /// Returns empty vec for indicators that do not track per-candle delta.
    fn recent_delta_slice(&self, _n: usize) -> Vec<f64> {
        Vec::new()
    }

    /// Expose existing OI data for bootstrapping dependent indicators.
    /// Only implemented by OpenInterestIndicator.
    fn oi_snapshot(&self) -> Option<Vec<exchange::OpenInterest>> {
        None
    }

    /// If the indicator needs data fetching, return the required range
    fn fetch_range(&mut self, _ctx: &FetchCtx) -> Option<FetchRange> {
        None
    }

    /// Rebuild data using kline(OHLCV) source
    fn rebuild_from_source(&mut self, _source: &PlotData<KlineDataPoint>) {}

    fn on_insert_klines(&mut self, _klines: &[Kline], _source: &PlotData<KlineDataPoint>) {}

    fn on_insert_trades(
        &mut self,
        _trades: &[Trade],
        _old_dp_len: usize,
        _source: &PlotData<KlineDataPoint>,
    ) {
    }

    fn on_ticksize_change(&mut self, _source: &PlotData<KlineDataPoint>) {}

    /// Timeframe/tick interval has changed
    fn on_basis_change(&mut self, _source: &PlotData<KlineDataPoint>) {}

    fn on_open_interest(&mut self, _pairs: &[exchange::OpenInterest]) {}

    /// Set a user-placed AVWAP anchor timestamp. ts=0 clears the anchor.
    fn set_user_avwap_anchor(&mut self, _ts: u64, _source: &PlotData<KlineDataPoint>) {}

    fn on_funding_rate(&mut self, _data: &[exchange::FundingRate]) {}

    /// Called each frame with the current visible time/tick range.
    /// Override to rebuild view-dependent data (e.g. VRVP histogram).
    fn update_visible_range(
        &mut self,
        _earliest: u64,
        _latest: u64,
        _source: &PlotData<KlineDataPoint>,
    ) {
    }
}

pub struct FetchCtx<'a> {
    pub main_chart: &'a ViewState,
    pub timeframe: Timeframe,
    pub visible_earliest: UnixMs,
    pub kline_latest: UnixMs,
    pub prefetch_earliest: UnixMs,
}

pub fn make_empty(which: KlineIndicator) -> Box<dyn KlineIndicatorImpl> {
    match which {
        KlineIndicator::Volume => Box::new(super::kline::volume::VolumeIndicator::new()),
        KlineIndicator::CumulativeDelta => {
            Box::new(super::kline::cumulative_delta::CumulativeDeltaIndicator::new())
        }
        KlineIndicator::OpenInterest => {
            Box::new(super::kline::open_interest::OpenInterestIndicator::new())
        }
        KlineIndicator::OiDelta => Box::new(super::kline::oi_delta::OiDeltaIndicator::new()),
        KlineIndicator::OiZScore => {
            Box::new(super::kline::oi_zscore::OiZScoreIndicator::new())
        }
        KlineIndicator::FundingRate => {
            Box::new(super::kline::funding_rate::FundingRateIndicator::new())
        }
        KlineIndicator::Vwap => Box::new(super::kline::vwap::VwapIndicator::new()),
        KlineIndicator::VolumeProfile => {
            Box::new(super::kline::volume_profile::VolumeProfileIndicator::new())
        }
        KlineIndicator::Atr => Box::new(super::kline::atr::AtrIndicator::new()),
        KlineIndicator::RelativeVolume => {
            Box::new(super::kline::relative_volume::RelativeVolumeIndicator::new())
        }
    }
}
