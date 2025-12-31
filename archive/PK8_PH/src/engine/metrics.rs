use serde::Deserialize;

#[derive(Debug, Clone, Copy, Default)]
pub struct BookMetrics {
    pub best_bid: Option<f64>,
    pub best_ask: Option<f64>,
    pub mid: Option<f64>,
    pub depth_bid_top: f64,
    pub depth_ask_top: f64,
    pub last_trade_price: Option<f64>,
    pub last_trade_size: Option<f64>,
    pub last_trade_is_sell: Option<bool>,
    pub last_trade_ts_ms: Option<i64>,
}

#[derive(Debug, Deserialize)]
pub struct WsBookLevel {
    pub price: String,
    pub size: String,
}

#[derive(Debug, Deserialize)]
pub struct WsBookMsg {
    pub market: Option<String>,
    pub asset_id: String,
    pub timestamp: Option<String>,
    #[serde(alias = "buys")]
    pub bids: Option<Vec<WsBookLevel>>,
    #[serde(alias = "sells")]
    pub asks: Option<Vec<WsBookLevel>>,
    pub event_type: Option<String>,
    pub last_trade_price: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct WsPriceChangeMsg {
    pub market: Option<String>,
    pub timestamp: Option<String>,
    pub event_type: Option<String>,
    pub price_changes: Vec<WsPriceChange>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct WsPriceChange {
    pub asset_id: String,
    pub price: String,
    pub size: String,
    pub side: String,
    pub best_bid: Option<String>,
    pub best_ask: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct WsLastTradePriceMsg {
    pub asset_id: String,
    pub event_type: Option<String>,
    pub price: String,
    #[serde(default)]
    pub size: Option<String>,
    #[serde(default)]
    pub side: Option<String>,
    pub timestamp: Option<String>,
}

impl BookMetrics {
    pub fn from_msg(msg: &WsBookMsg, top_levels: usize) -> Self {
        let (best_bid, depth_bid_top) = best_and_depth(msg.bids.as_deref(), Side::Bid, top_levels);
        let (best_ask, depth_ask_top) = best_and_depth(msg.asks.as_deref(), Side::Ask, top_levels);
        let mid = match (best_bid, best_ask) {
            (Some(b), Some(a)) => Some((b + a) / 2.0),
            _ => None,
        };

        let last_trade_price = msg
            .last_trade_price
            .as_deref()
            .and_then(|s| s.parse::<f64>().ok());

        Self {
            best_bid,
            best_ask,
            mid,
            depth_bid_top,
            depth_ask_top,
            last_trade_price,
            last_trade_size: None,
            last_trade_is_sell: None,
            last_trade_ts_ms: None,
        }
    }
}

#[derive(Debug, Clone, Copy)]
enum Side {
    Bid,
    Ask,
}

fn best_and_depth(
    levels: Option<&[WsBookLevel]>,
    side: Side,
    top_levels: usize,
) -> (Option<f64>, f64) {
    let Some(levels) = levels else {
        return (None, 0.0);
    };

    let mut parsed: Vec<(f64, f64)> = levels
        .iter()
        .filter_map(|l| {
            let p = l.price.parse::<f64>().ok()?;
            let s = l.size.parse::<f64>().ok()?;
            Some((p, s))
        })
        .collect();

    match side {
        Side::Bid => {
            parsed.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal))
        }
        Side::Ask => {
            parsed.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal))
        }
    }

    let best = parsed.first().map(|(p, _)| *p);
    let depth = parsed.iter().take(top_levels).map(|(_, s)| *s).sum::<f64>();
    (best, depth)
}
