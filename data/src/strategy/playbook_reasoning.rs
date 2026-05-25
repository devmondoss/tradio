use serde::{Deserialize, Serialize};

use crate::institutional::{DivergenceSignal, FundingRegime, LiqSide, OiTrendDir};
use crate::session::{SessionPhase, TradingSession};

use super::auction_state::AuctionState;
use super::detectors::toxic_flow_gate::toxic_flow_gate;
use super::types::*;
use super::vp_open_bias::DailyVpBias;

pub const REASONING_VERSION: &str = "playbook_reasoning_v1";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PlaybookScore {
    pub id: String,
    pub score: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PlaybookReasoning {
    pub version: String,
    pub primary_playbook: String,
    pub secondary_playbooks: Vec<PlaybookScore>,
    pub market_state: Vec<String>,
    pub location_tags: Vec<String>,
    pub flow_tags: Vec<String>,
    pub liquidity_tags: Vec<String>,
    pub book_tags: Vec<String>,
    pub institutional_tags: Vec<String>,
    pub structure_tags: Vec<String>,
    pub trigger_tags: Vec<String>,
    pub risk_tags: Vec<String>,
    pub confirmation_tags: Vec<String>,
    pub contradiction_tags: Vec<String>,
    pub missing_tags: Vec<String>,
    pub detector_role_tags: Vec<String>,
    pub confidence: f64,
    pub completeness: f64,
}

#[derive(Default)]
struct ScoreBook {
    trapped: f64,
    failed_auction: f64,
    footprint_absorption: f64,
    liquidity_sweep: f64,
    cvd_absorption: f64,
    funding_exhaustion: f64,
    smart_money_fade: f64,
    order_block_absorption: f64,
    value_extreme_rejection: f64,
    imbalance_continuation: f64,
    vwap_pullback: f64,
    lvn_vacuum: f64,
    dom_breakout: f64,
    session_open: f64,
    order_block_continuation: f64,
    fvg_rebalance: f64,
    trend_day: f64,
    thin_book_momentum: f64,
    value_rotation: f64,
    poc_magnet: f64,
    liquidity_magnet: f64,
    institutional_exhaustion: f64,
    oi_expansion_trend: f64,
    mixed: f64,
    data_insufficient: f64,
    no_clear: f64,
    possible_noise: f64,
}

pub fn classify_playbook_reasoning(
    ctx: &StrategyMarketContext,
    cfg: &StrategyConfig,
    signal: &StrategySignal,
    detector_log: &[DetectorSnap],
) -> PlaybookReasoning {
    let mut tags = TagBuckets::default();
    let mut scores = ScoreBook::default();
    let is_long = matches!(signal.side, Some(Side::Long));
    let is_short = matches!(signal.side, Some(Side::Short));

    classify_market_state(ctx, &mut tags, &mut scores);
    classify_location(ctx, &mut tags, &mut scores);
    classify_flow(ctx, is_long, is_short, &mut tags, &mut scores);
    classify_liquidity(ctx, &mut tags, &mut scores);
    classify_book(ctx, &mut tags, &mut scores);
    classify_institutional(ctx, is_long, is_short, &mut tags, &mut scores);
    classify_structure(ctx, &mut tags, &mut scores);
    classify_trigger(ctx, signal, &mut tags, &mut scores);
    classify_risk(ctx, cfg, signal, &mut tags, &mut scores);
    classify_detector_role(signal, detector_log, &mut tags, &mut scores);
    classify_data_quality(ctx, cfg, &mut tags, &mut scores);

    if signal.action == StrategyAction::Wait {
        tags.detector_role_tags
            .push("inactive_due_to_context".into());
        scores.no_clear += 0.10;
    }
    if signal.action == StrategyAction::Blocked {
        tags.detector_role_tags
            .push("inactive_due_to_context".into());
        scores.data_insufficient += 0.10;
    }

    let mut ranked = rank_playbooks(scores);
    if ranked.is_empty() {
        ranked.push(PlaybookScore {
            id: "no_clear_playbook".into(),
            score: 0.0,
        });
    }

    let contradiction_count = tags.contradiction_tags.len();
    if contradiction_count >= 3 {
        upsert_score(&mut ranked, "conflicting_signals", 0.50);
        tags.market_state.push("conflicting_signals".into());
    }
    if ranked.len() >= 2 && (ranked[0].score - ranked[1].score).abs() < 0.10 {
        upsert_score(&mut ranked, "mixed_context", 0.45);
        tags.market_state.push("mixed_context".into());
    }

    ranked.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.id.cmp(&b.id))
    });

    let primary = ranked
        .first()
        .map(|p| p.id.clone())
        .unwrap_or_else(|| "no_clear_playbook".into());
    let confidence = compute_confidence(&ranked, contradiction_count);
    let completeness = compute_completeness(&tags);

    PlaybookReasoning {
        version: REASONING_VERSION.into(),
        primary_playbook: primary,
        secondary_playbooks: ranked.into_iter().take(8).collect(),
        market_state: dedup(tags.market_state),
        location_tags: dedup(tags.location_tags),
        flow_tags: dedup(tags.flow_tags),
        liquidity_tags: dedup(tags.liquidity_tags),
        book_tags: dedup(tags.book_tags),
        institutional_tags: dedup(tags.institutional_tags),
        structure_tags: dedup(tags.structure_tags),
        trigger_tags: dedup(tags.trigger_tags),
        risk_tags: dedup(tags.risk_tags),
        confirmation_tags: dedup(tags.confirmation_tags),
        contradiction_tags: dedup(tags.contradiction_tags),
        missing_tags: dedup(tags.missing_tags),
        detector_role_tags: dedup(tags.detector_role_tags),
        confidence,
        completeness,
    }
}

#[derive(Default)]
struct TagBuckets {
    market_state: Vec<String>,
    location_tags: Vec<String>,
    flow_tags: Vec<String>,
    liquidity_tags: Vec<String>,
    book_tags: Vec<String>,
    institutional_tags: Vec<String>,
    structure_tags: Vec<String>,
    trigger_tags: Vec<String>,
    risk_tags: Vec<String>,
    confirmation_tags: Vec<String>,
    contradiction_tags: Vec<String>,
    missing_tags: Vec<String>,
    detector_role_tags: Vec<String>,
}

fn classify_market_state(ctx: &StrategyMarketContext, tags: &mut TagBuckets, s: &mut ScoreBook) {
    match ctx.regime {
        Regime::TrendUp => {
            tags.market_state.push("market_imbalance_up".into());
            s.imbalance_continuation += 0.18;
            s.oi_expansion_trend += 0.08;
        }
        Regime::TrendDown => {
            tags.market_state.push("market_imbalance_down".into());
            s.imbalance_continuation += 0.18;
            s.oi_expansion_trend += 0.08;
        }
        Regime::Chop => {
            tags.market_state.push("market_chop".into());
            s.value_rotation += 0.18;
            s.value_extreme_rejection += 0.08;
        }
        Regime::Compression => {
            tags.market_state.push("market_compression".into());
            s.value_rotation += 0.12;
            s.session_open += 0.05;
        }
        Regime::Expansion => {
            tags.market_state.push("market_expansion".into());
            s.imbalance_continuation += 0.22;
            s.thin_book_momentum += 0.12;
        }
        Regime::Stress => {
            tags.market_state.push("market_stress".into());
            s.data_insufficient += 0.15;
        }
        Regime::Aftermath => {
            tags.market_state.push("market_aftermath".into());
            s.value_extreme_rejection += 0.08;
            s.institutional_exhaustion += 0.08;
        }
        Regime::Unknown => {
            tags.market_state.push("market_unknown".into());
            tags.missing_tags.push("market_structure_missing".into());
        }
    }

    if let Some(ac) = &ctx.auction_state {
        match ac.state {
            AuctionState::Balance => {
                tags.market_state.push("market_balance".into());
                s.value_rotation += 0.15;
            }
            AuctionState::UpImbalance => {
                tags.market_state.push("market_imbalance_up".into());
                s.imbalance_continuation += 0.15;
                s.trend_day += 0.08;
            }
            AuctionState::DownImbalance => {
                tags.market_state.push("market_imbalance_down".into());
                s.imbalance_continuation += 0.15;
                s.trend_day += 0.08;
            }
            AuctionState::Accumulation => {
                tags.market_state.push("market_accumulation".into());
                s.trapped += 0.10;
                s.order_block_absorption += 0.08;
            }
            AuctionState::Distribution => {
                tags.market_state.push("market_distribution".into());
                s.trapped += 0.10;
                s.order_block_absorption += 0.08;
            }
            AuctionState::Unknown => tags.market_state.push("market_unknown".into()),
        }
    }

    if let Some(vp) = &ctx.vp_open_bias {
        match vp.bias {
            DailyVpBias::InsideValue => {
                tags.market_state.push("vp_bias_inside_value".into());
                s.value_rotation += 0.18;
                s.value_extreme_rejection += 0.08;
            }
            DailyVpBias::OutsideVaInsidePa => {
                if vp.bias_supports_long() {
                    tags.market_state
                        .push("vp_bias_outside_va_inside_pa_long".into());
                } else if vp.bias_supports_short() {
                    tags.market_state
                        .push("vp_bias_outside_va_inside_pa_short".into());
                }
                s.poc_magnet += 0.14;
                s.value_rotation += 0.08;
            }
            DailyVpBias::TrendDay => {
                if vp.trend_day_is_up() {
                    tags.market_state.push("vp_bias_trend_day_up".into());
                } else if vp.trend_day_is_down() {
                    tags.market_state.push("vp_bias_trend_day_down".into());
                }
                s.trend_day += 0.22;
                s.imbalance_continuation += 0.10;
            }
            DailyVpBias::FadeGap => {
                if vp.bias_supports_long() {
                    tags.market_state.push("vp_bias_fade_gap_long".into());
                } else if vp.bias_supports_short() {
                    tags.market_state.push("vp_bias_fade_gap_short".into());
                }
                s.failed_auction += 0.10;
                s.value_extreme_rejection += 0.10;
            }
            DailyVpBias::Unknown => tags.market_state.push("vp_bias_unknown".into()),
        }
    } else {
        tags.missing_tags.push("vp_bias_unknown".into());
    }

    if let Some(session) = &ctx.session {
        match session.session {
            TradingSession::Asia => tags.market_state.push("session_asia".into()),
            TradingSession::London => {
                tags.market_state.push("session_london".into());
                s.session_open += 0.04;
            }
            TradingSession::LondonNyOverlap => {
                tags.market_state.push("session_london_ny_overlap".into());
                s.session_open += 0.04;
                s.liquidity_sweep += 0.03;
            }
            TradingSession::NewYork => {
                tags.market_state.push("session_new_york".into());
                s.session_open += 0.04;
            }
            TradingSession::OffHours => tags.market_state.push("session_off".into()),
        }
        match session.phase {
            SessionPhase::OpeningRush => {
                tags.market_state.push("phase_opening_rush".into());
                s.session_open += 0.18;
                s.liquidity_sweep += 0.05;
            }
            SessionPhase::Open => {
                tags.market_state.push("phase_open".into());
                s.session_open += 0.08;
            }
            SessionPhase::Mid => tags.market_state.push("phase_mid".into()),
            SessionPhase::Close => {
                tags.market_state.push("phase_close".into());
                tags.contradiction_tags.push("late_session_risk".into());
            }
        }
    } else {
        tags.missing_tags.push("session_missing".into());
    }
}

fn classify_location(ctx: &StrategyMarketContext, tags: &mut TagBuckets, s: &mut ScoreBook) {
    let price = ctx.price;
    let atr = ctx.atr.unwrap_or(0.0).max(1.0);
    let vp = &ctx.volume_profile;

    match vp.value_location {
        ValueLocation::InValue => {
            tags.location_tags.push("inside_value".into());
            s.value_rotation += 0.14;
            s.vwap_pullback += 0.06;
        }
        ValueLocation::AboveVah => {
            tags.location_tags.push("above_vah".into());
            s.imbalance_continuation += 0.06;
            s.value_extreme_rejection += 0.10;
        }
        ValueLocation::BelowVal => {
            tags.location_tags.push("below_val".into());
            s.imbalance_continuation += 0.06;
            s.value_extreme_rejection += 0.10;
        }
        ValueLocation::Unknown => tags.missing_tags.push("value_location_unknown".into()),
    }

    if near(vp.vah, price, atr * 0.5) {
        tags.location_tags.push("near_vah".into());
        tags.location_tags.push("at_value_extreme".into());
        s.failed_auction += 0.08;
        s.footprint_absorption += 0.08;
        s.cvd_absorption += 0.05;
    }
    if near(vp.val, price, atr * 0.5) {
        tags.location_tags.push("near_val".into());
        tags.location_tags.push("at_value_extreme".into());
        s.failed_auction += 0.08;
        s.footprint_absorption += 0.08;
        s.cvd_absorption += 0.05;
    }
    if near(vp.poc, price, atr * 0.35) {
        tags.location_tags.push("near_poc".into());
        tags.location_tags.push("at_value_mid".into());
        s.poc_magnet += 0.10;
        s.value_rotation += 0.05;
    }
    if vp.hvn_nearby.iter().any(|&x| (x - price).abs() <= atr) {
        tags.location_tags.push("near_hvn".into());
        s.liquidity_magnet += 0.10;
    }
    if vp.lvn_nearby.iter().any(|&x| (x - price).abs() <= atr) {
        tags.location_tags.push("near_lvn".into());
        s.lvn_vacuum += 0.18;
        s.thin_book_momentum += 0.06;
    }
    if vp.naked_pocs.iter().any(|&x| (x - price).abs() <= atr) {
        tags.location_tags.push("near_naked_poc".into());
        s.poc_magnet += 0.18;
        s.liquidity_magnet += 0.08;
    }
    if vp.single_prints.iter().any(|&x| (x - price).abs() <= atr) {
        tags.location_tags.push("near_single_print".into());
        s.liquidity_magnet += 0.08;
        s.fvg_rebalance += 0.06;
    }

    if let (Some(poc), Some(vah), Some(val)) = (vp.poc, vp.vah, vp.val) {
        if price > poc && price < vah {
            tags.location_tags.push("between_poc_and_vah".into());
        } else if price < poc && price > val {
            tags.location_tags.push("between_poc_and_val".into());
        }
    } else {
        tags.missing_tags.push("volume_profile_missing".into());
    }
}

fn classify_flow(
    ctx: &StrategyMarketContext,
    is_long: bool,
    is_short: bool,
    tags: &mut TagBuckets,
    s: &mut ScoreBook,
) {
    let flow = &ctx.flow;
    push_signed(
        flow.delta,
        0.0,
        "delta_positive",
        "delta_negative",
        "delta_neutral",
        &mut tags.flow_tags,
    );
    push_signed(
        flow.cvd_slope,
        0.05,
        "cvd_slope_positive",
        "cvd_slope_negative",
        "cvd_slope_flat",
        &mut tags.flow_tags,
    );
    push_signed(
        flow.taker_imbalance,
        0.05,
        "taker_imbalance_buy",
        "taker_imbalance_sell",
        "taker_imbalance_neutral",
        &mut tags.flow_tags,
    );

    if side_aligned(flow.cvd_slope, is_long, is_short) {
        tags.flow_tags.push(
            if is_long {
                "cvd_confirms_long"
            } else {
                "cvd_confirms_short"
            }
            .into(),
        );
        s.imbalance_continuation += 0.08;
        s.vwap_pullback += 0.06;
    } else if side_contradicts(flow.cvd_slope, is_long, is_short) {
        tags.contradiction_tags.push(
            if is_long {
                "cvd_contradicts_long"
            } else {
                "cvd_contradicts_short"
            }
            .into(),
        );
        s.cvd_absorption += 0.05;
    }

    match flow.footprint_absorption {
        AbsorptionSide::Bid => {
            tags.flow_tags.push("footprint_absorption_bid".into());
            tags.confirmation_tags
                .push("seller_aggression_absorbed".into());
            s.footprint_absorption += 0.22;
            s.trapped += 0.12;
        }
        AbsorptionSide::Ask => {
            tags.flow_tags.push("footprint_absorption_ask".into());
            tags.confirmation_tags
                .push("buyer_aggression_absorbed".into());
            s.footprint_absorption += 0.22;
            s.trapped += 0.12;
        }
        AbsorptionSide::None => {}
        AbsorptionSide::Unknown => tags.missing_tags.push("absorption_unknown".into()),
    }

    match flow.cvd_divergence {
        Some(CvdDivergence::BullishAbsorption) => {
            tags.flow_tags.push("cvd_bullish_divergence".into());
            s.cvd_absorption += 0.15;
            s.trapped += 0.06;
        }
        Some(CvdDivergence::BearishAbsorption) => {
            tags.flow_tags.push("cvd_bearish_divergence".into());
            s.cvd_absorption += 0.15;
            s.trapped += 0.06;
        }
        None => {}
    }
    if let Some(p) = flow.cvd_divergence_persistence {
        if p >= 4 {
            tags.flow_tags.push("cvd_bearish_divergence".into());
            tags.flow_tags.push("cvd_divergence_persistent".into());
            s.cvd_absorption += 0.14;
        } else if p <= -4 {
            tags.flow_tags.push("cvd_bullish_divergence".into());
            tags.flow_tags.push("cvd_divergence_persistent".into());
            s.cvd_absorption += 0.14;
        }
    }

    match flow.stacked_imbalance {
        ImbalanceSide::Bullish => {
            tags.flow_tags.push("stacked_imbalance_bullish".into());
            s.imbalance_continuation += 0.08;
        }
        ImbalanceSide::Bearish => {
            tags.flow_tags.push("stacked_imbalance_bearish".into());
            s.imbalance_continuation += 0.08;
        }
        ImbalanceSide::None => tags.flow_tags.push("stacked_imbalance_none".into()),
        ImbalanceSide::Unknown => tags.flow_tags.push("stacked_imbalance_unknown".into()),
    }

    if flow.finish_action_bullish {
        tags.flow_tags.push("finish_action_bullish".into());
        s.footprint_absorption += 0.08;
        s.trapped += 0.05;
    }
    if flow.finish_action_bearish {
        tags.flow_tags.push("finish_action_bearish".into());
        s.footprint_absorption += 0.08;
        s.trapped += 0.05;
    }
    if flow.unfinish_action_bullish {
        tags.flow_tags.push("unfinish_action_bullish".into());
        if is_short {
            tags.contradiction_tags
                .push("unfinish_action_against_signal".into());
        }
    }
    if flow.unfinish_action_bearish {
        tags.flow_tags.push("unfinish_action_bearish".into());
        if is_long {
            tags.contradiction_tags
                .push("unfinish_action_against_signal".into());
        }
    }
    if flow.big_trade_bullish {
        tags.flow_tags.push("big_trade_bullish".into());
        s.trapped += 0.06;
        s.footprint_absorption += 0.06;
    }
    if flow.big_trade_bearish {
        tags.flow_tags.push("big_trade_bearish".into());
        s.trapped += 0.06;
        s.footprint_absorption += 0.06;
    }
    if let Some(v) = flow.delta_velocity {
        if v > 0.05 {
            tags.flow_tags.push("delta_drain_bullish".into());
            s.failed_auction += 0.08;
        } else if v < -0.05 {
            tags.flow_tags.push("delta_drain_bearish".into());
            s.failed_auction += 0.08;
        }
    } else {
        tags.missing_tags.push("delta_velocity_missing".into());
    }

    match flow.vpin {
        Some(v) if v < 0.30 => tags.flow_tags.push("vpin_clean".into()),
        Some(v) if v > 0.75 => {
            tags.flow_tags.push("vpin_toxic".into());
            tags.contradiction_tags
                .push("toxic_flow_environment".into());
        }
        Some(_) => tags.flow_tags.push("vpin_normal".into()),
        None => tags.missing_tags.push("vpin_missing".into()),
    }

    if flow.footprint_levels.is_empty() {
        tags.missing_tags.push("footprint_missing".into());
    }
}

fn classify_liquidity(ctx: &StrategyMarketContext, tags: &mut TagBuckets, s: &mut ScoreBook) {
    if ctx.flow.sweep_confirmed {
        tags.liquidity_tags.push("sweep_confirmed".into());
        s.liquidity_sweep += 0.16;
        s.trapped += 0.08;
    } else {
        tags.liquidity_tags.push("no_sweep".into());
    }

    let Some(inst) = &ctx.institutional else {
        tags.missing_tags.push("institutional_missing".into());
        tags.missing_tags.push("liq_feed_missing".into());
        return;
    };

    let liq = &inst.liquidations;
    if liq.short_liq_usd_5m > 0.0 {
        tags.liquidity_tags
            .push("short_liquidations_present".into());
        s.liquidity_sweep += 0.08;
    }
    if liq.long_liq_usd_5m > 0.0 {
        tags.liquidity_tags.push("long_liquidations_present".into());
        s.liquidity_sweep += 0.08;
    }
    if liq.total_zscore.map(|z| z > 1.5).unwrap_or(false) {
        tags.liquidity_tags.push("liquidation_outlier".into());
        s.liquidity_sweep += 0.10;
    }
    if liq.cascade_detected {
        tags.liquidity_tags
            .push("liquidation_cascade_active".into());
        tags.contradiction_tags
            .push("late_move_after_cascade".into());
    }
    match liq.dominant_side {
        LiqSide::Longs => tags.liquidity_tags.push("long_liquidations_present".into()),
        LiqSide::Shorts => tags
            .liquidity_tags
            .push("short_liquidations_present".into()),
        LiqSide::Neutral => {
            if liq.total_usd_5m <= 0.0 {
                tags.liquidity_tags.push("liq_feed_quiet".into());
            }
        }
    }

    if let Some(map) = &inst.liq_map {
        if map.primary_target_above.is_some() {
            tags.liquidity_tags.push("liq_target_above".into());
            s.liquidity_magnet += 0.08;
        }
        if map.primary_target_below.is_some() {
            tags.liquidity_tags.push("liq_target_below".into());
            s.liquidity_magnet += 0.08;
        }
        let density_above = map
            .density_above
            .iter()
            .map(|lvl| lvl.density as f64)
            .fold(0.0, f64::max);
        let density_below = map
            .density_below
            .iter()
            .map(|lvl| lvl.density as f64)
            .fold(0.0, f64::max);
        if density_above > density_below * 1.5 {
            tags.liquidity_tags.push("high_liq_density_above".into());
            s.liquidity_magnet += 0.06;
        }
        if density_below > density_above * 1.5 {
            tags.liquidity_tags.push("high_liq_density_below".into());
            s.liquidity_magnet += 0.06;
        }
    } else {
        tags.missing_tags.push("liq_map_missing".into());
    }
}

fn classify_book(ctx: &StrategyMarketContext, tags: &mut TagBuckets, s: &mut ScoreBook) {
    let ob = &ctx.orderbook;
    push_signed(
        ob.obi_l5,
        0.15,
        "obi_bid_dominant",
        "obi_ask_dominant",
        "obi_neutral",
        &mut tags.book_tags,
    );
    if ob.thin_zone_above {
        tags.book_tags.push("thin_zone_above".into());
        tags.book_tags.push("path_clear_above".into());
        s.thin_book_momentum += 0.10;
        s.lvn_vacuum += 0.06;
    } else {
        tags.book_tags.push("no_thin_zone_above".into());
    }
    if ob.thin_zone_below {
        tags.book_tags.push("thin_zone_below".into());
        tags.book_tags.push("path_clear_below".into());
        s.thin_book_momentum += 0.10;
        s.lvn_vacuum += 0.06;
    } else {
        tags.book_tags.push("no_thin_zone_below".into());
    }
    if ctx.flow.bid_wall_nearby {
        tags.book_tags.push("bid_wall_nearby".into());
        tags.book_tags.push("wall_support_below".into());
    }
    if ctx.flow.ask_wall_nearby {
        tags.book_tags.push("ask_wall_nearby".into());
        tags.book_tags.push("wall_resistance_above".into());
    }
    if let Some(m) = ob.microprice {
        if m > ctx.price {
            tags.book_tags.push("microprice_above_mid".into());
            s.dom_breakout += 0.04;
        } else if m < ctx.price {
            tags.book_tags.push("microprice_below_mid".into());
            s.dom_breakout += 0.04;
        }
    }
    match ob.spread_bps {
        Some(x) if x <= 1.5 => tags.book_tags.push("spread_clean".into()),
        Some(x) if x <= 2.0 => tags.book_tags.push("spread_wide".into()),
        Some(_) => {
            tags.book_tags.push("spread_untradable".into());
            tags.contradiction_tags
                .push("spread_untradable_environment".into());
        }
        None => tags.missing_tags.push("orderbook_missing".into()),
    }
    if ob.quality != DataQuality::Live {
        tags.missing_tags.push("orderbook_missing".into());
    }
    if ob.spoof.as_ref().map(|s| s.spoof_detected).unwrap_or(false) {
        tags.book_tags.push("spoof_detected".into());
        tags.contradiction_tags.push("spoof_detected".into());
    }
}

fn classify_institutional(
    ctx: &StrategyMarketContext,
    is_long: bool,
    is_short: bool,
    tags: &mut TagBuckets,
    s: &mut ScoreBook,
) {
    let Some(inst) = &ctx.institutional else {
        tags.missing_tags.push("institutional_missing".into());
        return;
    };
    match inst.funding.regime {
        FundingRegime::Neutral => tags.institutional_tags.push("funding_neutral".into()),
        FundingRegime::ElevatedLong => {
            tags.institutional_tags.push("funding_elevated_long".into());
            s.institutional_exhaustion += 0.06;
        }
        FundingRegime::ExtremeLong => {
            tags.institutional_tags.push("funding_extreme_long".into());
            s.funding_exhaustion += 0.16;
            s.institutional_exhaustion += 0.12;
        }
        FundingRegime::ElevatedShort => {
            tags.institutional_tags
                .push("funding_elevated_short".into());
            s.institutional_exhaustion += 0.06;
        }
        FundingRegime::ExtremeShort => {
            tags.institutional_tags.push("funding_extreme_short".into());
            s.funding_exhaustion += 0.16;
            s.institutional_exhaustion += 0.12;
        }
    }
    if inst.funding.velocity > 0.0 {
        tags.institutional_tags
            .push("funding_velocity_rising".into());
    } else if inst.funding.velocity < 0.0 {
        tags.institutional_tags
            .push("funding_velocity_retreating".into());
    }
    if inst.funding.peak_confirmed {
        tags.institutional_tags
            .push("funding_peak_confirmed".into());
        s.funding_exhaustion += 0.08;
    }

    match inst.oi_trend.trend {
        OiTrendDir::AccumulatingFast | OiTrendDir::Accumulating => {
            if ctx.flow.oi_momentum_aligned.unwrap_or(false) {
                tags.institutional_tags
                    .push("oi_accumulation_confirmed".into());
                s.oi_expansion_trend += 0.10;
            }
        }
        OiTrendDir::Decreasing | OiTrendDir::DecreasingFast => {
            tags.institutional_tags
                .push("oi_distribution_confirmed".into());
            s.institutional_exhaustion += 0.06;
        }
        OiTrendDir::Flat => {}
    }
    if inst.ls_ratio.top_traders_long_pct > 0.55 {
        tags.institutional_tags.push("top_traders_long".into());
    } else if inst.ls_ratio.top_traders_long_pct < 0.45 {
        tags.institutional_tags.push("top_traders_short".into());
    }
    if inst.ls_ratio.retail_long_pct > 0.60 {
        tags.institutional_tags.push("retail_long_crowded".into());
    } else if inst.ls_ratio.retail_long_pct < 0.40 {
        tags.institutional_tags.push("retail_short_crowded".into());
    }
    match inst.ls_ratio.divergence_signal {
        DivergenceSignal::SmartLongRetailShort => {
            tags.institutional_tags
                .push("smart_money_bullish_divergence".into());
            s.smart_money_fade += 0.16;
        }
        DivergenceSignal::SmartShortRetailLong => {
            tags.institutional_tags
                .push("smart_money_bearish_divergence".into());
            s.smart_money_fade += 0.16;
        }
        DivergenceSignal::Aligned => tags.institutional_tags.push("ls_ratio_aligned".into()),
        DivergenceSignal::Neutral => tags.institutional_tags.push("ls_ratio_neutral".into()),
    }
    if let Some(sms) = inst.smart_money_score {
        if sms > 0.40 {
            tags.institutional_tags
                .push("smart_money_score_long".into());
            if is_short {
                tags.contradiction_tags
                    .push("smart_money_against_signal".into());
            }
        } else if sms < -0.40 {
            tags.institutional_tags
                .push("smart_money_score_short".into());
            if is_long {
                tags.contradiction_tags
                    .push("smart_money_against_signal".into());
            }
        } else {
            tags.institutional_tags
                .push("smart_money_score_neutral".into());
        }
    } else {
        tags.missing_tags.push("smart_money_score_missing".into());
    }
    if is_long
        && matches!(
            inst.funding.regime,
            FundingRegime::ElevatedLong | FundingRegime::ExtremeLong
        )
    {
        tags.contradiction_tags
            .push("funding_crowded_against_signal".into());
    }
    if is_short
        && matches!(
            inst.funding.regime,
            FundingRegime::ElevatedShort | FundingRegime::ExtremeShort
        )
    {
        tags.contradiction_tags
            .push("funding_crowded_against_signal".into());
    }
}

fn classify_structure(ctx: &StrategyMarketContext, tags: &mut TagBuckets, s: &mut ScoreBook) {
    if ctx.flow.mss_active {
        tags.structure_tags.push("mss_active".into());
        s.imbalance_continuation += 0.05;
    }
    if let Some(ms) = &ctx.market_structure {
        tags.structure_tags
            .push(format!("htf_bias_{:?}", ms.htf_bias).to_lowercase());
        tags.structure_tags
            .push(format!("price_zone_{:?}", ms.price_zone).to_lowercase());
    } else {
        tags.missing_tags.push("market_structure_missing".into());
    }
    if let Some(obs) = &ctx.order_blocks {
        if let Some(ob) = &obs.nearest_bullish {
            if ob.price_inside(ctx.price) {
                tags.structure_tags
                    .push("inside_bullish_order_block".into());
                s.order_block_continuation += 0.12;
                s.order_block_absorption += 0.08;
            } else {
                tags.structure_tags.push("near_bullish_order_block".into());
            }
            if ob.volume_ratio >= 3.0 {
                tags.structure_tags.push("ob_volume_ratio_gt_3x".into());
            } else if ob.volume_ratio >= 1.5 {
                tags.structure_tags.push("ob_volume_ratio_gt_1_5x".into());
            }
            if ob.swings_broken >= 2 {
                tags.structure_tags.push("ob_swings_broken_ge_2".into());
            }
        }
        if let Some(ob) = &obs.nearest_bearish {
            if ob.price_inside(ctx.price) {
                tags.structure_tags
                    .push("inside_bearish_order_block".into());
                s.order_block_continuation += 0.12;
                s.order_block_absorption += 0.08;
            } else {
                tags.structure_tags.push("near_bearish_order_block".into());
            }
            if ob.volume_ratio >= 3.0 {
                tags.structure_tags.push("ob_volume_ratio_gt_3x".into());
            } else if ob.volume_ratio >= 1.5 {
                tags.structure_tags.push("ob_volume_ratio_gt_1_5x".into());
            }
            if ob.swings_broken >= 2 {
                tags.structure_tags.push("ob_swings_broken_ge_2".into());
            }
        }
    } else {
        tags.missing_tags.push("order_blocks_missing".into());
    }
    if let Some(fvg) = &ctx.fvg {
        if fvg.nearest_bullish.is_some() {
            tags.structure_tags.push("near_bullish_fvg".into());
            s.fvg_rebalance += 0.05;
        }
        if fvg.nearest_bearish.is_some() {
            tags.structure_tags.push("near_bearish_fvg".into());
            s.fvg_rebalance += 0.05;
        }
    } else {
        tags.missing_tags.push("fvg_missing".into());
    }
}

fn classify_trigger(
    ctx: &StrategyMarketContext,
    signal: &StrategySignal,
    tags: &mut TagBuckets,
    s: &mut ScoreBook,
) {
    if ctx.flow.failed_acceptance {
        tags.trigger_tags.push("breakout_failure".into());
        tags.trigger_tags.push("close_back_inside_value".into());
        s.failed_auction += 0.20;
        s.trapped += 0.08;
    }
    if signal
        .evidence
        .iter()
        .any(|e| e.contains("vwap") || e.contains("VWAP"))
    {
        tags.trigger_tags.push("vwap_reclaim".into());
        s.vwap_pullback += 0.10;
    }
    if signal
        .evidence
        .iter()
        .any(|e| e.contains("target_POC") || e.contains("target_poc"))
    {
        tags.risk_tags.push("target_poc".into());
        s.poc_magnet += 0.08;
    }
    if signal
        .evidence
        .iter()
        .any(|e| e.contains("target_next_HVN"))
    {
        tags.risk_tags.push("target_hvn".into());
        s.liquidity_magnet += 0.06;
    }
    if signal
        .evidence
        .iter()
        .any(|e| e.contains("liq_target_above"))
    {
        tags.risk_tags.push("target_liq_pool".into());
        s.liquidity_magnet += 0.06;
    }
    if signal
        .evidence
        .iter()
        .any(|e| e.contains("liq_target_below"))
    {
        tags.risk_tags.push("target_liq_pool".into());
        s.liquidity_magnet += 0.06;
    }
    if ctx.flow.mss_active {
        tags.trigger_tags.push("mss_active".into());
    }
    if signal.action == StrategyAction::ShadowSignal && signal.evidence.is_empty() {
        tags.trigger_tags.push("trigger_missing".into());
        tags.detector_role_tags.push("possible_noise_signal".into());
        s.possible_noise += 0.15;
    }
}

fn classify_risk(
    ctx: &StrategyMarketContext,
    cfg: &StrategyConfig,
    signal: &StrategySignal,
    tags: &mut TagBuckets,
    s: &mut ScoreBook,
) {
    match (signal.entry_price, signal.stop_price, signal.target_price) {
        (Some(entry), Some(stop), Some(target)) => {
            let risk = (entry - stop).abs();
            let reward = (target - entry).abs();
            if risk <= 1e-10 {
                tags.risk_tags.push("risk_degenerate".into());
                tags.contradiction_tags.push("risk_degenerate".into());
            } else {
                let rr = reward / risk;
                if rr >= cfg.min_rr {
                    tags.risk_tags.push("rr_ge_min".into());
                    s.imbalance_continuation += 0.03;
                    s.value_extreme_rejection += 0.03;
                } else {
                    tags.risk_tags.push("rr_below_min".into());
                    tags.contradiction_tags.push("rr_below_min".into());
                }
                if rr > cfg.max_rr_m5 {
                    tags.risk_tags.push("rr_too_large_fantasy".into());
                    tags.contradiction_tags.push("rr_too_large_fantasy".into());
                } else if rr >= cfg.min_rr * 1.5 {
                    tags.risk_tags.push("rr_high_but_reachable".into());
                }
            }
            tag_target(ctx, target, tags, s);
        }
        _ => {
            tags.risk_tags.push("target_missing".into());
            if signal.action == StrategyAction::ShadowSignal {
                tags.contradiction_tags.push("target_missing".into());
            }
        }
    }
}

fn classify_detector_role(
    signal: &StrategySignal,
    detector_log: &[DetectorSnap],
    tags: &mut TagBuckets,
    s: &mut ScoreBook,
) {
    let Some(id) = signal.strategy_id else {
        tags.detector_role_tags
            .push("inactive_due_to_context".into());
        s.no_clear += 0.10;
        return;
    };
    let name = detector_name(id);
    tags.detector_role_tags
        .push(format!("{name}_primary_setup").to_lowercase());

    match id {
        StrategyId::ValueAreaFailedAuction => {
            s.failed_auction += 0.22;
            s.value_rotation += 0.06;
        }
        StrategyId::FootprintAbsorptionReversal => {
            s.footprint_absorption += 0.25;
            s.trapped += 0.10;
        }
        StrategyId::LiquidationHunt => {
            s.liquidity_sweep += 0.25;
            s.trapped += 0.08;
        }
        StrategyId::CvdDivergenceReversal => {
            s.cvd_absorption += 0.24;
            s.value_extreme_rejection += 0.06;
        }
        StrategyId::LvnLiquidityVacuumBreakout => {
            s.lvn_vacuum += 0.22;
            s.imbalance_continuation += 0.08;
        }
        StrategyId::DomImbalanceBreakout => {
            s.dom_breakout += 0.22;
            s.thin_book_momentum += 0.10;
        }
        StrategyId::SessionOpenBreakout => {
            s.session_open += 0.24;
            s.imbalance_continuation += 0.08;
        }
        StrategyId::OrderBlockRetest => {
            s.order_block_continuation += 0.18;
            s.order_block_absorption += 0.10;
        }
        StrategyId::VwapValuePullbackContinuation => {
            s.vwap_pullback += 0.22;
            s.imbalance_continuation += 0.08;
        }
        StrategyId::FundingExhaustionReversal => {
            s.funding_exhaustion += 0.24;
            s.institutional_exhaustion += 0.12;
        }
        StrategyId::SmartMoneyDivergence => {
            s.smart_money_fade += 0.24;
            s.institutional_exhaustion += 0.08;
        }
    }

    for det in detector_log {
        let role = match det.status {
            DetectorStatus::Active => "primary_setup",
            DetectorStatus::Fired => "secondary_confirmation",
            DetectorStatus::LowScore => "inactive_due_to_low_score",
            DetectorStatus::SessionInvalid => "inactive_due_to_context",
            DetectorStatus::GlobalBlocked => "inactive_due_to_context",
            DetectorStatus::Skip => "inactive_due_to_context",
        };
        tags.detector_role_tags
            .push(format!("{}_{}", det.name.to_lowercase(), role));
    }
}

fn classify_data_quality(
    ctx: &StrategyMarketContext,
    cfg: &StrategyConfig,
    tags: &mut TagBuckets,
    s: &mut ScoreBook,
) {
    if ctx.atr.map(|a| a < 1.0).unwrap_or(true) {
        tags.missing_tags.push("atr_not_ready".into());
        s.data_insufficient += 0.20;
    }
    if ctx.volume_profile.quality != DataQuality::Live {
        tags.missing_tags.push("volume_profile_missing".into());
    }
    if ctx.vwap.quality != DataQuality::Live {
        tags.missing_tags.push("vwap_missing".into());
    }
    if ctx.flow.quality != DataQuality::Live {
        tags.missing_tags.push("data_degraded".into());
    }
    if let Some(inst) = &ctx.institutional {
        if inst.quality != DataQuality::Live {
            tags.missing_tags.push("data_fallback".into());
        }
    }
    if let Err(reason) = toxic_flow_gate(ctx, cfg) {
        tags.contradiction_tags.push(reason);
        s.data_insufficient += 0.15;
    }
    if tags.missing_tags.len() >= 4 {
        s.data_insufficient += 0.18;
    }
}

fn tag_target(ctx: &StrategyMarketContext, target: f64, tags: &mut TagBuckets, s: &mut ScoreBook) {
    let atr = ctx.atr.unwrap_or(0.0).max(1.0);
    let vp = &ctx.volume_profile;
    if near(vp.poc, target, atr * 0.35) {
        tags.risk_tags.push("target_poc".into());
        s.poc_magnet += 0.08;
    }
    if near(vp.vah, target, atr * 0.35) {
        tags.risk_tags.push("target_vah".into());
    }
    if near(vp.val, target, atr * 0.35) {
        tags.risk_tags.push("target_val".into());
    }
    if vp
        .hvn_nearby
        .iter()
        .any(|&x| (x - target).abs() <= atr * 0.35)
    {
        tags.risk_tags.push("target_hvn".into());
        s.liquidity_magnet += 0.05;
    }
    if vp
        .naked_pocs
        .iter()
        .any(|&x| (x - target).abs() <= atr * 0.35)
    {
        tags.risk_tags.push("target_naked_poc".into());
        s.poc_magnet += 0.10;
    }
    if vp
        .single_prints
        .iter()
        .any(|&x| (x - target).abs() <= atr * 0.35)
    {
        tags.risk_tags.push("target_single_print".into());
        s.liquidity_magnet += 0.06;
    }
    if ctx
        .swing_high_20
        .map(|x| (x - target).abs() <= atr * 0.35)
        .unwrap_or(false)
    {
        tags.risk_tags.push("target_swing_high".into());
    }
    if ctx
        .swing_low_20
        .map(|x| (x - target).abs() <= atr * 0.35)
        .unwrap_or(false)
    {
        tags.risk_tags.push("target_swing_low".into());
    }
}

fn rank_playbooks(s: ScoreBook) -> Vec<PlaybookScore> {
    let mut out = vec![
        ("trapped_traders_reversal", s.trapped),
        ("failed_auction_reversal", s.failed_auction),
        ("footprint_absorption_reversal", s.footprint_absorption),
        ("liquidity_sweep_reversal", s.liquidity_sweep),
        ("cvd_absorption_reversal", s.cvd_absorption),
        ("funding_exhaustion_reversal", s.funding_exhaustion),
        ("smart_money_fade_reversal", s.smart_money_fade),
        ("order_block_absorption_reversal", s.order_block_absorption),
        ("value_extreme_rejection", s.value_extreme_rejection),
        ("imbalance_continuation", s.imbalance_continuation),
        ("vwap_pullback_continuation", s.vwap_pullback),
        ("lvn_vacuum_breakout", s.lvn_vacuum),
        ("dom_imbalance_breakout", s.dom_breakout),
        ("session_open_expansion", s.session_open),
        (
            "order_block_retest_continuation",
            s.order_block_continuation,
        ),
        ("fvg_rebalance_continuation", s.fvg_rebalance),
        ("trend_day_continuation", s.trend_day),
        ("thin_book_momentum_continuation", s.thin_book_momentum),
        ("value_area_rotation", s.value_rotation),
        ("poc_magnet_rotation", s.poc_magnet),
        ("hvn_liquidity_magnet", s.liquidity_magnet),
        ("institutional_exhaustion", s.institutional_exhaustion),
        ("oi_expansion_trend", s.oi_expansion_trend),
        ("mixed_context", s.mixed),
        ("data_insufficient", s.data_insufficient),
        ("no_clear_playbook", s.no_clear),
        ("possible_noise_signal", s.possible_noise),
    ]
    .into_iter()
    .filter(|(_, score)| *score > 0.0)
    .map(|(id, score)| PlaybookScore {
        id: id.into(),
        score: score.clamp(0.0, 1.0),
    })
    .collect::<Vec<_>>();

    out.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.id.cmp(&b.id))
    });
    out
}

fn compute_confidence(ranked: &[PlaybookScore], contradiction_count: usize) -> f64 {
    let top = ranked.first().map(|p| p.score).unwrap_or(0.0);
    let second = ranked.get(1).map(|p| p.score).unwrap_or(0.0);
    let separation = (top - second).max(0.0);
    let contradiction_penalty = (contradiction_count as f64 * 0.06).min(0.30);
    (0.35 + top * 0.45 + separation * 0.35 - contradiction_penalty).clamp(0.0, 1.0)
}

fn compute_completeness(tags: &TagBuckets) -> f64 {
    let groups = [
        !tags.market_state.is_empty(),
        !tags.location_tags.is_empty(),
        !tags.flow_tags.is_empty(),
        !tags.liquidity_tags.is_empty(),
        !tags.book_tags.is_empty(),
        !tags.institutional_tags.is_empty(),
        !tags.structure_tags.is_empty(),
        !tags.trigger_tags.is_empty(),
        !tags.risk_tags.is_empty(),
    ];
    let present = groups.iter().filter(|&&x| x).count() as f64;
    (present / groups.len() as f64).clamp(0.0, 1.0)
}

fn upsert_score(scores: &mut Vec<PlaybookScore>, id: &str, score: f64) {
    if let Some(existing) = scores.iter_mut().find(|s| s.id == id) {
        existing.score = existing.score.max(score);
    } else {
        scores.push(PlaybookScore {
            id: id.into(),
            score,
        });
    }
}

fn detector_name(id: StrategyId) -> &'static str {
    match id {
        StrategyId::ValueAreaFailedAuction => "vafa",
        StrategyId::VwapValuePullbackContinuation => "vwap",
        StrategyId::LvnLiquidityVacuumBreakout => "lvn",
        StrategyId::DomImbalanceBreakout => "dib",
        StrategyId::SessionOpenBreakout => "sob",
        StrategyId::OrderBlockRetest => "obr",
        StrategyId::FootprintAbsorptionReversal => "far",
        StrategyId::LiquidationHunt => "liq",
        StrategyId::FundingExhaustionReversal => "fer",
        StrategyId::SmartMoneyDivergence => "smd",
        StrategyId::CvdDivergenceReversal => "cdr",
    }
}

fn near(level: Option<f64>, price: f64, tol: f64) -> bool {
    level
        .map(|x| x.is_finite() && price.is_finite() && (x - price).abs() <= tol)
        .unwrap_or(false)
}

fn push_signed(
    value: Option<f64>,
    threshold: f64,
    pos: &str,
    neg: &str,
    neutral: &str,
    tags: &mut Vec<String>,
) {
    match value {
        Some(x) if x > threshold => tags.push(pos.into()),
        Some(x) if x < -threshold => tags.push(neg.into()),
        Some(_) => tags.push(neutral.into()),
        None => tags.push(format!("{}_missing", neutral.trim_end_matches("_neutral"))),
    }
}

fn side_aligned(value: Option<f64>, is_long: bool, is_short: bool) -> bool {
    match value {
        Some(v) if is_long => v > 0.0,
        Some(v) if is_short => v < 0.0,
        _ => false,
    }
}

fn side_contradicts(value: Option<f64>, is_long: bool, is_short: bool) -> bool {
    match value {
        Some(v) if is_long => v < 0.0,
        Some(v) if is_short => v > 0.0,
        _ => false,
    }
}

fn dedup(mut values: Vec<String>) -> Vec<String> {
    values.sort();
    values.dedup();
    values
}
