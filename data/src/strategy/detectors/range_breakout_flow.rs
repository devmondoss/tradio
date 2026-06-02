//! Range Breakout Flow — detector de continuación
//!
//! Hipótesis validada en backtest (30 días M1, n=914 señales):
//!   Un rango de consolidación (0.08–0.55% de precio, 15–60 barras M1) donde el CVD
//!   acumula presión en una dirección, seguido de un cierre fuera del rango con VR ≥ 2×,
//!   produce edge positivo en horizonte de 30–60 minutos.
//!
//! Dos configuraciones:
//!   Config A — SHORT breakdown:  CVD bajista + VR≥2 + cierre bajo rango → target 0.5%
//!   Config B — LONG breakout:    CVD alcista + VR≥2 + cierre sobre rango → target 0.4%
//!              (mejor contra-tendencia; régimen macro bajista da más edge en short squeeze)

use std::collections::VecDeque;
use serde::{Deserialize, Serialize};
use crate::session::TradingSession;

// ── Parámetros del detector ───────────────────────────────────────────────────

/// Ventanas de rango a probar (barras M1)
const RANGE_WINDOWS: &[usize] = &[15, 20, 30, 45, 60];

/// Rango mínimo como % del precio (evita ruido puro)
const RANGE_MIN_PCT: f64 = 0.08;
/// Rango máximo como % del precio (evita mercado trending)
const RANGE_MAX_PCT: f64 = 0.55;

/// VR mínimo en la barra de breakout
const BREAKOUT_VR_MIN: f64 = 2.0;

/// Ventana para calcular VR (volumen relativo)
const VR_WINDOW: usize = 50;

/// Ventana para EMA de régimen macro (480 barras M1 ≈ 8 horas)
const EMA_MACRO: usize = 480;

/// Sessions operativas
const fn is_operative(s: TradingSession) -> bool {
    matches!(
        s,
        TradingSession::London | TradingSession::LondonNyOverlap | TradingSession::NewYork
    )
}

// ── Tipos públicos ─────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum RbfDirection {
    Short,
    Long,
}

/// Régimen macro derivado de la EMA480
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum MacroRegime {
    Bull,          // precio > EMA480, slope positivo
    Bear,          // precio < EMA480, slope negativo
    BullPullback,  // precio > EMA480 pero slope negativo
    BearPullback,  // precio < EMA480 pero slope positivo
    Unknown,
}

/// Señal emitida por el detector
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RbfSignal {
    pub direction: RbfDirection,
    pub entry_price: f64,
    pub stop_price: f64,
    pub target_price: f64,
    pub rr: f64,
    pub range_high: f64,
    pub range_low: f64,
    pub range_pct: f64,
    pub range_bars: usize,
    pub cvd_in_range: f64,
    pub vr_at_breakout: f64,
    pub macro_regime: MacroRegime,
    pub session: TradingSession,
    pub timestamp_ms: i64,
    pub evidence: Vec<String>,
}

// ── Estado interno ─────────────────────────────────────────────────────────────

#[derive(Debug)]
#[allow(dead_code)]
struct BarSnapshot {
    high:   f64,
    low:    f64,
    close:  f64,
    volume: f64,
    delta:  f64,  // bar delta (buy_vol - sell_vol proxy)
}

pub struct RangeBreakoutState {
    /// Historial de barras M1 para detección de rangos
    history:  VecDeque<BarSnapshot>,
    /// Historial de volúmenes para VR rolling
    vol_hist: VecDeque<f64>,
    /// EMA macro (480 barras) — se actualiza iterativamente
    ema480:   f64,
    ema480_prev: f64,
    ema480_initialized: bool,
    bars_seen: usize,
    /// ts de la última señal para cooldown (60 barras = 60 min)
    last_signal_bar: usize,
}

impl RangeBreakoutState {
    pub fn new() -> Self {
        Self {
            history:  VecDeque::with_capacity(RANGE_WINDOWS.iter().copied().max().unwrap_or(60) + 5),
            vol_hist: VecDeque::with_capacity(VR_WINDOW + 5),
            ema480:   0.0,
            ema480_prev: 0.0,
            ema480_initialized: false,
            bars_seen: 0,
            last_signal_bar: 0,
        }
    }

    /// Llamar en cada cierre de barra M1 con los datos de la barra cerrada.
    /// `bar_delta` = delta de la barra (buy_vol - sell_vol); puede ser estimado.
    pub fn on_bar_close(
        &mut self,
        open: f64, high: f64, low: f64, close: f64,
        volume: f64, bar_delta: f64,
        session: TradingSession,
        timestamp_ms: i64,
        cfg: &RangeBreakoutConfig,
    ) -> Option<RbfSignal> {
        let _ = open; // unused but kept for API clarity

        self.bars_seen += 1;

        // Actualizar EMA480 iterativa
        let k = 2.0 / (EMA_MACRO as f64 + 1.0);
        if !self.ema480_initialized {
            self.ema480      = close;
            self.ema480_prev = close;
            self.ema480_initialized = true;
        } else {
            self.ema480_prev = self.ema480;
            self.ema480 = close * k + self.ema480 * (1.0 - k);
        }

        // Actualizar historial de volumen para VR
        self.vol_hist.push_back(volume);
        if self.vol_hist.len() > VR_WINDOW {
            self.vol_hist.pop_front();
        }

        // Guardar snapshot
        let max_history = *RANGE_WINDOWS.iter().max().unwrap_or(&60);
        self.history.push_back(BarSnapshot { high, low, close, volume, delta: bar_delta });
        if self.history.len() > max_history + 2 {
            self.history.pop_front();
        }

        // Necesitamos suficientes datos
        if self.bars_seen < VR_WINDOW + *RANGE_WINDOWS.iter().max().unwrap_or(&60) {
            return None;
        }

        // Cooldown: 60 barras entre señales
        if self.bars_seen - self.last_signal_bar < 60 {
            return None;
        }

        // Solo sesiones operativas
        if !is_operative(session) {
            return None;
        }

        if !cfg.enabled {
            return None;
        }

        // VR actual
        let mean_vol = if self.vol_hist.is_empty() { 1.0 }
            else { self.vol_hist.iter().sum::<f64>() / self.vol_hist.len() as f64 };
        let vr = if mean_vol > 0.0 { volume / mean_vol } else { 0.0 };

        if vr < BREAKOUT_VR_MIN {
            return None;
        }

        // Régimen macro
        let slope_positive = self.ema480 > self.ema480_prev;
        let above_ema      = close > self.ema480;
        let macro_regime   = match (above_ema, slope_positive) {
            (true,  true)  => MacroRegime::Bull,
            (false, false) => MacroRegime::Bear,
            (true,  false) => MacroRegime::BullPullback,
            (false, true)  => MacroRegime::BearPullback,
        };

        // Probar distintas ventanas de rango
        let hist_len = self.history.len();
        for &range_bars in RANGE_WINDOWS {
            if hist_len < range_bars + 1 {
                continue;
            }

            // El rango son las `range_bars` barras ANTERIORES a la actual
            let window_start = hist_len - range_bars - 1;
            let window_end   = hist_len - 1; // excluye la barra actual

            let window: Vec<&BarSnapshot> = self.history
                .iter()
                .skip(window_start)
                .take(range_bars)
                .collect();

            let range_high = window.iter().map(|b| b.high).fold(f64::NEG_INFINITY, f64::max);
            let range_low  = window.iter().map(|b| b.low).fold(f64::INFINITY,  f64::min);
            let range_size = range_high - range_low;
            let range_pct  = range_size / close * 100.0;

            if range_pct < RANGE_MIN_PCT || range_pct > RANGE_MAX_PCT {
                continue;
            }

            // CVD acumulado dentro del rango
            let cvd_in_range: f64 = window.iter().map(|b| b.delta).sum();

            // ¿La barra actual rompe el rango?
            let breaks_down = close < range_low;
            let breaks_up   = close > range_high;

            if !breaks_down && !breaks_up {
                continue;
            }

            let direction = if breaks_down { RbfDirection::Short } else { RbfDirection::Long };

            // CVD debe respaldar la dirección
            let cvd_aligned = match direction {
                RbfDirection::Short => cvd_in_range < 0.0,
                RbfDirection::Long  => cvd_in_range > 0.0,
            };

            if !cvd_aligned {
                continue;
            }

            // Calcular stop y target
            let stop_pct   = cfg.stop_pct / 100.0;
            let target_pct = match direction {
                RbfDirection::Short => cfg.target_short_pct / 100.0,
                RbfDirection::Long  => cfg.target_long_pct  / 100.0,
            };

            let (stop_price, target_price) = match direction {
                RbfDirection::Short => (
                    close * (1.0 + stop_pct),
                    close * (1.0 - target_pct),
                ),
                RbfDirection::Long => (
                    close * (1.0 - stop_pct),
                    close * (1.0 + target_pct),
                ),
            };

            let risk   = (close - stop_price).abs();
            let reward = (target_price - close).abs();
            let rr     = if risk > 1e-10 { reward / risk } else { 0.0 };

            if rr < cfg.min_rr {
                continue;
            }

            // Evidencia
            let regime_label = format!("{:?}", macro_regime);
            let mut evidence = vec![
                format!("rbf:{:?}", direction),
                format!("range_pct={:.3}%", range_pct),
                format!("range_bars={}", range_bars),
                format!("cvd_in_range={:.1}", cvd_in_range),
                format!("vr={:.2}x", vr),
                format!("macro={}", regime_label),
                format!("session={:?}", session),
            ];

            // Flag contra-tendencia (útil para análisis posterior)
            let counter_trend = match direction {
                RbfDirection::Short => matches!(macro_regime, MacroRegime::Bull | MacroRegime::BullPullback),
                RbfDirection::Long  => matches!(macro_regime, MacroRegime::Bear | MacroRegime::BearPullback),
            };
            if counter_trend {
                evidence.push("contra_tendencia".to_string());
            }

            self.last_signal_bar = self.bars_seen;
            let _ = window_end;

            return Some(RbfSignal {
                direction,
                entry_price: close,
                stop_price,
                target_price,
                rr,
                range_high,
                range_low,
                range_pct,
                range_bars,
                cvd_in_range,
                vr_at_breakout: vr,
                macro_regime,
                session,
                timestamp_ms,
                evidence,
            });
        }

        None
    }
}

// ── Configuración ─────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RangeBreakoutConfig {
    pub enabled:          bool,
    /// Stop en % del precio (default 0.25%)
    pub stop_pct:         f64,
    /// Target para SHORT en % del precio (default 0.5%)
    pub target_short_pct: f64,
    /// Target para LONG en % del precio (default 0.45%)
    pub target_long_pct:  f64,
    /// R:R mínimo para emitir señal
    pub min_rr:           f64,
}

impl Default for RangeBreakoutConfig {
    fn default() -> Self {
        Self {
            enabled:          true,
            stop_pct:         0.25,
            target_short_pct: 0.50,
            target_long_pct:  0.45,
            min_rr:           1.5,
        }
    }
}
