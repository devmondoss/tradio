use crate::strategy::{
    lab::types::{BlockReason, LabSignal, LabStrategyId, StrategyMaturity, StrategyRuntimeStatus},
    types::{Regime, Side, StrategyAction, StrategyConfig, StrategyMarketContext},
};
use uuid::Uuid;

const ID: LabStrategyId = LabStrategyId::LiquidityMagnet;
const MATURITY: StrategyMaturity = StrategyMaturity::ObserveOnly;

/// LiquidityMagnet: price approaching an HVN within 2×ATR with aligned flow.
/// HVNs act as magnets — price tends to visit them and stall.
/// This captures the directional move INTO the magnet level.
pub fn evaluate(ctx: &StrategyMarketContext, cfg: &StrategyConfig) -> LabSignal {
    let atr = match ctx.atr {
        Some(a) if a > 0.0 => a,
        _ => return LabSignal::asleep(ID, MATURITY, ctx, vec!["atr_missing".into()]),
    };
    let vwap = match ctx.vwap.vwap_session {
        Some(v) => v,
        None => return LabSignal::asleep(ID, MATURITY, ctx, vec!["vwap_missing".into()]),
    };

    let flow = &ctx.flow;
    let ob = &ctx.orderbook;
    let vp = &ctx.volume_profile;
    let px = ctx.price;

    if ob.spread_bps.unwrap_or(999.0) > cfg.max_spread_bps {
        return LabSignal::blocked(ID, MATURITY, ctx, BlockReason::SpreadGate);
    }

    // Find nearest HVN above within 2×ATR
    let hvn_above = vp.hvn_nearby
        .iter()
        .copied()
        .filter(|&h| h > px && h <= px + 2.0 * atr)
        .reduce(f64::min);

    // Find nearest HVN below within 2×ATR
    let hvn_below = vp.hvn_nearby
        .iter()
        .copied()
        .filter(|&h| h < px && h >= px - 2.0 * atr)
        .reduce(f64::max);

    // Upward magnet: price above VWAP, fast_slope positive, OBI positive, thin zone above → HVN magnet pull
    if let Some(target_hvn) = hvn_above {
        let upward_ok = px > vwap
            && flow.fast_slope.unwrap_or(0.0) > 0.05
            && ob.obi_l5.unwrap_or(0.0) > 0.15
            && flow.cvd_slope.unwrap_or(0.0) > 0.0
            && ob.thin_zone_above
            && matches!(ctx.regime, Regime::TrendUp | Regime::Expansion | Regime::Chop);

        if upward_ok {
            let entry = px;
            let stop = entry - 1.0 * atr;
            let risk = entry - stop;
            if risk >= 10.0 {
                let reward = target_hvn - entry;
                let rr = reward / risk;
                if rr >= cfg.min_rr {
                    return make_signal(ctx, Side::Long, entry, stop, target_hvn, rr, 0.55);
                }
            }
        }
    }

    // Downward magnet: price below VWAP, fast_slope negative, OBI negative, thin zone below → HVN magnet pull
    if let Some(target_hvn) = hvn_below {
        let downward_ok = px < vwap
            && flow.fast_slope.unwrap_or(0.0) < -0.05
            && ob.obi_l5.unwrap_or(0.0) < -0.15
            && flow.cvd_slope.unwrap_or(0.0) < 0.0
            && ob.thin_zone_below
            && matches!(ctx.regime, Regime::TrendDown | Regime::Expansion | Regime::Chop);

        if downward_ok {
            let entry = px;
            let stop = entry + 1.0 * atr;
            let risk = stop - entry;
            if risk >= 10.0 {
                let reward = entry - target_hvn;
                let rr = reward / risk;
                if rr >= cfg.min_rr {
                    return make_signal(ctx, Side::Short, entry, stop, target_hvn, rr, 0.55);
                }
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
