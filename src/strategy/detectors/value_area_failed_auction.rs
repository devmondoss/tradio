use crate::strategy::types::*;
use super::toxic_flow_gate::toxic_flow_gate;

pub fn detect(ctx: &StrategyMarketContext, cfg: &StrategyConfig) -> Option<StrategySignal> {
    toxic_flow_gate(ctx, cfg).ok()?;

    let px = ctx.price;
    let atr = ctx.atr.unwrap_or(0.0);
    let vp = &ctx.volume_profile;
    let flow = &ctx.flow;
    let ob = &ctx.orderbook;

    let vah = vp.vah?;
    let val = vp.val?;
    let poc = vp.poc?;

    // SHORT: failed auction above VAH
    let short_location = px < vah
        && flow.failed_acceptance
        && flow.delta.unwrap_or(0.0) > 0.0
        && flow.footprint_absorption == AbsorptionSide::Ask;

    let short_flow =
        flow.cvd_slope.unwrap_or(0.0) <= 0.0 && flow.taker_imbalance.unwrap_or(0.0) < 0.25;

    let short_book =
        ob.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps && !ob.thin_zone_above;

    if short_location && short_flow && short_book {
        let entry = px;
        let stop = f64::max(vah + 0.25 * atr, px + 0.5 * atr);
        let target = poc;

        if target < entry && stop > entry {
            return Some(StrategySignal {
                action: StrategyAction::ShadowSignal,
                strategy_id: Some(StrategyId::ValueAreaFailedAuction),
                side: Some(Side::Short),
                entry_price: Some(entry),
                stop_price: Some(stop),
                target_price: Some(target),
                score: 0.0,
                ttl_ms: cfg.default_ttl_ms,
                evidence: vec![
                    "failed_acceptance_above_VAH".into(),
                    "ask_absorption".into(),
                    "cvd_not_confirming_breakout".into(),
                    "target_POC".into(),
                ],
                missing: vec![],
                invalidation: vec![
                    "price_reclaims_above_failed_auction_high".into(),
                    "vpin_becomes_toxic".into(),
                ],
                created_at_ms: ctx.timestamp_ms,
            });
        }
    }

    // LONG: failed auction below VAL
    let long_location = px > val
        && flow.failed_acceptance
        && flow.delta.unwrap_or(0.0) < 0.0
        && flow.footprint_absorption == AbsorptionSide::Bid;

    let long_flow =
        flow.cvd_slope.unwrap_or(0.0) >= 0.0 && flow.taker_imbalance.unwrap_or(0.0) > -0.25;

    let long_book =
        ob.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps && !ob.thin_zone_below;

    if long_location && long_flow && long_book {
        let entry = px;
        let stop = f64::min(val - 0.25 * atr, px - 0.5 * atr);
        let target = poc;

        if target > entry && stop < entry {
            return Some(StrategySignal {
                action: StrategyAction::ShadowSignal,
                strategy_id: Some(StrategyId::ValueAreaFailedAuction),
                side: Some(Side::Long),
                entry_price: Some(entry),
                stop_price: Some(stop),
                target_price: Some(target),
                score: 0.0,
                ttl_ms: cfg.default_ttl_ms,
                evidence: vec![
                    "failed_acceptance_below_VAL".into(),
                    "bid_absorption".into(),
                    "cvd_not_confirming_breakdown".into(),
                    "target_POC".into(),
                ],
                missing: vec![],
                invalidation: vec![
                    "price_loses_below_failed_auction_low".into(),
                    "vpin_becomes_toxic".into(),
                ],
                created_at_ms: ctx.timestamp_ms,
            });
        }
    }

    None
}

#[cfg(test)]
mod tests {
    use super::*;

    fn base_ctx() -> StrategyMarketContext {
        StrategyMarketContext {
            symbol: "BTCUSDT".to_string(),
            timestamp_ms: 1710000000000,
            price: 100050.0,
            regime: Regime::Chop,
            atr: Some(250.0),
            volume_profile: VolumeProfileContext {
                poc: Some(99500.0),
                vah: Some(100100.0),
                val: Some(99000.0),
                hvn_nearby: vec![99500.0],
                lvn_nearby: vec![],
                value_location: ValueLocation::InValue,
                quality: DataQuality::Live,
            },
            vwap: VwapContext {
                vwap_session: Some(99950.0),
                avwap_bos: None,
                avwap_event: None,
                price_vs_vwap: PriceRelation::Above,
                price_vs_avwap_bos: PriceRelation::Unknown,
                price_vs_avwap_event: PriceRelation::Unknown,
                quality: DataQuality::Live,
            },
            flow: OrderFlowContext {
                cvd: Some(1000.0),
                cvd_slope: Some(-0.2),
                delta: Some(120.0),
                taker_imbalance: Some(0.10),
                buy_volume: Some(5000.0),
                sell_volume: Some(4800.0),
                vpin: Some(0.45),
                footprint_absorption: AbsorptionSide::Ask,
                stacked_imbalance: ImbalanceSide::None,
                failed_acceptance: true,
                sweep_confirmed: false,
                mss_active: false,
                quality: DataQuality::Live,
            },
            orderbook: OrderBookContext {
                obi_l5: Some(-0.05),
                obi_l10: Some(-0.02),
                obi_l20: Some(0.01),
                microprice: Some(100040.0),
                spread_bps: Some(0.8),
                walls_above: vec![],
                walls_below: vec![99200.0],
                thin_zone_above: false,
                thin_zone_below: false,
                quality: DataQuality::Live,
            },
        }
    }

    #[test]
    fn detects_short_failed_auction_above_vah() {
        let ctx = base_ctx();
        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &cfg);
        assert!(signal.is_some());
        let s = signal.unwrap();
        assert_eq!(s.side, Some(Side::Short));
        assert_eq!(s.strategy_id, Some(StrategyId::ValueAreaFailedAuction));
        assert!(s.target_price.unwrap() < s.entry_price.unwrap());
    }

    #[test]
    fn detects_long_failed_auction_below_val() {
        let mut ctx = base_ctx();
        ctx.price = 99050.0;
        ctx.flow.delta = Some(-150.0);
        ctx.flow.footprint_absorption = AbsorptionSide::Bid;
        ctx.flow.cvd_slope = Some(0.1);

        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &cfg);
        assert!(signal.is_some());
        let s = signal.unwrap();
        assert_eq!(s.side, Some(Side::Long));
        assert!(s.target_price.unwrap() > s.entry_price.unwrap());
    }

    #[test]
    fn rejects_if_cvd_confirms_breakout() {
        let mut ctx = base_ctx();
        ctx.flow.cvd_slope = Some(0.8);
        let cfg = StrategyConfig::default();
        assert!(detect(&ctx, &cfg).is_none());
    }

    #[test]
    fn rejects_if_thin_zone_in_breakout_direction() {
        let mut ctx = base_ctx();
        ctx.orderbook.thin_zone_above = true;
        let cfg = StrategyConfig::default();
        assert!(detect(&ctx, &cfg).is_none());
    }
}
