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
    delta: Option<f64>,
    buy_volume: Option<f64>,
    sell_volume: Option<f64>,
) -> OrderFlowContext {
    let cvd_slope = None; // Needs multiple datapoints to compute slope
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
        vpin: None, // Requires bucket-based calculation, not yet implemented
        footprint_absorption: AbsorptionSide::Unknown,
        stacked_imbalance: ImbalanceSide::Unknown,
        failed_acceptance: false,
        sweep_confirmed: false,
        mss_active: false,
        quality: if cvd.is_some() || delta.is_some() {
            DataQuality::Live
        } else {
            DataQuality::Missing
        },
    }
}

pub fn build_vwap_context(
    price: f64,
    vwap_session: Option<f64>,
) -> VwapContext {
    let price_vs_vwap = StrategyMarketContext::price_relation(price, vwap_session);

    VwapContext {
        vwap_session,
        avwap_bos: None,   // Not yet implemented
        avwap_event: None, // Not yet implemented
        price_vs_vwap,
        price_vs_avwap_bos: PriceRelation::Unknown,
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
) -> VolumeProfileContext {
    let value_location = StrategyMarketContext::determine_value_location(price, vah, val);

    VolumeProfileContext {
        poc,
        vah,
        val,
        hvn_nearby: vec![], // Requires HVN/LVN detection algorithm
        lvn_nearby: vec![], // Requires HVN/LVN detection algorithm
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
