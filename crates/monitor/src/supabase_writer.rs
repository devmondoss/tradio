use buyer_exhaustion;
/// Writes signals, closed trades, regime changes, and lab signals to Supabase via REST API.
///
/// Uses fire-and-forget tokio tasks — never blocks the bar-close path.
/// Failures are logged to stderr but do not crash the monitor.
///
/// Environment variables:
///   SUPABASE_URL  — https://[PROJECT].supabase.co
///   SUPABASE_KEY  — service_role key (bypasses RLS)
use data::strategy::micro_window::MicroWindowRow;
use data::strategy::{
    paper::ClosedTrade,
    playbook_reasoning::PlaybookReasoning,
    types::{StrategyAction, StrategyMarketContext, StrategySignal},
};
use serde_json::{Value, json};

/// Posición RBF persistida en Supabase, restaurada al reiniciar el proceso.
pub struct RestoredRbfPosition {
    pub signal_id: String,
    pub direction: data::strategy::detectors::range_breakout_flow::RbfDirection,
    pub entry_price: f64,
    pub stop_price: f64,
    pub target_price: f64,
    pub entry_ms: i64,
}

/// Posición AMD persistida en Supabase, restaurada al reiniciar el proceso.
pub struct RestoredAmdPosition {
    pub signal_id: String,
    pub direction: data::strategy::detectors::amd_detector::AmdDirection,
    pub entry_price: f64,
    pub stop_price: f64,
    pub target_price: f64,
    pub entry_ms: i64,
}

/// Posición BE persistida en Supabase, restaurada al reiniciar el proceso.
pub struct RestoredBePosition {
    pub signal_id: String,
    pub entry_price: f64,
    pub stop_price: f64,
    pub target_price: f64,
    pub entry_ms: i64,
}

/// Contexto extra que se pasa junto a una ScalpingSignal para persistencia.
pub struct ScalpingWriteCtx<'a> {
    pub session: &'a str,
    pub obi: f64,
    pub obi_ema_fast: f64,
    pub dz: f64,
    pub vr: f64,
    pub cvd: f64,
    pub spread_ticks: i32,
    pub atr: f64,
    pub regime: &'a str,
    pub liq_ratio: f64,
    pub range_high: Option<f64>,
    pub range_low: Option<f64>,
    pub range_mid: Option<f64>,
    pub range_location: Option<&'a str>,
}

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
        println!("[supabase] writer initialized for {url}");
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
        self.write_signal_inner(signal, ctx, None).await
    }

    pub async fn write_signal_with_reasoning(
        &self,
        signal: &StrategySignal,
        ctx: &StrategyMarketContext,
        reasoning: &PlaybookReasoning,
    ) -> Option<String> {
        self.write_signal_inner(signal, ctx, Some(reasoning)).await
    }

    async fn write_signal_inner(
        &self,
        signal: &StrategySignal,
        ctx: &StrategyMarketContext,
        reasoning: Option<&PlaybookReasoning>,
    ) -> Option<String> {
        if signal.action == StrategyAction::Wait {
            return None;
        }

        let body = build_signal_row(signal, ctx, reasoning);
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
            eprintln!(
                "[supabase] write_trade skipped — no signal_uuid (trade not linked to a signal)"
            );
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
        let url = format!("{}/rest/v1/signal_outcomes?id=eq.{uuid}", self.url);
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
                eprintln!(
                    "[supabase] PATCH signal_outcomes horizon {label} error {status}: {text}"
                );
            }
            Ok(_) => {}
        }
    }

    // ── Subdimi Parallel Observer ─────────────────────────────────────────────

    /// Writes a Subdimi parallel signal to lab_signals for comparison against DRR Core.
    /// Fire-and-forget — failures are logged but never crash the monitor.
    pub async fn write_parallel_signal(&self, signal: &StrategySignal) {
        if signal.action != StrategyAction::ShadowSignal {
            return;
        }
        let body = build_parallel_signal_row(signal);
        self.post("lab_signals", &body).await;
    }

    /// Inserts a MicroWindowRow into micro_windows. Fire-and-forget.
    /// Called once per bar close regardless of whether a signal fired.
    pub async fn write_micro_window(&self, row: &MicroWindowRow) {
        let body = json!({
            "exchange":        row.exchange,
            "symbol":          row.symbol,
            "timeframe":       row.timeframe,
            "candle_open_ms":  row.candle_open_ms,
            "candle_close_ms": row.candle_close_ms,
            "anchor":          row.anchor,
            "window_start_ms": row.window_start_ms,
            "window_end_ms":   row.window_end_ms,
            "bucket_count":    row.bucket_count,
            "bucket_secs":     row.bucket_secs,
            "in_drr_zone":     row.in_drr_zone,
            "range_location":  row.range_location,
            "range_high":      row.range_high,
            "range_low":       row.range_low,
            "range_mid":       row.range_mid,
            "atr":             row.atr,
            "win_vol_total":   row.win_vol_total,
            "win_delta_total": row.win_delta_total,
            "win_trades_total":row.win_trades_total,
            "win_cvd_net":     row.win_cvd_net,
            "win_liq_total":   row.win_liq_total,
            "win_big_vol":     row.win_big_vol,
            "win_max_trade":   row.win_max_trade,
            "aggressor_ratio": row.aggressor_ratio,
            // arrays flatteados a columnas individuales
            "delta_b0": row.delta_b[0], "delta_b1": row.delta_b[1],
            "delta_b2": row.delta_b[2], "delta_b3": row.delta_b[3],
            "delta_b4": row.delta_b[4],
            "vol_b0":   row.vol_b[0],   "vol_b1":   row.vol_b[1],
            "vol_b2":   row.vol_b[2],   "vol_b3":   row.vol_b[3],
            "vol_b4":   row.vol_b[4],
            "delta_slope":      row.delta_slope,
            "delta_slope_norm": row.delta_slope_norm,
            "delta_accel":      row.delta_accel,
            "delta_flip":       row.delta_flip,
            "delta_flip_bucket":row.delta_flip_bucket,
            "monotonic_delta":  row.monotonic_delta,
            "vol_peak_bucket":  row.vol_peak_bucket,
            "vol_trajectory":   row.vol_trajectory,
            "late_surge_ratio": row.late_surge_ratio,
            "price_net":        row.price_net,
            "price_path_eff":   row.price_path_eff,
            "micro_range_atr":  row.micro_range_atr,
            "absorption_proxy": row.absorption_proxy,
            "reclaimed":        row.reclaimed,
            "reclaim_bucket":   row.reclaim_bucket,
            "sweep_depth_atr":  row.sweep_depth_atr,
            "buckets":          row.buckets,
        });
        self.post("micro_windows", &body).await;
    }

    // ── Scalping ──────────────────────────────────────────────────────────────

    /// Inserta una señal de scalping en scalping_signals. Fire-and-forget.
    pub fn write_scalping_signal(
        &self,
        signal: &data::strategy::scalping::ScalpingSignal,
        ctx: &ScalpingWriteCtx,
    ) {
        let body = json!({
            "timestamp_ms":      signal.timestamp_ms,
            "strategy":          signal.strategy.to_string(),
            "side":              format!("{:?}", signal.side),
            "entry_type":        &signal.entry_type,
            "session":           ctx.session,
            "entry_price":       signal.entry_price,
            "stop_price":        signal.stop_price,
            "tp1_price":         signal.tp1_price,
            "tp2_price":         signal.tp2_price,
            "rr_planned":        signal.rr,
            "conviction_score":  signal.conviction_score,
            "obi_at_entry":      ctx.obi,
            "obi_ema_fast":      ctx.obi_ema_fast,
            "dz_at_entry":       ctx.dz,
            "vr_at_entry":       ctx.vr,
            "cvd_at_entry":      ctx.cvd,
            "spread_ticks":      ctx.spread_ticks,
            "atr":               ctx.atr,
            "regime":            ctx.regime,
            "liq_ratio":         ctx.liq_ratio,
            "range_high":        ctx.range_high,
            "range_low":         ctx.range_low,
            "range_mid":         ctx.range_mid,
            "range_location":    ctx.range_location,
            "evidence":          &signal.evidence,
        });
        let writer = self.clone();
        tokio::spawn(async move {
            writer.post("scalping_signals", &body).await;
        });
    }

    /// Escribe una señal RBF y retorna el UUID asignado por Supabase (para PATCH posterior).
    pub async fn write_rbf_signal_async(
        &self,
        sig: &data::strategy::detectors::range_breakout_flow::RbfSignal,
        symbol: &str,
    ) -> Option<String> {
        let body = self.rbf_signal_body(sig, symbol);
        let url = format!("{}/rest/v1/rbf_signals", self.url);
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
            Ok(r) if r.status().is_success() => {
                let rows: serde_json::Value = r.json().await.unwrap_or_default();
                rows.as_array()
                    .and_then(|a| a.first())
                    .and_then(|o| o.get("id"))
                    .and_then(|v| v.as_i64())
                    .map(|id| id.to_string())
            }
            Ok(r) => {
                eprintln!("[supabase] write_rbf_signal_async HTTP {}", r.status());
                None
            }
            Err(e) => {
                eprintln!("[supabase] write_rbf_signal_async error: {e}");
                None
            }
        }
    }

    /// Actualiza el outcome de una señal RBF existente (PATCH por id).
    pub fn update_rbf_outcome(
        &self,
        id: &str,
        trade: &data::strategy::detectors::rbf_paper::RbfClosedTrade,
    ) {
        // closed_at es timestamptz en Supabase — debe ser ISO 8601, no ms epoch
        let closed_at_iso = chrono::DateTime::from_timestamp_millis(trade.exit_ms)
            .map(|dt| dt.to_rfc3339())
            .unwrap_or_default();
        let body = serde_json::json!({
            "status":            trade.exit_reason.as_str(),
            "exit_price":        trade.exit_price,
            "result_r":          trade.result_r,
            "exit_reason":       trade.exit_reason.as_str(),
            "closed_at":         closed_at_iso,
            "is_active":         false,
            "bars_held":         trade.bars_held as i64,
            "sizing_multiplier": trade.sizing_multiplier,
        });
        let url = format!("{}/rest/v1/rbf_signals?id=eq.{}", self.url, id);
        let writer = self.clone();
        let body_c = body.clone();
        tokio::spawn(async move {
            let result = writer
                .client
                .patch(&url)
                .header("apikey", &writer.key)
                .header("Authorization", format!("Bearer {}", writer.key))
                .header("Content-Type", "application/json")
                .header("Prefer", "return=minimal")
                .json(&body_c)
                .send()
                .await;
            if let Ok(r) = result {
                if !r.status().is_success() {
                    eprintln!("[supabase] update_rbf_outcome HTTP {}", r.status());
                }
            }
        });
    }

    /// Marca una fila de rbf_signals como posición activa del paper trader.
    /// Llamar cuando se conoce el supabase_id y la posición está abierta.
    /// Permite restaurar el estado tras un reinicio (Railway redeploy).
    pub fn mark_rbf_active(&self, id: &str) {
        let body = serde_json::json!({ "is_active": true });
        let url = format!("{}/rest/v1/rbf_signals?id=eq.{}", self.url, id);
        let writer = self.clone();
        tokio::spawn(async move {
            let result = writer
                .client
                .patch(&url)
                .header("apikey", &writer.key)
                .header("Authorization", format!("Bearer {}", writer.key))
                .header("Content-Type", "application/json")
                .header("Prefer", "return=minimal")
                .json(&body)
                .send()
                .await;
            if let Ok(r) = result {
                if !r.status().is_success() {
                    eprintln!("[supabase] mark_rbf_active HTTP {}", r.status());
                }
            }
        });
    }

    /// Carga la posición activa del paper trader desde Supabase.
    /// Retorna None si no hay posición activa para este símbolo.
    pub async fn load_rbf_active(&self, symbol: &str) -> Option<RestoredRbfPosition> {
        let url = format!(
            "{}/rest/v1/rbf_signals?is_active=eq.true&result_r=is.null&symbol=eq.{}&order=id.desc&limit=1",
            self.url, symbol
        );
        let result = self
            .client
            .get(&url)
            .header("apikey", &self.key)
            .header("Authorization", format!("Bearer {}", self.key))
            .header("Accept", "application/json")
            .send()
            .await;

        let rows: serde_json::Value = match result {
            Ok(r) if r.status().is_success() => r.json().await.unwrap_or_default(),
            Ok(r) => {
                eprintln!("[supabase] load_rbf_active HTTP {}", r.status());
                return None;
            }
            Err(e) => {
                eprintln!("[supabase] load_rbf_active error: {e}");
                return None;
            }
        };

        let row = rows.as_array()?.first()?;
        let signal_id = row.get("id")?.as_i64()?.to_string();
        let dir_str = row.get("direction")?.as_str()?;
        let direction = serde_json::from_str::<
            data::strategy::detectors::range_breakout_flow::RbfDirection,
        >(&format!("\"{}\"", dir_str))
        .ok()?;
        let entry_price = row.get("entry_price")?.as_f64()?;
        let stop_price = row.get("stop_price")?.as_f64()?;
        let target_price = row.get("target_price")?.as_f64()?;
        let entry_ms = row.get("timestamp_ms")?.as_i64()?;

        Some(RestoredRbfPosition {
            signal_id,
            direction,
            entry_price,
            stop_price,
            target_price,
            entry_ms,
        })
    }

    /// Posición AMD persistida en Supabase, restaurada al reiniciar el proceso.
    pub fn amd_signal_body(
        &self,
        sig: &data::strategy::detectors::amd_detector::AmdSignal,
        symbol: &str,
    ) -> serde_json::Value {
        serde_json::json!({
            "symbol":               symbol,
            "timestamp_ms":         sig.timestamp_ms,
            "direction":            format!("{:?}", sig.direction),
            "entry_price":          sig.entry_price,
            "stop_price":           sig.stop_price,
            "target_price":         sig.target_price,
            "rr":                   sig.rr,
            "range_high":           sig.range_high,
            "range_low":            sig.range_low,
            "range_pct":            sig.range_pct,
            "range_bars":           sig.range_bars,
            "cvd_in_range":         sig.cvd_in_range,
            "spike_extreme":        sig.spike_extreme,
            "spike_direction":      format!("{:?}", sig.spike_direction),
            "vr_at_spike":          sig.vr_at_spike,
            "vpin_at_spike":        sig.vpin_at_spike,
            "bar_delta_at_spike":   sig.bar_delta_at_spike,
            "liq_ratio_at_spike":   sig.liq_ratio_at_spike,
            "dz_at_spike":          sig.dz_at_spike,
            "vr_at_entry":          sig.vr_at_entry,
            "cvd_slope_at_entry":   sig.cvd_slope_at_entry,
            "obi_at_entry":         sig.obi_at_entry,
            "target_source":        format!("{:?}", sig.target_source),
            "session_name":         &sig.session_name,
            "funding_at_entry":     sig.funding_at_entry,
            "quality_score":        sig.quality_score,
            "absorption_in_range":  sig.absorption_in_range,
            "absorption_at_spike":  sig.absorption_at_spike,
            "regime_is_trending":   sig.regime_is_trending,
            "session_cvd":          sig.session_cvd,
            "delta_dz_at_spike":    sig.delta_dz_at_spike,
            "delta_dz_at_entry":    sig.delta_dz_at_entry,
            "oi_delta_pct_at_spike":  sig.oi_delta_pct_at_spike,
            "oi_delta_pct_at_entry":  sig.oi_delta_pct_at_entry,
            "cvd_divergence_bars":    sig.cvd_divergence_bars,
            "is_kill_zone":           sig.is_kill_zone,
            "kill_zone_name":         &sig.kill_zone_name,
            "bars_to_entry":          sig.bars_to_entry as i64,
            "spike_extension_pct":    sig.spike_extension_pct,
            "range_spike_ratio":      sig.range_spike_ratio,
            "htf_h1_trend":           sig.htf_h1_trend.as_deref(),
            "htf_h1_aligned":         sig.htf_h1_aligned,
            "signal_score_v2":        sig.signal_score_v2,
            "sizing_multiplier":      sig.sizing_multiplier,
        })
    }

    /// Inserta una señal AMD y retorna el UUID asignado (para PATCH de outcome).
    /// `is_paper_trade=true` escribe is_active=true en el INSERT — elimina el race condition
    /// con Railway redeployments que causaba que mark_amd_active nunca llegara.
    pub async fn write_amd_signal_async(
        &self,
        sig: &data::strategy::detectors::amd_detector::AmdSignal,
        symbol: &str,
        is_paper_trade: bool,
    ) -> Option<String> {
        let mut body = self.amd_signal_body(sig, symbol);
        if is_paper_trade {
            body["is_active"] = serde_json::json!(true);
        }
        let url = format!("{}/rest/v1/amd_signals", self.url);
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
            Ok(r) if r.status().is_success() => {
                let rows: serde_json::Value = r.json().await.unwrap_or_default();
                rows.as_array()
                    .and_then(|a| a.first())
                    .and_then(|o| o.get("id"))
                    .and_then(|v| v.as_i64())
                    .map(|id| id.to_string())
            }
            Ok(r) => {
                eprintln!("[supabase] write_amd_signal_async HTTP {}", r.status());
                None
            }
            Err(e) => {
                eprintln!("[supabase] write_amd_signal_async error: {e}");
                None
            }
        }
    }

    /// Actualiza el outcome de una señal AMD (PATCH por id).
    pub fn update_amd_outcome(
        &self,
        id: &str,
        trade: &data::strategy::detectors::amd_paper::AmdClosedTrade,
    ) {
        let closed_at_iso = chrono::DateTime::from_timestamp_millis(trade.exit_ms)
            .map(|dt| dt.to_rfc3339())
            .unwrap_or_default();
        let body = serde_json::json!({
            "exit_price":   trade.exit_price,
            "result_r":     trade.result_r,
            "exit_reason":  trade.exit_reason.as_str(),
            "closed_at_ms": trade.exit_ms,
            "is_active":    false,
        });
        let _ = closed_at_iso; // closed_at_ms es bigint en amd_signals
        let url = format!("{}/rest/v1/amd_signals?id=eq.{}", self.url, id);
        let writer = self.clone();
        let body_c = body.clone();
        tokio::spawn(async move {
            let result = writer
                .client
                .patch(&url)
                .header("apikey", &writer.key)
                .header("Authorization", format!("Bearer {}", writer.key))
                .header("Content-Type", "application/json")
                .header("Prefer", "return=minimal")
                .json(&body_c)
                .send()
                .await;
            if let Ok(r) = result {
                if !r.status().is_success() {
                    eprintln!("[supabase] update_amd_outcome HTTP {}", r.status());
                }
            }
        });
    }

    /// Marca una señal AMD como posición activa del paper trader.
    pub fn mark_amd_active(&self, id: &str) {
        let body = serde_json::json!({ "is_active": true });
        let url = format!("{}/rest/v1/amd_signals?id=eq.{}", self.url, id);
        let writer = self.clone();
        tokio::spawn(async move {
            let result = writer
                .client
                .patch(&url)
                .header("apikey", &writer.key)
                .header("Authorization", format!("Bearer {}", writer.key))
                .header("Content-Type", "application/json")
                .header("Prefer", "return=minimal")
                .json(&body)
                .send()
                .await;
            if let Ok(r) = result {
                if !r.status().is_success() {
                    eprintln!("[supabase] mark_amd_active HTTP {}", r.status());
                }
            }
        });
    }

    /// Carga posición AMD activa desde Supabase al arrancar el proceso.
    pub async fn load_amd_active(&self, symbol: &str) -> Option<RestoredAmdPosition> {
        let url = format!(
            "{}/rest/v1/amd_signals?is_active=eq.true&symbol=eq.{}&order=id.desc&limit=1",
            self.url, symbol
        );
        let result = self
            .client
            .get(&url)
            .header("apikey", &self.key)
            .header("Authorization", format!("Bearer {}", self.key))
            .header("Accept", "application/json")
            .send()
            .await;
        let rows: serde_json::Value = match result {
            Ok(r) if r.status().is_success() => r.json().await.unwrap_or_default(),
            Ok(r) => {
                eprintln!("[supabase] load_amd_active HTTP {}", r.status());
                return None;
            }
            Err(e) => {
                eprintln!("[supabase] load_amd_active error: {e}");
                return None;
            }
        };
        let row = rows.as_array()?.first()?;
        let signal_id = row.get("id")?.as_i64()?.to_string();
        let dir_str = row.get("direction")?.as_str()?;
        let direction =
            serde_json::from_str::<data::strategy::detectors::amd_detector::AmdDirection>(
                &format!("\"{}\"", dir_str),
            )
            .ok()?;
        let entry_price = row.get("entry_price")?.as_f64()?;
        let stop_price = row.get("stop_price")?.as_f64()?;
        let target_price = row.get("target_price")?.as_f64()?;
        let entry_ms = row.get("timestamp_ms")?.as_i64()?;
        Some(RestoredAmdPosition {
            signal_id,
            direction,
            entry_price,
            stop_price,
            target_price,
            entry_ms,
        })
    }

    fn rbf_signal_body(
        &self,
        sig: &data::strategy::detectors::range_breakout_flow::RbfSignal,
        symbol: &str,
    ) -> serde_json::Value {
        serde_json::json!({
            "symbol":             symbol,
            "timestamp_ms":       sig.timestamp_ms,
            "direction":          format!("{:?}", sig.direction),
            "session":            format!("{:?}", sig.session),
            "entry_price":        sig.entry_price,
            "stop_price":         sig.stop_price,
            "target_price":       sig.target_price,
            "rr":                 sig.rr,
            "range_high":         sig.range_high,
            "range_low":          sig.range_low,
            "range_pct":          sig.range_pct,
            "range_bars":         sig.range_bars as i64,
            "cvd_in_range":       sig.cvd_in_range,
            "vr_at_breakout":     sig.vr_at_breakout,
            "macro_regime":       format!("{:?}", sig.macro_regime),
            "evidence":           &sig.evidence,
            "range_touch_count":  sig.range_touch_count as i64,
            "session_phase":      format!("{:?}", sig.session_phase),
            "price_vs_vwap_pct":  sig.price_vs_vwap_pct,
            "funding_at_entry":   sig.funding_at_entry,
            "liq_ratio_pre":      sig.liq_ratio_pre,
            "cvd_slope_at_entry": sig.cvd_slope_at_entry,
            "dz_at_entry":        sig.dz_at_entry,
            "obi_at_entry":       sig.obi_at_entry,
            "confluence_score":   sig.confluence_score as i64,
            "confluence_flags":   sig.confluence_flags,
            "veto_reason":        sig.veto_reason,
            "absorption_score":   sig.absorption_score,
            "bar_displacement":   sig.bar_displacement,
            "oi_delta_pct":              sig.oi_delta_pct,
            "cvd_divergence_bars":       sig.cvd_divergence_bars,
            "vr_tier":                   sig.vr_tier as i64,
            "range_touch_symmetry":      sig.range_touch_symmetry,
            "cvd_per_bar":               sig.cvd_per_bar,
            "breakout_extension_pct":    sig.breakout_extension_pct,
            "htf_h1_trend":              sig.htf_h1_trend.as_deref(),
            "htf_h1_aligned":            sig.htf_h1_aligned,
            "vp_open_bias":              sig.vp_open_bias.as_deref(),
            "signal_score_v2":           sig.signal_score_v2,
            "sizing_multiplier":         sig.sizing_multiplier,
            "is_pre_breakout":           sig.is_pre_breakout,
            "is_sweep_reclaim":          sig.is_sweep_reclaim,
        })
    }

    /// Escribe una señal RBF a Supabase. Fire-and-forget.
    pub fn write_rbf_signal(
        &self,
        sig: &data::strategy::detectors::range_breakout_flow::RbfSignal,
        symbol: &str,
    ) {
        let body = self.rbf_signal_body(sig, symbol);
        let writer = self.clone();
        tokio::spawn(async move {
            writer.post("rbf_signals", &body).await;
        });
    }

    /// Escribe una barra M1 con contexto de microestructura para backtest futuro de RBF v2.
    #[allow(clippy::too_many_arguments)]
    pub fn write_rbf_bar(
        &self,
        symbol: &str,
        ts_ms: i64,
        session: &str,
        open: f64,
        high: f64,
        low: f64,
        close: f64,
        volume: f64,
        bar_delta: f64,
        cvd_slope: Option<f64>,
        obi_l5: f64,
        obi_l10: f64,
        obi_l20: f64,
        obi_fast: f64,
        obi_slow: f64,
        dz: f64,
        vr: f64,
        liq_ratio: f64,
        spread_ticks: i32,
        stacked_imb: &str,
        absorption: &str,
        thin_above: bool,
        thin_below: bool,
        bid_wall: bool,
        ask_wall: bool,
        vpin: Option<f64>,
        oi_momentum: Option<bool>,
        vwap: Option<f64>,
        regime: &str,
        atr: f64,
        operative: bool,
        // ICT AMD structural levels
        asian_high: Option<f64>,
        asian_low: Option<f64>,
        prev_day_high: Option<f64>,
        prev_day_low: Option<f64>,
        swing_high_50: Option<f64>,
        swing_low_50: Option<f64>,
        equal_high: bool,
        equal_low: bool,
        // microestructura adicional
        cvd_divergence: Option<&str>,
        sweep_confirmed: bool,
        // Volume Profile levels
        vp_poc: Option<f64>,
        vp_vah: Option<f64>,
        vp_val: Option<f64>,
        vp_lvn_below: Option<f64>,
    ) {
        let body = json!({
            "symbol":          symbol,
            "ts_ms":           ts_ms,
            "session":         session,
            "open":            open,
            "high":            high,
            "low":             low,
            "close":           close,
            "volume":          volume,
            "bar_delta":       bar_delta,
            "cvd_slope":       cvd_slope,
            "obi_l5":          obi_l5,
            "obi_l10":         obi_l10,
            "obi_l20":         obi_l20,
            "obi_fast":        obi_fast,
            "obi_slow":        obi_slow,
            "dz":              dz,
            "vr":              vr,
            "liq_ratio":       liq_ratio,
            "spread_ticks":    spread_ticks,
            "stacked_imb":     stacked_imb,
            "absorption":      absorption,
            "thin_above":      thin_above,
            "thin_below":      thin_below,
            "bid_wall":        bid_wall,
            "ask_wall":        ask_wall,
            "vpin":            vpin,
            "oi_momentum":     oi_momentum,
            "vwap":            vwap,
            "regime":          regime,
            "atr":             atr,
            "operative":       operative,
            "asian_high":      asian_high,
            "asian_low":       asian_low,
            "prev_day_high":   prev_day_high,
            "prev_day_low":    prev_day_low,
            "swing_high_50":   swing_high_50,
            "swing_low_50":    swing_low_50,
            "equal_high":      equal_high,
            "equal_low":       equal_low,
            "cvd_divergence":  cvd_divergence,
            "sweep_confirmed": sweep_confirmed,
            "vp_poc":          vp_poc,
            "vp_vah":          vp_vah,
            "vp_val":          vp_val,
            "vp_lvn_below":    vp_lvn_below,
        });
        let table = match symbol {
            "ETHUSDT" => "eth_bars",
            "BNBUSDT" => "bnb_bars",
            "SOLUSDT" => "sol_bars",
            "XRPUSDT" => "xrp_bars",
            _ => "btc_bars",
        }
        .to_string();
        let writer = self.clone();
        tokio::spawn(async move {
            writer.post(&table, &body).await;
        });
    }

    /// Escribe un lote de muestras OBI intrabar (sampleo 10s) a la tabla obi_10s.
    /// Usa upsert para evitar duplicados si el monitor reinicia dentro de la misma barra.
    pub fn write_obi_batch(&self, symbol: &str, samples: &[(i64, f32, f32, f32, f32)]) {
        if samples.is_empty() {
            return;
        }
        let rows: Vec<serde_json::Value> = samples
            .iter()
            .map(|&(ts, l5, l10, l20, sp)| {
                json!({
                    "ts_ms":      ts,
                    "symbol":     symbol,
                    "obi_l5":     l5,
                    "obi_l10":    l10,
                    "obi_l20":    l20,
                    "spread_bps": sp,
                })
            })
            .collect();
        let body = serde_json::Value::Array(rows);
        let url = format!("{}/rest/v1/obi_10s", self.url);
        let writer = self.clone();
        tokio::spawn(async move {
            let result = writer
                .client
                .post(&url)
                .header("apikey", &writer.key)
                .header("Authorization", format!("Bearer {}", writer.key))
                .header("Content-Type", "application/json")
                .header("Prefer", "resolution=ignore-duplicates")
                .json(&body)
                .send()
                .await;
            if let Ok(r) = result {
                if !r.status().is_success() {
                    eprintln!(
                        "[supabase] write_obi_batch {} HTTP {}",
                        writer.url.len(),
                        r.status()
                    );
                }
            }
        });
    }

    /// Inserta en scalping_trades y hace PATCH a scalping_signals para cerrar el outcome.
    /// Fire-and-forget.
    pub fn write_scalping_trade(&self, trade: &data::strategy::scalping::paper::ScalpingTrade) {
        let body_trade = json!({
            "trade_id":         &trade.trade_id,
            "strategy":         &trade.strategy,
            "side":             format!("{:?}", trade.side),
            "entry_type":       &trade.entry_type,
            "entry_price":      trade.entry_price,
            "stop_price":       trade.stop_price,
            "tp1_price":        trade.tp1_price,
            "tp2_price":        trade.tp2_price,
            "exit_price":       trade.exit_price,
            "rr_planned":       trade.rr_planned,
            "obi_at_entry":     trade.obi_at_entry,
            "dz_at_entry":      trade.dz_at_entry,
            "vr_at_entry":      trade.vr_at_entry,
            "cvd_at_entry":     trade.cvd_at_entry,
            "conviction_score": trade.conviction_score,
            "spread_ticks":     trade.spread_ticks,
            "exit_reason":      trade.exit_reason.to_string(),
            "pnl_gross":        trade.pnl_gross,
            "pnl_net":          trade.pnl_net,
            "result_r":         trade.result_r,
            "duration_ms":      trade.duration_ms,
            "entry_ms":         trade.entry_ms,
            "exit_ms":          trade.exit_ms,
            "mfe":              trade.mfe,
            "mae":              trade.mae,
        });
        let patch_signal = json!({
            "status":      "CLOSED",
            "exit_price":  trade.exit_price,
            "exit_reason": trade.exit_reason.to_string(),
            "pnl_net":     trade.pnl_net,
            "result_r":    trade.result_r,
            "duration_ms": trade.duration_ms,
            "closed_at":   trade.exit_ms,
        });
        let entry_ms = trade.entry_ms;
        let writer = self.clone();
        tokio::spawn(async move {
            writer.post("scalping_trades", &body_trade).await;
            // Cerrar la señal correspondiente en scalping_signals (match por timestamp_ms)
            let url = format!(
                "{}/rest/v1/scalping_signals?timestamp_ms=eq.{}",
                writer.url, entry_ms
            );
            let result = writer
                .client
                .patch(&url)
                .header("apikey", &writer.key)
                .header("Authorization", format!("Bearer {}", writer.key))
                .header("Content-Type", "application/json")
                .header("Prefer", "return=minimal")
                .json(&patch_signal)
                .send()
                .await;
            if let Ok(r) = result {
                if !r.status().is_success() {
                    eprintln!("[supabase] PATCH scalping_signals HTTP {}", r.status());
                }
            }
        });
    }

    /// Inserta una fila en scalping_bars — una fila por barra M1, siempre.
    #[allow(clippy::too_many_arguments)]
    pub fn write_scalping_bar(
        &self,
        ts_ms: i64,
        session: &str,
        regime: &str,
        open: f64,
        high: f64,
        low: f64,
        close: f64,
        atr: f64,
        funding_pct: Option<f64>,
        obi_fast: f64,
        obi_slow: f64,
        obi_l5: f64,
        cvd_session: f64,
        cvd_slope: Option<f64>,
        dz: f64,
        vr: f64,
        spread_ticks: i32,
        absorption_long: f64,
        absorption_short: f64,
        liq_ratio: f64,
        signal_fired: Option<&str>,
        signal_score: Option<f64>,
        blocked_by: Option<&str>,
    ) {
        let body = json!({
            "ts_ms":           ts_ms,
            "symbol":          "BTCUSDT",
            "session":         session,
            "regime":          regime,
            "open":            open,
            "high":            high,
            "low":             low,
            "close":           close,
            "atr":             atr,
            "funding_pct":     funding_pct,
            "obi_fast":        obi_fast,
            "obi_slow":        obi_slow,
            "obi_l5":          obi_l5,
            "l5_l10_div":      obi_l5 - obi_fast,
            "cvd_session":     cvd_session,
            "cvd_slope":       cvd_slope,
            "dz":              dz,
            "vr":              vr,
            "spread_ticks":    spread_ticks,
            "absorption_long": absorption_long,
            "absorption_short":absorption_short,
            "liq_ratio":       liq_ratio,
            "signal_fired":    signal_fired,
            "signal_score":    signal_score,
            "blocked_by":      blocked_by,
        });
        let writer = self.clone();
        tokio::spawn(async move {
            writer.post("scalping_bars", &body).await;
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

    // ── Buyer Exhaustion ─────────────────────────────────────────────────────

    /// Inserta una señal BE y retorna el UUID asignado por Supabase.
    pub async fn write_be_signal_async(
        &self,
        sig: &buyer_exhaustion::signal::BuyerExhaustionSignal,
        symbol: &str,
    ) -> Option<String> {
        let body = self.be_signal_body(sig, symbol);
        let url = format!("{}/rest/v1/be_signals", self.url);
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
            Ok(r) if r.status().is_success() => {
                let rows: serde_json::Value = r.json().await.unwrap_or_default();
                rows.as_array()
                    .and_then(|a| a.first())
                    .and_then(|o| o.get("id"))
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string())
            }
            Ok(r) => {
                eprintln!("[supabase] write_be_signal_async HTTP {}", r.status());
                None
            }
            Err(e) => {
                eprintln!("[supabase] write_be_signal_async error: {e}");
                None
            }
        }
    }

    /// Actualiza el outcome de una señal BE (PATCH por UUID).
    pub fn update_be_outcome(&self, id: &str, trade: &buyer_exhaustion::signal::BeClosedTrade) {
        let closed_at_iso = chrono::DateTime::from_timestamp_millis(trade.exit_ms)
            .map(|dt| dt.to_rfc3339())
            .unwrap_or_default();
        let body = serde_json::json!({
            "active":       false,
            "closed_at":    closed_at_iso,
            "exit_price":   trade.exit_price,
            "exit_reason":  trade.exit_reason.as_str(),
            "result_r":     trade.result_r,
            "bars_held":    trade.bars_held as i64,
        });
        let url = format!("{}/rest/v1/be_signals?id=eq.{}", self.url, id);
        let writer = self.clone();
        tokio::spawn(async move {
            let result = writer
                .client
                .patch(&url)
                .header("apikey", &writer.key)
                .header("Authorization", format!("Bearer {}", writer.key))
                .header("Content-Type", "application/json")
                .header("Prefer", "return=minimal")
                .json(&body)
                .send()
                .await;
            if let Ok(r) = result {
                if !r.status().is_success() {
                    eprintln!("[supabase] update_be_outcome HTTP {}", r.status());
                }
            }
        });
    }

    /// Carga la posición BE activa desde Supabase al arrancar.
    pub async fn load_be_active(&self, symbol: &str) -> Option<RestoredBePosition> {
        let url = format!(
            "{}/rest/v1/be_signals?active=eq.true&symbol=eq.{}&order=timestamp_ms.desc&limit=1",
            self.url, symbol
        );
        let result = self
            .client
            .get(&url)
            .header("apikey", &self.key)
            .header("Authorization", format!("Bearer {}", self.key))
            .header("Accept", "application/json")
            .send()
            .await;
        let rows: serde_json::Value = match result {
            Ok(r) if r.status().is_success() => r.json().await.unwrap_or_default(),
            Ok(r) => {
                eprintln!("[supabase] load_be_active HTTP {}", r.status());
                return None;
            }
            Err(e) => {
                eprintln!("[supabase] load_be_active error: {e}");
                return None;
            }
        };
        let row = rows.as_array()?.first()?;
        let signal_id = row.get("id")?.as_str()?.to_string();
        let entry_price = row.get("entry_price")?.as_f64()?;
        let stop_price = row.get("stop_price")?.as_f64()?;
        let target_price = row.get("target_price")?.as_f64()?;
        let entry_ms = row.get("timestamp_ms")?.as_i64()?;
        Some(RestoredBePosition {
            signal_id,
            entry_price,
            stop_price,
            target_price,
            entry_ms,
        })
    }

    fn be_signal_body(
        &self,
        sig: &buyer_exhaustion::signal::BuyerExhaustionSignal,
        symbol: &str,
    ) -> serde_json::Value {
        serde_json::json!({
            "symbol":          symbol,
            "session":         format!("{:?}", sig.session),
            "timestamp_ms":    sig.timestamp_ms,
            "entry_price":     sig.entry_price,
            "stop_price":      sig.stop_price,
            "target_price":    sig.target_price,
            "rr":              sig.rr,
            "range_high":      sig.range_high,
            "range_low":       sig.range_low,
            "range_pct":       sig.range_pct,
            "range_bars":      sig.range_bars as i64,
            "range_cvd":       sig.range_cvd,
            "pre_cvd_flip":    sig.pre_cvd_flip,
            "cvd_flip_ratio":  sig.cvd_flip_ratio,
            "vr_at_breakout":  sig.vr_at_breakout,
            "breakout_delta":  sig.breakout_delta,
            "active":          true,
        })
    }
}

fn build_signal_row(
    signal: &StrategySignal,
    ctx: &StrategyMarketContext,
    reasoning: Option<&PlaybookReasoning>,
) -> Value {
    let inst = ctx.institutional.as_ref();

    let evidence: Vec<String> = signal.evidence.iter().map(|e| format!("{e:?}")).collect();
    let missing: Vec<String> = signal.missing.iter().map(|m| format!("{m:?}")).collect();

    let regime_str = format!("{:?}", ctx.regime);

    // Split HVNs by current price for directional analysis in calibration
    let price = ctx.price;
    let hvn_above: Vec<f64> = ctx
        .volume_profile
        .hvn_nearby
        .iter()
        .copied()
        .filter(|&h| h > price)
        .collect();
    let hvn_below: Vec<f64> = ctx
        .volume_profile
        .hvn_nearby
        .iter()
        .copied()
        .filter(|&h| h < price)
        .collect();
    let nearest_wall_above = ctx.orderbook.walls_above.first().copied();
    let nearest_wall_below = ctx.orderbook.walls_below.first().copied();

    // Subdimi fields — aligned with signal direction
    let is_long = signal
        .side
        .map(|s| matches!(s, data::strategy::types::Side::Long))
        .unwrap_or(false);
    let finish_action = if is_long {
        ctx.flow.finish_action_bullish
    } else {
        ctx.flow.finish_action_bearish
    };
    let unfinish_action = if is_long {
        ctx.flow.unfinish_action_bearish
    } else {
        ctx.flow.unfinish_action_bullish
    };
    let big_trade = if is_long {
        ctx.flow.big_trade_bullish
    } else {
        ctx.flow.big_trade_bearish
    };

    let htf_weekly_location = ctx
        .htf_vp
        .as_ref()
        .and_then(|h| h.weekly.as_ref())
        .map(|w| format!("{:?}", w.location));
    let htf_monthly_location = ctx
        .htf_vp
        .as_ref()
        .and_then(|h| h.monthly.as_ref())
        .map(|w| format!("{:?}", w.location));
    let reasoning_tags = reasoning.map(|r| {
        json!({
            "market_state": &r.market_state,
            "location_tags": &r.location_tags,
            "flow_tags": &r.flow_tags,
            "liquidity_tags": &r.liquidity_tags,
            "book_tags": &r.book_tags,
            "institutional_tags": &r.institutional_tags,
            "structure_tags": &r.structure_tags,
            "trigger_tags": &r.trigger_tags,
            "risk_tags": &r.risk_tags,
            "confirmation_tags": &r.confirmation_tags,
            "contradiction_tags": &r.contradiction_tags,
            "missing_tags": &r.missing_tags,
            "detector_role_tags": &r.detector_role_tags,
        })
    });

    // Subdomi JSONB — contextual fields not worth individual columns
    // ── Bloque 1: Tiempo y sesión ─────────────────────────────────────────────
    let hour_utc = ((ctx.timestamp_ms / 1000) % 86_400 / 3600) as i16;
    let minute_utc = (((ctx.timestamp_ms / 1000) % 86_400) % 3600 / 60) as i32;
    let day_of_week = ((ctx.timestamp_ms / 86_400_000 + 3) % 7 + 1) as i16; // 1=Mon 7=Sun
    let session_name = ctx.session.as_ref().map(|s| format!("{:?}", s.session));
    let session_phase = ctx.session.as_ref().map(|s| format!("{:?}", s.phase));
    let minutes_since_session_open: Option<i16> = ctx.session.as_ref().map(|s| {
        use data::session::TradingSession;
        let start = match s.session {
            TradingSession::Asia => 0 * 60,
            TradingSession::London => 7 * 60,
            TradingSession::LondonNyOverlap => 12 * 60,
            TradingSession::NewYork => 13 * 60 + 30,
            TradingSession::OffHours => 0,
        };
        let current = hour_utc as i32 * 60 + minute_utc;
        (current - start).max(0) as i16
    });

    // ── Bloque 2: Calidad del rango ────────────────────────────────────────────
    let range_midline_slope = ctx.range.as_ref().map(|r| r.midline_slope);
    let range_bars_inside = ctx.range.as_ref().map(|r| r.bars_inside as i32);
    let range_second_test = {
        let is_long = signal
            .side
            .map(|s| matches!(s, data::strategy::types::Side::Long))
            .unwrap_or(false);
        ctx.range.as_ref().map(|r| {
            if is_long {
                r.touches_low >= 2
            } else {
                r.touches_high >= 2
            }
        })
    };
    let range_vs_value_area: Option<&str> = match (
        ctx.volume_profile.vah,
        ctx.volume_profile.val,
        ctx.range.as_ref(),
    ) {
        (Some(vah), Some(val), Some(r)) => {
            if r.range_low >= val && r.range_high <= vah {
                Some("inside_va")
            } else if r.range_low >= vah {
                Some("above_va")
            } else if r.range_high <= val {
                Some("below_va")
            } else {
                Some("spanning_va")
            }
        }
        _ => None,
    };

    // ── Bloque 3: Calidad de absorción ────────────────────────────────────────
    let is_long_signal = signal
        .side
        .map(|s| matches!(s, data::strategy::types::Side::Long))
        .unwrap_or(false);
    let absorption_count: i16 = if is_long_signal {
        [
            ctx.flow.footprint_absorption == data::strategy::types::AbsorptionSide::Bid,
            ctx.flow.big_trade_bearish,
            matches!(
                ctx.flow.cvd_divergence,
                Some(data::strategy::types::CvdDivergence::BullishAbsorption)
            ),
            ctx.flow.finish_action_bullish,
            ctx.range
                .as_ref()
                .map(|r| r.sweep_range_low)
                .unwrap_or(false),
        ]
        .iter()
        .filter(|&&b| b)
        .count() as i16
    } else {
        [
            ctx.flow.footprint_absorption == data::strategy::types::AbsorptionSide::Ask,
            ctx.flow.big_trade_bullish,
            matches!(
                ctx.flow.cvd_divergence,
                Some(data::strategy::types::CvdDivergence::BearishAbsorption)
            ),
            ctx.flow.finish_action_bearish,
            ctx.range
                .as_ref()
                .map(|r| r.sweep_range_high)
                .unwrap_or(false),
        ]
        .iter()
        .filter(|&&b| b)
        .count() as i16
    };
    let entry_type: Option<&str> = ctx.range.as_ref().map(|r| {
        if is_long_signal {
            if r.sweep_range_low {
                "sweep_reclaim"
            } else {
                "near_extreme"
            }
        } else {
            if r.sweep_range_high {
                "sweep_reclaim"
            } else {
                "near_extreme"
            }
        }
    });
    let sweep_depth_atr = ctx.range.as_ref().and_then(|r| {
        let depth = if is_long_signal {
            r.sweep_low_depth
        } else {
            r.sweep_high_depth
        };
        depth.zip(ctx.atr).map(|(d, a)| d / a)
    });
    let delta_at_extreme = ctx.flow.delta;
    let bar_volume = ctx
        .flow
        .buy_volume
        .zip(ctx.flow.sell_volume)
        .map(|(b, s)| b + s);

    // ── Bloque 4: Contexto de precio y estructura ─────────────────────────────
    let value_location = Some(format!("{:?}", ctx.volume_profile.value_location));
    let price_vs_vwap = Some(format!("{:?}", ctx.vwap.price_vs_vwap));
    let price_vs_avwap_bos = Some(format!("{:?}", ctx.vwap.price_vs_avwap_bos));
    let naked_poc_in_target_path = signal.entry_price.zip(signal.target_price).map(|(e, t)| {
        let (lo, hi) = if t > e { (e, t) } else { (t, e) };
        ctx.volume_profile
            .naked_pocs
            .iter()
            .any(|&p| p > lo && p < hi)
    });
    let hvn_between_entry_target = signal.entry_price.zip(signal.target_price).map(|(e, t)| {
        let (lo, hi) = if t > e { (e, t) } else { (t, e) };
        ctx.volume_profile
            .hvn_nearby
            .iter()
            .any(|&h| h > lo && h < hi)
    });
    let fast_slope_at_entry = ctx.flow.fast_slope;

    // ── Bloque 5: Institucional compacto ─────────────────────────────────────
    let oi_direction = ctx
        .flow
        .oi_momentum_aligned
        .map(|aligned| if aligned { "aligned" } else { "opposed" });
    let cvd_div_persist = ctx.flow.cvd_divergence_persistence;
    let vpin_val = ctx.flow.vpin;
    let funding_velocity_val = inst.map(|i| i.funding.velocity);

    // ── Bloque 6: Calidad del trade ───────────────────────────────────────────
    let rr_actual = signal
        .entry_price
        .zip(signal.stop_price)
        .zip(signal.target_price)
        .map(|((e, s), t)| {
            let risk = (e - s).abs();
            let rew = (t - e).abs();
            if risk > 1e-10 { rew / risk } else { 0.0 }
        });
    let distance_to_target_atr = signal
        .entry_price
        .zip(signal.target_price)
        .zip(ctx.atr)
        .map(|((e, t), a)| (t - e).abs() / a);
    let distance_to_stop_atr = signal
        .entry_price
        .zip(signal.stop_price)
        .zip(ctx.atr)
        .map(|((e, s), a)| (e - s).abs() / a);
    let obstacle_hvn_count: Option<i16> =
        signal.entry_price.zip(signal.target_price).map(|(e, t)| {
            let (lo, hi) = if t > e { (e, t) } else { (t, e) };
            ctx.volume_profile
                .hvn_nearby
                .iter()
                .filter(|&&h| h > lo && h < hi)
                .count() as i16
        });
    let nearest_naked_poc_dist_atr = ctx.atr.and_then(|a| {
        ctx.volume_profile
            .naked_pocs
            .iter()
            .map(|&p| (p - ctx.price).abs() / a)
            .min_by(|x, y| x.partial_cmp(y).unwrap_or(std::cmp::Ordering::Equal))
    });

    let subdomi_ctx = json!({
        "naked_poc_count":            ctx.volume_profile.naked_pocs.len(),
        "single_print_count":         ctx.volume_profile.single_prints.len(),
        "vpin_cdf":                   ctx.flow.vpin_cdf,
        "oi_delta_zscore":            ctx.flow.oi_delta_zscore,
        "cvd_divergence_persistence": ctx.flow.cvd_divergence_persistence,
        "htf_weekly_poc":             ctx.htf_vp.as_ref().and_then(|h| h.weekly.as_ref()).map(|w| w.poc),
        "htf_monthly_poc":            ctx.htf_vp.as_ref().and_then(|h| h.monthly.as_ref()).map(|w| w.poc),
        "finish_action_bullish":      ctx.flow.finish_action_bullish,
        "finish_action_bearish":      ctx.flow.finish_action_bearish,
        "unfinish_action_bullish":    ctx.flow.unfinish_action_bullish,
        "unfinish_action_bearish":    ctx.flow.unfinish_action_bearish,
        "playbook_reasoning":         reasoning,
        "primary_playbook":           reasoning.map(|r| r.primary_playbook.as_str()),
        "reasoning_confidence":       reasoning.map(|r| r.confidence),
        "reasoning_completeness":     reasoning.map(|r| r.completeness),
    });

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

        // Subdimi methodology — columnas individuales (filtrado estadístico)
        "finish_action":          finish_action,
        "unfinish_action":        unfinish_action,
        "big_trade":              big_trade,
        "stacked_imbalance":      format!("{:?}", ctx.flow.stacked_imbalance),
        "vp_open_bias":           ctx.vp_open_bias.as_ref().map(|v| format!("{:?}", v.bias)),
        "auction_state":          ctx.auction_state.as_ref().map(|a| format!("{:?}", a.state)),
        "htf_weekly_location":    htf_weekly_location,
        "htf_monthly_location":   htf_monthly_location,
        "delta_velocity":         ctx.flow.delta_velocity,

        // Playbook reasoning Fase 1 — observador, no gate
        "reasoning_version":       reasoning.map(|r| r.version.as_str()),
        "primary_playbook":        reasoning.map(|r| r.primary_playbook.as_str()),
        "secondary_playbooks":     reasoning.map(|r| &r.secondary_playbooks),
        "reasoning_tags":          reasoning_tags,
        "reasoning_confidence":    reasoning.map(|r| r.confidence),
        "reasoning_completeness":  reasoning.map(|r| r.completeness),

        // Subdimi contexto blob
        // DRR — contexto del rango intradío
        "range_high":         ctx.range.as_ref().map(|r| r.range_high),
        "range_low":          ctx.range.as_ref().map(|r| r.range_low),
        "range_mid":          ctx.range.as_ref().map(|r| r.range_mid),
        "range_poc":          ctx.range.as_ref().and_then(|r| r.range_poc),
        "range_size_atr":     ctx.range.as_ref().map(|r| r.range_size_atr),
        "range_location":     ctx.range.as_ref().map(|r| format!("{:?}", r.location)),
        "range_touches_high": ctx.range.as_ref().map(|r| r.touches_high),
        "range_touches_low":  ctx.range.as_ref().map(|r| r.touches_low),
        "range_sweep_low":    ctx.range.as_ref().map(|r| r.sweep_range_low),
        "range_sweep_high":   ctx.range.as_ref().map(|r| r.sweep_range_high),

        // Bloque 1: Tiempo y sesión
        "session_name":               session_name,
        "session_phase":              session_phase,
        "hour_utc":                   hour_utc,
        "day_of_week":                day_of_week,
        "minutes_since_session_open": minutes_since_session_open,

        // Bloque 2: Calidad del rango
        "range_midline_slope": range_midline_slope,
        "range_bars_inside":   range_bars_inside,
        "range_second_test":   range_second_test,
        "range_vs_value_area": range_vs_value_area,

        // Bloque 3: Absorción / trigger
        "absorption_count": absorption_count,
        "entry_type":        entry_type,
        "sweep_depth_atr":   sweep_depth_atr,
        "delta_at_extreme":  delta_at_extreme,
        "bar_volume":        bar_volume,

        // Bloque 4: Contexto de precio y estructura
        "value_location":            value_location,
        "price_vs_vwap":             price_vs_vwap,
        "price_vs_avwap_bos":        price_vs_avwap_bos,
        "naked_poc_in_target_path":  naked_poc_in_target_path,
        "hvn_between_entry_target":  hvn_between_entry_target,
        "fast_slope_at_entry":       fast_slope_at_entry,

        // Bloque 5: Institucional compacto
        "oi_direction":               oi_direction,
        "cvd_divergence_persistence": cvd_div_persist,
        "vpin":                       vpin_val,
        "funding_velocity":           funding_velocity_val,

        // Bloque 6: Calidad del trade
        "rr_actual":                  rr_actual,
        "distance_to_target_atr":     distance_to_target_atr,
        "distance_to_stop_atr":       distance_to_stop_atr,
        "obstacle_hvn_count":         obstacle_hvn_count,
        "nearest_naked_poc_dist_atr": nearest_naked_poc_dist_atr,


        // Subdomi contexto blob
        "subdomi_ctx":            subdomi_ctx,
    })
}

fn build_parallel_signal_row(signal: &StrategySignal) -> Value {
    let rr = signal
        .entry_price
        .zip(signal.stop_price)
        .zip(signal.target_price)
        .map(|((e, s), t)| {
            let risk = (e - s).abs();
            let rew = (t - e).abs();
            if risk > 1e-10 { rew / risk } else { 0.0 }
        });
    json!({
        "strategy_id":  signal.strategy_id.map(|id| format!("{id:?}")),
        "status":       "ShadowSignal",
        "maturity":     "SubdimiParallel",
        "timestamp_ms": signal.created_at_ms,
        "action":       format!("{:?}", signal.action),
        "entry_price":  signal.entry_price,
        "target":       signal.target_price,
        "stop":         signal.stop_price,
        "rr":           rr,
        "confidence":   signal.score,
        "missing_data": &signal.missing,
        "snapshot":     serde_json::to_value(&signal.evidence).unwrap_or(Value::Null),
    })
}

fn build_trade_row(trade: &ClosedTrade, signal_uuid: &str) -> Value {
    let duration_ms = trade.closed_at_ms - trade.opened_at_ms;
    let risk = (trade.entry_price - trade.stop_price.unwrap_or(trade.entry_price)).abs();
    let r_multiple = if risk > 0.0 {
        (trade.exit_price - trade.entry_price) * if trade.side == "Long" { 1.0 } else { -1.0 }
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

impl SupabaseWriter {
    /// Escribe un evento HTF (apertura o cierre de trade) en la tabla `htf_trades`.
    /// Llamada async fire-and-forget desde on_bar_close.
    pub async fn write_htf_trade(
        &self,
        event: &data::strategy::detectors::htf_shorts_detector::HtfTrade,
        symbol: &str,
    ) {
        use chrono::DateTime;
        let sig = &event.signal;
        let entry_at = DateTime::from_timestamp_millis(sig.ts_ms)
            .map(|dt| dt.to_rfc3339())
            .unwrap_or_default();
        let closed_at = event.exit_ts_ms
            .and_then(|ms| DateTime::from_timestamp_millis(ms))
            .map(|dt| dt.to_rfc3339());

        let body = serde_json::json!({
            "symbol":        symbol,
            "sig":           sig.sig,
            "session":       sig.session,
            "d1_trend":      sig.d1_trend,
            "entry":         sig.entry,
            "stop":          sig.stop,
            "target":        sig.target,
            "stop_pct":      sig.stop_pct,
            "is_open":       event.is_open,
            "result_r":      event.result_r,
            "gross_r":       event.gross_r,
            "fee_r":         event.fee_r,
            "reason":        event.reason,
            "exit_price":    event.exit_price,
            "duration_bars": event.duration_bars,
            "entry_at":      entry_at,
            "closed_at":     closed_at,
        });

        let url = format!("{}/rest/v1/htf_trades", self.url);
        let result = self.client
            .post(&url)
            .header("apikey", &self.key)
            .header("Authorization", format!("Bearer {}", self.key))
            .header("Content-Type", "application/json")
            .json(&body)
            .send()
            .await;
        if let Err(e) = result {
            eprintln!("[supabase] write_htf_trade error: {e}");
        }
    }
}
