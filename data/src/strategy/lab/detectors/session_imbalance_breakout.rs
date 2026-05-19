use crate::strategy::{
    lab::types::{BlockReason, LabSignal, LabStrategyId, StrategyMaturity, StrategyRuntimeStatus},
    types::{Regime, Side, StrategyAction, StrategyConfig, StrategyMarketContext},
};
use uuid::Uuid;

const ID: LabStrategyId = LabStrategyId::SessionImbalanceBreakout;
const MATURITY: StrategyMaturity = StrategyMaturity::ObserveOnly;

/// Wraps SOB hypothesis: session open (London/NY) breaks out of prior session range
/// with order flow confirmation.
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

    // Need at least Expansion or Trend context
    if !matches!(ctx.regime, Regime::Expansion | Regime::TrendUp | Regime::TrendDown) {
        return LabSignal::observed(ID, MATURITY, ctx, 0.1);
    }

    // Bullish breakout above VAH
    let bull_break = px > vah
        && flow.cvd_slope.unwrap_or(0.0) > 0.0
        && flow.delta.unwrap_or(0.0) > 0.0
        && flow.taker_imbalance.unwrap_or(0.0) > 0.0
        && ob.obi_l5.unwrap_or(0.0) > 0.1
        && ob.thin_zone_above
        && !flow.failed_acceptance;

    if bull_break {
        let entry = px;
        let stop = vah - 0.3 * atr;
        let risk = entry - stop;
        if risk < 10.0 { return LabSignal::observed(ID, MATURITY, ctx, 0.5); }
        let target = entry + risk * cfg.min_rr;
        let rr = (target - entry) / risk;
        return make_signal(ctx, Side::Long, entry, stop, target, rr, 0.65);
    }

    // Bearish breakout below VAL
    let bear_break = px < val
        && flow.cvd_slope.unwrap_or(0.0) < 0.0
        && flow.delta.unwrap_or(0.0) < 0.0
        && flow.taker_imbalance.unwrap_or(0.0) < 0.0
        && ob.obi_l5.unwrap_or(0.0) < -0.1
        && ob.thin_zone_below
        && !flow.failed_acceptance;

    if bear_break {
        let entry = px;
        let stop = val + 0.3 * atr;
        let risk = stop - entry;
        if risk < 10.0 { return LabSignal::observed(ID, MATURITY, ctx, 0.5); }
        let target = entry - risk * cfg.min_rr;
        let rr = (entry - target) / risk;
        return make_signal(ctx, Side::Short, entry, stop, target, rr, 0.65);
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
