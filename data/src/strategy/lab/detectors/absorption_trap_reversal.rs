use crate::strategy::{
    lab::types::{BlockReason, LabSignal, LabStrategyId, StrategyMaturity, StrategyRuntimeStatus},
    types::{AbsorptionSide, Regime, Side, StrategyAction, StrategyConfig, StrategyMarketContext},
};
use uuid::Uuid;

const ID: LabStrategyId = LabStrategyId::AbsorptionTrapReversal;
const MATURITY: StrategyMaturity = StrategyMaturity::ObserveOnly;

/// Wraps FAR hypothesis: footprint absorption at key level → price reversal.
/// ObserveOnly — logs the phenomenon, no Core involvement.
pub fn evaluate(ctx: &StrategyMarketContext, cfg: &StrategyConfig) -> LabSignal {
    let atr = match ctx.atr {
        Some(a) if a > 0.0 => a,
        _ => return LabSignal::asleep(ID, MATURITY, ctx, vec!["atr_missing".into()]),
    };
    let vah = match ctx.volume_profile.vah {
        Some(v) => v,
        None => return LabSignal::asleep(ID, MATURITY, ctx, vec!["vah_missing".into()]),
    };
    let val = match ctx.volume_profile.val {
        Some(v) => v,
        None => return LabSignal::asleep(ID, MATURITY, ctx, vec!["val_missing".into()]),
    };

    let flow = &ctx.flow;
    let ob = &ctx.orderbook;
    let px = ctx.price;

    if ob.spread_bps.unwrap_or(999.0) > cfg.max_spread_bps {
        return LabSignal::blocked(ID, MATURITY, ctx, BlockReason::SpreadGate);
    }

    // Bid absorption at support → Long reversal
    let bid_absorption = flow.footprint_absorption == AbsorptionSide::Bid
        && flow.delta.unwrap_or(0.0) > 0.0
        && flow.cvd_slope.unwrap_or(0.0) > 0.0
        && matches!(ctx.regime, Regime::TrendUp | Regime::Chop)
        && px <= val + 0.5 * atr;

    if bid_absorption {
        let entry = px;
        let stop = entry - 1.5 * atr;
        let risk = entry - stop;
        if risk < 10.0 { return LabSignal::observed(ID, MATURITY, ctx, 0.4); }
        let target = entry + risk * cfg.min_rr;
        let rr = (target - entry) / risk;
        return make_signal(ctx, Side::Long, entry, stop, target, rr, 0.6);
    }

    // Ask absorption at resistance → Short reversal
    let ask_absorption = flow.footprint_absorption == AbsorptionSide::Ask
        && flow.delta.unwrap_or(0.0) < 0.0
        && flow.cvd_slope.unwrap_or(0.0) < 0.0
        && matches!(ctx.regime, Regime::TrendDown | Regime::Chop)
        && px >= vah - 0.5 * atr;

    if ask_absorption {
        let entry = px;
        let stop = entry + 1.5 * atr;
        let risk = stop - entry;
        if risk < 10.0 { return LabSignal::observed(ID, MATURITY, ctx, 0.4); }
        let target = entry - risk * cfg.min_rr;
        let rr = (entry - target) / risk;
        return make_signal(ctx, Side::Short, entry, stop, target, rr, 0.6);
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
