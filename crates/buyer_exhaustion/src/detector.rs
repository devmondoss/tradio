//! Detector de Buyer Exhaustion.
//!
//! Hipótesis: precio consolida en un rango con CVD neto POSITIVO (compradores
//! activos = "trampa"). En las últimas N barras del rango el CVD gira negativo
//! (sellers aparecen y absorben a los compradores). La barra de ruptura cierra
//! por debajo del rango con VR ≥ 2× y delta negativo → distribución completada.
//!
//! Diferencia clave con RBF:
//!   RBF: cvd_in_range < 0 (sellers acumulan → breakout con convicción)
//!   BE:  cvd_in_range > 0 (compradores atrapados → buyer exhaustion)
//!
//! Referencia: Axia Futures "Exhaustion Move", Bookmap "Absorption",
//!             Wyckoff UTAD (Upthrust After Distribution).

use std::collections::VecDeque;

use data::session::session_tracker::{TradingSession, classify_session};
use data::strategy::detectors::range_breakout_flow::RbfGateContext;

use crate::config::BuyerExhaustionConfig;
use crate::signal::BuyerExhaustionSignal;

// ── Microestructura gate (Layer 2) ────────────────────────────────────────────
//
// Score 0-6 para señales BE Short:
//   VETO  spread_bps > 5   → mercado ilíquido, fakeout probable
//   VETO  bid_wall_nearby  → soporte fuerte bloquea el target
//   +1    absorption_ask   → sellers absorbiendo compradores en el offer = distribución
//   +1    stacked_imb_bear → footprint con presión vendedora apilada
//   +1    thin_zone_below  → sin liquidez debajo = target alcanzable
//   +1    oi_long_exit     → oi_delta_pct < 0 → longs cerrando = agotamiento real
//   +1    obi_l10_sell     → obi_l10 < -0.05 → presión vendedora en book (10 niveles)
//   +1    obi_intrabar     → obi_mean_intrabar < -0.05 → presión sostenida (no spike)
fn score_be_confluence(gate: &RbfGateContext) -> (u8, Vec<String>) {
    if gate.spread_bps > 5.0 {
        return (0, vec!["VETO:spread".into()]);
    }
    if gate.bid_wall_nearby {
        return (0, vec!["VETO:bid_wall".into()]);
    }

    let mut score = 0u8;
    let mut flags: Vec<String> = Vec::new();

    if gate.absorption_ask {
        score += 1;
        flags.push("absorption_ask".into());
    }
    if gate.stacked_imbalance_bearish {
        score += 1;
        flags.push("stacked_imb_bear".into());
    }
    if gate.thin_zone_below {
        score += 1;
        flags.push("thin_zone_below".into());
    }
    if gate.oi_delta_pct.map_or(false, |v| v < 0.0) {
        score += 1;
        flags.push("oi_long_exit".into());
    }
    if gate.obi_l10 < -0.05 {
        score += 1;
        flags.push("obi_l10_sell".into());
    }
    if gate.obi_mean_intrabar.map_or(false, |v| v < -0.05) {
        score += 1;
        flags.push("obi_intrabar_sell".into());
    }

    (score, flags)
}

// ── Snapshot interno de cada barra ───────────────────────────────────────────

struct BarSnapshot {
    high:   f64,
    low:    f64,
    open:   f64,
    close:  f64,
    volume: f64,
    delta:  f64,
}

// ── Estado del detector ───────────────────────────────────────────────────────

pub struct BuyerExhaustionState {
    /// Historial de barras (incluye la barra actual tras el push).
    history:          VecDeque<BarSnapshot>,
    /// Historial de volumen para calcular el VR base.
    vol_hist:         VecDeque<f64>,
    bars_seen:        usize,
    last_signal_bar:  usize,
}

impl BuyerExhaustionState {
    pub fn new() -> Self {
        Self {
            history:         VecDeque::with_capacity(40),
            vol_hist:        VecDeque::with_capacity(60),
            bars_seen:       0,
            last_signal_bar: 0,
        }
    }

    /// Resetea el cooldown de señal tras warm-up para no bloquear la primera
    /// barra live (igual que RbfBreakoutState::reset_signal_cooldown).
    pub fn reset_signal_cooldown(&mut self) {
        self.last_signal_bar = 0;
    }

    /// Llamar en cada cierre de barra M1.
    ///
    /// # Parámetros
    /// - `bar_delta`: taker_buy_base − taker_sell_base de la barra
    /// - `symbol`: usado solo para logs
    /// - `gate`: contexto microestructural live (None en warm-up)
    pub fn on_bar_close(
        &mut self,
        high:         f64,
        low:          f64,
        open:         f64,
        close:        f64,
        volume:       f64,
        bar_delta:    f64,
        timestamp_ms: i64,
        symbol:       &str,
        cfg:          &BuyerExhaustionConfig,
        gate:         Option<&RbfGateContext>,
    ) -> Option<BuyerExhaustionSignal> {
        self.bars_seen += 1;

        let max_window = cfg.range_windows.iter().copied().max().unwrap_or(30);

        // Actualizar historial de volumen (base para VR)
        self.vol_hist.push_back(volume);
        if self.vol_hist.len() > cfg.vr_window + 5 {
            self.vol_hist.pop_front();
        }

        // Agregar barra actual al historial
        self.history.push_back(BarSnapshot { high, low, open, close, volume, delta: bar_delta });
        if self.history.len() > max_window + 5 {
            self.history.pop_front();
        }

        // Warm-up mínimo antes de empezar a detectar
        if self.bars_seen < cfg.vr_window + max_window {
            return None;
        }

        // Cooldown entre señales
        if self.bars_seen.saturating_sub(self.last_signal_bar) < cfg.signal_cooldown_bars {
            return None;
        }

        if !cfg.enabled {
            return None;
        }

        // Clasificación de sesión
        let session_ctx = classify_session(timestamp_ms);
        let session = session_ctx.session;
        if !is_session_enabled(session, &cfg.sessions_enabled) {
            return None;
        }

        // VR de la barra actual
        let avg_vol = if self.vol_hist.is_empty() {
            1.0
        } else {
            self.vol_hist.iter().sum::<f64>() / self.vol_hist.len() as f64
        };
        let vr = if avg_vol > 0.0 { volume / avg_vol } else { 0.0 };

        // Salida rápida: VR insuficiente para cualquier ventana
        if vr < cfg.breakout_vr_min {
            return None;
        }

        // Salida rápida: delta no negativo (si se requiere)
        if cfg.require_negative_breakout_delta && bar_delta >= 0.0 {
            return None;
        }

        // Micro-confirmación de la barra de ruptura
        let bar_range = high - low;
        if bar_range > 1e-10 {
            if let Some(cl_max) = cfg.close_location_max {
                // Dónde cerró la barra dentro de su rango: 0 = en el low, 1 = en el high
                let close_loc = (close - low) / bar_range;
                if close_loc > cl_max {
                    return None;
                }
            }
            if let Some(body_min) = cfg.bear_body_min {
                // Cuerpo bajista: open - close (positivo en barra bear)
                let bear_body = (open - close).max(0.0) / bar_range;
                if bear_body < body_min {
                    return None;
                }
            }
            if let Some(uw_max) = cfg.upper_wick_max {
                // Cola superior: desde el máximo hasta el max(open, close)
                let upper_wick = (high - open.max(close)) / bar_range;
                if upper_wick > uw_max {
                    return None;
                }
            }
        }

        let hist_len = self.history.len();

        // Scanear ventanas de rango (de menor a mayor para preferir el rango más reciente)
        for &win in &cfg.range_windows {
            // El historial incluye la barra actual en hist_len−1.
            // La ventana = las `win` barras ANTES de la actual:
            //   índices [hist_len−win−1 .. hist_len−2]
            if hist_len < win + 1 {
                continue;
            }

            let win_start = hist_len - win - 1;
            let window: Vec<&BarSnapshot> = self.history
                .iter()
                .skip(win_start)
                .take(win)
                .collect();

            // Dimensiones del rango
            let range_high = window.iter().map(|b| b.high).fold(f64::NEG_INFINITY, f64::max);
            let range_low  = window.iter().map(|b| b.low ).fold(f64::INFINITY,     f64::min);
            let range_pct  = (range_high - range_low) / close * 100.0;

            if range_pct < cfg.range_min_pct || range_pct > cfg.range_max_pct {
                continue;
            }

            // CVD del rango completo
            let range_cvd: f64 = window.iter().map(|b| b.delta).sum();
            if cfg.require_positive_range_cvd && range_cvd <= 0.0 {
                continue; // sin compradores atrapados = no es el patrón
            }

            // Giro de CVD en las últimas N barras del rango
            let pre_n = cfg.pre_cvd_bars.min(win);
            let pre_cvd: f64 = window.iter().rev().take(pre_n).map(|b| b.delta).sum();
            if pre_cvd >= 0.0 {
                continue; // los sellers aún no aparecieron
            }

            // El giro debe ser suficientemente significativo
            let flip_ratio = if range_cvd > 0.0 {
                pre_cvd.abs() / range_cvd
            } else {
                0.0
            };
            if flip_ratio < cfg.cvd_flip_min_ratio {
                continue;
            }

            // BREAKOUT: barra actual cerró POR DEBAJO del rango
            if close >= range_low {
                continue;
            }

            let range_height = range_high - range_low;

            // Extensión máxima: no entrar si el breakout ya recorrió >50% del rango
            // (entrada sucia = chasing; WR=19% avgR=-0.47R según backtest)
            let extension_pct = (range_low - close) / range_height;
            if extension_pct > 0.50 {
                continue;
            }

            // Stop y target
            let stop_price = {
                let base = if cfg.stop_at_range_high {
                    range_high
                } else {
                    (range_high + range_low) / 2.0
                };
                base.max(high) // incluye el high de la barra de entrada
            };

            let risk         = stop_price - close;   // Short: stop > entry
            if risk <= 1e-10 {
                continue;
            }

            let target_price = close - 2.0 * risk; // 2R fijo: reward siempre ≥ 1R
            let reward       = close - target_price;

            // El low de la barra de ruptura ya tocó/superó el target → el move pasó
            if low <= target_price {
                continue;
            }

            let rr = reward / risk;
            if rr < cfg.min_rr {
                continue;
            }

            // Microestructura gate (Layer 2)
            let (confluence_score, confluence_flags) = gate
                .map(|g| score_be_confluence(g))
                .unwrap_or((0, vec![]));

            // Min-score filter: 0 = VETO activo, dejar pasar 1+
            if gate.is_some() && confluence_score == 0 && !confluence_flags.is_empty() {
                // Solo vetamos si hay flags (VETO:spread / VETO:bid_wall), no si gate=None
                continue;
            }

            self.last_signal_bar = self.bars_seen;

            println!(
                "[be] {} | session={:?} entry={:.2} stop={:.2} target={:.2} \
                 rr={:.2} range={:.3}%×{}b cvd={:.1} pre={:.1} flip={:.2} vr={:.2}x delta={:.1} \
                 μscore={}/6 flags={:?}",
                symbol, session,
                close, stop_price, target_price, rr,
                range_pct, win, range_cvd, pre_cvd, flip_ratio, vr, bar_delta,
                confluence_score, confluence_flags
            );

            return Some(BuyerExhaustionSignal {
                timestamp_ms,
                session,
                symbol: symbol.to_string(),
                entry_price:   close,
                stop_price,
                target_price,
                rr,
                range_high,
                range_low,
                range_pct,
                range_bars:    win,
                range_cvd,
                pre_cvd_flip:  pre_cvd,
                cvd_flip_ratio: flip_ratio,
                vr_at_breakout: vr,
                breakout_delta: bar_delta,
                confluence_score,
                confluence_flags,
                supabase_id:   None,
            });
        }

        None
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

fn is_session_enabled(session: TradingSession, enabled: &[String]) -> bool {
    let name = match session {
        TradingSession::Asia            => "Asia",
        TradingSession::London          => "London",
        TradingSession::LondonNyOverlap => "LondonNyOverlap",
        TradingSession::NewYork         => "NewYork",
        TradingSession::OffHours        => "OffHours",
    };
    enabled.iter().any(|s| s == name)
}
