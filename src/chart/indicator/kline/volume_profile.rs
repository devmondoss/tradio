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
const HISTOGRAM_BINS: usize = 150;

#[derive(Debug, Clone, Copy, Default)]
pub struct VolumeProfilePoint {
    pub poc: f32,
    pub vah: f32,
    pub val: f32,
}

/// Pre-binned bar for the horizontal histogram: (price_center_f32, volume_f64)
#[derive(Debug, Clone, Copy)]
pub struct ProfileBar {
    pub price: f32,
    pub volume: f64,
}

pub struct VolumeProfileIndicator {
    cache: Caches,
    data: BasisSeries<VolumeProfilePoint>,
    /// Sorted by price ascending; used for histogram rendering
    pub histogram: Vec<ProfileBar>,
    /// Max volume across all bins (cached for rendering)
    pub histogram_max: f64,
}

impl VolumeProfileIndicator {
    pub fn new() -> Self {
        Self {
            cache: Caches::default(),
            data: BasisSeries::default(),
            histogram: Vec::new(),
            histogram_max: 0.0,
        }
    }

    pub fn latest_levels(&self) -> Option<VolumeProfilePoint> {
        match &self.data {
            BasisSeries::Time(map) => map.values().last().copied(),
            BasisSeries::Tick(map) => map.values().last().copied(),
        }
    }

    // Distributes candle volume across 5 levels (high, mid-high, close, mid-low, low)
    fn distribute_candle_volume(volume_by_price: &mut BTreeMap<i64, f64>, kline: &Kline) {
        let high = kline.high.units;
        let low = kline.low.units;
        let close = kline.close.units;
        let vol = f64::from(f32::from(kline.volume.total()));

        if high == low || vol <= 0.0 {
            *volume_by_price.entry(close).or_insert(0.0) += vol;
            return;
        }

        let range = (high - low) as f64;

        let levels: [(i64, f64); 5] = [
            (high, 0.10),
            (low + (range * 0.75) as i64, 0.15),
            (close, 0.40),
            (low + (range * 0.25) as i64, 0.15),
            (low, 0.10),
        ];

        for (price_units, weight) in levels {
            *volume_by_price.entry(price_units).or_insert(0.0) += vol * weight;
        }
        // remaining 10% at close
        *volume_by_price.entry(close).or_insert(0.0) += vol * 0.10;
    }

    fn build_volume_by_price_time(
        datapoints: &BTreeMap<exchange::UnixMs, KlineDataPoint>,
    ) -> BTreeMap<i64, f64> {
        const WINDOW: usize = 300;
        let entries: Vec<_> = datapoints.iter().collect();
        let start = entries.len().saturating_sub(WINDOW);
        let mut volume_by_price: BTreeMap<i64, f64> = BTreeMap::new();

        for &(_, dp) in &entries[start..] {
            if dp.footprint.trades.is_empty() {
                Self::distribute_candle_volume(&mut volume_by_price, &dp.kline);
            } else {
                for (price, group) in dp.footprint.trades.iter() {
                    *volume_by_price.entry(price.units).or_insert(0.0) +=
                        f64::from(f32::from(group.total_qty()));
                }
            }
        }

        volume_by_price
    }

    fn build_volume_by_price_tick(datapoints: &[TickAccumulation]) -> BTreeMap<i64, f64> {
        const WINDOW: usize = 300;
        let start = datapoints.len().saturating_sub(WINDOW);
        let mut volume_by_price: BTreeMap<i64, f64> = BTreeMap::new();

        for dp in &datapoints[start..] {
            if dp.footprint.trades.is_empty() {
                Self::distribute_candle_volume(&mut volume_by_price, &dp.kline);
            } else {
                for (price, group) in dp.footprint.trades.iter() {
                    *volume_by_price.entry(price.units).or_insert(0.0) +=
                        f64::from(f32::from(group.total_qty()));
                }
            }
        }

        volume_by_price
    }

    fn compute_rolling_profile(
        datapoints: &BTreeMap<exchange::UnixMs, KlineDataPoint>,
    ) -> BTreeMap<exchange::UnixMs, VolumeProfilePoint> {
        let volume_by_price = Self::build_volume_by_price_time(datapoints);
        let entries: Vec<_> = datapoints.iter().collect();
        let mut result = BTreeMap::new();

        if let Some(point) = Self::compute_levels(&volume_by_price) {
            if let Some(&(&time, _)) = entries.last() {
                result.insert(time, point);
            }
        }

        result
    }

    fn compute_rolling_profile_tick(
        datapoints: &[TickAccumulation],
    ) -> BTreeMap<u64, VolumeProfilePoint> {
        let volume_by_price = Self::build_volume_by_price_tick(datapoints);
        let mut result = BTreeMap::new();

        if let Some(point) = Self::compute_levels(&volume_by_price) {
            let last_idx = datapoints.len().saturating_sub(1) as u64;
            result.insert(last_idx, point);
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

        let (&poc_units, _) = volume_by_price
            .iter()
            .max_by(|a, b| a.1.partial_cmp(b.1).unwrap())?;

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

    /// Bins the raw price→volume map into HISTOGRAM_BINS equal-width buckets.
    fn build_histogram(volume_by_price: &BTreeMap<i64, f64>) -> (Vec<ProfileBar>, f64) {
        if volume_by_price.len() < 2 {
            return (vec![], 0.0);
        }

        let &min_price = volume_by_price.keys().next().unwrap();
        let &max_price = volume_by_price.keys().last().unwrap();

        if min_price >= max_price {
            return (vec![], 0.0);
        }

        let price_range = (max_price - min_price) as f64;
        let bin_size = price_range / HISTOGRAM_BINS as f64;
        let mut bins = vec![0.0_f64; HISTOGRAM_BINS];

        for (&price, &vol) in volume_by_price {
            let idx = ((price - min_price) as f64 / bin_size) as usize;
            let idx = idx.min(HISTOGRAM_BINS - 1);
            bins[idx] += vol;
        }

        let scale = 1e-8_f32;
        let mut bars: Vec<ProfileBar> = bins
            .iter()
            .enumerate()
            .filter(|&(_, v)| *v > 0.0)
            .map(|(i, &v)| ProfileBar {
                price: (min_price as f64 + (i as f64 + 0.5) * bin_size) as f32 * scale,
                volume: v,
            })
            .collect();

        bars.sort_by(|a, b| a.price.partial_cmp(&b.price).unwrap());

        let max_vol = bars.iter().map(|b| b.volume).fold(0.0_f64, f64::max);
        (bars, max_vol)
    }

    fn rebuild_histogram_from_source(&mut self, source: &PlotData<KlineDataPoint>) {
        let raw = match source {
            PlotData::TimeBased(ts) => Self::build_volume_by_price_time(&ts.datapoints),
            PlotData::TickBased(ta) => Self::build_volume_by_price_tick(&ta.datapoints),
        };
        let (bars, max_vol) = Self::build_histogram(&raw);
        self.histogram = bars;
        self.histogram_max = max_vol;
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

    fn overlay_levels(&self) -> Vec<(f32, [f32; 4])> {
        match self.latest_levels() {
            Some(levels) => vec![
                (levels.poc, [1.0, 0.76, 0.03, 0.9]),  // POC: gold, prominent
                (levels.vah, [0.55, 0.36, 0.96, 0.6]), // VAH: purple
                (levels.val, [0.55, 0.36, 0.96, 0.6]), // VAL: purple
            ],
            None => vec![],
        }
    }

    fn overlay_bands(&self, earliest: u64, latest: u64) -> Vec<Vec<(u64, f32, f32)>> {
        let levels = match self.latest_levels() {
            Some(l) if l.vah > l.val && l.vah.is_finite() && l.val.is_finite() => l,
            _ => return vec![],
        };

        let band = vec![
            (earliest, levels.vah, levels.val),
            (latest, levels.vah, levels.val),
        ];
        vec![band]
    }

    fn latest_vol_profile_levels(&self) -> Option<(f64, f64, f64)> {
        self.latest_levels()
            .map(|p| (p.poc as f64, p.vah as f64, p.val as f64))
    }

    fn latest_hvn_lvn_nearby(&self, price: f64, atr: f64) -> (Vec<f64>, Vec<f64>) {
        if self.histogram.is_empty() || atr <= 0.0 {
            return (vec![], vec![]);
        }

        let window = atr * 3.0;
        let lo = price - window;
        let hi = price + window;

        let nearby: Vec<_> = self
            .histogram
            .iter()
            .filter(|b| b.price as f64 >= lo && b.price as f64 <= hi)
            .collect();

        if nearby.is_empty() {
            return (vec![], vec![]);
        }

        let mean = nearby.iter().map(|b| b.volume).sum::<f64>() / nearby.len() as f64;
        let variance = nearby.iter().map(|b| (b.volume - mean).powi(2)).sum::<f64>()
            / nearby.len() as f64;
        let std_dev = variance.sqrt();

        let hvn_threshold = mean + 0.5 * std_dev;
        let lvn_threshold = mean - 0.5 * std_dev;

        let hvn: Vec<f64> = nearby
            .iter()
            .filter(|b| b.volume >= hvn_threshold)
            .map(|b| b.price as f64)
            .collect();

        let lvn: Vec<f64> = nearby
            .iter()
            .filter(|b| b.volume <= lvn_threshold)
            .map(|b| b.price as f64)
            .collect();

        (hvn, lvn)
    }

    fn overlay_volume_profile(&self) -> &[ProfileBar] {
        &self.histogram
    }

    fn overlay_volume_profile_max(&self) -> f64 {
        self.histogram_max
    }

    fn rebuild_from_source(&mut self, source: &PlotData<KlineDataPoint>) {
        self.data = source.map_basis_series(
            |timeseries| Self::compute_rolling_profile(&timeseries.datapoints),
            |tickaggr| Self::compute_rolling_profile_tick(&tickaggr.datapoints),
        );
        self.rebuild_histogram_from_source(source);
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
