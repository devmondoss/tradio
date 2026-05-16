/// Writes signals and closed trades to Supabase via REST API.
///
/// Uses fire-and-forget tokio tasks — never blocks the bar-close path.
/// Failures are logged to stderr but do not crash the monitor.
///
/// Environment variables:
///   SUPABASE_URL  — https://[PROJECT].supabase.co
///   SUPABASE_KEY  — service_role key (bypasses RLS)
use data::strategy::{
    paper::ClosedTrade,
    types::{StrategyMarketContext, StrategySignal, StrategyAction},
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

    /// Inserts a signal into shadow_signals. Fire-and-forget.
    pub fn write_signal(
        &self,
        signal: &StrategySignal,
        ctx: &StrategyMarketContext,
    ) {
        if signal.action == StrategyAction::Wait {
            return;
        }

        let body = build_signal_row(signal, ctx);
        let writer = self.clone();

        tokio::spawn(async move {
            writer.post("shadow_signals", &body).await;
        });
    }

    /// Inserts a closed trade into signal_outcomes. Fire-and-forget.
    pub fn write_trade(&self, trade: &ClosedTrade) {
        let body = build_trade_row(trade);
        let writer = self.clone();

        tokio::spawn(async move {
            writer.post("signal_outcomes", &body).await;
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

fn build_trade_row(trade: &ClosedTrade) -> Value {
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
    })
}
