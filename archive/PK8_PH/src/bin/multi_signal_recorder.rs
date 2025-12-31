//! Multi-Signal Correlation Recorder v4.0
//!
//! Pro trader terminal with:
//! - Proper crossterm terminal control (no spam)
//! - Lead-lag detection with timestamps
//! - Dislocation tracking (Poly vs Chainlink)
//! - PnL proxy tracking
//! - Compact single-screen layout

use anyhow::Result;
use chrono::{TimeZone, Utc};
use crossterm::{
    cursor::{Hide, MoveTo, Show},
    execute,
    terminal::{Clear, ClearType, EnterAlternateScreen, LeaveAlternateScreen},
};
use futures_util::{SinkExt, StreamExt};
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;
use std::fs::OpenOptions;
use std::io::{BufRead, BufReader, BufWriter, Write, stdout};
use std::panic;
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::sync::RwLock;
use tokio_tungstenite::{connect_async, tungstenite::Message};

// =============================================================================
// ANSI COLORS (for content coloring - crossterm handles terminal control)
// =============================================================================

const GREEN: &str = "\x1b[32m";
const RED: &str = "\x1b[31m";
const YELLOW: &str = "\x1b[33m";
const CYAN: &str = "\x1b[36m";
const WHITE: &str = "\x1b[97m";
const BOLD: &str = "\x1b[1m";
const DIM: &str = "\x1b[2m";
const RESET: &str = "\x1b[0m";
const BG_YELLOW: &str = "\x1b[43m";
const BG_RED: &str = "\x1b[41m";

// =============================================================================
// DATA STRUCTURES
// =============================================================================

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
struct SignalSnapshot {
    timestamp: i64,
    checkpoint: String,
    binance_spot: Option<f64>,
    binance_futures: Option<f64>,
    coinbase: Option<f64>,
    bybit: Option<f64>,
    kraken: Option<f64>,
    okx: Option<f64>,
    chainlink_rtds: Option<f64>,
    pyth: Option<f64>,
    funding_rate: Option<f64>,
    open_interest: Option<f64>,
    long_short_ratio: Option<f64>,
    long_liquidations: Option<f64>,
    short_liquidations: Option<f64>,
    orderbook_imbalance: Option<f64>,
    cvd: Option<f64>,
    fear_greed: Option<f64>,
    // === NEW RTDS FIELDS (v2 - additive, optional) ===
    #[serde(skip_serializing_if = "Option::is_none", default)]
    rtds_ts_ms: Option<u128>,           // When RTDS price was received (epoch ms)
    #[serde(skip_serializing_if = "Option::is_none", default)]
    rtds_age_ms: Option<u128>,          // Age of RTDS price at snapshot time
    #[serde(skip_serializing_if = "Option::is_none", default)]
    rtds_frozen_ms: Option<u128>,       // Time since RTDS PRICE changed (oracle stagnation)
    #[serde(skip_serializing_if = "Option::is_none", default)]
    disloc_vs_rtds: Option<f64>,        // binance_mid - chainlink_rtds ($)
    #[serde(skip_serializing_if = "Option::is_none", default)]
    disloc_vs_rtds_bps: Option<f64>,    // dislocation in basis points
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct Session {
    session_id: i64,
    start_time: String,
    symbol: String,
    snapshots: Vec<SignalSnapshot>,
    winner: Option<String>,
    // === VERSIONING FIELDS (v1 - additive, optional) ===
    #[serde(skip_serializing_if = "Option::is_none", default)]
    run_id: Option<String>,           // Run identifier (e.g., "20251221_0400_V1")
    #[serde(skip_serializing_if = "Option::is_none", default)]
    schema_version: Option<u32>,      // Schema version (1 = with telemetry)
    // === EV FIELDS (v2 - additive, optional) ===
    // Correct binary option EV math:
    //   Entry price q (probability 0..1)
    //   R_win = (1 - q) / q   (e.g., q=0.55 => +81.8%)
    //   R_loss = -1.0         (lose 100% of stake)
    //   EV = win_rate × R_win + lose_rate × R_loss
    #[serde(skip_serializing_if = "Option::is_none", default)]
    assumed_q: Option<f64>,           // Assumed entry price for EV calc (default 0.55)
    #[serde(skip_serializing_if = "Option::is_none", default)]
    q_entry: Option<f64>,             // REAL entry price from avg_fill_q (replaces assumed_q)
    #[serde(skip_serializing_if = "Option::is_none", default)]
    ev_if_correct: Option<f64>,       // EV if our signal was correct: R_win at q_entry
    #[serde(skip_serializing_if = "Option::is_none", default)]
    ev_if_wrong: Option<f64>,         // EV if signal was wrong: R_loss = -1.0
    #[serde(skip_serializing_if = "Option::is_none", default)]
    realized_return_pct: Option<f64>, // Actual return for this session (R_win or R_loss)
    #[serde(skip_serializing_if = "Option::is_none", default)]
    realized_pnl_usd: Option<f64>,    // payout_usd - cost_usd - fees
}

#[derive(Debug, Clone)]
struct SessionHistory {
    time: String,
    winner: String,
    price_signal: String,
    cvd_signal: String,
    ob_signal: String,
    price_correct: bool,
    cvd_correct: bool,
    ob_correct: bool,
    edge_captured: f64,  // % move captured
}

// Lead-lag tracking for each exchange
#[derive(Debug, Clone, Default)]
struct ExchangeTiming {
    last_price: f64,
    last_update_ms: u128,  // milliseconds since epoch
    last_move_ms: u128,    // when last significant move detected
}

#[derive(Debug)]
struct LiveState {
    // Price sources
    binance_spot: Option<f64>,
    binance_futures: Option<f64>,
    coinbase: Option<f64>,
    bybit: Option<f64>,
    kraken: Option<f64>,
    okx: Option<f64>,
    chainlink_rtds: Option<f64>,
    pyth: Option<f64>,

    // Timing for lead-lag (milliseconds since start)
    timing_binance: ExchangeTiming,
    timing_coinbase: ExchangeTiming,
    timing_bybit: ExchangeTiming,
    timing_chainlink: ExchangeTiming,
    timing_pyth: ExchangeTiming,
    start_instant: Instant,

    // Lead-lag state
    last_significant_move_ms: u128,
    leader_exchange: String,
    leader_lag_ms: i64,
    settler_lag_ms: i64,

    // RTDS frozen tracking (oracle stagnation detection)
    last_rtds_price: f64,
    last_rtds_price_change_ms: u128,

    // Sentiment sources
    funding_rate: Option<f64>,
    open_interest: Option<f64>,
    long_short_ratio: Option<f64>,
    orderbook_imbalance: Option<f64>,
    fear_greed: Option<f64>,
    long_liquidations: f64,
    short_liquidations: f64,
    cvd: f64,

    // Connection status
    connected: u16,

    // Session state
    session_count: u32,
    t0_price: Option<f64>,
    t0_chainlink: Option<f64>,
    recorded_checkpoints: Vec<String>,
    history: VecDeque<SessionHistory>,

    // Running stats
    price_correct: u32,
    price_total: u32,
    cvd_correct: u32,
    cvd_total: u32,
    ob_correct: u32,
    ob_total: u32,
    total_edge: f64,  // sum of edge captured
}

impl Default for LiveState {
    fn default() -> Self {
        Self {
            binance_spot: None,
            binance_futures: None,
            coinbase: None,
            bybit: None,
            kraken: None,
            okx: None,
            chainlink_rtds: None,
            pyth: None,
            timing_binance: ExchangeTiming::default(),
            timing_coinbase: ExchangeTiming::default(),
            timing_bybit: ExchangeTiming::default(),
            timing_chainlink: ExchangeTiming::default(),
            timing_pyth: ExchangeTiming::default(),
            start_instant: Instant::now(),
            last_significant_move_ms: 0,
            leader_exchange: "---".to_string(),
            leader_lag_ms: 0,
            settler_lag_ms: 0,
            last_rtds_price: 0.0,
            last_rtds_price_change_ms: 0,
            funding_rate: None,
            open_interest: None,
            long_short_ratio: None,
            orderbook_imbalance: None,
            fear_greed: None,
            long_liquidations: 0.0,
            short_liquidations: 0.0,
            cvd: 0.0,
            connected: 0,
            session_count: 0,
            t0_price: None,
            t0_chainlink: None,
            recorded_checkpoints: Vec::new(),
            history: VecDeque::with_capacity(5),
            price_correct: 0,
            price_total: 0,
            cvd_correct: 0,
            cvd_total: 0,
            ob_correct: 0,
            ob_total: 0,
            total_edge: 0.0,
        }
    }
}

// Source bit flags
const SRC_BINANCE_SPOT: u16 = 1 << 0;
const SRC_BINANCE_FUT: u16 = 1 << 1;
const SRC_COINBASE: u16 = 1 << 2;
const SRC_BYBIT: u16 = 1 << 3;
const SRC_KRAKEN: u16 = 1 << 4;
const SRC_OKX: u16 = 1 << 5;
const SRC_CHAINLINK: u16 = 1 << 6;
const SRC_PYTH: u16 = 1 << 7;
const SRC_FUNDING: u16 = 1 << 8;
const SRC_OI: u16 = 1 << 9;
const SRC_LSRATIO: u16 = 1 << 10;
const SRC_ORDERBOOK: u16 = 1 << 11;
const SRC_CVD: u16 = 1 << 12;
const SRC_FEAR: u16 = 1 << 13;

// Significant move threshold: 0.02% in 2 seconds
const SIGNIFICANT_MOVE_PCT: f64 = 0.02;

// Price direction epsilon: 0.01% dead zone for FLAT
const PRICE_EPSILON: f64 = 0.0001;

// Dislocation thresholds
const DISLOCATION_THRESHOLD_USD: f64 = 25.0;   // $25 minimum dislocation
const DISLOCATION_THRESHOLD_PCT: f64 = 0.03;   // 0.03% = ~$26 on BTC ~$88k
const DISLOCATION_PERSIST_SECS: i64 = 60;      // Must persist 60s

// Lead-lag detection thresholds (from v2 analyzer)
const MOVE_THRESHOLD_PCT: f64 = 0.02;          // 0.02% = $17.60 on BTC ~$88k
const MOVE_WINDOW_MS: u128 = 2000;             // 2s window for move detection
const COOLDOWN_MS: u128 = 3000;                // 3s cooldown between events

// Flat/epsilon threshold
const EPSILON_FLAT_PCT: f64 = 0.0001;          // 0.01% dead zone for FLAT direction

// Staleness/outlier thresholds
const STALENESS_MS: u128 = 5000;               // 5s = stale price
const OUTLIER_PCT: f64 = 1.0;                  // 1% = outlier price (vs median)

// Run config struct for config.json - ALL thresholds must be here
#[derive(Debug, Clone, Serialize, Deserialize)]
struct RunConfig {
    version: String,
    run_id: Option<String>,
    start_time: String,
    symbol: String,
    // Move detection
    move_threshold_pct: f64,
    move_window_ms: u128,
    cooldown_ms: u128,
    // Dislocation
    dislocation_threshold_usd: f64,
    dislocation_threshold_pct: f64,
    dislocation_persist_secs: i64,
    // Direction
    epsilon_flat_pct: f64,
    // Staleness/outlier
    staleness_ms: u128,
    outlier_pct: f64,
    // EV
    assumed_q: f64,
    // Schedule
    checkpoints: Vec<String>,
}

const CHECKPOINTS: &[(&str, i64)] = &[
    ("T+0", 0),
    ("T+15s", 15),
    ("T+30s", 30),
    ("T+45s", 45),
    ("T+60s", 60),
    ("T+90s", 90),
    ("T+2m", 120),
    ("T+3m", 180),
    ("T+5m", 300),
    ("T+7m", 420),
    ("T+10m", 600),
    ("T+12m", 720),
    ("T+13m", 780),
    ("T+14m", 840),
    ("T+14m30s", 870),
    ("T+14m45s", 885),
    ("T+14m59s", 899),
];

fn now_secs() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs() as i64
}

fn now_millis() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_millis()
}

fn get_session_start(ts_secs: i64) -> i64 {
    let mins = (ts_secs / 60) % 60;
    let session_min = (mins / 15) * 15;
    let hour_start = (ts_secs / 3600) * 3600;
    hour_start + session_min * 60
}

fn get_session_offset(ts_secs: i64) -> i64 {
    ts_secs - get_session_start(ts_secs)
}

// =============================================================================
// TERMINAL CONTROL (crossterm)
// =============================================================================

fn setup_terminal() {
    let mut out = stdout();
    let _ = execute!(out, EnterAlternateScreen, Hide);

    // Set up panic handler to restore terminal
    let original_hook = panic::take_hook();
    panic::set_hook(Box::new(move |panic_info| {
        let mut out = std::io::stdout();
        let _ = execute!(out, Show, LeaveAlternateScreen);
        original_hook(panic_info);
    }));
}

fn cleanup_terminal() {
    let mut stdout = stdout();
    let _ = execute!(stdout, Show, LeaveAlternateScreen);
}

fn clear_and_home() {
    let mut stdout = stdout();
    let _ = execute!(stdout, Clear(ClearType::All), MoveTo(0, 0));
}

// =============================================================================
// PRICE SOURCES (with timing for lead-lag)
// =============================================================================

async fn binance_spot(state: Arc<RwLock<LiveState>>, symbol: &str) {
    let url = format!("wss://stream.binance.com:9443/ws/{}usdt@trade", symbol.to_lowercase());
    loop {
        if let Ok((ws, _)) = connect_async(&url).await {
            state.write().await.connected |= SRC_BINANCE_SPOT;
            let (_, mut read) = ws.split();
            while let Some(Ok(Message::Text(text))) = read.next().await {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                    if let Some(p) = v.get("p").and_then(|x| x.as_str()) {
                        if let Ok(price) = p.parse::<f64>() {
                            let mut s = state.write().await;
                            let now_ms = now_millis();

                            // Check for significant move
                            if s.timing_binance.last_price > 0.0 {
                                let pct_change = ((price - s.timing_binance.last_price) / s.timing_binance.last_price).abs() * 100.0;
                                if pct_change >= SIGNIFICANT_MOVE_PCT {
                                    s.timing_binance.last_move_ms = now_ms;
                                    // Check if this is the leader
                                    if now_ms - s.last_significant_move_ms > 2000 {
                                        // New significant move event
                                        s.last_significant_move_ms = now_ms;
                                        s.leader_exchange = "Binance".to_string();
                                        s.leader_lag_ms = 0;
                                    }
                                }
                            }

                            s.timing_binance.last_price = price;
                            s.timing_binance.last_update_ms = now_ms;
                            s.binance_spot = Some(price);
                        }
                    }
                }
            }
            state.write().await.connected &= !SRC_BINANCE_SPOT;
        }
        tokio::time::sleep(Duration::from_secs(2)).await;
    }
}

async fn binance_futures(state: Arc<RwLock<LiveState>>, symbol: &str) {
    let url = format!("wss://fstream.binance.com/ws/{}usdt@aggTrade", symbol.to_lowercase());
    loop {
        if let Ok((ws, _)) = connect_async(&url).await {
            state.write().await.connected |= SRC_BINANCE_FUT;
            let (_, mut read) = ws.split();
            while let Some(Ok(Message::Text(text))) = read.next().await {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                    if let Some(p) = v.get("p").and_then(|x| x.as_str()) {
                        if let Ok(price) = p.parse::<f64>() {
                            state.write().await.binance_futures = Some(price);
                        }
                    }
                }
            }
            state.write().await.connected &= !SRC_BINANCE_FUT;
        }
        tokio::time::sleep(Duration::from_secs(2)).await;
    }
}

async fn coinbase(state: Arc<RwLock<LiveState>>, symbol: &str) {
    let url = "wss://ws-feed.exchange.coinbase.com";
    let product = format!("{}-USD", symbol);
    loop {
        if let Ok((ws, _)) = connect_async(url).await {
            state.write().await.connected |= SRC_COINBASE;
            let (mut write, mut read) = ws.split();
            let sub = serde_json::json!({"type": "subscribe", "product_ids": [&product], "channels": ["matches"]});
            let _ = write.send(Message::Text(sub.to_string().into())).await;
            while let Some(Ok(Message::Text(text))) = read.next().await {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                    if v.get("type").and_then(|t| t.as_str()) == Some("match") {
                        if let Some(p) = v.get("price").and_then(|x| x.as_str()) {
                            if let Ok(price) = p.parse::<f64>() {
                                let mut s = state.write().await;
                                let now_ms = now_millis();

                                // Check for significant move
                                if s.timing_coinbase.last_price > 0.0 {
                                    let pct_change = ((price - s.timing_coinbase.last_price) / s.timing_coinbase.last_price).abs() * 100.0;
                                    if pct_change >= SIGNIFICANT_MOVE_PCT {
                                        s.timing_coinbase.last_move_ms = now_ms;
                                        if now_ms - s.last_significant_move_ms > 2000 {
                                            s.last_significant_move_ms = now_ms;
                                            s.leader_exchange = "Coinbase".to_string();
                                            s.leader_lag_ms = 0;
                                        }
                                    }
                                }

                                s.timing_coinbase.last_price = price;
                                s.timing_coinbase.last_update_ms = now_ms;
                                s.coinbase = Some(price);
                            }
                        }
                    }
                }
            }
            state.write().await.connected &= !SRC_COINBASE;
        }
        tokio::time::sleep(Duration::from_secs(2)).await;
    }
}

async fn bybit(state: Arc<RwLock<LiveState>>, symbol: &str) {
    let url = "wss://stream.bybit.com/v5/public/spot";
    let topic = format!("publicTrade.{}USDT", symbol);
    loop {
        if let Ok((ws, _)) = connect_async(url).await {
            state.write().await.connected |= SRC_BYBIT;
            let (mut write, mut read) = ws.split();
            let sub = serde_json::json!({"op": "subscribe", "args": [&topic]});
            let _ = write.send(Message::Text(sub.to_string().into())).await;
            while let Some(Ok(Message::Text(text))) = read.next().await {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                    if let Some(data) = v.get("data").and_then(|d| d.as_array()) {
                        for trade in data {
                            if let Some(p) = trade.get("p").and_then(|x| x.as_str()) {
                                if let Ok(price) = p.parse::<f64>() {
                                    let mut s = state.write().await;
                                    let now_ms = now_millis();

                                    if s.timing_bybit.last_price > 0.0 {
                                        let pct_change = ((price - s.timing_bybit.last_price) / s.timing_bybit.last_price).abs() * 100.0;
                                        if pct_change >= SIGNIFICANT_MOVE_PCT {
                                            s.timing_bybit.last_move_ms = now_ms;
                                            if now_ms - s.last_significant_move_ms > 2000 {
                                                s.last_significant_move_ms = now_ms;
                                                s.leader_exchange = "Bybit".to_string();
                                                s.leader_lag_ms = 0;
                                            }
                                        }
                                    }

                                    s.timing_bybit.last_price = price;
                                    s.timing_bybit.last_update_ms = now_ms;
                                    s.bybit = Some(price);
                                }
                            }
                        }
                    }
                }
            }
            state.write().await.connected &= !SRC_BYBIT;
        }
        tokio::time::sleep(Duration::from_secs(2)).await;
    }
}

async fn kraken(state: Arc<RwLock<LiveState>>) {
    let url = "wss://ws.kraken.com";
    loop {
        if let Ok((ws, _)) = connect_async(url).await {
            state.write().await.connected |= SRC_KRAKEN;
            let (mut write, mut read) = ws.split();
            let sub = serde_json::json!({"event": "subscribe", "pair": ["XBT/USD"], "subscription": {"name": "trade"}});
            let _ = write.send(Message::Text(sub.to_string().into())).await;
            while let Some(Ok(Message::Text(text))) = read.next().await {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                    if let Some(arr) = v.as_array() {
                        if arr.len() >= 2 {
                            if let Some(trades) = arr.get(1).and_then(|t| t.as_array()) {
                                if let Some(first) = trades.first().and_then(|t| t.as_array()) {
                                    if let Some(p) = first.first().and_then(|x| x.as_str()) {
                                        if let Ok(price) = p.parse::<f64>() {
                                            state.write().await.kraken = Some(price);
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            state.write().await.connected &= !SRC_KRAKEN;
        }
        tokio::time::sleep(Duration::from_secs(2)).await;
    }
}

async fn okx(state: Arc<RwLock<LiveState>>, symbol: &str) {
    let url = "wss://ws.okx.com:8443/ws/v5/public";
    loop {
        if let Ok((ws, _)) = connect_async(url).await {
            state.write().await.connected |= SRC_OKX;
            let (mut write, mut read) = ws.split();
            let sub = serde_json::json!({"op": "subscribe", "args": [{"channel": "trades", "instId": format!("{}-USDT", symbol)}]});
            let _ = write.send(Message::Text(sub.to_string().into())).await;
            while let Some(Ok(Message::Text(text))) = read.next().await {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                    if let Some(data) = v.get("data").and_then(|d| d.as_array()) {
                        for trade in data {
                            if let Some(p) = trade.get("px").and_then(|x| x.as_str()) {
                                if let Ok(price) = p.parse::<f64>() {
                                    state.write().await.okx = Some(price);
                                }
                            }
                        }
                    }
                }
            }
            state.write().await.connected &= !SRC_OKX;
        }
        tokio::time::sleep(Duration::from_secs(2)).await;
    }
}

// Chainlink RTDS - THE SETTLEMENT SOURCE
async fn chainlink_rtds(state: Arc<RwLock<LiveState>>, symbol: &str) {
    let url = "wss://ws-live-data.polymarket.com";
    let symbol_filter = format!("{{\"symbol\":\"{}/usd\"}}", symbol.to_lowercase());

    loop {
        if let Ok((ws, _)) = connect_async(url).await {
            state.write().await.connected |= SRC_CHAINLINK;
            let (mut write, mut read) = ws.split();

            let sub = serde_json::json!({
                "action": "subscribe",
                "subscriptions": [{
                    "topic": "crypto_prices_chainlink",
                    "type": "*",
                    "filters": symbol_filter
                }]
            });
            let _ = write.send(Message::Text(sub.to_string().into())).await;

            // Keep-alive pings
            let mut ping_write = write;
            tokio::spawn(async move {
                loop {
                    tokio::time::sleep(Duration::from_secs(5)).await;
                    if ping_write.send(Message::Ping(vec![].into())).await.is_err() {
                        break;
                    }
                }
            });

            while let Some(Ok(msg)) = read.next().await {
                if let Message::Text(text) = msg {
                    if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                        if let Some(payload) = v.get("payload") {
                            if let Some(value) = payload.get("value").and_then(|v| v.as_f64()) {
                                let mut s = state.write().await;
                                let now_ms = now_millis();

                                // Track Chainlink timing for settler lag calculation
                                if s.timing_chainlink.last_price > 0.0 {
                                    let pct_change = ((value - s.timing_chainlink.last_price) / s.timing_chainlink.last_price).abs() * 100.0;
                                    if pct_change >= SIGNIFICANT_MOVE_PCT {
                                        s.timing_chainlink.last_move_ms = now_ms;
                                        // Chainlink is always the settler, calculate lag from leader
                                        if s.last_significant_move_ms > 0 && now_ms - s.last_significant_move_ms < 5000 {
                                            s.settler_lag_ms = (now_ms - s.last_significant_move_ms) as i64;
                                        }
                                    }
                                }

                                // Track RTDS frozen state (oracle stagnation)
                                // Update last_rtds_price_change_ms only when price ACTUALLY changes
                                if s.last_rtds_price == 0.0 {
                                    // First price - initialize
                                    s.last_rtds_price = value;
                                    s.last_rtds_price_change_ms = now_ms;
                                } else if (value - s.last_rtds_price).abs() > 0.001 {
                                    // Price changed (more than $0.001 threshold to avoid float noise)
                                    s.last_rtds_price = value;
                                    s.last_rtds_price_change_ms = now_ms;
                                }
                                // If price is same, don't update last_rtds_price_change_ms -> frozen_ms grows

                                s.timing_chainlink.last_price = value;
                                s.timing_chainlink.last_update_ms = now_ms;
                                s.chainlink_rtds = Some(value);
                            }
                        }
                    }
                }
            }
            state.write().await.connected &= !SRC_CHAINLINK;
        }
        tokio::time::sleep(Duration::from_secs(2)).await;
    }
}

// Pyth Network
async fn pyth_network(state: Arc<RwLock<LiveState>>) {
    let btc_feed = "0xe62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43";
    let url = "wss://hermes.pyth.network/ws";

    loop {
        if let Ok((ws, _)) = connect_async(url).await {
            state.write().await.connected |= SRC_PYTH;
            let (mut write, mut read) = ws.split();

            let sub = serde_json::json!({
                "type": "subscribe",
                "ids": [btc_feed]
            });
            let _ = write.send(Message::Text(sub.to_string().into())).await;

            while let Some(Ok(msg)) = read.next().await {
                if let Message::Text(text) = msg {
                    if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                        // Try parsing price_feed format
                        if let Some(pf) = v.get("price_feed") {
                            if let Some(price_obj) = pf.get("price") {
                                if let (Some(price_str), Some(expo)) = (
                                    price_obj.get("price").and_then(|p| p.as_str()),
                                    price_obj.get("expo").and_then(|e| e.as_i64()),
                                ) {
                                    if let Ok(price_raw) = price_str.parse::<f64>() {
                                        let price = price_raw * 10_f64.powi(expo as i32);
                                        let mut s = state.write().await;
                                        let now_ms = now_millis();
                                        s.timing_pyth.last_price = price;
                                        s.timing_pyth.last_update_ms = now_ms;
                                        s.pyth = Some(price);
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
                                            state.write().await.pyth = Some(price);
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            state.write().await.connected &= !SRC_PYTH;
        }
        tokio::time::sleep(Duration::from_secs(2)).await;
    }
}

// =============================================================================
// SENTIMENT SOURCES
// =============================================================================

async fn binance_funding_rate(state: Arc<RwLock<LiveState>>, symbol: &str) {
    let url = format!("https://fapi.binance.com/fapi/v1/premiumIndex?symbol={}USDT", symbol);
    let client = reqwest::Client::new();
    loop {
        if let Ok(resp) = client.get(&url).send().await {
            if let Ok(v) = resp.json::<serde_json::Value>().await {
                if let Some(rate) = v.get("lastFundingRate").and_then(|r| r.as_str()) {
                    if let Ok(r) = rate.parse::<f64>() {
                        let mut s = state.write().await;
                        s.funding_rate = Some(r);
                        s.connected |= SRC_FUNDING;
                    }
                }
            }
        }
        tokio::time::sleep(Duration::from_secs(30)).await;
    }
}

async fn binance_open_interest(state: Arc<RwLock<LiveState>>, symbol: &str) {
    let url = format!("https://fapi.binance.com/fapi/v1/openInterest?symbol={}USDT", symbol);
    let client = reqwest::Client::new();
    loop {
        if let Ok(resp) = client.get(&url).send().await {
            if let Ok(v) = resp.json::<serde_json::Value>().await {
                if let Some(oi) = v.get("openInterest").and_then(|o| o.as_str()) {
                    if let Ok(o) = oi.parse::<f64>() {
                        let mut s = state.write().await;
                        s.open_interest = Some(o);
                        s.connected |= SRC_OI;
                    }
                }
            }
        }
        tokio::time::sleep(Duration::from_secs(30)).await;
    }
}

async fn binance_long_short_ratio(state: Arc<RwLock<LiveState>>, symbol: &str) {
    let url = format!("https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={}USDT&period=5m&limit=1", symbol);
    let client = reqwest::Client::new();
    loop {
        if let Ok(resp) = client.get(&url).send().await {
            if let Ok(v) = resp.json::<serde_json::Value>().await {
                if let Some(arr) = v.as_array() {
                    if let Some(first) = arr.first() {
                        if let Some(ratio) = first.get("longShortRatio").and_then(|r| r.as_str()) {
                            if let Ok(r) = ratio.parse::<f64>() {
                                let mut s = state.write().await;
                                s.long_short_ratio = Some(r);
                                s.connected |= SRC_LSRATIO;
                            }
                        }
                    }
                }
            }
        }
        tokio::time::sleep(Duration::from_secs(60)).await;
    }
}

async fn binance_liquidations(state: Arc<RwLock<LiveState>>, symbol: &str) {
    let url = "wss://fstream.binance.com/ws/!forceOrder@arr";
    loop {
        if let Ok((ws, _)) = connect_async(url).await {
            let (_, mut read) = ws.split();
            while let Some(Ok(Message::Text(text))) = read.next().await {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                    if let Some(order) = v.get("o") {
                        let sym = order.get("s").and_then(|s| s.as_str()).unwrap_or("");
                        if sym.contains(symbol) {
                            let side = order.get("S").and_then(|s| s.as_str()).unwrap_or("");
                            let qty = order.get("q").and_then(|q| q.as_str()).and_then(|q| q.parse::<f64>().ok()).unwrap_or(0.0);
                            let price = order.get("p").and_then(|p| p.as_str()).and_then(|p| p.parse::<f64>().ok()).unwrap_or(0.0);
                            let value = qty * price;
                            let mut s = state.write().await;
                            if side == "SELL" { s.long_liquidations += value; } else { s.short_liquidations += value; }
                        }
                    }
                }
            }
        }
        tokio::time::sleep(Duration::from_secs(2)).await;
    }
}

async fn orderbook_imbalance(state: Arc<RwLock<LiveState>>, symbol: &str) {
    let url = format!("wss://stream.binance.com:9443/ws/{}usdt@depth20@100ms", symbol.to_lowercase());
    loop {
        if let Ok((ws, _)) = connect_async(&url).await {
            state.write().await.connected |= SRC_ORDERBOOK;
            let (_, mut read) = ws.split();
            while let Some(Ok(Message::Text(text))) = read.next().await {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                    let mut total_bids = 0.0;
                    let mut total_asks = 0.0;
                    if let Some(bids) = v.get("bids").and_then(|b| b.as_array()) {
                        for bid in bids {
                            if let Some(arr) = bid.as_array() {
                                if let Some(qty) = arr.get(1).and_then(|q| q.as_str()) {
                                    total_bids += qty.parse::<f64>().unwrap_or(0.0);
                                }
                            }
                        }
                    }
                    if let Some(asks) = v.get("asks").and_then(|a| a.as_array()) {
                        for ask in asks {
                            if let Some(arr) = ask.as_array() {
                                if let Some(qty) = arr.get(1).and_then(|q| q.as_str()) {
                                    total_asks += qty.parse::<f64>().unwrap_or(0.0);
                                }
                            }
                        }
                    }
                    if total_bids + total_asks > 0.0 {
                        let imbalance = (total_bids - total_asks) / (total_bids + total_asks);
                        state.write().await.orderbook_imbalance = Some(imbalance);
                    }
                }
            }
            state.write().await.connected &= !SRC_ORDERBOOK;
        }
        tokio::time::sleep(Duration::from_secs(2)).await;
    }
}

async fn cvd_tracker(state: Arc<RwLock<LiveState>>, symbol: &str) {
    let url = format!("wss://stream.binance.com:9443/ws/{}usdt@aggTrade", symbol.to_lowercase());
    loop {
        if let Ok((ws, _)) = connect_async(&url).await {
            state.write().await.connected |= SRC_CVD;
            let (_, mut read) = ws.split();
            while let Some(Ok(Message::Text(text))) = read.next().await {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                    let qty = v.get("q").and_then(|q| q.as_str()).and_then(|q| q.parse::<f64>().ok()).unwrap_or(0.0);
                    let is_buyer_maker = v.get("m").and_then(|m| m.as_bool()).unwrap_or(false);
                    let delta = if is_buyer_maker { -qty } else { qty };
                    state.write().await.cvd += delta;
                }
            }
            state.write().await.connected &= !SRC_CVD;
        }
        tokio::time::sleep(Duration::from_secs(2)).await;
    }
}

async fn fear_greed_index(state: Arc<RwLock<LiveState>>) {
    let url = "https://api.alternative.me/fng/";
    let client = reqwest::Client::new();
    loop {
        if let Ok(resp) = client.get(url).send().await {
            if let Ok(v) = resp.json::<serde_json::Value>().await {
                if let Some(data) = v.get("data").and_then(|d| d.as_array()) {
                    if let Some(first) = data.first() {
                        if let Some(value) = first.get("value").and_then(|v| v.as_str()) {
                            if let Ok(v) = value.parse::<f64>() {
                                let mut s = state.write().await;
                                s.fear_greed = Some(v);
                                s.connected |= SRC_FEAR;
                            }
                        }
                    }
                }
            }
        }
        tokio::time::sleep(Duration::from_secs(300)).await;
    }
}

// =============================================================================
// UI RENDERING - COMPACT TRADER TERMINAL
// =============================================================================

fn render_ui(state: &LiveState, session_start: i64, session_offset: i64) {
    // Use crossterm to clear and move to home - NO SPAM
    clear_and_home();

    let now = now_secs();
    let offset = now - session_start;
    let time_left = 899 - offset;
    let time_left_mins = time_left / 60;
    let time_left_secs = time_left % 60;

    // Time left - THE MOST IMPORTANT LINE - make it LOUD
    let time_color = if time_left < 60 { RED } else if time_left < 180 { YELLOW } else { WHITE };
    let time_bg = if time_left < 30 { BG_RED } else if time_left < 60 { BG_YELLOW } else { "" };

    println!("{}{}══════════════════════════════════════════════════════════════════════════{}", CYAN, BOLD, RESET);
    println!("{}{}  TIME LEFT: {:>2}m {:02}s  {}│  SESSION {}  │  Sources: {}/14  │  {}{}",
        time_bg, BOLD, time_left_mins, time_left_secs, RESET,
        state.session_count,
        state.connected.count_ones(),
        Utc.timestamp_opt(session_start, 0).single().map(|dt| dt.format("%H:%M UTC").to_string()).unwrap_or_default(),
        RESET);
    println!("{}══════════════════════════════════════════════════════════════════════════{}", CYAN, RESET);

    // =========================================================================
    // DISLOCATION LINE (most important edge indicator)
    // =========================================================================
    let binance_price = state.binance_spot.unwrap_or(0.0);
    let chainlink_price = state.chainlink_rtds.unwrap_or(0.0);

    let dislocation = if binance_price > 0.0 && chainlink_price > 0.0 {
        ((binance_price - chainlink_price) / chainlink_price) * 100.0
    } else {
        0.0
    };

    // Z-score approximation (rough: assume stdev ~0.3%)
    let z_score = dislocation / 0.3;
    let disloc_color = if dislocation.abs() > 0.5 { format!("{}{}", BG_YELLOW, BOLD) } else if dislocation.abs() > 0.2 { YELLOW.to_string() } else { DIM.to_string() };

    println!("{}{}  DISLOCATION: Binance - Chainlink = {:+.3}% (z={:+.1})  {}{}",
        disloc_color, BOLD, dislocation, z_score, RESET,
        if dislocation.abs() > 0.5 { "<< MISPRICING" } else { "" });

    // =========================================================================
    // LEAD-LAG LINE (who moved first) - Fix #3: Only show when real move detected
    // =========================================================================
    let significant_move_detected = state.settler_lag_ms > 0 && state.leader_exchange != "---";
    if significant_move_detected {
        println!("{}{}  LEAD-LAG:{}  Leader: {}{}{}  │  Settler: Chainlink (+{}ms)  │  Thresh: {:.2}%/2s{}",
            YELLOW, BOLD, RESET,
            CYAN, state.leader_exchange, RESET,
            state.settler_lag_ms,
            SIGNIFICANT_MOVE_PCT, RESET);
    } else {
        println!("{}{}  LEAD-LAG:{}  No significant move (threshold {:.2}%/2s){}",
            DIM, BOLD, RESET,
            SIGNIFICANT_MOVE_PCT, RESET);
    }

    println!("{}──────────────────────────────────────────────────────────────────────────{}", DIM, RESET);

    // =========================================================================
    // PRICE SECTION - Fix #1 & #2: FLAT epsilon + clear Baseline Direction label
    // =========================================================================
    let baseline = state.t0_price.unwrap_or(binance_price);
    let change = binance_price - baseline;
    let change_pct = if baseline > 0.0 { (change / baseline) } else { 0.0 };

    // Fix #1: Use epsilon dead zone for FLAT
    let (dir_color, arrow, baseline_dir) = if change_pct > PRICE_EPSILON {
        (GREEN, "▲", "UP")
    } else if change_pct < -PRICE_EPSILON {
        (RED, "▼", "DOWN")
    } else {
        (WHITE, "─", "FLAT")
    };

    let check = |flag: u16| if state.connected & flag != 0 { format!("{}✓{}", GREEN, RESET) } else { format!("{}✗{}", RED, RESET) };
    let price_fmt = |p: Option<f64>| p.map(|v| format!("${:.2}", v)).unwrap_or_else(|| "---".to_string());

    println!("{}PRICES{}  T0: ${:<10.2}  Now: {}${:<10.2}{}  Δ: {}{:+.2} ({:+.2}%){}  {}{}",
        BOLD, RESET, baseline, dir_color, binance_price, RESET, dir_color, change, change_pct * 100.0, RESET, arrow, baseline_dir);

    // Fix #2: Add clear "Baseline Direction" label
    println!("        {}Baseline Direction: {}{}{}",
        DIM, dir_color, baseline_dir, RESET);

    println!("  Binance: {} {}  Coinbase: {} {}  Bybit: {} {}  Chainlink: {} {}  Pyth: {} {}",
        price_fmt(state.binance_spot), check(SRC_BINANCE_SPOT),
        price_fmt(state.coinbase), check(SRC_COINBASE),
        price_fmt(state.bybit), check(SRC_BYBIT),
        price_fmt(state.chainlink_rtds), check(SRC_CHAINLINK),
        price_fmt(state.pyth), check(SRC_PYTH));

    println!("{}──────────────────────────────────────────────────────────────────────────{}", DIM, RESET);

    // =========================================================================
    // DIRECTION CONSENSUS (the signal)
    // =========================================================================
    let mut up_count = 0;
    let mut down_count = 0;
    for price in [state.binance_spot, state.binance_futures, state.coinbase, state.bybit, state.kraken, state.okx] {
        if let Some(p) = price {
            if p > baseline { up_count += 1; } else { down_count += 1; }
        }
    }
    let total_exchanges = up_count + down_count;
    let price_signal = if up_count > down_count { "UP" } else { "DOWN" };
    let cvd_signal = if state.cvd >= 0.0 { "UP" } else { "DOWN" };
    let ob = state.orderbook_imbalance.unwrap_or(0.0);
    let ob_signal = if ob >= 0.0 { "UP" } else { "DOWN" };

    let up_signals = (if up_count > down_count { 1 } else { 0 }) + (if state.cvd >= 0.0 { 1 } else { 0 }) + (if ob >= 0.0 { 1 } else { 0 });
    let down_signals = 3 - up_signals;
    let consensus_pct = up_signals as f64 / 3.0 * 100.0;

    // Fix: Show majority direction clearly - "UP (67%)" or "DOWN (0% UP)"
    let (consensus_dir, consensus_color) = if up_signals > down_signals {
        ("UP", GREEN)
    } else if down_signals > up_signals {
        ("DOWN", RED)
    } else {
        ("MIXED", YELLOW)
    };

    println!("{}CONSENSUS:{} {}{}{} ({:.0}% UP)  │  Exchanges: {}{:>4}{} ({}/{})  │  CVD: {}{:>4}{}  │  OB: {}{:>4}{}",
        BOLD, RESET, consensus_color, consensus_dir, RESET, consensus_pct,
        if price_signal == "UP" { GREEN } else { RED }, price_signal, RESET, up_count.max(down_count), total_exchanges,
        if cvd_signal == "UP" { GREEN } else { RED }, cvd_signal, RESET,
        if ob_signal == "UP" { GREEN } else { RED }, ob_signal, RESET);

    // Visual bar
    let filled = (consensus_pct / 5.0) as usize;
    let bar = format!("{}{}{}{}",
        GREEN, "█".repeat(filled),
        DIM, "░".repeat(20 - filled));
    println!("          {}{}  CVD: {:+.1}  │  OB: {:+.3}  │  Funding: {:+.4}%",
        bar, RESET, state.cvd, ob, state.funding_rate.unwrap_or(0.0) * 100.0);

    println!("{}──────────────────────────────────────────────────────────────────────────{}", DIM, RESET);

    // =========================================================================
    // CHECKPOINTS (compact single line) - Fixed: show missed checkpoints, correct "Next"
    // =========================================================================
    print!("{}CHECKPOINTS:{} ", BOLD, RESET);

    // Find next checkpoint that hasn't happened yet
    let next_cp = CHECKPOINTS.iter().find(|(_, t)| *t > session_offset);

    // Show first 9 checkpoints with proper status
    for (name, cp_time) in CHECKPOINTS.iter().take(9) {
        let recorded = state.recorded_checkpoints.contains(&name.to_string());
        let is_past = session_offset > *cp_time;
        let is_current = session_offset >= *cp_time && session_offset < cp_time + 15; // within 15s window

        if recorded {
            print!("{}✓{}", GREEN, RESET);
        } else if is_current {
            print!("{}▶{}", YELLOW, RESET);
        } else if is_past {
            // Missed checkpoint (past but not recorded - started mid-session)
            print!("{}?{}", DIM, RESET);
        } else {
            // Future checkpoint
            print!("{}○{}", DIM, RESET);
        }
    }

    // Show "Next" only for future checkpoints
    if let Some((next_name, next_time)) = next_cp {
        let secs_until = next_time - session_offset;
        if secs_until > 0 {
            print!("  Next: {}{}{} in {}s", YELLOW, next_name, RESET, secs_until);
        }
    } else {
        // All checkpoints passed
        print!("  {}(session ending){}", DIM, RESET);
    }
    println!();

    println!("{}══════════════════════════════════════════════════════════════════════════{}", CYAN, RESET);

    // =========================================================================
    // SESSION HISTORY (with PnL proxy)
    // =========================================================================
    if !state.history.is_empty() {
        println!("{}HISTORY{}  │ # │ Time  │Winner│ Price │ CVD │ OB  │ Edge  │", BOLD, RESET);

        for (i, h) in state.history.iter().take(4).enumerate() {
            let p_mark = if h.price_correct { format!("{}✓{}", GREEN, RESET) } else { format!("{}✗{}", RED, RESET) };
            let c_mark = if h.cvd_correct { format!("{}✓{}", GREEN, RESET) } else { format!("{}✗{}", RED, RESET) };
            let o_mark = if h.ob_correct { format!("{}✓{}", GREEN, RESET) } else { format!("{}✗{}", RED, RESET) };
            let w_color = if h.winner == "UP" { GREEN } else { RED };
            let edge_color = if h.edge_captured > 0.0 { GREEN } else { RED };

            println!("         │{:>2} │ {} │{}{:^4}{}│  {}   │  {} │  {} │{}{:+.2}%{}│",
                state.history.len() - i, h.time,
                w_color, h.winner, RESET,
                p_mark, c_mark, o_mark,
                edge_color, h.edge_captured, RESET);
        }

        // Running stats
        let price_acc = if state.price_total > 0 { 100.0 * state.price_correct as f64 / state.price_total as f64 } else { 0.0 };
        let cvd_acc = if state.cvd_total > 0 { 100.0 * state.cvd_correct as f64 / state.cvd_total as f64 } else { 0.0 };
        let ob_acc = if state.ob_total > 0 { 100.0 * state.ob_correct as f64 / state.ob_total as f64 } else { 0.0 };
        let avg_edge = if state.price_total > 0 { state.total_edge / state.price_total as f64 } else { 0.0 };

        let p_color = if price_acc >= 55.0 { GREEN } else { RESET };
        let c_color = if cvd_acc >= 55.0 { GREEN } else { RESET };
        let o_color = if ob_acc >= 55.0 { GREEN } else { RESET };
        let e_color = if avg_edge > 0.0 { GREEN } else { RED };

        println!("{}ACCURACY:{} Price {}{:.0}%{}  CVD {}{:.0}%{}  OB {}{:.0}%{}  │  {}Avg Edge: {:+.2}%{}",
            BOLD, RESET,
            p_color, price_acc, RESET,
            c_color, cvd_acc, RESET,
            o_color, ob_acc, RESET,
            e_color, avg_edge, RESET);
    }

    println!("{}══════════════════════════════════════════════════════════════════════════{}", CYAN, RESET);
    println!("{}Recording to logs/  │  Press Ctrl+C to exit cleanly{}", DIM, RESET);

    let _ = stdout().flush();
}

fn render_session_complete(winner: &str, price_change: f64, session_num: u32) {
    clear_and_home();

    let winner_color = if winner == "UP" { GREEN } else { RED };
    let arrow = if winner == "UP" { "▲" } else { "▼" };

    println!();
    println!("{}{}══════════════════════════════════════════════════════════════════════════{}", YELLOW, BOLD, RESET);
    println!("{}{}                    SESSION {} COMPLETE!                                  {}", YELLOW, BOLD, session_num, RESET);
    println!();
    println!("                         {} WINNER: {}{:^6}{}  {}",
        arrow, winner_color, winner, RESET, arrow);
    println!("                       Price Change: {}{:+.2}{}", winner_color, price_change, RESET);
    println!();
    println!("{}{}══════════════════════════════════════════════════════════════════════════{}", YELLOW, BOLD, RESET);
    println!("                    Saving session data...");
    println!("                    Starting next session in 3s...");
    println!("{}{}══════════════════════════════════════════════════════════════════════════{}", YELLOW, BOLD, RESET);

    let _ = stdout().flush();
}

// =============================================================================
// SESSION RECORDER
// =============================================================================

async fn session_recorder(state: Arc<RwLock<LiveState>>, symbol: String, output_dir: String) {
    let filename = format!("{}/multi_signal_sessions_{}.jsonl", output_dir, symbol.to_lowercase());

    // Extract run_id from directory name (e.g., "./logs/runs/20251221_0400_V1" -> "20251221_0400_V1")
    let run_id: Option<String> = std::path::Path::new(&output_dir)
        .file_name()
        .and_then(|s| s.to_str())
        .filter(|s| s.contains("_V"))
        .map(|s| s.to_string());
    let schema_version: Option<u32> = if run_id.is_some() { Some(1) } else { None };

    let mut current_session: i64 = 0;
    let mut session_data: Session = Session {
        session_id: 0,
        start_time: String::new(),
        symbol: symbol.clone(),
        snapshots: Vec::new(),
        winner: None,
        // Versioning fields
        run_id: run_id.clone(),
        schema_version,
        // EV fields (v2)
        assumed_q: None,
        q_entry: None,
        ev_if_correct: None,
        ev_if_wrong: None,
        realized_return_pct: None,
        realized_pnl_usd: None,
    };

    // Wait for connections
    tokio::time::sleep(Duration::from_secs(3)).await;

    let mut session_complete_shown = false;

    loop {
        tokio::time::sleep(Duration::from_millis(1000)).await;

        let now = now_secs();
        let session_start = get_session_start(now);
        let session_offset = get_session_offset(now);

        // New session?
        if session_start != current_session {
            // Save previous session
            if current_session != 0 && !session_data.snapshots.is_empty() {
                let t0 = session_data.snapshots.iter().find(|s| s.checkpoint == "T+0");
                let t14m59s = session_data.snapshots.iter().find(|s| s.checkpoint == "T+14m59s");

                if let (Some(start), Some(end)) = (t0, t14m59s) {
                    if let (Some(p0), Some(p1)) = (start.binance_spot, end.binance_spot) {
                        let winner = if p1 > p0 { "UP" } else { "DOWN" };
                        let price_change = p1 - p0;
                        let edge_captured = ((p1 - p0) / p0) * 100.0;  // % change (OLD metric, kept for compat)
                        session_data.winner = Some(winner.to_string());

                        // === CORRECT EV MATH (v2) ===
                        // Binary option: pay q, receive 1 if win, 0 if lose
                        // R_win = (1 - q) / q   (e.g., q=0.55 => +81.8%)
                        // R_loss = -1.0         (lose 100%)
                        const ASSUMED_Q: f64 = 0.55;  // Default entry probability
                        let r_win = (1.0 - ASSUMED_Q) / ASSUMED_Q * 100.0;  // +81.8% at q=0.55
                        let r_loss = -100.0;  // -100%
                        session_data.assumed_q = Some(ASSUMED_Q);
                        session_data.ev_if_correct = Some(r_win);
                        session_data.ev_if_wrong = Some(r_loss);

                        // Show session complete screen
                        if !session_complete_shown {
                            let session_num = state.read().await.session_count;
                            render_session_complete(winner, price_change, session_num);
                            session_complete_shown = true;
                            tokio::time::sleep(Duration::from_secs(3)).await;
                        }

                        // Calculate signal accuracy at T+60s
                        let t60s = session_data.snapshots.iter().find(|s| s.checkpoint == "T+60s");
                        let (price_signal, cvd_signal, ob_signal, price_correct, cvd_correct, ob_correct) =
                            if let Some(cp) = t60s {
                                let ps = if cp.binance_spot.unwrap_or(p0) > p0 { "UP" } else { "DOWN" };
                                let cs = if cp.cvd.unwrap_or(0.0) > 0.0 { "UP" } else { "DOWN" };
                                let os = if cp.orderbook_imbalance.unwrap_or(0.0) > 0.0 { "UP" } else { "DOWN" };
                                (ps.to_string(), cs.to_string(), os.to_string(), ps == winner, cs == winner, os == winner)
                            } else {
                                ("?".to_string(), "?".to_string(), "?".to_string(), false, false, false)
                            };

                        // Calculate realized return based on OB signal (our primary signal)
                        // This shows: IF we traded based on OB @ T+60s, what return would we get?
                        session_data.realized_return_pct = Some(if ob_correct { r_win } else { r_loss });

                        // Update running totals and history
                        {
                            let mut s = state.write().await;
                            s.price_total += 1;
                            s.cvd_total += 1;
                            s.ob_total += 1;
                            if price_correct { s.price_correct += 1; }
                            if cvd_correct { s.cvd_correct += 1; }
                            if ob_correct { s.ob_correct += 1; }
                            s.total_edge += edge_captured.abs();  // Track absolute edge

                            let time = Utc.timestamp_opt(current_session, 0)
                                .single()
                                .map(|dt| dt.format("%H:%M").to_string())
                                .unwrap_or_default();

                            let history_entry = SessionHistory {
                                time,
                                winner: winner.to_string(),
                                price_signal,
                                cvd_signal,
                                ob_signal,
                                price_correct,
                                cvd_correct,
                                ob_correct,
                                edge_captured,
                            };

                            if s.history.len() >= 5 {
                                s.history.pop_back();
                            }
                            s.history.push_front(history_entry);
                        }
                    }
                }

                // Write to file - HARD EXIT ON FAILURE
                match OpenOptions::new().create(true).append(true).open(&filename) {
                    Ok(file) => {
                        let mut writer = BufWriter::new(file);
                        match serde_json::to_string(&session_data) {
                            Ok(json) => {
                                if writeln!(writer, "{}", json).is_err() || writer.flush().is_err() {
                                    cleanup_terminal();
                                    eprintln!("FATAL: Failed to write session to {}", filename);
                                    std::process::exit(1);
                                }
                            }
                            Err(e) => {
                                cleanup_terminal();
                                eprintln!("FATAL: Failed to serialize session: {}", e);
                                std::process::exit(1);
                            }
                        }
                    }
                    Err(e) => {
                        cleanup_terminal();
                        eprintln!("FATAL: Cannot open sessions file {}: {}", filename, e);
                        std::process::exit(1);
                    }
                }
            }

            session_complete_shown = false;

            // Reset for new session
            {
                let mut s = state.write().await;
                s.long_liquidations = 0.0;
                s.short_liquidations = 0.0;
                s.cvd = 0.0;
                s.session_count += 1;
                s.t0_price = None;
                s.t0_chainlink = None;
                s.recorded_checkpoints.clear();
            }

            current_session = session_start;

            let time_str = Utc.timestamp_opt(session_start, 0)
                .single()
                .map(|dt| dt.format("%H:%M:%S").to_string())
                .unwrap_or_default();

            session_data = Session {
                session_id: session_start,
                start_time: time_str,
                symbol: symbol.clone(),
                snapshots: Vec::new(),
                winner: None,
                // Versioning fields
                run_id: run_id.clone(),
                schema_version,
                // EV fields (v2)
                assumed_q: None,
                q_entry: None,
                ev_if_correct: None,
                ev_if_wrong: None,
                realized_return_pct: None,
                realized_pnl_usd: None,
            };
        }

        // Record at checkpoints
        for (checkpoint_name, checkpoint_offset) in CHECKPOINTS {
            if (session_offset - checkpoint_offset).abs() <= 1 {
                let mut s = state.write().await;
                if !s.recorded_checkpoints.contains(&checkpoint_name.to_string()) {
                    // Save T+0 prices for direction calculation
                    if *checkpoint_name == "T+0" {
                        s.t0_price = s.binance_spot;
                        s.t0_chainlink = s.chainlink_rtds;
                    }

                    // Calculate RTDS timing and dislocation
                    let snapshot_ms = now_millis();
                    let rtds_ts = if s.timing_chainlink.last_update_ms > 0 {
                        Some(s.timing_chainlink.last_update_ms)
                    } else {
                        None
                    };
                    let rtds_age = rtds_ts.map(|ts| snapshot_ms.saturating_sub(ts));

                    // RTDS frozen: time since price actually changed (oracle stagnation)
                    let rtds_frozen = if s.last_rtds_price_change_ms > 0 {
                        Some(snapshot_ms.saturating_sub(s.last_rtds_price_change_ms))
                    } else {
                        None
                    };

                    // Dislocation: Binance mid - RTDS (positive = Binance ahead)
                    let (disloc_usd, disloc_bps) = match (s.binance_spot, s.chainlink_rtds) {
                        (Some(bin), Some(rtds)) if rtds > 0.0 => {
                            let diff = bin - rtds;
                            let bps = (diff / rtds) * 10000.0;
                            (Some(diff), Some(bps))
                        }
                        _ => (None, None),
                    };

                    let snapshot = SignalSnapshot {
                        timestamp: now,
                        checkpoint: checkpoint_name.to_string(),
                        binance_spot: s.binance_spot,
                        binance_futures: s.binance_futures,
                        coinbase: s.coinbase,
                        bybit: s.bybit,
                        kraken: s.kraken,
                        okx: s.okx,
                        chainlink_rtds: s.chainlink_rtds,
                        pyth: s.pyth,
                        funding_rate: s.funding_rate,
                        open_interest: s.open_interest,
                        long_short_ratio: s.long_short_ratio,
                        long_liquidations: Some(s.long_liquidations),
                        short_liquidations: Some(s.short_liquidations),
                        orderbook_imbalance: s.orderbook_imbalance,
                        cvd: Some(s.cvd),
                        fear_greed: s.fear_greed,
                        // NEW RTDS fields
                        rtds_ts_ms: rtds_ts,
                        rtds_age_ms: rtds_age,
                        rtds_frozen_ms: rtds_frozen,
                        disloc_vs_rtds: disloc_usd,
                        disloc_vs_rtds_bps: disloc_bps,
                    };

                    session_data.snapshots.push(snapshot);
                    s.recorded_checkpoints.push(checkpoint_name.to_string());
                }
            }
        }

        // Render UI (1 second refresh - no spam due to crossterm)
        {
            let s = state.read().await;
            render_ui(&s, session_start, session_offset);
        }
    }
}

// =============================================================================
// ANALYZER
// =============================================================================

fn analyze_sessions(data_dir: &str, symbol: &str) -> Result<()> {
    let filename = format!("{}/multi_signal_sessions_{}.jsonl", data_dir, symbol.to_lowercase());

    println!("{}╔══════════════════════════════════════════════════════════════╗{}", CYAN, RESET);
    println!("{}║{}         MULTI-SIGNAL CORRELATION ANALYZER                    {}║{}", CYAN, BOLD, CYAN, RESET);
    println!("{}╚══════════════════════════════════════════════════════════════╝{}\n", CYAN, RESET);

    let file = std::fs::File::open(&filename)?;
    let reader = BufReader::new(file);

    let mut sessions: Vec<Session> = Vec::new();
    for line in reader.lines() {
        if let Ok(line) = line {
            if let Ok(session) = serde_json::from_str::<Session>(&line) {
                if session.winner.is_some() {
                    sessions.push(session);
                }
            }
        }
    }

    println!("Sessions with winners: {}", sessions.len());

    if sessions.is_empty() {
        println!("\nNo complete sessions found. Run --live longer.");
        return Ok(());
    }

    // Count winners
    let up_wins = sessions.iter().filter(|s| s.winner.as_deref() == Some("UP")).count();
    println!("UP wins: {} ({:.1}%)", up_wins, 100.0 * up_wins as f64 / sessions.len() as f64);
    println!("DOWN wins: {} ({:.1}%)\n", sessions.len() - up_wins, 100.0 * (sessions.len() - up_wins) as f64 / sessions.len() as f64);

    // Correlation table
    println!("{}CHECKPOINT  │ BIN_SPOT │ BIN_FUT  │ COINBASE │  BYBIT   │ CHAINLINK│   PYTH   │   CVD    │  OB_IMB{}", BOLD, RESET);
    println!("────────────┼──────────┼──────────┼──────────┼──────────┼──────────┼──────────┼──────────┼──────────");

    let price_sources = ["binance_spot", "binance_futures", "coinbase", "bybit", "chainlink_rtds", "pyth"];

    for (checkpoint_name, _) in CHECKPOINTS.iter().skip(1) {
        print!("{:>11} │", checkpoint_name);

        // Price sources
        for source in &price_sources {
            let mut correct = 0;
            let mut total = 0;

            for session in &sessions {
                let snapshots: std::collections::HashMap<_, _> = session.snapshots.iter()
                    .map(|s| (s.checkpoint.as_str(), s))
                    .collect();

                let t0 = snapshots.get("T+0");
                let cp = snapshots.get(*checkpoint_name);

                if let (Some(start), Some(current), Some(winner)) = (t0, cp, &session.winner) {
                    let get_price = |snap: &SignalSnapshot, src: &str| -> Option<f64> {
                        match src {
                            "binance_spot" => snap.binance_spot,
                            "binance_futures" => snap.binance_futures,
                            "coinbase" => snap.coinbase,
                            "bybit" => snap.bybit,
                            "chainlink_rtds" => snap.chainlink_rtds,
                            "pyth" => snap.pyth,
                            _ => None,
                        }
                    };

                    if let (Some(p0), Some(p1)) = (get_price(start, source), get_price(current, source)) {
                        let predicted = if p1 > p0 { "UP" } else { "DOWN" };
                        total += 1;
                        if predicted == winner { correct += 1; }
                    }
                }
            }

            if total > 0 {
                let pct = 100.0 * correct as f64 / total as f64;
                if pct >= 55.0 {
                    print!(" {}{:>6.1}%*{} │", GREEN, pct, RESET);
                } else {
                    print!(" {:>7.1}% │", pct);
                }
            } else {
                print!("    N/A   │");
            }
        }

        // CVD
        let mut cvd_correct = 0;
        let mut cvd_total = 0;
        for session in &sessions {
            let snapshots: std::collections::HashMap<_, _> = session.snapshots.iter()
                .map(|s| (s.checkpoint.as_str(), s))
                .collect();
            if let (Some(cp), Some(winner)) = (snapshots.get(*checkpoint_name), &session.winner) {
                if let Some(cvd) = cp.cvd {
                    let predicted = if cvd > 0.0 { "UP" } else { "DOWN" };
                    cvd_total += 1;
                    if predicted == winner { cvd_correct += 1; }
                }
            }
        }
        if cvd_total > 0 {
            let pct = 100.0 * cvd_correct as f64 / cvd_total as f64;
            if pct >= 55.0 {
                print!(" {}{:>6.1}%*{} │", GREEN, pct, RESET);
            } else {
                print!(" {:>7.1}% │", pct);
            }
        } else {
            print!("    N/A   │");
        }

        // OB Imbalance
        let mut ob_correct = 0;
        let mut ob_total = 0;
        for session in &sessions {
            let snapshots: std::collections::HashMap<_, _> = session.snapshots.iter()
                .map(|s| (s.checkpoint.as_str(), s))
                .collect();
            if let (Some(cp), Some(winner)) = (snapshots.get(*checkpoint_name), &session.winner) {
                if let Some(ob) = cp.orderbook_imbalance {
                    let predicted = if ob > 0.0 { "UP" } else { "DOWN" };
                    ob_total += 1;
                    if predicted == winner { ob_correct += 1; }
                }
            }
        }
        if ob_total > 0 {
            let pct = 100.0 * ob_correct as f64 / ob_total as f64;
            if pct >= 55.0 {
                print!(" {}{:>6.1}%*{}", GREEN, pct, RESET);
            } else {
                print!(" {:>7.1}%", pct);
            }
        } else {
            print!("    N/A  ");
        }

        println!();
    }

    println!("\n{}* = >55% accuracy (tradeable signal){}", GREEN, RESET);

    Ok(())
}

// =============================================================================
// MAIN
// =============================================================================

#[tokio::main]
async fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let mode = args.get(1).map(|s| s.as_str()).unwrap_or("--help");
    let symbol = args.get(2).map(|s| s.as_str()).unwrap_or("BTC");
    // LOG_DIR env var overrides CLI arg for versioned run directories
    let data_dir = std::env::var("LOG_DIR")
        .ok()
        .unwrap_or_else(|| args.get(3).map(|s| s.to_string()).unwrap_or_else(|| "./logs".to_string()));
    let data_dir = data_dir.as_str();

    match mode {
        "--live" => {
            std::fs::create_dir_all(data_dir)?;

            // === STARTUP PRINT ===
            let run_id: Option<String> = std::path::Path::new(data_dir)
                .file_name()
                .and_then(|s| s.to_str())
                .filter(|s| s.contains("_"))
                .map(|s| s.to_string());
            let start_time = Utc::now().to_rfc3339();

            eprintln!("┌─────────────────────────────────────────────────────────────┐");
            eprintln!("│ multi_signal_recorder v4.1 - OVERNIGHT COLLECTION          │");
            eprintln!("├─────────────────────────────────────────────────────────────┤");
            eprintln!("│ LOG_DIR:    {:<47}│", data_dir);
            eprintln!("│ RUN_ID:     {:<47}│", run_id.as_deref().unwrap_or("(none)"));
            eprintln!("│ SYMBOL:     {:<47}│", symbol);
            eprintln!("│ START:      {:<47}│", &start_time[..19]);
            eprintln!("└─────────────────────────────────────────────────────────────┘");

            // === WRITE CONFIG.JSON (ALL THRESHOLDS) ===
            let config = RunConfig {
                version: "4.1".to_string(),
                run_id: run_id.clone(),
                start_time: start_time.clone(),
                symbol: symbol.to_string(),
                // Move detection
                move_threshold_pct: MOVE_THRESHOLD_PCT,
                move_window_ms: MOVE_WINDOW_MS,
                cooldown_ms: COOLDOWN_MS,
                // Dislocation
                dislocation_threshold_usd: DISLOCATION_THRESHOLD_USD,
                dislocation_threshold_pct: DISLOCATION_THRESHOLD_PCT,
                dislocation_persist_secs: DISLOCATION_PERSIST_SECS,
                // Direction
                epsilon_flat_pct: EPSILON_FLAT_PCT,
                // Staleness/outlier
                staleness_ms: STALENESS_MS,
                outlier_pct: OUTLIER_PCT,
                // EV
                assumed_q: 0.55,
                // Schedule
                checkpoints: CHECKPOINTS.iter().map(|(n, _)| n.to_string()).collect(),
            };
            let config_path = format!("{}/config.json", data_dir);
            match std::fs::File::create(&config_path) {
                Ok(f) => {
                    if serde_json::to_writer_pretty(f, &config).is_err() {
                        eprintln!("FATAL: Failed to write config.json");
                        std::process::exit(1);
                    }
                    eprintln!("[startup] Wrote {}", config_path);
                }
                Err(e) => {
                    eprintln!("FATAL: Cannot create config.json: {}", e);
                    std::process::exit(1);
                }
            }

            // === WRITE RUN_START TO SESSIONS FILE ===
            let sessions_path = format!("{}/multi_signal_sessions_{}.jsonl", data_dir, symbol.to_lowercase());
            match OpenOptions::new().create(true).append(true).open(&sessions_path) {
                Ok(mut f) => {
                    let run_start = serde_json::json!({
                        "event": "RUN_START",
                        "run_id": run_id,
                        "start_time": start_time,
                        "symbol": symbol,
                        "version": "4.1"
                    });
                    if writeln!(f, "{}", run_start).is_err() || f.flush().is_err() {
                        eprintln!("FATAL: Failed to write RUN_START to sessions file");
                        std::process::exit(1);
                    }
                    eprintln!("[startup] Wrote RUN_START to {}", sessions_path);
                }
                Err(e) => {
                    eprintln!("FATAL: Cannot open sessions file: {}", e);
                    std::process::exit(1);
                }
            }

            // Setup terminal with crossterm (enters alt screen, hides cursor)
            setup_terminal();

            // Set up Ctrl+C handler to write RUN_END and restore terminal
            let sessions_path_clone = sessions_path.clone();
            let run_id_clone = run_id.clone();
            let _ = ctrlc::set_handler(move || {
                cleanup_terminal();
                // Write RUN_END to sessions file
                if let Ok(mut f) = OpenOptions::new().append(true).open(&sessions_path_clone) {
                    let run_end = serde_json::json!({
                        "event": "RUN_END",
                        "run_id": run_id_clone,
                        "end_time": Utc::now().to_rfc3339(),
                        "reason": "ctrl_c"
                    });
                    let _ = writeln!(f, "{}", run_end);
                    let _ = f.flush();
                    eprintln!("\n[shutdown] Wrote RUN_END to sessions file");
                }
                std::process::exit(0);
            });

            let state = Arc::new(RwLock::new(LiveState::default()));

            // Spawn all data sources
            let s = state.clone(); let sym = symbol.to_string();
            tokio::spawn(async move { binance_spot(s, &sym).await });

            let s = state.clone(); let sym = symbol.to_string();
            tokio::spawn(async move { binance_futures(s, &sym).await });

            let s = state.clone(); let sym = symbol.to_string();
            tokio::spawn(async move { coinbase(s, &sym).await });

            let s = state.clone(); let sym = symbol.to_string();
            tokio::spawn(async move { bybit(s, &sym).await });

            let s = state.clone();
            tokio::spawn(async move { kraken(s).await });

            let s = state.clone(); let sym = symbol.to_string();
            tokio::spawn(async move { okx(s, &sym).await });

            let s = state.clone(); let sym = symbol.to_string();
            tokio::spawn(async move { chainlink_rtds(s, &sym).await });

            let s = state.clone();
            tokio::spawn(async move { pyth_network(s).await });

            // Sentiment sources
            let s = state.clone(); let sym = symbol.to_string();
            tokio::spawn(async move { binance_funding_rate(s, &sym).await });

            let s = state.clone(); let sym = symbol.to_string();
            tokio::spawn(async move { binance_open_interest(s, &sym).await });

            let s = state.clone(); let sym = symbol.to_string();
            tokio::spawn(async move { binance_long_short_ratio(s, &sym).await });

            let s = state.clone(); let sym = symbol.to_string();
            tokio::spawn(async move { binance_liquidations(s, &sym).await });

            let s = state.clone(); let sym = symbol.to_string();
            tokio::spawn(async move { orderbook_imbalance(s, &sym).await });

            let s = state.clone(); let sym = symbol.to_string();
            tokio::spawn(async move { cvd_tracker(s, &sym).await });

            let s = state.clone();
            tokio::spawn(async move { fear_greed_index(s).await });

            // Run session recorder with UI
            session_recorder(state, symbol.to_string(), data_dir.to_string()).await;
        }
        "--analyze" => {
            analyze_sessions(data_dir, symbol)?;
        }
        _ => {
            println!("{}MULTI-SIGNAL RECORDER v4.0{}\n", BOLD, RESET);
            println!("Pro trader terminal with lead-lag detection and dislocation tracking.\n");
            println!("Usage:");
            println!("  {} --live [SYMBOL] [DATA_DIR]     Record signals with live UI", args[0]);
            println!("  {} --analyze [SYMBOL] [DATA_DIR]  Analyze correlation\n", args[0]);
            println!("Examples:");
            println!("  {} --live BTC ./logs", args[0]);
            println!("  {} --analyze BTC ./logs", args[0]);
        }
    }

    Ok(())
}
