use crate::strategy::{adapter, types::*};

const MIN_OBI_L5: f64 = 0.40;
const MIN_CVD_SLOPE: f64 = 0.05;
const MAX_COUNTER_FAST_SLOPE: f64 = 0.10;

fn lvn_near_price(levels: &[f64], price: f64, atr: f64) -> bool {
    atr > 0.0
        && levels
            .iter()
            .any(|&lvl| lvl.is_finite() && (lvl - price).abs() <= atr)
}

fn nearest_above(levels: &[f64], price: f64) -> Option<f64> {
    levels
        .iter()
        .copied()
        .filter(|x| x.is_finite() && *x > price)
        .min_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal))
}

fn nearest_below(levels: &[f64], price: f64) -> Option<f64> {
    levels
        .iter()
        .copied()
        .filter(|x| x.is_finite() && *x < price)
        .max_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal))
}

fn rr_ok(entry: f64, stop: f64, target: f64, cfg: &StrategyConfig) -> bool {
    let risk = (entry - stop).abs();
    let reward = (target - entry).abs();
    risk > 1e-10 && reward / risk >= cfg.min_rr
}

// DOMImbalanceBreakout: book imbalance + LVN vacuum + flow confirmation.
// Uses only fields already present in StrategyMarketContext.
pub fn detect(ctx: &StrategyMarketContext, cfg: &StrategyConfig) -> Option<StrategySignal> {
    let px = ctx.price;
    let atr = ctx.atr.unwrap_or(0.0);
    let vp = &ctx.volume_profile;
    let vw = &ctx.vwap;
    let flow = &ctx.flow;
    let ob = &ctx.orderbook;

    let has_lvn = lvn_near_price(&vp.lvn_nearby, px, atr);

    let long_setup = ob.obi_l5.unwrap_or(0.0) > MIN_OBI_L5
        && ob.thin_zone_above
        && has_lvn
        && vw.vwap_session.map(|v| px > v).unwrap_or(false)
        && flow.cvd_slope.unwrap_or(0.0) > MIN_CVD_SLOPE
        && flow
            .fast_slope
            .map(|fs| fs > -MAX_COUNTER_FAST_SLOPE)
            .unwrap_or(true)
        && ob.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps
        && adapter::basis_ok(flow.basis, true);

    if long_setup {
        let entry = px;
        let target = nearest_above(&vp.hvn_nearby, entry)?;
        let stop = nearest_below(&ob.walls_below, entry)?;
        if stop < entry && target > entry && rr_ok(entry, stop, target, cfg) {
            return Some(StrategySignal {
                action: StrategyAction::ShadowSignal,
                strategy_id: Some(StrategyId::DomImbalanceBreakout),
                side: Some(Side::Long),
                regime: ctx.regime,
                entry_price: Some(entry),
                stop_price: Some(stop),
                target_price: Some(target),
                score: 0.0,
                ttl_ms: cfg.default_ttl_ms,
                evidence: vec![
                    "obi_l5_bid_dominant".into(),
                    "thin_zone_above".into(),
                    "lvn_nearby".into(),
                    "price_above_vwap".into(),
                    "cvd_slope_positive".into(),
                    "target_hvn_above".into(),
                    "stop_bid_wall_below".into(),
                ],
                missing: vec![],
                invalidation: vec![
                    "obi_l5_flips_negative".into(),
                    "price_loses_VWAP".into(),
                    "cvd_turns_negative".into(),
                ],
                created_at_ms: ctx.timestamp_ms,
            });
        }
    }

    let short_setup = ob.obi_l5.unwrap_or(0.0) < -MIN_OBI_L5
        && ob.thin_zone_below
        && has_lvn
        && vw.vwap_session.map(|v| px < v).unwrap_or(false)
        && flow.cvd_slope.unwrap_or(0.0) < -MIN_CVD_SLOPE
        && flow
            .fast_slope
            .map(|fs| fs < MAX_COUNTER_FAST_SLOPE)
            .unwrap_or(true)
        && ob.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps
        && adapter::basis_ok(flow.basis, false);

    if short_setup {
        let entry = px;
        let target = nearest_below(&vp.hvn_nearby, entry)?;
        let stop = nearest_above(&ob.walls_above, entry)?;
        if stop > entry && target < entry && rr_ok(entry, stop, target, cfg) {
            return Some(StrategySignal {
                action: StrategyAction::ShadowSignal,
                strategy_id: Some(StrategyId::DomImbalanceBreakout),
                side: Some(Side::Short),
                regime: ctx.regime,
                entry_price: Some(entry),
                stop_price: Some(stop),
                target_price: Some(target),
                score: 0.0,
                ttl_ms: cfg.default_ttl_ms,
                evidence: vec![
                    "obi_l5_ask_dominant".into(),
                    "thin_zone_below".into(),
                    "lvn_nearby".into(),
                    "price_below_vwap".into(),
                    "cvd_slope_negative".into(),
                    "target_hvn_below".into(),
                    "stop_ask_wall_above".into(),
                ],
                missing: vec![],
                invalidation: vec![
                    "obi_l5_flips_positive".into(),
                    "price_reclaims_VWAP".into(),
                    "cvd_turns_positive".into(),
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
            price: 100_000.0,
            regime: Regime::Expansion,
            atr: Some(250.0),
            volume_profile: VolumeProfileContext {
                poc: Some(99_800.0),
                vah: Some(100_300.0),
                val: Some(99_400.0),
                hvn_nearby: vec![100_900.0, 101_500.0],
                lvn_nearby: vec![99_950.0],
                value_location: ValueLocation::InValue,
                quality: DataQuality::Live,
            },
            vwap: VwapContext {
                vwap_session: Some(99_700.0),
                avwap_bos: None,
                avwap_event: None,
                price_vs_vwap: PriceRelation::Above,
                price_vs_avwap_bos: PriceRelation::Unknown,
                price_vs_avwap_event: PriceRelation::Unknown,
                quality: DataQuality::Live,
            },
            flow: OrderFlowContext {
                cvd: Some(1_000.0),
                cvd_slope: Some(0.20),
                delta: Some(150.0),
                taker_imbalance: Some(0.20),
                buy_volume: Some(1_200.0),
                sell_volume: Some(900.0),
                vpin: Some(0.35),
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
                bid_wall_nearby: true,
                ask_wall_nearby: false,
                price_action_clean: true,
                fast_slope: Some(0.05),
                footprint_levels: vec![],
            },
            orderbook: OrderBookContext {
                obi_l5: Some(0.45),
                obi_l10: Some(0.25),
                obi_l20: Some(0.10),
                microprice: Some(100_010.0),
                spread_bps: Some(0.5),
                walls_above: vec![100_300.0],
                walls_below: vec![99_600.0],
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
        }
    }

    #[test]
    fn detects_long_dom_imbalance_breakout() {
        let ctx = base_ctx();
        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &cfg).expect("expected DIB long");
        assert_eq!(signal.strategy_id, Some(StrategyId::DomImbalanceBreakout));
        assert_eq!(signal.side, Some(Side::Long));
        assert_eq!(signal.target_price, Some(100_900.0));
        assert_eq!(signal.stop_price, Some(99_600.0));
    }

    #[test]
    fn rejects_without_lvn() {
        let mut ctx = base_ctx();
        ctx.volume_profile.lvn_nearby = vec![];
        let cfg = StrategyConfig::default();
        assert!(detect(&ctx, &cfg).is_none());
    }

    #[test]
    fn detects_short_dom_imbalance_breakout() {
        let mut ctx = base_ctx();
        ctx.price = 100_000.0;
        ctx.vwap.vwap_session = Some(100_300.0);
        ctx.vwap.price_vs_vwap = PriceRelation::Below;
        ctx.volume_profile.hvn_nearby = vec![99_100.0, 98_500.0];
        ctx.orderbook.obi_l5 = Some(-0.45);
        ctx.orderbook.thin_zone_above = false;
        ctx.orderbook.thin_zone_below = true;
        ctx.orderbook.walls_above = vec![100_400.0];
        ctx.orderbook.walls_below = vec![99_700.0];
        ctx.flow.cvd_slope = Some(-0.20);
        ctx.flow.delta = Some(-150.0);
        ctx.flow.taker_imbalance = Some(-0.20);
        ctx.flow.stacked_imbalance = ImbalanceSide::Bearish;
        ctx.flow.fast_slope = Some(-0.05);

        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &cfg).expect("expected DIB short");
        assert_eq!(signal.strategy_id, Some(StrategyId::DomImbalanceBreakout));
        assert_eq!(signal.side, Some(Side::Short));
        assert_eq!(signal.target_price, Some(99_100.0));
        assert_eq!(signal.stop_price, Some(100_400.0));
    }
}
