//! Paper trading engine para el motor de scalping.
//!
//! Opera a nivel de segundos (time-stop, OBI-reversal exit) en contraste con
//! el PaperAccount M5 del sistema DRR que opera en barras.

use serde::{Deserialize, Serialize};

use crate::strategy::types::Side;
use super::ScalpingSignal;

/// Fees maker→maker Binance con BNB (0.018% × 2 = 0.036%)
const MAKER_FEE_RT: f64 = 0.00036;
/// Slippage estimado (1bp × 2 lados)
const SLIPPAGE_RT: f64 = 0.0002;
/// Capital inicial del paper engine
const INITIAL_EQUITY: f64 = 100.0;
/// Riesgo máximo por trade como fracción del equity (1%)
const RISK_PCT_PER_TRADE: f64 = 0.01;
/// Apalancamiento del paper engine (10x)
const LEVERAGE: f64 = 10.0;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ScalpingExitReason {
    TP1,
    TP2,
    StopHit,
    TimeStop,
    ObiReversal,
    SpreadWidened,
    SessionEnd,
    DailyLossLimit,
}

impl std::fmt::Display for ScalpingExitReason {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::TP1 => write!(f, "TP1"),
            Self::TP2 => write!(f, "TP2"),
            Self::StopHit => write!(f, "STOP_HIT"),
            Self::TimeStop => write!(f, "TIME_STOP"),
            Self::ObiReversal => write!(f, "OBI_REVERSAL"),
            Self::SpreadWidened => write!(f, "SPREAD_WIDENED"),
            Self::SessionEnd => write!(f, "SESSION_END"),
            Self::DailyLossLimit => write!(f, "DAILY_LOSS_LIMIT"),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScalpingTrade {
    pub trade_id: String,
    pub strategy: String,
    pub session: String,
    pub side: Side,

    // Precios
    pub entry_price: f64,
    pub stop_price: f64,
    pub tp1_price: f64,
    pub tp2_price: f64,
    pub exit_price: f64,
    pub rr_planned: f64,

    // Tamaño de posición
    pub lot_btc: f64,
    pub lot_notional: f64,

    // Contexto en entrada
    pub obi_at_entry: f64,
    pub obi_l5_at_entry: f64,
    pub cvd_slope_at_entry: Option<f64>,
    pub dz_at_entry: f64,
    pub vr_at_entry: f64,
    pub cvd_at_entry: f64,
    pub conviction_score: f64,
    pub entry_type: String,
    pub spread_ticks: i32,

    // Resultado
    pub exit_reason: ScalpingExitReason,
    pub pnl_gross: f64,
    pub pnl_net: f64,
    pub result_r: f64,
    pub duration_ms: i64,

    // Timing
    pub entry_ms: i64,
    pub exit_ms: i64,

    // MFE / MAE
    pub mfe: f64,
    pub mae: f64,
}

/// Posición abierta activa
#[derive(Debug, Clone)]
struct ActivePosition {
    signal: ScalpingSignal,
    entry_ms: i64,
    obi_at_entry: f64,
    dz_at_entry: f64,
    vr_at_entry: f64,
    cvd_at_entry: f64,
    spread_ticks_at_entry: i32,
    mfe: f64,
    mae: f64,
    tp1_hit: bool,
    sl_moved_to_be: bool,
    /// Tamaño de posición en BTC (calculado en open por 1% risk + 10x leverage)
    lot_btc: f64,
    lot_notional: f64,
    /// Contexto adicional al momento de entrada (para journal)
    cvd_slope_at_entry: Option<f64>,
    obi_l5_at_entry: f64,
}

/// Estado del paper engine para la sesión scalping
pub struct ScalpingPaper {
    pub daily_pnl: f64,
    pub daily_trades: u32,
    pub consecutive_losses: u32,
    pub session_start_equity: f64,
    pub total_equity: f64,

    active: Option<ActivePosition>,
    pub closed_trades: Vec<ScalpingTrade>,

    trade_counter: u64,
}

impl ScalpingPaper {
    pub fn new() -> Self {
        Self {
            daily_pnl: 0.0,
            daily_trades: 0,
            consecutive_losses: 0,
            session_start_equity: INITIAL_EQUITY,
            total_equity: INITIAL_EQUITY,
            active: None,
            closed_trades: Vec::new(),
            trade_counter: 0,
        }
    }

    /// Calcula el tamaño de posición en BTC dado el equity actual.
    ///
    /// Regla: arriesgar RISK_PCT_PER_TRADE del equity por trade.
    /// Cap: no superar equity × LEVERAGE en notional (margen máximo = equity).
    pub fn position_size_btc(&self, entry_price: f64, stop_price: f64) -> (f64, f64) {
        let sl_dist = (entry_price - stop_price).abs().max(0.10);
        let risk_usd = self.total_equity * RISK_PCT_PER_TRADE;
        // Tamaño sin cap de leverage
        let lot_by_risk = risk_usd / sl_dist;
        // Cap: notional máximo = equity × leverage
        let max_notional = self.total_equity * LEVERAGE;
        let lot_by_margin = max_notional / entry_price.max(1.0);
        let lot_btc = lot_by_risk.min(lot_by_margin).max(1e-8);
        let lot_notional = lot_btc * entry_price;
        (lot_btc, lot_notional)
    }

    pub fn reset_daily(&mut self) {
        self.daily_pnl = 0.0;
        self.daily_trades = 0;
        self.consecutive_losses = 0;
        self.session_start_equity = self.total_equity;
    }

    pub fn has_position(&self) -> bool {
        self.active.is_some()
    }

    /// Circuit breaker: retorna false si no se puede operar.
    pub fn can_trade(
        &self,
        max_trades: u32,
        daily_loss_limit_pct: f64,
        max_consecutive_losses: u32,
    ) -> bool {
        if self.daily_trades >= max_trades {
            return false;
        }
        if self.daily_pnl / self.session_start_equity.max(1.0) <= -daily_loss_limit_pct {
            return false;
        }
        if self.consecutive_losses >= max_consecutive_losses {
            return false;
        }
        true
    }

    /// Abrir posición con la señal dada.
    pub fn open(
        &mut self,
        signal: ScalpingSignal,
        entry_ms: i64,
        obi: f64,
        obi_l5: f64,
        dz: f64,
        vr: f64,
        cvd: f64,
        cvd_slope: Option<f64>,
        spread_ticks: i32,
    ) {
        let (lot_btc, lot_notional) = self.position_size_btc(signal.entry_price, signal.stop_price);
        self.active = Some(ActivePosition {
            lot_btc,
            lot_notional,
            signal,
            entry_ms,
            obi_at_entry: obi,
            obi_l5_at_entry: obi_l5,
            cvd_slope_at_entry: cvd_slope,
            dz_at_entry: dz,
            vr_at_entry: vr,
            cvd_at_entry: cvd,
            spread_ticks_at_entry: spread_ticks,
            mfe: 0.0,
            mae: 0.0,
            tp1_hit: false,
            sl_moved_to_be: false,
        });
    }

    /// Tamaño en BTC de la posición activa (0 si no hay posición).
    pub fn active_lot_btc(&self) -> f64 {
        self.active.as_ref().map(|p| p.lot_btc).unwrap_or(0.0)
    }

    pub fn active_lot_notional(&self) -> f64 {
        self.active.as_ref().map(|p| p.lot_notional).unwrap_or(0.0)
    }

    /// Actualizar excursiones con el precio actual (llamar en on_trade).
    pub fn update_excursions(&mut self, price: f64) {
        let Some(pos) = &mut self.active else { return };
        let excursion = match pos.signal.side {
            Side::Long => price - pos.signal.entry_price,
            Side::Short => pos.signal.entry_price - price,
        };
        if excursion > pos.mfe {
            pos.mfe = excursion;
        }
        if -excursion > pos.mae {
            pos.mae = -excursion;
        }

        // Mover SL a BE después de TP1
        if !pos.tp1_hit && !pos.sl_moved_to_be {
            let reached_tp1 = match pos.signal.side {
                Side::Long => price >= pos.signal.tp1_price,
                Side::Short => price <= pos.signal.tp1_price,
            };
            if reached_tp1 {
                pos.tp1_hit = true;
                pos.sl_moved_to_be = true;
            }
        }
    }

    /// Evalúa si se debe cerrar la posición. Retorna Some(reason) si hay que cerrar.
    pub fn check_exit(
        &self,
        price: f64,
        obi_ema_fast: f64,
        spread_ticks: i32,
        now_ms: i64,
        time_stop_secs: u64,
    ) -> Option<ScalpingExitReason> {
        let pos = self.active.as_ref()?;
        let elapsed_secs = (now_ms - pos.entry_ms) / 1000;

        // Time-stop
        if elapsed_secs >= time_stop_secs as i64 {
            return Some(ScalpingExitReason::TimeStop);
        }

        // Spread ensanchado
        if spread_ticks > 3 {
            return Some(ScalpingExitReason::SpreadWidened);
        }

        let sl = if pos.sl_moved_to_be {
            pos.signal.entry_price
        } else {
            pos.signal.stop_price
        };

        match pos.signal.side {
            Side::Long => {
                if price <= sl {
                    return Some(ScalpingExitReason::StopHit);
                }
                if price >= pos.signal.tp2_price {
                    return Some(ScalpingExitReason::TP2);
                }
                if obi_ema_fast < 0.45 {
                    return Some(ScalpingExitReason::ObiReversal);
                }
            }
            Side::Short => {
                if price >= sl {
                    return Some(ScalpingExitReason::StopHit);
                }
                if price <= pos.signal.tp2_price {
                    return Some(ScalpingExitReason::TP2);
                }
                if obi_ema_fast > 0.55 {
                    return Some(ScalpingExitReason::ObiReversal);
                }
            }
        }

        None
    }

    /// Cerrar la posición activa y registrar el trade.
    pub fn close(&mut self, exit_price: f64, exit_ms: i64, reason: ScalpingExitReason) {
        let Some(pos) = self.active.take() else { return };

        let gross = match pos.signal.side {
            Side::Long => (exit_price - pos.signal.entry_price) * pos.lot_btc,
            Side::Short => (pos.signal.entry_price - exit_price) * pos.lot_btc,
        };
        let fees = pos.lot_notional * MAKER_FEE_RT;
        let slippage = pos.lot_notional * SLIPPAGE_RT;
        let net = gross - fees - slippage;

        // result_r: ganancia en múltiplos del riesgo planeado
        let risk_usd = pos.lot_btc * (pos.signal.entry_price - pos.signal.stop_price).abs().max(0.10);
        let result_r = if risk_usd > 0.0 { gross / risk_usd } else { 0.0 };

        self.daily_pnl += net;
        self.total_equity += net;
        self.daily_trades += 1;
        if net < 0.0 {
            self.consecutive_losses += 1;
        } else {
            self.consecutive_losses = 0;
        }

        self.trade_counter += 1;
        let trade = ScalpingTrade {
            trade_id: format!("SC_{}", self.trade_counter),
            strategy: pos.signal.strategy.to_string(),
            session: format!("{:?}", pos.signal.timestamp_ms),
            side: pos.signal.side,
            entry_price: pos.signal.entry_price,
            stop_price: pos.signal.stop_price,
            tp1_price: pos.signal.tp1_price,
            tp2_price: pos.signal.tp2_price,
            exit_price,
            rr_planned: pos.signal.rr,
            lot_btc: pos.lot_btc,
            lot_notional: pos.lot_notional,
            obi_at_entry: pos.obi_at_entry,
            obi_l5_at_entry: pos.obi_l5_at_entry,
            cvd_slope_at_entry: pos.cvd_slope_at_entry,
            dz_at_entry: pos.dz_at_entry,
            vr_at_entry: pos.vr_at_entry,
            cvd_at_entry: pos.cvd_at_entry,
            conviction_score: pos.signal.conviction_score,
            entry_type: pos.signal.entry_type.clone(),
            spread_ticks: pos.spread_ticks_at_entry,
            exit_reason: reason,
            pnl_gross: gross,
            pnl_net: net,
            result_r,
            duration_ms: exit_ms - pos.entry_ms,
            entry_ms: pos.entry_ms,
            exit_ms,
            mfe: pos.mfe,
            mae: pos.mae,
        };

        println!(
            "[scalping] {} {} side={:?} entry={:.1} exit={:.1} reason={} \
             pnl_net={:.4} result_r={:.2} duration={}s",
            trade.trade_id,
            trade.strategy,
            trade.side,
            trade.entry_price,
            trade.exit_price,
            trade.exit_reason,
            trade.pnl_net,
            trade.result_r,
            trade.duration_ms / 1000,
        );

        self.closed_trades.push(trade);
    }
}
