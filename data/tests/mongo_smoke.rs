//! Smoke test contra una instancia local de MongoDB.
//!
//! Marcado `#[ignore]` para que `cargo test` por defecto no lo corra (requiere
//! el `mongod` dedicado en localhost:27018 — arrancar con `start-mongo.bat`).
//! Ejecutar a mano con:
//!
//! ```
//! cargo test -p flowsurface-data --test mongo_smoke -- --ignored --nocapture
//! ```
//!
//! Verifica:
//!   1. El writer se conecta.
//!   2. Una inserción a `flowsurface.smoke_test` aparece en la colección.
//!   3. La conversión Document↔BSON funciona round-trip.

use mongodb::Client;
use mongodb::bson::{Document, doc, oid::ObjectId};

const URI: &str = "mongodb://localhost:27018";
const DB: &str = "flowsurface";
const COLL: &str = "smoke_test";

#[tokio::test(flavor = "current_thread")]
#[ignore]
async fn writer_inserts_to_local_mongo() {
    let client = Client::with_uri_str(URI)
        .await
        .expect("conectar a localhost:27018 (¿corriste start-mongo.bat?)");
    let coll = client.database(DB).collection::<Document>(COLL);

    let oid = ObjectId::new();
    let marker = format!("smoke-{}", oid);
    let ts_ms = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0);
    let doc = doc! {
        "_id": oid,
        "marker": &marker,
        "ts": ts_ms,
    };

    coll.insert_one(doc)
        .await
        .expect("insert_one debe tener éxito contra Mongo local");

    let found = coll
        .find_one(doc! { "_id": oid })
        .await
        .expect("find_one no debe fallar")
        .expect("el documento recién insertado debe existir");

    assert_eq!(found.get_str("marker").ok(), Some(marker.as_str()));

    // Limpieza — borrar el documento de smoke para no acumular basura.
    coll.delete_one(doc! { "_id": oid })
        .await
        .expect("delete_one debe limpiar el doc");
}

/// End-to-end: el `MongoConfigLoader` debe recibir el override que el script
/// Python `seed_deployed_params.py` acaba de insertar en `flowsurface.deployed_params`.
///
/// Pre-requisito: haber corrido `python scripts/seed_deployed_params.py` antes.
/// El seed conservador escribe `min_rr=1.8`, `min_score=0.70` (los defaults son 1.5 / 0.60),
/// así que el override es detectable trivialmente.
#[test]
#[ignore]
fn loader_reads_seeded_params_for_trendup() {
    use flowsurface_data::strategy::mongo_config_loader::MongoConfigLoader;
    use flowsurface_data::strategy::types::StrategyConfig;
    use std::thread::sleep;
    use std::time::Duration;

    let base = StrategyConfig::default();
    assert_eq!(base.min_rr, 1.5, "default min_rr cambió, ajustar el test");
    assert_eq!(
        base.min_score, 0.60,
        "default min_score cambió, ajustar el test"
    );

    let loader = MongoConfigLoader::spawn(
        "mongodb://localhost:27018".into(),
        "flowsurface".into(),
        base.clone(),
    );
    loader.request_reload("TrendUp");

    // Polling corto: el loader publica vía RwLock asíncronamente.
    let mut applied = base.clone();
    for _ in 0..30 {
        sleep(Duration::from_millis(100));
        applied = loader.current();
        if (applied.min_rr - 1.8).abs() < 1e-9 {
            break;
        }
    }

    assert!(
        (applied.min_rr - 1.8).abs() < 1e-9,
        "loader no aplicó el override min_rr=1.8 (sigue en {}). \
         ¿Corriste `python scripts/seed_deployed_params.py`?",
        applied.min_rr
    );
    assert!(
        (applied.min_score - 0.70).abs() < 1e-9,
        "loader no aplicó el override min_score=0.70 (sigue en {})",
        applied.min_score
    );
    // Campos NO presentes en el seed deben conservar el default.
    assert_eq!(applied.max_vpin, base.max_vpin);
}

/// End-to-end "no broken pipes": ejerce exactamente la ruta que la UI usa en
/// `kline.rs::run_strategy_detection` cuando dispara una señal y luego se
/// cierra el trade. Si este test pasa, el writer está sano:
///   1. `MongoWriter::write_signal` inserta a `shadow_signals` con el oid generado.
///   2. `MongoWriter::write_trade` inserta a `signal_outcomes` con FK al oid.
///   3. Ambos documentos están en Mongo con la forma correcta y se enlazan vía
///      `signal_outcomes.signal_id == shadow_signals._id`.
///
/// Requiere `mongod` activo. Limpia los docs `_test_e2e: true` al terminar.
#[tokio::test(flavor = "current_thread")]
#[ignore]
async fn end_to_end_signal_and_trade_pipeline() {
    use flowsurface_data::strategy::mongo_writer::MongoWriter;
    use flowsurface_data::strategy::paper::ClosedTrade;
    use flowsurface_data::strategy::types::{StrategyMarketContext, StrategySignal};
    use mongodb::Client;
    use mongodb::bson::{Document, doc};
    use serde_json::json;
    use std::thread::sleep;
    use std::time::Duration;

    // Helper: limpieza de docs marcados como sintéticos
    async fn cleanup(coll: &mongodb::Collection<Document>, filter: Document) {
        let _ = coll.delete_many(filter).await;
    }

    // ── 1. Build inputs vía JSON (más conciso que struct literals) ─────────
    let ctx_json = json!({
        "symbol": "BTCUSDT_E2E_TEST",
        "timestamp_ms": 1747700000000_i64,
        "price": 95000.0,
        "regime": "TrendUp",
        "atr": 250.0,
        "volume_profile": {
            "poc": 94800.0, "vah": 95200.0, "val": 94500.0,
            "hvn_nearby": [94900.0, 95100.0], "lvn_nearby": [],
            "value_location": "InValue", "quality": "Live"
        },
        "vwap": {
            "vwap_session": 94900.0, "avwap_bos": null, "avwap_event": null,
            "price_vs_vwap": "Above", "price_vs_avwap_bos": "Unknown",
            "price_vs_avwap_event": "Unknown", "quality": "Live"
        },
        "flow": {
            "cvd": 12345.0, "cvd_slope": 0.42, "delta": 100.0,
            "taker_imbalance": 0.15, "buy_volume": 600.0, "sell_volume": 400.0,
            "vpin": 0.3, "cvd_divergence": null,
            "footprint_absorption": "None", "stacked_imbalance": "Unknown",
            "failed_acceptance": false, "sweep_confirmed": false, "mss_active": false,
            "quality": "Live", "funding_rate": 0.0001, "basis": 0.05,
            "oi_delta": 100.0, "oi_momentum_aligned": true,
            "bid_wall_nearby": false, "ask_wall_nearby": false,
            "price_action_clean": true, "fast_slope": 0.31
        },
        "orderbook": {
            "obi_l5": 0.1, "obi_l10": 0.08, "obi_l20": 0.05,
            "microprice": 95001.0, "spread_bps": 1.2,
            "walls_above": [95500.0], "walls_below": [94500.0],
            "thin_zone_above": false, "thin_zone_below": false, "quality": "Live"
        },
        "institutional": null,
        "market_structure": null,
        "session": null,
        "order_blocks": null,
        "fvg": null,
        "leverage": 1.0,
        "prev_obi_l5": null,
    });
    let signal_json = json!({
        "action": "ShadowSignal",
        "strategy_id": "VwapValuePullbackContinuation",
        "side": "Long",
        "regime": "TrendUp",
        "entry_price": 95000.0,
        "stop_price": 94800.0,
        "target_price": 95400.0,
        "score": 0.78,
        "ttl_ms": 1_500_000,
        "evidence": [],
        "missing": [],
        "invalidation": [],
        "created_at_ms": 1747700000000_i64,
    });
    let trade_json = json!({
        "id": 1,
        "symbol": "BTCUSDT_E2E_TEST",
        "strategy_id": "VwapValuePullbackContinuation",
        "side": "Long",
        "entry_price": 95000.0,
        "intended_entry": 95000.0,
        "exit_price": 95400.0,
        "stop_price": 94800.0,
        "target_price": 95400.0,
        "size": 0.01,
        "notional": 950.0,
        "score": 0.78,
        "opened_at_ms": 1747700000000_i64,
        "closed_at_ms": 1747700600000_i64,
        "ttl_ms": 1_500_000,
        "close_reason": "TARGET_HIT",
        "gross_pnl": 4.0,
        "fees_paid": 0.08,
        "funding_paid": 0.0,
        "net_pnl": 3.92,
        "net_pnl_pct": 0.41,
        "mfe": 4.5,
        "mae": -1.0,
    });
    let ctx: StrategyMarketContext = serde_json::from_value(ctx_json).expect("ctx parse");
    let signal: StrategySignal = serde_json::from_value(signal_json).expect("signal parse");
    let trade: ClosedTrade = serde_json::from_value(trade_json).expect("trade parse");

    // ── 2. Spawn writer y ejecutar la ruta exacta de la UI ─────────────────
    let writer = MongoWriter::spawn("mongodb://localhost:27018".into(), "flowsurface".into());
    let oid = writer
        .write_signal(&signal, &ctx)
        .expect("write_signal debe retornar oid (action!=Wait)");
    writer.write_trade(&trade, Some(oid));

    // ── 3. Polling: el writer es async, esperar a que ambos docs aparezcan ─
    let client = Client::with_uri_str("mongodb://localhost:27018")
        .await
        .expect("conectar a Mongo para verificar");
    let db = client.database("flowsurface");
    let signals = db.collection::<Document>("shadow_signals");
    let outcomes = db.collection::<Document>("signal_outcomes");

    let mut sig_doc: Option<Document> = None;
    let mut out_doc: Option<Document> = None;
    for _ in 0..40 {
        sleep(Duration::from_millis(100));
        sig_doc = signals
            .find_one(doc! { "_id": oid })
            .await
            .expect("find shadow_signals");
        out_doc = outcomes
            .find_one(doc! { "signal_id": oid })
            .await
            .expect("find signal_outcomes");
        if sig_doc.is_some() && out_doc.is_some() {
            break;
        }
    }

    // ── 4. Aserciones de shape ─────────────────────────────────────────────
    let sig_doc = sig_doc.expect("shadow_signals doc no apareció en 4s");
    let out_doc = out_doc.expect("signal_outcomes doc no apareció en 4s");

    assert_eq!(sig_doc.get_str("symbol").ok(), Some("BTCUSDT_E2E_TEST"));
    assert_eq!(
        sig_doc.get_str("strategy").ok(),
        Some("VwapValuePullbackContinuation")
    );
    assert_eq!(sig_doc.get_str("side").ok(), Some("Long"));
    assert_eq!(sig_doc.get_str("regime_combined").ok(), Some("TrendUp"));
    assert_eq!(sig_doc.get_str("action").ok(), Some("ShadowSignal"));
    assert!((sig_doc.get_f64("score").unwrap() - 0.78).abs() < 1e-9);
    assert!((sig_doc.get_f64("entry_price").unwrap() - 95000.0).abs() < 1e-9);

    // FK: signal_outcomes.signal_id == shadow_signals._id
    assert_eq!(
        out_doc.get_object_id("signal_id").ok(),
        Some(oid),
        "FK rota: signal_outcomes.signal_id no coincide con shadow_signals._id"
    );
    assert_eq!(out_doc.get_str("close_reason").ok(), Some("TARGET_HIT"));
    assert!((out_doc.get_f64("pnl_net_usd").unwrap() - 3.92).abs() < 1e-9);
    let r_mult = out_doc.get_f64("r_multiple").unwrap();
    assert!(
        r_mult > 1.9 && r_mult < 2.1,
        "r_multiple esperado ~2.0, fue {r_mult}"
    );

    // ── 5. Cleanup ────────────────────────────────────────────────────────
    cleanup(&signals, doc! { "_id": oid }).await;
    cleanup(&outcomes, doc! { "signal_id": oid }).await;
}
