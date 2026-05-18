use crate::institutional::{FundingRegime, InstitutionalContext, OiTrendDir};
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

    // SHORT — funding extremo positivo: demasiados longs, costo de carry insostenible
    let funding_extreme_long = matches!(inst.funding.regime, FundingRegime::ExtremeLong)
        && inst.funding.current > cfg.funding_extreme_threshold;

    let institutional_diverging_short = inst.ls_ratio.top_traders_long_pct < 0.52
        && inst.ls_ratio.retail_long_pct > 0.62;

    let oi_weakening = matches!(
        inst.oi_trend.trend,
        OiTrendDir::Decreasing | OiTrendDir::DecreasingFast
    );

    let flow_weakening_short = flow.cvd_slope.unwrap_or(0.0) <= 0.0
        && inst
            .taker_ratio
            .as_ref()
            .map(|t| t.buy_sell_ratio < 1.0)
            .unwrap_or(false);

    // Gate: cascade activa de longs = ya tarde para entrar short
    let no_long_cascade_gate = inst.liquidations.long_liq_usd_5m < cfg.liq_cascade_threshold;

    if funding_extreme_long
        && institutional_diverging_short
        && oi_weakening
        && flow_weakening_short
        && no_long_cascade_gate
    {
        let entry = px;
        let stop = f64::max(
            vp.vah.unwrap_or(px + atr),
            entry + 0.75 * atr,
        );
        let target = vp.val.unwrap_or(entry - 2.0 * atr);

        if target < entry && stop > entry {
            return Some(StrategySignal {
                action: StrategyAction::ShadowSignal,
                strategy_id: Some(StrategyId::FundingExhaustionReversal),
                side: Some(Side::Short),
                regime: ctx.regime,
                entry_price: Some(entry),
                stop_price: Some(stop),
                target_price: Some(target),
                score: 0.0,
                ttl_ms: cfg.funding_ttl_ms,
                evidence: vec![
                    "funding_extreme_positive".into(),
                    "top_traders_exiting_long".into(),
                    "retail_still_long".into(),
                    "oi_decreasing".into(),
                    "cvd_weakening".into(),
                ],
                missing: vec![],
                invalidation: vec![
                    "funding_drops_below_threshold".into(),
                    "oi_resumes_accumulation".into(),
                    "cvd_turns_strongly_positive".into(),
                ],
                created_at_ms: ctx.timestamp_ms,
            });
        }
    }

    // LONG — funding extremo negativo: demasiados shorts, reversión inminente
    let funding_extreme_short = matches!(inst.funding.regime, FundingRegime::ExtremeShort)
        && inst.funding.current < -cfg.funding_extreme_threshold;

    let institutional_diverging_long = inst.ls_ratio.top_traders_long_pct > cfg.fer_top_long_min
        && inst.ls_ratio.retail_long_pct < cfg.fer_retail_long_max;

    let flow_weakening_long = flow.cvd_slope.unwrap_or(0.0) >= 0.0
        && inst
            .taker_ratio
            .as_ref()
            .map(|t| t.buy_sell_ratio > 1.0)
            .unwrap_or(false);

    let no_short_cascade_gate = inst.liquidations.short_liq_usd_5m < cfg.liq_cascade_threshold;

    if funding_extreme_short
        && institutional_diverging_long
        && oi_weakening
        && flow_weakening_long
        && no_short_cascade_gate
    {
        let entry = px;
        let stop = f64::min(
            vp.val.unwrap_or(px - atr),
            entry - 0.75 * atr,
        );
        let target = vp.vah.unwrap_or(entry + 2.0 * atr);

        if target > entry && stop < entry {
            return Some(StrategySignal {
                action: StrategyAction::ShadowSignal,
                strategy_id: Some(StrategyId::FundingExhaustionReversal),
                side: Some(Side::Long),
                regime: ctx.regime,
                entry_price: Some(entry),
                stop_price: Some(stop),
                target_price: Some(target),
                score: 0.0,
                ttl_ms: cfg.funding_ttl_ms,
                evidence: vec![
                    "funding_extreme_negative".into(),
                    "top_traders_exiting_short".into(),
                    "retail_still_short".into(),
                    "oi_decreasing".into(),
                    "cvd_recovering".into(),
                ],
                missing: vec![],
                invalidation: vec![
                    "funding_rises_above_threshold".into(),
                    "oi_resumes_accumulation_short".into(),
                    "cvd_turns_strongly_negative".into(),
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
    use crate::institutional::{
        DivergenceSignal, FundingContext, FundingRegime, InstitutionalContext, LiqSide,
        LiquidationSnapshot, LsRatioContext, OiTrend, OiTrendDir, TakerRatioSnapshot,
    };

    fn base_ctx() -> StrategyMarketContext {
        StrategyMarketContext {
            symbol: "BTCUSDT".to_string(),
            timestamp_ms: 1710000000000,
            price: 70000.0,
            regime: Regime::TrendDown,
            atr: Some(1000.0),
            volume_profile: VolumeProfileContext {
                poc: Some(68000.0),
                vah: Some(71000.0),
                val: Some(66000.0),
                hvn_nearby: vec![],
                lvn_nearby: vec![],
                value_location: ValueLocation::AboveVah,
                quality: DataQuality::Live,
            },
            vwap: VwapContext {
                vwap_session: Some(69500.0),
                avwap_bos: None,
                avwap_event: None,
                price_vs_vwap: PriceRelation::Above,
                price_vs_avwap_bos: PriceRelation::Unknown,
                price_vs_avwap_event: PriceRelation::Unknown,
                quality: DataQuality::Live,
            },
            flow: OrderFlowContext {
                cvd: Some(500.0),
                cvd_slope: Some(-0.3),
                delta: Some(-100.0),
                taker_imbalance: Some(-0.1),
                buy_volume: Some(4000.0),
                sell_volume: Some(5000.0),
                vpin: Some(0.45),
                cvd_divergence: None,
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
            },
            orderbook: OrderBookContext {
                obi_l5: Some(-0.05),
                obi_l10: Some(-0.02),
                obi_l20: Some(0.0),
                microprice: Some(69990.0),
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
        }
    }

    fn base_inst_extreme_long() -> InstitutionalContext {
        InstitutionalContext {
            timestamp_ms: 1710000000000,
            liquidations: LiquidationSnapshot {
                long_liq_usd_5m: 100_000.0,
                short_liq_usd_5m: 50_000.0,
                total_usd_5m: 150_000.0,
                dominant_side: LiqSide::Longs,
                cascade_detected: false,
                last_event_ms: None,
            },
            ls_ratio: LsRatioContext {
                top_traders_long_pct: 0.45, // smart money exiting
                retail_long_pct: 0.70,      // retail still long
                divergence_signal: DivergenceSignal::SmartShortRetailLong,
            },
            oi_trend: OiTrend {
                current: 1_000_000_000.0,
                change_30m: -0.5,
                slope_5bar: -500.0,
                trend: OiTrendDir::Decreasing,
            },
            taker_ratio: Some(TakerRatioSnapshot {
                timestamp_ms: 1710000000000,
                buy_sell_ratio: 0.85,
                taker_imbalance: -0.08,
            }),
            funding: FundingContext {
                current: 0.0008, // above 0.0006 threshold
                avg: 0.0004,
                regime: FundingRegime::ExtremeLong,
            },
            quality: DataQuality::Live,
            smart_money_score: None,
            liq_map: None,
        }
    }

    #[test]
    fn detects_short_when_funding_extreme_long() {
        let ctx = base_ctx();
        let inst = base_inst_extreme_long();
        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &inst, &cfg);
        assert!(signal.is_some(), "expected SHORT signal");
        let s = signal.unwrap();
        assert_eq!(s.side, Some(Side::Short));
        assert_eq!(s.strategy_id, Some(StrategyId::FundingExhaustionReversal));
        assert!(s.target_price.unwrap() < s.entry_price.unwrap());
        assert!(s.stop_price.unwrap() > s.entry_price.unwrap());
    }

    #[test]
    fn no_signal_when_cascade_active() {
        let ctx = base_ctx();
        let mut inst = base_inst_extreme_long();
        inst.liquidations.long_liq_usd_5m = 6_000_000.0; // above cascade threshold
        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &inst, &cfg);
        assert!(signal.is_none());
    }

    #[test]
    fn no_signal_when_oi_accumulating() {
        let ctx = base_ctx();
        let mut inst = base_inst_extreme_long();
        inst.oi_trend.trend = OiTrendDir::Accumulating;
        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &inst, &cfg);
        assert!(signal.is_none());
    }
}
