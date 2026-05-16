use super::toxic_flow_gate::toxic_flow_gate;
use crate::strategy::{adapter, types::*};

pub fn detect(ctx: &StrategyMarketContext, cfg: &StrategyConfig) -> Option<StrategySignal> {
    toxic_flow_gate(ctx, cfg).ok()?;

    let px = ctx.price;
    let atr = ctx.atr.unwrap_or(0.0);
    let vp = &ctx.volume_profile;
    let vw = &ctx.vwap;
    let flow = &ctx.flow;
    let ob = &ctx.orderbook;

    let vah = vp.vah?;
    let val = vp.val?;

    // Anchor fallback: use AVWAP-BOS if established, otherwise fall back to session VWAP.
    // The label is stored in evidence so post-analysis can distinguish the two cases.
    let (long_anchor_ok, long_anchor_label) = if vw.price_vs_avwap_bos != PriceRelation::Unknown {
        (
            matches!(
                vw.price_vs_avwap_bos,
                PriceRelation::Above | PriceRelation::At
            ),
            "anchor_avwap_bos",
        )
    } else {
        (
            matches!(vw.price_vs_vwap, PriceRelation::Above | PriceRelation::At),
            "anchor_vwap_session",
        )
    };

    let (short_anchor_ok, short_anchor_label) = if vw.price_vs_avwap_bos != PriceRelation::Unknown {
        (
            matches!(
                vw.price_vs_avwap_bos,
                PriceRelation::Below | PriceRelation::At
            ),
            "anchor_avwap_bos",
        )
    } else {
        (
            matches!(vw.price_vs_vwap, PriceRelation::Below | PriceRelation::At),
            "anchor_vwap_session",
        )
    };

    // LONG: trend up, pullback into value, flow realigns
    let long_context = matches!(ctx.regime, Regime::TrendUp | Regime::Expansion)
        && long_anchor_ok
        && matches!(
            vp.value_location,
            ValueLocation::InValue | ValueLocation::BelowVal
        );

    let long_flow = flow.cvd_slope.unwrap_or(0.0) >= 0.0
        && flow.delta.unwrap_or(0.0) > 0.0
        && !flow.failed_acceptance;

    let long_book = ob.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps
        && ob.microprice.map(|m| m >= px * 0.9998).unwrap_or(true);

    if long_context && long_flow && long_book && adapter::basis_ok(flow.basis, true) {
        let entry = px;
        let stop = f64::min(val, entry - 0.75 * atr);
        let target = vah;

        if target > entry && stop < entry {
            return Some(StrategySignal {
                action: StrategyAction::ShadowSignal,
                strategy_id: Some(StrategyId::VwapValuePullbackContinuation),
                side: Some(Side::Long),
                entry_price: Some(entry),
                stop_price: Some(stop),
                target_price: Some(target),
                score: 0.0,
                ttl_ms: cfg.default_ttl_ms,
                evidence: vec![
                    "trend_up".into(),
                    long_anchor_label.into(),
                    "pullback_into_value".into(),
                    "positive_delta_reentry".into(),
                    "cvd_aligned".into(),
                ],
                missing: vec![],
                invalidation: vec![
                    "price_loses_VAL".into(),
                    "price_loses_AVWAP_BOS".into(),
                    "flow_turns_negative".into(),
                ],
                created_at_ms: ctx.timestamp_ms,
            });
        }
    }

    // SHORT: trend down, pullback into value, flow realigns.
    // Expansion is accepted symmetrically with the long side; delta/cvd_slope/value_location
    // already filter direction, so Expansion alone does not create false shorts.
    let short_context = matches!(ctx.regime, Regime::TrendDown | Regime::Expansion)
        && short_anchor_ok
        && matches!(
            vp.value_location,
            ValueLocation::InValue | ValueLocation::AboveVah
        );

    let short_flow = flow.cvd_slope.unwrap_or(0.0) <= 0.0
        && flow.delta.unwrap_or(0.0) < 0.0
        && !flow.failed_acceptance;

    let short_book = ob.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps
        && ob.microprice.map(|m| m <= px * 1.0002).unwrap_or(true);

    if short_context && short_flow && short_book && adapter::basis_ok(flow.basis, false) {
        let entry = px;
        let stop = f64::max(vah, entry + 0.75 * atr);
        let target = val;

        if target < entry && stop > entry {
            return Some(StrategySignal {
                action: StrategyAction::ShadowSignal,
                strategy_id: Some(StrategyId::VwapValuePullbackContinuation),
                side: Some(Side::Short),
                entry_price: Some(entry),
                stop_price: Some(stop),
                target_price: Some(target),
                score: 0.0,
                ttl_ms: cfg.default_ttl_ms,
                evidence: vec![
                    "trend_down".into(),
                    short_anchor_label.into(),
                    "pullback_into_value".into(),
                    "negative_delta_reentry".into(),
                    "cvd_aligned".into(),
                ],
                missing: vec![],
                invalidation: vec![
                    "price_reclaims_VAH".into(),
                    "price_reclaims_AVWAP_BOS".into(),
                    "flow_turns_positive".into(),
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

    fn base_long_ctx() -> StrategyMarketContext {
        StrategyMarketContext {
            symbol: "BTCUSDT".to_string(),
            timestamp_ms: 1710000000000,
            price: 99200.0,
            regime: Regime::TrendUp,
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
                vwap_session: Some(99300.0),
                avwap_bos: Some(99100.0),
                avwap_event: None,
                price_vs_vwap: PriceRelation::Below,
                price_vs_avwap_bos: PriceRelation::Above,
                price_vs_avwap_event: PriceRelation::Unknown,
                quality: DataQuality::Live,
            },
            flow: OrderFlowContext {
                cvd: Some(800.0),
                cvd_slope: Some(0.3),
                delta: Some(50.0),
                taker_imbalance: Some(0.08),
                buy_volume: Some(4200.0),
                sell_volume: Some(4000.0),
                vpin: Some(0.40),
                cvd_divergence: None,
                footprint_absorption: AbsorptionSide::None,
                stacked_imbalance: ImbalanceSide::None,
                failed_acceptance: false,
                sweep_confirmed: false,
                mss_active: true,
                quality: DataQuality::Live,
                funding_rate: None,
                basis: None,
                oi_delta: None,
                oi_momentum_aligned: None,
                bid_wall_nearby: false,
                ask_wall_nearby: false,
                price_action_clean: true,
            },
            orderbook: OrderBookContext {
                obi_l5: Some(0.03),
                obi_l10: Some(0.01),
                obi_l20: Some(0.0),
                microprice: Some(99210.0),
                spread_bps: Some(0.6),
                walls_above: vec![],
                walls_below: vec![98800.0],
                thin_zone_above: false,
                thin_zone_below: false,
                quality: DataQuality::Live,
            },
        }
    }

    #[test]
    fn detects_long_continuation_in_trend_up() {
        let ctx = base_long_ctx();
        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &cfg);
        assert!(signal.is_some());
        let s = signal.unwrap();
        assert_eq!(s.side, Some(Side::Long));
        assert_eq!(
            s.strategy_id,
            Some(StrategyId::VwapValuePullbackContinuation)
        );
    }

    #[test]
    fn detects_short_continuation_in_trend_down() {
        let mut ctx = base_long_ctx();
        ctx.regime = Regime::TrendDown;
        ctx.price = 100050.0;
        ctx.volume_profile.value_location = ValueLocation::AboveVah;
        ctx.vwap.price_vs_avwap_bos = PriceRelation::Below;
        ctx.flow.cvd_slope = Some(-0.4);
        ctx.flow.delta = Some(-80.0);
        ctx.orderbook.microprice = Some(100040.0);

        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &cfg);
        assert!(signal.is_some());
        let s = signal.unwrap();
        assert_eq!(s.side, Some(Side::Short));
    }

    #[test]
    fn rejects_if_wrong_side_of_avwap() {
        let mut ctx = base_long_ctx();
        ctx.vwap.price_vs_avwap_bos = PriceRelation::Below;
        let cfg = StrategyConfig::default();
        assert!(detect(&ctx, &cfg).is_none());
    }

    #[test]
    fn rejects_if_flow_contradicts() {
        let mut ctx = base_long_ctx();
        ctx.flow.delta = Some(-100.0);
        let cfg = StrategyConfig::default();
        assert!(detect(&ctx, &cfg).is_none());
    }
}
