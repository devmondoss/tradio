use crate::chart::{
    Caches, Message, ViewState,
    indicator::{
        indicator_row,
        kline::{BasisSeries, BasisSeriesExt, KlineIndicatorImpl},
        plot::{
            PlotTooltip,
            bar::{BarClass, BarPlot},
        },
    },
};

use data::chart::{PlotData, kline::KlineDataPoint};
use exchange::{Kline, Trade};

use std::ops::RangeInclusive;

const LOOKBACK: usize = 20;

/// Current candle volume relative to the rolling mean of the last LOOKBACK candles.
/// 1.0 = average; 2.0 = twice the average.
#[derive(Debug, Clone, Copy, Default)]
pub struct RelativeVolumePoint {
    /// Ratio: current_volume / mean_of_last_N
    pub ratio: f32,
    /// Sign: positive = buy > sell, negative = sell > buy, zero = unknown
    pub direction: f32,
}

pub struct RelativeVolumeIndicator {
    cache: Caches,
    data: BasisSeries<RelativeVolumePoint>,
}

impl RelativeVolumeIndicator {
    pub fn new() -> Self {
        Self {
            cache: Caches::default(),
            data: BasisSeries::default(),
        }
    }

    fn compute_time(
        datapoints: &std::collections::BTreeMap<exchange::UnixMs, KlineDataPoint>,
    ) -> std::collections::BTreeMap<exchange::UnixMs, RelativeVolumePoint> {
        let entries: Vec<_> = datapoints.iter().collect();
        let mut result = std::collections::BTreeMap::new();

        for (i, (time, dp)) in entries.iter().enumerate() {
            let start = i.saturating_sub(LOOKBACK);
            let window = &entries[start..i];

            let mean = if window.is_empty() {
                f64::from(f32::from(dp.kline.volume.total()))
            } else {
                let sum: f64 = window
                    .iter()
                    .map(|(_, d)| f64::from(f32::from(d.kline.volume.total())))
                    .sum();
                sum / window.len() as f64
            };

            let current = f64::from(f32::from(dp.kline.volume.total()));
            let ratio = if mean > 0.0 {
                (current / mean) as f32
            } else {
                1.0
            };

            let direction = dp
                .kline
                .volume
                .buy_sell()
                .map(|(buy, sell)| {
                    let b = f32::from(buy);
                    let s = f32::from(sell);
                    if b > s {
                        1.0
                    } else if s > b {
                        -1.0
                    } else {
                        0.0
                    }
                })
                .unwrap_or(0.0);

            result.insert(**time, RelativeVolumePoint { ratio, direction });
        }

        result
    }

    fn compute_tick(
        datapoints: &[data::aggr::ticks::TickAccumulation],
    ) -> std::collections::BTreeMap<u64, RelativeVolumePoint> {
        let mut result = std::collections::BTreeMap::new();

        for (i, dp) in datapoints.iter().enumerate() {
            let start = i.saturating_sub(LOOKBACK);
            let window = &datapoints[start..i];

            let mean = if window.is_empty() {
                f64::from(f32::from(dp.kline.volume.total()))
            } else {
                let sum: f64 = window
                    .iter()
                    .map(|d| f64::from(f32::from(d.kline.volume.total())))
                    .sum();
                sum / window.len() as f64
            };

            let current = f64::from(f32::from(dp.kline.volume.total()));
            let ratio = if mean > 0.0 {
                (current / mean) as f32
            } else {
                1.0
            };

            let direction = dp
                .kline
                .volume
                .buy_sell()
                .map(|(buy, sell)| {
                    let b = f32::from(buy);
                    let s = f32::from(sell);
                    if b > s {
                        1.0
                    } else if s > b {
                        -1.0
                    } else {
                        0.0
                    }
                })
                .unwrap_or(0.0);

            result.insert(i as u64, RelativeVolumePoint { ratio, direction });
        }

        result
    }

    fn indicator_elem<'a>(
        &'a self,
        main_chart: &'a ViewState,
        visible_range: RangeInclusive<u64>,
    ) -> iced::Element<'a, Message> {
        let tooltip = |point: &RelativeVolumePoint, _next: Option<&RelativeVolumePoint>| {
            PlotTooltip::new(format!("Rel Vol: {:.2}x", point.ratio))
        };

        let bar_kind = |point: &RelativeVolumePoint| {
            if point.direction != 0.0 {
                BarClass::Overlay {
                    overlay: point.ratio * point.direction,
                }
            } else {
                BarClass::Single
            }
        };

        let value_fn = |point: &RelativeVolumePoint| point.ratio;

        let plot = BarPlot::new(value_fn, bar_kind)
            .bar_width_factor(0.9)
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

impl KlineIndicatorImpl for RelativeVolumeIndicator {
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
            |ts| Self::compute_time(&ts.datapoints),
            |ta| Self::compute_tick(&ta.datapoints),
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
