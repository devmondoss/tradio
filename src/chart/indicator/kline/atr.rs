use crate::chart::{
    Caches, Message, ViewState,
    indicator::{
        indicator_row,
        kline::{BasisSeries, BasisSeriesExt, KlineIndicatorImpl},
        plot::{PlotTooltip, line::LinePlot},
    },
};

use data::aggr::ticks::TickAccumulation;
use data::chart::{PlotData, kline::KlineDataPoint};
use data::util::format_with_commas;
use exchange::{Kline, Trade};

use std::collections::BTreeMap;
use std::ops::RangeInclusive;

const ATR_PERIOD: usize = 14;

pub struct AtrIndicator {
    cache: Caches,
    data: BasisSeries<f32>,
}

impl AtrIndicator {
    pub fn new() -> Self {
        Self {
            cache: Caches::default(),
            data: BasisSeries::default(),
        }
    }

    fn compute_atr_time(datapoints: &BTreeMap<exchange::UnixMs, KlineDataPoint>) -> BTreeMap<exchange::UnixMs, f32> {
        let entries: Vec<_> = datapoints.iter().collect();
        let mut result = BTreeMap::new();

        if entries.is_empty() {
            return result;
        }

        let mut prev_close = entries[0].1.kline.close.to_f32();
        let mut atr_values: Vec<f32> = Vec::new();
        let mut current_atr: f32 = 0.0;

        for (i, (&time, dp)) in entries.iter().enumerate() {
            let high = dp.kline.high.to_f32();
            let low = dp.kline.low.to_f32();

            let tr = if i == 0 {
                high - low
            } else {
                f32::max(
                    high - low,
                    f32::max((high - prev_close).abs(), (low - prev_close).abs()),
                )
            };

            if atr_values.len() < ATR_PERIOD {
                atr_values.push(tr);
                if atr_values.len() == ATR_PERIOD {
                    current_atr = atr_values.iter().sum::<f32>() / ATR_PERIOD as f32;
                } else {
                    current_atr = atr_values.iter().sum::<f32>() / atr_values.len() as f32;
                }
            } else {
                current_atr = (current_atr * (ATR_PERIOD - 1) as f32 + tr) / ATR_PERIOD as f32;
            }

            result.insert(*time, current_atr);
            prev_close = dp.kline.close.to_f32();
        }

        result
    }

    fn compute_atr_tick(datapoints: &[TickAccumulation]) -> BTreeMap<u64, f32> {
        let mut result = BTreeMap::new();

        if datapoints.is_empty() {
            return result;
        }

        let mut prev_close = datapoints[0].kline.close.to_f32();
        let mut atr_values: Vec<f32> = Vec::new();
        let mut current_atr: f32 = 0.0;

        for (i, dp) in datapoints.iter().enumerate() {
            let high = dp.kline.high.to_f32();
            let low = dp.kline.low.to_f32();

            let tr = if i == 0 {
                high - low
            } else {
                f32::max(
                    high - low,
                    f32::max((high - prev_close).abs(), (low - prev_close).abs()),
                )
            };

            if atr_values.len() < ATR_PERIOD {
                atr_values.push(tr);
                if atr_values.len() == ATR_PERIOD {
                    current_atr = atr_values.iter().sum::<f32>() / ATR_PERIOD as f32;
                } else {
                    current_atr = atr_values.iter().sum::<f32>() / atr_values.len() as f32;
                }
            } else {
                current_atr = (current_atr * (ATR_PERIOD - 1) as f32 + tr) / ATR_PERIOD as f32;
            }

            result.insert(i as u64, current_atr);
            prev_close = dp.kline.close.to_f32();
        }

        result
    }

    fn indicator_elem<'a>(
        &'a self,
        main_chart: &'a ViewState,
        visible_range: RangeInclusive<u64>,
    ) -> iced::Element<'a, Message> {
        let tooltip = |atr: &f32, _next: Option<&f32>| {
            PlotTooltip::new(format!("ATR(14): {}", format_with_commas(*atr)))
        };

        let value_fn = |atr: &f32| *atr;

        let plot = LinePlot::new(value_fn)
            .stroke_width(1.0)
            .show_points(false)
            .padding(0.10)
            .with_tooltip(tooltip);

        indicator_row(
            main_chart,
            &self.cache,
            plot,
            self.data.as_plot_series(),
            visible_range,
        )
    }
}

impl KlineIndicatorImpl for AtrIndicator {
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

    fn rebuild_from_source(&mut self, source: &PlotData<KlineDataPoint>) {
        self.data = source.map_basis_series(
            |timeseries| Self::compute_atr_time(&timeseries.datapoints),
            |tickaggr| Self::compute_atr_tick(&tickaggr.datapoints),
        );
        self.clear_all_caches();
    }

    fn on_insert_klines(&mut self, _klines: &[Kline], source: &PlotData<KlineDataPoint>) {
        self.rebuild_from_source(source);
    }

    fn on_insert_trades(
        &mut self,
        _trades: &[Trade],
        _old_dp_len: usize,
        source: &PlotData<KlineDataPoint>,
    ) {
        self.rebuild_from_source(source);
    }

    fn on_ticksize_change(&mut self, source: &PlotData<KlineDataPoint>) {
        self.rebuild_from_source(source);
    }

    fn on_basis_change(&mut self, source: &PlotData<KlineDataPoint>) {
        self.rebuild_from_source(source);
    }
}
