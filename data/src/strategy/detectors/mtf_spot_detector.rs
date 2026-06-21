use serde::{Deserialize, Serialize};
use std::collections::VecDeque;

const MIN_STOP_PCT: f64 = 0.0030;
const MAX_STOP_PCT: f64 = 0.0075;
const TARGET_R: f64 = 2.8; // barrido 2026-06-18: mejor IS/OOS balance
/// Fee round-trip por DEFECTO (Bybit perp/linear taker ≈ 0.055% × 2 = 0.0011).
/// Es propiedad de la VENUE, no de la estrategia — se puede sobre-escribir por venue
/// (Binance/WhiteBit/spot/futuros) vía `MtfSpotState::set_fee_rt`. El default preserva
/// la paridad con el backtest (fee 0.0011).
const DEFAULT_FEE_RT: f64 = 0.0011;
const FORWARD: usize = 240; // timeout 4h (mtf_system directions, 2026-06-19); la cola swing aporta poco
const COOLDOWN: i64 = 15;   // barras entre entradas de la misma dirección (anti-stack)
const LEVEL_TOL: f64 = 0.007;
const ATR_MULT: f64 = 0.40;
const H1_MS: i64 = 3_600_000;
const H4_MS: i64 = 14_400_000;
const D1_MS: i64 = 86_400_000;
const WEEK_ROLLING_BARS: usize = 5 * 24 * 60;
const D1_EMA_ALPHA: f64 = 2.0 / 21.0; // EMA20
const H1_EMA_ALPHA: f64 = 2.0 / 21.0; // EMA20 sobre H1
const D1_REGIME_THR: f64 = 0.980;       // close < d1_ema * 0.98 para shorts
const D1_REGIME_LONG_LO: f64 = 1.000;   // close >= d1_ema * 1.000 para longs
const D1_REGIME_LONG_HI: f64 = 1.030;   // close <= d1_ema * 1.030 para longs

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
    // v2 gates — opcionales para compatibilidad con live existente
    #[serde(default)]
    pub body_below_poc: bool,
    #[serde(default)]
    pub minus_ticks: f64,
    #[serde(default)]
    pub plus_ticks: f64,
    #[serde(default)]
    pub fp_absorb_buy: bool,
    #[serde(default)]
    pub fp_absorb_sell: bool,
    // sizing fields — opcionales, 0.0 si no disponible
    #[serde(default)]
    pub n_trades: f64,
    // ── directions / ICT union (mtf_system 2026-06-19) ──────────────────────
    // Disparadores ICT shorts (además de rejection@VAH):
    #[serde(default)]
    pub near_bearish_fvg: bool,
    #[serde(default)]
    pub near_bearish_ob: bool,
    #[serde(default)]
    pub displacement_bear: bool,
    #[serde(default)]
    pub sweep_confirmed: bool,
    // Veto shorts: sin LVN (void) por debajo = sin espacio limpio al target
    #[serde(default)]
    pub vp_lvn_below: bool,
    // Gate longs: cuerpo no por encima del POC
    #[serde(default)]
    pub vp_poc: Option<f64>,
    // Sizing vpin-aware (no afecta paridad: sizing_mult no se compara)
    #[serde(default)]
    pub vpin: f64,
    // Estructura H1/H4 precomputada (parquet). Si Some, se usa en vez del cálculo
    // interno del detector (que queda como fallback live). Igual patrón que h1_high.
    #[serde(default)]
    pub h1_bos_bear: Option<bool>,
    #[serde(default)]
    pub h1_choch_bear: Option<bool>,
    #[serde(default)]
    pub h1_bos_bull: Option<bool>,
    #[serde(default)]
    pub h4_bos_bear: Option<bool>,
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
            Self::Long => "mtf_directions_long",
            Self::Short => "mtf_directions_short",
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
    pub sizing_mult: f64,
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
    open: f64,
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
    fee_rt: f64, // venue-specific round-trip fee (default DEFAULT_FEE_RT)
    active_trade: Option<ActiveTrade>,
    bar_count: usize,

    // H1 tracking
    h1_current: Option<H1Candle>,
    h1_history: VecDeque<H1Candle>,  // completed H1 bars, max 20
    h1_ema20: Option<f64>,
    h1_uptrend_streak: usize,        // consecutive H1 bars con close > h1_ema20 (para ChoCH bear)
    h1_downtrend_streak: usize,      // consecutive H1 bars con close < h1_ema20 (para ChoCH bull)
    h1_bos_bars_ago: usize,          // BOS bajista: 0 = acaba de ocurrir, 999 = no hay reciente
    h1_choch_bars_ago: usize,        // ChoCH bajista
    h1_bos_bull_bars_ago: usize,     // BOS alcista: 0 = acaba de ocurrir, 999 = no hay reciente
    h1_choch_bull_bars_ago: usize,   // ChoCH alcista

    // H4 tracking (para gate NOT h4_bos_bear en longs)
    h4_current: Option<H1Candle>,    // reutiliza misma struct (OHLC + bucket)
    h4_history: VecDeque<H1Candle>,  // completadas, max 10
    h4_bos_bear_bars_ago: usize,     // BOS bajista H4: 0 = acaba de ocurrir, 999 = no hay

    // D1 EMA regime
    d1_ema20: Option<f64>,
    daily_close: f64,               // cierre del dia actual (se actualiza cada M1)

    // Sizing calibration — percentil 50 de n_trades / vpin en IS (Ene25–Feb26)
    // Actualizado por warm_up; cero hasta que tengamos suficientes datos
    n_trades_q50: f64,
    n_trades_samples: VecDeque<f64>,
    vpin_q50: f64,
    vpin_samples: VecDeque<f64>,

    // Cooldown por dirección (modelo directions): barra de la última entrada
    last_short_entry_bar: i64,
    last_long_entry_bar: i64,

    // Levels
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
            fee_rt: DEFAULT_FEE_RT,
            active_trade: None,
            bar_count: 0,
            h1_current: None,
            h1_history: VecDeque::with_capacity(20),
            h1_ema20: None,
            h1_uptrend_streak: 0,
            h1_downtrend_streak: 0,
            h1_bos_bars_ago: 999,
            h1_choch_bars_ago: 999,
            h1_bos_bull_bars_ago: 999,
            h1_choch_bull_bars_ago: 999,
            h4_current: None,
            h4_history: VecDeque::with_capacity(10),
            h4_bos_bear_bars_ago: 999,
            d1_ema20: None,
            daily_close: 0.0,
            n_trades_q50: 0.0,
            n_trades_samples: VecDeque::with_capacity(1024),
            vpin_q50: 0.0,
            vpin_samples: VecDeque::with_capacity(1024),
            last_short_entry_bar: i64::MIN / 2,
            last_long_entry_bar: i64::MIN / 2,
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

    /// Sobre-escribe el fee round-trip según la venue (Bybit/Binance/WhiteBit, spot/perp).
    /// La estrategia es agnóstica; el fee es config de venue. Default = DEFAULT_FEE_RT.
    pub fn set_fee_rt(&mut self, fee_rt: f64) {
        if fee_rt >= 0.0 {
            self.fee_rt = fee_rt;
        }
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
        let fee_r = self.fee_rt * entry / risk;
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
            sizing_mult: 1.0,  // restore no recalcula sizing
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
        if ctx.n_trades > 0.0 {
            self.n_trades_samples.push_back(ctx.n_trades);
            if self.n_trades_samples.len() % 1000 == 0 {
                self.n_trades_q50 = median_of(&self.n_trades_samples);
            }
        }
        if ctx.vpin > 0.0 {
            self.vpin_samples.push_back(ctx.vpin);
            if self.vpin_samples.len() % 1000 == 0 {
                self.vpin_q50 = median_of(&self.vpin_samples);
            }
        }
    }

    /// Devuelve los eventos generados en esta barra. Normalmente 0 o 1, pero en la
    /// barra que CIERRA un trade puede haber 2: [cierre, apertura] — replica el orden
    /// salida→entrada de mtf_system (re-entrada en la misma barra de cierre).
    pub fn on_bar_close(
        &mut self,
        ctx: &MtfSpotBarContext,
        allow_shorts: bool,
        allow_longs: bool,
    ) -> Vec<MtfSpotTrade> {
        self.bar_count += 1;
        self.update_levels(ctx);

        // Actualizar mediana n_trades / vpin en tiempo real (ventana deslizante, max 10k)
        if ctx.n_trades > 0.0 {
            if self.n_trades_samples.len() >= 10_000 {
                self.n_trades_samples.pop_front();
            }
            self.n_trades_samples.push_back(ctx.n_trades);
            if self.n_trades_q50 == 0.0 || self.n_trades_samples.len() % 500 == 0 {
                self.n_trades_q50 = median_of(&self.n_trades_samples);
            }
        }
        if ctx.vpin > 0.0 {
            if self.vpin_samples.len() >= 10_000 {
                self.vpin_samples.pop_front();
            }
            self.vpin_samples.push_back(ctx.vpin);
            if self.vpin_q50 == 0.0 || self.vpin_samples.len() % 500 == 0 {
                self.vpin_q50 = median_of(&self.vpin_samples);
            }
        }

        let mut events: Vec<MtfSpotTrade> = Vec::new();

        if let Some(trade) = self.active_trade.take() {
            match self.update_active_trade(trade, ctx) {
                TradeUpdate::StillOpen(t) => {
                    self.active_trade = Some(t);
                    return events; // sigue abierto: nada que emitir
                }
                TradeUpdate::Closed(t) => {
                    events.push(t); // cierre; intentamos re-entrar en ESTA misma barra
                }
            }
        }

        if self.symbol != "BTCUSDT" {
            return events;
        }

        let candidate = match (allow_shorts, allow_longs) {
            (true, true) => self.detect_short(ctx).or_else(|| self.detect_long(ctx)),
            (true, false) => self.detect_short(ctx),
            (false, true) => self.detect_long(ctx),
            (false, false) => None,
        };

        if let Some(candidate) = candidate {
            if let Some(open_ev) = self.open_trade(candidate, ctx) {
                events.push(open_ev);
            }
        }

        events
    }

    fn update_levels(&mut self, ctx: &MtfSpotBarContext) {
        self.update_h1(ctx);
        self.update_h4(ctx);
        self.update_daily_levels(ctx);
        self.daily_close = ctx.close;
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
                    open: ctx.open,
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
                        open: ctx.open,
                        high: ctx.high,
                        low: ctx.low,
                        close: ctx.close,
                    },
                );
                self.on_h1_complete(&completed);
                self.h1_history.push_back(completed);
                if self.h1_history.len() > 20 {
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

    fn on_h1_complete(&mut self, candle: &H1Candle) {
        // Actualizar H1 EMA20
        self.h1_ema20 = Some(match self.h1_ema20 {
            None => candle.close,
            Some(prev) => H1_EMA_ALPHA * candle.close + (1.0 - H1_EMA_ALPHA) * prev,
        });
        let ema = self.h1_ema20.unwrap();

        // Racha uptrend (para ChoCH bearish) y downtrend (para ChoCH bullish)
        if candle.close > ema {
            self.h1_uptrend_streak += 1;
            self.h1_downtrend_streak = 0;
        } else {
            self.h1_uptrend_streak = 0;
            self.h1_downtrend_streak += 1;
        }

        // Envejecer BOS y ChoCH (bear y bull)
        self.h1_bos_bars_ago       = self.h1_bos_bars_ago.saturating_add(1);
        self.h1_choch_bars_ago     = self.h1_choch_bars_ago.saturating_add(1);
        self.h1_bos_bull_bars_ago  = self.h1_bos_bull_bars_ago.saturating_add(1);
        self.h1_choch_bull_bars_ago = self.h1_choch_bull_bars_ago.saturating_add(1);

        let hist = &self.h1_history;
        let n = hist.len();

        // H1 BOS bajista: close rompe bajo el swing low de las 3 H1 anteriores
        if n >= 2 {
            let swing_lo = if n >= 3 {
                hist[n-1].low.min(hist[n-2].low).min(hist[n-3].low)
            } else {
                hist[n-1].low.min(hist[n-2].low)
            };
            if candle.close < swing_lo {
                self.h1_bos_bars_ago = 0;
            }
        }

        // H1 ChoCH bajista: primer Lower High tras uptrend (>= 3 barras alcistas previas)
        if n >= 1 && self.h1_uptrend_streak == 0 {
            let bullish_before = hist.iter().rev().take(3).filter(|c| c.close > ema).count();
            if bullish_before >= 3 && candle.high < hist[n-1].high && candle.close < hist[n-1].close {
                self.h1_choch_bars_ago = 0;
            }
        }

        // H1 BOS alcista: close rompe sobre el swing high de las 3 H1 anteriores
        if n >= 2 {
            let swing_hi = if n >= 3 {
                hist[n-1].high.max(hist[n-2].high).max(hist[n-3].high)
            } else {
                hist[n-1].high.max(hist[n-2].high)
            };
            if candle.close > swing_hi {
                self.h1_bos_bull_bars_ago = 0;
            }
        }

        // H1 ChoCH alcista: primer Higher Low tras downtrend (>= 3 barras bajistas previas)
        if n >= 1 && self.h1_downtrend_streak == 0 {
            let bearish_before = hist.iter().rev().take(3).filter(|c| c.close < ema).count();
            if bearish_before >= 3 && candle.low > hist[n-1].low && candle.close > hist[n-1].close {
                self.h1_choch_bull_bars_ago = 0;
            }
        }
    }

    fn update_h4(&mut self, ctx: &MtfSpotBarContext) {
        let bucket = ctx.ts_ms / H4_MS;
        match self.h4_current.as_mut() {
            None => {
                self.h4_current = Some(H1Candle {
                    bucket,
                    open: ctx.open,
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
                        open: ctx.open,
                        high: ctx.high,
                        low: ctx.low,
                        close: ctx.close,
                    },
                );
                self.on_h4_complete(&completed);
                self.h4_history.push_back(completed);
                if self.h4_history.len() > 10 {
                    self.h4_history.pop_front();
                }
            }
            Some(current) => {
                current.high = current.high.max(ctx.high);
                current.low = current.low.min(ctx.low);
                current.close = ctx.close;
            }
        }
    }

    fn on_h4_complete(&mut self, candle: &H1Candle) {
        self.h4_bos_bear_bars_ago = self.h4_bos_bear_bars_ago.saturating_add(1);

        let hist = &self.h4_history;
        let n = hist.len();
        if n >= 2 {
            let swing_lo = if n >= 3 {
                hist[n-1].low.min(hist[n-2].low).min(hist[n-3].low)
            } else {
                hist[n-1].low.min(hist[n-2].low)
            };
            if candle.close < swing_lo {
                self.h4_bos_bear_bars_ago = 0;
            }
        }
    }

    fn update_daily_levels(&mut self, ctx: &MtfSpotBarContext) {
        let day = ctx.ts_ms / D1_MS;
        let hm = (ctx.ts_ms / 60_000) % 1440;

        if day != self.current_day {
            // Cerrar el dia anterior y actualizar D1 EMA
            if self.current_day >= 0 && self.daily_high.is_finite() && self.daily_low.is_finite() {
                self.prev_day_high = Some(self.daily_high);
                self.prev_day_low = Some(self.daily_low);
                // daily_close es el ultimo close del dia anterior
                self.d1_ema20 = Some(match self.d1_ema20 {
                    None => self.daily_close,
                    Some(prev) => D1_EMA_ALPHA * self.daily_close + (1.0 - D1_EMA_ALPHA) * prev,
                });
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

    fn h1_bos_bear_active(&self) -> bool {
        self.h1_bos_bars_ago <= 2
    }

    fn h1_choch_bear_active(&self) -> bool {
        self.h1_choch_bars_ago <= 2
    }

    fn h1_bos_bull_active(&self) -> bool {
        self.h1_bos_bull_bars_ago <= 2
    }

    fn h4_bos_bear_active(&self) -> bool {
        self.h4_bos_bear_bars_ago <= 2
    }

    fn d1_regime_short_ok(&self, ctx: &MtfSpotBarContext) -> bool {
        match self.d1_ema20 {
            None => false,
            Some(ema) => ctx.close <= ema * D1_REGIME_THR,
        }
    }

    fn d1_regime_long_ok(&self, ctx: &MtfSpotBarContext) -> bool {
        match self.d1_ema20 {
            None => false,
            Some(ema) => {
                let ratio = ctx.close / ema;
                ratio >= D1_REGIME_LONG_LO && ratio <= D1_REGIME_LONG_HI
            }
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
        // Cooldown: misma dirección no re-entra dentro de COOLDOWN barras de la última
        if (self.bar_count as i64) - self.last_short_entry_bar < COOLDOWN {
            return None;
        }

        // Gate 0: Régimen D1 EMA (close <= ema * 0.98)
        if !self.d1_regime_short_ok(ctx) {
            return None;
        }

        // Gate 0b: Estructura H1 bajista (BOS o ChoCH). Preferir parquet si viene en ctx.
        let bos_bear = ctx.h1_bos_bear.unwrap_or_else(|| self.h1_bos_bear_active());
        let choch_bear = ctx.h1_choch_bear.unwrap_or_else(|| self.h1_choch_bear_active());
        if !bos_bear && !choch_bear {
            return None;
        }

        let session = short_session(ctx.ts_ms)?;

        // ── DISPARADOR: unión ICT validada (mtf_system 2026-06-19). El "level"
        // del signal pasa a ser el NOMBRE del disparador (no la composición de niveles).
        // Prioridad por orden; OTE y EqualHighSweep EXCLUIDOS (flip OOS).
        let (trigger, wick_pct): (&'static str, f64) = {
            let rej = rejection_short(ctx);
            let at_vah = self
                .short_level(ctx)
                .map(|l| l.split('+').any(|p| p == "VAH"))
                .unwrap_or(false);
            if at_vah && rej.is_some() {
                ("rejection_VAH", rej.unwrap())
            } else if ctx.near_bearish_ob && rej.is_some() {
                ("OrderBlock", rej.unwrap())
            } else if ctx.near_bearish_fvg && rej.is_some() {
                ("FVG", rej.unwrap())
            } else if ctx.displacement_bear {
                ("Displacement", 0.0)
            } else if ctx.sweep_confirmed {
                ("LiquiditySweep", 0.0)
            } else {
                return None;
            }
        };

        // Confirmación orderflow constante. Fallback baseline solo si el pipeline live
        // aún no envía v2 data (parity siempre la trae → fallback nunca se activa).
        let has_v2_data = ctx.minus_ticks > 0.0 || ctx.plus_ticks > 0.0;
        if has_v2_data {
            // Gate: body_below_poc
            if !ctx.body_below_poc {
                return None;
            }
            // Gate: agresión (minus_ticks > plus_ticks)
            if ctx.minus_ticks <= ctx.plus_ticks {
                return None;
            }
            // Veto: absorción compradora
            if ctx.fp_absorb_buy {
                return None;
            }
            // Veto: sin LVN (void) por debajo del nivel = sin espacio limpio al target
            if !ctx.vp_lvn_below {
                return None;
            }
        } else {
            // Fallback baseline mientras el pipeline live no envíe v2 data
            if !(ctx.obi10_mean < -0.05 || ctx.delta < 0.0) {
                return None;
            }
        }

        Some(SignalCandidate {
            direction: MtfSpotDirection::Short,
            session,
            level: trigger.to_string(),
            wick_pct,
        })
    }

    fn detect_long(&self, ctx: &MtfSpotBarContext) -> Option<SignalCandidate> {
        // Cooldown por dirección
        if (self.bar_count as i64) - self.last_long_entry_bar < COOLDOWN {
            return None;
        }

        // Gate 0: Régimen D1 EMA — precio entre 1.000x-1.030x EMA (no crash, no burbuja)
        if !self.d1_regime_long_ok(ctx) {
            return None;
        }

        // Gate 0b: H4 no bajista — H4 BOS bearish NO activo. Preferir parquet si viene.
        if ctx.h4_bos_bear.unwrap_or_else(|| self.h4_bos_bear_active()) {
            return None;
        }

        // Gate 0c: Estructura H1 alcista — BOS bullish activo. Preferir parquet si viene.
        if !ctx.h1_bos_bull.unwrap_or_else(|| self.h1_bos_bull_active()) {
            return None;
        }

        let session = long_session(ctx.ts_ms)?;

        // Gate 1: Nivel de soporte — cualquiera de VAL/AL/PDL/WL (no solo VAL).
        let level = self.long_level(ctx)?;
        let parts: Vec<&str> = level.split('+').collect();
        if !parts.iter().any(|p| matches!(*p, "VAL" | "AL" | "PDL" | "WL")) {
            return None;
        }
        // Triple PDL+AL+otros → demasiado ruido (mismo filtro que shorts)
        if parts.len() >= 3 && parts.contains(&"PDL") && parts.contains(&"AL") {
            return None;
        }

        // Gate 2: Rechazo wick alcista en M1
        let wick_pct = rejection_long(ctx)?;

        // Gate 2a: cuerpo no por encima del POC (precio aún en zona de soporte)
        if let Some(poc) = ctx.vp_poc.filter(|v| *v > 0.0) {
            if ctx.open.min(ctx.close) > poc {
                return None;
            }
        }

        let has_v2_data = ctx.minus_ticks > 0.0 || ctx.plus_ticks > 0.0;
        if has_v2_data {
            // Gate 2b: agresión compradora (plus_ticks > minus_ticks)
            if ctx.plus_ticks <= ctx.minus_ticks {
                return None;
            }
            // Veto: absorción vendedora
            if ctx.fp_absorb_sell {
                return None;
            }
        } else {
            // Fallback baseline mientras el pipeline live no envíe v2 data
            if !(ctx.obi10_mean > 0.05 || ctx.delta > 0.0) {
                return None;
            }
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
        let fee_r = self.fee_rt * entry / risk;

        // ── Sizing vpin-aware (mtf_system 2026-06-19) ────────────────────────
        // `tape & vpin_hi` = flujo tóxico con volumen = firma institucional robusta OOS.
        // vpin es ORTOGONAL a tape (corr=0.12). NO afecta paridad (sizing_mult no se compara).
        //   Short: cvd>0 (absorción) / obi>=0 (bids pesadas = bull trap en VAH).
        //   Long:  cvd>0 (compradores defendiendo) / obi>0 (bids pesadas en VAL). Tape-solo NO sube.
        let cvd      = ctx.cvd_slope.unwrap_or(0.0);
        let obi      = ctx.obi10_mean;
        let tape     = ctx.n_trades > 0.0 && ctx.n_trades >= self.n_trades_q50;
        let vpin_hi  = ctx.vpin > 0.0 && ctx.vpin >= self.vpin_q50;
        let cvd_ok   = cvd > 0.0;
        let sizing_mult = if tape && vpin_hi && cvd_ok {
            2.5
        } else if tape && vpin_hi {
            2.0
        } else {
            let third = match candidate.direction {
                MtfSpotDirection::Short => cvd_ok || tape || obi >= 0.0,
                MtfSpotDirection::Long => cvd_ok || obi > 0.0,
            };
            if third { 1.5 } else { 1.0 }
        };

        match candidate.direction {
            MtfSpotDirection::Short => self.last_short_entry_bar = self.bar_count as i64,
            MtfSpotDirection::Long => self.last_long_entry_bar = self.bar_count as i64,
        }

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
            sizing_mult,
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
                // mtf_system: longs salen solo por target/stop/timeout (sin CVD exit)
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
        Some("london".into())
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
    if (12 * 60..16 * 60).contains(&hm) {
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

/// Mediana (elemento len/2 del orden ascendente, igual que el q50 de Python con
/// índice s[len/2]). Copia a un Vec contiguo y ordena — O(n log n), no O(n²).
fn median_of(samples: &VecDeque<f64>) -> f64 {
    if samples.is_empty() {
        return 0.0;
    }
    let mut s: Vec<f64> = samples.iter().copied().collect();
    s.sort_by(|a, b| a.partial_cmp(b).unwrap());
    s[s.len() / 2]
}
