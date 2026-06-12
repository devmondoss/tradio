//! Tipos de señal y trade cerrado del detector Buyer Exhaustion.

use serde::{Deserialize, Serialize};
use data::session::session_tracker::TradingSession;

/// Señal emitida cuando se detecta el patrón completo:
/// rango con CVD positivo + giro de CVD + ruptura a la baja con VR expandido.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BuyerExhaustionSignal {
    pub timestamp_ms: i64,
    pub session:      TradingSession,
    pub symbol:       String,

    // ── Niveles del trade ───────────────────────────────────────────────────
    pub entry_price:  f64,  // close de la barra de ruptura
    pub stop_price:   f64,  // range_high (tesis inválida si precio recupera el rango)
    pub target_price: f64,  // T1 = range_low − range_height (medida del rango)
    pub rr:           f64,  // (entry−target) / (stop−entry)

    // ── Rango ───────────────────────────────────────────────────────────────
    pub range_high:  f64,
    pub range_low:   f64,
    pub range_pct:   f64,   // (range_high−range_low)/price × 100
    pub range_bars:  usize, // ventana que generó la señal

    // ── Evidencia CVD ───────────────────────────────────────────────────────
    /// CVD acumulado durante el rango (positivo = compradores atrapados).
    pub range_cvd:      f64,
    /// CVD de las últimas N barras del rango (negativo = giro de sellers).
    pub pre_cvd_flip:   f64,
    /// |pre_cvd_flip| / range_cvd — fuerza del giro (0.30+ significativo).
    pub cvd_flip_ratio: f64,

    // ── Evidencia del breakout ──────────────────────────────────────────────
    /// Volume Ratio de la barra de ruptura vs media del VR window.
    pub vr_at_breakout:  f64,
    /// Delta (taker_buy − taker_sell) de la barra de ruptura (negativo).
    pub breakout_delta:  f64,

    // ── Microestructura (gate layer 2) ─────────────────────────────────────
    /// Score de confluencia microestructural: 0 = sin confirmación, 6 = máximo.
    /// Calculado a partir del RbfGateContext disponible en el monitor.
    pub confluence_score: u8,
    /// Flags individuales que contribuyeron al score (para diagnóstico).
    pub confluence_flags: Vec<String>,

    /// UUID de Supabase asignado async. No se serializa a JSON.
    #[serde(skip)]
    pub supabase_id: Option<String>,
}

/// Razón de cierre de una posición Buyer Exhaustion.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum BeExitReason {
    Target,
    Stop,
    TimeStop,
    SessionEnd,
    DailyLimitHit,
}

impl BeExitReason {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Target        => "TARGET",
            Self::Stop          => "STOP",
            Self::TimeStop      => "TIME_STOP",
            Self::SessionEnd    => "SESSION_END",
            Self::DailyLimitHit => "DAILY_LIMIT",
        }
    }
}

/// Trade cerrado con resultado en R-múltiplos.
#[derive(Debug, Clone)]
pub struct BeClosedTrade {
    pub entry_price:  f64,
    pub exit_price:   f64,
    /// R-múltiplo puro. Positivo = ganancia, negativo = pérdida.
    pub result_r:     f64,
    pub exit_reason:  BeExitReason,
    pub entry_ms:     i64,
    pub exit_ms:      i64,
    pub bars_held:    u32,
    pub supabase_id:  Option<String>,
}
