use crate::exchange_config::ExchangeTarget;

#[derive(Debug, Clone)]
pub struct MonitorRuntimeConfig {
    pub profile: String,
    pub mtf_futures_shorts: bool,
    pub mtf_futures_longs: bool,
    pub mtf_spot_shorts: bool,
    pub mtf_spot_longs: bool,
    /// True when MONITOR_PROFILE=mtf_spot_live — enables real order execution on Bybit.
    pub live_mode: bool,
}

impl MonitorRuntimeConfig {
    pub fn from_env(exchange: ExchangeTarget) -> Self {
        let profile = std::env::var("MONITOR_PROFILE")
            .ok()
            .filter(|v| !v.trim().is_empty())
            .unwrap_or_else(|| {
                if exchange.is_spot() {
                    "mtf_spot_paper".to_string()
                } else {
                    "mtf_futures_paper".to_string()
                }
            })
            .to_lowercase()
            .replace('-', "_");

        let mut cfg = match profile.as_str() {
            "mtf_spot" | "mtf_spot_paper" | "spot_paper" => Self {
                profile,
                mtf_futures_shorts: false,
                mtf_futures_longs: false,
                mtf_spot_shorts: true,
                mtf_spot_longs: true,
                live_mode: false,
            },
            "mtf_spot_live" | "spot_live" => Self {
                profile,
                mtf_futures_shorts: false,
                mtf_futures_longs: false,
                mtf_spot_shorts: true,
                mtf_spot_longs: true,
                live_mode: true,
            },
            // Corre la estrategia calibrada en SPOT contra un exchange de FUTUROS
            // (bybit_linear). Solo shorts (longs descartados, sin edge). Paper.
            // El detector spot consume el order book del feed conectado = futuros.
            "mtf_spot_futures_paper" | "spot_futures_paper" => Self {
                profile,
                mtf_futures_shorts: false,
                mtf_futures_longs: false,
                mtf_spot_shorts: true,
                mtf_spot_longs: false,
                live_mode: false,
            },
            "mtf_all" | "mtf_all_paper" | "all_paper" => Self {
                profile,
                mtf_futures_shorts: true,
                mtf_futures_longs: true,
                mtf_spot_shorts: true,
                mtf_spot_longs: true,
                live_mode: false,
            },
            "off" | "none" | "disabled" => Self {
                profile,
                mtf_futures_shorts: false,
                mtf_futures_longs: false,
                mtf_spot_shorts: false,
                mtf_spot_longs: false,
                live_mode: false,
            },
            _ => Self {
                profile,
                mtf_futures_shorts: true,
                mtf_futures_longs: true,
                mtf_spot_shorts: false,
                mtf_spot_longs: false,
                live_mode: false,
            },
        };

        if let Ok(raw) = std::env::var("MONITOR_STRATEGIES") {
            cfg.mtf_futures_shorts = false;
            cfg.mtf_futures_longs = false;
            cfg.mtf_spot_shorts = false;
            cfg.mtf_spot_longs = false;

            for token in raw
                .split(',')
                .map(|s| s.trim().to_lowercase().replace('-', "_"))
            {
                match token.as_str() {
                    "mtf_futures_shorts" | "futures_shorts" | "shorts_futures" => {
                        cfg.mtf_futures_shorts = true;
                    }
                    "mtf_futures_longs" | "futures_longs" | "longs_futures" => {
                        cfg.mtf_futures_longs = true;
                    }
                    "mtf_spot_shorts" | "spot_shorts" | "shorts_spot" => {
                        cfg.mtf_spot_shorts = true;
                    }
                    "mtf_spot_longs" | "spot_longs" | "longs_spot" => {
                        cfg.mtf_spot_longs = true;
                    }
                    "mtf_futures" | "futures" => {
                        cfg.mtf_futures_shorts = true;
                        cfg.mtf_futures_longs = true;
                    }
                    "mtf_spot" | "spot" => {
                        cfg.mtf_spot_shorts = true;
                        cfg.mtf_spot_longs = true;
                    }
                    "none" | "off" | "disabled" | "" => {}
                    other => eprintln!("[monitor_config] ignoring unknown strategy token: {other}"),
                }
            }
        }

        // Perfil que corre la estrategia spot-calibrada sobre un exchange de futuros.
        let spot_on_futures = matches!(
            cfg.profile.as_str(),
            "mtf_spot_futures_paper" | "spot_futures_paper"
        );
        if exchange.is_spot() {
            cfg.mtf_futures_shorts = false;
            cfg.mtf_futures_longs = false;
        } else if exchange.is_futures() && !spot_on_futures {
            cfg.mtf_spot_shorts = false;
            cfg.mtf_spot_longs = false;
        }

        cfg
    }

    pub fn uses_spot_mtf(&self) -> bool {
        self.mtf_spot_shorts || self.mtf_spot_longs
    }

    pub fn uses_futures_mtf(&self) -> bool {
        self.mtf_futures_shorts || self.mtf_futures_longs
    }

    pub fn log_boot(&self, exchange: ExchangeTarget) {
        println!(
            "[monitor_config] profile={} exchange={:?} futures_mtf=[shorts:{} longs:{}] spot_mtf=[shorts:{} longs:{}]",
            self.profile,
            exchange,
            self.mtf_futures_shorts,
            self.mtf_futures_longs,
            self.mtf_spot_shorts,
            self.mtf_spot_longs,
        );

        if exchange.is_spot() && self.uses_futures_mtf() {
            eprintln!(
                "[monitor_config] spot exchange selected with futures MTF enabled; futures detectors will be skipped"
            );
        }
        if exchange.is_futures() && self.uses_spot_mtf() {
            eprintln!(
                "[monitor_config] futures exchange selected with spot MTF enabled; spot detectors are separated and not emitted by futures MTF"
            );
        }
        if self.uses_spot_mtf() {
            if self.live_mode {
                println!("[monitor_config] MTF spot LIVE detector enabled — real orders will be placed on Bybit");
            } else {
                println!("[monitor_config] MTF spot paper detector enabled");
            }
        }
    }
}
