/// Subdimi Parallel Observer
///
/// Runs the 6 Subdimi detectors on every bar in parallel — no winner-takes-all,
/// no paper trading, no maturity lifecycle. All signals that fire are returned
/// for logging and comparison against the DRR Core strategy.
///
/// Purpose: measure what the previous Subdimi-only router would have signaled
/// alongside DRR, so edge can be compared once enough data accumulates.
use super::detectors::{
    cvd_divergence_reversal, footprint_absorption_reversal, lvn_liquidity_vacuum_breakout,
    order_block_retest, value_area_failed_auction,
};
use super::scoring::score_signal;
use super::types::{StrategyConfig, StrategyMarketContext, StrategySignal};

/// Evaluate all 6 Subdimi detectors and return every signal that fires.
/// Signals are scored but no winner is selected — all go to the parallel log.
pub fn run_subdimi_parallel(
    ctx: &StrategyMarketContext,
    cfg: &StrategyConfig,
) -> Vec<StrategySignal> {
    let mut signals: Vec<StrategySignal> = Vec::new();

    // The 6 Subdimi Core detectors (blocked in live router, active here for observation)
    macro_rules! try_parallel {
        ($expr:expr) => {
            if let Some(s) = $expr {
                signals.push(score_signal(ctx, s));
            }
        };
    }

    try_parallel!(value_area_failed_auction::detect(ctx, cfg));
    try_parallel!(footprint_absorption_reversal::detect(ctx, cfg));
    try_parallel!(lvn_liquidity_vacuum_breakout::detect(ctx, cfg));
    try_parallel!(cvd_divergence_reversal::detect(ctx, cfg));
    try_parallel!(order_block_retest::detect(ctx, cfg));

    // LiquidationHunt requires institutional data
    if let Some(inst) = ctx.institutional.as_ref() {
        use super::detectors::liquidation_hunt;
        try_parallel!(liquidation_hunt::detect(ctx, inst, cfg));
    }

    signals
}
