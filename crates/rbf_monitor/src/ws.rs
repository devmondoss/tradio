use futures::{StreamExt, channel::mpsc::unbounded};
use serde::Deserialize;
use tokio_tungstenite::{connect_async, tungstenite::Message};

#[derive(Debug, Clone)]
pub enum KlineEvent {
    Connected,
    Disconnected,
    Bar { ts_ms: i64, open: f64, high: f64, low: f64, close: f64, closed: bool },
}

#[derive(Deserialize)]
struct Msg { k: K }

#[derive(Deserialize)]
struct K { t: i64, o: String, h: String, l: String, c: String, x: bool }

/// Subscription a Binance Futures kline stream (auto-reconecta).
/// stream_url: wss://fstream.binance.com/ws/btcusdt@kline_1m
pub fn connect(stream_url: String) -> iced::Subscription<KlineEvent> {
    iced::Subscription::run_with(stream_url, |url| {
        let url = url.clone();
        let (tx, rx) = unbounded::<KlineEvent>();

        tokio::spawn(async move {
            loop {
                match connect_async(url.as_str()).await {
                    Ok((ws, _)) => {
                        let _ = tx.unbounded_send(KlineEvent::Connected);
                        let (_sink, mut stream) = ws.split();

                        while let Some(msg) = stream.next().await {
                            match msg {
                                Ok(Message::Text(text)) => {
                                    if let Ok(m) = serde_json::from_str::<Msg>(&text) {
                                        let k = m.k;
                                        if let (Ok(o), Ok(h), Ok(l), Ok(c)) = (
                                            k.o.parse::<f64>(), k.h.parse::<f64>(),
                                            k.l.parse::<f64>(), k.c.parse::<f64>(),
                                        ) {
                                            let _ = tx.unbounded_send(KlineEvent::Bar {
                                                ts_ms: k.t,
                                                open: o, high: h, low: l, close: c,
                                                closed: k.x,
                                            });
                                        }
                                    }
                                }
                                Ok(Message::Close(_)) | Err(_) => break,
                                _ => {}
                            }
                        }
                    }
                    Err(e) => { eprintln!("[ws] binance: {e}"); }
                }
                let _ = tx.unbounded_send(KlineEvent::Disconnected);
                tokio::time::sleep(std::time::Duration::from_secs(3)).await;
            }
        });

        rx
    })
}
