use crate::chart::{
    Basis, Caches, Message, ViewState,
    indicator::{
        indicator_row,
        kline::{AvailabilityCause, IndicatorAvailability, KlineIndicatorImpl},
        plot::{
            AnySeries, PlotTooltip,
            bar::{BarClass, BarPlot, Baseline},
        },
    },
};
use crate::connector::fetcher::FetchRange;

use data::chart::{PlotData, kline::KlineDataPoint};
use data::util::format_with_commas;
use exchange::adapter::Exchange;
use exchange::{Kline, Timeframe, Trade, UnixMs};

use std::{collections::BTreeMap, ops::RangeInclusive};

pub struct OiDeltaIndicator {
    cache: Caches,
    raw: BTreeMap<UnixMs, f32>,
    delta: BTreeMap<UnixMs, f32>,
}

impl OiDeltaIndicator {
    pub fn new() -> Self {
        Self {
            cache: Caches::default(),
            raw: BTreeMap::new(),
            delta: BTreeMap::new(),
        }
    }

    fn recompute_delta(&mut self) {
        self.delta.clear();
        let entries: Vec<_> = self.raw.iter().collect();
        for i in 1..entries.len() {
            let (&time, &curr) = entries[i];
            let (_, &prev) = entries[i - 1];
            self.delta.insert(time, curr - prev);
        }
    }

    fn indicator_elem<'a>(
        &'a self,
        main_chart: &'a ViewState,
        visible_range: RangeInclusive<u64>,
    ) -> iced::Element<'a, Message> {
        if let Some(message) = self.unavailable_message(main_chart, "OI Delta") {
            return iced::widget::center(iced::widget::text(message)).into();
        }

        let (earliest, latest) = visible_range.clone().into_inner();
        if latest < earliest {
            return iced::widget::row![].into();
        }

        let tooltip = |delta: &f32, _next: Option<&f32>| {
            let sign = if *delta >= 0.0 { "+" } else { "" };
            PlotTooltip::new(format!("OI Δ: {}{}", sign, format_with_commas(*delta)))
        };

        let value_fn = |delta: &f32| delta.abs();
        let classify = |delta: &f32| BarClass::Overlay { overlay: *delta };

        let plot = BarPlot::new(value_fn, classify)
            .bar_width_factor(0.85)
            .baseline(Baseline::Zero)
            .padding(0.08)
            .with_tooltip(tooltip);

        indicator_row(
            main_chart,
            &self.cache,
            plot,
            AnySeries::forward_unix_ms(&self.delta),
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

impl KlineIndicatorImpl for OiDeltaIndicator {
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

    fn fetch_range(&mut self, ctx: &super::FetchCtx) -> Option<FetchRange> {
        let availability = Self::availability_for(
            Basis::Time(ctx.timeframe),
            ctx.main_chart.ticker_info.exchange(),
        );
        if !matches!(availability, IndicatorAvailability::Available) {
            return None;
        }

        let from_time = self.raw.keys().next().copied().unwrap_or(ctx.kline_latest);
        let to_time = self.raw.keys().last().copied().unwrap_or(UnixMs::ZERO);

        if ctx.visible_earliest < from_time {
            return Some(FetchRange::OpenInterest(ctx.prefetch_earliest, from_time));
        }

        if to_time < ctx.kline_latest {
            return Some(FetchRange::OpenInterest(
                to_time.max(ctx.prefetch_earliest),
                ctx.kline_latest,
            ));
        }

        None
    }

    fn rebuild_from_source(&mut self, _source: &PlotData<KlineDataPoint>) {
        self.clear_all_caches();
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
        self.delta.clear();
        self.cache.clear_all();
    }

    fn on_open_interest(&mut self, data: &[exchange::OpenInterest]) {
        self.raw.extend(data.iter().map(|oi| (oi.time, oi.value)));
        self.recompute_delta();
        self.cache.clear_all();
    }
}
