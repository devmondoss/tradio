use crate::session::{TradingSession, classify_session};

use super::auction_state::{AuctionState, AuctionStateContext};
use super::vp_open_bias::DailyVpContext;
use super::detectors::{
    cvd_divergence_reversal, dom_imbalance_breakout, footprint_absorption_reversal,
    funding_exhaustion_reversal, liquidation_hunt, lvn_liquidity_vacuum_breakout,
    order_block_retest, session_open_breakout, smart_money_divergence,
    toxic_flow_gate::toxic_flow_gate, value_area_failed_auction, vwap_value_pullback_continuation,
};
use super::scoring::{StrategyProfile, score_signal};
use super::types::*;

fn blocked_log(missing_reason: &str) -> Vec<DetectorSnap> {
    let names = ["VAFA","LVN","DIB","SOB","OBR","FAR","VWAP","CDR","LIQ","FER","SMD"];
    names.iter().map(|&n| DetectorSnap {
        name: n.into(),
        status: DetectorStatus::GlobalBlocked,
        ..Default::default()
    }).chain(std::iter::once(DetectorSnap {
        name: "GLOBAL".into(),
        status: DetectorStatus::GlobalBlocked,
        missing: vec![missing_reason.into()],
        ..Default::default()
    })).collect()
}

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
        // CDR: divergencia CVD-precio — válido en sesiones con liquidez
        StrategyId::CvdDivergenceReversal => matches!(
            session,
            TradingSession::London | TradingSession::LondonNyOverlap | TradingSession::NewYork
        ),
        // SMD necesita volumen de sesión para confirmar divergencia
        StrategyId::SmartMoneyDivergence => matches!(
            session,
            TradingSession::London | TradingSession::LondonNyOverlap | TradingSession::NewYork
        ),
    }
}

fn detector_name_for(id: Option<StrategyId>) -> &'static str {
    match id {
        Some(StrategyId::ValueAreaFailedAuction) => "VAFA",
        Some(StrategyId::LvnLiquidityVacuumBreakout) => "LVN",
        Some(StrategyId::DomImbalanceBreakout) => "DIB",
        Some(StrategyId::SessionOpenBreakout) => "SOB",
        Some(StrategyId::OrderBlockRetest) => "OBR",
        Some(StrategyId::FootprintAbsorptionReversal) => "FAR",
        Some(StrategyId::VwapValuePullbackContinuation) => "VWAP",
        Some(StrategyId::LiquidationHunt) => "LIQ",
        Some(StrategyId::FundingExhaustionReversal) => "FER",
        Some(StrategyId::SmartMoneyDivergence) => "SMD",
        Some(StrategyId::CvdDivergenceReversal) => "CDR",
        None => "UNKNOWN",
    }
}

fn signal_is_reversal(id: Option<StrategyId>) -> bool {
    matches!(
        id,
        Some(StrategyId::ValueAreaFailedAuction)
            | Some(StrategyId::FootprintAbsorptionReversal)
            | Some(StrategyId::CvdDivergenceReversal)
            | Some(StrategyId::FundingExhaustionReversal)
            | Some(StrategyId::SmartMoneyDivergence)
    )
}

fn signal_is_breakout(id: Option<StrategyId>) -> bool {
    matches!(
        id,
        Some(StrategyId::LvnLiquidityVacuumBreakout)
            | Some(StrategyId::SessionOpenBreakout)
            | Some(StrategyId::DomImbalanceBreakout)
    )
}

/// VP Open Bias gate (Subdimi methodology): day type determines which setups have edge.
/// - TrendDay up   → block SHORT reversals (counter-trend against strong gap)
/// - TrendDay down → block LONG reversals
/// - InsideValue   → block breakout detectors (range day, trade extremes not breakouts)
/// - OutsideVA/FadeGap/Unknown → no hard blocks (softer bias applied in scoring)
fn apply_vp_bias_gate(
    candidates: &mut Vec<StrategySignal>,
    vp_bias: Option<&DailyVpContext>,
    rejections: &mut Vec<String>,
    detector_log: &mut Vec<DetectorSnap>,
) {
    let Some(bias_ctx) = vp_bias else { return };

    let blocked: Vec<(usize, &'static str)> = candidates
        .iter()
        .enumerate()
        .filter_map(|(i, s)| {
            let reason = if bias_ctx.trend_day_is_up() {
                if s.side == Some(Side::Short) && signal_is_reversal(s.strategy_id) {
                    Some("VP_TREND_DAY_UP_BLOCKS_SHORT_REVERSAL")
                } else {
                    None
                }
            } else if bias_ctx.trend_day_is_down() {
                if s.side == Some(Side::Long) && signal_is_reversal(s.strategy_id) {
                    Some("VP_TREND_DAY_DOWN_BLOCKS_LONG_REVERSAL")
                } else {
                    None
                }
            } else if bias_ctx.is_range_day() {
                if signal_is_breakout(s.strategy_id) {
                    Some("VP_RANGE_DAY_BLOCKS_BREAKOUT")
                } else {
                    None
                }
            } else {
                None
            };
            reason.map(|r| (i, r))
        })
        .collect();

    for (i, reason) in blocked.into_iter().rev() {
        let removed = candidates.remove(i);
        rejections.push(format!("{}:{}", detector_name_for(removed.strategy_id), reason));
        let name = detector_name_for(removed.strategy_id);
        if let Some(snap) = detector_log
            .iter_mut()
            .find(|d| d.name == name && d.status == DetectorStatus::Fired && d.score == removed.score)
        {
            snap.status = DetectorStatus::Skip;
            snap.missing.push(reason.into());
        }
    }
}

fn apply_auction_state_gate(
    candidates: &mut Vec<StrategySignal>,
    auction: Option<&AuctionStateContext>,
    rejections: &mut Vec<String>,
    detector_log: &mut Vec<DetectorSnap>,
) {
    let Some(ac) = auction else { return };

    let is_trend_continuation = |id: Option<StrategyId>| {
        matches!(
            id,
            Some(StrategyId::VwapValuePullbackContinuation)
                | Some(StrategyId::DomImbalanceBreakout)
                | Some(StrategyId::SessionOpenBreakout)
                | Some(StrategyId::LvnLiquidityVacuumBreakout)
        )
    };

    let is_vwap_dib = |id: Option<StrategyId>| {
        matches!(
            id,
            Some(StrategyId::VwapValuePullbackContinuation) | Some(StrategyId::DomImbalanceBreakout)
        )
    };

    let blocked: Vec<(usize, &'static str)> = candidates
        .iter()
        .enumerate()
        .filter_map(|(i, s)| {
            let reason = match ac.state {
                AuctionState::Distribution if ac.confidence >= 50 => {
                    if s.side == Some(Side::Long) && is_trend_continuation(s.strategy_id) {
                        Some("AUCTION:DISTRIBUTION_BLOCKS_LONG_CONTINUATION")
                    } else {
                        None
                    }
                }
                AuctionState::Accumulation if ac.confidence >= 50 => {
                    if s.side == Some(Side::Short) && is_trend_continuation(s.strategy_id) {
                        Some("AUCTION:ACCUMULATION_BLOCKS_SHORT_CONTINUATION")
                    } else {
                        None
                    }
                }
                AuctionState::UpImbalance if ac.confidence >= 70 => {
                    if s.side == Some(Side::Short) && is_vwap_dib(s.strategy_id) {
                        Some("AUCTION:UP_IMBALANCE_BLOCKS_SHORT")
                    } else {
                        None
                    }
                }
                AuctionState::DownImbalance if ac.confidence >= 70 => {
                    if s.side == Some(Side::Long) && is_vwap_dib(s.strategy_id) {
                        Some("AUCTION:DOWN_IMBALANCE_BLOCKS_LONG")
                    } else {
                        None
                    }
                }
                _ => None,
            };
            reason.map(|r| (i, r))
        })
        .collect();

    // Remove in reverse order to preserve indices
    for (i, reason) in blocked.into_iter().rev() {
        let removed = candidates.remove(i);
        rejections.push(format!("{}:{}", detector_name_for(removed.strategy_id), reason));
        let name = detector_name_for(removed.strategy_id);
        if let Some(snap) = detector_log
            .iter_mut()
            .find(|d| d.name == name && d.status == DetectorStatus::Fired && d.score == removed.score)
        {
            snap.status = DetectorStatus::Skip;
            snap.missing.push(reason.into());
        }
    }
}

pub fn route_strategy(ctx: &StrategyMarketContext, cfg: &StrategyConfig) -> (StrategySignal, Vec<DetectorSnap>) {
    if !cfg.enabled {
        return (StrategySignal {
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
        }, blocked_log("STRATEGY_DISABLED"));
    }

    // ATR guard: if ATR is unavailable or < $1, stops and R:R are unreliable.
    if ctx.atr.map(|a| a < 1.0).unwrap_or(true) {
        return (StrategySignal {
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
        }, blocked_log("ATR_NOT_READY"));
    }

    if let Err(reason) = toxic_flow_gate(ctx, cfg) {
        return (StrategySignal {
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
            missing: vec![reason.clone()],
            invalidation: vec![],
            created_at_ms: ctx.timestamp_ms,
        }, blocked_log(&reason));
    }

    // Sesión activa para filtrado opcional
    let current_session = classify_session(ctx.timestamp_ms).session;

    let mut candidates: Vec<StrategySignal> = Vec::new();
    // Rejection reasons per detector — populated when a detector returns None
    let mut rejections: Vec<String> = Vec::new();
    let mut detector_log: Vec<DetectorSnap> = Vec::new();

    macro_rules! try_detect {
        ($name:literal, $id:expr, $expr:expr) => {
            // Filtro de sesión: descartar antes de pasar al scoring
            if cfg.session_filter_enabled && !session_valid_for($id, current_session) {
                rejections.push(format!("{}:SESSION_INVALID", $name));
                detector_log.push(DetectorSnap {
                    name: $name.into(),
                    status: DetectorStatus::SessionInvalid,
                    ..Default::default()
                });
            } else {
                match $expr {
                    Some(s) => {
                        let scored = score_signal(ctx, s);
                        detector_log.push(DetectorSnap {
                            name: $name.into(),
                            status: DetectorStatus::Fired,
                            score: scored.score,
                            side: scored.side,
                            evidence: scored.evidence.clone(),
                            missing: scored.missing.clone(),
                        });
                        candidates.push(scored);
                    }
                    None => {
                        rejections.push(format!("{}:SKIP", $name));
                        detector_log.push(DetectorSnap {
                            name: $name.into(),
                            status: DetectorStatus::Skip,
                            ..Default::default()
                        });
                    }
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
    try_detect!(
        "CDR",
        StrategyId::CvdDivergenceReversal,
        cvd_divergence_reversal::detect(ctx, cfg)
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
        for name in ["LIQ", "FER", "SMD"] {
            detector_log.push(DetectorSnap {
                name: name.into(),
                status: DetectorStatus::GlobalBlocked,
                missing: vec!["INST:NULL".into()],
                ..Default::default()
            });
        }
    }

    apply_auction_state_gate(&mut candidates, ctx.auction_state.as_ref(), &mut rejections, &mut detector_log);
    apply_vp_bias_gate(&mut candidates, ctx.vp_open_bias.as_ref(), &mut rejections, &mut detector_log);

    let best_idx = candidates
        .iter()
        .enumerate()
        .max_by(|(_, a), (_, b)| {
            a.score
                .partial_cmp(&b.score)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| a.strategy_id.map(|s| s as u8).cmp(&b.strategy_id.map(|s| s as u8)))
        })
        .map(|(i, _)| i);

    // min_score diferenciado por perfil de estrategia
    let effective_min_score = |id: Option<StrategyId>| -> f64 {
        match id.map(StrategyProfile::from_id) {
            Some(StrategyProfile::Institutional) => cfg.min_score_institutional,
            _ => cfg.min_score,
        }
    };

    // Mark detector_log entries for candidates that fired
    // (they were added with Fired status; we now promote winner to Active or LowScore)
    let result = match best_idx {
        Some(idx) => {
            let signal = candidates.remove(idx);
            let min_score = effective_min_score(signal.strategy_id);
            if signal.score >= min_score {
                // Mark the winning detector as Active
                if let Some(snap) = detector_log.iter_mut().find(|d| {
                    d.status == DetectorStatus::Fired
                        && d.score == signal.score
                        && d.side == signal.side
                }) {
                    snap.status = DetectorStatus::Active;
                }
                signal
            } else {
                // Mark it as LowScore
                if let Some(snap) = detector_log.iter_mut().find(|d| {
                    d.status == DetectorStatus::Fired
                        && d.score == signal.score
                        && d.side == signal.side
                }) {
                    snap.status = DetectorStatus::LowScore;
                }
                StrategySignal {
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
                }
            }
        }
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
    };

    (result, detector_log)
}
