use crate::session::{SessionPhase, TradingSession};
use crate::strategy::types::*;

const MIN_OBI_L5: f64 = 0.20;
const MIN_CVD_SLOPE: f64 = 0.15;
const MIN_FAST_SLOPE: f64 = 0.10;

fn rr_ok(entry: f64, stop: f64, target: f64, cfg: &StrategyConfig) -> bool {
    let risk = (entry - stop).abs();
    let reward = (target - entry).abs();
    risk > 1e-10 && reward / risk >= cfg.min_rr
}

fn valid_opening_session(ctx: &StrategyMarketContext) -> bool {
    ctx.session
        .as_ref()
        .map(|s| {
            matches!(s.phase, SessionPhase::OpeningRush)
                && matches!(
                    s.session,
                    TradingSession::London
                        | TradingSession::LondonNyOverlap
                        | TradingSession::NewYork
                )
        })
        .unwrap_or(false)
}

pub fn detect(ctx: &StrategyMarketContext, cfg: &StrategyConfig) -> Option<StrategySignal> {
    if !valid_opening_session(ctx) {
        return None;
    }

    let px = ctx.price;
    let atr = ctx.atr.unwrap_or(0.0);
    if atr <= 0.0 {
        return None;
    }

    let flow = &ctx.flow;
    let book = &ctx.orderbook;

    let long_ref = ctx.swing_high_20?;
    let long_setup = px > long_ref
        && flow.cvd_slope.unwrap_or(0.0) > MIN_CVD_SLOPE
        && book.thin_zone_above
        && book.obi_l5.unwrap_or(0.0) > MIN_OBI_L5
        && flow.fast_slope.unwrap_or(0.0) > MIN_FAST_SLOPE
        && book.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps;

    if long_setup {
        let entry = px;
        let stop = entry - 0.5 * atr;
        let target = long_ref + atr;
        if target > entry && rr_ok(entry, stop, target, cfg) {
            return Some(StrategySignal {
                action: StrategyAction::ShadowSignal,
                strategy_id: Some(StrategyId::SessionOpenBreakout),
                side: Some(Side::Long),
                regime: ctx.regime,
                entry_price: Some(entry),
                stop_price: Some(stop),
                target_price: Some(target),
                score: 0.0,
                ttl_ms: cfg.default_ttl_ms,
                evidence: vec![
                    "opening_rush".into(),
                    "breaks_recent_high".into(),
                    "cvd_slope_positive".into(),
                    "thin_zone_above".into(),
                    "obi_l5_bid_dominant".into(),
                    "fast_slope_positive".into(),
                ],
                missing: vec![],
                invalidation: vec![
                    "price_reclaims_breakout_level".into(),
                    "cvd_turns_negative".into(),
                    "opening_momentum_fades".into(),
                ],
                created_at_ms: ctx.timestamp_ms,
            });
        }
    }

    let short_ref = ctx.swing_low_20?;
    let short_setup = px < short_ref
        && flow.cvd_slope.unwrap_or(0.0) < -MIN_CVD_SLOPE
        && book.thin_zone_below
        && book.obi_l5.unwrap_or(0.0) < -MIN_OBI_L5
        && flow.fast_slope.unwrap_or(0.0) < -MIN_FAST_SLOPE
        && book.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps;

    if short_setup {
        let entry = px;
        let stop = entry + 0.5 * atr;
        let target = short_ref - atr;
        if target < entry && rr_ok(entry, stop, target, cfg) {
            return Some(StrategySignal {
                action: StrategyAction::ShadowSignal,
                strategy_id: Some(StrategyId::SessionOpenBreakout),
                side: Some(Side::Short),
                regime: ctx.regime,
                entry_price: Some(entry),
                stop_price: Some(stop),
                target_price: Some(target),
                score: 0.0,
                ttl_ms: cfg.default_ttl_ms,
                evidence: vec![
                    "opening_rush".into(),
                    "breaks_recent_low".into(),
                    "cvd_slope_negative".into(),
                    "thin_zone_below".into(),
                    "obi_l5_ask_dominant".into(),
                    "fast_slope_negative".into(),
                ],
                missing: vec![],
                invalidation: vec![
                    "price_reclaims_breakout_level".into(),
                    "cvd_turns_positive".into(),
                    "opening_momentum_fades".into(),
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
    use crate::session::{SessionContext, SessionPhase, TradingSession};

    fn base_ctx() -> StrategyMarketContext {
        StrategyMarketContext {
            symbol: "BTCUSDT".into(),
            timestamp_ms: 7 * 3600 * 1000,
            price: 100_020.0,
            regime: Regime::Expansion,
            atr: Some(100.0),
            volume_profile: VolumeProfileContext {
                poc: Some(99_500.0),
                vah: Some(99_900.0),
                val: Some(99_100.0),
                hvn_nearby: vec![],
                lvn_nearby: vec![],
                value_location: ValueLocation::AboveVah,
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
                delta: Some(300.0),
                taker_imbalance: Some(0.30),
                buy_volume: Some(800.0),
                sell_volume: Some(500.0),
                vpin: Some(0.30),
                cvd_divergence: None,
                footprint_absorption: AbsorptionSide::None,
                stacked_imbalance: ImbalanceSide::Bullish,
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
                fast_slope: Some(0.15),
                footprint_levels: vec![],
                oi_delta_zscore: None,
            },
            orderbook: OrderBookContext {
                obi_l5: Some(0.30),
                obi_l10: Some(0.20),
                obi_l20: Some(0.10),
                microprice: None,
                spread_bps: Some(0.5),
                walls_above: vec![],
                walls_below: vec![],
                thin_zone_above: true,
                thin_zone_below: false,
                quality: DataQuality::Live,
                spoof: None,
            },
            institutional: None,
            swing_high_20: Some(100_000.0),
            swing_low_20: Some(99_000.0),
            market_structure: None,
            session: Some(SessionContext {
                session: TradingSession::London,
                phase: SessionPhase::OpeningRush,
                minutes_since_open: 0,
                minutes_until_close: 360,
            }),
            order_blocks: None,
            fvg: None,
            leverage: 1.0,
            prev_obi_l5: None,
        }
    }

    #[test]
    fn detects_long_session_open_breakout() {
        let signal = detect(&base_ctx(), &StrategyConfig::default()).unwrap();
        assert_eq!(signal.strategy_id, Some(StrategyId::SessionOpenBreakout));
        assert_eq!(signal.side, Some(Side::Long));
    }

    #[test]
    fn rejects_outside_opening_rush() {
        let mut ctx = base_ctx();
        ctx.session.as_mut().unwrap().phase = SessionPhase::Mid;
        assert!(detect(&ctx, &StrategyConfig::default()).is_none());
    }
}
