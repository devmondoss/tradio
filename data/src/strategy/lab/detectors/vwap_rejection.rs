use crate::strategy::{
    lab::types::{
        BlockReason, LabSignal, LabStrategyId, StrategyMaturity, StrategyRuntimeStatus,
    },
    types::{PriceRelation, Regime, Side, StrategyAction, StrategyMarketContext, StrategyConfig},
};
use uuid::Uuid;

const ID: LabStrategyId = LabStrategyId::VwapRejection;
const MATURITY: StrategyMaturity = StrategyMaturity::ShadowLab;

/// VWAP Rejection: price approaches VWAP from one side and gets rejected with orderflow confirmation.
/// Unlike VVPC (which needs price INSIDE value), rejection fires when price reaches VWAP/AVWAP and bounces.
pub fn evaluate(ctx: &StrategyMarketContext, cfg: &StrategyConfig) -> LabSignal {
    let px = ctx.price;
    let atr = match ctx.atr {
        Some(a) if a > 0.0 => a,
        _ => return LabSignal::asleep(ID, MATURITY, ctx, vec!["atr_missing".into()]),
    };
    let vwap = match ctx.vwap.vwap_session {
        Some(v) => v,
        None => return LabSignal::asleep(ID, MATURITY, ctx, vec!["vwap_missing".into()]),
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

    // Spread gate
    if ob.spread_bps.unwrap_or(999.0) > cfg.max_spread_bps {
        return LabSignal::blocked(ID, MATURITY, ctx, BlockReason::SpreadGate);
    }

    // Price must be within 0.5×ATR of VWAP to be "near" it
    let near_vwap = (px - vwap).abs() <= 0.5 * atr;
    if !near_vwap {
        return LabSignal::observed(ID, MATURITY, ctx, 0.1);
    }

    // LONG rejection: price came from below VAL (or near VWAP from below), gets rejected upward
    // Context: regime allows counter-trend bounce, price has approached VWAP from below
    let long_ok = matches!(ctx.regime, Regime::TrendUp | Regime::Chop | Regime::Compression)
        && matches!(ctx.vwap.price_vs_vwap, PriceRelation::Below | PriceRelation::At)
        && flow.delta.unwrap_or(0.0) > 0.0
        && flow.cvd_slope.unwrap_or(0.0) > 0.0
        && flow.taker_imbalance.unwrap_or(0.0) > 0.0
        && ob.obi_l5.unwrap_or(0.0) > 0.0
        && !flow.failed_acceptance;

    if long_ok {
        let entry = px;
        let stop = f64::max(val, entry - 1.0 * atr);
        let risk = entry - stop;
        if risk < 10.0 || stop >= entry {
            return LabSignal::blocked(ID, MATURITY, ctx, BlockReason::RRTooLow { calculated: 0.0, minimum: cfg.min_rr });
        }
        let target = entry + risk * cfg.min_rr;
        if target > vah + atr {
            return LabSignal::blocked(ID, MATURITY, ctx, BlockReason::RRTooLow { calculated: risk * cfg.min_rr, minimum: cfg.min_rr });
        }
        let rr = (target - entry) / risk;
        return make_signal(ctx, Side::Long, entry, stop, target, rr);
    }

    // SHORT rejection: price approached from above VAH and gets rejected downward
    let short_ok = matches!(ctx.regime, Regime::TrendDown | Regime::Chop | Regime::Compression)
        && matches!(ctx.vwap.price_vs_vwap, PriceRelation::Above | PriceRelation::At)
        && flow.delta.unwrap_or(0.0) < 0.0
        && flow.cvd_slope.unwrap_or(0.0) < 0.0
        && flow.taker_imbalance.unwrap_or(0.0) < 0.0
        && ob.obi_l5.unwrap_or(0.0) < 0.0
        && !flow.failed_acceptance;

    if short_ok {
        let entry = px;
        let stop = f64::min(vah, entry + 1.0 * atr);
        let risk = stop - entry;
        if risk < 10.0 || stop <= entry {
            return LabSignal::blocked(ID, MATURITY, ctx, BlockReason::RRTooLow { calculated: 0.0, minimum: cfg.min_rr });
        }
        let target = entry - risk * cfg.min_rr;
        if target < val - atr {
            return LabSignal::blocked(ID, MATURITY, ctx, BlockReason::RRTooLow { calculated: risk * cfg.min_rr, minimum: cfg.min_rr });
        }
        let rr = (entry - target) / risk;
        return make_signal(ctx, Side::Short, entry, stop, target, rr);
    }

    LabSignal::observed(ID, MATURITY, ctx, 0.3)
}

fn make_signal(
    ctx: &StrategyMarketContext,
    side: Side,
    entry: f64,
    stop: f64,
    target: f64,
    rr: f64,
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
        confidence: 0.7,
        snapshot: LabFeatureSnapshot::from_ctx(ctx),
        missing_data: vec![],
    }
}
