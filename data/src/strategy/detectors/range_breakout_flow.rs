//! Range Breakout Flow — detector de continuación con gate de microestructura
//!
//! Hipótesis validada en backtest (30 días M1, n=318 señales London+Overlap):
//!   Rango de consolidación (0.08–0.55%, 15–60 barras M1) + CVD acumulado alineado
//!   + VR ≥ 2× en breakout → edge positivo en horizonte 30–60 min.
//!
//! Gate de microestructura (backtest offline 30d):
//!   cvd_slope confirma dirección  (+11pp WR en Q4 vs Q1)
//!   dz entre 0.5 y 3.0            (extremos >3 revierten; pico en dz ~2)
//!   obi confirma dirección        (pendiente validación con datos reales)
//!
//! Sessions operativas — London (08-13 UTC) y Overlap (13-17 UTC).
//! NewYork excluido: backtest 30d/43200 barras → WR 24.7% avgR -0.065.

use std::collections::VecDeque;
use serde::{Deserialize, Serialize};
use crate::session::{TradingSession, SessionPhase};

// ── Tipos de confluencia ──────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ConfluenceFlag {
    CvdSlopeSostenido,
    ObiAlineado,
    StackedImbalance,
    AbsorcionFootprint,
    LvnOThinZone,
    VwapBias,
    OiMomentum,
    SessionCvdAligned,
    BigCvdAligned,
}

impl ConfluenceFlag {
    fn as_str(&self) -> &'static str {
        match self {
            Self::CvdSlopeSostenido  => "cvd_slope",
            Self::ObiAlineado        => "obi",
            Self::StackedImbalance   => "stacked_imbalance",
            Self::AbsorcionFootprint => "absorption",
            Self::LvnOThinZone       => "lvn_thin",
            Self::VwapBias           => "vwap_bias",
            Self::OiMomentum         => "oi_momentum",
            Self::SessionCvdAligned  => "session_cvd",
            Self::BigCvdAligned      => "big_cvd",
        }
    }
}

/// Datos de microestructura externos que no residen en RangeBreakoutState.
/// Los valores son crudos (no por-dirección) porque en main.rs aún no se
/// conoce la dirección del breakout cuando se construye este contexto.
pub struct RbfGateContext {
    /// Stacked imbalance bearish activo (Bearish/FBG-Bearish).
    pub stacked_imbalance_bearish: bool,
    /// Stacked imbalance bullish activo (Bullish/FBG-Bullish).
    pub stacked_imbalance_bullish: bool,
    /// Absorción ask activa (señal bajista — asks absorbidos).
    pub absorption_ask: bool,
    /// Absorción bid activa (señal alcista — bids absorbidos).
    pub absorption_bid: bool,
    /// LVN cerca del precio actual (proxy: al menos un nivel en lvn_nearby).
    pub lvn_nearby: bool,
    /// Thin zone por debajo del precio (favorece SHORT breakout).
    pub thin_zone_below: bool,
    /// Thin zone por encima del precio (favorece LONG breakout).
    pub thin_zone_above: bool,
    /// Pared de bids cerca — bloquea SHORT (target difícil de alcanzar).
    pub bid_wall_nearby: bool,
    /// Pared de asks cerca — bloquea LONG (target difícil de alcanzar).
    pub ask_wall_nearby: bool,
    /// HVN levels para verificar obstáculos entre entry y target.
    pub hvn_levels: Vec<f64>,
    /// VPIN de la barra actual.
    pub vpin: Option<f64>,
    /// OI momentum alineado: precio + OI expandiéndose en la misma dirección.
    pub oi_momentum_aligned: Option<bool>,
    /// CVD acumulado desde el inicio de la sesión (reset diario UTC).
    pub session_cvd: f64,
    /// CVD acumulado de big trades (≥$100k notional) desde apertura de sesión.
    /// Fabio: "las órdenes grandes son las que importan" — si el breakout viene con big_cvd alineado, es real.
    pub big_trade_cvd_session: f64,
    /// % de cambio en Open Interest en la ventana reciente (~6 lecturas).
    /// Positivo = contratos abiertos creciendo (nuevas posiciones = convicción).
    /// Negativo = posiciones cerrando (cobertura / liquidación = debilidad).
    pub oi_delta_pct: Option<f64>,
    /// Barras consecutivas de divergencia CVD-precio al momento de evaluar el breakout.
    /// Negativo = bullish div (útil para LONG); positivo = bearish div (útil para SHORT).
    pub cvd_divergence_bars: Option<i32>,
    /// Tendencia H4 al momento del breakout: Some("Bull") / Some("Bear") / None si aún sin warmup (< 240 barras M1).
    /// Deriva de EMA-240M1 (≈ 4H): precio > EMA → Bull.
    pub htf_h1_trend: Option<String>,
    /// VP Open Variant del día actual (FASE 2.1): "InsideValue", "OutsideVaInsidePa", "TrendDay", "FadeGap".
    /// Clasifica el tipo de apertura de sesión vs el Value Area del día anterior.
    pub vp_open_bias: Option<String>,
}

fn score_confluence(
    direction: RbfDirection,
    macro_regime: MacroRegime,
    cvd_slope: Option<f64>,
    obi: f64,
    vwap: Option<f64>,
    entry_price: f64,
    target_price: f64,
    gate: &RbfGateContext,
    cfg: &RangeBreakoutConfig,
) -> (u8, Vec<String>, Option<String>) {
    // ── VETOS: cancelan la señal independientemente del score ─────────────────
    // wall_target desactivado — 1×ATR es demasiado amplio, vetaba el 100% de señales.
    // Pendiente calibración con datos reales cuando haya 50+ señales con outcome.

    // HVN en el camino al target — solo veta si el HVN está ENTRE entry y target
    // (dirección correcta) y en la primera mitad del recorrido desde entry.
    let full_path = (target_price - entry_price).abs();
    let has_hvn_obstacle = gate.hvn_levels.iter().any(|&lvl| {
        let in_path = match direction {
            RbfDirection::Short => lvl < entry_price && lvl > target_price,
            RbfDirection::Long  => lvl > entry_price && lvl < target_price,
        };
        let dist_from_entry = (lvl - entry_price).abs();
        in_path && dist_from_entry < full_path * 0.5
    });
    if has_hvn_obstacle {
        return (0, vec![], Some("hvn_target".into()));
    }

    if gate.vpin.map_or(false, |v| v > 0.65) {
        return (0, vec![], Some("vpin_toxic".into()));
    }

    // ── PUNTOS ────────────────────────────────────────────────────────────────
    let mut score: u8 = 0;
    let mut flags: Vec<ConfluenceFlag> = vec![];

    if let Some(slope) = cvd_slope {
        let t = cfg.cvd_slope_threshold;
        let aligned = match direction {
            RbfDirection::Short => slope < -t,
            RbfDirection::Long  => slope >  t,
        };
        if aligned { score += 1; flags.push(ConfluenceFlag::CvdSlopeSostenido); }
    }

    let obi_aligned = match direction {
        RbfDirection::Short => obi < -cfg.obi_threshold,
        RbfDirection::Long  => obi >  cfg.obi_threshold,
    };
    if obi_aligned { score += 1; flags.push(ConfluenceFlag::ObiAlineado); }

    let stacked = match direction {
        RbfDirection::Short => gate.stacked_imbalance_bearish,
        RbfDirection::Long  => gate.stacked_imbalance_bullish,
    };
    if stacked { score += 1; flags.push(ConfluenceFlag::StackedImbalance); }

    let absorbed = match direction {
        RbfDirection::Short => gate.absorption_ask,
        RbfDirection::Long  => gate.absorption_bid,
    };
    if absorbed { score += 1; flags.push(ConfluenceFlag::AbsorcionFootprint); }

    let thin = match direction {
        RbfDirection::Short => gate.thin_zone_below,
        RbfDirection::Long  => gate.thin_zone_above,
    };
    if gate.lvn_nearby || thin {
        score += 1;
        flags.push(ConfluenceFlag::LvnOThinZone);
    }

    if let Some(v) = vwap.filter(|&v| v > 0.0) {
        let above = entry_price > v;
        let aligned = match direction {
            RbfDirection::Short => !above,
            RbfDirection::Long  =>  above,
        };
        if aligned { score += 1; flags.push(ConfluenceFlag::VwapBias); }
    }

    if gate.oi_momentum_aligned == Some(true) {
        score += 1;
        flags.push(ConfluenceFlag::OiMomentum);
    }

    // [+1] Session CVD alineado con dirección del breakout (Fabio: expansión de sesión).
    // session_cvd está en unidades de moneda; multiplicar por entry_price da USD.
    // Threshold ±$500k: sesión con >$500k net alineado con breakout = expansión real.
    let session_cvd_usd = gate.session_cvd * entry_price;
    let session_cvd_aligned = match direction {
        RbfDirection::Long  => session_cvd_usd >  500_000.0,
        RbfDirection::Short => session_cvd_usd < -500_000.0,
    };
    if session_cvd_aligned {
        score += 1;
        flags.push(ConfluenceFlag::SessionCvdAligned);
    }

    // [+1] Big trade CVD alineado: breakout respaldado por órdenes grandes (Fabio: "big trades filter").
    // big_trade_cvd_session en unidades de moneda → USD. Threshold $2M: acumulación significativa
    // de órdenes grandes (≥$100k c/u) en la dirección del breakout durante la sesión.
    let big_cvd_usd = gate.big_trade_cvd_session * entry_price;
    let big_cvd_aligned = match direction {
        RbfDirection::Long  => big_cvd_usd >  2_000_000.0,
        RbfDirection::Short => big_cvd_usd < -2_000_000.0,
    };
    if big_cvd_aligned {
        score += 1;
        flags.push(ConfluenceFlag::BigCvdAligned);
    }

    // Veto especial: Long contra tendencia bajista sin máxima confluencia
    if direction == RbfDirection::Long
        && matches!(macro_regime, MacroRegime::Bear | MacroRegime::BearPullback)
        && score < cfg.bear_long_min_score
    {
        let flags_str = flags.iter().map(|f| f.as_str().to_string()).collect();
        return (score, flags_str, Some("long_bear_low_score".into()));
    }

    let flags_str = flags.iter().map(|f| f.as_str().to_string()).collect();
    (score, flags_str, None)
}

// ── Parámetros del detector ───────────────────────────────────────────────────

const RANGE_WINDOWS:  &[usize] = &[15, 20, 30, 45, 60];
const RANGE_MIN_PCT:  f64 = 0.08;
const RANGE_MAX_PCT:  f64 = 0.55;
const BREAKOUT_VR_MIN: f64 = 2.0;
const VR_WINDOW:      usize = 50;
const DZ_WINDOW:      usize = 50;
const CVD_SLOPE_WIN:  usize = 20;
const EMA_MACRO:      usize = 480;

/// Sessions operativas — London, Overlap y NewYork.
/// Asia y OffHours excluidos (volumen insuficiente para RBF).
const fn is_operative(s: TradingSession) -> bool {
    matches!(s, TradingSession::London | TradingSession::LondonNyOverlap | TradingSession::NewYork)
}

// ── Tipos públicos ─────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum RbfDirection {
    Short,
    Long,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum MacroRegime {
    Bull,
    Bear,
    BullPullback,
    BearPullback,
    Unknown,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RbfSignal {
    pub direction:        RbfDirection,
    pub entry_price:      f64,
    pub stop_price:       f64,
    pub target_price:     f64,
    pub rr:               f64,
    pub range_high:       f64,
    pub range_low:        f64,
    pub range_pct:        f64,
    pub range_bars:       usize,
    pub cvd_in_range:     f64,
    pub vr_at_breakout:   f64,
    pub macro_regime:     MacroRegime,
    pub session:          TradingSession,
    pub timestamp_ms:     i64,
    pub evidence:         Vec<String>,

    // Contexto adicional
    pub range_touch_count:  usize,
    pub session_phase:      SessionPhase,
    pub price_vs_vwap_pct:  Option<f64>,
    pub funding_at_entry:   Option<f64>,
    pub liq_ratio_pre:      f64,

    // Microestructura en la barra de breakout
    /// CVD slope (USD/bar) en la barra de ruptura; positivo = compradores acumulando.
    pub cvd_slope_at_entry: Option<f64>,
    /// Delta z-score de la barra de ruptura (normalizado 50 barras).
    pub dz_at_entry:        f64,
    /// Order Book Imbalance en la barra de ruptura (bid-ask / total).
    pub obi_at_entry:       f64,

    // Microestructura de absorción (FASE 1.1)
    /// Intensidad de delta direccional normalizada (0–1). max(dz_dir,0)/3.
    /// 0 = sin presión alineada, 1 = dz≥3 en la dirección del breakout.
    pub absorption_score:   f64,
    /// Ratio cuerpo/rango de la vela de ruptura (0–1). 0 = pin bar, 1 = marubozu.
    /// Bajo valor = precio apenas se desplazó pese al delta → absorción.
    pub bar_displacement:   f64,

    // OI delta % en evento (FASE 1.2)
    /// Cambio porcentual de Open Interest en la ventana reciente al momento del breakout.
    /// Positivo = expansión (nueva convicción); negativo = cierre de posiciones.
    pub oi_delta_pct:       Option<f64>,

    // CVD divergencia explícita (FASE 1.3)
    /// Barras consecutivas de divergencia CVD-precio al momento del breakout.
    /// Positivo = bearish div (precio sube pero CVD no); negativo = bullish div (precio baja pero CVD sube).
    /// Valor absoluto alto = divergencia persistente = mayor convicción en el breakout contrario.
    pub cvd_divergence_bars: Option<i32>,

    // VR tier (FASE 2.2)
    /// Categoría de volumen relativo: 1=2-3×, 2=3-4×, 3=4×+.
    /// Tier 3 = breakout real con volumen extraordinario (análogos a eventos institucionales).
    pub vr_tier: u8,

    // Calidad del rango (FASE 2.3)
    /// Touches del lado opuesto al breakout vs del mismo lado (0–1).
    /// Cerca de 0.5 = rango simétrico (ambos lados tocados igualmente).
    /// Cerca de 1 o 0 = asimétrico (muchos rechazos en un solo lado).
    pub range_touch_symmetry: f64,
    /// CVD acumulado en rango / número de barras. Mide densidad de presión direccional.
    /// Valor absoluto alto = mucho CVD por barra = acumulación intensa.
    pub cvd_per_bar: f64,
    /// % de extensión del close más allá del rango (distancia desde el nivel roto).
    /// Ej: 0.02 = close está 0.02% más allá del range_high/low.
    pub breakout_extension_pct: f64,

    // HTF H4 estructura (FASE 2.6)
    /// Tendencia H4 al momento del breakout: "Bull" (precio > EMA-240M1), "Bear", o None si sin warmup.
    pub htf_h1_trend: Option<String>,
    // VP Open Variant (FASE 2.1)
    /// Variante del día: "InsideValue", "OutsideVaInsidePa", "TrendDay", "FadeGap", o None.
    pub vp_open_bias: Option<String>,
    /// True si la dirección del trade está alineada con la tendencia H4.
    /// Long+Bull o Short+Bear = alineado; contra-tendencia = false.
    pub htf_h1_aligned: Option<bool>,

    // Score continuo + sizing dinámico (FASE 4)
    /// Score continuo 0–1 ponderando absorción, VR tier, extensión, H4 alineamiento y confluencia.
    pub signal_score_v2: f64,
    /// Multiplicador sugerido: 0.5× (<0.3), 1.0× (0.3–0.5), 1.5× (0.5–0.7), 2.0× (≥0.7).
    pub sizing_multiplier: f64,

    // Confluencia v2
    /// Puntuación de confluencia (0–7). Señales con veto tienen score calculado antes del veto.
    pub confluence_score: u8,
    /// Flags activos al disparar (ej. ["cvd_slope", "obi", "vwap_bias"]).
    pub confluence_flags: Vec<String>,
    /// Razón de veto si la señal fue vetada (no se abre posición, sí se registra en DB).
    pub veto_reason: Option<String>,
}

// ── Estado interno ─────────────────────────────────────────────────────────────

#[derive(Debug)]
#[allow(dead_code)]
struct BarSnapshot {
    high:   f64,
    low:    f64,
    close:  f64,
    volume: f64,
    delta:  f64,
}

pub struct RangeBreakoutState {
    history:              VecDeque<BarSnapshot>,
    vol_hist:             VecDeque<f64>,
    /// Historial de bar_delta para calcular dz internamente.
    delta_hist:           VecDeque<f64>,
    /// Historial de CVD acumulado para calcular cvd_slope internamente (fallback).
    cvd_acc_hist:         VecDeque<f64>,
    cvd_running:          f64,
    ema480:               f64,
    ema480_prev:          f64,
    ema480_initialized:   bool,
    bars_seen:            usize,
    last_signal_bar:      usize,
}

impl RangeBreakoutState {
    pub fn new() -> Self {
        Self {
            history:            VecDeque::with_capacity(65),
            vol_hist:           VecDeque::with_capacity(VR_WINDOW + 5),
            delta_hist:         VecDeque::with_capacity(DZ_WINDOW + 5),
            cvd_acc_hist:       VecDeque::with_capacity(CVD_SLOPE_WIN + 5),
            cvd_running:        0.0,
            ema480:             0.0,
            ema480_prev:        0.0,
            ema480_initialized: false,
            bars_seen:          0,
            last_signal_bar:    0,
        }
    }

    /// Resetea el cooldown de señal tras warm-up para no bloquear la primera barra live.
    pub fn reset_signal_cooldown(&mut self) {
        self.last_signal_bar = 0;
    }

    /// OLS slope del CVD acumulado sobre las últimas `window` barras.
    fn compute_cvd_slope(&self) -> Option<f64> {
        let n = self.cvd_acc_hist.len();
        if n < 5 { return None; }
        let slice: Vec<f64> = self.cvd_acc_hist.iter().copied().collect();
        let nf = n as f64;
        let sum_x: f64 = (0..n).map(|i| i as f64).sum();
        let sum_y: f64 = slice.iter().sum();
        let sum_xy: f64 = slice.iter().enumerate().map(|(i, &y)| i as f64 * y).sum();
        let sum_x2: f64 = (0..n).map(|i| (i * i) as f64).sum();
        let denom = nf * sum_x2 - sum_x * sum_x;
        if denom.abs() < 1e-10 { return None; }
        Some((nf * sum_xy - sum_x * sum_y) / denom)
    }

    /// Delta z-score de la barra actual.
    fn compute_dz(&self, bar_delta: f64) -> f64 {
        let n = self.delta_hist.len();
        if n < 5 { return 0.0; }
        let mean = self.delta_hist.iter().sum::<f64>() / n as f64;
        let var  = self.delta_hist.iter().map(|&d| (d - mean).powi(2)).sum::<f64>() / n as f64;
        let std  = var.sqrt();
        if std < 1e-8 { return 0.0; }
        (bar_delta - mean) / std
    }

    /// Llamar en cada cierre de barra M1.
    pub fn on_bar_close(
        &mut self,
        open: f64, high: f64, low: f64, close: f64,
        volume: f64, bar_delta: f64,
        session: TradingSession,
        timestamp_ms: i64,
        cfg: &RangeBreakoutConfig,
        // Microestructura externa
        vwap:         Option<f64>,
        funding_rate: Option<f64>,
        liq_ratio:    f64,
        obi:          f64,
        cvd_slope_ext: Option<f64>, // slope computado externamente (preferido)
        // Contexto de confluencia v2 (opcional — si es None no se puntúa)
        gate: Option<&RbfGateContext>,
    ) -> Option<RbfSignal> {
        let _ = open;

        self.bars_seen += 1;

        // EMA480
        let k = 2.0 / (EMA_MACRO as f64 + 1.0);
        if !self.ema480_initialized {
            self.ema480      = close;
            self.ema480_prev = close;
            self.ema480_initialized = true;
        } else {
            self.ema480_prev = self.ema480;
            self.ema480 = close * k + self.ema480 * (1.0 - k);
        }

        // Historial de volumen (VR)
        self.vol_hist.push_back(volume);
        if self.vol_hist.len() > VR_WINDOW { self.vol_hist.pop_front(); }

        // Historial de delta (dz)
        self.delta_hist.push_back(bar_delta);
        if self.delta_hist.len() > DZ_WINDOW { self.delta_hist.pop_front(); }

        // Historial de CVD acumulado (slope fallback)
        self.cvd_running += bar_delta;
        self.cvd_acc_hist.push_back(self.cvd_running);
        if self.cvd_acc_hist.len() > CVD_SLOPE_WIN { self.cvd_acc_hist.pop_front(); }

        // Snapshot de la barra
        let max_history = *RANGE_WINDOWS.iter().max().unwrap_or(&60);
        self.history.push_back(BarSnapshot { high, low, close, volume, delta: bar_delta });
        if self.history.len() > max_history + 2 { self.history.pop_front(); }

        if self.bars_seen < VR_WINDOW + max_history { return None; }
        if self.bars_seen - self.last_signal_bar < 60 { return None; }
        if !is_operative(session) { return None; }
        if !cfg.enabled { return None; }

        // VR
        let mean_vol = if self.vol_hist.is_empty() { 1.0 }
            else { self.vol_hist.iter().sum::<f64>() / self.vol_hist.len() as f64 };
        let vr = if mean_vol > 0.0 { volume / mean_vol } else { 0.0 };
        if vr < BREAKOUT_VR_MIN { return None; }

        // Microestructura en esta barra
        let dz         = self.compute_dz(bar_delta);
        let cvd_slope  = cvd_slope_ext.or_else(|| self.compute_cvd_slope());

        // Fase / régimen macro
        let session_ctx  = crate::session::classify_session(timestamp_ms);
        let session_phase = session_ctx.phase;
        let slope_pos    = self.ema480 > self.ema480_prev;
        let above_ema    = close > self.ema480;
        let macro_regime = match (above_ema, slope_pos) {
            (true,  true)  => MacroRegime::Bull,
            (false, false) => MacroRegime::Bear,
            (true,  false) => MacroRegime::BullPullback,
            (false, true)  => MacroRegime::BearPullback,
        };

        let price_vs_vwap_pct = vwap
            .filter(|v| *v > 0.0)
            .map(|v| (close - v) / v * 100.0);

        let hist_len = self.history.len();
        for &range_bars in RANGE_WINDOWS {
            if hist_len < range_bars + 1 { continue; }

            let window_start = hist_len - range_bars - 1;
            let window: Vec<&BarSnapshot> = self.history
                .iter()
                .skip(window_start)
                .take(range_bars)
                .collect();

            let range_high = window.iter().map(|b| b.high).fold(f64::NEG_INFINITY, f64::max);
            let range_low  = window.iter().map(|b| b.low).fold(f64::INFINITY,  f64::min);
            let range_pct  = (range_high - range_low) / close * 100.0;

            if range_pct < RANGE_MIN_PCT || range_pct > RANGE_MAX_PCT { continue; }

            let cvd_in_range: f64 = window.iter().map(|b| b.delta).sum();

            let touches_high = window.iter().filter(|b| b.high >= range_high * 0.999 && b.close < range_high).count();
            let touches_low  = window.iter().filter(|b| b.low  <= range_low  * 1.001 && b.close > range_low).count();

            let breaks_down = close < range_low;
            let breaks_up   = close > range_high;
            if !breaks_down && !breaks_up { continue; }

            let direction = if breaks_down { RbfDirection::Short } else { RbfDirection::Long };
            let sign: f64 = if breaks_down { 1.0 } else { -1.0 }; // sign para "move in direction"

            // CVD acumulado en rango alineado con dirección
            let cvd_aligned = match direction {
                RbfDirection::Short => cvd_in_range < 0.0,
                RbfDirection::Long  => cvd_in_range > 0.0,
            };
            if !cvd_aligned { continue; }

            // ── Gate de microestructura ────────────────────────────────────────
            // cvd_slope alineado: SHORT quiere slope negativo, LONG positivo
            let cvd_slope_dir = cvd_slope.map(|s| sign * (-s));
            if cfg.cvd_slope_gate {
                if let Some(sd) = cvd_slope_dir {
                    if sd <= 0.0 { continue; }
                }
            }

            // dz alineado con la dirección
            let dz_dir = sign * (-dz); // SHORT quiere dz negativo (vendedores), LONG positivo
            if dz_dir < cfg.dz_min || dz_dir > cfg.dz_max { continue; }

            // OBI alineado: SHORT quiere obi < threshold, LONG quiere obi > (1 - threshold)
            if cfg.obi_gate {
                // obi ∈ [-1, +1]: SHORT quiere obi < -threshold (ask dominante), LONG quiere obi > threshold (bid dominante)
                let obi_ok = match direction {
                    RbfDirection::Short => obi < -cfg.obi_threshold,
                    RbfDirection::Long  => obi >  cfg.obi_threshold,
                };
                if !obi_ok { continue; }
            }

            // Calcular stop y target
            let stop_pct   = cfg.stop_pct / 100.0;
            let target_pct = match direction {
                RbfDirection::Short => cfg.target_short_pct / 100.0,
                RbfDirection::Long  => cfg.target_long_pct  / 100.0,
            };
            let (stop_price, target_price) = match direction {
                RbfDirection::Short => (close * (1.0 + stop_pct),   close * (1.0 - target_pct)),
                RbfDirection::Long  => (close * (1.0 - stop_pct),   close * (1.0 + target_pct)),
            };
            let risk   = (close - stop_price).abs();
            let reward = (target_price - close).abs();
            let rr     = if risk > 1e-10 { reward / risk } else { 0.0 };
            if rr < cfg.min_rr { continue; }

            // Evidencia
            let mut evidence = vec![
                format!("rbf:{:?}", direction),
                format!("range_pct={:.3}%", range_pct),
                format!("range_bars={}", range_bars),
                format!("cvd_in_range={:.1}", cvd_in_range),
                format!("vr={:.2}x", vr),
                format!("macro={:?}", macro_regime),
                format!("session={:?}", session),
            ];
            if let Some(sd) = cvd_slope_dir {
                evidence.push(format!("cvd_slope_dir={:.2}", sd));
            }
            evidence.push(format!("dz_dir={:.2}", dz_dir));
            if cfg.obi_gate {
                evidence.push(format!("obi={:.3}", obi));
            }

            let counter_trend = match direction {
                RbfDirection::Short => matches!(macro_regime, MacroRegime::Bull | MacroRegime::BullPullback),
                RbfDirection::Long  => matches!(macro_regime, MacroRegime::Bear | MacroRegime::BearPullback),
            };
            if counter_trend { evidence.push("contra_tendencia".to_string()); }

            let range_touch_count = if breaks_down { touches_low } else { touches_high };

            // ── VR tier (FASE 2.2) ───────────────────────────────────────────
            let vr_tier: u8 = if vr >= 4.0 { 3 } else if vr >= 3.0 { 2 } else { 1 };

            // ── Calidad del rango (FASE 2.3) ─────────────────────────────────
            // Simetría de toques: cuánto balance había entre ambos lados del rango
            let total_touches = (touches_high + touches_low) as f64;
            let range_touch_symmetry = if total_touches > 0.0 {
                let hi_ratio = touches_high as f64 / total_touches;
                // 0.5 = simétrico, 0 o 1 = todos los toques en un lado
                1.0 - (hi_ratio - 0.5).abs() * 2.0
            } else {
                0.5
            };
            // CVD acumulado por barra del rango
            let cvd_per_bar = if range_bars > 0 { cvd_in_range / range_bars as f64 } else { 0.0 };
            // Extensión del cierre más allá del rango roto (%)
            let breakout_extension_pct = match direction {
                RbfDirection::Short => (range_low - close) / close * 100.0,
                RbfDirection::Long  => (close - range_high) / close * 100.0,
            }.max(0.0);

            // ── Absorción (FASE 1.1) ──────────────────────────────────────────
            // Intensidad del delta direccional normalizada: max(dz_dir,0)/3 ∈ [0,1]
            let absorption_score = (dz_dir.max(0.0) / 3.0).min(1.0);
            // Ratio cuerpo/rango: low = pin bar / absorbed; high = engulfing candle
            let bar_displacement = if high > low {
                (close - open).abs() / (high - low)
            } else {
                0.5
            };

            // ── Confluencia v2 ────────────────────────────────────────────────
            let (confluence_score, confluence_flags, veto_reason) = if let Some(g) = gate {
                score_confluence(
                    direction, macro_regime,
                    cvd_slope, obi, vwap,
                    close, target_price,
                    g, cfg,
                )
            } else {
                (0, vec![], None)
            };

            if let Some(ref reason) = veto_reason {
                evidence.push(format!("veto={}", reason));
            } else {
                evidence.push(format!("confluence={}/{}", confluence_score, 8));
            }

            self.last_signal_bar = self.bars_seen;

            // ── Score continuo (FASE 4) ───────────────────────────────────────
            let h4_aligned = gate.and_then(|g| g.htf_h1_trend.as_ref().map(|t| {
                matches!((direction, t.as_str()), (RbfDirection::Long,"Bull")|(RbfDirection::Short,"Bear"))
            }));
            let signal_score_v2 = {
                let mut s = 0.0_f64;
                s += absorption_score * 0.25;
                s += (vr_tier as f64 - 1.0) / 2.0 * 0.20;
                s += breakout_extension_pct.min(0.10) / 0.10 * 0.15;
                s += if h4_aligned.unwrap_or(false) { 0.15 } else { 0.0 };
                s += (confluence_score as f64 / 9.0) * 0.25;
                s.min(1.0_f64)
            };
            let sizing_multiplier: f64 = if signal_score_v2 >= 0.70 { 2.0 }
                else if signal_score_v2 >= 0.50 { 1.5 }
                else if signal_score_v2 >= 0.30 { 1.0 }
                else { 0.5 };

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
                range_touch_count,
                session_phase,
                price_vs_vwap_pct,
                funding_at_entry:    funding_rate,
                liq_ratio_pre:       liq_ratio,
                cvd_slope_at_entry:  cvd_slope,
                dz_at_entry:         dz,
                obi_at_entry:        obi,
                absorption_score,
                bar_displacement,
                oi_delta_pct:        gate.map(|g| g.oi_delta_pct).flatten(),
                cvd_divergence_bars: gate.map(|g| g.cvd_divergence_bars).flatten(),
                vr_tier,
                range_touch_symmetry,
                cvd_per_bar,
                breakout_extension_pct,
                signal_score_v2,
                sizing_multiplier,
                htf_h1_trend: gate.and_then(|g| g.htf_h1_trend.clone()),
                htf_h1_aligned: h4_aligned,
                vp_open_bias: gate.and_then(|g| g.vp_open_bias.clone()),
                confluence_score,
                confluence_flags,
                veto_reason,
            });
        }

        None
    }
}

// ── Configuración ─────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RangeBreakoutConfig {
    pub enabled:           bool,
    pub stop_pct:          f64,
    pub target_short_pct:  f64,
    pub target_long_pct:   f64,
    pub min_rr:            f64,

    // Gates de microestructura (validados backtest 30d)
    /// Activar gate de CVD slope: slope debe confirmar dirección del breakout.
    pub cvd_slope_gate:    bool,
    /// dz mínimo alineado con dirección (0.5 = backtest óptimo, elimina breakouts planos).
    pub dz_min:            f64,
    /// dz máximo alineado con dirección (3.0 = cortar extremos que revierten).
    pub dz_max:            f64,
    /// Activar gate de OBI (requiere validación con datos reales; desactivado por defecto).
    pub obi_gate:          bool,
    /// Umbral OBI para el gate: SHORT quiere obi < threshold, LONG quiere obi > (1-threshold).
    pub obi_threshold:     f64,

    // Sistema de confluencia v2
    /// Score mínimo para abrir posición. 1 = shadow mode (grabar todo). 3+ = producción.
    pub min_confluence_score: u8,
    /// |cvd_slope| mínimo para sumar el flag CvdSlopeSostenido.
    pub cvd_slope_threshold: f64,
    /// Score mínimo requerido para Long en régimen Bear/BearPullback.
    pub bear_long_min_score: u8,
}

impl Default for RangeBreakoutConfig {
    fn default() -> Self {
        Self {
            enabled:          true,
            stop_pct:         0.25,
            target_short_pct: 0.50,
            target_long_pct:  0.45,
            min_rr:           1.5,
            // Microestructura — valores del backtest 30d
            cvd_slope_gate:   true,
            dz_min:           0.5,
            dz_max:           3.0,
            obi_gate:         false, // pendiente validación con datos reales
            obi_threshold:    0.45,
            // v2: shadow mode por defecto (grabar todo, sin filtro de score)
            min_confluence_score: 1,
            cvd_slope_threshold:  15.0,
            bear_long_min_score:  5,
        }
    }
}
