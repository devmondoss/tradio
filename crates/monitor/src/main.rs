#![recursion_limit = "512"]
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

mod intrabar;
mod supabase_writer;
mod ws_server;

use std::collections::VecDeque;
use std::sync::{
    Arc,
    atomic::{AtomicU64, Ordering},
};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use bytes::Bytes;
use fastwebsockets::{FragmentCollector, OpCode};
use http_body_util::Empty;
use hyper::{
    Request,
    header::{CONNECTION, UPGRADE},
    upgrade::Upgraded,
};
use hyper_util::rt::{TokioExecutor, TokioIo};
use reqwest;
use std::sync::LazyLock;
use tokio_rustls::{
    TlsConnector,
    rustls::{ClientConfig, RootCertStore, crypto::aws_lc_rs, pki_types::ServerName},
};

use data::chart::kline::KlineTrades;
use data::detectors::{
    FvgContext, FvgDetector, OrderBlockContext, OrderBlockDetector, RangeContext, RangeDetector,
    SpoofDetector,
};
use data::institutional::{
    FundingRateSample, FundingTracker, InstitutionalContext, LiqMapSnapshot, LiqMapTracker,
    LiqSide, LiquidationEvent, LiquidationTracker, LongShortSnapshot, LsRatioTracker, LsSource,
    OiHistSnapshot, OiTracker, TakerRatioSnapshot, compute_smart_money_score,
};
use data::session::{SessionContext, classify_session};
use data::strategy::mongo_config_loader::MongoConfigLoader;
use data::strategy::mongo_writer::{MongoWriter, SignalOid};
use data::strategy::{
    adapter::{
        SWING_CONFIRM_BARS, build_flow_context, build_orderbook_context,
        build_volume_profile_context, build_vwap_context, count_price_reversals, derive_big_trade,
        derive_confirmed_swings, derive_cvd_divergence, derive_delta_velocity,
        derive_failed_acceptance_and_absorption, derive_fbg_imbalance,
        derive_finish_unfinish_action, derive_footprint_absorption_at_value_edge, derive_regime,
        derive_regime_with_hysteresis, derive_stacked_imbalance,
        derive_stacked_imbalance_from_levels, wall_nearby,
    },
    micro_window::{CandleMicroBuffer, MicroCtx, MicroTrade, MicroWindowConfig},
    paper::PaperAccount,
    playbook_reasoning::classify_playbook_reasoning,
    router::route_strategy,
    scalping::{
        ScalpingRegime, ScalpingState, absorption, cvd_divergence as cvd_div_scalping, obi_maker,
    },
    subdimi_parallel::run_subdimi_parallel,
    types::{
        AbsorptionSide, CvdDivergence, DataQuality, FootprintLevel, ImbalanceSide,
        OrderBookContext, Regime, Side, StrategyAction, StrategyConfig, StrategyMarketContext,
    },
};
use data::structure::{MarketStructureContext, MarketStructureTracker};
use exchange::{
    Kline, PushFrequency, Ticker, TickerInfo, Timeframe, Trade, Volume,
    adapter::{
        AdapterHandles, AdapterNetworkConfig, Event, Exchange, MarketKind, StreamConfig, Venue,
    },
    depth::Depth,
    unit::PriceStep,
};
use futures::StreamExt;
use intrabar::{IntrabarConfig, IntrabarDetector, IntrabarMode};
use supabase_writer::SupabaseWriter;

// ── constants ───────────────────────────────────────────────────────────────

const VP_WINDOW: usize = 300;
const VP_BINS: usize = 150;
// 20 bars = 100 min at M5. Was 14 (70 min) — too reactive, caused flip-flop in chop.
// Wider window smooths the OLS slope and reduces false regime changes.
// Note: slow_slope gate (> 0.20 / < -0.20) was calibrated at 14 bars; with 20 bars
// slopes are smaller in magnitude — if signals dry up, lower the gate to 0.15.
const REGIME_WINDOW: usize = 20;
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
    liq_raw_messages: u64, // text frames from btcusdt@forceOrder (BTC-specific)
    liq_global_raw_messages: u64, // text frames from !forceOrder@arr (all-market diagnostic)
    ws_reconnects: u64,
    // Throttle: timestamp of last liq-silence warning emitted (avoid repeating every minute)
    liq_warn_emitted_at: Option<Instant>,
}

// ── data freshness ────────────────────────────────────────────────────────────

/// Tracks when each institutional data source was last updated.
/// Used to compute ages shown in [bar] log and trigger [WARN] in [metrics].
#[derive(Default)]
struct DataFreshness {
    liq_last_event_at: Option<Instant>,
    liq_ws_connected_at: Option<Instant>, // when the forceOrder WS last connected
    liq_stream_ok: bool,                  // true while forceOrder WS is connected
    ls_fetched_at: Option<Instant>,
    taker_fetched_at: Option<Instant>,
    oi_fetched_at: Option<Instant>,
    funding_tick_at: Option<Instant>,
}

impl DataFreshness {
    fn age_str(t: Option<Instant>) -> String {
        match t {
            None => "never".into(),
            Some(t) => {
                let s = t.elapsed().as_secs();
                if s < 60 {
                    format!("{s}s")
                } else {
                    format!("{}m{:02}s", s / 60, s % 60)
                }
            }
        }
    }

    fn liq_age_str(&self) -> String {
        Self::age_str(self.liq_last_event_at)
    }
    fn liq_connected_age_str(&self) -> String {
        Self::age_str(self.liq_ws_connected_at)
    }
    fn ls_age_str(&self) -> String {
        Self::age_str(self.ls_fetched_at)
    }
    fn taker_age_str(&self) -> String {
        Self::age_str(self.taker_fetched_at)
    }
    fn oi_age_str(&self) -> String {
        Self::age_str(self.oi_fetched_at)
    }
    fn funding_age_str(&self) -> String {
        Self::age_str(self.funding_tick_at)
    }

    /// Returns (sources_ok, total_sources). A source is "ok" if updated within 10 min.
    /// Liq requires stream connected AND at least one raw message received ever.
    /// Connected-but-silent = unvalidated, does not count toward ok.
    fn quality(&self, liq_raw_total: u64) -> (u8, u8) {
        let threshold = Duration::from_secs(10 * 60);
        let fresh = |t: Option<Instant>| t.map(|i| i.elapsed() < threshold).unwrap_or(false);
        let mut ok = 0u8;
        if self.liq_stream_ok && liq_raw_total > 0 {
            ok += 1;
        } // connected + has delivered data = validated
        if fresh(self.ls_fetched_at) {
            ok += 1;
        }
        if fresh(self.ls_fetched_at) {
            ok += 1;
        } // top + global share same fetch
        if fresh(self.oi_fetched_at) {
            ok += 1;
        }
        if fresh(self.taker_fetched_at) {
            ok += 1;
        }
        if fresh(self.funding_tick_at) {
            ok += 1;
        }
        (ok, 6)
    }
}

// ── stream health ─────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, Default, PartialEq)]
enum StreamHealth {
    #[default]
    Unknown,
    Ok,
    Reconnecting,
    Disc,
}

impl std::fmt::Display for StreamHealth {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Unknown => write!(f, "?"),
            Self::Ok => write!(f, "ok"),
            Self::Reconnecting => write!(f, "recon"),
            Self::Disc => write!(f, "disc"),
        }
    }
}

#[derive(Default)]
struct StreamStates {
    klines: StreamHealth,
    depth: StreamHealth,
    liq: StreamHealth,
}

// ── outcome tracking ──────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy)]
enum OutcomeSource {
    BarClose,
    Intrabar,
}

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
    mfe: f64, // best favorable excursion in pts
    mae: f64, // worst adverse excursion in pts
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
            signal_ms,
            source,
            strategy_id,
            is_long,
            entry_px,
            stop_px,
            target_px,
            risk,
            mfe: 0.0,
            mae: 0.0,
            rr_at_1m: None,
            rr_at_3m: None,
            rr_at_5m: None,
            hit_target: false,
            hit_stop: false,
        }
    }

    fn update(&mut self, px: f64, now_ms: i64) {
        let excursion = if self.is_long {
            px - self.entry_px
        } else {
            self.entry_px - px
        };
        let adverse = -excursion;

        if excursion > self.mfe {
            self.mfe = excursion;
        }
        if adverse > self.mae {
            self.mae = adverse;
        }

        if self.is_long {
            if px <= self.stop_px {
                self.hit_stop = true;
            }
            if px >= self.target_px {
                self.hit_target = true;
            }
        } else {
            if px >= self.stop_px {
                self.hit_stop = true;
            }
            if px <= self.target_px {
                self.hit_target = true;
            }
        }

        let rr = excursion / self.risk;
        let elapsed = now_ms - self.signal_ms;
        if elapsed >= 60_000 && self.rr_at_1m.is_none() {
            self.rr_at_1m = Some(rr);
        }
        if elapsed >= 180_000 && self.rr_at_3m.is_none() {
            self.rr_at_3m = Some(rr);
        }
        if elapsed >= 300_000 && self.rr_at_5m.is_none() {
            self.rr_at_5m = Some(rr);
        }
    }

    fn log(&self, bar_close_ms: i64) {
        let age_ms = bar_close_ms - self.signal_ms;
        println!(
            "[outcome] src={} id={} side={} entry={:.1} stop={:.1} target={:.1} \
             risk={:.1} mfe={:.1} mae={:.1} \
             rr_1m={} rr_3m={} rr_5m={} \
             hit_target={} hit_stop={} age={}ms",
            self.source,
            self.strategy_id,
            if self.is_long { "L" } else { "S" },
            self.entry_px,
            self.stop_px,
            self.target_px,
            self.risk,
            self.mfe,
            self.mae,
            fmt_opt(self.rr_at_1m),
            fmt_opt(self.rr_at_3m),
            fmt_opt(self.rr_at_5m),
            self.hit_target,
            self.hit_stop,
            age_ms,
        );
    }
}

fn fmt_opt(v: Option<f64>) -> String {
    v.map(|x| format!("{x:.2}")).unwrap_or_else(|| "-".into())
}

/// Bar-level state frozen at each 5m close, reused by the intrabar evaluator.
/// Only bar-level fields go here; tick-level fields come from live BarState.
#[derive(Clone)]
struct FrozenBarCtx {
    regime: Regime,
    slow_slope: f64,
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
    avwap_event: Option<f64>,
    // Flow (bar-computed, frozen)
    cvd_slope: Option<f64>,
    failed_acceptance: bool,
    footprint_absorption: AbsorptionSide,
    cvd_divergence: Option<CvdDivergence>,
    stacked_imbalance: ImbalanceSide,
    footprint_levels: Vec<FootprintLevel>,
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
    // Higher-level context snapshots
    market_structure: Option<MarketStructureContext>,
    session: SessionContext,
    order_blocks: OrderBlockContext,
    fvg: FvgContext,
    liq_map: Option<LiqMapSnapshot>,
    bar_vpin: Option<f64>,
    // Timing
    bar_close_ms: i64,
    bar_close_price: f64,
    // VP open bias (frozen at bar close, stable for intrabar evals within same bar)
    vp_open_bias: Option<data::strategy::vp_open_bias::DailyVpContext>,
    // Intraday range (frozen at bar close)
    range: Option<RangeContext>,
}

impl PipelineMetrics {
    fn report(&mut self, freshness: &DataFreshness, streams: &StreamStates) {
        let avg_lat = if self.latencies_ms.is_empty() {
            0.0
        } else {
            self.latencies_ms.iter().sum::<i64>() as f64 / self.latencies_ms.len() as f64
        };
        let max_lat = self.latencies_ms.iter().max().copied().unwrap_or(0);

        let depth_age_ms = self
            .last_depth_at
            .map(|t| t.elapsed().as_millis())
            .unwrap_or(999_999);
        let trade_age_ms = self
            .last_trade_at
            .map(|t| t.elapsed().as_millis())
            .unwrap_or(999_999);

        let (inst_ok, inst_total) = freshness.quality(self.liq_raw_messages);
        println!(
            "[metrics] bars={} signals={} liq_events={} liq_raw={} liq_global_raw={} ws_reconnects={} | \
             kline_ticks={} trades={} depth_updates={} | \
             bar_latency_avg={:.0}ms max={max_lat}ms | \
             depth_age={depth_age_ms}ms trade_age={trade_age_ms}ms | \
             inst={inst_ok}/{inst_total} liq_age={} liq_connected={} ls_age={} taker_age={} oi_age={} funding_age={} | \
             ws=[kline:{} depth:{} trades:ok liq:{}]",
            self.bars_processed,
            self.signals_today,
            self.liq_events_today,
            self.liq_raw_messages,
            self.liq_global_raw_messages,
            self.ws_reconnects,
            self.kline_ticks,
            self.trade_count,
            self.depth_updates,
            avg_lat,
            freshness.liq_age_str(),
            freshness.liq_connected_age_str(),
            freshness.ls_age_str(),
            freshness.taker_age_str(),
            freshness.oi_age_str(),
            freshness.funding_age_str(),
            streams.klines,
            streams.depth,
            streams.liq,
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
        // Liq silence warnings — throttled to once per 30 min to avoid flooding.
        // Re-arms automatically when a new liquidation event arrives (liq_last_event_at resets).
        let warn_cooldown = Duration::from_secs(30 * 60);
        let warn_due = self
            .liq_warn_emitted_at
            .map(|t| t.elapsed() >= warn_cooldown)
            .unwrap_or(true);
        if streams.liq == StreamHealth::Disc {
            eprintln!("[WARN] forceOrder stream disconnected — liq data unavailable");
        } else if let Some(age) = freshness.liq_last_event_at.map(|t| t.elapsed().as_secs()) {
            // Had events before, now stopped
            if age > 300 && warn_due {
                eprintln!(
                    "[WARN] forceOrder stream: no liquidation events for {age}s — market very quiet or stream issue"
                );
                self.liq_warn_emitted_at = Some(Instant::now());
            }
        } else if self.liq_raw_messages == 0 && warn_due {
            // Never received a single message — warn after 30 min connected
            let connected_secs = freshness
                .liq_ws_connected_at
                .map(|t| t.elapsed().as_secs())
                .unwrap_or(0);
            if connected_secs > 1800 {
                eprintln!(
                    "[WARN] forceOrder stream: connected {connected_secs}s but liq_raw=0 — market extremely quiet or endpoint issue"
                );
                self.liq_warn_emitted_at = Some(Instant::now());
            }
        }
        warn_age("LS ratio fetch", freshness.ls_fetched_at, 600);
        warn_age("taker ratio fetch", freshness.taker_fetched_at, 300);
        warn_age("OI fetch", freshness.oi_fetched_at, 600);
        warn_age("funding stream", freshness.funding_tick_at, 120);

        let avg_proc = if self.processing_ms.is_empty() {
            0.0
        } else {
            self.processing_ms.iter().sum::<u128>() as f64 / self.processing_ms.len() as f64
        };
        let max_proc = self.processing_ms.iter().max().copied().unwrap_or(0);
        println!(
            "[metrics] proc_avg={avg_proc:.0}ms proc_max={max_proc}ms bars_since_signal={}",
            self.bars_since_signal
        );
        if self.bars_processed > 0 && avg_proc > 100.0 {
            eprintln!("[WARN] high bar processing time {avg_proc:.0}ms — check compute path");
        }
    }
}

struct BarState {
    bars: VecDeque<Kline>,
    cvd: f64,
    cvd_history: VecDeque<f64>,
    bar_delta_history: VecDeque<f64>,
    bar_footprint: KlineTrades,
    footprint_step: PriceStep,
    bar_buy_vol: f64,
    bar_sell_vol: f64,
    bar_big_buy_vol: f64,
    bar_big_sell_vol: f64,
    vwap_cum_pv: f64,
    vwap_cum_vol: f64,
    vwap_day: i64,
    vwap_session: Option<f64>,
    depth: Option<Depth>,
    paper: PaperAccount,
    metrics: PipelineMetrics,
    liq_raw_counter: Arc<AtomicU64>, // shared with btcusdt@forceOrder task
    liq_global_raw_counter: Arc<AtomicU64>, // shared with !forceOrder@arr diagnostic task
    // Crypto-native context
    funding_rate: Option<f64>,
    spot_price: Option<f64>,
    oi_history: VecDeque<f64>,
    // Institutional trackers
    liq_tracker: LiquidationTracker,
    ls_tracker: LsRatioTracker,
    oi_tracker: OiTracker,
    funding_tracker: FundingTracker,
    ms_tracker: MarketStructureTracker,
    ob_detector: OrderBlockDetector,
    fvg_detector: FvgDetector,
    range_detector: RangeDetector,
    micro_buffer: CandleMicroBuffer,
    /// Open time of the current (live) candle — updated at each bar close.
    current_candle_open_ms: i64,
    liq_map_tracker: LiqMapTracker,
    liq_map_snapshot: Option<LiqMapSnapshot>,
    last_taker_ratio: Option<TakerRatioSnapshot>,
    spoof_detector: SpoofDetector,
    avwap_event_anchor_ms: Option<i64>,
    // MongoDB writer — local persistence, signal↔trade linking via ObjectId
    mongo: MongoWriter,
    // ObjectId of the most recently written shadow_signals document — used to link signal_outcomes
    pending_signal_oid: Option<SignalOid>,
    // Supabase writer — cloud persistence (Railway); None if SUPABASE_URL not set
    supabase: Option<SupabaseWriter>,
    // UUID returned by Supabase write_signal — used to link signal_outcomes rows.
    // Resolved via oneshot channel one bar after the signal fires (Supabase is async).
    pending_supabase_uuid_rx: Option<tokio::sync::oneshot::Receiver<Option<String>>>,
    pending_supabase_uuid: Option<String>,
    // Dynamic config loaded from MongoDB deployed_params
    config_loader: MongoConfigLoader,
    // Last regime seen — used to detect regime changes and trigger config reload
    last_regime: Option<String>,
    // Last regime as enum — used for hysteresis in derive_regime_with_hysteresis
    last_regime_enum: data::strategy::types::Regime,
    // Timestamp (ms) when the current regime started — for duration_ms in regime_history
    regime_started_at_ms: Option<i64>,
    // Rolling window de los últimos 25 regímenes (1 por barra M1) para el filtro expansion_n de RBF.
    regime_hist_25: VecDeque<data::strategy::types::Regime>,
    // Rolling window de los últimos 25 valores de oi_momentum_aligned para el gate pre-breakout.
    oi_mom_hist_25: VecDeque<bool>,
    // Rolling window de los últimos 25 bar_delta para el gate cum_delta por símbolo.
    delta_hist_25: VecDeque<f64>,
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
    last_intrabar_signal_ms: i64,
    // OBI L5 from the previous bar — injected into StrategyMarketContext for DIB persistence gate
    prev_obi_l5: f64,
    // EMA suavizadas de OBI L5 — escritas a btc/eth/bnb/sol_bars cada barra
    obi_ema_fast: f64, // alpha = 2/(5+1)  ≈ 0.333
    obi_ema_slow: f64, // alpha = 2/(20+1) ≈ 0.095
    // Muestras intrabar OBI — sampleo cada 10s para calcular promedio/mín real
    obi_intrabar: Vec<(i64, f32, f32, f32, f32)>, // (ts_ms, l5, l10, l20, spread_bps)
    last_obi_sample_ms: i64,
    // ICT AMD structural levels — acumulados barra a barra para backtest de estrategia
    asian_high: Option<f64>, // High de la sesión Asia del día actual (00:00–07:00 UTC)
    asian_low: Option<f64>,  // Low  de la sesión Asia del día actual
    asian_day: i64,          // día UTC de la sesión Asia activa (para reset diario)
    prev_day_high: Option<f64>, // PDH: High del día anterior completo
    prev_day_low: Option<f64>, // PDL: Low  del día anterior completo
    daily_high: f64,         // Acumulador del high del día en curso (UTC)
    daily_low: f64,          // Acumulador del low  del día en curso (UTC)
    daily_day: i64,          // día UTC del acumulador diario
    // VPIN rolling history for CDF (percentile rank) computation
    vpin_history: VecDeque<f64>,
    // Consecutive bars of CVD-vs-price divergence (positive=bearish, negative=bullish, 0=aligned)
    cvd_divergence_bars: i32,
    // VP open bias tracker — classifies daily session open vs previous day's value area
    vp_bias_tracker: data::strategy::vp_open_bias::DailyVpTracker,
    // Naked POC tracker — tracks previous-session POCs not yet revisited by price
    naked_poc_tracker: data::strategy::vp_open_bias::NakedPocTracker,
    // HTF VP cascade trackers — weekly and monthly volume profiles for top-down bias
    htf_weekly_tracker: data::strategy::vp_open_bias::HtfVpTracker,
    htf_monthly_tracker: data::strategy::vp_open_bias::HtfVpTracker,
    // TPO Single Print tracker — 30-min Market Profile, detects structural gaps
    tpo_tracker: data::strategy::tpo::TpoTracker,
    // Scalping engine — 3 strategies (S1/OBI, S2/Absorption, S3/CVD Divergence)
    scalping_state: ScalpingState,
    // Índice del último trade de scalping ya escrito a Supabase (evita doble escritura)
    scalping_paper_written_idx: usize,
    // AMD (Accumulation · Manipulation · Distribution) detector state
    amd_state: data::strategy::detectors::amd_detector::AmdDetectorState,
    // AMD paper trader — trackea outcomes de señales AMD
    amd_paper: data::strategy::detectors::amd_paper::AmdPaperTrader,
    // UUID pendiente de asignar al paper trader AMD (llega async tras write_amd_signal_async)
    amd_pending_id_rx: Option<tokio::sync::oneshot::Receiver<Option<String>>>,
    // Outcome AMD pendiente si el trade cerró antes de que llegara el supabase_id
    amd_pending_outcome: Option<data::strategy::detectors::amd_paper::AmdClosedTrade>,
    // Última sesión vista para AMD — detecta cambio y fuerza cierre SESSION_END
    amd_last_session: data::session::session_tracker::TradingSession,
    // Range Breakout Flow detector state
    rbf_state: data::strategy::detectors::range_breakout_flow::RangeBreakoutState,
    // RBF paper trader — trackea outcomes de señales RBF
    rbf_paper: data::strategy::detectors::rbf_paper::RbfPaperTrader,
    // UUID pendiente de asignar al paper trader (llega async tras write_rbf_signal_async)
    rbf_pending_id_rx: Option<tokio::sync::oneshot::Receiver<Option<String>>>,
    // Outcome pendiente de escribir: trade cerró antes de que llegara el supabase_id
    rbf_pending_outcome: Option<data::strategy::detectors::rbf_paper::RbfClosedTrade>,
    // Última sesión vista — detecta cambio de sesión para forzar cierre de posición RBF abierta
    rbf_last_session: data::session::session_tracker::TradingSession,
    // Último RbfGateContext ensamblado — compartido con el detector BE en el mismo bar
    last_rbf_gate: Option<data::strategy::detectors::range_breakout_flow::RbfGateContext>,
    // Buyer Exhaustion detector state
    be_state: buyer_exhaustion::detector::BuyerExhaustionState,
    // BE paper trader
    be_paper: buyer_exhaustion::paper::BePaperTrader,
    // UUID pendiente de asignar al paper trader BE
    be_pending_id_rx: Option<tokio::sync::oneshot::Receiver<Option<String>>>,
    // Outcome BE pendiente si el trade cerró antes de que llegara el supabase_id
    be_pending_outcome: Option<buyer_exhaustion::signal::BeClosedTrade>,
    // Última sesión vista para BE — detecta cambio y fuerza cierre SESSION_END
    be_last_session: data::session::session_tracker::TradingSession,
    // H4 EMA (FASE 2.6) — EMA-240M1 ≈ 4H para contexto estructural de RBF y AMD
    ema_h1: f64,        // EMA-60M1 (≈1H) alpha = 2/(60+1) ≈ 0.0328
    ema_h1_bars: usize, // barras acumuladas hasta warmup (warmup = 60)
    // HTF Shorts detector — D1 EMA20 real + H1 buckets UTC reales + fees descontados
    mtf_state: data::strategy::detectors::mtf_shorts_detector::MtfShortsState,
    // HTF Longs detector — H4 EMA20 + H1 low-stop + patrones London/NY alcistas
    mtf_longs_state: data::strategy::detectors::mtf_longs_detector::MtfLongsState,
    // WebSocket broadcast — envía barras y señales al dashboard web
    ws_tx: ws_server::Sender,
}

impl BarState {
    fn new(
        mongo: MongoWriter,
        supabase: Option<SupabaseWriter>,
        config_loader: MongoConfigLoader,
        footprint_step: PriceStep,
        liq_raw_counter: Arc<AtomicU64>,
        liq_global_raw_counter: Arc<AtomicU64>,
        ws_tx: ws_server::Sender,
        symbol: &str,
    ) -> Self {
        Self {
            bars: VecDeque::with_capacity(VP_WINDOW + 1),
            cvd: 0.0,
            cvd_history: VecDeque::with_capacity(CVD_WINDOW + 1),
            bar_delta_history: VecDeque::with_capacity(CVD_WINDOW + 1),
            bar_footprint: KlineTrades::new(),
            footprint_step,
            bar_buy_vol: 0.0,
            bar_sell_vol: 0.0,
            bar_big_buy_vol: 0.0,
            bar_big_sell_vol: 0.0,
            vwap_cum_pv: 0.0,
            vwap_cum_vol: 0.0,
            vwap_day: -1,
            vwap_session: None,
            depth: None,
            paper: PaperAccount::load_or_new(),
            metrics: PipelineMetrics::default(),
            liq_raw_counter,
            liq_global_raw_counter,
            funding_rate: None,
            spot_price: None,
            oi_history: VecDeque::with_capacity(7),
            liq_tracker: {
                let mut t = LiquidationTracker::new();
                t.seed_bar_history();
                t
            },
            ls_tracker: LsRatioTracker::new(),
            oi_tracker: OiTracker::new(),
            funding_tracker: FundingTracker::new(),
            ms_tracker: MarketStructureTracker::new(100, 2),
            ob_detector: OrderBlockDetector::new(100),
            fvg_detector: FvgDetector::new(100),
            range_detector: RangeDetector::new(),
            micro_buffer: CandleMicroBuffer::new(MicroWindowConfig::default(), 0),
            current_candle_open_ms: 0,
            liq_map_tracker: LiqMapTracker::new(),
            liq_map_snapshot: None,
            last_taker_ratio: None,
            spoof_detector: SpoofDetector::new(),
            avwap_event_anchor_ms: None,
            mongo,
            pending_signal_oid: None,
            supabase,
            pending_supabase_uuid_rx: None,
            pending_supabase_uuid: None,
            config_loader,
            last_regime: None,
            last_regime_enum: data::strategy::types::Regime::Unknown,
            regime_started_at_ms: None,
            regime_hist_25: VecDeque::with_capacity(26),
            oi_mom_hist_25: VecDeque::with_capacity(26),
            delta_hist_25: VecDeque::with_capacity(26),
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
            last_intrabar_signal_ms: 0,
            prev_obi_l5: 0.0,
            obi_ema_fast: 0.0,
            obi_ema_slow: 0.0,
            obi_intrabar: Vec::new(),
            last_obi_sample_ms: 0,
            asian_high: None,
            asian_low: None,
            asian_day: -1,
            prev_day_high: None,
            prev_day_low: None,
            daily_high: f64::NEG_INFINITY,
            daily_low: f64::INFINITY,
            daily_day: -1,
            vpin_history: VecDeque::with_capacity(51),
            cvd_divergence_bars: 0,
            vp_bias_tracker: data::strategy::vp_open_bias::DailyVpTracker::new(),
            naked_poc_tracker: data::strategy::vp_open_bias::NakedPocTracker::new(10),
            htf_weekly_tracker: data::strategy::vp_open_bias::HtfVpTracker::new_weekly(),
            htf_monthly_tracker: data::strategy::vp_open_bias::HtfVpTracker::new_monthly(),
            tpo_tracker: data::strategy::tpo::TpoTracker::new(),
            scalping_state: ScalpingState::new(25),
            scalping_paper_written_idx: 0,
            amd_state: data::strategy::detectors::amd_detector::AmdDetectorState::new(),
            amd_paper: data::strategy::detectors::amd_paper::AmdPaperTrader::new(),
            amd_pending_id_rx: None,
            amd_pending_outcome: None,
            amd_last_session: data::session::session_tracker::TradingSession::OffHours,
            rbf_state: data::strategy::detectors::range_breakout_flow::RangeBreakoutState::new(),
            rbf_paper: data::strategy::detectors::rbf_paper::RbfPaperTrader::new(),
            rbf_pending_id_rx: None,
            rbf_pending_outcome: None,
            rbf_last_session: data::session::session_tracker::TradingSession::OffHours,
            last_rbf_gate: None,
            be_state: buyer_exhaustion::detector::BuyerExhaustionState::new(),
            be_paper: buyer_exhaustion::paper::BePaperTrader::new(),
            be_pending_id_rx: None,
            be_pending_outcome: None,
            be_last_session: data::session::session_tracker::TradingSession::OffHours,
            ema_h1: 0.0,
            ema_h1_bars: 0,
            mtf_state: data::strategy::detectors::mtf_shorts_detector::MtfShortsState::new(symbol),
            mtf_longs_state: data::strategy::detectors::mtf_longs_detector::MtfLongsState::new(symbol),
            ws_tx,
        }
    }

    fn on_liquidation(&mut self, event: LiquidationEvent) {
        self.freshness.liq_last_event_at = Some(Instant::now());
        self.metrics.liq_events_today += 1;
        self.metrics.liq_warn_emitted_at = None; // re-arm silence warning after activity resumes
        self.liq_tracker.push(event);
    }

    fn on_trade(&mut self, trade: &Trade) {
        let is_sell = trade.is_sell;
        let qty = trade.qty.to_f32_lossy();
        let price = trade.price.to_f32() as f64;
        if price > 0.0 {
            self.current_price = price;
        }
        self.bar_footprint
            .add_trade_to_nearest_bin(trade, self.footprint_step);
        let delta = f64::from(qty);
        let is_big = delta * price >= 100_000.0;
        if is_sell {
            self.bar_sell_vol += delta;
            self.cvd -= delta;
            if is_big {
                self.bar_big_sell_vol += delta;
            }
        } else {
            self.bar_buy_vol += delta;
            self.cvd += delta;
            if is_big {
                self.bar_big_buy_vol += delta;
            }
        }
        self.metrics.trade_count += 1;
        self.metrics.last_trade_at = Some(Instant::now());
        // Micro-window: accumulate trade into the current candle's buffer.
        // Size is in base units (BTC) — consistent with CVD/footprint.
        if self.current_candle_open_ms > 0 {
            let mw_trade = MicroTrade {
                ts_ms: trade.time.as_u64() as i64,
                price,
                size: f64::from(qty),
                is_buy: !is_sell,
                is_big,
                liq_usd: 0.0, // liquidations come from separate @forceOrder stream
            };
            self.micro_buffer.on_trade(&mw_trade);
        }
        // Scalping: actualizar excursiones y evaluar exit en cada trade
        if self.scalping_state.paper.has_position() && price > 0.0 {
            self.scalping_state.paper.update_excursions(price);
            let now_ms = trade.time.as_u64() as i64;
            let cfg = self.config_loader.current();
            if let Some(exit_reason) = self.scalping_state.paper.check_exit(
                price,
                self.scalping_state.obi_ema_fast,
                self.spread_ticks_now(),
                now_ms,
                cfg.scalping.time_stop_secs,
            ) {
                self.scalping_state.paper.close(price, now_ms, exit_reason);
                self.scalping_state.active_signal = None;
                self.scalping_state.signal_entry_ms = None;
            }
        }
    }

    fn on_trade_batch(&mut self) {
        self.metrics.trade_batches += 1;
    }

    /// Spread actual en ticks ($0.10) del libro L2.
    fn spread_ticks_now(&self) -> i32 {
        if let Some(d) = &self.depth {
            if let (Some((&ask, _)), Some((&bid, _))) =
                (d.asks.iter().next(), d.bids.iter().next_back())
            {
                let spread_usd = (ask.to_f32() as f64 - bid.to_f32() as f64).max(0.0);
                return (spread_usd / 0.10).round() as i32;
            }
        }
        1
    }

    fn current_footprint_levels(&self) -> Vec<FootprintLevel> {
        let mut levels: Vec<FootprintLevel> = self
            .bar_footprint
            .trades
            .iter()
            .map(|(price, group)| {
                let buy_volume = group.buy_qty.to_f32_lossy() as f64;
                let sell_volume = group.sell_qty.to_f32_lossy() as f64;
                FootprintLevel {
                    price: price.to_f32() as f64,
                    buy_volume,
                    sell_volume,
                    delta: buy_volume - sell_volume,
                }
            })
            .collect();
        levels.sort_by(|a, b| {
            a.price
                .partial_cmp(&b.price)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        levels
    }

    // ── Outcome tracking ──────────────────────────────────────────────────────

    fn push_outcome(
        &mut self,
        signal: &data::strategy::types::StrategySignal,
        source: OutcomeSource,
        at_ms: i64,
    ) {
        let (Some(entry), Some(stop), Some(target), Some(side)) = (
            signal.entry_price,
            signal.stop_price,
            signal.target_price,
            signal.side,
        ) else {
            return;
        };
        let is_long = matches!(side, Side::Long);
        let id = signal
            .strategy_id
            .as_ref()
            .map(|s| format!("{s:?}"))
            .unwrap_or_else(|| "Unknown".into());
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
            if o.rr_at_1m.is_none() {
                o.rr_at_1m = Some(rr_final);
            }
            if o.rr_at_3m.is_none() {
                o.rr_at_3m = Some(rr_final);
            }
            if o.rr_at_5m.is_none() {
                o.rr_at_5m = Some(rr_final);
            }
            o.log(bar_close_ms);
        }
    }

    // ── Intrabar tactical layer ───────────────────────────────────────────────

    fn should_eval_intrabar(&self, now_ms: i64) -> bool {
        let cfg = &self.intrabar_cfg;
        if !cfg.enabled {
            return false;
        }
        let Some(frozen) = &self.frozen else {
            return false;
        };
        if self.intrabar_signal_fired {
            return false;
        }
        let ms_since_signal = (now_ms - self.last_intrabar_signal_ms).max(0) as u64;
        if ms_since_signal < cfg.signal_cooldown_ms {
            return false;
        }
        if self.intrabar_eval_count >= cfg.max_evals_per_bar {
            return false;
        }
        if self.current_price <= 0.0 {
            return false;
        }

        let elapsed_ms = (now_ms - self.last_intrabar_eval_ms).max(0) as u64;
        if elapsed_ms < cfg.min_seconds_between_evals * 1000 {
            return false;
        }

        let price_move = (self.current_price - self.last_intrabar_eval_price).abs();
        let time_fallback = elapsed_ms >= cfg.time_fallback_ms;
        if frozen.atr > 0.0 {
            price_move >= cfg.price_move_atr_k * frozen.atr || time_fallback
        } else {
            time_fallback
        }
    }

    fn build_intrabar_ctx(
        &self,
        frozen: &FrozenBarCtx,
        now_ms: i64,
        symbol: &str,
    ) -> StrategyMarketContext {
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

        let vwap_ctx = build_vwap_context(
            px,
            frozen.vwap_session,
            frozen.avwap_bos,
            frozen.avwap_event,
        );
        let vp_ctx = build_volume_profile_context(
            px,
            frozen.poc,
            frozen.vah,
            frozen.val,
            frozen.hvn_nearby.clone(),
            frozen.lvn_nearby.clone(),
        );
        let mut ob_ctx = match &self.depth {
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
        ob_ctx.spoof = Some(self.spoof_detector.context().clone());

        let live_delta = self.bar_buy_vol - self.bar_sell_vol;
        let live_vpin = if self.bar_buy_vol + self.bar_sell_vol > 0.0 {
            Some(
                ((self.bar_buy_vol - self.bar_sell_vol) / (self.bar_buy_vol + self.bar_sell_vol))
                    .abs(),
            )
        } else {
            frozen.bar_vpin
        };
        let bid_wall = wall_nearby(&ob_ctx.walls_below, px, frozen.atr);
        let ask_wall = wall_nearby(&ob_ctx.walls_above, px, frozen.atr);
        let oi_delta = if self.oi_history.len() >= 2 {
            let r = self.oi_history.back().copied().unwrap_or(0.0);
            let o = self.oi_history.front().copied().unwrap_or(0.0);
            Some(r - o)
        } else {
            None
        };

        let mut flow = build_flow_context(
            Some(self.cvd),
            frozen.cvd_slope,
            Some(live_delta),
            Some(self.bar_buy_vol),
            Some(self.bar_sell_vol),
            live_vpin,
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
            frozen.stacked_imbalance,
            frozen.mss_active,
            frozen.sweep_confirmed,
            Some(frozen.fast_slope),
            None, // oi_delta_zscore — intrabar uses frozen snapshot
            None, // vpin_cdf — not recomputed intrabar
            None, // cvd_divergence_persistence — bar-close metric only
        );
        flow.footprint_levels = frozen.footprint_levels.clone();
        {
            let (fb, fbe, ub, ube) = derive_finish_unfinish_action(&flow.footprint_levels);
            flow.finish_action_bullish = fb;
            flow.finish_action_bearish = fbe;
            flow.unfinish_action_bullish = ub;
            flow.unfinish_action_bearish = ube;
            let (btb, btbe) = derive_big_trade(&flow.footprint_levels);
            flow.big_trade_bullish = btb;
            flow.big_trade_bearish = btbe;
        }
        // Delta velocity: OLS slope of per-bar delta, last 5 bars, normalized by ATR
        {
            let recent: Vec<f64> = self.bar_delta_history.iter().copied().collect();
            flow.delta_velocity = derive_delta_velocity(&recent, 5, frozen.atr);
        }

        let liq_snap = self.liq_tracker.snapshot(now_ms);
        let ls_snap = self.ls_tracker.snapshot();
        let oi_snap = self.oi_tracker.snapshot();
        let fund_snap = self.funding_tracker.snapshot();
        let smart_money_score = Some(compute_smart_money_score(
            &ls_snap, &oi_snap, &fund_snap, &liq_snap,
        ));
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
            smart_money_score,
            liq_map: frozen.liq_map.clone(),
        });

        StrategyMarketContext {
            symbol: symbol.to_string(),
            timestamp_ms: now_ms,
            price: px,
            regime: effective_regime,
            atr: if frozen.atr > 0.0 {
                Some(frozen.atr)
            } else {
                None
            },
            volume_profile: vp_ctx,
            vwap: vwap_ctx,
            flow,
            orderbook: ob_ctx,
            institutional,
            swing_high_20: frozen.swing_high_20,
            swing_low_20: frozen.swing_low_20,
            market_structure: frozen.market_structure.clone(),
            session: Some(frozen.session.clone()),
            order_blocks: Some(frozen.order_blocks.clone()),
            fvg: Some(frozen.fvg.clone()),
            leverage: self.paper.config.leverage,
            prev_obi_l5: Some(self.prev_obi_l5),
            slow_slope: Some(frozen.slow_slope),
            auction_state: None, // not recomputed intrabar
            vp_open_bias: frozen.vp_open_bias.clone(),
            htf_vp: None, // not recomputed intrabar
            range: frozen.range.clone(),
        }
    }

    async fn on_intrabar_tick(&mut self, now_ms: i64, triggered_by: &str, symbol: &str) {
        let Some(frozen) = self.frozen.clone() else {
            return;
        };
        let ctx = self.build_intrabar_ctx(&frozen, now_ms, symbol);
        let cfg = self.config_loader.current();
        let (signal, detector_log) = route_strategy(&ctx, &cfg);
        let reasoning = classify_playbook_reasoning(&ctx, &cfg, &signal, &detector_log);

        self.last_intrabar_eval_price = self.current_price;
        self.last_intrabar_eval_ms = now_ms;
        self.intrabar_eval_count += 1;

        let fired = signal.action == StrategyAction::ShadowSignal;
        println!(
            "[intrabar] ts={now_ms} px={:.2} regime={:?} fast_slope={:.3} trigger={triggered_by} \
             eval={}/{} action={:?} score={:.3} id={:?}",
            ctx.price,
            ctx.regime,
            frozen.fast_slope,
            self.intrabar_eval_count,
            self.intrabar_cfg.max_evals_per_bar,
            signal.action,
            signal.score,
            signal.strategy_id,
        );

        if !fired {
            return;
        }

        // Track outcome regardless of mode — measures suppressed signals too
        self.push_outcome(&signal, OutcomeSource::Intrabar, now_ms);
        // Always record signal timestamp so the cross-bar cooldown applies in all modes.
        self.last_intrabar_signal_ms = now_ms;

        match self.intrabar_cfg.mode {
            IntrabarMode::ObserveOnly => {
                println!("[intrabar] signal suppressed — mode=ObserveOnly");
            }
            IntrabarMode::ShadowEvent => {
                if let Ok(json) = serde_json::to_string(&signal) {
                    println!("[intrabar_signal] {json}");
                }
                // Write to persistence even in ShadowEvent mode — for observability.
                // Paper trader is NOT affected (intrabar_signal_fired stays false).
                self.mongo.write_signal(&signal, &ctx);
                if let Some(sb) = self.supabase.clone() {
                    let s = signal.clone();
                    let c = ctx.clone();
                    let r = reasoning.clone();
                    tokio::spawn(async move {
                        sb.write_signal_with_reasoning(&s, &c, &r).await;
                    });
                }
            }
            IntrabarMode::ShadowSignal => {
                self.intrabar_signal_fired = true;
                self.metrics.signals_today += 1;
                if let Ok(json) = serde_json::to_string(&signal) {
                    println!("[intrabar_signal] {json}");
                }
                self.mongo.write_signal(&signal, &ctx);
                if let Some(sb) = self.supabase.clone() {
                    let s = signal.clone();
                    let c = ctx.clone();
                    let r = reasoning.clone();
                    tokio::spawn(async move {
                        sb.write_signal_with_reasoning(&s, &c, &r).await;
                    });
                }
            }
        }
    }

    fn on_depth(&mut self, depth: Depth) {
        let now_ms = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis() as i64;
        let bids: Vec<(f64, f64)> = depth
            .bids
            .iter()
            .rev()
            .take(20)
            .map(|(p, q)| (p.to_f32() as f64, q.to_f32_lossy() as f64))
            .collect();
        let asks: Vec<(f64, f64)> = depth
            .asks
            .iter()
            .take(20)
            .map(|(p, q)| (p.to_f32() as f64, q.to_f32_lossy() as f64))
            .collect();
        self.spoof_detector
            .update(&bids, &asks, self.current_price, now_ms);

        // ── Sampleo intrabar OBI cada 10 segundos ─────────────────────────────
        // Permite calcular OBI promedio real de la barra vs snapshot al cierre.
        // Las muestras se escriben a obi_10s en Supabase al cerrar cada barra.
        if now_ms - self.last_obi_sample_ms >= 10_000 {
            let obi_fn = |n: usize| -> f32 {
                let bid: f64 = depth
                    .bids
                    .iter()
                    .rev()
                    .take(n)
                    .map(|(_, q)| f64::from(q.to_f32_lossy()))
                    .sum();
                let ask: f64 = depth
                    .asks
                    .iter()
                    .take(n)
                    .map(|(_, q)| f64::from(q.to_f32_lossy()))
                    .sum();
                let tot = bid + ask;
                if tot > 0.0 {
                    ((bid - ask) / tot) as f32
                } else {
                    0.0
                }
            };
            let spread_bps = if let (Some((best_ask, _)), Some((best_bid, _))) =
                (depth.asks.iter().next(), depth.bids.iter().rev().next())
            {
                let mid = (best_ask.to_f32() as f64 + best_bid.to_f32() as f64) / 2.0;
                if mid > 0.0 {
                    ((best_ask.to_f32() as f64 - best_bid.to_f32() as f64) / mid * 10_000.0) as f32
                } else {
                    0.0
                }
            } else {
                0.0
            };
            self.obi_intrabar
                .push((now_ms, obi_fn(5), obi_fn(10), obi_fn(20), spread_bps));
            self.last_obi_sample_ms = now_ms;
        }

        // Scalping: OBI L10 → EMA normalizada [0,1] (consistente con local UI)
        {
            let bid10: f64 = depth
                .bids
                .iter()
                .rev()
                .take(10)
                .map(|(_, q)| f64::from(q.to_f32_lossy()))
                .sum();
            let ask10: f64 = depth
                .asks
                .iter()
                .take(10)
                .map(|(_, q)| f64::from(q.to_f32_lossy()))
                .sum();
            let total10 = bid10 + ask10;
            if total10 > 0.0 {
                self.scalping_state.on_depth((bid10 - ask10) / total10); // on_depth normaliza internamente
            }
        }
        self.depth = Some(depth);
        self.metrics.depth_updates += 1;
        self.metrics.last_depth_at = Some(Instant::now());
    }

    async fn on_bar_close(&mut self, bar: Kline, bar_close_ms: u64, symbol: &str) {
        // obi_min/max_intrabar added 2026-06-15
        // Resolve Supabase UUID from the previous bar's write_signal (should be ready by now).
        if let Some(mut rx) = self.pending_supabase_uuid_rx.take() {
            self.pending_supabase_uuid = rx.try_recv().unwrap_or(None);
        }

        let cfg = self.config_loader.current();
        let bar_ms = bar.time.as_u64() as i64;
        self.metrics.bars_processed += 1;

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
        let o = bar.open.to_f32() as f64;
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
        let bar_vpin = if bar_buy + bar_sell > 0.0 {
            Some(((bar_buy - bar_sell) / (bar_buy + bar_sell)).abs())
        } else {
            None
        };
        let bar_big_cvd = self.bar_big_buy_vol - self.bar_big_sell_vol;
        let bar_vol_usd = (bar_buy + bar_sell) * c;

        self.bar_buy_vol = 0.0;
        self.bar_sell_vol = 0.0;
        self.bar_big_buy_vol = 0.0;
        self.bar_big_sell_vol = 0.0;

        self.bars.push_back(bar);
        if self.bars.len() > VP_WINDOW {
            self.bars.pop_front();
        }

        self.cvd_history.push_back(self.cvd);
        if self.cvd_history.len() > CVD_WINDOW {
            self.cvd_history.pop_front();
        }
        self.bar_delta_history.push_back(bar_delta);
        if self.bar_delta_history.len() > CVD_WINDOW {
            self.bar_delta_history.pop_front();
        }

        let closes: Vec<f64> = self.bars.iter().map(|b| b.close.to_f32() as f64).collect();
        let highs: Vec<f64> = self.bars.iter().map(|b| b.high.to_f32() as f64).collect();
        let lows: Vec<f64> = self.bars.iter().map(|b| b.low.to_f32() as f64).collect();

        let atr = compute_atr(&highs, &lows, &closes, ATR_WINDOW);
        let regime_window = &closes[closes.len().saturating_sub(REGIME_WINDOW)..];
        let regime = derive_regime_with_hysteresis(regime_window, atr, self.last_regime_enum);

        // Compute slow/fast slopes for diagnostics (mirrors derive_regime internals)
        let slow_slope = compute_ols_slope(regime_window, atr);
        let fast_slope =
            compute_ols_slope(&regime_window[regime_window.len().saturating_sub(5)..], atr);

        // Apply the same fast_slope override used in the intrabar evaluator so that
        // bar-close detectors and intrabar detectors see the same effective regime.
        // last_regime_enum stays on the canonical (pre-override) regime so hysteresis
        // is not corrupted by transient fast_slope spikes.
        let effective_regime = if fast_slope < -0.35 && regime == Regime::TrendUp {
            Regime::TrendDown
        } else if fast_slope > 0.35 && regime == Regime::TrendDown {
            Regime::TrendUp
        } else {
            regime
        };

        let regime_str = format!("{effective_regime:?}");

        // Detect regime changes and trigger config reload
        if self.last_regime.as_deref() != Some(&regime_str) {
            self.config_loader.request_reload(&regime_str);
            let duration_ms = self.regime_started_at_ms.map(|t| bar_ms - t);
            // Bug #4 fix: persist regime changes to Supabase regime_history table
            if let Some(sb) = self.supabase.clone() {
                let rs = regime_str.clone();
                tokio::spawn(async move {
                    sb.write_regime_change(bar_ms, &rs, &rs, &rs, duration_ms, c);
                });
            }
            self.regime_started_at_ms = Some(bar_ms);
            self.last_regime = Some(regime_str.clone());
            self.last_regime_enum = regime;
        }
        // Mantener ventana de 25 regímenes para el filtro expansion_n del detector RBF.
        self.regime_hist_25.push_back(effective_regime);
        if self.regime_hist_25.len() > 25 {
            self.regime_hist_25.pop_front();
        }

        let cvd_slope = compute_cvd_slope(&self.cvd_history);
        let cvd_divergence = derive_cvd_divergence(&highs, &lows, cvd_slope);

        let (poc, vah, val, hvn_nearby, lvn_nearby) =
            compute_volume_profile(&self.bars, VP_BINS, c);

        // VP open bias: update tracker with current bar's data and VP snapshot
        let vp_open_bias = self
            .vp_bias_tracker
            .update(bar_ms, o, h, l, poc, vah, val)
            .cloned();

        // Naked POC: track previous-session POCs not yet revisited (0.05% touch band)
        self.naked_poc_tracker.update(bar_ms, poc, c, 0.0005);

        // TPO single prints: update 30-min Market Profile tracker
        self.tpo_tracker
            .update(bar_ms, h, l, c, if atr > 0.0 { Some(atr) } else { None });

        // HTF VP cascade: update weekly and monthly VP trackers
        let htf_weekly = self
            .htf_weekly_tracker
            .update(bar_ms, h, l, c, vol, c)
            .cloned();
        let htf_monthly = self
            .htf_monthly_tracker
            .update(bar_ms, h, l, c, vol, c)
            .cloned();

        let footprint_levels = self.current_footprint_levels();
        let recent_deltas_fa: Vec<f64> = self.bar_delta_history.iter().copied().collect();
        let (failed_acceptance, footprint_absorption_estimate) =
            derive_failed_acceptance_and_absorption(
                &highs,
                &lows,
                &closes,
                vah,
                val,
                &recent_deltas_fa,
                cvd_slope,
            );
        let footprint_absorption_real =
            derive_footprint_absorption_at_value_edge(&footprint_levels, vah, val, atr, cvd_slope);
        let footprint_absorption = match footprint_absorption_real {
            AbsorptionSide::Bid | AbsorptionSide::Ask => footprint_absorption_real,
            _ => footprint_absorption_estimate,
        };

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

        // OI momentum alignment: OI must be EXPANDING (new positions) AND align with price.
        // Declining OI (delta ≤ 0) = positions closing (covering/liquidation), never trend fuel.
        // Bug fix: old code (price_rising == (delta > 0.0)) gave true when both were false,
        // i.e., OI declining while price falls — that's not conviction, it's distribution.
        let oi_momentum_aligned = oi_delta.map(|delta| {
            let bars_back = self.bars.len().saturating_sub(6);
            let px_5bars_ago = self
                .bars
                .get(bars_back)
                .map(|b| b.close.to_f32() as f64)
                .unwrap_or(c);
            let price_rising = c > px_5bars_ago;
            delta > 0.0 && (price_rising == (delta > 0.0))
        });
        // Mantener ventana de 25 valores de OI momentum para el gate pre-breakout de RBF.
        self.oi_mom_hist_25
            .push_back(oi_momentum_aligned.unwrap_or(false));
        if self.oi_mom_hist_25.len() > 25 {
            self.oi_mom_hist_25.pop_front();
        }
        self.delta_hist_25.push_back(bar_delta);
        if self.delta_hist_25.len() > 25 {
            self.delta_hist_25.pop_front();
        }

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
        let mss_active = prior_swing_high.is_finite()
            && prior_swing_low.is_finite()
            && (c > prior_swing_high || c < prior_swing_low);

        // Sweep: in the last 3 bars price pierced a swing extreme intrabar but
        // closed back inside — classic liquidity grab.
        let sweep_confirmed =
            if n >= 4 && prior_swing_high.is_finite() && prior_swing_low.is_finite() {
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
            let mut prefix_low = vec![0.0f64; n];
            prefix_high[0] = bars_vec[0].high.to_f32() as f64;
            prefix_low[0] = bars_vec[0].low.to_f32() as f64;
            for i in 1..n {
                prefix_high[i] = prefix_high[i - 1].max(bars_vec[i].high.to_f32() as f64);
                prefix_low[i] = prefix_low[i - 1].min(bars_vec[i].low.to_f32() as f64);
            }
            // Scan newest→oldest (skip index 0 — needs at least one prior bar).
            // BOS at bar i: close breaks above all-time high of bars[0..i-1]
            //               or below all-time low of bars[0..i-1].
            let bos_idx = (1..n.saturating_sub(1)).rev().find(|&i| {
                let cl = bars_vec[i].close.to_f32() as f64;
                let ref_high = prefix_high[i - 1];
                let ref_low = prefix_low[i - 1];
                cl > ref_high || cl < ref_low
            });
            bos_idx
                .map(|anchor| {
                    let (cum_pv, cum_vol) =
                        bars_vec[anchor..]
                            .iter()
                            .fold((0.0_f64, 0.0_f64), |(pv, v), b| {
                                let bh = b.high.to_f32() as f64;
                                let bl = b.low.to_f32() as f64;
                                let bc = b.close.to_f32() as f64;
                                let bv = b.volume.total().to_f32_lossy() as f64;
                                (pv + (bh + bl + bc) / 3.0 * bv, v + bv)
                            });
                    if cum_vol > 0.0 { cum_pv / cum_vol } else { 0.0 }
                })
                .filter(|&v| v > 0.0)
        } else {
            None
        };

        // AVWAP-Event: anchored VWAP from the most recent significant event
        // (funding rate extreme OR liquidation cascade). Gives context for
        // how price has traded since the event that changed market structure.
        let liq_snap_for_event = self.liq_tracker.snapshot(bar_ms as i64);
        if let Some(rate) = self.funding_rate {
            if rate.abs() > 0.0006 {
                self.avwap_event_anchor_ms = Some(bar_ms as i64);
            }
        }
        if liq_snap_for_event.cascade_detected {
            self.avwap_event_anchor_ms = Some(bar_ms as i64);
        }
        let avwap_event: Option<f64> = if let Some(anchor_ms) = self.avwap_event_anchor_ms {
            let bars_vec: Vec<_> = self.bars.iter().collect();
            // Approximate bars-back using configured TF_MIN (default 5).
            // Kline ring buffer doesn't store per-bar timestamps; use ratio of elapsed time.
            let tf_min = std::env::var("TF_MIN")
                .ok()
                .and_then(|s| s.parse::<i64>().ok())
                .unwrap_or(5);
            let tf_ms_approx = tf_min * 60_000;
            let bars_back = ((bar_ms as i64 - anchor_ms) / tf_ms_approx).max(0) as usize;
            let start_idx = bars_vec.len().saturating_sub(bars_back + 1);
            if start_idx < bars_vec.len() {
                let (cum_pv, cum_vol) =
                    bars_vec[start_idx..]
                        .iter()
                        .fold((0.0_f64, 0.0_f64), |(pv, v), b| {
                            let bh = b.high.to_f32() as f64;
                            let bl = b.low.to_f32() as f64;
                            let bc = b.close.to_f32() as f64;
                            let bv = b.volume.total().to_f32_lossy() as f64;
                            (pv + (bh + bl + bc) / 3.0 * bv, v + bv)
                        });
                if cum_vol > 0.0 {
                    Some(cum_pv / cum_vol)
                } else {
                    None
                }
            } else {
                None
            }
        } else {
            None
        };

        let vwap_ctx = build_vwap_context(c, self.vwap_session, avwap_bos, avwap_event);
        let mut vp_ctx =
            build_volume_profile_context(c, poc, vah, val, hvn_nearby.clone(), lvn_nearby.clone());
        vp_ctx.naked_pocs = self.naked_poc_tracker.naked_poc_prices();
        vp_ctx.single_prints = self.tpo_tracker.single_print_mids();
        // Snapshot VP lists before vp_ctx is moved into StrategyMarketContext
        let frozen_hvn = vp_ctx.hvn_nearby.clone();
        let frozen_lvn = vp_ctx.lvn_nearby.clone();
        let mut ob_ctx = match &self.depth {
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
        ob_ctx.spoof = Some(self.spoof_detector.context().clone());

        let bid_wall_nearby = wall_nearby(&ob_ctx.walls_below, c, atr);
        let ask_wall_nearby = wall_nearby(&ob_ctx.walls_above, c, atr);
        let price_action_clean = {
            let recent_5 = &closes[closes.len().saturating_sub(5)..];
            count_price_reversals(recent_5) <= 2
        };

        let recent_deltas: Vec<f64> = self.bar_delta_history.iter().copied().collect();
        // FBG (Footprint Buy/Bear Gap) is the strongest signal: one side completely absent.
        // Falls back to delta-direction stacking, then to multi-bar delta streak.
        let fbg = derive_fbg_imbalance(&footprint_levels, 3);
        let stacked_imbalance = if matches!(fbg, ImbalanceSide::Bullish | ImbalanceSide::Bearish) {
            fbg
        } else {
            let footprint_stacked = derive_stacked_imbalance_from_levels(&footprint_levels, 3);
            match footprint_stacked {
                ImbalanceSide::Bullish | ImbalanceSide::Bearish => footprint_stacked,
                _ => derive_stacked_imbalance(&recent_deltas),
            }
        };

        // VPIN CDF: percentile rank of current bar_vpin vs rolling 50-bar history
        if let Some(v) = bar_vpin {
            self.vpin_history.push_back(v);
            if self.vpin_history.len() > 50 {
                self.vpin_history.pop_front();
            }
        }
        let vpin_cdf = bar_vpin.and_then(|v| {
            let n = self.vpin_history.len();
            if n < 3 {
                return None;
            }
            let below = self.vpin_history.iter().filter(|&&x| x <= v).count();
            Some(below as f64 / n as f64)
        });

        // CVD divergence persistence: count consecutive bars where CVD direction ≠ price direction
        let prev_close = self
            .bars
            .iter()
            .rev()
            .nth(1)
            .map(|b| b.close.to_f32() as f64);
        let prev_cvd = self.cvd_history.iter().rev().nth(1).copied();
        let cvd_divergence_persistence = if let (Some(pc), Some(pcvd)) = (prev_close, prev_cvd) {
            let price_up = c > pc;
            let cvd_up = self.cvd > pcvd;
            if price_up != cvd_up {
                // Divergence: price and CVD moving in opposite directions
                let sign = if price_up { 1 } else { -1 }; // positive=bearish div, negative=bullish div
                self.cvd_divergence_bars = if self.cvd_divergence_bars * sign > 0 {
                    self.cvd_divergence_bars + sign
                } else {
                    sign
                };
            } else {
                self.cvd_divergence_bars = 0;
            }
            Some(self.cvd_divergence_bars)
        } else {
            None
        };

        let mut flow = build_flow_context(
            Some(self.cvd),
            cvd_slope,
            Some(bar_delta),
            Some(bar_buy),
            Some(bar_sell),
            bar_vpin,
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
            stacked_imbalance,
            mss_active,
            sweep_confirmed,
            Some(fast_slope),
            self.oi_tracker.delta_zscore(),
            vpin_cdf,
            cvd_divergence_persistence,
        );
        flow.footprint_levels = footprint_levels.clone();
        {
            let (fb, fbe, ub, ube) = derive_finish_unfinish_action(&flow.footprint_levels);
            flow.finish_action_bullish = fb;
            flow.finish_action_bearish = fbe;
            flow.unfinish_action_bullish = ub;
            flow.unfinish_action_bearish = ube;
            let (btb, btbe) = derive_big_trade(&flow.footprint_levels);
            flow.big_trade_bullish = btb;
            flow.big_trade_bearish = btbe;
        }
        // Delta velocity: OLS slope of per-bar delta, last 5 bars, normalized by ATR
        {
            let recent: Vec<f64> = self.bar_delta_history.iter().copied().collect();
            flow.delta_velocity = derive_delta_velocity(&recent, 5, atr);
        }
        // Big-trade tape metrics (Fabio: "las órdenes grandes son las que importan")
        flow.big_trade_cvd_bar = bar_big_cvd;
        flow.big_trade_cvd_session = self.scalping_state.big_cvd_session;
        flow.session_vol_usd = self.scalping_state.session_vol_usd;

        self.ms_tracker.push_bar(o, h, l, c, bar_ms);
        let market_structure = self.ms_tracker.snapshot();
        self.ob_detector.push_bar(o, h, l, c, vol, bar_ms);
        let order_blocks = self.ob_detector.snapshot(c);
        self.fvg_detector.push_bar(h, l, bar_ms);
        let fvg = self.fvg_detector.snapshot(c);
        self.range_detector.push_bar(h, l, c);
        let range_ctx = self.range_detector.compute(c, atr, poc);
        let session = classify_session(bar_ms);

        let current_oi = self
            .oi_history
            .back()
            .copied()
            .or_else(|| {
                let snap = self.oi_tracker.snapshot();
                (snap.current > 0.0).then_some(snap.current)
            })
            .unwrap_or(0.0);
        let swing_high = market_structure.as_ref().and_then(|ms| ms.range_high);
        let swing_low = market_structure.as_ref().and_then(|ms| ms.range_low);
        self.liq_map_tracker
            .update(c, swing_high, swing_low, current_oi, bar_ms);
        self.liq_map_snapshot = Some(self.liq_map_tracker.snapshot().clone());

        // Prune stale liquidation events before building the snapshot
        self.liq_tracker.prune(bar_ms);
        let liq_snap = self.liq_tracker.snapshot(bar_ms);
        self.liq_tracker.record_bar_total(liq_snap.total_usd_5m);
        let ls_snap = self.ls_tracker.snapshot();
        let oi_snap = self.oi_tracker.snapshot();
        let fund_snap = self.funding_tracker.snapshot();
        let smart_money_score = Some(compute_smart_money_score(
            &ls_snap, &oi_snap, &fund_snap, &liq_snap,
        ));

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
            smart_money_score,
            liq_map: self.liq_map_snapshot.clone(),
        });

        // Confirmed swing high/low: pivot with 2-bar bilateral confirmation.
        // Window = 25 bars gives enough history to find at least one pivot in normal markets.
        const SWING_WINDOW: usize = 25;
        let sw_n = n.min(SWING_WINDOW);
        let (swing_high_20, swing_low_20) = if sw_n >= 2 * SWING_CONFIRM_BARS + 1 {
            derive_confirmed_swings(&highs[n - sw_n..n], &lows[n - sw_n..n], SWING_CONFIRM_BARS)
        } else {
            (None, None)
        };

        let current_obi_l5 = ob_ctx.obi_l5.unwrap_or(0.0);
        let mut ctx = StrategyMarketContext {
            symbol: symbol.to_string(),
            timestamp_ms: bar_ms,
            price: c,
            regime: effective_regime,
            atr: if atr > 0.0 { Some(atr) } else { None },
            volume_profile: vp_ctx,
            vwap: vwap_ctx,
            flow,
            orderbook: ob_ctx,
            institutional,
            swing_high_20,
            swing_low_20,
            market_structure: market_structure.clone(),
            session: Some(session.clone()),
            order_blocks: Some(order_blocks.clone()),
            fvg: Some(fvg.clone()),
            leverage: self.paper.config.leverage,
            prev_obi_l5: Some(self.prev_obi_l5),
            slow_slope: Some(slow_slope),
            auction_state: None, // populated below after ctx is built
            vp_open_bias: vp_open_bias.clone(),
            htf_vp: {
                let weekly = htf_weekly.clone();
                let monthly = htf_monthly.clone();
                if weekly.is_some() || monthly.is_some() {
                    Some(data::strategy::vp_open_bias::HtfVpContext { weekly, monthly })
                } else {
                    None
                }
            },
            range: if range_ctx.valid {
                Some(range_ctx.clone())
            } else {
                None
            },
        };
        // Auction state requires the full context, so classify after building it
        let auction = data::strategy::auction_state::classify(&ctx);
        ctx.auction_state = Some(auction);
        self.prev_obi_l5 = current_obi_l5;

        let (signal, detector_log) = route_strategy(&ctx, &cfg);
        let reasoning = classify_playbook_reasoning(&ctx, &cfg, &signal, &detector_log);
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

        // ── Microestructura por barra — siempre, independiente del scalping engine ──
        let bar_liq_ratio = {
            let snap = self.liq_tracker.snapshot(bar_ms);
            snap.total_zscore.map(|z| z.abs()).unwrap_or(0.0)
        };
        let bar_spread_ticks = self.spread_ticks_now();
        let bar_obi_l5 = ctx.orderbook.obi_l5.unwrap_or(0.0);
        let bar_obi_l10 = ctx.orderbook.obi_l10.unwrap_or(0.0);
        let bar_obi_l20 = ctx.orderbook.obi_l20.unwrap_or(0.0);
        // EMA 5-bar y 20-bar de OBI L5
        self.obi_ema_fast = self.obi_ema_fast * (1.0 - 0.333) + bar_obi_l5 * 0.333;
        self.obi_ema_slow = self.obi_ema_slow * (1.0 - 0.095) + bar_obi_l5 * 0.095;

        // ── Flush buffer OBI intrabar → Supabase obi_10s ──────────────────────
        let (obi_mean_intrabar, obi_min_intrabar, obi_max_intrabar) = if self.obi_intrabar.is_empty() {
            (None, None, None)
        } else {
            let mean_l5 = self.obi_intrabar.iter().map(|s| s.1 as f64).sum::<f64>()
                / self.obi_intrabar.len() as f64;
            let min_l5 = self.obi_intrabar.iter().map(|s| s.1).fold(f32::MAX, f32::min) as f64;
            let max_l5 = self.obi_intrabar.iter().map(|s| s.1).fold(f32::MIN, f32::max) as f64;
            (Some(mean_l5), Some(min_l5), Some(max_l5))
        };
        let bar_spread_bps = ctx.orderbook.spread_bps.unwrap_or(0.0);
        if let Some(sb) = self.supabase.clone() {
            let samples = std::mem::take(&mut self.obi_intrabar);
            let sym_c = symbol.to_string();
            tokio::spawn(async move {
                sb.write_obi_batch(&sym_c, &samples);
            });
        } else {
            self.obi_intrabar.clear();
        }
        // EMA-240M1 ≈ 4H para contexto estructural HTF (FASE 2.6)
        {
            const H1_ALPHA: f64 = 2.0 / (60.0 + 1.0); // ≈ 0.03279 — EMA-60M1 = 1H
            if self.ema_h1_bars == 0 {
                self.ema_h1 = c;
            } else {
                self.ema_h1 = self.ema_h1 * (1.0 - H1_ALPHA) + c * H1_ALPHA;
            }
            if self.ema_h1_bars < 60 {
                self.ema_h1_bars += 1;
            }
        }
        let htf_h1_trend: Option<String> = if self.ema_h1_bars >= 60 {
            Some(if c > self.ema_h1 {
                "Bull".into()
            } else {
                "Bear".into()
            })
        } else {
            None
        };

        // ── Scalping engine (S1/OBI, S2/Absorption, S3/CVD Divergence) ────────
        if cfg.scalping.enabled {
            let scalping_regime = match effective_regime {
                Regime::TrendUp | Regime::TrendDown => ScalpingRegime::Trend,
                _ => ScalpingRegime::Range,
            };
            let liq_ratio = bar_liq_ratio;
            let obi_l5 = bar_obi_l5;
            let micro_price = ctx.orderbook.microprice.unwrap_or(c);
            let spread_ticks = bar_spread_ticks;

            // Actualizar historiales del scalping state
            self.scalping_state.on_bar_close(
                self.cvd,
                bar_delta,
                vol,
                h,
                l,
                session.session,
                bar_big_cvd,
                bar_vol_usd,
            );

            // Construir ScalpingContext para los detectores
            let scalp_ctx = self.scalping_state.build_context(
                obi_l5,
                micro_price,
                spread_ticks,
                self.cvd,
                cvd_slope,
                o,
                h,
                l,
                c,
                bar_delta,
                vol,
                atr,
                scalping_regime,
                session.session,
                self.vwap_session,
                poc,
                self.funding_rate,
                liq_ratio,
                bar_ms,
                if range_ctx.valid {
                    Some(range_ctx.clone())
                } else {
                    None
                },
                footprint_levels.clone(),
                ctx.flow.big_trade_bullish,
                ctx.flow.big_trade_bearish,
            );

            // Cerrar posición si la sesión terminó
            if self.scalping_state.paper.has_position() {
                if !data::strategy::scalping::is_scalping_session(session.session) {
                    self.scalping_state.paper.close(
                        c,
                        bar_ms,
                        data::strategy::scalping::paper::ScalpingExitReason::SessionEnd,
                    );
                    self.scalping_state.active_signal = None;
                    self.scalping_state.signal_entry_ms = None;
                }
            }

            // Detectar nuevas señales si no hay posición activa
            if !self.scalping_state.paper.has_position()
                && self.scalping_state.paper.can_trade(
                    cfg.scalping.max_trades_per_session,
                    cfg.scalping.daily_loss_limit_pct,
                    cfg.scalping.max_consecutive_losses,
                )
            {
                let scalp_signal = absorption::detect(&scalp_ctx, &cfg.scalping)
                    .or_else(|| cvd_div_scalping::detect(&scalp_ctx, &cfg.scalping))
                    .or_else(|| obi_maker::detect(&scalp_ctx, &cfg.scalping));

                if let Some(sig) = scalp_signal {
                    println!(
                        "[scalping] SIGNAL strategy={} side={:?} entry={:.1} sl={:.1} \
                         tp1={:.1} tp2={:.1} rr={:.2} score={:.1} type={}",
                        sig.strategy,
                        sig.side,
                        sig.entry_price,
                        sig.stop_price,
                        sig.tp1_price,
                        sig.tp2_price,
                        sig.rr,
                        sig.conviction_score,
                        sig.entry_type,
                    );

                    // Persistir señal en Supabase
                    if let Some(sb) = &self.supabase {
                        let session_str = format!("{:?}", session.session);
                        let regime_str = format!(
                            "{}",
                            if scalping_regime == ScalpingRegime::Range {
                                "Range"
                            } else {
                                "Trend"
                            }
                        );
                        let write_ctx = supabase_writer::ScalpingWriteCtx {
                            session: &session_str,
                            obi: obi_l5,
                            obi_ema_fast: self.scalping_state.obi_ema_fast,
                            dz: scalp_ctx.dz,
                            vr: scalp_ctx.vr,
                            cvd: self.cvd,
                            spread_ticks,
                            atr,
                            regime: &regime_str,
                            liq_ratio,
                            range_high: scalp_ctx.range.as_ref().map(|r| r.range_high),
                            range_low: scalp_ctx.range.as_ref().map(|r| r.range_low),
                            range_mid: scalp_ctx.range.as_ref().map(|r| r.range_mid),
                            range_location: scalp_ctx.range.as_ref().map(|r| match r.location {
                                data::detectors::range_detector::RangeLocation::NearHigh => {
                                    "NearHigh"
                                }
                                data::detectors::range_detector::RangeLocation::NearLow => {
                                    "NearLow"
                                }
                                data::detectors::range_detector::RangeLocation::NoTrade => {
                                    "NoTrade"
                                }
                                _ => "Inside",
                            }),
                        };
                        sb.write_scalping_signal(&sig, &write_ctx);
                    }

                    self.scalping_state.paper.open(
                        sig.clone(),
                        bar_ms,
                        scalp_ctx.obi_ema_fast,
                        obi_l5,
                        scalp_ctx.dz,
                        scalp_ctx.vr,
                        self.cvd,
                        cvd_slope,
                        spread_ticks,
                    );
                    self.scalping_state.active_signal = Some(sig);
                    self.scalping_state.signal_entry_ms = Some(bar_ms);
                }
            }

            // Log de diagnóstico scalping por barra (siempre visible, sin señal)
            {
                let dz = self.scalping_state.compute_dz();
                let vr = self.scalping_state.compute_vr();
                let obi = self.scalping_state.obi_ema_fast;
                let cvd = self.scalping_state.cvd_session;
                let slope = self
                    .scalping_state
                    .compute_cvd_slope(10)
                    .map(|s| format!("{:+.1}", s))
                    .unwrap_or_else(|| "n/a".into());
                let can = self.scalping_state.paper.can_trade(
                    cfg.scalping.max_trades_per_session,
                    cfg.scalping.daily_loss_limit_pct,
                    cfg.scalping.max_consecutive_losses,
                );
                let ses = data::strategy::scalping::is_scalping_session(session.session);
                println!(
                    "[scalping] session={ses} can_trade={can} obi={obi:.3} dz={dz:.2} vr={vr:.2} cvd_ses={cvd:.0} slope={slope} trades={}/{}",
                    self.scalping_state.paper.daily_trades, cfg.scalping.max_trades_per_session,
                );
            }

            // Persistir cualquier trade cerrado que aún no se haya escrito a Supabase.
            // scalping_paper_written_idx rastrea hasta qué índice ya se persistió.
            if let Some(sb) = &self.supabase {
                let written = self.scalping_paper_written_idx;
                let total = self.scalping_state.paper.closed_trades.len();
                if total > written {
                    for trade in &self.scalping_state.paper.closed_trades[written..] {
                        sb.write_scalping_trade(trade);
                    }
                    self.scalping_paper_written_idx = total;
                }
            }
        }

        // ── Broadcast barra al dashboard web ─────────────────────────────────
        ws_server::broadcast(
            &self.ws_tx,
            ws_server::WsEvent::Bar {
                ts_ms: bar_ms,
                open: o,
                high: h,
                low: l,
                close: c,
                regime: format!("{:?}", effective_regime),
            },
        );

        // ── Range Breakout Flow detector + paper trader ───────────────────────
        {
            // 1) Resolver UUID pendiente PRIMERO — antes de cualquier cierre de posición.
            //    Así trades que cierran por session-end o stop en el mismo bar ya tienen el ID.
            if let Some(mut rx) = self.rbf_pending_id_rx.take() {
                match rx.try_recv() {
                    Ok(maybe_id) => {
                        if let Some(id) = maybe_id {
                            // Si hay un outcome pendiente de escribir (trade cerró antes de que
                            // llegara el ID), escribir ahora que tenemos el ID.
                            if let Some(pending) = self.rbf_pending_outcome.take() {
                                if let Some(sb) = &self.supabase {
                                    sb.update_rbf_outcome(&id, &pending);
                                }
                            } else {
                                // Marcar como activo en Supabase (persistencia cross-deploy)
                                if let Some(sb) = &self.supabase {
                                    sb.mark_rbf_active(&id);
                                }
                                self.rbf_paper.set_supabase_id(id);
                            }
                        }
                    }
                    Err(_) => {
                        // Todavía no llegó — devolver al slot para el próximo bar
                        self.rbf_pending_id_rx = Some(rx);
                    }
                }
            }

            // Helper inline para escribir un outcome cerrado
            // (evita duplicar la lógica en los dos bloques siguientes)
            macro_rules! write_outcome {
                ($trade:expr) => {{
                    let trade = $trade;
                    println!(
                        "[rbf_paper] {:?} {} entry={:.1} exit={:.1} R={:.2} day_R={:.2}",
                        trade.direction,
                        trade.exit_reason.as_str(),
                        trade.entry_price,
                        trade.exit_price,
                        trade.result_r,
                        self.rbf_paper.day_r,
                    );
                    if let (Some(sb), Some(id)) = (&self.supabase, &trade.supabase_id) {
                        sb.update_rbf_outcome(id, &trade);
                    } else if trade.supabase_id.is_none() && self.rbf_pending_id_rx.is_some() {
                        self.rbf_pending_outcome = Some(trade);
                    }
                }};
            }

            // 2) Cambio de sesión — stop/target tienen prioridad sobre SESSION_END.
            //    Esto también cubre el caso de startup con posición restaurada: la primera
            //    barra siempre ve un cambio de sesión (OffHours→X), pero si el precio ya
            //    tocó stop/target lo cierra correctamente en lugar de usar el precio actual.
            if session.session != self.rbf_last_session {
                let closed_by_price = if self.rbf_paper.has_position() {
                    if let Some(trade) = self.rbf_paper.on_bar_close(h, l, c, bar_ms, atr) {
                        write_outcome!(trade);
                        true
                    } else {
                        false
                    }
                } else {
                    false
                };
                if !closed_by_price {
                    if let Some(trade) = self.rbf_paper.close_session(c, bar_ms) {
                        write_outcome!(trade);
                    }
                }
                self.rbf_last_session = session.session;
            }

            // 3) Chequear stop/target en barras normales (sin cambio de sesión)
            if self.rbf_paper.has_position() {
                if let Some(trade) = self.rbf_paper.on_bar_close(h, l, c, bar_ms, atr) {
                    write_outcome!(trade);
                }
            }

            let liq_ratio_rbf = {
                let liq_snap = self.liq_tracker.snapshot(bar_ms);
                liq_snap.total_zscore.map(|z| z.abs()).unwrap_or(0.0)
            };
            let obi_rbf = ctx.orderbook.obi_l5.unwrap_or(0.0);
            let _ = liq_ratio_rbf; // usado dentro del bloque
            if !self.rbf_paper.has_position() {
                // Construir contexto de confluencia v2
                // Contar barras en régimen Expansion de las últimas 25.
                // SOL tiene correlación invertida con expansion_n → se pasa 0 para
                // que el filtro nunca se active (expansion_bars_recent=0 ≤ cualquier max).
                let expansion_bars_recent: u8 = if symbol == "SOLUSDT" || symbol == "XRPUSDT" {
                    0
                } else {
                    self.regime_hist_25
                        .iter()
                        .filter(|&&r| r == data::strategy::types::Regime::Expansion)
                        .count()
                        .min(25) as u8
                };
                let oi_mom_bars_recent: u8 =
                    self.oi_mom_hist_25.iter().filter(|&&v| v).count().min(25) as u8;
                let cum_delta_25b: f64 = self.delta_hist_25.iter().sum();
                let rbf_gate = data::strategy::detectors::range_breakout_flow::RbfGateContext {
                    stacked_imbalance_bearish: stacked_imbalance == ImbalanceSide::Bearish,
                    stacked_imbalance_bullish: stacked_imbalance == ImbalanceSide::Bullish,
                    absorption_ask: footprint_absorption == AbsorptionSide::Ask,
                    absorption_bid: footprint_absorption == AbsorptionSide::Bid,
                    lvn_nearby: !lvn_nearby.is_empty(),
                    thin_zone_below: ctx.orderbook.thin_zone_below,
                    thin_zone_above: ctx.orderbook.thin_zone_above,
                    bid_wall_nearby,
                    ask_wall_nearby,
                    hvn_levels: hvn_nearby.clone(),
                    vpin: bar_vpin,
                    oi_momentum_aligned,
                    session_cvd: self.scalping_state.cvd_session,
                    big_trade_cvd_session: self.scalping_state.big_cvd_session,
                    oi_delta_pct: {
                        let front = self.oi_history.front().copied().unwrap_or(0.0);
                        oi_delta.map(|d| {
                            if front.abs() > 1e-9 {
                                d / front * 100.0
                            } else {
                                0.0
                            }
                        })
                    },
                    cvd_divergence_bars: ctx.flow.cvd_divergence_persistence,
                    htf_h1_trend: htf_h1_trend.clone(),
                    vp_open_bias: ctx.vp_open_bias.as_ref().map(|v| format!("{:?}", v.bias)),
                    atr,
                    expansion_bars_recent,
                    oi_mom_bars_recent,
                    cum_delta_25b,
                    obi_l10: bar_obi_l10,
                    obi_l20: bar_obi_l20,
                    spread_bps: bar_spread_bps,
                    obi_mean_intrabar,
                };
                self.last_rbf_gate = Some(rbf_gate.clone());
                // Config por símbolo — calibraciones promovidas desde backtest app.
                let mut rbf_cfg = cfg.range_breakout.clone();
                // ETH London: backtest/app mostró edge débil. Se llama al detector con
                // enabled=false para mantener warmup/buffers, pero no emitir señal.
                if symbol == "ETHUSDT"
                    && matches!(
                        session.session,
                        data::session::session_tracker::TradingSession::London
                    )
                {
                    rbf_cfg.enabled = false;
                }
                // expansion_max_bars: BTC/ETH/BNB → Some(1). SOL/XRP quedan en bypass.
                // La app calibrada usa expansión estricta para evitar moves ya maduros.
                rbf_cfg.expansion_max_bars = match symbol {
                    "SOLUSDT" | "XRPUSDT" => None,
                    _ => Some(1),
                };
                // Gates por símbolo promovidos desde el backend de backtest.
                match symbol {
                    "BTCUSDT" => {
                        rbf_cfg.cum_delta_max_short = Some(200.0);
                        rbf_cfg.sweep_min_risk_usd = 15.0; // filtro anti-ruido: wick < $15 = spread
                    }
                    "BNBUSDT" => {
                        rbf_cfg.cum_delta_min_short = Some(-500.0);
                        rbf_cfg.sweep_min_risk_usd = 0.5;  // filtro anti-ruido: wick < $0.50 = spread
                    }
                    "SOLUSDT" => {
                        rbf_cfg.sweep_min_risk_usd = 0.08; // filtro anti-ruido: wick < $0.08 = spread
                    }
                    "ETHUSDT" => {
                        rbf_cfg.cvd_in_range_min_short = Some(-700.0);
                        rbf_cfg.obi_max_short = Some(0.10);
                        rbf_cfg.sweep_reclaim_long_enabled = false; // ETH: WR=22% en backtest
                    }
                    "XRPUSDT" => {
                        rbf_cfg.sweep_reclaim_long_enabled = false; // XRP: WR=25% en backtest
                    }
                    _ => {}
                };
                // Pre-breakout VR: en Overlap el volumen es mayor y hay más fakeouts → exigir 2.0×.
                // En London/NY mantener 1.5× (volumen moderado, precio en borde es más informativo).
                if matches!(
                    session.session,
                    data::session::session_tracker::TradingSession::LondonNyOverlap
                ) {
                    rbf_cfg.pre_breakout_vr_min = rbf_cfg.pre_breakout_vr_min.max(2.0);
                }
                if let Some(sig) = self.rbf_state.on_bar_close(
                    o,
                    h,
                    l,
                    c,
                    vol,
                    bar_delta,
                    session.session,
                    bar_ms,
                    &rbf_cfg,
                    self.vwap_session,
                    self.funding_rate,
                    liq_ratio_rbf,
                    obi_rbf,
                    cvd_slope,
                    Some(&rbf_gate),
                ) {
                    // score=4: WR=16.7% avg=-0.62R n=18 → no operar (sigue registrando en Supabase)
                    let trade_min_score = rbf_cfg.min_confluence_score.max(2);
                    let tradeable = sig.veto_reason.is_none()
                        && sig.confluence_score >= trade_min_score
                        && sig.confluence_score != 4;
                    println!(
                        "[rbf] {:?} entry={:.1} rr={:.2} range={:.3}% vr={:.2}x {:?} score={}/{} veto={:?} trade={}",
                        sig.direction,
                        sig.entry_price,
                        sig.rr,
                        sig.range_pct,
                        sig.vr_at_breakout,
                        sig.macro_regime,
                        sig.confluence_score,
                        6,
                        sig.veto_reason,
                        tradeable,
                    );
                    // Broadcast señal al dashboard web
                    ws_server::broadcast(
                        &self.ws_tx,
                        ws_server::WsEvent::Signal {
                            ts_ms: sig.timestamp_ms,
                            direction: format!("{:?}", sig.direction),
                            score: Some(sig.confluence_score),
                            veto: sig.veto_reason.clone(),
                            entry: sig.entry_price,
                            rr: sig.rr,
                            session: format!("{:?}", sig.session),
                        },
                    );

                    // Siempre escribir a Supabase (incluye señales vetadas, para calibración)
                    if let Some(sb) = self.supabase.clone() {
                        let sig_c = sig.clone();
                        let sym_c = symbol.to_string();
                        let (tx, rx) = tokio::sync::oneshot::channel();
                        self.rbf_pending_id_rx = Some(rx);
                        tokio::spawn(async move {
                            let id = sb.write_rbf_signal_async(&sig_c, &sym_c).await;
                            let _ = tx.send(id);
                        });
                    }
                    // Paper trade solo si pasa veto, score mínimo y no se alcanzó el límite diario
                    if tradeable {
                        if self.rbf_paper.is_daily_limit_hit() {
                            println!(
                                "[rbf_paper] daily limit hit (day_r={:.2}R) — señal bloqueada",
                                self.rbf_paper.day_r,
                            );
                        } else {
                            println!(
                                "[rbf_paper] open {:?} score_v2={:.2} sizing={:.1}x entry={:.2}",
                                sig.direction,
                                sig.signal_score_v2,
                                sig.sizing_multiplier,
                                sig.entry_price,
                            );
                            self.rbf_paper.open(&sig, atr);
                        }
                    }
                }
            }
        }

        // ── AMD detector ──────────────────────────────────────────────────────────
        {
            use data::strategy::detectors::amd_detector::AmdContext;

            // Extraer midpoints de Order Blocks como niveles estructurales
            let ob_levels: Vec<f64> = ctx
                .order_blocks
                .as_ref()
                .map(|ob| {
                    let mut v: Vec<f64> = vec![];
                    if let Some(ref b) = ob.nearest_bullish {
                        v.push((b.high + b.low) / 2.0);
                    }
                    if let Some(ref b) = ob.nearest_bearish {
                        v.push((b.high + b.low) / 2.0);
                    }
                    v
                })
                .unwrap_or_default();

            // Extraer midpoints de FVGs como niveles estructurales
            let fvg_levels: Vec<f64> = ctx
                .fvg
                .as_ref()
                .map(|fvg| {
                    let mut v: Vec<f64> = vec![];
                    if let Some(ref b) = fvg.nearest_bullish {
                        v.push((b.high + b.low) / 2.0);
                    }
                    if let Some(ref b) = fvg.nearest_bearish {
                        v.push((b.high + b.low) / 2.0);
                    }
                    v
                })
                .unwrap_or_default();

            // vwap_dz = (close - vwap) / atr — cuántas ATRs está el spike de la VWAP
            let vwap_dz = self
                .vwap_session
                .map(|vwap| if atr > 0.0 { (c - vwap) / atr } else { 0.0 });

            let amd_ctx = AmdContext {
                vpin: bar_vpin,
                cvd_slope,
                obi_l5: ctx.orderbook.obi_l5,
                vwap: self.vwap_session,
                lvn_levels: ctx.volume_profile.lvn_nearby.clone(),
                naked_pocs: ctx.volume_profile.naked_pocs.clone(),
                ob_levels,
                fvg_levels,
                funding_rate: self.funding_rate,
                session_name: format!("{:?}", session.session),
                vwap_dz,
                liq_ratio: bar_liq_ratio,
                absorption_bid: footprint_absorption == AbsorptionSide::Bid,
                absorption_ask: footprint_absorption == AbsorptionSide::Ask,
                regime_is_trending: matches!(
                    regime,
                    data::strategy::types::Regime::TrendUp
                        | data::strategy::types::Regime::TrendDown
                        | data::strategy::types::Regime::Expansion
                ),
                session_cvd: self.scalping_state.cvd_session,
                val: ctx.volume_profile.val,
                vah: ctx.volume_profile.vah,
                bid_wall_nearby: ctx.flow.bid_wall_nearby,
                ask_wall_nearby: ctx.flow.ask_wall_nearby,
                big_trade_cvd_bar: ctx.flow.big_trade_cvd_bar,
                oi_delta_pct: {
                    let front = self.oi_history.front().copied().unwrap_or(0.0);
                    oi_delta.map(|d| {
                        if front.abs() > 1e-9 {
                            d / front * 100.0
                        } else {
                            0.0
                        }
                    })
                },
                cvd_divergence_bars: ctx.flow.cvd_divergence_persistence,
                htf_h1_trend: htf_h1_trend.clone(),
            };

            // ── AMD paper trader: resolver UUID pendiente ─────────────────────
            if let Some(mut rx) = self.amd_pending_id_rx.take() {
                match rx.try_recv() {
                    Ok(maybe_id) => {
                        if let Some(id) = maybe_id {
                            if let Some(pending) = self.amd_pending_outcome.take() {
                                // El trade cerró antes de que llegara el UUID — escribir outcome ahora
                                if let Some(sb) = &self.supabase {
                                    sb.update_amd_outcome(&id, &pending);
                                }
                            } else {
                                // is_active ya fue true en el INSERT — solo guardar el id en memoria
                                self.amd_paper.set_supabase_id(id);
                            }
                        }
                    }
                    Err(_) => {
                        self.amd_pending_id_rx = Some(rx);
                    }
                }
            }

            // ── AMD paper trader: cierre por cambio de sesión ─────────────────
            macro_rules! write_amd_outcome {
                ($trade:expr) => {{
                    let trade = $trade;
                    println!(
                        "[amd_paper] {:?} {} entry={:.4} exit={:.4} R={:.2}",
                        trade.direction,
                        trade.exit_reason.as_str(),
                        trade.entry_price,
                        trade.exit_price,
                        trade.result_r,
                    );
                    if let (Some(sb), Some(id)) = (&self.supabase, &trade.supabase_id) {
                        sb.update_amd_outcome(id, &trade);
                    } else if trade.supabase_id.is_none() && self.amd_pending_id_rx.is_some() {
                        self.amd_pending_outcome = Some(trade);
                    }
                }};
            }

            if session.session != self.amd_last_session {
                let closed_by_price = if self.amd_paper.has_position() {
                    if let Some(trade) = self.amd_paper.on_bar_close(h, l, bar_ms) {
                        write_amd_outcome!(trade);
                        true
                    } else {
                        false
                    }
                } else {
                    false
                };
                if !closed_by_price {
                    if let Some(trade) = self.amd_paper.close_session(c, bar_ms) {
                        write_amd_outcome!(trade);
                    }
                }
                self.amd_last_session = session.session;
            }

            // ── AMD paper trader: chequeo stop/target en barra normal ─────────
            if self.amd_paper.has_position() {
                if let Some(trade) = self.amd_paper.on_bar_close(h, l, bar_ms) {
                    write_amd_outcome!(trade);
                }
            }

            // ── AMD detector ──────────────────────────────────────────────────
            if let Some(sig) = self
                .amd_state
                .on_bar_close(h, l, c, vol, bar_delta, bar_ms, &amd_ctx, &cfg.amd)
            {
                println!(
                    "[amd] {:?} entry={:.4} stop={:.4} target={:.4} rr={:.2} \
                     range={:.3}% bars={} spike={:?} vr={:.2}x \
                     liq={:.2} dz={:.2} delta={:.1} src={:?} ses={} \
                     quality={}/10",
                    sig.direction,
                    sig.entry_price,
                    sig.stop_price,
                    sig.target_price,
                    sig.rr,
                    sig.range_pct,
                    sig.range_bars,
                    sig.spike_direction,
                    sig.vr_at_spike,
                    sig.liq_ratio_at_spike,
                    sig.dz_at_spike.unwrap_or(0.0),
                    sig.bar_delta_at_spike,
                    sig.target_source,
                    sig.session_name,
                    sig.quality_score,
                );

                // Filtro de sesión: Asia y OffHours excluidos (live data: 0 wins, -2.62R en Asia).
                // Score mínimo: 4/10 — score=1-3 no tiene edge con los datos actuales.
                let amd_session_ok = matches!(
                    session.session,
                    data::session::session_tracker::TradingSession::London
                        | data::session::session_tracker::TradingSession::LondonNyOverlap
                        | data::session::session_tracker::TradingSession::NewYork
                );
                let amd_tradeable =
                    amd_session_ok && sig.quality_score >= 4 && !self.amd_paper.has_position();

                if amd_tradeable {
                    // Señal operada: is_active=true en Supabase
                    if let Some(sb) = self.supabase.clone() {
                        let sig_c = sig.clone();
                        let sym_c = symbol.to_string();
                        let (tx, rx) = tokio::sync::oneshot::channel();
                        self.amd_pending_id_rx = Some(rx);
                        tokio::spawn(async move {
                            let id = sb.write_amd_signal_async(&sig_c, &sym_c, true).await;
                            let _ = tx.send(id);
                        });
                    }
                    println!(
                        "[amd_paper] open {:?} score={} ses={:?} entry={:.4}",
                        sig.direction, sig.quality_score, session.session, sig.entry_price,
                    );
                    self.amd_paper.open(&sig);
                } else {
                    // Solo registrar: sesión excluida, score bajo, o posición ya abierta
                    let skip_reason = if !amd_session_ok {
                        "session_skip"
                    } else if sig.quality_score < 4 {
                        "low_score"
                    } else {
                        "has_position"
                    };
                    println!(
                        "[amd] recorded (not traded) reason={} score={}",
                        skip_reason, sig.quality_score
                    );
                    if let Some(sb) = self.supabase.clone() {
                        let sig_c = sig.clone();
                        let sym_c = symbol.to_string();
                        tokio::spawn(async move {
                            sb.write_amd_signal_async(&sig_c, &sym_c, false).await;
                        });
                    }
                }
            }
        }

        // ── Buyer Exhaustion detector ─────────────────────────────────────────────
        {
            // Helper para escribir outcome BE
            macro_rules! write_be_outcome {
                ($trade:expr) => {{
                    let trade = $trade;
                    println!(
                        "[be_paper] {} entry={:.2} exit={:.2} R={:.2} day_R={:.2}",
                        trade.exit_reason.as_str(),
                        trade.entry_price,
                        trade.exit_price,
                        trade.result_r,
                        self.be_paper.day_r,
                    );
                    if let (Some(sb), Some(id)) = (&self.supabase, &trade.supabase_id) {
                        sb.update_be_outcome(id, &trade);
                    } else if trade.supabase_id.is_none() && self.be_pending_id_rx.is_some() {
                        self.be_pending_outcome = Some(trade);
                    }
                }};
            }

            // Resolver UUID pendiente del task async de escritura
            if let Some(mut rx) = self.be_pending_id_rx.take() {
                match rx.try_recv() {
                    Ok(maybe_id) => {
                        if let Some(id) = maybe_id {
                            if let Some(pending) = self.be_pending_outcome.take() {
                                if let Some(sb) = &self.supabase {
                                    sb.update_be_outcome(&id, &pending);
                                }
                            } else {
                                self.be_paper.set_supabase_id(id);
                            }
                        }
                    }
                    Err(_) => {
                        self.be_pending_id_rx = Some(rx);
                    }
                }
            }

            // Cambio de sesión — stop/target tienen prioridad sobre SESSION_END
            if session.session != self.be_last_session {
                let closed_by_price = if self.be_paper.has_position() {
                    if let Some(trade) = self.be_paper.on_bar_close(h, l, c, bar_ms, 30) {
                        write_be_outcome!(trade);
                        true
                    } else {
                        false
                    }
                } else {
                    false
                };
                if !closed_by_price {
                    if let Some(trade) = self.be_paper.close_session(c, bar_ms) {
                        write_be_outcome!(trade);
                    }
                }
                self.be_last_session = session.session;
            }

            // Chequeo stop/target en barra normal
            if self.be_paper.has_position() {
                if let Some(trade) = self.be_paper.on_bar_close(h, l, c, bar_ms, 30) {
                    write_be_outcome!(trade);
                }
            }

            // Detección de señal (solo si no hay posición abierta y límite diario no alcanzado)
            if !self.be_paper.has_position() {
                let be_cfg = buyer_exhaustion::config::BuyerExhaustionConfig::default();
                if let Some(sig) = self.be_state.on_bar_close(
                    h,
                    l,
                    o,
                    c,
                    vol,
                    bar_delta,
                    bar_ms,
                    symbol,
                    &be_cfg,
                    self.last_rbf_gate.as_ref(),
                ) {
                    if let Some(sb) = self.supabase.clone() {
                        let sig_c = sig.clone();
                        let sym_c = symbol.to_string();
                        let (tx, rx) = tokio::sync::oneshot::channel();
                        self.be_pending_id_rx = Some(rx);
                        tokio::spawn(async move {
                            let id = sb.write_be_signal_async(&sig_c, &sym_c).await;
                            let _ = tx.send(id);
                        });
                    }
                    if !self.be_paper.is_daily_limit_hit() {
                        println!(
                            "[be_paper] open entry={:.2} stop={:.2} target={:.2} rr={:.2}",
                            sig.entry_price, sig.stop_price, sig.target_price, sig.rr,
                        );
                        self.be_paper.open(&sig);
                    } else {
                        println!(
                            "[be_paper] daily limit hit (day_r={:.2}R) — señal bloqueada",
                            self.be_paper.day_r
                        );
                    }
                }
            }
        }

        // ── HTF Shorts detector ───────────────────────────────────────────────────
        {
            use data::strategy::detectors::mtf_shorts_detector::MtfBarContext;

            let htf_vr = {
                let vols: Vec<f64> = self.bars.iter().map(|b| b.volume.total().to_f32_lossy() as f64).collect();
                let n = vols.len().min(50);
                if n > 0 {
                    let mean = vols[vols.len()-n..].iter().sum::<f64>() / n as f64;
                    if mean > 0.0 { vol / mean } else { 0.0 }
                } else { 0.0 }
            };
            let htf_dz = {
                let n = self.bar_delta_history.len();
                if n >= 5 {
                    let mean = self.bar_delta_history.iter().sum::<f64>() / n as f64;
                    let std = (self.bar_delta_history.iter().map(|d| (d-mean).powi(2)).sum::<f64>() / n as f64).sqrt();
                    if std > 1e-8 { (bar_delta - mean) / std } else { 0.0 }
                } else { 0.0 }
            };
            let htf_eq_high = {
                let eq_tol = 0.0003;
                let highs50 = self.bars.iter().rev().take(50)
                    .map(|b| b.high.to_f32() as f64)
                    .fold(f64::NEG_INFINITY, f64::max);
                highs50 > f64::NEG_INFINITY && (h - highs50).abs() / highs50 <= eq_tol
            };

            let htf_eq_low = {
                let eq_tol = 0.0003;
                let lows50 = self.bars.iter().rev().take(50)
                    .map(|b| b.low.to_f32() as f64)
                    .fold(f64::INFINITY, f64::min);
                lows50 < f64::INFINITY && (l - lows50).abs() / lows50 <= eq_tol
            };
            let htf_funding_regime = ctx.institutional
                .as_ref()
                .map(|inst| format!("{:?}", inst.funding.regime))
                .unwrap_or_else(|| "Neutral".into());

            let htf_ctx = MtfBarContext {
                ts_ms:       bar_ms,
                open:        o,
                high:        h,
                low:         l,
                close:       c,
                cvd_slope,
                obi_l5:      ctx.orderbook.obi_l5.unwrap_or(0.0),
                obi_fast:    self.obi_ema_fast,
                vr:          htf_vr,
                oi_momentum: oi_momentum_aligned,
                equal_high:  htf_eq_high,
                equal_low:   htf_eq_low,
                dz:          htf_dz,
                absorption:  match footprint_absorption {
                    AbsorptionSide::Ask => "Ask".into(),
                    AbsorptionSide::Bid => "Bid".into(),
                    _ => "None".into(),
                },
                stacked_imb: match stacked_imbalance {
                    ImbalanceSide::Bullish => "Bullish".into(),
                    ImbalanceSide::Bearish => "Bearish".into(),
                    _ => "None".into(),
                },
                vpin:    bar_vpin.unwrap_or(0.0),
                regime:  format!("{:?}", effective_regime),
                session: format!("{:?}", session.session),
                atr,
                funding_regime: htf_funding_regime,
                vwap_session: ctx.vwap.vwap_session,
            };

            if let Some(event) = self.mtf_state.on_bar_close(&htf_ctx) {
                if let Some(sb) = self.supabase.clone() {
                    let ev = event.clone();
                    let sym = symbol.to_string();
                    tokio::spawn(async move { sb.write_mtf_trade(&ev, &sym).await; });
                }
                if event.is_open {
                    println!(
                        "[mtf] SIGNAL {} sig={} entry={:.2} stop={:.3}% d1={} session={}",
                        symbol, event.signal.sig, event.signal.entry,
                        event.signal.stop_pct, event.signal.d1_trend, event.signal.session
                    );
                } else {
                    println!(
                        "[mtf] CLOSED {} sig={} net={:+.4}R gross={:+.4}R fee={:.4}R reason={} dur={}bars",
                        symbol, event.signal.sig,
                        event.result_r.unwrap_or(0.0),
                        event.gross_r.unwrap_or(0.0),
                        event.fee_r.unwrap_or(0.0),
                        event.reason.as_deref().unwrap_or("?"),
                        event.duration_bars.unwrap_or(0)
                    );
                }
            }

            // ── HTF Longs (ETH/SOL únicamente) ──────────────────────────────
            if matches!(symbol, "ETHUSDT" | "SOLUSDT") {
                if let Some(event) = self.mtf_longs_state.on_bar_close(&htf_ctx) {
                    if let Some(sb) = self.supabase.clone() {
                        let ev = event.clone();
                        let sym = symbol.to_string();
                        tokio::spawn(async move { sb.write_mtf_long_trade(&ev, &sym).await; });
                    }
                    if event.is_open {
                        println!(
                            "[mtf_long] SIGNAL {} sig={} entry={:.2} stop={:.3}% h4={} session={}",
                            symbol, event.signal.sig, event.signal.entry,
                            event.signal.stop_pct, event.signal.h4_trend, event.signal.session
                        );
                    } else {
                        println!(
                            "[mtf_long] CLOSED {} sig={} net={:+.4}R gross={:+.4}R fee={:.4}R reason={} dur={}bars",
                            symbol, event.signal.sig,
                            event.result_r.unwrap_or(0.0),
                            event.gross_r.unwrap_or(0.0),
                            event.fee_r.unwrap_or(0.0),
                            event.reason.as_deref().unwrap_or("?"),
                            event.duration_bars.unwrap_or(0)
                        );
                    }
                }
            }
        }

        // ── RBF bar capture — microestructura por barra M1 para backtest futuro ─
        if let Some(sb) = self.supabase.clone() {
            // dz: delta z-score normalizado sobre últimas 50 barras
            let rbf_dz = {
                let n = self.bar_delta_history.len();
                if n >= 5 {
                    let mean = self.bar_delta_history.iter().sum::<f64>() / n as f64;
                    let std = (self
                        .bar_delta_history
                        .iter()
                        .map(|d| (d - mean).powi(2))
                        .sum::<f64>()
                        / n as f64)
                        .sqrt();
                    if std > 1e-8 {
                        (bar_delta - mean) / std
                    } else {
                        0.0
                    }
                } else {
                    0.0
                }
            };
            // vr: volume ratio vs media de últimas 50 barras
            let rbf_vr = {
                let vols: Vec<f64> = self
                    .bars
                    .iter()
                    .map(|b| b.volume.total().to_f32_lossy() as f64)
                    .collect();
                let n = vols.len().min(50);
                if n > 0 {
                    let mean = vols[vols.len() - n..].iter().sum::<f64>() / n as f64;
                    if mean > 0.0 { vol / mean } else { 0.0 }
                } else {
                    0.0
                }
            };
            let stacked_str = match stacked_imbalance {
                ImbalanceSide::Bullish => "Bullish",
                ImbalanceSide::Bearish => "Bearish",
                _ => "None",
            };
            let absorption_str = match footprint_absorption {
                AbsorptionSide::Bid => "Bid",
                AbsorptionSide::Ask => "Ask",
                _ => "None",
            };
            let operative = matches!(
                session.session,
                data::session::TradingSession::London
                    | data::session::TradingSession::LondonNyOverlap
            );

            // ── ICT AMD structural levels ─────────────────────────────────────
            // UTC día actual (días desde epoch)
            let bar_day = bar_ms / 86_400_000;
            let bar_hour_utc = (bar_ms / 3_600_000) % 24;

            // Asian session: 00:00–07:00 UTC — acumular H/L durante esa ventana
            if bar_hour_utc < 7 {
                if self.asian_day != bar_day {
                    // nuevo día asiático — reset
                    self.asian_high = Some(h);
                    self.asian_low = Some(l);
                    self.asian_day = bar_day;
                } else {
                    self.asian_high = Some(self.asian_high.map_or(h, |v| v.max(h)));
                    self.asian_low = Some(self.asian_low.map_or(l, |v| v.min(l)));
                }
            }

            // Daily H/L — día UTC completo; cuando cambia el día, guardar como prev_day
            if self.daily_day != bar_day {
                if self.daily_day >= 0 {
                    self.prev_day_high = Some(self.daily_high);
                    self.prev_day_low = Some(self.daily_low);
                }
                self.daily_high = h;
                self.daily_low = l;
                self.daily_day = bar_day;
            } else {
                self.daily_high = self.daily_high.max(h);
                self.daily_low = self.daily_low.min(l);
            }

            // swing_high/low_50 — rolling max/min sobre las últimas 50 barras (excluyendo la actual)
            let highs50: Vec<f64> = self
                .bars
                .iter()
                .rev()
                .take(50)
                .map(|b| b.high.to_f32() as f64)
                .collect();
            let lows50: Vec<f64> = self
                .bars
                .iter()
                .rev()
                .take(50)
                .map(|b| b.low.to_f32() as f64)
                .collect();
            let swing_high_50: Option<f64> = if highs50.is_empty() {
                None
            } else {
                Some(highs50.iter().cloned().fold(f64::NEG_INFINITY, f64::max))
            };
            let swing_low_50: Option<f64> = if lows50.is_empty() {
                None
            } else {
                Some(lows50.iter().cloned().fold(f64::INFINITY, f64::min))
            };

            // equal_high/low — ¿el high/low de esta barra está a ≤0.03% de un swing previo en la ventana de 50?
            let eq_tol = 0.0003; // 0.03%
            let equal_high = swing_high_50.map_or(false, |sh| {
                (h - sh).abs() / sh <= eq_tol && h >= sh * (1.0 - eq_tol)
            });
            let equal_low = swing_low_50.map_or(false, |sl| {
                (l - sl).abs() / sl <= eq_tol && l <= sl * (1.0 + eq_tol)
            });

            let cvd_div_str = cvd_divergence.as_ref().map(|d| match d {
                data::strategy::types::CvdDivergence::BearishAbsorption => "BearishAbsorption",
                data::strategy::types::CvdDivergence::BullishAbsorption => "BullishAbsorption",
            });
            sb.write_rbf_bar(
                symbol,
                bar_ms,
                &format!("{:?}", session.session),
                o,
                h,
                l,
                c,
                vol,
                bar_delta,
                cvd_slope,
                ctx.orderbook.obi_l5.unwrap_or(0.0),
                bar_obi_l10,
                bar_obi_l20,
                self.obi_ema_fast,
                self.obi_ema_slow,
                rbf_dz,
                rbf_vr,
                bar_liq_ratio,
                bar_spread_ticks,
                stacked_str,
                absorption_str,
                ctx.orderbook.thin_zone_above,
                ctx.orderbook.thin_zone_below,
                bid_wall_nearby,
                ask_wall_nearby,
                bar_vpin,
                oi_momentum_aligned,
                self.vwap_session,
                &format!("{:?}", effective_regime),
                atr,
                operative,
                self.asian_high,
                self.asian_low,
                self.prev_day_high,
                self.prev_day_low,
                swing_high_50,
                swing_low_50,
                equal_high,
                equal_low,
                cvd_div_str,
                sweep_confirmed,
                poc,
                vah,
                val,
                lvn_nearby.iter().copied().filter(|&p| p < c).reduce(f64::max),
                ctx.flow.big_trade_bearish,
                ctx.flow.big_trade_bullish,
                obi_min_intrabar,
                obi_max_intrabar,
            );
        }

        // Reset micro buffer (legacy DRR system — writes removed, kept for state compat)
        let next_open_ms = bar_ms + 300_000;
        self.micro_buffer.reset(next_open_ms);
        self.current_candle_open_ms = next_open_ms;

        // Take the OID of the currently open position BEFORE processing the new signal —
        // a new signal clears pending_signal_oid, which would cause write_trade to skip
        // linking any trade that closes in this same bar.
        let trade_oid = self.pending_signal_oid.take();

        let prev_closed = self.paper.closed_trades.len();
        let paper_signal = if signal_fired { Some(&signal) } else { None };
        self.paper
            .on_bar_close(symbol, c, h, l, bar_ms, paper_signal, Some(&ctx));

        for trade in self.paper.closed_trades[prev_closed..].iter() {
            if let Ok(json) = serde_json::to_string(trade) {
                println!("[trade_closed] {json}");
            }
            self.mongo.write_trade(trade, trade_oid);
            if let Some(sb) = self.supabase.clone() {
                let supabase_uuid = self.pending_supabase_uuid.clone();
                let t = trade.clone();
                tokio::spawn(async move {
                    sb.write_trade(&t, supabase_uuid);
                });
            }
        }

        // Write the new signal AFTER closing any trades, so the OID of the previous
        // signal is still intact when write_trade runs above.
        if signal_fired {
            if let Ok(json) = serde_json::to_string(&signal) {
                println!("[signal] {json}");
            }
            // MongoDB: sync, returns ObjectId for trade linking
            self.pending_signal_oid = self.mongo.write_signal(&signal, &ctx);
            // Supabase: async — capture UUID via oneshot for trade linking
            if let Some(sb) = self.supabase.clone() {
                let s = signal.clone();
                let c = ctx.clone();
                let r = reasoning.clone();
                let (tx, rx) = tokio::sync::oneshot::channel();
                self.pending_supabase_uuid_rx = Some(rx);
                tokio::spawn(async move {
                    let _ = tx.send(sb.write_signal_with_reasoning(&s, &c, &r).await);
                });
            }
        }

        let processing_ms = proc_start.elapsed().as_millis();
        let inst_ref = ctx.institutional.as_ref();
        let (inst_ok, inst_total) = self.freshness.quality(self.metrics.liq_raw_messages);
        let liq_silent = self.freshness.liq_stream_ok && self.metrics.liq_raw_messages == 0;
        let inst_label = if inst_ref.is_none() {
            "null".to_string()
        } else if inst_ok == inst_total {
            format!("Live({inst_ok}/{inst_total})")
        } else if inst_ok > 0 {
            if liq_silent {
                format!("Partial({inst_ok}/{inst_total})+LiqSilent")
            } else {
                format!("Partial({inst_ok}/{inst_total})")
            }
        } else {
            format!("Stale(0/{inst_total})")
        };
        let ws_label = format!(
            "kline:{} depth:{} trades:ok liq:{}",
            self.streams.klines, self.streams.depth, self.streams.liq
        );
        let liq_age = self.freshness.liq_age_str();
        let rbf_pos = if self.rbf_paper.has_position() {
            "open"
        } else {
            "-"
        };
        println!(
            "[bar] ts={bar_ms} close={c:.2} regime={effective_regime:?} \
             slow={slow_slope:.3} fast={fast_slope:.3} \
             funding={:.4} basis={:.3}% oi_delta={:.0} \
             vwap={:.2} cvd={:.1} ob={} \
             inst={inst_label} ls_top={:.1}%/{:.1}% liq={:.0}$ liq_age={liq_age} \
             ws=[{ws_label}] rbf={rbf_pos} bss={} delivery={delivery_lag_ms}ms proc={processing_ms}ms",
            self.funding_rate.unwrap_or(0.0) * 10_000.0,
            basis.unwrap_or(0.0),
            oi_delta.unwrap_or(0.0),
            self.vwap_session.unwrap_or(0.0),
            self.cvd,
            if self.depth.is_some() { "live" } else { "miss" },
            inst_ref
                .map(|i| i.ls_ratio.top_traders_long_pct * 100.0)
                .unwrap_or(0.0),
            inst_ref
                .map(|i| i.ls_ratio.retail_long_pct * 100.0)
                .unwrap_or(0.0),
            inst_ref.map(|i| i.liquidations.total_usd_5m).unwrap_or(0.0),
            self.metrics.bars_since_signal,
        );

        // Freeze bar-level context for intrabar evaluation during the next bar.
        // Store effective_regime (with fast_slope override) so the intrabar evaluator
        // sees the same regime as the bar-close detectors did.
        self.frozen = Some(FrozenBarCtx {
            regime: effective_regime,
            slow_slope,
            fast_slope,
            atr,
            poc,
            vah,
            val,
            hvn_nearby: frozen_hvn,
            lvn_nearby: frozen_lvn,
            vwap_session: self.vwap_session,
            avwap_bos,
            avwap_event,
            cvd_slope,
            failed_acceptance,
            footprint_absorption,
            cvd_divergence,
            stacked_imbalance,
            footprint_levels,
            mss_active,
            sweep_confirmed,
            price_action_clean,
            bid_wall_nearby,
            ask_wall_nearby,
            swing_high_20,
            swing_low_20,
            oi_momentum_aligned,
            basis,
            market_structure,
            session,
            order_blocks,
            fvg,
            liq_map: self.liq_map_snapshot.clone(),
            bar_vpin,
            bar_close_ms: bar_ms,
            bar_close_price: c,
            vp_open_bias: vp_open_bias.clone(),
            range: if range_ctx.valid {
                Some(range_ctx)
            } else {
                None
            },
        });
        self.bar_footprint.clear();
        self.intrabar_signal_fired = false;
        self.intrabar_eval_count = 0;
        self.last_intrabar_eval_price = c;
        self.last_intrabar_eval_ms = bar_ms;

        self.metrics.processing_ms.push(processing_ms);
        if self.metrics.bars_processed % 10 == 0 {
            self.metrics.liq_raw_messages = self.liq_raw_counter.load(Ordering::Relaxed);
            self.metrics.liq_global_raw_messages =
                self.liq_global_raw_counter.load(Ordering::Relaxed);
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
    let sum_xy: f64 = closes.iter().enumerate().map(|(i, y)| i as f64 * y).sum();
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
    let window = 5.min(n);
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
    Some(LongShortSnapshot {
        timestamp_ms: ts,
        long_ratio,
        short_ratio,
        ls_ratio,
        source: LsSource::TopTraderPosition,
    })
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
    Some(LongShortSnapshot {
        timestamp_ms: ts,
        long_ratio,
        short_ratio,
        ls_ratio,
        source: LsSource::GlobalAccount,
    })
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
    let taker_imbalance = if total > 0.0 {
        (buy_vol - sell_vol) / total
    } else {
        0.0
    };
    Some(TakerRatioSnapshot {
        timestamp_ms: ts,
        buy_sell_ratio,
        taker_imbalance,
    })
}

// ── forceOrder WebSocket stream ───────────────────────────────────────────────
// Binance @forceOrder is a PUBLIC stream — no API key needed.
// The old REST /fapi/v1/forceOrders endpoint requires USER_DATA auth (signature),
// which caused liq=0$ on every bar. This stream replaces it.

static LIQ_TLS: LazyLock<TlsConnector> = LazyLock::new(|| {
    let _ = aws_lc_rs::default_provider().install_default();
    let root_store = RootCertStore {
        roots: webpki_roots::TLS_SERVER_ROOTS.to_vec(),
    };
    let cfg = ClientConfig::builder()
        .with_root_certificates(root_store)
        .with_no_client_auth();
    TlsConnector::from(std::sync::Arc::new(cfg))
});

async fn connect_force_order_ws(
    symbol: &str,
) -> Result<FragmentCollector<TokioIo<Upgraded>>, Box<dyn std::error::Error + Send + Sync>> {
    let domain = "fstream.binance.com";
    // Binance migrated market streams from /ws/ to /market/ws/ (deadline Apr 2026).
    let path = format!("/market/ws/{}@forceOrder", symbol.to_lowercase());
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
        .header(
            "Sec-WebSocket-Key",
            fastwebsockets::handshake::generate_key(),
        )
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
        "BUY" => LiqSide::Shorts,
        _ => LiqSide::Neutral,
    };
    let qty: f64 = o.get("z")?.as_str()?.parse().ok()?;
    let price: f64 = o.get("ap")?.as_str()?.parse().ok()?;
    let ts: i64 = o.get("T")?.as_i64()?;
    Some(LiquidationEvent {
        timestamp_ms: ts,
        side: liq_side,
        quantity_usd: qty * price,
    })
}

/// Spawns a task that streams @forceOrder events into `tx` with auto-reconnect.
/// Sends `true` to `health_tx` on connect and `false` on disconnect.
/// Increments `raw_counter` for every text frame received (parsed or not).
fn spawn_force_order_stream(
    symbol: String,
    tx: tokio::sync::mpsc::Sender<Vec<LiquidationEvent>>,
    health_tx: tokio::sync::mpsc::Sender<bool>,
    raw_counter: Arc<AtomicU64>,
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
                    println!("[liq] forceOrder WS connected for {symbol}");
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
                                if frame.opcode != OpCode::Text {
                                    continue;
                                }
                                let text = match std::str::from_utf8(&frame.payload) {
                                    Ok(s) => s,
                                    Err(_) => continue,
                                };
                                raw_counter.fetch_add(1, Ordering::Relaxed);
                                if let Some(ev) = parse_force_order_event(text) {
                                    let _ = tx.send(vec![ev]).await;
                                } else {
                                    let preview = &text[..text.len().min(160)];
                                    eprintln!(
                                        "[liq] parse_failure (not a liquidation?): {preview}"
                                    );
                                }
                            }
                        }
                    }
                }
            }
        }
    });
}

/// Spawns a diagnostic-only task that connects to `!forceOrder@arr` (all-market
/// liquidations). Does not parse or forward events — only increments `raw_counter`
/// so [metrics] can show `liq_global_raw`. Useful to distinguish:
///   liq_raw=0 + liq_global_raw>0 → BTC was quiet, stream works
///   liq_raw=0 + liq_global_raw=0 → endpoint or parser broken
fn spawn_global_liq_counter(raw_counter: Arc<AtomicU64>) {
    tokio::spawn(async move {
        loop {
            let domain = "fstream.binance.com";
            let path = "/market/ws/!forceOrder@arr";
            let addr = format!("{domain}:443");
            let Ok(tcp) = tokio::net::TcpStream::connect(&addr).await else {
                tokio::time::sleep(Duration::from_secs(10)).await;
                continue;
            };
            let Ok(sn) = ServerName::try_from(domain.to_string()) else {
                break;
            };
            let Ok(tls) = LIQ_TLS.connect(sn, tcp).await else {
                tokio::time::sleep(Duration::from_secs(10)).await;
                continue;
            };
            let Ok(req) = Request::builder()
                .method("GET")
                .uri(path)
                .header("Host", domain)
                .header(UPGRADE, "websocket")
                .header(CONNECTION, "upgrade")
                .header(
                    "Sec-WebSocket-Key",
                    fastwebsockets::handshake::generate_key(),
                )
                .header("Sec-WebSocket-Version", "13")
                .body(Empty::<Bytes>::new())
            else {
                break;
            };
            let Ok((ws, _)) =
                fastwebsockets::handshake::client(&TokioExecutor::new(), req, tls).await
            else {
                tokio::time::sleep(Duration::from_secs(10)).await;
                continue;
            };
            println!("[liq-global] !forceOrder@arr connected");
            let mut ws = FragmentCollector::new(ws);
            loop {
                match ws.read_frame().await {
                    Err(_)
                    | Ok(fastwebsockets::Frame {
                        opcode: OpCode::Close,
                        ..
                    }) => break,
                    Ok(frame) if frame.opcode == OpCode::Text => {
                        raw_counter.fetch_add(1, Ordering::Relaxed);
                    }
                    _ => {}
                }
            }
            eprintln!("[liq-global] !forceOrder@arr disconnected — retry in 5s");
            tokio::time::sleep(Duration::from_secs(5)).await;
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
        Err(e) => {
            eprintln!("[fetch] fundingRate history failed: {e}");
            return vec![];
        }
    };
    let json: serde_json::Value = match resp.json().await {
        Ok(v) => v,
        Err(e) => {
            eprintln!("[fetch] fundingRate history parse failed: {e}");
            return vec![];
        }
    };
    json.as_array()
        .map(|arr| {
            arr.iter()
                .filter_map(|entry| {
                    let rate: f64 = entry.get("fundingRate")?.as_str()?.parse().ok()?;
                    let ts: i64 = entry.get("fundingTime")?.as_i64()?;
                    Some(FundingRateSample {
                        timestamp_ms: ts,
                        rate,
                    })
                })
                .collect()
        })
        .unwrap_or_default()
}

/// Fetches historical open interest snapshots to pre-seed the OiTracker,
/// eliminating the 15-min cold-start window for oi_delta_zscore.
async fn fetch_oi_history(symbol: &str) -> Vec<OiHistSnapshot> {
    let url = format!(
        "https://fapi.binance.com/futures/data/openInterestHist?symbol={}&period=5m&limit=30",
        symbol
    );
    let resp = match reqwest::get(&url).await {
        Ok(r) => r,
        Err(e) => {
            eprintln!("[fetch] OI history failed: {e}");
            return vec![];
        }
    };
    let json: serde_json::Value = match resp.json().await {
        Ok(v) => v,
        Err(e) => {
            eprintln!("[fetch] OI history parse failed: {e}");
            return vec![];
        }
    };
    json.as_array()
        .map(|arr| {
            arr.iter()
                .filter_map(|entry| {
                    let oi: f64 = entry.get("sumOpenInterest")?.as_str()?.parse().ok()?;
                    let ts: i64 = entry.get("timestamp")?.as_i64()?;
                    Some(OiHistSnapshot {
                        timestamp_ms: ts,
                        open_interest_usd: oi,
                    })
                })
                .collect()
        })
        .unwrap_or_default()
}

/// Fetches historical top-trader and global L/S snapshots to pre-seed LsRatioTracker,
/// eliminating the 5-min cold-start window with fallback 0.50/0.50 defaults.
async fn fetch_ls_history(symbol: &str) -> Vec<LongShortSnapshot> {
    let mut result = Vec::new();
    let endpoints: &[(&str, LsSource)] = &[
        ("topLongShortPositionRatio", LsSource::TopTraderPosition),
        ("globalLongShortAccountRatio", LsSource::GlobalAccount),
    ];
    for (endpoint, source) in endpoints {
        let url = format!(
            "https://fapi.binance.com/futures/data/{}?symbol={}&period=5m&limit=6",
            endpoint, symbol
        );
        let resp = match reqwest::get(&url).await {
            Ok(r) => r,
            Err(e) => {
                eprintln!("[fetch] LS history ({endpoint}) failed: {e}");
                continue;
            }
        };
        let json: serde_json::Value = match resp.json().await {
            Ok(v) => v,
            Err(_) => continue,
        };
        if let Some(arr) = json.as_array() {
            for entry in arr {
                let long_ratio: f64 = match entry
                    .get("longAccount")
                    .and_then(|v| v.as_str())
                    .and_then(|s| s.parse().ok())
                {
                    Some(v) => v,
                    None => continue,
                };
                let short_ratio = 1.0 - long_ratio;
                let ls_ratio = if short_ratio > 0.0 {
                    long_ratio / short_ratio
                } else {
                    0.0
                };
                let ts: i64 = entry.get("timestamp").and_then(|v| v.as_i64()).unwrap_or(0);
                result.push(LongShortSnapshot {
                    timestamp_ms: ts,
                    long_ratio,
                    short_ratio,
                    ls_ratio,
                    source: *source,
                });
            }
        }
    }
    result
}

/// Fetches `limit` historical closed klines from Binance FAPI REST and seeds BarState
/// with them so ATR, VWAP, VP, and regime are warm before the first live bar is processed.
/// Signals are NOT emitted for historical bars — Supabase writes are suppressed.
async fn warm_up_history(state: &mut BarState, symbol: &str, tf_min: u64, limit: usize) {
    let interval_str = match tf_min {
        1 => "1m",
        3 => "3m",
        5 => "5m",
        15 => "15m",
        30 => "30m",
        60 => "1h",
        _ => "5m",
    };
    let capped = (limit + 1).min(1500); // Binance FAPI max limit = 1500
    let url = format!(
        "https://fapi.binance.com/fapi/v1/klines?symbol={}&interval={}&limit={}",
        symbol,
        interval_str,
        capped
    );
    let resp = match reqwest::get(&url).await {
        Ok(r) => r,
        Err(e) => {
            eprintln!("[warmup] klines fetch failed: {e}");
            return;
        }
    };
    let json: serde_json::Value = match resp.json().await {
        Ok(v) => v,
        Err(e) => {
            eprintln!("[warmup] klines parse failed: {e}");
            return;
        }
    };
    let arr = match json.as_array() {
        Some(a) if a.len() > 1 => a,
        _ => {
            eprintln!("[warmup] klines response unexpected shape: {}", &json.to_string()[..json.to_string().len().min(200)]);
            return;
        }
    };

    // Skip last entry — it's the still-open current bar
    let closed = &arr[..arr.len() - 1];
    println!(
        "[warmup] seeding {} historical bars ({})",
        closed.len(),
        interval_str
    );

    let rbf_warm_cfg =
        data::strategy::detectors::range_breakout_flow::RangeBreakoutConfig::default();
    for entry in closed {
        let arr = match entry.as_array() {
            Some(a) if a.len() >= 10 => a,
            _ => continue,
        };
        let open_ms: i64 = arr[0].as_i64().unwrap_or(0);
        let open: f64 = arr[1].as_str().and_then(|s| s.parse().ok()).unwrap_or(0.0);
        let high: f64 = arr[2].as_str().and_then(|s| s.parse().ok()).unwrap_or(0.0);
        let low: f64 = arr[3].as_str().and_then(|s| s.parse().ok()).unwrap_or(0.0);
        let close: f64 = arr[4].as_str().and_then(|s| s.parse().ok()).unwrap_or(0.0);
        let volume: f64 = arr[5].as_str().and_then(|s| s.parse().ok()).unwrap_or(0.0);
        let taker_buy_vol: f64 = arr[9].as_str().and_then(|s| s.parse().ok()).unwrap_or(0.0);

        if close <= 0.0 || volume <= 0.0 {
            continue;
        }

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
        let bar_delta = taker_buy_vol - taker_sell_vol;
        state.cvd += bar_delta;
        state.cvd_history.push_back(state.cvd);
        if state.cvd_history.len() > CVD_WINDOW {
            state.cvd_history.pop_front();
        }
        state.bar_delta_history.push_back(bar_delta);
        if state.bar_delta_history.len() > CVD_WINDOW {
            state.bar_delta_history.pop_front();
        }

        state.ms_tracker.push_bar(open, high, low, close, open_ms);
        state
            .ob_detector
            .push_bar(open, high, low, close, volume, open_ms);
        state.fvg_detector.push_bar(high, low, open_ms);
        state.range_detector.push_bar(high, low, close);
        let market_structure = state.ms_tracker.snapshot();
        let swing_high = market_structure.as_ref().and_then(|ms| ms.range_high);
        let swing_low = market_structure.as_ref().and_then(|ms| ms.range_low);
        let current_oi = state
            .oi_history
            .back()
            .copied()
            .or_else(|| {
                let snap = state.oi_tracker.snapshot();
                (snap.current > 0.0).then_some(snap.current)
            })
            .unwrap_or(0.0);
        state
            .liq_map_tracker
            .update(close, swing_high, swing_low, current_oi, open_ms);
        state.liq_map_snapshot = Some(state.liq_map_tracker.snapshot().clone());

        // Build a synthetic Kline and push it into the bar history
        use exchange::{
            UnixMs,
            unit::{Price, Qty},
        };
        let bar = Kline {
            time: UnixMs(open_ms as u64),
            open: Price::from_f32(open as f32),
            high: Price::from_f32(high as f32),
            low: Price::from_f32(low as f32),
            close: Price::from_f32(close as f32),
            volume: Volume::TotalOnly(Qty::from_f32(volume as f32)),
            is_closed: true,
        };
        state.bars.push_back(bar);
        if state.bars.len() > VP_WINDOW {
            state.bars.pop_front();
        }
        // Feed amd_state so VR/history buffers warm al arrancar.
        {
            let amd_ctx = data::strategy::detectors::amd_detector::AmdContext {
                vpin: None,
                cvd_slope: None,
                obi_l5: None,
                vwap: None,
                lvn_levels: vec![],
                naked_pocs: vec![],
                ob_levels: vec![],
                fvg_levels: vec![],
                funding_rate: None,
                session_name: "warmup".into(),
                vwap_dz: None,
                liq_ratio: 0.0,
                absorption_bid: false,
                absorption_ask: false,
                regime_is_trending: false,
                session_cvd: 0.0,
                val: None,
                vah: None,
                bid_wall_nearby: false,
                ask_wall_nearby: false,
                big_trade_cvd_bar: 0.0,
                oi_delta_pct: None,
                cvd_divergence_bars: None,
                htf_h1_trend: None,
            };
            let _ = state.amd_state.on_bar_close(
                high,
                low,
                close,
                volume,
                bar_delta,
                open_ms,
                &amd_ctx,
                &data::strategy::detectors::amd_detector::AmdDetectorConfig {
                    enabled: true,
                    ..data::strategy::detectors::amd_detector::AmdDetectorConfig::default()
                },
            );
        }

        // Feed rbf_state so VR/delta/EMA480 buffers son warm al arrancar.
        // El resultado se descarta — solo queremos poblar el estado interno.
        let vwap = state.vwap_session;
        let warm_session = classify_session(open_ms).session;
        let _ = state.rbf_state.on_bar_close(
            open,
            high,
            low,
            close,
            volume,
            bar_delta,
            warm_session,
            open_ms,
            &rbf_warm_cfg,
            vwap,
            None,
            0.0,
            0.0,
            None,
            None,
        );

        // Feed rbf_paper si hay posición restaurada — detecta SL/TP que ocurrieron
        // durante el downtime (barras posteriores a la entrada).
        if state.rbf_paper.has_position() {
            if let Some(entry_ms) = state.rbf_paper.entry_ms() {
                if open_ms > entry_ms {
                    if let Some(trade) =
                        state.rbf_paper.on_bar_close(high, low, close, open_ms, 0.0)
                    {
                        println!(
                            "[rbf_paper] warm-up close {:?} entry={:.4} exit={:.4} R={:.3}",
                            trade.exit_reason, trade.entry_price, trade.exit_price, trade.result_r
                        );
                        if let (Some(sb), Some(id)) = (&state.supabase, &trade.supabase_id) {
                            sb.update_rbf_outcome(id, &trade);
                        }
                    }
                }
            }
        }

        // Feed be_state so vol_hist y history buffers están warm al arrancar.
        {
            let be_warm_cfg = buyer_exhaustion::config::BuyerExhaustionConfig::default();
            let _ = state.be_state.on_bar_close(
                high,
                low,
                open,
                close,
                volume,
                bar_delta,
                open_ms,
                symbol,
                &be_warm_cfg,
                None,
            );
        }

        // Feed be_paper si hay posición restaurada.
        if state.be_paper.has_position() {
            if let Some(entry_ms) = state.be_paper.entry_ms() {
                if open_ms > entry_ms {
                    if let Some(trade) = state.be_paper.on_bar_close(high, low, close, open_ms, 30)
                    {
                        println!(
                            "[be_paper] warm-up close {} entry={:.4} exit={:.4} R={:.3}",
                            trade.exit_reason.as_str(),
                            trade.entry_price,
                            trade.exit_price,
                            trade.result_r
                        );
                        if let (Some(sb), Some(id)) = (&state.supabase, &trade.supabase_id) {
                            sb.update_be_outcome(id, &trade);
                        }
                    }
                }
            }
        }

        // Feed mtf_state si hay posición restaurada — detecta SL/TP durante downtime.
        // cvd_slope=None suprime CVD exhaustion; solo se evalúa TP/SL/EXPIRED.
        if state.mtf_state.has_active_trade() {
            if let Some(entry_ms) = state.mtf_state.active_entry_ms() {
                if open_ms > entry_ms {
                    use data::strategy::detectors::mtf_shorts_detector::MtfBarContext;
                    let warmup_ctx = MtfBarContext {
                        ts_ms: open_ms, open, high, low, close,
                        cvd_slope: None, obi_l5: 0.0, obi_fast: 0.0, vr: 0.0,
                        oi_momentum: None, equal_high: false, equal_low: false,
                        dz: 0.0, absorption: "None".into(), vpin: 0.0,
                        regime: "Neutral".into(), session: "warmup".into(),
                        atr: 0.0, stacked_imb: "None".into(),
                        funding_regime: "Neutral".into(),
                        vwap_session: None,
                    };
                    if let Some(closed) = state.mtf_state.on_bar_close(&warmup_ctx) {
                        if !closed.is_open {
                            println!(
                                "[mtf] warm-up close {} reason={} R={:.3}",
                                symbol,
                                closed.reason.as_deref().unwrap_or("?"),
                                closed.result_r.unwrap_or(0.0)
                            );
                            if let Some(sb) = &state.supabase {
                                let ev = closed;
                                let sym_s = symbol.to_string();
                                let sb2 = sb.clone();
                                tokio::spawn(async move { sb2.write_mtf_trade(&ev, &sym_s).await; });
                            }
                        }
                    }
                }
            }
        }

        // Feed mtf_longs_state si hay posición restaurada.
        if state.mtf_longs_state.has_active_trade() {
            if let Some(entry_ms) = state.mtf_longs_state.active_entry_ms() {
                if open_ms > entry_ms {
                    use data::strategy::detectors::mtf_shorts_detector::MtfBarContext;
                    let warmup_ctx = MtfBarContext {
                        ts_ms: open_ms, open, high, low, close,
                        cvd_slope: None, obi_l5: 0.0, obi_fast: 0.0, vr: 0.0,
                        oi_momentum: None, equal_high: false, equal_low: false,
                        dz: 0.0, absorption: "None".into(), vpin: 0.0,
                        regime: "Neutral".into(), session: "warmup".into(),
                        atr: 0.0, stacked_imb: "None".into(),
                        funding_regime: "Neutral".into(),
                        vwap_session: None,
                    };
                    if let Some(closed) = state.mtf_longs_state.on_bar_close(&warmup_ctx) {
                        if !closed.is_open {
                            println!(
                                "[mtf_long] warm-up close {} reason={} R={:.3}",
                                symbol,
                                closed.reason.as_deref().unwrap_or("?"),
                                closed.result_r.unwrap_or(0.0)
                            );
                            if let Some(sb) = &state.supabase {
                                let ev = closed;
                                let sym_s = symbol.to_string();
                                let sb2 = sb.clone();
                                tokio::spawn(async move { sb2.write_mtf_long_trade(&ev, &sym_s).await; });
                            }
                        }
                    }
                }
            }
        }
    }

    // Prime last_regime_enum so hysteresis starts with the correct state
    let closes: Vec<f64> = state.bars.iter().map(|b| b.close.to_f32() as f64).collect();
    let highs: Vec<f64> = state.bars.iter().map(|b| b.high.to_f32() as f64).collect();
    let lows: Vec<f64> = state.bars.iter().map(|b| b.low.to_f32() as f64).collect();
    let atr = compute_atr(&highs, &lows, &closes, ATR_WINDOW);
    let regime_window = &closes[closes.len().saturating_sub(REGIME_WINDOW)..];
    let regime = derive_regime(regime_window, atr);
    state.last_regime_enum = regime;
    state.last_regime = Some(format!("{regime:?}"));
    // Initialize micro_buffer open time to the bar after the last warmup bar
    if let Some(last_bar) = state.bars.back() {
        let last_open_ms = last_bar.time.as_u64() as i64;
        let next_open_ms = last_open_ms + 300_000;
        state.micro_buffer.reset(next_open_ms);
        state.current_candle_open_ms = next_open_ms;
    }
    // ── HTF seed: D1 EMA20 real + H1 buckets reales ──────────────────────────
    // Fetch 30 velas D1 para que EMA20 sea precisa desde el arranque
    {
        let d1_url = format!(
            "https://fapi.binance.com/fapi/v1/klines?symbol={}&interval=1d&limit=31",
            symbol
        );
        if let Ok(resp) = reqwest::get(&d1_url).await {
            if let Ok(json) = resp.json::<serde_json::Value>().await {
                if let Some(arr) = json.as_array() {
                    // excluir la vela D1 aún abierta (la última)
                    let closes: Vec<f64> = arr[..arr.len().saturating_sub(1)]
                        .iter()
                        .filter_map(|e| e.as_array())
                        .filter_map(|a| a.get(4)?.as_str()?.parse::<f64>().ok())
                        .collect();
                    state.mtf_state.seed_d1(&closes);
                }
            }
        }
    }
    // Fetch 14 velas H1 para que ATR H1 sea preciso
    {
        let h1_url = format!(
            "https://fapi.binance.com/fapi/v1/klines?symbol={}&interval=1h&limit=15",
            symbol
        );
        if let Ok(resp) = reqwest::get(&h1_url).await {
            if let Ok(json) = resp.json::<serde_json::Value>().await {
                if let Some(arr) = json.as_array() {
                    let candles: Vec<(i64, f64, f64, f64)> = arr[..arr.len().saturating_sub(1)]
                        .iter()
                        .filter_map(|e| e.as_array())
                        .filter_map(|a| {
                            let ts   = a.get(0)?.as_i64()?;
                            let high = a.get(2)?.as_str()?.parse::<f64>().ok()?;
                            let low  = a.get(3)?.as_str()?.parse::<f64>().ok()?;
                            let cls  = a.get(4)?.as_str()?.parse::<f64>().ok()?;
                            Some((ts, high, low, cls))
                        })
                        .collect();
                    state.mtf_state.seed_h1(&candles);
                    state.mtf_longs_state.seed_h1(&candles);
                }
            }
        }
    }
    // Seed H4 EMA20 para el detector de longs (solo ETH/SOL)
    if matches!(symbol, "ETHUSDT" | "SOLUSDT") {
        let h4_url = format!(
            "https://fapi.binance.com/fapi/v1/klines?symbol={}&interval=4h&limit=25",
            symbol
        );
        if let Ok(resp) = reqwest::get(&h4_url).await {
            if let Ok(json) = resp.json::<serde_json::Value>().await {
                if let Some(arr) = json.as_array() {
                    let candles: Vec<(i64, f64, f64, f64)> = arr[..arr.len().saturating_sub(1)]
                        .iter()
                        .filter_map(|e| e.as_array())
                        .filter_map(|a| {
                            let ts   = a.get(0)?.as_i64()?;
                            let high = a.get(2)?.as_str()?.parse::<f64>().ok()?;
                            let low  = a.get(3)?.as_str()?.parse::<f64>().ok()?;
                            let cls  = a.get(4)?.as_str()?.parse::<f64>().ok()?;
                            Some((ts, high, low, cls))
                        })
                        .collect();
                    state.mtf_longs_state.seed_h4(&candles);
                }
            }
        }
    }

    println!(
        "[warmup] complete — {} bars loaded, atr={atr:.2}, regime={regime:?}",
        state.bars.len()
    );
}

// ── main ──────────────────────────────────────────────────────────────────────

#[tokio::main]
async fn main() {
    println!("monitor: starting up");

    // SYMBOLS acepta lista separada por coma: "BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT"
    // SYMBOL (singular) sigue funcionando para compatibilidad
    let symbols_raw = std::env::var("SYMBOLS")
        .or_else(|_| std::env::var("SYMBOL"))
        .unwrap_or_else(|_| "BTCUSDT".to_string());
    let symbols: Vec<String> = symbols_raw
        .split(',')
        .map(|s| s.trim().to_uppercase())
        .filter(|s| !s.is_empty())
        .collect();

    let tf_min: u64 = std::env::var("TIMEFRAME_MIN")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(1);

    println!("monitor: símbolos activos = {:?}", symbols);

    let mut tasks = vec![];
    for (i, symbol_str) in symbols.into_iter().enumerate() {
        let sym = symbol_str.clone();
        let tf = tf_min;
        let primary = i == 0; // solo el primer símbolo arranca el ws_server
        tasks.push(tokio::spawn(async move {
            run_symbol(sym, tf, primary).await;
        }));
    }
    futures::future::join_all(tasks).await;
}

async fn run_symbol(symbol_str: String, tf_min: u64, primary: bool) {
    let tf_min: u64 = tf_min;

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

    println!("monitor: fetching {symbol_str} LinearPerps metadata…");
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

    println!("monitor: streaming {symbol_str} at {timeframe}");

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

    // ── Persistence: MongoDB (local) + Supabase (Railway) ────────────────────
    let mongo = MongoWriter::from_env();
    let supabase = SupabaseWriter::from_env();

    // ── WebSocket server — solo el task primario bindea el puerto ─────────────
    let ws_port: u16 = std::env::var("WS_PORT")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(9001);
    let ws_tx = if primary {
        ws_server::start(ws_port)
    } else {
        ws_server::start(0) // puerto 0 = no bindea, devuelve sender nulo
    };
    if supabase.is_none() {
        println!("[supabase] SUPABASE_URL not set — cloud persistence disabled");
    }

    // The monitor always runs with strategy enabled — override the default (false)
    // so Railway deployments without a config/strategy.toml still work correctly.
    let mut base_cfg = StrategyConfig::load();
    base_cfg.enabled = true;
    // scalping.enabled viene del strategy.toml — no hardcodear
    let config_loader = MongoConfigLoader::from_env(base_cfg);

    let footprint_step: PriceStep = ticker_info.min_ticksize.into();
    let liq_raw_counter = Arc::new(AtomicU64::new(0));
    let liq_global_raw_counter = Arc::new(AtomicU64::new(0));
    let mut state = BarState::new(
        mongo,
        supabase,
        config_loader,
        footprint_step,
        Arc::clone(&liq_raw_counter),
        Arc::clone(&liq_global_raw_counter),
        ws_tx,
        &symbol_str,
    );
    state.intrabar_cfg.log_boot();

    // Restaurar posiciones activas ANTES del warm-up para que las barras históricas
    // sean evaluadas por el paper trader y detecten SL/TP ocurridos durante el downtime.
    // earliest_entry_ms: rastrea la entrada más antigua entre todas las posiciones
    // restauradas para calcular cuántas barras históricas necesitamos en el warm-up.
    let mut earliest_entry_ms: i64 = i64::MAX;

    if let Some(sb) = state.supabase.clone() {
        if let Some(pos) = sb.load_rbf_active(&symbol_str).await {
            println!(
                "[rbf_paper] RESTORED {:?} entry={:.1} stop={:.1} target={:.1} id={}",
                pos.direction, pos.entry_price, pos.stop_price, pos.target_price, pos.signal_id
            );
            earliest_entry_ms = earliest_entry_ms.min(pos.entry_ms);
            state.rbf_paper.restore(
                pos.signal_id,
                pos.direction,
                pos.entry_price,
                pos.stop_price,
                pos.target_price,
                pos.entry_ms,
            );
        }
        if let Some(pos) = sb.load_amd_active(&symbol_str).await {
            println!(
                "[amd_paper] RESTORED {:?} entry={:.4} stop={:.4} target={:.4} id={}",
                pos.direction, pos.entry_price, pos.stop_price, pos.target_price, pos.signal_id
            );
            earliest_entry_ms = earliest_entry_ms.min(pos.entry_ms);
            state.amd_paper.restore(
                pos.signal_id,
                pos.direction,
                pos.entry_price,
                pos.stop_price,
                pos.target_price,
                pos.entry_ms,
            );
        }
        if let Some(pos) = sb.load_be_active(&symbol_str).await {
            println!(
                "[be_paper] RESTORED entry={:.4} stop={:.4} target={:.4} id={}",
                pos.entry_price, pos.stop_price, pos.target_price, pos.signal_id
            );
            earliest_entry_ms = earliest_entry_ms.min(pos.entry_ms);
            state.be_paper.restore(
                pos.signal_id,
                pos.entry_price,
                pos.stop_price,
                pos.target_price,
                pos.entry_ms,
            );
        }

        // ── Restaurar trade HTF Short abierto ────────────────────────────────
        if let Some(pos) = sb.load_mtf_active(&symbol_str, "Short").await {
            println!(
                "[mtf] RESTORED Short {} sig={} entry={:.4} stop={:.4} target={:.4}",
                symbol_str, pos.sig, pos.entry, pos.stop, pos.target
            );
            earliest_entry_ms = earliest_entry_ms.min(pos.ts_ms);
            state.mtf_state.restore_active_trade(
                pos.entry, pos.stop, pos.target,
                pos.ts_ms, pos.sig, pos.session, pos.trend,
                pos.stop_pct, pos.obi_entry, pos.cvd_slope_entry,
                pos.dz_score, pos.stacked_imb, pos.equal_low,
            );
        }

        // ── Restaurar trade HTF Long abierto (solo ETH/SOL) ──────────────────
        if matches!(symbol_str.as_str(), "ETHUSDT" | "SOLUSDT") {
            if let Some(pos) = sb.load_mtf_active(&symbol_str, "Long").await {
                println!(
                    "[mtf_long] RESTORED Long {} sig={} entry={:.4} stop={:.4} target={:.4}",
                    symbol_str, pos.sig, pos.entry, pos.stop, pos.target
                );
                earliest_entry_ms = earliest_entry_ms.min(pos.ts_ms);
                state.mtf_longs_state.restore_active_trade(
                    pos.entry, pos.stop, pos.target,
                    pos.ts_ms, pos.sig, pos.session, pos.trend,
                    pos.stop_pct, pos.obi_entry, pos.cvd_slope_entry,
                    pos.dz_score, pos.stacked_imb, pos.equal_low,
                );
            }
        }
    }

    // Calcular cuántas barras necesitamos: desde la entrada más antigua hasta ahora.
    // Esto garantiza cobertura completa del downtime sin importar cuánto duró.
    // Mínimo 150 (warm-up normal), máximo 1500 (25h a M1 = FORWARD_MAX completo).
    let warmup_limit = if earliest_entry_ms < i64::MAX {
        let now_ms = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis() as i64;
        let bars_since_entry = ((now_ms - earliest_entry_ms) / (tf_min as i64 * 60_000)) as usize;
        let needed = bars_since_entry + 20; // +20 buffer
        println!("[warmup] posición restaurada hace ~{bars_since_entry} barras → cargando {needed} barras");
        needed.clamp(150, 1500)
    } else {
        150
    };

    // Seed bar history from REST — si hay posición restaurada, warm_up_history también
    // la alimenta al paper trader para cerrar cualquier SL/TP ocurrido durante downtime.
    warm_up_history(&mut state, &symbol_str, tf_min, warmup_limit).await;
    state.rbf_state.reset_signal_cooldown();
    state.amd_state.reset_signal_cooldown();
    state.be_state.reset_signal_cooldown();

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
    spawn_force_order_stream(
        symbol_str.clone(),
        liq_tx,
        liq_health_tx,
        Arc::clone(&liq_raw_counter),
    );
    // Diagnostic: all-market liquidations — proves the /market/ws/ endpoint works even when BTC is quiet
    spawn_global_liq_counter(Arc::clone(&liq_global_raw_counter));

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
    let (funding_hist_tx, mut funding_hist_rx) =
        tokio::sync::mpsc::channel::<Vec<FundingRateSample>>(2);
    let fh_symbol = symbol_str.clone();
    tokio::spawn(async move {
        let samples = fetch_funding_history(&fh_symbol).await;
        let _ = funding_hist_tx.send(samples).await;
    });

    // OI history: once at startup to pre-seed OiTracker (eliminates 15-min cold-start for z-score)
    let (oi_hist_tx, mut oi_hist_rx) = tokio::sync::mpsc::channel::<Vec<OiHistSnapshot>>(2);
    let oi_hist_symbol = symbol_str.clone();
    tokio::spawn(async move {
        let snaps = fetch_oi_history(&oi_hist_symbol).await;
        let _ = oi_hist_tx.send(snaps).await;
    });

    // L/S history: once at startup to pre-seed LsRatioTracker (eliminates 5-min fallback window)
    let (ls_hist_tx, mut ls_hist_rx) = tokio::sync::mpsc::channel::<Vec<LongShortSnapshot>>(2);
    let ls_hist_symbol = symbol_str.clone();
    tokio::spawn(async move {
        let snaps = fetch_ls_history(&ls_hist_symbol).await;
        let _ = ls_hist_tx.send(snaps).await;
    });

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
                                    // Tick vivo — broadcast precio actual al dashboard web
                                    ws_server::broadcast(&state.ws_tx, ws_server::WsEvent::Tick {
                                        ts_ms: open_ms as i64,
                                        open:  kline.open.to_f32() as f64,
                                        high:  kline.high.to_f32() as f64,
                                        low:   kline.low.to_f32() as f64,
                                        close: kline.close.to_f32() as f64,
                                    });
                                    pending = Some((open_ms, kline));
                                }
                            }
                            _ => {}
                        }
                    }
                    Event::Connected(ex) => {
                        println!("[kline] connected ({ex:?})");
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
                        state.on_trade(trade);
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
                if connected {
                    state.freshness.liq_ws_connected_at = Some(Instant::now());
                }
                if !connected { state.metrics.ws_reconnects += 1; }
            }

            Some(samples) = funding_hist_rx.recv() => {
                if !samples.is_empty() {
                    println!("[inst] loaded {} funding rate history samples", samples.len());
                    state.funding_tracker.load(samples);
                }
            }

            Some(snaps) = oi_hist_rx.recv() => {
                if !snaps.is_empty() {
                    println!("[inst] seeding OI tracker with {} historical snapshots", snaps.len());
                    for snap in snaps {
                        state.oi_tracker.push(snap);
                    }
                    state.freshness.oi_fetched_at = Some(Instant::now());
                }
            }

            Some(snaps) = ls_hist_rx.recv() => {
                if !snaps.is_empty() {
                    println!("[inst] seeding L/S tracker with {} historical snapshots", snaps.len());
                    for snap in snaps {
                        state.ls_tracker.push(snap);
                    }
                    state.freshness.ls_fetched_at = Some(Instant::now());
                }
            }

        }

        if last_metrics_print.elapsed() >= metrics_interval {
            state.metrics.liq_raw_messages = liq_raw_counter.load(Ordering::Relaxed);
            state.metrics.liq_global_raw_messages = liq_global_raw_counter.load(Ordering::Relaxed); // already cloned into state
            state.metrics.report(&state.freshness, &state.streams);
            last_metrics_print = Instant::now();
        }
    }
}
