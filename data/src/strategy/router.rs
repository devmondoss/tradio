use super::detectors::{
    funding_exhaustion_reversal, liquidation_hunt, lvn_liquidity_vacuum_breakout,
    smart_money_divergence, toxic_flow_gate::toxic_flow_gate, value_area_failed_auction,
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
            regime: ctx.regime,
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

    // ATR guard: if ATR is unavailable or < $1, stops and R:R are unreliable.
    if ctx.atr.map(|a| a < 1.0).unwrap_or(true) {
        return StrategySignal {
            action: StrategyAction::Wait,
            strategy_id: None,
            side: None,
            regime: ctx.regime,
            entry_price: None,
            stop_price: None,
            target_price: None,
            score: 0.0,
            ttl_ms: 0,
            evidence: vec![],
            missing: vec!["ATR_NOT_READY".into()],
            invalidation: vec![],
            created_at_ms: ctx.timestamp_ms,
        };
    }

    if let Err(reason) = toxic_flow_gate(ctx, cfg) {
        return StrategySignal {
            action: StrategyAction::Blocked,
            strategy_id: None,
            side: None,
            regime: ctx.regime,
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

    // Institutional detectors — only run when institutional data is available
    if let Some(inst) = &ctx.institutional {
        if let Some(s) = liquidation_hunt::detect(ctx, inst, cfg) {
            candidates.push(score_signal(ctx, s));
        }

        if let Some(s) = funding_exhaustion_reversal::detect(ctx, inst, cfg) {
            candidates.push(score_signal(ctx, s));
        }

        if let Some(s) = smart_money_divergence::detect(ctx, inst, cfg) {
            candidates.push(score_signal(ctx, s));
        }
    }

    let best = candidates
        .into_iter()
        .max_by(|a, b| a.score.partial_cmp(&b.score).unwrap());

    match best {
        Some(signal) if signal.score >= cfg.min_score => signal,
        Some(signal) => StrategySignal {
            action: StrategyAction::Wait,
            regime: signal.regime,
            entry_price: None,
            stop_price: None,
            target_price: None,
            missing: {
                let mut m = signal.missing.clone();
                m.push("LOW_SCORE".into());
                m
            },
            // retain strategy_id, side, score, evidence for calibration logging
            strategy_id: signal.strategy_id,
            side: signal.side,
            score: signal.score,
            ttl_ms: signal.ttl_ms,
            evidence: signal.evidence,
            invalidation: vec![],
            created_at_ms: signal.created_at_ms,
        },
        None => StrategySignal {
            action: StrategyAction::Wait,
            strategy_id: None,
            side: None,
            regime: ctx.regime,
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
