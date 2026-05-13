use crate::chart::{
    Caches, Message, ViewState,
    indicator::{
        indicator_row,
        kline::{BasisSeries, BasisSeriesExt, IndicatorAvailability, KlineIndicatorImpl},
        plot::{PlotTooltip, line::LinePlot},
    },
};

use data::aggr::ticks::TickAccumulation;
use data::chart::{PlotData, kline::KlineDataPoint};
use data::util::format_with_commas;
use exchange::{Kline, Trade};

use std::collections::BTreeMap;
use std::ops::RangeInclusive;

const VALUE_AREA_PCT: f64 = 0.70;

#[derive(Debug, Clone, Copy, Default)]
pub struct VolumeProfilePoint {
    pub poc: f32,
    pub vah: f32,
    pub val: f32,
}

pub struct VolumeProfileIndicator {
    cache: Caches,
    data: BasisSeries<VolumeProfilePoint>,
}

impl VolumeProfileIndicator {
    pub fn new() -> Self {
        Self {
            cache: Caches::default(),
            data: BasisSeries::default(),
        }
    }

    fn compute_rolling_profile(
        datapoints: &BTreeMap<exchange::UnixMs, KlineDataPoint>,
    ) -> BTreeMap<exchange::UnixMs, VolumeProfilePoint> {
        let mut result = BTreeMap::new();
        let mut volume_by_price: BTreeMap<i64, f64> = BTreeMap::new();

        for (&time, dp) in datapoints.iter() {
            for (price, group) in dp.footprint.trades.iter() {
                let key = price.units;
                let vol = f64::from(f32::from(group.total_qty()));
                *volume_by_price.entry(key).or_insert(0.0) += vol;
            }

            if volume_by_price.is_empty() {
                let close_units = dp.kline.close.units;
                let vol = f64::from(f32::from(dp.kline.volume.total()));
                *volume_by_price.entry(close_units).or_insert(0.0) += vol;
            }

            if let Some(point) = Self::compute_levels(&volume_by_price) {
                result.insert(time, point);
            }
        }

        result
    }

    fn compute_rolling_profile_tick(
        datapoints: &[TickAccumulation],
    ) -> BTreeMap<u64, VolumeProfilePoint> {
        let mut result = BTreeMap::new();
        let mut volume_by_price: BTreeMap<i64, f64> = BTreeMap::new();

        for (idx, dp) in datapoints.iter().enumerate() {
            for (price, group) in dp.footprint.trades.iter() {
                let key = price.units;
                let vol = f64::from(f32::from(group.total_qty()));
                *volume_by_price.entry(key).or_insert(0.0) += vol;
            }

            if volume_by_price.is_empty() {
                let close_units = dp.kline.close.units;
                let vol = f64::from(f32::from(dp.kline.volume.total()));
                *volume_by_price.entry(close_units).or_insert(0.0) += vol;
            }

            if let Some(point) = Self::compute_levels(&volume_by_price) {
                result.insert(idx as u64, point);
            }
        }

        result
    }

    fn compute_levels(volume_by_price: &BTreeMap<i64, f64>) -> Option<VolumeProfilePoint> {
        if volume_by_price.is_empty() {
            return None;
        }

        let total_volume: f64 = volume_by_price.values().sum();
        if total_volume <= 0.0 {
            return None;
        }

        // POC = price level with highest volume
        let (&poc_units, _) = volume_by_price
            .iter()
            .max_by(|a, b| a.1.partial_cmp(b.1).unwrap())?;

        // VAH/VAL via expanding from POC
        let prices: Vec<i64> = volume_by_price.keys().copied().collect();
        let poc_idx = prices.iter().position(|&p| p == poc_units).unwrap_or(0);
        let value_area_target = total_volume * VALUE_AREA_PCT;

        let mut accumulated = *volume_by_price.get(&poc_units).unwrap_or(&0.0);
        let mut low_idx = poc_idx;
        let mut high_idx = poc_idx;

        while accumulated < value_area_target && (low_idx > 0 || high_idx < prices.len() - 1) {
            let vol_below = if low_idx > 0 {
                *volume_by_price.get(&prices[low_idx - 1]).unwrap_or(&0.0)
            } else {
                0.0
            };

            let vol_above = if high_idx < prices.len() - 1 {
                *volume_by_price.get(&prices[high_idx + 1]).unwrap_or(&0.0)
            } else {
                0.0
            };

            if vol_above >= vol_below && high_idx < prices.len() - 1 {
                high_idx += 1;
                accumulated += vol_above;
            } else if low_idx > 0 {
                low_idx -= 1;
                accumulated += vol_below;
            } else if high_idx < prices.len() - 1 {
                high_idx += 1;
                accumulated += vol_above;
            } else {
                break;
            }
        }

        let scale = 1e-8_f32;
        Some(VolumeProfilePoint {
            poc: poc_units as f32 * scale,
            vah: prices[high_idx] as f32 * scale,
            val: prices[low_idx] as f32 * scale,
        })
    }

    fn indicator_elem<'a>(
        &'a self,
        main_chart: &'a ViewState,
        visible_range: RangeInclusive<u64>,
    ) -> iced::Element<'a, Message> {
        let tooltip = |point: &VolumeProfilePoint, _next: Option<&VolumeProfilePoint>| {
            PlotTooltip::new(format!(
                "POC: {}\nVAH: {}\nVAL: {}",
                format_with_commas(point.poc),
                format_with_commas(point.vah),
                format_with_commas(point.val),
            ))
        };

        let value_fn = |point: &VolumeProfilePoint| point.poc;

        let plot = LinePlot::new(value_fn)
            .stroke_width(1.5)
            .show_points(false)
            .padding(0.02)
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

impl KlineIndicatorImpl for VolumeProfileIndicator {
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

    fn availability(&self, _chart: &ViewState) -> IndicatorAvailability {
        IndicatorAvailability::Available
    }

    fn rebuild_from_source(&mut self, source: &PlotData<KlineDataPoint>) {
        self.data = source.map_basis_series(
            |timeseries| Self::compute_rolling_profile(&timeseries.datapoints),
            |tickaggr| Self::compute_rolling_profile_tick(&tickaggr.datapoints),
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
