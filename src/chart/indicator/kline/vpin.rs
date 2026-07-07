//! VPIN — Volume-Synchronized Probability of Informed Trading, rango [0, 100].
//!
//! Aproximación por barras (tenemos buy/sell por vela, no buckets de volumen
//! puro): sobre una ventana móvil de `WINDOW` barras,
//!
//! `VPIN = Σ|buy − sell| / Σ(buy + sell) × 100`
//!
//! Interpretación (toxicidad del order flow):
//!  - Alto (→100) → flujo muy desbalanceado/tóxico (informed trading), suele
//!    preceder expansiones de volatilidad.
//!  - Bajo (→0) → flujo equilibrado, mercado "sano"/two-sided.
//!
//! No arrastra estado (a diferencia de Market Pressure): cada barra se recomputa
//! desde su ventana, por eso `on_insert_klines` reconstruye desde el source.
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

/// Ventana móvil en barras. ~20 ≈ 100 min en M5.
const WINDOW: usize = 20;

pub struct VpinIndicator {
    cache: Caches,
    data: BasisSeries<f32>,
}

impl VpinIndicator {
    pub fn new() -> Self {
        Self {
            cache: Caches::default(),
            data: BasisSeries::default(),
        }
    }

    fn buy_sell(kline: &Kline) -> (f64, f64) {
        match kline.volume.buy_sell() {
            Some((buy, sell)) => (f64::from(f32::from(buy)), f64::from(f32::from(sell))),
            None => (0.0, 0.0),
        }
    }

    /// VPIN por índice sobre una serie de (buy, sell) usando ventana móvil.
    fn vpin_at(bars: &[(f64, f64)], i: usize) -> f32 {
        let lo = i.saturating_sub(WINDOW - 1);
        let (mut num, mut den) = (0.0_f64, 0.0_f64);
        for &(buy, sell) in &bars[lo..=i] {
            num += (buy - sell).abs();
            den += buy + sell;
        }
        if den > 0.0 {
            (num / den * 100.0) as f32
        } else {
            0.0
        }
    }

    fn compute_time(
        datapoints: &std::collections::BTreeMap<exchange::UnixMs, KlineDataPoint>,
    ) -> std::collections::BTreeMap<exchange::UnixMs, f32> {
        let times: Vec<exchange::UnixMs> = datapoints.keys().copied().collect();
        let bars: Vec<(f64, f64)> = datapoints.values().map(|dp| Self::buy_sell(&dp.kline)).collect();
        let mut result = std::collections::BTreeMap::new();
        for (i, time) in times.iter().enumerate() {
            result.insert(*time, Self::vpin_at(&bars, i));
        }
        result
    }

    fn compute_tick(
        datapoints: &[data::aggr::ticks::TickAccumulation],
    ) -> std::collections::BTreeMap<u64, f32> {
        let bars: Vec<(f64, f64)> = datapoints.iter().map(|dp| Self::buy_sell(&dp.kline)).collect();
        let mut result = std::collections::BTreeMap::new();
        for i in 0..bars.len() {
            result.insert(i as u64, Self::vpin_at(&bars, i));
        }
        result
    }

    fn rebuild_from_source(&mut self, source: &PlotData<KlineDataPoint>) {
        match source {
            PlotData::TimeBased(ts) => {
                self.data = BasisSeries::Time(Self::compute_time(&ts.datapoints));
            }
            PlotData::TickBased(ta) => {
                self.data = BasisSeries::Tick(Self::compute_tick(&ta.datapoints));
            }
        }
        self.clear_all_caches();
    }

    fn indicator_elem<'a>(
        &'a self,
        main_chart: &'a ViewState,
        visible_range: RangeInclusive<u64>,
    ) -> iced::Element<'a, Message> {
        let tooltip = |v: &f32, _: Option<&f32>| {
            let tag = if *v > 40.0 {
                "toxic"
            } else if *v < 15.0 {
                "calm"
            } else {
                "~"
            };
            PlotTooltip::new(format!("VPIN: {:.1} {}", v, tag))
        };

        let plot = LinePlot::new(|v: &f32| *v)
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

impl KlineIndicatorImpl for VpinIndicator {
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

    fn on_insert_klines(&mut self, _klines: &[Kline], source: &PlotData<KlineDataPoint>) {
        // VPIN usa ventana móvil sin estado arrastrado → recomputar desde source.
        self.rebuild_from_source(source);
    }

    fn on_insert_trades(
        &mut self,
        _trades: &[Trade],
        _old_dp_len: usize,
        _source: &PlotData<KlineDataPoint>,
    ) {
        // VPIN se actualiza por barra cerrada (on_insert_klines), no intrabar.
    }

    fn on_ticksize_change(&mut self, source: &PlotData<KlineDataPoint>) {
        self.rebuild_from_source(source);
    }
    fn on_basis_change(&mut self, source: &PlotData<KlineDataPoint>) {
        self.rebuild_from_source(source);
    }
}
