use crate::institutional::{DivergenceSignal, FundingRegime, InstitutionalContext, OiTrendDir};
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

    // Gate: cascade activa → no entrar (esperar que se calme)
    if inst.liquidations.cascade_detected {
        return None;
    }

    let top_long = inst.ls_ratio.top_traders_long_pct;
    let retail_long = inst.ls_ratio.retail_long_pct;
    let divergence = retail_long - top_long; // positivo = retail más long que smart

    // SHORT — smart money short, retail long
    let strong_divergence_short = top_long < cfg.smart_short_threshold
        && retail_long > cfg.retail_long_threshold
        && divergence > cfg.min_divergence;

    let funding_confirms_short = matches!(
        inst.funding.regime,
        FundingRegime::ElevatedLong | FundingRegime::ExtremeLong
    );

    // Movimiento maduro — sin acumulación nueva de OI
    let oi_confirms = !matches!(inst.oi_trend.trend, OiTrendDir::AccumulatingFast);

    // Precio en zona de resistencia
    let at_resistance = vp
        .vah
        .map(|vah| (px - vah).abs() < 0.5 * atr)
        .unwrap_or(false)
        || ob
            .walls_above
            .iter()
            .any(|&w| w.is_finite() && (w - px).abs() < 0.3 * atr);

    // CVD no confirmando el precio (debilidad)
    let cvd_weak = flow.cvd_slope.unwrap_or(0.0) <= 0.0;

    if strong_divergence_short && funding_confirms_short && oi_confirms && at_resistance && cvd_weak
    {
        let entry = px;
        let stop = entry + 1.5 * atr;
        let target = vp.val.unwrap_or(entry - 3.0 * atr);

        if target < entry && stop > entry {
            return Some(StrategySignal {
                action: StrategyAction::ShadowSignal,
                strategy_id: Some(StrategyId::SmartMoneyDivergence),
                side: Some(Side::Short),
                regime: ctx.regime,
                entry_price: Some(entry),
                stop_price: Some(stop),
                target_price: Some(target),
                score: 0.0,
                ttl_ms: cfg.smd_ttl_ms,
                evidence: vec![
                    "smart_money_short".into(),
                    "retail_long_extreme".into(),
                    "funding_elevated".into(),
                    "oi_mature".into(),
                    "price_at_resistance".into(),
                    "cvd_weak".into(),
                ],
                missing: vec![],
                invalidation: vec![
                    "smart_money_flips_long".into(),
                    "funding_normalizes".into(),
                    "price_breaks_resistance".into(),
                ],
                created_at_ms: ctx.timestamp_ms,
            });
        }
    }

    // LONG — smart money long, retail short
    let strong_divergence_long = top_long > (1.0 - cfg.smart_short_threshold)
        && retail_long < (1.0 - cfg.retail_long_threshold)
        && (-divergence) > cfg.min_divergence;

    let funding_confirms_long = matches!(
        inst.funding.regime,
        FundingRegime::ElevatedShort | FundingRegime::ExtremeShort
    );

    // Precio en zona de soporte
    let at_support = vp
        .val
        .map(|val| (px - val).abs() < 0.5 * atr)
        .unwrap_or(false)
        || ob
            .walls_below
            .iter()
            .any(|&w| w.is_finite() && (px - w).abs() < 0.3 * atr);

    let cvd_recovering = flow.cvd_slope.unwrap_or(0.0) >= 0.0;

    let divergence_signal_ok = matches!(
        inst.ls_ratio.divergence_signal,
        DivergenceSignal::SmartLongRetailShort | DivergenceSignal::Neutral
    );

    if strong_divergence_long
        && funding_confirms_long
        && oi_confirms
        && at_support
        && cvd_recovering
        && divergence_signal_ok
    {
        let entry = px;
        let stop = entry - 1.5 * atr;
        let target = vp.vah.unwrap_or(entry + 3.0 * atr);

        if target > entry && stop < entry {
            return Some(StrategySignal {
                action: StrategyAction::ShadowSignal,
                strategy_id: Some(StrategyId::SmartMoneyDivergence),
                side: Some(Side::Long),
                regime: ctx.regime,
                entry_price: Some(entry),
                stop_price: Some(stop),
                target_price: Some(target),
                score: 0.0,
                ttl_ms: cfg.smd_ttl_ms,
                evidence: vec![
                    "smart_money_long".into(),
                    "retail_short_extreme".into(),
                    "funding_negative_elevated".into(),
                    "oi_mature".into(),
                    "price_at_support".into(),
                    "cvd_recovering".into(),
                ],
                missing: vec![],
                invalidation: vec![
                    "smart_money_flips_short".into(),
                    "funding_normalizes".into(),
                    "price_breaks_support".into(),
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
            regime: Regime::Chop,
            atr: Some(1000.0),
            volume_profile: VolumeProfileContext {
                poc: Some(69000.0),
                vah: Some(70300.0), // price near VAH: |70000 - 70300| = 300 < 0.5 * 1000 = 500
                val: Some(67000.0),
                hvn_nearby: vec![],
                lvn_nearby: vec![],
                value_location: ValueLocation::AboveVah,
                quality: DataQuality::Live,
            },
            vwap: VwapContext {
                vwap_session: Some(69000.0),
                avwap_bos: None,
                avwap_event: None,
                price_vs_vwap: PriceRelation::Above,
                price_vs_avwap_bos: PriceRelation::Unknown,
                price_vs_avwap_event: PriceRelation::Unknown,
                quality: DataQuality::Live,
            },
            flow: OrderFlowContext {
                cvd: Some(100.0),
                cvd_slope: Some(-0.2), // weak
                delta: Some(-50.0),
                taker_imbalance: Some(-0.05),
                buy_volume: Some(5000.0),
                sell_volume: Some(5200.0),
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
                obi_l5: Some(-0.02),
                obi_l10: Some(-0.01),
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

    fn base_inst_smd_short() -> InstitutionalContext {
        InstitutionalContext {
            timestamp_ms: 1710000000000,
            liquidations: LiquidationSnapshot {
                long_liq_usd_5m: 50_000.0,
                short_liq_usd_5m: 30_000.0,
                total_usd_5m: 80_000.0,
                dominant_side: LiqSide::Longs,
                cascade_detected: false,
                last_event_ms: None,
            },
            ls_ratio: LsRatioContext {
                top_traders_long_pct: 0.38, // smart money predominantly short
                retail_long_pct: 0.65,      // retail predominantly long
                divergence_signal: DivergenceSignal::SmartShortRetailLong,
            },
            oi_trend: OiTrend {
                current: 1_000_000_000.0,
                change_30m: 0.1,
                slope_5bar: 100.0,
                trend: OiTrendDir::Flat, // mature move
            },
            taker_ratio: Some(TakerRatioSnapshot {
                timestamp_ms: 1710000000000,
                buy_sell_ratio: 0.95,
                taker_imbalance: -0.03,
            }),
            funding: FundingContext {
                current: 0.0005,
                avg: 0.0003,
                regime: FundingRegime::ElevatedLong,
            },
            quality: DataQuality::Live,
            smart_money_score: None,
            liq_map: None,
        }
    }

    #[test]
    fn detects_short_when_smart_money_diverges() {
        let ctx = base_ctx();
        let inst = base_inst_smd_short();
        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &inst, &cfg);
        assert!(signal.is_some(), "expected SHORT signal");
        let s = signal.unwrap();
        assert_eq!(s.side, Some(Side::Short));
        assert_eq!(s.strategy_id, Some(StrategyId::SmartMoneyDivergence));
        assert!(s.target_price.unwrap() < s.entry_price.unwrap());
    }

    #[test]
    fn no_signal_when_cascade_active() {
        let ctx = base_ctx();
        let mut inst = base_inst_smd_short();
        inst.liquidations.cascade_detected = true;
        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &inst, &cfg);
        assert!(signal.is_none());
    }

    #[test]
    fn no_signal_when_divergence_below_threshold() {
        let ctx = base_ctx();
        let mut inst = base_inst_smd_short();
        // divergence = 0.65 - 0.38 = 0.27 normally, reduce to below min_divergence (0.18)
        inst.ls_ratio.retail_long_pct = 0.52; // divergence now = 0.52 - 0.38 = 0.14 < 0.18
        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &inst, &cfg);
        assert!(signal.is_none());
    }

    #[test]
    fn no_signal_when_not_at_resistance() {
        let mut ctx = base_ctx();
        // Move price far from VAH, so it's not at resistance
        ctx.price = 68000.0;
        ctx.volume_profile.vah = Some(70500.0); // |68000 - 70500| = 2500 >> 0.5 * 1000 = 500
        let inst = base_inst_smd_short();
        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &inst, &cfg);
        assert!(signal.is_none());
    }
}
