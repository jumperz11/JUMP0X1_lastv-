use anyhow::{Context, Result};
use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tokio::sync::watch;
use tokio_tungstenite::{connect_async, tungstenite::Message};

#[derive(Clone)]
pub struct LiveDataWs {
    url: String,
}

impl LiveDataWs {
    pub fn new(url: &str) -> Result<Self> {
        Ok(Self {
            url: url.to_string(),
        })
    }

    pub async fn stream_orders_matched(
        &self,
        event_slug: String,
        mut cancel: watch::Receiver<bool>,
        mut on_text: impl FnMut(String) + Send,
        mut on_rtt_ms: impl FnMut(i64) + Send,
    ) -> Result<()> {
        let (mut ws, _resp) = connect_async(self.url.as_str())
            .await
            .context("ws connect failed")?;

        // NOTE: Polymarket live-data WS expects `filters` as a JSON string.
        let subscribe = json!({
            "action": "subscribe",
            "subscriptions": [{
                "topic": "activity",
                "type": "orders_matched",
                "filters": json!({ "event_slug": event_slug }).to_string(),
            }]
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
                    if *cancel.borrow() { break; }
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

#[derive(Debug, Clone, PartialEq)]
pub struct OrdersMatchedTrade {
    pub event_slug: Option<String>,
    pub condition_id: Option<String>,
    pub token_id: Option<String>,
    pub outcome: String,
    pub price: f64,
    pub size: f64,
    pub side: String,
    pub username: Option<String>,
    pub pseudonym: Option<String>,
    pub proxy_wallet: Option<String>,
    pub ts_ms: Option<i64>,
}

pub fn parse_orders_matched_trade(v: &Value) -> Option<OrdersMatchedTrade> {
    if v.get("topic")?.as_str()? != "activity" {
        return None;
    }
    if v.get("type")?.as_str()? != "orders_matched" {
        return None;
    }

    let payload = v.get("payload")?;
    let condition_id = payload
        .get("conditionId")
        .and_then(|x| x.as_str())
        .map(|s| s.to_string());
    let token_id = payload
        .get("asset")
        .or_else(|| payload.get("asset_id"))
        .or_else(|| payload.get("assetId"))
        .and_then(|x| x.as_str())
        .map(|s| s.to_string());
    let outcome = payload.get("outcome")?.as_str()?.to_string();
    let side = payload.get("side")?.as_str()?.to_string();
    let price = payload
        .get("price")
        .and_then(|x| x.as_f64())
        .or_else(|| payload.get("price")?.as_str()?.parse::<f64>().ok())?;
    let size = payload
        .get("size")
        .and_then(|x| x.as_f64())
        .or_else(|| payload.get("size")?.as_str()?.parse::<f64>().ok())?;

    let event_slug = payload
        .get("eventSlug")
        .or_else(|| payload.get("event_slug"))
        .and_then(|x| x.as_str())
        .map(|s| s.to_string())
        .or_else(|| payload.get("slug").and_then(|x| x.as_str()).map(|s| s.to_string()));

    let ts_ms = v
        .get("timestamp")
        .and_then(extract_ts_ms)
        .or_else(|| payload.get("timestamp").and_then(extract_ts_ms));

    Some(OrdersMatchedTrade {
        event_slug,
        condition_id,
        token_id,
        outcome,
        price,
        size,
        side,
        username: payload
            .get("name")
            .and_then(|x| x.as_str())
            .and_then(|s| if s.trim().is_empty() { None } else { Some(s) })
            .map(|s| s.to_string()),
        pseudonym: payload
            .get("pseudonym")
            .and_then(|x| x.as_str())
            .and_then(|s| if s.trim().is_empty() { None } else { Some(s) })
            .map(|s| s.to_string()),
        proxy_wallet: payload
            .get("proxyWallet")
            .and_then(|x| x.as_str())
            .map(|s| s.to_string()),
        ts_ms,
    })
}

fn extract_ts_ms(v: &Value) -> Option<i64> {
    let raw = v
        .as_i64()
        .or_else(|| v.as_u64().and_then(|u| i64::try_from(u).ok()))
        .or_else(|| v.as_str().and_then(|s| s.parse::<i64>().ok()))?;

    // Heuristic: if it's already in ms (~>= 2001-09-09 in ms), keep; otherwise interpret seconds.
    if raw >= 1_000_000_000_000 {
        Some(raw)
    } else if raw >= 1_000_000_000 {
        Some(raw.saturating_mul(1000))
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_orders_matched_example() {
        let msg = r#"{
          "connection_id":"VxVYOejSLPECEtQ=",
          "payload":{
            "asset":"112313203800236648856331212946197233566221210028691505424844865917309418995862",
            "conditionId":"0x15b1225e863600684b5e8845490881dbf43c2201bf6f7844f7caaf9415e6ef01",
            "eventSlug":"eth-updown-15m-1766038500",
            "pseudonym":"ExpressoMartini",
            "name":"gabagool22",
            "proxyWallet":"0x29bc82f761749E67fa00D62896bC6855097b683C",
            "outcome":"Up",
            "price":0.49,
            "side":"BUY",
            "size":50,
            "timestamp":1766038581
          },
          "timestamp":1766038581067,
          "topic":"activity",
          "type":"orders_matched"
        }"#;

        let v: Value = serde_json::from_str(msg).unwrap();
        let trade = parse_orders_matched_trade(&v).unwrap();
        assert_eq!(trade.event_slug.as_deref(), Some("eth-updown-15m-1766038500"));
        assert_eq!(
            trade.token_id.as_deref(),
            Some("112313203800236648856331212946197233566221210028691505424844865917309418995862")
        );
        assert_eq!(
            trade.condition_id.as_deref(),
            Some("0x15b1225e863600684b5e8845490881dbf43c2201bf6f7844f7caaf9415e6ef01")
        );
        assert_eq!(trade.outcome, "Up");
        assert_eq!(trade.side, "BUY");
        assert!((trade.price - 0.49).abs() < 1e-9);
        assert!((trade.size - 50.0).abs() < 1e-9);
        assert_eq!(trade.username.as_deref(), Some("gabagool22"));
        assert_eq!(trade.pseudonym.as_deref(), Some("ExpressoMartini"));
        assert_eq!(
            trade.proxy_wallet.as_deref(),
            Some("0x29bc82f761749E67fa00D62896bC6855097b683C")
        );
        assert_eq!(trade.ts_ms, Some(1766038581067));
    }
}
