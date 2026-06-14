use crate::strategy::types::*;

/// CVD Divergence Reversal (CDR)
///
/// Fires when price and CVD diverge persistently (>= 4 bars), signaling institutional
/// absorption: price moves in one direction while CVD contradicts it.
///
/// Methodology (Subdimi/Supreme Trading): "precio hace HH pero CVD no confirma →
/// reversión inminente. Los institucionales absorben el movimiento sin confirmar con flow."
///
/// SHORT: price sustained above VAH / near resistance but CVD flat/falling (persistence >= 4)
/// LONG:  price sustained below VAL / near support but CVD flat/rising  (persistence <= -4)
pub fn detect(ctx: &StrategyMarketContext, cfg: &StrategyConfig) -> Option<StrategySignal> {
    let px = ctx.price;
    let atr = ctx.atr.unwrap_or(0.0);
    let vp = &ctx.volume_profile;
    let flow = &ctx.flow;

    let vah = vp.vah?;
    let val = vp.val?;
    let poc = vp.poc?;

    // CVD divergence persistence: positive = bearish divergence (price↑, CVD↓/flat)
    //                              negative = bullish divergence (price↓, CVD↑/flat)
    let persist = flow.cvd_divergence_persistence?;

    const PERSIST_THRESHOLD: i32 = 4;

    // SHORT: bearish divergence — price at/near VAH or above, CVD not confirming
    if persist >= PERSIST_THRESHOLD {
        // Block in strong bull — divergence can be fake breakout absorption, not reversal
        if matches!(ctx.regime, Regime::TrendUp | Regime::Expansion) {
            return None;
        }

        // Price must be near VAH (within 1.0 ATR) — confluence with value area resistance
        let near_vah = atr <= 0.0 || (px >= vah - atr && px <= vah + 1.5 * atr);
        if !near_vah {
            return None;
        }

        // Spread gate
        if ctx.orderbook.spread_bps.unwrap_or(999.0) > cfg.max_spread_bps {
            return None;
        }

        // Entry below VAH (already failing auction), stop above recent high
        let entry = px;
        let stop = vah + 0.5 * atr;
        let target = poc;

        if target >= entry || stop <= entry {
            return None;
        }

        let risk = (stop - entry).abs();
        let reward = (target - entry).abs();
        if risk < 1e-10 || reward / risk < cfg.min_rr {
            return None;
        }

        let mut evidence = vec![
            "cvd_bearish_divergence".into(),
            format!("persist_{}_bars", persist),
            "near_VAH_resistance".into(),
            "target_POC".into(),
        ];
        if matches!(ctx.regime, Regime::TrendDown) {
            evidence.push("regime_trend_down_confirmed".into());
        }
        if matches!(flow.stacked_imbalance, ImbalanceSide::Bearish) {
            evidence.push("stacked_imbalance_bearish".into());
        }
        if flow.vpin.map(|v| v < 0.35).unwrap_or(false) {
            evidence.push("vpin_clean".into());
        }

        return Some(StrategySignal {
            action: StrategyAction::ShadowSignal,
            strategy_id: Some(StrategyId::CvdDivergenceReversal),
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
                "cvd_divergence_resolves_bullish".into(),
                "price_reclaims_above_VAH_with_volume".into(),
            ],
            created_at_ms: ctx.timestamp_ms,
        });
    }

    // LONG: bullish divergence — price at/near VAL or below, CVD not confirming
    if persist <= -PERSIST_THRESHOLD {
        // Block in strong bear — divergence can be fake breakdown, not reversal
        if matches!(ctx.regime, Regime::TrendDown) {
            return None;
        }

        // Price must be near VAL (within 1.0 ATR)
        let near_val = atr <= 0.0 || (px <= val + atr && px >= val - 1.5 * atr);
        if !near_val {
            return None;
        }

        if ctx.orderbook.spread_bps.unwrap_or(999.0) > cfg.max_spread_bps {
            return None;
        }

        let entry = px;
        let stop = val - 0.5 * atr;
        let target = poc;

        if target <= entry || stop >= entry {
            return None;
        }

        let risk = (stop - entry).abs();
        let reward = (target - entry).abs();
        if risk < 1e-10 || reward / risk < cfg.min_rr {
            return None;
        }

        let mut evidence = vec![
            "cvd_bullish_divergence".into(),
            format!("persist_{}_bars", persist.abs()),
            "near_VAL_support".into(),
            "target_POC".into(),
        ];
        if matches!(ctx.regime, Regime::TrendUp) {
            evidence.push("regime_trend_up_confirmed".into());
        }
        if matches!(flow.stacked_imbalance, ImbalanceSide::Bullish) {
            evidence.push("stacked_imbalance_bullish".into());
        }
        if flow.vpin.map(|v| v < 0.35).unwrap_or(false) {
            evidence.push("vpin_clean".into());
        }

        return Some(StrategySignal {
            action: StrategyAction::ShadowSignal,
            strategy_id: Some(StrategyId::CvdDivergenceReversal),
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
                "cvd_divergence_resolves_bearish".into(),
                "price_breaks_below_VAL_with_volume".into(),
            ],
            created_at_ms: ctx.timestamp_ms,
        });
    }

    None
}

#[cfg(test)]
mod tests {
    use super::*;

    fn base_ctx(price: f64, persist: i32, regime: Regime) -> StrategyMarketContext {
        StrategyMarketContext {
            symbol: "BTCUSDT".to_string(),
            timestamp_ms: 1710000000000,
            price,
            regime,
            atr: Some(250.0),
            volume_profile: VolumeProfileContext {
                poc: Some(100000.0),
                vah: Some(100500.0),
                val: Some(99500.0),
                hvn_nearby: vec![],
                lvn_nearby: vec![],
                value_location: ValueLocation::InValue,
                quality: DataQuality::Live,
                naked_pocs: vec![],
                single_prints: vec![],
            },
            vwap: VwapContext {
                vwap_session: Some(100000.0),
                avwap_bos: None,
                avwap_event: None,
                price_vs_vwap: PriceRelation::Above,
                price_vs_avwap_bos: PriceRelation::Unknown,
                price_vs_avwap_event: PriceRelation::Unknown,
                quality: DataQuality::Live,
            },
            flow: OrderFlowContext {
                cvd: Some(-500.0),
                cvd_slope: Some(-0.1),
                delta: Some(-50.0),
                taker_imbalance: Some(-0.02),
                buy_volume: Some(4000.0),
                sell_volume: Some(4200.0),
                vpin: Some(0.30),
                cvd_divergence: None,
                cvd_divergence_persistence: Some(persist),
                footprint_absorption: AbsorptionSide::None,
                stacked_imbalance: ImbalanceSide::None,
                failed_acceptance: false,
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
                oi_delta_zscore: None,
                vpin_cdf: None,
                finish_action_bullish: false,
                finish_action_bearish: false,
                unfinish_action_bullish: false,
                unfinish_action_bearish: false,
                big_trade_bullish: false,
                big_trade_bearish: false,
            },
            orderbook: OrderBookContext {
                obi_l5: Some(0.0),
                obi_l10: Some(0.0),
                obi_l20: Some(0.0),
                microprice: Some(price),
                spread_bps: Some(0.8),
                walls_above: vec![],
                walls_below: vec![],
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
            prev_obi_l5: None,
            slow_slope: None,
            auction_state: None,
            vp_open_bias: None,
            htf_vp: None,
        }
    }

    #[test]
    fn short_on_bearish_divergence_at_vah() {
        // persist=5, price near VAH=100500, poc=100000, regime=Chop
        let ctx = base_ctx(100400.0, 5, Regime::Chop);
        let cfg = StrategyConfig::default();
        let result = detect(&ctx, &cfg);
        assert!(result.is_some(), "expected SHORT signal");
        let s = result.unwrap();
        assert_eq!(s.side, Some(Side::Short));
        assert_eq!(s.strategy_id, Some(StrategyId::CvdDivergenceReversal));
        assert!(s.target_price.unwrap() < s.entry_price.unwrap());
        assert!(s.stop_price.unwrap() > s.entry_price.unwrap());
    }

    #[test]
    fn long_on_bullish_divergence_at_val() {
        // persist=-5, price near VAL=99500, poc=100000, regime=Chop
        let mut ctx = base_ctx(99600.0, -5, Regime::Chop);
        ctx.flow.cvd_slope = Some(0.1);
        ctx.flow.delta = Some(50.0);
        let cfg = StrategyConfig::default();
        let result = detect(&ctx, &cfg);
        assert!(result.is_some(), "expected LONG signal");
        let s = result.unwrap();
        assert_eq!(s.side, Some(Side::Long));
        assert!(s.target_price.unwrap() > s.entry_price.unwrap());
        assert!(s.stop_price.unwrap() < s.entry_price.unwrap());
    }

    #[test]
    fn no_signal_when_persistence_below_threshold() {
        let ctx = base_ctx(100400.0, 2, Regime::Chop);
        let cfg = StrategyConfig::default();
        assert!(
            detect(&ctx, &cfg).is_none(),
            "persistence=2 should not fire"
        );
    }

    #[test]
    fn no_short_in_trend_up_regime() {
        let ctx = base_ctx(100400.0, 5, Regime::TrendUp);
        let cfg = StrategyConfig::default();
        assert!(detect(&ctx, &cfg).is_none(), "SHORT blocked in TrendUp");
    }

    #[test]
    fn no_long_in_trend_down_regime() {
        let mut ctx = base_ctx(99600.0, -5, Regime::TrendDown);
        ctx.flow.cvd_slope = Some(0.1);
        let cfg = StrategyConfig::default();
        assert!(detect(&ctx, &cfg).is_none(), "LONG blocked in TrendDown");
    }

    #[test]
    fn no_signal_when_price_far_from_va() {
        // Price 3 ATR below VAH — not near VAH
        let ctx = base_ctx(99000.0, 5, Regime::Chop);
        let cfg = StrategyConfig::default();
        assert!(detect(&ctx, &cfg).is_none(), "price too far from VAH");
    }
}
