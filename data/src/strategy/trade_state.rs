use serde::{Deserialize, Serialize};

/// Estado progresivo del stop en un trade activo.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum StopState {
    /// Stop en precio original — riesgo máximo activo.
    Original,
    /// Stop movido a break-even después de confirmar nivel estructural intermedio.
    BreakEven { confirmed_level: f64 },
    /// Stop siguiendo swings confirmados en TF de ejecución.
    TrailingStructural { last_swing: f64 },
}

impl StopState {
    pub fn name(&self) -> &'static str {
        match self {
            Self::Original => "Original",
            Self::BreakEven { .. } => "BreakEven",
            Self::TrailingStructural { .. } => "TrailingStructural",
        }
    }
}

/// Fase del trade según avance hacia el target.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum TradePhase {
    Open,
    Level1Confirmed,
    TargetExceeded,
}

impl TradePhase {
    pub fn name(&self) -> &'static str {
        match self {
            Self::Open => "Open",
            Self::Level1Confirmed => "Level1Confirmed",
            Self::TargetExceeded => "TargetExceeded",
        }
    }
}

/// Razón de cierre del trade.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum CloseReason {
    /// Stop original tocado.
    StopHit,
    /// Target tocado antes de activar trailing (en fase Original o BreakEven).
    TargetHit,
    /// Stop trailing tocado después de superar el target.
    TrailingHit,
    /// Máximo de barras alcanzado sin avance.
    TTLExpired,
    /// Nivel de contexto invalidado (VWAP/VAH cruzado en contra).
    Invalidated,
}

impl CloseReason {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::StopHit => "STOP_HIT",
            Self::TargetHit => "TARGET_HIT",
            Self::TrailingHit => "TRAILING_HIT",
            Self::TTLExpired => "TTL_EXPIRED",
            Self::Invalidated => "INVALIDATED",
        }
    }
}

/// Niveles estructurales para el trade.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StructuralLevels {
    /// Target principal — primer nivel estructural en dirección del trade.
    pub target: f64,
    /// Nivel intermedio opcional — trigger para mover stop a break-even.
    pub intermediate: Option<f64>,
    /// Distancia en ATR al target (calculada al abrir).
    pub atr_distance: f64,
}
