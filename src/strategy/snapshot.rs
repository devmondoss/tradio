#[derive(Debug, Clone, Default)]
pub struct StrategySnapshot {
    pub symbol: String,
    pub equity: f64,
    pub balance: f64,
    pub initial_capital: f64,
    pub wins: usize,
    pub losses: usize,
    pub overlay_enabled: bool,
    pub open_positions: Vec<PositionSnap>,
    pub active_signal: Option<SignalSnap>,
    pub recent_trades: Vec<TradeSnap>,
}

#[derive(Debug, Clone)]
pub struct PositionSnap {
    pub side: String,
    pub entry_price: f64,
    pub stop_price: Option<f64>,
    pub target_price: Option<f64>,
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
