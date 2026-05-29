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

fn build_signal_row(
    signal: &StrategySignal,
    ctx: &StrategyMarketContext,
    reasoning: Option<&PlaybookReasoning>,
) -> Value {
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

    // Subdimi fields — aligned with signal direction
    let is_long = signal.side.map(|s| matches!(s, data::strategy::types::Side::Long)).unwrap_or(false);
    let finish_action   = if is_long { ctx.flow.finish_action_bullish  } else { ctx.flow.finish_action_bearish  };
    let unfinish_action = if is_long { ctx.flow.unfinish_action_bearish } else { ctx.flow.unfinish_action_bullish };
    let big_trade       = if is_long { ctx.flow.big_trade_bullish       } else { ctx.flow.big_trade_bearish      };

    let htf_weekly_location  = ctx.htf_vp.as_ref().and_then(|h| h.weekly.as_ref())
        .map(|w| format!("{:?}", w.location));
    let htf_monthly_location = ctx.htf_vp.as_ref().and_then(|h| h.monthly.as_ref())
        .map(|w| format!("{:?}", w.location));
    let reasoning_tags = reasoning.map(|r| json!({
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
    }));

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
            TradingSession::Asia             =>  0 * 60,
            TradingSession::London           =>  7 * 60,
            TradingSession::LondonNyOverlap  => 12 * 60,
            TradingSession::NewYork          => 13 * 60 + 30,
            TradingSession::OffHours              =>  0,
        };
        let current = hour_utc as i32 * 60 + minute_utc;
        (current - start).max(0) as i16
    });

    // ── Bloque 2: Calidad del rango ────────────────────────────────────────────
    let range_midline_slope = ctx.range.as_ref().map(|r| r.midline_slope);
    let range_bars_inside   = ctx.range.as_ref().map(|r| r.bars_inside as i32);
    let range_second_test = {
        let is_long = signal.side.map(|s| matches!(s, data::strategy::types::Side::Long)).unwrap_or(false);
        ctx.range.as_ref().map(|r| if is_long { r.touches_low >= 2 } else { r.touches_high >= 2 })
    };
    let range_vs_value_area: Option<&str> = match (ctx.volume_profile.vah, ctx.volume_profile.val, ctx.range.as_ref()) {
        (Some(vah), Some(val), Some(r)) => {
            if r.range_low >= val && r.range_high <= vah       { Some("inside_va") }
            else if r.range_low >= vah                         { Some("above_va") }
            else if r.range_high <= val                        { Some("below_va") }
            else                                               { Some("spanning_va") }
        }
        _ => None,
    };

    // ── Bloque 3: Calidad de absorción ────────────────────────────────────────
    let is_long_signal = signal.side.map(|s| matches!(s, data::strategy::types::Side::Long)).unwrap_or(false);
    let absorption_count: i16 = if is_long_signal {
        [
            ctx.flow.footprint_absorption == data::strategy::types::AbsorptionSide::Bid,
            ctx.flow.big_trade_bearish,
            matches!(ctx.flow.cvd_divergence, Some(data::strategy::types::CvdDivergence::BullishAbsorption)),
            ctx.flow.finish_action_bullish,
            ctx.range.as_ref().map(|r| r.sweep_range_low).unwrap_or(false),
        ].iter().filter(|&&b| b).count() as i16
    } else {
        [
            ctx.flow.footprint_absorption == data::strategy::types::AbsorptionSide::Ask,
            ctx.flow.big_trade_bullish,
            matches!(ctx.flow.cvd_divergence, Some(data::strategy::types::CvdDivergence::BearishAbsorption)),
            ctx.flow.finish_action_bearish,
            ctx.range.as_ref().map(|r| r.sweep_range_high).unwrap_or(false),
        ].iter().filter(|&&b| b).count() as i16
    };
    let entry_type: Option<&str> = ctx.range.as_ref().map(|r| {
        if is_long_signal { if r.sweep_range_low { "sweep_reclaim" } else { "near_extreme" } }
        else              { if r.sweep_range_high { "sweep_reclaim" } else { "near_extreme" } }
    });
    let sweep_depth_atr = ctx.range.as_ref().and_then(|r| {
        let depth = if is_long_signal { r.sweep_low_depth } else { r.sweep_high_depth };
        depth.zip(ctx.atr).map(|(d, a)| d / a)
    });
    let delta_at_extreme = ctx.flow.delta;
    let bar_volume = ctx.flow.buy_volume.zip(ctx.flow.sell_volume).map(|(b, s)| b + s);

    // ── Bloque 4: Contexto de precio y estructura ─────────────────────────────
    let value_location  = Some(format!("{:?}", ctx.volume_profile.value_location));
    let price_vs_vwap   = Some(format!("{:?}", ctx.vwap.price_vs_vwap));
    let price_vs_avwap_bos = Some(format!("{:?}", ctx.vwap.price_vs_avwap_bos));
    let naked_poc_in_target_path = signal.entry_price.zip(signal.target_price).map(|(e, t)| {
        let (lo, hi) = if t > e { (e, t) } else { (t, e) };
        ctx.volume_profile.naked_pocs.iter().any(|&p| p > lo && p < hi)
    });
    let hvn_between_entry_target = signal.entry_price.zip(signal.target_price).map(|(e, t)| {
        let (lo, hi) = if t > e { (e, t) } else { (t, e) };
        ctx.volume_profile.hvn_nearby.iter().any(|&h| h > lo && h < hi)
    });
    let fast_slope_at_entry = ctx.flow.fast_slope;

    // ── Bloque 5: Institucional compacto ─────────────────────────────────────
    let oi_direction = ctx.flow.oi_momentum_aligned.map(|aligned| if aligned { "aligned" } else { "opposed" });
    let cvd_div_persist = ctx.flow.cvd_divergence_persistence;
    let vpin_val = ctx.flow.vpin;
    let funding_velocity_val = inst.map(|i| i.funding.velocity);

    // ── Bloque 6: Calidad del trade ───────────────────────────────────────────
    let rr_actual = signal.entry_price.zip(signal.stop_price).zip(signal.target_price)
        .map(|((e, s), t)| { let risk = (e - s).abs(); let rew = (t - e).abs(); if risk > 1e-10 { rew / risk } else { 0.0 } });
    let distance_to_target_atr = signal.entry_price.zip(signal.target_price).zip(ctx.atr)
        .map(|((e, t), a)| (t - e).abs() / a);
    let distance_to_stop_atr = signal.entry_price.zip(signal.stop_price).zip(ctx.atr)
        .map(|((e, s), a)| (e - s).abs() / a);
    let obstacle_hvn_count: Option<i16> = signal.entry_price.zip(signal.target_price).map(|(e, t)| {
        let (lo, hi) = if t > e { (e, t) } else { (t, e) };
        ctx.volume_profile.hvn_nearby.iter().filter(|&&h| h > lo && h < hi).count() as i16
    });
    let nearest_naked_poc_dist_atr = ctx.atr.and_then(|a| {
        ctx.volume_profile.naked_pocs.iter()
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
    let rr = signal.entry_price.zip(signal.stop_price).zip(signal.target_price).map(|((e, s), t)| {
        let risk = (e - s).abs();
        let rew  = (t - e).abs();
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
        "missing_data": signal.missing.join(", "),
        "snapshot":     serde_json::to_value(&signal.evidence).unwrap_or(Value::Null),
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
