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
    liq_map_tracker: data::institutional::LiqMapTracker,
    liq_map_snapshot: Option<data::institutional::LiqMapSnapshot>,
    funding_tracker: data::institutional::FundingTracker,
    oi_tracker: data::institutional::OiTracker,
    cooldown_registry: data::strategy::cooldown::CooldownRegistry,
    bar_index: u64,
}

struct DetectorBootstrap {
    ms_tracker: data::structure::MarketStructureTracker,
    ms_context: Option<data::structure::MarketStructureContext>,
    structure_breaks: Vec<data::structure::StructureBreak>,
    ob_detector: data::detectors::OrderBlockDetector,
    ob_context: Option<data::detectors::OrderBlockContext>,
    fvg_detector: data::detectors::FvgDetector,
    fvg_context: Option<data::detectors::FvgContext>,
    liq_map_tracker: data::institutional::LiqMapTracker,
    liq_map_snapshot: Option<data::institutional::LiqMapSnapshot>,
}

fn bootstrap_detectors(klines: &[Kline]) -> DetectorBootstrap {
    let mut ms_tracker = data::structure::MarketStructureTracker::new(200, 3);
    let mut ob_detector = data::detectors::OrderBlockDetector::new(100);
    let mut fvg_detector = data::detectors::FvgDetector::new(100);

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

    let mut liq_map_tracker = data::institutional::LiqMapTracker::new();
    if let (Some(ms), true) = (&ms_snap, last_price > 0.0) {
        liq_map_tracker.update(last_price, ms.range_high, ms.range_low, 0.0, last_ts);
    }
    let liq_map_snapshot = Some(liq_map_tracker.snapshot().clone());

    DetectorBootstrap {
        ms_context: ms_snap,
        structure_breaks,
        ms_tracker,
        ob_detector,
        ob_context,
        fvg_detector,
        fvg_context,
        liq_map_tracker,
        liq_map_snapshot,
    }
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
                    liq_map_tracker: boot.liq_map_tracker,
                    liq_map_snapshot: boot.liq_map_snapshot,
                    funding_tracker: data::institutional::FundingTracker::new(),
                    oi_tracker: data::institutional::OiTracker::new(),
                    cooldown_registry: data::strategy::cooldown::CooldownRegistry::new(5),
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
                    liq_map_tracker: boot.liq_map_tracker,
                    liq_map_snapshot: boot.liq_map_snapshot,
                    funding_tracker: data::institutional::FundingTracker::new(),
                    oi_tracker: data::institutional::OiTracker::new(),
                    cooldown_registry: data::strategy::cooldown::CooldownRegistry::new(5),
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

        if is_new_bar
            && self.strategy_overlay_enabled
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
                        self.liq_map_tracker = boot.liq_map_tracker;
                        self.liq_map_snapshot = boot.liq_map_snapshot;
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
        if !self.strategy_overlay_enabled {
            self.strategy_signals.clear();
        }
        vec![]
    }

    pub fn update_depth(&mut self, depth: &exchange::depth::Depth) {
        self.last_depth = Some(depth.clone());
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
        let vwap = adapter::build_vwap_context(price, vwap_value, avwap_bos);

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
        );

        // Alimentar trackers con la vela cerrada y obtener sus snapshots
        self.ms_tracker
            .push_bar(bar_open, bar_high, bar_low, bar_close, bar_ts_ms);
        let bar_volume = buy_vol.unwrap_or(0.0) + sell_vol.unwrap_or(0.0);
        self.ob_detector.push_bar(
            bar_open, bar_high, bar_low, bar_close, bar_volume, bar_ts_ms,
        );
        self.fvg_detector.push_bar(bar_high, bar_low, bar_ts_ms);

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

        // Alimentar LiqMapTracker con swings del ms_context y OI actual
        {
            let swing_high = self.ms_context.as_ref().and_then(|ms| ms.range_high);
            let swing_low = self.ms_context.as_ref().and_then(|ms| ms.range_low);
            let oi_current = self.indicators[KlineIndicator::OpenInterest]
                .as_ref()
                .and_then(|i| i.oi_snapshot())
                .and_then(|v| v.last().map(|o| o.value as f64))
                .unwrap_or(0.0);
            self.liq_map_tracker
                .update(price, swing_high, swing_low, oi_current, bar_ts_ms);
            self.liq_map_snapshot = Some(self.liq_map_tracker.snapshot().clone());
        }
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
                liq_map: self.liq_map_snapshot.clone(),
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
        };

        let cfg = StrategyConfig {
            enabled: true,
            default_ttl_ms: ttl_ms,
            htf_scoring_enabled: true,
            session_filter_enabled: true,
            ..Default::default()
        };

        self.last_regime = format!("{:?}", ctx.regime);
        self.last_regime_enum = ctx.regime;

        self.bar_index += 1;
        let current_bar = self.bar_index;

        let mut signal = router::route_strategy(&ctx, &cfg);

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

        logger::log_signal(&ctx, &signal);

        let signal_fired = signal.action == StrategyAction::ShadowSignal;
        crate::strategy::intent_logger::log_near_misses(&ctx, &cfg, signal_fired);

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

        if signal.action == StrategyAction::ShadowSignal {
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

    fn draw_liq_map(
        &self,
        frame: &mut canvas::Frame,
        region: &Rectangle,
        price_to_y: impl Fn(Price) -> f32,
    ) {
        let Some(ref snap) = self.liq_map_snapshot else {
            return;
        };

        let right_x = region.x + region.width;
        // Density threshold — skip noise below this
        const MIN_DENSITY: f32 = 0.15;

        for level in snap.density_above.iter().chain(snap.density_below.iter()) {
            if level.density < MIN_DENSITY {
                continue;
            }

            let y = price_to_y(Price::from_f32(level.price as f32));
            if !y.is_finite() {
                continue;
            }

            let is_primary = snap
                .primary_target_above
                .map(|p| (p - level.price).abs() < 0.01)
                .unwrap_or(false)
                || snap
                    .primary_target_below
                    .map(|p| (p - level.price).abs() < 0.01)
                    .unwrap_or(false);

            // Opacity y grosor escalan con densidad; primario = más visible
            let alpha = if is_primary {
                0.85
            } else {
                (level.density * 0.70).clamp(0.15, 0.65)
            };
            let width = if is_primary { 1.5 } else { 0.8 };

            let color = Color::from_rgba(1.0, 0.82, 0.10, alpha); // gold

            let stroke = Stroke {
                style: canvas::stroke::Style::Solid(color),
                width,
                line_dash: LineDash {
                    segments: &[6.0, 3.0],
                    offset: 0,
                },
                ..Default::default()
            };
            frame.stroke(
                &Path::line(Point::new(region.x, y), Point::new(right_x, y)),
                stroke,
            );

            // Etiqueta en el target principal
            if is_primary {
                let label = if snap
                    .primary_target_above
                    .map(|p| (p - level.price).abs() < 0.01)
                    .unwrap_or(false)
                {
                    "LIQ↑"
                } else {
                    "LIQ↓"
                };
                frame.fill_text(canvas::Text {
                    content: label.to_string(),
                    position: Point::new(right_x - 4.0, y - 2.0),
                    size: iced::Pixels(TEXT_SIZE * 0.78),
                    color,
                    align_x: iced::alignment::Horizontal::Right.into(),
                    align_y: iced::alignment::Vertical::Bottom,
                    font: style::AZERET_MONO,
                    ..canvas::Text::default()
                });
            }
        }
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

        let all_fvgs = ctx.bullish_fvgs.iter().chain(ctx.bearish_fvgs.iter());

        for fvg in all_fvgs {
            let (fill_color, border_color) = match (&fvg.fvg_type, &fvg.status) {
                (data::detectors::FvgType::Bullish, data::detectors::FvgStatus::Unfilled) => (
                    Color::from_rgba(0.10, 0.78, 0.80, 0.14),
                    Color::from_rgba(0.10, 0.78, 0.80, 0.60),
                ),
                (
                    data::detectors::FvgType::Bullish,
                    data::detectors::FvgStatus::PartiallyFilled,
                ) => (
                    Color::from_rgba(0.10, 0.78, 0.80, 0.07),
                    Color::from_rgba(0.10, 0.78, 0.80, 0.30),
                ),
                (data::detectors::FvgType::Bearish, data::detectors::FvgStatus::Unfilled) => (
                    Color::from_rgba(0.95, 0.55, 0.10, 0.14),
                    Color::from_rgba(0.95, 0.55, 0.10, 0.60),
                ),
                (
                    data::detectors::FvgType::Bearish,
                    data::detectors::FvgStatus::PartiallyFilled,
                ) => (
                    Color::from_rgba(0.95, 0.55, 0.10, 0.07),
                    Color::from_rgba(0.95, 0.55, 0.10, 0.30),
                ),
                _ => continue, // Filled — skip
            };

            let ts = fvg.timestamp_ms as u64;
            let x_left = if ts >= earliest {
                interval_to_x(ts).max(region.x)
            } else {
                region.x
            };
            let y_top = price_to_y(Price::from_f32(fvg.high as f32));
            let y_bottom = price_to_y(Price::from_f32(fvg.low as f32));

            let width = right_x - x_left;
            let height = y_bottom - y_top;

            if width <= 0.0 || height <= 0.0 || !y_top.is_finite() || !y_bottom.is_finite() {
                continue;
            }

            frame.fill_rectangle(
                Point::new(x_left, y_top),
                Size::new(width, height),
                fill_color,
            );

            let stroke = Stroke {
                style: canvas::stroke::Style::Solid(border_color),
                width: 0.8,
                ..Default::default()
            };
            frame.stroke(
                &Path::line(Point::new(x_left, y_top), Point::new(right_x, y_top)),
                stroke.clone(),
            );
            frame.stroke(
                &Path::line(Point::new(x_left, y_bottom), Point::new(right_x, y_bottom)),
                stroke,
            );

            // FVG label
            let fvg_tag = match &fvg.status {
                data::detectors::FvgStatus::Unfilled => "FVG",
                data::detectors::FvgStatus::PartiallyFilled => "FVG~",
                _ => "FVG",
            };
            frame.fill_text(canvas::Text {
                content: fvg_tag.to_string(),
                position: Point::new(right_x - 4.0, y_top + 2.0),
                size: iced::Pixels(TEXT_SIZE * 0.72),
                color: border_color,
                align_x: iced::alignment::Horizontal::Right.into(),
                align_y: iced::alignment::Vertical::Top,
                font: style::AZERET_MONO,
                ..canvas::Text::default()
            });
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

        // Premium / Discount zones from current ms_context
        if let Some(ref ms) = self.ms_context {
            // HTF bias label in top-right corner
            let (bias_label, bias_color) = match ms.htf_bias {
                data::structure::HtfBias::Bullish => {
                    ("HTF: Bull", Color::from_rgba(0.25, 0.85, 0.45, 0.85))
                }
                data::structure::HtfBias::Bearish => {
                    ("HTF: Bear", Color::from_rgba(0.90, 0.30, 0.30, 0.85))
                }
                data::structure::HtfBias::Neutral => {
                    ("HTF: Neutral", Color::from_rgba(0.70, 0.70, 0.70, 0.70))
                }
            };
            frame.fill_text(canvas::Text {
                content: bias_label.to_string(),
                position: Point::new(right_x - 8.0, region.y + 6.0),
                size: iced::Pixels(TEXT_SIZE * 0.78),
                color: bias_color,
                align_x: iced::alignment::Horizontal::Right.into(),
                align_y: iced::alignment::Vertical::Top,
                font: style::AZERET_MONO,
                ..canvas::Text::default()
            });

            if let (Some(rh), Some(premium), Some(discount), Some(rl)) = (
                ms.range_high,
                ms.premium_threshold,
                ms.discount_threshold,
                ms.range_low,
            ) {
                // Premium zone: price_range_high → premium_threshold
                let y_rh = price_to_y(Price::from_f32(rh as f32));
                let y_premium = price_to_y(Price::from_f32(premium as f32));
                let premium_h = y_premium - y_rh;
                if premium_h > 0.0 && y_rh.is_finite() && y_premium.is_finite() {
                    frame.fill_rectangle(
                        Point::new(region.x, y_rh),
                        Size::new(region.width, premium_h),
                        Color::from_rgba(0.90, 0.20, 0.20, 0.08),
                    );
                }

                // Discount zone: discount_threshold → range_low
                let y_discount = price_to_y(Price::from_f32(discount as f32));
                let y_rl = price_to_y(Price::from_f32(rl as f32));
                let discount_h = y_rl - y_discount;
                if discount_h > 0.0 && y_discount.is_finite() && y_rl.is_finite() {
                    frame.fill_rectangle(
                        Point::new(region.x, y_discount),
                        Size::new(region.width, discount_h),
                        Color::from_rgba(0.20, 0.80, 0.30, 0.08),
                    );
                }
            }
        }

        // BOS / CHoCH markers
        for sb in &self.structure_breaks {
            let ts = sb.timestamp_ms as u64;
            if ts < earliest || ts > latest {
                continue;
            }

            let x = interval_to_x(ts);
            let y = price_to_y(Price::from_f32(sb.broken_level as f32));

            if !x.is_finite() || !y.is_finite() {
                continue;
            }

            let (label, color) = match (&sb.event, &sb.direction) {
                (data::structure::StructureEvent::Bos, data::structure::HtfBias::Bullish) => {
                    ("BOS", Color::from_rgba(0.25, 0.85, 0.45, 0.90))
                }
                (data::structure::StructureEvent::Bos, _) => {
                    ("BOS", Color::from_rgba(0.90, 0.30, 0.30, 0.90))
                }
                (data::structure::StructureEvent::Choch, data::structure::HtfBias::Bullish) => {
                    ("CHoCH", Color::from_rgba(0.35, 0.60, 1.0, 0.90))
                }
                (data::structure::StructureEvent::Choch, _) => {
                    ("CHoCH", Color::from_rgba(0.85, 0.45, 1.0, 0.90))
                }
            };

            // Línea horizontal punteada en el nivel roto
            let h_stroke = Stroke {
                style: canvas::stroke::Style::Solid(color.scale_alpha(0.40)),
                width: 0.8,
                line_dash: LineDash {
                    segments: &[5.0, 3.0],
                    offset: 0,
                },
                ..Default::default()
            };
            frame.stroke(
                &Path::line(Point::new(x, y), Point::new(right_x, y)),
                h_stroke,
            );

            // Label en el punto de ruptura
            frame.fill_text(canvas::Text {
                content: label.to_string(),
                position: Point::new(x + 4.0, y - 2.0),
                size: iced::Pixels(TEXT_SIZE * 0.80),
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

        let all_obs = ctx.bullish_obs.iter().chain(ctx.bearish_obs.iter());

        for ob in all_obs {
            let (fill_color, border_color) = match (&ob.ob_type, &ob.status) {
                (data::detectors::OBType::Bullish, data::detectors::OBStatus::Active) => (
                    Color::from_rgba(0.20, 0.80, 0.40, 0.18),
                    Color::from_rgba(0.20, 0.80, 0.40, 0.70),
                ),
                (data::detectors::OBType::Bullish, data::detectors::OBStatus::Tested) => (
                    Color::from_rgba(0.20, 0.80, 0.40, 0.10),
                    Color::from_rgba(0.20, 0.80, 0.40, 0.40),
                ),
                (data::detectors::OBType::Bullish, data::detectors::OBStatus::Mitigated) => (
                    Color::from_rgba(0.20, 0.80, 0.40, 0.05),
                    Color::from_rgba(0.20, 0.80, 0.40, 0.18),
                ),
                (data::detectors::OBType::Bearish, data::detectors::OBStatus::Active) => (
                    Color::from_rgba(0.90, 0.28, 0.28, 0.18),
                    Color::from_rgba(0.90, 0.28, 0.28, 0.70),
                ),
                (data::detectors::OBType::Bearish, data::detectors::OBStatus::Tested) => (
                    Color::from_rgba(0.90, 0.28, 0.28, 0.10),
                    Color::from_rgba(0.90, 0.28, 0.28, 0.40),
                ),
                (data::detectors::OBType::Bearish, data::detectors::OBStatus::Mitigated) => (
                    Color::from_rgba(0.90, 0.28, 0.28, 0.05),
                    Color::from_rgba(0.90, 0.28, 0.28, 0.18),
                ),
                _ => continue, // Invalidated — skip
            };

            let ts = ob.timestamp_ms as u64;
            let x_left = if ts >= earliest {
                interval_to_x(ts)
            } else {
                region.x
            };
            let x_left = x_left.max(region.x);
            let y_top = price_to_y(Price::from_f32(ob.high as f32));
            let y_bottom = price_to_y(Price::from_f32(ob.low as f32));

            let width = right_x - x_left;
            let height = y_bottom - y_top;

            if width <= 0.0 || height <= 0.0 || !y_top.is_finite() || !y_bottom.is_finite() {
                continue;
            }

            // Fill
            frame.fill_rectangle(
                Point::new(x_left, y_top),
                Size::new(width, height),
                fill_color,
            );

            // Border (top and bottom lines only — sides would look noisy)
            let stroke = Stroke {
                style: canvas::stroke::Style::Solid(border_color),
                width: 1.0,
                ..Default::default()
            };
            let top_line = Path::line(Point::new(x_left, y_top), Point::new(right_x, y_top));
            let bot_line = Path::line(Point::new(x_left, y_bottom), Point::new(right_x, y_bottom));
            frame.stroke(&top_line, stroke.clone());
            frame.stroke(&bot_line, stroke);

            // Mid-line (50% mitigation level) — dashed, very subtle
            let y_mid = price_to_y(Price::from_f32(ob.mid as f32));
            if y_mid.is_finite() {
                let mid_stroke = Stroke {
                    style: canvas::stroke::Style::Solid(border_color.scale_alpha(0.5)),
                    width: 0.5,
                    line_dash: LineDash {
                        segments: &[4.0, 4.0],
                        offset: 0,
                    },
                    ..Default::default()
                };
                let mid_line = Path::line(Point::new(x_left, y_mid), Point::new(right_x, y_mid));
                frame.stroke(&mid_line, mid_stroke);
            }

            // OB label in top-right corner
            let status_tag = match &ob.status {
                data::detectors::OBStatus::Active => "OB",
                data::detectors::OBStatus::Tested => "OB~",
                data::detectors::OBStatus::Mitigated => "OB×",
                _ => "OB",
            };
            frame.fill_text(canvas::Text {
                content: status_tag.to_string(),
                position: Point::new(right_x - 4.0, y_top + 2.0),
                size: iced::Pixels(TEXT_SIZE * 0.72),
                color: border_color,
                align_x: iced::alignment::Horizontal::Right.into(),
                align_y: iced::alignment::Vertical::Top,
                font: style::AZERET_MONO,
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

            // Semi-transparent zone between entry and target
            let zone_top = f32::min(entry_y, target_y);
            let zone_height = (entry_y - target_y).abs();
            let zone_color = if is_long {
                Color::from_rgba(0.2, 0.8, 0.4, 0.05)
            } else {
                Color::from_rgba(0.9, 0.3, 0.3, 0.05)
            };
            frame.fill_rectangle(
                Point::new(0.0, zone_top),
                Size::new(line_width, zone_height),
                zone_color,
            );
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
                if self.config.show_liq_map {
                    self.draw_liq_map(frame, &region, price_to_y);
                }

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
            }

            if self.strategy_overlay_enabled {
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
