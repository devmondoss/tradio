#![recursion_limit = "256"]
//! Layer 2: headless strategy monitor for cloud deployment (Railway).
//!
//! Connects to Binance LinearPerps WebSocket streams, accumulates kline/depth/trade
//! data, runs strategy detection on every bar close, and emits JSON to stdout
//! (captured by Railway deploy logs).
//!
//! Environment variables (all optional):
//!   SYMBOL           — ticker to monitor (default: BTCUSDT)
//!   TIMEFRAME_MIN    — kline timeframe in minutes (default: 5)
//!   PAPER_INITIAL_CAPITAL, PAPER_LEVERAGE, PAPER_MAX_POSITIONS,
//!   PAPER_RISK_PCT, PAPER_SLIPPAGE_BPS, PAPER_TAKER_FEE, PAPER_FUNDING_RATE

mod config_loader;
mod intrabar;
mod supabase_writer;

use std::collections::VecDeque;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use reqwest;
use bytes::Bytes;
use fastwebsockets::{FragmentCollector, OpCode};
use http_body_util::Empty;
use hyper::{Request, header::{CONNECTION, UPGRADE}, upgrade::Upgraded};
use hyper_util::rt::{TokioExecutor, TokioIo};
use std::sync::LazyLock;
use tokio_rustls::{TlsConnector, rustls::{ClientConfig, RootCertStore, crypto::aws_lc_rs, pki_types::ServerName}};

use config_loader::ConfigLoader;
use intrabar::{IntrabarConfig, IntrabarDetector, IntrabarMode};
use supabase_writer::SupabaseWriter;
use data::institutional::{
    FundingRateSample, FundingTracker, InstitutionalContext, LiqSide,
    LiquidationEvent, LiquidationTracker, LongShortSnapshot, LsRatioTracker, LsSource,
    OiHistSnapshot, OiTracker, TakerRatioSnapshot,
};
use data::strategy::{
    adapter::{
        build_flow_context, build_orderbook_context, build_volume_profile_context,
        build_vwap_context, count_price_reversals, derive_cvd_divergence,
        derive_failed_acceptance_and_absorption, derive_regime, derive_regime_with_hysteresis, wall_nearby,
    },
    intent_logger::collect_near_misses,
    paper::PaperAccount,
    router::route_strategy,
    types::{
        AbsorptionSide, CvdDivergence, DataQuality, OrderBookContext, Regime, Side,
        StrategyAction, StrategyConfig, StrategyMarketContext,
    },
};
use exchange::{
    Kline, Volume, PushFrequency, Ticker, TickerInfo, Timeframe,
    adapter::{
        AdapterHandles, AdapterNetworkConfig, Event, Exchange, MarketKind, StreamConfig, Venue,
    },
    depth::Depth,
};
use futures::StreamExt;

// ── constants ───────────────────────────────────────────────────────────────

const VP_WINDOW: usize = 300;
const VP_BINS: usize = 150;
const REGIME_WINDOW: usize = 14;
const CVD_WINDOW: usize = 50;
const ATR_WINDOW: usize = 14;

// ── pipeline metrics ──────────────────────────────────────────────────────────

#[derive(Default)]
struct PipelineMetrics {
    kline_ticks: u64,
    trade_batches: u64,
    trade_count: u64,
    depth_updates: u64,
    bars_processed: u64,
    latencies_ms: Vec<i64>,
    processing_ms: Vec<u128>,
    last_depth_at: Option<Instant>,
    last_trade_at: Option<Instant>,
    // counters
    bars_since_signal: u64,
    signals_today: u64,
    liq_events_today: u64,
    ws_reconnects: u64,
}

// ── data freshness ────────────────────────────────────────────────────────────

/// Tracks when each institutional data source was last updated.
/// Used to compute ages shown in [bar] log and trigger [WARN] in [metrics].
#[derive(Default)]
struct DataFreshness {
    liq_last_event_at:  Option<Instant>,
    liq_stream_ok:      bool,            // true while forceOrder WS is connected
    ls_fetched_at:      Option<Instant>,
    taker_fetched_at:   Option<Instant>,
    oi_fetched_at:      Option<Instant>,
    funding_tick_at:    Option<Instant>,
}

impl DataFreshness {
    fn age_str(t: Option<Instant>) -> String {
        match t {
            None => "never".into(),
            Some(t) => {
                let s = t.elapsed().as_secs();
                if s < 60 { format!("{s}s") } else { format!("{}m{:02}s", s / 60, s % 60) }
            }
        }
    }

    fn liq_age_str(&self)     -> String { Self::age_str(self.liq_last_event_at) }
    fn ls_age_str(&self)      -> String { Self::age_str(self.ls_fetched_at) }
    fn taker_age_str(&self)   -> String { Self::age_str(self.taker_fetched_at) }
    fn oi_age_str(&self)      -> String { Self::age_str(self.oi_fetched_at) }
    fn funding_age_str(&self) -> String { Self::age_str(self.funding_tick_at) }

    /// Returns (sources_ok, total_sources). A source is "ok" if updated within 10 min.
    /// Liq is counted as ok when the forceOrder stream is connected (events are 0 on quiet markets).
    fn quality(&self) -> (u8, u8) {
        let threshold = Duration::from_secs(10 * 60);
        let fresh = |t: Option<Instant>| t.map(|i| i.elapsed() < threshold).unwrap_or(false);
        let mut ok = 0u8;
        if self.liq_stream_ok                { ok += 1; }  // stream connected = liq ok
        if fresh(self.ls_fetched_at)         { ok += 1; }
        if fresh(self.ls_fetched_at)         { ok += 1; }  // top + global share same fetch
        if fresh(self.oi_fetched_at)         { ok += 1; }
        if fresh(self.taker_fetched_at)      { ok += 1; }
        if fresh(self.funding_tick_at)       { ok += 1; }
        (ok, 6)
    }
}

// ── stream health ─────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, Default, PartialEq)]
enum StreamHealth { #[default] Unknown, Ok, Reconnecting, Disc }

impl std::fmt::Display for StreamHealth {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Unknown      => write!(f, "?"),
            Self::Ok           => write!(f, "ok"),
            Self::Reconnecting => write!(f, "recon"),
            Self::Disc         => write!(f, "disc"),
        }
    }
}

#[derive(Default)]
struct StreamStates {
    klines: StreamHealth,
    depth:  StreamHealth,
    liq:    StreamHealth,
}

// ── outcome tracking ──────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy)]
enum OutcomeSource { BarClose, Intrabar }

impl std::fmt::Display for OutcomeSource {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::BarClose => write!(f, "bar"),
            Self::Intrabar => write!(f, "intrabar"),
        }
    }
}

/// Tracks MFE/MAE/RR for one signal from entry until the next bar closes.
#[derive(Debug, Clone)]
struct PendingOutcome {
    signal_ms: i64,
    source: OutcomeSource,
    strategy_id: String,
    is_long: bool,
    entry_px: f64,
    stop_px: f64,
    target_px: f64,
    risk: f64,
    mfe: f64,           // best favorable excursion in pts
    mae: f64,           // worst adverse excursion in pts
    rr_at_1m: Option<f64>,
    rr_at_3m: Option<f64>,
    rr_at_5m: Option<f64>,
    hit_target: bool,
    hit_stop: bool,
}

impl PendingOutcome {
    fn new(
        signal_ms: i64,
        source: OutcomeSource,
        strategy_id: String,
        is_long: bool,
        entry_px: f64,
        stop_px: f64,
        target_px: f64,
    ) -> Self {
        let risk = (entry_px - stop_px).abs().max(1.0);
        Self {
            signal_ms, source, strategy_id, is_long,
            entry_px, stop_px, target_px, risk,
            mfe: 0.0, mae: 0.0,
            rr_at_1m: None, rr_at_3m: None, rr_at_5m: None,
            hit_target: false, hit_stop: false,
        }
    }

    fn update(&mut self, px: f64, now_ms: i64) {
        let excursion = if self.is_long { px - self.entry_px } else { self.entry_px - px };
        let adverse   = -excursion;

        if excursion > self.mfe { self.mfe = excursion; }
        if adverse   > self.mae { self.mae = adverse; }

        if self.is_long {
            if px <= self.stop_px   { self.hit_stop   = true; }
            if px >= self.target_px { self.hit_target = true; }
        } else {
            if px >= self.stop_px   { self.hit_stop   = true; }
            if px <= self.target_px { self.hit_target = true; }
        }

        let rr = excursion / self.risk;
        let elapsed = now_ms - self.signal_ms;
        if elapsed >= 60_000  && self.rr_at_1m.is_none() { self.rr_at_1m = Some(rr); }
        if elapsed >= 180_000 && self.rr_at_3m.is_none() { self.rr_at_3m = Some(rr); }
        if elapsed >= 300_000 && self.rr_at_5m.is_none() { self.rr_at_5m = Some(rr); }
    }

    fn log(&self, bar_close_ms: i64) {
        let age_ms = bar_close_ms - self.signal_ms;
        eprintln!(
            "[outcome] src={} id={} side={} entry={:.1} stop={:.1} target={:.1} \
             risk={:.1} mfe={:.1} mae={:.1} \
             rr_1m={} rr_3m={} rr_5m={} \
             hit_target={} hit_stop={} age={}ms",
            self.source, self.strategy_id,
            if self.is_long { "L" } else { "S" },
            self.entry_px, self.stop_px, self.target_px,
            self.risk, self.mfe, self.mae,
            fmt_opt(self.rr_at_1m), fmt_opt(self.rr_at_3m), fmt_opt(self.rr_at_5m),
            self.hit_target, self.hit_stop, age_ms,
        );
    }
}

fn fmt_opt(v: Option<f64>) -> String {
    v.map(|x| format!("{x:.2}")).unwrap_or_else(|| "-".into())
}

/// Tracks forward price horizons for a closed paper trade so we can PATCH
/// signal_outcomes with price_5m/r_5m etc. after the appropriate bars elapse.
#[derive(Clone)]
struct PendingHorizon {
    signal_uuid: String,
    entry_price: f64,
    stop_price: f64,
    side: String,      // "Long" or "Short"
    close_bar_n: u64,  // bar_counter when trade closed
}

/// Bar-level state frozen at each 5m close, reused by the intrabar evaluator.
/// Only bar-level fields go here; tick-level fields come from live BarState.
#[derive(Clone)]
struct FrozenBarCtx {
    regime: Regime,
    fast_slope: f64,
    atr: f64,
    // Volume profile
    poc: Option<f64>,
    vah: Option<f64>,
    val: Option<f64>,
    hvn_nearby: Vec<f64>,
    lvn_nearby: Vec<f64>,
    // VWAP / AVWAP
    vwap_session: Option<f64>,
    avwap_bos: Option<f64>,
    // Flow (bar-computed, frozen)
    cvd_slope: Option<f64>,
    failed_acceptance: bool,
    footprint_absorption: AbsorptionSide,
    cvd_divergence: Option<CvdDivergence>,
    // Market structure
    mss_active: bool,
    sweep_confirmed: bool,
    price_action_clean: bool,
    // Order-book walls (snapshot from last close)
    bid_wall_nearby: bool,
    ask_wall_nearby: bool,
    // Swing levels
    swing_high_20: Option<f64>,
    swing_low_20: Option<f64>,
    // OI / basis (slow-moving — freeze from last bar)
    oi_momentum_aligned: Option<bool>,
    basis: Option<f64>,
    // Timing
    bar_close_ms: i64,
    bar_close_price: f64,
}

impl PipelineMetrics {
    fn report(&self, freshness: &DataFreshness, streams: &StreamStates) {
        let avg_lat = if self.latencies_ms.is_empty() {
            0.0
        } else {
            self.latencies_ms.iter().sum::<i64>() as f64 / self.latencies_ms.len() as f64
        };
        let max_lat = self.latencies_ms.iter().max().copied().unwrap_or(0);

        let depth_age_ms = self.last_depth_at.map(|t| t.elapsed().as_millis()).unwrap_or(999_999);
        let trade_age_ms = self.last_trade_at.map(|t| t.elapsed().as_millis()).unwrap_or(999_999);

        let (inst_ok, inst_total) = freshness.quality();
        eprintln!(
            "[metrics] bars={} signals={} liq_events={} ws_reconnects={} | \
             kline_ticks={} trades={} depth_updates={} | \
             bar_latency_avg={:.0}ms max={max_lat}ms | \
             depth_age={depth_age_ms}ms trade_age={trade_age_ms}ms | \
             inst={inst_ok}/{inst_total} liq_age={} ls_age={} taker_age={} oi_age={} funding_age={} | \
             ws=[kline:{} depth:{} trades:ok liq:{}]",
            self.bars_processed, self.signals_today, self.liq_events_today, self.ws_reconnects,
            self.kline_ticks, self.trade_count, self.depth_updates,
            avg_lat,
            freshness.liq_age_str(), freshness.ls_age_str(),
            freshness.taker_age_str(), freshness.oi_age_str(), freshness.funding_age_str(),
            streams.klines, streams.depth, streams.liq,
        );

        // stream health warnings
        if depth_age_ms > 5_000 {
            eprintln!("[WARN] depth stream stale — last update {depth_age_ms}ms ago");
        }
        if trade_age_ms > 5_000 {
            eprintln!("[WARN] trade stream stale — last update {trade_age_ms}ms ago");
        }
        // institutional freshness warnings
        let warn_age = |label: &str, t: Option<Instant>, threshold_secs: u64| {
            if let Some(inst) = t {
                let age = inst.elapsed().as_secs();
                if age > threshold_secs {
                    eprintln!("[WARN] {label}: last update {age}s ago — fetch may be failing");
                }
            } else {
                eprintln!("[WARN] {label}: no data received yet");
            }
        };
        if streams.liq == StreamHealth::Disc {
            eprintln!("[WARN] forceOrder stream disconnected — liq data unavailable");
        } else if let Some(age) = freshness.liq_last_event_at.map(|t| t.elapsed().as_secs()) {
            // Only warn if we previously had events and now they've stopped (liq=never is normal on quiet markets)
            if age > 300 {
                eprintln!("[WARN] forceOrder stream: no liquidation events for {age}s — market very quiet or stream issue");
            }
        }
        warn_age("LS ratio fetch",    freshness.ls_fetched_at,      600);
        warn_age("taker ratio fetch",  freshness.taker_fetched_at,   300);
        warn_age("OI fetch",           freshness.oi_fetched_at,       600);
        warn_age("funding stream",     freshness.funding_tick_at,     120);

        let avg_proc = if self.processing_ms.is_empty() {
            0.0
        } else {
            self.processing_ms.iter().sum::<u128>() as f64 / self.processing_ms.len() as f64
        };
        let max_proc = self.processing_ms.iter().max().copied().unwrap_or(0);
        eprintln!("[metrics] proc_avg={avg_proc:.0}ms proc_max={max_proc}ms bars_since_signal={}",
            self.bars_since_signal);
        if self.bars_processed > 0 && avg_proc > 100.0 {
            eprintln!("[WARN] high bar processing time {avg_proc:.0}ms — check compute path");
        }
    }
}

struct BarState {
    bars: VecDeque<Kline>,
    cvd: f64,
    cvd_history: VecDeque<f64>,
    bar_buy_vol: f64,
    bar_sell_vol: f64,
    vwap_cum_pv: f64,
    vwap_cum_vol: f64,
    vwap_day: i64,
    vwap_session: Option<f64>,
    depth: Option<Depth>,
    paper: PaperAccount,
    metrics: PipelineMetrics,
    // Crypto-native context
    funding_rate: Option<f64>,
    spot_price: Option<f64>,
    oi_history: VecDeque<f64>,
    // Institutional trackers
    liq_tracker: LiquidationTracker,
    ls_tracker: LsRatioTracker,
    oi_tracker: OiTracker,
    funding_tracker: FundingTracker,
    last_taker_ratio: Option<TakerRatioSnapshot>,
    // Supabase writer (None if SUPABASE_URL not set)
    supabase: Option<SupabaseWriter>,
    // UUID of the most recently written shadow_signals row — used to link signal_outcomes
    pending_signal_uuid: Option<String>,
    // Receives the UUID from an in-flight write_signal task (fire-and-forget, non-blocking)
    pending_uuid_rx: Option<tokio::sync::oneshot::Receiver<Option<String>>>,
    // Dynamic config loaded from Supabase
    cfg: StrategyConfig,
    config_loader: ConfigLoader,
    // Sends detected regime string to the async loop for config reloading
    regime_tx: Option<tokio::sync::mpsc::Sender<String>>,
    // Last regime seen — used to detect regime changes and write regime_history
    last_regime: Option<String>,
    // Last regime as enum — used for hysteresis in derive_regime_with_hysteresis
    last_regime_enum: data::strategy::types::Regime,
    // Timestamp (ms) when the current regime started — for duration_ms in regime_history
    regime_started_at_ms: Option<i64>,
    // Health monitoring
    freshness: DataFreshness,
    streams: StreamStates,
    // Outcome tracking (intrabar + bar-close signals)
    pending_outcomes: Vec<PendingOutcome>,
    // Intrabar tactical layer
    intrabar_cfg: IntrabarConfig,
    current_price: f64,
    frozen: Option<FrozenBarCtx>,
    last_intrabar_eval_price: f64,
    last_intrabar_eval_ms: i64,
    intrabar_eval_count: u32,
    intrabar_signal_fired: bool,
    // Forward horizon tracker: populated when a trade closes, patched over the next 12 bars
    bar_counter: u64,
    pending_horizons: Vec<PendingHorizon>,
}

impl BarState {
    fn new(supabase: Option<SupabaseWriter>) -> Self {
        Self {
            bars: VecDeque::with_capacity(VP_WINDOW + 1),
            cvd: 0.0,
            cvd_history: VecDeque::with_capacity(CVD_WINDOW + 1),
            bar_buy_vol: 0.0,
            bar_sell_vol: 0.0,
            vwap_cum_pv: 0.0,
            vwap_cum_vol: 0.0,
            vwap_day: -1,
            vwap_session: None,
            depth: None,
            paper: PaperAccount::load_or_new(),
            metrics: PipelineMetrics::default(),
            funding_rate: None,
            spot_price: None,
            oi_history: VecDeque::with_capacity(7),
            liq_tracker: LiquidationTracker::new(),
            ls_tracker: LsRatioTracker::new(),
            oi_tracker: OiTracker::new(),
            funding_tracker: FundingTracker::new(),
            last_taker_ratio: None,
            supabase,
            pending_signal_uuid: None,
            pending_uuid_rx: None,
            cfg: StrategyConfig { enabled: true, ..StrategyConfig::default() },
            config_loader: ConfigLoader::new(),
            regime_tx: None,
            last_regime: None,
            last_regime_enum: data::strategy::types::Regime::Unknown,
            regime_started_at_ms: None,
            freshness: DataFreshness::default(),
            streams: StreamStates::default(),
            pending_outcomes: Vec::new(),
            intrabar_cfg: IntrabarConfig::from_env(),
            current_price: 0.0,
            frozen: None,
            last_intrabar_eval_price: 0.0,
            last_intrabar_eval_ms: 0,
            intrabar_eval_count: 0,
            intrabar_signal_fired: false,
            bar_counter: 0,
            pending_horizons: Vec::new(),
        }
    }

    fn notify_regime(&self, regime: &str) {
        if let Some(tx) = &self.regime_tx {
            let _ = tx.try_send(regime.to_string());
        }
    }

    fn on_liquidation(&mut self, event: LiquidationEvent) {
        self.freshness.liq_last_event_at = Some(Instant::now());
        self.metrics.liq_events_today += 1;
        self.liq_tracker.push(event);
    }

    fn on_trade(&mut self, is_sell: bool, qty: f32, price: f64) {
        if price > 0.0 {
            self.current_price = price;
        }
        let delta = f64::from(qty);
        if is_sell {
            self.bar_sell_vol += delta;
            self.cvd -= delta;
        } else {
            self.bar_buy_vol += delta;
            self.cvd += delta;
        }
        self.metrics.trade_count += 1;
        self.metrics.last_trade_at = Some(Instant::now());
    }

    fn on_trade_batch(&mut self) {
        self.metrics.trade_batches += 1;
    }

    // ── Outcome tracking ──────────────────────────────────────────────────────

    fn push_outcome(&mut self, signal: &data::strategy::types::StrategySignal, source: OutcomeSource, at_ms: i64) {
        let (Some(entry), Some(stop), Some(target), Some(side)) = (
            signal.entry_price, signal.stop_price, signal.target_price, signal.side
        ) else { return; };
        let is_long = matches!(side, Side::Long);
        let id = signal.strategy_id.as_ref().map(|s| format!("{s:?}")).unwrap_or_else(|| "Unknown".into());
        self.pending_outcomes.push(PendingOutcome::new(
            at_ms, source, id, is_long, entry, stop, target,
        ));
    }

    fn update_outcomes(&mut self, px: f64, now_ms: i64) {
        for o in &mut self.pending_outcomes {
            o.update(px, now_ms);
        }
    }

    fn resolve_outcomes(&mut self, bar_close_ms: i64, bar_close_px: f64) {
        let mut resolved = vec![];
        self.pending_outcomes.retain(|o| {
            if bar_close_ms >= o.signal_ms + 300_000 {
                resolved.push(o.clone());
                false
            } else {
                true
            }
        });
        for mut o in resolved {
            o.update(bar_close_px, bar_close_ms);
            let rr_final = if o.is_long {
                (bar_close_px - o.entry_px) / o.risk
            } else {
                (o.entry_px - bar_close_px) / o.risk
            };
            if o.rr_at_1m.is_none() { o.rr_at_1m = Some(rr_final); }
            if o.rr_at_3m.is_none() { o.rr_at_3m = Some(rr_final); }
            if o.rr_at_5m.is_none() { o.rr_at_5m = Some(rr_final); }
            o.log(bar_close_ms);
            if let Some(sb) = &self.supabase {
                let age_ms = bar_close_ms - o.signal_ms;
                sb.write_outcome(
                    o.signal_ms,
                    &o.source.to_string(),
                    &o.strategy_id,
                    if o.is_long { "L" } else { "S" },
                    o.entry_px, o.stop_px, o.target_px, o.risk,
                    o.mfe, o.mae,
                    o.rr_at_1m, o.rr_at_3m, o.rr_at_5m,
                    o.hit_target, o.hit_stop, age_ms,
                );
            }
        }
    }

    // ── Forward horizon tracker ───────────────────────────────────────────────

    /// Called at the start of each on_bar_close() with the previous bar's close price.
    /// Checks all pending trades and patches signal_outcomes with price_Xm/r_Xm when
    /// the corresponding bar horizon has elapsed.
    async fn flush_pending_horizons(&mut self, bar_close: f64) {
        // Horizons mapped to elapsed bars (5m bars): 5m=1, 15m=3, 30m=6, 1h=12
        const HORIZONS: &[(&str, u64)] = &[("5m", 1), ("15m", 3), ("30m", 6), ("1h", 12)];

        let mut completed: Vec<usize> = Vec::new();

        for (idx, h) in self.pending_horizons.iter().enumerate() {
            let elapsed = self.bar_counter.saturating_sub(h.close_bar_n);
            let sign = if h.side == "Long" { 1.0 } else { -1.0 };
            let risk = (h.entry_price - h.stop_price).abs();

            for &(label, bars) in HORIZONS {
                if elapsed == bars {
                    let r = if risk > 0.0 {
                        sign * (bar_close - h.entry_price) / risk
                    } else {
                        0.0
                    };
                    if let Some(sb) = &self.supabase {
                        let sb = sb.clone();
                        let uuid = h.signal_uuid.clone();
                        let lbl = label.to_string();
                        tokio::spawn(async move {
                            sb.patch_horizon(&uuid, &lbl, bar_close, r).await;
                        });
                    }
                }
            }

            // Mark complete once 1h horizon (12 bars) has passed
            if elapsed > 12 {
                completed.push(idx);
            }
        }

        // Remove completed in reverse order to preserve indices
        for idx in completed.into_iter().rev() {
            self.pending_horizons.swap_remove(idx);
        }
    }

    // ── Intrabar tactical layer ───────────────────────────────────────────────

    fn should_eval_intrabar(&self, now_ms: i64) -> bool {
        let cfg = &self.intrabar_cfg;
        if !cfg.enabled { return false; }
        let Some(frozen) = &self.frozen else { return false; };
        if self.intrabar_signal_fired { return false; }
        if self.intrabar_eval_count >= cfg.max_evals_per_bar { return false; }
        if self.current_price <= 0.0 { return false; }

        let elapsed_ms = (now_ms - self.last_intrabar_eval_ms).max(0) as u64;
        if elapsed_ms < cfg.min_seconds_between_evals * 1000 { return false; }

        let price_move = (self.current_price - self.last_intrabar_eval_price).abs();
        let time_fallback = elapsed_ms >= cfg.time_fallback_ms;
        if frozen.atr > 0.0 {
            price_move >= cfg.price_move_atr_k * frozen.atr || time_fallback
        } else {
            time_fallback
        }
    }

    fn build_intrabar_ctx(&self, frozen: &FrozenBarCtx, now_ms: i64, symbol: &str) -> StrategyMarketContext {
        let px = self.current_price;

        // Regime override: fast_slope from last bar close reveals directional bias
        // even when the canonical regime hasn't flipped yet (requires bar close).
        let effective_regime = if frozen.fast_slope < -0.35 && frozen.regime == Regime::TrendUp {
            Regime::TrendDown
        } else if frozen.fast_slope > 0.35 && frozen.regime == Regime::TrendDown {
            Regime::TrendUp
        } else {
            frozen.regime
        };

        let vwap_ctx = build_vwap_context(px, frozen.vwap_session, frozen.avwap_bos);
        let vp_ctx = build_volume_profile_context(
            px,
            frozen.poc,
            frozen.vah,
            frozen.val,
            frozen.hvn_nearby.clone(),
            frozen.lvn_nearby.clone(),
        );
        let ob_ctx = match &self.depth {
            Some(d) => build_orderbook_context(d),
            None => OrderBookContext {
                obi_l5: None, obi_l10: None, obi_l20: None,
                microprice: None, spread_bps: None,
                walls_above: vec![], walls_below: vec![],
                thin_zone_above: false, thin_zone_below: false,
                quality: DataQuality::Missing,
                spoof: None,
            },
        };

        let live_delta = self.bar_buy_vol - self.bar_sell_vol;
        let bid_wall = wall_nearby(&ob_ctx.walls_below, px, frozen.atr);
        let ask_wall = wall_nearby(&ob_ctx.walls_above, px, frozen.atr);
        let oi_delta = if self.oi_history.len() >= 2 {
            let r = self.oi_history.back().copied().unwrap_or(0.0);
            let o = self.oi_history.front().copied().unwrap_or(0.0);
            Some(r - o)
        } else {
            None
        };

        let flow = build_flow_context(
            Some(self.cvd),
            frozen.cvd_slope,
            Some(live_delta),
            Some(self.bar_buy_vol),
            Some(self.bar_sell_vol),
            None,
            frozen.failed_acceptance,
            frozen.footprint_absorption,
            frozen.cvd_divergence,
            self.funding_rate,
            frozen.basis,
            oi_delta,
            frozen.oi_momentum_aligned,
            bid_wall,
            ask_wall,
            frozen.price_action_clean,
            frozen.mss_active,
            frozen.sweep_confirmed,
            Some(frozen.fast_slope),
        );

        let liq_snap = self.liq_tracker.snapshot(now_ms);
        let ls_snap = self.ls_tracker.snapshot();
        let oi_snap = self.oi_tracker.snapshot();
        let fund_snap = self.funding_tracker.snapshot();
        let inst_quality = if ls_snap.top_traders_long_pct == 0.5
            && ls_snap.retail_long_pct == 0.5
            && fund_snap.current == 0.0
        {
            DataQuality::Fallback
        } else {
            DataQuality::Live
        };
        let institutional = Some(InstitutionalContext {
            timestamp_ms: now_ms,
            liquidations: liq_snap,
            ls_ratio: ls_snap,
            oi_trend: oi_snap,
            taker_ratio: self.last_taker_ratio.clone(),
            funding: fund_snap,
            quality: inst_quality,
            smart_money_score: None,
            liq_map: None,
        });

        StrategyMarketContext {
            symbol: symbol.to_string(),
            timestamp_ms: now_ms,
            price: px,
            regime: effective_regime,
            atr: if frozen.atr > 0.0 { Some(frozen.atr) } else { None },
            volume_profile: vp_ctx,
            vwap: vwap_ctx,
            flow,
            orderbook: ob_ctx,
            institutional,
            swing_high_20: frozen.swing_high_20,
            swing_low_20: frozen.swing_low_20,
            market_structure: None,
            session: None,
            order_blocks: None,
            fvg: None,
        }
    }

    async fn on_intrabar_tick(&mut self, now_ms: i64, triggered_by: &str, symbol: &str) {
        let Some(frozen) = self.frozen.clone() else { return; };
        let ctx = self.build_intrabar_ctx(&frozen, now_ms, symbol);
        let cfg = self.cfg.clone();
        let signal = route_strategy(&ctx, &cfg);

        self.last_intrabar_eval_price = self.current_price;
        self.last_intrabar_eval_ms = now_ms;
        self.intrabar_eval_count += 1;

        let fired = signal.action == StrategyAction::ShadowSignal;
        eprintln!(
            "[intrabar] ts={now_ms} px={:.2} regime={:?} fast_slope={:.3} trigger={triggered_by} \
             eval={}/{} action={:?} score={:.3} id={:?}",
            ctx.price, ctx.regime, frozen.fast_slope,
            self.intrabar_eval_count, self.intrabar_cfg.max_evals_per_bar,
            signal.action, signal.score, signal.strategy_id,
        );

        if !fired { return; }

        // Track outcome regardless of mode — measures suppressed signals too
        self.push_outcome(&signal, OutcomeSource::Intrabar, now_ms);

        match self.intrabar_cfg.mode {
            IntrabarMode::ObserveOnly => {
                eprintln!("[intrabar] signal suppressed — mode=ObserveOnly");
            }
            IntrabarMode::ShadowEvent => {
                if let Ok(json) = serde_json::to_string(&signal) {
                    println!("{{\"event\":\"intrabar_signal\",\"data\":{json}}}");
                }
            }
            IntrabarMode::ShadowSignal => {
                self.intrabar_signal_fired = true;
                self.metrics.signals_today += 1;
                if let Ok(json) = serde_json::to_string(&signal) {
                    println!("{{\"event\":\"intrabar_signal\",\"data\":{json}}}");
                }
                if let Some(sb) = self.supabase.clone() {
                    let signal_clone = signal.clone();
                    let ctx_clone = ctx.clone();
                    tokio::spawn(async move {
                        sb.write_signal(&signal_clone, &ctx_clone).await;
                    });
                }
            }
        }
    }

    fn on_depth(&mut self, depth: Depth) {
        self.depth = Some(depth);
        self.metrics.depth_updates += 1;
        self.metrics.last_depth_at = Some(Instant::now());
    }

    async fn on_bar_close(&mut self, bar: Kline, bar_close_ms: u64, symbol: &str) {
        let cfg = self.cfg.clone();
        let bar_ms = bar.time.as_u64() as i64;
        self.metrics.bars_processed += 1;

        // --- Forward horizon check (runs before processing current bar) ---
        let bar_c_prev = bar.open.to_f32() as f64; // use open of new bar = close of prev bar
        self.flush_pending_horizons(bar_c_prev).await;

        let proc_start = Instant::now();
        let now_ms = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_millis() as i64)
            .unwrap_or(0);
        // delivery_lag: Binance WS delay from theoretical bar close to our receipt
        // processing_ms: our actual compute time (measured at end of fn)
        let delivery_lag_ms = now_ms - bar_close_ms as i64;
        let latency_ms = delivery_lag_ms; // kept for metrics compatibility
        self.metrics.latencies_ms.push(latency_ms);

        // VWAP — reset at UTC midnight
        let day = bar_ms / 86_400_000;
        if day != self.vwap_day {
            self.vwap_cum_pv = 0.0;
            self.vwap_cum_vol = 0.0;
            self.vwap_day = day;
        }
        let h = bar.high.to_f32() as f64;
        let l = bar.low.to_f32() as f64;
        let c = bar.close.to_f32() as f64;
        let vol = bar.volume.total().to_f32_lossy() as f64;
        let typical = (h + l + c) / 3.0;
        if vol > 0.0 {
            self.vwap_cum_pv += typical * vol;
            self.vwap_cum_vol += vol;
            self.vwap_session = Some(self.vwap_cum_pv / self.vwap_cum_vol);
        }

        let bar_buy = self.bar_buy_vol;
        let bar_sell = self.bar_sell_vol;
        let bar_delta = bar_buy - bar_sell;
        self.bar_buy_vol = 0.0;
        self.bar_sell_vol = 0.0;

        self.bars.push_back(bar);
        if self.bars.len() > VP_WINDOW {
            self.bars.pop_front();
        }

        self.cvd_history.push_back(self.cvd);
        if self.cvd_history.len() > CVD_WINDOW {
            self.cvd_history.pop_front();
        }

        let closes: Vec<f64> = self.bars.iter().map(|b| b.close.to_f32() as f64).collect();
        let highs: Vec<f64> = self.bars.iter().map(|b| b.high.to_f32() as f64).collect();
        let lows: Vec<f64> = self.bars.iter().map(|b| b.low.to_f32() as f64).collect();

        let atr = compute_atr(&highs, &lows, &closes, ATR_WINDOW);
        let regime_window = &closes[closes.len().saturating_sub(REGIME_WINDOW)..];
        let regime = derive_regime_with_hysteresis(regime_window, atr, self.last_regime_enum);
        let regime_str = format!("{regime:?}");
        self.notify_regime(&regime_str);

        // Detect regime changes and persist to regime_history
        if self.last_regime.as_deref() != Some(&regime_str) {
            if let Some(sb) = &self.supabase {
                let duration_ms = self.regime_started_at_ms.map(|start| bar_ms - start);
                sb.write_regime_change(bar_ms, &regime_str, &regime_str, &regime_str, duration_ms, c);
            }
            self.regime_started_at_ms = Some(bar_ms);
            self.last_regime = Some(regime_str.clone());
            self.last_regime_enum = regime;
        }
        // Compute slow/fast slopes for diagnostics (mirrors derive_regime internals)
        let slow_slope = compute_ols_slope(regime_window, atr);
        let fast_slope = compute_ols_slope(
            &regime_window[regime_window.len().saturating_sub(5)..],
            atr,
        );
        let cvd_slope = compute_cvd_slope(&self.cvd_history);
        let cvd_divergence = derive_cvd_divergence(&highs, &lows, cvd_slope);

        let (poc, vah, val, hvn_nearby, lvn_nearby) =
            compute_volume_profile(&self.bars, VP_BINS, c);

        let deltas = [bar_delta];
        let (failed_acceptance, footprint_absorption) = derive_failed_acceptance_and_absorption(
            &highs, &lows, &closes, vah, val, &deltas, cvd_slope,
        );

        // Basis: (perp / spot - 1) * 100 in %
        let basis = self.spot_price.and_then(|spot| {
            if spot > 0.0 {
                Some((c / spot - 1.0) * 100.0)
            } else {
                None
            }
        });

        // OI delta: most recent minus oldest in history window
        let oi_delta = if self.oi_history.len() >= 2 {
            let recent = self.oi_history.back().copied().unwrap_or(0.0);
            let old = self.oi_history.front().copied().unwrap_or(0.0);
            Some(recent - old)
        } else {
            None
        };

        // OI momentum alignment: price direction matches OI delta direction
        let oi_momentum_aligned = oi_delta.map(|delta| {
            let bars_back = self.bars.len().saturating_sub(6);
            let px_5bars_ago = self.bars.get(bars_back).map(|b| b.close.to_f32() as f64).unwrap_or(c);
            let price_rising = c > px_5bars_ago;
            // Aligned for long if price rising AND oi growing (new longs)
            // We'll store raw — scoring layer interprets per direction
            price_rising == (delta > 0.0)
        });

        // --- Market structure: MSS, sweep, AVWAP-BOS ---
        // Use a lookback of 10 bars for swing detection.
        const SWING_LB: usize = 10;
        let n = closes.len();
        // Identify the most recent swing high and swing low over [0..n-SWING_LB).
        // A swing high is the max of highs[i-k..i+k] for some k; here we use a
        // simple rolling max over the prior SWING_LB bars (excluding the last bar).
        let (prior_swing_high, prior_swing_low) = if n > SWING_LB {
            let window = &highs[..n - 1]; // exclude the current (last) bar
            let wl = &lows[..n - 1];
            let sh = window.iter().copied().fold(f64::NEG_INFINITY, f64::max);
            let sl = wl.iter().copied().fold(f64::INFINITY, f64::min);
            (sh, sl)
        } else {
            (f64::NEG_INFINITY, f64::INFINITY)
        };

        // MSS: current close breaks above prior swing high (bullish) or
        // below prior swing low (bearish).
        let mss_active = prior_swing_high.is_finite() && prior_swing_low.is_finite()
            && (c > prior_swing_high || c < prior_swing_low);

        // Sweep: in the last 3 bars price pierced a swing extreme intrabar but
        // closed back inside — classic liquidity grab.
        let sweep_confirmed = if n >= 4 && prior_swing_high.is_finite() && prior_swing_low.is_finite() {
            let recent_bars: Vec<_> = self.bars.iter().rev().take(3).collect();
            // Bullish sweep: wick below prior swing low, closed above it
            let bull_sweep = recent_bars.iter().any(|b| {
                (b.low.to_f32() as f64) < prior_swing_low
                    && (b.close.to_f32() as f64) > prior_swing_low
            });
            // Bearish sweep: wick above prior swing high, closed below it
            let bear_sweep = recent_bars.iter().any(|b| {
                (b.high.to_f32() as f64) > prior_swing_high
                    && (b.close.to_f32() as f64) < prior_swing_high
            });
            bull_sweep || bear_sweep
        } else {
            false
        };

        // AVWAP-BOS: anchored VWAP from the most recent Break of Structure bar.
        // O(n) via prefix running max/min — avoids the O(n²) nested scan.
        let avwap_bos: Option<f64> = if n > SWING_LB {
            let bars_vec: Vec<_> = self.bars.iter().collect();
            // Build prefix running max of highs and min of lows (oldest-first).
            // prefix_high[i] = max(highs[0..=i]), prefix_low[i] = min(lows[0..=i]).
            let mut prefix_high = vec![0.0f64; n];
            let mut prefix_low  = vec![0.0f64; n];
            prefix_high[0] = bars_vec[0].high.to_f32() as f64;
            prefix_low[0]  = bars_vec[0].low.to_f32() as f64;
            for i in 1..n {
                prefix_high[i] = prefix_high[i - 1].max(bars_vec[i].high.to_f32() as f64);
                prefix_low[i]  = prefix_low[i - 1].min(bars_vec[i].low.to_f32() as f64);
            }
            // Scan newest→oldest (skip index 0 — needs at least one prior bar).
            // BOS at bar i: close breaks above all-time high of bars[0..i-1]
            //               or below all-time low of bars[0..i-1].
            let bos_idx = (1..n.saturating_sub(1)).rev().find(|&i| {
                let cl       = bars_vec[i].close.to_f32() as f64;
                let ref_high = prefix_high[i - 1];
                let ref_low  = prefix_low[i - 1];
                cl > ref_high || cl < ref_low
            });
            bos_idx.map(|anchor| {
                let (cum_pv, cum_vol) = bars_vec[anchor..].iter().fold((0.0_f64, 0.0_f64), |(pv, v), b| {
                    let bh = b.high.to_f32() as f64;
                    let bl = b.low.to_f32() as f64;
                    let bc = b.close.to_f32() as f64;
                    let bv = b.volume.total().to_f32_lossy() as f64;
                    (pv + (bh + bl + bc) / 3.0 * bv, v + bv)
                });
                if cum_vol > 0.0 { cum_pv / cum_vol } else { 0.0 }
            }).filter(|&v| v > 0.0)
        } else {
            None
        };

        let vwap_ctx = build_vwap_context(c, self.vwap_session, avwap_bos);
        let vp_ctx = build_volume_profile_context(c, poc, vah, val, hvn_nearby, lvn_nearby);
        // Snapshot VP lists before vp_ctx is moved into StrategyMarketContext
        let frozen_hvn = vp_ctx.hvn_nearby.clone();
        let frozen_lvn = vp_ctx.lvn_nearby.clone();
        let ob_ctx = match &self.depth {
            Some(d) => build_orderbook_context(d),
            None => OrderBookContext {
                obi_l5: None,
                obi_l10: None,
                obi_l20: None,
                microprice: None,
                spread_bps: None,
                walls_above: vec![],
                walls_below: vec![],
                thin_zone_above: false,
                thin_zone_below: false,
                quality: DataQuality::Missing,
                spoof: None,
            },
        };

        let bid_wall_nearby = wall_nearby(&ob_ctx.walls_below, c, atr);
        let ask_wall_nearby = wall_nearby(&ob_ctx.walls_above, c, atr);
        let price_action_clean = {
            let recent_5 = &closes[closes.len().saturating_sub(5)..];
            count_price_reversals(recent_5) <= 2
        };

        let flow = build_flow_context(
            Some(self.cvd),
            cvd_slope,
            Some(bar_delta),
            Some(bar_buy),
            Some(bar_sell),
            None,
            failed_acceptance,
            footprint_absorption,
            cvd_divergence,
            self.funding_rate,
            basis,
            oi_delta,
            oi_momentum_aligned,
            bid_wall_nearby,
            ask_wall_nearby,
            price_action_clean,
            mss_active,
            sweep_confirmed,
            Some(fast_slope),
        );

        // Prune stale liquidation events before building the snapshot
        self.liq_tracker.prune(bar_ms);
        let liq_snap = self.liq_tracker.snapshot(bar_ms);
        let ls_snap = self.ls_tracker.snapshot();
        let oi_snap = self.oi_tracker.snapshot();
        let fund_snap = self.funding_tracker.snapshot();

        // Build institutional context — only live once we have at least one L/S
        // fetch (ls_snap defaults to 0.5/0.5 until then, quality = Fallback).
        let inst_quality = if ls_snap.top_traders_long_pct == 0.5
            && ls_snap.retail_long_pct == 0.5
            && fund_snap.current == 0.0
        {
            DataQuality::Fallback
        } else {
            DataQuality::Live
        };

        let institutional = Some(InstitutionalContext {
            timestamp_ms: bar_ms,
            liquidations: liq_snap,
            ls_ratio: ls_snap,
            oi_trend: oi_snap,
            taker_ratio: self.last_taker_ratio.clone(),
            funding: fund_snap,
            quality: inst_quality,
            smart_money_score: None,
            liq_map: None,
        });

        // 20-bar swing high/low for structural target selection.
        const SWING20: usize = 20;
        let (swing_high_20, swing_low_20) = if n >= SWING20 {
            let sh = highs[n - SWING20..n - 1].iter().copied().fold(f64::NEG_INFINITY, f64::max);
            let sl = lows[n - SWING20..n - 1].iter().copied().fold(f64::INFINITY, f64::min);
            (if sh.is_finite() { Some(sh) } else { None },
             if sl.is_finite() { Some(sl) } else { None })
        } else {
            (None, None)
        };

        let ctx = StrategyMarketContext {
            symbol: symbol.to_string(),
            timestamp_ms: bar_ms,
            price: c,
            regime,
            atr: if atr > 0.0 { Some(atr) } else { None },
            volume_profile: vp_ctx,
            vwap: vwap_ctx,
            flow,
            orderbook: ob_ctx,
            institutional,
            swing_high_20,
            swing_low_20,
            market_structure: None,
            session: None,
            order_blocks: None,
            fvg: None,
        };

        let signal = route_strategy(&ctx, &cfg);
        let signal_fired = signal.action == StrategyAction::ShadowSignal;
        if signal_fired {
            self.metrics.signals_today += 1;
            self.metrics.bars_since_signal = 0;
            self.push_outcome(&signal, OutcomeSource::BarClose, now_ms);
        } else {
            self.metrics.bars_since_signal += 1;
        }

        // Resolve outcomes that are at least one bar (5m) old
        self.resolve_outcomes(bar_ms, c);

        let near_misses = collect_near_misses(&ctx, &cfg, signal_fired);

        // Build compact skip summary for [bar] log (only when no signal fired)
        let skip_str: String = if signal_fired || near_misses.is_empty() {
            String::new()
        } else {
            near_misses.iter()
                .filter(|nm| !nm.blocked.is_empty())
                .map(|nm| {
                    let abbr = match nm.detector {
                        "VwapValuePullbackContinuation" => "VWAP",
                        "LvnLiquidityVacuumBreakout"   => "LVN",
                        "ValueAreaFailedAuction"        => "VAFA",
                        "LiquidationHunt"               => "LIQ",
                        "FundingExhaustionReversal"     => "FUND",
                        "SmartMoneyDivergence"          => "SMD",
                        other => other,
                    };
                    let top = nm.blocked.iter().take(2).cloned().collect::<Vec<_>>().join("+");
                    format!("{abbr}/{}:{top}", &nm.side[..1])
                })
                .collect::<Vec<_>>()
                .join(" ")
        };

        for nm in &near_misses {
            if let Ok(json) = serde_json::to_string(nm) {
                println!("{{\"event\":\"near_miss\",\"data\":{json}}}");
            }
        }

        // Collect UUID from the previous bar's in-flight write (non-blocking try_recv).
        if let Some(mut rx) = self.pending_uuid_rx.take() {
            match rx.try_recv() {
                Ok(uuid) => self.pending_signal_uuid = uuid,
                Err(tokio::sync::oneshot::error::TryRecvError::Empty) => {
                    // Write still in flight — trade linker will miss this one, acceptable.
                    eprintln!("[supabase] UUID not ready by next bar — trade won't be linked");
                }
                Err(_) => {} // sender dropped (write failed), uuid stays None
            }
        }

        if signal_fired {
            if let Ok(json) = serde_json::to_string(&signal) {
                println!("{{\"event\":\"signal\",\"data\":{json}}}");
            }
            // Spawn write_signal off the hot path — send UUID back via oneshot when done.
            if let Some(sb) = self.supabase.clone() {
                let (tx, rx) = tokio::sync::oneshot::channel();
                self.pending_uuid_rx = Some(rx);
                self.pending_signal_uuid = None; // cleared until write completes
                let signal_clone = signal.clone();
                let ctx_clone = ctx.clone();
                tokio::spawn(async move {
                    let uuid = sb.write_signal(&signal_clone, &ctx_clone).await;
                    let _ = tx.send(uuid);
                });
            }
        }

        let prev_closed = self.paper.closed_trades.len();
        let paper_signal = if signal_fired { Some(&signal) } else { None };
        self.paper
            .on_bar_close(symbol, c, h, l, bar_ms, paper_signal, Some(&ctx));

        for trade in self.paper.closed_trades[prev_closed..].iter() {
            if let Ok(json) = serde_json::to_string(trade) {
                println!("{{\"event\":\"trade_closed\",\"data\":{json}}}");
            }
            if let Some(sb) = &self.supabase {
                // Pass the UUID of the signal that opened this position
                sb.write_trade(trade, self.pending_signal_uuid.clone());
            }
            // Register trade for forward horizon tracking (price_5m, price_15m, etc.)
            if let Some(uuid) = &self.pending_signal_uuid {
                if let Some(stop) = trade.stop_price {
                    let h = PendingHorizon {
                        signal_uuid: uuid.clone(),
                        entry_price: trade.entry_price,
                        stop_price: stop,
                        side: trade.side.clone(),
                        close_bar_n: self.bar_counter,
                    };
                    eprintln!(
                        "[horizon_pending] uuid={} close_bar={} entry={} stop={} side={}",
                        h.signal_uuid, h.close_bar_n, h.entry_price, h.stop_price, h.side
                    );
                    self.pending_horizons.push(h);
                }
            }
        }

        let missing_str = signal
            .missing
            .iter()
            .map(|m| format!("{m:?}"))
            .collect::<Vec<_>>()
            .join(",");

        let processing_ms = proc_start.elapsed().as_millis();
        let inst_ref = ctx.institutional.as_ref();
        let (inst_ok, inst_total) = self.freshness.quality();
        let inst_label = if inst_ref.is_none() {
            "null".to_string()
        } else if inst_ok == inst_total {
            format!("Live({inst_ok}/{inst_total})")
        } else if inst_ok > 0 {
            format!("Partial({inst_ok}/{inst_total})")
        } else {
            format!("Stale(0/{inst_total})")
        };
        let ws_label = format!(
            "kline:{} depth:{} trades:ok liq:{}",
            self.streams.klines, self.streams.depth, self.streams.liq
        );
        let liq_age = self.freshness.liq_age_str();
        eprintln!(
            "[bar] ts={bar_ms} close={c:.2} regime={regime:?} \
             slow={slow_slope:.3} fast={fast_slope:.3} \
             funding={:.4} basis={:.3}% oi_delta={:.0} \
             vwap={:.2} cvd={:.1} ob={} \
             inst={inst_label} ls_top={:.1}%/{:.1}% liq={:.0}$ liq_age={liq_age} \
             ws=[{ws_label}] \
             action={:?} score={:.3} delivery={delivery_lag_ms}ms proc={processing_ms}ms equity={:.2} \
             missing=[{missing_str}] skip=[{skip_str}]",
            self.funding_rate.unwrap_or(0.0) * 10_000.0,
            basis.unwrap_or(0.0),
            oi_delta.unwrap_or(0.0),
            self.vwap_session.unwrap_or(0.0),
            self.cvd,
            if self.depth.is_some() { "live" } else { "miss" },
            inst_ref.map(|i| i.ls_ratio.top_traders_long_pct * 100.0).unwrap_or(0.0),
            inst_ref.map(|i| i.ls_ratio.retail_long_pct * 100.0).unwrap_or(0.0),
            inst_ref.map(|i| i.liquidations.total_usd_5m).unwrap_or(0.0),
            signal.action,
            signal.score,
            self.paper.equity,
        );

        // Freeze bar-level context for intrabar evaluation during the next bar
        self.frozen = Some(FrozenBarCtx {
            regime,
            fast_slope,
            atr,
            poc,
            vah,
            val,
            hvn_nearby: frozen_hvn,
            lvn_nearby: frozen_lvn,
            vwap_session: self.vwap_session,
            avwap_bos,
            cvd_slope,
            failed_acceptance,
            footprint_absorption,
            cvd_divergence,
            mss_active,
            sweep_confirmed,
            price_action_clean,
            bid_wall_nearby,
            ask_wall_nearby,
            swing_high_20,
            swing_low_20,
            oi_momentum_aligned,
            basis,
            bar_close_ms: bar_ms,
            bar_close_price: c,
        });
        self.bar_counter += 1;
        self.intrabar_signal_fired = false;
        self.intrabar_eval_count = 0;
        self.last_intrabar_eval_price = c;
        self.last_intrabar_eval_ms = bar_ms;

        self.metrics.processing_ms.push(processing_ms);
        if self.metrics.bars_processed % 10 == 0 {
            let freshness = &self.freshness;
            let streams = &self.streams;
            self.metrics.report(freshness, streams);
        }
    }
}

// ── volume profile ───────────────────────────────────────────────────────────

fn compute_volume_profile(
    bars: &VecDeque<Kline>,
    n_bins: usize,
    current_price: f64,
) -> (Option<f64>, Option<f64>, Option<f64>, Vec<f64>, Vec<f64>) {
    if bars.is_empty() {
        return (None, None, None, vec![], vec![]);
    }

    let price_high = bars
        .iter()
        .map(|b| b.high.to_f32() as f64)
        .fold(f64::NEG_INFINITY, f64::max);
    let price_low = bars
        .iter()
        .map(|b| b.low.to_f32() as f64)
        .fold(f64::INFINITY, f64::min);
    let range = price_high - price_low;
    if range <= 0.0 {
        return (None, None, None, vec![], vec![]);
    }

    let bin_size = range / n_bins as f64;
    let mut histogram = vec![0.0f64; n_bins];

    for bar in bars.iter() {
        let bh = bar.high.to_f32() as f64;
        let bl = bar.low.to_f32() as f64;
        let vol = bar.volume.total().to_f32_lossy() as f64;
        if vol <= 0.0 {
            continue;
        }
        let lo_bin = ((bl - price_low) / bin_size).floor() as usize;
        let hi_bin = ((bh - price_low) / bin_size).floor() as usize;
        let lo_bin = lo_bin.min(n_bins - 1);
        let hi_bin = hi_bin.min(n_bins - 1);
        let n_covered = (hi_bin - lo_bin + 1) as f64;
        let vol_per_bin = vol / n_covered;
        for b in lo_bin..=hi_bin {
            histogram[b] += vol_per_bin;
        }
    }

    let poc_bin = histogram
        .iter()
        .enumerate()
        .max_by(|(_, a), (_, b)| a.partial_cmp(b).unwrap())
        .map(|(i, _)| i)
        .unwrap_or(0);
    let poc = Some(price_low + (poc_bin as f64 + 0.5) * bin_size);

    let total_vol: f64 = histogram.iter().sum();
    let value_target = total_vol * 0.70;
    let mut va_vol = histogram[poc_bin];
    let mut lo = poc_bin;
    let mut hi = poc_bin;
    while va_vol < value_target && (lo > 0 || hi + 1 < n_bins) {
        let add_above = if hi + 1 < n_bins {
            histogram[hi + 1]
        } else {
            0.0
        };
        let add_below = if lo > 0 { histogram[lo - 1] } else { 0.0 };
        if add_above >= add_below && hi + 1 < n_bins {
            hi += 1;
            va_vol += histogram[hi];
        } else if lo > 0 {
            lo -= 1;
            va_vol += histogram[lo];
        } else if hi + 1 < n_bins {
            hi += 1;
            va_vol += histogram[hi];
        } else {
            break;
        }
    }
    let vah = Some(price_low + (hi as f64 + 1.0) * bin_size);
    let val = Some(price_low + lo as f64 * bin_size);

    let avg_vol = total_vol / n_bins as f64;
    let nearby_range = current_price * 0.05;
    let mut hvn_nearby = vec![];
    let mut lvn_nearby = vec![];
    for (i, &bvol) in histogram.iter().enumerate() {
        let bin_price = price_low + (i as f64 + 0.5) * bin_size;
        if (bin_price - current_price).abs() <= nearby_range {
            if bvol > avg_vol * 2.0 {
                hvn_nearby.push(bin_price);
            } else if bvol < avg_vol * 0.3 {
                lvn_nearby.push(bin_price);
            }
        }
    }

    (poc, vah, val, hvn_nearby, lvn_nearby)
}

// ── OLS slope / ATR (mirrors adapter::ols_slope_per_atr for diagnostics) ─────

fn compute_ols_slope(closes: &[f64], atr: f64) -> f64 {
    if closes.len() < 2 || atr <= 0.0 {
        return 0.0;
    }
    let n = closes.len() as f64;
    let sum_x: f64 = (0..closes.len()).map(|i| i as f64).sum();
    let sum_y: f64 = closes.iter().sum();
    let sum_xy: f64 = closes
        .iter()
        .enumerate()
        .map(|(i, y)| i as f64 * y)
        .sum();
    let sum_x2: f64 = (0..closes.len()).map(|i| (i * i) as f64).sum();
    let denom = n * sum_x2 - sum_x * sum_x;
    if denom.abs() < 1e-10 {
        return 0.0;
    }
    (n * sum_xy - sum_x * sum_y) / denom / atr
}

// ── ATR ───────────────────────────────────────────────────────────────────────

fn compute_atr(highs: &[f64], lows: &[f64], closes: &[f64], period: usize) -> f64 {
    if highs.len() < 2 || period == 0 {
        return 0.0;
    }
    let n = highs.len().min(period + 1);
    let start = highs.len() - n;
    let trs: Vec<f64> = (start + 1..highs.len())
        .map(|i| {
            let prev_close = closes[i - 1];
            (highs[i] - lows[i])
                .max((highs[i] - prev_close).abs())
                .max((lows[i] - prev_close).abs())
        })
        .collect();
    if trs.is_empty() {
        0.0
    } else {
        trs.iter().sum::<f64>() / trs.len() as f64
    }
}

// ── CVD slope ─────────────────────────────────────────────────────────────────

fn compute_cvd_slope(history: &VecDeque<f64>) -> Option<f64> {
    let n = history.len();
    if n < 5 {
        return None;
    }
    let window = 10.min(n);
    let vals: Vec<f64> = history
        .iter()
        .rev()
        .take(window)
        .cloned()
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
        .collect();
    let n_f = vals.len() as f64;
    let sum_x: f64 = (0..vals.len()).map(|i| i as f64).sum();
    let sum_y: f64 = vals.iter().sum();
    let sum_xy: f64 = vals.iter().enumerate().map(|(i, y)| i as f64 * y).sum();
    let sum_x2: f64 = (0..vals.len()).map(|i| (i * i) as f64).sum();
    let denom = n_f * sum_x2 - sum_x * sum_x;
    if denom.abs() < 1e-10 {
        return None;
    }
    Some((n_f * sum_xy - sum_x * sum_y) / denom)
}

// ── REST fetch helpers ────────────────────────────────────────────────────────

/// Fetches funding rate and mark price from Binance FAPI premiumIndex.
/// Returns (funding_rate, mark_price). Both are None on failure.
async fn fetch_premium_index(symbol: &str) -> (Option<f64>, Option<f64>) {
    let url = format!(
        "https://fapi.binance.com/fapi/v1/premiumIndex?symbol={}",
        symbol
    );
    let resp = match reqwest::get(&url).await {
        Ok(r) => r,
        Err(e) => {
            eprintln!("[fetch] premiumIndex failed: {e}");
            return (None, None);
        }
    };
    let json: serde_json::Value = match resp.json().await {
        Ok(v) => v,
        Err(e) => {
            eprintln!("[fetch] premiumIndex parse failed: {e}");
            return (None, None);
        }
    };
    let funding = json
        .get("lastFundingRate")
        .and_then(|v| v.as_str())
        .and_then(|s| s.parse::<f64>().ok());
    let mark = json
        .get("markPrice")
        .and_then(|v| v.as_str())
        .and_then(|s| s.parse::<f64>().ok());
    (funding, mark)
}

/// Fetches spot price from Binance REST API.
async fn fetch_spot_price(symbol: &str) -> Option<f64> {
    let url = format!(
        "https://api.binance.com/api/v3/ticker/price?symbol={}",
        symbol
    );
    let resp = reqwest::get(&url).await.ok()?;
    let json: serde_json::Value = resp.json().await.ok()?;
    json.get("price")?.as_str()?.parse::<f64>().ok()
}

/// Fetches open interest (in contracts) from Binance FAPI.
async fn fetch_open_interest(symbol: &str) -> Option<f64> {
    let url = format!(
        "https://fapi.binance.com/fapi/v1/openInterest?symbol={}",
        symbol
    );
    let resp = reqwest::get(&url).await.ok()?;
    let json: serde_json::Value = resp.json().await.ok()?;
    json.get("openInterest")?.as_str()?.parse::<f64>().ok()
}

/// Fetches top-trader long/short position ratio from Binance FAPI.
async fn fetch_top_trader_ls(symbol: &str) -> Option<LongShortSnapshot> {
    let url = format!(
        "https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol={}&period=5m&limit=1",
        symbol
    );
    let resp = reqwest::get(&url).await.ok()?;
    let json: serde_json::Value = resp.json().await.ok()?;
    let entry = json.as_array()?.first()?;
    let long_ratio: f64 = entry.get("longAccount")?.as_str()?.parse().ok()?;
    let short_ratio: f64 = entry.get("shortAccount")?.as_str()?.parse().ok()?;
    let ls_ratio: f64 = entry.get("longShortRatio")?.as_str()?.parse().ok()?;
    let ts: i64 = entry.get("timestamp")?.as_i64()?;
    Some(LongShortSnapshot { timestamp_ms: ts, long_ratio, short_ratio, ls_ratio, source: LsSource::TopTraderPosition })
}

/// Fetches global account long/short ratio from Binance FAPI (retail proxy).
async fn fetch_global_ls(symbol: &str) -> Option<LongShortSnapshot> {
    let url = format!(
        "https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={}&period=5m&limit=1",
        symbol
    );
    let resp = reqwest::get(&url).await.ok()?;
    let json: serde_json::Value = resp.json().await.ok()?;
    let entry = json.as_array()?.first()?;
    let long_ratio: f64 = entry.get("longAccount")?.as_str()?.parse().ok()?;
    let short_ratio: f64 = entry.get("shortAccount")?.as_str()?.parse().ok()?;
    let ls_ratio: f64 = entry.get("longShortRatio")?.as_str()?.parse().ok()?;
    let ts: i64 = entry.get("timestamp")?.as_i64()?;
    Some(LongShortSnapshot { timestamp_ms: ts, long_ratio, short_ratio, ls_ratio, source: LsSource::GlobalAccount })
}

/// Fetches taker buy/sell volume ratio from Binance FAPI.
async fn fetch_taker_ratio(symbol: &str) -> Option<TakerRatioSnapshot> {
    let url = format!(
        "https://fapi.binance.com/futures/data/takerlongshortRatio?symbol={}&period=5m&limit=1",
        symbol
    );
    let resp = reqwest::get(&url).await.ok()?;
    let json: serde_json::Value = resp.json().await.ok()?;
    let entry = json.as_array()?.first()?;
    let buy_sell_ratio: f64 = entry.get("buySellRatio")?.as_str()?.parse().ok()?;
    let buy_vol: f64 = entry.get("buyVol")?.as_str()?.parse().ok()?;
    let sell_vol: f64 = entry.get("sellVol")?.as_str()?.parse().ok()?;
    let ts: i64 = entry.get("timestamp")?.as_i64()?;
    let total = buy_vol + sell_vol;
    let taker_imbalance = if total > 0.0 { (buy_vol - sell_vol) / total } else { 0.0 };
    Some(TakerRatioSnapshot { timestamp_ms: ts, buy_sell_ratio, taker_imbalance })
}

// ── forceOrder WebSocket stream ───────────────────────────────────────────────
// Binance @forceOrder is a PUBLIC stream — no API key needed.
// The old REST /fapi/v1/forceOrders endpoint requires USER_DATA auth (signature),
// which caused liq=0$ on every bar. This stream replaces it.

static LIQ_TLS: LazyLock<TlsConnector> = LazyLock::new(|| {
    let _ = aws_lc_rs::default_provider().install_default();
    let root_store = RootCertStore { roots: webpki_roots::TLS_SERVER_ROOTS.to_vec() };
    let cfg = ClientConfig::builder()
        .with_root_certificates(root_store)
        .with_no_client_auth();
    TlsConnector::from(std::sync::Arc::new(cfg))
});

async fn connect_force_order_ws(
    symbol: &str,
) -> Result<FragmentCollector<TokioIo<Upgraded>>, Box<dyn std::error::Error + Send + Sync>> {
    let domain = "fstream.binance.com";
    let path = format!("/ws/{}@forceOrder", symbol.to_lowercase());
    let addr = format!("{domain}:443");

    let tcp = tokio::net::TcpStream::connect(&addr).await?;
    let sn = ServerName::try_from(domain.to_string())?;
    let tls = LIQ_TLS.connect(sn, tcp).await?;

    let req: Request<Empty<Bytes>> = Request::builder()
        .method("GET")
        .uri(&path)
        .header("Host", domain)
        .header(UPGRADE, "websocket")
        .header(CONNECTION, "upgrade")
        .header("Sec-WebSocket-Key", fastwebsockets::handshake::generate_key())
        .header("Sec-WebSocket-Version", "13")
        .body(Empty::<Bytes>::new())?;

    let (ws, _) = fastwebsockets::handshake::client(&TokioExecutor::new(), req, tls).await?;
    Ok(FragmentCollector::new(ws))
}

fn parse_force_order_event(text: &str) -> Option<LiquidationEvent> {
    let v: serde_json::Value = serde_json::from_str(text).ok()?;
    let o = v.get("o")?;
    let side_str = o.get("S")?.as_str()?;
    // In Binance force orders the order side closes the position:
    // SELL = long was liquidated, BUY = short was liquidated.
    let liq_side = match side_str {
        "SELL" => LiqSide::Longs,
        "BUY"  => LiqSide::Shorts,
        _      => LiqSide::Neutral,
    };
    let qty: f64 = o.get("z")?.as_str()?.parse().ok()?;
    let price: f64 = o.get("ap")?.as_str()?.parse().ok()?;
    let ts: i64 = o.get("T")?.as_i64()?;
    Some(LiquidationEvent { timestamp_ms: ts, side: liq_side, quantity_usd: qty * price })
}

/// Spawns a task that streams @forceOrder events into `tx` with auto-reconnect.
/// Sends `true` to `health_tx` on connect and `false` on disconnect.
fn spawn_force_order_stream(
    symbol: String,
    tx: tokio::sync::mpsc::Sender<Vec<LiquidationEvent>>,
    health_tx: tokio::sync::mpsc::Sender<bool>,
) {
    tokio::spawn(async move {
        loop {
            match connect_force_order_ws(&symbol).await {
                Err(e) => {
                    eprintln!("[liq] forceOrder WS connect failed: {e} — retry in 5s");
                    let _ = health_tx.send(false).await;
                    tokio::time::sleep(Duration::from_secs(5)).await;
                }
                Ok(mut ws) => {
                    eprintln!("[liq] forceOrder WS connected for {symbol}");
                    let _ = health_tx.send(true).await;
                    loop {
                        match ws.read_frame().await {
                            Err(e) => {
                                eprintln!("[liq] forceOrder WS read error: {e} — reconnecting");
                                let _ = health_tx.send(false).await;
                                break;
                            }
                            Ok(frame) => {
                                if frame.opcode == OpCode::Close {
                                    let _ = health_tx.send(false).await;
                                    break;
                                }
                                if frame.opcode != OpCode::Text { continue; }
                                let text = match std::str::from_utf8(&frame.payload) {
                                    Ok(s) => s,
                                    Err(_) => continue,
                                };
                                if let Some(ev) = parse_force_order_event(text) {
                                    let _ = tx.send(vec![ev]).await;
                                }
                            }
                        }
                    }
                }
            }
        }
    });
}

/// Fetches recent funding rate history for the FundingTracker (last 21 samples).
async fn fetch_funding_history(symbol: &str) -> Vec<FundingRateSample> {
    let url = format!(
        "https://fapi.binance.com/fapi/v1/fundingRate?symbol={}&limit=21",
        symbol
    );
    let resp = match reqwest::get(&url).await {
        Ok(r) => r,
        Err(e) => { eprintln!("[fetch] fundingRate history failed: {e}"); return vec![]; }
    };
    let json: serde_json::Value = match resp.json().await {
        Ok(v) => v,
        Err(e) => { eprintln!("[fetch] fundingRate history parse failed: {e}"); return vec![]; }
    };
    json.as_array()
        .map(|arr| {
            arr.iter()
                .filter_map(|entry| {
                    let rate: f64 = entry.get("fundingRate")?.as_str()?.parse().ok()?;
                    let ts: i64 = entry.get("fundingTime")?.as_i64()?;
                    Some(FundingRateSample { timestamp_ms: ts, rate })
                })
                .collect()
        })
        .unwrap_or_default()
}

/// Fetches `limit` historical closed klines from Binance FAPI REST and seeds BarState
/// with them so ATR, VWAP, VP, and regime are warm before the first live bar is processed.
/// Signals are NOT emitted for historical bars — Supabase writes are suppressed.
async fn warm_up_history(state: &mut BarState, symbol: &str, tf_min: u64, limit: usize) {
    let interval_str = match tf_min {
        1 => "1m", 3 => "3m", 5 => "5m", 15 => "15m", 30 => "30m", 60 => "1h", _ => "5m",
    };
    let url = format!(
        "https://fapi.binance.com/fapi/v1/klines?symbol={}&interval={}&limit={}",
        symbol, interval_str, limit + 1 // +1 so we skip the current (open) bar
    );
    let resp = match reqwest::get(&url).await {
        Ok(r) => r,
        Err(e) => { eprintln!("[warmup] klines fetch failed: {e}"); return; }
    };
    let json: serde_json::Value = match resp.json().await {
        Ok(v) => v,
        Err(e) => { eprintln!("[warmup] klines parse failed: {e}"); return; }
    };
    let arr = match json.as_array() {
        Some(a) if a.len() > 1 => a,
        _ => { eprintln!("[warmup] klines response unexpected shape"); return; }
    };

    // Skip last entry — it's the still-open current bar
    let closed = &arr[..arr.len() - 1];
    eprintln!("[warmup] seeding {} historical bars ({})", closed.len(), interval_str);

    for entry in closed {
        let arr = match entry.as_array() {
            Some(a) if a.len() >= 10 => a,
            _ => continue,
        };
        let open_ms: i64  = arr[0].as_i64().unwrap_or(0);
        let high: f64     = arr[2].as_str().and_then(|s| s.parse().ok()).unwrap_or(0.0);
        let low: f64      = arr[3].as_str().and_then(|s| s.parse().ok()).unwrap_or(0.0);
        let close: f64    = arr[4].as_str().and_then(|s| s.parse().ok()).unwrap_or(0.0);
        let volume: f64   = arr[5].as_str().and_then(|s| s.parse().ok()).unwrap_or(0.0);
        let taker_buy_vol: f64 = arr[9].as_str().and_then(|s| s.parse().ok()).unwrap_or(0.0);

        if close <= 0.0 || volume <= 0.0 { continue; }

        // Update VWAP accumulators
        let day = open_ms / 86_400_000;
        if day != state.vwap_day {
            state.vwap_cum_pv = 0.0;
            state.vwap_cum_vol = 0.0;
            state.vwap_day = day;
        }
        let typical = (high + low + close) / 3.0;
        state.vwap_cum_pv += typical * volume;
        state.vwap_cum_vol += volume;
        state.vwap_session = Some(state.vwap_cum_pv / state.vwap_cum_vol);

        // Accumulate CVD (taker_buy_vol from REST; taker_sell = vol - taker_buy)
        let taker_sell_vol = (volume - taker_buy_vol).max(0.0);
        state.cvd += taker_buy_vol - taker_sell_vol;
        state.cvd_history.push_back(state.cvd);
        if state.cvd_history.len() > CVD_WINDOW {
            state.cvd_history.pop_front();
        }

        // Build a synthetic Kline and push it into the bar history
        use exchange::{UnixMs, unit::{Price, Qty}};
        let bar = Kline {
            time:  UnixMs(open_ms as u64),
            open:  Price::from_f32(close as f32),
            high:  Price::from_f32(high as f32),
            low:   Price::from_f32(low as f32),
            close: Price::from_f32(close as f32),
            volume: Volume::TotalOnly(Qty::from_f32(volume as f32)),
            is_closed: true,
        };
        state.bars.push_back(bar);
        if state.bars.len() > VP_WINDOW {
            state.bars.pop_front();
        }
    }

    // Prime last_regime_enum so hysteresis starts with the correct state
    let closes: Vec<f64> = state.bars.iter().map(|b| b.close.to_f32() as f64).collect();
    let highs:  Vec<f64> = state.bars.iter().map(|b| b.high.to_f32() as f64).collect();
    let lows:   Vec<f64> = state.bars.iter().map(|b| b.low.to_f32() as f64).collect();
    let atr = compute_atr(&highs, &lows, &closes, ATR_WINDOW);
    let regime_window = &closes[closes.len().saturating_sub(REGIME_WINDOW)..];
    let regime = derive_regime(regime_window, atr);
    state.last_regime_enum = regime;
    state.last_regime = Some(format!("{regime:?}"));
    eprintln!("[warmup] complete — {} bars loaded, atr={atr:.2}, regime={regime:?}", state.bars.len());
}

// ── main ──────────────────────────────────────────────────────────────────────

#[tokio::main]
async fn main() {
    eprintln!("monitor: starting up");

    let symbol_str = std::env::var("SYMBOL").unwrap_or_else(|_| "BTCUSDT".to_string());
    let tf_min: u64 = std::env::var("TIMEFRAME_MIN")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(5);

    let timeframe = match tf_min {
        1 => Timeframe::M1,
        3 => Timeframe::M3,
        5 => Timeframe::M5,
        15 => Timeframe::M15,
        30 => Timeframe::M30,
        60 => Timeframe::H1,
        _ => {
            eprintln!("monitor: unsupported TIMEFRAME_MIN={tf_min}, defaulting to 5m");
            Timeframe::M5
        }
    };

    let handles = AdapterHandles::spawn_selected(AdapterNetworkConfig::default(), [Venue::Binance])
        .expect("monitor: failed to spawn Binance adapter");

    eprintln!("monitor: fetching {symbol_str} LinearPerps metadata…");
    let metadata = handles
        .fetch_ticker_metadata(Venue::Binance, &[MarketKind::LinearPerps])
        .await
        .expect("monitor: metadata fetch failed");

    let ticker_info: TickerInfo = metadata
        .iter()
        .find_map(|(ticker, info_opt)| {
            if ticker.to_string().eq_ignore_ascii_case(&symbol_str) {
                *info_opt
            } else {
                None
            }
        })
        .unwrap_or_else(|| {
            eprintln!("monitor: {symbol_str} not found in metadata, using fallback TickerInfo");
            let ticker = Ticker::new(&symbol_str, Exchange::BinanceLinear);
            TickerInfo::new(ticker, 0.1, 0.001, None)
        });

    eprintln!("monitor: streaming {symbol_str} at {timeframe}");

    let kline_stream = handles.kline_stream(&StreamConfig::new(
        vec![(ticker_info, timeframe)],
        Exchange::BinanceLinear,
        None,
        PushFrequency::ServerDefault,
    ));

    let depth_stream = handles.depth_stream(&StreamConfig::new(
        ticker_info,
        Exchange::BinanceLinear,
        None,
        PushFrequency::ServerDefault,
    ));

    let trade_stream = handles.trade_stream(&StreamConfig::new(
        vec![ticker_info],
        Exchange::BinanceLinear,
        None,
        PushFrequency::ServerDefault,
    ));

    let mut kline_stream = Box::pin(kline_stream);
    let mut depth_stream = Box::pin(depth_stream);
    let mut trade_stream = Box::pin(trade_stream);

    // ── Supabase writer (replaces MongoDB) ───────────────────────────────────
    let supabase = SupabaseWriter::from_env();
    if supabase.is_none() {
        eprintln!("[supabase] SUPABASE_URL not set — signals/trades logged to stdout only");
    }

    let mut state = BarState::new(supabase);
    state.intrabar_cfg.log_boot();

    // Seed bar history from REST before the live stream starts
    warm_up_history(&mut state, &symbol_str, tf_min, 50).await;

    let tf_ms = timeframe.to_milliseconds();
    let mut pending: Option<(u64, Kline)> = None;

    let metrics_interval = Duration::from_secs(60);
    let mut last_metrics_print = Instant::now();

    // ── Periodic REST fetch tasks ─────────────────────────────────────────────

    // funding + spot: every 60s
    let (funding_tx, mut funding_rx) = tokio::sync::mpsc::channel::<(Option<f64>, Option<f64>)>(4);
    let funding_symbol = symbol_str.clone();
    tokio::spawn(async move {
        let mut interval = tokio::time::interval(Duration::from_secs(60));
        loop {
            interval.tick().await;
            let result = fetch_premium_index(&funding_symbol).await;
            let _ = funding_tx.send(result).await;
        }
    });

    // spot price: every 30s
    let (spot_tx, mut spot_rx) = tokio::sync::mpsc::channel::<Option<f64>>(4);
    let spot_symbol = symbol_str.clone();
    tokio::spawn(async move {
        let mut interval = tokio::time::interval(Duration::from_secs(30));
        loop {
            interval.tick().await;
            let price = fetch_spot_price(&spot_symbol).await;
            let _ = spot_tx.send(price).await;
        }
    });

    // open interest: every 5 min
    let (oi_tx, mut oi_rx) = tokio::sync::mpsc::channel::<Option<f64>>(4);
    let oi_symbol = symbol_str.clone();
    tokio::spawn(async move {
        let mut interval = tokio::time::interval(Duration::from_secs(300));
        loop {
            interval.tick().await;
            let oi = fetch_open_interest(&oi_symbol).await;
            let _ = oi_tx.send(oi).await;
        }
    });

    // ── Institutional REST fetch tasks ────────────────────────────────────────

    // Liquidations: real-time via @forceOrder WebSocket (public stream, no auth needed)
    let (liq_tx, mut liq_rx) = tokio::sync::mpsc::channel::<Vec<LiquidationEvent>>(16);
    let (liq_health_tx, mut liq_health_rx) = tokio::sync::mpsc::channel::<bool>(4);
    spawn_force_order_stream(symbol_str.clone(), liq_tx, liq_health_tx);

    // L/S ratios (top traders + global): every 5 min
    type LsPayload = (Option<LongShortSnapshot>, Option<LongShortSnapshot>);
    let (ls_tx, mut ls_rx) = tokio::sync::mpsc::channel::<LsPayload>(4);
    let ls_symbol = symbol_str.clone();
    tokio::spawn(async move {
        let mut interval = tokio::time::interval(Duration::from_secs(300));
        loop {
            interval.tick().await;
            let top = fetch_top_trader_ls(&ls_symbol).await;
            let global = fetch_global_ls(&ls_symbol).await;
            let _ = ls_tx.send((top, global)).await;
        }
    });

    // Taker buy/sell ratio: every 5 min
    let (taker_tx, mut taker_rx) = tokio::sync::mpsc::channel::<Option<TakerRatioSnapshot>>(4);
    let taker_symbol = symbol_str.clone();
    tokio::spawn(async move {
        let mut interval = tokio::time::interval(Duration::from_secs(300));
        loop {
            interval.tick().await;
            let snap = fetch_taker_ratio(&taker_symbol).await;
            let _ = taker_tx.send(snap).await;
        }
    });

    // Funding rate history: once at startup to seed the FundingTracker percentile
    let (funding_hist_tx, mut funding_hist_rx) = tokio::sync::mpsc::channel::<Vec<FundingRateSample>>(2);
    let fh_symbol = symbol_str.clone();
    tokio::spawn(async move {
        let samples = fetch_funding_history(&fh_symbol).await;
        let _ = funding_hist_tx.send(samples).await;
    });

    // Trigger config reloads whenever the regime changes (sent from on_bar_close).
    // The reload HTTP fetch is done in a spawned task — result comes back via cfg_rx.
    let (regime_tx, mut regime_rx) = tokio::sync::mpsc::channel::<String>(8);
    let (cfg_tx, mut cfg_rx) = tokio::sync::mpsc::channel::<data::strategy::types::StrategyConfig>(4);
    state.regime_tx = Some(regime_tx);

    loop {
        tokio::select! {
            Some(event) = kline_stream.next() => {
                state.metrics.kline_ticks += 1;

                match event {
                    Event::KlineReceived(_kind, kline) => {
                        let open_ms = kline.time.as_u64();

                        match pending {
                            None => {
                                pending = Some((open_ms, kline));
                            }
                            Some((prev_open, _)) if open_ms > prev_open => {
                                let (closed_open_ms, closed_kline) = pending.take().unwrap();
                                let bar_close_ms = closed_open_ms + tf_ms;
                                state.on_bar_close(closed_kline, bar_close_ms, &symbol_str).await;
                                pending = Some((open_ms, kline));
                            }
                            Some((prev_open, _)) if open_ms == prev_open => {
                                if kline.is_closed {
                                    pending = None;
                                    let bar_close_ms = open_ms + tf_ms;
                                    state.on_bar_close(kline, bar_close_ms, &symbol_str).await;
                                } else {
                                    pending = Some((open_ms, kline));
                                }
                            }
                            _ => {}
                        }
                    }
                    Event::Connected(ex) => {
                        eprintln!("[kline] connected ({ex:?})");
                        state.streams.klines = StreamHealth::Ok;
                    }
                    Event::Disconnected(ex, reason) => {
                        eprintln!("[kline] DISCONNECTED ({ex:?}): {reason}");
                        state.streams.klines = StreamHealth::Reconnecting;
                        state.metrics.ws_reconnects += 1;
                    }
                    _ => {}
                }
            }

            Some(event) = depth_stream.next() => {
                match event {
                    Event::DepthReceived(_kind, _ts, depth_arc) => {
                        state.streams.depth = StreamHealth::Ok;
                        state.on_depth((*depth_arc).clone());
                    }
                    Event::Connected(_) => { state.streams.depth = StreamHealth::Ok; }
                    Event::Disconnected(_, _) => {
                        state.streams.depth = StreamHealth::Reconnecting;
                        state.metrics.ws_reconnects += 1;
                    }
                    _ => {}
                }
            }

            Some(event) = trade_stream.next() => {
                if let Event::TradesReceived(_kind, ts, trades) = event {
                    state.on_trade_batch();
                    let now_ms = ts.as_u64() as i64;
                    for trade in trades.iter() {
                        state.on_trade(trade.is_sell, trade.qty.to_f32_lossy(), trade.price.to_f32() as f64);
                    }
                    if !state.pending_outcomes.is_empty() && state.current_price > 0.0 {
                        state.update_outcomes(state.current_price, now_ms);
                    }
                    if state.intrabar_cfg.enabled && state.should_eval_intrabar(now_ms) {
                        state.on_intrabar_tick(now_ms, "price_move", &symbol_str).await;
                    }
                }
            }

            Some((funding, mark_price)) = funding_rx.recv() => {
                if let Some(r) = funding {
                    state.funding_rate = Some(r);
                    state.freshness.funding_tick_at = Some(Instant::now());
                    let now_ms = SystemTime::now()
                        .duration_since(UNIX_EPOCH)
                        .unwrap_or_default()
                        .as_millis() as i64;
                    state.funding_tracker.push(FundingRateSample { timestamp_ms: now_ms, rate: r });
                }
                let _ = mark_price;
            }

            Some(spot) = spot_rx.recv() => {
                if let Some(p) = spot {
                    state.spot_price = Some(p);
                }
            }

            Some(oi) = oi_rx.recv() => {
                if let Some(o) = oi {
                    state.freshness.oi_fetched_at = Some(Instant::now());
                    state.oi_history.push_back(o);
                    if state.oi_history.len() > 6 {
                        state.oi_history.pop_front();
                    }
                    let now_ms = SystemTime::now()
                        .duration_since(UNIX_EPOCH)
                        .unwrap_or_default()
                        .as_millis() as i64;
                    state.oi_tracker.push(OiHistSnapshot { timestamp_ms: now_ms, open_interest_usd: o });
                }
            }

            Some(events) = liq_rx.recv() => {
                for ev in events {
                    state.on_liquidation(ev);
                }
                if state.intrabar_cfg.enabled
                    && state.intrabar_cfg.detectors.contains(&IntrabarDetector::Liq)
                    && !state.intrabar_signal_fired
                    && state.frozen.is_some()
                    && state.current_price > 0.0
                {
                    let now_ms = SystemTime::now()
                        .duration_since(UNIX_EPOCH)
                        .unwrap_or_default()
                        .as_millis() as i64;
                    state.on_intrabar_tick(now_ms, "liquidation", &symbol_str).await;
                }
            }

            Some((top, global)) = ls_rx.recv() => {
                state.freshness.ls_fetched_at = Some(Instant::now());
                if let Some(snap) = top {
                    state.ls_tracker.push(snap);
                }
                if let Some(snap) = global {
                    state.ls_tracker.push(snap);
                }
            }

            Some(snap) = taker_rx.recv() => {
                state.freshness.taker_fetched_at = Some(Instant::now());
                if let Some(s) = snap {
                    state.last_taker_ratio = Some(s);
                }
            }

            Some(connected) = liq_health_rx.recv() => {
                state.streams.liq = if connected { StreamHealth::Ok } else { StreamHealth::Disc };
                state.freshness.liq_stream_ok = connected;
                if !connected { state.metrics.ws_reconnects += 1; }
            }

            Some(samples) = funding_hist_rx.recv() => {
                if !samples.is_empty() {
                    eprintln!("[inst] loaded {} funding rate history samples", samples.len());
                    state.funding_tracker.load(samples);
                }
            }

            Some(regime) = regime_rx.recv() => {
                // Spawn the Supabase config fetch off the event loop.
                if state.config_loader.should_reload(&regime) {
                    let mut loader = ConfigLoader::new();
                    loader.current_regime = state.config_loader.current_regime.clone();
                    loader.last_reload = state.config_loader.last_reload;
                    let tx = cfg_tx.clone();
                    let regime_clone = regime.clone();
                    tokio::spawn(async move {
                        let cfg = loader.load_for_regime(&regime_clone).await;
                        let _ = tx.send(cfg).await;
                    });
                    // Optimistically mark as reloaded so should_reload won't re-trigger
                    // while the fetch is in flight.
                    state.config_loader.current_regime = regime;
                    state.config_loader.last_reload = Some(Instant::now());
                }
            }

            Some(cfg) = cfg_rx.recv() => {
                state.cfg = cfg;
            }
        }

        if last_metrics_print.elapsed() >= metrics_interval {
            state.metrics.report(&state.freshness, &state.streams);
            last_metrics_print = Instant::now();
        }
    }
}
