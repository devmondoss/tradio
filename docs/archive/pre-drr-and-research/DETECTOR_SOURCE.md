# Código Fuente de Detectores — Extracción Literal

> Generado desde el repositorio sin modificaciones.
> Estructura confirmada: los detectores están en `src/strategy/detectors/` tal como se asumió.
> Todos los archivos se incluyen completos, incluyendo tests.

---

## `src/strategy/types.rs` — 185 líneas

```rust
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Side {
    Long,
    Short,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum StrategyId {
    ValueAreaFailedAuction,
    VwapValuePullbackContinuation,
    LvnLiquidityVacuumBreakout,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum StrategyAction {
    Wait,
    ShadowSignal,
    Blocked,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Regime {
    TrendUp,
    TrendDown,
    Chop,
    Compression,
    Expansion,
    Stress,
    Aftermath,
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum DataQuality {
    Live,
    Fallback,
    Degraded,
    Stale,
    Missing,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ValueLocation {
    AboveVah,
    BelowVal,
    InValue,
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum PriceRelation {
    Above,
    Below,
    At,
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum AbsorptionSide {
    Bid,
    Ask,
    None,
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum CvdDivergence {
    BearishAbsorption,
    BullishAbsorption,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ImbalanceSide {
    Bullish,
    Bearish,
    None,
    Unknown,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VolumeProfileContext {
    pub poc: Option<f64>,
    pub vah: Option<f64>,
    pub val: Option<f64>,
    pub hvn_nearby: Vec<f64>,
    pub lvn_nearby: Vec<f64>,
    pub value_location: ValueLocation,
    pub quality: DataQuality,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VwapContext {
    pub vwap_session: Option<f64>,
    pub avwap_bos: Option<f64>,
    pub avwap_event: Option<f64>,
    pub price_vs_vwap: PriceRelation,
    pub price_vs_avwap_bos: PriceRelation,
    pub price_vs_avwap_event: PriceRelation,
    pub quality: DataQuality,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrderFlowContext {
    pub cvd: Option<f64>,
    pub cvd_slope: Option<f64>,
    pub delta: Option<f64>,
    pub taker_imbalance: Option<f64>,
    pub buy_volume: Option<f64>,
    pub sell_volume: Option<f64>,
    pub vpin: Option<f64>,
    pub cvd_divergence: Option<CvdDivergence>,
    pub footprint_absorption: AbsorptionSide,
    pub stacked_imbalance: ImbalanceSide,
    pub failed_acceptance: bool,
    pub sweep_confirmed: bool,
    pub mss_active: bool,
    pub quality: DataQuality,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrderBookContext {
    pub obi_l5: Option<f64>,
    pub obi_l10: Option<f64>,
    pub obi_l20: Option<f64>,
    pub microprice: Option<f64>,
    pub spread_bps: Option<f64>,
    pub walls_above: Vec<f64>,
    pub walls_below: Vec<f64>,
    pub thin_zone_above: bool,
    pub thin_zone_below: bool,
    pub quality: DataQuality,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StrategyMarketContext {
    pub symbol: String,
    pub timestamp_ms: i64,
    pub price: f64,
    pub regime: Regime,
    pub atr: Option<f64>,
    pub volume_profile: VolumeProfileContext,
    pub vwap: VwapContext,
    pub flow: OrderFlowContext,
    pub orderbook: OrderBookContext,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StrategySignal {
    pub action: StrategyAction,
    pub strategy_id: Option<StrategyId>,
    pub side: Option<Side>,
    pub entry_price: Option<f64>,
    pub stop_price: Option<f64>,
    pub target_price: Option<f64>,
    pub score: f64,
    pub ttl_ms: i64,
    pub evidence: Vec<String>,
    pub missing: Vec<String>,
    pub invalidation: Vec<String>,
    pub created_at_ms: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StrategyConfig {
    pub enabled: bool,
    pub max_spread_bps: f64,
    pub max_vpin: f64,
    pub min_score: f64,
    pub default_ttl_ms: i64,
}

impl Default for StrategyConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            max_spread_bps: 2.0,
            max_vpin: 0.75,
            min_score: 0.70,
            default_ttl_ms: 5 * 60 * 1000,
        }
    }
}
```

---

## `src/strategy/adapter.rs` — 322 líneas

```rust
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
        stacked_imbalance,
        failed_acceptance,
        sweep_confirmed,
        mss_active,
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
```

---

## `src/strategy/detectors/value_area_failed_auction.rs` — 209 líneas

```rust
use crate::strategy::types::*;
use super::toxic_flow_gate::toxic_flow_gate;

pub fn detect(ctx: &StrategyMarketContext, cfg: &StrategyConfig) -> Option<StrategySignal> {
    toxic_flow_gate(ctx, cfg).ok()?;

    let px = ctx.price;
    let atr = ctx.atr.unwrap_or(0.0);
    let vp = &ctx.volume_profile;
    let flow = &ctx.flow;
    let ob = &ctx.orderbook;

    let vah = vp.vah?;
    let val = vp.val?;
    let poc = vp.poc?;

    // SHORT: failed auction above VAH
    let short_location = px < vah
        && flow.failed_acceptance
        && flow.delta.unwrap_or(0.0) > 0.0
        && flow.footprint_absorption == AbsorptionSide::Ask;

    let short_flow =
        flow.cvd_slope.unwrap_or(0.0) <= 0.0 && flow.taker_imbalance.unwrap_or(0.0) < 0.25;

    let short_book =
        ob.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps && !ob.thin_zone_above;

    if short_location && short_flow && short_book {
        let entry = px;
        let stop = f64::max(vah + 0.25 * atr, px + 0.5 * atr);
        let target = poc;

        if target < entry && stop > entry {
            return Some(StrategySignal {
                action: StrategyAction::ShadowSignal,
                strategy_id: Some(StrategyId::ValueAreaFailedAuction),
                side: Some(Side::Short),
                entry_price: Some(entry),
                stop_price: Some(stop),
                target_price: Some(target),
                score: 0.0,
                ttl_ms: cfg.default_ttl_ms,
                evidence: vec![
                    "failed_acceptance_above_VAH".into(),
                    "ask_absorption".into(),
                    "cvd_not_confirming_breakout".into(),
                    "target_POC".into(),
                ],
                missing: vec![],
                invalidation: vec![
                    "price_reclaims_above_failed_auction_high".into(),
                    "vpin_becomes_toxic".into(),
                ],
                created_at_ms: ctx.timestamp_ms,
            });
        }
    }

    // LONG: failed auction below VAL
    let long_location = px > val
        && flow.failed_acceptance
        && flow.delta.unwrap_or(0.0) < 0.0
        && flow.footprint_absorption == AbsorptionSide::Bid;

    let long_flow =
        flow.cvd_slope.unwrap_or(0.0) >= 0.0 && flow.taker_imbalance.unwrap_or(0.0) > -0.25;

    let long_book =
        ob.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps && !ob.thin_zone_below;

    if long_location && long_flow && long_book {
        let entry = px;
        let stop = f64::min(val - 0.25 * atr, px - 0.5 * atr);
        let target = poc;

        if target > entry && stop < entry {
            return Some(StrategySignal {
                action: StrategyAction::ShadowSignal,
                strategy_id: Some(StrategyId::ValueAreaFailedAuction),
                side: Some(Side::Long),
                entry_price: Some(entry),
                stop_price: Some(stop),
                target_price: Some(target),
                score: 0.0,
                ttl_ms: cfg.default_ttl_ms,
                evidence: vec![
                    "failed_acceptance_below_VAL".into(),
                    "bid_absorption".into(),
                    "cvd_not_confirming_breakdown".into(),
                    "target_POC".into(),
                ],
                missing: vec![],
                invalidation: vec![
                    "price_loses_below_failed_auction_low".into(),
                    "vpin_becomes_toxic".into(),
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

    fn base_ctx() -> StrategyMarketContext {
        StrategyMarketContext {
            symbol: "BTCUSDT".to_string(),
            timestamp_ms: 1710000000000,
            price: 100050.0,
            regime: Regime::Chop,
            atr: Some(250.0),
            volume_profile: VolumeProfileContext {
                poc: Some(99500.0),
                vah: Some(100100.0),
                val: Some(99000.0),
                hvn_nearby: vec![99500.0],
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
                cvd_slope: Some(-0.2),
                delta: Some(120.0),
                taker_imbalance: Some(0.10),
                buy_volume: Some(5000.0),
                sell_volume: Some(4800.0),
                vpin: Some(0.45),
                footprint_absorption: AbsorptionSide::Ask,
                stacked_imbalance: ImbalanceSide::None,
                failed_acceptance: true,
                sweep_confirmed: false,
                mss_active: false,
                quality: DataQuality::Live,
            },
            orderbook: OrderBookContext {
                obi_l5: Some(-0.05),
                obi_l10: Some(-0.02),
                obi_l20: Some(0.01),
                microprice: Some(100040.0),
                spread_bps: Some(0.8),
                walls_above: vec![],
                walls_below: vec![99200.0],
                thin_zone_above: false,
                thin_zone_below: false,
                quality: DataQuality::Live,
            },
        }
    }

    #[test]
    fn detects_short_failed_auction_above_vah() {
        let ctx = base_ctx();
        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &cfg);
        assert!(signal.is_some());
        let s = signal.unwrap();
        assert_eq!(s.side, Some(Side::Short));
        assert_eq!(s.strategy_id, Some(StrategyId::ValueAreaFailedAuction));
        assert!(s.target_price.unwrap() < s.entry_price.unwrap());
    }

    #[test]
    fn detects_long_failed_auction_below_val() {
        let mut ctx = base_ctx();
        ctx.price = 99050.0;
        ctx.flow.delta = Some(-150.0);
        ctx.flow.footprint_absorption = AbsorptionSide::Bid;
        ctx.flow.cvd_slope = Some(0.1);

        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &cfg);
        assert!(signal.is_some());
        let s = signal.unwrap();
        assert_eq!(s.side, Some(Side::Long));
        assert!(s.target_price.unwrap() > s.entry_price.unwrap());
    }

    #[test]
    fn rejects_if_cvd_confirms_breakout() {
        let mut ctx = base_ctx();
        ctx.flow.cvd_slope = Some(0.8);
        let cfg = StrategyConfig::default();
        assert!(detect(&ctx, &cfg).is_none());
    }

    #[test]
    fn rejects_if_thin_zone_in_breakout_direction() {
        let mut ctx = base_ctx();
        ctx.orderbook.thin_zone_above = true;
        let cfg = StrategyConfig::default();
        assert!(detect(&ctx, &cfg).is_none());
    }
}
```

---

## `src/strategy/detectors/vwap_value_pullback_continuation.rs` — 229 líneas

```rust
use crate::strategy::types::*;
use super::toxic_flow_gate::toxic_flow_gate;

pub fn detect(ctx: &StrategyMarketContext, cfg: &StrategyConfig) -> Option<StrategySignal> {
    toxic_flow_gate(ctx, cfg).ok()?;

    let px = ctx.price;
    let atr = ctx.atr.unwrap_or(0.0);
    let vp = &ctx.volume_profile;
    let vw = &ctx.vwap;
    let flow = &ctx.flow;
    let ob = &ctx.orderbook;

    let vah = vp.vah?;
    let val = vp.val?;

    // LONG: trend up, pullback into value, flow realigns
    let long_context = matches!(ctx.regime, Regime::TrendUp | Regime::Expansion)
        && matches!(
            vw.price_vs_avwap_bos,
            PriceRelation::Above | PriceRelation::At
        )
        && matches!(
            vp.value_location,
            ValueLocation::InValue | ValueLocation::BelowVal
        );

    let long_flow = flow.cvd_slope.unwrap_or(0.0) >= 0.0
        && flow.delta.unwrap_or(0.0) > 0.0
        && !flow.failed_acceptance;

    let long_book = ob.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps
        && ob.microprice.map(|m| m >= px * 0.9998).unwrap_or(true);

    if long_context && long_flow && long_book {
        let entry = px;
        let stop = f64::min(val, entry - 0.75 * atr);
        let target = vah;

        if target > entry && stop < entry {
            return Some(StrategySignal {
                action: StrategyAction::ShadowSignal,
                strategy_id: Some(StrategyId::VwapValuePullbackContinuation),
                side: Some(Side::Long),
                entry_price: Some(entry),
                stop_price: Some(stop),
                target_price: Some(target),
                score: 0.0,
                ttl_ms: cfg.default_ttl_ms,
                evidence: vec![
                    "trend_up".into(),
                    "above_or_at_avwap_bos".into(),
                    "pullback_into_value".into(),
                    "positive_delta_reentry".into(),
                    "cvd_aligned".into(),
                ],
                missing: vec![],
                invalidation: vec![
                    "price_loses_VAL".into(),
                    "price_loses_AVWAP_BOS".into(),
                    "flow_turns_negative".into(),
                ],
                created_at_ms: ctx.timestamp_ms,
            });
        }
    }

    // SHORT: trend down, pullback into value, flow realigns
    let short_context = matches!(ctx.regime, Regime::TrendDown)
        && matches!(
            vw.price_vs_avwap_bos,
            PriceRelation::Below | PriceRelation::At
        )
        && matches!(
            vp.value_location,
            ValueLocation::InValue | ValueLocation::AboveVah
        );

    let short_flow = flow.cvd_slope.unwrap_or(0.0) <= 0.0
        && flow.delta.unwrap_or(0.0) < 0.0
        && !flow.failed_acceptance;

    let short_book = ob.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps
        && ob.microprice.map(|m| m <= px * 1.0002).unwrap_or(true);

    if short_context && short_flow && short_book {
        let entry = px;
        let stop = f64::max(vah, entry + 0.75 * atr);
        let target = val;

        if target < entry && stop > entry {
            return Some(StrategySignal {
                action: StrategyAction::ShadowSignal,
                strategy_id: Some(StrategyId::VwapValuePullbackContinuation),
                side: Some(Side::Short),
                entry_price: Some(entry),
                stop_price: Some(stop),
                target_price: Some(target),
                score: 0.0,
                ttl_ms: cfg.default_ttl_ms,
                evidence: vec![
                    "trend_down".into(),
                    "below_or_at_avwap_bos".into(),
                    "pullback_into_value".into(),
                    "negative_delta_reentry".into(),
                    "cvd_aligned".into(),
                ],
                missing: vec![],
                invalidation: vec![
                    "price_reclaims_VAH".into(),
                    "price_reclaims_AVWAP_BOS".into(),
                    "flow_turns_positive".into(),
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

    fn base_long_ctx() -> StrategyMarketContext {
        StrategyMarketContext {
            symbol: "BTCUSDT".to_string(),
            timestamp_ms: 1710000000000,
            price: 99200.0,
            regime: Regime::TrendUp,
            atr: Some(250.0),
            volume_profile: VolumeProfileContext {
                poc: Some(99500.0),
                vah: Some(100100.0),
                val: Some(99000.0),
                hvn_nearby: vec![99500.0],
                lvn_nearby: vec![],
                value_location: ValueLocation::InValue,
                quality: DataQuality::Live,
            },
            vwap: VwapContext {
                vwap_session: Some(99300.0),
                avwap_bos: Some(99100.0),
                avwap_event: None,
                price_vs_vwap: PriceRelation::Below,
                price_vs_avwap_bos: PriceRelation::Above,
                price_vs_avwap_event: PriceRelation::Unknown,
                quality: DataQuality::Live,
            },
            flow: OrderFlowContext {
                cvd: Some(800.0),
                cvd_slope: Some(0.3),
                delta: Some(50.0),
                taker_imbalance: Some(0.08),
                buy_volume: Some(4200.0),
                sell_volume: Some(4000.0),
                vpin: Some(0.40),
                footprint_absorption: AbsorptionSide::None,
                stacked_imbalance: ImbalanceSide::None,
                failed_acceptance: false,
                sweep_confirmed: false,
                mss_active: true,
                quality: DataQuality::Live,
            },
            orderbook: OrderBookContext {
                obi_l5: Some(0.03),
                obi_l10: Some(0.01),
                obi_l20: Some(0.0),
                microprice: Some(99210.0),
                spread_bps: Some(0.6),
                walls_above: vec![],
                walls_below: vec![98800.0],
                thin_zone_above: false,
                thin_zone_below: false,
                quality: DataQuality::Live,
            },
        }
    }

    #[test]
    fn detects_long_continuation_in_trend_up() {
        let ctx = base_long_ctx();
        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &cfg);
        assert!(signal.is_some());
        let s = signal.unwrap();
        assert_eq!(s.side, Some(Side::Long));
        assert_eq!(
            s.strategy_id,
            Some(StrategyId::VwapValuePullbackContinuation)
        );
    }

    #[test]
    fn detects_short_continuation_in_trend_down() {
        let mut ctx = base_long_ctx();
        ctx.regime = Regime::TrendDown;
        ctx.price = 100050.0;
        ctx.volume_profile.value_location = ValueLocation::AboveVah;
        ctx.vwap.price_vs_avwap_bos = PriceRelation::Below;
        ctx.flow.cvd_slope = Some(-0.4);
        ctx.flow.delta = Some(-80.0);
        ctx.orderbook.microprice = Some(100040.0);

        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &cfg);
        assert!(signal.is_some());
        let s = signal.unwrap();
        assert_eq!(s.side, Some(Side::Short));
    }

    #[test]
    fn rejects_if_wrong_side_of_avwap() {
        let mut ctx = base_long_ctx();
        ctx.vwap.price_vs_avwap_bos = PriceRelation::Below;
        let cfg = StrategyConfig::default();
        assert!(detect(&ctx, &cfg).is_none());
    }

    #[test]
    fn rejects_if_flow_contradicts() {
        let mut ctx = base_long_ctx();
        ctx.flow.delta = Some(-100.0);
        let cfg = StrategyConfig::default();
        assert!(detect(&ctx, &cfg).is_none());
    }
}
```

---

## `src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs` — 264 líneas

```rust
use crate::strategy::types::*;
use super::toxic_flow_gate::toxic_flow_gate;

fn nearest_above(levels: &[f64], price: f64) -> Option<f64> {
    levels
        .iter()
        .copied()
        .filter(|x| *x > price)
        .min_by(|a, b| a.partial_cmp(b).unwrap())
}

fn nearest_below(levels: &[f64], price: f64) -> Option<f64> {
    levels
        .iter()
        .copied()
        .filter(|x| *x < price)
        .max_by(|a, b| a.partial_cmp(b).unwrap())
}

pub fn detect(ctx: &StrategyMarketContext, cfg: &StrategyConfig) -> Option<StrategySignal> {
    toxic_flow_gate(ctx, cfg).ok()?;

    let px = ctx.price;
    let atr = ctx.atr.unwrap_or(0.0);
    let vp = &ctx.volume_profile;
    let vw = &ctx.vwap;
    let flow = &ctx.flow;
    let ob = &ctx.orderbook;

    // LONG: thin zone above, flow confirms
    let long_location = ob.thin_zone_above
        && matches!(
            vw.price_vs_vwap,
            PriceRelation::Above | PriceRelation::At
        )
        && matches!(
            vp.value_location,
            ValueLocation::InValue | ValueLocation::AboveVah
        );

    let long_flow = flow.delta.unwrap_or(0.0) > 0.0
        && flow.cvd_slope.unwrap_or(0.0) > 0.0
        && matches!(
            flow.stacked_imbalance,
            ImbalanceSide::Bullish | ImbalanceSide::None | ImbalanceSide::Unknown
        )
        && flow.taker_imbalance.unwrap_or(0.0).abs() < 0.90;

    let long_book = ob.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps
        && ob.microprice.map(|m| m >= px).unwrap_or(true);

    if long_location && long_flow && long_book {
        let mut targets = vp.hvn_nearby.clone();
        if let Some(vah) = vp.vah {
            targets.push(vah);
        }

        if let Some(target) = nearest_above(&targets, px) {
            let entry = px;
            let stop_anchor = vw.vwap_session.unwrap_or(px);
            let stop = f64::min(stop_anchor, entry - 0.75 * atr);

            if target > entry && stop < entry {
                return Some(StrategySignal {
                    action: StrategyAction::ShadowSignal,
                    strategy_id: Some(StrategyId::LvnLiquidityVacuumBreakout),
                    side: Some(Side::Long),
                    entry_price: Some(entry),
                    stop_price: Some(stop),
                    target_price: Some(target),
                    score: 0.0,
                    ttl_ms: cfg.default_ttl_ms,
                    evidence: vec![
                        "thin_zone_above".into(),
                        "vwap_reclaim_or_above".into(),
                        "positive_delta".into(),
                        "cvd_positive".into(),
                        "target_next_HVN_or_VAH".into(),
                    ],
                    missing: vec![],
                    invalidation: vec![
                        "price_loses_VWAP".into(),
                        "cvd_turns_negative".into(),
                        "spread_expands".into(),
                    ],
                    created_at_ms: ctx.timestamp_ms,
                });
            }
        }
    }

    // SHORT: thin zone below, flow confirms
    let short_location = ob.thin_zone_below
        && matches!(
            vw.price_vs_vwap,
            PriceRelation::Below | PriceRelation::At
        )
        && matches!(
            vp.value_location,
            ValueLocation::InValue | ValueLocation::BelowVal
        );

    let short_flow = flow.delta.unwrap_or(0.0) < 0.0
        && flow.cvd_slope.unwrap_or(0.0) < 0.0
        && matches!(
            flow.stacked_imbalance,
            ImbalanceSide::Bearish | ImbalanceSide::None | ImbalanceSide::Unknown
        )
        && flow.taker_imbalance.unwrap_or(0.0).abs() < 0.90;

    let short_book = ob.spread_bps.unwrap_or(999.0) <= cfg.max_spread_bps
        && ob.microprice.map(|m| m <= px).unwrap_or(true);

    if short_location && short_flow && short_book {
        let mut targets = vp.hvn_nearby.clone();
        if let Some(val) = vp.val {
            targets.push(val);
        }

        if let Some(target) = nearest_below(&targets, px) {
            let entry = px;
            let stop_anchor = vw.vwap_session.unwrap_or(px);
            let stop = f64::max(stop_anchor, entry + 0.75 * atr);

            if target < entry && stop > entry {
                return Some(StrategySignal {
                    action: StrategyAction::ShadowSignal,
                    strategy_id: Some(StrategyId::LvnLiquidityVacuumBreakout),
                    side: Some(Side::Short),
                    entry_price: Some(entry),
                    stop_price: Some(stop),
                    target_price: Some(target),
                    score: 0.0,
                    ttl_ms: cfg.default_ttl_ms,
                    evidence: vec![
                        "thin_zone_below".into(),
                        "vwap_loss_or_below".into(),
                        "negative_delta".into(),
                        "cvd_negative".into(),
                        "target_next_HVN_or_VAL".into(),
                    ],
                    missing: vec![],
                    invalidation: vec![
                        "price_reclaims_VWAP".into(),
                        "cvd_turns_positive".into(),
                        "spread_expands".into(),
                    ],
                    created_at_ms: ctx.timestamp_ms,
                });
            }
        }
    }

    None
}

#[cfg(test)]
mod tests {
    use super::*;

    fn base_long_ctx() -> StrategyMarketContext {
        StrategyMarketContext {
            symbol: "ETHUSDT".to_string(),
            timestamp_ms: 1710000000000,
            price: 3500.0,
            regime: Regime::Expansion,
            atr: Some(30.0),
            volume_profile: VolumeProfileContext {
                poc: Some(3480.0),
                vah: Some(3550.0),
                val: Some(3420.0),
                hvn_nearby: vec![3550.0, 3600.0],
                lvn_nearby: vec![3510.0],
                value_location: ValueLocation::InValue,
                quality: DataQuality::Live,
            },
            vwap: VwapContext {
                vwap_session: Some(3470.0),
                avwap_bos: Some(3460.0),
                avwap_event: None,
                price_vs_vwap: PriceRelation::Above,
                price_vs_avwap_bos: PriceRelation::Above,
                price_vs_avwap_event: PriceRelation::Unknown,
                quality: DataQuality::Live,
            },
            flow: OrderFlowContext {
                cvd: Some(2000.0),
                cvd_slope: Some(0.6),
                delta: Some(300.0),
                taker_imbalance: Some(0.20),
                buy_volume: Some(8000.0),
                sell_volume: Some(7200.0),
                vpin: Some(0.35),
                footprint_absorption: AbsorptionSide::None,
                stacked_imbalance: ImbalanceSide::Bullish,
                failed_acceptance: false,
                sweep_confirmed: false,
                mss_active: false,
                quality: DataQuality::Live,
            },
            orderbook: OrderBookContext {
                obi_l5: Some(0.10),
                obi_l10: Some(0.06),
                obi_l20: Some(0.03),
                microprice: Some(3502.0),
                spread_bps: Some(0.5),
                walls_above: vec![],
                walls_below: vec![3400.0],
                thin_zone_above: true,
                thin_zone_below: false,
                quality: DataQuality::Live,
            },
        }
    }

    #[test]
    fn detects_long_thin_zone_breakout() {
        let ctx = base_long_ctx();
        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &cfg);
        assert!(signal.is_some());
        let s = signal.unwrap();
        assert_eq!(s.side, Some(Side::Long));
        assert_eq!(s.strategy_id, Some(StrategyId::LvnLiquidityVacuumBreakout));
        assert!(s.target_price.unwrap() > s.entry_price.unwrap());
    }

    #[test]
    fn detects_short_thin_zone_breakout() {
        let mut ctx = base_long_ctx();
        ctx.price = 3430.0;
        ctx.orderbook.thin_zone_above = false;
        ctx.orderbook.thin_zone_below = true;
        ctx.orderbook.microprice = Some(3428.0);
        ctx.vwap.price_vs_vwap = PriceRelation::Below;
        ctx.volume_profile.value_location = ValueLocation::BelowVal;
        ctx.flow.delta = Some(-250.0);
        ctx.flow.cvd_slope = Some(-0.5);
        ctx.flow.stacked_imbalance = ImbalanceSide::Bearish;

        let cfg = StrategyConfig::default();
        let signal = detect(&ctx, &cfg);
        assert!(signal.is_some());
        let s = signal.unwrap();
        assert_eq!(s.side, Some(Side::Short));
    }

    #[test]
    fn rejects_if_no_thin_zone() {
        let mut ctx = base_long_ctx();
        ctx.orderbook.thin_zone_above = false;
        let cfg = StrategyConfig::default();
        assert!(detect(&ctx, &cfg).is_none());
    }

    #[test]
    fn rejects_if_cvd_contradicts() {
        let mut ctx = base_long_ctx();
        ctx.flow.cvd_slope = Some(-0.3);
        let cfg = StrategyConfig::default();
        assert!(detect(&ctx, &cfg).is_none());
    }
}
```

---

## `src/strategy/detectors/toxic_flow_gate.rs` — 155 líneas

```rust
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

    if let Some(spread) = ctx.orderbook.spread_bps {
        if spread > cfg.max_spread_bps {
            return Err("SPREAD_TOO_WIDE".to_string());
        }
    }

    if let Some(vpin) = ctx.flow.vpin {
        if vpin > cfg.max_vpin {
            return Err("VPIN_TOXIC".to_string());
        }
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::strategy::types::*;

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
```

---

## Constantes

Todos los literales numéricos con rol de umbral o parámetro, organizados por archivo. Los `const` explícitos son pocos — la mayoría son literales inline.

### `src/strategy/types.rs` — `StrategyConfig::default()`

| Nombre de campo | Valor | Descripción |
|----------------|-------|-------------|
| `enabled` | `false` | Desactivado por defecto (sobreescrito a `true` en `kline.rs:1054`) |
| `max_spread_bps` | `2.0` | Spread máximo permitido en basis points |
| `max_vpin` | `0.75` | VPIN máximo antes de bloqueo |
| `min_score` | `0.70` | Score mínimo para emitir `ShadowSignal` |
| `default_ttl_ms` | `300_000` (5 min) | Tiempo de vida de una señal en milisegundos |

### `src/strategy/adapter.rs` — literales inline

| Literal | Línea | Descripción |
|---------|-------|-------------|
| `TARGET_N: usize = 10`, `MIN_N: usize = 5` | adapter.rs | Divergencia CVD usa hasta 10 puntos y degrada a 5 cuando no hay mas historial. |
| `1.0001` | adapter.rs:166 | Umbral de higher-high para divergencia bearish: `last_max > first_max * 1.0001` |
| `0.9999` | adapter.rs:169 | Umbral de lower-low para divergencia bullish: `last_min < first_min * 0.9999` |
| `-0.5` | adapter.rs:166 | CVD slope mínimo (negativo) para confirmar divergencia bearish |
| `0.5` | adapter.rs:169 | CVD slope mínimo (positivo) para confirmar divergencia bullish |
| `5` | adapter.rs:178 | Mínimo de closes para calcular regime (`recent_closes.len() < 5`) |
| `1e-10` | adapter.rs:191 | Epsilon para evitar división por cero en OLS |
| `0.8` | adapter.rs:209 | range_atr mínimo para Compression: `range_atr < 0.8` |
| `4.0` | adapter.rs:211 | range_atr para Expansion: `range_atr > 4.0` |
| `0.15` | adapter.rs:211–216 | slope_per_atr umbral para TrendUp/TrendDown/Expansion |
| `5.0` | adapter.rs:71–72 | Multiplicador para detectar walls: qty > avg * 5.0 |
| `20` | adapter.rs:57, 71 | Niveles del orderbook evaluados para walls |
| `30` | adapter.rs:71–72 | Niveles del orderbook inspeccionados antes de filtrar walls |
| `10` | adapter.rs:75–85 | Niveles evaluados para thin zone |
| `3` | adapter.rs:81 | Mínimo de niveles thin para declarar thin_zone = true |
| `0.3` | adapter.rs:80 | Umbral de thin: qty < avg * 0.3 |
| `0.0002` | context.rs | Tolerancia para `PriceRelation::At`: `|price − level| / level < 0.0002` |

### `src/chart/kline.rs` — constantes en `run_strategy_detection`

| Nombre | Valor | Línea | Descripción |
|--------|-------|-------|-------------|
| `REGIME_N` | `20` | kline.rs:994 | Número de closes para calcular regime y CVD slope |
| `MICRO_N` | `20` | kline.rs | Numero de highs/lows/deltas para failed acceptance, CVD divergence, stacked imbalance, sweep y MSS. |
| `MAX_SIGNALS` | `50` | kline.rs:1071 | Máximo de señales visibles simultáneamente en el overlay |

### `src/strategy/scoring.rs` — pesos del scorer (literales inline)

| Evidencia | Puntos | Penalización |
|-----------|--------|-------------|
| `"target_POC"` | +0.20 | — |
| `"target_next_HVN_or_VAH"` / `"..._VAL"` | +0.20 | — |
| contiene `"vwap"` o `"avwap"` | +0.15 | — |
| contiene `"cvd"` | +0.20 | — |
| `"ask_absorption"` / `"bid_absorption"` | +0.20 | — |
| `"positive_delta*"` / `"negative_delta*"` | +0.15 | — |
| `"thin_zone_above"` / `"thin_zone_below"` | +0.15 | — |
| `spread_bps > 1.5` | — | −0.15 |
| `vpin > 0.65` | — | −0.20 |

### Indicadores — constantes relevantes al strategy

Definidas en sus respectivos archivos de indicadores:

| Constante | Valor | Archivo | Descripción |
|-----------|-------|---------|-------------|
| `ATR_PERIOD` | `14` | `indicator/kline/atr.rs` | Período del ATR Wilder |
| CVD slope lookback | `20` | `indicator/kline/cumulative_delta.rs` | Velas para OLS de CVD slope |
| VPIN lookback | `50` | `indicator/kline/cumulative_delta.rs` | Velas para media de VPIN |
| AVWAP BOS lookback | `50` | `indicator/kline/vwap.rs` | Velas para buscar swing-low |
| `VALUE_AREA_PCT` | `0.70` | `indicator/kline/volume_profile.rs` | 70% del volumen = value area |
| `HISTOGRAM_BINS` | `150` | `indicator/kline/volume_profile.rs` | Bins del Volume Profile |
| HVN umbral | `mean + 0.5σ` | `indicator/kline/volume_profile.rs` | Bins sobre este nivel son HVN |
| LVN umbral | `mean − 0.5σ` | `indicator/kline/volume_profile.rs` | Bins bajo este nivel son LVN |
| HVN/LVN radio | `±3 × ATR` | `indicator/kline/volume_profile.rs` | Rango de búsqueda desde el precio |
