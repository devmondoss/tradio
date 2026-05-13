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

#[derive(Debug, Clone, Copy, Default)]
pub struct VwapPoint {
    pub vwap: f32,
    pub cum_volume: f64,
    pub cum_pv: f64,
}

pub struct VwapIndicator {
    cache: Caches,
    data: BasisSeries<VwapPoint>,
}

impl VwapIndicator {
    pub fn new() -> Self {
        Self {
            cache: Caches::default(),
            data: BasisSeries::default(),
        }
    }

    fn compute_vwap_time(datapoints: &BTreeMap<exchange::UnixMs, KlineDataPoint>) -> BTreeMap<exchange::UnixMs, VwapPoint> {
        let mut result = BTreeMap::new();
        let mut cum_volume: f64 = 0.0;
        let mut cum_pv: f64 = 0.0;

        for (&time, dp) in datapoints.iter() {
            let typical_price = {
                let h = f64::from(dp.kline.high.to_f32());
                let l = f64::from(dp.kline.low.to_f32());
                let c = f64::from(dp.kline.close.to_f32());
                (h + l + c) / 3.0
            };

            let vol = f64::from(f32::from(dp.kline.volume.total()));
            cum_volume += vol;
            cum_pv += typical_price * vol;

            let vwap = if cum_volume > 0.0 {
                (cum_pv / cum_volume) as f32
            } else {
                dp.kline.close.to_f32()
            };

            result.insert(time, VwapPoint {
                vwap,
                cum_volume,
                cum_pv,
            });
        }

        result
    }

    fn compute_vwap_tick(datapoints: &[TickAccumulation]) -> BTreeMap<u64, VwapPoint> {
        let mut result = BTreeMap::new();
        let mut cum_volume: f64 = 0.0;
        let mut cum_pv: f64 = 0.0;

        for (idx, dp) in datapoints.iter().enumerate() {
            let typical_price = {
                let h = f64::from(dp.kline.high.to_f32());
                let l = f64::from(dp.kline.low.to_f32());
                let c = f64::from(dp.kline.close.to_f32());
                (h + l + c) / 3.0
            };

            let vol = f64::from(f32::from(dp.kline.volume.total()));
            cum_volume += vol;
            cum_pv += typical_price * vol;

            let vwap = if cum_volume > 0.0 {
                (cum_pv / cum_volume) as f32
            } else {
                dp.kline.close.to_f32()
            };

            result.insert(idx as u64, VwapPoint {
                vwap,
                cum_volume,
                cum_pv,
            });
        }

        result
    }

    fn indicator_elem<'a>(
        &'a self,
        main_chart: &'a ViewState,
        visible_range: RangeInclusive<u64>,
    ) -> iced::Element<'a, Message> {
        let tooltip = |point: &VwapPoint, _next: Option<&VwapPoint>| {
            PlotTooltip::new(format!("VWAP: {}", format_with_commas(point.vwap)))
        };

        let value_fn = |point: &VwapPoint| point.vwap;

        let plot = LinePlot::new(value_fn)
            .stroke_width(1.5)
            .show_points(false)
            .padding(0.05)
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

impl KlineIndicatorImpl for VwapIndicator {
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
            |timeseries| Self::compute_vwap_time(&timeseries.datapoints),
            |tickaggr| Self::compute_vwap_tick(&tickaggr.datapoints),
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
