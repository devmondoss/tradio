//! Writer asíncrono a MongoDB para señales y trades cerrados.
//!
//! ## Arquitectura
//!
//! La UI corre sobre `iced` (no tokio). Para escribir a Mongo sin bloquear el
//! loop de renderizado, este módulo levanta un **thread dedicado** con su
//! **propio runtime tokio `current_thread`**. La UI manda mensajes por un
//! `mpsc::Sender` y el thread los persiste async.
//!
//! Es *fire-and-forget*: la UI nunca espera al thread, y los errores del thread
//! solo se loguean a `stderr` — Mongo caído nunca rompe la UI.
//!
//! ## Linking signal ↔ trade
//!
//! Para enlazar un trade cerrado con la señal que lo originó **sin** esperar a
//! que Mongo nos devuelva un id (round-trip → bloqueo), generamos el
//! `ObjectId` en la UI con [`ObjectId::new()`] y lo usamos como `_id` del
//! documento de la señal. La UI guarda ese id y lo pasa a `write_trade` cuando
//! el trade se cierra. Mongo respeta el `_id` provisto.
//!
//! ## Variables de entorno
//!
//! - `MONGODB_URI` — default `mongodb://localhost:27017`
//! - `MONGODB_DB`  — default `flowsurface`

use super::paper::ClosedTrade;
use super::types::{StrategyAction, StrategyMarketContext, StrategySignal};
use mongodb::bson::{Bson, Document, oid::ObjectId};

use mongodb::Client;
/// Re-exportado para que la UI pueda almacenar el oid sin importar `mongodb`.
pub use mongodb::bson::oid::ObjectId as SignalOid;
use std::sync::mpsc;
use std::thread;

// Puerto 27018 aislado del :27017 por defecto. Datapath en
// %LOCALAPPDATA%\flowsurface\mongo-data — ver start-mongo.bat.
// Solo activo en local cuando MONGODB_URI está seteada.
const DEFAULT_DB: &str = "flowsurface";

enum MongoMsg {
    Insert { coll: &'static str, doc: Document },
    Shutdown,
}

/// Handle clonable que la UI usa para mandar escrituras. Internamente es un
/// `mpsc::Sender`; los `send` no bloquean (canal unbounded).
#[derive(Clone)]
pub struct MongoWriter {
    tx: mpsc::Sender<MongoMsg>,
}

impl MongoWriter {
    /// Lanza el thread escritor si `MONGODB_URI` está en el entorno.
    /// Sin esa variable retorna un writer noop (Railway usa Supabase, no Mongo).
    pub fn from_env() -> Self {
        match std::env::var("MONGODB_URI") {
            Ok(uri) => {
                let db = std::env::var("MONGODB_DB").unwrap_or_else(|_| DEFAULT_DB.into());
                Self::spawn(uri, db)
            }
            Err(_) => {
                println!("[mongo] MONGODB_URI not set — writer disabled (Supabase only)");
                let (tx, _rx) = mpsc::channel::<MongoMsg>();
                Self { tx }
            }
        }
    }

    /// Lanza el thread escritor con URI y base explícitos. Siempre retorna un
    /// handle: si la conexión falla, el thread loguea y termina; los `send`
    /// posteriores se descartan silenciosamente (canal cerrado).
    pub fn spawn(uri: String, db_name: String) -> Self {
        let (tx, rx) = mpsc::channel::<MongoMsg>();
        thread::Builder::new()
            .name("mongo-writer".into())
            .spawn(move || run_writer_thread(uri, db_name, rx))
            .expect("spawn mongo-writer thread");
        Self { tx }
    }

    /// Inserta una señal en `shadow_signals`. Retorna el `ObjectId` asignado
    /// (preservar para enlazar el trade posteriormente vía [`write_trade`]).
    /// Devuelve `None` si la acción es `Wait` (no se persiste).
    pub fn write_signal(
        &self,
        signal: &StrategySignal,
        ctx: &StrategyMarketContext,
    ) -> Option<ObjectId> {
        if signal.action == StrategyAction::Wait {
            return None;
        }
        let oid = ObjectId::new();
        let doc = build_signal_doc(oid, signal, ctx);
        let _ = self.tx.send(MongoMsg::Insert {
            coll: "shadow_signals",
            doc,
        });
        Some(oid)
    }

    /// Inserta un trade cerrado en `signal_outcomes`, enlazado a la señal que
    /// lo originó. Si no hay `signal_oid`, se descarta loggeando.
    pub fn write_trade(&self, trade: &ClosedTrade, signal_oid: Option<ObjectId>) {
        let Some(oid) = signal_oid else {
            eprintln!("[mongo] write_trade skipped — no signal_oid (trade not linked)");
            return;
        };
        let doc = build_trade_doc(trade, oid);
        let _ = self.tx.send(MongoMsg::Insert {
            coll: "signal_outcomes",
            doc,
        });
    }

    /// Detiene el thread limpiamente. Opcional — al cerrar la UI el canal se
    /// dropea y el thread sale por sí solo.
    pub fn shutdown(&self) {
        let _ = self.tx.send(MongoMsg::Shutdown);
    }
}

fn run_writer_thread(uri: String, db_name: String, rx: mpsc::Receiver<MongoMsg>) {
    let rt = match tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
    {
        Ok(rt) => rt,
        Err(e) => {
            eprintln!("[mongo] runtime build failed: {e}; writer disabled");
            return;
        }
    };

    rt.block_on(async move {
        let client = match Client::with_uri_str(&uri).await {
            Ok(c) => c,
            Err(e) => {
                eprintln!("[mongo] connect {uri} failed: {e}; writer disabled");
                return;
            }
        };
        let db = client.database(&db_name);
        eprintln!("[mongo] writer connected → {uri} / db={db_name}");

        while let Ok(msg) = rx.recv() {
            match msg {
                MongoMsg::Shutdown => break,
                MongoMsg::Insert { coll, doc } => {
                    if let Err(e) = db.collection::<Document>(coll).insert_one(doc).await {
                        eprintln!("[mongo] insert into {coll} failed: {e}");
                    }
                }
            }
        }
        eprintln!("[mongo] writer thread exiting");
    });
}

// ────────────────────────────────────────────────────────────────────────────
//  Constructores de Document — espejo conceptual de build_signal_row /
//  build_trade_row en `crates/monitor/src/supabase_writer.rs`. Se mantienen
//  sincronizados a mano por ahora (rara vez cambian; cada uno serializa el
//  mismo contexto).
// ────────────────────────────────────────────────────────────────────────────

fn build_signal_doc(
    oid: ObjectId,
    signal: &StrategySignal,
    ctx: &StrategyMarketContext,
) -> Document {
    let inst = ctx.institutional.as_ref();
    let regime_str = format!("{:?}", ctx.regime);

    let price = ctx.price;
    let hvn_above: Vec<Bson> = ctx
        .volume_profile
        .hvn_nearby
        .iter()
        .copied()
        .filter(|&h| h > price)
        .map(Bson::Double)
        .collect();
    let hvn_below: Vec<Bson> = ctx
        .volume_profile
        .hvn_nearby
        .iter()
        .copied()
        .filter(|&h| h < price)
        .map(Bson::Double)
        .collect();

    let evidence: Vec<Bson> = signal
        .evidence
        .iter()
        .map(|e| Bson::String(format!("{e:?}")))
        .collect();
    let missing: Vec<Bson> = signal
        .missing
        .iter()
        .map(|m| Bson::String(format!("{m:?}")))
        .collect();

    // Construido manualmente (no `doc!`) porque el macro satura el
    // recursion_limit de proc-macro con ~50 campos.
    let mut d = Document::new();
    d.insert("_id", oid);
    d.insert("timestamp_ms", ctx.timestamp_ms);
    d.insert("symbol", ctx.symbol.clone());
    d.insert(
        "strategy",
        signal
            .strategy_id
            .map(|s| format!("{s:?}"))
            .unwrap_or_default(),
    );
    d.insert("side", opt_str(signal.side.map(|s| format!("{s:?}"))));
    d.insert("regime_slow", regime_str.clone());
    d.insert("regime_fast", regime_str.clone());
    d.insert("regime_combined", regime_str);
    d.insert("action", format!("{:?}", signal.action));
    d.insert("entry_price", opt_f64(signal.entry_price));
    d.insert("stop_price", opt_f64(signal.stop_price));
    d.insert("target_price", opt_f64(signal.target_price));
    d.insert("score", signal.score);
    d.insert("ttl_ms", signal.ttl_ms);
    d.insert("evidence", Bson::Array(evidence));
    d.insert("missing", Bson::Array(missing));

    d.insert("leverage", ctx.leverage);

    d.insert("price", ctx.price);
    d.insert("vwap_session", opt_f64(ctx.vwap.vwap_session));
    d.insert("poc", opt_f64(ctx.volume_profile.poc));
    d.insert("vah", opt_f64(ctx.volume_profile.vah));
    d.insert("val", opt_f64(ctx.volume_profile.val));
    d.insert("cvd_slope", opt_f64(ctx.flow.cvd_slope));
    d.insert("cvd_absolute", opt_f64(ctx.flow.cvd));
    d.insert("atr", opt_f64(ctx.atr));
    d.insert("spread_bps", opt_f64(ctx.orderbook.spread_bps));
    d.insert("obi_l5", opt_f64(ctx.orderbook.obi_l5));
    d.insert("microprice", opt_f64(ctx.orderbook.microprice));

    d.insert("hvn_levels_above", Bson::Array(hvn_above));
    d.insert("hvn_levels_below", Bson::Array(hvn_below));
    d.insert(
        "nearest_wall_above",
        opt_f64(ctx.orderbook.walls_above.first().copied()),
    );
    d.insert(
        "nearest_wall_below",
        opt_f64(ctx.orderbook.walls_below.first().copied()),
    );
    d.insert("swing_high_20", opt_f64(ctx.swing_high_20));
    d.insert("swing_low_20", opt_f64(ctx.swing_low_20));

    d.insert(
        "short_liq_usd_5m",
        opt_f64(inst.map(|i| i.liquidations.short_liq_usd_5m)),
    );
    d.insert(
        "long_liq_usd_5m",
        opt_f64(inst.map(|i| i.liquidations.long_liq_usd_5m)),
    );
    d.insert(
        "total_liq_usd_5m",
        opt_f64(inst.map(|i| i.liquidations.total_usd_5m)),
    );
    d.insert(
        "cascade_active",
        opt_bool(inst.map(|i| i.liquidations.cascade_detected)),
    );
    d.insert(
        "liq_dominant_side",
        opt_str(inst.map(|i| format!("{:?}", i.liquidations.dominant_side))),
    );
    d.insert(
        "top_traders_long_pct",
        opt_f64(inst.map(|i| i.ls_ratio.top_traders_long_pct)),
    );
    d.insert(
        "retail_long_pct",
        opt_f64(inst.map(|i| i.ls_ratio.retail_long_pct)),
    );
    d.insert(
        "ls_divergence",
        opt_f64(inst.map(|i| i.ls_ratio.top_traders_long_pct - i.ls_ratio.retail_long_pct)),
    );
    d.insert(
        "divergence_signal",
        opt_str(inst.map(|i| format!("{:?}", i.ls_ratio.divergence_signal))),
    );
    d.insert("oi_current_btc", opt_f64(inst.map(|i| i.oi_trend.current)));
    d.insert(
        "oi_change_30m_pct",
        opt_f64(inst.map(|i| i.oi_trend.change_30m)),
    );
    d.insert(
        "oi_trend",
        opt_str(inst.map(|i| format!("{:?}", i.oi_trend.trend))),
    );
    d.insert("funding_current", opt_f64(inst.map(|i| i.funding.current)));
    d.insert(
        "funding_regime",
        opt_str(inst.map(|i| format!("{:?}", i.funding.regime))),
    );
    d.insert(
        "taker_imbalance",
        opt_f64(inst.and_then(|i| i.taker_ratio.as_ref().map(|t| t.taker_imbalance))),
    );
    d
}

fn build_trade_doc(trade: &ClosedTrade, signal_oid: ObjectId) -> Document {
    let duration_ms = trade.closed_at_ms - trade.opened_at_ms;
    let risk = (trade.entry_price - trade.stop_price.unwrap_or(trade.entry_price)).abs();
    let r_multiple = if risk > 0.0 {
        (trade.exit_price - trade.entry_price) * if trade.side == "Long" { 1.0 } else { -1.0 }
            / risk
    } else {
        0.0
    };

    let mut d = Document::new();
    d.insert("signal_id", signal_oid);
    d.insert("timestamp_ms", trade.opened_at_ms);
    d.insert("close_reason", trade.close_reason.clone());
    d.insert("close_price", trade.exit_price);
    d.insert("duration_ms", duration_ms);
    d.insert("r_multiple", r_multiple);
    d.insert("pnl_gross_usd", trade.gross_pnl);
    d.insert("pnl_net_usd", trade.net_pnl);
    d.insert("fee_entry_usd", trade.fees_paid / 2.0);
    d.insert("fee_exit_usd", trade.fees_paid / 2.0);
    d.insert("funding_cost_usd", trade.funding_paid);
    d.insert("mfe_r", if risk > 0.0 { trade.mfe / risk } else { 0.0 });
    d.insert("mae_r", if risk > 0.0 { trade.mae / risk } else { 0.0 });
    d.insert("is_partial", trade.is_partial);
    d.insert("partial_fraction", trade.partial_fraction);
    d
}

/// `Bson::Null` para `None`, conserva el tipo para `Some`. Sin esto el macro
/// `doc!` no acepta `Option<T>` directo para tipos numéricos.
fn opt_f64(v: Option<f64>) -> Bson {
    match v {
        Some(x) => Bson::Double(x),
        None => Bson::Null,
    }
}
fn opt_bool(v: Option<bool>) -> Bson {
    match v {
        Some(x) => Bson::Boolean(x),
        None => Bson::Null,
    }
}
fn opt_str(v: Option<String>) -> Bson {
    match v {
        Some(x) => Bson::String(x),
        None => Bson::Null,
    }
}
