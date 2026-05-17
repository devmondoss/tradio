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
mod supabase_writer;

use std::collections::VecDeque;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use reqwest;

use config_loader::ConfigLoader;
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
        derive_failed_acceptance_and_absorption, derive_regime, wall_nearby,
    },
    intent_logger::collect_near_misses,
    paper::PaperAccount,
    router::route_strategy,
    types::{DataQuality, OrderBookContext, StrategyAction, StrategyConfig, StrategyMarketContext},
};
use exchange::{
    Kline, PushFrequency, Ticker, TickerInfo, Timeframe,
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
    last_depth_at: Option<Instant>,
    last_trade_at: Option<Instant>,
}

impl PipelineMetrics {
    fn report(&self) {
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

        eprintln!(
            "[metrics] bars={} kline_ticks={} trade_batches={} trades={} depth_updates={} \
             bar_latency_avg={:.0}ms max={max_lat}ms | \
             depth_age={depth_age_ms}ms trade_age={trade_age_ms}ms",
            self.bars_processed,
            self.kline_ticks,
            self.trade_batches,
            self.trade_count,
            self.depth_updates,
            avg_lat,
        );

        if depth_age_ms > 5_000 {
            eprintln!("[WARN] depth stream stale — last update {depth_age_ms}ms ago");
        }
        if trade_age_ms > 5_000 {
            eprintln!("[WARN] trade stream stale — last update {trade_age_ms}ms ago");
        }
        if self.bars_processed > 0 && avg_lat > 500.0 {
            eprintln!("[WARN] high bar-close latency {avg_lat:.0}ms — strategy signals delayed");
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
    // Dynamic config loaded from Supabase
    cfg: StrategyConfig,
    config_loader: ConfigLoader,
    // Sends detected regime string to the async loop for config reloading
    regime_tx: Option<tokio::sync::mpsc::Sender<String>>,
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
            cfg: StrategyConfig { enabled: true, ..StrategyConfig::default() },
            config_loader: ConfigLoader::new(),
            regime_tx: None,
        }
    }

    /// Reloads StrategyConfig from Supabase if regime changed or config is stale.
    async fn maybe_reload_config(&mut self, regime: &str) {
        if self.config_loader.should_reload(regime) {
            self.cfg = self.config_loader.load_for_regime(regime).await;
        }
    }

    fn notify_regime(&self, regime: &str) {
        if let Some(tx) = &self.regime_tx {
            let _ = tx.try_send(regime.to_string());
        }
    }

    fn on_liquidation(&mut self, event: LiquidationEvent) {
        self.liq_tracker.push(event);
    }

    fn on_trade(&mut self, is_sell: bool, qty: f32) {
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

    fn on_depth(&mut self, depth: Depth) {
        self.depth = Some(depth);
        self.metrics.depth_updates += 1;
        self.metrics.last_depth_at = Some(Instant::now());
    }

    fn on_bar_close(&mut self, bar: Kline, bar_close_ms: u64, symbol: &str) {
        let cfg = self.cfg.clone();
        let bar_ms = bar.time.as_u64() as i64;
        self.metrics.bars_processed += 1;

        let now_ms = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_millis() as i64)
            .unwrap_or(0);
        let latency_ms = now_ms - bar_close_ms as i64;
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
        let regime = derive_regime(regime_window, atr);
        self.notify_regime(&format!("{regime:?}"));
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
        // BOS is defined as a close above prior_swing_high (bullish) or below
        // prior_swing_low (bearish). We scan backwards for the most recent such bar.
        let avwap_bos: Option<f64> = if n > SWING_LB {
            // Scan from newest to oldest (skip the last bar — that's current)
            let bars_vec: Vec<_> = self.bars.iter().collect();
            let bos_idx = (0..n.saturating_sub(1)).rev().find(|&i| {
                let cl = bars_vec[i].close.to_f32() as f64;
                // Reference swing: max/min of bars *before* i
                if i == 0 { return false; }
                let ref_high = bars_vec[..i].iter()
                    .map(|b| b.high.to_f32() as f64)
                    .fold(f64::NEG_INFINITY, f64::max);
                let ref_low = bars_vec[..i].iter()
                    .map(|b| b.low.to_f32() as f64)
                    .fold(f64::INFINITY, f64::min);
                cl > ref_high || cl < ref_low
            });
            bos_idx.map(|anchor| {
                // Compute cumulative VWAP from anchor bar to the last bar
                let (cum_pv, cum_vol) = bars_vec[anchor..].iter().fold((0.0_f64, 0.0_f64), |(pv, v), b| {
                    let bh = b.high.to_f32() as f64;
                    let bl = b.low.to_f32() as f64;
                    let bc = b.close.to_f32() as f64;
                    let bv = b.volume.total().to_f32_lossy() as f64;
                    let tp = (bh + bl + bc) / 3.0;
                    (pv + tp * bv, v + bv)
                });
                if cum_vol > 0.0 { cum_pv / cum_vol } else { 0.0 }
            }).filter(|&v| v > 0.0)
        } else {
            None
        };

        let vwap_ctx = build_vwap_context(c, self.vwap_session, avwap_bos);
        let vp_ctx = build_volume_profile_context(c, poc, vah, val, hvn_nearby, lvn_nearby);
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
        });

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
        };

        let signal = route_strategy(&ctx, &cfg);
        let signal_fired = signal.action == StrategyAction::ShadowSignal;

        for nm in collect_near_misses(&ctx, &cfg, signal_fired) {
            if let Ok(json) = serde_json::to_string(&nm) {
                println!("{{\"event\":\"near_miss\",\"data\":{json}}}");
            }
        }

        if signal_fired {
            if let Ok(json) = serde_json::to_string(&signal) {
                println!("{{\"event\":\"signal\",\"data\":{json}}}");
            }
            if let Some(sb) = &self.supabase {
                sb.write_signal(&signal, &ctx);
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
                sb.write_trade(trade);
            }
        }

        let missing_str = signal
            .missing
            .iter()
            .map(|m| format!("{m:?}"))
            .collect::<Vec<_>>()
            .join(",");
        let evidence_str = signal
            .evidence
            .iter()
            .map(|e| format!("{e:?}"))
            .collect::<Vec<_>>()
            .join(",");

        eprintln!(
            "[bar] ts={bar_ms} close={c:.2} regime={regime:?} \
             slow={slow_slope:.3} fast={fast_slope:.3} \
             funding={:.4} basis={:.3}% oi_delta={:.0} \
             vwap={:.2} cvd={:.1} ob={} \
             action={:?} score={:.3} latency={latency_ms}ms equity={:.2} \
             missing=[{missing_str}] evidence=[{evidence_str}]",
            self.funding_rate.unwrap_or(0.0) * 10_000.0,
            basis.unwrap_or(0.0),
            oi_delta.unwrap_or(0.0),
            self.vwap_session.unwrap_or(0.0),
            self.cvd,
            if self.depth.is_some() { "live" } else { "miss" },
            signal.action,
            signal.score,
            self.paper.equity,
        );

        if self.metrics.bars_processed % 10 == 0 {
            self.metrics.report();
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

/// Fetches recent force-liquidation orders from Binance FAPI (last 100, within past 5 min).
async fn fetch_recent_liquidations(symbol: &str) -> Vec<LiquidationEvent> {
    let url = format!(
        "https://fapi.binance.com/fapi/v1/forceOrders?symbol={}&autoCloseType=LIQUIDATION&limit=100",
        symbol
    );
    let resp = match reqwest::get(&url).await {
        Ok(r) => r,
        Err(e) => { eprintln!("[fetch] forceOrders failed: {e}"); return vec![]; }
    };
    let json: serde_json::Value = match resp.json().await {
        Ok(v) => v,
        Err(e) => { eprintln!("[fetch] forceOrders parse failed: {e}"); return vec![]; }
    };
    let now_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as i64;
    let cutoff = now_ms - 5 * 60_000;
    json.as_array()
        .map(|arr| {
            arr.iter()
                .filter_map(|entry| {
                    let ts: i64 = entry.get("time")?.as_i64()?;
                    if ts < cutoff { return None; }
                    let side_str = entry.get("side")?.as_str()?;
                    // In Binance force orders, "side" is the order side that was placed to close.
                    // SELL = long position liquidated, BUY = short position liquidated.
                    let liq_side = match side_str {
                        "SELL" => LiqSide::Longs,
                        "BUY"  => LiqSide::Shorts,
                        _      => LiqSide::Neutral,
                    };
                    let qty: f64 = entry.get("executedQty")?.as_str()?.parse().ok()?;
                    let price: f64 = entry.get("avgPrice")?.as_str()?.parse().ok()?;
                    Some(LiquidationEvent { timestamp_ms: ts, side: liq_side, quantity_usd: qty * price })
                })
                .collect()
        })
        .unwrap_or_default()
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

    // Liquidations: every 60s (poll forceOrders for last 5 min)
    let (liq_tx, mut liq_rx) = tokio::sync::mpsc::channel::<Vec<LiquidationEvent>>(4);
    let liq_symbol = symbol_str.clone();
    tokio::spawn(async move {
        let mut interval = tokio::time::interval(Duration::from_secs(60));
        loop {
            interval.tick().await;
            let events = fetch_recent_liquidations(&liq_symbol).await;
            let _ = liq_tx.send(events).await;
        }
    });

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
    let (regime_tx, mut regime_rx) = tokio::sync::mpsc::channel::<String>(8);
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
                                state.on_bar_close(closed_kline, bar_close_ms, &symbol_str);
                                pending = Some((open_ms, kline));
                            }
                            Some((prev_open, _)) if open_ms == prev_open => {
                                if kline.is_closed {
                                    pending = None;
                                    let bar_close_ms = open_ms + tf_ms;
                                    state.on_bar_close(kline, bar_close_ms, &symbol_str);
                                } else {
                                    pending = Some((open_ms, kline));
                                }
                            }
                            _ => {}
                        }
                    }
                    Event::Connected(ex) => eprintln!("[kline] connected ({ex:?})"),
                    Event::Disconnected(ex, reason) => {
                        eprintln!("[kline] DISCONNECTED ({ex:?}): {reason}");
                    }
                    _ => {}
                }
            }

            Some(event) = depth_stream.next() => {
                if let Event::DepthReceived(_kind, _ts, depth_arc) = event {
                    state.on_depth((*depth_arc).clone());
                }
            }

            Some(event) = trade_stream.next() => {
                if let Event::TradesReceived(_kind, _ts, trades) = event {
                    state.on_trade_batch();
                    for trade in trades.iter() {
                        state.on_trade(trade.is_sell, trade.qty.to_f32_lossy());
                    }
                }
            }

            Some((funding, mark_price)) = funding_rx.recv() => {
                if let Some(r) = funding {
                    state.funding_rate = Some(r);
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
                    // Feed both the legacy VecDeque and the OiTracker
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
            }

            Some((top, global)) = ls_rx.recv() => {
                if let Some(snap) = top {
                    state.ls_tracker.push(snap);
                }
                if let Some(snap) = global {
                    state.ls_tracker.push(snap);
                }
            }

            Some(snap) = taker_rx.recv() => {
                if let Some(s) = snap {
                    state.last_taker_ratio = Some(s);
                }
            }

            Some(samples) = funding_hist_rx.recv() => {
                if !samples.is_empty() {
                    eprintln!("[inst] loaded {} funding rate history samples", samples.len());
                    state.funding_tracker.load(samples);
                }
            }

            Some(regime) = regime_rx.recv() => {
                state.maybe_reload_config(&regime).await;
            }
        }

        if last_metrics_print.elapsed() >= metrics_interval {
            state.metrics.report();
            last_metrics_print = Instant::now();
        }
    }
}
