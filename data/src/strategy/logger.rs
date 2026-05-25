use super::playbook_reasoning::PlaybookReasoning;
use super::types::*;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::PathBuf;

fn shadow_events_dir() -> PathBuf {
    let base = std::env::current_dir()
        .unwrap_or_else(|_| PathBuf::from("."))
        .join("logs");
    let _ = fs::create_dir_all(&base);
    base
}

/// Log every signal action to its corresponding JSONL file.
///
/// - `strategy_signals.jsonl`  — ShadowSignal (active paper trades)
/// - `strategy_rejected.jsonl` — Wait with LOW_SCORE (for threshold calibration)
/// - `strategy_blocked.jsonl`  — Blocked by gate (for gate calibration)
pub fn log_signal(ctx: &StrategyMarketContext, signal: &StrategySignal) {
    log_signal_inner(ctx, signal, None);
}

pub fn log_signal_with_reasoning(
    ctx: &StrategyMarketContext,
    signal: &StrategySignal,
    reasoning: &PlaybookReasoning,
) {
    log_signal_inner(ctx, signal, Some(reasoning));
}

fn log_signal_inner(
    ctx: &StrategyMarketContext,
    signal: &StrategySignal,
    reasoning: Option<&PlaybookReasoning>,
) {
    let filename = match signal.action {
        StrategyAction::ShadowSignal => "strategy_signals.jsonl",
        StrategyAction::Wait => "strategy_rejected.jsonl",
        StrategyAction::Blocked => "strategy_blocked.jsonl",
    };

    let entry = SignalLogEntry {
        symbol: ctx.symbol.clone(),
        timestamp_ms: ctx.timestamp_ms,
        strategy: signal.strategy_id.map(|id| format!("{:?}", id)),
        side: signal.side.map(|s| format!("{:?}", s)),
        regime: format!("{:?}", signal.regime),
        action: format!("{:?}", signal.action),
        entry_price: signal.entry_price,
        stop_price: signal.stop_price,
        target_price: signal.target_price,
        score: signal.score,
        ttl_ms: signal.ttl_ms,
        evidence: signal.evidence.clone(),
        missing: signal.missing.clone(),
        invalidation: signal.invalidation.clone(),
        playbook_reasoning: reasoning.cloned(),
        context: SignalContext {
            price: ctx.price,
            vwap_session: ctx.vwap.vwap_session,
            poc: ctx.volume_profile.poc,
            vah: ctx.volume_profile.vah,
            val: ctx.volume_profile.val,
            cvd_slope: ctx.flow.cvd_slope,
            delta: ctx.flow.delta,
            vpin: ctx.flow.vpin,
            spread_bps: ctx.orderbook.spread_bps,
            obi_l5: ctx.orderbook.obi_l5,
        },
    };

    let path = shadow_events_dir().join(filename);

    if let Ok(json) = serde_json::to_string(&entry)
        && let Ok(mut file) = OpenOptions::new().create(true).append(true).open(&path)
    {
        let _ = writeln!(file, "{}", json);
    }
}

#[derive(serde::Serialize)]
struct SignalLogEntry {
    symbol: String,
    timestamp_ms: i64,
    strategy: Option<String>,
    side: Option<String>,
    regime: String,
    action: String,
    entry_price: Option<f64>,
    stop_price: Option<f64>,
    target_price: Option<f64>,
    score: f64,
    ttl_ms: i64,
    evidence: Vec<String>,
    missing: Vec<String>,
    invalidation: Vec<String>,
    playbook_reasoning: Option<PlaybookReasoning>,
    context: SignalContext,
}

#[derive(serde::Serialize)]
struct SignalContext {
    price: f64,
    vwap_session: Option<f64>,
    poc: Option<f64>,
    vah: Option<f64>,
    val: Option<f64>,
    cvd_slope: Option<f64>,
    delta: Option<f64>,
    vpin: Option<f64>,
    spread_bps: Option<f64>,
    obi_l5: Option<f64>,
}
