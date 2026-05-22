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
    //
    // fast_slope gate: require mild positive momentum (> -0.05) but block exhaustion (>= 0.50).
    // Tightened lower bound from -0.08 to -0.05 for symmetry with short gate (-0.10).
    // Audit 2026-05-20: fast_slope=0.924/1.114 produced 0% win-rate longs (exhaustion).
    let fast_slope_ok_long = flow
        .fast_slope
        .map(|fs| fs > -0.05 && fs < 0.50)
        .unwrap_or(true);

    // Slow slope strength gate: avoid entering trend-continuation when slope is marginal.
    // A slope barely above the 0.10 entry threshold is chop — the regime could flip in 1-2 bars.
    // Require slow_slope > 0.20 so we're firmly in trend, not at the chop/trend boundary.
    let slow_slope_ok_long = ctx.slow_slope.map(|s| s > 0.20).unwrap_or(true);

    // VAL proximity gate: "in value" at the midpoint is NOT a pullback.
    // Require price to be in the lower 40% of the value range (closer to VAL = structural support).
    // At 21:30 the rejected trade had price at 55% of range — this gate blocks that case.
    let val_proximity_ok_long = match (vp.val, vp.vah) {
        (Some(val_level), Some(vah_level)) => {
            let range = vah_level - val_level;
            range > 0.0 && (px - val_level) / range <= 0.40
        }
        _ => true, // no VP data → don't block
    };

    let long_context = matches!(ctx.regime, Regime::TrendUp | Regime::Expansion)
        && long_anchor_ok
        && vp.value_location == ValueLocation::InValue
        && fast_slope_ok_long
        && slow_slope_ok_long
        && val_proximity_ok_long;

    // CVD level gate: a single bar of positive delta cannot override strongly adverse
    // cumulative flow. Threshold -200 was chosen after observing CVD=-471 triggering
    // a spurious long in a day-long sell-side session (2026-05-14 deployment).
    let long_flow = flow.cvd_slope.unwrap_or(0.0) >= 0.0
        && flow.delta.unwrap_or(0.0) > 0.0
        && flow.cvd.unwrap_or(0.0) > -200.0
        && flow.taker_imbalance.unwrap_or(0.0) > 0.0
        && !flow.failed_acceptance;

    let long_book = ob.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps
        && ob.microprice.map(|m| m >= px * 0.9998).unwrap_or(true)
        && ob.obi_l5.unwrap_or(0.0) >= 0.0;

    // Funding gate: block longs when funding is elevated/extreme long side.
    // If institutional data is absent, block by default (conservative — feed failure
    // should not permit long entries into potentially overleveraged markets).
    let funding_ok_long = ctx
        .institutional
        .as_ref()
        .map(|inst| {
            !matches!(
                inst.funding.regime,
                crate::institutional::FundingRegime::ExtremeLong
                    | crate::institutional::FundingRegime::ElevatedLong
            )
        })
        .unwrap_or(false);

    if long_context
        && long_flow
        && long_book
        && funding_ok_long
        && adapter::basis_ok(flow.basis, true)
    {
        let entry = px;
        // max() → closest of (VAL structural level, 1×ATR floor).
        // Original min() was choosing the farthest, creating R:R ~0.2 in production.
        let stop = f64::max(val, entry - 1.0 * atr);

        let risk = entry - stop;
        if risk < 10.0 || stop >= entry {
            return None;
        }

        let target = find_structural_target(
            entry,
            risk,
            Side::Long,
            vp,
            ob,
            atr,
            ctx.swing_high_20,
            ctx.swing_low_20,
            cfg,
        )?;

        let mut evidence = vec![
            "trend_up".into(),
            long_anchor_label.into(),
            "pullback_into_value".into(),
            "positive_delta_reentry".into(),
            "cvd_aligned".into(),
            "taker_imbalance_positive".into(),
            "obi_l5_positive".into(),
        ];
        // Fase A — OB logging (peso 0, solo evidencia)
        if let Some(ref obs) = ctx.order_blocks {
            if obs.nearest_bullish.is_some() {
                evidence.push("bullish_ob_in_pullback_zone".into());
            }
        }
        if let Some(ref fvg_ctx) = ctx.fvg {
            if fvg_ctx.nearest_bullish.is_some() {
                evidence.push("bullish_fvg_nearby".into());
            }
        }
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
            evidence,
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
    // fast_slope gate: require bearish bar momentum (<= -0.10). Audit 2026-05-19:
    // fast_slope=-0.067 passed the old gate (< 0.08) producing 4 losing short signals.
    //
    // Bearish divergence in TrendUp: the 14-bar OLS can stay positive while the intraday
    // flow is clearly bearish (CVD slope negative, price below session VWAP). This happens
    // when price makes slightly higher lows on the 70-min window but sustained selling pressure
    // continues — a "fake recovery" that resolves to the downside. All short_flow and
    // fast_slope guards still apply; this only relaxes the regime gate.
    let bearish_divergence_in_uptrend = ctx.regime == Regime::TrendUp
        && matches!(vw.price_vs_vwap, PriceRelation::Below)
        && flow.cvd_slope.map(|s| s < 0.0).unwrap_or(false);

    let fast_slope_ok_short = flow.fast_slope.map(|fs| fs <= -0.10).unwrap_or(true);

    // Slow slope strength gate: symmetric with long gate — require slope < -0.20 for shorts.
    // bearish_divergence_in_uptrend bypasses this since it's a divergence scenario, not pure trend.
    let slow_slope_ok_short = if bearish_divergence_in_uptrend {
        true
    } else {
        ctx.slow_slope.map(|s| s < -0.20).unwrap_or(true)
    };

    // VAH proximity gate: for shorts, price should be in the upper 40% of the value range
    // (closer to VAH = structural resistance). Midpoint entries have no edge.
    let vah_proximity_ok_short = match (vp.val, vp.vah) {
        (Some(val_level), Some(vah_level)) => {
            let range = vah_level - val_level;
            range > 0.0 && (vah_level - px) / range <= 0.40
        }
        _ => true,
    };

    let short_context = (matches!(ctx.regime, Regime::TrendDown | Regime::Expansion)
        || bearish_divergence_in_uptrend)
        && short_anchor_ok
        && vp.value_location == ValueLocation::InValue
        && fast_slope_ok_short
        && slow_slope_ok_short
        && vah_proximity_ok_short;

    let short_flow = flow.cvd_slope.unwrap_or(0.0) <= 0.0
        && flow.delta.unwrap_or(0.0) < 0.0
        && flow.cvd.unwrap_or(0.0) < 200.0
        && flow.taker_imbalance.unwrap_or(0.0) < 0.0
        && !flow.failed_acceptance;

    let short_book = ob.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps
        && ob.microprice.map(|m| m <= px * 1.0002).unwrap_or(true)
        && ob.obi_l5.unwrap_or(0.0) <= 0.0;

    // BullishAbsorption: price making lower lows but CVD rising — buyers absorbing.
    // Do not enter SHORT when the market is actively defending against selling.
    if flow.cvd_divergence == Some(CvdDivergence::BullishAbsorption) {
        return None;
    }

    // Funding gate: block shorts when long-side funding is elevated/extreme.
    // High positive funding compresses short positions and signals structural long bias.
    // No inst data → allow shorts (opposite of longs which block on missing data).
    let funding_ok_short = ctx
        .institutional
        .as_ref()
        .map(|inst| {
            !matches!(
                inst.funding.regime,
                crate::institutional::FundingRegime::ExtremeLong
                    | crate::institutional::FundingRegime::ElevatedLong
            )
        })
        .unwrap_or(true);

    if short_context && short_flow && short_book && funding_ok_short && adapter::basis_ok(flow.basis, false) {
        let entry = px;
        // min() → closest of (VAH structural level, 1×ATR ceiling).
        let stop = f64::min(vah, entry + 1.0 * atr);

        let risk = stop - entry;
        if risk < 10.0 || stop <= entry {
            return None;
        }

        let target = find_structural_target(
            entry,
            risk,
            Side::Short,
            vp,
            ob,
            atr,
            ctx.swing_high_20,
            ctx.swing_low_20,
            cfg,
        )?;

        let regime_tag = if bearish_divergence_in_uptrend {
            "bearish_divergence_in_uptrend"
        } else {
            "trend_down"
        };
        let mut evidence = vec![
            regime_tag.into(),
            short_anchor_label.into(),
            "pullback_into_value".into(),
            "negative_delta_reentry".into(),
            "cvd_aligned".into(),
            "taker_imbalance_negative".into(),
            "obi_l5_negative".into(),
        ];
        // Fase A — OB logging (peso 0, solo evidencia)
        if let Some(ref obs) = ctx.order_blocks {
            if obs.nearest_bearish.is_some() {
                evidence.push("bearish_ob_in_pullback_zone".into());
            }
        }
        if let Some(ref fvg_ctx) = ctx.fvg {
            if fvg_ctx.nearest_bearish.is_some() {
                evidence.push("bearish_fvg_nearby".into());
            }
        }
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
            evidence,
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
    let min_reward = f64::max(risk * cfg.min_rr, atr);
    let max_reward = f64::min(risk * cfg.max_rr_m5, 2.0 * atr);
    if max_reward < min_reward {
        return None;
    }

    let mut candidates: Vec<f64> = Vec::new();

    match side {
        Side::Long => {
            let lo = entry + min_reward;
            let hi = entry + max_reward;

            candidates.extend(
                vp.hvn_nearby
                    .iter()
                    .copied()
                    .filter(|&h| h >= lo && h <= hi),
            );
            if let Some(vah) = vp.vah {
                if vah >= lo && vah <= hi {
                    candidates.push(vah);
                }
            }
            if let Some(sh) = swing_high {
                if sh >= lo && sh <= hi {
                    candidates.push(sh);
                }
            }
            candidates.extend(
                ob.walls_above
                    .iter()
                    .copied()
                    .filter(|&w| w >= lo && w <= hi),
            );

            if candidates.is_empty() {
                let fallback = entry + 2.0 * atr;
                if fallback >= lo && fallback <= hi {
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

            candidates.extend(
                vp.hvn_nearby
                    .iter()
                    .copied()
                    .filter(|&h| h <= hi && h >= lo),
            );
            if let Some(val) = vp.val {
                if val <= hi && val >= lo {
                    candidates.push(val);
                }
            }
            if let Some(sl) = swing_low {
                if sl <= hi && sl >= lo {
                    candidates.push(sl);
                }
            }
            candidates.extend(
                ob.walls_below
                    .iter()
                    .copied()
                    .filter(|&w| w <= hi && w >= lo),
            );

            if candidates.is_empty() {
                let fallback = entry - 2.0 * atr;
                if fallback <= hi && fallback >= lo {
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
    use crate::institutional::{
        DivergenceSignal, FundingContext, FundingRegime, InstitutionalContext, LiqSide,
        LiquidationSnapshot, LsRatioContext, OiTrend, OiTrendDir, TakerRatioSnapshot,
    };

    fn base_inst(regime: FundingRegime) -> InstitutionalContext {
        InstitutionalContext {
            timestamp_ms: 1710000000000,
            liquidations: LiquidationSnapshot {
                long_liq_usd_5m: 0.0,
                short_liq_usd_5m: 0.0,
                total_usd_5m: 0.0,
                dominant_side: LiqSide::Neutral,
                cascade_detected: false,
                last_event_ms: None,
            },
            ls_ratio: LsRatioContext {
                top_traders_long_pct: 0.52,
                retail_long_pct: 0.50,
                divergence_signal: DivergenceSignal::Neutral,
            },
            oi_trend: OiTrend {
                current: 1_000_000_000.0,
                change_30m: 0.0,
                slope_5bar: 0.0,
                trend: OiTrendDir::Flat,
            },
            taker_ratio: Some(TakerRatioSnapshot {
                timestamp_ms: 1710000000000,
                buy_sell_ratio: 1.0,
                taker_imbalance: 0.0,
            }),
            funding: FundingContext {
                current: 0.0001,
                avg: 0.0001,
                regime,
                velocity: 0.0,
                peak_confirmed: false,
            },
            quality: DataQuality::Live,
            smart_money_score: None,
            liq_map: None,
        }
    }

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
                footprint_levels: vec![],
                oi_delta_zscore: None,
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
            institutional: Some(base_inst(FundingRegime::Neutral)),
            swing_high_20: None,
            swing_low_20: None,
            market_structure: None,
            session: None,
            order_blocks: None,
            fvg: None,
            leverage: 1.0,
            prev_obi_l5: None,
            slow_slope: None,
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
        assert_eq!(
            s.strategy_id,
            Some(StrategyId::VwapValuePullbackContinuation)
        );
        assert!(
            (s.stop_price.unwrap() - 99000.0).abs() < 1.0,
            "stop should be VAL=99000"
        );
        assert!(
            s.target_price.unwrap() >= 99500.0,
            "target must satisfy min_rr=1.5"
        );
        assert_eq!(s.ttl_ms, 250 * 60 * 1000);
    }

    #[test]
    fn stop_long_uses_atr_when_val_is_far() {
        // val farther than 1×ATR but still within lower 40% of value range → stop = ATR floor.
        // val=98800: range=100100-98800=1300, (99200-98800)/1300=0.31 → passes val_proximity gate.
        // stop = max(98800, 99200-250) = max(98800, 98950) = 98950 (ATR floor wins).
        let mut ctx = base_long_ctx();
        ctx.volume_profile.val = Some(98800.0);
        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &cfg);
        assert!(signal.is_some());
        let s = signal.unwrap();
        let expected_stop = 99200.0 - 250.0; // 98950
        assert!(
            (s.stop_price.unwrap() - expected_stop).abs() < 1.0,
            "stop should be ATR floor"
        );
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
        assert!(
            (s.stop_price.unwrap() - 99100.0).abs() < 1.0,
            "stop should be VAL=99100"
        );
    }

    #[test]
    fn no_signal_when_no_structural_target_exists() {
        // No HVNs, no walls — fallback 2×ATR target fires when price is in the lower value zone.
        // price=99120 → gate: (99120-99000)/(99350-99000)=0.343 ≤ 0.40 → passes.
        // stop=max(99000, 99120-250)=99000, risk=120.
        // min_reward=max(180, 250)=250 → reward(vah=99350)=230 < 250 → VAH filtered.
        // fallback=99120+500=99620, fits within [99370, 99620] → selected.
        let mut ctx = base_long_ctx();
        ctx.price = 99120.0;
        ctx.volume_profile.hvn_nearby = vec![];
        ctx.volume_profile.vah = Some(99350.0);
        ctx.orderbook.walls_above = vec![];
        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &cfg);
        assert!(signal.is_some());
        let s = signal.unwrap();
        let expected_target = 99120.0 + 2.0 * 250.0; // 99620
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
        ctx.flow.taker_imbalance = Some(-0.08);
        ctx.orderbook.obi_l5 = Some(-0.03);
        ctx.orderbook.microprice = Some(99790.0);
        // stop = min(100100, 99800+250) = min(100100, 100050) = 100050 (ATR closer)
        // risk = 100050-99800 = 250
        // min_reward = 250*1.5 = 375 → target must be < 99800-375 = 99425
        // VAL is outside the 1-2 ATR target band, so fallback target is entry - 2×ATR.
        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &cfg);
        assert!(signal.is_some());
        let s = signal.unwrap();
        assert_eq!(s.side, Some(Side::Short));
        assert!(
            (s.stop_price.unwrap() - 100050.0).abs() < 1.0,
            "stop should be ATR ceiling"
        );
        assert!(
            (s.target_price.unwrap() - 99300.0).abs() < 1.0,
            "target should be 2×ATR fallback"
        );
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

    #[test]
    fn rejects_long_when_fast_slope_exhaustion_bullish() {
        // Audit 2026-05-20: fast_slope=0.924/1.114 produced 0% WR longs (exhaustion).
        let mut ctx = base_long_ctx();
        ctx.flow.fast_slope = Some(0.924);
        assert!(detect(&ctx, &StrategyConfig::default()).is_none());
    }

    #[test]
    fn allows_long_when_fast_slope_moderate_bullish() {
        let mut ctx = base_long_ctx();
        ctx.flow.fast_slope = Some(0.30);
        assert!(detect(&ctx, &StrategyConfig::default()).is_some());
    }

    #[test]
    fn rejects_long_when_fast_slope_is_bearish() {
        let mut ctx = base_long_ctx();
        ctx.flow.fast_slope = Some(-0.10);
        let cfg = StrategyConfig::default();
        assert!(
            detect(&ctx, &cfg).is_none(),
            "fast_slope <= -0.08 should block longs"
        );
    }

    #[test]
    fn rejects_long_when_funding_extreme_long() {
        let mut ctx = base_long_ctx();
        ctx.institutional = Some(base_inst(FundingRegime::ExtremeLong));
        let cfg = StrategyConfig::default();
        assert!(
            detect(&ctx, &cfg).is_none(),
            "ExtremeLong funding should block long entries"
        );
    }

    #[test]
    fn rejects_long_when_funding_elevated_long() {
        let mut ctx = base_long_ctx();
        ctx.institutional = Some(base_inst(FundingRegime::ElevatedLong));
        let cfg = StrategyConfig::default();
        assert!(
            detect(&ctx, &cfg).is_none(),
            "ElevatedLong funding should block long entries"
        );
    }

    #[test]
    fn rejects_long_when_no_institutional_data() {
        let mut ctx = base_long_ctx();
        ctx.institutional = None; // feed failure — conservative: block
        let cfg = StrategyConfig::default();
        assert!(
            detect(&ctx, &cfg).is_none(),
            "no inst data should block longs (conservative)"
        );
    }

    #[test]
    fn allows_long_when_funding_neutral() {
        let ctx = base_long_ctx(); // base has Neutral funding
        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &cfg);
        assert!(signal.is_some(), "Neutral funding should allow long");
        assert_eq!(signal.unwrap().side, Some(Side::Long));
    }

    #[test]
    fn rejects_short_when_funding_extreme_long() {
        // Audit 2026-05-19: funding=0.71-0.79% (ElevatedLong) — should block SHORT entries.
        let mut ctx = make_short_ctx();
        ctx.institutional = Some(base_inst(FundingRegime::ExtremeLong));
        let cfg = StrategyConfig::default();
        assert!(
            detect(&ctx, &cfg).is_none(),
            "ExtremeLong funding should block shorts"
        );
    }

    fn make_short_ctx() -> StrategyMarketContext {
        let mut ctx = base_long_ctx();
        ctx.regime = Regime::TrendDown;
        ctx.price = 99800.0;
        ctx.volume_profile.value_location = ValueLocation::InValue;
        ctx.vwap.price_vs_avwap_bos = PriceRelation::Below;
        ctx.flow.cvd = Some(-300.0);
        ctx.flow.cvd_slope = Some(-0.4);
        ctx.flow.delta = Some(-80.0);
        ctx.flow.taker_imbalance = Some(-0.08);
        ctx.flow.fast_slope = Some(-0.15);
        ctx.orderbook.obi_l5 = Some(-0.03);
        ctx.orderbook.microprice = Some(99790.0);
        ctx.institutional = Some(base_inst(FundingRegime::Neutral));
        ctx
    }

    #[test]
    fn rejects_short_when_fast_slope_weak_negative() {
        // fast_slope=-0.067 was the audit value — must be blocked after fix.
        let mut ctx = make_short_ctx();
        ctx.flow.fast_slope = Some(-0.067);
        assert!(detect(&ctx, &StrategyConfig::default()).is_none());
    }

    #[test]
    fn allows_short_when_fast_slope_sufficiently_bearish() {
        let mut ctx = make_short_ctx();
        ctx.flow.fast_slope = Some(-0.15);
        assert!(detect(&ctx, &StrategyConfig::default()).is_some());
    }

    #[test]
    fn rejects_short_on_bullish_absorption() {
        let mut ctx = make_short_ctx();
        ctx.flow.cvd_divergence = Some(CvdDivergence::BullishAbsorption);
        assert!(detect(&ctx, &StrategyConfig::default()).is_none());
    }

    #[test]
    fn rejects_short_when_funding_elevated_long() {
        let mut ctx = make_short_ctx();
        ctx.institutional = Some(base_inst(FundingRegime::ElevatedLong));
        assert!(detect(&ctx, &StrategyConfig::default()).is_none());
    }

    #[test]
    fn allows_short_when_no_institutional_data() {
        let mut ctx = make_short_ctx();
        ctx.institutional = None; // data gap → allow shorts (conservative opposite of longs)
        assert!(detect(&ctx, &StrategyConfig::default()).is_some());
    }
}
