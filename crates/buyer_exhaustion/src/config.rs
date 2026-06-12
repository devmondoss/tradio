//! Configuración del detector Buyer Exhaustion.
//!
//! Patrón: rango con CVD neto positivo (compradores atrapados) + giro de CVD
//! en las últimas N barras + ruptura a la baja con volumen expandido.
//! Opuesto al RBF: aquí el CVD durante el rango es POSITIVO (la trampa).

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BuyerExhaustionConfig {
    pub enabled: bool,

    // ── Formación del rango ─────────────────────────────────────────────────
    /// Ventanas de barras M1 a escanear. Se prueba cada una en orden; la
    /// primera que cumple todas las condiciones emite la señal.
    pub range_windows: Vec<usize>,
    /// Rango mínimo (high−low)/precio en %. Filtra ruido intrabar.
    pub range_min_pct: f64,
    /// Rango máximo (high−low)/precio en %. Filtra rangos demasiado amplios
    /// donde el "breakout" ya recorrió demasiado camino.
    pub range_max_pct: f64,

    // ── Condición de trampa (CVD positivo en rango) ──────────────────────────
    /// Exigir que el CVD acumulado del rango sea > 0.
    /// Si es false, acepta cualquier signo (modo bidir para sell exhaustion).
    pub require_positive_range_cvd: bool,

    // ── Giro de CVD (la señal real) ─────────────────────────────────────────
    /// Número de barras finales del rango para medir el "giro".
    /// Referencia literature: 5 barras = últimos ~5 min en M1.
    pub pre_cvd_bars: usize,
    /// El giro debe ser significativo: |pre_cvd| / range_cvd >= ratio.
    /// 0.30 = los últimos N bars deshicieron ≥30% de toda la compra del rango.
    /// Calibración BTC (1 caso): 0.52 — usar 0.30 como piso inicial.
    pub cvd_flip_min_ratio: f64,

    // ── Confirmación del breakout ───────────────────────────────────────────
    /// Volumen mínimo de la barra de ruptura vs el promedio del vol_window.
    /// Referencia Axia/Jigsaw: ≥2× confirma convicción institucional.
    pub breakout_vr_min: f64,
    /// Ventana de barras para calcular el vol promedio base del VR.
    pub vr_window: usize,
    /// La barra de ruptura DEBE tener delta negativo (sellers agresivos).
    pub require_negative_breakout_delta: bool,

    // ── Micro-confirmación de la barra de ruptura ───────────────────────────
    /// Posición del close dentro del rango de la barra. <0.35 = cierre en tercio
    /// inferior → barra bear fuerte. None = sin filtro.
    pub close_location_max: Option<f64>,
    /// Cuerpo bajista mínimo (|close-open|/rango). >0.35 = dominio de sellers.
    pub bear_body_min: Option<f64>,
    /// Cola superior máxima (high-max(o,c))/rango. <0.30 = sin rechazo arriba.
    pub upper_wick_max: Option<f64>,

    // ── Gestión de trade ────────────────────────────────────────────────────
    /// true = stop en range_high (conservador, estructura real).
    /// false = stop en range_mid (ajustado, Axia tight stop).
    pub stop_at_range_high: bool,
    /// Time stop: si a N barras la posición sigue en pérdida, cierra al close.
    pub time_stop_bars: u32,
    /// RR mínimo (reward/risk) para emitir señal.
    pub min_rr: f64,

    // ── Risk cap diario ─────────────────────────────────────────────────────
    pub day_loss_limit:  f64,
    pub day_profit_cap:  f64,

    // ── Cooldown entre señales ──────────────────────────────────────────────
    /// Barras mínimas entre dos señales consecutivas. Evita múltiples entradas
    /// en el mismo rango si el primer breakout falla y retestea.
    pub signal_cooldown_bars: usize,

    // ── Sessions habilitadas ────────────────────────────────────────────────
    pub sessions_enabled: Vec<String>,
}

impl Default for BuyerExhaustionConfig {
    fn default() -> Self {
        Self {
            enabled:                     true,
            range_windows:               vec![8],
            range_min_pct:               0.05,
            range_max_pct:               0.50,
            require_positive_range_cvd:  true,
            pre_cvd_bars:                5,
            cvd_flip_min_ratio:          0.40,
            breakout_vr_min:             2.5,
            vr_window:                   50,
            require_negative_breakout_delta: true,
            // Micro-confirmación (backtest 163d: +3pp WR, +0.047R avg vs sin filtros)
            close_location_max:          Some(0.35),
            bear_body_min:               Some(0.35),
            upper_wick_max:              Some(0.30),
            stop_at_range_high:          true,
            time_stop_bars:              60,
            min_rr:                      1.80,
            day_loss_limit:             -3.0,
            day_profit_cap:              6.0,
            signal_cooldown_bars:        60,
            sessions_enabled: vec![
                // London excluido: WR=24% avg=-0.202R (180d backtest 2026-06-11)
                "LondonNyOverlap".into(),
            ],
        }
    }
}
