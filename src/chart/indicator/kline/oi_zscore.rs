use crate::chart::{
    Basis, Caches, Message, ViewState,
    indicator::{
        indicator_row,
        kline::{AvailabilityCause, IndicatorAvailability, KlineIndicatorImpl},
        plot::{AnySeries, PlotTooltip, line::LinePlot},
    },
};
use data::chart::{PlotData, kline::KlineDataPoint};
use exchange::adapter::Exchange;
use exchange::{Kline, OpenInterest, Timeframe, Trade, UnixMs};

use std::{collections::BTreeMap, ops::RangeInclusive};

/// Rolling window for z-score computation.
const ZSCORE_WINDOW: usize = 20;

pub struct OiZScoreIndicator {
    cache: Caches,
    /// Raw OI keyed by time (same data as OpenInterestIndicator).
    raw: BTreeMap<UnixMs, f32>,
    /// Computed rolling z-scores.
    zscore: BTreeMap<UnixMs, f32>,
}

impl OiZScoreIndicator {
    pub fn new() -> Self {
        Self {
            cache: Caches::default(),
            raw: BTreeMap::new(),
            zscore: BTreeMap::new(),
        }
    }

    fn recompute_zscore(&mut self) {
        self.zscore.clear();
        let entries: Vec<_> = self.raw.iter().collect();
        if entries.len() < 2 {
            return;
        }

        for i in 1..entries.len() {
            let (&time, _) = entries[i];
            let window_start = i.saturating_sub(ZSCORE_WINDOW - 1);
            let window: Vec<f32> = entries[window_start..=i]
                .iter()
                .map(|&(_, &v)| v)
                .collect();

            let n = window.len() as f32;
            let mean = window.iter().sum::<f32>() / n;
            let variance = window.iter().map(|&v| (v - mean).powi(2)).sum::<f32>() / n;
            let std = variance.sqrt();

            let current = *entries[i].1;
            let z = if std > 1e-10 {
                (current - mean) / std
            } else {
                0.0
            };
            self.zscore.insert(time, z);
        }
    }

    fn indicator_elem<'a>(
        &'a self,
        main_chart: &'a ViewState,
        visible_range: RangeInclusive<u64>,
    ) -> iced::Element<'a, Message> {
        if let Some(message) = self.unavailable_message(main_chart, "OI Z-Score") {
            return iced::widget::center(iced::widget::text(message)).into();
        }

        let (earliest, latest) = visible_range.clone().into_inner();
        if latest < earliest {
            return iced::widget::row![].into();
        }

        let tooltip = |z: &f32, _next: Option<&f32>| {
            let sign = if *z >= 0.0 { "+" } else { "" };
            PlotTooltip::new(format!("OI Z: {sign}{z:.2}σ"))
        };

        let value_fn = |v: &f32| *v;

        let plot = LinePlot::new(value_fn)
            .stroke_width(1.5)
            .show_points(true)
            .point_radius_factor(0.2)
            .padding(0.15)
            .with_tooltip(tooltip);

        indicator_row(
            main_chart,
            &self.cache,
            plot,
            AnySeries::forward_unix_ms(&self.zscore),
            visible_range,
        )
    }

    fn is_supported_exchange(exchange: Exchange) -> bool {
        exchange.is_perps()
            && exchange != Exchange::HyperliquidLinear
            && exchange != Exchange::MexcLinear
            && exchange != Exchange::MexcInverse
    }

    fn is_supported_timeframe(timeframe: Timeframe) -> bool {
        timeframe >= Timeframe::M5 && timeframe <= Timeframe::H4 && timeframe != Timeframe::H2
    }

    fn availability_for(basis: Basis, exchange: Exchange) -> IndicatorAvailability {
        match basis {
            Basis::Tick(_) => IndicatorAvailability::Unavailable(AvailabilityCause::Basis(basis)),
            Basis::Time(timeframe) => {
                if !Self::is_supported_exchange(exchange) {
                    IndicatorAvailability::Unavailable(AvailabilityCause::Exchange(exchange))
                } else if !Self::is_supported_timeframe(timeframe) {
                    IndicatorAvailability::Unavailable(AvailabilityCause::Timeframe(timeframe))
                } else {
                    IndicatorAvailability::Available
                }
            }
        }
    }
}

impl KlineIndicatorImpl for OiZScoreIndicator {
    fn clear_all_caches(&mut self) {
        self.cache.clear_all();
    }

    fn clear_crosshair_caches(&mut self) {
        self.cache.clear_crosshair();
    }

    fn element<'a>(
        &'a self,
        chart: &'a ViewState,
        visible_range: RangeInclusive<u64>,
    ) -> iced::Element<'a, Message> {
        self.indicator_elem(chart, visible_range)
    }

    fn availability(&self, chart: &ViewState) -> IndicatorAvailability {
        Self::availability_for(chart.basis, chart.ticker_info.exchange())
    }

    fn rebuild_from_source(&mut self, _source: &PlotData<KlineDataPoint>) {
        self.cache.clear_all();
    }

    fn on_insert_klines(&mut self, _klines: &[Kline], _source: &PlotData<KlineDataPoint>) {}

    fn on_insert_trades(
        &mut self,
        _trades: &[Trade],
        _old_dp_len: usize,
        _source: &PlotData<KlineDataPoint>,
    ) {
    }

    fn on_ticksize_change(&mut self, _source: &PlotData<KlineDataPoint>) {}

    fn on_basis_change(&mut self, _source: &PlotData<KlineDataPoint>) {
        self.raw.clear();
        self.zscore.clear();
        self.cache.clear_all();
    }

    fn on_open_interest(&mut self, data: &[OpenInterest]) {
        self.raw.extend(data.iter().map(|oi| (oi.time, oi.value)));
        self.recompute_zscore();
        self.cache.clear_all();
    }
}
