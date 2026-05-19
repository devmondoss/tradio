use crate::session::{TradingSession, classify_session};

use super::detectors::{
    dom_imbalance_breakout, footprint_absorption_reversal, funding_exhaustion_reversal,
    liquidation_hunt, lvn_liquidity_vacuum_breakout, order_block_retest, session_open_breakout,
    smart_money_divergence, toxic_flow_gate::toxic_flow_gate, value_area_failed_auction,
    vwap_value_pullback_continuation,
};
use super::scoring::{StrategyProfile, score_signal};
use super::types::*;

/// Sesiones válidas hardcodeadas por estrategia según la propuesta de integración.
/// Retorna `true` si la sesión activa es válida para ejecutar esa estrategia.
fn session_valid_for(id: StrategyId, session: TradingSession) -> bool {
    match id {
        // VAFA requiere liquidez real para acceptance/rejection
        StrategyId::ValueAreaFailedAuction => matches!(
            session,
            TradingSession::London | TradingSession::LondonNyOverlap | TradingSession::NewYork
        ),
        // VWAP pullback más limpio en aperturas de sesión
        StrategyId::VwapValuePullbackContinuation => matches!(
            session,
            TradingSession::London | TradingSession::LondonNyOverlap | TradingSession::NewYork
        ),
        // LVN vacíos se llenan con liquidez de sesión
        StrategyId::LvnLiquidityVacuumBreakout => matches!(
            session,
            TradingSession::London | TradingSession::LondonNyOverlap | TradingSession::NewYork
        ),
        StrategyId::DomImbalanceBreakout => matches!(
            session,
            TradingSession::London | TradingSession::LondonNyOverlap | TradingSession::NewYork
        ),
        StrategyId::SessionOpenBreakout => matches!(
            session,
            TradingSession::London | TradingSession::LondonNyOverlap | TradingSession::NewYork
        ),
        StrategyId::OrderBlockRetest => matches!(
            session,
            TradingSession::London | TradingSession::LondonNyOverlap | TradingSession::NewYork
        ),
        StrategyId::FootprintAbsorptionReversal => matches!(
            session,
            TradingSession::London | TradingSession::LondonNyOverlap | TradingSession::NewYork
        ),
        // Liquidaciones masivas ocurren en aperturas de Londres y NY
        StrategyId::LiquidationHunt => matches!(
            session,
            TradingSession::London | TradingSession::LondonNyOverlap | TradingSession::NewYork
        ),
        // Funding extremo se acumula en rangos lentos (Asia y pre-London)
        StrategyId::FundingExhaustionReversal => {
            matches!(session, TradingSession::Asia | TradingSession::London)
        }
        // SMD necesita volumen de sesión para confirmar divergencia
        StrategyId::SmartMoneyDivergence => matches!(
            session,
            TradingSession::London | TradingSession::LondonNyOverlap | TradingSession::NewYork
        ),
    }
}

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

    // Sesión activa para filtrado opcional
    let current_session = classify_session(ctx.timestamp_ms).session;

    let mut candidates = Vec::new();
    // Rejection reasons per detector — populated when a detector returns None
    let mut rejections: Vec<String> = Vec::new();

    macro_rules! try_detect {
        ($name:literal, $id:expr, $expr:expr) => {
            // Filtro de sesión: descartar antes de pasar al scoring
            if cfg.session_filter_enabled && !session_valid_for($id, current_session) {
                rejections.push(format!("{}:SESSION_INVALID", $name));
            } else {
                match $expr {
                    Some(s) => candidates.push(score_signal(ctx, s)),
                    None => rejections.push(format!("{}:SKIP", $name)),
                }
            }
        };
    }

    try_detect!(
        "VAFA",
        StrategyId::ValueAreaFailedAuction,
        value_area_failed_auction::detect(ctx, cfg)
    );
    try_detect!(
        "LVN",
        StrategyId::LvnLiquidityVacuumBreakout,
        lvn_liquidity_vacuum_breakout::detect(ctx, cfg)
    );
    try_detect!(
        "DIB",
        StrategyId::DomImbalanceBreakout,
        dom_imbalance_breakout::detect(ctx, cfg)
    );
    try_detect!(
        "SOB",
        StrategyId::SessionOpenBreakout,
        session_open_breakout::detect(ctx, cfg)
    );
    try_detect!(
        "OBR",
        StrategyId::OrderBlockRetest,
        order_block_retest::detect(ctx, cfg)
    );
    try_detect!(
        "FAR",
        StrategyId::FootprintAbsorptionReversal,
        footprint_absorption_reversal::detect(ctx, cfg)
    );
    try_detect!(
        "VWAP",
        StrategyId::VwapValuePullbackContinuation,
        vwap_value_pullback_continuation::detect(ctx, cfg)
    );

    // Institutional detectors — only run when institutional data is available
    if let Some(inst) = &ctx.institutional {
        try_detect!(
            "LIQ",
            StrategyId::LiquidationHunt,
            liquidation_hunt::detect(ctx, inst, cfg)
        );
        try_detect!(
            "FER",
            StrategyId::FundingExhaustionReversal,
            funding_exhaustion_reversal::detect(ctx, inst, cfg)
        );
        try_detect!(
            "SMD",
            StrategyId::SmartMoneyDivergence,
            smart_money_divergence::detect(ctx, inst, cfg)
        );
    } else {
        rejections.push("INST:NULL".into());
    }

    let best = candidates
        .into_iter()
        .max_by(|a, b| a.score.partial_cmp(&b.score).unwrap());

    // min_score diferenciado por perfil de estrategia
    let effective_min_score = |id: Option<StrategyId>| -> f64 {
        match id.map(StrategyProfile::from_id) {
            Some(StrategyProfile::Institutional) => cfg.min_score_institutional,
            _ => cfg.min_score,
        }
    };

    match best {
        Some(ref signal) if signal.score >= effective_min_score(signal.strategy_id) => {
            best.unwrap()
        }
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
            missing: rejections,
            invalidation: vec![],
            created_at_ms: ctx.timestamp_ms,
        },
    }
}
