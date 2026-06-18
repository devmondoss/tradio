//! MTF Longs Detector — señales M1 mineadas con stop estructural H1
//!
//! Espejo alcista del sistema MTF Shorts.
//! Patrones mineados 2026-06-14 v2 sobre ~10,000 barras M1 por símbolo.
//!
//! Filtro H4 EMA20 en vez de D1 — permite capturar recuperaciones intraday
//! dentro de períodos D1 bear (donde D1 bloqueaba todo).
//!
//! Edge confirmado (v2 con VWAP + nuevos activos):
//!   ETH: +6 patrones (hammer+ny, WR=62.2%)
//!   SOL: +9 patrones incluyendo above_vwap+stk+london WR=71.9%
//!   XRP: 5 patrones nuevos (n=13-35, WR=57-69%)
//!   BNB: 1 patrón (hammer+dz_buy n=20, WR=55%)
//!   BTC: sin edge suficiente (n<10 en todos los candidatos)

use super::mtf_shorts_detector::{H1Candle, MtfBarContext};
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;

// ── Constantes ────────────────────────────────────────────────────────────────

const MIN_STOP_PCT: f64 = 0.30;
const MAX_STOP_PCT: f64 = 0.75;
const TARGET_R: f64 = 2.5;
const CVD_FLIP_BARS: usize = 5;
const OBI_FLIP_THR: f64 = 0.15;
const MIN_PROFIT_CVD: f64 = 1.0;
const COOLDOWN_BARS: usize = 30;
const FORWARD_MAX: usize = 1200;
const FEE_RT: f64 = 0.0007;

// ── Tipos públicos ────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MtfLongSignal {
    pub symbol: String,
    pub ts_ms: i64,
    pub sig: String,
    pub entry: f64,
    pub stop: f64,
    pub target: f64,
    pub stop_pct: f64,
    pub session: String,
    pub h4_trend: String,
    // microestructura snapshot en entrada
    pub obi_entry: f64,
    pub cvd_slope_entry: Option<f64>,
    pub dz_score: f64,
    pub stacked_imb: String,
    pub equal_low: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MtfLongTrade {
    pub signal: MtfLongSignal,
    pub result_r: Option<f64>,
    pub gross_r: Option<f64>,
    pub fee_r: Option<f64>,
    pub reason: Option<String>,
    pub exit_price: Option<f64>,
    pub exit_ts_ms: Option<i64>,
    pub duration_bars: Option<usize>,
    pub is_open: bool,
}

// ── Estado del detector ───────────────────────────────────────────────────────

pub struct MtfLongsState {
    symbol: String,
    last_sig_bar: usize,
    bar_count: usize,
    active_trade: Option<ActiveTrade>,
    cvd_streak: usize,
    obi_streak: usize,

    // H4 EMA20 — seeded al arrancar desde Binance H4 REST
    h4_ema20: Option<f64>,
    h4_closes: VecDeque<f64>,
    h4_last_bucket: i64, // ts_ms / (4*3_600_000)
    h4_high: f64,
    h4_low: f64,
    h4_close: f64,

    // H1 buckets (misma lógica que shorts, pero usamos low en vez de high)
    h1_current: H1Candle,
    h1_history: VecDeque<H1Candle>,
}

struct ActiveTrade {
    entry: f64,
    stop: f64,
    risk: f64,
    target: f64,
    fee_r: f64,
    signal: MtfLongSignal,
    bars_in_trade: usize,
}

impl MtfLongsState {
    pub fn new(symbol: &str) -> Self {
        Self {
            symbol: symbol.to_string(),
            last_sig_bar: 0,
            bar_count: 0,
            active_trade: None,
            cvd_streak: 0,
            obi_streak: 0,
            h4_ema20: None,
            h4_closes: VecDeque::with_capacity(25),
            h4_last_bucket: -1,
            h4_high: f64::NEG_INFINITY,
            h4_low: f64::INFINITY,
            h4_close: 0.0,
            h1_current: H1Candle::default(),
            h1_history: VecDeque::with_capacity(15),
        }
    }

    /// Restaura un trade Long abierto desde Supabase después de un redeploy.
    pub fn restore_active_trade(
        &mut self,
        entry: f64,
        stop: f64,
        target: f64,
        ts_ms: i64,
        sig: String,
        session: String,
        h4_trend: String,
        stop_pct: f64,
        obi_entry: f64,
        cvd_slope_entry: Option<f64>,
        dz_score: f64,
        stacked_imb: String,
        equal_low: bool,
    ) {
        let risk = entry - stop;
        let fee_r = 0.0007 * entry / risk;
        let signal = MtfLongSignal {
            symbol: self.symbol.clone(),
            ts_ms,
            sig,
            entry,
            stop,
            target,
            stop_pct,
            session,
            h4_trend,
            obi_entry,
            cvd_slope_entry,
            dz_score,
            stacked_imb,
            equal_low,
        };
        self.active_trade = Some(ActiveTrade {
            entry,
            stop,
            risk,
            target,
            fee_r,
            signal,
            bars_in_trade: 0,
        });
        self.last_sig_bar = self.bar_count;
    }

    pub fn has_active_trade(&self) -> bool {
        self.active_trade.is_some()
    }

    /// ts_ms de entrada del trade activo — para filtrar barras de warmup anteriores a la entrada.
    pub fn active_entry_ms(&self) -> Option<i64> {
        self.active_trade.as_ref().map(|t| t.signal.ts_ms)
    }

    /// Seed con velas H4 históricas — llamar en warm_up.
    /// Cada entrada es (ts_ms_open, high, low, close).
    pub fn seed_h4(&mut self, candles: &[(i64, f64, f64, f64)]) {
        for &(ts_ms, _h, _l, c) in candles {
            let bucket = ts_ms / (4 * 3_600_000);
            self.h4_last_bucket = bucket;
            self.push_h4_close(c);
        }
        println!(
            "[mtf_long] H4 seeded {} candles, EMA20={:?}",
            candles.len(),
            self.h4_ema20.map(|v| format!("{v:.2}"))
        );
    }

    /// Seed con velas H1 históricas para ATR H1.
    pub fn seed_h1(&mut self, candles: &[(i64, f64, f64, f64)]) {
        for &(ts_ms, h, l, c) in candles {
            let ts_h = (ts_ms / 3_600_000) * 3600;
            let candle = H1Candle {
                ts_h,
                high: h,
                low: l,
                close: c,
                bar_count: 60,
            };
            self.push_h1_complete(candle);
        }
        println!("[mtf_long] H1 seeded {} candles", candles.len());
    }

    fn push_h4_close(&mut self, close: f64) {
        self.h4_closes.push_back(close);
        if self.h4_closes.len() > 25 {
            self.h4_closes.pop_front();
        }
        const ALPHA: f64 = 2.0 / 21.0;
        if self.h4_closes.len() < 20 {
            return;
        }
        self.h4_ema20 = Some(match self.h4_ema20 {
            None => self.h4_closes.iter().rev().take(20).sum::<f64>() / 20.0,
            Some(prev) => prev * (1.0 - ALPHA) + close * ALPHA,
        });
    }

    fn push_h1_complete(&mut self, candle: H1Candle) {
        self.h1_history.push_back(candle);
        if self.h1_history.len() > 14 {
            self.h1_history.pop_front();
        }
    }

    fn h4_trend(&self, price: f64) -> &'static str {
        match self.h4_ema20 {
            None => "unknown",
            Some(ema) => {
                if price > ema * 1.005 {
                    "bull"
                } else if price < ema * 0.995 {
                    "bear"
                } else {
                    "neutral"
                }
            }
        }
    }

    fn h1_atr(&self) -> f64 {
        let n = self.h1_history.len();
        if n == 0 {
            return 0.0;
        }
        self.h1_history.iter().map(|c| c.high - c.low).sum::<f64>() / n as f64
    }

    pub fn on_bar_close(&mut self, ctx: &MtfBarContext) -> Option<MtfLongTrade> {
        self.bar_count += 1;

        // ── Mantener H4 bucket UTC ────────────────────────────────────────────
        let bucket = ctx.ts_ms / (4 * 3_600_000);
        if self.h4_last_bucket == -1 {
            self.h4_last_bucket = bucket;
            self.h4_high = ctx.high;
            self.h4_low = ctx.low;
            self.h4_close = ctx.close;
        } else if bucket != self.h4_last_bucket {
            self.push_h4_close(self.h4_close);
            self.h4_last_bucket = bucket;
            self.h4_high = ctx.high;
            self.h4_low = ctx.low;
            self.h4_close = ctx.close;
        } else {
            if ctx.high > self.h4_high {
                self.h4_high = ctx.high;
            }
            if ctx.low < self.h4_low {
                self.h4_low = ctx.low;
            }
            self.h4_close = ctx.close;
        }

        // ── Mantener H1 bucket UTC real ───────────────────────────────────────
        let bar_h = ctx.ts_ms / 3_600_000;
        if self.h1_current.ts_h == 0 {
            self.h1_current = H1Candle {
                ts_h: bar_h,
                high: ctx.high,
                low: ctx.low,
                close: ctx.close,
                bar_count: 1,
            };
        } else if bar_h != self.h1_current.ts_h {
            let completed = std::mem::replace(
                &mut self.h1_current,
                H1Candle {
                    ts_h: bar_h,
                    high: ctx.high,
                    low: ctx.low,
                    close: ctx.close,
                    bar_count: 1,
                },
            );
            self.push_h1_complete(completed);
        } else {
            self.h1_current.high = self.h1_current.high.max(ctx.high);
            self.h1_current.low = self.h1_current.low.min(ctx.low);
            self.h1_current.close = ctx.close;
            self.h1_current.bar_count += 1;
        }

        // ── 1. Actualizar trade activo ────────────────────────────────────────
        if let Some(trade) = self.active_trade.take() {
            let result = self.update_active_trade(trade, ctx);
            match result {
                TradeUpdate::StillOpen(t) => {
                    self.active_trade = Some(t);
                }
                TradeUpdate::Closed(t) => {
                    self.cvd_streak = 0;
                    self.obi_streak = 0;
                    return Some(t);
                }
            }
            return None;
        }

        // ── 2. Cooldown ───────────────────────────────────────────────────────
        if self.bar_count - self.last_sig_bar < COOLDOWN_BARS {
            return None;
        }

        // ── 3. H4 trend filter — no entrar en H4 bear ────────────────────────
        if self.h4_trend(ctx.close) == "bear" {
            return None;
        }

        // ── 4. Detectar señal M1 ─────────────────────────────────────────────
        let sig = detect_signal_long(&self.symbol, ctx)?;

        // ── 5. Stop = H1_low - 0.3×ATR ───────────────────────────────────────
        let h1_low = self.h1_current.low;
        let h1_atr = self.h1_atr();
        if h1_atr <= 0.0 {
            return None;
        }

        let stop_price = h1_low - 0.3 * h1_atr;
        let entry_price = ctx.close;
        let risk = entry_price - stop_price;
        if risk <= 0.0 {
            return None;
        }

        let stop_pct = risk / entry_price * 100.0;
        if stop_pct < MIN_STOP_PCT || stop_pct > MAX_STOP_PCT {
            return None;
        }

        let fee_r = FEE_RT * entry_price / risk;

        self.last_sig_bar = self.bar_count;
        self.cvd_streak = 0;
        self.obi_streak = 0;

        let h4t = self.h4_trend(ctx.close).to_string();
        let signal = MtfLongSignal {
            symbol: self.symbol.clone(),
            ts_ms: ctx.ts_ms,
            sig,
            entry: entry_price,
            stop: stop_price,
            target: entry_price + TARGET_R * risk,
            stop_pct: (stop_pct * 1000.0).round() / 1000.0,
            session: ctx.session.clone(),
            h4_trend: h4t,
            obi_entry: ctx.obi_fast,
            cvd_slope_entry: ctx.cvd_slope,
            dz_score: ctx.dz,
            stacked_imb: ctx.stacked_imb.clone(),
            equal_low: ctx.equal_low,
        };

        let htf_trade = MtfLongTrade {
            signal: signal.clone(),
            result_r: None,
            gross_r: None,
            fee_r: Some((fee_r * 10000.0).round() / 10000.0),
            reason: None,
            exit_price: None,
            exit_ts_ms: None,
            duration_bars: None,
            is_open: true,
        };

        self.active_trade = Some(ActiveTrade {
            entry: entry_price,
            stop: stop_price,
            risk,
            target: entry_price + TARGET_R * risk,
            fee_r,
            signal,
            bars_in_trade: 0,
        });

        Some(htf_trade)
    }

    fn update_active_trade(&mut self, mut trade: ActiveTrade, ctx: &MtfBarContext) -> TradeUpdate {
        trade.bars_in_trade += 1;
        let h = ctx.high;
        let l = ctx.low;

        // Long: TP si el precio sube al target, SL si baja al stop
        if h >= trade.target {
            let target = trade.target;
            return TradeUpdate::Closed(self.close_trade(
                trade,
                TARGET_R,
                "TAKE_PROFIT",
                target,
                ctx.ts_ms,
            ));
        }
        if l <= trade.stop {
            let stop = trade.stop;
            return TradeUpdate::Closed(self.close_trade(
                trade,
                -1.0,
                "STOP_LOSS",
                stop,
                ctx.ts_ms,
            ));
        }

        let cvd_slope = ctx.cvd_slope.unwrap_or(0.0);
        let obi_fast = ctx.obi_fast;
        let curr_r = (ctx.close - trade.entry) / trade.risk;

        // CVD exit para longs: sellers retomando (CVD negativo, OBI negativo)
        if cvd_slope < 0.0 {
            self.cvd_streak += 1;
        } else {
            self.cvd_streak = 0;
        }
        if obi_fast < -OBI_FLIP_THR {
            self.obi_streak += 1;
        } else {
            self.obi_streak = 0;
        }

        if self.cvd_streak >= CVD_FLIP_BARS && self.obi_streak >= 1 && curr_r >= MIN_PROFIT_CVD {
            let exit_px = ctx.close;
            let gross = (exit_px - trade.entry) / trade.risk;
            return TradeUpdate::Closed(self.close_trade(
                trade,
                gross,
                "CVD_EXHAUSTION",
                exit_px,
                ctx.ts_ms,
            ));
        }

        if trade.bars_in_trade >= FORWARD_MAX {
            let exit_px = ctx.close;
            let gross = (exit_px - trade.entry) / trade.risk;
            return TradeUpdate::Closed(
                self.close_trade(trade, gross, "EXPIRED", exit_px, ctx.ts_ms),
            );
        }

        TradeUpdate::StillOpen(trade)
    }

    fn close_trade(
        &self,
        trade: ActiveTrade,
        gross_r: f64,
        reason: &str,
        exit_px: f64,
        exit_ts_ms: i64,
    ) -> MtfLongTrade {
        let net_r = gross_r - trade.fee_r;
        MtfLongTrade {
            signal: trade.signal,
            result_r: Some((net_r * 10000.0).round() / 10000.0),
            gross_r: Some((gross_r * 10000.0).round() / 10000.0),
            fee_r: Some((trade.fee_r * 10000.0).round() / 10000.0),
            reason: Some(reason.to_string()),
            exit_price: Some(exit_px),
            exit_ts_ms: Some(exit_ts_ms),
            duration_bars: Some(trade.bars_in_trade),
            is_open: false,
        }
    }
}

enum TradeUpdate {
    StillOpen(ActiveTrade),
    Closed(MtfLongTrade),
}

// ── Detector de señales M1 para longs ────────────────────────────────────────

fn detect_signal_long(symbol: &str, ctx: &MtfBarContext) -> Option<String> {
    if matches!(ctx.session.as_str(), "OffHours" | "Asia") {
        return None;
    }

    let is_london = matches!(ctx.session.as_str(), "London" | "LondonNyOverlap");
    let is_ny = ctx.session == "NewYork";
    let is_exp = ctx.regime == "Expansion";
    let oi = ctx.oi_momentum.unwrap_or(false);
    let eq_low = ctx.equal_low;
    let obi = ctx.obi_l5;
    let dz = ctx.dz;
    let stk_bull = ctx.stacked_imb == "Bullish";
    let obi_pos = obi > 0.2;
    let dz_buy = dz > 0.5;
    let vr = ctx.vr;

    let above_vwap = ctx.vwap_session.map_or(false, |v| v > 0.0 && ctx.close > v);

    let rng = (ctx.high - ctx.low).max(1e-10);
    let body = (ctx.close - ctx.open).abs();
    let wick_lo = ctx.open.min(ctx.close) - ctx.low;
    let is_hammer = (wick_lo / rng) > 0.45 && (body / rng) < 0.40;

    match symbol {
        // ETH: mineado v2 2026-06-14 — London + NY con edge WR≥55%
        "ETHUSDT" => {
            if stk_bull && is_london {
                return Some("eth:stacked_bull+london".into());
            }
            if stk_bull && is_ny {
                return Some("eth:stacked_bull+ny".into());
            }
            if is_hammer && dz_buy {
                return Some("eth:hammer+dz_buy".into());
            }
            if is_hammer && is_ny {
                return Some("eth:hammer+ny".into());
            }
            if eq_low && is_london && is_exp {
                return Some("eth:eq_low+london+exp".into());
            }
            if oi && is_ny {
                return Some("eth:oi+ny".into());
            }
        }
        // SOL: London domina, VWAP-centric subsets primero (más específicos)
        "SOLUSDT" => {
            if above_vwap && stk_bull && is_london {
                return Some("sol:above_vwap+stk+london".into());
            }
            if above_vwap && stk_bull && oi {
                return Some("sol:above_vwap+stk+oi".into());
            }
            if above_vwap && oi && is_ny {
                return Some("sol:above_vwap+oi+ny".into());
            }
            if vr > 3.0 && is_london {
                return Some("sol:vr_high+london".into());
            }
            if is_hammer && dz_buy {
                return Some("sol:hammer+dz_buy".into());
            }
            if is_hammer && is_london {
                return Some("sol:hammer+london".into());
            }
            if stk_bull && is_london {
                return Some("sol:stacked_bull+london".into());
            }
            if is_hammer && obi_pos {
                return Some("sol:hammer+obi_pos".into());
            }
            if oi && is_london {
                return Some("sol:oi+london".into());
            }
            if eq_low && is_london && is_exp {
                return Some("sol:eq_low+london+exp".into());
            }
            if eq_low && is_london {
                return Some("sol:eq_low+london".into());
            }
            if stk_bull && is_ny {
                return Some("sol:stacked_bull+ny".into());
            }
            if is_hammer && is_ny {
                return Some("sol:hammer+ny".into());
            }
            if oi && is_ny {
                return Some("sol:oi+ny".into());
            }
        }
        // XRP: patrones mineados 2026-06-14 — NY domina (n≥13, WR≥56%)
        "XRPUSDT" => {
            if is_hammer && dz_buy && is_ny {
                return Some("xrp:hammer+dz_buy".into());
            }
            if is_hammer && obi_pos && is_ny {
                return Some("xrp:hammer+obi_pos".into());
            }
            if is_hammer && stk_bull && is_ny {
                return Some("xrp:hammer+stacked".into());
            }
            if is_hammer && is_ny {
                return Some("xrp:hammer+ny".into());
            }
            if stk_bull && is_ny {
                return Some("xrp:stacked_bull+ny".into());
            }
        }
        // BNB: un patrón NY con edge claro (mineado 2026-06-14)
        "BNBUSDT" => {
            if is_hammer && dz_buy && is_ny {
                return Some("bnb:hammer+dz_buy".into());
            }
        }
        // BTC: sin edge en longs con n suficiente (mineado 2026-06-14)
        _ => {}
    }

    None
}
