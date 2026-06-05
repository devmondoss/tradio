use futures::{StreamExt, channel::mpsc::unbounded};
use tokio_tungstenite::{connect_async, tungstenite::Message};

use crate::data::WsMessage;

#[derive(Debug, Clone)]
pub enum WsEvent {
    Connected,
    Disconnected,
    Message(WsMessage),
}

pub fn connect(url: String) -> iced::Subscription<WsEvent> {
    iced::Subscription::run_with(url, |url| {
        let (tx, rx) = unbounded::<WsEvent>();
        let url = url.clone();

        tokio::spawn(async move {
            loop {
                match connect_async(url.as_str()).await {
                    Ok((ws, _)) => {
                        let _ = tx.unbounded_send(WsEvent::Connected);
                        let (_sink, mut stream) = ws.split();

                        while let Some(msg) = stream.next().await {
                            match msg {
                                Ok(Message::Text(text)) => {
                                    if let Ok(ev) = serde_json::from_str::<WsMessage>(&text) {
                                        let _ = tx.unbounded_send(WsEvent::Message(ev));
                                    }
                                }
                                Ok(Message::Close(_)) | Err(_) => break,
                                _ => {}
                            }
                        }
                    }
                    Err(e) => {
                        eprintln!("[ws] connect error {url}: {e}");
                    }
                }

                let _ = tx.unbounded_send(WsEvent::Disconnected);
                tokio::time::sleep(std::time::Duration::from_secs(3)).await;
            }
        });

        rx
    })
}
