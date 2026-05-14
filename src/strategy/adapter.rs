use exchange::depth::Depth;
use exchange::unit::{Price, Qty};

use super::types::*;

pub fn build_orderbook_context(depth: &Depth) -> OrderBookContext {
    let best_bid = depth.bids.iter().next_back();
    let best_ask = depth.asks.iter().next();

    let (spread_bps, microprice) = match (best_bid, best_ask) {
        (Some((&bid_price, &bid_qty)), Some((&ask_price, &ask_qty))) => {
            let bid_f = bid_price.to_f32() as f64;
            let ask_f = ask_price.to_f32() as f64;
            let mid = (bid_f + ask_f) / 2.0;
            let spread = if mid > 0.0 {
                (ask_f - bid_f) / mid * 10_000.0
            } else {
                0.0
            };

            let bid_sz = f64::from(bid_qty.to_f32_lossy());
            let ask_sz = f64::from(ask_qty.to_f32_lossy());
            let denom = bid_sz + ask_sz;
            let micro = if denom > 0.0 {
                (bid_f * ask_sz + ask_f * bid_sz) / denom
            } else {
                mid
            };

            (Some(spread), Some(micro))
        }
        _ => (None, None),
    };

    let obi = |levels: usize| -> Option<f64> {
        let bid_sum: f64 = depth
            .bids
            .iter()
            .rev()
            .take(levels)
            .map(|(_, q)| f64::from(q.to_f32_lossy()))
            .sum();
        let ask_sum: f64 = depth
            .asks
            .iter()
            .take(levels)
            .map(|(_, q)| f64::from(q.to_f32_lossy()))
            .sum();
        let total = bid_sum + ask_sum;
        if total > 0.0 {
            Some((bid_sum - ask_sum) / total)
        } else {
            None
        }
    };

    let detect_walls = |side_iter: Box<dyn Iterator<Item = (&Price, &Qty)> + '_>, threshold_mult: f64| -> Vec<f64> {
        let levels: Vec<_> = side_iter.take(20).collect();
        if levels.is_empty() {
            return vec![];
        }
        let avg_qty: f64 = levels.iter().map(|(_, q)| f64::from(q.to_f32_lossy())).sum::<f64>() / levels.len() as f64;
        let threshold = avg_qty * threshold_mult;
        levels
            .iter()
            .filter(|(_, q)| f64::from(q.to_f32_lossy()) > threshold)
            .map(|(p, _)| p.to_f32() as f64)
            .collect()
    };

    let walls_above = detect_walls(Box::new(depth.asks.iter().take(30)), 5.0);
    let walls_below = detect_walls(Box::new(depth.bids.iter().rev().take(30)), 5.0);

    let thin_zone = |side_iter: Box<dyn Iterator<Item = (&Price, &Qty)> + '_>| -> bool {
        let levels: Vec<_> = side_iter.take(10).collect();
        if levels.len() < 3 {
            return false;
        }
        let avg_qty: f64 = levels.iter().map(|(_, q)| f64::from(q.to_f32_lossy())).sum::<f64>() / levels.len() as f64;
        let thin_count = levels.iter().filter(|(_, q)| f64::from(q.to_f32_lossy()) < avg_qty * 0.3).count();
        thin_count >= 3
    };

    let thin_zone_above = thin_zone(Box::new(depth.asks.iter().take(10)));
    let thin_zone_below = thin_zone(Box::new(depth.bids.iter().rev().take(10)));

    OrderBookContext {
        obi_l5: obi(5),
        obi_l10: obi(10),
        obi_l20: obi(20),
        microprice,
        spread_bps,
        walls_above,
        walls_below,
        thin_zone_above,
        thin_zone_below,
        quality: DataQuality::Live,
    }
}

pub fn build_flow_context(
    cvd: Option<f64>,
    cvd_slope: Option<f64>,
    delta: Option<f64>,
    buy_volume: Option<f64>,
    sell_volume: Option<f64>,
    vpin: Option<f64>,
    failed_acceptance: bool,
    footprint_absorption: AbsorptionSide,
    cvd_divergence: Option<CvdDivergence>,
) -> OrderFlowContext {
    let taker_imbalance = match (buy_volume, sell_volume) {
        (Some(buy), Some(sell)) => {
            let total = buy + sell;
            if total > 0.0 {
                Some((buy - sell) / total)
            } else {
                None
            }
        }
        _ => None,
    };

    OrderFlowContext {
        cvd,
        cvd_slope,
        delta,
        taker_imbalance,
        buy_volume,
        sell_volume,
        vpin,
        cvd_divergence,
        footprint_absorption,
        stacked_imbalance: ImbalanceSide::Unknown,
        failed_acceptance,
        sweep_confirmed: false,
        mss_active: false,
        quality: if cvd.is_some() || delta.is_some() {
            DataQuality::Live
        } else {
            DataQuality::Missing
        },
    }
}

/// Detects CVD divergence against price action over the last N candles.
/// Bearish: price made higher high but CVD slope is declining → absorption at highs.
/// Bullish: price made lower low but CVD slope is rising → absorption at lows.
pub fn derive_cvd_divergence(
    recent_highs: &[f64],
    recent_lows: &[f64],
    cvd_slope: Option<f64>,
) -> Option<CvdDivergence> {
    const N: usize = 10;
    if recent_highs.len() < N || recent_lows.len() < N {
        return None;
    }
    let slope = cvd_slope?;
    let highs = &recent_highs[recent_highs.len() - N..];
    let lows = &recent_lows[recent_lows.len() - N..];
    let mid = N / 2;
    let first_max = highs[..mid].iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let last_max = highs[mid..].iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let first_min = lows[..mid].iter().copied().fold(f64::INFINITY, f64::min);
    let last_min = lows[mid..].iter().copied().fold(f64::INFINITY, f64::min);
    if last_max > first_max * 1.0001 && slope < -0.5 {
        return Some(CvdDivergence::BearishAbsorption);
    }
    if last_min < first_min * 0.9999 && slope > 0.5 {
        return Some(CvdDivergence::BullishAbsorption);
    }
    None
}

/// Derives market regime from recent close prices (oldest-first) and current ATR.
/// Uses OLS slope normalized by ATR to classify trend strength.
pub fn derive_regime(recent_closes: &[f64], atr: f64) -> Regime {
    if recent_closes.len() < 5 || atr <= 0.0 {
        return Regime::Unknown;
    }

    let n = recent_closes.len() as f64;
    let sum_x: f64 = (0..recent_closes.len()).map(|i| i as f64).sum();
    let sum_y: f64 = recent_closes.iter().sum();
    let sum_xy: f64 = recent_closes
        .iter()
        .enumerate()
        .map(|(i, y)| i as f64 * y)
        .sum();
    let sum_x2: f64 = (0..recent_closes.len()).map(|i| (i * i) as f64).sum();
    let denom = n * sum_x2 - sum_x * sum_x;
    if denom.abs() < 1e-10 {
        return Regime::Chop;
    }

    let slope = (n * sum_xy - sum_x * sum_y) / denom;
    let slope_per_atr = slope / atr;

    let high = recent_closes
        .iter()
        .copied()
        .fold(f64::NEG_INFINITY, f64::max);
    let low = recent_closes
        .iter()
        .copied()
        .fold(f64::INFINITY, f64::min);
    let range_atr = (high - low) / atr;

    if range_atr < 0.8 {
        Regime::Compression
    } else if range_atr > 4.0 && slope_per_atr.abs() > 0.15 {
        Regime::Expansion
    } else if slope_per_atr > 0.15 {
        Regime::TrendUp
    } else if slope_per_atr < -0.15 {
        Regime::TrendDown
    } else {
        Regime::Chop
    }
}

/// Derives failed_acceptance and footprint_absorption from recent candle OHLC.
///
/// Failed acceptance: price briefly broke above VAH or below VAL within the
/// last few candles but the most recent close returned inside value.
///
/// Absorption side: confirmed when flow (delta/CVD) diverges from price action
/// at the key level (e.g., positive delta at VAH but price rejected → ask absorption).
pub fn derive_failed_acceptance_and_absorption(
    recent_highs: &[f64],
    recent_lows: &[f64],
    recent_closes: &[f64],
    vah: Option<f64>,
    val: Option<f64>,
    delta: Option<f64>,
    cvd_slope: Option<f64>,
) -> (bool, AbsorptionSide) {
    if recent_highs.is_empty() || recent_closes.is_empty() {
        return (false, AbsorptionSide::Unknown);
    }

    let last_close = *recent_closes.last().unwrap();

    // Failed auction above VAH: any candle spiked above VAH but last close returned below it
    if let Some(vah) = vah {
        if recent_highs.iter().any(|&h| h > vah) && last_close < vah {
            // Ask absorption: buyers were active (delta > 0) but sellers rejected the breakout
            let absorption = if delta.unwrap_or(0.0) > 0.0 && cvd_slope.unwrap_or(0.0) <= 0.0 {
                AbsorptionSide::Ask
            } else {
                AbsorptionSide::None
            };
            return (true, absorption);
        }
    }

    // Failed auction below VAL: any candle spiked below VAL but last close returned above it
    if let Some(val) = val {
        if recent_lows.iter().any(|&l| l < val) && last_close > val {
            // Bid absorption: sellers were active (delta < 0) but buyers rejected the breakdown
            let absorption = if delta.unwrap_or(0.0) < 0.0 && cvd_slope.unwrap_or(0.0) >= 0.0 {
                AbsorptionSide::Bid
            } else {
                AbsorptionSide::None
            };
            return (true, absorption);
        }
    }

    (false, AbsorptionSide::None)
}

pub fn build_vwap_context(
    price: f64,
    vwap_session: Option<f64>,
    avwap_bos: Option<f64>,
) -> VwapContext {
    let price_vs_vwap = StrategyMarketContext::price_relation(price, vwap_session);
    let price_vs_avwap_bos = StrategyMarketContext::price_relation(price, avwap_bos);

    VwapContext {
        vwap_session,
        avwap_bos,
        avwap_event: None,
        price_vs_vwap,
        price_vs_avwap_bos,
        price_vs_avwap_event: PriceRelation::Unknown,
        quality: if vwap_session.is_some() {
            DataQuality::Live
        } else {
            DataQuality::Missing
        },
    }
}

pub fn build_volume_profile_context(
    price: f64,
    poc: Option<f64>,
    vah: Option<f64>,
    val: Option<f64>,
    hvn_nearby: Vec<f64>,
    lvn_nearby: Vec<f64>,
) -> VolumeProfileContext {
    let value_location = StrategyMarketContext::determine_value_location(price, vah, val);

    VolumeProfileContext {
        poc,
        vah,
        val,
        hvn_nearby,
        lvn_nearby,
        value_location,
        quality: if poc.is_some() && vah.is_some() && val.is_some() {
            DataQuality::Live
        } else if poc.is_some() {
            DataQuality::Fallback
        } else {
            DataQuality::Missing
        },
    }
}
