use super::types::*;

pub fn score_signal(ctx: &StrategyMarketContext, mut signal: StrategySignal) -> StrategySignal {
    let mut score = 0.0;

    let has = |s: &str| signal.evidence.iter().any(|e| e == s);

    // Location / target quality
    if has("target_POC") {
        score += 0.20;
    }
    if has("target_next_HVN_or_VAH") || has("target_next_HVN_or_VAL") {
        score += 0.20;
    }

    // VWAP / AVWAP confirmation
    if signal
        .evidence
        .iter()
        .any(|e| e.contains("vwap") || e.contains("avwap"))
    {
        score += 0.15;
    }

    // Order flow
    if signal.evidence.iter().any(|e| e.contains("cvd")) {
        score += 0.20;
    }
    if has("ask_absorption") || has("bid_absorption") {
        score += 0.20;
    }
    if has("positive_delta") || has("negative_delta") || has("positive_delta_reentry") || has("negative_delta_reentry") {
        score += 0.15;
    }

    // Orderbook / path
    if has("thin_zone_above") || has("thin_zone_below") {
        score += 0.15;
    }

    // Penalties
    if let Some(spread) = ctx.orderbook.spread_bps {
        if spread > 1.5 {
            score -= 0.15;
        }
    }

    if let Some(vpin) = ctx.flow.vpin {
        if vpin > 0.65 {
            score -= 0.20;
        }
    }

    signal.score = score.clamp(0.0, 1.0);
    signal
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dummy_ctx() -> StrategyMarketContext {
        StrategyMarketContext {
            symbol: "BTCUSDT".to_string(),
            timestamp_ms: 1710000000000,
            price: 100000.0,
            regime: Regime::TrendUp,
            atr: Some(250.0),
            volume_profile: VolumeProfileContext {
                poc: Some(99500.0),
                vah: Some(100100.0),
                val: Some(99000.0),
                hvn_nearby: vec![],
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
                cvd_slope: Some(0.3),
                delta: Some(100.0),
                taker_imbalance: Some(0.1),
                buy_volume: Some(5000.0),
                sell_volume: Some(4800.0),
                vpin: Some(0.40),
                footprint_absorption: AbsorptionSide::None,
                stacked_imbalance: ImbalanceSide::None,
                failed_acceptance: false,
                sweep_confirmed: false,
                mss_active: false,
                quality: DataQuality::Live,
            },
            orderbook: OrderBookContext {
                obi_l5: Some(0.05),
                obi_l10: Some(0.02),
                obi_l20: Some(0.0),
                microprice: Some(100005.0),
                spread_bps: Some(0.8),
                walls_above: vec![],
                walls_below: vec![],
                thin_zone_above: false,
                thin_zone_below: false,
                quality: DataQuality::Live,
            },
        }
    }

    #[test]
    fn scores_high_evidence_signal() {
        let ctx = dummy_ctx();
        let signal = StrategySignal {
            action: StrategyAction::ShadowSignal,
            strategy_id: Some(StrategyId::ValueAreaFailedAuction),
            side: Some(Side::Short),
            entry_price: Some(100050.0),
            stop_price: Some(100300.0),
            target_price: Some(99500.0),
            score: 0.0,
            ttl_ms: 300000,
            evidence: vec![
                "failed_acceptance_above_VAH".into(),
                "ask_absorption".into(),
                "cvd_not_confirming_breakout".into(),
                "target_POC".into(),
            ],
            missing: vec![],
            invalidation: vec![],
            created_at_ms: ctx.timestamp_ms,
        };

        let scored = score_signal(&ctx, signal);
        assert!(scored.score >= 0.55);
    }

    #[test]
    fn penalizes_wide_spread() {
        let mut ctx = dummy_ctx();
        ctx.orderbook.spread_bps = Some(1.8);

        let signal = StrategySignal {
            action: StrategyAction::ShadowSignal,
            strategy_id: Some(StrategyId::LvnLiquidityVacuumBreakout),
            side: Some(Side::Long),
            entry_price: Some(3500.0),
            stop_price: Some(3470.0),
            target_price: Some(3550.0),
            score: 0.0,
            ttl_ms: 300000,
            evidence: vec![
                "thin_zone_above".into(),
                "vwap_reclaim_or_above".into(),
                "positive_delta".into(),
                "cvd_positive".into(),
                "target_next_HVN_or_VAH".into(),
            ],
            missing: vec![],
            invalidation: vec![],
            created_at_ms: ctx.timestamp_ms,
        };

        let scored = score_signal(&ctx, signal);
        // Would be 0.85 without penalty, minus 0.15 = 0.70
        assert!(scored.score < 0.85);
    }
}
