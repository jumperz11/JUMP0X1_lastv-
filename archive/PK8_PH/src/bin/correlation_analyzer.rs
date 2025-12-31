//! Correlation Analyzer
//!
//! Compares price direction from multiple sources against Polymarket 15m settlement.
//! Goal: Find which source best predicts the winner at what timestamp.
//!
//! For each 15-minute session:
//! 1. Record starting price (T+0) from each source
//! 2. Record prices at T+60s, T+5m, T+10m, T+14m, T+14m59s
//! 3. Calculate direction (UP or DOWN) vs opening
//! 4. Compare to actual Polymarket winner
//!
//! Usage:
//!   cargo run --release --bin correlation_analyzer -- --live    # Live recording
//!   cargo run --release --bin correlation_analyzer -- --analyze ./data  # Analyze saved

use anyhow::Result;
use chrono::{DateTime, Duration, TimeZone, Utc};
use futures_util::{SinkExt, StreamExt};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::time::{SystemTime, UNIX_EPOCH};
use tokio::sync::mpsc;
use tokio_tungstenite::{connect_async, tungstenite::Message};

/// A 15-minute session's data
#[derive(Debug, Clone, Serialize, Deserialize)]
struct Session {
    /// Session ID (unix timestamp of start)
    session_id: i64,
    /// Start timestamp ISO
    start_time: String,
    /// Symbol (BTC, ETH)
    symbol: String,
    /// Prices at various checkpoints per source
    /// Key: "source:checkpoint" e.g. "binance_spot:T+0"
    prices: HashMap<String, f64>,
    /// Actual Polymarket winner (UP or DOWN)
    pm_winner: Option<String>,
    /// Was our prediction correct per source?
    predictions: HashMap<String, bool>,
}

/// Price tick from any source
#[derive(Debug, Clone, Deserialize)]
struct PriceTick {
    ts_us: i64,
    source: String,
    price: f64,
}

/// Polymarket market resolution
#[derive(Debug, Clone, Deserialize)]
struct PMResolution {
    market_id: String,
    winner: String, // "UP" or "DOWN"
    resolution_time: i64,
}

const CHECKPOINTS: &[(&str, i64)] = &[
    ("T+0", 0),
    ("T+30s", 30),
    ("T+60s", 60),
    ("T+2m", 120),
    ("T+5m", 300),
    ("T+10m", 600),
    ("T+13m", 780),
    ("T+14m", 840),
    ("T+14m30s", 870),
    ("T+14m59s", 899),
];

const SOURCES: &[&str] = &["binance_spot", "binance_futures", "coinbase", "bybit"];

fn now_micros() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_micros() as i64
}

/// Get the current 15-minute session start time
fn get_session_start(ts_secs: i64) -> i64 {
    // 15-minute sessions start at :00, :15, :30, :45
    let mins = (ts_secs / 60) % 60;
    let session_min = (mins / 15) * 15;
    let hour_start = (ts_secs / 3600) * 3600;
    hour_start + session_min * 60
}

/// Get seconds into current session
fn get_session_offset(ts_secs: i64) -> i64 {
    let session_start = get_session_start(ts_secs);
    ts_secs - session_start
}

async fn connect_binance_spot(tx: mpsc::Sender<(String, f64)>, symbol: &str) -> Result<()> {
    let symbol_lower = symbol.to_lowercase();
    let url = format!("wss://stream.binance.com:9443/ws/{}usdt@trade", symbol_lower);

    loop {
        match connect_async(&url).await {
            Ok((ws_stream, _)) => {
                println!("[BINANCE_SPOT] Connected");
                let (_, mut read) = ws_stream.split();

                while let Some(msg) = read.next().await {
                    if let Ok(Message::Text(text)) = msg {
                        if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                            if let Some(price_str) = v.get("p").and_then(|p| p.as_str()) {
                                if let Ok(price) = price_str.parse::<f64>() {
                                    let _ = tx.send(("binance_spot".to_string(), price)).await;
                                }
                            }
                        }
                    }
                }
            }
            Err(e) => eprintln!("[BINANCE_SPOT] Error: {}", e),
        }
        tokio::time::sleep(tokio::time::Duration::from_secs(5)).await;
    }
}

async fn connect_binance_futures(tx: mpsc::Sender<(String, f64)>, symbol: &str) -> Result<()> {
    let symbol_lower = symbol.to_lowercase();
    let url = format!("wss://fstream.binance.com/ws/{}usdt@aggTrade", symbol_lower);

    loop {
        match connect_async(&url).await {
            Ok((ws_stream, _)) => {
                println!("[BINANCE_FUTURES] Connected");
                let (_, mut read) = ws_stream.split();

                while let Some(msg) = read.next().await {
                    if let Ok(Message::Text(text)) = msg {
                        if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                            if let Some(price_str) = v.get("p").and_then(|p| p.as_str()) {
                                if let Ok(price) = price_str.parse::<f64>() {
                                    let _ = tx.send(("binance_futures".to_string(), price)).await;
                                }
                            }
                        }
                    }
                }
            }
            Err(e) => eprintln!("[BINANCE_FUTURES] Error: {}", e),
        }
        tokio::time::sleep(tokio::time::Duration::from_secs(5)).await;
    }
}

async fn connect_coinbase(tx: mpsc::Sender<(String, f64)>, symbol: &str) -> Result<()> {
    let url = "wss://ws-feed.exchange.coinbase.com";
    let product = format!("{}-USD", symbol);

    loop {
        match connect_async(url).await {
            Ok((ws_stream, _)) => {
                println!("[COINBASE] Connected");
                let (mut write, mut read) = ws_stream.split();

                let subscribe = serde_json::json!({
                    "type": "subscribe",
                    "product_ids": [&product],
                    "channels": ["matches"]
                });

                let _ = write.send(Message::Text(subscribe.to_string().into())).await;

                while let Some(msg) = read.next().await {
                    if let Ok(Message::Text(text)) = msg {
                        if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                            if v.get("type").and_then(|t| t.as_str()) == Some("match") {
                                if let Some(price_str) = v.get("price").and_then(|p| p.as_str()) {
                                    if let Ok(price) = price_str.parse::<f64>() {
                                        let _ = tx.send(("coinbase".to_string(), price)).await;
                                    }
                                }
                            }
                        }
                    }
                }
            }
            Err(e) => eprintln!("[COINBASE] Error: {}", e),
        }
        tokio::time::sleep(tokio::time::Duration::from_secs(5)).await;
    }
}

async fn connect_bybit(tx: mpsc::Sender<(String, f64)>, symbol: &str) -> Result<()> {
    let url = "wss://stream.bybit.com/v5/public/spot";
    let topic = format!("publicTrade.{}USDT", symbol);

    loop {
        match connect_async(url).await {
            Ok((ws_stream, _)) => {
                println!("[BYBIT] Connected");
                let (mut write, mut read) = ws_stream.split();

                let subscribe = serde_json::json!({
                    "op": "subscribe",
                    "args": [&topic]
                });

                let _ = write.send(Message::Text(subscribe.to_string().into())).await;

                while let Some(msg) = read.next().await {
                    if let Ok(Message::Text(text)) = msg {
                        if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                            if let Some(data) = v.get("data").and_then(|d| d.as_array()) {
                                for trade in data {
                                    if let Some(price_str) = trade.get("p").and_then(|p| p.as_str()) {
                                        if let Ok(price) = price_str.parse::<f64>() {
                                            let _ = tx.send(("bybit".to_string(), price)).await;
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            Err(e) => eprintln!("[BYBIT] Error: {}", e),
        }
        tokio::time::sleep(tokio::time::Duration::from_secs(5)).await;
    }
}

/// Session recorder - tracks prices at checkpoints
async fn session_recorder(
    mut rx: mpsc::Receiver<(String, f64)>,
    symbol: String,
    output_dir: String,
) -> Result<()> {
    let mut current_session: i64 = 0;
    let mut session_data: Session = Session {
        session_id: 0,
        start_time: String::new(),
        symbol: symbol.clone(),
        prices: HashMap::new(),
        pm_winner: None,
        predictions: HashMap::new(),
    };
    let mut last_prices: HashMap<String, f64> = HashMap::new();
    let mut recorded_checkpoints: HashMap<String, bool> = HashMap::new();

    // File for session data
    let filename = format!("{}/sessions_{}.jsonl", output_dir, symbol.to_lowercase());

    println!("[RECORDER] Writing sessions to {}", filename);

    loop {
        // Check for new prices (with timeout for checkpoint checking)
        let timeout = tokio::time::timeout(
            tokio::time::Duration::from_millis(100),
            rx.recv()
        ).await;

        if let Ok(Some((source, price))) = timeout {
            last_prices.insert(source, price);
        }

        // Current time
        let now_secs = (now_micros() / 1_000_000) as i64;
        let session_start = get_session_start(now_secs);
        let session_offset = get_session_offset(now_secs);

        // New session?
        if session_start != current_session {
            // Save previous session if it has data
            if current_session != 0 && !session_data.prices.is_empty() {
                // Calculate predictions based on direction
                let t0_key_prefix = "T+0";
                let t14m59s_key_prefix = "T+14m59s";

                for source in SOURCES {
                    let t0_key = format!("{}:{}", source, t0_key_prefix);
                    let t14m_key = format!("{}:{}", source, t14m59s_key_prefix);

                    if let (Some(&p0), Some(&p14m)) = (
                        session_data.prices.get(&t0_key),
                        session_data.prices.get(&t14m_key)
                    ) {
                        let predicted = if p14m > p0 { "UP" } else { "DOWN" };
                        session_data.prices.insert(
                            format!("{}:predicted", source),
                            if predicted == "UP" { 1.0 } else { 0.0 }
                        );
                    }
                }

                // Write to file
                if let Ok(file) = OpenOptions::new()
                    .create(true)
                    .append(true)
                    .open(&filename)
                {
                    let mut writer = BufWriter::new(file);
                    if let Ok(json) = serde_json::to_string(&session_data) {
                        let _ = writeln!(writer, "{}", json);
                        let _ = writer.flush();
                    }
                }

                println!(
                    "[SESSION] Saved session {} with {} prices",
                    current_session,
                    session_data.prices.len()
                );
            }

            // Start new session
            current_session = session_start;
            session_data = Session {
                session_id: session_start,
                start_time: Utc.timestamp_opt(session_start, 0)
                    .single()
                    .map(|dt| dt.format("%Y-%m-%dT%H:%M:%SZ").to_string())
                    .unwrap_or_default(),
                symbol: symbol.clone(),
                prices: HashMap::new(),
                pm_winner: None,
                predictions: HashMap::new(),
            };
            recorded_checkpoints.clear();

            println!(
                "\n[SESSION] New session started: {} ({})",
                session_start,
                session_data.start_time
            );
        }

        // Record prices at checkpoints
        for (checkpoint_name, checkpoint_offset) in CHECKPOINTS {
            // Allow 2 second window around checkpoint
            if (session_offset - checkpoint_offset).abs() <= 2 {
                for (source, &price) in &last_prices {
                    let key = format!("{}:{}", source, checkpoint_name);
                    if !recorded_checkpoints.contains_key(&key) {
                        session_data.prices.insert(key.clone(), price);
                        recorded_checkpoints.insert(key.clone(), true);
                        println!(
                            "  [{}] {} @ {} = ${:.2}",
                            checkpoint_name, source, session_offset, price
                        );
                    }
                }
            }
        }
    }
}

/// Analyze saved session data
fn analyze_sessions(data_dir: &str, symbol: &str) -> Result<()> {
    let filename = format!("{}/sessions_{}.jsonl", data_dir, symbol.to_lowercase());

    println!("╔═══════════════════════════════════════════════════════════════╗");
    println!("║           CORRELATION ANALYZER v1.0                           ║");
    println!("╚═══════════════════════════════════════════════════════════════╝");
    println!();
    println!("Analyzing: {}", filename);
    println!();

    let file = File::open(&filename)?;
    let reader = BufReader::new(file);

    let mut sessions: Vec<Session> = Vec::new();
    for line in reader.lines() {
        if let Ok(line) = line {
            if let Ok(session) = serde_json::from_str::<Session>(&line) {
                sessions.push(session);
            }
        }
    }

    println!("Loaded {} sessions", sessions.len());
    println!();

    if sessions.is_empty() {
        println!("No sessions to analyze. Run with --live first to collect data.");
        return Ok(());
    }

    // For each checkpoint, calculate correlation with final direction
    println!("=== DIRECTION CORRELATION BY CHECKPOINT ===");
    println!();
    println!("{:<15} {:>10} {:>10} {:>10} {:>10}",
        "CHECKPOINT", "BINANCE_S", "BINANCE_F", "COINBASE", "BYBIT");
    println!("{}", "-".repeat(60));

    for (checkpoint_name, _) in CHECKPOINTS {
        if *checkpoint_name == "T+0" {
            continue; // Can't compare T+0 to itself
        }

        let mut correct: HashMap<&str, usize> = HashMap::new();
        let mut total: HashMap<&str, usize> = HashMap::new();

        for session in &sessions {
            // Get T+0 and this checkpoint for each source
            for source in SOURCES {
                let t0_key = format!("{}:T+0", source);
                let cp_key = format!("{}:{}", source, checkpoint_name);
                let final_key = format!("{}:T+14m59s", source);

                if let (Some(&p0), Some(&p_cp), Some(&p_final)) = (
                    session.prices.get(&t0_key),
                    session.prices.get(&cp_key),
                    session.prices.get(&final_key),
                ) {
                    let cp_direction = if p_cp > p0 { "UP" } else { "DOWN" };
                    let final_direction = if p_final > p0 { "UP" } else { "DOWN" };

                    *total.entry(*source).or_insert(0) += 1;
                    if cp_direction == final_direction {
                        *correct.entry(*source).or_insert(0) += 1;
                    }
                }
            }
        }

        // Print row
        print!("{:<15}", checkpoint_name);
        for source in SOURCES {
            let c = correct.get(source).copied().unwrap_or(0);
            let t = total.get(source).copied().unwrap_or(0);
            if t > 0 {
                let pct = 100.0 * c as f64 / t as f64;
                print!(" {:>9.1}%", pct);
            } else {
                print!(" {:>10}", "N/A");
            }
        }
        println!();
    }

    println!();
    println!("=== INTERPRETATION ===");
    println!();
    println!("Higher % = better predictor of final direction at that checkpoint.");
    println!("Look for:");
    println!("  - Which source has highest correlation overall?");
    println!("  - At what checkpoint does correlation peak?");
    println!("  - Is there a source that leads others consistently?");
    println!();
    println!("For trading:");
    println!("  - If correlation > 60% at T+60s, you have 14 minutes to trade");
    println!("  - If correlation only peaks at T+14m, you have no edge");
    println!("  - Higher correlation = more confident directional bet");

    Ok(())
}

#[tokio::main]
async fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().collect();

    let mode = args.get(1).map(|s| s.as_str()).unwrap_or("--help");
    let symbol = args.get(2).map(|s| s.as_str()).unwrap_or("BTC");
    let data_dir = args.get(3).map(|s| s.as_str()).unwrap_or("./logs");

    match mode {
        "--live" => {
            println!("╔═══════════════════════════════════════════════════════════════╗");
            println!("║           CORRELATION RECORDER v1.0                           ║");
            println!("║                                                               ║");
            println!("║  Recording price checkpoints for {} 15-minute sessions       ║", symbol);
            println!("║  Checkpoints: T+0, T+30s, T+60s, T+2m, T+5m, T+10m, T+14m59s  ║");
            println!("╚═══════════════════════════════════════════════════════════════╝");
            println!();

            std::fs::create_dir_all(data_dir)?;

            let (tx, rx) = mpsc::channel::<(String, f64)>(1000);

            // Spawn price sources
            let tx1 = tx.clone();
            let s1 = symbol.to_string();
            tokio::spawn(async move { let _ = connect_binance_spot(tx1, &s1).await; });

            let tx2 = tx.clone();
            let s2 = symbol.to_string();
            tokio::spawn(async move { let _ = connect_binance_futures(tx2, &s2).await; });

            let tx3 = tx.clone();
            let s3 = symbol.to_string();
            tokio::spawn(async move { let _ = connect_coinbase(tx3, &s3).await; });

            let tx4 = tx.clone();
            let s4 = symbol.to_string();
            tokio::spawn(async move { let _ = connect_bybit(tx4, &s4).await; });

            // Run session recorder
            let sym = symbol.to_string();
            let dir = data_dir.to_string();
            session_recorder(rx, sym, dir).await?;
        }
        "--analyze" => {
            analyze_sessions(data_dir, symbol)?;
        }
        _ => {
            println!("CORRELATION ANALYZER");
            println!();
            println!("Usage:");
            println!("  {} --live [SYMBOL] [DATA_DIR]     Record price checkpoints", args[0]);
            println!("  {} --analyze [SYMBOL] [DATA_DIR]  Analyze saved sessions", args[0]);
            println!();
            println!("Examples:");
            println!("  {} --live BTC ./logs              Record BTC 15m sessions", args[0]);
            println!("  {} --analyze BTC ./logs           Analyze BTC correlation", args[0]);
            println!();
            println!("Checkpoints recorded:");
            for (name, offset) in CHECKPOINTS {
                println!("  {} ({}s into session)", name, offset);
            }
        }
    }

    Ok(())
}
