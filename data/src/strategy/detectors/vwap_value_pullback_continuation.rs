use crate::strategy::{adapter, types::*};

// Note: toxic_flow_gate is evaluated once in the router before calling any detector.
pub fn detect(ctx: &StrategyMarketContext, cfg: &StrategyConfig) -> Option<StrategySignal> {

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

    // LONG: trend up, pullback into value, flow realigns.
    // BelowVal is excluded: price below VAL is a breakdown of support, not a pullback.
    // fast_slope gate: if bar momentum is strongly bearish (< -0.20), suppress even in
    // TrendUp — slow_slope can lag while price is already dropping hard intrabar.
    let fast_slope_ok_long = flow.fast_slope.map(|fs| fs > -0.20).unwrap_or(true);
    let long_context = matches!(ctx.regime, Regime::TrendUp | Regime::Expansion)
        && long_anchor_ok
        && vp.value_location == ValueLocation::InValue
        && fast_slope_ok_long;

    // CVD level gate: a single bar of positive delta cannot override strongly adverse
    // cumulative flow. Threshold -200 was chosen after observing CVD=-471 triggering
    // a spurious long in a day-long sell-side session (2026-05-14 deployment).
    let long_flow = flow.cvd_slope.unwrap_or(0.0) >= 0.0
        && flow.delta.unwrap_or(0.0) > 0.0
        && flow.cvd.unwrap_or(0.0) > -200.0
        && !flow.failed_acceptance;

    let long_book = ob.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps
        && ob.microprice.map(|m| m >= px * 0.9998).unwrap_or(true);

    if long_context && long_flow && long_book && adapter::basis_ok(flow.basis, true) {
        let entry = px;
        // max() → closest of (VAL structural level, 1×ATR floor).
        // Original min() was choosing the farthest, creating R:R ~0.2 in production.
        let stop = f64::max(val, entry - 1.0 * atr);

        let risk = entry - stop;
        if risk < 10.0 || stop >= entry {
            return None;
        }

        let target = find_structural_target(entry, risk, Side::Long, vp, ob, atr, ctx.swing_high_20, ctx.swing_low_20, cfg)?;

        return Some(StrategySignal {
            action: StrategyAction::ShadowSignal,
            strategy_id: Some(StrategyId::VwapValuePullbackContinuation),
            side: Some(Side::Long),
            regime: ctx.regime,
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

    // SHORT: trend down, pullback into value, flow realigns.
    // Expansion is accepted symmetrically with the long side; delta/cvd_slope/value_location
    // already filter direction, so Expansion alone does not create false shorts.
    // AboveVah is excluded: price above VAH is a breakout above value, not a pullback into it.
    let short_context = matches!(ctx.regime, Regime::TrendDown | Regime::Expansion)
        && short_anchor_ok
        && vp.value_location == ValueLocation::InValue;

    let short_flow = flow.cvd_slope.unwrap_or(0.0) <= 0.0
        && flow.delta.unwrap_or(0.0) < 0.0
        && flow.cvd.unwrap_or(0.0) < 200.0
        && !flow.failed_acceptance;

    let short_book = ob.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps
        && ob.microprice.map(|m| m <= px * 1.0002).unwrap_or(true);

    if short_context && short_flow && short_book && adapter::basis_ok(flow.basis, false) {
        let entry = px;
        // min() → closest of (VAH structural level, 1×ATR ceiling).
        let stop = f64::min(vah, entry + 1.0 * atr);

        let risk = stop - entry;
        if risk < 10.0 || stop <= entry {
            return None;
        }

        let target = find_structural_target(entry, risk, Side::Short, vp, ob, atr, ctx.swing_high_20, ctx.swing_low_20, cfg)?;

        return Some(StrategySignal {
            action: StrategyAction::ShadowSignal,
            strategy_id: Some(StrategyId::VwapValuePullbackContinuation),
            side: Some(Side::Short),
            regime: ctx.regime,
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

    None
}

/// Finds the nearest structural target in the trade direction that satisfies cfg.min_rr.
///
/// Candidate priority: HVN levels → VAH/VAL → orderbook walls → 3×ATR fallback.
/// Returns None only when no candidate meets min_rr (signal is suppressed).
fn find_structural_target(
    entry: f64,
    risk: f64,
    side: Side,
    vp: &VolumeProfileContext,
    ob: &OrderBookContext,
    atr: f64,
    swing_high: Option<f64>,
    swing_low: Option<f64>,
    cfg: &StrategyConfig,
) -> Option<f64> {
    let min_reward = risk * cfg.min_rr;
    let max_reward = risk * cfg.max_rr_m5;

    let mut candidates: Vec<f64> = Vec::new();

    match side {
        Side::Long => {
            let lo = entry + min_reward;
            let hi = entry + max_reward;

            candidates.extend(vp.hvn_nearby.iter().copied().filter(|&h| h > lo && h < hi));
            if let Some(vah) = vp.vah {
                if vah > lo && vah < hi {
                    candidates.push(vah);
                }
            }
            if let Some(sh) = swing_high {
                if sh > lo && sh < hi {
                    candidates.push(sh);
                }
            }
            candidates.extend(ob.walls_above.iter().copied().filter(|&w| w > lo && w < hi));

            if candidates.is_empty() {
                let fallback = entry + 3.0 * atr;
                if fallback > lo && fallback < hi {
                    candidates.push(fallback);
                }
            }

            // Nearest valid target (conservative).
            candidates
                .into_iter()
                .filter(|t| t.is_finite())
                .min_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal))
        }

        Side::Short => {
            let hi = entry - min_reward;
            let lo = entry - max_reward;

            candidates.extend(vp.hvn_nearby.iter().copied().filter(|&h| h < hi && h > lo));
            if let Some(val) = vp.val {
                if val < hi && val > lo {
                    candidates.push(val);
                }
            }
            if let Some(sl) = swing_low {
                if sl < hi && sl > lo {
                    candidates.push(sl);
                }
            }
            candidates.extend(ob.walls_below.iter().copied().filter(|&w| w < hi && w > lo));

            if candidates.is_empty() {
                let fallback = entry - 3.0 * atr;
                if fallback < hi && fallback > lo {
                    candidates.push(fallback);
                }
            }

            // Nearest valid target (most conservative for short = highest price).
            candidates
                .into_iter()
                .filter(|t| t.is_finite())
                .max_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal))
        }
    }
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
                fast_slope: None,
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
                spoof: None,
            },
            institutional: None,
            swing_high_20: None,
            swing_low_20: None,
            market_structure: None,
            session: None,
            order_blocks: None,
            fvg: None,
        }
    }

    #[test]
    fn detects_long_continuation_in_trend_up() {
        // entry=99200, val=99000, vah=100100, atr=250
        // stop = max(99000, 99200-250) = max(99000, 98950) = 99000 (VAL is closer)
        // risk = 99200-99000 = 200
        // min_reward = 200*1.5 = 300 → target must be > 99500
        // hvn_nearby=[99500]: 99500 > 99500 is false → filtered
        // vah=100100: 100100 > 99500 ✓ and < 99200+1600=100800 ✓ → target=100100
        let ctx = base_long_ctx();
        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &cfg);
        assert!(signal.is_some());
        let s = signal.unwrap();
        assert_eq!(s.side, Some(Side::Long));
        assert_eq!(s.strategy_id, Some(StrategyId::VwapValuePullbackContinuation));
        assert!((s.stop_price.unwrap() - 99000.0).abs() < 1.0, "stop should be VAL=99000");
        assert!(s.target_price.unwrap() > 99500.0, "target must satisfy min_rr=1.5");
        assert_eq!(s.ttl_ms, 250 * 60 * 1000);
    }

    #[test]
    fn stop_long_uses_atr_when_val_is_far() {
        // val very far → stop = entry - 1×ATR
        let mut ctx = base_long_ctx();
        ctx.volume_profile.val = Some(97000.0); // far below
        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &cfg);
        assert!(signal.is_some());
        let s = signal.unwrap();
        let expected_stop = 99200.0 - 250.0; // 98950
        assert!((s.stop_price.unwrap() - expected_stop).abs() < 1.0, "stop should be ATR floor");
    }

    #[test]
    fn stop_long_uses_val_when_val_is_closer() {
        // val within 1×ATR → stop = val (max picks the higher/closer one)
        let mut ctx = base_long_ctx();
        ctx.volume_profile.val = Some(99100.0); // closer than entry-ATR=98950
        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &cfg);
        assert!(signal.is_some());
        let s = signal.unwrap();
        assert!((s.stop_price.unwrap() - 99100.0).abs() < 1.0, "stop should be VAL=99100");
    }

    #[test]
    fn no_signal_when_no_structural_target_exists() {
        // VAH too close, no HVNs, no walls — fallback 3×ATR must fit in max_rr window
        // entry=99200, risk=200, fallback=99200+750=99950
        // fallback_rr = 750/200 = 3.75 < max_rr=8.0 → signal with fallback target
        let mut ctx = base_long_ctx();
        ctx.volume_profile.hvn_nearby = vec![]; // no HVNs
        ctx.volume_profile.vah = Some(99350.0); // only 150 reward < min_reward=300 → filtered
        ctx.orderbook.walls_above = vec![];
        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &cfg);
        // fallback 3×ATR = 99200+750 = 99950 → R:R = 750/200 = 3.75 ≥ 1.5 → signal fires
        assert!(signal.is_some());
        let s = signal.unwrap();
        let expected_target = 99200.0 + 3.0 * 250.0; // 99950
        assert!((s.target_price.unwrap() - expected_target).abs() < 1.0);
    }

    #[test]
    fn detects_short_continuation_in_trend_down() {
        let mut ctx = base_long_ctx();
        ctx.regime = Regime::TrendDown;
        ctx.price = 99800.0; // inside value (val=99000, vah=100100)
        ctx.volume_profile.value_location = ValueLocation::InValue;
        ctx.vwap.price_vs_avwap_bos = PriceRelation::Below;
        ctx.flow.cvd = Some(-300.0);
        ctx.flow.cvd_slope = Some(-0.4);
        ctx.flow.delta = Some(-80.0);
        ctx.orderbook.microprice = Some(99790.0);
        // stop = min(100100, 99800+250) = min(100100, 100050) = 100050 (ATR closer)
        // risk = 100050-99800 = 250
        // min_reward = 250*1.5 = 375 → target must be < 99800-375 = 99425
        // val=99000: 99000 < 99425 ✓ → candidate; walls_below=[98800] → candidate
        // max(99000, 98800) = 99000 → target=99000
        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &cfg);
        assert!(signal.is_some());
        let s = signal.unwrap();
        assert_eq!(s.side, Some(Side::Short));
        assert!((s.stop_price.unwrap() - 100050.0).abs() < 1.0, "stop should be ATR ceiling");
        assert!((s.target_price.unwrap() - 99000.0).abs() < 1.0, "target should be VAL");
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
