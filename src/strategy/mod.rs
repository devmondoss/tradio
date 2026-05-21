pub mod snapshot;
pub use data::strategy::*;

use data::strategy::mongo_config_loader::MongoConfigLoader;
use data::strategy::mongo_writer::MongoWriter;
use data::strategy::types::StrategyConfig;
use std::sync::OnceLock;

/// Singleton del writer + loader de Mongo. La `KlineChart` se recrea al
/// cambiar de timeframe; usar un `OnceLock` evita levantar un thread/conexión
/// nueva cada vez. La inicialización es perezosa: solo se conecta a Mongo la
/// primera vez que algo invoca [`mongo_handles`].
pub struct MongoHandles {
    pub writer: MongoWriter,
    pub loader: MongoConfigLoader,
}

static MONGO: OnceLock<MongoHandles> = OnceLock::new();

pub fn mongo_handles() -> &'static MongoHandles {
    MONGO.get_or_init(|| {
        let writer = MongoWriter::from_env();
        let base = StrategyConfig::load();
        let loader = MongoConfigLoader::from_env(base);
        MongoHandles { writer, loader }
    })
}
