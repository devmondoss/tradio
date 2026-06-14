use futures::{SinkExt, StreamExt};
/// WebSocket server — broadcast de barras y señales RBF en tiempo real.
///
/// El dashboard web se conecta a ws://[host]:9001 y recibe mensajes JSON
/// con cada barra M1 cerrada y cada señal RBF emitida.
///
/// Mensajes enviados:
///   {"type":"bar",  "ts_ms":…, "open":…, "high":…, "low":…, "close":…, "regime":"…"}
///   {"type":"signal","ts_ms":…, "direction":"Long"|"Short", "score":…, "veto":…}
use std::sync::Arc;
use tokio::net::TcpListener;
use tokio::sync::broadcast;
use tokio_tungstenite::accept_async;
use tokio_tungstenite::tungstenite::Message;

const PORT: u16 = 9001;

#[derive(Clone, serde::Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum WsEvent {
    /// Tick vivo — la vela M1 en construcción, llega ~1s
    Tick {
        ts_ms: i64,
        open: f64,
        high: f64,
        low: f64,
        close: f64,
    },
    /// Vela M1 cerrada con régimen calculado
    Bar {
        ts_ms: i64,
        open: f64,
        high: f64,
        low: f64,
        close: f64,
        regime: String,
    },
    /// Señal RBF emitida
    Signal {
        ts_ms: i64,
        direction: String,
        score: Option<u8>,
        veto: Option<String>,
        entry: f64,
        rr: f64,
        session: String,
    },
}

pub type Sender = broadcast::Sender<String>;

/// Arranca el servidor en un tokio task separado. Devuelve el Sender
/// para que el main loop pueda hacer broadcast.
pub fn start(port: u16) -> Sender {
    let (tx, _rx) = broadcast::channel::<String>(256);
    let tx_clone = tx.clone();

    tokio::spawn(async move {
        let addr = format!("0.0.0.0:{port}");
        let listener = match TcpListener::bind(&addr).await {
            Ok(l) => {
                println!("[ws_server] escuchando en {addr}");
                l
            }
            Err(e) => {
                eprintln!("[ws_server] no pudo bindear {addr}: {e}");
                return;
            }
        };

        loop {
            let (stream, peer) = match listener.accept().await {
                Ok(s) => s,
                Err(e) => {
                    eprintln!("[ws_server] accept error: {e}");
                    continue;
                }
            };

            let mut rx = tx_clone.subscribe();
            tokio::spawn(async move {
                let ws_stream = match accept_async(stream).await {
                    Ok(ws) => ws,
                    Err(e) => {
                        eprintln!("[ws_server] handshake {peer}: {e}");
                        return;
                    }
                };

                println!("[ws_server] cliente conectado: {peer}");
                let (mut sink, mut source) = ws_stream.split();

                loop {
                    tokio::select! {
                        msg = rx.recv() => {
                            match msg {
                                Ok(json) => {
                                    if sink.send(Message::Text(json.into())).await.is_err() { break; }
                                }
                                Err(broadcast::error::RecvError::Lagged(n)) => {
                                    eprintln!("[ws_server] cliente {peer} perdió {n} mensajes");
                                }
                                Err(_) => break,
                            }
                        }
                        // Mantener conexión viva / detectar desconexión
                        frame = source.next() => {
                            match frame {
                                Some(Ok(Message::Close(_))) | None => break,
                                Some(Ok(Message::Ping(d))) => {
                                    let _ = sink.send(Message::Pong(d)).await;
                                }
                                _ => {}
                            }
                        }
                    }
                }
                println!("[ws_server] cliente desconectado: {peer}");
            });
        }
    });

    tx
}

/// Serializa y envía un evento a todos los clientes conectados.
/// Fire-and-forget — falla silenciosamente si no hay clientes.
pub fn broadcast(tx: &Sender, event: WsEvent) {
    if let Ok(json) = serde_json::to_string(&event) {
        let _ = tx.send(json);
    }
}
