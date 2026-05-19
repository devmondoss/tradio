/// Writes signals, closed trades, regime changes, and lab signals to Supabase via REST API.
///
/// Uses fire-and-forget tokio tasks — never blocks the bar-close path.
/// Failures are logged to stderr but do not crash the monitor.
///
/// Environment variables:
///   SUPABASE_URL  — https://[PROJECT].supabase.co
///   SUPABASE_KEY  — service_role key (bypasses RLS)
use data::strategy::{
    lab::types::{
        BlockReason, HorizonOutcome, LabOutcome, LabSignal, OutcomeStatus,
        StrategyRuntimeStatus,
    },
    paper::ClosedTrade,
    types::{StrategyAction, StrategyMarketContext, StrategySignal},
};
use serde_json::{json, Value};

#[derive(Clone)]
pub struct SupabaseWriter {
    url: String,
    key: String,
    client: reqwest::Client,
}

impl SupabaseWriter {
    /// Returns None if SUPABASE_URL or SUPABASE_KEY are not set.
    pub fn from_env() -> Option<Self> {
        let url = std::env::var("SUPABASE_URL").ok()?;
        let key = std::env::var("SUPABASE_KEY").ok()?;
        eprintln!("[supabase] writer initialized for {url}");
        Some(Self {
            url,
            key,
            client: reqwest::Client::new(),
        })
    }

    /// Inserts a signal into shadow_signals. Returns the UUID assigned by Supabase.
    /// The UUID is needed to link signal_outcomes rows. Returns None on failure.
    pub async fn write_signal(
        &self,
        signal: &StrategySignal,
        ctx: &StrategyMarketContext,
    ) -> Option<String> {
        if signal.action == StrategyAction::Wait {
            return None;
        }

        let body = build_signal_row(signal, ctx);
        let url = format!("{}/rest/v1/shadow_signals", self.url);
        let result = self
            .client
            .post(&url)
            .header("apikey", &self.key)
            .header("Authorization", format!("Bearer {}", self.key))
            .header("Content-Type", "application/json")
            .header("Prefer", "return=representation")
            .json(&body)
            .send()
            .await;

        match result {
            Err(e) => {
                eprintln!("[supabase] POST shadow_signals failed: {e}");
                None
            }
            Ok(r) if !r.status().is_success() => {
                let status = r.status();
                let text = r.text().await.unwrap_or_default();
                eprintln!("[supabase] POST shadow_signals error {status}: {text}");
                None
            }
            Ok(r) => {
                let json: Value = r.json().await.unwrap_or(Value::Null);
                json.as_array()
                    .and_then(|arr| arr.first())
                    .and_then(|row| row.get("id"))
                    .and_then(|id| id.as_str())
                    .map(|s| s.to_string())
            }
        }
    }

    /// Inserts a closed trade into signal_outcomes. Fire-and-forget.
    /// signal_uuid links this trade to its shadow_signals row (NOT NULL FK).
    pub fn write_trade(&self, trade: &ClosedTrade, signal_uuid: Option<String>) {
        let Some(uuid) = signal_uuid else {
            eprintln!("[supabase] write_trade skipped — no signal_uuid (trade not linked to a signal)");
            return;
        };
        let body = build_trade_row(trade, &uuid);
        let writer = self.clone();

        tokio::spawn(async move {
            writer.post("signal_outcomes", &body).await;
        });
    }

    /// Inserts a row into regime_history when the regime changes. Fire-and-forget.
    pub fn write_regime_change(
        &self,
        timestamp_ms: i64,
        regime_slow: &str,
        regime_fast: &str,
        regime_combined: &str,
        duration_ms: Option<i64>,
        price: f64,
    ) {
        let body = json!({
            "timestamp_ms":    timestamp_ms,
            "regime_slow":     regime_slow,
            "regime_fast":     regime_fast,
            "regime_combined": regime_combined,
            "duration_ms":     duration_ms,
            "price_at_change": price,
        });
        let writer = self.clone();

        tokio::spawn(async move {
            writer.post("regime_history", &body).await;
        });
    }

    /// Inserts a resolved outcome into intrabar_outcomes. Fire-and-forget.
    pub fn write_outcome(
        &self,
        signal_ms: i64,
        source: &str,
        strategy_id: &str,
        side: &str,
        entry_px: f64,
        stop_px: f64,
        target_px: f64,
        risk: f64,
        mfe: f64,
        mae: f64,
        rr_at_1m: Option<f64>,
        rr_at_3m: Option<f64>,
        rr_at_5m: Option<f64>,
        hit_target: bool,
        hit_stop: bool,
        age_ms: i64,
    ) {
        let body = json!({
            "signal_ms":   signal_ms,
            "source":      source,
            "strategy_id": strategy_id,
            "side":        side,
            "entry_px":    entry_px,
            "stop_px":     stop_px,
            "target_px":   target_px,
            "risk":        risk,
            "mfe":         mfe,
            "mae":         mae,
            "rr_at_1m":    rr_at_1m,
            "rr_at_3m":    rr_at_3m,
            "rr_at_5m":    rr_at_5m,
            "hit_target":  hit_target,
            "hit_stop":    hit_stop,
            "age_ms":      age_ms,
        });
        let writer = self.clone();
        tokio::spawn(async move {
            writer.post("intrabar_outcomes", &body).await;
        });
    }

    /// PATCHes signal_outcomes with a forward price horizon (price_5m, r_5m, etc.).
    /// Fire-and-forget — called from flush_pending_horizons via tokio::spawn.
    pub async fn patch_horizon(&self, uuid: &str, label: &str, price: f64, r: f64) {
        let price_field = format!("price_{label}");
        let r_field = format!("r_{label}");
        let body = json!({ &price_field: price, &r_field: r });
        let url = format!(
            "{}/rest/v1/signal_outcomes?id=eq.{uuid}",
            self.url
        );
        let result = self
            .client
            .patch(&url)
            .header("apikey", &self.key)
            .header("Authorization", format!("Bearer {}", self.key))
            .header("Content-Type", "application/json")
            .header("Prefer", "return=minimal")
            .json(&body)
            .send()
            .await;

        match result {
            Err(e) => eprintln!("[supabase] PATCH signal_outcomes horizon {label} failed: {e}"),
            Ok(r) if !r.status().is_success() => {
                let status = r.status();
                let text = r.text().await.unwrap_or_default();
                eprintln!("[supabase] PATCH signal_outcomes horizon {label} error {status}: {text}");
            }
            Ok(_) => {}
        }
    }

    // ── Lab tables ────────────────────────────────────────────────────────────

    /// Inserts a LabSignal into lab_signals. Returns the Supabase UUID on success.
    /// The UUID is needed to link lab_outcomes rows (signal_id FK).
    /// Returns None on failure — the signal is lost but the monitor keeps running.
    pub async fn write_lab_signal(&self, signal: &LabSignal) -> Option<String> {
        let body = build_lab_signal_row(signal);
        let url = format!("{}/rest/v1/lab_signals", self.url);
        let result = self
            .client
            .post(&url)
            .header("apikey", &self.key)
            .header("Authorization", format!("Bearer {}", self.key))
            .header("Content-Type", "application/json")
            .header("Prefer", "return=representation")
            .json(&body)
            .send()
            .await;

        match result {
            Err(e) => {
                eprintln!("[supabase] POST lab_signals failed: {e}");
                None
            }
            Ok(r) if !r.status().is_success() => {
                let status = r.status();
                let text = r.text().await.unwrap_or_default();
                eprintln!("[supabase] POST lab_signals error {status}: {text}");
                None
            }
            Ok(r) => {
                let json: Value = r.json().await.unwrap_or(Value::Null);
                json.as_array()
                    .and_then(|arr| arr.first())
                    .and_then(|row| row.get("id"))
                    .and_then(|id| id.as_str())
                    .map(|s| s.to_string())
            }
        }
    }

    /// Inserts a completed LabOutcome into lab_outcomes. Fire-and-forget.
    /// signal_uuid links this outcome to its lab_signals row (FK).
    pub fn write_lab_outcome(&self, outcome: &LabOutcome, signal_uuid: Option<String>) {
        let Some(uuid) = signal_uuid else {
            eprintln!("[supabase] write_lab_outcome skipped — no signal_uuid");
            return;
        };
        let body = build_lab_outcome_row(outcome, &uuid);
        let writer = self.clone();
        tokio::spawn(async move {
            writer.post("lab_outcomes", &body).await;
        });
    }

    async fn post(&self, table: &str, body: &Value) {
        let url = format!("{}/rest/v1/{}", self.url, table);
        let result = self
            .client
            .post(&url)
            .header("apikey", &self.key)
            .header("Authorization", format!("Bearer {}", self.key))
            .header("Content-Type", "application/json")
            .header("Prefer", "return=minimal")
            .json(body)
            .send()
            .await;

        match result {
            Err(e) => eprintln!("[supabase] POST {table} failed: {e}"),
            Ok(r) if !r.status().is_success() => {
                let status = r.status();
                let text = r.text().await.unwrap_or_default();
                eprintln!("[supabase] POST {table} error {status}: {text}");
            }
            Ok(_) => {}
        }
    }
}

fn build_signal_row(signal: &StrategySignal, ctx: &StrategyMarketContext) -> Value {
    let inst = ctx.institutional.as_ref();

    let evidence: Vec<String> = signal.evidence.iter().map(|e| format!("{e:?}")).collect();
    let missing: Vec<String>  = signal.missing.iter().map(|m| format!("{m:?}")).collect();

    let regime_str = format!("{:?}", ctx.regime);

    // Split HVNs by current price for directional analysis in calibration
    let price = ctx.price;
    let hvn_above: Vec<f64> = ctx.volume_profile.hvn_nearby.iter().copied()
        .filter(|&h| h > price).collect();
    let hvn_below: Vec<f64> = ctx.volume_profile.hvn_nearby.iter().copied()
        .filter(|&h| h < price).collect();
    let nearest_wall_above = ctx.orderbook.walls_above.first().copied();
    let nearest_wall_below = ctx.orderbook.walls_below.first().copied();

    json!({
        "timestamp_ms":   ctx.timestamp_ms,
        "strategy":       signal.strategy_id.map(|s| format!("{s:?}")).unwrap_or_default(),
        "side":           signal.side.map(|s| format!("{s:?}")),
        "regime_slow":    regime_str,
        "regime_fast":    regime_str,
        "regime_combined":regime_str,
        "action":         format!("{:?}", signal.action),
        "entry_price":    signal.entry_price,
        "stop_price":     signal.stop_price,
        "target_price":   signal.target_price,
        "score":          signal.score,
        "ttl_ms":         signal.ttl_ms,
        "evidence":       evidence,
        "missing":        missing,

        // Paper trader config
        "leverage":       ctx.leverage,

        // Technical context
        "price":          ctx.price,
        "vwap_session":   ctx.vwap.vwap_session,
        "poc":            ctx.volume_profile.poc,
        "vah":            ctx.volume_profile.vah,
        "val":            ctx.volume_profile.val,
        "cvd_slope":      ctx.flow.cvd_slope,
        "cvd_absolute":   ctx.flow.cvd,
        "atr":            ctx.atr,
        "spread_bps":     ctx.orderbook.spread_bps,
        "obi_l5":         ctx.orderbook.obi_l5,
        "microprice":     ctx.orderbook.microprice,

        // Structural levels (for calibration of find_structural_target)
        "hvn_levels_above":   hvn_above,
        "hvn_levels_below":   hvn_below,
        "nearest_wall_above": nearest_wall_above,
        "nearest_wall_below": nearest_wall_below,
        "swing_high_20":      ctx.swing_high_20,
        "swing_low_20":       ctx.swing_low_20,

        // Institutional context
        "short_liq_usd_5m":     inst.map(|i| i.liquidations.short_liq_usd_5m),
        "long_liq_usd_5m":      inst.map(|i| i.liquidations.long_liq_usd_5m),
        "total_liq_usd_5m":     inst.map(|i| i.liquidations.total_usd_5m),
        "cascade_active":       inst.map(|i| i.liquidations.cascade_detected),
        "liq_dominant_side":    inst.map(|i| format!("{:?}", i.liquidations.dominant_side)),
        "top_traders_long_pct": inst.map(|i| i.ls_ratio.top_traders_long_pct),
        "retail_long_pct":      inst.map(|i| i.ls_ratio.retail_long_pct),
        "ls_divergence":        inst.map(|i| i.ls_ratio.top_traders_long_pct - i.ls_ratio.retail_long_pct),
        "divergence_signal":    inst.map(|i| format!("{:?}", i.ls_ratio.divergence_signal)),
        "oi_current_btc":       inst.map(|i| i.oi_trend.current),
        "oi_change_30m_pct":    inst.map(|i| i.oi_trend.change_30m),
        "oi_trend":             inst.map(|i| format!("{:?}", i.oi_trend.trend)),
        "funding_current":      inst.map(|i| i.funding.current),
        "funding_regime":       inst.map(|i| format!("{:?}", i.funding.regime)),
        "taker_imbalance":      inst.and_then(|i| i.taker_ratio.as_ref().map(|t| t.taker_imbalance)),
    })
}

fn build_lab_signal_row(signal: &LabSignal) -> Value {
    let snap = &signal.snapshot;

    let status_str = match &signal.status {
        StrategyRuntimeStatus::Asleep => "Asleep".to_string(),
        StrategyRuntimeStatus::Observed => "Observed".to_string(),
        StrategyRuntimeStatus::ShadowSignal => "ShadowSignal".to_string(),
        StrategyRuntimeStatus::Blocked { .. } => "Blocked".to_string(),
    };
    let block_reason = match &signal.status {
        StrategyRuntimeStatus::Blocked { reason } => Some(match reason {
            BlockReason::SessionFilter => "SessionFilter".to_string(),
            BlockReason::DataQuality { .. } => "DataQuality".to_string(),
            BlockReason::SpreadGate => "SpreadGate".to_string(),
            BlockReason::RegimeStress => "RegimeStress".to_string(),
            BlockReason::CooldownActive => "CooldownActive".to_string(),
            BlockReason::RRTooLow { .. } => "RRTooLow".to_string(),
            BlockReason::LiqInstability => "LiqInstability".to_string(),
        }),
        _ => None,
    };

    // Serialize the full snapshot as jsonb
    let snapshot_value = serde_json::to_value(snap).unwrap_or(Value::Null);

    json!({
        "id":            signal.signal_id.to_string(),
        "strategy_id":   signal.strategy_id.as_str(),
        "status":        status_str,
        "maturity":      format!("{:?}", signal.maturity),
        "timestamp_ms":  signal.timestamp_ms,
        "action":        signal.action.map(|a| format!("{a:?}")),
        "entry_price":   signal.entry_price,
        "target":        signal.target,
        "stop":          signal.stop,
        "rr":            signal.rr,
        "confidence":    signal.confidence,
        "missing_data":  signal.missing_data,
        "block_reason":  block_reason,
        "snapshot":      snapshot_value,
    })
}

fn build_lab_outcome_row(outcome: &LabOutcome, signal_uuid: &str) -> Value {
    fn horizon_json(h: &Option<HorizonOutcome>) -> Value {
        match h {
            None => Value::Null,
            Some(h) => json!({
                "price": h.price_at_horizon,
                "r":     h.r_achieved,
                "correct": h.direction_correct,
            }),
        }
    }

    let final_status = outcome.final_status.as_ref().map(|s| match s {
        OutcomeStatus::TargetHit => "TargetHit".to_string(),
        OutcomeStatus::StopHit => "StopHit".to_string(),
        OutcomeStatus::TtlExpired { .. } => "TtlExpired".to_string(),
        OutcomeStatus::StillOpen => "StillOpen".to_string(),
    });

    json!({
        "signal_id":     signal_uuid,
        "strategy_id":   outcome.strategy_id.as_str(),
        "entry_price":   outcome.entry_price,
        "target":        outcome.target,
        "stop":          outcome.stop,
        "side":          format!("{:?}", outcome.side),
        "outcome_30s":   horizon_json(&outcome.outcome_30s),
        "outcome_1m":    horizon_json(&outcome.outcome_1m),
        "outcome_3m":    horizon_json(&outcome.outcome_3m),
        "outcome_5m":    horizon_json(&outcome.outcome_5m),
        "outcome_15m":   horizon_json(&outcome.outcome_15m),
        "outcome_ttl":   horizon_json(&outcome.outcome_ttl),
        "mfe":           outcome.mfe,
        "mae":           outcome.mae,
        "final_status":  final_status,
    })
}

fn build_trade_row(trade: &ClosedTrade, signal_uuid: &str) -> Value {
    let duration_ms = trade.closed_at_ms - trade.opened_at_ms;
    let risk = (trade.entry_price - trade.stop_price.unwrap_or(trade.entry_price)).abs();
    let r_multiple = if risk > 0.0 {
        (trade.exit_price - trade.entry_price)
            * if trade.side == "Long" { 1.0 } else { -1.0 }
            / risk
    } else {
        0.0
    };

    json!({
        "signal_id":         signal_uuid,
        "timestamp_ms":      trade.opened_at_ms,
        "close_reason":      trade.close_reason,
        "close_price":       trade.exit_price,
        "duration_ms":       duration_ms,
        "r_multiple":        r_multiple,
        "pnl_gross_usd":     trade.gross_pnl,
        "pnl_net_usd":       trade.net_pnl,
        "fee_entry_usd":     trade.fees_paid / 2.0,
        "fee_exit_usd":      trade.fees_paid / 2.0,
        "funding_cost_usd":  trade.funding_paid,
        "mfe_r":             if risk > 0.0 { trade.mfe / risk } else { 0.0 },
        "mae_r":             if risk > 0.0 { trade.mae / risk } else { 0.0 },
        "is_partial":        trade.is_partial,
        "partial_fraction":  trade.partial_fraction,
    })
}
