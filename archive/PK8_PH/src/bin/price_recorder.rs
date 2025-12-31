//! Multi-Source Price Recorder
//!
//! Records price ticks from ALL sources to find lead/lag relationships.
//! Goal: Find which source LEADS Chainlink to predict 15m settlement.
//!
//! Sources:
//!   - Binance Spot (websocket)
//!   - Binance Futures (websocket)
//!   - Coinbase (websocket)
//!   - Bybit (websocket)
//!   - Kraken (websocket)
//!   - OKX (websocket)
//!   - Chainlink RTDS (websocket via Polymarket)
//!   - Pyth Network (websocket)

use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use futures_util::{SinkExt, StreamExt};
use serde::{Deserialize, Serialize};
use std::fs::{File, OpenOptions};
use std::io::{BufWriter, Write};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};
use tokio::sync::mpsc;
use tokio_tungstenite::{connect_async, tungstenite::Message};

/// Price tick from any source
#[derive(Debug, Clone, Serialize)]
struct PriceTick {
    /// Microseconds since epoch (for precision)
    ts_us: i64,
    /// ISO timestamp for readability
    ts_iso: String,
    /// Source name: binance_spot, binance_futures, coinbase, bybit, chainlink
    source: String,
    /// Symbol (BTC, ETH)
    symbol: String,
    /// Price in USD
    price: f64,
    /// Volume (if available)
    volume: Option<f64>,
    /// Trade ID from exchange (for dedup)
    trade_id: Option<String>,
}

/// Binance spot trade message
#[derive(Debug, Deserialize)]
struct BinanceTrade {
    #[serde(rename = "e")]
    event_type: String,
    #[serde(rename = "E")]
    event_time: i64,
    #[serde(rename = "s")]
    symbol: String,
    #[serde(rename = "t")]
    trade_id: i64,
    #[serde(rename = "p")]
    price: String,
    #[serde(rename = "q")]
    quantity: String,
    #[serde(rename = "T")]
    trade_time: i64,
    #[serde(rename = "m")]
    is_buyer_maker: bool,
}

/// Binance futures trade message (aggTrade)
#[derive(Debug, Deserialize)]
struct BinanceFuturesTrade {
    #[serde(rename = "e")]
    event_type: String,
    #[serde(rename = "E")]
    event_time: i64,
    #[serde(rename = "s")]
    symbol: String,
    #[serde(rename = "a")]
    agg_trade_id: i64,
    #[serde(rename = "p")]
    price: String,
    #[serde(rename = "q")]
    quantity: String,
    #[serde(rename = "T")]
    trade_time: i64,
}

/// Coinbase trade message
#[derive(Debug, Deserialize)]
struct CoinbaseTrade {
    #[serde(rename = "type")]
    msg_type: String,
    trade_id: Option<i64>,
    product_id: Option<String>,
    price: Option<String>,
    size: Option<String>,
    time: Option<String>,
}

/// Bybit trade message
#[derive(Debug, Deserialize)]
struct BybitMessage {
    topic: Option<String>,
    data: Option<Vec<BybitTrade>>,
}

#[derive(Debug, Deserialize)]
struct BybitTrade {
    #[serde(rename = "T")]
    timestamp: i64,
    #[serde(rename = "s")]
    symbol: String,
    #[serde(rename = "p")]
    price: String,
    #[serde(rename = "v")]
    volume: String,
    #[serde(rename = "i")]
    trade_id: String,
}

fn now_micros() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_micros() as i64
}

fn now_iso() -> String {
    Utc::now().format("%Y-%m-%dT%H:%M:%S%.6fZ").to_string()
}

async fn connect_binance_spot(tx: mpsc::Sender<PriceTick>, symbol: &str) -> Result<()> {
    let symbol_lower = symbol.to_lowercase();
    let url = format!("wss://stream.binance.com:9443/ws/{}usdt@trade", symbol_lower);

    println!("[BINANCE_SPOT] Connecting to {}...", url);

    loop {
        match connect_async(&url).await {
            Ok((ws_stream, _)) => {
                println!("[BINANCE_SPOT] Connected!");
                let (_, mut read) = ws_stream.split();

                while let Some(msg) = read.next().await {
                    match msg {
                        Ok(Message::Text(text)) => {
                            if let Ok(trade) = serde_json::from_str::<BinanceTrade>(&text) {
                                if let Ok(price) = trade.price.parse::<f64>() {
                                    let tick = PriceTick {
                                        ts_us: now_micros(),
                                        ts_iso: now_iso(),
                                        source: "binance_spot".to_string(),
                                        symbol: symbol.to_string(),
                                        price,
                                        volume: trade.quantity.parse().ok(),
                                        trade_id: Some(trade.trade_id.to_string()),
                                    };
                                    let _ = tx.send(tick).await;
                                }
                            }
                        }
                        Ok(Message::Ping(data)) => {
                            // Ping handled automatically by tungstenite
                        }
                        Err(e) => {
                            eprintln!("[BINANCE_SPOT] Error: {}", e);
                            break;
                        }
                        _ => {}
                    }
                }
            }
            Err(e) => {
                eprintln!("[BINANCE_SPOT] Connection failed: {}", e);
            }
        }

        println!("[BINANCE_SPOT] Reconnecting in 5s...");
        tokio::time::sleep(tokio::time::Duration::from_secs(5)).await;
    }
}

async fn connect_binance_futures(tx: mpsc::Sender<PriceTick>, symbol: &str) -> Result<()> {
    let symbol_lower = symbol.to_lowercase();
    let url = format!("wss://fstream.binance.com/ws/{}usdt@aggTrade", symbol_lower);

    println!("[BINANCE_FUTURES] Connecting to {}...", url);

    loop {
        match connect_async(&url).await {
            Ok((ws_stream, _)) => {
                println!("[BINANCE_FUTURES] Connected!");
                let (_, mut read) = ws_stream.split();

                while let Some(msg) = read.next().await {
                    match msg {
                        Ok(Message::Text(text)) => {
                            if let Ok(trade) = serde_json::from_str::<BinanceFuturesTrade>(&text) {
                                if let Ok(price) = trade.price.parse::<f64>() {
                                    let tick = PriceTick {
                                        ts_us: now_micros(),
                                        ts_iso: now_iso(),
                                        source: "binance_futures".to_string(),
                                        symbol: symbol.to_string(),
                                        price,
                                        volume: trade.quantity.parse().ok(),
                                        trade_id: Some(trade.agg_trade_id.to_string()),
                                    };
                                    let _ = tx.send(tick).await;
                                }
                            }
                        }
                        Err(e) => {
                            eprintln!("[BINANCE_FUTURES] Error: {}", e);
                            break;
                        }
                        _ => {}
                    }
                }
            }
            Err(e) => {
                eprintln!("[BINANCE_FUTURES] Connection failed: {}", e);
            }
        }

        println!("[BINANCE_FUTURES] Reconnecting in 5s...");
        tokio::time::sleep(tokio::time::Duration::from_secs(5)).await;
    }
}

async fn connect_coinbase(tx: mpsc::Sender<PriceTick>, symbol: &str) -> Result<()> {
    let url = "wss://ws-feed.exchange.coinbase.com";
    let product = format!("{}-USD", symbol);

    println!("[COINBASE] Connecting to {}...", url);

    loop {
        match connect_async(url).await {
            Ok((ws_stream, _)) => {
                println!("[COINBASE] Connected! Subscribing to {}...", product);
                let (mut write, mut read) = ws_stream.split();

                // Subscribe to trades
                let subscribe = serde_json::json!({
                    "type": "subscribe",
                    "product_ids": [&product],
                    "channels": ["matches"]
                });

                if let Err(e) = write.send(Message::Text(subscribe.to_string().into())).await {
                    eprintln!("[COINBASE] Subscribe failed: {}", e);
                    continue;
                }

                while let Some(msg) = read.next().await {
                    match msg {
                        Ok(Message::Text(text)) => {
                            if let Ok(trade) = serde_json::from_str::<CoinbaseTrade>(&text) {
                                if trade.msg_type == "match" || trade.msg_type == "last_match" {
                                    if let (Some(price_str), Some(size_str)) = (&trade.price, &trade.size) {
                                        if let Ok(price) = price_str.parse::<f64>() {
                                            let tick = PriceTick {
                                                ts_us: now_micros(),
                                                ts_iso: now_iso(),
                                                source: "coinbase".to_string(),
                                                symbol: symbol.to_string(),
                                                price,
                                                volume: size_str.parse().ok(),
                                                trade_id: trade.trade_id.map(|id| id.to_string()),
                                            };
                                            let _ = tx.send(tick).await;
                                        }
                                    }
                                }
                            }
                        }
                        Err(e) => {
                            eprintln!("[COINBASE] Error: {}", e);
                            break;
                        }
                        _ => {}
                    }
                }
            }
            Err(e) => {
                eprintln!("[COINBASE] Connection failed: {}", e);
            }
        }

        println!("[COINBASE] Reconnecting in 5s...");
        tokio::time::sleep(tokio::time::Duration::from_secs(5)).await;
    }
}

async fn connect_bybit(tx: mpsc::Sender<PriceTick>, symbol: &str) -> Result<()> {
    let url = "wss://stream.bybit.com/v5/public/spot";
    let topic = format!("publicTrade.{}USDT", symbol);

    println!("[BYBIT] Connecting to {}...", url);

    loop {
        match connect_async(url).await {
            Ok((ws_stream, _)) => {
                println!("[BYBIT] Connected! Subscribing to {}...", topic);
                let (mut write, mut read) = ws_stream.split();

                // Subscribe to trades
                let subscribe = serde_json::json!({
                    "op": "subscribe",
                    "args": [&topic]
                });

                if let Err(e) = write.send(Message::Text(subscribe.to_string().into())).await {
                    eprintln!("[BYBIT] Subscribe failed: {}", e);
                    continue;
                }

                while let Some(msg) = read.next().await {
                    match msg {
                        Ok(Message::Text(text)) => {
                            if let Ok(msg) = serde_json::from_str::<BybitMessage>(&text) {
                                if let Some(trades) = msg.data {
                                    for trade in trades {
                                        if let Ok(price) = trade.price.parse::<f64>() {
                                            let tick = PriceTick {
                                                ts_us: now_micros(),
                                                ts_iso: now_iso(),
                                                source: "bybit".to_string(),
                                                symbol: symbol.to_string(),
                                                price,
                                                volume: trade.volume.parse().ok(),
                                                trade_id: Some(trade.trade_id),
                                            };
                                            let _ = tx.send(tick).await;
                                        }
                                    }
                                }
                            }
                        }
                        Err(e) => {
                            eprintln!("[BYBIT] Error: {}", e);
                            break;
                        }
                        _ => {}
                    }
                }
            }
            Err(e) => {
                eprintln!("[BYBIT] Connection failed: {}", e);
            }
        }

        println!("[BYBIT] Reconnecting in 5s...");
        tokio::time::sleep(tokio::time::Duration::from_secs(5)).await;
    }
}

async fn connect_kraken(tx: mpsc::Sender<PriceTick>, _symbol: &str) -> Result<()> {
    let url = "wss://ws.kraken.com";

    println!("[KRAKEN] Connecting to {}...", url);

    loop {
        match connect_async(url).await {
            Ok((ws_stream, _)) => {
                println!("[KRAKEN] Connected!");
                let (mut write, mut read) = ws_stream.split();

                let subscribe = serde_json::json!({
                    "event": "subscribe",
                    "pair": ["XBT/USD"],
                    "subscription": {"name": "trade"}
                });

                if let Err(e) = write.send(Message::Text(subscribe.to_string().into())).await {
                    eprintln!("[KRAKEN] Subscribe failed: {}", e);
                    continue;
                }

                while let Some(msg) = read.next().await {
                    match msg {
                        Ok(Message::Text(text)) => {
                            if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                                if let Some(arr) = v.as_array() {
                                    if arr.len() >= 2 {
                                        if let Some(trades) = arr.get(1).and_then(|t| t.as_array()) {
                                            for trade in trades {
                                                if let Some(trade_arr) = trade.as_array() {
                                                    if let Some(p) = trade_arr.first().and_then(|x| x.as_str()) {
                                                        if let Ok(price) = p.parse::<f64>() {
                                                            let tick = PriceTick {
                                                                ts_us: now_micros(),
                                                                ts_iso: now_iso(),
                                                                source: "kraken".to_string(),
                                                                symbol: "BTC".to_string(),
                                                                price,
                                                                volume: trade_arr.get(1).and_then(|v| v.as_str()).and_then(|s| s.parse().ok()),
                                                                trade_id: None,
                                                            };
                                                            let _ = tx.send(tick).await;
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        Err(e) => {
                            eprintln!("[KRAKEN] Error: {}", e);
                            break;
                        }
                        _ => {}
                    }
                }
            }
            Err(e) => {
                eprintln!("[KRAKEN] Connection failed: {}", e);
            }
        }

        println!("[KRAKEN] Reconnecting in 5s...");
        tokio::time::sleep(tokio::time::Duration::from_secs(5)).await;
    }
}

async fn connect_okx(tx: mpsc::Sender<PriceTick>, symbol: &str) -> Result<()> {
    let url = "wss://ws.okx.com:8443/ws/v5/public";
    let inst_id = format!("{}-USDT", symbol);

    println!("[OKX] Connecting to {}...", url);

    loop {
        match connect_async(url).await {
            Ok((ws_stream, _)) => {
                println!("[OKX] Connected! Subscribing to {}...", inst_id);
                let (mut write, mut read) = ws_stream.split();

                let subscribe = serde_json::json!({
                    "op": "subscribe",
                    "args": [{"channel": "trades", "instId": &inst_id}]
                });

                if let Err(e) = write.send(Message::Text(subscribe.to_string().into())).await {
                    eprintln!("[OKX] Subscribe failed: {}", e);
                    continue;
                }

                while let Some(msg) = read.next().await {
                    match msg {
                        Ok(Message::Text(text)) => {
                            if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                                if let Some(data) = v.get("data").and_then(|d| d.as_array()) {
                                    for trade in data {
                                        if let Some(p) = trade.get("px").and_then(|x| x.as_str()) {
                                            if let Ok(price) = p.parse::<f64>() {
                                                let tick = PriceTick {
                                                    ts_us: now_micros(),
                                                    ts_iso: now_iso(),
                                                    source: "okx".to_string(),
                                                    symbol: symbol.to_string(),
                                                    price,
                                                    volume: trade.get("sz").and_then(|s| s.as_str()).and_then(|s| s.parse().ok()),
                                                    trade_id: trade.get("tradeId").and_then(|t| t.as_str()).map(|s| s.to_string()),
                                                };
                                                let _ = tx.send(tick).await;
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        Err(e) => {
                            eprintln!("[OKX] Error: {}", e);
                            break;
                        }
                        _ => {}
                    }
                }
            }
            Err(e) => {
                eprintln!("[OKX] Connection failed: {}", e);
            }
        }

        println!("[OKX] Reconnecting in 5s...");
        tokio::time::sleep(tokio::time::Duration::from_secs(5)).await;
    }
}

async fn connect_chainlink(tx: mpsc::Sender<PriceTick>, symbol: &str) -> Result<()> {
    let url = "wss://ws-live-data.polymarket.com";
    let symbol_filter = format!("{{\"symbol\":\"{}/usd\"}}", symbol.to_lowercase());

    println!("[CHAINLINK] Connecting to {}...", url);

    loop {
        match connect_async(url).await {
            Ok((ws_stream, _)) => {
                println!("[CHAINLINK] Connected!");
                let (mut write, mut read) = ws_stream.split();

                let subscribe = serde_json::json!({
                    "action": "subscribe",
                    "subscriptions": [{
                        "topic": "crypto_prices_chainlink",
                        "type": "*",
                        "filters": symbol_filter
                    }]
                });

                if let Err(e) = write.send(Message::Text(subscribe.to_string().into())).await {
                    eprintln!("[CHAINLINK] Subscribe failed: {}", e);
                    continue;
                }

                // Spawn keep-alive pinger
                let mut ping_write = write;
                tokio::spawn(async move {
                    loop {
                        tokio::time::sleep(tokio::time::Duration::from_secs(5)).await;
                        if ping_write.send(Message::Ping(vec![].into())).await.is_err() {
                            break;
                        }
                    }
                });

                while let Some(msg) = read.next().await {
                    match msg {
                        Ok(Message::Text(text)) => {
                            if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                                if let Some(payload) = v.get("payload") {
                                    if let Some(value) = payload.get("value").and_then(|v| v.as_f64()) {
                                        let tick = PriceTick {
                                            ts_us: now_micros(),
                                            ts_iso: now_iso(),
                                            source: "chainlink".to_string(),
                                            symbol: symbol.to_string(),
                                            price: value,
                                            volume: None,
                                            trade_id: None,
                                        };
                                        let _ = tx.send(tick).await;
                                    }
                                }
                            }
                        }
                        Err(e) => {
                            eprintln!("[CHAINLINK] Error: {}", e);
                            break;
                        }
                        _ => {}
                    }
                }
            }
            Err(e) => {
                eprintln!("[CHAINLINK] Connection failed: {}", e);
            }
        }

        println!("[CHAINLINK] Reconnecting in 5s...");
        tokio::time::sleep(tokio::time::Duration::from_secs(5)).await;
    }
}

async fn connect_pyth(tx: mpsc::Sender<PriceTick>, _symbol: &str) -> Result<()> {
    let btc_feed = "0xe62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43";
    let url = "wss://hermes.pyth.network/ws";

    println!("[PYTH] Connecting to {}...", url);

    loop {
        match connect_async(url).await {
            Ok((ws_stream, _)) => {
                println!("[PYTH] Connected!");
                let (mut write, mut read) = ws_stream.split();

                let subscribe = serde_json::json!({
                    "type": "subscribe",
                    "ids": [btc_feed]
                });

                if let Err(e) = write.send(Message::Text(subscribe.to_string().into())).await {
                    eprintln!("[PYTH] Subscribe failed: {}", e);
                    continue;
                }

                while let Some(msg) = read.next().await {
                    match msg {
                        Ok(Message::Text(text)) => {
                            if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                                // Try price_feed format
                                if let Some(pf) = v.get("price_feed") {
                                    if let Some(price_obj) = pf.get("price") {
                                        if let (Some(price_str), Some(expo)) = (
                                            price_obj.get("price").and_then(|p| p.as_str()),
                                            price_obj.get("expo").and_then(|e| e.as_i64()),
                                        ) {
                                            if let Ok(price_raw) = price_str.parse::<f64>() {
                                                let price = price_raw * 10_f64.powi(expo as i32);
                                                let tick = PriceTick {
                                                    ts_us: now_micros(),
                                                    ts_iso: now_iso(),
                                                    source: "pyth".to_string(),
                                                    symbol: "BTC".to_string(),
                                                    price,
                                                    volume: None,
                                                    trade_id: None,
                                                };
                                                let _ = tx.send(tick).await;
                                            }
                                        }
                                    }
                                }
                                // Also try parsed array format
                                if let Some(parsed) = v.get("parsed").and_then(|p| p.as_array()) {
                                    for item in parsed {
                                        if let Some(price_obj) = item.get("price") {
                                            if let (Some(price_str), Some(expo)) = (
                                                price_obj.get("price").and_then(|p| p.as_str()),
                                                price_obj.get("expo").and_then(|e| e.as_i64()),
                                            ) {
                                                if let Ok(price_raw) = price_str.parse::<f64>() {
                                                    let price = price_raw * 10_f64.powi(expo as i32);
                                                    let tick = PriceTick {
                                                        ts_us: now_micros(),
                                                        ts_iso: now_iso(),
                                                        source: "pyth".to_string(),
                                                        symbol: "BTC".to_string(),
                                                        price,
                                                        volume: None,
                                                        trade_id: None,
                                                    };
                                                    let _ = tx.send(tick).await;
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        Err(e) => {
                            eprintln!("[PYTH] Error: {}", e);
                            break;
                        }
                        _ => {}
                    }
                }
            }
            Err(e) => {
                eprintln!("[PYTH] Connection failed: {}", e);
            }
        }

        println!("[PYTH] Reconnecting in 5s...");
        tokio::time::sleep(tokio::time::Duration::from_secs(5)).await;
    }
}

/// Write ticks to JSONL file
async fn tick_writer(mut rx: mpsc::Receiver<PriceTick>, output_dir: &str) -> Result<()> {
    let date = Utc::now().format("%Y%m%d").to_string();
    let filename = format!("{}/price_ticks_{}.jsonl", output_dir, date);

    println!("[WRITER] Writing to {}", filename);

    let file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&filename)?;

    let mut writer = BufWriter::new(file);
    let mut count: u64 = 0;
    let mut last_report = std::time::Instant::now();

    while let Some(tick) = rx.recv().await {
        let json = serde_json::to_string(&tick)?;
        writeln!(writer, "{}", json)?;
        count += 1;

        // Flush and report every 10 seconds
        if last_report.elapsed().as_secs() >= 10 {
            writer.flush()?;
            println!("[WRITER] {} ticks recorded", count);
            last_report = std::time::Instant::now();
        }
    }

    Ok(())
}

/// Print live stats
async fn stats_printer(tx: mpsc::Sender<PriceTick>) -> Result<()> {
    // This is a placeholder - in production we'd track stats
    loop {
        tokio::time::sleep(tokio::time::Duration::from_secs(60)).await;
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    println!("╔═══════════════════════════════════════════════════════════════╗");
    println!("║           MULTI-SOURCE PRICE RECORDER v2.0                    ║");
    println!("║                                                               ║");
    println!("║  Goal: Find which source LEADS Chainlink settlement           ║");
    println!("║                                                               ║");
    println!("║  Sources (8 total):                                           ║");
    println!("║    - Binance Spot      (wss://stream.binance.com)            ║");
    println!("║    - Binance Futures   (wss://fstream.binance.com)           ║");
    println!("║    - Coinbase          (wss://ws-feed.exchange.coinbase.com) ║");
    println!("║    - Bybit             (wss://stream.bybit.com)              ║");
    println!("║    - Kraken            (wss://ws.kraken.com)                 ║");
    println!("║    - OKX               (wss://ws.okx.com)                    ║");
    println!("║    - Chainlink RTDS    (wss://ws-live-data.polymarket.com)  ║");
    println!("║    - Pyth Network      (wss://hermes.pyth.network)          ║");
    println!("╚═══════════════════════════════════════════════════════════════╝");
    println!();

    // Parse args
    let args: Vec<String> = std::env::args().collect();
    let symbol = args.get(1).map(|s| s.as_str()).unwrap_or("BTC");
    let output_dir = args.get(2).map(|s| s.as_str()).unwrap_or("./logs");

    println!("Recording {} prices to {}/", symbol, output_dir);
    println!();

    // Create output directory
    std::fs::create_dir_all(output_dir)?;

    // Channel for all ticks
    let (tx, rx) = mpsc::channel::<PriceTick>(10000);

    // Spawn writer
    let writer_dir = output_dir.to_string();
    tokio::spawn(async move {
        if let Err(e) = tick_writer(rx, &writer_dir).await {
            eprintln!("[WRITER] Error: {}", e);
        }
    });

    // Spawn all sources
    let tx1 = tx.clone();
    let symbol1 = symbol.to_string();
    tokio::spawn(async move {
        let _ = connect_binance_spot(tx1, &symbol1).await;
    });

    let tx2 = tx.clone();
    let symbol2 = symbol.to_string();
    tokio::spawn(async move {
        let _ = connect_binance_futures(tx2, &symbol2).await;
    });

    let tx3 = tx.clone();
    let symbol3 = symbol.to_string();
    tokio::spawn(async move {
        let _ = connect_coinbase(tx3, &symbol3).await;
    });

    let tx4 = tx.clone();
    let symbol4 = symbol.to_string();
    tokio::spawn(async move {
        let _ = connect_bybit(tx4, &symbol4).await;
    });

    let tx5 = tx.clone();
    let symbol5 = symbol.to_string();
    tokio::spawn(async move {
        let _ = connect_kraken(tx5, &symbol5).await;
    });

    let tx6 = tx.clone();
    let symbol6 = symbol.to_string();
    tokio::spawn(async move {
        let _ = connect_okx(tx6, &symbol6).await;
    });

    let tx7 = tx.clone();
    let symbol7 = symbol.to_string();
    tokio::spawn(async move {
        let _ = connect_chainlink(tx7, &symbol7).await;
    });

    let tx8 = tx.clone();
    let symbol8 = symbol.to_string();
    tokio::spawn(async move {
        let _ = connect_pyth(tx8, &symbol8).await;
    });

    println!("Press Ctrl+C to stop recording...");
    println!();

    // Keep running
    loop {
        tokio::time::sleep(tokio::time::Duration::from_secs(3600)).await;
    }
}
