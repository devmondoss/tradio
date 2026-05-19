use crate::strategy::{
    lab::types::{BlockReason, LabSignal, LabStrategyId, StrategyMaturity, StrategyRuntimeStatus},
    types::{Regime, Side, StrategyAction, StrategyConfig, StrategyMarketContext},
};
use uuid::Uuid;

const ID: LabStrategyId = LabStrategyId::OrderBlockFlowRetest;
const MATURITY: StrategyMaturity = StrategyMaturity::ObserveOnly;

/// Wraps OBR hypothesis: price retests a known order block with confirming orderflow.
/// ObserveOnly until we have enough data to know if OBs hold statistically.
pub fn evaluate(ctx: &StrategyMarketContext, cfg: &StrategyConfig) -> LabSignal {
    let atr = match ctx.atr {
        Some(a) if a > 0.0 => a,
        _ => return LabSignal::asleep(ID, MATURITY, ctx, vec!["atr_missing".into()]),
    };
    let obs = match ctx.order_blocks.as_ref() {
        Some(o) => o,
        None => return LabSignal::asleep(ID, MATURITY, ctx, vec!["order_blocks_missing".into()]),
    };

    let flow = &ctx.flow;
    let ob = &ctx.orderbook;
    let px = ctx.price;

    if ob.spread_bps.unwrap_or(999.0) > cfg.max_spread_bps {
        return LabSignal::blocked(ID, MATURITY, ctx, BlockReason::SpreadGate);
    }

    // Bullish OB retest: price near bullish OB with long flow
    if let Some(ref bull_ob) = obs.nearest_bullish {
        let ob_mid = (bull_ob.high + bull_ob.low) * 0.5;
        let near_ob = (px - ob_mid).abs() <= 0.5 * atr;
        let bull_flow = flow.delta.unwrap_or(0.0) > 0.0
            && flow.cvd_slope.unwrap_or(0.0) > 0.0
            && flow.taker_imbalance.unwrap_or(0.0) > 0.0
            && !flow.failed_acceptance
            && matches!(ctx.regime, Regime::TrendUp | Regime::Expansion | Regime::Chop);

        if near_ob && bull_flow {
            let entry = px;
            let stop = bull_ob.low - 0.5 * atr;
            let risk = entry - stop;
            if risk >= 10.0 {
                let target = entry + risk * cfg.min_rr;
                let rr = (target - entry) / risk;
                return make_signal(ctx, Side::Long, entry, stop, target, rr, 0.65);
            }
        }
    }

    // Bearish OB retest: price near bearish OB with short flow
    if let Some(ref bear_ob) = obs.nearest_bearish {
        let ob_mid = (bear_ob.high + bear_ob.low) * 0.5;
        let near_ob = (px - ob_mid).abs() <= 0.5 * atr;
        let bear_flow = flow.delta.unwrap_or(0.0) < 0.0
            && flow.cvd_slope.unwrap_or(0.0) < 0.0
            && flow.taker_imbalance.unwrap_or(0.0) < 0.0
            && !flow.failed_acceptance
            && matches!(ctx.regime, Regime::TrendDown | Regime::Expansion | Regime::Chop);

        if near_ob && bear_flow {
            let entry = px;
            let stop = bear_ob.high + 0.5 * atr;
            let risk = stop - entry;
            if risk >= 10.0 {
                let target = entry - risk * cfg.min_rr;
                let rr = (entry - target) / risk;
                return make_signal(ctx, Side::Short, entry, stop, target, rr, 0.65);
            }
        }
    }

    LabSignal::observed(ID, MATURITY, ctx, 0.1)
}

fn make_signal(
    ctx: &StrategyMarketContext,
    side: Side,
    entry: f64,
    stop: f64,
    target: f64,
    rr: f64,
    confidence: f64,
) -> LabSignal {
    use crate::strategy::lab::types::LabFeatureSnapshot;
    LabSignal {
        signal_id: Uuid::new_v4(),
        strategy_id: ID,
        status: StrategyRuntimeStatus::ShadowSignal,
        maturity: MATURITY,
        timestamp_ms: ctx.timestamp_ms,
        action: Some(StrategyAction::ShadowSignal),
        side: Some(side),
        entry_price: Some(entry),
        target: Some(target),
        stop: Some(stop),
        rr: Some(rr),
        confidence,
        snapshot: LabFeatureSnapshot::from_ctx(ctx),
        missing_data: vec![],
    }
}
