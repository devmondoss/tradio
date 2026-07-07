//! CVD Large — per-bar delta of "large" trades only.
//!
//! Each trade is compared against a slow EMA of trade sizes. If the trade
//! exceeds `EMA × LARGE_FACTOR`, it counts as a "large" (institutional-scale)
//! trade. The per-bar net delta of large trades is displayed as a BarPlot.
//!
//! Interpretation:
//!  - Green bar: large buyers dominated this bar (accumulation signal).
//!  - Red bar:   large sellers dominated (distribution signal).
//!  - Height:    magnitude of large-trade pressure.
//!  - Divergence between this and regular CVD: smart money vs retail flow.
use crate::chart::{
    Caches, Message, ViewState,
    indicator::{
        indicator_row,
        kline::{BasisSeries, BasisSeriesExt, KlineIndicatorImpl},
        plot::{
            PlotTooltip,
            bar::{BarClass, BarPlot, Baseline},
        },
    },
};

use data::chart::{PlotData, kline::KlineDataPoint};
use exchange::{Kline, Trade};
use std::ops::RangeInclusive;

/// A trade is "large" when its size exceeds `size_ema × LARGE_FACTOR`.
const LARGE_FACTOR: f64 = 4.0;
/// EMA decay for the trade-size tracker (≈ half-life of ~50 trades).
const EMA_ALPHA: f64 = 0.02;
/// Seed value for the EMA (0.05 BTC — conservative for BTCUSDT).
const EMA_SEED: f64 = 0.05;

#[derive(Debug, Clone, Copy, Default)]
pub struct CvdLargePoint {
    /// Net delta of large trades in this bar (positive = large buys).
    pub delta: f32,
}

pub struct CvdLargeIndicator {
    cache: Caches,
    data: BasisSeries<CvdLargePoint>,
    // Live accumulators
    size_ema: f64,
    bar_large_delta: f64,
}

impl CvdLargeIndicator {
    pub fn new() -> Self {
        Self {
            cache: Caches::default(),
            data: BasisSeries::default(),
            size_ema: EMA_SEED,
            bar_large_delta: 0.0,
        }
    }

    fn indicator_elem<'a>(
        &'a self,
        main_chart: &'a ViewState,
        visible_range: RangeInclusive<u64>,
    ) -> iced::Element<'a, Message> {
        let tooltip = |p: &CvdLargePoint, _: Option<&CvdLargePoint>| {
            let side = if p.delta > 0.0 {
                "▲ Large buy"
            } else {
                "▼ Large sell"
            };
            PlotTooltip::new(format!("{} {:.2}", side, p.delta.abs()))
        };

        let classify = |p: &CvdLargePoint| BarClass::Overlay { overlay: p.delta };

        let plot = BarPlot::new(|p: &CvdLargePoint| p.delta, classify)
            .bar_width_factor(0.7)
            .baseline(Baseline::Zero)
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

impl KlineIndicatorImpl for CvdLargeIndicator {
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

    /// Historical data: no per-trade info available — data starts empty and fills live.
    fn rebuild_from_source(&mut self, _source: &PlotData<KlineDataPoint>) {
        self.data = BasisSeries::default();
        self.size_ema = EMA_SEED;
        self.bar_large_delta = 0.0;
        self.clear_all_caches();
    }

    /// Bar just closed — store accumulated large delta then reset.
    fn on_insert_klines(&mut self, klines: &[Kline], _source: &PlotData<KlineDataPoint>) {
        let delta = self.bar_large_delta as f32;
        if let Some(k) = klines.last() {
            let point = CvdLargePoint { delta };
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
        self.bar_large_delta = 0.0;
        self.clear_all_caches();
    }

    /// Per-trade: update EMA, accumulate large delta.
    fn on_insert_trades(
        &mut self,
        trades: &[Trade],
        _old_dp_len: usize,
        _source: &PlotData<KlineDataPoint>,
    ) {
        let threshold = self.size_ema * LARGE_FACTOR;
        for t in trades {
            let size = f64::from(t.qty.to_f32_lossy());
            // Slowly track mean trade size
            self.size_ema = self.size_ema * (1.0 - EMA_ALPHA) + size * EMA_ALPHA;
            // Accumulate delta only for large trades
            if size >= threshold {
                let signed = if t.is_sell { -size } else { size };
                self.bar_large_delta += signed;
            }
        }
    }

    fn on_ticksize_change(&mut self, source: &PlotData<KlineDataPoint>) {
        self.rebuild_from_source(source);
    }
    fn on_basis_change(&mut self, source: &PlotData<KlineDataPoint>) {
        self.rebuild_from_source(source);
    }
}
