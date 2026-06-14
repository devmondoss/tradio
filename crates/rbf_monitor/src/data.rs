#![allow(dead_code)]
use serde::Deserialize;

// ── Colores del tema FlowSurface ─────────────────────────────────────────────
pub mod theme {
    use iced::Color;
    pub const BG: Color = Color {
        r: 0.094,
        g: 0.086,
        b: 0.086,
        a: 1.0,
    };
    pub const CARD: Color = Color {
        r: 0.118,
        g: 0.110,
        b: 0.110,
        a: 1.0,
    };
    pub const CARD2: Color = Color {
        r: 0.145,
        g: 0.133,
        b: 0.133,
        a: 1.0,
    };
    pub const BORDER: Color = Color {
        r: 0.180,
        g: 0.169,
        b: 0.169,
        a: 1.0,
    };
    pub const TEXT: Color = Color {
        r: 0.773,
        g: 0.788,
        b: 0.773,
        a: 1.0,
    };
    pub const TEXT2: Color = Color {
        r: 0.478,
        g: 0.502,
        b: 0.478,
        a: 1.0,
    };
    pub const TEXT3: Color = Color {
        r: 0.282,
        g: 0.298,
        b: 0.282,
        a: 1.0,
    };
    pub const UP: Color = Color {
        r: 0.318,
        g: 0.804,
        b: 0.627,
        a: 1.0,
    };
    pub const DOWN: Color = Color {
        r: 0.753,
        g: 0.314,
        b: 0.302,
        a: 1.0,
    };
    pub const ACCENT: Color = Color {
        r: 0.784,
        g: 0.784,
        b: 0.784,
        a: 1.0,
    };
}

// ── Temporalidades ───────────────────────────────────────────────────────────
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Timeframe {
    M1,
    M3,
    M5,
    M15,
    M30,
    H1,
    H4,
    D1,
}

impl Timeframe {
    pub fn interval(self) -> &'static str {
        match self {
            Self::M1 => "1m",
            Self::M3 => "3m",
            Self::M5 => "5m",
            Self::M15 => "15m",
            Self::M30 => "30m",
            Self::H1 => "1h",
            Self::H4 => "4h",
            Self::D1 => "1d",
        }
    }
    pub fn label(self) -> &'static str {
        self.interval()
    }
    pub fn all() -> &'static [Self] {
        &[
            Self::M1,
            Self::M3,
            Self::M5,
            Self::M15,
            Self::M30,
            Self::H1,
            Self::H4,
            Self::D1,
        ]
    }
    pub fn ws_url(self) -> String {
        let symbol = std::env::var("SYMBOL")
            .unwrap_or_else(|_| "BTCUSDT".into())
            .to_lowercase();
        format!(
            "wss://fstream.binance.com/ws/{}@kline_{}",
            symbol,
            self.interval()
        )
    }
}

// ── Barra OHLC ───────────────────────────────────────────────────────────────
#[derive(Debug, Clone)]
pub struct Bar {
    pub ts_ms: i64,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub live: bool,
}

// ── Señal RBF ────────────────────────────────────────────────────────────────
#[derive(Debug, Clone, Deserialize)]
pub struct RbfSignal {
    pub id: i64,
    pub timestamp_ms: i64,
    pub direction: String,
    pub session: String,
    pub macro_regime: Option<String>,
    pub entry_price: f64,
    pub status: String,
    pub result_r: Option<f64>,
    pub exit_reason: Option<String>,
    pub confluence_score: Option<u8>,
    pub confluence_flags: Option<Vec<String>>,
    pub veto_reason: Option<String>,
    pub rr: f64,
}

impl RbfSignal {
    pub fn is_open(&self) -> bool {
        self.status == "OPEN"
    }
    pub fn is_win(&self) -> bool {
        self.result_r.map(|r| r > 0.0).unwrap_or(false)
    }
    pub fn is_closed(&self) -> bool {
        self.result_r.is_some()
    }
}
