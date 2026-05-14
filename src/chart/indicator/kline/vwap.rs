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
    pub upper_band1: f32,
    pub lower_band1: f32,
    pub upper_band2: f32,
    pub lower_band2: f32,
}

pub struct VwapIndicator {
    cache: Caches,
    data: BasisSeries<VwapPoint>,
    avwap_bos: Option<f32>,
}

impl VwapIndicator {
    pub fn new() -> Self {
        Self {
            cache: Caches::default(),
            data: BasisSeries::default(),
            avwap_bos: None,
        }
    }

    /// Finds the most recent swing-low pivot in [start..n-1) and returns its index.
    /// A swing low is a candle whose low is strictly less than both its immediate neighbors.
    fn find_swing_low_anchor<T: Copy>(lows: &[(T, f32)]) -> Option<usize> {
        let n = lows.len();
        if n < 3 {
            return None;
        }
        (1..n - 1)
            .rev()
            .find(|&i| lows[i].1 < lows[i - 1].1 && lows[i].1 < lows[i + 1].1)
    }

    fn compute_avwap_from_anchor(
        entries: &[(&exchange::UnixMs, &KlineDataPoint)],
        anchor: usize,
    ) -> Option<f32> {
        let mut cum_vol = 0.0_f64;
        let mut cum_pv = 0.0_f64;
        for (_, dp) in &entries[anchor..] {
            let tp = (dp.kline.high.to_f32() as f64
                + dp.kline.low.to_f32() as f64
                + dp.kline.close.to_f32() as f64)
                / 3.0;
            let vol = f32::from(dp.kline.volume.total()) as f64;
            cum_vol += vol;
            cum_pv += tp * vol;
        }
        if cum_vol > 0.0 {
            Some((cum_pv / cum_vol) as f32)
        } else {
            None
        }
    }

    fn compute_avwap_time(datapoints: &BTreeMap<exchange::UnixMs, KlineDataPoint>) -> Option<f32> {
        const LOOKBACK: usize = 50;
        let entries: Vec<_> = datapoints.iter().collect();
        let n = entries.len();
        if n < 3 {
            return None;
        }
        let start = n.saturating_sub(LOOKBACK);
        let window = &entries[start..];
        let lows: Vec<_> = window
            .iter()
            .map(|(_, dp)| ((), dp.kline.low.to_f32()))
            .collect();
        let anchor_rel = Self::find_swing_low_anchor(&lows).unwrap_or(0);
        Self::compute_avwap_from_anchor(&entries, start + anchor_rel)
    }

    fn compute_avwap_tick(datapoints: &[TickAccumulation]) -> Option<f32> {
        const LOOKBACK: usize = 50;
        let n = datapoints.len();
        if n < 3 {
            return None;
        }
        let start = n.saturating_sub(LOOKBACK);
        let window = &datapoints[start..];
        let lows: Vec<_> = window
            .iter()
            .map(|dp| ((), dp.kline.low.to_f32()))
            .collect();
        let anchor_rel = Self::find_swing_low_anchor(&lows).unwrap_or(0);
        let anchor = start + anchor_rel;

        let mut cum_vol = 0.0_f64;
        let mut cum_pv = 0.0_f64;
        for dp in &datapoints[anchor..] {
            let tp = (dp.kline.high.to_f32() as f64
                + dp.kline.low.to_f32() as f64
                + dp.kline.close.to_f32() as f64)
                / 3.0;
            let vol = f32::from(dp.kline.volume.total()) as f64;
            cum_vol += vol;
            cum_pv += tp * vol;
        }
        if cum_vol > 0.0 {
            Some((cum_pv / cum_vol) as f32)
        } else {
            None
        }
    }

    pub fn visible_points(&self, earliest: u64, latest: u64) -> Vec<(u64, VwapPoint)> {
        match &self.data {
            BasisSeries::Time(map) => map
                .range(exchange::UnixMs::new(earliest)..=exchange::UnixMs::new(latest))
                .map(|(t, p)| (t.as_u64(), *p))
                .collect(),
            BasisSeries::Tick(map) => map
                .range(earliest..=latest)
                .map(|(t, p)| (*t, *p))
                .collect(),
        }
    }

    fn compute_vwap_time(
        datapoints: &BTreeMap<exchange::UnixMs, KlineDataPoint>,
    ) -> BTreeMap<exchange::UnixMs, VwapPoint> {
        let mut result = BTreeMap::new();
        let mut cum_volume: f64 = 0.0;
        let mut cum_pv: f64 = 0.0;
        let mut cum_pv2: f64 = 0.0;
        let mut current_day: i64 = -1;

        for (&time, dp) in datapoints.iter() {
            // Reset accumulators at each new UTC day (session VWAP)
            let day = time.as_u64() as i64 / 86_400_000;
            if day != current_day {
                current_day = day;
                cum_volume = 0.0;
                cum_pv = 0.0;
                cum_pv2 = 0.0;
            }

            let typical_price = {
                let h = f64::from(dp.kline.high.to_f32());
                let l = f64::from(dp.kline.low.to_f32());
                let c = f64::from(dp.kline.close.to_f32());
                (h + l + c) / 3.0
            };

            let vol = f64::from(f32::from(dp.kline.volume.total()));
            cum_volume += vol;
            cum_pv += typical_price * vol;
            cum_pv2 += typical_price * typical_price * vol;

            let (vwap, std_dev) = if cum_volume > 0.0 {
                let v = cum_pv / cum_volume;
                let variance = (cum_pv2 / cum_volume) - (v * v);
                let sd = if variance > 0.0 { variance.sqrt() } else { 0.0 };
                (v as f32, sd as f32)
            } else {
                (dp.kline.close.to_f32(), 0.0)
            };

            result.insert(
                time,
                VwapPoint {
                    vwap,
                    upper_band1: vwap + std_dev,
                    lower_band1: vwap - std_dev,
                    upper_band2: vwap + 2.0 * std_dev,
                    lower_band2: vwap - 2.0 * std_dev,
                },
            );
        }

        result
    }

    fn compute_vwap_tick(datapoints: &[TickAccumulation]) -> BTreeMap<u64, VwapPoint> {
        let mut result = BTreeMap::new();
        let mut cum_volume: f64 = 0.0;
        let mut cum_pv: f64 = 0.0;
        let mut cum_pv2: f64 = 0.0;

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
            cum_pv2 += typical_price * typical_price * vol;

            let (vwap, std_dev) = if cum_volume > 0.0 {
                let v = cum_pv / cum_volume;
                let variance = (cum_pv2 / cum_volume) - (v * v);
                let sd = if variance > 0.0 { variance.sqrt() } else { 0.0 };
                (v as f32, sd as f32)
            } else {
                (dp.kline.close.to_f32(), 0.0)
            };

            result.insert(
                idx as u64,
                VwapPoint {
                    vwap,
                    upper_band1: vwap + std_dev,
                    lower_band1: vwap - std_dev,
                    upper_band2: vwap + 2.0 * std_dev,
                    lower_band2: vwap - 2.0 * std_dev,
                },
            );
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

    fn latest_vwap(&self) -> Option<f64> {
        match &self.data {
            data::chart::BasisSeries::Time(map) => map.values().last().map(|p| p.vwap as f64),
            data::chart::BasisSeries::Tick(map) => map.values().last().map(|p| p.vwap as f64),
        }
    }

    fn latest_avwap_bos(&self) -> Option<f64> {
        self.avwap_bos.map(|v| v as f64)
    }

    fn overlay_line_points(&self, earliest: u64, latest: u64) -> Vec<(u64, f32)> {
        self.visible_points(earliest, latest)
            .into_iter()
            .map(|(t, p)| (t, p.vwap))
            .collect()
    }

    fn overlay_bands(&self, earliest: u64, latest: u64) -> Vec<Vec<(u64, f32, f32)>> {
        let points = self.visible_points(earliest, latest);
        if points.len() < 2 {
            return vec![];
        }

        let band1: Vec<_> = points
            .iter()
            .filter(|(_, p)| p.upper_band1.is_finite() && p.lower_band1.is_finite())
            .map(|(t, p)| (*t, p.upper_band1, p.lower_band1))
            .collect();

        let band2: Vec<_> = points
            .iter()
            .filter(|(_, p)| p.upper_band2.is_finite() && p.lower_band2.is_finite())
            .map(|(t, p)| (*t, p.upper_band2, p.lower_band2))
            .collect();

        vec![band2, band1]
    }

    fn rebuild_from_source(&mut self, source: &PlotData<KlineDataPoint>) {
        self.data = source.map_basis_series(
            |timeseries| Self::compute_vwap_time(&timeseries.datapoints),
            |tickaggr| Self::compute_vwap_tick(&tickaggr.datapoints),
        );
        self.avwap_bos = match source {
            PlotData::TimeBased(ts) => Self::compute_avwap_time(&ts.datapoints),
            PlotData::TickBased(ta) => Self::compute_avwap_tick(&ta.datapoints),
        };
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
