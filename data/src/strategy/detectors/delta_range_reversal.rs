use crate::detectors::range_detector::RangeLocation;
use crate::strategy::types::*;

use super::toxic_flow_gate::toxic_flow_gate;

fn rr_ok(entry: f64, stop: f64, target: f64, cfg: &StrategyConfig) -> bool {
    let risk = (entry - stop).abs();
    let reward = (target - entry).abs();
    risk > 1e-10 && reward / risk >= cfg.min_rr
}

/// Delta Range Reversal con Absorción
///
/// Tesis: en un rango intradía, el precio llega a un extremo (Range High o Range Low)
/// con agresión de flujo (delta negativo en low, positivo en high) pero falla en
/// aceptar fuera del rango. Los agresivos quedan atrapados. Se opera la reversión
/// hacia el POC del rango o el extremo opuesto.
///
/// Solo opera en extremos — el centro del rango (35–65%) es zona de no-trade.
pub fn detect(ctx: &StrategyMarketContext, cfg: &StrategyConfig) -> Option<StrategySignal> {
    toxic_flow_gate(ctx, cfg).ok()?;

    let px = ctx.price;
    let atr = ctx.atr?;
    if atr <= 0.0 {
        return None;
    }

    let range = ctx.range.as_ref()?;
    if !range.valid {
        return None;
    }

    let flow = &ctx.flow;

    // ── LONG: Range Low Absorption ────────────────────────────────────────────
    //
    // Condición: precio en o cerca del extremo inferior del rango.
    // Los vendedores agresivos intentaron romper pero el precio no aceptó abajo.
    // Se busca el POC del rango o el mid como target.

    let at_low = matches!(
        range.location,
        RangeLocation::NearLow | RangeLocation::OutsideLow
    );

    if at_low && !range.breakout_down {
        // Agresión vendedora: entraron vendedores takers (delta < 0 o taker_imbalance sell-side)
        let sell_aggression =
            flow.delta.unwrap_or(0.0) < 0.0 || flow.taker_imbalance.unwrap_or(0.0) < -0.05;

        // Absorción: al menos una señal confirma que los vendedores fueron absorbidos
        let absorbed = flow.footprint_absorption == AbsorptionSide::Bid
            || flow.big_trade_bearish // gran venta pero precio no cayó → trampa
            || matches!(
                flow.cvd_divergence,
                Some(CvdDivergence::BullishAbsorption)
            )
            || flow.finish_action_bullish // exhaustión de vendedores en mínimo
            || range.sweep_range_low; // mecha bajo rango + cierre dentro = absorción

        // Trigger: alguna señal de que los vendedores están cediendo
        let trigger = range.sweep_range_low
            || flow.sweep_confirmed
            || flow.failed_acceptance
            || flow.delta.unwrap_or(-1.0) > 0.0 // delta flipeó positivo
            || flow.stacked_imbalance == ImbalanceSide::Bullish;

        // Red flags: señales de ruptura real hacia abajo
        let cvd_breaking_down = flow.cvd_slope.unwrap_or(0.0) < -0.25;
        let accepted_below =
            px < range.range_low && !flow.failed_acceptance && !range.sweep_range_low;

        if sell_aggression && absorbed && trigger && !cvd_breaking_down && !accepted_below {
            let entry = px;
            // Stop debajo del low de la barrida — al menos 0.3 ATR bajo precio
            let stop = f64::min(range.range_low - 0.5 * atr, px - 0.3 * atr);
            // TP1: POC del rango o midpoint
            let target = range.range_poc.unwrap_or(range.range_mid);

            if target > entry && rr_ok(entry, stop, target, cfg) {
                let mut evidence = vec!["range_low_absorption".into()];
                if range.sweep_range_low {
                    evidence.push("sweep_range_low_reclaim".into());
                }
                if flow.footprint_absorption == AbsorptionSide::Bid {
                    evidence.push("footprint_absorption_bid".into());
                }
                if flow.big_trade_bearish {
                    evidence.push("big_trade_trapped_sellers".into());
                }
                if matches!(flow.cvd_divergence, Some(CvdDivergence::BullishAbsorption)) {
                    evidence.push("cvd_bullish_divergence".into());
                }
                if flow.finish_action_bullish {
                    evidence.push("finish_action_exhaustion".into());
                }
                if flow.delta.unwrap_or(-1.0) > 0.0 {
                    evidence.push("delta_flip_positive".into());
                }
                if flow.failed_acceptance {
                    evidence.push("failed_acceptance_below_range".into());
                }
                if flow.stacked_imbalance == ImbalanceSide::Bullish {
                    evidence.push("stacked_imbalance_bullish".into());
                }
                // Informa target secundario (extremo opuesto) para análisis posterior
                evidence.push(format!("tp2_range_high:{:.0}", range.range_high));

                let mut missing = vec![];
                if flow.unfinish_action_bearish {
                    // Imán bajista en extremo inferior — puede volver a empujar
                    missing.push("unfinish_action_bearish_magnet".into());
                }
                if flow.cvd_divergence_persistence.unwrap_or(0) <= -2 {
                    // CVD sigue divergente bajista — cuidado
                    missing.push("cvd_divergence_still_bearish".into());
                }

                return Some(StrategySignal {
                    action: StrategyAction::ShadowSignal,
                    strategy_id: Some(StrategyId::DeltaRangeReversal),
                    side: Some(Side::Long),
                    regime: ctx.regime,
                    entry_price: Some(entry),
                    stop_price: Some(stop),
                    target_price: Some(target),
                    score: 0.0,
                    ttl_ms: cfg.default_ttl_ms,
                    evidence,
                    missing,
                    invalidation: vec![
                        "close_below_range_low".into(),
                        "cvd_breaks_down".into(),
                        "delta_continues_negative_after_reclaim".into(),
                    ],
                    created_at_ms: ctx.timestamp_ms,
                });
            }
        }
    }

    // ── SHORT: Range High Absorption ──────────────────────────────────────────
    //
    // Condición: precio en o cerca del extremo superior del rango.
    // Los compradores agresivos intentaron romper pero el precio no aceptó arriba.
    // Se busca el POC del rango o el mid como target.

    let at_high = matches!(
        range.location,
        RangeLocation::NearHigh | RangeLocation::OutsideHigh
    );

    if at_high && !range.breakout_up {
        let buy_aggression =
            flow.delta.unwrap_or(0.0) > 0.0 || flow.taker_imbalance.unwrap_or(0.0) > 0.05;

        let absorbed = flow.footprint_absorption == AbsorptionSide::Ask
            || flow.big_trade_bullish // gran compra pero precio no subió → trampa
            || matches!(
                flow.cvd_divergence,
                Some(CvdDivergence::BearishAbsorption)
            )
            || flow.finish_action_bearish // exhaustión de compradores en máximo
            || range.sweep_range_high;

        let trigger = range.sweep_range_high
            || flow.sweep_confirmed
            || flow.failed_acceptance
            || flow.delta.unwrap_or(1.0) < 0.0 // delta flipeó negativo
            || flow.stacked_imbalance == ImbalanceSide::Bearish;

        let cvd_breaking_up = flow.cvd_slope.unwrap_or(0.0) > 0.25;
        let accepted_above =
            px > range.range_high && !flow.failed_acceptance && !range.sweep_range_high;

        if buy_aggression && absorbed && trigger && !cvd_breaking_up && !accepted_above {
            let entry = px;
            let stop = f64::max(range.range_high + 0.5 * atr, px + 0.3 * atr);
            let target = range.range_poc.unwrap_or(range.range_mid);

            if target < entry && rr_ok(entry, stop, target, cfg) {
                let mut evidence = vec!["range_high_absorption".into()];
                if range.sweep_range_high {
                    evidence.push("sweep_range_high_reclaim".into());
                }
                if flow.footprint_absorption == AbsorptionSide::Ask {
                    evidence.push("footprint_absorption_ask".into());
                }
                if flow.big_trade_bullish {
                    evidence.push("big_trade_trapped_buyers".into());
                }
                if matches!(flow.cvd_divergence, Some(CvdDivergence::BearishAbsorption)) {
                    evidence.push("cvd_bearish_divergence".into());
                }
                if flow.finish_action_bearish {
                    evidence.push("finish_action_exhaustion".into());
                }
                if flow.delta.unwrap_or(1.0) < 0.0 {
                    evidence.push("delta_flip_negative".into());
                }
                if flow.failed_acceptance {
                    evidence.push("failed_acceptance_above_range".into());
                }
                if flow.stacked_imbalance == ImbalanceSide::Bearish {
                    evidence.push("stacked_imbalance_bearish".into());
                }
                evidence.push(format!("tp2_range_low:{:.0}", range.range_low));

                let mut missing = vec![];
                if flow.unfinish_action_bullish {
                    missing.push("unfinish_action_bullish_magnet".into());
                }
                if flow.cvd_divergence_persistence.unwrap_or(0) >= 2 {
                    missing.push("cvd_divergence_still_bullish".into());
                }

                return Some(StrategySignal {
                    action: StrategyAction::ShadowSignal,
                    strategy_id: Some(StrategyId::DeltaRangeReversal),
                    side: Some(Side::Short),
                    regime: ctx.regime,
                    entry_price: Some(entry),
                    stop_price: Some(stop),
                    target_price: Some(target),
                    score: 0.0,
                    ttl_ms: cfg.default_ttl_ms,
                    evidence,
                    missing,
                    invalidation: vec![
                        "close_above_range_high".into(),
                        "cvd_breaks_up".into(),
                        "delta_continues_positive_after_reclaim".into(),
                    ],
                    created_at_ms: ctx.timestamp_ms,
                });
            }
        }
    }

    None
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::detectors::range_detector::{RangeContext, RangeLocation};

    fn base_ctx_long() -> StrategyMarketContext {
        StrategyMarketContext {
            symbol: "BTCUSDT".into(),
            timestamp_ms: 1_710_000_000_000,
            price: 100_010.0,
            regime: Regime::Chop,
            atr: Some(250.0),
            volume_profile: VolumeProfileContext {
                poc: Some(100_100.0),
                vah: Some(100_200.0),
                val: Some(99_900.0),
                hvn_nearby: vec![100_100.0],
                lvn_nearby: vec![],
                value_location: ValueLocation::InValue,
                quality: DataQuality::Live,
                naked_pocs: vec![],
                single_prints: vec![],
            },
            vwap: VwapContext {
                vwap_session: Some(100_100.0),
                avwap_bos: None,
                avwap_event: None,
                price_vs_vwap: PriceRelation::Below,
                price_vs_avwap_bos: PriceRelation::Unknown,
                price_vs_avwap_event: PriceRelation::Unknown,
                quality: DataQuality::Live,
            },
            flow: OrderFlowContext {
                cvd: Some(-500.0),
                cvd_slope: Some(-0.05),
                delta: Some(-200.0),
                taker_imbalance: Some(-0.15),
                buy_volume: Some(3000.0),
                sell_volume: Some(4000.0),
                vpin: Some(0.40),
                cvd_divergence: Some(CvdDivergence::BullishAbsorption),
                footprint_absorption: AbsorptionSide::Bid,
                stacked_imbalance: ImbalanceSide::None,
                failed_acceptance: true,
                sweep_confirmed: true,
                mss_active: false,
                quality: DataQuality::Live,
                funding_rate: Some(0.0001),
                basis: Some(-0.01),
                oi_delta: Some(-50.0),
                oi_momentum_aligned: Some(false),
                bid_wall_nearby: true,
                ask_wall_nearby: false,
                price_action_clean: true,
                fast_slope: Some(-0.05),
                footprint_levels: vec![],
                oi_delta_zscore: None,
                vpin_cdf: None,
                cvd_divergence_persistence: Some(-3),
                finish_action_bullish: false,
                finish_action_bearish: false,
                unfinish_action_bullish: false,
                unfinish_action_bearish: false,
                big_trade_bullish: false,
                big_trade_bearish: false,
                delta_velocity: None,
            },
            orderbook: OrderBookContext {
                obi_l5: Some(0.10),
                obi_l10: Some(0.05),
                obi_l20: Some(0.02),
                microprice: Some(100_012.0),
                spread_bps: Some(0.8),
                walls_above: vec![],
                walls_below: vec![99_800.0],
                thin_zone_above: false,
                thin_zone_below: false,
                quality: DataQuality::Live,
                spoof: None,
            },
            institutional: None,
            swing_high_20: Some(100_200.0),
            swing_low_20: Some(99_900.0),
            market_structure: None,
            session: None,
            order_blocks: None,
            fvg: None,
            leverage: 10.0,
            prev_obi_l5: Some(0.08),
            slow_slope: Some(-0.02),
            auction_state: None,
            vp_open_bias: None,
            htf_vp: None,
            range: Some(RangeContext {
                valid: true,
                range_high: 100_180.0,
                range_low: 100_000.0,
                range_mid: 100_090.0,
                range_poc: Some(100_090.0),
                range_size: 180.0,
                range_size_atr: 0.72,
                touches_high: 3,
                touches_low: 4,
                bars_inside: 25,
                midline_slope: 0.01,
                location: RangeLocation::NearLow,
                no_trade_zone: false,
                sweep_range_low: true,
                sweep_range_high: false,
                breakout_up: false,
                breakout_down: false,
                quality: DataQuality::Live,
            }),
        }
    }

    fn default_cfg() -> StrategyConfig {
        StrategyConfig {
            enabled: true,
            ..StrategyConfig::default()
        }
    }

    #[test]
    fn detects_long_range_low_absorption() {
        let ctx = base_ctx_long();
        let cfg = default_cfg();
        let sig = detect(&ctx, &cfg);
        assert!(sig.is_some(), "should detect long setup at range low");
        let s = sig.unwrap();
        assert_eq!(s.side, Some(Side::Long));
        assert_eq!(s.strategy_id, Some(StrategyId::DeltaRangeReversal));
        assert!(s.target_price.unwrap() > s.entry_price.unwrap());
        assert!(s.stop_price.unwrap() < s.entry_price.unwrap());
    }

    #[test]
    fn no_signal_in_no_trade_zone() {
        let mut ctx = base_ctx_long();
        // Price in middle zone
        ctx.price = 100_090.0;
        if let Some(ref mut r) = ctx.range {
            r.location = RangeLocation::NoTrade;
            r.no_trade_zone = true;
        }
        let cfg = default_cfg();
        assert!(detect(&ctx, &cfg).is_none(), "no signal in no-trade zone");
    }

    #[test]
    fn no_signal_when_breakout_confirmed() {
        let mut ctx = base_ctx_long();
        if let Some(ref mut r) = ctx.range {
            r.breakout_down = true;
        }
        let cfg = default_cfg();
        assert!(
            detect(&ctx, &cfg).is_none(),
            "no long signal when breakout_down confirmed"
        );
    }

    #[test]
    fn no_signal_when_cvd_breaks_down() {
        let mut ctx = base_ctx_long();
        ctx.flow.cvd_slope = Some(-0.40);
        let cfg = default_cfg();
        assert!(
            detect(&ctx, &cfg).is_none(),
            "no signal when CVD is confirming breakdown"
        );
    }

    #[test]
    fn detects_short_range_high_absorption() {
        let mut ctx = base_ctx_long();
        ctx.price = 100_170.0;
        ctx.flow.delta = Some(300.0);
        ctx.flow.taker_imbalance = Some(0.20);
        ctx.flow.footprint_absorption = AbsorptionSide::Ask;
        ctx.flow.cvd_divergence = Some(CvdDivergence::BearishAbsorption);
        ctx.flow.failed_acceptance = true;
        ctx.flow.cvd_slope = Some(0.05);
        if let Some(ref mut r) = ctx.range {
            r.location = RangeLocation::NearHigh;
            r.sweep_range_low = false;
            r.sweep_range_high = true;
        }
        let cfg = default_cfg();
        let sig = detect(&ctx, &cfg);
        assert!(sig.is_some(), "should detect short setup at range high");
        let s = sig.unwrap();
        assert_eq!(s.side, Some(Side::Short));
        assert!(s.target_price.unwrap() < s.entry_price.unwrap());
        assert!(s.stop_price.unwrap() > s.entry_price.unwrap());
    }
}
