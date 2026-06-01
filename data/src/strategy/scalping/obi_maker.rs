//! S1 — OBI Micro-Price Maker Reversion
//!
//! Market-making sesgado: el sistema detecta cuándo el OBI tiene convicción
//! suficiente para emitir una señal direccional post-only.
//!
//! Basado en Cont-Kukanov-Stoikov 2014: el micro-precio predice mejor el
//! precio futuro inmediato que el mid-price simple.

use crate::strategy::types::Side;
use super::{ScalpingContext, ScalpingSignal, ScalpingStrategyId, is_scalping_session};
use crate::strategy::types::ScalpingConfig;

/// Evalúa si el OBI tiene convicción suficiente para sesgarse en un lado.
///
/// Retorna `Some(side)` si la señal es válida para emitir cotización.
pub fn detect(ctx: &ScalpingContext, cfg: &ScalpingConfig) -> Option<ScalpingSignal> {
    // Gate 1: solo sesiones operativas
    if !is_scalping_session(ctx.session) {
        return None;
    }

    // Gate 2: spread aceptable (libro sano)
    if ctx.spread_ticks > cfg.max_spread_ticks {
        return None;
    }

    // Gate 3: sin evento de volatilidad extrema (VR > 5 = detener cotización)
    if ctx.vr > cfg.max_vr {
        return None;
    }

    // Gate 4: OBI tiene convicción mínima (|OBI - 0.5| ≥ 0.05)
    let obi_deviation = (ctx.obi_ema_fast - 0.5).abs();
    if obi_deviation < 0.05 {
        return None;
    }

    // Determinar el lado por el OBI sesgado
    let side = if ctx.obi_ema_fast >= cfg.obi_threshold_long && ctx.cvd > 0.0 {
        Side::Long
    } else if ctx.obi_ema_fast <= cfg.obi_threshold_short && ctx.cvd < 0.0 {
        Side::Short
    } else {
        return None;
    };

    // Gate 5: micro-precio confirma la dirección (micro > close para long, micro < close para short)
    let micro_confirms = match side {
        Side::Long => ctx.micro_price > ctx.bar_close,
        Side::Short => ctx.micro_price < ctx.bar_close,
    };
    if !micro_confirms {
        return None;
    }

    // Calcular quotes y targets
    let atr = ctx.atr.max(10.0);
    let tick = 0.10_f64;

    // Half-spread dinámico basado en ATR (C1=2.0, en USD)
    let vol_estimate = (atr / 100.0).max(1.0);
    let half = 2.0 * vol_estimate * tick;

    // Skew por OBI
    let skew = 4.0 * (ctx.obi_ema_fast - 0.5) * tick;

    let (entry, sl, tp1, tp2) = match side {
        Side::Long => {
            let bid_px = round_tick(ctx.micro_price - half + skew);
            let be_move = 0.00040 * bid_px;
            let target = (be_move * 1.2).max(4.0 * tick);
            let tp1 = round_tick(bid_px + target);        // 1R
            let tp2 = round_tick(bid_px + target * 1.5);  // 1.5R
            let sl  = round_tick(bid_px - target * 0.65); // RR = 1/0.65 = 1.54
            (bid_px, sl, tp1, tp2)
        }
        Side::Short => {
            let ask_px = round_tick(ctx.micro_price + half + skew);
            let be_move = 0.00040 * ask_px;
            let target = (be_move * 1.2).max(4.0 * tick);
            let tp1 = round_tick(ask_px - target);
            let tp2 = round_tick(ask_px - target * 1.5);
            let sl  = round_tick(ask_px + target * 0.65);
            (ask_px, sl, tp1, tp2)
        }
    };

    let risk = (entry - sl).abs().max(tick);
    let reward = (tp1 - entry).abs();
    let rr = reward / risk;

    if rr < cfg.min_rr {
        return None;
    }

    let evidence = build_evidence(ctx, side, obi_deviation);

    Some(ScalpingSignal {
        strategy: ScalpingStrategyId::ObiMaker,
        side,
        entry_price: entry,
        stop_price: sl,
        tp1_price: tp1,
        tp2_price: tp2,
        rr,
        conviction_score: (obi_deviation * 200.0).clamp(55.0, 95.0),
        entry_type: "post_only".to_string(),
        timestamp_ms: ctx.timestamp_ms,
        evidence,
    })
}

fn round_tick(price: f64) -> f64 {
    (price / 0.10).round() * 0.10
}

fn build_evidence(ctx: &ScalpingContext, side: Side, obi_dev: f64) -> Vec<String> {
    let mut ev = Vec::new();
    match side {
        Side::Long => ev.push("S1:OBI_LONG_BIAS".into()),
        Side::Short => ev.push("S1:OBI_SHORT_BIAS".into()),
    }
    ev.push(format!("obi_fast={:.3}", ctx.obi_ema_fast));
    ev.push(format!("obi_dev={:.3}", obi_dev));
    ev.push(format!("micro_price={:.2}", ctx.micro_price));
    ev.push(format!("cvd={:.2}", ctx.cvd));
    ev.push(format!("spread_ticks={}", ctx.spread_ticks));
    ev
}
