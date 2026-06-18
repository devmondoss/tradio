use serde::{Deserialize, Serialize};
use std::collections::VecDeque;

const MIN_STOP_PCT: f64 = 0.0030;
const MAX_STOP_PCT: f64 = 0.0075;
const TARGET_R: f64 = 2.5; // fijo (Paso 1 2026-06-18): supera a regime+CVD en AvgR neto
const CVD_FLIP_BARS: usize = 5;
const OBI_FLIP_THR: f64 = 0.15;
const MIN_PROFIT_CVD: f64 = 1.0;
const FEE_RT: f64 = 0.0011; // futuros Bybit round-trip (taker 0.055% x2). Spot basico seria 0.0020
const FORWARD: usize = 1200; // timeout en barras M1 (~20h) — paridad con backtest Python
const LEVEL_TOL: f64 = 0.007;
const ATR_MULT: f64 = 0.40;
const H1_MS: i64 = 3_600_000;
const D1_MS: i64 = 86_400_000;
const WEEK_ROLLING_BARS: usize = 5 * 24 * 60;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MtfSpotBarContext {
    pub ts_ms: i64,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub cvd_slope: Option<f64>,
    pub obi10_mean: f64,
    pub delta: f64,
    pub vp_vah: Option<f64>,
    pub vp_val: Option<f64>,
    #[serde(default)]
    pub h1_high: Option<f64>,
    #[serde(default)]
    pub h1_low: Option<f64>,
    #[serde(default)]
    pub h1_atr: Option<f64>,
    #[serde(default)]
    pub prev_day_high: Option<f64>,
    #[serde(default)]
    pub prev_day_low: Option<f64>,
    #[serde(default)]
    pub asian_high: Option<f64>,
    #[serde(default)]
    pub asian_low: Option<f64>,
    #[serde(default)]
    pub weekly_high: Option<f64>,
    #[serde(default)]
    pub weekly_low: Option<f64>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum MtfSpotDirection {
    Long,
    Short,
}

impl MtfSpotDirection {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Long => "Long",
            Self::Short => "Short",
        }
    }

    pub fn strategy(self) -> &'static str {
        match self {
            Self::Long => "mtf_spot_longs_v1",
            Self::Short => "mtf_spot_shorts_v4",
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "Long" | "long" => Some(Self::Long),
            "Short" | "short" => Some(Self::Short),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MtfSpotSignal {
    pub symbol: String,
    pub ts_ms: i64,
    pub direction: MtfSpotDirection,
    pub strategy: String,
    pub sig: String,
    pub entry: f64,
    pub stop: f64,
    pub target: f64,
    pub stop_pct: f64,
    pub session: String,
    pub level: String,
    pub wick_pct: f64,
    pub obi_entry: f64,
    pub delta_entry: f64,
    pub cvd_slope_entry: Option<f64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MtfSpotTrade {
    pub signal: MtfSpotSignal,
    pub result_r: Option<f64>,
    pub gross_r: Option<f64>,
    pub fee_r: Option<f64>,
    pub reason: Option<String>,
    pub exit_price: Option<f64>,
    pub exit_ts_ms: Option<i64>,
    pub duration_bars: Option<usize>,
    pub mfe_r: Option<f64>,
    pub mae_r: Option<f64>,
    pub is_open: bool,
}

#[derive(Debug, Clone, Default)]
struct H1Candle {
    bucket: i64,
    high: f64,
    low: f64,
    close: f64,
}

#[derive(Debug, Clone)]
struct ActiveTrade {
    signal: MtfSpotSignal,
    entry: f64,
    stop: f64,
    target: f64,
    risk: f64,
    fee_r: f64,
    bars_in_trade: usize,
    mfe_r: f64,
    mae_r: f64,
}

#[derive(Debug)]
pub struct MtfSpotState {
    symbol: String,
    active_trade: Option<ActiveTrade>,
    bar_count: usize,
    cvd_streak: usize,
    h1_current: Option<H1Candle>,
    h1_history: VecDeque<H1Candle>,
    rolling_5d: VecDeque<(f64, f64)>,
    current_day: i64,
    daily_high: f64,
    daily_low: f64,
    prev_day_high: Option<f64>,
    prev_day_low: Option<f64>,
    asian_high: Option<f64>,
    asian_low: Option<f64>,
}

impl MtfSpotState {
    pub fn new(symbol: &str) -> Self {
        Self {
            symbol: symbol.to_string(),
            active_trade: None,
            bar_count: 0,
            cvd_streak: 0,
            h1_current: None,
            h1_history: VecDeque::with_capacity(14),
            rolling_5d: VecDeque::with_capacity(WEEK_ROLLING_BARS + 1),
            current_day: -1,
            daily_high: f64::NEG_INFINITY,
            daily_low: f64::INFINITY,
            prev_day_high: None,
            prev_day_low: None,
            asian_high: None,
            asian_low: None,
        }
    }

    pub fn has_active_trade(&self) -> bool {
        self.active_trade.is_some()
    }

    pub fn active_entry_ms(&self) -> Option<i64> {
        self.active_trade.as_ref().map(|trade| trade.signal.ts_ms)
    }

    pub fn restore_active_trade(
        &mut self,
        direction: MtfSpotDirection,
        strategy: String,
        sig: String,
        entry: f64,
        stop: f64,
        target: f64,
        ts_ms: i64,
        session: String,
        level: String,
        stop_pct: f64,
        wick_pct: f64,
        obi_entry: f64,
        delta_entry: f64,
        cvd_slope_entry: Option<f64>,
    ) {
        let risk = match direction {
            MtfSpotDirection::Short => stop - entry,
            MtfSpotDirection::Long => entry - stop,
        };
        if risk <= 0.0 {
            return;
        }
        let fee_r = FEE_RT * entry / risk;
        let signal = MtfSpotSignal {
            symbol: self.symbol.clone(),
            ts_ms,
            direction,
            strategy,
            sig,
            entry,
            stop,
            target,
            stop_pct,
            session,
            level,
            wick_pct,
            obi_entry,
            delta_entry,
            cvd_slope_entry,
        };
        self.active_trade = Some(ActiveTrade {
            signal,
            entry,
            stop,
            target,
            risk,
            fee_r,
            bars_in_trade: 0,
            mfe_r: 0.0,
            mae_r: 0.0,
        });
    }

    pub fn warm_up_bar(&mut self, ctx: &MtfSpotBarContext) {
        self.bar_count += 1;
        self.update_levels(ctx);
    }

    pub fn on_bar_close(
        &mut self,
        ctx: &MtfSpotBarContext,
        allow_shorts: bool,
        allow_longs: bool,
    ) -> Option<MtfSpotTrade> {
        self.bar_count += 1;
        self.update_levels(ctx);

        if let Some(trade) = self.active_trade.take() {
            match self.update_active_trade(trade, ctx) {
                TradeUpdate::StillOpen(t) => {
                    self.active_trade = Some(t);
                    return None;
                }
                TradeUpdate::Closed(t) => {
                    self.cvd_streak = 0;
                    return Some(t);
                }
            }
        }

        if self.symbol != "BTCUSDT" {
            return None;
        }

        let candidate = match (allow_shorts, allow_longs) {
            (true, true) => self.detect_short(ctx).or_else(|| self.detect_long(ctx)),
            (true, false) => self.detect_short(ctx),
            (false, true) => self.detect_long(ctx),
            (false, false) => None,
        }?;

        self.open_trade(candidate, ctx)
    }

    fn update_levels(&mut self, ctx: &MtfSpotBarContext) {
        self.update_h1(ctx);
        self.update_daily_levels(ctx);
        self.rolling_5d.push_back((ctx.high, ctx.low));
        if self.rolling_5d.len() > WEEK_ROLLING_BARS {
            self.rolling_5d.pop_front();
        }
    }

    fn update_h1(&mut self, ctx: &MtfSpotBarContext) {
        let bucket = ctx.ts_ms / H1_MS;
        match self.h1_current.as_mut() {
            None => {
                self.h1_current = Some(H1Candle {
                    bucket,
                    high: ctx.high,
                    low: ctx.low,
                    close: ctx.close,
                });
            }
            Some(current) if current.bucket != bucket => {
                let completed = std::mem::replace(
                    current,
                    H1Candle {
                        bucket,
                        high: ctx.high,
                        low: ctx.low,
                        close: ctx.close,
                    },
                );
                self.h1_history.push_back(completed);
                if self.h1_history.len() > 14 {
                    self.h1_history.pop_front();
                }
            }
            Some(current) => {
                current.high = current.high.max(ctx.high);
                current.low = current.low.min(ctx.low);
                current.close = ctx.close;
            }
        }
    }

    fn update_daily_levels(&mut self, ctx: &MtfSpotBarContext) {
        let day = ctx.ts_ms / D1_MS;
        let hm = (ctx.ts_ms / 60_000) % 1440;

        if day != self.current_day {
            if self.current_day >= 0 && self.daily_high.is_finite() && self.daily_low.is_finite() {
                self.prev_day_high = Some(self.daily_high);
                self.prev_day_low = Some(self.daily_low);
            }
            self.current_day = day;
            self.daily_high = ctx.high;
            self.daily_low = ctx.low;
            self.asian_high = None;
            self.asian_low = None;
        } else {
            self.daily_high = self.daily_high.max(ctx.high);
            self.daily_low = self.daily_low.min(ctx.low);
        }

        if hm < 2 * 60 {
            self.asian_high = Some(self.asian_high.map_or(ctx.high, |v| v.max(ctx.high)));
            self.asian_low = Some(self.asian_low.map_or(ctx.low, |v| v.min(ctx.low)));
        }
    }

    fn h1_atr(&self) -> f64 {
        if self.h1_history.is_empty() {
            return 0.0;
        }
        self.h1_history.iter().map(|c| c.high - c.low).sum::<f64>() / self.h1_history.len() as f64
    }

    fn rolling_week_high(&self) -> Option<f64> {
        if self.rolling_5d.len() < 60 {
            return None;
        }
        Some(
            self.rolling_5d
                .iter()
                .map(|(h, _)| *h)
                .fold(f64::NEG_INFINITY, f64::max),
        )
    }

    fn rolling_week_low(&self) -> Option<f64> {
        if self.rolling_5d.len() < 60 {
            return None;
        }
        Some(
            self.rolling_5d
                .iter()
                .map(|(_, l)| *l)
                .fold(f64::INFINITY, f64::min),
        )
    }

    fn detect_short(&self, ctx: &MtfSpotBarContext) -> Option<SignalCandidate> {
        let session = short_session(ctx.ts_ms)?;
        let level = self.short_level(ctx)?;
        let parts: Vec<&str> = level.split('+').collect();

        if !parts.contains(&"VAH") {
            return None;
        }
        if parts.len() >= 3 && parts.contains(&"PDH") && parts.contains(&"AH") {
            return None;
        }
        if parts.len() == 2 && parts.contains(&"PDH") && parts.contains(&"VAH") {
            return None;
        }
        let hour = ((ctx.ts_ms / H1_MS) % 24) as u8;
        if parts.contains(&"WH") && hour == 15 {
            return None;
        }
        if level == "AH+VAH" && ctx.obi10_mean < -0.15 {
            return None;
        }

        let wick_pct = rejection_short(ctx)?;
        if !(ctx.obi10_mean < -0.05 || ctx.delta < 0.0) {
            return None;
        }

        Some(SignalCandidate {
            direction: MtfSpotDirection::Short,
            session,
            level,
            wick_pct,
        })
    }

    fn detect_long(&self, ctx: &MtfSpotBarContext) -> Option<SignalCandidate> {
        let session = long_session(ctx.ts_ms)?;
        let level = self.long_level(ctx)?;
        let parts: Vec<&str> = level.split('+').collect();

        if !parts.contains(&"VAL") {
            return None;
        }
        if parts.len() >= 3 && parts.contains(&"PDL") && parts.contains(&"AL") {
            return None;
        }

        let wick_pct = rejection_long(ctx)?;
        if !(ctx.obi10_mean > 0.05 || ctx.delta > 0.0) {
            return None;
        }

        Some(SignalCandidate {
            direction: MtfSpotDirection::Long,
            session,
            level,
            wick_pct,
        })
    }

    fn short_level(&self, ctx: &MtfSpotBarContext) -> Option<String> {
        let checks = [
            ("PDH", ctx.prev_day_high.or(self.prev_day_high)),
            ("AH", ctx.asian_high.or(self.asian_high)),
            ("WH", ctx.weekly_high.or_else(|| self.rolling_week_high())),
            ("VAH", ctx.vp_vah),
        ];
        join_near_levels(ctx.high, &checks)
    }

    fn long_level(&self, ctx: &MtfSpotBarContext) -> Option<String> {
        let checks = [
            ("PDL", ctx.prev_day_low.or(self.prev_day_low)),
            ("AL", ctx.asian_low.or(self.asian_low)),
            ("WL", ctx.weekly_low.or_else(|| self.rolling_week_low())),
            ("VAL", ctx.vp_val),
        ];
        join_near_levels(ctx.low, &checks)
    }

    fn open_trade(
        &mut self,
        candidate: SignalCandidate,
        ctx: &MtfSpotBarContext,
    ) -> Option<MtfSpotTrade> {
        let h1 = self.h1_current.as_ref();
        let h1_atr = ctx.h1_atr.unwrap_or_else(|| self.h1_atr());
        if h1_atr <= 0.0 {
            return None;
        }

        let entry = ctx.close;
        let stop = match candidate.direction {
            MtfSpotDirection::Short => {
                ctx.h1_high.or_else(|| h1.map(|c| c.high))? + ATR_MULT * h1_atr
            }
            MtfSpotDirection::Long => ctx.h1_low.or_else(|| h1.map(|c| c.low))? - ATR_MULT * h1_atr,
        };
        let risk = match candidate.direction {
            MtfSpotDirection::Short => stop - entry,
            MtfSpotDirection::Long => entry - stop,
        };
        if risk <= 0.0 {
            return None;
        }
        let stop_frac = risk / entry;
        if !(MIN_STOP_PCT..=MAX_STOP_PCT).contains(&stop_frac) {
            return None;
        }

        let target = match candidate.direction {
            MtfSpotDirection::Short => entry - TARGET_R * risk,
            MtfSpotDirection::Long => entry + TARGET_R * risk,
        };
        let fee_r = FEE_RT * entry / risk;
        let signal = MtfSpotSignal {
            symbol: self.symbol.clone(),
            ts_ms: ctx.ts_ms,
            direction: candidate.direction,
            strategy: candidate.direction.strategy().to_string(),
            sig: format!("{}:{}", candidate.direction.strategy(), candidate.level),
            entry,
            stop,
            target,
            stop_pct: (stop_frac * 100.0 * 1000.0).round() / 1000.0,
            session: candidate.session,
            level: candidate.level,
            wick_pct: candidate.wick_pct,
            obi_entry: ctx.obi10_mean,
            delta_entry: ctx.delta,
            cvd_slope_entry: ctx.cvd_slope,
        };

        self.active_trade = Some(ActiveTrade {
            signal: signal.clone(),
            entry,
            stop,
            target,
            risk,
            fee_r,
            bars_in_trade: 0,
            mfe_r: 0.0,
            mae_r: 0.0,
        });
        self.cvd_streak = 0;

        Some(MtfSpotTrade {
            signal,
            result_r: None,
            gross_r: None,
            fee_r: Some(round4(fee_r)),
            reason: None,
            exit_price: None,
            exit_ts_ms: None,
            duration_bars: None,
            mfe_r: None,
            mae_r: None,
            is_open: true,
        })
    }

    fn update_active_trade(
        &mut self,
        mut trade: ActiveTrade,
        ctx: &MtfSpotBarContext,
    ) -> TradeUpdate {
        trade.bars_in_trade += 1;
        match trade.signal.direction {
            MtfSpotDirection::Short => {
                trade.mfe_r = trade.mfe_r.max((trade.entry - ctx.low) / trade.risk);
                trade.mae_r = trade.mae_r.max((ctx.high - trade.entry) / trade.risk);

                if ctx.high >= trade.stop {
                    let stop = trade.stop;
                    return TradeUpdate::Closed(
                        self.close_trade(trade, -1.0, "stop", stop, ctx.ts_ms),
                    );
                }
                if ctx.low <= trade.target {
                    let target = trade.target;
                    return TradeUpdate::Closed(
                        self.close_trade(trade, TARGET_R, "target", target, ctx.ts_ms),
                    );
                }
                // CVD exit ELIMINADO en shorts (Paso 1 2026-06-18): cortaba ganadores
                // a ~1.3R. Solo stop/target/timeout. Ver docs/mtf/MTF_SPOT_EDGE_REALITY_Y_PLAN.md
                if trade.bars_in_trade >= FORWARD {
                    let gross = (trade.entry - ctx.close) / trade.risk;
                    return TradeUpdate::Closed(
                        self.close_trade(trade, gross, "timeout", ctx.close, ctx.ts_ms),
                    );
                }
            }
            MtfSpotDirection::Long => {
                trade.mfe_r = trade.mfe_r.max((ctx.high - trade.entry) / trade.risk);
                trade.mae_r = trade.mae_r.max((trade.entry - ctx.low) / trade.risk);

                if ctx.low <= trade.stop {
                    let stop = trade.stop;
                    return TradeUpdate::Closed(
                        self.close_trade(trade, -1.0, "stop", stop, ctx.ts_ms),
                    );
                }
                if ctx.high >= trade.target {
                    let target = trade.target;
                    return TradeUpdate::Closed(
                        self.close_trade(trade, TARGET_R, "target", target, ctx.ts_ms),
                    );
                }
                if trade.bars_in_trade >= FORWARD {
                    let gross = (ctx.close - trade.entry) / trade.risk;
                    return TradeUpdate::Closed(
                        self.close_trade(trade, gross, "timeout", ctx.close, ctx.ts_ms),
                    );
                }
                if ctx.cvd_slope.unwrap_or(0.0) < 0.0 {
                    self.cvd_streak += 1;
                } else {
                    self.cvd_streak = 0;
                }
                let curr_r = (ctx.close - trade.entry) / trade.risk;
                if self.cvd_streak >= CVD_FLIP_BARS
                    && ctx.obi10_mean < -OBI_FLIP_THR
                    && curr_r >= MIN_PROFIT_CVD
                {
                    let gross = (ctx.close - trade.entry) / trade.risk;
                    return TradeUpdate::Closed(
                        self.close_trade(trade, gross, "cvd_exit", ctx.close, ctx.ts_ms),
                    );
                }
            }
        }

        TradeUpdate::StillOpen(trade)
    }

    fn close_trade(
        &self,
        trade: ActiveTrade,
        gross_r: f64,
        reason: &str,
        exit_price: f64,
        exit_ts_ms: i64,
    ) -> MtfSpotTrade {
        let net_r = gross_r - trade.fee_r;
        MtfSpotTrade {
            signal: trade.signal,
            result_r: Some(round4(net_r)),
            gross_r: Some(round4(gross_r)),
            fee_r: Some(round4(trade.fee_r)),
            reason: Some(reason.to_string()),
            exit_price: Some(exit_price),
            exit_ts_ms: Some(exit_ts_ms),
            duration_bars: Some(trade.bars_in_trade),
            mfe_r: Some(round4(trade.mfe_r)),
            mae_r: Some(round4(trade.mae_r)),
            is_open: false,
        }
    }
}

struct SignalCandidate {
    direction: MtfSpotDirection,
    session: String,
    level: String,
    wick_pct: f64,
}

enum TradeUpdate {
    StillOpen(ActiveTrade),
    Closed(MtfSpotTrade),
}

fn short_session(ts_ms: i64) -> Option<String> {
    let hm = (ts_ms / 60_000) % 1440;
    if (7 * 60..12 * 60).contains(&hm) {
        Some("london".into()) // re-habilitada v5 — paridad con backtest Python
    } else if (12 * 60..16 * 60).contains(&hm) {
        Some("overlap".into())
    } else if (16 * 60..20 * 60).contains(&hm) {
        Some("ny".into())
    } else {
        None
    }
}

fn long_session(ts_ms: i64) -> Option<String> {
    let hm = (ts_ms / 60_000) % 1440;
    if (14 * 60..16 * 60).contains(&hm) {
        Some("overlap".into())
    } else if (16 * 60..20 * 60).contains(&hm) {
        Some("ny".into())
    } else {
        None
    }
}

fn rejection_short(ctx: &MtfSpotBarContext) -> Option<f64> {
    let range = ctx.high - ctx.low;
    if range <= 0.0 {
        return None;
    }
    let wick = ctx.high - ctx.open.max(ctx.close);
    let wick_pct = wick / range;
    (wick_pct > 0.30 && wick_pct < 0.85 && ctx.close <= ctx.open).then_some(wick_pct)
}

fn rejection_long(ctx: &MtfSpotBarContext) -> Option<f64> {
    let range = ctx.high - ctx.low;
    if range <= 0.0 {
        return None;
    }
    let wick = ctx.open.min(ctx.close) - ctx.low;
    let wick_pct = wick / range;
    (wick_pct > 0.30 && wick_pct < 0.85 && ctx.close >= ctx.open).then_some(wick_pct)
}

fn join_near_levels(price: f64, checks: &[(&'static str, Option<f64>)]) -> Option<String> {
    let mut levels = Vec::new();
    for (name, level) in checks {
        if let Some(level) = level.filter(|v| *v > 0.0) {
            if (price - level).abs() / level <= LEVEL_TOL {
                levels.push(*name);
            }
        }
    }
    (!levels.is_empty()).then(|| levels.join("+"))
}

fn round4(v: f64) -> f64 {
    (v * 10_000.0).round() / 10_000.0
}
