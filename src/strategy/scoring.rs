use super::types::*;

// VALORES DE ARRANQUE — se tunean en Fase D con datos reales

// --- Pesos de la suma ponderada (deben sumar 1.0) ---
const W_CVD_SLOPE: f64 = 0.25;
const W_TAKER_IMBALANCE: f64 = 0.20;
const W_DELTA_ALIGNED: f64 = 0.10; // binary: normalization vs. instrument volume → taker_imbalance ya cubre la magnitud
const W_TARGET_ATR_DIST: f64 = 0.25;
const W_RR: f64 = 0.20;

// --- Rampas: cvd_slope (signed, aligned with side) ---
const CVD_SLOPE_MIN: f64 = 0.0;
const CVD_SLOPE_MAX: f64 = 1.0;

// --- Rampas: taker_imbalance (signed, aligned with side, range -1..+1) ---
const TAKER_IMBAL_MIN: f64 = 0.0;
const TAKER_IMBAL_MAX: f64 = 0.5;

// --- Rampas: distancia entry→target en múltiplos de ATR ---
const TARGET_ATR_MIN: f64 = 0.5;
const TARGET_ATR_MAX: f64 = 3.0;

// --- Rampas: R:R (reward/risk) ---
const RR_MIN: f64 = 1.0;
const RR_MAX: f64 = 3.0;

// --- Factor VPIN ---
const VPIN_TOXIC: f64 = 0.75; // por encima: zona de veto
const VPIN_CLEAN: f64 = 0.30; // por debajo: amplificador
const FACTOR_VPIN_TOXIC: f64 = 0.35; // veto fuerte
const FACTOR_VPIN_CLEAN: f64 = 1.20; // amplificador moderado
const VETO_SCORE_CAP: f64 = 0.25; // techo absoluto cuando vpin es tóxico

// --- Factor spread ---
const SPREAD_BAD_BPS: f64 = 1.5;
const FACTOR_SPREAD_BAD: f64 = 0.80;

// --- Factor regime ---
const FACTOR_REGIME_WITH: f64 = 1.15;
const FACTOR_REGIME_AGAINST: f64 = 0.70;

// --- Factor confluencia (proximidad de niveles de VP al target) ---
const CONFLUENCE_TOL_ATR: f64 = 0.25; // tolerancia: 0.25 × ATR
const FACTOR_CONFLUENCIA_2: f64 = 1.10;
const FACTOR_CONFLUENCIA_3: f64 = 1.20;

/// Mapea `value` linealmente de [min, max] a [0.0, 1.0], clampeado.
fn ramp(value: f64, min: f64, max: f64) -> f64 {
    if value <= min {
        0.0
    } else if value >= max {
        1.0
    } else {
        (value - min) / (max - min)
    }
}

pub fn score_signal(ctx: &StrategyMarketContext, mut signal: StrategySignal) -> StrategySignal {
    let is_long = matches!(signal.side, Some(Side::Long));
    let sign = if is_long { 1.0_f64 } else { -1.0_f64 };
    let atr = ctx.atr.unwrap_or(0.0);

    // --- Suma ponderada por magnitud ---

    // CVD slope alineado con el side
    let w_cvd = {
        let signed = ctx.flow.cvd_slope.unwrap_or(0.0) * sign;
        W_CVD_SLOPE * ramp(signed, CVD_SLOPE_MIN, CVD_SLOPE_MAX)
    };

    // Taker imbalance alineado (ya está en [-1, 1], cubre la magnitud del delta normalizado)
    let w_taker = {
        let signed = ctx.flow.taker_imbalance.unwrap_or(0.0) * sign;
        W_TAKER_IMBALANCE * ramp(signed, TAKER_IMBAL_MIN, TAKER_IMBAL_MAX)
    };

    // Delta alineado: binario — magnitud cruda no es normalizable cross-instrument
    let w_delta = {
        let signed = ctx.flow.delta.map(|d| d * sign).unwrap_or(0.0);
        if signed > 0.0 { W_DELTA_ALIGNED } else { 0.0 }
    };

    // Distancia entry→target en múltiplos de ATR
    let w_target_dist = if atr > 0.0 {
        let entry = signal.entry_price.unwrap_or(ctx.price);
        let target = signal.target_price.unwrap_or(entry);
        let dist_atr = (target - entry).abs() / atr;
        W_TARGET_ATR_DIST * ramp(dist_atr, TARGET_ATR_MIN, TARGET_ATR_MAX)
    } else {
        0.0
    };

    // Calidad del R:R
    let w_rr = match (signal.entry_price, signal.stop_price, signal.target_price) {
        (Some(entry), Some(stop), Some(target)) => {
            let risk = (entry - stop).abs();
            let reward = (target - entry).abs();
            if risk > 0.0 {
                W_RR * ramp(reward / risk, RR_MIN, RR_MAX)
            } else {
                0.0
            }
        }
        _ => 0.0,
    };

    let base_score = w_cvd + w_taker + w_delta + w_target_dist + w_rr;

    // --- Factores moduladores ---

    // factor_vpin: veto fuerte si tóxico, amplificador si limpio, interpolación entre medias
    let vpin_is_toxic = ctx.flow.vpin.map(|v| v > VPIN_TOXIC).unwrap_or(false);
    let factor_vpin = match ctx.flow.vpin {
        None => 1.0,
        Some(v) if v > VPIN_TOXIC => FACTOR_VPIN_TOXIC,
        Some(v) if v < VPIN_CLEAN => FACTOR_VPIN_CLEAN,
        Some(v) => {
            let t = (v - VPIN_CLEAN) / (VPIN_TOXIC - VPIN_CLEAN);
            FACTOR_VPIN_CLEAN + t * (1.0 - FACTOR_VPIN_CLEAN)
        }
    };

    // factor_spread: penaliza spreads anchos, spread normal no premia
    let factor_spread = match ctx.orderbook.spread_bps {
        Some(s) if s > SPREAD_BAD_BPS => FACTOR_SPREAD_BAD,
        _ => 1.0,
    };

    // factor_regime: premia si el régimen favorece la señal, castiga si la contradice
    let factor_regime = match (ctx.regime, is_long) {
        (Regime::TrendUp, true) => FACTOR_REGIME_WITH,
        (Regime::TrendDown, false) => FACTOR_REGIME_WITH,
        (Regime::Expansion, _) => FACTOR_REGIME_WITH,
        (Regime::TrendDown, true) => FACTOR_REGIME_AGAINST,
        (Regime::TrendUp, false) => FACTOR_REGIME_AGAINST,
        _ => 1.0, // Chop, Compression, Stress, Aftermath, Unknown → neutral
    };

    // factor_confluencia: cuántos niveles de VP caen dentro de ±0.25 ATR del target
    let factor_confluencia = if atr > 0.0 {
        let target = signal.target_price.unwrap_or(f64::NAN);
        if target.is_finite() {
            let tol = atr * CONFLUENCE_TOL_ATR;
            let vp = &ctx.volume_profile;
            let count = [vp.poc, vp.vah, vp.val]
                .into_iter()
                .flatten()
                .chain(vp.hvn_nearby.iter().copied())
                .filter(|&lvl| (lvl - target).abs() <= tol)
                .count();
            match count {
                0 | 1 => 1.0,
                2 => FACTOR_CONFLUENCIA_2,
                _ => FACTOR_CONFLUENCIA_3,
            }
        } else {
            1.0
        }
    } else {
        1.0
    };

    let mut score = base_score * factor_vpin * factor_spread * factor_regime * factor_confluencia;

    // Regla de precedencia del veto: VPIN tóxico clampea sin importar amplificadores
    if vpin_is_toxic {
        score = score.min(VETO_SCORE_CAP);
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

    // Una señal fuerte requiere flujo alineado con el side. dummy_ctx tiene flujo alcista
    // (cvd_slope > 0, delta > 0, taker_imbalance > 0). Para testear un short fuerte hay
    // que ajustar el contexto; para testear un long fuerte el contexto ya es favorable.
    #[test]
    fn scores_high_evidence_signal() {
        let mut ctx = dummy_ctx();
        // Hacemos el contexto bearish para alinear con la señal Short
        ctx.regime = Regime::TrendDown;
        ctx.flow.cvd_slope = Some(-0.8);
        ctx.flow.delta = Some(-200.0);
        ctx.flow.taker_imbalance = Some(-0.4);
        ctx.flow.vpin = Some(0.25); // limpio → amplifica

        let signal = StrategySignal {
            action: StrategyAction::ShadowSignal,
            strategy_id: Some(StrategyId::ValueAreaFailedAuction),
            side: Some(Side::Short),
            entry_price: Some(100050.0),
            stop_price: Some(100300.0),  // risk = 250 = 1 ATR
            target_price: Some(99000.0), // reward = 1050, R:R ≈ 4.2
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
        // base ≈ 0.91, × vpin(1.20) × spread(1.0) × regime_with(1.15) → > 1.0 → capped at 1.0
        assert!(
            scored.score > 0.85,
            "strong aligned signal should score high, got {}",
            scored.score
        );
    }

    #[test]
    fn penalizes_wide_spread() {
        let mut ctx = dummy_ctx();
        ctx.orderbook.spread_bps = Some(1.8); // ancho → factor 0.80

        // Señal long alineada con TrendUp (regime = TrendUp en dummy_ctx)
        let signal = StrategySignal {
            action: StrategyAction::ShadowSignal,
            strategy_id: Some(StrategyId::LvnLiquidityVacuumBreakout),
            side: Some(Side::Long),
            entry_price: Some(100000.0),
            stop_price: Some(99750.0),    // risk = 250 = 1 ATR
            target_price: Some(100750.0), // reward = 750 = 3 ATR, R:R = 3.0
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
        // base ≈ 0.665, × vpin(≈1.156) × spread_malo(0.80) × regime_with(1.15) ≈ 0.71
        // Sin spread ancho sería ≈ 0.88 — el penalty es visible y la señal pasa de > 0.85 a < 0.85
        assert!(
            scored.score < 0.85,
            "wide spread should pull score below 0.85, got {}",
            scored.score
        );
        assert!(
            scored.score > 0.50,
            "signal should still be meaningful despite spread, got {}",
            scored.score
        );
    }

    #[test]
    fn veto_vpin_clamps_score() {
        let mut ctx = dummy_ctx();
        ctx.flow.vpin = Some(0.85); // tóxico → veto
        // Incluso con un régimen y flujo perfectos el veto clampea
        ctx.regime = Regime::TrendUp;
        ctx.flow.cvd_slope = Some(1.0);
        ctx.flow.taker_imbalance = Some(0.5);

        let signal = StrategySignal {
            action: StrategyAction::ShadowSignal,
            strategy_id: Some(StrategyId::LvnLiquidityVacuumBreakout),
            side: Some(Side::Long),
            entry_price: Some(100000.0),
            stop_price: Some(99750.0),
            target_price: Some(100750.0),
            score: 0.0,
            ttl_ms: 300000,
            evidence: vec![],
            missing: vec![],
            invalidation: vec![],
            created_at_ms: ctx.timestamp_ms,
        };

        let scored = score_signal(&ctx, signal);
        assert!(
            scored.score <= VETO_SCORE_CAP,
            "toxic VPIN must cap score at {VETO_SCORE_CAP}, got {}",
            scored.score
        );
    }
}
