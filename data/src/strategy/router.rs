use super::detectors::{
    lvn_liquidity_vacuum_breakout, toxic_flow_gate::toxic_flow_gate, value_area_failed_auction,
    vwap_value_pullback_continuation,
};
use super::scoring::score_signal;
use super::types::*;

pub fn route_strategy(ctx: &StrategyMarketContext, cfg: &StrategyConfig) -> StrategySignal {
    if !cfg.enabled {
        return StrategySignal {
            action: StrategyAction::Wait,
            strategy_id: None,
            side: None,
            entry_price: None,
            stop_price: None,
            target_price: None,
            score: 0.0,
            ttl_ms: 0,
            evidence: vec![],
            missing: vec!["STRATEGY_DISABLED".into()],
            invalidation: vec![],
            created_at_ms: ctx.timestamp_ms,
        };
    }

    if let Err(reason) = toxic_flow_gate(ctx, cfg) {
        return StrategySignal {
            action: StrategyAction::Blocked,
            strategy_id: None,
            side: None,
            entry_price: None,
            stop_price: None,
            target_price: None,
            score: 0.0,
            ttl_ms: 0,
            evidence: vec![],
            missing: vec![reason],
            invalidation: vec![],
            created_at_ms: ctx.timestamp_ms,
        };
    }

    let mut candidates = Vec::new();

    if let Some(s) = value_area_failed_auction::detect(ctx, cfg) {
        candidates.push(score_signal(ctx, s));
    }

    if let Some(s) = lvn_liquidity_vacuum_breakout::detect(ctx, cfg) {
        candidates.push(score_signal(ctx, s));
    }

    if let Some(s) = vwap_value_pullback_continuation::detect(ctx, cfg) {
        candidates.push(score_signal(ctx, s));
    }

    let best = candidates
        .into_iter()
        .max_by(|a, b| a.score.partial_cmp(&b.score).unwrap());

    match best {
        Some(signal) if signal.score >= cfg.min_score => signal,
        Some(mut signal) => {
            signal.action = StrategyAction::Wait;
            signal.missing.push("LOW_SCORE".into());
            signal
        }
        None => StrategySignal {
            action: StrategyAction::Wait,
            strategy_id: None,
            side: None,
            entry_price: None,
            stop_price: None,
            target_price: None,
            score: 0.0,
            ttl_ms: 0,
            evidence: vec![],
            missing: vec!["NO_VALID_SETUP".into()],
            invalidation: vec![],
            created_at_ms: ctx.timestamp_ms,
        },
    }
}
