//! Carga de `config/strategy.toml` — la fuente única de verdad para los
//! parámetros base de estrategia.
//!
//! Capas de precedencia (de menor a mayor):
//!   1. [`StrategyConfig::default`]  → fallback de emergencia si el archivo falta o no parsea
//!   2. `config/strategy.toml`       → este loader (fuente base canónica)
//!   3. Mongo `deployed_params`      → override por régimen (aplicado por el config loader de Mongo)
//!
//! Todos los campos del TOML son opcionales: lo ausente conserva el valor de
//! [`StrategyConfig::default`], de modo que un archivo parcial nunca rompe nada.

use super::types::StrategyConfig;
use serde::Deserialize;
use std::path::PathBuf;

/// Resuelve `config/strategy.toml` relativo al directorio de trabajo actual,
/// igual que `logs/` para los JSONL. `cargo run` arranca desde la raíz del repo.
fn config_path() -> PathBuf {
    std::env::current_dir()
        .unwrap_or_else(|_| PathBuf::from("."))
        .join("config")
        .join("strategy.toml")
}

#[derive(Deserialize, Default)]
struct GeneralSection {
    enabled: Option<bool>,
    drr_enabled: Option<bool>,
    min_score: Option<f64>,
    min_score_institutional: Option<f64>,
    max_spread_bps: Option<f64>,
    max_vpin: Option<f64>,
    min_rr: Option<f64>,
    max_rr_m5: Option<f64>,
    default_ttl_min: Option<i64>,
    session_filter_enabled: Option<bool>,
    htf_scoring_enabled: Option<bool>,
    spoof_gate_enabled: Option<bool>,
    cooldown_bars: Option<u64>,
}

#[derive(Deserialize, Default)]
struct ScalpingSection {
    enabled: Option<bool>,
    max_spread_ticks: Option<i32>,
    max_vr: Option<f64>,
    obi_threshold_long: Option<f64>,
    obi_threshold_short: Option<f64>,
    min_conviction_score: Option<f64>,
    max_trades_per_session: Option<u32>,
    daily_loss_limit_pct: Option<f64>,
    max_consecutive_losses: Option<u32>,
    time_stop_secs: Option<u64>,
    s2_dz_min: Option<f64>,
    s2_vr_min: Option<f64>,
    s3_lookback_bars: Option<usize>,
    s3_min_divergence_strength: Option<f64>,
    london_min_score:           Option<f64>,
    min_rr: Option<f64>,
}

#[derive(Deserialize, Default)]
struct LiquidationHuntSection {
    min_usd: Option<f64>,
    cascade_threshold: Option<f64>,
    ttl_min: Option<i64>,
}

#[derive(Deserialize, Default)]
struct FundingExhaustionSection {
    extreme_threshold: Option<f64>,
    top_long_min: Option<f64>,
    retail_long_max: Option<f64>,
    ttl_min: Option<i64>,
}

#[derive(Deserialize, Default)]
struct SmartMoneyDivergenceSection {
    short_threshold: Option<f64>,
    retail_long_threshold: Option<f64>,
    min_divergence: Option<f64>,
    ttl_min: Option<i64>,
}

#[derive(Deserialize, Default)]
struct RangeBreakoutSection {
    enabled:          Option<bool>,
    stop_pct:         Option<f64>,
    target_short_pct: Option<f64>,
    target_long_pct:  Option<f64>,
    min_rr:           Option<f64>,
    cvd_slope_gate:   Option<bool>,
    dz_min:           Option<f64>,
    dz_max:           Option<f64>,
    obi_gate:             Option<bool>,
    obi_threshold:        Option<f64>,
    min_confluence_score: Option<u8>,
    cvd_slope_threshold:  Option<f64>,
    bear_long_min_score:  Option<u8>,
    pre_breakout_enabled:  Option<bool>,
    pre_breakout_zone_pct: Option<f64>,
    pre_breakout_rr:       Option<f64>,
    pre_breakout_vr_min:   Option<f64>,
    pre_breakout_oi_max:   Option<u8>,
}

#[derive(Deserialize, Default)]
struct AmdDetectorSection {
    enabled:                   Option<bool>,
    accum_range_min_pct:       Option<f64>,
    accum_range_max_pct:       Option<f64>,
    accum_min_bars:            Option<usize>,
    accum_max_bars:            Option<usize>,
    manip_min_vr:              Option<f64>,
    manip_vpin_threshold:      Option<f64>,
    dist_min_vr:               Option<f64>,
    dist_cvd_slope:            Option<f64>,
    dist_obi_confirm:          Option<f64>,
    stop_buffer_pct:           Option<f64>,
    min_rr:                    Option<f64>,
    cooldown_bars:             Option<usize>,
    max_wait_bars_after_spike: Option<usize>,
}

#[derive(Deserialize, Default)]
struct StrategyConfigFile {
    #[serde(default)]
    general: GeneralSection,
    #[serde(default)]
    liquidation_hunt: LiquidationHuntSection,
    #[serde(default)]
    funding_exhaustion: FundingExhaustionSection,
    #[serde(default)]
    smart_money_divergence: SmartMoneyDivergenceSection,
    #[serde(default)]
    scalping: ScalpingSection,
    #[serde(default)]
    range_breakout: RangeBreakoutSection,
    #[serde(default)]
    amd_detector: AmdDetectorSection,
}

const MIN_MS: i64 = 60 * 1000;

impl StrategyConfigFile {
    /// Aplica los valores presentes en el archivo sobre un `StrategyConfig` base
    /// (normalmente `StrategyConfig::default()`). Lo ausente conserva el valor base.
    fn merge_onto(self, mut cfg: StrategyConfig) -> StrategyConfig {
        let g = self.general;
        if let Some(v) = g.enabled { cfg.enabled = v; }
        if let Some(v) = g.drr_enabled { cfg.drr_enabled = v; }
        if let Some(v) = g.min_score { cfg.min_score = v; }
        if let Some(v) = g.min_score_institutional { cfg.min_score_institutional = v; }
        if let Some(v) = g.max_spread_bps { cfg.max_spread_bps = v; }
        if let Some(v) = g.max_vpin { cfg.max_vpin = v; }
        if let Some(v) = g.min_rr { cfg.min_rr = v; }
        if let Some(v) = g.max_rr_m5 { cfg.max_rr_m5 = v; }
        if let Some(v) = g.default_ttl_min { cfg.default_ttl_ms = v * MIN_MS; }
        if let Some(v) = g.session_filter_enabled { cfg.session_filter_enabled = v; }
        if let Some(v) = g.htf_scoring_enabled { cfg.htf_scoring_enabled = v; }
        if let Some(v) = g.spoof_gate_enabled { cfg.spoof_gate_enabled = v; }
        if let Some(v) = g.cooldown_bars { cfg.cooldown_bars = v; }

        let lh = self.liquidation_hunt;
        if let Some(v) = lh.min_usd { cfg.liq_hunt_min_usd = v; }
        if let Some(v) = lh.cascade_threshold { cfg.liq_cascade_threshold = v; }
        if let Some(v) = lh.ttl_min { cfg.liq_ttl_ms = v * MIN_MS; }

        let fe = self.funding_exhaustion;
        if let Some(v) = fe.extreme_threshold { cfg.funding_extreme_threshold = v; }
        if let Some(v) = fe.top_long_min { cfg.fer_top_long_min = v; }
        if let Some(v) = fe.retail_long_max { cfg.fer_retail_long_max = v; }
        if let Some(v) = fe.ttl_min { cfg.funding_ttl_ms = v * MIN_MS; }

        let smd = self.smart_money_divergence;
        if let Some(v) = smd.short_threshold { cfg.smart_short_threshold = v; }
        if let Some(v) = smd.retail_long_threshold { cfg.retail_long_threshold = v; }
        if let Some(v) = smd.min_divergence { cfg.min_divergence = v; }
        if let Some(v) = smd.ttl_min { cfg.smd_ttl_ms = v * MIN_MS; }

        let sc = self.scalping;
        if let Some(v) = sc.enabled { cfg.scalping.enabled = v; }
        if let Some(v) = sc.max_spread_ticks { cfg.scalping.max_spread_ticks = v; }
        if let Some(v) = sc.max_vr { cfg.scalping.max_vr = v; }
        if let Some(v) = sc.obi_threshold_long { cfg.scalping.obi_threshold_long = v; }
        if let Some(v) = sc.obi_threshold_short { cfg.scalping.obi_threshold_short = v; }
        if let Some(v) = sc.min_conviction_score { cfg.scalping.min_conviction_score = v; }
        if let Some(v) = sc.max_trades_per_session { cfg.scalping.max_trades_per_session = v; }
        if let Some(v) = sc.daily_loss_limit_pct { cfg.scalping.daily_loss_limit_pct = v; }
        if let Some(v) = sc.max_consecutive_losses { cfg.scalping.max_consecutive_losses = v; }
        if let Some(v) = sc.time_stop_secs { cfg.scalping.time_stop_secs = v; }
        if let Some(v) = sc.s2_dz_min { cfg.scalping.s2_dz_min = v; }
        if let Some(v) = sc.s2_vr_min { cfg.scalping.s2_vr_min = v; }
        if let Some(v) = sc.s3_lookback_bars { cfg.scalping.s3_lookback_bars = v; }
        if let Some(v) = sc.s3_min_divergence_strength { cfg.scalping.s3_min_divergence_strength = v; }
        if let Some(v) = sc.london_min_score           { cfg.scalping.london_min_score           = v; }
        if let Some(v) = sc.min_rr { cfg.scalping.min_rr = v; }

        let rb = self.range_breakout;
        if let Some(v) = rb.enabled          { cfg.range_breakout.enabled          = v; }
        if let Some(v) = rb.stop_pct         { cfg.range_breakout.stop_pct         = v; }
        if let Some(v) = rb.target_short_pct { cfg.range_breakout.target_short_pct = v; }
        if let Some(v) = rb.target_long_pct  { cfg.range_breakout.target_long_pct  = v; }
        if let Some(v) = rb.min_rr           { cfg.range_breakout.min_rr           = v; }
        if let Some(v) = rb.cvd_slope_gate   { cfg.range_breakout.cvd_slope_gate   = v; }
        if let Some(v) = rb.dz_min           { cfg.range_breakout.dz_min           = v; }
        if let Some(v) = rb.dz_max           { cfg.range_breakout.dz_max           = v; }
        if let Some(v) = rb.obi_gate             { cfg.range_breakout.obi_gate             = v; }
        if let Some(v) = rb.obi_threshold        { cfg.range_breakout.obi_threshold        = v; }
        if let Some(v) = rb.min_confluence_score { cfg.range_breakout.min_confluence_score = v; }
        if let Some(v) = rb.cvd_slope_threshold  { cfg.range_breakout.cvd_slope_threshold  = v; }
        if let Some(v) = rb.bear_long_min_score  { cfg.range_breakout.bear_long_min_score  = v; }
        if let Some(v) = rb.pre_breakout_enabled  { cfg.range_breakout.pre_breakout_enabled  = v; }
        if let Some(v) = rb.pre_breakout_zone_pct { cfg.range_breakout.pre_breakout_zone_pct = v; }
        if let Some(v) = rb.pre_breakout_rr       { cfg.range_breakout.pre_breakout_rr       = v; }
        if let Some(v) = rb.pre_breakout_vr_min   { cfg.range_breakout.pre_breakout_vr_min   = v; }
        if rb.pre_breakout_oi_max.is_some() { cfg.range_breakout.pre_breakout_oi_max = rb.pre_breakout_oi_max; }

        let amd = self.amd_detector;
        if let Some(v) = amd.enabled                   { cfg.amd.enabled                   = v; }
        if let Some(v) = amd.accum_range_min_pct       { cfg.amd.accum_range_min_pct       = v; }
        if let Some(v) = amd.accum_range_max_pct       { cfg.amd.accum_range_max_pct       = v; }
        if let Some(v) = amd.accum_min_bars            { cfg.amd.accum_min_bars            = v; }
        if let Some(v) = amd.accum_max_bars            { cfg.amd.accum_max_bars            = v; }
        if let Some(v) = amd.manip_min_vr              { cfg.amd.manip_min_vr              = v; }
        if let Some(v) = amd.manip_vpin_threshold      { cfg.amd.manip_vpin_threshold      = v; }
        if let Some(v) = amd.dist_min_vr               { cfg.amd.dist_min_vr               = v; }
        if let Some(v) = amd.dist_cvd_slope            { cfg.amd.dist_cvd_slope            = v; }
        if let Some(v) = amd.dist_obi_confirm          { cfg.amd.dist_obi_confirm          = v; }
        if let Some(v) = amd.stop_buffer_pct           { cfg.amd.stop_buffer_pct           = v; }
        if let Some(v) = amd.min_rr                    { cfg.amd.min_rr                    = v; }
        if let Some(v) = amd.cooldown_bars             { cfg.amd.cooldown_bars             = v; }
        if let Some(v) = amd.max_wait_bars_after_spike { cfg.amd.max_wait_bars_after_spike = v; }

        cfg
    }
}

impl StrategyConfig {
    /// Carga la configuración base desde `config/strategy.toml`, cayendo a
    /// [`StrategyConfig::default`] si el archivo falta o no parsea.
    ///
    /// Esta es la fuente base que usan tanto la UI como el monitor; la
    /// calibración la sobreescribe por régimen vía Mongo `deployed_params`.
    pub fn load() -> Self {
        let path = config_path();
        let base = StrategyConfig::default();
        match std::fs::read_to_string(&path) {
            Ok(contents) => match toml::from_str::<StrategyConfigFile>(&contents) {
                Ok(file) => file.merge_onto(base),
                Err(e) => {
                    log::warn!(
                        "strategy config: {} no parsea ({e}); usando defaults",
                        path.display()
                    );
                    base
                }
            },
            Err(_) => {
                log::info!(
                    "strategy config: {} no encontrado; usando defaults",
                    path.display()
                );
                base
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// El `config/strategy.toml` versionado debe parsear y producir los valores
    /// esperados, incluida la conversión de TTL minutos→ms. Protege contra que un
    /// rename de campo en el TOML deje de mapear silenciosamente al StrategyConfig.
    #[test]
    fn shipped_toml_parses_and_maps() {
        let toml_src = include_str!("../../../config/strategy.toml");
        let file: StrategyConfigFile =
            toml::from_str(toml_src).expect("config/strategy.toml debe parsear");
        let cfg = file.merge_onto(StrategyConfig::default());

        assert!(cfg.enabled);
        assert!(cfg.session_filter_enabled);
        assert!(cfg.htf_scoring_enabled);
        assert_eq!(cfg.min_score, 0.60);
        assert_eq!(cfg.min_score_institutional, 0.55);
        assert_eq!(cfg.cooldown_bars, 5);
        // TTL en minutos en el archivo → ms en el struct.
        assert_eq!(cfg.default_ttl_ms, 250 * 60 * 1000);
        assert_eq!(cfg.liq_ttl_ms, 10 * 60 * 1000);
        assert_eq!(cfg.funding_ttl_ms, 30 * 60 * 1000);
        assert_eq!(cfg.smd_ttl_ms, 20 * 60 * 1000);
        assert_eq!(cfg.liq_hunt_min_usd, 100_000.0);
        assert_eq!(cfg.funding_extreme_threshold, 0.001);
    }

    /// Un archivo parcial conserva los defaults para lo ausente.
    #[test]
    fn partial_toml_keeps_defaults() {
        let cfg = toml::from_str::<StrategyConfigFile>("[general]\nmin_score = 0.9\n")
            .unwrap()
            .merge_onto(StrategyConfig::default());
        assert_eq!(cfg.min_score, 0.9);
        // No tocado por el archivo → valor de Default.
        assert_eq!(cfg.max_vpin, StrategyConfig::default().max_vpin);
    }
}
