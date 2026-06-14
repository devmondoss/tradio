//! HTF Shorts Detector — señales M1 mineadas con stop estructural H1
//!
//! Sistema portado desde `apps/rbf-review/api/shorts_htf_backtest.py`.
//! Patrones mineados por MFE/MAE sobre 9,000+ barras M1 reales.
//! Edge existe EXCLUSIVAMENTE con stop H1 < 0.75% del precio de entrada.
//!
//! Reglas congeladas 2026-06-13. No modificar hasta walk-forward ~2026-07-05.
//! Criterio pass: WR ≥ 55% y AvgR ≥ +0.30R en datos nuevos.
//!
//! Correcciones vs implementación inicial (alineación 1:1 con backtest Python):
//! - D1 trend: EMA20 real desde velas D1 Binance (no proxy EMA-60 M1)
//! - H1 high/ATR: buckets H1 UTC reales (no rolling 60 barras M1)
//! - Fees: descontados en result_r (0.07% RT = fee_r = 0.0007 * entry / risk)
//! - Cooldown: aplica también al cerrar un trade (no solo al abrir)

use serde::{Deserialize, Serialize};
use std::collections::VecDeque;

// ── Constantes calibradas v2 ──────────────────────────────────────────────────

const MIN_STOP_PCT: f64 = 0.30;
const MAX_STOP_PCT: f64 = 0.75;
const TARGET_R: f64 = 2.5;
const CVD_FLIP_BARS: usize = 5;
const OBI_FLIP_THR: f64 = 0.15;
const MIN_PROFIT_CVD: f64 = 1.0;
const COOLDOWN_BARS: usize = 30;
const FORWARD_MAX: usize = 1200;
const FEE_RT: f64 = 0.0007; // 0.07% round-trip (taker × 2)

/// Sin bloqueo de horas — sobreajuste con n<100 trades (revertido 2026-06-14)
const BLOCKED_HOURS: [u8; 0] = [];

// ── Tipos públicos ────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HtfSignal {
    pub symbol: String,
    pub ts_ms: i64,
    pub sig: String,
    pub entry: f64,
    pub stop: f64,
    pub target: f64,
    pub stop_pct: f64,
    pub session: String,
    pub d1_trend: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HtfTrade {
    pub signal: HtfSignal,
    pub result_r: Option<f64>,
    pub gross_r: Option<f64>,   // antes de fees (para comparar con backtest)
    pub fee_r: Option<f64>,     // costo en R (fee_r = 0.07% * entry / risk)
    pub reason: Option<String>,
    pub exit_price: Option<f64>,
    pub exit_ts_ms: Option<i64>,
    pub duration_bars: Option<usize>,
    pub is_open: bool,
}

/// Vela H1 agregada desde barras M1 (bucket horario UTC exacto)
#[derive(Debug, Clone, Default)]
pub struct H1Candle {
    pub ts_h: i64,   // hora UTC en segundos (ts_ms / 3_600_000 * 3600)
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub bar_count: usize,
}

/// Contexto de barra M1 que el monitor pasa al detector
#[derive(Debug, Clone)]
pub struct HtfBarContext {
    pub ts_ms: i64,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub cvd_slope: Option<f64>,
    pub obi_l5: f64,
    pub obi_fast: f64,
    pub vr: f64,
    pub oi_momentum: Option<bool>,
    pub equal_high: bool,
    pub dz: f64,
    pub absorption: String,
    pub vpin: f64,
    pub regime: String,
    pub session: String,
    pub atr: f64,
}

// ── Estado del detector ───────────────────────────────────────────────────────

pub struct HtfShortsState {
    symbol: String,
    last_sig_bar: usize,
    bar_count: usize,
    active_trade: Option<ActiveTrade>,
    cvd_streak: usize,
    obi_streak: usize,

    // D1 EMA20 real — seeded al arrancar desde Binance D1 REST
    d1_ema20: Option<f64>,
    d1_closes: VecDeque<f64>, // últimas 30 cierres D1 para calcular EMA20
    d1_last_day: i64,         // día UTC del último cierre procesado

    // H1 buckets reales — velas H1 exactas por hora UTC
    h1_current: H1Candle,           // vela H1 en construcción (barra actual)
    h1_prev: Option<H1Candle>,      // vela H1 anterior (completa, usada para stop)
    h1_history: VecDeque<H1Candle>, // últimas 14 H1 completas para ATR H1
}

struct ActiveTrade {
    entry: f64,
    stop: f64,
    risk: f64,
    target: f64,
    fee_r: f64,
    signal: HtfSignal,
    bars_in_trade: usize,
}

impl HtfShortsState {
    pub fn new(symbol: &str) -> Self {
        Self {
            symbol: symbol.to_string(),
            last_sig_bar: 0,
            bar_count: 0,
            active_trade: None,
            cvd_streak: 0,
            obi_streak: 0,
            d1_ema20: None,
            d1_closes: VecDeque::with_capacity(31),
            d1_last_day: -1,
            h1_current: H1Candle::default(),
            h1_prev: None,
            h1_history: VecDeque::with_capacity(15),
        }
    }

    /// Seed con velas D1 históricas de Binance — llamar en warm_up antes de on_bar_close.
    /// `closes` debe estar ordenado de más antiguo a más reciente.
    pub fn seed_d1(&mut self, closes: &[f64]) {
        for &c in closes {
            self.push_d1_close(c);
        }
        println!(
            "[htf] D1 seeded {} closes, EMA20={:?}",
            closes.len(),
            self.d1_ema20.map(|v| format!("{v:.2}"))
        );
    }

    /// Seed con velas H1 históricas — llamar en warm_up antes de on_bar_close.
    /// Cada entrada es (ts_ms_open, high, low, close).
    pub fn seed_h1(&mut self, candles: &[(i64, f64, f64, f64)]) {
        for &(ts_ms, h, l, c) in candles {
            let ts_h = (ts_ms / 3_600_000) * 3600;
            let candle = H1Candle { ts_h, high: h, low: l, close: c, bar_count: 60 };
            self.push_h1_complete(candle);
        }
        println!("[htf] H1 seeded {} candles", candles.len());
    }

    fn push_d1_close(&mut self, close: f64) {
        self.d1_closes.push_back(close);
        if self.d1_closes.len() > 30 {
            self.d1_closes.pop_front();
        }
        // EMA20: alpha = 2/(20+1)
        const ALPHA: f64 = 2.0 / 21.0;
        if self.d1_closes.len() < 20 {
            return;
        }
        self.d1_ema20 = Some(match self.d1_ema20 {
            None => {
                // seed con SMA20 de las primeras 20 velas
                self.d1_closes.iter().rev().take(20).sum::<f64>() / 20.0
            }
            Some(prev) => prev * (1.0 - ALPHA) + close * ALPHA,
        });
    }

    fn push_h1_complete(&mut self, candle: H1Candle) {
        self.h1_prev = Some(candle.clone());
        self.h1_history.push_back(candle);
        if self.h1_history.len() > 14 {
            self.h1_history.pop_front();
        }
    }

    fn d1_trend(&self, price: f64) -> &'static str {
        match self.d1_ema20 {
            None => "unknown",
            Some(ema) => {
                if price > ema * 1.005 { "bull" }
                else if price < ema * 0.995 { "bear" }
                else { "neutral" }
            }
        }
    }

    fn h1_atr(&self) -> f64 {
        let n = self.h1_history.len();
        if n == 0 { return 0.0; }
        self.h1_history.iter().map(|c| c.high - c.low).sum::<f64>() / n as f64
    }

    /// Llamar en cada cierre de barra M1.
    pub fn on_bar_close(&mut self, ctx: &HtfBarContext) -> Option<HtfTrade> {
        self.bar_count += 1;

        // ── Mantener H1 bucket UTC real ───────────────────────────────────────
        let bar_h = ctx.ts_ms / 3_600_000; // hora UTC como entero
        if self.h1_current.ts_h == 0 {
            // primera barra — inicializar bucket
            self.h1_current = H1Candle {
                ts_h: bar_h,
                high: ctx.high,
                low: ctx.low,
                close: ctx.close,
                bar_count: 1,
            };
        } else if bar_h != self.h1_current.ts_h {
            // hora cambió — cerrar la H1 anterior y abrir nueva
            let completed = std::mem::replace(&mut self.h1_current, H1Candle {
                ts_h: bar_h,
                high: ctx.high,
                low: ctx.low,
                close: ctx.close,
                bar_count: 1,
            });
            self.push_h1_complete(completed);
        } else {
            self.h1_current.high = self.h1_current.high.max(ctx.high);
            self.h1_current.low  = self.h1_current.low.min(ctx.low);
            self.h1_current.close = ctx.close;
            self.h1_current.bar_count += 1;
        }

        // ── Mantener D1 EMA20 real ────────────────────────────────────────────
        // Actualizar al primer cierre del día UTC (barra M1 cuya hora es 23:59)
        let bar_day = ctx.ts_ms / 86_400_000;
        let bar_hour = (ctx.ts_ms / 3_600_000) % 24;
        if bar_hour == 23 && bar_day != self.d1_last_day {
            self.push_d1_close(ctx.close);
            self.d1_last_day = bar_day;
        }

        // ── 1. Actualizar trade activo si existe ──────────────────────────────
        if let Some(trade) = self.active_trade.take() {
            let result = self.update_active_trade(trade, ctx);
            match result {
                TradeUpdate::StillOpen(t) => { self.active_trade = Some(t); }
                TradeUpdate::Closed(htf_trade) => {
                    self.cvd_streak = 0;
                    self.obi_streak = 0;
                    return Some(htf_trade);
                }
            }
            return None;
        }

        // ── 2. Cooldown ───────────────────────────────────────────────────────
        if self.bar_count - self.last_sig_bar < COOLDOWN_BARS {
            return None;
        }

        // ── 3. D1 trend filter ────────────────────────────────────────────────
        if self.d1_trend(ctx.close) == "bull" {
            return None;
        }

        // ── 4. Detectar señal M1 ─────────────────────────────────────────────
        let sig = detect_signal(&self.symbol, ctx)?;

        // ── 5. Stop H1 real (vela H1 que contiene esta barra M1) ─────────────
        // Usamos la H1 en construcción (la hora actual), no la anterior
        let h1_high = self.h1_current.high;
        let h1_atr  = self.h1_atr();
        if h1_atr <= 0.0 { return None; }

        let stop_price  = h1_high + 0.3 * h1_atr;
        let entry_price = ctx.close;
        let risk = stop_price - entry_price;
        if risk <= 0.0 { return None; }

        let stop_pct = risk / entry_price * 100.0;
        if stop_pct < MIN_STOP_PCT || stop_pct > MAX_STOP_PCT { return None; }

        // ── 6. Fee en R (igual que el backtest Python) ────────────────────────
        let fee_r = FEE_RT * entry_price / risk;

        self.last_sig_bar = self.bar_count;
        self.cvd_streak = 0;
        self.obi_streak = 0;

        let d1_trend = self.d1_trend(ctx.close).to_string();
        let signal = HtfSignal {
            symbol: self.symbol.clone(),
            ts_ms: ctx.ts_ms,
            sig,
            entry: entry_price,
            stop: stop_price,
            target: entry_price - TARGET_R * risk,
            stop_pct: (stop_pct * 1000.0).round() / 1000.0,
            session: ctx.session.clone(),
            d1_trend,
        };

        let htf_trade = HtfTrade {
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
            target: entry_price - TARGET_R * risk,
            fee_r,
            signal,
            bars_in_trade: 0,
        });

        Some(htf_trade)
    }

    fn update_active_trade(&mut self, mut trade: ActiveTrade, ctx: &HtfBarContext) -> TradeUpdate {
        trade.bars_in_trade += 1;
        let h = ctx.high;
        let l = ctx.low;

        if l <= trade.target {
            let target = trade.target;
            let gross = TARGET_R;
            return TradeUpdate::Closed(self.close_trade(trade, gross, "TAKE_PROFIT", target, ctx.ts_ms));
        }

        if h >= trade.stop {
            let stop = trade.stop;
            return TradeUpdate::Closed(self.close_trade(trade, -1.0, "STOP_LOSS", stop, ctx.ts_ms));
        }

        let cvd_slope = ctx.cvd_slope.unwrap_or(0.0);
        let obi_fast  = ctx.obi_fast;
        let curr_r    = (trade.entry - ctx.close) / trade.risk;

        if cvd_slope > 0.0 { self.cvd_streak += 1; } else { self.cvd_streak = 0; }
        if obi_fast > OBI_FLIP_THR { self.obi_streak += 1; } else { self.obi_streak = 0; }

        if self.cvd_streak >= CVD_FLIP_BARS && self.obi_streak >= 1 && curr_r >= MIN_PROFIT_CVD {
            let exit_px = ctx.close;
            let gross   = (trade.entry - exit_px) / trade.risk;
            return TradeUpdate::Closed(self.close_trade(trade, gross, "CVD_EXHAUSTION", exit_px, ctx.ts_ms));
        }

        if trade.bars_in_trade >= FORWARD_MAX {
            let exit_px = ctx.close;
            let gross   = (trade.entry - exit_px) / trade.risk;
            return TradeUpdate::Closed(self.close_trade(trade, gross, "EXPIRED", exit_px, ctx.ts_ms));
        }

        TradeUpdate::StillOpen(trade)
    }

    fn close_trade(&self, trade: ActiveTrade, gross_r: f64, reason: &str, exit_px: f64, exit_ts_ms: i64) -> HtfTrade {
        let net_r = gross_r - trade.fee_r;
        HtfTrade {
            signal:        trade.signal,
            result_r:      Some((net_r   * 10000.0).round() / 10000.0),
            gross_r:       Some((gross_r * 10000.0).round() / 10000.0),
            fee_r:         Some((trade.fee_r * 10000.0).round() / 10000.0),
            reason:        Some(reason.to_string()),
            exit_price:    Some(exit_px),
            exit_ts_ms:    Some(exit_ts_ms),
            duration_bars: Some(trade.bars_in_trade),
            is_open:       false,
        }
    }
}

enum TradeUpdate {
    StillOpen(ActiveTrade),
    Closed(HtfTrade),
}

// ── Detector de señales M1 ────────────────────────────────────────────────────

fn detect_signal(symbol: &str, ctx: &HtfBarContext) -> Option<String> {
    if matches!(ctx.session.as_str(), "OffHours" | "Asia") {
        return None;
    }

    let hour = ((ctx.ts_ms / 1000) % 86400 / 3600) as u8;
    if BLOCKED_HOURS.contains(&hour) {
        return None;
    }

    let is_london = matches!(ctx.session.as_str(), "London" | "LondonNyOverlap");
    let is_ny     = ctx.session == "NewYork";
    let is_exp    = ctx.regime == "Expansion";
    let abs_ask   = ctx.absorption == "Ask";
    let eq_true   = ctx.equal_high;
    let oi        = ctx.oi_momentum.unwrap_or(false);
    let vr        = ctx.vr;
    let vpin      = ctx.vpin;
    let obif      = ctx.obi_fast;

    let rng     = (ctx.high - ctx.low).max(1e-10);
    let body    = (ctx.close - ctx.open).abs();
    let wick_hi = ctx.high - ctx.open.max(ctx.close);
    let is_shoot = (wick_hi / rng) > 0.45 && (body / rng) < 0.40;

    match symbol {
        "BTCUSDT" => {
            if !is_london && !is_ny { return None; }
            if is_shoot && abs_ask && obif < 0.0 { return Some("btc:shoot+ask+obi".into()); }
            if is_shoot && is_london              { return Some("btc:shoot+london".into()); }
        }
        "ETHUSDT" => {
            if is_ny && oi && eq_true             { return Some("eth:ny+oi+eq".into()); }
            if abs_ask && is_london && is_exp     { return Some("eth:ask+london+exp".into()); }
            if abs_ask && vpin > 0.6 && is_ny    { return Some("eth:ask+vpin+ny".into()); }
        }
        "SOLUSDT" => {
            if is_ny && vr > 4.0 && oi            { return Some("sol:ny+vr4+oi".into()); }
            if is_ny && vr > 4.0 && eq_true       { return Some("sol:ny+vr4+eq".into()); }
            if eq_true && is_london && is_exp     { return Some("sol:eq+london+exp".into()); }
        }
        // BNB: solo NY — London WR=30-42% en todos los patrones (calibrado 2026-06-14)
        "BNBUSDT" => {
            if !is_ny { return None; }
            if eq_true && oi                       { return Some("bnb:eq+ny+oi".into()); }
            if oi                                  { return Some("bnb:oi+ny".into()); }
        }
        // XRP: solo NY — London WR=26-39% en todos los patrones (calibrado 2026-06-14)
        "XRPUSDT" => {
            if !is_ny { return None; }
            if eq_true && oi                       { return Some("xrp:eq+ny+oi".into()); }
            if abs_ask                             { return Some("xrp:ask+ny".into()); }
            if oi                                  { return Some("xrp:oi+ny".into()); }
        }
        _ => {}
    }

    None
}
