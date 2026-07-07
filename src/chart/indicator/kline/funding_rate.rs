use crate::chart::{
    Basis, Caches, Message, ViewState,
    indicator::{
        indicator_row,
        kline::{AvailabilityCause, FetchCtx, IndicatorAvailability, KlineIndicatorImpl},
        plot::{AnySeries, PlotTooltip, line::LinePlot},
    },
};
use crate::connector::fetcher::FetchRange;

use data::chart::{PlotData, kline::KlineDataPoint};
use exchange::adapter::Exchange;
use exchange::{FundingRate, Kline, Timeframe, UnixMs};

use iced::widget::{center, row, text};
use std::{collections::BTreeMap, ops::RangeInclusive};

pub struct FundingRateIndicator {
    cache: Caches,
    pub data: BTreeMap<UnixMs, f32>,
}

impl FundingRateIndicator {
    pub fn new() -> Self {
        Self {
            cache: Caches::default(),
            data: BTreeMap::new(),
        }
    }

    fn indicator_elem<'a>(
        &'a self,
        main_chart: &'a ViewState,
        visible_range: RangeInclusive<u64>,
    ) -> iced::Element<'a, Message> {
        if let Some(message) = self.unavailable_message(main_chart, "Funding Rate") {
            return center(text(message)).into();
        }

        let (earliest, latest) = visible_range.clone().into_inner();
        if latest < earliest {
            return row![].into();
        }

        let tooltip = |rate: &f32, _next: Option<&f32>| {
            let pct = *rate * 100.0;
            let sign = if pct >= 0.0 { "+" } else { "" };
            PlotTooltip::new(format!("Funding: {sign}{pct:.4}%"))
        };

        let value_fn = |v: &f32| *v;

        let plot = LinePlot::new(value_fn)
            .stroke_width(1.5)
            .show_points(true)
            .point_radius_factor(0.25)
            .padding(0.15)
            .with_tooltip(tooltip);

        indicator_row(
            main_chart,
            &self.cache,
            plot,
            AnySeries::forward_unix_ms(&self.data),
            visible_range,
        )
    }

    fn data_timerange(&self, latest_kline: UnixMs) -> (UnixMs, UnixMs) {
        let mut from_time = latest_kline;
        let mut to_time = UnixMs::ZERO;
        self.data.iter().for_each(|(time, _)| {
            from_time = from_time.min(*time);
            to_time = to_time.max(*time);
        });
        (from_time, to_time)
    }

    fn is_supported_exchange(exchange: Exchange) -> bool {
        matches!(exchange, Exchange::BinanceLinear | Exchange::BinanceInverse)
    }

    fn is_supported_timeframe(timeframe: Timeframe) -> bool {
        // Funding is emitted every 8h on Binance — only useful at M5 or higher
        timeframe >= Timeframe::M5
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

impl KlineIndicatorImpl for FundingRateIndicator {
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

    fn fetch_range(&mut self, ctx: &FetchCtx) -> Option<FetchRange> {
        let availability = Self::availability_for(
            Basis::Time(ctx.timeframe),
            ctx.main_chart.ticker_info.exchange(),
        );
        if !matches!(availability, IndicatorAvailability::Available) {
            return None;
        }

        let (data_earliest, data_latest) = self.data_timerange(ctx.kline_latest);

        if ctx.visible_earliest < data_earliest {
            return Some(FetchRange::FundingRate(
                ctx.prefetch_earliest,
                data_earliest,
            ));
        }

        if data_latest < ctx.kline_latest {
            return Some(FetchRange::FundingRate(
                data_latest.max(ctx.prefetch_earliest),
                ctx.kline_latest,
            ));
        }

        None
    }

    fn rebuild_from_source(&mut self, _source: &PlotData<KlineDataPoint>) {
        self.cache.clear_all();
    }

    fn on_insert_klines(&mut self, _klines: &[Kline], _source: &PlotData<KlineDataPoint>) {}

    fn on_insert_trades(
        &mut self,
        _trades: &[exchange::Trade],
        _old_dp_len: usize,
        _source: &PlotData<KlineDataPoint>,
    ) {
    }

    fn on_ticksize_change(&mut self, _source: &PlotData<KlineDataPoint>) {}
    fn on_basis_change(&mut self, _source: &PlotData<KlineDataPoint>) {
        self.data.clear();
        self.cache.clear_all();
    }

    fn on_funding_rate(&mut self, data: &[FundingRate]) {
        self.data.extend(data.iter().map(|fr| (fr.time, fr.rate)));
        self.cache.clear_all();
    }
}
