//! Market Pressure — exponential-decay smoothed taker imbalance, range [-100, +100].
//!
//! On each bar close: `pressure = pressure × (1 − α) + taker_imbalance × α × 100`
//!
//! α = 0.30 → half-life ≈ 2 bars (∼10 min on M5). Increasing α makes the
//! indicator more reactive; decreasing it emphasises trend over momentum.
//!
//! Interpretation:
//!  - +100 → sustained aggressive buying every bar.
//!  - −100 → sustained aggressive selling.
//!  - Crossing zero = buying/selling pressure regime change.
//!  - Divergence with price = hidden buying/selling pressure.
use crate::chart::{
    Caches, Message, ViewState,
    indicator::{
        indicator_row,
        kline::{BasisSeries, BasisSeriesExt, KlineIndicatorImpl},
        plot::{PlotTooltip, line::LinePlot},
    },
};

use data::chart::{PlotData, kline::KlineDataPoint};
use exchange::{Kline, Trade};
use std::ops::RangeInclusive;

/// EMA decay factor. 0.30 → half-life ≈ 2 bars.
const ALPHA: f64 = 0.30;

pub struct MarketPressureIndicator {
    cache: Caches,
    data: BasisSeries<f32>,
    pressure: f64,
}

impl MarketPressureIndicator {
    pub fn new() -> Self {
        Self {
            cache: Caches::default(),
            data: BasisSeries::default(),
            pressure: 0.0,
        }
    }

    fn taker_imbalance(kline: &Kline) -> f64 {
        match kline.volume.buy_sell() {
            Some((buy, sell)) => {
                let b = f64::from(f32::from(buy));
                let s = f64::from(f32::from(sell));
                let total = b + s;
                if total > 0.0 { (b - s) / total } else { 0.0 }
            }
            None => 0.0,
        }
    }

    /// Rebuild the full pressure series from historical kline data.
    fn compute_time(
        datapoints: &std::collections::BTreeMap<exchange::UnixMs, KlineDataPoint>,
    ) -> (std::collections::BTreeMap<exchange::UnixMs, f32>, f64) {
        let mut result = std::collections::BTreeMap::new();
        let mut pressure = 0.0_f64;
        for (time, dp) in datapoints {
            let imb = Self::taker_imbalance(&dp.kline);
            pressure = pressure * (1.0 - ALPHA) + imb * ALPHA * 100.0;
            result.insert(*time, pressure as f32);
        }
        (result, pressure)
    }

    fn compute_tick(
        datapoints: &[data::aggr::ticks::TickAccumulation],
    ) -> (std::collections::BTreeMap<u64, f32>, f64) {
        let mut result = std::collections::BTreeMap::new();
        let mut pressure = 0.0_f64;
        for (i, dp) in datapoints.iter().enumerate() {
            let imb = Self::taker_imbalance(&dp.kline);
            pressure = pressure * (1.0 - ALPHA) + imb * ALPHA * 100.0;
            result.insert(i as u64, pressure as f32);
        }
        (result, pressure)
    }

    fn indicator_elem<'a>(
        &'a self,
        main_chart: &'a ViewState,
        visible_range: RangeInclusive<u64>,
    ) -> iced::Element<'a, Message> {
        let tooltip = |v: &f32, _: Option<&f32>| {
            let emoji = if *v > 20.0 { "▲" } else if *v < -20.0 { "▼" } else { "~" };
            PlotTooltip::new(format!("Pressure: {:+.1} {}", v, emoji))
        };

        let plot = LinePlot::new(|v: &f32| *v)
            .stroke_width(1.5)
            .show_points(false)
            .padding(0.05)
            .with_tooltip(tooltip);

        indicator_row(main_chart, &self.cache, plot, self.data.as_plot_series(), visible_range)
    }
}

impl KlineIndicatorImpl for MarketPressureIndicator {
    fn clear_all_caches(&mut self) { self.cache.clear_all(); }
    fn clear_crosshair_caches(&mut self) { self.cache.clear_crosshair(); }

    fn element<'a>(
        &'a self, chart: &'a ViewState, visible_range: RangeInclusive<u64>,
    ) -> iced::Element<'a, Message> {
        self.indicator_elem(chart, visible_range)
    }

    fn rebuild_from_source(&mut self, source: &PlotData<KlineDataPoint>) {
        match source {
            PlotData::TimeBased(ts) => {
                let (map, last_pressure) = Self::compute_time(&ts.datapoints);
                self.data = BasisSeries::Time(map);
                self.pressure = last_pressure;
            }
            PlotData::TickBased(ta) => {
                let (map, last_pressure) = Self::compute_tick(&ta.datapoints);
                self.data = BasisSeries::Tick(map);
                self.pressure = last_pressure;
            }
        }
        self.clear_all_caches();
    }

    fn on_insert_klines(&mut self, klines: &[Kline], _source: &PlotData<KlineDataPoint>) {
        for k in klines {
            let imb = Self::taker_imbalance(k);
            self.pressure = self.pressure * (1.0 - ALPHA) + imb * ALPHA * 100.0;
            let point = self.pressure as f32;
            match &mut self.data {
                BasisSeries::Time(map) => { map.insert(k.time, point); }
                BasisSeries::Tick(map) => {
                    let idx = map.len() as u64;
                    map.insert(idx, point);
                }
            }
        }
        self.clear_all_caches();
    }

    fn on_insert_trades(
        &mut self, _trades: &[Trade], _old_dp_len: usize, _source: &PlotData<KlineDataPoint>,
    ) {
        // Pressure is updated per bar close only (on_insert_klines), not intrabar.
    }

    fn on_ticksize_change(&mut self, source: &PlotData<KlineDataPoint>) { self.rebuild_from_source(source); }
    fn on_basis_change(&mut self, source: &PlotData<KlineDataPoint>) { self.rebuild_from_source(source); }
}
