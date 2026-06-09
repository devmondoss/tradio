use super::{
    Action, Basis, Chart, Interaction, Message, PlotConstants, PlotData, TEXT_SIZE, ViewState,
    indicator, request_fetch, scale::linear::PriceInfoLabel,
};
use crate::chart::indicator::kline::KlineIndicatorImpl;
use crate::connector::fetcher::{FetchRange, RequestHandler, is_trade_fetch_enabled};
use crate::strategy::types::{Side, StrategyAction, StrategySignal};
use crate::{modal::pane::settings::study, style};
use data::aggr::ticks::TickAggr;
use data::aggr::time::TimeSeries;
use data::chart::indicator::{Indicator, KlineIndicator};
use data::chart::kline::{
    ClusterKind, ClusterScaling, FootprintStudy, KlineDataPoint, KlineTrades, NPoc, PointOfControl,
};
use data::chart::{Autoscale, KlineChartKind, ViewConfig};

use data::util::abbr_large_numbers;
use exchange::unit::{Price, PriceStep, Qty};
use exchange::{FundingRate as FRData, Kline, OpenInterest as OIData, TickerInfo, Trade, UnixMs};

use iced::task::Handle;
use iced::theme::palette::Extended;
use iced::widget::canvas::{self, Event, Geometry, LineDash, Path, Stroke};
use iced::{Alignment, Color, Element, Point, Rectangle, Renderer, Size, Theme, Vector, mouse};

use enum_map::EnumMap;
use std::time::Instant;

impl Chart for KlineChart {
    type IndicatorKind = KlineIndicator;

    fn state(&self) -> &ViewState {
        &self.chart
    }

    fn mut_state(&mut self) -> &mut ViewState {
        &mut self.chart
    }

    fn invalidate_crosshair(&mut self) {
        self.chart.cache.clear_crosshair();
        self.indicators
            .values_mut()
            .filter_map(Option::as_mut)
            .for_each(|indi| indi.clear_crosshair_caches());
    }

    fn invalidate_all(&mut self) {
        self.invalidate(None);
    }

    fn view_indicators(&'_ self, enabled: &[Self::IndicatorKind]) -> Vec<Element<'_, Message>> {
        let chart_state = self.state();
        let visible_region = chart_state.visible_region(chart_state.bounds.size());
        let (earliest, latest) = chart_state.interval_range(&visible_region);
        if earliest > latest {
            return vec![];
        }

        let market = chart_state.ticker_info.market_type();

        // Canonical sub-panel display order
        fn display_priority(k: KlineIndicator) -> u8 {
            match k {
                KlineIndicator::CumulativeDelta => 0,
                KlineIndicator::Volume => 1,
                KlineIndicator::OpenInterest => 2,
                KlineIndicator::OiDelta => 3,
                KlineIndicator::OiZScore => 4,
                KlineIndicator::FundingRate => 5,
                KlineIndicator::RelativeVolume => 6,
                KlineIndicator::Atr => 7,
                _ => 255, // overlays — won't reach render
            }
        }

        let mut sorted: Vec<KlineIndicator> = enabled
            .iter()
            .copied()
            .filter(|k| {
                KlineIndicator::for_market(market).contains(k)
                    && !indicator::kline::is_overlay_indicator(*k)
            })
            .collect();
        sorted.sort_by_key(|k| display_priority(*k));

        let mut elements = vec![];
        for selected_indicator in &sorted {
            if let Some(indi) = self.indicators[*selected_indicator].as_ref() {
                elements.push(indi.element(chart_state, earliest..=latest));
            }
        }
        elements
    }

    fn visible_timerange(&self) -> Option<(u64, u64)> {
        let chart = self.state();
        let region = chart.visible_region(chart.bounds.size());

        if region.width == 0.0 {
            return None;
        }

        Some(chart.interval_range(&region))
    }

    fn interval_keys(&self) -> Option<Vec<u64>> {
        match &self.data_source {
            PlotData::TimeBased(_) => None,
            PlotData::TickBased(tick_aggr) => Some(
                tick_aggr
                    .datapoints
                    .iter()
                    .map(|dp| dp.kline.time.as_u64())
                    .collect(),
            ),
        }
    }

    fn autoscaled_coords(&self) -> Vector {
        let chart = self.state();
        let x_translation = match &self.kind {
            KlineChartKind::Footprint { .. } => {
                0.5 * (chart.bounds.width / chart.scaling) - (chart.cell_width / chart.scaling)
            }
            KlineChartKind::Candles => {
                0.5 * (chart.bounds.width / chart.scaling)
                    - (8.0 * chart.cell_width / chart.scaling)
            }
        };
        Vector::new(x_translation, chart.translation.y)
    }

    fn supports_fit_autoscaling(&self) -> bool {
        true
    }

    fn is_empty(&self) -> bool {
        match &self.data_source {
            PlotData::TimeBased(timeseries) => timeseries.datapoints.is_empty(),
            PlotData::TickBased(tick_aggr) => tick_aggr.datapoints.is_empty(),
        }
    }

    fn on_avwap_anchor_set(&mut self, ts: u64) {
        if let Some(indi) = self.indicators[KlineIndicator::Vwap].as_mut() {
            indi.set_user_avwap_anchor(ts, &self.data_source);
        }
    }

    fn on_scalping_panel_moved(&mut self, x: f32, y: f32) {
        self.scalping_panel_x = x;
        self.scalping_panel_y = y;
        self.state().cache.main.clear();
    }

    fn on_scalping_history_toggled(&mut self) {
        self.scalping_hud.show_history = !self.scalping_hud.show_history;
        self.state().cache.main.clear();
    }
}

impl PlotConstants for KlineChart {
    fn min_scaling(&self) -> f32 {
        self.kind.min_scaling()
    }

    fn max_scaling(&self) -> f32 {
        self.kind.max_scaling()
    }

    fn max_cell_width(&self) -> f32 {
        self.kind.max_cell_width()
    }

    fn min_cell_width(&self) -> f32 {
        self.kind.min_cell_width()
    }

    fn max_cell_height(&self) -> f32 {
        self.kind.max_cell_height()
    }

    fn min_cell_height(&self) -> f32 {
        self.kind.min_cell_height()
    }

    fn default_cell_width(&self) -> f32 {
        self.kind.default_cell_width()
    }
}

pub struct KlineChart {
    chart: ViewState,
    data_source: PlotData<KlineDataPoint>,
    raw_trades: Vec<Trade>,
    indicators: EnumMap<KlineIndicator, Option<Box<dyn KlineIndicatorImpl>>>,
    fetching_trades: (bool, Option<Handle>),
    pub(crate) kind: KlineChartKind,
    request_handler: RequestHandler,
    study_configurator: study::Configurator<FootprintStudy>,
    last_tick: Instant,
    pub strategy_signals: Vec<StrategySignal>,
    pub strategy_overlay_enabled: bool,
    pub last_depth: Option<exchange::depth::Depth>,
    pub last_regime: String,
    last_regime_enum: crate::strategy::types::Regime,
    /// Live snapshot of DRR context for the HUD overlay — updated each bar close.
    drr_hud: DrrHudState,
    /// Live snapshot del panel de scalping — actualizado en cada bar close y depth.
    scalping_hud: ScalpingHudState,
    scalping_state: data::strategy::scalping::ScalpingState,
    /// Posición actual del panel de scalping en coordenadas de bounds (top-left origin).
    scalping_panel_x: f32,
    scalping_panel_y: f32,
    /// RangeBreakoutFlow detector state — se actualiza en cada cierre de barra.
    rbf_state: data::strategy::detectors::range_breakout_flow::RangeBreakoutState,
    /// Última señal RBF activa — se muestra en el overlay hasta que el precio toca SL o TP.
    rbf_active_signal: Option<data::strategy::detectors::range_breakout_flow::RbfSignal>,
    pub config: data::chart::kline::Config,
    outcome_tracker: crate::strategy::tracker::OutcomeTracker,
    paper_account: crate::strategy::paper::PaperAccount,
    ms_tracker: data::structure::MarketStructureTracker,
    ms_context: Option<data::structure::MarketStructureContext>,
    structure_breaks: Vec<data::structure::StructureBreak>,
    ob_detector: data::detectors::OrderBlockDetector,
    ob_context: Option<data::detectors::OrderBlockContext>,
    fvg_detector: data::detectors::FvgDetector,
    fvg_context: Option<data::detectors::FvgContext>,
    range_detector: data::detectors::RangeDetector,
    range_context: Option<data::detectors::RangeContext>,
    liq_events: std::collections::VecDeque<exchange::Liquidation>,
    funding_tracker: data::institutional::FundingTracker,
    oi_tracker: data::institutional::OiTracker,
    cooldown_registry: data::strategy::cooldown::CooldownRegistry,
    micro_buffer: data::strategy::micro_window::CandleMicroBuffer,
    micro_snaps: std::collections::VecDeque<(i64, MicroSnap)>,
    big_trades_acc: BigTradesAccumulator,
    big_trade_bars: std::collections::VecDeque<BigTradeBar>,
    bar_index: u64,
    /// Timestamp ms UTC de la última barra que el detector evaluó. Sirve para
    /// el panel Strategy Monitor (vitals "last HH:MM:SS UTC").
    last_evaluated_bar_ms: Option<i64>,
    /// Oid de la última señal escrita a Mongo; se preserva entre barras para
    /// poder enlazar el trade cerrado correspondiente con su señal vía FK.
    pending_signal_oid: Option<data::strategy::mongo_writer::SignalOid>,
    /// Estado por-detector de la última barra evaluada — para el panel debug.
    pub last_detector_log: Vec<data::strategy::types::DetectorSnap>,
}

// ── Adaptive Big Trades ───────────────────────────────────────────────────────

/// Per-bar summary of large-trade activity for the chart overlay.
#[derive(Clone, Default)]
struct BigTradeBar {
    /// Timestamp of the bar open (ms UTC) — used as x-axis key.
    ts_ms: i64,
    /// Net buy volume from large trades (absolute BTC).
    buy_vol: f64,
    /// Net sell volume from large trades (absolute BTC).
    sell_vol: f64,
    /// Adaptive threshold used for this bar.
    threshold: f64,
}

/// Running accumulator fed by `insert_trades()`, finalised at bar close.
struct BigTradesAccumulator {
    /// Slow EMA of trade sizes — the adaptive baseline.
    size_ema: f64,
    buy_vol:  f64,
    sell_vol: f64,
}

impl Default for BigTradesAccumulator {
    fn default() -> Self {
        Self { size_ema: 0.05, buy_vol: 0.0, sell_vol: 0.0 }
    }
}

impl BigTradesAccumulator {
    fn on_trade(&mut self, size: f64, is_sell: bool) {
        // EMA decay ≈ half-life of 50 trades
        self.size_ema = self.size_ema * 0.98 + size * 0.02;
        let threshold = self.size_ema * 4.0;
        if size >= threshold {
            if is_sell { self.sell_vol += size; } else { self.buy_vol += size; }
        }
    }

    fn finalise(&mut self, ts_ms: i64) -> BigTradeBar {
        let bar = BigTradeBar {
            ts_ms,
            buy_vol:   self.buy_vol,
            sell_vol:  self.sell_vol,
            threshold: self.size_ema * 4.0,
        };
        self.buy_vol  = 0.0;
        self.sell_vol = 0.0;
        bar
    }
}

/// Lightweight shape snapshot of one closed bar's micro-window.
/// Stored in a rolling deque for per-candle strip rendering.
#[derive(Clone, Default)]
struct MicroSnap {
    vol_trajectory:  String, // "front"|"back"|"u_shape"|"mid"|"flat"
    late_surge_ratio: f64,
    delta_slope_norm: f64,
    absorption_proxy: f64,
}

/// Live snapshot of DRR-relevant context for the HUD overlay.
/// Updated on every bar close in run_strategy_detection().
#[derive(Clone, Default)]
struct DrrHudState {
    // Market context
    regime:        String,
    session_name:  String,
    session_phase: String,
    vp_bias:       String,
    auction_state: String,
    // Range state
    range_valid:      bool,
    range_location:   String,
    range_size_atr:   f64,
    range_touches_hi: u32,
    range_touches_lo: u32,
    range_sweep_low:  bool,
    range_sweep_high: bool,
    // Absorption signals (individual booleans for coloring)
    footprint_absorption: bool, // Bid/Ask active
    big_trade_active:     bool,
    cvd_divergence:       bool,
    finish_action:        bool,
    sweep_present:        bool,
    absorption_count:     u8,
    // Flow
    cvd_slope:      Option<f64>,
    vpin:           Option<f64>,
    delta_velocity: Option<f64>,
    // Micro-window shape (last closed bar)
    micro_vol_traj:    String,   // "front" | "back" | "u_shape" | "mid" | "flat"
    micro_late_surge:  f64,      // last-bucket vol / mean-bucket vol
    micro_delta_slope: f64,      // OLS slope of delta normalised by stdev
    micro_absorb:      f64,      // vol / (|Δprice|/ATR) — high = absorption
}

/// Estado del panel de scalping — snapshot actualizado en cada bar close y depth update.
#[derive(Default, Clone)]
struct ScalpingHudState {
    // Circuit breaker
    can_trade: bool,
    daily_trades: u32,
    max_trades: u32,
    daily_pnl: f64,
    consecutive_losses: u32,
    session: String,
    // OBI en vivo (normalizado [0,1]: 0.5=balanceado, >0.5=bids, <0.5=asks)
    obi_fast: f64,  // EMA rápida L10
    obi_slow: f64,  // EMA lenta L10
    obi_l5_norm: f64, // OBI L5 sin EMA — presión inmediata, para divergencia
    spread_ticks: i32,
    // CVD de sesión + slope (OLS 20 barras)
    cvd_session: f64,
    cvd_slope: Option<f64>,
    // Posición activa
    has_position: bool,
    pos_strategy: String,
    pos_side: String,
    pos_entry: f64,
    pos_stop: f64,
    pos_tp1: f64,
    pos_entry_ms: i64,
    pos_tp2: f64,
    pos_sl_at_be: bool,
    pos_current_price: f64,
    pos_lot_btc: f64,
    pos_lot_notional: f64,
    // Últimos 5 trades cerrados (para la tabla compacta)
    trades: Vec<ScalpingTradeSnap>,

    // Gates — estado de cada condición de entrada por estrategia
    session_ok: bool,
    s1_obi_dev: f64,
    s1_obi_ok: bool,
    s1_spread_ok: bool,
    s1_vr_ok: bool,
    s2_regime_ok: bool,
    s2_dz: f64,
    s2_dz_ok: bool,
    s2_vr: f64,
    s2_vr_ok: bool,

    // Timer — countdown hasta cierre de la barra
    last_bar_close_instant: Option<std::time::Instant>,
    bar_period_secs: u32,

    // Historia completa de trades
    show_history: bool,
    all_trades: Vec<ScalpingTradeSnap>,

    // Capital del paper engine
    total_equity: f64,
    session_start_equity: f64,
}

#[derive(Default, Clone)]
struct ScalpingTradeSnap {
    strategy: String,
    side: String,
    result_r: f64,
    exit_reason: String,
    duration_secs: i64,
    won: bool,
}

struct DetectorBootstrap {
    ms_tracker: data::structure::MarketStructureTracker,
    ms_context: Option<data::structure::MarketStructureContext>,
    structure_breaks: Vec<data::structure::StructureBreak>,
    ob_detector: data::detectors::OrderBlockDetector,
    ob_context: Option<data::detectors::OrderBlockContext>,
    fvg_detector: data::detectors::FvgDetector,
    fvg_context: Option<data::detectors::FvgContext>,
    range_detector: data::detectors::RangeDetector,
    range_context: Option<data::detectors::RangeContext>,
    scalping_state: data::strategy::scalping::ScalpingState,
    rbf_state: data::strategy::detectors::range_breakout_flow::RangeBreakoutState,
}

fn bootstrap_detectors(klines: &[Kline]) -> DetectorBootstrap {
    let mut ms_tracker = data::structure::MarketStructureTracker::new(200, 3);
    let mut ob_detector = data::detectors::OrderBlockDetector::new(100);
    let mut fvg_detector = data::detectors::FvgDetector::new(100);
    let mut range_detector = data::detectors::RangeDetector::new();

    // Calentar el scalping state y el RBF state con klines históricas.
    let mut scalping_state = data::strategy::scalping::ScalpingState::new(25);
    let mut rbf_state = data::strategy::detectors::range_breakout_flow::RangeBreakoutState::new();
    for kline in klines {
        let o = kline.open.to_f32() as f64;
        let h = kline.high.to_f32() as f64;
        let l = kline.low.to_f32() as f64;
        let c = kline.close.to_f32() as f64;
        let v = kline.volume.total().to_f32_lossy() as f64;
        let ts = kline.time.as_u64() as i64;
        ms_tracker.push_bar(o, h, l, c, ts);
        ob_detector.push_bar(o, h, l, c, v, ts);
        fvg_detector.push_bar(h, l, ts);
        range_detector.push_bar(h, l, c);
        let body = c - o;
        let estimated_delta = body * v / (h - l + 1e-9);
        let session = data::session::classify_session(ts).session;
        scalping_state.on_bar_close(c, estimated_delta, v, h, l, session, 0.0, 0.0);
        // Calentamos el RBF pero descartamos señales del bootstrap
        let _ = rbf_state.on_bar_close(
            o, h, l, c, v, estimated_delta, session, ts,
            &Default::default(), None, None, 0.0, 0.0, None, None,
        );
    }

    let last_price = klines
        .last()
        .map(|k| k.close.to_f32() as f64)
        .unwrap_or(0.0);
    let last_ts = klines.last().map(|k| k.time.as_u64() as i64).unwrap_or(0);

    let ms_snap = ms_tracker.snapshot();
    let mut structure_breaks = Vec::new();
    if let Some(ref ms) = ms_snap {
        if let Some(ref b) = ms.last_event {
            structure_breaks.push(b.clone());
        }
    }

    let ob_context = if last_price > 0.0 {
        Some(ob_detector.snapshot(last_price))
    } else {
        None
    };
    let fvg_context = if last_price > 0.0 {
        Some(fvg_detector.snapshot(last_price))
    } else {
        None
    };
    let range_context = {
        // Use a placeholder ATR approximation for bootstrap; will be recomputed on first live bar.
        let atr_approx = klines
            .windows(2)
            .map(|w| (w[1].high.to_f32() as f64 - w[1].low.to_f32() as f64).abs())
            .sum::<f64>()
            / klines.len().max(1) as f64;
        let ctx = range_detector.compute(last_price, atr_approx, None);
        if ctx.valid { Some(ctx) } else { None }
    };

    DetectorBootstrap {
        ms_context: ms_snap,
        structure_breaks,
        ms_tracker,
        ob_detector,
        ob_context,
        fvg_detector,
        fvg_context,
        range_detector,
        range_context,
        scalping_state,
        rbf_state,
    }
}

/// Convierte Unix timestamp (segundos) a YYYYMMDD sin dependencias externas.
fn unix_to_yyyymmdd(secs: u64) -> u32 {
    let mut days = secs / 86400;
    let mut year = 1970u32;
    loop {
        let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
        let days_in_year = if leap { 366 } else { 365 };
        if days < days_in_year { break; }
        days -= days_in_year;
        year += 1;
    }
    let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
    let dim = [31u64, if leap {29} else {28}, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    let mut month = 1u32;
    for &d in &dim {
        if days < d { break; }
        days -= d;
        month += 1;
    }
    year * 10000 + month * 100 + (days + 1) as u32
}

impl KlineChart {
    pub fn new(
        layout: ViewConfig,
        basis: Basis,
        step: PriceStep,
        klines_raw: &[Kline],
        raw_trades: Vec<Trade>,
        enabled_indicators: &[KlineIndicator],
        ticker_info: TickerInfo,
        kind: &KlineChartKind,
        config: data::chart::kline::Config,
    ) -> Self {
        match basis {
            Basis::Time(interval) => {
                let timeseries = TimeSeries::<KlineDataPoint>::new(interval, step, klines_raw)
                    .with_trades(&raw_trades);

                let base_price_y = timeseries.base_price();
                let latest_x = timeseries
                    .latest_timestamp()
                    .map_or(0, |timestamp| timestamp.as_u64());
                let (scale_high, scale_low) = timeseries.price_scale({
                    match kind {
                        KlineChartKind::Footprint { .. } => 12,
                        KlineChartKind::Candles => 60,
                    }
                });

                let low_rounded = scale_low.round_to_side_step(true, step);
                let high_rounded = scale_high.round_to_side_step(false, step);

                let y_ticks = Price::steps_between_inclusive(low_rounded, high_rounded, step)
                    .map(|n| n.saturating_sub(1))
                    .unwrap_or(1)
                    .max(1) as f32;

                let cell_width = match kind {
                    KlineChartKind::Footprint { .. } => 80.0,
                    KlineChartKind::Candles => 4.0,
                };
                let cell_height = match kind {
                    KlineChartKind::Footprint { .. } => 800.0 / y_ticks,
                    KlineChartKind::Candles => 200.0 / y_ticks,
                };

                let mut chart = ViewState::new(
                    basis,
                    step,
                    step.decimal_places(),
                    ticker_info,
                    ViewConfig {
                        splits: layout.splits,
                        autoscale: Some(Autoscale::FitToVisible),
                    },
                    cell_width,
                    cell_height,
                );
                chart.base_price_y = base_price_y;
                chart.latest_x = latest_x;

                let x_translation = match &kind {
                    KlineChartKind::Footprint { .. } => {
                        0.5 * (chart.bounds.width / chart.scaling)
                            - (chart.cell_width / chart.scaling)
                    }
                    KlineChartKind::Candles => {
                        0.5 * (chart.bounds.width / chart.scaling)
                            - (8.0 * chart.cell_width / chart.scaling)
                    }
                };
                chart.translation.x = x_translation;

                let data_source = PlotData::TimeBased(timeseries);

                let mut indicators = EnumMap::default();
                for &i in enabled_indicators {
                    let mut indi = indicator::kline::make_empty(i);
                    indi.rebuild_from_source(&data_source);
                    indicators[i] = Some(indi);
                }

                let boot = bootstrap_detectors(klines_raw);

                KlineChart {
                    chart,
                    data_source,
                    raw_trades,
                    indicators,
                    fetching_trades: (false, None),
                    request_handler: RequestHandler::default(),
                    kind: kind.clone(),
                    study_configurator: study::Configurator::new(),
                    last_tick: Instant::now(),
                    strategy_signals: Vec::new(),
                    strategy_overlay_enabled: config.strategy_overlay_enabled,
                    last_depth: None,
                    last_regime: String::new(),
                    last_regime_enum: crate::strategy::types::Regime::Unknown,
                    drr_hud: DrrHudState::default(),
                    scalping_hud: ScalpingHudState::default(),
                    scalping_state: boot.scalping_state,
                    scalping_panel_x: 8.0,
                    scalping_panel_y: 8.0,
                    rbf_state: boot.rbf_state,
                    rbf_active_signal: None,
                    config,
                    outcome_tracker: crate::strategy::tracker::OutcomeTracker::new(),
                    paper_account: crate::strategy::paper::PaperAccount::load_or_new(),
                    ms_tracker: boot.ms_tracker,
                    ms_context: boot.ms_context,
                    structure_breaks: boot.structure_breaks,
                    ob_detector: boot.ob_detector,
                    ob_context: boot.ob_context,
                    fvg_detector: boot.fvg_detector,
                    fvg_context: boot.fvg_context,
                    range_detector: boot.range_detector,
                    range_context: boot.range_context,
                    liq_events: std::collections::VecDeque::new(),
                    funding_tracker: data::institutional::FundingTracker::new(),
                    oi_tracker: data::institutional::OiTracker::new(),
                    cooldown_registry: data::strategy::cooldown::CooldownRegistry::new(5),
                    micro_buffer: data::strategy::micro_window::CandleMicroBuffer::new(
                        data::strategy::micro_window::MicroWindowConfig::default(),
                        0,
                    ),
                    micro_snaps: std::collections::VecDeque::new(),
                    big_trades_acc: BigTradesAccumulator::default(),
                    big_trade_bars: std::collections::VecDeque::new(),
                    last_evaluated_bar_ms: None,
                    pending_signal_oid: None,
                    last_detector_log: Vec::new(),
                    bar_index: 0,
                }
            }
            Basis::Tick(interval) => {
                let cell_width = match kind {
                    KlineChartKind::Footprint { .. } => 80.0,
                    KlineChartKind::Candles => 4.0,
                };
                let cell_height = match kind {
                    KlineChartKind::Footprint { .. } => 90.0,
                    KlineChartKind::Candles => 8.0,
                };

                let mut chart = ViewState::new(
                    basis,
                    step,
                    step.decimal_places(),
                    ticker_info,
                    ViewConfig {
                        splits: layout.splits,
                        autoscale: Some(Autoscale::FitToVisible),
                    },
                    cell_width,
                    cell_height,
                );

                let x_translation = match &kind {
                    KlineChartKind::Footprint { .. } => {
                        0.5 * (chart.bounds.width / chart.scaling)
                            - (chart.cell_width / chart.scaling)
                    }
                    KlineChartKind::Candles => {
                        0.5 * (chart.bounds.width / chart.scaling)
                            - (8.0 * chart.cell_width / chart.scaling)
                    }
                };
                chart.translation.x = x_translation;

                let data_source = PlotData::TickBased(TickAggr::new(interval, step, &raw_trades));

                let mut indicators = EnumMap::default();
                for &i in enabled_indicators {
                    let mut indi = indicator::kline::make_empty(i);
                    indi.rebuild_from_source(&data_source);
                    indicators[i] = Some(indi);
                }

                let boot = bootstrap_detectors(klines_raw);

                KlineChart {
                    chart,
                    data_source,
                    raw_trades,
                    indicators,
                    fetching_trades: (false, None),
                    request_handler: RequestHandler::default(),
                    kind: kind.clone(),
                    study_configurator: study::Configurator::new(),
                    last_tick: Instant::now(),
                    strategy_signals: Vec::new(),
                    strategy_overlay_enabled: config.strategy_overlay_enabled,
                    last_depth: None,
                    last_regime: String::new(),
                    last_regime_enum: crate::strategy::types::Regime::Unknown,
                    drr_hud: DrrHudState::default(),
                    scalping_hud: ScalpingHudState::default(),
                    scalping_state: boot.scalping_state,
                    scalping_panel_x: 8.0,
                    scalping_panel_y: 8.0,
                    rbf_state: boot.rbf_state,
                    rbf_active_signal: None,
                    config,
                    outcome_tracker: crate::strategy::tracker::OutcomeTracker::new(),
                    paper_account: crate::strategy::paper::PaperAccount::load_or_new(),
                    ms_tracker: boot.ms_tracker,
                    ms_context: boot.ms_context,
                    structure_breaks: boot.structure_breaks,
                    ob_detector: boot.ob_detector,
                    ob_context: boot.ob_context,
                    fvg_detector: boot.fvg_detector,
                    fvg_context: boot.fvg_context,
                    range_detector: boot.range_detector,
                    range_context: boot.range_context,
                    liq_events: std::collections::VecDeque::new(),
                    funding_tracker: data::institutional::FundingTracker::new(),
                    oi_tracker: data::institutional::OiTracker::new(),
                    cooldown_registry: data::strategy::cooldown::CooldownRegistry::new(5),
                    micro_buffer: data::strategy::micro_window::CandleMicroBuffer::new(
                        data::strategy::micro_window::MicroWindowConfig::default(),
                        0,
                    ),
                    micro_snaps: std::collections::VecDeque::new(),
                    big_trades_acc: BigTradesAccumulator::default(),
                    big_trade_bars: std::collections::VecDeque::new(),
                    last_evaluated_bar_ms: None,
                    pending_signal_oid: None,
                    last_detector_log: Vec::new(),
                    bar_index: 0,
                }
            }
        }
    }

    pub fn update_latest_kline(&mut self, kline: &Kline) {
        let latest_x = self.chart.latest_x;
        let is_new_bar = kline.time.as_u64() > latest_x && latest_x > 0;

        // Read closed bar OHLCТ before inserting the new bar into the timeseries.
        // Tuple: (close, high, low, open, timestamp_ms)
        let closed_bar: Option<(f64, f64, f64, f64, i64)> = if is_new_bar {
            match &self.data_source {
                PlotData::TimeBased(ts) => ts.datapoints.values().last().map(|dp| {
                    (
                        dp.kline.close.to_f32() as f64,
                        dp.kline.high.to_f32() as f64,
                        dp.kline.low.to_f32() as f64,
                        dp.kline.open.to_f32() as f64,
                        dp.kline.time.as_u64() as i64,
                    )
                }),
                PlotData::TickBased(_) => None,
            }
        } else {
            None
        };

        match self.data_source {
            PlotData::TimeBased(ref mut timeseries) => {
                timeseries.insert_klines(&[*kline]);

                self.indicators
                    .values_mut()
                    .filter_map(Option::as_mut)
                    .for_each(|indi| indi.on_insert_klines(&[*kline], &self.data_source));

                let chart = self.mut_state();

                if kline.time.as_u64() > chart.latest_x {
                    chart.latest_x = kline.time.as_u64();
                }

                chart.last_price = Some(PriceInfoLabel::new(kline.close, kline.open));
            }
            PlotData::TickBased(_) => {}
        }

        // El overlay puede forzarse vía env var FLOWSURFACE_FORCE_STRATEGY (cualquier
        // valor distinto de "0"/""). Útil para ejecutar la detección sin tocar el
        // toggle de la UI cuando un layout persistido tiene el flag en false.
        let force_overlay = std::env::var("FLOWSURFACE_FORCE_STRATEGY")
            .map(|v| !v.is_empty() && v != "0")
            .unwrap_or(false);
        if is_new_bar
            && (self.strategy_overlay_enabled || force_overlay || self.config.show_scalping_panel)
            && let Some((close, high, low, open, ts_ms)) = closed_bar
        {
            self.run_strategy_detection(close, high, low, open, ts_ms);
        }
    }

    pub fn kind(&self) -> &KlineChartKind {
        &self.kind
    }

    fn fetch_missing_data(&mut self) -> Option<Action> {
        match &self.data_source {
            PlotData::TimeBased(timeseries) => {
                let timeframe_ms = timeseries.interval.to_milliseconds();

                if timeseries.datapoints.is_empty() {
                    let latest = chrono::Utc::now().timestamp_millis() as u64;
                    let earliest = latest.saturating_sub(450 * timeframe_ms);

                    let range = FetchRange::Kline(UnixMs::new(earliest), UnixMs::new(latest));
                    if let Some(action) = request_fetch(&mut self.request_handler, range) {
                        return Some(action);
                    }
                }

                let (visible_earliest, visible_latest) = self.visible_timerange()?;
                let (kline_earliest, kline_latest) = timeseries.timerange();
                let visible_earliest_ms = UnixMs::new(visible_earliest);
                let visible_latest_ms = UnixMs::new(visible_latest);
                let visible_span = visible_latest.saturating_sub(visible_earliest);
                let prefetch_earliest = visible_earliest.saturating_sub(visible_span);

                // priority 1, initial klines for visible range
                if visible_earliest_ms < kline_earliest {
                    let range = FetchRange::Kline(UnixMs::new(prefetch_earliest), kline_earliest);

                    if let Some(action) = request_fetch(&mut self.request_handler, range) {
                        return Some(action);
                    }
                }

                // priority 2, trades
                if let KlineChartKind::Footprint { .. } = self.kind
                    && !self.fetching_trades.0
                    && is_trade_fetch_enabled()
                    && let Some((fetch_from, fetch_to)) =
                        timeseries.suggest_trade_fetch_range(visible_earliest_ms, visible_latest_ms)
                {
                    let range = FetchRange::Trades(fetch_from, fetch_to);
                    if let Some(action) = request_fetch(&mut self.request_handler, range) {
                        self.fetching_trades = (true, None);
                        return Some(action);
                    }
                }

                // priority 3, indicators
                // (e.g. open interest needs external fetch as it's not derived from klines)
                let ctx = indicator::kline::FetchCtx {
                    main_chart: &self.chart,
                    timeframe: timeseries.interval,
                    visible_earliest: visible_earliest_ms,
                    kline_latest,
                    prefetch_earliest: UnixMs::new(prefetch_earliest),
                };
                for indi in self.indicators.values_mut().filter_map(Option::as_mut) {
                    if let Some(range) = indi.fetch_range(&ctx)
                        && let Some(action) = request_fetch(&mut self.request_handler, range)
                    {
                        return Some(action);
                    }
                }

                // priority 4, missing klines & integrity check
                let check_earliest = UnixMs::new(prefetch_earliest).max(kline_earliest);
                let check_latest = visible_latest_ms.saturating_add(timeframe_ms);

                if let Some(missing_keys) =
                    timeseries.check_kline_integrity(check_earliest, check_latest)
                {
                    let latest = missing_keys
                        .iter()
                        .max()
                        .unwrap_or(&visible_latest_ms)
                        .saturating_add(timeframe_ms);
                    let earliest = missing_keys
                        .iter()
                        .min()
                        .unwrap_or(&visible_earliest_ms)
                        .saturating_sub(timeframe_ms);

                    let range = FetchRange::Kline(earliest, latest);
                    if let Some(action) = request_fetch(&mut self.request_handler, range) {
                        return Some(action);
                    }
                }
            }
            PlotData::TickBased(_) => {
                // TODO: implement trade fetch
            }
        }

        None
    }

    pub fn reset_request_handler(&mut self) {
        self.request_handler = RequestHandler::default();
        self.fetching_trades = (false, None);
    }

    pub fn mark_request_failed(&mut self, req_id: uuid::Uuid, error: String) {
        self.request_handler.mark_failed(req_id, error);
    }

    pub fn raw_trades(&self) -> Vec<Trade> {
        self.raw_trades.clone()
    }

    pub fn set_handle(&mut self, handle: Handle) {
        self.fetching_trades.1 = Some(handle);
    }

    pub fn tick_size(&self) -> PriceStep {
        self.chart.tick_size
    }

    pub fn study_configurator(&self) -> &study::Configurator<FootprintStudy> {
        &self.study_configurator
    }

    pub fn update_study_configurator(&mut self, message: study::Message<FootprintStudy>) {
        let KlineChartKind::Footprint {
            ref mut studies, ..
        } = self.kind
        else {
            return;
        };

        match self.study_configurator.update(message) {
            Some(study::Action::ToggleStudy(study, is_selected)) => {
                if is_selected {
                    let already_exists = studies.iter().any(|s| s.is_same_type(&study));
                    if !already_exists {
                        studies.push(study);
                    }
                } else {
                    studies.retain(|s| !s.is_same_type(&study));
                }
            }
            Some(study::Action::ConfigureStudy(study)) => {
                if let Some(existing_study) = studies.iter_mut().find(|s| s.is_same_type(&study)) {
                    *existing_study = study;
                }
            }
            None => {}
        }

        self.invalidate(None);
    }

    pub fn chart_layout(&self) -> ViewConfig {
        self.chart.layout()
    }

    pub fn set_cluster_kind(&mut self, new_kind: ClusterKind) {
        if let KlineChartKind::Footprint {
            ref mut clusters, ..
        } = self.kind
        {
            *clusters = new_kind;
        }

        self.invalidate(None);
    }

    pub fn set_cluster_scaling(&mut self, new_scaling: ClusterScaling) {
        if let KlineChartKind::Footprint {
            ref mut scaling, ..
        } = self.kind
        {
            *scaling = new_scaling;
        }

        self.invalidate(None);
    }

    pub fn basis(&self) -> Basis {
        self.chart.basis
    }

    pub fn change_tick_size(&mut self, new_step: PriceStep) {
        let chart = self.mut_state();

        chart.cell_height *= (new_step.units as f32) / (chart.tick_size.units as f32);
        chart.tick_size = new_step;

        match self.data_source {
            PlotData::TickBased(ref mut tick_aggr) => {
                tick_aggr.change_tick_size(new_step, &self.raw_trades);
            }
            PlotData::TimeBased(ref mut timeseries) => {
                timeseries.change_tick_size(new_step, &self.raw_trades);
            }
        }

        self.indicators
            .values_mut()
            .filter_map(Option::as_mut)
            .for_each(|indi| indi.on_ticksize_change(&self.data_source));

        self.invalidate(None);
    }

    pub fn set_basis(&mut self, new_basis: Basis) -> Option<Action> {
        self.chart.last_price = None;
        self.chart.basis = new_basis;

        match new_basis {
            Basis::Time(interval) => {
                let step = self.chart.tick_size;
                let timeseries = TimeSeries::<KlineDataPoint>::new(interval, step, &[]);
                self.data_source = PlotData::TimeBased(timeseries);
            }
            Basis::Tick(tick_count) => {
                let step = self.chart.tick_size;
                let tick_aggr = TickAggr::new(tick_count, step, &self.raw_trades);
                self.data_source = PlotData::TickBased(tick_aggr);
            }
        }

        self.indicators
            .values_mut()
            .filter_map(Option::as_mut)
            .for_each(|indi| indi.on_basis_change(&self.data_source));

        self.reset_request_handler();
        self.invalidate(Some(Instant::now()))
    }

    pub fn studies(&self) -> Option<Vec<FootprintStudy>> {
        match &self.kind {
            KlineChartKind::Footprint { studies, .. } => Some(studies.clone()),
            _ => None,
        }
    }

    pub fn set_studies(&mut self, new_studies: Vec<FootprintStudy>) {
        if let KlineChartKind::Footprint {
            ref mut studies, ..
        } = self.kind
        {
            *studies = new_studies;
        }

        self.invalidate(None);
    }

    pub fn insert_trades(&mut self, buffer: &[Trade]) {
        self.raw_trades.extend_from_slice(buffer);

        match self.data_source {
            PlotData::TickBased(ref mut tick_aggr) => {
                let old_dp_len = tick_aggr.datapoints.len();
                tick_aggr.insert_trades(buffer);

                if let Some(last_dp) = tick_aggr.datapoints.last() {
                    self.chart.last_price =
                        Some(PriceInfoLabel::new(last_dp.kline.close, last_dp.kline.open));
                } else {
                    self.chart.last_price = None;
                }

                self.indicators
                    .values_mut()
                    .filter_map(Option::as_mut)
                    .for_each(|indi| indi.on_insert_trades(buffer, old_dp_len, &self.data_source));

                self.invalidate(None);
            }
            PlotData::TimeBased(ref mut timeseries) => {
                timeseries.insert_trades_existing_buckets(buffer);

                self.indicators
                    .values_mut()
                    .filter_map(Option::as_mut)
                    .for_each(|indi| indi.on_insert_trades(buffer, 0, &self.data_source));

                // Feed micro-window buffer and big-trades accumulator
                for t in buffer {
                    let size = t.qty.to_f32_lossy() as f64;
                    self.micro_buffer.on_trade(
                        &data::strategy::micro_window::MicroTrade {
                            ts_ms:   t.time.as_u64() as i64,
                            price:   t.price.to_f32() as f64,
                            size,
                            is_buy:  !t.is_sell,
                            is_big:  false,
                            liq_usd: 0.0,
                        },
                    );
                    self.big_trades_acc.on_trade(size, t.is_sell);
                }

                self.invalidate(None);
            }
        }
    }

    pub fn insert_raw_trades(&mut self, raw_trades: Vec<Trade>, is_batches_done: bool) {
        match self.data_source {
            PlotData::TickBased(ref mut tick_aggr) => {
                tick_aggr.insert_trades(&raw_trades);
            }
            PlotData::TimeBased(ref mut timeseries) => {
                timeseries.insert_trades_existing_buckets(&raw_trades);
            }
        }

        self.raw_trades.extend_from_slice(&raw_trades);

        self.indicators
            .values_mut()
            .filter_map(Option::as_mut)
            .for_each(|indi| indi.on_insert_trades(&raw_trades, 0, &self.data_source));

        if is_batches_done {
            self.fetching_trades = (false, None);
        }

        self.invalidate(None);
    }

    pub fn insert_hist_klines(&mut self, req_id: uuid::Uuid, klines_raw: &[Kline]) {
        match self.data_source {
            PlotData::TimeBased(ref mut timeseries) => {
                timeseries.insert_klines(klines_raw);
                timeseries.insert_trades_existing_buckets(&self.raw_trades);

                self.indicators
                    .values_mut()
                    .filter_map(Option::as_mut)
                    .for_each(|indi| indi.on_insert_klines(klines_raw, &self.data_source));

                if klines_raw.is_empty() {
                    self.request_handler
                        .mark_failed(req_id, "No data received".to_string());
                } else {
                    self.request_handler.mark_completed(req_id);
                    // Bootstrap OB/FVG/Structure detectors on first historical load.
                    // Chart is created with &[] so detectors have no context until here.
                    if self.ob_context.is_none() {
                        let boot = bootstrap_detectors(klines_raw);
                        self.ms_tracker = boot.ms_tracker;
                        self.ms_context = boot.ms_context;
                        self.structure_breaks = boot.structure_breaks;
                        self.ob_detector = boot.ob_detector;
                        self.ob_context = boot.ob_context;
                        self.fvg_detector = boot.fvg_detector;
                        self.fvg_context = boot.fvg_context;
                        self.range_detector = boot.range_detector;
                        self.range_context = boot.range_context;
                    }
                }
                self.invalidate(None);
            }
            PlotData::TickBased(_) => {}
        }
    }

    pub fn insert_open_interest(&mut self, req_id: Option<uuid::Uuid>, oi_data: &[OIData]) {
        if let Some(req_id) = req_id {
            if oi_data.is_empty() {
                self.request_handler
                    .mark_failed(req_id, "No data received".to_string());
            } else {
                self.request_handler.mark_completed(req_id);
            }
        }

        for key in [
            KlineIndicator::OpenInterest,
            KlineIndicator::OiDelta,
            KlineIndicator::OiZScore,
        ] {
            if let Some(indi) = self.indicators[key].as_mut() {
                indi.on_open_interest(oi_data);
            }
        }

        // Alimentar OiTracker institucional
        for oi in oi_data {
            self.oi_tracker.push(data::institutional::OiHistSnapshot {
                timestamp_ms: oi.time.as_u64() as i64,
                open_interest_usd: oi.value as f64,
            });
        }
    }

    pub fn insert_funding_rate(&mut self, req_id: Option<uuid::Uuid>, data: &[FRData]) {
        if let Some(req_id) = req_id {
            if data.is_empty() {
                self.request_handler
                    .mark_failed(req_id, "No data received".to_string());
            } else {
                self.request_handler.mark_completed(req_id);
            }
        }

        if let Some(indi) = self.indicators[KlineIndicator::FundingRate].as_mut() {
            indi.on_funding_rate(data);
        }

        // Alimentar FundingTracker institucional
        for fr in data {
            self.funding_tracker
                .push(data::institutional::FundingRateSample {
                    timestamp_ms: fr.time.as_u64() as i64,
                    rate: fr.rate as f64,
                });
        }
    }

    fn calc_qty_scales(
        &self,
        earliest: u64,
        latest: u64,
        highest: Price,
        lowest: Price,
        step: PriceStep,
        cluster_kind: ClusterKind,
    ) -> f32 {
        let rounded_highest = highest.round_to_side_step(false, step).add_steps(1, step);
        let rounded_lowest = lowest.round_to_side_step(true, step).add_steps(-1, step);

        match &self.data_source {
            PlotData::TimeBased(timeseries) => timeseries
                .max_qty_ts_range(
                    cluster_kind,
                    UnixMs::new(earliest),
                    UnixMs::new(latest),
                    rounded_highest,
                    rounded_lowest,
                )
                .into(),
            PlotData::TickBased(tick_aggr) => {
                let earliest = earliest as usize;
                let latest = latest as usize;

                tick_aggr
                    .max_qty_idx_range(
                        cluster_kind,
                        earliest,
                        latest,
                        rounded_highest,
                        rounded_lowest,
                    )
                    .into()
            }
        }
    }

    pub fn last_update(&self) -> Instant {
        self.last_tick
    }

    pub fn invalidate(&mut self, now: Option<Instant>) -> Option<Action> {
        let chart = &mut self.chart;

        if let Some(autoscale) = chart.layout.autoscale {
            match autoscale {
                super::Autoscale::CenterLatest => {
                    let x_translation = match &self.kind {
                        KlineChartKind::Footprint { .. } => {
                            0.5 * (chart.bounds.width / chart.scaling)
                                - (chart.cell_width / chart.scaling)
                        }
                        KlineChartKind::Candles => {
                            0.5 * (chart.bounds.width / chart.scaling)
                                - (8.0 * chart.cell_width / chart.scaling)
                        }
                    };
                    chart.translation.x = x_translation;

                    let calculate_target_y = |kline: exchange::Kline| -> f32 {
                        let y_low = chart.price_to_y(kline.low);
                        let y_high = chart.price_to_y(kline.high);
                        let y_close = chart.price_to_y(kline.close);

                        let mut target_y_translation = -(y_low + y_high) / 2.0;

                        if chart.bounds.height > f32::EPSILON && chart.scaling > f32::EPSILON {
                            let visible_half_height = (chart.bounds.height / chart.scaling) / 2.0;

                            let view_center_y_centered = -target_y_translation;

                            let visible_y_top = view_center_y_centered - visible_half_height;
                            let visible_y_bottom = view_center_y_centered + visible_half_height;

                            let padding = chart.cell_height;

                            if y_close < visible_y_top {
                                target_y_translation = -(y_close - padding + visible_half_height);
                            } else if y_close > visible_y_bottom {
                                target_y_translation = -(y_close + padding - visible_half_height);
                            }
                        }
                        target_y_translation
                    };

                    chart.translation.y = self.data_source.latest_y_midpoint(calculate_target_y);
                }
                super::Autoscale::FitToVisible => {
                    let visible_region = chart.visible_region(chart.bounds.size());
                    let (start_interval, end_interval) = chart.interval_range(&visible_region);

                    if let Some((lowest, highest)) = self
                        .data_source
                        .visible_price_range(start_interval, end_interval)
                    {
                        let chart_height = chart.bounds.height;
                        let tick_size = chart.tick_size.to_f32_lossy();

                        if chart_height > f32::EPSILON && tick_size > 0.0 {
                            let (fit_lowest, fit_highest) =
                                if let KlineChartKind::Footprint { .. } = self.kind {
                                    if let Some((footprint_low, footprint_high)) = self
                                        .data_source
                                        .visible_footprint_price_range(start_interval, end_interval)
                                    {
                                        let half_tick = tick_size * 0.5;
                                        (
                                            footprint_low.to_f32_lossy() - half_tick,
                                            footprint_high.to_f32_lossy() + half_tick,
                                        )
                                    } else {
                                        (lowest, highest)
                                    }
                                } else {
                                    (lowest, highest)
                                };

                            let visible_span = (fit_highest - fit_lowest).max(tick_size);
                            let base_padding = visible_span * 0.05; // 5% padding on top and bottom

                            let mut top_padding = base_padding;
                            let mut bottom_padding = base_padding;

                            if let KlineChartKind::Footprint { clusters, .. } = self.kind {
                                let provisional_span = visible_span + top_padding + bottom_padding;
                                if provisional_span > 0.0 {
                                    let provisional_cell_height =
                                        (chart_height * tick_size) / provisional_span;

                                    let outer_padding = price_padding_from_pixels(
                                        provisional_cell_height,
                                        tick_size,
                                    );

                                    top_padding += outer_padding;
                                    bottom_padding += outer_padding;

                                    bottom_padding = bottom_padding.max(footprint_summary_padding(
                                        provisional_cell_height,
                                        chart.scaling,
                                        chart.cell_width,
                                        tick_size,
                                        clusters,
                                    ));
                                }
                            }

                            let padded_span = visible_span + top_padding + bottom_padding;
                            if padded_span > 0.0 {
                                chart.cell_height = (chart_height * tick_size) / padded_span;
                                chart.base_price_y = Price::from_f32(fit_highest + top_padding);
                                chart.translation.y = -chart_height / 2.0;
                            }
                        }
                    }
                }
            }
        }

        let visible_range_for_vrvp = {
            let region = chart.visible_region(chart.bounds.size());
            let (e, l) = chart.interval_range(&region);
            if chart.bounds.width > 0.0 && e < l {
                Some((e, l))
            } else {
                None
            }
        };

        chart.cache.clear_all();
        for indi in self.indicators.values_mut().filter_map(Option::as_mut) {
            indi.clear_all_caches();
        }

        if let Some((earliest, latest)) = visible_range_for_vrvp
            && let Some(indi) = self.indicators[KlineIndicator::VolumeProfile].as_mut()
        {
            indi.update_visible_range(earliest, latest, &self.data_source);
        }

        if let Some(t) = now {
            self.last_tick = t;
            self.fetch_missing_data()
        } else {
            None
        }
    }

    pub fn toggle_indicator(&mut self, indicator: KlineIndicator) {
        let prev_indi_count = self.indicators.values().filter(|v| v.is_some()).count();

        if self.indicators[indicator].is_some() {
            self.indicators[indicator] = None;
        } else {
            let mut box_indi = indicator::kline::make_empty(indicator);
            box_indi.rebuild_from_source(&self.data_source);

            // Bootstrap OI-dependent indicators with data the OI indicator already has,
            // so they don't start empty when toggled on after OI data was already fetched.
            if indicator == KlineIndicator::OiDelta
                && let Some(oi_indi) = self.indicators[KlineIndicator::OpenInterest].as_ref()
                && let Some(existing) = oi_indi.oi_snapshot()
            {
                box_indi.on_open_interest(&existing);
            }

            self.indicators[indicator] = Some(box_indi);
        }

        if let Some(main_split) = self.chart.layout.splits.first() {
            let current_indi_count = self.indicators.values().filter(|v| v.is_some()).count();
            self.chart.layout.splits = data::util::calc_panel_splits(
                *main_split,
                current_indi_count,
                Some(prev_indi_count),
            );
        }
    }

    /// Indicators required for strategy detection to have full context.
    pub const STRATEGY_INDICATORS: &'static [KlineIndicator] = &[
        KlineIndicator::Vwap,
        KlineIndicator::VolumeProfile,
        KlineIndicator::CumulativeDelta,
        KlineIndicator::Volume,
        KlineIndicator::Atr,
    ];

    pub fn toggle_strategy_overlay(&mut self) -> Vec<KlineIndicator> {
        self.strategy_overlay_enabled = !self.strategy_overlay_enabled;
        self.config.strategy_overlay_enabled = self.strategy_overlay_enabled;
        if self.strategy_overlay_enabled {
            // Al encender, instanciamos los indicadores necesarios para que
            // run_strategy_detection tenga ATR / regime / VWAP / VP / CVD.
            // Sin esto el detector rechaza cada barra con ATR_NOT_READY.
            self.ensure_strategy_indicators()
        } else {
            self.strategy_signals.clear();
            vec![]
        }
    }

    /// Garantiza que los `STRATEGY_INDICATORS` estén instanciados cuando el
    /// overlay está activo, sin tocar el flag. Útil al arrancar la UI con un
    /// layout que tiene `strategy_overlay_enabled=true` por default pero la
    /// lista de indicadores vacía. Devuelve los recién añadidos (el pane
    /// debe agregarlos a su `indicators` Vec).
    pub fn ensure_strategy_indicators(&mut self) -> Vec<KlineIndicator> {
        // Solo el chart Candlestick recibe los indicadores de estrategia; el
        // Footprint queda limpio para orderflow.
        if !self.strategy_overlay_enabled || !matches!(self.kind, KlineChartKind::Candles) {
            return vec![];
        }
        let mut added = vec![];
        let prev_indi_count = self.indicators.values().filter(|v| v.is_some()).count();
        for &ind in Self::STRATEGY_INDICATORS {
            if self.indicators[ind].is_some() {
                continue;
            }
            let mut box_indi = indicator::kline::make_empty(ind);
            box_indi.rebuild_from_source(&self.data_source);
            self.indicators[ind] = Some(box_indi);
            added.push(ind);
        }
        if !added.is_empty()
            && let Some(main_split) = self.chart.layout.splits.first()
        {
            let current_indi_count =
                self.indicators.values().filter(|v| v.is_some()).count();
            self.chart.layout.splits = data::util::calc_panel_splits(
                *main_split,
                current_indi_count,
                Some(prev_indi_count),
            );
        }
        added
    }

    pub fn update_depth(&mut self, depth: &exchange::depth::Depth) {
        self.last_depth = Some(depth.clone());

        // OBI L10 — señal principal para el EMA scalping
        let bid10: f64 = depth.bids.iter().rev().take(10)
            .map(|(_, q)| f64::from(q.to_f32_lossy())).sum();
        let ask10: f64 = depth.asks.iter().take(10)
            .map(|(_, q)| f64::from(q.to_f32_lossy())).sum();
        let total10 = bid10 + ask10;
        if total10 > 0.0 {
            let obi_l10_raw = (bid10 - ask10) / total10;
            self.scalping_state.on_depth(obi_l10_raw); // normaliza a [0,1] internamente
            self.scalping_hud.obi_fast = self.scalping_state.obi_ema_fast;
            self.scalping_hud.obi_slow = self.scalping_state.obi_ema_slow;
        }

        // OBI L5 — señal auxiliar de presión inmediata (para divergencia L5/L10)
        let bid5: f64 = depth.bids.iter().rev().take(5)
            .map(|(_, q)| f64::from(q.to_f32_lossy())).sum();
        let ask5: f64 = depth.asks.iter().take(5)
            .map(|(_, q)| f64::from(q.to_f32_lossy())).sum();
        let total5 = bid5 + ask5;
        if total5 > 0.0 {
            let obi_l5_raw = (bid5 - ask5) / total5;
            self.scalping_hud.obi_l5_norm = (obi_l5_raw + 1.0) / 2.0; // [0,1]
        }

        // Spread en ticks
        if let (Some((&ask, _)), Some((&bid, _))) =
            (depth.asks.iter().next(), depth.bids.iter().next_back())
        {
            let spread_usd = (ask.to_f32() as f64 - bid.to_f32() as f64).max(0.0);
            self.scalping_hud.spread_ticks = (spread_usd / 0.10).round() as i32;
        }
    }

    fn run_strategy_detection(
        &mut self,
        bar_close: f64,
        bar_high: f64,
        bar_low: f64,
        bar_open: f64,
        bar_ts_ms: i64,
    ) {
        use crate::strategy::{adapter, logger, router, types::*};
        use data::session::classify_session;

        // Las estrategias y su overlay viven SOLO en el pane Candlestick.
        // El Footprint es para orderflow puro y no debe contaminarse con
        // indicadores ni cajas TP/SL (el usuario tampoco podría quitarlos
        // porque el auto-heal los reinstanciaría).
        if !matches!(self.kind, KlineChartKind::Candles) {
            return;
        }

        let Some(depth) = &self.last_depth else {
            return;
        };

        let price = bar_close;

        // TTL in bars: VALOR DE ARRANQUE — se tunea en Fase D con datos reales.
        const TTL_BARS: u64 = 12;
        let interval_ms = match &self.data_source {
            PlotData::TimeBased(ts) => ts.interval.to_milliseconds(),
            PlotData::TickBased(_) => 60_000,
        };
        let ttl_ms = (TTL_BARS * interval_ms) as i64;

        let orderbook = adapter::build_orderbook_context(depth);

        let vwap_value = self.indicators[KlineIndicator::Vwap]
            .as_ref()
            .and_then(|i| i.latest_vwap());
        let avwap_bos = self.indicators[KlineIndicator::Vwap]
            .as_ref()
            .and_then(|i| i.latest_avwap_bos());
        let vwap = adapter::build_vwap_context(price, vwap_value, avwap_bos, None);

        let atr = self.indicators[KlineIndicator::Atr]
            .as_ref()
            .and_then(|i| i.latest_atr());

        let (poc, vah, val) = self.indicators[KlineIndicator::VolumeProfile]
            .as_ref()
            .and_then(|i| i.latest_vol_profile_levels())
            .map(|(p, h, l)| (Some(p), Some(h), Some(l)))
            .unwrap_or((None, None, None));
        let (hvn_nearby, lvn_nearby) = self.indicators[KlineIndicator::VolumeProfile]
            .as_ref()
            .map(|i| i.latest_hvn_lvn_nearby(price, atr.unwrap_or(0.0)))
            .unwrap_or_default();
        let volume_profile =
            adapter::build_volume_profile_context(price, poc, vah, val, hvn_nearby, lvn_nearby);

        let (cvd, delta) = self.indicators[KlineIndicator::CumulativeDelta]
            .as_ref()
            .and_then(|i| i.latest_cvd())
            .map(|(c, d)| (Some(c), Some(d)))
            .unwrap_or((None, None));
        let cvd_slope = self.indicators[KlineIndicator::CumulativeDelta]
            .as_ref()
            .and_then(|i| i.latest_cvd_slope());
        let vpin = self.indicators[KlineIndicator::CumulativeDelta]
            .as_ref()
            .and_then(|i| i.latest_vpin());
        let (buy_vol, sell_vol) = self.indicators[KlineIndicator::Volume]
            .as_ref()
            .and_then(|i| i.latest_volume())
            .map(|(b, s)| (Some(b), Some(s)))
            .unwrap_or((None, None));

        // Extract recent candles: REGIME_N bars for closes, highs, lows (oldest-first).
        // Highs/lows expanded to REGIME_N for swing pivot detection, MSS, sweep, and stress regime.
        // Delta slice aligned index-for-index with recent_highs/recent_lows.
        const REGIME_N: usize = 20;
        let (recent_closes, recent_highs, recent_lows): (Vec<f64>, Vec<f64>, Vec<f64>) =
            match &self.data_source {
                PlotData::TimeBased(ts) => {
                    let closes = ts
                        .datapoints
                        .values()
                        .rev()
                        .take(REGIME_N)
                        .map(|dp| dp.kline.close.to_f32() as f64)
                        .collect::<Vec<_>>()
                        .into_iter()
                        .rev()
                        .collect();
                    let highs = ts
                        .datapoints
                        .values()
                        .rev()
                        .take(REGIME_N)
                        .map(|dp| dp.kline.high.to_f32() as f64)
                        .collect::<Vec<_>>()
                        .into_iter()
                        .rev()
                        .collect();
                    let lows = ts
                        .datapoints
                        .values()
                        .rev()
                        .take(REGIME_N)
                        .map(|dp| dp.kline.low.to_f32() as f64)
                        .collect::<Vec<_>>()
                        .into_iter()
                        .rev()
                        .collect();
                    (closes, highs, lows)
                }
                PlotData::TickBased(ta) => {
                    let closes = ta
                        .datapoints
                        .iter()
                        .rev()
                        .take(REGIME_N)
                        .map(|dp| dp.kline.close.to_f32() as f64)
                        .collect::<Vec<_>>()
                        .into_iter()
                        .rev()
                        .collect();
                    let highs = ta
                        .datapoints
                        .iter()
                        .rev()
                        .take(REGIME_N)
                        .map(|dp| dp.kline.high.to_f32() as f64)
                        .collect::<Vec<_>>()
                        .into_iter()
                        .rev()
                        .collect();
                    let lows = ta
                        .datapoints
                        .iter()
                        .rev()
                        .take(REGIME_N)
                        .map(|dp| dp.kline.low.to_f32() as f64)
                        .collect::<Vec<_>>()
                        .into_iter()
                        .rev()
                        .collect();
                    (closes, highs, lows)
                }
            };

        let atr_f64 = atr.unwrap_or(0.0);
        let regime = adapter::derive_regime_with_stress(
            &recent_closes,
            &recent_highs,
            &recent_lows,
            atr_f64,
            self.last_regime_enum,
        );

        // Delta slice oldest-first, aligned index-for-index with recent_highs/recent_lows.
        // Only the most recent REGIME_N bars are available; absorption falls back to Unknown
        // for bars where the delta slice is shorter than the highs/lows slice.
        let recent_deltas: Vec<f64> = self.indicators[KlineIndicator::CumulativeDelta]
            .as_ref()
            .map(|i| i.recent_delta_slice(REGIME_N))
            .unwrap_or_default();
        let (failed_acceptance, footprint_absorption) =
            adapter::derive_failed_acceptance_and_absorption(
                &recent_highs,
                &recent_lows,
                &recent_closes,
                vah,
                val,
                &recent_deltas,
                cvd_slope,
            );
        let cvd_divergence = adapter::derive_cvd_divergence(&recent_highs, &recent_lows, cvd_slope);

        let bid_wall_nearby = adapter::wall_nearby(&orderbook.walls_below, price, atr_f64);
        let ask_wall_nearby = adapter::wall_nearby(&orderbook.walls_above, price, atr_f64);
        let price_action_clean = {
            let n = recent_closes.len();
            let recent_5 = &recent_closes[n.saturating_sub(5)..];
            adapter::count_price_reversals(recent_5) <= 2
        };

        let stacked_imbalance = adapter::derive_stacked_imbalance(&recent_deltas);
        let (mss_active, sweep_confirmed) =
            adapter::derive_mss_and_sweep(&recent_highs, &recent_lows, &recent_closes);

        let flow = adapter::build_flow_context(
            cvd,
            cvd_slope,
            delta,
            buy_vol,
            sell_vol,
            vpin,
            failed_acceptance,
            footprint_absorption,
            cvd_divergence,
            None, // funding_rate — not available in GUI
            None, // basis — not available in GUI
            None, // oi_delta — not available in GUI
            None, // oi_momentum_aligned — not available in GUI
            bid_wall_nearby,
            ask_wall_nearby,
            price_action_clean,
            stacked_imbalance,
            mss_active,
            sweep_confirmed,
            None, // fast_slope — not available in GUI chart
            None, // oi_delta_zscore — not available in GUI chart
            None, // vpin_cdf — not available in GUI chart
            None, // cvd_divergence_persistence — not available in GUI chart
        );

        // Alimentar trackers con la vela cerrada y obtener sus snapshots
        self.ms_tracker
            .push_bar(bar_open, bar_high, bar_low, bar_close, bar_ts_ms);
        let bar_volume = buy_vol.unwrap_or(0.0) + sell_vol.unwrap_or(0.0);
        self.ob_detector.push_bar(
            bar_open, bar_high, bar_low, bar_close, bar_volume, bar_ts_ms,
        );
        self.fvg_detector.push_bar(bar_high, bar_low, bar_ts_ms);
        self.range_detector.push_bar(bar_high, bar_low, bar_close);

        let market_structure = self.ms_tracker.snapshot();

        // Guardar contexto de estructura y acumular breaks históricos (max 30)
        if let Some(ref ms) = market_structure {
            if let Some(ref new_break) = ms.last_event {
                let already_recorded = self
                    .structure_breaks
                    .last()
                    .map(|b| b.timestamp_ms == new_break.timestamp_ms)
                    .unwrap_or(false);
                if !already_recorded {
                    self.structure_breaks.push(new_break.clone());
                    if self.structure_breaks.len() > 30 {
                        self.structure_breaks.remove(0);
                    }
                }
            }
            self.ms_context = Some(ms.clone());
        }

        let ob_snap = self.ob_detector.snapshot(price);
        self.ob_context = Some(ob_snap.clone());
        let order_blocks = Some(ob_snap);

        let fvg_snap = self.fvg_detector.snapshot(price);
        self.fvg_context = Some(fvg_snap.clone());
        let fvg = Some(fvg_snap);

        let range = {
            let atr_val = self.indicators[KlineIndicator::Atr]
                .as_ref()
                .and_then(|i| i.latest_atr())
                .unwrap_or(0.0);
            let vp_poc = self.indicators[KlineIndicator::VolumeProfile]
                .as_ref()
                .and_then(|i| i.latest_vol_profile_levels())
                .map(|(poc, _, _)| poc);
            let ctx = self.range_detector.compute(price, atr_val, vp_poc);
            if ctx.valid {
                self.range_context = Some(ctx.clone());
                Some(ctx)
            } else {
                self.range_context = None;
                None
            }
        };

        let session = Some(classify_session(bar_ts_ms));

        // Construir InstitutionalContext con los trackers disponibles.
        // L/S ratios y liquidaciones quedan en Default (sin connector wired aún).
        let institutional = {
            let funding = self.funding_tracker.snapshot();
            let oi_trend = self.oi_tracker.snapshot();
            let ls_ratio = data::institutional::LsRatioContext::default();
            let liquidations = data::institutional::LiquidationSnapshot::default();

            let quality = if funding.current != 0.0 && oi_trend.current > 0.0 {
                data::strategy::types::DataQuality::Degraded // Partial — missing L/S + liqs
            } else {
                data::strategy::types::DataQuality::Missing
            };

            let sms = data::institutional::compute_smart_money_score(
                &ls_ratio,
                &oi_trend,
                &funding,
                &liquidations,
            );

            Some(data::institutional::InstitutionalContext {
                timestamp_ms: bar_ts_ms,
                liquidations,
                ls_ratio,
                oi_trend,
                taker_ratio: None,
                funding,
                quality,
                smart_money_score: Some(sms),
                liq_map: None,
            })
        };

        let (swing_high_20, swing_low_20) =
            adapter::derive_swing_highs_lows(&recent_highs, &recent_lows);

        let ctx = StrategyMarketContext {
            symbol: self.chart.ticker_info.ticker.to_string(),
            timestamp_ms: bar_ts_ms,
            price,
            regime,
            atr,
            volume_profile,
            vwap,
            flow,
            orderbook,
            institutional,
            swing_high_20,
            swing_low_20,
            market_structure,
            session,
            order_blocks,
            fvg,
            leverage: 0.0,
            prev_obi_l5: None,
            slow_slope: None,
            auction_state: None,
            vp_open_bias: None,
            htf_vp: None,
            range,
        };

        // Capas: TOML base → Mongo override por régimen (vía mongo_handles).
        // Único override en runtime: default_ttl_ms = TTL_BARS × interval del chart,
        // que depende del timeframe activo y por eso no vive en archivo ni en BD.
        let mongo = crate::strategy::mongo_handles();
        let cfg = StrategyConfig {
            default_ttl_ms: ttl_ms,
            ..mongo.loader.current()
        };

        // Al cambiar de régimen, solicitamos al loader de Mongo que recargue
        // deployed_params; el override se aplica a partir de la próxima barra.
        if ctx.regime != self.last_regime_enum {
            mongo.loader.request_reload(&format!("{:?}", ctx.regime));
        }
        self.last_regime = format!("{:?}", ctx.regime);
        self.last_regime_enum = ctx.regime;

        // Update DRR HUD snapshot from the latest context
        {
            use data::strategy::types::{AbsorptionSide, CvdDivergence, Side as SignalSide};
            let rng = ctx.range.as_ref();
            let is_long_bias = rng.map(|r| matches!(r.location,
                data::detectors::range_detector::RangeLocation::NearLow |
                data::detectors::range_detector::RangeLocation::OutsideLow
            )).unwrap_or(false);
            let absorption_active = [
                !matches!(ctx.flow.footprint_absorption, AbsorptionSide::None | AbsorptionSide::Unknown),
                if is_long_bias { ctx.flow.big_trade_bearish } else { ctx.flow.big_trade_bullish },
                matches!(ctx.flow.cvd_divergence,
                    Some(CvdDivergence::BullishAbsorption) | Some(CvdDivergence::BearishAbsorption)),
                if is_long_bias { ctx.flow.finish_action_bullish } else { ctx.flow.finish_action_bearish },
                rng.map(|r| r.sweep_range_low || r.sweep_range_high).unwrap_or(false),
            ];
            self.drr_hud = DrrHudState {
                regime:        format!("{:?}", ctx.regime),
                session_name:  ctx.session.as_ref().map(|s| format!("{:?}", s.session)).unwrap_or_default(),
                session_phase: ctx.session.as_ref().map(|s| format!("{:?}", s.phase)).unwrap_or_default(),
                vp_bias:       ctx.vp_open_bias.as_ref().map(|v| format!("{:?}", v.bias)).unwrap_or_default(),
                auction_state: ctx.auction_state.as_ref().map(|a| format!("{:?}", a.state)).unwrap_or_default(),
                range_valid:      rng.map(|r| r.valid).unwrap_or(false),
                range_location:   rng.map(|r| format!("{:?}", r.location)).unwrap_or_default(),
                range_size_atr:   rng.map(|r| r.range_size_atr).unwrap_or(0.0),
                range_touches_hi: rng.map(|r| r.touches_high).unwrap_or(0),
                range_touches_lo: rng.map(|r| r.touches_low).unwrap_or(0),
                range_sweep_low:  rng.map(|r| r.sweep_range_low).unwrap_or(false),
                range_sweep_high: rng.map(|r| r.sweep_range_high).unwrap_or(false),
                footprint_absorption: !matches!(ctx.flow.footprint_absorption, AbsorptionSide::None | AbsorptionSide::Unknown),
                big_trade_active: ctx.flow.big_trade_bullish || ctx.flow.big_trade_bearish,
                cvd_divergence:   ctx.flow.cvd_divergence.is_some(),
                finish_action:    ctx.flow.finish_action_bullish || ctx.flow.finish_action_bearish,
                sweep_present:    rng.map(|r| r.sweep_range_low || r.sweep_range_high).unwrap_or(false),
                absorption_count: absorption_active.iter().filter(|&&b| b).count() as u8,
                cvd_slope:        ctx.flow.cvd_slope,
                vpin:             ctx.flow.vpin,
                delta_velocity:   ctx.flow.delta_velocity,
                micro_vol_traj:   String::new(),
                micro_late_surge: 0.0,
                micro_delta_slope: 0.0,
                micro_absorb:     0.0,
            };
        }

        // ── Micro-window snapshot — build at bar close ────────────────────────
        {
            use data::strategy::micro_window::MicroCtx;
            let rng = ctx.range.as_ref();
            let micro_ctx = MicroCtx {
                atr:         ctx.atr.unwrap_or(1.0).max(1e-9),
                range_loc:   rng.map(|r| r.location),
                range_high:  rng.filter(|r| r.valid).map(|r| r.range_high),
                range_low:   rng.filter(|r| r.valid).map(|r| r.range_low),
                range_mid:   rng.filter(|r| r.valid).map(|r| r.range_mid),
                in_drr_zone: rng.map(|r| r.no_trade_zone),
            };
            // Only build when buffer has received at least one trade
            let row = self.micro_buffer.build_row(&micro_ctx, "binance", &ctx.symbol);
            self.drr_hud.micro_vol_traj    = row.vol_trajectory.clone();
            self.drr_hud.micro_late_surge  = row.late_surge_ratio;
            self.drr_hud.micro_delta_slope = row.delta_slope_norm;
            self.drr_hud.micro_absorb      = row.absorption_proxy;
            // Store snap for strip rendering (rolling 200 bars)
            if self.micro_snaps.len() >= 200 {
                self.micro_snaps.pop_front();
            }
            self.micro_snaps.push_back((bar_ts_ms, MicroSnap {
                vol_trajectory:  row.vol_trajectory,
                late_surge_ratio: row.late_surge_ratio,
                delta_slope_norm: row.delta_slope_norm,
                absorption_proxy: row.absorption_proxy,
            }));
            // Reset buffer for the next candle
            self.micro_buffer.reset(bar_ts_ms + interval_ms as i64);
        }

        // ── Scalping engine — bar close update ───────────────────────────────
        {
            use data::strategy::scalping::{ScalpingRegime, absorption, cvd_divergence, obi_maker, is_scalping_session};
            use data::strategy::scalping::paper::ScalpingExitReason;

            let session_ts = data::session::classify_session(bar_ts_ms);
            let current_session = session_ts.session;

            self.scalping_state.on_bar_close(
                ctx.flow.cvd.unwrap_or(0.0),
                ctx.flow.delta.unwrap_or(0.0),
                (ctx.flow.buy_volume.unwrap_or(0.0) + ctx.flow.sell_volume.unwrap_or(0.0)),
                bar_high,
                bar_low,
                current_session,
                0.0, // big_cvd no disponible en UI (solo en monitor live)
                0.0, // bar_vol_usd no disponible en UI
            );

            let cfg_scalping = &cfg.scalping;
            if cfg_scalping.enabled {
                let scalping_regime = match ctx.regime {
                    data::strategy::types::Regime::TrendUp | data::strategy::types::Regime::TrendDown => ScalpingRegime::Trend,
                    _ => ScalpingRegime::Range,
                };
                let obi_l5 = ctx.orderbook.obi_l5.unwrap_or(0.0);
                let micro_price = ctx.orderbook.microprice.unwrap_or(bar_close);
                let spread_ticks = self.scalping_hud.spread_ticks;

                // Cerrar posición si la sesión terminó
                if self.scalping_state.paper.has_position() && !is_scalping_session(current_session) {
                    let obi_exit = self.scalping_state.obi_ema_fast;
                    let slope_exit = self.scalping_hud.cvd_slope;
                    self.scalping_state.paper.close(bar_close, bar_ts_ms, ScalpingExitReason::SessionEnd);
                    Self::journal_close(&self.scalping_state.paper, obi_exit, slope_exit);
                    self.scalping_state.active_signal = None;
                }

                let scalp_ctx = self.scalping_state.build_context(
                    obi_l5, micro_price, spread_ticks,
                    ctx.flow.cvd.unwrap_or(0.0),
                    ctx.flow.cvd_slope,
                    bar_open, bar_high, bar_low, bar_close,
                    ctx.flow.delta.unwrap_or(0.0),
                    ctx.flow.buy_volume.unwrap_or(0.0) + ctx.flow.sell_volume.unwrap_or(0.0),
                    ctx.atr.unwrap_or(100.0),
                    scalping_regime,
                    current_session,
                    ctx.vwap.vwap_session,
                    ctx.volume_profile.poc,
                    ctx.flow.funding_rate,
                    0.0, // liq_ratio — no disponible en el chart local
                    bar_ts_ms,
                    ctx.range.clone(),
                    ctx.flow.footprint_levels.clone(),
                    ctx.flow.big_trade_bullish,
                    ctx.flow.big_trade_bearish,
                );

                // ── Gates en tiempo real ─────────────────────────────────────────
                {
                    // obi_ema_fast ya está en [0,1] — 0.5 = neutral
                    let obi_dev = (scalp_ctx.obi_ema_fast - 0.5).abs();
                    self.scalping_hud.session_ok = is_scalping_session(current_session);
                    self.scalping_hud.s1_obi_dev = obi_dev; // desviación del neutral [0..0.5]
                    self.scalping_hud.s1_obi_ok = obi_dev >= 0.05;
                    self.scalping_hud.s1_spread_ok = spread_ticks <= cfg_scalping.max_spread_ticks;
                    self.scalping_hud.s1_vr_ok = scalp_ctx.vr <= cfg_scalping.max_vr;
                    self.scalping_hud.s2_regime_ok = matches!(scalping_regime, ScalpingRegime::Range);
                    self.scalping_hud.s2_dz = scalp_ctx.dz;
                    self.scalping_hud.s2_dz_ok = scalp_ctx.dz.abs() >= cfg_scalping.s2_dz_min;
                    self.scalping_hud.s2_vr = scalp_ctx.vr;
                    self.scalping_hud.s2_vr_ok = scalp_ctx.vr >= cfg_scalping.s2_vr_min;
                    self.scalping_hud.last_bar_close_instant = Some(std::time::Instant::now());
                    self.scalping_hud.bar_period_secs = (interval_ms / 1000) as u32;
                }

                // ── Check TP/SL/time-stop al cierre de cada barra ────────────
                if self.scalping_state.paper.has_position() {
                    let obi_now = self.scalping_state.obi_ema_fast;
                    if let Some(reason) = self.scalping_state.paper.check_exit(
                        bar_close,
                        obi_now,
                        spread_ticks,
                        bar_ts_ms,
                        cfg_scalping.time_stop_secs,
                    ) {
                        self.scalping_state.paper.close(bar_close, bar_ts_ms, reason);
                        Self::journal_close(
                            &self.scalping_state.paper,
                            obi_now,
                            scalp_ctx.cvd_slope,
                        );
                        self.scalping_state.active_signal = None;
                    }
                }

                // ── Detectar nuevas señales ───────────────────────────────────
                let can_trade = self.scalping_state.paper.can_trade(
                    cfg_scalping.max_trades_per_session,
                    cfg_scalping.daily_loss_limit_pct,
                    cfg_scalping.max_consecutive_losses,
                );

                if !self.scalping_state.paper.has_position() {
                    let sig = absorption::detect(&scalp_ctx, cfg_scalping)
                        .or_else(|| cvd_divergence::detect(&scalp_ctx, cfg_scalping))
                        .or_else(|| obi_maker::detect(&scalp_ctx, cfg_scalping));

                    if let Some(s) = sig {
                        if can_trade {
                            Self::journal_open(&s, &scalp_ctx, self.scalping_hud.obi_l5_norm, bar_ts_ms);
                            self.scalping_state.paper.open(
                                s.clone(), bar_ts_ms,
                                scalp_ctx.obi_ema_fast,
                                self.scalping_hud.obi_l5_norm,
                                scalp_ctx.dz, scalp_ctx.vr,
                                scalp_ctx.cvd, scalp_ctx.cvd_slope,
                                spread_ticks,
                            );
                            self.scalping_state.active_signal = Some(s);
                            self.scalping_state.signal_entry_ms = Some(bar_ts_ms);
                        } else {
                            // Señal válida pero circuit breaker activo — registrar para análisis
                            Self::journal_skipped(&s, &scalp_ctx, bar_ts_ms, "circuit_breaker");
                        }
                    }
                }

                // Actualizar HUD
                let paper = &self.scalping_state.paper;
                self.scalping_hud.can_trade = paper.can_trade(
                    cfg_scalping.max_trades_per_session,
                    cfg_scalping.daily_loss_limit_pct,
                    cfg_scalping.max_consecutive_losses,
                );
                self.scalping_hud.daily_trades = paper.daily_trades;
                self.scalping_hud.max_trades = cfg_scalping.max_trades_per_session;
                self.scalping_hud.daily_pnl = paper.daily_pnl;
                self.scalping_hud.consecutive_losses = paper.consecutive_losses;
                self.scalping_hud.total_equity = paper.total_equity;
                self.scalping_hud.session_start_equity = paper.session_start_equity;
                self.scalping_hud.session = format!("{:?}", current_session);
                self.scalping_hud.cvd_session = scalp_ctx.cvd;
                self.scalping_hud.cvd_slope = scalp_ctx.cvd_slope;

                self.scalping_hud.has_position = paper.has_position();
                self.scalping_hud.pos_lot_btc = paper.active_lot_btc();
                self.scalping_hud.pos_lot_notional = paper.active_lot_notional();
                if let Some(sig) = &self.scalping_state.active_signal {
                    self.scalping_hud.pos_strategy = sig.strategy.to_string();
                    self.scalping_hud.pos_side     = format!("{:?}", sig.side);
                    self.scalping_hud.pos_entry    = sig.entry_price;
                    self.scalping_hud.pos_stop     = sig.stop_price;
                    self.scalping_hud.pos_tp1      = sig.tp1_price;
                    self.scalping_hud.pos_tp2      = sig.tp2_price;
                    self.scalping_hud.pos_entry_ms = self.scalping_state.signal_entry_ms.unwrap_or(0);
                    // BE: si el paper engine ya movió el SL a entry
                    self.scalping_hud.pos_sl_at_be = self.scalping_state.paper
                        .closed_trades.last()
                        .map(|_| false) // no closed = still open; check via paper internals
                        .unwrap_or(false);
                }
                self.scalping_hud.pos_current_price = bar_close;

                // Últimos 5 trades para la tabla compacta + historial completo
                let trades = &paper.closed_trades;
                let start = trades.len().saturating_sub(5);
                let snap_fn = |t: &data::strategy::scalping::paper::ScalpingTrade| ScalpingTradeSnap {
                    strategy: t.strategy.clone(),
                    side: format!("{:?}", t.side),
                    result_r: t.result_r,
                    exit_reason: t.exit_reason.to_string(),
                    duration_secs: t.duration_ms / 1000,
                    won: t.pnl_net > 0.0,
                };
                self.scalping_hud.trades = trades[start..].iter().rev().map(&snap_fn).collect();
                self.scalping_hud.all_trades = trades.iter().rev().map(&snap_fn).collect();
            }
        }

        // ── RangeBreakoutFlow detector ────────────────────────────────────────
        {
            let session_rbf = data::session::classify_session(bar_ts_ms).session;
            let bar_delta_rbf = ctx.flow.delta.unwrap_or(0.0);
            let vol_rbf = ctx.flow.buy_volume.unwrap_or(0.0)
                + ctx.flow.sell_volume.unwrap_or(0.0);

            // Invalidar señal activa si el precio tocó stop o target
            if let Some(ref sig) = self.rbf_active_signal {
                use data::strategy::detectors::range_breakout_flow::RbfDirection;
                let hit_stop = match sig.direction {
                    RbfDirection::Short => bar_close >= sig.stop_price,
                    RbfDirection::Long  => bar_close <= sig.stop_price,
                };
                let hit_target = match sig.direction {
                    RbfDirection::Short => bar_close <= sig.target_price,
                    RbfDirection::Long  => bar_close >= sig.target_price,
                };
                if hit_stop || hit_target {
                    self.rbf_active_signal = None;
                }
            }

            let vwap_rbf = ctx.vwap.vwap_session;
            let fund_rbf = ctx.flow.funding_rate;
            if let Some(new_sig) = self.rbf_state.on_bar_close(
                bar_open, bar_high, bar_low, bar_close,
                vol_rbf, bar_delta_rbf,
                session_rbf,
                bar_ts_ms,
                &cfg.range_breakout,
                vwap_rbf,
                fund_rbf,
                0.0, // liq_ratio no disponible en UI local
                ctx.orderbook.obi_l5.unwrap_or(0.0),
                None, // cvd_slope_ext
                None, // gate de confluencia no disponible en UI local
            ) {
                self.rbf_active_signal = Some(new_sig);
            }
        }

        // ── Finalise big-trades bar ───────────────────────────────────────────
        {
            let bar = self.big_trades_acc.finalise(bar_ts_ms);
            if self.big_trade_bars.len() >= 200 { self.big_trade_bars.pop_front(); }
            self.big_trade_bars.push_back(bar);
        }

        self.bar_index += 1;
        self.last_evaluated_bar_ms = Some(bar_ts_ms);
        let current_bar = self.bar_index;

        let (mut signal, detector_log) = router::route_strategy(&ctx, &cfg);
        self.last_detector_log = detector_log.clone();

        // Cooldown: suppress repeat signals from the same strategy within cooldown_bars.
        // Only the winning strategy enters cooldown — others remain available.
        if signal.action == StrategyAction::ShadowSignal {
            if let Some(id) = signal.strategy_id {
                if !self.cooldown_registry.is_available(id, current_bar) {
                    let remaining = self.cooldown_registry.bars_remaining(id, current_bar);
                    signal.action = StrategyAction::Wait;
                    signal
                        .missing
                        .push(format!("COOLDOWN_ACTIVE:{remaining}_bars_remaining"));
                } else {
                    let side = match signal.side {
                        Some(Side::Long) => data::strategy::cooldown::SignalSide::Long,
                        _ => data::strategy::cooldown::SignalSide::Short,
                    };
                    self.cooldown_registry.register_signal(
                        id,
                        current_bar,
                        side,
                        signal.entry_price.unwrap_or(price),
                    );
                }
            }
        }

        let reasoning = data::strategy::playbook_reasoning::classify_playbook_reasoning(
            &ctx,
            &cfg,
            &signal,
            &detector_log,
        );
        logger::log_signal_with_reasoning(&ctx, &signal, &reasoning);

        let signal_fired = signal.action == StrategyAction::ShadowSignal;
        crate::strategy::intent_logger::log_near_misses(&ctx, &cfg, signal_fired);

        // Snapshot del oid ANTES de paper.on_bar_close: un trade que se cierra
        // en esta barra pertenece a la señal previa, no a la nueva que pueda
        // dispararse aquí. Mismo orden que el monitor de Railway.
        let trade_oid_for_close = self.pending_signal_oid;
        let prev_closed = self.paper_account.closed_trades.len();

        let paper_sig = if signal_fired { Some(&signal) } else { None };
        self.paper_account.on_bar_close(
            &ctx.symbol,
            bar_close,
            bar_high,
            bar_low,
            ctx.timestamp_ms,
            paper_sig,
            Some(&ctx),
        );

        // Persistir a Mongo los trades que se cerraron en esta barra.
        for trade in &self.paper_account.closed_trades[prev_closed..] {
            mongo.writer.write_trade(trade, trade_oid_for_close);
        }

        if signal.action == StrategyAction::ShadowSignal {
            // Generar oid síncrono y persistir la señal antes de mover `signal`.
            // El oid queda como `pending_signal_oid` para enlazar trades futuros.
            self.pending_signal_oid = mongo.writer.write_signal(&signal, &ctx);
            self.outcome_tracker
                .push_signal(&ctx.symbol, &signal, bar_close);
            self.push_strategy_signal(signal);
        }

        self.outcome_tracker
            .update(bar_high, bar_low, ctx.timestamp_ms);
    }

    pub fn push_strategy_signal(&mut self, signal: StrategySignal) {
        const MAX_SIGNALS: usize = 50;
        if self.strategy_signals.len() >= MAX_SIGNALS {
            self.strategy_signals.remove(0);
        }
        self.strategy_signals.push(signal);
        self.chart.cache.main.clear();
    }

    pub fn strategy_snapshot(&self) -> crate::strategy::snapshot::StrategySnapshot {
        use crate::strategy::snapshot::{PositionSnap, SignalSnap, StrategySnapshot, TradeSnap};

        let paper = &self.paper_account;
        let wins = paper
            .closed_trades
            .iter()
            .filter(|t| t.net_pnl > 0.0)
            .count();
        let losses = paper
            .closed_trades
            .iter()
            .filter(|t| t.net_pnl <= 0.0)
            .count();

        let current_price = self
            .chart
            .last_price
            .map(|lp| match lp {
                crate::chart::scale::linear::PriceInfoLabel::Up(p)
                | crate::chart::scale::linear::PriceInfoLabel::Down(p)
                | crate::chart::scale::linear::PriceInfoLabel::Neutral(p) => p.to_f32() as f64,
            })
            .unwrap_or(0.0);

        let open_positions = paper
            .open_positions
            .iter()
            .map(|p| {
                let upnl_pct = if p.balance_at_open > 0.0 {
                    let upnl = match p.side {
                        crate::strategy::types::Side::Long => {
                            p.size * (current_price - p.entry_price)
                        }
                        crate::strategy::types::Side::Short => {
                            p.size * (p.entry_price - current_price)
                        }
                    };
                    upnl / p.balance_at_open * 100.0
                } else {
                    0.0
                };
                PositionSnap {
                    side: if p.side == crate::strategy::types::Side::Long {
                        "Long".into()
                    } else {
                        "Short".into()
                    },
                    strategy_name: p
                        .strategy_id
                        .map_or("Unknown".into(), |id| format!("{id:?}")),
                    entry_price: p.entry_price,
                    stop_price: p.stop_price,
                    target_price: p.target_price,
                    unrealized_pnl_pct: upnl_pct,
                }
            })
            .collect();

        let active_signal = self
            .strategy_signals
            .iter()
            .find(|s| s.action == StrategyAction::ShadowSignal)
            .map(|s| SignalSnap {
                strategy_name: s
                    .strategy_id
                    .map_or("Unknown".into(), |id| format!("{id:?}")),
                side: s.side.map_or("?".into(), |side| {
                    if side == crate::strategy::types::Side::Long {
                        "Long".into()
                    } else {
                        "Short".into()
                    }
                }),
                score: s.score,
                evidence: s.evidence.clone(),
                missing: s.missing.clone(),
            });

        let recent_trades = paper
            .closed_trades
            .iter()
            .rev()
            .take(5)
            .map(|t| TradeSnap {
                strategy_name: t.strategy_id.clone().unwrap_or_else(|| "Unknown".into()),
                side: t.side.clone(),
                close_reason: t.close_reason.clone(),
                net_pnl: t.net_pnl,
                net_pnl_pct: t.net_pnl_pct,
            })
            .collect();

        let last_score = self.strategy_signals.last().map(|s| s.score).unwrap_or(0.0);
        let regime = self.last_regime.clone();

        // Señales vitales para feedback visual mientras los detectores aún no
        // aprueban: cuántas barras se evaluaron desde que arrancó la UI,
        // timestamp de la última, y el primer motivo de rechazo más reciente
        // (lo que le falta al detector ahora mismo).
        let last_missing = self
            .strategy_signals
            .last()
            .and_then(|s| s.missing.first().cloned());

        StrategySnapshot {
            symbol: self.chart.ticker_info.ticker.display_symbol_and_type().0,
            equity: paper.equity,
            balance: paper.balance,
            initial_capital: paper.config.initial_capital,
            wins,
            losses,
            overlay_enabled: self.strategy_overlay_enabled,
            regime,
            last_score,
            open_positions,
            active_signal,
            recent_trades,
            bars_evaluated: self.bar_index,
            last_bar_ms: self.last_evaluated_bar_ms,
            last_missing,
            detector_log: self.last_detector_log.clone(),
        }
    }

    pub fn clear_expired_signals(&mut self, now_ms: i64) {
        let before = self.strategy_signals.len();
        self.strategy_signals
            .retain(|s| s.created_at_ms + s.ttl_ms > now_ms);
        if self.strategy_signals.len() != before {
            self.chart.cache.main.clear();
        }
    }

    fn draw_indicator_overlays(
        &self,
        frame: &mut canvas::Frame,
        region: &Rectangle,
        earliest: u64,
        latest: u64,
        interval_to_x: impl Fn(u64) -> f32,
        price_to_y: impl Fn(Price) -> f32,
    ) {
        let line_width = region.x + region.width;

        for (kind, indicator) in self.indicators.iter() {
            if !indicator::kline::is_overlay_indicator(kind) {
                continue;
            }
            let Some(indi) = indicator.as_ref() else {
                continue;
            };

            // Per-indicator band colors: VWAP uses blue, VolumeProfile uses purple
            let band_colors: [Color; 2] = match kind {
                data::chart::indicator::KlineIndicator::VolumeProfile => [
                    Color::from_rgba(0.55, 0.36, 0.96, 0.05), // value area: subtle purple
                    Color::from_rgba(0.55, 0.36, 0.96, 0.05),
                ],
                _ => [
                    Color::from_rgba(0.0, 0.55, 1.0, 0.05), // VWAP ±2σ: outer blue
                    Color::from_rgba(0.0, 0.55, 1.0, 0.09), // VWAP ±1σ: inner blue
                ],
            };

            // Draw bands (shaded regions between upper/lower)
            let bands = indi.overlay_bands(earliest, latest);
            for (band_idx, band) in bands.iter().enumerate() {
                if band.len() < 2 {
                    continue;
                }
                let color = band_colors[band_idx % band_colors.len()];

                let fill_path = Path::new(|builder| {
                    let mut started = false;

                    // Forward pass: upper edge
                    for &(key, upper, _) in band.iter() {
                        if !upper.is_finite() || upper <= 0.0 {
                            continue;
                        }
                        let x = interval_to_x(key);
                        let y = price_to_y(Price::from_f32(upper));
                        if !x.is_finite() || !y.is_finite() {
                            continue;
                        }
                        if !started {
                            builder.move_to(Point::new(x, y));
                            started = true;
                        } else {
                            builder.line_to(Point::new(x, y));
                        }
                    }

                    if !started {
                        return;
                    }

                    // Reverse pass: lower edge (closes the shape)
                    for &(key, _, lower) in band.iter().rev() {
                        if !lower.is_finite() || lower <= 0.0 {
                            continue;
                        }
                        let x = interval_to_x(key);
                        let y = price_to_y(Price::from_f32(lower));
                        if !x.is_finite() || !y.is_finite() {
                            continue;
                        }
                        builder.line_to(Point::new(x, y));
                    }

                    builder.close();
                });

                frame.fill(&fill_path, color);
            }

            // Draw main overlay line (VWAP center line)
            let points: Vec<_> = indi
                .overlay_line_points(earliest, latest)
                .into_iter()
                .filter(|(_, price)| price.is_finite() && *price > 0.0)
                .collect();
            if points.len() >= 2 {
                let path = Path::new(|builder| {
                    let mut started = false;
                    for &(key, price) in &points {
                        let x = interval_to_x(key);
                        let y = price_to_y(Price::from_f32(price));
                        if !x.is_finite() || !y.is_finite() {
                            continue;
                        }
                        if !started {
                            builder.move_to(Point::new(x, y));
                            started = true;
                        } else {
                            builder.line_to(Point::new(x, y));
                        }
                    }
                });
                let line_color = match kind {
                    data::chart::indicator::KlineIndicator::Vwap => {
                        Color::from_rgba(0.20, 0.75, 1.0, 0.95)
                    }
                    _ => Color::from_rgba(0.0, 0.6, 1.0, 0.9),
                };
                frame.stroke(
                    &path,
                    Stroke::with_color(
                        Stroke {
                            width: 2.0,
                            ..Default::default()
                        },
                        line_color,
                    ),
                );
            }

            // Draw extra overlay lines (session VWAPs, etc.)
            for (pts, rgba) in indi.overlay_extra_lines(earliest, latest) {
                let filtered: Vec<_> = pts
                    .iter()
                    .filter(|(_, p)| p.is_finite() && *p > 0.0)
                    .collect();
                if filtered.len() < 2 {
                    continue;
                }
                let path = Path::new(|builder| {
                    let mut started = false;
                    for &&(key, price) in &filtered {
                        let x = interval_to_x(key);
                        let y = price_to_y(Price::from_f32(price));
                        if !x.is_finite() || !y.is_finite() {
                            started = false;
                            continue;
                        }
                        if !started {
                            builder.move_to(Point::new(x, y));
                            started = true;
                        } else {
                            builder.line_to(Point::new(x, y));
                        }
                    }
                });
                let color = Color::from_rgba(rgba[0], rgba[1], rgba[2], rgba[3]);
                frame.stroke(
                    &path,
                    Stroke::with_color(
                        Stroke {
                            width: 1.5,
                            ..Default::default()
                        },
                        color,
                    ),
                );
            }

            // Draw overlay levels (Volume Profile POC/VAH/VAL)
            let levels = indi.overlay_levels();
            for (price, rgba) in &levels {
                if *price <= 0.0 || !price.is_finite() {
                    continue;
                }
                let y = price_to_y(Price::from_f32(*price));
                if !y.is_finite() {
                    continue;
                }
                let color = Color::from_rgba(rgba[0], rgba[1], rgba[2], rgba[3]);
                frame.stroke(
                    &Path::line(Point::new(region.x, y), Point::new(line_width, y)),
                    Stroke::with_color(
                        Stroke {
                            width: 1.0,
                            line_dash: LineDash {
                                segments: &[6.0, 4.0],
                                offset: 0,
                            },
                            ..Default::default()
                        },
                        color,
                    ),
                );
            }

            // Draw Volume Profile horizontal histogram on the right side
            let histogram = indi.overlay_volume_profile();
            let max_vol = indi.overlay_volume_profile_max();
            if !histogram.is_empty() && max_vol > 0.0 {
                let poc_price = levels.first().map(|(p, _)| *p).unwrap_or(0.0);
                let max_bar_width = region.width * 0.14;
                let right_edge = region.x + region.width;
                let n = histogram.len();

                for i in 0..n {
                    let bar = histogram[i];
                    if !bar.price.is_finite() || bar.price <= 0.0 {
                        continue;
                    }

                    let y_center = price_to_y(Price::from_f32(bar.price));
                    if !y_center.is_finite() {
                        continue;
                    }

                    // Bar height: half the gap to neighbors, minimum 1px
                    let bar_height = {
                        let y_next = if i + 1 < n {
                            price_to_y(Price::from_f32(histogram[i + 1].price))
                        } else {
                            y_center
                        };
                        let y_prev = if i > 0 {
                            price_to_y(Price::from_f32(histogram[i - 1].price))
                        } else {
                            y_center
                        };
                        let spacing = if i == 0 {
                            (y_next - y_center).abs()
                        } else if i == n - 1 {
                            (y_center - y_prev).abs()
                        } else {
                            (y_next - y_prev).abs() / 2.0
                        };
                        spacing.max(1.0)
                    };

                    let vol_ratio = bar.volume / max_vol;
                    let bar_width = vol_ratio as f32 * max_bar_width;
                    let x = right_edge - bar_width;
                    let is_poc = (bar.price - poc_price).abs() < poc_price * 0.0005;

                    let color = if is_poc {
                        Color::from_rgba(1.0, 0.78, 0.05, 0.90) // gold — POC
                    } else if vol_ratio > 0.65 {
                        Color::from_rgba(0.55, 0.36, 0.96, 0.60) // purple — HVN
                    } else if vol_ratio < 0.12 {
                        Color::from_rgba(0.3, 0.65, 1.0, 0.18) // dim blue — LVN
                    } else {
                        Color::from_rgba(0.45, 0.65, 0.95, 0.35) // blue — normal
                    };

                    frame.fill_rectangle(
                        Point::new(x, y_center - bar_height / 2.0),
                        Size::new(bar_width, bar_height),
                        color,
                    );
                }
            }
        }
    }

    // ── Liquidation event strip ──────────────────────────────────────────────
    // Per-candle aggregated liquidation volume shown as a colored strip at the
    // bottom of the chart. Long liq = orange (longs blown out), short liq = cyan.
    // Big single events (>$1M) also show a triangle marker on the candle itself.
    fn draw_liq_strip(
        &self,
        frame: &mut canvas::Frame,
        region: &Rectangle,
        interval_to_x: impl Fn(u64) -> f32,
        cell_width: f32,
        interval_ms: u64,
    ) {
        let strip_h  = 8.0_f32;
        // Position: sits just above the x-axis labels, below the candles area
        let strip_y  = region.y + region.height - strip_h - 2.0;
        let half_w   = (cell_width * 0.42).max(1.5);
        let col_long  = Color::from_rgba(0.95, 0.50, 0.10, 0.88); // long liq  → orange
        let col_short = Color::from_rgba(0.20, 0.85, 0.85, 0.88); // short liq → cyan

        // Always draw a dim background line so the strip zone is always visible
        frame.fill_rectangle(
            Point::new(region.x, strip_y + strip_h - 1.0),
            Size::new(region.width, 1.0),
            Color::from_rgba(0.45, 0.45, 0.50, 0.25),
        );

        if self.liq_events.is_empty() {
            return; // no events yet — base line still visible above
        }

        // Group events by candle bucket
        let mut buckets: std::collections::HashMap<u64, (f64, f64)> =
            std::collections::HashMap::new();
        for ev in &self.liq_events {
            let ts     = ev.time.as_u64();
            let bucket = (ts / interval_ms) * interval_ms;
            let usd    = ev.price.to_f32() as f64 * ev.qty.to_f32_lossy() as f64;
            let e      = buckets.entry(bucket).or_default();
            if ev.is_long_liq { e.0 += usd; } else { e.1 += usd; }
        }

        // Normalise height: log scale so small events are still visible
        let max_usd = buckets.values()
            .map(|(a, b)| a.max(*b))
            .fold(1.0_f64, f64::max);

        for (bucket_ts, (long_usd, short_usd)) in &buckets {
            let cx = interval_to_x(*bucket_ts);
            if !cx.is_finite() || cx < region.x || cx > region.x + region.width { continue; }

            // Long liq (red side — longs blown out)
            if *long_usd > 10.0 {
                let ratio = (long_usd.ln_1p() / max_usd.ln_1p()) as f32;
                let h = (ratio * strip_h).clamp(1.0, strip_h);
                frame.fill_rectangle(
                    Point::new(cx - half_w, strip_y + strip_h - h),
                    Size::new(half_w, h),
                    col_long,
                );
            }
            // Short liq (cyan side — shorts blown out)
            if *short_usd > 10.0 {
                let ratio = (short_usd.ln_1p() / max_usd.ln_1p()) as f32;
                let h = (ratio * strip_h).clamp(1.0, strip_h);
                frame.fill_rectangle(
                    Point::new(cx, strip_y + strip_h - h),
                    Size::new(half_w, h),
                    col_short,
                );
            }
        }
    }

    /// Receives forced-liquidation events from the `@forceOrder` stream.
    pub fn on_liquidations(&mut self, events: &[exchange::Liquidation]) {
        const MAX_EVENTS: usize = 2000;
        for &ev in events {
            if self.liq_events.len() >= MAX_EVENTS { self.liq_events.pop_front(); }
            self.liq_events.push_back(ev);
        }
        self.invalidate(None);
    }

    fn draw_fvgs(
        &self,
        frame: &mut canvas::Frame,
        region: &Rectangle,
        earliest: u64,
        interval_to_x: impl Fn(u64) -> f32,
        price_to_y: impl Fn(Price) -> f32,
    ) {
        let Some(ref ctx) = self.fvg_context else {
            return;
        };

        let right_x = region.x + region.width;

        // Show at most 8 FVGs total (4 per side), most recent first
        let all_fvgs = ctx.bullish_fvgs.iter().rev().take(4)
            .chain(ctx.bearish_fvgs.iter().rev().take(4));

        for fvg in all_fvgs {
            // Keep fills very subtle — FVGs are reference zones, not highlights
            let (fill_a, border_a, base_color) = match (&fvg.fvg_type, &fvg.status) {
                (data::detectors::FvgType::Bullish, data::detectors::FvgStatus::Unfilled) =>
                    (0.10, 0.55, [0.10_f32, 0.78, 0.80]),
                (data::detectors::FvgType::Bullish, data::detectors::FvgStatus::PartiallyFilled) =>
                    (0.05, 0.28, [0.10, 0.78, 0.80]),
                (data::detectors::FvgType::Bearish, data::detectors::FvgStatus::Unfilled) =>
                    (0.10, 0.55, [0.95, 0.45, 0.10]),
                (data::detectors::FvgType::Bearish, data::detectors::FvgStatus::PartiallyFilled) =>
                    (0.05, 0.28, [0.95, 0.45, 0.10]),
                _ => continue, // Filled — skip
            };

            let fill_color   = Color::from_rgba(base_color[0], base_color[1], base_color[2], fill_a);
            let border_color = Color::from_rgba(base_color[0], base_color[1], base_color[2], border_a);

            let ts = fvg.timestamp_ms as u64;
            let x_left = if ts >= earliest {
                interval_to_x(ts).max(region.x)
            } else {
                region.x
            };
            let y_top    = price_to_y(Price::from_f32(fvg.high as f32));
            let y_bottom = price_to_y(Price::from_f32(fvg.low  as f32));
            let width    = right_x - x_left;
            let height   = y_bottom - y_top;

            if width <= 0.0 || height <= 0.0 || !y_top.is_finite() || !y_bottom.is_finite() {
                continue;
            }

            // Filled rectangle (no extended border lines)
            frame.fill_rectangle(Point::new(x_left, y_top), Size::new(width, height), fill_color);

            // Clean border: only top and bottom edges of the zone, not extended lines
            let border_stroke = Stroke {
                style: canvas::stroke::Style::Solid(border_color),
                width: 0.9,
                ..Default::default()
            };
            frame.stroke(&Path::line(Point::new(x_left, y_top),    Point::new(right_x, y_top)),    border_stroke.clone());
            frame.stroke(&Path::line(Point::new(x_left, y_bottom), Point::new(right_x, y_bottom)), border_stroke);

            // Small label only on unfilled zones, left-aligned near origin of the FVG
            if fvg.status == data::detectors::FvgStatus::Unfilled {
                frame.fill_text(canvas::Text {
                    content: "FVG".to_string(),
                    position: Point::new(x_left + 3.0, y_top + 2.0),
                    size: iced::Pixels(TEXT_SIZE * 0.70),
                    color: border_color,
                    align_x: iced::alignment::Horizontal::Left.into(),
                    align_y: iced::alignment::Vertical::Top,
                    font: style::AZERET_MONO,
                    ..canvas::Text::default()
                });
            }
        }
    }

    fn draw_structure(
        &self,
        frame: &mut canvas::Frame,
        region: &Rectangle,
        earliest: u64,
        latest: u64,
        interval_to_x: impl Fn(u64) -> f32,
        price_to_y: impl Fn(Price) -> f32,
    ) {
        let right_x = region.x + region.width;

        // ── Premium / Discount zones ──────────────────────────────────────────
        if let Some(ref ms) = self.ms_context {
            if let (Some(rh), Some(premium), Some(discount), Some(rl)) = (
                ms.range_high,
                ms.premium_threshold,
                ms.discount_threshold,
                ms.range_low,
            ) {
                // Premium zone (top of range → 75% line) — red tint
                let y_rh      = price_to_y(Price::from_f32(rh      as f32));
                let y_premium = price_to_y(Price::from_f32(premium as f32));
                let premium_h = y_premium - y_rh;
                if premium_h > 0.0 && y_rh.is_finite() && y_premium.is_finite() {
                    frame.fill_rectangle(
                        Point::new(region.x, y_rh),
                        Size::new(region.width, premium_h),
                        Color::from_rgba(0.90, 0.20, 0.20, 0.07),
                    );
                    // Border line at premium threshold
                    frame.stroke(
                        &Path::line(Point::new(region.x, y_premium), Point::new(right_x, y_premium)),
                        Stroke {
                            style: canvas::stroke::Style::Solid(Color::from_rgba(0.90, 0.35, 0.35, 0.35)),
                            width: 0.6,
                            line_dash: LineDash { segments: &[4.0, 4.0], offset: 0 },
                            ..Default::default()
                        },
                    );
                    frame.fill_text(canvas::Text {
                        content: "Premium".to_string(),
                        position: Point::new(region.x + 4.0, y_rh + 3.0),
                        size: iced::Pixels(TEXT_SIZE * 0.70),
                        color: Color::from_rgba(0.90, 0.45, 0.45, 0.55),
                        align_x: iced::alignment::Horizontal::Left.into(),
                        align_y: iced::alignment::Vertical::Top,
                        font: style::AZERET_MONO,
                        ..canvas::Text::default()
                    });
                }

                // Discount zone (25% line → bottom of range) — green tint
                let y_discount = price_to_y(Price::from_f32(discount as f32));
                let y_rl       = price_to_y(Price::from_f32(rl       as f32));
                let discount_h = y_rl - y_discount;
                if discount_h > 0.0 && y_discount.is_finite() && y_rl.is_finite() {
                    frame.fill_rectangle(
                        Point::new(region.x, y_discount),
                        Size::new(region.width, discount_h),
                        Color::from_rgba(0.20, 0.80, 0.30, 0.07),
                    );
                    frame.stroke(
                        &Path::line(Point::new(region.x, y_discount), Point::new(right_x, y_discount)),
                        Stroke {
                            style: canvas::stroke::Style::Solid(Color::from_rgba(0.35, 0.80, 0.40, 0.35)),
                            width: 0.6,
                            line_dash: LineDash { segments: &[4.0, 4.0], offset: 0 },
                            ..Default::default()
                        },
                    );
                    frame.fill_text(canvas::Text {
                        content: "Discount".to_string(),
                        position: Point::new(region.x + 4.0, y_rl - 3.0),
                        size: iced::Pixels(TEXT_SIZE * 0.70),
                        color: Color::from_rgba(0.40, 0.85, 0.45, 0.55),
                        align_x: iced::alignment::Horizontal::Left.into(),
                        align_y: iced::alignment::Vertical::Bottom,
                        font: style::AZERET_MONO,
                        ..canvas::Text::default()
                    });
                }
            }
        }

        // ── BOS / CHoCH markers — only last 8 visible breaks ─────────────────
        // Find the 8 most recent breaks that are in the visible range
        let visible_breaks: Vec<_> = self.structure_breaks.iter()
            .filter(|sb| {
                let ts = sb.timestamp_ms as u64;
                ts >= earliest && ts <= latest
            })
            .collect();
        // Show only the 8 most recent
        let start = visible_breaks.len().saturating_sub(8);
        let recent_breaks = &visible_breaks[start..];

        for sb in recent_breaks {
            let ts = sb.timestamp_ms as u64;
            let x = interval_to_x(ts);
            let y = price_to_y(Price::from_f32(sb.broken_level as f32));

            if !x.is_finite() || !y.is_finite() {
                continue;
            }

            let (label, color) = match (&sb.event, &sb.direction) {
                (data::structure::StructureEvent::Bos, data::structure::HtfBias::Bullish) =>
                    ("BOS▲", Color::from_rgba(0.25, 0.88, 0.50, 0.95)),
                (data::structure::StructureEvent::Bos, _) =>
                    ("BOS▼", Color::from_rgba(0.92, 0.30, 0.30, 0.95)),
                (data::structure::StructureEvent::Choch, data::structure::HtfBias::Bullish) =>
                    ("CHoCH▲", Color::from_rgba(0.40, 0.65, 1.0, 0.95)),
                (data::structure::StructureEvent::Choch, _) =>
                    ("CHoCH▼", Color::from_rgba(0.88, 0.50, 1.0, 0.95)),
            };

            // Dashed horizontal level line from break candle to right edge
            frame.stroke(
                &Path::line(Point::new(x, y), Point::new(right_x, y)),
                Stroke {
                    style: canvas::stroke::Style::Solid(color.scale_alpha(0.50)),
                    width: 0.8,
                    line_dash: LineDash { segments: &[5.0, 4.0], offset: 0 },
                    ..Default::default()
                },
            );

            // Small solid vertical tick at the break point
            frame.stroke(
                &Path::line(Point::new(x, y - 4.0), Point::new(x, y + 4.0)),
                Stroke {
                    style: canvas::stroke::Style::Solid(color),
                    width: 1.5,
                    ..Default::default()
                },
            );

            // Compact label immediately right of the tick
            frame.fill_text(canvas::Text {
                content: label.to_string(),
                position: Point::new(x + 4.0, y - 1.0),
                size: iced::Pixels(TEXT_SIZE * 0.78),
                color,
                align_x: iced::alignment::Horizontal::Left.into(),
                align_y: iced::alignment::Vertical::Bottom,
                font: style::AZERET_MONO,
                ..canvas::Text::default()
            });
        }
    }

    fn draw_order_blocks(
        &self,
        frame: &mut canvas::Frame,
        region: &Rectangle,
        earliest: u64,
        interval_to_x: impl Fn(u64) -> f32,
        price_to_y: impl Fn(Price) -> f32,
    ) {
        let Some(ref ctx) = self.ob_context else {
            return;
        };

        let right_x = region.x + region.width;

        // Show only the 3 most recent active/tested OBs per side — mitigated are hidden.
        // Sorted by recency: detector returns them oldest-first, so we take from the end.
        let bullish_obs: Vec<_> = ctx.bullish_obs.iter()
            .filter(|ob| !matches!(ob.status, data::detectors::OBStatus::Mitigated | data::detectors::OBStatus::Invalidated))
            .rev().take(3).collect();
        let bearish_obs: Vec<_> = ctx.bearish_obs.iter()
            .filter(|ob| !matches!(ob.status, data::detectors::OBStatus::Mitigated | data::detectors::OBStatus::Invalidated))
            .rev().take(3).collect();

        for ob in bullish_obs.iter().chain(bearish_obs.iter()) {
            let is_bullish = matches!(ob.ob_type, data::detectors::OBType::Bullish);
            let is_active  = matches!(ob.status, data::detectors::OBStatus::Active);

            // Colour palette — professional: minimal fill, strong origin border
            let (r, g, b): (f32, f32, f32) = if is_bullish { (0.20, 0.85, 0.45) } else { (0.92, 0.28, 0.28) };
            let fill_a   = if is_active { 0.06 } else { 0.03 };
            let border_a = if is_active { 0.75 } else { 0.40 };

            let fill_color    = Color::from_rgba(r, g, b, fill_a);
            let border_color  = Color::from_rgba(r, g, b, border_a);
            let origin_color  = Color::from_rgba(r, g, b, if is_active { 0.90 } else { 0.55 });

            let ts = ob.timestamp_ms as u64;
            let x_left = if ts >= earliest { interval_to_x(ts) } else { region.x };
            let x_left = x_left.max(region.x);
            let y_top    = price_to_y(Price::from_f32(ob.high as f32));
            let y_bottom = price_to_y(Price::from_f32(ob.low  as f32));
            let height   = y_bottom - y_top;
            let width    = right_x - x_left;

            if width <= 0.0 || height <= 0.0 || !y_top.is_finite() || !y_bottom.is_finite() {
                continue;
            }

            // Very subtle fill — just enough to show the zone without overpowering candles
            frame.fill_rectangle(Point::new(x_left, y_top), Size::new(width, height), fill_color);

            // Top + bottom borders (thin, extend to right edge)
            let edge = Stroke {
                style: canvas::stroke::Style::Solid(border_color),
                width: 0.7,
                ..Default::default()
            };
            frame.stroke(&Path::line(Point::new(x_left, y_top),    Point::new(right_x, y_top)),    edge.clone());
            frame.stroke(&Path::line(Point::new(x_left, y_bottom), Point::new(right_x, y_bottom)), edge);

            // Origin left-edge marker — thick solid line, this is the most visible cue
            frame.stroke(
                &Path::line(Point::new(x_left, y_top), Point::new(x_left, y_bottom)),
                Stroke {
                    style: canvas::stroke::Style::Solid(origin_color),
                    width: 2.5,
                    ..Default::default()
                },
            );

            // Label at the origin (left edge), near the top border
            let label = if is_active { "OB" } else { "OB~" };
            frame.fill_text(canvas::Text {
                content: label.to_string(),
                position: Point::new(x_left + 4.0, y_top + 2.0),
                size: iced::Pixels(TEXT_SIZE * 0.72),
                color: origin_color,
                align_x: iced::alignment::Horizontal::Left.into(),
                align_y: iced::alignment::Vertical::Top,
                font: style::AZERET_MONO,
                ..canvas::Text::default()
            });
        }
    }

    // ── DRR Range Overlay ────────────────────────────────────────────────────
    // Draws Range High / Low / Mid as dashed lines, shades the no-trade zone
    // (35-65% of range), and marks any detected sweep with a small triangle.
    fn draw_drr_range(
        range: &data::detectors::RangeContext,
        frame: &mut canvas::Frame,
        region: Rectangle,
        price_to_y: impl Fn(f64) -> f32,
    ) {
        if !range.valid { return; }

        let x0 = region.x;
        let x1 = region.x + region.width;

        let y_hi  = price_to_y(range.range_high);
        let y_lo  = price_to_y(range.range_low);
        let y_mid = price_to_y(range.range_mid);

        if !y_hi.is_finite() || !y_lo.is_finite() || !y_mid.is_finite() { return; }
        if y_hi >= y_lo { return; } // chart clipped

        // ── No-trade zone (35-65%) — subtle amber fill ──────────────────────
        let range_h = y_lo - y_hi;
        let y_zone_top = y_hi + range_h * 0.35;
        let y_zone_bot = y_hi + range_h * 0.65;
        frame.fill_rectangle(
            Point::new(x0, y_zone_top),
            Size::new(x1 - x0, y_zone_bot - y_zone_top),
            Color::from_rgba(0.95, 0.75, 0.20, 0.06),
        );

        // ── Range High line (orange dashed) ─────────────────────────────────
        let dashed = Stroke {
            style: canvas::stroke::Style::Solid(Color::from_rgba(0.95, 0.60, 0.15, 0.85)),
            width: 1.5,
            line_dash: LineDash { segments: &[6.0, 4.0], offset: 0 },
            ..Default::default()
        };
        frame.stroke(
            &Path::line(Point::new(x0, y_hi), Point::new(x1, y_hi)),
            dashed.clone(),
        );

        // ── Range Low line (orange dashed) ───────────────────────────────────
        frame.stroke(
            &Path::line(Point::new(x0, y_lo), Point::new(x1, y_lo)),
            dashed.clone(),
        );

        // ── Range Mid line (faint dotted) ────────────────────────────────────
        let mid_stroke = Stroke {
            style: canvas::stroke::Style::Solid(Color::from_rgba(0.95, 0.60, 0.15, 0.35)),
            width: 1.0,
            line_dash: LineDash { segments: &[3.0, 5.0], offset: 0 },
            ..Default::default()
        };
        frame.stroke(
            &Path::line(Point::new(x0, y_mid), Point::new(x1, y_mid)),
            mid_stroke,
        );

        // ── Sweep markers ────────────────────────────────────────────────────
        // Small upward triangle at Range Low if sweep_range_low detected
        if range.sweep_range_low {
            let cx = x1 - 16.0;
            let cy = y_lo + 5.0;
            let tri = Path::new(|b| {
                b.move_to(Point::new(cx, cy - 8.0));
                b.line_to(Point::new(cx - 5.0, cy));
                b.line_to(Point::new(cx + 5.0, cy));
                b.close();
            });
            frame.fill(&tri, Color::from_rgba(0.25, 0.90, 0.45, 0.90));
        }
        if range.sweep_range_high {
            let cx = x1 - 16.0;
            let cy = y_hi - 5.0;
            let tri = Path::new(|b| {
                b.move_to(Point::new(cx, cy + 8.0));
                b.line_to(Point::new(cx - 5.0, cy));
                b.line_to(Point::new(cx + 5.0, cy));
                b.close();
            });
            frame.fill(&tri, Color::from_rgba(0.90, 0.30, 0.30, 0.90));
        }

        // ── Labels ───────────────────────────────────────────────────────────
        let label_color = Color::from_rgba(0.95, 0.70, 0.20, 0.85);
        let small = iced::widget::canvas::Text {
            size: iced::Pixels(10.0),
            color: label_color,
            ..iced::widget::canvas::Text::default()
        };
        frame.fill_text(iced::widget::canvas::Text {
            content: format!("RH  {:.0}", range.range_high),
            position: Point::new(x0 + 4.0, y_hi - 12.0),
            ..small.clone()
        });
        frame.fill_text(iced::widget::canvas::Text {
            content: format!("RL  {:.0}", range.range_low),
            position: Point::new(x0 + 4.0, y_lo + 3.0),
            ..small.clone()
        });
        frame.fill_text(iced::widget::canvas::Text {
            content: format!("T {}/{}", range.touches_high, range.touches_low),
            position: Point::new(x0 + 60.0, y_hi - 12.0),
            ..small
        });
    }

    // ── Adaptive Big Trades overlay ──────────────────────────────────────────
    // Draws triangles above (large sell) and below (large buy) each candle.
    // Triangle size is proportional to log(vol / threshold), clamped to [4, 12] px.
    fn draw_big_trades(
        bars: &std::collections::VecDeque<BigTradeBar>,
        frame: &mut canvas::Frame,
        region: Rectangle,
        interval_to_x: impl Fn(u64) -> f32,
        price_to_y: impl Fn(f64) -> f32,
        data_source: &PlotData<KlineDataPoint>,
    ) {
        let col_buy  = iced::Color::from_rgba(0.20, 0.95, 0.35, 0.85);
        let col_sell = iced::Color::from_rgba(0.95, 0.25, 0.25, 0.85);

        for bar in bars {
            let cx = interval_to_x(bar.ts_ms as u64);
            if !cx.is_finite() { continue; }
            if cx < region.x || cx > region.x + region.width { continue; }

            let t = bar.threshold.max(1e-9);

            // ── Buy triangle (below candle low) ───────────────────────────────
            if bar.buy_vol >= t {
                // Find candle low for this bar
                let bar_y = match data_source {
                    PlotData::TimeBased(ts) => {
                        let key = exchange::UnixMs::new(bar.ts_ms as u64);
                        ts.datapoints.get(&key).map(|dp| price_to_y(dp.kline.low.to_f32() as f64))
                    }
                    PlotData::TickBased(_) => None,
                };
                if let Some(y_low) = bar_y {
                    if y_low.is_finite() {
                        let size = (((bar.buy_vol / t).ln() + 1.0) * 4.0).clamp(4.0, 12.0) as f32;
                        let y = y_low + size + 2.0;
                        // Up-pointing triangle
                        let path = iced::widget::canvas::Path::new(|b| {
                            b.move_to(iced::Point::new(cx, y - size));
                            b.line_to(iced::Point::new(cx - size * 0.6, y));
                            b.line_to(iced::Point::new(cx + size * 0.6, y));
                            b.close();
                        });
                        frame.fill(&path, col_buy);
                    }
                }
            }

            // ── Sell triangle (above candle high) ─────────────────────────────
            if bar.sell_vol >= t {
                let bar_y = match data_source {
                    PlotData::TimeBased(ts) => {
                        let key = exchange::UnixMs::new(bar.ts_ms as u64);
                        ts.datapoints.get(&key).map(|dp| price_to_y(dp.kline.high.to_f32() as f64))
                    }
                    PlotData::TickBased(_) => None,
                };
                if let Some(y_high) = bar_y {
                    if y_high.is_finite() {
                        let size = (((bar.sell_vol / t).ln() + 1.0) * 4.0).clamp(4.0, 12.0) as f32;
                        let y = y_high - size - 2.0;
                        // Down-pointing triangle
                        let path = iced::widget::canvas::Path::new(|b| {
                            b.move_to(iced::Point::new(cx, y + size));
                            b.line_to(iced::Point::new(cx - size * 0.6, y));
                            b.line_to(iced::Point::new(cx + size * 0.6, y));
                            b.close();
                        });
                        frame.fill(&path, col_sell);
                    }
                }
            }
        }
    }

    // ── Micro-window strip ───────────────────────────────────────────────────
    // One colored square per candle at the bottom of the chart, showing
    // vol_trajectory: back=orange, u_shape=purple, front=blue, mid=teal, flat=gray
    fn draw_micro_strip(
        snaps: &std::collections::VecDeque<(i64, MicroSnap)>,
        frame: &mut canvas::Frame,
        region: Rectangle,
        interval_to_x: impl Fn(u64) -> f32,
        cell_width: f32,
    ) {
        let strip_h = 5.0_f32;
        let y = region.y + region.height - strip_h - 1.0;
        let half_w = (cell_width * 0.45).max(1.0);

        for (ts_ms, snap) in snaps {
            let cx = interval_to_x(*ts_ms as u64);
            if !cx.is_finite() { continue; }
            if cx < region.x || cx > region.x + region.width { continue; }

            let color = match snap.vol_trajectory.as_str() {
                "back"    => Color::from_rgba(0.95, 0.55, 0.10, 0.80),
                "u_shape" => Color::from_rgba(0.65, 0.30, 0.95, 0.80),
                "front"   => Color::from_rgba(0.25, 0.60, 0.95, 0.80),
                "mid"     => Color::from_rgba(0.20, 0.82, 0.82, 0.80),
                _         => Color::from_rgba(0.45, 0.45, 0.50, 0.60),
            };

            // Height scales with late_surge_ratio clamped to 0.5–1.0×strip_h
            let h = (snap.late_surge_ratio.clamp(0.5, 3.0) / 3.0 * strip_h as f64) as f32;
            frame.fill_rectangle(
                Point::new(cx - half_w, y + strip_h - h),
                Size::new(half_w * 2.0, h),
                color,
            );
        }
    }

    // ── DRR HUD Overlay ──────────────────────────────────────────────────────
    // Compact info panel top-right: regime, session, range state, absorptions.
    fn draw_drr_hud(
        hud: &DrrHudState,
        frame: &mut canvas::Frame,
        region: Rectangle,
    ) {
        let x = region.x + region.width - 204.0;
        let y0 = region.y + 8.0;
        let line_h = 13.0;
        let mut row = 0usize;

        // Background — sized for up to 16 rows (A + B sections)
        frame.fill_rectangle(
            Point::new(x - 4.0, y0 - 2.0),
            Size::new(204.0, line_h * 16.0 + 6.0),
            Color::from_rgba(0.05, 0.05, 0.08, 0.75),
        );

        let col_a = Color::from_rgba(0.70, 0.70, 0.80, 0.95);  // label
        let col_v = Color::from_rgba(0.95, 0.95, 0.95, 1.00);  // value
        let col_dim = Color::from_rgba(0.45, 0.45, 0.50, 1.00); // separator / dimmed

        macro_rules! hud_row {
            ($label:expr, $value:expr, $color:expr) => {{
                let y = y0 + row as f32 * line_h;
                let text_base = iced::widget::canvas::Text {
                    size: iced::Pixels(10.5),
                    ..iced::widget::canvas::Text::default()
                };
                frame.fill_text(iced::widget::canvas::Text {
                    content: format!("{:<8}", $label),
                    position: Point::new(x, y),
                    color: col_a,
                    ..text_base.clone()
                });
                frame.fill_text(iced::widget::canvas::Text {
                    content: $value.to_string(),
                    position: Point::new(x + 62.0, y),
                    color: $color,
                    ..text_base
                });
                row += 1;
            }};
        }

        // ── Regime ────────────────────────────────────────────────────────────
        let regime_color = match hud.regime.as_str() {
            "TrendUp"              => Color::from_rgba(0.30, 0.90, 0.40, 1.0),
            "TrendDown"            => Color::from_rgba(0.90, 0.30, 0.30, 1.0),
            "Expansion"            => Color::from_rgba(0.90, 0.65, 0.10, 1.0),
            "Chop" | "Compression" => Color::from_rgba(0.70, 0.70, 0.30, 1.0),
            "Stress" | "Aftermath" => Color::from_rgba(1.0,  0.20, 0.20, 1.0),
            _                      => col_v,
        };
        hud_row!("Regime", &hud.regime, regime_color);

        hud_row!("Session", format!("{} / {}", &hud.session_name, &hud.session_phase), col_v);

        // ── VPBias — colored ──────────────────────────────────────────────────
        let vp_color = match hud.vp_bias.as_str() {
            "TrendDayUp"   => Color::from_rgba(0.30, 0.90, 0.40, 1.0),
            "TrendDayDown" => Color::from_rgba(0.90, 0.30, 0.30, 1.0),
            "InsideValue"  => Color::from_rgba(0.90, 0.80, 0.10, 1.0),
            "OpeningRange" => Color::from_rgba(0.90, 0.60, 0.10, 1.0),
            "FadeGap"      => Color::from_rgba(0.60, 0.40, 0.90, 1.0),
            _              => Color::from_rgba(0.65, 0.65, 0.70, 1.0),
        };
        hud_row!("VPBias", &hud.vp_bias, vp_color);

        // ── AuctionState — colored ────────────────────────────────────────────
        let auc_color = match hud.auction_state.as_str() {
            "Balance"              => Color::from_rgba(0.90, 0.80, 0.10, 1.0),
            "Auction" | "Initiative" => Color::from_rgba(0.30, 0.75, 0.95, 1.0),
            "FailedAuction"        => Color::from_rgba(0.90, 0.30, 0.30, 1.0),
            _                      => Color::from_rgba(0.65, 0.65, 0.70, 1.0),
        };
        hud_row!("Auction", &hud.auction_state, auc_color);

        // ── Range ─────────────────────────────────────────────────────────────
        let (rng_str, rng_color) = if hud.range_valid {
            (
                format!("{} ({:.1}×ATR)", &hud.range_location, hud.range_size_atr),
                Color::from_rgba(0.95, 0.70, 0.20, 1.0),
            )
        } else {
            ("No range".to_string(), Color::from_rgba(0.50, 0.50, 0.50, 1.0))
        };
        hud_row!("Range", rng_str, rng_color);

        if hud.range_valid {
            // Touches — separate Hi / Lo counts
            let touch_color = Color::from_rgba(0.75, 0.75, 0.85, 1.0);
            hud_row!(
                "Touches",
                format!("Hi:{} Lo:{}", hud.range_touches_hi, hud.range_touches_lo),
                touch_color
            );

            let sweep_str = match (hud.range_sweep_low, hud.range_sweep_high) {
                (true,  false) => "▲ sweep low",
                (false, true)  => "▼ sweep high",
                (true,  true)  => "▲▼ both",
                _              => "—",
            };
            hud_row!("Sweep", sweep_str, Color::from_rgba(0.30, 0.90, 0.50, 1.0));
        }

        // ── Absorption signals ────────────────────────────────────────────────
        let abs_count = hud.absorption_count;
        let abs_color = match abs_count {
            4..=5 => Color::from_rgba(0.20, 0.95, 0.30, 1.0),
            2..=3 => Color::from_rgba(0.90, 0.80, 0.10, 1.0),
            1     => Color::from_rgba(0.80, 0.50, 0.10, 1.0),
            _     => Color::from_rgba(0.45, 0.45, 0.45, 1.0),
        };
        let signals_str = format!(
            "{}/5 [{}{}{}{}{}]",
            abs_count,
            if hud.footprint_absorption { "F" } else { "." },
            if hud.big_trade_active     { "B" } else { "." },
            if hud.cvd_divergence       { "C" } else { "." },
            if hud.finish_action        { "X" } else { "." },
            if hud.sweep_present        { "S" } else { "." },
        );
        hud_row!("Absorb", signals_str, abs_color);

        // ── CVD slope ─────────────────────────────────────────────────────────
        if let Some(cvd) = hud.cvd_slope {
            let cvd_color = if cvd > 0.1 {
                Color::from_rgba(0.30, 0.90, 0.40, 1.0)
            } else if cvd < -0.1 {
                Color::from_rgba(0.90, 0.30, 0.30, 1.0)
            } else {
                col_v
            };
            hud_row!("CVD slp", format!("{:+.2}", cvd), cvd_color);
        }

        // ── VPIN ──────────────────────────────────────────────────────────────
        if let Some(vpin) = hud.vpin {
            let vpin_color = if vpin < 0.45 {
                Color::from_rgba(0.30, 0.90, 0.40, 1.0) // clean
            } else if vpin < 0.65 {
                Color::from_rgba(0.90, 0.80, 0.10, 1.0) // moderate
            } else {
                Color::from_rgba(0.90, 0.30, 0.30, 1.0) // toxic
            };
            hud_row!("VPIN", format!("{:.2}", vpin), vpin_color);
        }

        // ── Delta velocity ────────────────────────────────────────────────────
        if let Some(dv) = hud.delta_velocity {
            if dv.is_finite() && dv.abs() > 1e-6 {
                let dv_color = if dv > 0.0 {
                    Color::from_rgba(0.30, 0.90, 0.40, 1.0)
                } else {
                    Color::from_rgba(0.90, 0.30, 0.30, 1.0)
                };
                hud_row!("ΔVel", format!("{:+.2}", dv), dv_color);
            }
        }

        // ── Micro-window section ──────────────────────────────────────────────
        if !hud.micro_vol_traj.is_empty() {
            // Separator
            {
                let y = y0 + row as f32 * line_h - 2.0;
                frame.fill_rectangle(
                    Point::new(x - 2.0, y),
                    Size::new(200.0, 1.0),
                    col_dim,
                );
                row += 1;
            }

            let (traj_sym, traj_color) = match hud.micro_vol_traj.as_str() {
                "back"    => ("▶▶ back",  Color::from_rgba(0.95, 0.55, 0.10, 1.0)), // back-loaded
                "u_shape" => ("∪ u_shp",  Color::from_rgba(0.70, 0.35, 0.95, 1.0)), // absorption
                "front"   => ("◀◀ front", Color::from_rgba(0.30, 0.65, 0.95, 1.0)), // front-loaded
                "mid"     => ("~ mid",    Color::from_rgba(0.30, 0.85, 0.85, 1.0)), // mid-peak
                _         => ("— flat",   Color::from_rgba(0.55, 0.55, 0.55, 1.0)), // flat/unknown
            };

            let surge_str = if hud.micro_late_surge > 0.01 {
                format!("{}  {:.1}×", traj_sym, hud.micro_late_surge)
            } else {
                traj_sym.to_string()
            };
            hud_row!("VolTraj", surge_str, traj_color);

            // delta slope norm + absorption proxy on one row
            let dslope_color = if hud.micro_delta_slope > 0.3 {
                Color::from_rgba(0.30, 0.90, 0.40, 1.0)
            } else if hud.micro_delta_slope < -0.3 {
                Color::from_rgba(0.90, 0.30, 0.30, 1.0)
            } else {
                col_v
            };
            let absorb_str = if hud.micro_absorb > 0.01 {
                format!("δ{:+.1}  ab{:.0}", hud.micro_delta_slope, hud.micro_absorb)
            } else {
                format!("δ{:+.1}", hud.micro_delta_slope)
            };
            hud_row!("μShape", absorb_str, dslope_color);
        }
    }

    // ── Scalping Journal ──────────────────────────────────────────────────────

    fn journal_path() -> String {
        let secs = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        format!("scalping_journal_{}.jsonl", unix_to_yyyymmdd(secs))
    }

    fn journal_append(value: serde_json::Value) {
        use std::io::Write;
        let path = Self::journal_path();
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&path) {
            if let Ok(line) = serde_json::to_string(&value) {
                let _ = f.write_all(format!("{}\n", line).as_bytes());
            }
        }
    }

    fn journal_open(
        sig: &data::strategy::scalping::ScalpingSignal,
        ctx: &data::strategy::scalping::ScalpingContext,
        obi_l5: f64,
        ts_ms: i64,
    ) {
        use serde_json::json;
        Self::journal_append(json!({
            "event": "OPEN",
            "ts_ms": ts_ms,
            "strategy": sig.strategy.to_string(),
            "side": format!("{:?}", sig.side),
            "entry": sig.entry_price,
            "sl": sig.stop_price,
            "tp1": sig.tp1_price,
            "tp2": sig.tp2_price,
            "rr": sig.rr,
            "conviction": sig.conviction_score,
            "entry_type": sig.entry_type,
            "obi_l10": ctx.obi_ema_fast,
            "obi_l5": obi_l5,
            "l5_l10_div": obi_l5 - ctx.obi_ema_fast,
            "cvd": ctx.cvd,
            "cvd_slope": ctx.cvd_slope,
            "dz": ctx.dz,
            "vr": ctx.vr,
            "spread_ticks": ctx.spread_ticks,
            "regime": format!("{:?}", ctx.regime),
            "session": format!("{:?}", ctx.session),
            "atr": ctx.atr,
        }));
    }

    fn journal_close(
        paper: &data::strategy::scalping::paper::ScalpingPaper,
        obi_at_exit: f64,
        cvd_slope_at_exit: Option<f64>,
    ) {
        use serde_json::json;
        let Some(t) = paper.closed_trades.last() else { return };
        Self::journal_append(json!({
            "event": "CLOSE",
            "ts_ms": t.exit_ms,
            "trade_id": t.trade_id,
            "strategy": t.strategy,
            "side": format!("{:?}", t.side),
            "entry": t.entry_price,
            "exit": t.exit_price,
            "sl": t.stop_price,
            "tp1": t.tp1_price,
            "exit_reason": t.exit_reason.to_string(),
            "pnl_gross": t.pnl_gross,
            "pnl_net": t.pnl_net,
            "result_r": t.result_r,
            "duration_s": t.duration_ms / 1000,
            "mfe": t.mfe,
            "mae": t.mae,
            "lot_btc": t.lot_btc,
            "lot_notional": t.lot_notional,
            // Entry context
            "obi_at_entry": t.obi_at_entry,
            "obi_l5_at_entry": t.obi_l5_at_entry,
            "cvd_slope_at_entry": t.cvd_slope_at_entry,
            "dz_at_entry": t.dz_at_entry,
            "vr_at_entry": t.vr_at_entry,
            "conviction": t.conviction_score,
            // Exit context — para calibrar thresholds de salida
            "obi_at_exit": obi_at_exit,
            "cvd_slope_at_exit": cvd_slope_at_exit,
        }));
    }

    fn journal_skipped(
        sig: &data::strategy::scalping::ScalpingSignal,
        ctx: &data::strategy::scalping::ScalpingContext,
        ts_ms: i64,
        reason: &str,
    ) {
        use serde_json::json;
        Self::journal_append(json!({
            "event": "SKIPPED",
            "ts_ms": ts_ms,
            "strategy": sig.strategy.to_string(),
            "side": format!("{:?}", sig.side),
            "reason": reason,
            "conviction": sig.conviction_score,
            "obi_l10": ctx.obi_ema_fast,
            "cvd": ctx.cvd,
            "cvd_slope": ctx.cvd_slope,
            "dz": ctx.dz,
            "vr": ctx.vr,
            "session": format!("{:?}", ctx.session),
        }));
    }

    // ── Scalping Monitor Panel ────────────────────────────────────────────────
    /// Devuelve el Rectangle que ocupa el header del panel (para detección de drag).
    pub fn scalping_panel_header_rect(panel_x: f32, panel_y: f32) -> Rectangle {
        Rectangle { x: panel_x, y: panel_y, width: 310.0, height: 18.0 }
    }

    /// Devuelve el Rectangle del botón de historial para detección de click.
    fn scalping_history_button_rect(panel_x: f32, panel_y: f32, hud: &ScalpingHudState) -> Rectangle {
        let panel_w = 310.0_f32;
        let line_h  = 15.0_f32;
        let trade_rows = hud.trades.len().min(5) as f32;
        let pos_rows   = if hud.has_position { 3.0 } else { 1.0 };
        // cb + cvd_slope + ses + obi + gates(2) + pos + trades (sin separadores que no consumen row)
        let base_rows = 2.0 + 1.0 + 1.0 + 2.0 + pos_rows + trade_rows.max(1.0);
        let body_y = panel_y + 18.0;
        let btn_y = body_y + 4.0 + base_rows * line_h;
        Rectangle { x: panel_x + 4.0, y: btn_y, width: panel_w - 8.0, height: 14.0 }
    }

    fn draw_scalping_panel(
        hud: &ScalpingHudState,
        frame: &mut canvas::Frame,
        panel_x: f32,
        panel_y: f32,
    ) {
        let panel_w = 310.0_f32;
        let line_h  = 15.0_f32;

        let trade_rows = hud.trades.len().min(5) as f32;
        let pos_rows   = if hud.has_position { 3.0 } else { 1.0 };
        // rows: cb + cvd_slope + ses + obi + sep + gates(2) + sep + pos + sep + trades + btn
        let normal_rows = 2.0 + 1.0 + 1.0 + 1.0 + 2.0 + 1.0 + pos_rows + 1.0 + trade_rows.max(1.0) + 1.0;
        let hist_rows = if hud.show_history {
            let n = hud.all_trades.len() as f32;
            1.0 + n.max(1.0) // header-stats + trades
        } else {
            0.0
        };
        let panel_h = (normal_rows + hist_rows) * line_h + 14.0;

        let x  = panel_x;
        let y0 = panel_y + 18.0;

        let col_green = Color::from_rgba(0.25, 0.88, 0.50, 1.00);
        let col_red   = Color::from_rgba(0.92, 0.28, 0.28, 1.00);
        let col_amber = Color::from_rgba(0.95, 0.72, 0.20, 1.00);
        let col_dim   = Color::from_rgba(0.42, 0.44, 0.52, 1.00);
        let col_blue  = Color::from_rgba(0.40, 0.68, 1.00, 1.00);
        let col_bg    = Color::from_rgba(0.04, 0.05, 0.12, 0.92);

        // ── Header (drag area) ────────────────────────────────────────────────
        let header_bg = if hud.has_position {
            if hud.pos_side == "Long" { Color::from_rgba(0.10, 0.28, 0.14, 0.97) }
            else                      { Color::from_rgba(0.28, 0.10, 0.10, 0.97) }
        } else {
            Color::from_rgba(0.06, 0.08, 0.22, 0.97)
        };
        frame.fill_rectangle(Point::new(x, panel_y), Size::new(panel_w, 18.0), header_bg);

        let (dot_col, status_txt) = if !hud.can_trade {
            (col_red, "CIRCUIT BREAK")
        } else if hud.has_position {
            if hud.pos_side == "Long" { (col_green, "IN LONG") } else { (col_red, "IN SHORT") }
        } else if hud.session_ok {
            (col_green, "READY")
        } else {
            (col_dim, "OFF-SESSION")
        };

        let mk_txt = |content: String, px: f32, py: f32, color: Color, size: f32| canvas::Text {
            content,
            position: Point::new(px, py),
            color,
            size: iced::Pixels(size),
            ..canvas::Text::default()
        };

        frame.fill_text(mk_txt("SCALPING".into(), x + 6.0, panel_y + 3.0, col_blue, 11.5));
        frame.fill_text(mk_txt(format!("● {}", status_txt), x + 175.0, panel_y + 3.0, dot_col, 10.5));

        // ── Cuerpo ────────────────────────────────────────────────────────────
        frame.fill_rectangle(Point::new(x, y0), Size::new(panel_w, panel_h), col_bg);
        frame.stroke_rectangle(
            Point::new(x, panel_y),
            Size::new(panel_w, panel_h + 18.0),
            canvas::Stroke::default()
                .with_color(Color::from_rgba(0.28, 0.38, 0.65, 0.70))
                .with_width(1.0),
        );

        let mut row = 0usize;
        let ry = |r: usize| y0 + 4.0 + r as f32 * line_h;

        let sep = |frame: &mut canvas::Frame, y: f32| {
            frame.stroke(
                &canvas::Path::line(Point::new(x + 2.0, y), Point::new(x + panel_w - 2.0, y)),
                canvas::Stroke::default().with_color(Color::from_rgba(0.22, 0.28, 0.55, 0.50)).with_width(0.7),
            );
        };

        // ── Row 0: Capital + Circuit breaker ──────────────────────────────────
        let cb_col = if hud.can_trade { col_green } else { col_red };
        // Capital
        let cap_pct = if hud.session_start_equity > 0.0 {
            (hud.total_equity - hud.session_start_equity) / hud.session_start_equity * 100.0
        } else { 0.0 };
        let cap_col = if hud.total_equity >= hud.session_start_equity { col_green } else { col_red };
        frame.fill_text(mk_txt(format!("${:.2}", hud.total_equity), x + 6.0, ry(row), cap_col, 11.0));
        frame.fill_text(mk_txt(format!("{:+.1}%", cap_pct), x + 72.0, ry(row), cap_col, 10.0));
        // Trades / losses / P&L
        frame.fill_text(mk_txt(format!("{}/{}", hud.daily_trades, hud.max_trades), x + 118.0, ry(row), cb_col, 10.5));
        frame.fill_text(mk_txt(format!("L:{}", hud.consecutive_losses), x + 158.0, ry(row), col_dim, 10.0));
        let pnl_col = if hud.daily_pnl >= 0.0 { col_green } else { col_red };
        frame.fill_text(mk_txt(format!("P&L {:+.3}$", hud.daily_pnl), x + 192.0, ry(row), pnl_col, 10.5));
        row += 1;

        // ── Row 0b: CVD sesión + slope ─────────────────────────────────────────
        let cvd_col = if hud.cvd_session > 0.0 { col_green } else if hud.cvd_session < 0.0 { col_red } else { col_dim };
        frame.fill_text(mk_txt(format!("CVD {:+.0}", hud.cvd_session), x + 6.0, ry(row), cvd_col, 10.5));

        let (slope_sym, slope_col, slope_str) = match hud.cvd_slope {
            Some(s) if s > 50.0  => ("↗", col_green, format!("{:+.0}/bar", s)),
            Some(s) if s < -50.0 => ("↘", col_red,   format!("{:+.0}/bar", s)),
            Some(s)              => ("→", col_amber,  format!("{:+.0}/bar", s)),
            None                 => ("?", col_dim,    "slope n/a".into()),
        };
        frame.fill_text(mk_txt(slope_sym.into(), x + 90.0, ry(row), slope_col, 11.5));
        frame.fill_text(mk_txt(slope_str, x + 104.0, ry(row), slope_col, 10.0));

        // Divergencia CVD vs L5: ^L5 + CVD cayendo = absorción real
        let l5_div = hud.obi_l5_norm - hud.obi_fast;
        if l5_div.abs() > 0.06 {
            let div_desc = if l5_div > 0.0 && hud.cvd_slope.unwrap_or(0.0) < -30.0 {
                ("ABSORB?", col_amber) // presión compra L5 pero CVD cae → absorción
            } else if l5_div < 0.0 && hud.cvd_slope.unwrap_or(0.0) > 30.0 {
                ("ABSORB?", col_amber) // presión venta L5 pero CVD sube → absorción
            } else {
                ("SPOOF?", col_dim)    // divergencia sin confirmación de delta
            };
            frame.fill_text(mk_txt(div_desc.0.into(), x + 220.0, ry(row), div_desc.1, 9.5));
        }
        row += 1;

        // ── Row 1: Session + Bar timer ─────────────────────────────────────────
        let ses_col = if hud.session_ok { col_green } else { col_dim };
        let ses_label = {
            let s = hud.session.as_str();
            if s == "OutOfSession" { "off" } else { s }
        };
        frame.fill_text(mk_txt(format!("SES {}", ses_label), x + 6.0, ry(row), ses_col, 10.5));

        // Bar timer progress bar
        let elapsed = hud.last_bar_close_instant
            .map(|t| t.elapsed().as_secs_f32())
            .unwrap_or(0.0);
        let period = hud.bar_period_secs.max(1) as f32;
        let pct = (elapsed / period).clamp(0.0, 1.0);
        let remaining = (period - elapsed).max(0.0) as u32;
        let bar_x = x + 100.0;
        let bar_w = 120.0_f32;
        frame.fill_rectangle(Point::new(bar_x, ry(row) + 3.0), Size::new(bar_w, 8.0),
            Color::from_rgba(0.15, 0.16, 0.22, 1.0));
        let fill_col = if pct < 0.6 { col_green } else if pct < 0.85 { col_amber } else { col_red };
        frame.fill_rectangle(Point::new(bar_x, ry(row) + 3.0), Size::new(bar_w * pct, 8.0), fill_col);
        frame.fill_text(mk_txt(format!("{}s", remaining), x + 226.0, ry(row), col_dim, 10.5));
        frame.fill_text(mk_txt(format!("{}tk", hud.spread_ticks), x + 264.0, ry(row), col_dim, 10.5));
        row += 1;

        // ── Row 2: OBI bar L10 + L5 divergencia ──────────────────────────────
        // obi_fast ∈ [0,1]: 0.5=neutral, >0.5=bids, <0.5=asks
        let obi_bar_w = 80.0_f32;
        let obi_pct = (hud.obi_fast.clamp(0.0, 1.0) as f32) * obi_bar_w;
        let obi_col = if hud.obi_fast > 0.54 { col_green }
                      else if hud.obi_fast < 0.46 { col_red }
                      else { col_amber };
        frame.fill_text(mk_txt("L10".into(), x + 6.0, ry(row), col_dim, 9.5));
        frame.fill_rectangle(Point::new(x + 28.0, ry(row) + 3.0), Size::new(obi_bar_w, 8.0),
            Color::from_rgba(0.15, 0.16, 0.20, 1.0));
        // Línea central (0.5 = neutral)
        frame.fill_rectangle(Point::new(x + 28.0 + obi_bar_w * 0.5 - 0.5, ry(row) + 3.0),
            Size::new(1.0, 8.0), Color::from_rgba(0.35, 0.38, 0.50, 0.80));
        frame.fill_rectangle(Point::new(x + 28.0, ry(row) + 3.0), Size::new(obi_pct, 8.0), obi_col);
        frame.fill_text(mk_txt(format!("{:.3}", hud.obi_fast), x + 114.0, ry(row), obi_col, 10.5));

        // Divergencia L5 vs L10
        let div = hud.obi_l5_norm - hud.obi_fast;
        let div_col = if div.abs() > 0.06 { col_amber } else { col_dim };
        let div_sym = if div > 0.06 { "^L5" } else if div < -0.06 { "vL5" } else { "=L5" };
        frame.fill_text(mk_txt(format!("{} {:.3}", div_sym, hud.obi_l5_norm), x + 170.0, ry(row), div_col, 9.5));
        row += 1;

        // ── Separator + GATES section ─────────────────────────────────────────
        sep(frame, ry(row) - 2.0);

        // Gate row 1: session | S1 OBI | S1 spread | S1 VR
        let gok  = col_green;
        let gfail = col_red;
        let gd   = col_dim;

        let gate_dot = |ok: bool| if ok { "✓" } else { "✗" };
        let gate_col = |ok: bool| if ok { gok } else { gfail };

        // S1 gates
        frame.fill_text(mk_txt("S1".into(), x + 6.0, ry(row), col_blue, 10.0));
        frame.fill_text(mk_txt(format!("OBI{}", gate_dot(hud.s1_obi_ok)), x + 22.0, ry(row), gate_col(hud.s1_obi_ok), 10.0));
        frame.fill_text(mk_txt(format!("SPD{}", gate_dot(hud.s1_spread_ok)), x + 74.0, ry(row), gate_col(hud.s1_spread_ok), 10.0));
        frame.fill_text(mk_txt(format!("VR{}", gate_dot(hud.s1_vr_ok)), x + 126.0, ry(row), gate_col(hud.s1_vr_ok), 10.0));
        frame.fill_text(mk_txt(format!("SES{}", gate_dot(hud.session_ok)), x + 166.0, ry(row), gate_col(hud.session_ok), 10.0));
        row += 1;

        // S2 gates
        frame.fill_text(mk_txt("S2".into(), x + 6.0, ry(row), col_blue, 10.0));
        frame.fill_text(mk_txt(format!("RNG{}", gate_dot(hud.s2_regime_ok)), x + 22.0, ry(row), gate_col(hud.s2_regime_ok), 10.0));
        frame.fill_text(mk_txt(format!("DZ {:.1}{}", hud.s2_dz, gate_dot(hud.s2_dz_ok)), x + 74.0, ry(row), gate_col(hud.s2_dz_ok), 10.0));
        frame.fill_text(mk_txt(format!("VR {:.1}{}", hud.s2_vr, gate_dot(hud.s2_vr_ok)), x + 148.0, ry(row), gate_col(hud.s2_vr_ok), 10.0));
        let _ = (gd, gok, gfail);
        row += 1;

        // ── Separator + Posición activa ───────────────────────────────────────
        sep(frame, ry(row) - 2.0);

        if hud.has_position {
            let side_col = if hud.pos_side == "Long" { col_green } else { col_red };
            frame.fill_text(mk_txt(
                format!("{} {}  @ {:.1}", hud.pos_strategy, hud.pos_side.to_uppercase(), hud.pos_entry),
                x + 6.0, ry(row), side_col, 11.5,
            ));
            row += 1;

            let sl_label = if hud.pos_sl_at_be { "BE".to_string() } else { format!("SL {:.1}", hud.pos_stop) };
            let sl_col = if hud.pos_sl_at_be { col_amber } else { col_red };
            frame.fill_text(mk_txt(sl_label, x + 6.0, ry(row), sl_col, 11.0));
            frame.fill_text(mk_txt(format!("TP1 {:.1}", hud.pos_tp1), x + 102.0, ry(row), col_green, 11.0));
            frame.fill_text(mk_txt(format!("TP2 {:.1}", hud.pos_tp2), x + 196.0, ry(row), col_dim, 11.0));
            row += 1;

            let float_pnl = if hud.pos_side == "Long" {
                (hud.pos_current_price - hud.pos_entry) * hud.pos_lot_btc
            } else {
                (hud.pos_entry - hud.pos_current_price) * hud.pos_lot_btc
            };
            let float_col = if float_pnl >= 0.0 { col_green } else { col_red };
            frame.fill_text(mk_txt(
                format!("Float {:+.4}$   now {:.1}", float_pnl, hud.pos_current_price),
                x + 6.0, ry(row), float_col, 10.5,
            ));
            frame.fill_text(mk_txt(
                format!("{:.5} BTC  ${:.1} notional", hud.pos_lot_btc, hud.pos_lot_notional),
                x + 174.0, ry(row), col_dim, 9.5,
            ));
            row += 1;
        } else {
            frame.fill_text(mk_txt("── sin posición activa ──".into(), x + 6.0, ry(row), col_dim, 10.5));
            row += 1;
        }

        sep(frame, ry(row) - 2.0);

        // ── Trades recientes (compact) ────────────────────────────────────────
        if hud.trades.is_empty() {
            frame.fill_text(mk_txt("sin trades todavía".into(), x + 6.0, ry(row), col_dim, 10.5));
            row += 1;
        } else {
            for t in &hud.trades {
                let r_col = if t.won { col_green } else { col_red };
                let side_s = if t.side == "Long" { "L" } else { "S" };
                let strat_s = &t.strategy[..t.strategy.len().min(7)];
                let exit_s  = &t.exit_reason[..t.exit_reason.len().min(8)];
                frame.fill_text(mk_txt(
                    format!("{} {} {:+.2}R  {}  {}s",
                        strat_s, side_s, t.result_r, exit_s, t.duration_secs),
                    x + 6.0, ry(row), r_col, 10.5,
                ));
                row += 1;
            }
        }

        // ── History toggle button ─────────────────────────────────────────────
        let btn_label = if hud.show_history {
            format!("[ cerrar historial ]")
        } else {
            format!("[ ver historial ({} trades) ]", hud.all_trades.len())
        };
        let btn_bg = Color::from_rgba(0.12, 0.16, 0.30, 0.90);
        frame.fill_rectangle(Point::new(x + 4.0, ry(row) + 1.0), Size::new(panel_w - 8.0, 13.0), btn_bg);
        frame.fill_text(mk_txt(btn_label, x + 10.0, ry(row) + 1.0, col_blue, 10.0));
        row += 1;

        // ── History expanded view ─────────────────────────────────────────────
        if hud.show_history {
            sep(frame, ry(row) - 2.0);

            // Summary stats
            let n = hud.all_trades.len();
            if n == 0 {
                frame.fill_text(mk_txt("sin historial".into(), x + 6.0, ry(row), col_dim, 10.5));
                row += 1;
            } else {
                let wins = hud.all_trades.iter().filter(|t| t.won).count();
                let win_pct = wins * 100 / n;
                let avg_r: f64 = hud.all_trades.iter().map(|t| t.result_r).sum::<f64>() / n as f64;
                frame.fill_text(mk_txt(
                    format!("HISTORIAL  {}t  WR {}%  avgR {:+.2}", n, win_pct, avg_r),
                    x + 6.0, ry(row), col_blue, 10.0,
                ));
                row += 1;

                for t in &hud.all_trades {
                    let r_col = if t.won { col_green } else { col_red };
                    let side_s = if t.side == "Long" { "L" } else { "S" };
                    let strat_s = &t.strategy[..t.strategy.len().min(7)];
                    let exit_s  = &t.exit_reason[..t.exit_reason.len().min(9)];
                    frame.fill_text(mk_txt(
                        format!("{} {} {:+.2}R  {}  {}s",
                            strat_s, side_s, t.result_r, exit_s, t.duration_secs),
                        x + 6.0, ry(row), r_col, 10.0,
                    ));
                    row += 1;
                }
            }
        }
        let _ = row;
    }

    // ── Scalping Signal Overlay (Entry/SL/BE/TP1/TP2 en el chart) ─────────────
    fn draw_scalping_overlay(
        hud: &ScalpingHudState,
        frame: &mut canvas::Frame,
        price_to_y: impl Fn(f64) -> f32,
        region: Rectangle,
    ) {
        if !hud.has_position {
            return;
        }

        let is_long = hud.pos_side == "Long";
        let line_w = region.x + region.width;

        let entry_col = if is_long {
            Color::from_rgba(0.25, 0.88, 0.50, 0.90)
        } else {
            Color::from_rgba(0.92, 0.28, 0.28, 0.90)
        };
        let sl_col    = Color::from_rgba(0.90, 0.20, 0.20, 0.75);
        let be_col    = Color::from_rgba(0.95, 0.75, 0.20, 0.80);
        let tp1_col   = Color::from_rgba(0.25, 0.88, 0.50, 0.70);
        let tp2_col   = Color::from_rgba(0.20, 0.70, 0.40, 0.45);

        let entry_y = price_to_y(hud.pos_entry);
        let sl_y    = price_to_y(if hud.pos_sl_at_be { hud.pos_entry } else { hud.pos_stop });
        let tp1_y   = price_to_y(hud.pos_tp1);
        let tp2_y   = price_to_y(hud.pos_tp2);

        let dash = LineDash { segments: &[5.0, 3.5], offset: 0 };

        // Zonas coloreadas
        let tp_top = f32::min(entry_y, tp1_y);
        let tp_h   = (entry_y - tp1_y).abs();
        frame.fill_rectangle(Point::new(0.0, tp_top), Size::new(line_w, tp_h),
            Color::from_rgba(0.20, 0.80, 0.40, 0.12));
        let sl_top = f32::min(entry_y, sl_y);
        let sl_h   = (entry_y - sl_y).abs();
        frame.fill_rectangle(Point::new(0.0, sl_top), Size::new(line_w, sl_h),
            Color::from_rgba(0.90, 0.25, 0.28, 0.12));

        // Línea Entry (sólida, más gruesa)
        if entry_y.is_finite() {
            frame.stroke(&canvas::Path::line(Point::new(0.0, entry_y), Point::new(line_w, entry_y)),
                canvas::Stroke::default().with_color(entry_col).with_width(1.8));
            // Label Entry
            frame.fill_text(canvas::Text {
                content: format!("{} {:.1}", if is_long { "LONG" } else { "SHORT" }, hud.pos_entry),
                position: Point::new(region.x + 6.0, entry_y - 12.0),
                color: entry_col,
                size: iced::Pixels(10.5),
                ..canvas::Text::default()
            });
        }

        // SL o BE
        if sl_y.is_finite() {
            let (sl_col_use, sl_label) = if hud.pos_sl_at_be {
                (be_col, format!("BE {:.1}", hud.pos_entry))
            } else {
                (sl_col, format!("SL {:.1}", hud.pos_stop))
            };
            frame.stroke(&canvas::Path::line(Point::new(0.0, sl_y), Point::new(line_w, sl_y)),
                canvas::Stroke { width: 1.2, line_dash: dash, ..Default::default() }.with_color(sl_col_use));
            frame.fill_text(canvas::Text {
                content: sl_label,
                position: Point::new(region.x + 6.0, sl_y + 2.0),
                color: sl_col_use,
                size: iced::Pixels(10.0),
                ..canvas::Text::default()
            });
        }

        // TP1
        if tp1_y.is_finite() {
            frame.stroke(&canvas::Path::line(Point::new(0.0, tp1_y), Point::new(line_w, tp1_y)),
                canvas::Stroke { width: 1.2, line_dash: dash, ..Default::default() }.with_color(tp1_col));
            frame.fill_text(canvas::Text {
                content: format!("TP1 {:.1}", hud.pos_tp1),
                position: Point::new(region.x + 6.0, tp1_y - 11.0),
                color: tp1_col,
                size: iced::Pixels(10.0),
                ..canvas::Text::default()
            });
        }

        // TP2 (más tenue)
        if tp2_y.is_finite() && (hud.pos_tp2 - hud.pos_tp1).abs() > 1.0 {
            frame.stroke(&canvas::Path::line(Point::new(0.0, tp2_y), Point::new(line_w, tp2_y)),
                canvas::Stroke { width: 0.9, line_dash: dash, ..Default::default() }.with_color(tp2_col));
            frame.fill_text(canvas::Text {
                content: format!("TP2 {:.1}", hud.pos_tp2),
                position: Point::new(region.x + 6.0, tp2_y - 11.0),
                color: tp2_col,
                size: iced::Pixels(10.0),
                ..canvas::Text::default()
            });
        }
    }

    fn draw_rbf_overlay(
        sig: &data::strategy::detectors::range_breakout_flow::RbfSignal,
        frame: &mut canvas::Frame,
        price_to_y: impl Fn(f64) -> f32,
        region: Rectangle,
    ) {
        use data::strategy::detectors::range_breakout_flow::RbfDirection;

        let is_short = matches!(sig.direction, RbfDirection::Short);
        let line_w   = region.x + region.width;
        let dash     = LineDash { segments: &[6.0, 4.0], offset: 0 };

        // Colores — naranja/ámbar para distinguir de scalping (verde/rojo)
        let entry_col  = Color::from_rgba(0.95, 0.65, 0.10, 0.95); // ámbar
        let stop_col   = Color::from_rgba(0.90, 0.25, 0.25, 0.80);
        let target_col = Color::from_rgba(0.25, 0.85, 0.55, 0.80);
        let range_col  = Color::from_rgba(0.95, 0.65, 0.10, 0.07); // relleno rango tenue

        // Zona del rango de consolidación previo (contexto visual)
        let rh_y = price_to_y(sig.range_high);
        let rl_y = price_to_y(sig.range_low);
        if rh_y.is_finite() && rl_y.is_finite() {
            let top = f32::min(rh_y, rl_y);
            let h   = (rh_y - rl_y).abs();
            frame.fill_rectangle(Point::new(0.0, top), Size::new(line_w, h), range_col);
            // Borde superior e inferior del rango
            frame.stroke(
                &canvas::Path::line(Point::new(0.0, rh_y), Point::new(line_w, rh_y)),
                canvas::Stroke { width: 0.8, line_dash: dash, ..Default::default() }
                    .with_color(Color::from_rgba(0.95, 0.65, 0.10, 0.40)),
            );
            frame.stroke(
                &canvas::Path::line(Point::new(0.0, rl_y), Point::new(line_w, rl_y)),
                canvas::Stroke { width: 0.8, line_dash: dash, ..Default::default() }
                    .with_color(Color::from_rgba(0.95, 0.65, 0.10, 0.40)),
            );
        }

        let entry_y  = price_to_y(sig.entry_price);
        let stop_y   = price_to_y(sig.stop_price);
        let target_y = price_to_y(sig.target_price);

        // Zona profit (entry → target)
        if entry_y.is_finite() && target_y.is_finite() {
            let top = f32::min(entry_y, target_y);
            let h   = (entry_y - target_y).abs();
            frame.fill_rectangle(
                Point::new(0.0, top), Size::new(line_w, h),
                Color::from_rgba(0.20, 0.80, 0.40, 0.10),
            );
        }
        // Zona riesgo (entry → stop)
        if entry_y.is_finite() && stop_y.is_finite() {
            let top = f32::min(entry_y, stop_y);
            let h   = (entry_y - stop_y).abs();
            frame.fill_rectangle(
                Point::new(0.0, top), Size::new(line_w, h),
                Color::from_rgba(0.90, 0.25, 0.25, 0.10),
            );
        }

        // Línea Entry
        if entry_y.is_finite() {
            frame.stroke(
                &canvas::Path::line(Point::new(0.0, entry_y), Point::new(line_w, entry_y)),
                canvas::Stroke::default().with_color(entry_col).with_width(1.8),
            );
            frame.fill_text(canvas::Text {
                content: format!("RBF {} {:.1}  rng{:.2}% vr{:.1}x",
                    if is_short { "SHORT" } else { "LONG" },
                    sig.entry_price, sig.range_pct, sig.vr_at_breakout),
                position: Point::new(region.x + 6.0, entry_y - 13.0),
                color: entry_col,
                size: iced::Pixels(10.5),
                ..canvas::Text::default()
            });
        }

        // Stop
        if stop_y.is_finite() {
            frame.stroke(
                &canvas::Path::line(Point::new(0.0, stop_y), Point::new(line_w, stop_y)),
                canvas::Stroke { width: 1.2, line_dash: dash, ..Default::default() }
                    .with_color(stop_col),
            );
            frame.fill_text(canvas::Text {
                content: format!("SL {:.1}", sig.stop_price),
                position: Point::new(region.x + 6.0, stop_y + 2.0),
                color: stop_col,
                size: iced::Pixels(10.0),
                ..canvas::Text::default()
            });
        }

        // Target
        if target_y.is_finite() {
            frame.stroke(
                &canvas::Path::line(Point::new(0.0, target_y), Point::new(line_w, target_y)),
                canvas::Stroke { width: 1.2, line_dash: dash, ..Default::default() }
                    .with_color(target_col),
            );
            frame.fill_text(canvas::Text {
                content: format!("TP {:.1}  ({:.1}R)", sig.target_price, sig.rr),
                position: Point::new(region.x + 6.0, target_y - 11.0),
                color: target_col,
                size: iced::Pixels(10.0),
                ..canvas::Text::default()
            });
        }
    }

    fn draw_strategy_overlay(
        signals: &[StrategySignal],
        frame: &mut canvas::Frame,
        price_to_y: impl Fn(f64) -> f32,
        region: Rectangle,
    ) {
        for signal in signals {
            if signal.action != StrategyAction::ShadowSignal {
                continue;
            }

            let (entry, stop, target) =
                match (signal.entry_price, signal.stop_price, signal.target_price) {
                    (Some(e), Some(s), Some(t)) => (e, s, t),
                    _ => continue,
                };

            let is_long = signal.side == Some(Side::Long);

            let entry_color = if is_long {
                Color::from_rgba(0.2, 0.8, 0.4, 0.8)
            } else {
                Color::from_rgba(0.9, 0.3, 0.3, 0.8)
            };
            let stop_color = Color::from_rgba(0.9, 0.2, 0.2, 0.6);
            let target_color = Color::from_rgba(0.2, 0.8, 0.4, 0.6);

            let entry_y = price_to_y(entry);
            let stop_y = price_to_y(stop);
            let target_y = price_to_y(target);

            if !entry_y.is_finite() || !stop_y.is_finite() || !target_y.is_finite() {
                continue;
            }

            let line_width = region.x + region.width;

            // Entry line (solid)
            let entry_stroke = Stroke::with_color(
                Stroke {
                    width: 1.5,
                    ..Default::default()
                },
                entry_color,
            );
            frame.stroke(
                &Path::line(Point::new(0.0, entry_y), Point::new(line_width, entry_y)),
                entry_stroke,
            );

            // Stop line (dashed)
            let stop_stroke = Stroke::with_color(
                Stroke {
                    width: 1.0,
                    line_dash: LineDash {
                        segments: &[4.0, 3.0],
                        offset: 0,
                    },
                    ..Default::default()
                },
                stop_color,
            );
            frame.stroke(
                &Path::line(Point::new(0.0, stop_y), Point::new(line_width, stop_y)),
                stop_stroke,
            );

            // Target line (dashed)
            let target_stroke = Stroke::with_color(
                Stroke {
                    width: 1.0,
                    line_dash: LineDash {
                        segments: &[4.0, 3.0],
                        offset: 0,
                    },
                    ..Default::default()
                },
                target_color,
            );
            frame.stroke(
                &Path::line(Point::new(0.0, target_y), Point::new(line_width, target_y)),
                target_stroke,
            );

            // Cajas estilo TradingView: verde para zona TP (entry↔target),
            // rojo para zona SL (entry↔stop). Alpha lo bastante alto para
            // verse sobre las velas pero no taparlas.
            let tp_top = f32::min(entry_y, target_y);
            let tp_height = (entry_y - target_y).abs();
            let tp_color = Color::from_rgba(0.20, 0.80, 0.45, 0.22);
            frame.fill_rectangle(
                Point::new(0.0, tp_top),
                Size::new(line_width, tp_height),
                tp_color,
            );

            let sl_top = f32::min(entry_y, stop_y);
            let sl_height = (entry_y - stop_y).abs();
            let sl_color = Color::from_rgba(0.90, 0.25, 0.30, 0.22);
            frame.fill_rectangle(
                Point::new(0.0, sl_top),
                Size::new(line_width, sl_height),
                sl_color,
            );

            // Etiqueta lateral con la estrategia y el R:R alcanzable
            let risk = (entry - stop).abs();
            let reward = (target - entry).abs();
            let rr = if risk > 0.0 { reward / risk } else { 0.0 };
            let strat_tag = signal
                .strategy_id
                .map(|s| format!("{s:?}"))
                .unwrap_or_default();
            let label = format!(
                "{} {} · RR {:.2}",
                if is_long { "LONG" } else { "SHORT" },
                strat_tag,
                rr
            );
            frame.fill_text(canvas::Text {
                content: label,
                position: Point::new(region.x + 6.0, entry_y - 2.0),
                size: iced::Pixels(TEXT_SIZE * 0.75),
                color: entry_color,
                align_x: iced::alignment::Horizontal::Left.into(),
                align_y: iced::alignment::Vertical::Bottom,
                font: style::AZERET_MONO,
                ..canvas::Text::default()
            });
        }
    }
}

impl canvas::Program<Message> for KlineChart {
    type State = Interaction;

    fn update(
        &self,
        interaction: &mut Interaction,
        event: &Event,
        bounds: Rectangle,
        cursor: mouse::Cursor,
    ) -> Option<canvas::Action<Message>> {
        // ── Scalping panel drag ───────────────────────────────────────────────
        if self.config.show_scalping_panel {
            let header = Self::scalping_panel_header_rect(self.scalping_panel_x, self.scalping_panel_y);
            // Adjust header to bounds-space (header coords are relative to top-left of bounds)
            let abs_header = Rectangle {
                x: bounds.x + header.x,
                y: bounds.y + header.y,
                width: header.width,
                height: header.height,
            };

            match event {
                Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Left)) => {
                    if let Some(pos) = cursor.position_in(bounds) {
                        if header.contains(pos) {
                            *interaction = Interaction::DraggingScalpingPanel {
                                start: pos,
                                base_x: self.scalping_panel_x,
                                base_y: self.scalping_panel_y,
                            };
                            return Some(canvas::Action::capture());
                        }
                    }
                }
                Event::Mouse(mouse::Event::CursorMoved { .. }) => {
                    if let Interaction::DraggingScalpingPanel { start, base_x, base_y } = *interaction {
                        if let Some(pos) = cursor.position_in(bounds) {
                            let dx = pos.x - start.x;
                            let dy = pos.y - start.y;
                            let new_x = (base_x + dx).max(0.0).min(bounds.width - 310.0);
                            let new_y = (base_y + dy).max(0.0).min(bounds.height - 50.0);
                            return Some(canvas::Action::publish(Message::ScalpingPanelMoved(new_x, new_y)));
                        }
                    }
                }
                Event::Mouse(mouse::Event::ButtonReleased(mouse::Button::Left)) => {
                    if matches!(interaction, Interaction::DraggingScalpingPanel { .. }) {
                        *interaction = Interaction::None;
                        return Some(canvas::Action::capture());
                    }
                    // History button click
                    if let Some(pos) = cursor.position_in(bounds) {
                        let btn = Self::scalping_history_button_rect(
                            self.scalping_panel_x,
                            self.scalping_panel_y,
                            &self.scalping_hud,
                        );
                        if btn.contains(pos) {
                            return Some(canvas::Action::publish(Message::ToggleScalpingHistory));
                        }
                    }
                }
                _ => {}
            }
            let _ = abs_header;
        }

        super::canvas_interaction(self, interaction, event, bounds, cursor)
    }

    fn draw(
        &self,
        interaction: &Interaction,
        renderer: &Renderer,
        theme: &Theme,
        bounds: Rectangle,
        cursor: mouse::Cursor,
    ) -> Vec<Geometry> {
        let chart = self.state();

        if chart.bounds.width == 0.0 {
            return vec![];
        }

        let bounds_size = bounds.size();
        let palette = theme.extended_palette();

        let klines = chart.cache.main.draw(renderer, bounds_size, |frame| {
            let center = Vector::new(bounds.width / 2.0, bounds.height / 2.0);

            frame.translate(center);
            frame.scale(chart.scaling);
            frame.translate(chart.translation);

            let region = chart.visible_region(frame.size());
            let (earliest, latest) = chart.interval_range(&region);

            let price_to_y = |price| chart.price_to_y(price);
            let interval_to_x = |interval| chart.interval_to_x(interval);

            match &self.kind {
                KlineChartKind::Footprint {
                    clusters,
                    scaling,
                    studies,
                } => {
                    let (highest, lowest) = chart.price_range(&region);

                    let max_cluster_qty = self.calc_qty_scales(
                        earliest,
                        latest,
                        highest,
                        lowest,
                        chart.tick_size,
                        *clusters,
                    );

                    let cell_height_unscaled = chart.cell_height * chart.scaling;
                    let cell_width_unscaled = chart.cell_width * chart.scaling;

                    let text_size =
                        footprint_cluster_text_size(cell_height_unscaled, cell_width_unscaled);

                    let candle_width = 0.1 * chart.cell_width;
                    let content_spacing = ContentGaps::from_view(candle_width, chart.scaling);

                    let imbalance = studies.iter().find_map(|study| {
                        if let FootprintStudy::Imbalance {
                            threshold,
                            color_scale,
                            ignore_zeros,
                        } = study
                        {
                            Some((*threshold, *color_scale, *ignore_zeros))
                        } else {
                            None
                        }
                    });

                    let show_text = should_show_text(
                        cell_height_unscaled,
                        cell_width_unscaled,
                        footprint_cluster_min_width(*clusters),
                    );

                    draw_all_npocs(
                        &self.data_source,
                        frame,
                        price_to_y,
                        interval_to_x,
                        candle_width,
                        chart.cell_width,
                        chart.cell_height,
                        palette,
                        studies,
                        earliest,
                        latest,
                        *clusters,
                        content_spacing,
                        imbalance.is_some(),
                    );

                    render_data_source(
                        &self.data_source,
                        frame,
                        earliest,
                        latest,
                        interval_to_x,
                        |frame, x_position, kline, trades| {
                            let cluster_scaling =
                                effective_cluster_qty(*scaling, max_cluster_qty, trades, *clusters);

                            draw_clusters(
                                frame,
                                price_to_y,
                                x_position,
                                chart.cell_width,
                                chart.cell_height,
                                candle_width,
                                cluster_scaling,
                                palette,
                                text_size,
                                self.tick_size(),
                                show_text,
                                imbalance,
                                kline,
                                trades,
                                *clusters,
                                content_spacing,
                            );
                        },
                    );
                }
                KlineChartKind::Candles => {
                    let candle_width = chart.cell_width * 0.8;

                    render_data_source(
                        &self.data_source,
                        frame,
                        earliest,
                        latest,
                        interval_to_x,
                        |frame, x_position, kline, _| {
                            draw_candle_dp(
                                frame,
                                price_to_y,
                                candle_width,
                                palette,
                                x_position,
                                kline,
                            );
                        },
                    );
                }
            }

            chart.draw_last_price_line(frame, palette, region);

            if let (PlotData::TimeBased(ts), KlineChartKind::Candles) =
                (&self.data_source, &self.kind)
            {
                if self.config.show_session_lines {
                    draw_session_lines(
                        frame,
                        &ts.datapoints,
                        earliest,
                        latest,
                        ts.interval.to_milliseconds(),
                        interval_to_x,
                        price_to_y,
                    );
                }
                if self.config.show_key_levels {
                    draw_key_levels(frame, &region, &ts.datapoints, &price_to_y);
                }
            }

            self.draw_indicator_overlays(
                frame,
                &region,
                earliest,
                latest,
                interval_to_x,
                price_to_y,
            );

            if matches!(self.kind, KlineChartKind::Candles) {
                // liq_map removed — real liquidation events rendered via draw_liq_strip()

                if self.config.show_fvgs {
                    self.draw_fvgs(frame, &region, earliest, interval_to_x, price_to_y);
                }

                if self.config.show_structure {
                    self.draw_structure(
                        frame,
                        &region,
                        earliest,
                        latest,
                        interval_to_x,
                        price_to_y,
                    );
                }

                if self.config.show_order_blocks {
                    self.draw_order_blocks(frame, &region, earliest, interval_to_x, price_to_y);
                }

                // DRR range overlay — always shown when a valid range is detected
                if let Some(ref rng) = self.range_context {
                    if rng.valid {
                        Self::draw_drr_range(
                            rng,
                            frame,
                            region,
                            |price| chart.price_to_y(Price::from_f32(price as f32)),
                        );
                    }
                }
            }

            // Adaptive Big Trades — triangles above/below candles
            if self.strategy_overlay_enabled && matches!(self.kind, KlineChartKind::Candles) {
                Self::draw_big_trades(
                    &self.big_trade_bars,
                    frame,
                    region,
                    interval_to_x,
                    |price| chart.price_to_y(Price::from_f32(price as f32)),
                    &self.data_source,
                );
            }

            // Liquidation event strip — per-candle long/short liq volume
            if self.config.show_liq_events && matches!(self.kind, KlineChartKind::Candles) {
                let interval_ms = match &self.data_source {
                    PlotData::TimeBased(ts) => ts.interval.to_milliseconds(),
                    PlotData::TickBased(_) => 300_000,
                };
                self.draw_liq_strip(frame, &region, interval_to_x, chart.cell_width, interval_ms);
            }

            // Micro-window strip — one colored square per candle at bottom of chart
            if self.strategy_overlay_enabled && matches!(self.kind, KlineChartKind::Candles) {
                Self::draw_micro_strip(
                    &self.micro_snaps,
                    frame,
                    region,
                    interval_to_x,
                    chart.cell_width,
                );
            }

            // DRR HUD — shown when strategy overlay is active
            if self.strategy_overlay_enabled && matches!(self.kind, KlineChartKind::Candles) {
                Self::draw_drr_hud(&self.drr_hud, frame, region);
            }

            // Scalping Monitor Panel
            if self.config.show_scalping_panel && matches!(self.kind, KlineChartKind::Candles) {
                Self::draw_scalping_panel(&self.scalping_hud, frame, self.scalping_panel_x, self.scalping_panel_y);
                Self::draw_scalping_overlay(
                    &self.scalping_hud,
                    frame,
                    |price| chart.price_to_y(exchange::unit::Price::from_f32(price as f32)),
                    region,
                );
            }

            // RangeBreakoutFlow overlay — muestra la señal activa mientras no toque SL/TP
            if self.strategy_overlay_enabled
                && matches!(self.kind, KlineChartKind::Candles)
            {
                if let Some(ref sig) = self.rbf_active_signal {
                    Self::draw_rbf_overlay(
                        sig,
                        frame,
                        |price| chart.price_to_y(exchange::unit::Price::from_f32(price as f32)),
                        region,
                    );
                }
            }

            // Overlay de estrategia solo en Candlestick (mismo criterio que
            // run_strategy_detection y ensure_strategy_indicators).
            if self.strategy_overlay_enabled
                && matches!(self.kind, KlineChartKind::Candles)
            {
                Self::draw_strategy_overlay(
                    &self.strategy_signals,
                    frame,
                    |price| chart.price_to_y(Price::from_f32(price as f32)),
                    region,
                );
            }
        });

        let crosshair = chart.cache.crosshair.draw(renderer, bounds_size, |frame| {
            if let Some(cursor_position) = cursor.position_in(bounds) {
                let (_, rounded_aggregation) =
                    chart.draw_crosshair(frame, theme, bounds_size, cursor_position, interaction);

                draw_crosshair_tooltip(
                    &self.data_source,
                    &chart.ticker_info,
                    frame,
                    palette,
                    rounded_aggregation,
                );

                if self.config.show_key_levels
                    && matches!(self.kind, KlineChartKind::Candles)
                    && let PlotData::TimeBased(ts) = &self.data_source
                {
                    // price_to_y returns chart-space Y (origin = center, scaled+translated).
                    // The crosshair frame has no transform applied, so we convert to
                    // screen-space: screen_y = height/2 + (chart_y * scaling) + (translation.y * scaling)
                    let h2 = bounds.height / 2.0;
                    let sc = chart.scaling;
                    let ty = chart.translation.y;
                    draw_key_level_tooltip(
                        frame,
                        palette,
                        &ts.datapoints,
                        cursor_position,
                        bounds,
                        |price| h2 + (chart.price_to_y(price) + ty) * sc,
                    );
                }
            }
        });

        vec![klines, crosshair]
    }

    fn mouse_interaction(
        &self,
        interaction: &Interaction,
        bounds: Rectangle,
        cursor: mouse::Cursor,
    ) -> mouse::Interaction {
        match interaction {
            Interaction::Panning { .. } => mouse::Interaction::Grabbing,
            Interaction::Zoomin { .. } => mouse::Interaction::ZoomIn,
            Interaction::PlacingAvwapAnchor => mouse::Interaction::Cell,
            Interaction::DraggingScalpingPanel { .. } => mouse::Interaction::Grabbing,
            Interaction::None | Interaction::Ruler { .. } => {
                if cursor.is_over(bounds) {
                    mouse::Interaction::Crosshair
                } else {
                    mouse::Interaction::default()
                }
            }
        }
    }
}

fn draw_footprint_kline(
    frame: &mut canvas::Frame,
    price_to_y: impl Fn(Price) -> f32,
    x_position: f32,
    candle_width: f32,
    kline: &Kline,
    palette: &Extended,
) {
    let y_open = price_to_y(kline.open);
    let y_high = price_to_y(kline.high);
    let y_low = price_to_y(kline.low);
    let y_close = price_to_y(kline.close);

    let body_color = if kline.close >= kline.open {
        palette.success.weak.color
    } else {
        palette.danger.weak.color
    };
    frame.fill_rectangle(
        Point::new(x_position - (candle_width / 8.0), y_open.min(y_close)),
        Size::new(candle_width / 4.0, (y_open - y_close).abs()),
        body_color,
    );

    let wick_color = if kline.close >= kline.open {
        palette.success.weak.color
    } else {
        palette.danger.weak.color
    };
    let marker_line = Stroke::with_color(
        Stroke {
            width: 1.0,
            ..Default::default()
        },
        wick_color.scale_alpha(0.6),
    );
    frame.stroke(
        &Path::line(
            Point::new(x_position, y_high),
            Point::new(x_position, y_low),
        ),
        marker_line,
    );
}

fn draw_candle_dp(
    frame: &mut canvas::Frame,
    price_to_y: impl Fn(Price) -> f32,
    candle_width: f32,
    _palette: &Extended,
    x_position: f32,
    kline: &Kline,
) {
    let y_open = price_to_y(kline.open);
    let y_high = price_to_y(kline.high);
    let y_low = price_to_y(kline.low);
    let y_close = price_to_y(kline.close);

    let is_bull = kline.close >= kline.open;
    let body_color = if is_bull {
        iced::Color::from_rgb(0.149, 0.651, 0.604) // #26a69a
    } else {
        iced::Color::from_rgb(0.937, 0.325, 0.314) // #ef5350
    };

    // Wick drawn first so body sits on top
    frame.fill_rectangle(
        Point::new(x_position - (candle_width / 8.0), y_high),
        Size::new(candle_width / 4.0, (y_high - y_low).abs()),
        body_color,
    );
    frame.fill_rectangle(
        Point::new(x_position - (candle_width / 2.0), y_open.min(y_close)),
        Size::new(candle_width, (y_open - y_close).abs().max(1.0)),
        body_color,
    );
}

fn render_data_source<F>(
    data_source: &PlotData<KlineDataPoint>,
    frame: &mut canvas::Frame,
    earliest: u64,
    latest: u64,
    interval_to_x: impl Fn(u64) -> f32,
    draw_fn: F,
) where
    F: Fn(&mut canvas::Frame, f32, &Kline, &KlineTrades),
{
    match data_source {
        PlotData::TickBased(tick_aggr) => {
            let earliest = earliest as usize;
            let latest = latest as usize;

            tick_aggr
                .datapoints
                .iter()
                .rev()
                .enumerate()
                .filter(|(index, _)| *index <= latest && *index >= earliest)
                .for_each(|(index, tick_aggr)| {
                    let x_position = interval_to_x(index as u64);

                    draw_fn(frame, x_position, &tick_aggr.kline, &tick_aggr.footprint);
                });
        }
        PlotData::TimeBased(timeseries) => {
            if latest < earliest {
                return;
            }

            timeseries
                .datapoints
                .range(UnixMs::new(earliest)..=UnixMs::new(latest))
                .for_each(|(timestamp, dp)| {
                    let x_position = interval_to_x(timestamp.as_u64());

                    draw_fn(frame, x_position, &dp.kline, &dp.footprint);
                });
        }
    }
}

fn draw_all_npocs(
    data_source: &PlotData<KlineDataPoint>,
    frame: &mut canvas::Frame,
    price_to_y: impl Fn(Price) -> f32,
    interval_to_x: impl Fn(u64) -> f32,
    candle_width: f32,
    cell_width: f32,
    cell_height: f32,
    palette: &Extended,
    studies: &[FootprintStudy],
    visible_earliest: u64,
    visible_latest: u64,
    cluster_kind: ClusterKind,
    spacing: ContentGaps,
    imb_study_on: bool,
) {
    let Some(lookback) = studies.iter().find_map(|study| {
        if let FootprintStudy::NPoC { lookback } = study {
            Some(*lookback)
        } else {
            None
        }
    }) else {
        return;
    };

    let (filled_color, naked_color) = (
        palette.background.strong.color,
        if palette.is_dark {
            palette.warning.weak.color.scale_alpha(0.5)
        } else {
            palette.warning.strong.color
        },
    );

    let line_height = cell_height.min(1.0);

    let bar_width_factor: f32 = 0.9;
    let inset = (cell_width * (1.0 - bar_width_factor)) / 2.0;

    let candle_lane_factor: f32 = match cluster_kind {
        ClusterKind::VolumeProfile | ClusterKind::DeltaProfile => 0.25,
        ClusterKind::BidAsk => 1.0,
    };

    let start_x_for = |cell_center_x: f32| -> f32 {
        match cluster_kind {
            ClusterKind::BidAsk => cell_center_x + (candle_width / 2.0) + spacing.candle_to_cluster,
            ClusterKind::VolumeProfile | ClusterKind::DeltaProfile => {
                let content_left = (cell_center_x - (cell_width / 2.0)) + inset;
                let candle_lane_left = content_left
                    + if imb_study_on {
                        candle_width + spacing.marker_to_candle
                    } else {
                        0.0
                    };
                candle_lane_left + candle_width * candle_lane_factor + spacing.candle_to_cluster
            }
        }
    };

    let wick_x_for = |cell_center_x: f32| -> f32 {
        match cluster_kind {
            ClusterKind::BidAsk => cell_center_x, // not used for BidAsk clustering
            ClusterKind::VolumeProfile | ClusterKind::DeltaProfile => {
                let content_left = (cell_center_x - (cell_width / 2.0)) + inset;
                let candle_lane_left = content_left
                    + if imb_study_on {
                        candle_width + spacing.marker_to_candle
                    } else {
                        0.0
                    };
                candle_lane_left + (candle_width * candle_lane_factor) / 2.0
                    - (spacing.candle_to_cluster * 0.5)
            }
        }
    };

    let end_x_for = |cell_center_x: f32| -> f32 {
        match cluster_kind {
            ClusterKind::BidAsk => cell_center_x - (candle_width / 2.0) - spacing.candle_to_cluster,
            ClusterKind::VolumeProfile | ClusterKind::DeltaProfile => wick_x_for(cell_center_x),
        }
    };

    let rightmost_cell_center_x = {
        let earliest_x = interval_to_x(visible_earliest);
        let latest_x = interval_to_x(visible_latest);
        if earliest_x > latest_x {
            earliest_x
        } else {
            latest_x
        }
    };

    let mut draw_the_line = |interval: u64, poc: &PointOfControl| {
        let start_x = start_x_for(interval_to_x(interval));

        let (line_width, color) = match poc.status {
            NPoc::Naked => {
                let end_x = end_x_for(rightmost_cell_center_x);
                let line_width = end_x - start_x;
                if line_width.abs() <= cell_width {
                    return;
                }
                (line_width, naked_color)
            }
            NPoc::Filled { at } => {
                let end_x = end_x_for(interval_to_x(at));
                let line_width = end_x - start_x;
                if line_width.abs() <= cell_width {
                    return;
                }
                (line_width, filled_color)
            }
            _ => return,
        };

        frame.fill_rectangle(
            Point::new(start_x, price_to_y(poc.price) - line_height / 2.0),
            Size::new(line_width, line_height),
            color,
        );
    };

    match data_source {
        PlotData::TickBased(tick_aggr) => {
            tick_aggr
                .datapoints
                .iter()
                .rev()
                .enumerate()
                .take(lookback)
                .filter_map(|(index, dp)| dp.footprint.poc.as_ref().map(|poc| (index as u64, poc)))
                .for_each(|(interval, poc)| draw_the_line(interval, poc));
        }
        PlotData::TimeBased(timeseries) => {
            timeseries
                .datapoints
                .iter()
                .rev()
                .take(lookback)
                .filter_map(|(timestamp, dp)| {
                    dp.footprint
                        .poc
                        .as_ref()
                        .map(|poc| (timestamp.as_u64(), poc))
                })
                .for_each(|(interval, poc)| draw_the_line(interval, poc));
        }
    }
}

struct KeyLevels {
    prev_day_high: Option<f32>,
    prev_day_low: Option<f32>,
    daily_open: Option<f32>,
    weekly_open: Option<f32>,
}

fn compute_key_levels(
    datapoints: &std::collections::BTreeMap<UnixMs, data::chart::kline::KlineDataPoint>,
) -> KeyLevels {
    let Some((&latest_ts, _)) = datapoints.iter().next_back() else {
        return KeyLevels {
            prev_day_high: None,
            prev_day_low: None,
            daily_open: None,
            weekly_open: None,
        };
    };

    const DAY_MS: u64 = 86_400_000;
    let current_day = latest_ts.as_u64() / DAY_MS;
    let prev_day = current_day.saturating_sub(1);
    // Day of week: epoch day 0 = Thursday; (day + 3) % 7 → 0 = Monday
    let day_of_week = (current_day + 3) % 7;
    let this_monday = current_day - day_of_week;

    let mut prev_high: Option<f32> = None;
    let mut prev_low: Option<f32> = None;
    let mut daily_open: Option<f32> = None;
    let mut weekly_open: Option<f32> = None;

    for (&ts, dp) in datapoints.iter() {
        let day = ts.as_u64() / DAY_MS;

        if day == current_day && daily_open.is_none() {
            daily_open = Some(dp.kline.open.to_f32());
        }
        if day >= this_monday && weekly_open.is_none() {
            weekly_open = Some(dp.kline.open.to_f32());
        }
        if day == prev_day {
            let h = dp.kline.high.to_f32();
            let l = dp.kline.low.to_f32();
            prev_high = Some(prev_high.map_or(h, |old: f32| old.max(h)));
            prev_low = Some(prev_low.map_or(l, |old: f32| old.min(l)));
        }
    }

    KeyLevels {
        prev_day_high: prev_high,
        prev_day_low: prev_low,
        daily_open,
        weekly_open,
    }
}

fn draw_key_levels(
    frame: &mut canvas::Frame,
    region: &Rectangle,
    datapoints: &std::collections::BTreeMap<UnixMs, data::chart::kline::KlineDataPoint>,
    price_to_y: &impl Fn(Price) -> f32,
) {
    let kl = compute_key_levels(datapoints);
    let line_width = region.x + region.width;

    let levels: &[(Option<f32>, &str, [f32; 4])] = &[
        (kl.prev_day_high, "PDH", [0.85, 0.85, 0.85, 0.55]),
        (kl.prev_day_low, "PDL", [0.85, 0.85, 0.85, 0.55]),
        (kl.daily_open, "DO", [0.40, 0.90, 0.45, 0.60]),
        (kl.weekly_open, "WO", [0.35, 0.65, 1.00, 0.60]),
    ];

    for &(price_opt, label, rgba) in levels {
        let Some(price) = price_opt else { continue };
        if !price.is_finite() || price <= 0.0 {
            continue;
        }
        let y = price_to_y(Price::from_f32(price));
        if !y.is_finite() {
            continue;
        }

        let color = Color::from_rgba(rgba[0], rgba[1], rgba[2], rgba[3]);
        frame.stroke(
            &Path::line(Point::new(region.x, y), Point::new(line_width, y)),
            Stroke::with_color(
                Stroke {
                    width: 1.0,
                    line_dash: LineDash {
                        segments: &[3.0, 6.0],
                        offset: 0,
                    },
                    ..Stroke::default()
                },
                color,
            ),
        );
        frame.fill_text(canvas::Text {
            content: label.to_string(),
            position: Point::new(line_width - 3.0, y - 2.0),
            size: iced::Pixels(TEXT_SIZE * 0.82),
            color,
            align_x: iced::alignment::Horizontal::Right.into(),
            align_y: iced::alignment::Vertical::Bottom,
            font: style::AZERET_MONO,
            ..canvas::Text::default()
        });
    }
}

fn draw_key_level_tooltip(
    frame: &mut canvas::Frame,
    palette: &Extended,
    datapoints: &std::collections::BTreeMap<UnixMs, data::chart::kline::KlineDataPoint>,
    cursor: Point,
    bounds: Rectangle,
    price_to_y: impl Fn(Price) -> f32,
) {
    const DESCRIPTIONS: &[(&str, &str, &str)] = &[
        (
            "PDH",
            "Previous Day High",
            "Highest price reached yesterday",
        ),
        ("PDL", "Previous Day Low", "Lowest price reached yesterday"),
        ("DO", "Daily Open", "Opening price of today (00:00 UTC)"),
        (
            "WO",
            "Weekly Open",
            "Opening price of this week (Mon 00:00 UTC)",
        ),
    ];

    let kl = compute_key_levels(datapoints);
    let prices: &[Option<f32>] = &[
        kl.prev_day_high,
        kl.prev_day_low,
        kl.daily_open,
        kl.weekly_open,
    ];

    for (price_opt, (abbr, name, desc)) in prices.iter().zip(DESCRIPTIONS.iter()) {
        let Some(price) = price_opt else { continue };
        if !price.is_finite() || *price <= 0.0 {
            continue;
        }
        let y = price_to_y(Price::from_f32(*price));
        if !y.is_finite() {
            continue;
        }

        if (cursor.y - y).abs() > 8.0 {
            continue;
        }

        // Draw tooltip box
        let pad = 6.0;
        let line_h = 14.0;
        let box_w = 200.0;
        let box_h = pad * 2.0 + line_h * 2.0;

        let mut box_x = cursor.x - box_w - 8.0;
        if box_x < 4.0 {
            box_x = cursor.x + 8.0;
        }
        let box_y = (cursor.y - box_h / 2.0)
            .max(4.0)
            .min(bounds.height - box_h - 4.0);

        let bg = palette.background.weakest.color.scale_alpha(0.95);
        frame.fill_rectangle(Point::new(box_x, box_y), Size::new(box_w, box_h), bg);

        // Border
        let border_color = palette.background.strong.color.scale_alpha(0.6);
        let border = Path::rectangle(Point::new(box_x, box_y), Size::new(box_w, box_h));
        frame.stroke(
            &border,
            Stroke::with_color(
                Stroke {
                    width: 1.0,
                    ..Stroke::default()
                },
                border_color,
            ),
        );

        // Abbreviation + full name
        frame.fill_text(canvas::Text {
            content: format!("{abbr} — {name}"),
            position: Point::new(box_x + pad, box_y + pad),
            size: iced::Pixels(11.0),
            color: palette.background.base.text,
            align_x: iced::alignment::Horizontal::Left.into(),
            align_y: iced::alignment::Vertical::Top,
            font: style::AZERET_MONO,
            ..canvas::Text::default()
        });

        // Description
        frame.fill_text(canvas::Text {
            content: desc.to_string(),
            position: Point::new(box_x + pad, box_y + pad + line_h),
            size: iced::Pixels(10.0),
            color: palette.background.base.text.scale_alpha(0.65),
            align_x: iced::alignment::Horizontal::Left.into(),
            align_y: iced::alignment::Vertical::Top,
            font: style::AZERET_MONO,
            ..canvas::Text::default()
        });

        break; // Only show one tooltip at a time
    }
}

/// Draws session background rectangles sized to the actual high/low of each session's candles.
/// Sessions use real market hours (UTC): Asia 23:00–08:00, London 07:00–16:00, NY 13:00–21:00.
/// Asia crosses midnight so its open_offset is negative (relative to current day start).
/// Only drawn when timeframe <= 4h.
fn draw_session_lines(
    frame: &mut canvas::Frame,
    datapoints: &std::collections::BTreeMap<UnixMs, data::chart::kline::KlineDataPoint>,
    earliest: u64,
    latest: u64,
    timeframe_ms: u64,
    interval_to_x: impl Fn(u64) -> f32,
    price_to_y: impl Fn(Price) -> f32,
) {
    if timeframe_ms > 4 * 3600 * 1000 {
        return;
    }

    const DAY_MS: u64 = 86_400_000;
    // (label, open_offset_ms: i64, close_offset_ms: i64, fill, label_bottom)
    // Asia has negative open_offset: starts at 23:00 of the previous calendar day.
    // label_bottom=true: draw label at bottom of rect and skip left border (avoids overlap with NY).
    const SESSIONS: &[(&str, i64, i64, Color, bool)] = &[
        (
            "Asia",
            -(1 * 3600 * 1000), // 23:00 prev day
            8 * 3600 * 1000,    // 08:00 current day
            Color {
                r: 0.1,
                g: 0.22,
                b: 0.36,
                a: 0.2,
            },
            false,
        ),
        (
            "London",
            7 * 3600 * 1000,
            16 * 3600 * 1000,
            Color {
                r: 0.1,
                g: 0.24,
                b: 0.17,
                a: 0.2,
            },
            false,
        ),
        (
            "New York",
            13 * 3600 * 1000,
            21 * 3600 * 1000,
            Color {
                r: 0.24,
                g: 0.16,
                b: 0.1,
                a: 0.2,
            },
            false,
        ),
        (
            "LON+NY",
            13 * 3600 * 1000,
            16 * 3600 * 1000,
            Color {
                r: 0.24,
                g: 0.23,
                b: 0.1,
                a: 0.25,
            },
            true, // same open as NY — put label at bottom, skip border
        ),
        (
            "Dead zone",
            21 * 3600 * 1000,
            23 * 3600 * 1000,
            Color {
                r: 0.24,
                g: 0.1,
                b: 0.1,
                a: 0.15,
            },
            false,
        ),
    ];

    let first_day = (earliest / DAY_MS) * DAY_MS;
    let last_day = (latest / DAY_MS) * DAY_MS + DAY_MS;

    let mut day = first_day;
    while day <= last_day {
        for &(label, open_off, close_off, fill, label_bottom) in SESSIONS {
            let ts_open = (day as i64 + open_off) as u64;
            let ts_close = (day as i64 + close_off) as u64;

            if ts_close < earliest || ts_open > latest {
                continue;
            }

            let x_open = interval_to_x(ts_open.max(earliest));
            let x_close = interval_to_x(ts_close.min(latest));
            if !x_open.is_finite() || !x_close.is_finite() {
                continue;
            }
            let x_left = x_open.min(x_close);
            let x_right = x_open.max(x_close);
            let width = x_right - x_left;
            if width <= 0.0 {
                continue;
            }

            // High/low of candles within this session's time window
            let (ses_high, ses_low) = datapoints
                .range(UnixMs::new(ts_open)..UnixMs::new(ts_close))
                .fold((f32::NEG_INFINITY, f32::INFINITY), |(h, l), (_, dp)| {
                    (h.max(dp.kline.high.to_f32()), l.min(dp.kline.low.to_f32()))
                });

            if !ses_high.is_finite() || !ses_low.is_finite() {
                continue;
            }

            let y_top = price_to_y(Price::from_f32(ses_high));
            let y_bottom = price_to_y(Price::from_f32(ses_low));
            let rect_h = y_bottom - y_top;

            if rect_h <= 0.0 {
                continue;
            }

            // Filled background rectangle sized to session's actual price range
            frame.fill(
                &Path::rectangle(Point::new(x_left, y_top), Size::new(width, rect_h)),
                fill,
            );

            // Left border — skipped for sessions that share their open with another (e.g. LON+NY)
            if !label_bottom && ts_open >= earliest && ts_open <= latest {
                let border_color = Color { a: 0.35, ..fill };
                frame.stroke(
                    &Path::line(Point::new(x_open, y_top), Point::new(x_open, y_bottom)),
                    Stroke {
                        style: canvas::stroke::Style::Solid(border_color),
                        width: 1.0,
                        ..Stroke::default()
                    },
                );
            }

            // Label: top-left for normal sessions, bottom-left for overlapping ones
            let (label_x, label_y, align_y) = if label_bottom {
                (
                    x_left + 4.0,
                    y_bottom - 4.0,
                    iced::alignment::Vertical::Bottom,
                )
            } else {
                (x_left + 4.0, y_top + 4.0, iced::alignment::Vertical::Top)
            };
            if label_y.is_finite() && label_x.is_finite() {
                frame.fill_text(canvas::Text {
                    content: label.to_string(),
                    position: Point::new(label_x, label_y),
                    size: iced::Pixels(10.0),
                    color: Color {
                        r: 0.9,
                        g: 0.9,
                        b: 0.9,
                        a: 0.9,
                    },
                    align_x: iced::alignment::Horizontal::Left.into(),
                    align_y,
                    font: style::AZERET_MONO,
                    ..canvas::Text::default()
                });
            }
        }
        day += DAY_MS;
    }
}

fn effective_cluster_qty(
    scaling: ClusterScaling,
    visible_max: f32,
    footprint: &KlineTrades,
    cluster_kind: ClusterKind,
) -> f32 {
    let individual_max = match cluster_kind {
        ClusterKind::BidAsk => footprint
            .trades
            .values()
            .map(|group| group.buy_qty.max(group.sell_qty))
            .max()
            .unwrap_or_default(),
        ClusterKind::DeltaProfile => footprint
            .trades
            .values()
            .map(|group| group.buy_qty.abs_diff(group.sell_qty))
            .max()
            .unwrap_or_default(),
        ClusterKind::VolumeProfile => footprint
            .trades
            .values()
            .map(|group| group.buy_qty + group.sell_qty)
            .max()
            .unwrap_or_default(),
    };
    let individual_max_f32 = f32::from(individual_max);

    match scaling {
        ClusterScaling::VisibleRange => Qty::scale_or_one(visible_max),
        ClusterScaling::Datapoint => individual_max.to_scale_or_one(),
        ClusterScaling::Hybrid { weight } => {
            let w = weight.clamp(0.0, 1.0);
            Qty::scale_or_one(visible_max * w + individual_max_f32 * (1.0 - w))
        }
    }
}

fn draw_clusters(
    frame: &mut canvas::Frame,
    price_to_y: impl Fn(Price) -> f32,
    x_position: f32,
    cell_width: f32,
    cell_height: f32,
    candle_width: f32,
    max_cluster_qty: f32,
    palette: &Extended,
    text_size: f32,
    step: PriceStep,
    show_text: bool,
    imbalance: Option<(usize, Option<usize>, bool)>,
    kline: &Kline,
    footprint: &KlineTrades,
    cluster_kind: ClusterKind,
    spacing: ContentGaps,
) {
    let text_color = palette.background.weakest.text;

    let bar_width_factor: f32 = 0.9;
    let inset = (cell_width * (1.0 - bar_width_factor)) / 2.0;

    let cell_left = x_position - (cell_width / 2.0);
    let content_left = cell_left + inset;
    let content_right = x_position + (cell_width / 2.0) - inset;

    match cluster_kind {
        ClusterKind::VolumeProfile | ClusterKind::DeltaProfile => {
            let area = ProfileArea::new(
                content_left,
                content_right,
                candle_width,
                spacing,
                imbalance.is_some(),
            );
            let bar_alpha = if show_text { 0.25 } else { 1.0 };

            for (price, group) in &footprint.trades {
                let buy_qty = f32::from(group.buy_qty);
                let sell_qty = f32::from(group.sell_qty);
                let y = price_to_y(*price);

                match cluster_kind {
                    ClusterKind::VolumeProfile => {
                        super::draw_volume_bar(
                            frame,
                            area.bars_left,
                            y,
                            buy_qty,
                            sell_qty,
                            max_cluster_qty,
                            area.bars_width,
                            cell_height,
                            palette.success.base.color,
                            palette.danger.base.color,
                            bar_alpha,
                            true,
                        );

                        if show_text {
                            draw_cluster_text(
                                frame,
                                &abbr_large_numbers(f32::from(group.total_qty())),
                                Point::new(area.bars_left, y),
                                text_size,
                                text_color,
                                Alignment::Start,
                                Alignment::Center,
                            );
                        }
                    }
                    ClusterKind::DeltaProfile => {
                        let delta = f32::from(group.delta_qty());
                        if show_text {
                            draw_cluster_text(
                                frame,
                                &abbr_large_numbers(delta),
                                Point::new(area.bars_left, y),
                                text_size,
                                text_color,
                                Alignment::Start,
                                Alignment::Center,
                            );
                        }

                        let bar_width = (delta.abs() / max_cluster_qty) * area.bars_width;
                        if bar_width > 0.0 {
                            let color = if delta >= 0.0 {
                                palette.success.base.color.scale_alpha(bar_alpha)
                            } else {
                                palette.danger.base.color.scale_alpha(bar_alpha)
                            };
                            frame.fill_rectangle(
                                Point::new(area.bars_left, y - (cell_height / 2.0)),
                                Size::new(bar_width, cell_height),
                                color,
                            );
                        }
                    }
                    _ => {}
                }

                if let Some((threshold, color_scale, ignore_zeros)) = imbalance {
                    let higher_price =
                        Price::from_f32(price.to_f32() + step.to_f32_lossy()).round_to_step(step);

                    let rect_w = ((area.imb_marker_width - 1.0) / 2.0).max(1.0);
                    let buyside_x = area.imb_marker_left + area.imb_marker_width - rect_w;
                    let sellside_x =
                        area.imb_marker_left + area.imb_marker_width - (2.0 * rect_w) - 1.0;

                    draw_imbalance_markers(
                        frame,
                        &price_to_y,
                        footprint,
                        *price,
                        sell_qty,
                        higher_price,
                        threshold,
                        color_scale,
                        ignore_zeros,
                        cell_height,
                        palette,
                        buyside_x,
                        sellside_x,
                        rect_w,
                    );
                }
            }

            draw_footprint_kline(
                frame,
                &price_to_y,
                area.candle_center_x,
                candle_width,
                kline,
                palette,
            );
        }
        ClusterKind::BidAsk => {
            let area = BidAskArea::new(
                x_position,
                content_left,
                content_right,
                candle_width,
                spacing,
            );

            let bar_alpha = if show_text { 0.25 } else { 1.0 };

            let imb_marker_reserve = if imbalance.is_some() {
                ((area.imb_marker_width - 1.0) / 2.0).max(1.0)
            } else {
                0.0
            };

            let right_max_x =
                area.bid_area_right - imb_marker_reserve - (2.0 * spacing.marker_to_bars);
            let right_area_width = (right_max_x - area.bid_area_left).max(0.0);

            let left_min_x =
                area.ask_area_left + imb_marker_reserve + (2.0 * spacing.marker_to_bars);
            let left_area_width = (area.ask_area_right - left_min_x).max(0.0);

            for (price, group) in &footprint.trades {
                let buy_qty = f32::from(group.buy_qty);
                let sell_qty = f32::from(group.sell_qty);
                let y = price_to_y(*price);

                if buy_qty > 0.0 && right_area_width > 0.0 {
                    if show_text {
                        draw_cluster_text(
                            frame,
                            &abbr_large_numbers(buy_qty),
                            Point::new(area.bid_area_left, y),
                            text_size,
                            text_color,
                            Alignment::Start,
                            Alignment::Center,
                        );
                    }

                    let bar_width = (buy_qty / max_cluster_qty) * right_area_width;
                    if bar_width > 0.0 {
                        frame.fill_rectangle(
                            Point::new(area.bid_area_left, y - (cell_height / 2.0)),
                            Size::new(bar_width, cell_height),
                            palette.success.base.color.scale_alpha(bar_alpha),
                        );
                    }
                }
                if sell_qty > 0.0 && left_area_width > 0.0 {
                    if show_text {
                        draw_cluster_text(
                            frame,
                            &abbr_large_numbers(sell_qty),
                            Point::new(area.ask_area_right, y),
                            text_size,
                            text_color,
                            Alignment::End,
                            Alignment::Center,
                        );
                    }

                    let bar_width = (sell_qty / max_cluster_qty) * left_area_width;
                    if bar_width > 0.0 {
                        frame.fill_rectangle(
                            Point::new(area.ask_area_right, y - (cell_height / 2.0)),
                            Size::new(-bar_width, cell_height),
                            palette.danger.base.color.scale_alpha(bar_alpha),
                        );
                    }
                }

                if let Some((threshold, color_scale, ignore_zeros)) = imbalance
                    && area.imb_marker_width > 0.0
                {
                    let higher_price =
                        Price::from_f32(price.to_f32() + step.to_f32_lossy()).round_to_step(step);

                    let rect_width = ((area.imb_marker_width - 1.0) / 2.0).max(1.0);

                    let buyside_x = area.bid_area_right - rect_width - spacing.marker_to_bars;
                    let sellside_x = area.ask_area_left + spacing.marker_to_bars;

                    draw_imbalance_markers(
                        frame,
                        &price_to_y,
                        footprint,
                        *price,
                        sell_qty,
                        higher_price,
                        threshold,
                        color_scale,
                        ignore_zeros,
                        cell_height,
                        palette,
                        buyside_x,
                        sellside_x,
                        rect_width,
                    );
                }
            }

            draw_footprint_kline(
                frame,
                &price_to_y,
                area.candle_center_x,
                candle_width,
                kline,
                palette,
            );
        }
    }

    if show_text {
        let mut total_buy = Qty::zero();
        let mut total_sell = Qty::zero();
        let mut total_delta = Qty::zero();

        for group in footprint.trades.values() {
            total_buy += group.buy_qty;
            total_sell += group.sell_qty;
            total_delta += group.delta_qty();
        }

        let summary_y = price_to_y(kline.low) + cell_height * 1.5;
        let line_spacing = text_size * 1.2;

        let total_vol = total_buy + total_sell;

        draw_cluster_text(
            frame,
            &format!("V: {}", abbr_large_numbers(total_vol.to_f32_lossy())),
            Point::new(x_position, summary_y),
            text_size * 0.9,
            palette.background.weakest.text,
            Alignment::Center,
            Alignment::Start,
        );

        let delta_color = if total_delta >= Qty::zero() {
            palette.success.base.color
        } else {
            palette.danger.base.color
        };

        draw_cluster_text(
            frame,
            &format!("Δ: {}", abbr_large_numbers(total_delta.to_f32_lossy())),
            Point::new(x_position, summary_y + line_spacing),
            text_size * 0.9,
            delta_color,
            Alignment::Center,
            Alignment::Start,
        );
    }
}

fn draw_imbalance_markers(
    frame: &mut canvas::Frame,
    price_to_y: &impl Fn(Price) -> f32,
    footprint: &KlineTrades,
    price: Price,
    sell_qty: f32,
    higher_price: Price,
    threshold: usize,
    color_scale: Option<usize>,
    ignore_zeros: bool,
    cell_height: f32,
    palette: &Extended,
    buyside_x: f32,
    sellside_x: f32,
    rect_width: f32,
) {
    if ignore_zeros && sell_qty <= 0.0 {
        return;
    }

    if let Some(group) = footprint.trades.get(&higher_price) {
        let diagonal_buy_qty = f32::from(group.buy_qty);

        if ignore_zeros && diagonal_buy_qty <= 0.0 {
            return;
        }

        let rect_height = cell_height / 2.0;

        let alpha_from_ratio = |ratio: f32| -> f32 {
            if let Some(scale) = color_scale {
                let divisor = (scale as f32 / 10.0) - 1.0;
                (0.2 + 0.8 * ((ratio - 1.0) / divisor).min(1.0)).min(1.0)
            } else {
                1.0
            }
        };

        if diagonal_buy_qty >= sell_qty {
            let required_qty = sell_qty * (100 + threshold) as f32 / 100.0;
            if diagonal_buy_qty > required_qty {
                let ratio = diagonal_buy_qty / required_qty;
                let alpha = alpha_from_ratio(ratio);

                let y = price_to_y(higher_price);
                frame.fill_rectangle(
                    Point::new(buyside_x, y - (rect_height / 2.0)),
                    Size::new(rect_width, rect_height),
                    palette.success.weak.color.scale_alpha(alpha),
                );
            }
        } else {
            let required_qty = diagonal_buy_qty * (100 + threshold) as f32 / 100.0;
            if sell_qty > required_qty {
                let ratio = sell_qty / required_qty;
                let alpha = alpha_from_ratio(ratio);

                let y = price_to_y(price);
                frame.fill_rectangle(
                    Point::new(sellside_x, y - (rect_height / 2.0)),
                    Size::new(rect_width, rect_height),
                    palette.danger.weak.color.scale_alpha(alpha),
                );
            }
        }
    }
}

impl ContentGaps {
    fn from_view(candle_width: f32, scaling: f32) -> Self {
        let px = |p: f32| p / scaling;
        let base = (candle_width * 0.2).max(px(2.0));
        Self {
            marker_to_candle: base,
            candle_to_cluster: base,
            marker_to_bars: px(2.0),
        }
    }
}

#[derive(Clone, Copy, Debug)]
struct ContentGaps {
    /// Space between imb. markers candle body
    marker_to_candle: f32,
    /// Space between candle body and clusters
    candle_to_cluster: f32,
    /// Inner space reserved between imb. markers and clusters (used for BidAsk)
    marker_to_bars: f32,
}

fn draw_cluster_text(
    frame: &mut canvas::Frame,
    text: &str,
    position: Point,
    text_size: f32,
    color: iced::Color,
    align_x: Alignment,
    align_y: Alignment,
) {
    frame.fill_text(canvas::Text {
        content: text.to_string(),
        position,
        size: iced::Pixels(text_size),
        color,
        align_x: align_x.into(),
        align_y: align_y.into(),
        font: style::AZERET_MONO,
        ..canvas::Text::default()
    });
}

fn draw_crosshair_tooltip(
    data: &PlotData<KlineDataPoint>,
    ticker_info: &TickerInfo,
    frame: &mut canvas::Frame,
    palette: &Extended,
    at_interval: u64,
) {
    let kline_opt = match data {
        PlotData::TimeBased(timeseries) => timeseries
            .datapoints
            .iter()
            .find(|(time, _)| **time == UnixMs::new(at_interval))
            .map(|(_, dp)| &dp.kline)
            .or_else(|| {
                if timeseries.datapoints.is_empty() {
                    None
                } else {
                    let (last_time, dp) = timeseries.datapoints.last_key_value()?;
                    if at_interval > last_time.as_u64() {
                        Some(&dp.kline)
                    } else {
                        None
                    }
                }
            }),
        PlotData::TickBased(tick_aggr) => {
            let index = (at_interval / u64::from(tick_aggr.interval.0)) as usize;
            if index < tick_aggr.datapoints.len() {
                Some(&tick_aggr.datapoints[tick_aggr.datapoints.len() - 1 - index].kline)
            } else {
                None
            }
        }
    };

    if let Some(kline) = kline_opt {
        let change_pct = ((kline.close - kline.open).to_f32() / kline.open.to_f32()) * 100.0;
        let change_color = if change_pct >= 0.0 {
            palette.success.base.color
        } else {
            palette.danger.base.color
        };

        let base_color = palette.background.base.text;
        let precision = ticker_info.min_ticksize;

        let segments = [
            ("O", base_color, false),
            (&kline.open.to_string(precision), change_color, true),
            ("H", base_color, false),
            (&kline.high.to_string(precision), change_color, true),
            ("L", base_color, false),
            (&kline.low.to_string(precision), change_color, true),
            ("C", base_color, false),
            (&kline.close.to_string(precision), change_color, true),
            (&format!("{change_pct:+.2}%"), change_color, true),
        ];

        let total_width: f32 = segments
            .iter()
            .map(|(s, _, _)| s.len() as f32 * (TEXT_SIZE * 0.8))
            .sum();

        let position = Point::new(8.0, 8.0);

        let tooltip_rect = Rectangle {
            x: position.x,
            y: position.y,
            width: total_width,
            height: 16.0,
        };

        frame.fill_rectangle(
            tooltip_rect.position(),
            tooltip_rect.size(),
            palette.background.weakest.color.scale_alpha(0.9),
        );

        let mut x = position.x;
        for (text, seg_color, is_value) in segments {
            frame.fill_text(canvas::Text {
                content: text.to_string(),
                position: Point::new(x, position.y),
                size: iced::Pixels(12.0),
                color: seg_color,
                font: style::AZERET_MONO,
                ..canvas::Text::default()
            });
            x += text.len() as f32 * 8.0;
            x += if is_value { 6.0 } else { 2.0 };
        }
    }
}

struct ProfileArea {
    imb_marker_left: f32,
    imb_marker_width: f32,
    bars_left: f32,
    bars_width: f32,
    candle_center_x: f32,
}

impl ProfileArea {
    fn new(
        content_left: f32,
        content_right: f32,
        candle_width: f32,
        gaps: ContentGaps,
        has_imbalance: bool,
    ) -> Self {
        let candle_lane_left = if has_imbalance {
            content_left + candle_width + gaps.marker_to_candle
        } else {
            content_left
        };
        let candle_lane_width = candle_width * 0.25;

        let bars_left = candle_lane_left + candle_lane_width + gaps.candle_to_cluster;
        let bars_width = (content_right - bars_left).max(0.0);

        let candle_center_x = candle_lane_left + (candle_lane_width / 2.0);

        Self {
            imb_marker_left: content_left,
            imb_marker_width: if has_imbalance { candle_width } else { 0.0 },
            bars_left,
            bars_width,
            candle_center_x,
        }
    }
}

struct BidAskArea {
    bid_area_left: f32,
    bid_area_right: f32,
    ask_area_left: f32,
    ask_area_right: f32,
    candle_center_x: f32,
    imb_marker_width: f32,
}

impl BidAskArea {
    fn new(
        x_position: f32,
        content_left: f32,
        content_right: f32,
        candle_width: f32,
        spacing: ContentGaps,
    ) -> Self {
        let candle_body_width = candle_width * 0.25;

        let candle_left = x_position - (candle_body_width / 2.0);
        let candle_right = x_position + (candle_body_width / 2.0);

        let ask_area_right = candle_left - spacing.candle_to_cluster;
        let bid_area_left = candle_right + spacing.candle_to_cluster;

        Self {
            bid_area_left,
            bid_area_right: content_right,
            ask_area_left: content_left,
            ask_area_right,
            candle_center_x: x_position,
            imb_marker_width: candle_width,
        }
    }
}

#[inline]
fn footprint_cluster_min_width(cluster_kind: ClusterKind) -> f32 {
    match cluster_kind {
        ClusterKind::VolumeProfile | ClusterKind::DeltaProfile => 80.0,
        ClusterKind::BidAsk => 120.0,
    }
}

#[inline]
fn footprint_cluster_text_size(cell_height_unscaled: f32, cell_width_unscaled: f32) -> f32 {
    let text_size_from_height = cell_height_unscaled.round().min(16.0) - 3.0;
    let text_size_from_width = (cell_width_unscaled * 0.1).round().min(16.0) - 3.0;

    text_size_from_height.min(text_size_from_width)
}

#[inline]
fn price_padding_from_pixels(cell_height: f32, tick_size: f32) -> f32 {
    const OUTER_BOUND_PADDING_PX: f32 = 4.0;

    if cell_height <= f32::EPSILON {
        return 0.0;
    }

    (OUTER_BOUND_PADDING_PX / cell_height) * tick_size
}

fn footprint_summary_padding(
    cell_height: f32,
    scaling: f32,
    cell_width: f32,
    tick_size: f32,
    cluster_kind: ClusterKind,
) -> f32 {
    if cell_height <= f32::EPSILON {
        return 0.0;
    }

    let cell_height_unscaled = cell_height * scaling;
    let cell_width_unscaled = cell_width * scaling;

    if !should_show_text(
        cell_height_unscaled,
        cell_width_unscaled,
        footprint_cluster_min_width(cluster_kind),
    ) {
        return 0.0;
    }

    let text_size = footprint_cluster_text_size(cell_height_unscaled, cell_width_unscaled);
    let line_spacing = text_size * 1.2;

    let summary_text_height_px = text_size * 0.9;
    let summary_y_start_px = cell_height * 1.5;

    let second_line_y_start_px = summary_y_start_px + line_spacing;
    let summary_y_end_px = second_line_y_start_px + summary_text_height_px;

    let extra_bottom_padding_px = summary_text_height_px;
    let summary_y_end_with_padding_px = summary_y_end_px + extra_bottom_padding_px;
    let summary_ticks = summary_y_end_with_padding_px / cell_height;

    summary_ticks * tick_size
}

#[inline]
fn should_show_text(cell_height_unscaled: f32, cell_width_unscaled: f32, min_w: f32) -> bool {
    cell_height_unscaled > 8.0 && cell_width_unscaled > min_w
}
