use crate::strategy::types::*;

pub fn toxic_flow_gate(ctx: &StrategyMarketContext, cfg: &StrategyConfig) -> Result<(), String> {
    if matches!(ctx.regime, Regime::Stress | Regime::Aftermath) {
        return Err("BLOCKED_REGIME".to_string());
    }

    if ctx.flow.quality != DataQuality::Live {
        return Err("FLOW_NOT_LIVE".to_string());
    }

    if ctx.volume_profile.quality != DataQuality::Live {
        return Err("VOLUME_PROFILE_NOT_LIVE".to_string());
    }

    if ctx.orderbook.quality != DataQuality::Live {
        return Err("ORDERBOOK_NOT_LIVE".to_string());
    }

    if let Some(spread) = ctx.orderbook.spread_bps
        && spread > cfg.max_spread_bps
    {
        return Err("SPREAD_TOO_WIDE".to_string());
    }

    if let Some(vpin) = ctx.flow.vpin
        && vpin > cfg.max_vpin
    {
        return Err("VPIN_TOXIC".to_string());
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_valid_ctx() -> StrategyMarketContext {
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
                hvn_nearby: vec![99500.0],
                lvn_nearby: vec![99800.0],
                value_location: ValueLocation::InValue,
                quality: DataQuality::Live,
            },
            vwap: VwapContext {
                vwap_session: Some(99950.0),
                avwap_bos: Some(99800.0),
                avwap_event: None,
                price_vs_vwap: PriceRelation::Above,
                price_vs_avwap_bos: PriceRelation::Above,
                price_vs_avwap_event: PriceRelation::Unknown,
                quality: DataQuality::Live,
            },
            flow: OrderFlowContext {
                cvd: Some(1500.0),
                cvd_slope: Some(0.5),
                delta: Some(200.0),
                taker_imbalance: Some(0.15),
                buy_volume: Some(5000.0),
                sell_volume: Some(4800.0),
                vpin: Some(0.45),
                cvd_divergence: None,
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
                obi_l20: Some(-0.01),
                microprice: Some(100005.0),
                spread_bps: Some(0.8),
                walls_above: vec![100500.0],
                walls_below: vec![99200.0],
                thin_zone_above: false,
                thin_zone_below: false,
                quality: DataQuality::Live,
            },
        }
    }

    fn default_cfg() -> StrategyConfig {
        StrategyConfig::default()
    }

    #[test]
    fn allows_valid_context() {
        let ctx = make_valid_ctx();
        assert!(toxic_flow_gate(&ctx, &default_cfg()).is_ok());
    }

    #[test]
    fn blocks_stress_regime() {
        let mut ctx = make_valid_ctx();
        ctx.regime = Regime::Stress;
        assert_eq!(
            toxic_flow_gate(&ctx, &default_cfg()),
            Err("BLOCKED_REGIME".to_string())
        );
    }

    #[test]
    fn blocks_aftermath_regime() {
        let mut ctx = make_valid_ctx();
        ctx.regime = Regime::Aftermath;
        assert_eq!(
            toxic_flow_gate(&ctx, &default_cfg()),
            Err("BLOCKED_REGIME".to_string())
        );
    }

    #[test]
    fn blocks_wide_spread() {
        let mut ctx = make_valid_ctx();
        ctx.orderbook.spread_bps = Some(3.5);
        assert_eq!(
            toxic_flow_gate(&ctx, &default_cfg()),
            Err("SPREAD_TOO_WIDE".to_string())
        );
    }

    #[test]
    fn blocks_toxic_vpin() {
        let mut ctx = make_valid_ctx();
        ctx.flow.vpin = Some(0.85);
        assert_eq!(
            toxic_flow_gate(&ctx, &default_cfg()),
            Err("VPIN_TOXIC".to_string())
        );
    }

    #[test]
    fn blocks_missing_flow_quality() {
        let mut ctx = make_valid_ctx();
        ctx.flow.quality = DataQuality::Stale;
        assert_eq!(
            toxic_flow_gate(&ctx, &default_cfg()),
            Err("FLOW_NOT_LIVE".to_string())
        );
    }
}
