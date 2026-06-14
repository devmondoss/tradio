//! S2 — Absorción / Trapped Traders Reversal
//!
//! Cuando agresores atacan un extremo del rango con delta fuerte pero el precio
//! NO acepta afuera (vela cierra dentro del rango), los atrapados deben cerrar.
//! El fade captura el reversal hacia el POC del rango.
//!
//! Solo opera en ScalpingRegime::Range.

use super::{
    ScalpingContext, ScalpingRegime, ScalpingSignal, ScalpingStrategyId, is_scalping_session,
};
use crate::strategy::types::ScalpingConfig;
use crate::strategy::types::Side;

/// Score de absorción: cuantifica la intensidad de la señal.
///
/// Retorna un valor 0–100 basado en los componentes del documento.
pub fn absorption_score(ctx: &ScalpingContext, side: Side) -> f64 {
    let dz_abs = ctx.dz.abs();
    let vr = ctx.vr;

    fn intensity(value: f64, min_t: f64, strong_t: f64) -> f64 {
        ((value - min_t) / (strong_t - min_t).max(1e-9)).clamp(0.0, 1.0)
    }

    let mut score = 0.0;

    // Absorción base (peso 22): DZ fuerte en contra del precio
    let as_score = match side {
        Side::Long => (-ctx.dz).max(0.0), // delta negativo fuerte = vendedores absorbidos
        Side::Short => ctx.dz.max(0.0),   // delta positivo fuerte = compradores absorbidos
    };
    score += 22.0 * intensity(as_score, 1.5, 3.0);

    // Delta Z-score (peso 12)
    score += 12.0 * intensity(dz_abs, 1.5, 2.5);

    // Volume Ratio (peso 10)
    score += 10.0 * intensity(vr, 2.0, 4.0);

    // POC de la vela quedó en la mecha (peso 12)
    if let Some(poc) = ctx.candle_poc {
        let range_candle = (ctx.bar_high - ctx.bar_low).max(1e-9);
        let poc_wick = match side {
            // Long: POC en tercio inferior = vendedores absorbidos en la mecha
            Side::Long => (poc - ctx.bar_low) / range_candle,
            // Short: POC en tercio superior = compradores absorbidos en la mecha
            Side::Short => (ctx.bar_high - poc) / range_candle,
        };
        // poc_wick cerca de 0 = POC en la mecha correcta
        let poc_signal = (0.35 - poc_wick).max(0.0) / 0.35;
        score += 12.0 * poc_signal;
    }

    // Proximidad al extremo del rango (peso 18)
    if let Some(range) = &ctx.range {
        let rng_size = (range.range_high - range.range_low).max(1e-9);
        let dist_atr = match side {
            Side::Long => (ctx.bar_close - range.range_low).abs() / ctx.atr.max(1e-9),
            Side::Short => (range.range_high - ctx.bar_close).abs() / ctx.atr.max(1e-9),
        };
        let proximity = (0.25 - dist_atr).max(0.0) / 0.25;
        score += 18.0 * proximity;
        let _ = rng_size;
    }

    // CVD divergencia persistente (peso 8)
    if !ctx.cvd_history.is_empty() {
        let recent_swings = count_cvd_divergence_swings(&ctx.cvd_history, side, 20);
        score += 8.0 * intensity(recent_swings as f64, 1.0, 3.0);
    }

    // Big trades atrapados (peso 6)
    match side {
        Side::Long if ctx.big_trade_bearish => {
            // Big trade vendedor en zona de soporte = atrapado
            score += 6.0;
        }
        Side::Short if ctx.big_trade_bullish => {
            // Big trade comprador en zona de resistencia = atrapado
            score += 6.0;
        }
        _ => {}
    }

    // Liquidaciones (peso 6)
    score += 6.0 * intensity(ctx.liq_ratio, 3.0, 8.0);

    score
}

/// Cuenta swings de divergencia CVD vs precio en los últimos N valores del historial.
fn count_cvd_divergence_swings(cvd_history: &[f64], side: Side, lookback: usize) -> usize {
    let n = cvd_history.len().min(lookback);
    if n < 4 {
        return 0;
    }
    let slice = &cvd_history[cvd_history.len() - n..];
    let mut count = 0;
    for i in 1..slice.len() {
        match side {
            // Long: CVD haciendo higher-low mientras precio hace lower-low
            Side::Long if slice[i] > slice[i - 1] => count += 1,
            // Short: CVD haciendo lower-high mientras precio hace higher-high
            Side::Short if slice[i] < slice[i - 1] => count += 1,
            _ => {}
        }
    }
    count
}

/// Detector principal S2.
///
/// Retorna `Some(signal)` cuando hay una señal de absorción válida.
pub fn detect(ctx: &ScalpingContext, cfg: &ScalpingConfig) -> Option<ScalpingSignal> {
    // Gate 1: solo sesiones operativas
    if !is_scalping_session(ctx.session) {
        return None;
    }

    // Gate 2: solo en rango
    if ctx.regime != ScalpingRegime::Range {
        return None;
    }

    // Gate 3: necesitamos el rango detectado
    let range = ctx.range.as_ref()?;

    // Gate 4: spread aceptable
    if ctx.spread_ticks > cfg.max_spread_ticks {
        return None;
    }

    // Gate 5: no hay evento de volatilidad extrema
    if ctx.vr > cfg.max_vr {
        return None;
    }

    // Determinar el lado según la ubicación del precio en el rango
    let rng_size = range.range_high - range.range_low;
    let pct_in_range = if rng_size > 0.0 {
        (ctx.bar_close - range.range_low) / rng_size
    } else {
        0.5
    };

    // No-trade zone 35%-65% del rango
    if (0.35..=0.65).contains(&pct_in_range) {
        return None;
    }

    let side = if pct_in_range < 0.35 {
        Side::Long // precio cerca del low → buscar long en absorción bajista
    } else {
        Side::Short // precio cerca del high → buscar short en absorción alcista
    };

    // Gate 6: DZ en dirección de la absorción (agresores en contra del extremo)
    let dz_ok = match side {
        Side::Long => ctx.dz <= -cfg.s2_dz_min,
        Side::Short => ctx.dz >= cfg.s2_dz_min,
    };
    if !dz_ok {
        return None;
    }

    // Gate 7: volumen significativo
    if ctx.vr < cfg.s2_vr_min {
        return None;
    }

    // Gate 8: vela NO aceptó fuera del rango (cierre dentro del rango)
    let acceptance_outside = match side {
        Side::Long => ctx.bar_close < range.range_low,
        Side::Short => ctx.bar_close > range.range_high,
    };
    if acceptance_outside {
        return None;
    }

    // Gate 9: precio cerró en contra del delta (absorción real)
    let price_vs_delta_ok = match side {
        Side::Long => ctx.bar_close > ctx.bar_open, // vela alcista pese a delta negativo
        Side::Short => ctx.bar_close < ctx.bar_open, // vela bajista pese a delta positivo
    };
    if !price_vs_delta_ok {
        return None;
    }

    // Calcular conviction score
    let score = absorption_score(ctx, side);
    if score < cfg.min_conviction_score {
        return None;
    }

    // Calcular SL / TP
    let sl_signal = build_sl_tp(ctx, side, range)?;
    if sl_signal.rr < cfg.min_rr {
        return None;
    }

    let entry_type = if ctx.dz.abs() > 2.5 && ctx.vr > 3.0 {
        "aggressive".to_string()
    } else {
        "conservative".to_string()
    };

    let evidence = build_evidence(ctx, side, score);

    Some(ScalpingSignal {
        strategy: ScalpingStrategyId::Absorption,
        side,
        entry_price: sl_signal.entry,
        stop_price: sl_signal.sl,
        tp1_price: sl_signal.tp1,
        tp2_price: sl_signal.tp2,
        rr: sl_signal.rr,
        conviction_score: score,
        entry_type,
        timestamp_ms: ctx.timestamp_ms,
        evidence,
    })
}

struct SlTpResult {
    entry: f64,
    sl: f64,
    tp1: f64,
    tp2: f64,
    rr: f64,
}

fn build_sl_tp(
    ctx: &ScalpingContext,
    side: Side,
    range: &crate::detectors::range_detector::RangeContext,
) -> Option<SlTpResult> {
    let tick = 0.10_f64;
    let atr = ctx.atr.max(10.0);

    let (entry, sl, tp1, tp2) = match side {
        Side::Long => {
            let entry = ctx.bar_close + tick;
            let sl = (ctx.bar_low - 0.25 * atr).min(range.range_low - 3.0 * tick);
            let tp1 = ctx
                .poc
                .unwrap_or(range.range_low + (range.range_high - range.range_low) * 0.5);
            let tp2 = range.range_high;
            (entry, sl, tp1, tp2)
        }
        Side::Short => {
            let entry = ctx.bar_close - tick;
            let sl = (ctx.bar_high + 0.25 * atr).max(range.range_high + 3.0 * tick);
            let tp1 = ctx
                .poc
                .unwrap_or(range.range_low + (range.range_high - range.range_low) * 0.5);
            let tp2 = range.range_low;
            (entry, sl, tp1, tp2)
        }
    };

    let risk = (entry - sl).abs().max(tick);
    let reward = (tp1 - entry).abs();
    let rr = reward / risk;

    Some(SlTpResult {
        entry,
        sl,
        tp1,
        tp2,
        rr,
    })
}

fn build_evidence(ctx: &ScalpingContext, side: Side, score: f64) -> Vec<String> {
    let mut ev = Vec::new();
    ev.push(format!("score={:.1}", score));
    ev.push(format!("dz={:.2}", ctx.dz));
    ev.push(format!("vr={:.2}", ctx.vr));
    ev.push(format!("obi={:.3}", ctx.obi));
    match side {
        Side::Long => ev.push("S2:BEARISH_ABSORBED".into()),
        Side::Short => ev.push("S2:BULLISH_ABSORBED".into()),
    }
    if ctx.big_trade_bearish && side == Side::Long {
        ev.push("big_trade_bear_trapped".into());
    }
    if ctx.big_trade_bullish && side == Side::Short {
        ev.push("big_trade_bull_trapped".into());
    }
    if ctx.liq_ratio >= 3.0 {
        ev.push(format!("liq_ratio={:.1}", ctx.liq_ratio));
    }
    ev
}
