use data;

#[derive(Debug, Clone, Default)]
pub struct StrategySnapshot {
    pub symbol: String,
    pub equity: f64,
    pub balance: f64,
    pub initial_capital: f64,
    pub wins: usize,
    pub losses: usize,
    pub overlay_enabled: bool,
    pub regime: String,
    pub last_score: f64,
    pub open_positions: Vec<PositionSnap>,
    pub active_signal: Option<SignalSnap>,
    pub recent_trades: Vec<TradeSnap>,

    /// Barras evaluadas desde que arrancó la UI (cada `run_strategy_detection`).
    pub bars_evaluated: u64,
    /// Timestamp ms UTC de la última barra que el detector procesó.
    pub last_bar_ms: Option<i64>,
    /// Motivo del último rechazo (primer ítem de `missing`). Útil para ver
    /// "qué le falta" al detector en tiempo real cuando aún no aprueba.
    pub last_missing: Option<String>,
    /// Estado per-detector de la última barra evaluada.
    pub detector_log: Vec<data::strategy::types::DetectorSnap>,
}

#[derive(Debug, Clone)]
pub struct PositionSnap {
    pub side: String,
    pub strategy_name: String,
    pub entry_price: f64,
    pub stop_price: Option<f64>,
    pub target_price: Option<f64>,
    pub unrealized_pnl_pct: f64,
}

#[derive(Debug, Clone)]
pub struct SignalSnap {
    pub strategy_name: String,
    pub side: String,
    pub score: f64,
    pub evidence: Vec<String>,
    pub missing: Vec<String>,
}

#[derive(Debug, Clone)]
pub struct TradeSnap {
    pub strategy_name: String,
    pub side: String,
    pub close_reason: String,
    pub net_pnl: f64,
    pub net_pnl_pct: f64,
}
