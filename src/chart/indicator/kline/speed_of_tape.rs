//! Speed of Tape — normalized trade-count velocity per bar.
//!
//! Shows how "fast" the tape is running relative to the rolling 20-bar mean.
//! A ratio of 2.0 means twice the typical trade activity. Bars above the
//! anomaly threshold are colored orange to flag bursts of execution.
//!
//! Historical bars (before the chart opened) use relative volume as a proxy
//! since per-trade counts are unavailable in kline OHLCV data.
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
use std::{collections::VecDeque, ops::RangeInclusive};

const LOOKBACK: usize = 20;
const ANOMALY_THRESHOLD: f32 = 2.0; // ratio above mean = fast tape

#[derive(Debug, Clone, Copy, Default)]
pub struct SpeedPoint {
    /// Normalised activity: 1.0 = average, 2.0 = twice average.
    pub ratio: f32,
    /// +1 = buy dominated, -1 = sell dominated, 0 = unknown.
    pub direction: f32,
    /// True when ratio > ANOMALY_THRESHOLD.
    pub anomaly: bool,
}

pub struct SpeedOfTapeIndicator {
    cache: Caches,
    data: BasisSeries<SpeedPoint>,
    // Live-only accumulator (reset on bar close)
    current_bar_trades: u32,
    history: VecDeque<u32>, // last LOOKBACK trade counts per bar
}

impl SpeedOfTapeIndicator {
    pub fn new() -> Self {
        Self {
            cache: Caches::default(),
            data: BasisSeries::default(),
            current_bar_trades: 0,
            history: VecDeque::with_capacity(LOOKBACK + 1),
        }
    }

    /// Compute from kline volume (historical data — trade count unavailable).
    fn compute_time(
        datapoints: &std::collections::BTreeMap<exchange::UnixMs, KlineDataPoint>,
    ) -> std::collections::BTreeMap<exchange::UnixMs, SpeedPoint> {
        let entries: Vec<_> = datapoints.iter().collect();
        let mut result = std::collections::BTreeMap::new();
        for (i, (time, dp)) in entries.iter().enumerate() {
            let start = i.saturating_sub(LOOKBACK);
            let window = &entries[start..i];
            let cur_vol = f64::from(f32::from(dp.kline.volume.total()));
            let mean = if window.is_empty() {
                cur_vol
            } else {
                window
                    .iter()
                    .map(|(_, d)| f64::from(f32::from(d.kline.volume.total())))
                    .sum::<f64>()
                    / window.len() as f64
            };
            let ratio = if mean > 0.0 {
                (cur_vol / mean) as f32
            } else {
                1.0
            };
            let direction = dp
                .kline
                .volume
                .buy_sell()
                .map(|(b, s)| {
                    let bf = f32::from(b);
                    let sf = f32::from(s);
                    if bf > sf {
                        1.0
                    } else if sf > bf {
                        -1.0
                    } else {
                        0.0
                    }
                })
                .unwrap_or(0.0);
            result.insert(
                **time,
                SpeedPoint {
                    ratio,
                    direction,
                    anomaly: ratio > ANOMALY_THRESHOLD,
                },
            );
        }
        result
    }

    fn compute_tick(
        datapoints: &[data::aggr::ticks::TickAccumulation],
    ) -> std::collections::BTreeMap<u64, SpeedPoint> {
        let mut result = std::collections::BTreeMap::new();
        for (i, dp) in datapoints.iter().enumerate() {
            let start = i.saturating_sub(LOOKBACK);
            let window = &datapoints[start..i];
            let cur_vol = f64::from(f32::from(dp.kline.volume.total()));
            let mean = if window.is_empty() {
                cur_vol
            } else {
                window
                    .iter()
                    .map(|d| f64::from(f32::from(d.kline.volume.total())))
                    .sum::<f64>()
                    / window.len() as f64
            };
            let ratio = if mean > 0.0 {
                (cur_vol / mean) as f32
            } else {
                1.0
            };
            let direction = dp
                .kline
                .volume
                .buy_sell()
                .map(|(b, s)| {
                    let bf = f32::from(b);
                    let sf = f32::from(s);
                    if bf > sf {
                        1.0
                    } else if sf > bf {
                        -1.0
                    } else {
                        0.0
                    }
                })
                .unwrap_or(0.0);
            result.insert(
                i as u64,
                SpeedPoint {
                    ratio,
                    direction,
                    anomaly: ratio > ANOMALY_THRESHOLD,
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
        let tooltip = |p: &SpeedPoint, _: Option<&SpeedPoint>| {
            let flag = if p.anomaly { " ⚡" } else { "" };
            PlotTooltip::new(format!("Speed: {:.2}×{}", p.ratio, flag))
        };

        let classify = |p: &SpeedPoint| {
            if p.anomaly {
                BarClass::Overlay {
                    overlay: p.ratio * if p.direction >= 0.0 { 1.0 } else { -1.0 },
                }
            } else if p.direction != 0.0 {
                BarClass::Overlay {
                    overlay: p.ratio * p.direction,
                }
            } else {
                BarClass::Single
            }
        };

        let plot = BarPlot::new(|p: &SpeedPoint| p.ratio, classify)
            .bar_width_factor(0.85)
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

impl KlineIndicatorImpl for SpeedOfTapeIndicator {
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
        self.current_bar_trades = 0;
        self.history.clear();
        self.clear_all_caches();
    }

    /// Bar just closed — compute ratio from accumulated live trade count, then reset.
    fn on_insert_klines(&mut self, klines: &[Kline], source: &PlotData<KlineDataPoint>) {
        // For each newly closed kline, record speed using live count if available,
        // otherwise fall back to volume-based rebuild.
        if !self.history.is_empty() {
            // We have live trade counts — update the latest bar entry.
            let count = self.current_bar_trades;
            if self.history.len() >= LOOKBACK {
                self.history.pop_front();
            }
            self.history.push_back(count);

            let mean = self.history.iter().copied().map(|c| c as f64).sum::<f64>()
                / self.history.len() as f64;
            let ratio = if mean > 0.0 {
                count as f32 / mean as f32
            } else {
                1.0
            };

            // Determine direction from the latest kline
            if let Some(k) = klines.last() {
                let direction = k
                    .volume
                    .buy_sell()
                    .map(|(b, s)| {
                        let bf = f32::from(b);
                        let sf = f32::from(s);
                        if bf > sf {
                            1.0
                        } else if sf > bf {
                            -1.0
                        } else {
                            0.0
                        }
                    })
                    .unwrap_or(0.0);

                let point = SpeedPoint {
                    ratio,
                    direction,
                    anomaly: ratio > ANOMALY_THRESHOLD,
                };
                match &mut self.data {
                    BasisSeries::Time(map) => {
                        map.insert(k.time, point);
                    }
                    BasisSeries::Tick(map) => {
                        let idx = map.len() as u64;
                        map.insert(idx, point);
                    }
                }
            }
            self.current_bar_trades = 0;
            self.clear_all_caches();
        } else {
            // No live trades yet — use volume proxy for historical klines
            self.rebuild_from_source(source);
        }
    }

    fn on_insert_trades(
        &mut self,
        trades: &[Trade],
        _old_dp_len: usize,
        _source: &PlotData<KlineDataPoint>,
    ) {
        self.current_bar_trades += trades.len() as u32;
        // Initialise history on first trades so we switch to live mode
        if self.history.is_empty() {
            self.history.push_back(0);
        }
    }

    fn on_ticksize_change(&mut self, source: &PlotData<KlineDataPoint>) {
        self.rebuild_from_source(source);
    }
    fn on_basis_change(&mut self, source: &PlotData<KlineDataPoint>) {
        self.rebuild_from_source(source);
    }
}
