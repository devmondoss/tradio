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

/// Session VWAP window defined by UTC hour offsets.
struct SessionWindow {
    start_hour: u64,
    end_hour: u64,
}

impl SessionWindow {
    /// Returns the UTC session epoch (ms) for the day containing `time_ms`.
    fn session_start_ms(&self, time_ms: u64) -> u64 {
        let day_start = (time_ms / 86_400_000) * 86_400_000;
        day_start + self.start_hour * 3_600_000
    }

    fn contains(&self, time_ms: u64) -> bool {
        let ms_in_day = time_ms % 86_400_000;
        let start_ms = self.start_hour * 3_600_000;
        let end_ms = self.end_hour * 3_600_000;
        ms_in_day >= start_ms && ms_in_day < end_ms
    }
}

const ASIA: SessionWindow = SessionWindow {
    start_hour: 0,
    end_hour: 8,
};
const LONDON: SessionWindow = SessionWindow {
    start_hour: 8,
    end_hour: 16,
};
const NY: SessionWindow = SessionWindow {
    start_hour: 13,
    end_hour: 21,
};

pub struct VwapIndicator {
    cache: Caches,
    data: BasisSeries<VwapPoint>,
    avwap_bos: Option<f32>,
    /// Session VWAPs keyed by time: (asia, london, ny) — None outside their session.
    session_vwap: BTreeMap<exchange::UnixMs, [Option<f32>; 3]>,
    /// User-placed AVWAP anchor timestamp. None = no anchor set.
    user_anchor: Option<exchange::UnixMs>,
    /// Computed AVWAP values from user_anchor to the latest candle.
    user_avwap: BTreeMap<exchange::UnixMs, f32>,
}

impl VwapIndicator {
    pub fn new() -> Self {
        Self {
            cache: Caches::default(),
            data: BasisSeries::default(),
            avwap_bos: None,
            session_vwap: BTreeMap::new(),
            user_anchor: None,
            user_avwap: BTreeMap::new(),
        }
    }

    fn compute_session_vwap(
        datapoints: &BTreeMap<exchange::UnixMs, KlineDataPoint>,
    ) -> BTreeMap<exchange::UnixMs, [Option<f32>; 3]> {
        let sessions = [&ASIA, &LONDON, &NY];
        let mut accum: [(u64, f64, f64); 3] = [(0, 0.0, 0.0); 3]; // (anchor_ms, cum_vol, cum_pv)
        let mut result = BTreeMap::new();

        for (&time, dp) in datapoints.iter() {
            let t = time.as_u64();
            let tp = (dp.kline.high.to_f32() as f64
                + dp.kline.low.to_f32() as f64
                + dp.kline.close.to_f32() as f64)
                / 3.0;
            let vol = f32::from(dp.kline.volume.total()) as f64;

            let mut values: [Option<f32>; 3] = [None; 3];

            for (i, session) in sessions.iter().enumerate() {
                if !session.contains(t) {
                    accum[i] = (0, 0.0, 0.0);
                    continue;
                }
                let sess_start = session.session_start_ms(t);
                if accum[i].0 != sess_start {
                    // New session instance — reset
                    accum[i] = (sess_start, 0.0, 0.0);
                }
                accum[i].1 += vol;
                accum[i].2 += tp * vol;
                if accum[i].1 > 0.0 {
                    values[i] = Some((accum[i].2 / accum[i].1) as f32);
                }
            }

            result.insert(time, values);
        }

        result
    }

    pub fn session_vwap_lines(
        &self,
        earliest: u64,
        latest: u64,
    ) -> Vec<(Vec<(u64, f32)>, [f32; 4])> {
        let colors: [[f32; 4]; 3] = [
            [1.0, 0.7, 0.0, 0.75], // Asia  — amber
            [0.2, 0.8, 0.4, 0.75], // London — green
            [0.7, 0.3, 1.0, 0.75], // NY    — purple
        ];

        let mut series: [Vec<(u64, f32)>; 3] = [vec![], vec![], vec![]];

        let range = exchange::UnixMs::new(earliest)..=exchange::UnixMs::new(latest);
        for (&time, values) in self.session_vwap.range(range) {
            for (i, &v) in values.iter().enumerate() {
                if let Some(price) = v {
                    series[i].push((time.as_u64(), price));
                }
            }
        }

        series
            .into_iter()
            .zip(colors.iter())
            .filter(|(pts, _)| !pts.is_empty())
            .map(|(pts, &col)| (pts, col))
            .collect()
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

    fn rebuild_user_avwap(&mut self, source: &PlotData<KlineDataPoint>) {
        self.user_avwap.clear();
        let Some(anchor) = self.user_anchor else {
            return;
        };

        let PlotData::TimeBased(ts) = source else {
            return;
        };

        let mut cum_vol = 0.0_f64;
        let mut cum_pv = 0.0_f64;
        for (&time, dp) in ts.datapoints.range(anchor..) {
            let tp = (dp.kline.high.to_f32() as f64
                + dp.kline.low.to_f32() as f64
                + dp.kline.close.to_f32() as f64)
                / 3.0;
            let vol = f32::from(dp.kline.volume.total()) as f64;
            cum_vol += vol;
            cum_pv += tp * vol;
            if cum_vol > 0.0 {
                self.user_avwap.insert(time, (cum_pv / cum_vol) as f32);
            }
        }
    }

    fn user_avwap_line(&self, earliest: u64, latest: u64) -> Option<(Vec<(u64, f32)>, [f32; 4])> {
        if self.user_avwap.is_empty() {
            return None;
        }
        let range = exchange::UnixMs::new(earliest)..=exchange::UnixMs::new(latest);
        let pts: Vec<(u64, f32)> = self
            .user_avwap
            .range(range)
            .map(|(t, &v)| (t.as_u64(), v))
            .collect();
        if pts.is_empty() {
            return None;
        }
        // Cyan color for user-anchored AVWAP
        Some((pts, [0.0, 0.9, 1.0, 0.9]))
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

    fn overlay_extra_lines(&self, earliest: u64, latest: u64) -> Vec<(Vec<(u64, f32)>, [f32; 4])> {
        let mut lines = self.session_vwap_lines(earliest, latest);
        if let Some(user_line) = self.user_avwap_line(earliest, latest) {
            lines.push(user_line);
        }
        lines
    }

    fn set_user_avwap_anchor(&mut self, ts: u64, source: &PlotData<KlineDataPoint>) {
        if ts == 0 {
            self.user_anchor = None;
            self.user_avwap.clear();
        } else {
            self.user_anchor = Some(exchange::UnixMs::new(ts));
            self.rebuild_user_avwap(source);
        }
        self.cache.clear_all();
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
        self.session_vwap = match source {
            PlotData::TimeBased(ts) => Self::compute_session_vwap(&ts.datapoints),
            PlotData::TickBased(_) => BTreeMap::new(),
        };
        self.rebuild_user_avwap(source);
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
