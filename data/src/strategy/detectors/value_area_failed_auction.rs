use crate::strategy::{adapter, types::*};

// Note: toxic_flow_gate is evaluated once in the router before calling any detector.
pub fn detect(ctx: &StrategyMarketContext, cfg: &StrategyConfig) -> Option<StrategySignal> {

    let px = ctx.price;
    let atr = ctx.atr.unwrap_or(0.0);
    let vp = &ctx.volume_profile;
    let flow = &ctx.flow;
    let ob = &ctx.orderbook;

    let vah = vp.vah?;
    let val = vp.val?;
    let poc = vp.poc?;

    // In Chop regime, VAFA generates more false signals (price bouncing inside VA without
    // a real acceptance attempt). Require significantly stronger confirmation.
    let chop_strict = matches!(ctx.regime, Regime::Chop);

    // SHORT: failed auction above VAH
    // Fix 2: price must still be near VAH (within 0.5 ATR), not already deep inside value area.
    // Fix 3 (delta): require delta < 0 — momentum aligned with SHORT, consistent with scorer.
    let short_location = px < vah
        && (atr <= 0.0 || px > vah - 0.5 * atr)  // fix 2: proximity to edge
        && flow.failed_acceptance
        && flow.delta.unwrap_or(0.0) < 0.0         // fix 3: aligned delta for SHORT
        && flow.footprint_absorption == AbsorptionSide::Ask;

    // In Chop: require clear directional flow (strong cvd_slope + taker_imbalance + delta magnitude).
    // Normal: requires neutral-to-bearish flow (< 0.10 taker_imbalance filters ~20% of market time).
    let short_flow = if chop_strict {
        flow.cvd_slope.unwrap_or(0.0) < -0.20
            && flow.taker_imbalance.unwrap_or(0.0) < -0.15
            && (atr <= 0.0 || flow.delta.unwrap_or(0.0) / atr < -0.35)
    } else {
        flow.cvd_slope.unwrap_or(0.0) <= 0.0 && flow.taker_imbalance.unwrap_or(0.0) < 0.10
    };

    let short_book = ob.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps && !ob.thin_zone_above;

    if short_location && short_flow && short_book && adapter::basis_ok(flow.basis, false) {
        let entry = px;
        let stop = f64::max(vah + 0.25 * atr, px + 0.5 * atr);
        let target = poc;

        // Fix 1 (R:R gate): require at least 1.5:1 reward/risk before emitting.
        let risk = (stop - entry).abs();
        let reward = (target - entry).abs();
        if target < entry && stop > entry && risk > 1e-10 && reward / risk >= 1.5 {
            let mut evidence = vec![
                "failed_acceptance_above_VAH".into(),
                "ask_absorption".into(),
                "delta_aligned_short".into(),
                "target_POC".into(),
            ];
            if chop_strict { evidence.push("chop_strict_gates_passed".into()); }
            // Fase A — OB logging (peso 0, solo evidencia)
            if let Some(ref obs) = ctx.order_blocks {
                if let Some(ref ob) = obs.nearest_bearish {
                    if (ob.high - px).abs() < atr { evidence.push("bearish_ob_nearby".into()); }
                }
            }
            return Some(StrategySignal {
                action: StrategyAction::ShadowSignal,
                strategy_id: Some(StrategyId::ValueAreaFailedAuction),
                side: Some(Side::Short),
                regime: ctx.regime,
                entry_price: Some(entry),
                stop_price: Some(stop),
                target_price: Some(target),
                score: 0.0,
                ttl_ms: cfg.default_ttl_ms,
                evidence,
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
    // Fix 2: price must still be near VAL (within 0.5 ATR).
    // Fix 3 (delta): require delta > 0 — momentum aligned with LONG, consistent with scorer.
    let long_location = px > val
        && (atr <= 0.0 || px < val + 0.5 * atr)   // fix 2: proximity to edge
        && flow.failed_acceptance
        && flow.delta.unwrap_or(0.0) > 0.0          // fix 3: aligned delta for LONG
        && flow.footprint_absorption == AbsorptionSide::Bid;

    // In Chop: mirror of short — require strong bullish conviction.
    let long_flow = if chop_strict {
        flow.cvd_slope.unwrap_or(0.0) > 0.20
            && flow.taker_imbalance.unwrap_or(0.0) > 0.15
            && (atr <= 0.0 || flow.delta.unwrap_or(0.0) / atr > 0.35)
    } else {
        flow.cvd_slope.unwrap_or(0.0) >= 0.0 && flow.taker_imbalance.unwrap_or(0.0) > -0.10
    };

    let long_book = ob.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps && !ob.thin_zone_below;

    if long_location && long_flow && long_book && adapter::basis_ok(flow.basis, true) {
        let entry = px;
        let stop = f64::min(val - 0.25 * atr, px - 0.5 * atr);
        let target = poc;

        // Fix 1 (R:R gate): require at least 1.5:1 reward/risk before emitting.
        let risk = (stop - entry).abs();
        let reward = (target - entry).abs();
        if target > entry && stop < entry && risk > 1e-10 && reward / risk >= 1.5 {
            let mut evidence = vec![
                "failed_acceptance_below_VAL".into(),
                "bid_absorption".into(),
                "delta_aligned_long".into(),
                "target_POC".into(),
            ];
            if chop_strict { evidence.push("chop_strict_gates_passed".into()); }
            // Fase A — OB logging (peso 0, solo evidencia)
            if let Some(ref obs) = ctx.order_blocks {
                if let Some(ref ob) = obs.nearest_bullish {
                    if (px - ob.low).abs() < atr { evidence.push("bullish_ob_nearby".into()); }
                }
            }
            return Some(StrategySignal {
                action: StrategyAction::ShadowSignal,
                strategy_id: Some(StrategyId::ValueAreaFailedAuction),
                side: Some(Side::Long),
                regime: ctx.regime,
                entry_price: Some(entry),
                stop_price: Some(stop),
                target_price: Some(target),
                score: 0.0,
                ttl_ms: cfg.default_ttl_ms,
                evidence,
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
        // SHORT setup: price at 100050, VAH=100100, POC=99200, ATR=250
        // proximity: 100050 > 100100 - 0.5*250 = 99975 ✓
        // delta < 0 (aligned SHORT) ✓
        // R:R: reward = 100050-99200 = 850, risk = max(100100+62.5, 100050+125) = 100175 → 125
        // R:R = 850/125 = 6.8 ✓  (>= 1.5)
        StrategyMarketContext {
            symbol: "BTCUSDT".to_string(),
            timestamp_ms: 1710000000000,
            price: 100050.0,
            regime: Regime::TrendDown, // non-Chop so basic gate thresholds apply
            atr: Some(250.0),
            volume_profile: VolumeProfileContext {
                poc: Some(99200.0), // far enough for good R:R
                vah: Some(100100.0),
                val: Some(99000.0),
                hvn_nearby: vec![99200.0],
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
                delta: Some(-80.0), // aligned SHORT (negative)
                taker_imbalance: Some(0.05), // < 0.10 threshold (neutral-to-bearish for SHORT)
                buy_volume: Some(5000.0),
                sell_volume: Some(4800.0),
                vpin: Some(0.45),
                cvd_divergence: None,
                footprint_absorption: AbsorptionSide::Ask,
                stacked_imbalance: ImbalanceSide::None,
                failed_acceptance: true,
                sweep_confirmed: false,
                mss_active: false,
                quality: DataQuality::Live,
                funding_rate: None,
                basis: None,
                oi_delta: None,
                oi_momentum_aligned: None,
                bid_wall_nearby: false,
                ask_wall_nearby: false,
                price_action_clean: true,
                fast_slope: None,
                footprint_levels: vec![],
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
                spoof: None,
            },
            institutional: None,
            swing_high_20: None,
            swing_low_20: None,
            market_structure: None,
            session: None,
            order_blocks: None,
            fvg: None,
            leverage: 1.0,
        }
    }

    #[test]
    fn detects_short_failed_auction_above_vah() {
        let ctx = base_ctx();
        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &cfg);
        assert!(
            signal.is_some(),
            "expected SHORT signal with valid R:R and aligned delta"
        );
        let s = signal.unwrap();
        assert_eq!(s.side, Some(Side::Short));
        assert_eq!(s.strategy_id, Some(StrategyId::ValueAreaFailedAuction));
        assert!(s.target_price.unwrap() < s.entry_price.unwrap());
        // R:R >= 1.5
        let risk = (s.stop_price.unwrap() - s.entry_price.unwrap()).abs();
        let reward = (s.target_price.unwrap() - s.entry_price.unwrap()).abs();
        assert!(
            reward / risk >= 1.5,
            "R:R must be >= 1.5, got {:.2}",
            reward / risk
        );
    }

    #[test]
    fn detects_long_failed_auction_below_val() {
        let mut ctx = base_ctx();
        // LONG setup: price just above VAL, POC above, delta positive (aligned LONG)
        ctx.price = 99050.0; // close to VAL=99000, within 0.5*ATR=125
        ctx.flow.delta = Some(80.0); // aligned LONG (positive)
        ctx.flow.footprint_absorption = AbsorptionSide::Bid;
        ctx.flow.cvd_slope = Some(0.2);
        ctx.volume_profile.poc = Some(99800.0); // far enough: reward=750, risk≈125 → R:R=6

        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &cfg);
        assert!(
            signal.is_some(),
            "expected LONG signal with valid R:R and aligned delta"
        );
        let s = signal.unwrap();
        assert_eq!(s.side, Some(Side::Long));
        assert!(s.target_price.unwrap() > s.entry_price.unwrap());
        let risk = (s.stop_price.unwrap() - s.entry_price.unwrap()).abs();
        let reward = (s.target_price.unwrap() - s.entry_price.unwrap()).abs();
        assert!(
            reward / risk >= 1.5,
            "R:R must be >= 1.5, got {:.2}",
            reward / risk
        );
    }

    #[test]
    fn rejects_misaligned_delta_short() {
        let mut ctx = base_ctx();
        ctx.flow.delta = Some(120.0); // positive delta = wrong direction for SHORT
        let cfg = StrategyConfig::default();
        assert!(
            detect(&ctx, &cfg).is_none(),
            "positive delta should reject SHORT signal"
        );
    }

    #[test]
    fn rejects_misaligned_delta_long() {
        let mut ctx = base_ctx();
        ctx.price = 99050.0;
        ctx.flow.delta = Some(-80.0); // negative delta = wrong direction for LONG
        ctx.flow.footprint_absorption = AbsorptionSide::Bid;
        ctx.flow.cvd_slope = Some(0.2);
        ctx.volume_profile.poc = Some(99800.0);
        let cfg = StrategyConfig::default();
        assert!(
            detect(&ctx, &cfg).is_none(),
            "negative delta should reject LONG signal"
        );
    }

    #[test]
    fn rejects_price_too_far_from_edge() {
        let mut ctx = base_ctx();
        // price already deep inside value area, > 0.5 ATR below VAH
        ctx.price = 99800.0; // VAH=100100, ATR=250 → threshold = 100100-125 = 99975; 99800 < 99975
        let cfg = StrategyConfig::default();
        assert!(
            detect(&ctx, &cfg).is_none(),
            "stale entry too far from VAH should be rejected"
        );
    }

    #[test]
    fn rejects_poor_rr() {
        let mut ctx = base_ctx();
        // POC very close to entry → R:R < 1.5
        ctx.volume_profile.poc = Some(100000.0); // only 50 points from entry=100050, stop≈100175
        let cfg = StrategyConfig::default();
        assert!(detect(&ctx, &cfg).is_none(), "R:R < 1.5 should be rejected");
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
