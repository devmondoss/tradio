use super::trade_state::StructuralLevels;
use super::types::{Side, StrategyMarketContext, StrategySignal};

pub struct TargetSelector;

impl TargetSelector {
    /// Builds StructuralLevels from a signal + market context.
    ///
    /// Primary target: taken directly from `signal.target_price`.
    /// Intermediate level: nearest HVN between entry and target (for break-even trigger).
    ///
    /// Returns None if ATR is unavailable, target is missing, or direction is invalid.
    pub fn from_signal(
        signal: &StrategySignal,
        ctx: Option<&StrategyMarketContext>,
        side: Side,
    ) -> Option<StructuralLevels> {
        let ctx = ctx?;
        let atr = ctx.atr.filter(|&a| a > 0.0)?;
        let entry = signal.entry_price?;
        let target = signal.target_price?;

        let valid_dir = match side {
            Side::Long => target > entry,
            Side::Short => target < entry,
        };
        if !valid_dir {
            return None;
        }

        // Reject if R:R < 1.5 — win rate ~25% requires at least 1.5R to approach breakeven
        // after fees. Signals with tiny targets (e.g. VAH 28pts away, stop 155pts away = 0.18R)
        // would always be fee-negative regardless of outcome.
        let stop = signal.stop_price?;
        let risk = (entry - stop).abs();
        let reward = (target - entry).abs();
        if risk > 0.0 && reward / risk < 1.5 {
            log::debug!(
                "[target_selector] Rechazado R:R={:.2} (reward={:.1}, risk={:.1})",
                reward / risk,
                reward,
                risk
            );
            return None;
        }

        let atr_distance = (target - entry).abs() / atr;

        // Find intermediate: nearest HVN strictly between entry and target.
        let intermediate = {
            let mut candidates: Vec<f64> = ctx
                .volume_profile
                .hvn_nearby
                .iter()
                .copied()
                .filter(|&hvn| {
                    let in_range = match side {
                        Side::Long => hvn > entry && hvn < target,
                        Side::Short => hvn < entry && hvn > target,
                    };
                    in_range && (hvn - entry).abs() > atr * 0.25
                })
                .collect();
            candidates.sort_by(|a, b| {
                let da = (a - entry).abs();
                let db = (b - entry).abs();
                da.partial_cmp(&db).unwrap_or(std::cmp::Ordering::Equal)
            });
            candidates.into_iter().next()
        };

        Some(StructuralLevels {
            target,
            intermediate,
            atr_distance,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::strategy::types::*;


    fn make_signal(entry: f64, stop: f64, target: f64, side: Side) -> StrategySignal {
        StrategySignal {
            action: StrategyAction::ShadowSignal,
            strategy_id: None,
            side: Some(side),
            regime: Regime::TrendUp,
            entry_price: Some(entry),
            stop_price: Some(stop),
            target_price: Some(target),
            score: 0.0,
            ttl_ms: 0,
            evidence: vec![],
            missing: vec![],
            invalidation: vec![],
            created_at_ms: 0,
        }
    }

    fn make_ctx(atr: f64) -> StrategyMarketContext {
        StrategyMarketContext {
            symbol: "BTCUSDT".into(),
            timestamp_ms: 0,
            price: 78130.0,
            regime: Regime::TrendUp,
            atr: Some(atr),
            volume_profile: VolumeProfileContext {
                poc: None,
                vah: None,
                val: None,
                hvn_nearby: vec![],
                lvn_nearby: vec![],
                value_location: ValueLocation::InValue,
                quality: DataQuality::Live,
            },
            vwap: VwapContext {
                vwap_session: None,
                avwap_bos: None,
                avwap_event: None,
                price_vs_vwap: PriceRelation::Unknown,
                price_vs_avwap_bos: PriceRelation::Unknown,
                price_vs_avwap_event: PriceRelation::Unknown,
                quality: DataQuality::Live,
            },
            flow: OrderFlowContext {
                cvd: None,
                cvd_slope: None,
                delta: None,
                taker_imbalance: None,
                buy_volume: None,
                sell_volume: None,
                vpin: None,
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
                footprint_levels: vec![],
                oi_delta_zscore: None,
            },
            orderbook: OrderBookContext {
                obi_l5: None,
                obi_l10: None,
                obi_l20: None,
                microprice: None,
                spread_bps: None,
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
            leverage: 1.0,
            prev_obi_l5: None,
            slow_slope: None,
        }
    }

    #[test]
    fn rejects_rr_below_1_5() {
        // Replicates the May-17 case: entry=78130, stop=77985, target=78168
        // risk=145, reward=38, R:R=0.26 → must reject
        let signal = make_signal(78130.0, 77985.0, 78168.0, Side::Long);
        let ctx = make_ctx(155.0);
        assert!(
            TargetSelector::from_signal(&signal, Some(&ctx), Side::Long).is_none(),
            "R:R=0.26 should be rejected (< 1.5)"
        );
    }

    #[test]
    fn accepts_rr_above_1_5() {
        // entry=78130, stop=77985 (risk=145), target=78360 (reward=230, R:R=1.59) → accept
        let signal = make_signal(78130.0, 77985.0, 78360.0, Side::Long);
        let ctx = make_ctx(155.0);
        assert!(
            TargetSelector::from_signal(&signal, Some(&ctx), Side::Long).is_some(),
            "R:R=1.50 should be accepted"
        );
    }

    #[test]
    fn rejects_short_rr_below_1_5() {
        // entry=77000, stop=77200 (risk=200), target=76850 (reward=150, R:R=0.75) → reject
        let signal = make_signal(77000.0, 77200.0, 76850.0, Side::Short);
        let ctx = make_ctx(200.0);
        assert!(
            TargetSelector::from_signal(&signal, Some(&ctx), Side::Short).is_none(),
            "Short R:R=0.75 should be rejected"
        );
    }

    #[test]
    fn returns_none_without_ctx() {
        let signal = make_signal(78130.0, 77985.0, 78500.0, Side::Long);
        assert!(TargetSelector::from_signal(&signal, None, Side::Long).is_none());
    }
}
