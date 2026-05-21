//! Loader que sobreescribe `StrategyConfig` por régimen leyendo la colección
//! `deployed_params` de MongoDB.
//!
//! ## Capas
//!
//! 1. `StrategyConfig::default()`            ← fallback de emergencia
//! 2. `config/strategy.toml` vía `load()`    ← base canónica
//! 3. `deployed_params` por régimen (Mongo)  ← este loader, override de calibración
//!
//! ## Modelo
//!
//! Colección `deployed_params`, un documento por (régimen × strategy):
//!
//! ```text
//! {
//!   "regime": "TrendUp",       // o null para "todos los regímenes"
//!   "strategy": null,           // null = aplica a todos los detectores
//!   "is_active": true,
//!   "params": { "min_score": 0.68, "cooldown_bars": 7, ... },
//!   "updated_at": ISODate(...)
//! }
//! ```
//!
//! El loader corre en un thread con runtime tokio (igual que el writer) y
//! publica el `StrategyConfig` resultante en un `Arc<RwLock<_>>` que la UI lee
//! sin bloquearse.

use super::types::StrategyConfig;
use mongodb::bson::{doc, Bson, Document};
use mongodb::options::FindOneOptions;
use mongodb::Client;
use std::sync::{mpsc, Arc, RwLock};
use std::thread;

// Misma instancia dedicada que el writer; ver mongo_writer.rs.
// Solo activo en local cuando MONGODB_URI está seteada.
const DEFAULT_DB: &str = "flowsurface";

enum LoaderCmd {
    Reload(String),
    Shutdown,
}

/// Handle clonable. Internamente comparte un `Arc<RwLock<StrategyConfig>>`
/// publicado por el thread loader.
#[derive(Clone)]
pub struct MongoConfigLoader {
    current: Arc<RwLock<StrategyConfig>>,
    tx: mpsc::Sender<LoaderCmd>,
}

impl MongoConfigLoader {
    /// Lanza el loader si `MONGODB_URI` está en el entorno.
    /// Sin esa variable retorna un loader estático con `base` (Railway usa Supabase, no Mongo).
    pub fn from_env(base: StrategyConfig) -> Self {
        match std::env::var("MONGODB_URI") {
            Ok(uri) => {
                let db = std::env::var("MONGODB_DB").unwrap_or_else(|_| DEFAULT_DB.into());
                Self::spawn(uri, db, base)
            }
            Err(_) => {
                eprintln!("[mongo-cfg] MONGODB_URI not set — config loader disabled (using base only)");
                let current = Arc::new(RwLock::new(base));
                let (tx, _rx) = mpsc::channel::<LoaderCmd>();
                Self { current, tx }
            }
        }
    }

    /// Lanza el loader con URI/db explícitos. Siempre retorna un handle; si la
    /// conexión falla, `current()` simplemente devolverá el `base` para
    /// siempre.
    pub fn spawn(uri: String, db_name: String, base: StrategyConfig) -> Self {
        let current = Arc::new(RwLock::new(base.clone()));
        let (tx, rx) = mpsc::channel::<LoaderCmd>();
        let current_w = current.clone();
        thread::Builder::new()
            .name("mongo-config-loader".into())
            .spawn(move || run_loader_thread(uri, db_name, base, current_w, rx))
            .expect("spawn mongo-config-loader thread");
        Self { current, tx }
    }

    /// Snapshot actual del config (clona, no bloquea).
    pub fn current(&self) -> StrategyConfig {
        self.current.read().expect("config rwlock poisoned").clone()
    }

    /// Solicita recarga para un régimen dado. No bloquea — el thread actualiza
    /// el `RwLock` cuando termine. Llama esto al detectar cambio de régimen.
    pub fn request_reload(&self, regime: &str) {
        let _ = self.tx.send(LoaderCmd::Reload(regime.to_string()));
    }

    pub fn shutdown(&self) {
        let _ = self.tx.send(LoaderCmd::Shutdown);
    }
}

fn run_loader_thread(
    uri: String,
    db_name: String,
    base: StrategyConfig,
    current: Arc<RwLock<StrategyConfig>>,
    rx: mpsc::Receiver<LoaderCmd>,
) {
    let rt = match tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
    {
        Ok(rt) => rt,
        Err(e) => {
            eprintln!("[mongo-cfg] runtime build failed: {e}; loader disabled (using base only)");
            return;
        }
    };

    rt.block_on(async move {
        let client = match Client::with_uri_str(&uri).await {
            Ok(c) => c,
            Err(e) => {
                eprintln!(
                    "[mongo-cfg] connect {uri} failed: {e}; loader disabled (using base only)"
                );
                return;
            }
        };
        let db = client.database(&db_name);
        eprintln!("[mongo-cfg] loader connected → {uri} / db={db_name}");

        while let Ok(msg) = rx.recv() {
            match msg {
                LoaderCmd::Shutdown => break,
                LoaderCmd::Reload(regime) => {
                    let coll = db.collection::<Document>("deployed_params");
                    let filter = doc! {
                        "regime": &regime,
                        "is_active": true,
                        // strategy null = se aplica a todos los detectores
                        "strategy": Bson::Null,
                    };
                    let opts = FindOneOptions::builder()
                        .sort(doc! { "updated_at": -1 })
                        .build();
                    let cfg = match coll.find_one(filter).with_options(opts).await {
                        Ok(Some(doc)) => apply_overrides(base.clone(), &doc),
                        Ok(None) => {
                            eprintln!(
                                "[mongo-cfg] no deployed_params for regime={regime}; using base"
                            );
                            base.clone()
                        }
                        Err(e) => {
                            eprintln!("[mongo-cfg] query failed for regime={regime}: {e}; using base");
                            base.clone()
                        }
                    };
                    *current.write().expect("config rwlock poisoned") = cfg;
                }
            }
        }
        eprintln!("[mongo-cfg] loader thread exiting");
    });
}

fn apply_overrides(mut cfg: StrategyConfig, doc: &Document) -> StrategyConfig {
    let Some(params) = doc.get_document("params").ok() else {
        eprintln!("[mongo-cfg] deployed_params doc has no `params` subdoc; using base");
        return cfg;
    };
    let g_f64 = |k: &str| params.get_f64(k).ok();
    let g_i64 = |k: &str| params.get_i64(k).ok();
    let g_bool = |k: &str| params.get_bool(k).ok();

    if let Some(v) = g_bool("enabled") { cfg.enabled = v; }
    if let Some(v) = g_f64("min_score") { cfg.min_score = v; }
    if let Some(v) = g_f64("min_score_institutional") { cfg.min_score_institutional = v; }
    if let Some(v) = g_f64("max_spread_bps") { cfg.max_spread_bps = v; }
    if let Some(v) = g_f64("max_vpin") { cfg.max_vpin = v; }
    if let Some(v) = g_f64("min_rr") { cfg.min_rr = v; }
    if let Some(v) = g_f64("max_rr_m5") { cfg.max_rr_m5 = v; }
    if let Some(v) = g_i64("default_ttl_ms") { cfg.default_ttl_ms = v; }
    if let Some(v) = g_bool("session_filter_enabled") { cfg.session_filter_enabled = v; }
    if let Some(v) = g_bool("htf_scoring_enabled") { cfg.htf_scoring_enabled = v; }
    if let Some(v) = g_bool("spoof_gate_enabled") { cfg.spoof_gate_enabled = v; }
    if let Some(v) = g_i64("cooldown_bars") { cfg.cooldown_bars = v as u64; }
    if let Some(v) = g_f64("liq_hunt_min_usd") { cfg.liq_hunt_min_usd = v; }
    if let Some(v) = g_f64("liq_cascade_threshold") { cfg.liq_cascade_threshold = v; }
    if let Some(v) = g_i64("liq_ttl_ms") { cfg.liq_ttl_ms = v; }
    if let Some(v) = g_f64("funding_extreme_threshold") { cfg.funding_extreme_threshold = v; }
    if let Some(v) = g_i64("funding_ttl_ms") { cfg.funding_ttl_ms = v; }
    if let Some(v) = g_f64("fer_top_long_min") { cfg.fer_top_long_min = v; }
    if let Some(v) = g_f64("fer_retail_long_max") { cfg.fer_retail_long_max = v; }
    if let Some(v) = g_f64("smart_short_threshold") { cfg.smart_short_threshold = v; }
    if let Some(v) = g_f64("retail_long_threshold") { cfg.retail_long_threshold = v; }
    if let Some(v) = g_f64("min_divergence") { cfg.min_divergence = v; }
    if let Some(v) = g_i64("smd_ttl_ms") { cfg.smd_ttl_ms = v; }
    cfg
}
