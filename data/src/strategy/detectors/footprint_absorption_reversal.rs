use crate::strategy::types::*;

fn rr_ok(entry: f64, stop: f64, target: f64, cfg: &StrategyConfig) -> bool {
    let risk = (entry - stop).abs();
    let reward = (target - entry).abs();
    risk > 1e-10 && reward / risk >= cfg.min_rr
}

fn has_delta_run(levels: &[FootprintLevel], want_positive: bool, min_run: usize) -> bool {
    let mut sorted = levels.to_vec();
    sorted.sort_by(|a, b| {
        a.price
            .partial_cmp(&b.price)
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    let mut run = 0usize;
    for level in sorted {
        let matches = if want_positive {
            level.delta > 0.0
        } else {
            level.delta < 0.0
        };
        if matches {
            run += 1;
            if run >= min_run {
                return true;
            }
        } else {
            run = 0;
        }
    }
    false
}

fn target_above(entry: f64, poc: f64, vah: f64) -> Option<f64> {
    if poc > entry {
        Some(poc)
    } else if vah > entry {
        Some(vah)
    } else {
        None
    }
}

fn target_below(entry: f64, poc: f64, val: f64) -> Option<f64> {
    if poc < entry {
        Some(poc)
    } else if val < entry {
        Some(val)
    } else {
        None
    }
}

pub fn detect(ctx: &StrategyMarketContext, cfg: &StrategyConfig) -> Option<StrategySignal> {
    let px = ctx.price;
    let atr = ctx.atr.unwrap_or(0.0);
    if atr <= 0.0 || ctx.flow.footprint_levels.is_empty() {
        return None;
    }

    let vp = &ctx.volume_profile;
    let val = vp.val?;
    let vah = vp.vah?;
    let poc = vp.poc?;
    let flow = &ctx.flow;
    let book = &ctx.orderbook;

    let long_zone = px >= val - 0.5 * atr && px <= val + 0.3 * atr && px > val;
    let long_levels: Vec<_> = flow
        .footprint_levels
        .iter()
        .filter(|level| level.price >= val - 0.5 * atr && level.price <= val + 0.3 * atr)
        .cloned()
        .collect();
    let long_setup = long_zone
        && has_delta_run(&long_levels, false, 3)
        && book.obi_l5.unwrap_or(0.0) > 0.0
        && flow.cvd_slope.unwrap_or(0.0) > -0.05
        && flow.taker_imbalance.unwrap_or(0.0) > -0.10
        && !matches!(ctx.regime, Regime::TrendDown | Regime::Stress)
        && book.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps;

    if long_setup {
        let entry = px;
        let stop = val - atr;
        let target = target_above(entry, poc, vah)?;
        if stop < entry && target > entry && rr_ok(entry, stop, target, cfg) {
            return Some(StrategySignal {
                action: StrategyAction::ShadowSignal,
                strategy_id: Some(StrategyId::FootprintAbsorptionReversal),
                side: Some(Side::Long),
                regime: ctx.regime,
                entry_price: Some(entry),
                stop_price: Some(stop),
                target_price: Some(target),
                score: 0.0,
                ttl_ms: cfg.default_ttl_ms,
                evidence: vec![
                    "val_absorption_zone".into(),
                    "three_negative_delta_levels".into(),
                    "close_back_above_val".into(),
                    "obi_l5_bid_side".into(),
                    "seller_pressure_fading".into(),
                    "target_poc_or_vah".into(),
                ],
                missing: vec![],
                invalidation: vec![
                    "price_closes_below_val".into(),
                    "negative_delta_expands".into(),
                    "obi_flips_ask_side".into(),
                ],
                created_at_ms: ctx.timestamp_ms,
            });
        }
    }

    let short_zone = px <= vah + 0.5 * atr && px >= vah - 0.3 * atr && px < vah;
    let short_levels: Vec<_> = flow
        .footprint_levels
        .iter()
        .filter(|level| level.price >= vah - 0.3 * atr && level.price <= vah + 0.5 * atr)
        .cloned()
        .collect();
    let short_setup = short_zone
        && has_delta_run(&short_levels, true, 3)
        && book.obi_l5.unwrap_or(0.0) < 0.0
        && flow.cvd_slope.unwrap_or(0.0) < 0.05
        && flow.taker_imbalance.unwrap_or(0.0) < 0.10
        && !matches!(ctx.regime, Regime::TrendUp | Regime::Stress)
        && book.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps;

    if short_setup {
        let entry = px;
        let stop = vah + atr;
        let target = target_below(entry, poc, val)?;
        if stop > entry && target < entry && rr_ok(entry, stop, target, cfg) {
            return Some(StrategySignal {
                action: StrategyAction::ShadowSignal,
                strategy_id: Some(StrategyId::FootprintAbsorptionReversal),
                side: Some(Side::Short),
                regime: ctx.regime,
                entry_price: Some(entry),
                stop_price: Some(stop),
                target_price: Some(target),
                score: 0.0,
                ttl_ms: cfg.default_ttl_ms,
                evidence: vec![
                    "vah_absorption_zone".into(),
                    "three_positive_delta_levels".into(),
                    "close_back_below_vah".into(),
                    "obi_l5_ask_side".into(),
                    "buyer_pressure_fading".into(),
                    "target_poc_or_val".into(),
                ],
                missing: vec![],
                invalidation: vec![
                    "price_closes_above_vah".into(),
                    "positive_delta_expands".into(),
                    "obi_flips_bid_side".into(),
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

    fn level(price: f64, delta: f64) -> FootprintLevel {
        if delta >= 0.0 {
            FootprintLevel {
                price,
                buy_volume: delta,
                sell_volume: 0.0,
                delta,
            }
        } else {
            FootprintLevel {
                price,
                buy_volume: 0.0,
                sell_volume: -delta,
                delta,
            }
        }
    }

    fn base_ctx() -> StrategyMarketContext {
        StrategyMarketContext {
            symbol: "BTCUSDT".into(),
            timestamp_ms: 1,
            price: 99_520.0,
            regime: Regime::Compression,
            atr: Some(100.0),
            volume_profile: VolumeProfileContext {
                poc: Some(99_850.0),
                vah: Some(100_200.0),
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
                price_vs_vwap: PriceRelation::Below,
                price_vs_avwap_bos: PriceRelation::Unknown,
                price_vs_avwap_event: PriceRelation::Unknown,
                quality: DataQuality::Live,
            },
            flow: OrderFlowContext {
                cvd: Some(-1_000.0),
                cvd_slope: Some(-0.02),
                delta: Some(-200.0),
                taker_imbalance: Some(-0.05),
                buy_volume: Some(900.0),
                sell_volume: Some(1_000.0),
                vpin: Some(0.30),
                cvd_divergence: None,
                footprint_absorption: AbsorptionSide::Bid,
                stacked_imbalance: ImbalanceSide::Bearish,
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
                fast_slope: Some(0.02),
                footprint_levels: vec![
                    level(99_460.0, -20.0),
                    level(99_470.0, -30.0),
                    level(99_480.0, -25.0),
                    level(99_560.0, 10.0),
                ],
                oi_delta_zscore: None,
            },
            orderbook: OrderBookContext {
                obi_l5: Some(0.10),
                obi_l10: Some(0.04),
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
            order_blocks: None,
            fvg: None,
            leverage: 1.0,
            prev_obi_l5: None,
            slow_slope: None,
        }
    }

    #[test]
    fn detects_long_footprint_absorption_reversal() {
        let signal = detect(&base_ctx(), &StrategyConfig::default()).unwrap();
        assert_eq!(
            signal.strategy_id,
            Some(StrategyId::FootprintAbsorptionReversal)
        );
        assert_eq!(signal.side, Some(Side::Long));
    }

    #[test]
    fn rejects_without_footprint_levels() {
        let mut ctx = base_ctx();
        ctx.flow.footprint_levels = vec![];
        assert!(detect(&ctx, &StrategyConfig::default()).is_none());
    }
}
