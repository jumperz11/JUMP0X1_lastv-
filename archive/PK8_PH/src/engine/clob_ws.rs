use anyhow::{Context, Result};
use futures_util::{SinkExt, StreamExt};
use serde_json::json;
use tokio::sync::watch;
use tokio_tungstenite::{connect_async, tungstenite::Message};

#[derive(Clone)]
pub struct MarketWs {
    url: String,
}

impl MarketWs {
    pub fn new(url: &str) -> Result<Self> {
        Ok(Self {
            url: url.to_string(),
        })
    }

    pub async fn stream_market(
        &self,
        token_ids: Vec<String>,
        mut cancel: watch::Receiver<bool>,
        mut on_text: impl FnMut(String) + Send,
        mut on_rtt_ms: impl FnMut(i64) + Send,
    ) -> Result<()> {
        let (mut ws, _resp) = connect_async(self.url.as_str())
            .await
            .context("ws connect failed")?;

        let subscribe = json!({
            "assets_ids": token_ids,
            "type": "market",
        })
        .to_string();
        ws.send(Message::Text(subscribe.into()))
            .await
            .context("ws subscribe send failed")?;

        let mut ping = tokio::time::interval(std::time::Duration::from_secs(10));
        let mut last_ping = None::<std::time::Instant>;

        loop {
            tokio::select! {
                _ = cancel.changed() => {
                    if *cancel.borrow() {
                        break;
                    }
                }
                _ = ping.tick() => {
                    last_ping = Some(std::time::Instant::now());
                    ws.send(Message::Text("PING".into())).await.context("ws ping send failed")?;
                }
                msg = ws.next() => {
                    let Some(msg) = msg else { break };
                    let msg = msg.context("ws read failed")?;
                    match msg {
                        Message::Text(text) => {
                            if text.as_str() == "PONG" {
                                if let Some(t0) = last_ping.take() {
                                    on_rtt_ms(t0.elapsed().as_millis() as i64);
                                }
                            } else {
                                on_text(text.to_string());
                            }
                        }
                        Message::Binary(bin) => on_text(String::from_utf8_lossy(&bin).to_string()),
                        Message::Ping(_) => {}
                        Message::Pong(_) => {
                            if let Some(t0) = last_ping.take() {
                                on_rtt_ms(t0.elapsed().as_millis() as i64);
                            }
                        }
                        Message::Close(_) => break,
                        Message::Frame(_) => {}
                    }
                }
            }
        }

        Ok(())
    }
}
