pub mod absorption;
pub mod cvd_divergence;
pub mod obi_maker;
pub mod paper;

use std::collections::VecDeque;
use serde::{Deserialize, Serialize};

use crate::detectors::range_detector::RangeContext;
use crate::session::TradingSession;
use crate::strategy::types::{FootprintLevel, Side};

// ── IDs de estrategia scalping ────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ScalpingStrategyId {
    /// S1: OBI Micro-Price Maker Reversion (market-making sesgado)
    ObiMaker,
    /// S2: Absorción / Trapped Traders Reversal (delta fuerte no acepta extremo)
    Absorption,
    /// S3: CVD Divergence Fade (precio nuevo swing high/low sin confirmar CVD)
    CvdDivergence,
}

impl std::fmt::Display for ScalpingStrategyId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::ObiMaker => write!(f, "S1_OBI"),
            Self::Absorption => write!(f, "S2_ABSORPTION"),
            Self::CvdDivergence => write!(f, "S3_DIVERGENCE"),
        }
    }
}

// ── Régimen scalping ──────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
pub enum ScalpingRegime {
    Range,
    #[default]
    Trend,
}

// ── Señal emitida por cualquier detector scalping ─────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScalpingSignal {
    pub strategy: ScalpingStrategyId,
    pub side: Side,
    pub entry_price: f64,
    pub stop_price: f64,
    pub tp1_price: f64,
    pub tp2_price: f64,
    pub rr: f64,
    pub conviction_score: f64,
    /// "aggressive" | "conservative" | "reclaim" | "divergence"
    pub entry_type: String,
    pub timestamp_ms: i64,
    pub evidence: Vec<String>,
}

// ── Contexto de mercado construido en cada bar close para los detectores ──────

#[derive(Debug, Clone)]
pub struct ScalpingContext {
    // ── Libro / OBI ───────────────────────────────────────────────────────────
    /// Order Book Imbalance en los 5 mejores niveles: (bid_sum - ask_sum) / total
    pub obi: f64,
    /// EMA rápida del OBI (~10 eventos). Actualizada en on_depth().
    pub obi_ema_fast: f64,
    /// EMA lenta del OBI (~60 eventos). Actualizada en on_depth().
    pub obi_ema_slow: f64,
    /// Micro-precio: best_ask × OBI_l1 + best_bid × (1 - OBI_l1)
    pub micro_price: f64,
    /// Spread en ticks (tick size = $0.10)
    pub spread_ticks: i32,

    // ── Delta / CVD / Volumen ─────────────────────────────────────────────────
    /// CVD acumulado desde apertura de sesión
    pub cvd: f64,
    /// Slope del CVD — regresión OLS sobre las últimas 20 barras (USD/bar).
    /// Positivo = CVD subiendo (compradores acumulando), Negativo = vendedores.
    pub cvd_slope: Option<f64>,
    /// Delta Z-score de la barra actual (normalizado por ventana de 50 barras)
    pub dz: f64,
    /// Volume Ratio: volumen de la barra / SMA(volumen, 50)
    pub vr: f64,
    /// Delta de la barra actual (buy_vol - sell_vol)
    pub bar_delta: f64,

    // ── OHLC de la barra ──────────────────────────────────────────────────────
    pub bar_open: f64,
    pub bar_high: f64,
    pub bar_low: f64,
    pub bar_close: f64,

    // ── Mercado / Contexto ────────────────────────────────────────────────────
    pub price: f64,
    pub atr: f64,
    pub regime: ScalpingRegime,
    pub session: TradingSession,
    pub vwap: Option<f64>,
    pub poc: Option<f64>,
    pub funding_rate: Option<f64>,
    /// Ratio de liquidaciones: liq_volume_reciente / SMA(liq_volume)
    pub liq_ratio: f64,
    pub timestamp_ms: i64,

    // ── Rango intradía (reutilizamos RangeDetector existente) ─────────────────
    pub range: Option<RangeContext>,

    // ── Historial para S3 (detección de swings CVD/precio) ───────────────────
    /// Últimas `s3_lookback_bars` entradas de CVD de cierre de vela
    pub cvd_history: Vec<f64>,
    /// Máximos de las últimas `s3_lookback_bars` velas
    pub price_highs: Vec<f64>,
    /// Mínimos de las últimas `s3_lookback_bars` velas
    pub price_lows: Vec<f64>,

    // ── Footprint para S2 ────────────────────────────────────────────────────
    pub footprint_levels: Vec<FootprintLevel>,
    pub big_trade_bullish: bool,
    pub big_trade_bearish: bool,
    /// POC de la vela actual (nivel con mayor volumen del footprint)
    pub candle_poc: Option<f64>,
}

// ── Estado persistente del motor scalping (vive en BarState del monitor) ──────

pub struct ScalpingState {
    /// EMA rápida del OBI (alpha ≈ 0.10, ~10 eventos)
    pub obi_ema_fast: f64,
    /// EMA lenta del OBI (alpha ≈ 0.017, ~60 eventos)
    pub obi_ema_slow: f64,

    /// CVD acumulado de la sesión = cvd_raw - cvd_baseline_at_session_open
    pub cvd_session: f64,
    /// Valor del CVD (del indicador externo) al abrir la sesión actual
    cvd_session_baseline: f64,
    /// Sesión anterior para detectar cambio de sesión
    pub last_session: TradingSession,

    /// CVD acumulado solo de big trades (≥$100k notional) desde apertura de sesión.
    /// Fabio: "las órdenes grandes son las que importan".
    pub big_cvd_session: f64,
    /// Volumen total de sesión en USD. Fabio: milestones 1B/2B/3B.
    pub session_vol_usd: f64,
    /// Último milestone cruzado: 0=ninguno, 1=1B, 2=2B, 3=3B.
    pub session_vol_milestone: u32,

    /// Historial de CVD por barra (para S3 divergencia de swings)
    pub cvd_bar_history: VecDeque<f64>,
    /// Historial de máximos por barra (para S3)
    pub high_bar_history: VecDeque<f64>,
    /// Historial de mínimos por barra (para S3)
    pub low_bar_history: VecDeque<f64>,

    /// Historial de delta por barra (para DZ)
    pub delta_bar_history: VecDeque<f64>,
    /// Historial de volumen por barra (para VR)
    pub volume_bar_history: VecDeque<f64>,

    /// Señal activa en curso (None = sin posición)
    pub active_signal: Option<ScalpingSignal>,
    /// Timestamp de entrada a la posición activa
    pub signal_entry_ms: Option<i64>,

    /// Resultado del paper engine para la sesión actual
    pub paper: paper::ScalpingPaper,

    /// Número máximo de barras a retener en historial
    lookback: usize,
}

impl ScalpingState {
    pub fn new(lookback: usize) -> Self {
        Self {
            obi_ema_fast: 0.5,
            obi_ema_slow: 0.5,
            cvd_session: 0.0,
            cvd_session_baseline: 0.0,
            last_session: TradingSession::OffHours,
            big_cvd_session: 0.0,
            session_vol_usd: 0.0,
            session_vol_milestone: 0,
            cvd_bar_history: VecDeque::with_capacity(lookback + 5),
            high_bar_history: VecDeque::with_capacity(lookback + 5),
            low_bar_history: VecDeque::with_capacity(lookback + 5),
            delta_bar_history: VecDeque::with_capacity(55),
            volume_bar_history: VecDeque::with_capacity(55),
            active_signal: None,
            signal_entry_ms: None,
            paper: paper::ScalpingPaper::new(),
            lookback,
        }
    }

    /// Llamar en cada depth update (O(1)) para mantener las EMAs del OBI actualizadas.
    ///
    /// `obi_raw` ∈ [-1, +1] (bid-ask)/(bid+ask). Se normaliza a [0, 1] antes de la EMA
    /// para que 0.5 = balanceado, >0.5 = presión compradora, <0.5 = presión vendedora.
    /// Los thresholds de config y la inicialización de las EMAs (0.5) usan este rango.
    pub fn on_depth(&mut self, obi_raw: f64) {
        let obi = (obi_raw + 1.0) / 2.0; // [-1,+1] → [0,1]
        let alpha_fast = 0.10;
        let alpha_slow = 0.017;
        self.obi_ema_fast = self.obi_ema_fast + alpha_fast * (obi - self.obi_ema_fast);
        self.obi_ema_slow = self.obi_ema_slow + alpha_slow * (obi - self.obi_ema_slow);
    }

    /// Llamar al cierre de cada barra M1 para actualizar historiales.
    pub fn on_bar_close(
        &mut self,
        cvd: f64,
        bar_delta: f64,
        bar_volume: f64,
        bar_high: f64,
        bar_low: f64,
        current_session: TradingSession,
        bar_big_cvd: f64,
        bar_vol_usd: f64,
    ) {
        // Detectar cambio de sesión
        if current_session != self.last_session {
            let is_new_trading_session = matches!(
                current_session,
                TradingSession::Asia | TradingSession::London | TradingSession::NewYork
            );
            if is_new_trading_session {
                self.cvd_session_baseline = cvd;
                self.big_cvd_session = 0.0;
                self.session_vol_usd = 0.0;
                self.session_vol_milestone = 0;
            }
            if current_session == TradingSession::Asia {
                self.paper.reset_daily();
            }
            self.last_session = current_session;
        }
        self.cvd_session = cvd - self.cvd_session_baseline;

        // Acumular big-trade CVD y volumen de sesión
        self.big_cvd_session += bar_big_cvd;
        self.session_vol_usd += bar_vol_usd;

        // Milestones de volumen de sesión (Fabio: 1B, 2B, 3B)
        for (i, &threshold) in [1e9_f64, 2e9, 3e9].iter().enumerate() {
            let lvl = (i + 1) as u32;
            if self.session_vol_milestone < lvl && self.session_vol_usd >= threshold {
                self.session_vol_milestone = lvl;
                println!("[vol_milestone] {}B USD — session_vol={:.2}B big_cvd={:+.1}",
                    lvl, self.session_vol_usd / 1e9, self.big_cvd_session);
            }
        }

        // Actualizar historiales con límite de lookback
        push_bounded(&mut self.cvd_bar_history, cvd, self.lookback);
        push_bounded(&mut self.high_bar_history, bar_high, self.lookback);
        push_bounded(&mut self.low_bar_history, bar_low, self.lookback);
        push_bounded(&mut self.delta_bar_history, bar_delta, 50);
        push_bounded(&mut self.volume_bar_history, bar_volume, 50);
    }

    /// Calcula el Delta Z-score del último delta vs la ventana de 50 barras.
    pub fn compute_dz(&self) -> f64 {
        let n = self.delta_bar_history.len();
        if n < 3 {
            return 0.0;
        }
        let deltas: Vec<f64> = self.delta_bar_history.iter().copied().collect();
        let mean = deltas.iter().sum::<f64>() / n as f64;
        let variance = deltas.iter().map(|d| (d - mean).powi(2)).sum::<f64>() / n as f64;
        let std = variance.sqrt();
        if std < 1e-9 {
            return 0.0;
        }
        (deltas[n - 1] - mean) / std
    }

    /// Calcula el slope del CVD con regresión OLS sobre las últimas `window` barras.
    /// No depende de ningún indicador externo — usa el historial interno del estado.
    pub fn compute_cvd_slope(&self, window: usize) -> Option<f64> {
        let n = self.cvd_bar_history.len();
        if n < 5 {
            return None;
        }
        let w = window.min(n);
        let vals: Vec<f64> = self.cvd_bar_history.iter().rev().take(w)
            .cloned().collect::<Vec<_>>().into_iter().rev().collect();
        let nf = vals.len() as f64;
        let sx: f64 = (0..vals.len()).map(|i| i as f64).sum();
        let sy: f64 = vals.iter().sum();
        let sxy: f64 = vals.iter().enumerate().map(|(i, y)| i as f64 * y).sum();
        let sx2: f64 = (0..vals.len()).map(|i| (i * i) as f64).sum();
        let den = nf * sx2 - sx * sx;
        if den.abs() < 1e-10 { return None; }
        Some((nf * sxy - sx * sy) / den)
    }

    /// Calcula el Volume Ratio: volumen actual / media de los últimos 50 barras.
    pub fn compute_vr(&self) -> f64 {
        let n = self.volume_bar_history.len();
        if n < 2 {
            return 1.0;
        }
        let vols: Vec<f64> = self.volume_bar_history.iter().copied().collect();
        let mean = vols[..n - 1].iter().sum::<f64>() / (n - 1) as f64;
        if mean < 1e-9 {
            return 1.0;
        }
        vols[n - 1] / mean
    }

    /// Construye el `ScalpingContext` completo para los detectores.
    #[allow(clippy::too_many_arguments)]
    pub fn build_context(
        &self,
        obi_l5: f64,
        micro_price: f64,
        spread_ticks: i32,
        cvd: f64,
        cvd_slope: Option<f64>,
        bar_open: f64,
        bar_high: f64,
        bar_low: f64,
        bar_close: f64,
        bar_delta: f64,
        bar_volume: f64,
        atr: f64,
        regime: ScalpingRegime,
        session: TradingSession,
        vwap: Option<f64>,
        poc: Option<f64>,
        funding_rate: Option<f64>,
        liq_ratio: f64,
        timestamp_ms: i64,
        range: Option<RangeContext>,
        footprint_levels: Vec<FootprintLevel>,
        big_trade_bullish: bool,
        big_trade_bearish: bool,
    ) -> ScalpingContext {
        let dz = self.compute_dz();
        let vr = if bar_volume > 0.0 { self.compute_vr() } else { 1.0 };
        // Slope externo (indicador CumulativeDelta) tiene prioridad; fallback al interno (20 barras)
        let cvd_slope_resolved = cvd_slope.or_else(|| self.compute_cvd_slope(20));

        let candle_poc = compute_candle_poc(&footprint_levels);

        ScalpingContext {
            obi: obi_l5,
            obi_ema_fast: self.obi_ema_fast,
            obi_ema_slow: self.obi_ema_slow,
            micro_price,
            spread_ticks,
            cvd,
            cvd_slope: cvd_slope_resolved,
            dz,
            vr,
            bar_delta,
            bar_open,
            bar_high,
            bar_low,
            bar_close,
            price: bar_close,
            atr,
            regime,
            session,
            vwap,
            poc,
            funding_rate,
            liq_ratio,
            timestamp_ms,
            range,
            cvd_history: self.cvd_bar_history.iter().copied().collect(),
            price_highs: self.high_bar_history.iter().copied().collect(),
            price_lows: self.low_bar_history.iter().copied().collect(),
            footprint_levels,
            big_trade_bullish,
            big_trade_bearish,
            candle_poc,
        }
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

fn push_bounded<T: Copy>(deque: &mut VecDeque<T>, value: T, max: usize) {
    deque.push_back(value);
    if deque.len() > max {
        deque.pop_front();
    }
}

/// POC de la vela: nivel de precio con mayor volumen total en el footprint.
fn compute_candle_poc(levels: &[FootprintLevel]) -> Option<f64> {
    levels
        .iter()
        .max_by(|a, b| {
            let va = a.buy_volume + a.sell_volume;
            let vb = b.buy_volume + b.sell_volume;
            va.partial_cmp(&vb).unwrap_or(std::cmp::Ordering::Equal)
        })
        .map(|l| l.price)
}

/// Verifica si la sesión actual es operativa para scalping.
///
/// Asia se incluye por su régimen dominante de rango (ideal S2).
/// Los trades de Asia se marcan con session="Asia" en el journal para
/// análisis separado — los thresholds de DZ/VR pueden necesitar calibración
/// distinta a London/NY por el menor volumen absoluto.
pub fn is_scalping_session(session: TradingSession) -> bool {
    matches!(
        session,
        TradingSession::Asia
            | TradingSession::London
            | TradingSession::LondonNyOverlap
            | TradingSession::NewYork
    )
}
