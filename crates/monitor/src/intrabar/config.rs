use std::env;

#[derive(Debug, Clone)]
pub struct IntrabarConfig {
    pub enabled: bool,
    pub mode: IntrabarMode,
    pub detectors: Vec<IntrabarDetector>,
    pub allow_execution: bool,
    pub write_decision_events: bool,
    pub write_outcomes: bool,

    // Trigger thresholds
    pub min_seconds_between_evals: u64,
    pub max_evals_per_bar: u32,
    pub price_move_atr_k: f64,
    pub time_fallback_ms: u64,

    // VWAP detector
    pub vwap_near_bps: f64,
    pub min_rr: f64,

    // LIQ detector
    pub liq_min_notional_usd: f64,
    pub liq_event_max_age_ms: u64,
}

#[derive(Debug, Clone, PartialEq)]
pub enum IntrabarMode {
    ObserveOnly,
    ShadowEvent,
    ShadowSignal,
}

#[derive(Debug, Clone, PartialEq)]
pub enum IntrabarDetector {
    Vwap,
    Liq,
}

impl IntrabarConfig {
    pub fn from_env() -> Self {
        let enabled = env_bool("TRADIO_INTRABAR_ENABLED", false);
        let mode = match env::var("TRADIO_INTRABAR_MODE")
            .unwrap_or_default()
            .to_uppercase()
            .as_str()
        {
            "SHADOW_EVENT" => IntrabarMode::ShadowEvent,
            "SHADOW_SIGNAL" => IntrabarMode::ShadowSignal,
            _ => IntrabarMode::ObserveOnly,
        };
        let detectors = parse_detectors(
            &env::var("TRADIO_INTRABAR_DETECTORS").unwrap_or_else(|_| "VWAP,LIQ".into()),
        );

        Self {
            enabled,
            mode,
            detectors,
            allow_execution: false, // hardcoded off — never from env in v0
            write_decision_events: env_bool("TRADIO_INTRABAR_WRITE_DECISION_EVENTS", true),
            write_outcomes: env_bool("TRADIO_INTRABAR_WRITE_OUTCOMES", true),
            min_seconds_between_evals: env_u64("TRADIO_INTRABAR_MIN_SECONDS_BETWEEN_EVALS", 15),
            max_evals_per_bar: env_u64("TRADIO_INTRABAR_MAX_EVALS_PER_BAR", 6) as u32,
            price_move_atr_k: env_f64("TRADIO_INTRABAR_PRICE_MOVE_ATR_K", 0.25),
            time_fallback_ms: env_u64("TRADIO_INTRABAR_TIME_FALLBACK_MS", 60_000),
            vwap_near_bps: env_f64("TRADIO_INTRABAR_VWAP_NEAR_BPS", 8.0),
            min_rr: env_f64("TRADIO_INTRABAR_MIN_RR", 1.2),
            liq_min_notional_usd: env_f64("TRADIO_INTRABAR_LIQ_MIN_NOTIONAL_USD", 50_000.0),
            liq_event_max_age_ms: env_u64("TRADIO_INTRABAR_LIQ_EVENT_MAX_AGE_MS", 5_000),
        }
    }

    pub fn log_boot(&self) {
        if !self.enabled {
            eprintln!(
                "[intrabar] disabled — set TRADIO_INTRABAR_ENABLED=true to activate"
            );
            return;
        }
        let detectors: Vec<&str> = self
            .detectors
            .iter()
            .map(|d| match d {
                IntrabarDetector::Vwap => "VWAP",
                IntrabarDetector::Liq => "LIQ",
            })
            .collect();
        eprintln!(
            "[intrabar] enabled mode={:?} detectors={} allow_execution={} \
             write_events={} write_outcomes={} \
             price_move_atr_k={} time_fallback_ms={} vwap_near_bps={} min_rr={} \
             liq_min_notional={} liq_max_age_ms={}",
            self.mode,
            detectors.join(","),
            self.allow_execution,
            self.write_decision_events,
            self.write_outcomes,
            self.price_move_atr_k,
            self.time_fallback_ms,
            self.vwap_near_bps,
            self.min_rr,
            self.liq_min_notional_usd,
            self.liq_event_max_age_ms,
        );
    }
}

fn env_bool(key: &str, default: bool) -> bool {
    env::var(key)
        .map(|v| matches!(v.to_lowercase().as_str(), "true" | "1" | "yes"))
        .unwrap_or(default)
}

fn env_u64(key: &str, default: u64) -> u64 {
    env::var(key)
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}

fn env_f64(key: &str, default: f64) -> f64 {
    env::var(key)
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}

fn parse_detectors(s: &str) -> Vec<IntrabarDetector> {
    s.split(',')
        .filter_map(|part| match part.trim().to_uppercase().as_str() {
            "VWAP" => Some(IntrabarDetector::Vwap),
            "LIQ" => Some(IntrabarDetector::Liq),
            _ => None,
        })
        .collect()
}
