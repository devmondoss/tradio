//! S3 — Delta / CVD Divergence Fade
//!
//! Cuando el precio hace un nuevo swing high/low pero el CVD no confirma,
//! el movimiento carece de soporte institucional → fade hacia nivel previo.
//!
//! Puede operar tanto en rango como en tendencia (con cautela).

use super::{ScalpingContext, ScalpingSignal, ScalpingStrategyId, is_scalping_session};
use crate::strategy::types::ScalpingConfig;
use crate::strategy::types::Side;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DivergenceType {
    /// Precio higher-high pero CVD lower-high → debilidad alcista, sesgar shorts
    Bearish,
    /// Precio lower-low pero CVD higher-low → debilidad bajista, sesgar longs
    Bullish,
}

#[derive(Debug, Clone)]
pub struct DivergenceResult {
    pub div_type: DivergenceType,
    /// Nivel de precio del swing reciente donde se detectó la divergencia
    pub price_level: f64,
    /// Fuerza de la divergencia: gap_cvd / media(abs(cvd))
    pub strength: f64,
    /// Barras desde que ocurrió el swing más reciente
    pub bars_ago: usize,
}

/// Detecta divergencias entre swings de precio y CVD.
///
/// Implementa las reglas estrictas del documento para evitar falsos positivos:
/// - Bearish: price >= prev_price_high AND cvd < prev_cvd_high
/// - Bullish: price <= prev_price_low AND cvd > prev_cvd_low
pub fn detect_divergence(
    price_highs: &[f64],
    price_lows: &[f64],
    cvd_history: &[f64],
    lookback: usize,
    min_strength: f64,
) -> Option<DivergenceResult> {
    let n = price_highs
        .len()
        .min(price_lows.len())
        .min(cvd_history.len());
    if n < lookback {
        return None;
    }

    let ph = &price_highs[n - lookback..];
    let pl = &price_lows[n - lookback..];
    let cvd = &cvd_history[n - lookback..];

    let cvd_mean_abs = cvd.iter().map(|v| v.abs()).sum::<f64>() / cvd.len() as f64;
    if cvd_mean_abs < 1e-9 {
        return None;
    }

    // Encontrar swing highs locales (local maxima con order=2)
    let mut bearish: Option<DivergenceResult> = None;
    let mut bullish: Option<DivergenceResult> = None;

    // Swing highs
    let swing_highs: Vec<usize> = (2..ph.len() - 1)
        .filter(|&i| ph[i] >= ph[i - 1] && ph[i] >= ph[i - 2] && ph[i] >= ph[i + 1])
        .collect();

    if swing_highs.len() >= 2 {
        let last_h = *swing_highs.last().unwrap();
        let prev_h = swing_highs[swing_highs.len() - 2];
        if ph[last_h] >= ph[prev_h] && cvd[last_h] < cvd[prev_h] {
            let gap_cvd = cvd[prev_h] - cvd[last_h];
            let strength = gap_cvd / cvd_mean_abs;
            if strength >= min_strength {
                bearish = Some(DivergenceResult {
                    div_type: DivergenceType::Bearish,
                    price_level: ph[last_h],
                    strength,
                    bars_ago: ph.len() - 1 - last_h,
                });
            }
        }
    }

    // Swing lows
    let swing_lows: Vec<usize> = (2..pl.len() - 1)
        .filter(|&i| pl[i] <= pl[i - 1] && pl[i] <= pl[i - 2] && pl[i] <= pl[i + 1])
        .collect();

    if swing_lows.len() >= 2 {
        let last_l = *swing_lows.last().unwrap();
        let prev_l = swing_lows[swing_lows.len() - 2];
        if pl[last_l] <= pl[prev_l] && cvd[last_l] > cvd[prev_l] {
            let gap_cvd = cvd[last_l] - cvd[prev_l];
            let strength = gap_cvd / cvd_mean_abs;
            if strength >= min_strength {
                bullish = Some(DivergenceResult {
                    div_type: DivergenceType::Bullish,
                    price_level: pl[last_l],
                    strength,
                    bars_ago: pl.len() - 1 - last_l,
                });
            }
        }
    }

    // Retorna la divergencia más fuerte si hay conflicto
    match (bearish, bullish) {
        (Some(b), Some(u)) => {
            if b.strength >= u.strength {
                Some(b)
            } else {
                Some(u)
            }
        }
        (Some(b), None) => Some(b),
        (None, Some(u)) => Some(u),
        (None, None) => None,
    }
}

/// Detector principal S3.
pub fn detect(ctx: &ScalpingContext, cfg: &ScalpingConfig) -> Option<ScalpingSignal> {
    // Gate 1: solo sesiones operativas
    if !is_scalping_session(ctx.session) {
        return None;
    }

    // Gate 2: spread aceptable
    if ctx.spread_ticks > cfg.max_spread_ticks {
        return None;
    }

    // Gate 3: sin evento de volatilidad extrema
    if ctx.vr > cfg.max_vr {
        return None;
    }

    // Gate 4: necesitamos suficiente historial
    let n = ctx
        .price_highs
        .len()
        .min(ctx.price_lows.len())
        .min(ctx.cvd_history.len());
    if n < cfg.s3_lookback_bars {
        return None;
    }

    // Detectar divergencia
    let div = detect_divergence(
        &ctx.price_highs,
        &ctx.price_lows,
        &ctx.cvd_history,
        cfg.s3_lookback_bars,
        cfg.s3_min_divergence_strength,
    )?;

    // Gate 5: divergencia reciente (≤5 barras)
    if div.bars_ago > 5 {
        return None;
    }

    let side = match div.div_type {
        DivergenceType::Bearish => Side::Short,
        DivergenceType::Bullish => Side::Long,
    };

    // Gate 6: confirmación de OBI en la dirección del fade
    let obi_ok = match side {
        Side::Long => ctx.obi_ema_fast > 0.48,
        Side::Short => ctx.obi_ema_fast < 0.52,
    };
    if !obi_ok {
        return None;
    }

    // Gate 7: DZ no está confirmando el breakout (si DZ muy fuerte en dirección contraria, no es divergencia válida)
    let dz_ok = match side {
        Side::Long => ctx.dz > -0.5,
        Side::Short => ctx.dz < 0.5,
    };
    if !dz_ok {
        return None;
    }

    // Calcular SL/TP
    let sl_tp = build_sl_tp(ctx, side, &div)?;
    if sl_tp.rr < cfg.min_rr {
        return None;
    }

    let evidence = build_evidence(ctx, side, &div);

    Some(ScalpingSignal {
        strategy: ScalpingStrategyId::CvdDivergence,
        side,
        entry_price: sl_tp.entry,
        stop_price: sl_tp.sl,
        tp1_price: sl_tp.tp1,
        tp2_price: sl_tp.tp2,
        rr: sl_tp.rr,
        conviction_score: (div.strength * 30.0).clamp(55.0, 95.0),
        entry_type: "divergence".to_string(),
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

fn build_sl_tp(ctx: &ScalpingContext, side: Side, div: &DivergenceResult) -> Option<SlTpResult> {
    let tick = 0.10_f64;
    let atr = ctx.atr.max(10.0);

    // Encontrar swing anterior como TP1
    let (entry, sl, tp1, tp2) = match side {
        Side::Short => {
            let entry = ctx.bar_close - tick;
            let sl = div.price_level + atr * 0.3;
            // TP1: swing low previo más cercano
            let tp1 = ctx
                .price_lows
                .iter()
                .rev()
                .skip(1)
                .find(|&&v| v < ctx.bar_close)
                .copied()
                .unwrap_or(ctx.bar_close - atr * 0.8);
            let tp2 = ctx.poc.unwrap_or(ctx.bar_close - atr * 1.5);
            (entry, sl, tp1.min(tp2), tp2.min(tp1))
        }
        Side::Long => {
            let entry = ctx.bar_close + tick;
            let sl = div.price_level - atr * 0.3;
            let tp1 = ctx
                .price_highs
                .iter()
                .rev()
                .skip(1)
                .find(|&&v| v > ctx.bar_close)
                .copied()
                .unwrap_or(ctx.bar_close + atr * 0.8);
            let tp2 = ctx.poc.unwrap_or(ctx.bar_close + atr * 1.5);
            (entry, sl, tp1.max(tp2), tp2.max(tp1))
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

fn build_evidence(ctx: &ScalpingContext, side: Side, div: &DivergenceResult) -> Vec<String> {
    let mut ev = Vec::new();
    match side {
        Side::Short => ev.push("S3:BEARISH_DIV".into()),
        Side::Long => ev.push("S3:BULLISH_DIV".into()),
    }
    ev.push(format!("div_strength={:.2}", div.strength));
    ev.push(format!("bars_ago={}", div.bars_ago));
    ev.push(format!("price_level={:.1}", div.price_level));
    ev.push(format!("obi_fast={:.3}", ctx.obi_ema_fast));
    ev.push(format!("dz={:.2}", ctx.dz));
    ev
}
