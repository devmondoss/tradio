//! Layer 2: headless strategy monitor for cloud deployment (Railway).
//!
//! Connects to Binance LinearPerps WebSocket streams, accumulates kline/depth/trade
//! data, runs strategy detection on every bar close, and writes all output to
//! ./logs/ — the same directory the GUI layer reads from.
//!
//! Environment variables (all optional):
//!   SYMBOL           — ticker to monitor (default: BTCUSDT)
//!   TIMEFRAME_MIN    — kline timeframe in minutes (default: 5)
//!   PAPER_INITIAL_CAPITAL, PAPER_LEVERAGE, PAPER_MAX_POSITIONS,
//!   PAPER_RISK_PCT, PAPER_SLIPPAGE_BPS, PAPER_TAKER_FEE, PAPER_FUNDING_RATE

use std::collections::VecDeque;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use data::strategy::{
    adapter::{
        build_flow_context, build_orderbook_context, build_vwap_context,
        build_volume_profile_context, derive_cvd_divergence, derive_failed_acceptance_and_absorption,
        derive_regime,
    },
    intent_logger::log_near_misses,
    paper::PaperAccount,
    router::route_strategy,
    types::{DataQuality, OrderBookContext, StrategyAction, StrategyConfig, StrategyMarketContext},
};
use exchange::{
    Kline, PushFrequency, TickerInfo, Timeframe, Ticker,
    adapter::{
        AdapterHandles, AdapterNetworkConfig, Event, Exchange, MarketKind, StreamConfig, Venue,
    },
    depth::Depth,
};
use futures::StreamExt;

// ── constants ───────────────────────────────────────────────────────────────

const VP_WINDOW: usize = 300;
const VP_BINS: usize = 150;
const REGIME_WINDOW: usize = 20;
const CVD_WINDOW: usize = 50;
const ATR_WINDOW: usize = 14;

// ── state ────────────────────────────────────────────────────────────────────

// ── pipeline metrics ──────────────────────────────────────────────────────────

#[derive(Default)]
struct PipelineMetrics {
    kline_ticks: u64,
    trade_batches: u64,
    trade_count: u64,
    depth_updates: u64,
    bars_processed: u64,
    // latency from theoretical bar_close_ms to actual processing wall-clock
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

        let depth_age_ms = self.last_depth_at.map(|t| t.elapsed().as_millis()).unwrap_or(999_999);
        let trade_age_ms = self.last_trade_at.map(|t| t.elapsed().as_millis()).unwrap_or(999_999);

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

        // Health checks
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
}

impl BarState {
    fn new() -> Self {
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
        }
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

    fn on_bar_close(&mut self, bar: Kline, bar_close_ms: u64, symbol: &str, cfg: &StrategyConfig) {
        let bar_ms = bar.time.as_u64() as i64;
        self.metrics.bars_processed += 1;

        // Measure latency: wall-clock now vs theoretical bar close time
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

        // Flush bar trade delta
        let bar_buy = self.bar_buy_vol;
        let bar_sell = self.bar_sell_vol;
        let bar_delta = bar_buy - bar_sell;
        self.bar_buy_vol = 0.0;
        self.bar_sell_vol = 0.0;

        // Push bar to history
        self.bars.push_back(bar);
        if self.bars.len() > VP_WINDOW {
            self.bars.pop_front();
        }

        // CVD snapshot
        self.cvd_history.push_back(self.cvd);
        if self.cvd_history.len() > CVD_WINDOW {
            self.cvd_history.pop_front();
        }

        // Derive indicators
        let closes: Vec<f64> = self.bars.iter().map(|b| b.close.to_f32() as f64).collect();
        let highs: Vec<f64> = self.bars.iter().map(|b| b.high.to_f32() as f64).collect();
        let lows: Vec<f64> = self.bars.iter().map(|b| b.low.to_f32() as f64).collect();

        let atr = compute_atr(&highs, &lows, &closes, ATR_WINDOW);
        let regime = derive_regime(
            &closes[closes.len().saturating_sub(REGIME_WINDOW)..],
            atr,
        );

        let cvd_slope = compute_cvd_slope(&self.cvd_history);
        let cvd_divergence = derive_cvd_divergence(&highs, &lows, cvd_slope);

        let (poc, vah, val, hvn_nearby, lvn_nearby) =
            compute_volume_profile(&self.bars, VP_BINS, c);

        let deltas = [bar_delta];
        let (failed_acceptance, footprint_absorption) =
            derive_failed_acceptance_and_absorption(
                &highs,
                &lows,
                &closes,
                vah,
                val,
                &deltas,
                cvd_slope,
            );

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
        );

        let vwap_ctx = build_vwap_context(c, self.vwap_session, None);
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
        };

        let signal = route_strategy(&ctx, cfg);
        let signal_fired = signal.action == StrategyAction::ShadowSignal;

        log_near_misses(&ctx, cfg, signal_fired);

        let paper_signal = if signal_fired { Some(&signal) } else { None };
        self.paper.on_bar_close(symbol, c, h, l, bar_ms, paper_signal);

        eprintln!(
            "[bar] ts={bar_ms} close={c:.2} regime={regime:?} vwap={:.2} cvd={:.1} \
             ob={} action={:?} score={:.3} latency={latency_ms}ms equity={:.2}",
            self.vwap_session.unwrap_or(0.0),
            self.cvd,
            if self.depth.is_some() { "live" } else { "miss" },
            signal.action,
            signal.score,
            self.paper.equity,
        );

        // Print metrics every 10 bars
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
        let add_above = if hi + 1 < n_bins { histogram[hi + 1] } else { 0.0 };
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

    let handles = AdapterHandles::spawn_selected(
        AdapterNetworkConfig::default(),
        [Venue::Binance],
    )
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

    let mut state = BarState::new();
    let cfg = StrategyConfig {
        enabled: true,
        ..StrategyConfig::default()
    };

    let tf_ms = timeframe.to_milliseconds();

    // We keep the last kline seen per bar open_time.
    // A bar closes when a kline with a NEWER open_time arrives — that guarantees
    // Binance has finalised the previous bar. We never process the same bar twice.
    let mut pending: Option<(u64, Kline)> = None; // (open_ms, latest kline for that bar)

    // Emit a metrics summary every minute
    let metrics_interval = Duration::from_secs(60);
    let mut last_metrics_print = Instant::now();

    loop {
        tokio::select! {
            Some(event) = kline_stream.next() => {
                state.metrics.kline_ticks += 1;

                match event {
                    Event::KlineReceived(_kind, kline) => {
                        let open_ms = kline.time.as_u64();

                        match pending {
                            None => {
                                // First kline ever — start accumulating
                                pending = Some((open_ms, kline));
                            }
                            Some((prev_open, _)) if open_ms > prev_open => {
                                // New bar started → the pending bar is definitively closed
                                let (closed_open_ms, closed_kline) = pending.take().unwrap();
                                let bar_close_ms = closed_open_ms + tf_ms;
                                state.on_bar_close(closed_kline, bar_close_ms, &symbol_str, &cfg);
                                // Start accumulating new bar
                                pending = Some((open_ms, kline));
                            }
                            Some((prev_open, _)) if open_ms == prev_open => {
                                // Same bar updated — replace with latest (more accurate H/L/C/volume)
                                pending = Some((open_ms, kline));
                            }
                            _ => {
                                // Stale/reordered update — ignore
                            }
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
        }

        if last_metrics_print.elapsed() >= metrics_interval {
            state.metrics.report();
            last_metrics_print = Instant::now();
        }
    }
}
