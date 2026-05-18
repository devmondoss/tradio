/// Loads calibrated StrategyConfig from Supabase deployed_params table.
///
/// Uses Supabase REST API (no sqlx dependency) — works with the existing
/// reqwest client already in this crate.
///
/// Environment variables:
///   SUPABASE_URL  — e.g. https://[PROJECT].supabase.co
///   SUPABASE_KEY  — anon or service-role key
///
/// When SUPABASE_URL / SUPABASE_KEY are not set the loader returns
/// StrategyConfig::default() silently — safe for local dev.
use serde::Deserialize;
use serde_json::Value;
use std::time::{Duration, Instant};

use data::strategy::types::StrategyConfig;

const RELOAD_INTERVAL: Duration = Duration::from_secs(300);

#[derive(Deserialize)]
struct DeployedParamRow {
    params: Value,
}

pub struct ConfigLoader {
    supabase_url: Option<String>,
    supabase_key: Option<String>,
    client: reqwest::Client,
    pub current_regime: String,
    pub last_reload: Option<Instant>,
}

impl ConfigLoader {
    pub fn new() -> Self {
        let supabase_url = std::env::var("SUPABASE_URL").ok();
        let supabase_key = std::env::var("SUPABASE_KEY").ok();

        if supabase_url.is_none() {
            eprintln!("[config] SUPABASE_URL not set — using hardcoded defaults");
        }

        Self {
            supabase_url,
            supabase_key,
            client: reqwest::Client::new(),
            current_regime: String::new(),
            last_reload: None,
        }
    }

    /// Returns true when a reload should be attempted.
    pub fn should_reload(&self, new_regime: &str) -> bool {
        let regime_changed = new_regime != self.current_regime;
        let stale = self
            .last_reload
            .map(|t| t.elapsed() >= RELOAD_INTERVAL)
            .unwrap_or(true);
        regime_changed || stale
    }

    /// Fetches calibrated params for `regime` from Supabase.
    /// Falls back to StrategyConfig::default() on any error or missing config.
    pub async fn load_for_regime(&mut self, regime: &str) -> StrategyConfig {
        let (url, key) = match (&self.supabase_url, &self.supabase_key) {
            (Some(u), Some(k)) => (u.clone(), k.clone()),
            _ => {
                self.current_regime = regime.to_string();
                self.last_reload = Some(Instant::now());
                return StrategyConfig { enabled: true, ..StrategyConfig::default() };
            }
        };

        let endpoint = format!(
            "{}/rest/v1/deployed_params\
             ?regime=eq.{}\
             &strategy=is.null\
             &is_active=eq.true\
             &order=updated_at.desc\
             &limit=1",
            url, regime
        );

        let result = self
            .client
            .get(&endpoint)
            .header("apikey", &key)
            .header("Authorization", format!("Bearer {key}"))
            .header("Accept", "application/json")
            .send()
            .await;

        let cfg = match result {
            Err(e) => {
                eprintln!("[config] Supabase request failed: {e} — keeping defaults");
                StrategyConfig { enabled: true, ..StrategyConfig::default() }
            }
            Ok(resp) => {
                let rows: Vec<DeployedParamRow> = match resp.json().await {
                    Ok(v) => v,
                    Err(e) => {
                        eprintln!("[config] Supabase parse failed: {e} — keeping defaults");
                        return StrategyConfig { enabled: true, ..StrategyConfig::default() };
                    }
                };

                match rows.into_iter().next() {
                    None => {
                        eprintln!(
                            "[config] No calibrated params for regime '{regime}' — using defaults"
                        );
                        StrategyConfig { enabled: true, ..StrategyConfig::default() }
                    }
                    Some(row) => {
                        let cfg = parse_config_from_json(&row.params);
                        eprintln!(
                            "[config] Loaded calibrated params for regime '{regime}': \
                             min_score={:.3} liq_min={:.0} divergence={:.3}",
                            cfg.min_score, cfg.liq_hunt_min_usd, cfg.min_divergence
                        );
                        cfg
                    }
                }
            }
        };

        self.current_regime = regime.to_string();
        self.last_reload = Some(Instant::now());
        cfg
    }
}

impl Default for ConfigLoader {
    fn default() -> Self {
        Self::new()
    }
}

fn parse_config_from_json(params: &Value) -> StrategyConfig {
    let f = |key: &str, default: f64| -> f64 {
        params.get(key).and_then(|v| v.as_f64()).unwrap_or(default)
    };
    let i = |key: &str, default: i64| -> i64 {
        params.get(key).and_then(|v| v.as_i64()).unwrap_or(default)
    };

    let d = StrategyConfig::default();
    StrategyConfig {
        enabled: true,
        max_spread_bps:            f("max_spread_bps",            d.max_spread_bps),
        max_vpin:                  f("max_vpin",                  d.max_vpin),
        min_score:                 f("min_score",                 d.min_score),
        default_ttl_ms:            i("default_ttl_ms",            d.default_ttl_ms),
        liq_hunt_min_usd:          f("liq_hunt_min_usd",          d.liq_hunt_min_usd),
        liq_cascade_threshold:     f("liq_cascade_threshold",     d.liq_cascade_threshold),
        liq_ttl_ms:                i("liq_ttl_ms",                d.liq_ttl_ms),
        funding_extreme_threshold: f("funding_extreme_threshold", d.funding_extreme_threshold),
        funding_ttl_ms:            i("funding_ttl_ms",            d.funding_ttl_ms),
        fer_top_long_min:          f("fer_top_long_min",          d.fer_top_long_min),
        fer_retail_long_max:       f("fer_retail_long_max",       d.fer_retail_long_max),
        smart_short_threshold:     f("smart_short_threshold",     d.smart_short_threshold),
        retail_long_threshold:     f("retail_long_threshold",     d.retail_long_threshold),
        min_divergence:            f("min_divergence",            d.min_divergence),
        smd_ttl_ms:                i("smd_ttl_ms",                d.smd_ttl_ms),
        min_rr:                    f("min_rr",                    d.min_rr),
        max_rr_m5:                 f("max_rr_m5",                 d.max_rr_m5),
        session_filter_enabled:    d.session_filter_enabled,
        min_score_institutional:   f("min_score_institutional",   d.min_score_institutional),
        htf_scoring_enabled:       d.htf_scoring_enabled,
        spoof_gate_enabled:        d.spoof_gate_enabled,
    }
}
