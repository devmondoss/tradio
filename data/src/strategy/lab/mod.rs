pub mod detectors;
pub mod tracker;
pub mod types;

pub use tracker::LabTracker;
pub use types::*;

use crate::strategy::types::{StrategyConfig, StrategyMarketContext};

/// Evalúa todas las hipótesis del Lab en paralelo (ninguna bloquea a las demás).
/// Cada detector devuelve un LabSignal independientemente del resultado de los otros.
pub fn run_strategy_lab(ctx: &StrategyMarketContext, cfg: &StrategyConfig, lab_cfg: &LabConfig) -> Vec<LabSignal> {
    if !lab_cfg.enabled {
        return vec![];
    }

    vec![
        detectors::vwap_rejection::evaluate(ctx, cfg),
        detectors::absorption_trap_reversal::evaluate(ctx, cfg),
        detectors::session_imbalance_breakout::evaluate(ctx, cfg),
        detectors::order_block_flow_retest::evaluate(ctx, cfg),
        detectors::liquidity_magnet::evaluate(ctx, cfg),
    ]
}
