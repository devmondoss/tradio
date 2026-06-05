#![allow(dead_code)]
use serde::Deserialize;

// ── Colores del tema FlowSurface (data/src/config/theme.rs) ─────────────────
pub mod theme {
    use iced::Color;
    pub const BG:      Color = Color { r: 0.094, g: 0.086, b: 0.086, a: 1.0 }; // #181616
    pub const CARD:    Color = Color { r: 0.118, g: 0.110, b: 0.110, a: 1.0 }; // #1e1c1c
    pub const CARD2:   Color = Color { r: 0.145, g: 0.133, b: 0.133, a: 1.0 }; // #252222
    pub const BORDER:  Color = Color { r: 0.180, g: 0.169, b: 0.169, a: 1.0 }; // #2e2b2b
    pub const TEXT:    Color = Color { r: 0.773, g: 0.788, b: 0.773, a: 1.0 }; // #c5c9c5
    pub const TEXT2:   Color = Color { r: 0.478, g: 0.502, b: 0.478, a: 1.0 }; // #7a807a
    pub const TEXT3:   Color = Color { r: 0.282, g: 0.298, b: 0.282, a: 1.0 }; // #484c48
    pub const UP:      Color = Color { r: 0.318, g: 0.804, b: 0.627, a: 1.0 }; // #51cda0
    pub const DOWN:    Color = Color { r: 0.753, g: 0.314, b: 0.302, a: 1.0 }; // #c0504d
    pub const ACCENT:  Color = Color { r: 0.784, g: 0.784, b: 0.784, a: 1.0 }; // #c8c8c8
}

// ── Eventos del WS del monitor ───────────────────────────────────────────────
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum WsMessage {
    Tick { ts_ms: i64, open: f64, high: f64, low: f64, close: f64 },
    Bar  { ts_ms: i64, open: f64, high: f64, low: f64, close: f64, regime: String },
    Signal {
        ts_ms: i64, direction: String,
        score: Option<u8>, veto: Option<String>,
        entry: f64, rr: f64, session: String,
    },
}

// ── Barra OHLC ───────────────────────────────────────────────────────────────
#[derive(Debug, Clone)]
pub struct Bar {
    pub ts_ms:  i64,
    pub open:   f64,
    pub high:   f64,
    pub low:    f64,
    pub close:  f64,
    pub live:   bool,   // true = vela en construcción
}

// ── Señal RBF ────────────────────────────────────────────────────────────────
#[derive(Debug, Clone, Deserialize)]
pub struct RbfSignal {
    pub id:                i64,
    pub timestamp_ms:      i64,
    pub direction:         String,
    pub session:           String,
    pub macro_regime:      Option<String>,
    pub entry_price:       f64,
    pub status:            String,
    pub result_r:          Option<f64>,
    pub exit_reason:       Option<String>,
    pub confluence_score:  Option<u8>,
    pub confluence_flags:  Option<Vec<String>>,
    pub veto_reason:       Option<String>,
    pub rr:                f64,
}

impl RbfSignal {
    pub fn is_open(&self)   -> bool { self.status == "OPEN" }
    pub fn is_win(&self)    -> bool { self.result_r.map(|r| r > 0.0).unwrap_or(false) }
    pub fn is_closed(&self) -> bool { self.result_r.is_some() }
}
