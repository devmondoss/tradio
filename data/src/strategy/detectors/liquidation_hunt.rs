use crate::institutional::{InstitutionalContext, LiqSide};
use crate::strategy::types::*;

// Note: toxic_flow_gate is evaluated once in the router before calling any detector.
pub fn detect(
    ctx: &StrategyMarketContext,
    inst: &InstitutionalContext,
    cfg: &StrategyConfig,
) -> Option<StrategySignal> {
    let px = ctx.price;
    let atr = ctx.atr.filter(|&a| a > 1.0)?;
    let flow = &ctx.flow;
    let vp = &ctx.volume_profile;
    let ob = &ctx.orderbook;

    // LONG — barrido de shorts
    // Use z-score when available (outlier vs rolling distribution); fall back to absolute threshold.
    let liq_confirms_long = matches!(inst.liquidations.dominant_side, LiqSide::Shorts)
        && match inst.liquidations.total_zscore {
            Some(z) => z > 1.5,
            None => inst.liquidations.short_liq_usd_5m > cfg.liq_hunt_min_usd,
        };

    let momentum_long = inst.oi_trend.slope_5bar > 0.0
        && inst
            .taker_ratio
            .as_ref()
            .map(|t| t.taker_imbalance > 0.15)
            .unwrap_or(false)
        && flow.cvd_slope.unwrap_or(0.0) > 0.0;

    let path_clear_long = ob.thin_zone_above;

    // Gate: si funding extremo positivo Y smart money short → no entrar long
    let funding_gate_ok_long = !matches!(
        inst.funding.regime,
        crate::institutional::FundingRegime::ExtremeLong
    ) || !matches!(
        inst.ls_ratio.divergence_signal,
        crate::institutional::DivergenceSignal::SmartShortRetailLong
    );

    // Gate: cascade activa de longs (tarde para entrar long)
    let no_long_cascade = inst.liquidations.long_liq_usd_5m < cfg.liq_cascade_threshold;

    if liq_confirms_long
        && momentum_long
        && path_clear_long
        && funding_gate_ok_long
        && no_long_cascade
    {
        let entry = px;
        let stop = entry - 1.0 * atr;

        let mut candidates: Vec<f64> = vp.hvn_nearby.clone();
        if let Some(vah) = vp.vah {
            candidates.push(vah);
        }
        candidates.extend_from_slice(&ob.walls_above);

        let target = candidates
            .iter()
            .copied()
            .filter(|&t| t.is_finite() && t > entry + cfg.min_rr * atr)
            .min_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal))?;

        if target > entry && stop < entry {
            let mut evidence = vec![
                "short_liquidations_cascade".into(),
                "oi_slope_positive".into(),
                "taker_imbalance_bullish".into(),
                "cvd_slope_positive".into(),
                "thin_zone_above".into(),
            ];
            // Fase A — LiqMap logging (peso 0, sin cambio de gate)
            if let Some(ref lm) = inst.liq_map {
                if lm.primary_target_above.is_some() {
                    evidence.push("liq_target_above".into());
                }
                if lm.density_above.iter().any(|d| d.density > 0.5) {
                    evidence.push("high_liq_density_above".into());
                }
            }
            return Some(StrategySignal {
                action: StrategyAction::ShadowSignal,
                strategy_id: Some(StrategyId::LiquidationHunt),
                side: Some(Side::Long),
                regime: ctx.regime,
                entry_price: Some(entry),
                stop_price: Some(stop),
                target_price: Some(target),
                score: 0.0,
                ttl_ms: cfg.liq_ttl_ms,
                evidence,
                missing: vec![],
                invalidation: vec!["cvd_turns_negative".into(), "liq_cascade_reverses".into()],
                created_at_ms: ctx.timestamp_ms,
            });
        }
    }

    // SHORT — barrido de longs
    let liq_confirms_short = matches!(inst.liquidations.dominant_side, LiqSide::Longs)
        && match inst.liquidations.total_zscore {
            Some(z) => z > 1.5,
            None => inst.liquidations.long_liq_usd_5m > cfg.liq_hunt_min_usd,
        };

    let momentum_short = inst.oi_trend.slope_5bar < 0.0
        && inst
            .taker_ratio
            .as_ref()
            .map(|t| t.taker_imbalance < -0.15)
            .unwrap_or(false)
        && flow.cvd_slope.unwrap_or(0.0) < 0.0;

    let path_clear_short = ob.thin_zone_below;

    let funding_gate_ok_short = !matches!(
        inst.funding.regime,
        crate::institutional::FundingRegime::ExtremeShort
    ) || !matches!(
        inst.ls_ratio.divergence_signal,
        crate::institutional::DivergenceSignal::SmartLongRetailShort
    );

    let no_short_cascade = inst.liquidations.short_liq_usd_5m < cfg.liq_cascade_threshold;

    if liq_confirms_short
        && momentum_short
        && path_clear_short
        && funding_gate_ok_short
        && no_short_cascade
    {
        let entry = px;
        let stop = entry + 1.0 * atr;

        let mut candidates: Vec<f64> = vp.hvn_nearby.clone();
        if let Some(val) = vp.val {
            candidates.push(val);
        }
        candidates.extend_from_slice(&ob.walls_below);

        let target = candidates
            .iter()
            .copied()
            .filter(|&t| t.is_finite() && t < entry - cfg.min_rr * atr)
            .max_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal))?;

        if target < entry && stop > entry {
            let mut evidence = vec![
                "long_liquidations_cascade".into(),
                "oi_slope_negative".into(),
                "taker_imbalance_bearish".into(),
                "cvd_slope_negative".into(),
                "thin_zone_below".into(),
            ];
            // Fase A — LiqMap logging (peso 0)
            if let Some(ref lm) = inst.liq_map {
                if lm.primary_target_below.is_some() {
                    evidence.push("liq_target_below".into());
                }
                if lm.density_below.iter().any(|d| d.density > 0.5) {
                    evidence.push("high_liq_density_below".into());
                }
            }
            return Some(StrategySignal {
                action: StrategyAction::ShadowSignal,
                strategy_id: Some(StrategyId::LiquidationHunt),
                side: Some(Side::Short),
                regime: ctx.regime,
                entry_price: Some(entry),
                stop_price: Some(stop),
                target_price: Some(target),
                score: 0.0,
                ttl_ms: cfg.liq_ttl_ms,
                evidence,
                missing: vec![],
                invalidation: vec!["cvd_turns_positive".into(), "liq_cascade_reverses".into()],
                created_at_ms: ctx.timestamp_ms,
            });
        }
    }

    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::institutional::{
        DivergenceSignal, FundingContext, FundingRegime, InstitutionalContext, LiqSide,
        LiquidationSnapshot, LsRatioContext, OiTrend, OiTrendDir, TakerRatioSnapshot,
    };

    fn base_ctx() -> StrategyMarketContext {
        StrategyMarketContext {
            symbol: "BTCUSDT".to_string(),
            timestamp_ms: 1710000000000,
            price: 100000.0,
            regime: Regime::TrendUp,
            atr: Some(400.0),
            volume_profile: VolumeProfileContext {
                poc: Some(99000.0),
                vah: Some(100200.0),
                val: Some(98500.0),
                hvn_nearby: vec![101000.0],
                lvn_nearby: vec![],
                value_location: ValueLocation::AboveVah,
                quality: DataQuality::Live,
                naked_pocs: vec![],
                single_prints: vec![],
            },
            vwap: VwapContext {
                vwap_session: Some(99500.0),
                avwap_bos: None,
                avwap_event: None,
                price_vs_vwap: PriceRelation::Above,
                price_vs_avwap_bos: PriceRelation::Unknown,
                price_vs_avwap_event: PriceRelation::Unknown,
                quality: DataQuality::Live,
            },
            flow: OrderFlowContext {
                cvd: Some(2000.0),
                cvd_slope: Some(0.5),
                delta: Some(200.0),
                taker_imbalance: Some(0.25),
                buy_volume: Some(8000.0),
                sell_volume: Some(6000.0),
                vpin: Some(0.40),
                cvd_divergence: None,
                footprint_absorption: AbsorptionSide::None,
                stacked_imbalance: ImbalanceSide::Bullish,
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
                cvd_divergence_persistence: None,
                finish_action_bullish: false,
                finish_action_bearish: false,
                unfinish_action_bullish: false,
                unfinish_action_bearish: false,
                big_trade_bullish: false,
                big_trade_bearish: false,
            },
            orderbook: OrderBookContext {
                obi_l5: Some(0.10),
                obi_l10: Some(0.06),
                obi_l20: Some(0.02),
                microprice: Some(100010.0),
                spread_bps: Some(0.6),
                walls_above: vec![101500.0],
                walls_below: vec![],
                thin_zone_above: true,
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

    fn base_inst_long() -> InstitutionalContext {
        InstitutionalContext {
            timestamp_ms: 1710000000000,
            liquidations: LiquidationSnapshot {
                long_liq_usd_5m: 0.0,
                short_liq_usd_5m: 800_000.0,
                total_usd_5m: 800_000.0,
                dominant_side: LiqSide::Shorts,
                cascade_detected: false,
                last_event_ms: Some(1710000000000),
                total_zscore: None,
            },
            ls_ratio: LsRatioContext {
                top_traders_long_pct: 0.55,
                retail_long_pct: 0.52,
                divergence_signal: DivergenceSignal::Neutral,
            },
            oi_trend: OiTrend {
                current: 1_000_000_000.0,
                change_30m: 0.5,
                slope_5bar: 1000.0,
                trend: OiTrendDir::Accumulating,
            },
            taker_ratio: Some(TakerRatioSnapshot {
                timestamp_ms: 1710000000000,
                buy_sell_ratio: 1.4,
                taker_imbalance: 0.17,
            }),
            funding: FundingContext {
                current: 0.0003,
                avg: 0.0002,
                regime: FundingRegime::Neutral,
                ..FundingContext::default()
            },
            quality: DataQuality::Live,
            smart_money_score: None,
            liq_map: None,
        }
    }

    #[test]
    fn detects_long_when_short_liquidations_cascade() {
        let ctx = base_ctx();
        let inst = base_inst_long();
        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &inst, &cfg);
        assert!(signal.is_some(), "expected LONG signal");
        let s = signal.unwrap();
        assert_eq!(s.side, Some(Side::Long));
        assert_eq!(s.strategy_id, Some(StrategyId::LiquidationHunt));
        assert!(s.target_price.unwrap() > s.entry_price.unwrap());
    }

    #[test]
    fn blocked_when_liq_below_threshold() {
        let ctx = base_ctx();
        let mut inst = base_inst_long();
        inst.liquidations.short_liq_usd_5m = 10_000.0; // below 25K threshold
        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &inst, &cfg);
        assert!(signal.is_none());
    }

    #[test]
    fn detects_short_when_long_liquidations_cascade() {
        let mut ctx = base_ctx();
        ctx.regime = Regime::TrendDown;
        ctx.flow.cvd_slope = Some(-0.5);
        ctx.flow.taker_imbalance = Some(-0.25);
        ctx.orderbook.thin_zone_above = false;
        ctx.orderbook.thin_zone_below = true;
        ctx.orderbook.walls_below = vec![98000.0];
        ctx.volume_profile.hvn_nearby = vec![98000.0];

        let mut inst = base_inst_long();
        inst.liquidations = LiquidationSnapshot {
            long_liq_usd_5m: 800_000.0,
            short_liq_usd_5m: 0.0,
            total_usd_5m: 800_000.0,
            dominant_side: LiqSide::Longs,
            cascade_detected: false,
            last_event_ms: Some(1710000000000),
            total_zscore: None,
        };
        inst.oi_trend.slope_5bar = -1000.0;
        if let Some(ref mut tr) = inst.taker_ratio {
            tr.taker_imbalance = -0.17;
        }

        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &inst, &cfg);
        assert!(signal.is_some(), "expected SHORT signal");
        let s = signal.unwrap();
        assert_eq!(s.side, Some(Side::Short));
        assert!(s.target_price.unwrap() < s.entry_price.unwrap());
    }
}
