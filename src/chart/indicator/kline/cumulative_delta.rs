use crate::chart::{
    Caches, Message, ViewState,
    indicator::{
        indicator_row,
        kline::{
            AvailabilityCause, BasisSeries, BasisSeriesExt, IndicatorAvailability,
            KlineIndicatorImpl,
        },
        plot::{PlotTooltip, line::LinePlot},
    },
};

use data::chart::{
    PlotData,
    kline::{KlineDataPoint, KlineTrades},
};
use data::util::format_with_commas;
use exchange::{Kline, Trade, Volume, unit::Qty};

use iced::widget::{center, text};

use std::collections::BTreeSet;
use std::ops::RangeInclusive;

#[derive(Debug, Clone, Copy, Default)]
pub struct CumulativeDeltaPoint {
    /// Buy volume - sell volume for this candle / tick bucket.
    pub delta: Qty,
    /// Running sum of delta from the oldest loaded datapoint to this datapoint.
    pub cumulative: Qty,
}

pub struct CumulativeDeltaIndicator {
    cache: Caches,
    /// Per-bucket delta. Stored separately so inserting/replacing older klines can
    /// rebuild the cumulative line without needing the full chart source.
    delta: BasisSeries<Qty>,
    data: BasisSeries<CumulativeDeltaPoint>,
    availability: IndicatorAvailability,
    /// Candle-level approximation: mean(|delta_i| / vol_i) over last 50 candles.
    vpin: f64,
}

impl CumulativeDeltaIndicator {
    pub fn new() -> Self {
        Self {
            cache: Caches::default(),
            delta: BasisSeries::default(),
            data: BasisSeries::default(),
            availability: IndicatorAvailability::Unknown,
            vpin: 0.0,
        }
    }

    fn indicator_elem<'a>(
        &'a self,
        main_chart: &'a ViewState,
        visible_range: RangeInclusive<u64>,
    ) -> iced::Element<'a, Message> {
        if let Some(message) = self.unavailable_message(main_chart, "CVD") {
            return center(text(message)).into();
        }

        let tooltip = |point: &CumulativeDeltaPoint, _next: Option<&CumulativeDeltaPoint>| {
            let cvd = format!(
                "CVD: {}",
                format_with_commas(point.cumulative.to_f32_lossy())
            );
            let sign = if point.delta >= Qty::ZERO { "+" } else { "" };
            let delta = format!(
                "Delta: {sign}{}",
                format_with_commas(point.delta.to_f32_lossy())
            );
            PlotTooltip::new(format!("{cvd}\n{delta}"))
        };

        let value_fn = |point: &CumulativeDeltaPoint| point.cumulative.to_f32_lossy();

        let plot = LinePlot::new(value_fn)
            .stroke_width(1.0)
            .show_points(true)
            .point_radius_factor(0.2)
            .padding(0.08)
            .with_tooltip(tooltip);

        indicator_row(
            main_chart,
            &self.cache,
            plot,
            self.data.as_plot_series(),
            visible_range,
        )
    }

    fn has_directional_volume(volume: Volume) -> bool {
        volume.buy_sell().is_some()
    }

    fn volume_delta(volume: Volume) -> Qty {
        volume
            .buy_sell()
            .map(|(buy, sell)| buy - sell)
            .unwrap_or(Qty::ZERO)
    }

    fn delta_from_parts(footprint: &KlineTrades, volume: Volume) -> Qty {
        if footprint.trades.is_empty() {
            Self::volume_delta(volume)
        } else {
            footprint
                .trades
                .values()
                .fold(Qty::ZERO, |acc, group| acc + group.delta_qty())
        }
    }

    fn is_directional_parts(footprint: &KlineTrades, volume: Volume) -> bool {
        !footprint.trades.is_empty() || Self::has_directional_volume(volume)
    }

    fn datapoint_delta(dp: &KlineDataPoint) -> Qty {
        Self::delta_from_parts(&dp.footprint, dp.kline.volume)
    }

    fn is_datapoint_directional(dp: &KlineDataPoint) -> bool {
        Self::is_directional_parts(&dp.footprint, dp.kline.volume)
    }

    fn set_availability(&mut self, has_points: bool, has_directional: bool) {
        self.availability = if !has_points {
            IndicatorAvailability::Unknown
        } else if has_directional {
            IndicatorAvailability::Available
        } else {
            IndicatorAvailability::Unavailable(AvailabilityCause::TradeData)
        };
    }

    fn rebuild_cumulative(&mut self) {
        let new_data = match &self.delta {
            data::chart::BasisSeries::Time(deltas) => {
                let mut cumulative = Qty::ZERO;
                let mut current_day: i64 = -1;
                let result: std::collections::BTreeMap<exchange::UnixMs, CumulativeDeltaPoint> =
                    deltas
                        .iter()
                        .map(|(time, delta)| {
                            let t = *time;
                            let d = *delta;
                            let day = t.as_u64() as i64 / 86_400_000;
                            if day != current_day {
                                current_day = day;
                                cumulative = Qty::ZERO;
                            }
                            cumulative += d;
                            (
                                t,
                                CumulativeDeltaPoint {
                                    delta: d,
                                    cumulative,
                                },
                            )
                        })
                        .collect();
                data::chart::BasisSeries::Time(result)
            }
            data::chart::BasisSeries::Tick(deltas) => {
                let mut cumulative = Qty::ZERO;
                let result: std::collections::BTreeMap<u64, CumulativeDeltaPoint> = deltas
                    .iter()
                    .map(|(idx, delta)| {
                        let i = *idx;
                        let d = *delta;
                        cumulative += d;
                        (
                            i,
                            CumulativeDeltaPoint {
                                delta: d,
                                cumulative,
                            },
                        )
                    })
                    .collect();
                data::chart::BasisSeries::Tick(result)
            }
        };
        self.data = new_data;
        self.clear_all_caches();
    }

    fn compute_vpin(source: &PlotData<KlineDataPoint>) -> f64 {
        const N: usize = 50;
        let ratios: Vec<f64> = match source {
            PlotData::TimeBased(ts) => ts
                .datapoints
                .values()
                .rev()
                .take(N)
                .filter_map(|dp| {
                    let vol = f64::from(f32::from(dp.kline.volume.total()));
                    if vol <= 0.0 {
                        return None;
                    }
                    let delta = Self::datapoint_delta(dp).to_f32_lossy() as f64;
                    Some(delta.abs() / vol)
                })
                .collect(),
            PlotData::TickBased(ta) => ta
                .datapoints
                .iter()
                .rev()
                .take(N)
                .filter_map(|dp| {
                    let vol = f64::from(f32::from(dp.kline.volume.total()));
                    if vol <= 0.0 {
                        return None;
                    }
                    let delta = Self::delta_from_parts(&dp.footprint, dp.kline.volume)
                        .to_f32_lossy() as f64;
                    Some(delta.abs() / vol)
                })
                .collect(),
        };
        if ratios.is_empty() {
            return 0.0;
        }
        ratios.iter().sum::<f64>() / ratios.len() as f64
    }

    fn rebuild_from_deltas(&mut self, deltas: BasisSeries<Qty>) {
        self.delta = deltas;
        self.rebuild_cumulative();
    }
}

impl KlineIndicatorImpl for CumulativeDeltaIndicator {
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
        self.availability.clone()
    }

    fn latest_cvd(&self) -> Option<(f64, f64)> {
        match &self.data {
            data::chart::BasisSeries::Time(map) => map.values().last().map(|p| {
                (
                    p.cumulative.to_f32_lossy() as f64,
                    p.delta.to_f32_lossy() as f64,
                )
            }),
            data::chart::BasisSeries::Tick(map) => map.values().last().map(|p| {
                (
                    p.cumulative.to_f32_lossy() as f64,
                    p.delta.to_f32_lossy() as f64,
                )
            }),
        }
    }

    fn latest_vpin(&self) -> Option<f64> {
        if self.vpin > 0.0 {
            Some(self.vpin)
        } else {
            None
        }
    }

    fn latest_cvd_slope(&self) -> Option<f64> {
        const N: usize = 20;
        let values: Vec<f64> = match &self.data {
            data::chart::BasisSeries::Time(map) => map
                .values()
                .rev()
                .take(N)
                .map(|p| p.cumulative.to_f32_lossy() as f64)
                .collect(),
            data::chart::BasisSeries::Tick(map) => map
                .values()
                .rev()
                .take(N)
                .map(|p| p.cumulative.to_f32_lossy() as f64)
                .collect(),
        };
        if values.len() < 3 {
            return None;
        }
        // Reverse so index 0 = oldest
        let values: Vec<f64> = values.into_iter().rev().collect();
        let n = values.len() as f64;
        let sum_x: f64 = (0..values.len()).map(|i| i as f64).sum();
        let sum_y: f64 = values.iter().sum();
        let sum_xy: f64 = values.iter().enumerate().map(|(i, y)| i as f64 * y).sum();
        let sum_x2: f64 = (0..values.len()).map(|i| (i * i) as f64).sum();
        let denom = n * sum_x2 - sum_x * sum_x;
        if denom.abs() < 1e-10 {
            return None;
        }
        Some((n * sum_xy - sum_x * sum_y) / denom)
    }

    fn recent_delta_slice(&self, n: usize) -> Vec<f64> {
        match &self.data {
            data::chart::BasisSeries::Time(map) => {
                let v: Vec<f64> = map
                    .values()
                    .rev()
                    .take(n)
                    .map(|p| p.delta.to_f32_lossy() as f64)
                    .collect();
                v.into_iter().rev().collect()
            }
            data::chart::BasisSeries::Tick(map) => {
                let v: Vec<f64> = map
                    .values()
                    .rev()
                    .take(n)
                    .map(|p| p.delta.to_f32_lossy() as f64)
                    .collect();
                v.into_iter().rev().collect()
            }
        }
    }

    fn rebuild_from_source(&mut self, source: &PlotData<KlineDataPoint>) {
        let deltas = source.map_basis_series(
            |timeseries| {
                timeseries
                    .datapoints
                    .iter()
                    .map(|(&time, dp)| (time, Self::datapoint_delta(dp)))
                    .collect()
            },
            |tickseries| {
                tickseries
                    .datapoints
                    .iter()
                    .enumerate()
                    .map(|(idx, dp)| {
                        (
                            idx as u64,
                            Self::delta_from_parts(&dp.footprint, dp.kline.volume),
                        )
                    })
                    .collect()
            },
        );

        let (deltas, has_points, has_directional) = match source {
            PlotData::TimeBased(timeseries) => {
                let has_points = !timeseries.datapoints.is_empty();
                let has_directional = timeseries
                    .datapoints
                    .values()
                    .any(Self::is_datapoint_directional);

                (deltas, has_points, has_directional)
            }
            PlotData::TickBased(tickseries) => {
                let has_points = !tickseries.datapoints.is_empty();
                let has_directional = tickseries
                    .datapoints
                    .iter()
                    .any(|dp| Self::is_directional_parts(&dp.footprint, dp.kline.volume));

                (deltas, has_points, has_directional)
            }
        };

        self.set_availability(has_points, has_directional);
        self.vpin = Self::compute_vpin(source);
        self.rebuild_from_deltas(deltas);
    }

    fn on_insert_klines(&mut self, klines: &[Kline], source: &PlotData<KlineDataPoint>) {
        let mut has_directional = false;

        let has_data = {
            let PlotData::TimeBased(timeseries) = source else {
                return;
            };

            let Some(deltas) = self.delta.time_mut() else {
                return;
            };

            for kline in klines {
                let (delta, directional) = if let Some(dp) = timeseries.datapoints.get(&kline.time)
                {
                    (
                        Self::datapoint_delta(dp),
                        Self::is_datapoint_directional(dp),
                    )
                } else {
                    (
                        Self::volume_delta(kline.volume),
                        Self::has_directional_volume(kline.volume),
                    )
                };

                deltas.insert(kline.time, delta);
                has_directional |= directional;
            }

            !deltas.is_empty()
        };

        if has_directional {
            self.availability = IndicatorAvailability::Available;
        }

        if self.availability == IndicatorAvailability::Unknown && has_data {
            self.availability = IndicatorAvailability::Unavailable(AvailabilityCause::TradeData);
        }

        self.rebuild_cumulative();
    }

    fn on_insert_trades(
        &mut self,
        trades: &[Trade],
        old_dp_len: usize,
        source: &PlotData<KlineDataPoint>,
    ) {
        let mut touched = false;

        match source {
            PlotData::TimeBased(timeseries) => {
                if trades.is_empty() {
                    return;
                }

                let Some(deltas) = self.delta.time_mut() else {
                    return;
                };

                let mut touched_times = BTreeSet::new();

                for trade in trades {
                    let rounded_time = trade.time.floor_to(timeseries.interval);
                    touched_times.insert(rounded_time);
                }

                for time in touched_times {
                    if let Some(dp) = timeseries.datapoints.get(&time) {
                        deltas.insert(time, Self::datapoint_delta(dp));
                        touched = true;
                    }
                }
            }
            PlotData::TickBased(tickseries) => {
                let Some(deltas) = self.delta.tick_mut() else {
                    return;
                };

                let start_idx = old_dp_len.saturating_sub(1);

                for (idx, dp) in tickseries.datapoints.iter().enumerate().skip(start_idx) {
                    deltas.insert(
                        idx as u64,
                        Self::delta_from_parts(&dp.footprint, dp.kline.volume),
                    );
                    touched = true;
                }
            }
        }

        if touched {
            self.availability = IndicatorAvailability::Available;
            self.rebuild_cumulative();
        }
    }

    fn on_ticksize_change(&mut self, source: &PlotData<KlineDataPoint>) {
        self.rebuild_from_source(source);
    }

    fn on_basis_change(&mut self, source: &PlotData<KlineDataPoint>) {
        self.rebuild_from_source(source);
    }
}
