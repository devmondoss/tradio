use super::toxic_flow_gate::toxic_flow_gate;
use crate::strategy::types::*;

const MAX_STOP_ATR_MULT: f64 = 1.5;
const MIN_RR: f64 = 1.0;

fn nearest_above(levels: &[f64], price: f64) -> Option<f64> {
    levels
        .iter()
        .copied()
        .filter(|x| *x > price)
        .min_by(|a, b| a.partial_cmp(b).unwrap())
}

fn nearest_below(levels: &[f64], price: f64) -> Option<f64> {
    levels
        .iter()
        .copied()
        .filter(|x| *x < price)
        .max_by(|a, b| a.partial_cmp(b).unwrap())
}

pub fn detect(ctx: &StrategyMarketContext, cfg: &StrategyConfig) -> Option<StrategySignal> {
    toxic_flow_gate(ctx, cfg).ok()?;

    let px = ctx.price;
    let atr = ctx.atr.unwrap_or(0.0);
    let vp = &ctx.volume_profile;
    let vw = &ctx.vwap;
    let flow = &ctx.flow;
    let ob = &ctx.orderbook;

    // LONG: thin zone above, flow confirms
    let long_location = ob.thin_zone_above
        && matches!(vw.price_vs_vwap, PriceRelation::Above | PriceRelation::At)
        && matches!(
            vp.value_location,
            ValueLocation::InValue | ValueLocation::AboveVah
        );

    let long_flow = flow.delta.unwrap_or(0.0) > 0.0
        && flow.cvd_slope.unwrap_or(0.0) > 0.0
        && matches!(
            flow.stacked_imbalance,
            ImbalanceSide::Bullish | ImbalanceSide::None | ImbalanceSide::Unknown
        )
        && flow.taker_imbalance.unwrap_or(0.0).abs() < 0.90;

    let long_book = ob.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps
        && ob.microprice.map(|m| m >= px).unwrap_or(true);

    if long_location && long_flow && long_book {
        let mut targets = vp.hvn_nearby.clone();
        if let Some(vah) = vp.vah {
            targets.push(vah);
        }

        if let Some(target) = nearest_above(&targets, px) {
            let entry = px;
            let stop_anchor = vw.vwap_session.unwrap_or(px);
            let stop = f64::max(
                f64::min(stop_anchor, entry - 0.75 * atr),
                entry - MAX_STOP_ATR_MULT * atr,
            );

            if target > entry && stop < entry {
                let risk = entry - stop;
                let reward = target - entry;
                if reward / risk < MIN_RR {
                    return None;
                }
                return Some(StrategySignal {
                    action: StrategyAction::ShadowSignal,
                    strategy_id: Some(StrategyId::LvnLiquidityVacuumBreakout),
                    side: Some(Side::Long),
                    entry_price: Some(entry),
                    stop_price: Some(stop),
                    target_price: Some(target),
                    score: 0.0,
                    ttl_ms: cfg.default_ttl_ms,
                    evidence: vec![
                        "thin_zone_above".into(),
                        "vwap_reclaim_or_above".into(),
                        "positive_delta".into(),
                        "cvd_positive".into(),
                        "target_next_HVN_or_VAH".into(),
                    ],
                    missing: vec![],
                    invalidation: vec![
                        "price_loses_VWAP".into(),
                        "cvd_turns_negative".into(),
                        "spread_expands".into(),
                    ],
                    created_at_ms: ctx.timestamp_ms,
                });
            }
        }
    }

    // SHORT: thin zone below, flow confirms
    let short_location = ob.thin_zone_below
        && matches!(vw.price_vs_vwap, PriceRelation::Below | PriceRelation::At)
        && matches!(
            vp.value_location,
            ValueLocation::InValue | ValueLocation::BelowVal
        );

    let short_flow = flow.delta.unwrap_or(0.0) < 0.0
        && flow.cvd_slope.unwrap_or(0.0) < 0.0
        && matches!(
            flow.stacked_imbalance,
            ImbalanceSide::Bearish | ImbalanceSide::None | ImbalanceSide::Unknown
        )
        && flow.taker_imbalance.unwrap_or(0.0).abs() < 0.90;

    let short_book = ob.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps
        && ob.microprice.map(|m| m <= px).unwrap_or(true);

    if short_location && short_flow && short_book {
        let mut targets = vp.hvn_nearby.clone();
        if let Some(val) = vp.val {
            targets.push(val);
        }

        if let Some(target) = nearest_below(&targets, px) {
            let entry = px;
            let stop_anchor = vw.vwap_session.unwrap_or(px);
            let stop = f64::min(
                f64::max(stop_anchor, entry + 0.75 * atr),
                entry + MAX_STOP_ATR_MULT * atr,
            );

            if target < entry && stop > entry {
                let risk = stop - entry;
                let reward = entry - target;
                if reward / risk < MIN_RR {
                    return None;
                }
                return Some(StrategySignal {
                    action: StrategyAction::ShadowSignal,
                    strategy_id: Some(StrategyId::LvnLiquidityVacuumBreakout),
                    side: Some(Side::Short),
                    entry_price: Some(entry),
                    stop_price: Some(stop),
                    target_price: Some(target),
                    score: 0.0,
                    ttl_ms: cfg.default_ttl_ms,
                    evidence: vec![
                        "thin_zone_below".into(),
                        "vwap_loss_or_below".into(),
                        "negative_delta".into(),
                        "cvd_negative".into(),
                        "target_next_HVN_or_VAL".into(),
                    ],
                    missing: vec![],
                    invalidation: vec![
                        "price_reclaims_VWAP".into(),
                        "cvd_turns_positive".into(),
                        "spread_expands".into(),
                    ],
                    created_at_ms: ctx.timestamp_ms,
                });
            }
        }
    }

    None
}

#[cfg(test)]
mod tests {
    use super::*;

    fn base_long_ctx() -> StrategyMarketContext {
        StrategyMarketContext {
            symbol: "ETHUSDT".to_string(),
            timestamp_ms: 1710000000000,
            price: 3500.0,
            regime: Regime::Expansion,
            atr: Some(30.0),
            volume_profile: VolumeProfileContext {
                poc: Some(3480.0),
                vah: Some(3550.0),
                val: Some(3420.0),
                hvn_nearby: vec![3550.0, 3600.0],
                lvn_nearby: vec![3510.0],
                value_location: ValueLocation::InValue,
                quality: DataQuality::Live,
            },
            vwap: VwapContext {
                vwap_session: Some(3470.0),
                avwap_bos: Some(3460.0),
                avwap_event: None,
                price_vs_vwap: PriceRelation::Above,
                price_vs_avwap_bos: PriceRelation::Above,
                price_vs_avwap_event: PriceRelation::Unknown,
                quality: DataQuality::Live,
            },
            flow: OrderFlowContext {
                cvd: Some(2000.0),
                cvd_slope: Some(0.6),
                delta: Some(300.0),
                taker_imbalance: Some(0.20),
                buy_volume: Some(8000.0),
                sell_volume: Some(7200.0),
                vpin: Some(0.35),
                cvd_divergence: None,
                footprint_absorption: AbsorptionSide::None,
                stacked_imbalance: ImbalanceSide::Bullish,
                failed_acceptance: false,
                sweep_confirmed: false,
                mss_active: false,
                quality: DataQuality::Live,
            },
            orderbook: OrderBookContext {
                obi_l5: Some(0.10),
                obi_l10: Some(0.06),
                obi_l20: Some(0.03),
                microprice: Some(3502.0),
                spread_bps: Some(0.5),
                walls_above: vec![],
                walls_below: vec![3400.0],
                thin_zone_above: true,
                thin_zone_below: false,
                quality: DataQuality::Live,
            },
        }
    }

    #[test]
    fn detects_long_thin_zone_breakout() {
        let ctx = base_long_ctx();
        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &cfg);
        assert!(signal.is_some());
        let s = signal.unwrap();
        assert_eq!(s.side, Some(Side::Long));
        assert_eq!(s.strategy_id, Some(StrategyId::LvnLiquidityVacuumBreakout));
        assert!(s.target_price.unwrap() > s.entry_price.unwrap());
    }

    #[test]
    fn detects_short_thin_zone_breakout() {
        let mut ctx = base_long_ctx();
        ctx.price = 3430.0;
        ctx.orderbook.thin_zone_above = false;
        ctx.orderbook.thin_zone_below = true;
        ctx.orderbook.microprice = Some(3428.0);
        ctx.vwap.price_vs_vwap = PriceRelation::Below;
        // Bring vwap_session closer so the capped stop gives risk ≈ 30 (=1 ATR).
        // With vwap_session=3460 and atr=30: stop = min(max(3460, 3452.5), 3475) = 3460, risk=30.
        ctx.vwap.vwap_session = Some(3460.0);
        ctx.volume_profile.value_location = ValueLocation::BelowVal;
        // Set val below entry so target = 3390, reward = 40 → R:R = 40/30 = 1.33 ≥ 1.0.
        ctx.volume_profile.val = Some(3390.0);
        ctx.flow.delta = Some(-250.0);
        ctx.flow.cvd_slope = Some(-0.5);
        ctx.flow.stacked_imbalance = ImbalanceSide::Bearish;

        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &cfg);
        assert!(signal.is_some());
        let s = signal.unwrap();
        assert_eq!(s.side, Some(Side::Short));
    }

    #[test]
    fn rejects_if_no_thin_zone() {
        let mut ctx = base_long_ctx();
        ctx.orderbook.thin_zone_above = false;
        let cfg = StrategyConfig::default();
        assert!(detect(&ctx, &cfg).is_none());
    }

    #[test]
    fn rejects_if_cvd_contradicts() {
        let mut ctx = base_long_ctx();
        ctx.flow.cvd_slope = Some(-0.3);
        let cfg = StrategyConfig::default();
        assert!(detect(&ctx, &cfg).is_none());
    }
}
