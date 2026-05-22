use crate::detectors::OBStatus;
use crate::strategy::types::*;

fn rr_ok(entry: f64, stop: f64, target: f64, cfg: &StrategyConfig) -> bool {
    let risk = (entry - stop).abs();
    let reward = (target - entry).abs();
    risk > 1e-10 && reward / risk >= cfg.min_rr
}

pub fn detect(ctx: &StrategyMarketContext, cfg: &StrategyConfig) -> Option<StrategySignal> {
    let obs = ctx.order_blocks.as_ref()?;
    let px = ctx.price;
    let atr = ctx.atr.unwrap_or(0.0);
    if atr <= 0.0 {
        return None;
    }

    let flow = &ctx.flow;
    let book = &ctx.orderbook;

    if let Some(ob) = obs.nearest_bullish.as_ref() {
        let status_ok = matches!(ob.status, OBStatus::Active | OBStatus::Tested);
        let long_setup = status_ok
            && ob.price_inside(px)
            && flow.footprint_absorption == AbsorptionSide::Bid
            && flow.cvd_slope.unwrap_or(0.0) >= 0.0
            && book.obi_l5.unwrap_or(0.0) > 0.0
            && !matches!(
                ctx.regime,
                Regime::TrendDown | Regime::Stress | Regime::Aftermath
            )
            && book.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps;

        if long_setup {
            let entry = px;
            let stop = ob.low - 0.5 * atr;
            let target = ctx.swing_high_20?;
            if target > entry && stop < entry && rr_ok(entry, stop, target, cfg) {
                let mut evidence = vec![
                    "bullish_ob_retest".into(),
                    "price_inside_ob".into(),
                    "bid_absorption".into(),
                    "cvd_non_negative".into(),
                    "obi_l5_bid_side".into(),
                    "target_swing_high_20".into(),
                ];
                if ob.volume_ratio > 1.5 {
                    evidence.push("ob_volume_ratio_gt_1_5".into());
                }
                if ob.swings_broken >= 2 {
                    evidence.push("ob_swings_broken_ge_2".into());
                }
                return Some(StrategySignal {
                    action: StrategyAction::ShadowSignal,
                    strategy_id: Some(StrategyId::OrderBlockRetest),
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
                        "price_closes_below_ob_low".into(),
                        "cvd_turns_negative".into(),
                        "obi_flips_ask_side".into(),
                    ],
                    created_at_ms: ctx.timestamp_ms,
                });
            }
        }
    }

    if let Some(ob) = obs.nearest_bearish.as_ref() {
        let status_ok = matches!(ob.status, OBStatus::Active | OBStatus::Tested);
        let short_setup = status_ok
            && ob.price_inside(px)
            && flow.footprint_absorption == AbsorptionSide::Ask
            && flow.cvd_slope.unwrap_or(0.0) <= 0.0
            && book.obi_l5.unwrap_or(0.0) < 0.0
            && !matches!(
                ctx.regime,
                Regime::TrendUp | Regime::Stress | Regime::Aftermath
            )
            && book.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps;

        if short_setup {
            let entry = px;
            let stop = ob.high + 0.5 * atr;
            let target = ctx.swing_low_20?;
            if target < entry && stop > entry && rr_ok(entry, stop, target, cfg) {
                let mut evidence = vec![
                    "bearish_ob_retest".into(),
                    "price_inside_ob".into(),
                    "ask_absorption".into(),
                    "cvd_non_positive".into(),
                    "obi_l5_ask_side".into(),
                    "target_swing_low_20".into(),
                ];
                if ob.volume_ratio > 1.5 {
                    evidence.push("ob_volume_ratio_gt_1_5".into());
                }
                if ob.swings_broken >= 2 {
                    evidence.push("ob_swings_broken_ge_2".into());
                }
                return Some(StrategySignal {
                    action: StrategyAction::ShadowSignal,
                    strategy_id: Some(StrategyId::OrderBlockRetest),
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
                        "price_closes_above_ob_high".into(),
                        "cvd_turns_positive".into(),
                        "obi_flips_bid_side".into(),
                    ],
                    created_at_ms: ctx.timestamp_ms,
                });
            }
        }
    }

    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::detectors::{OBType, OrderBlock, OrderBlockContext};

    fn bullish_ob() -> OrderBlock {
        OrderBlock {
            ob_type: OBType::Bullish,
            high: 100_000.0,
            low: 99_900.0,
            mid: 99_950.0,
            timestamp_ms: 1,
            status: OBStatus::Active,
            volume_ratio: 1.8,
            swings_broken: 2,
        }
    }

    fn base_ctx() -> StrategyMarketContext {
        StrategyMarketContext {
            symbol: "BTCUSDT".into(),
            timestamp_ms: 1,
            price: 99_960.0,
            regime: Regime::TrendUp,
            atr: Some(50.0),
            volume_profile: VolumeProfileContext {
                poc: Some(99_800.0),
                vah: Some(100_300.0),
                val: Some(99_500.0),
                hvn_nearby: vec![],
                lvn_nearby: vec![],
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
                cvd_slope: Some(0.05),
                delta: Some(200.0),
                taker_imbalance: Some(0.10),
                buy_volume: Some(600.0),
                sell_volume: Some(400.0),
                vpin: Some(0.30),
                cvd_divergence: None,
                footprint_absorption: AbsorptionSide::Bid,
                stacked_imbalance: ImbalanceSide::Bullish,
                failed_acceptance: false,
                sweep_confirmed: false,
                mss_active: true,
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
                oi_delta_zscore: None,
            },
            orderbook: OrderBookContext {
                obi_l5: Some(0.10),
                obi_l10: Some(0.05),
                obi_l20: Some(0.02),
                microprice: None,
                spread_bps: Some(0.5),
                walls_above: vec![],
                walls_below: vec![],
                thin_zone_above: false,
                thin_zone_below: false,
                quality: DataQuality::Live,
                spoof: None,
            },
            institutional: None,
            swing_high_20: Some(100_300.0),
            swing_low_20: Some(99_000.0),
            market_structure: None,
            session: None,
            order_blocks: Some(OrderBlockContext {
                bullish_obs: vec![bullish_ob()],
                bearish_obs: vec![],
                nearest_bullish: Some(bullish_ob()),
                nearest_bearish: None,
            }),
            fvg: None,
            leverage: 1.0,
            prev_obi_l5: None,
            slow_slope: None,
        }
    }

    #[test]
    fn detects_long_order_block_retest() {
        let signal = detect(&base_ctx(), &StrategyConfig::default()).unwrap();
        assert_eq!(signal.strategy_id, Some(StrategyId::OrderBlockRetest));
        assert_eq!(signal.side, Some(Side::Long));
        assert!(signal.evidence.contains(&"ob_volume_ratio_gt_1_5".into()));
    }

    #[test]
    fn rejects_without_order_blocks() {
        let mut ctx = base_ctx();
        ctx.order_blocks = None;
        assert!(detect(&ctx, &StrategyConfig::default()).is_none());
    }
}
