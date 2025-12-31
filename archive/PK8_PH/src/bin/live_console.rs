use anyhow::{Context, Result};
use chrono::TimeZone;
use crossterm::{
    event::{self, Event, KeyCode, KeyEventKind},
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use pair_arb_backtester::engine::{
    clob_ws::MarketWs,
    gamma::GammaClient,
    live_data_ws::{parse_orders_matched_trade, LiveDataWs},
    metrics::{BookMetrics, WsBookMsg, WsLastTradePriceMsg, WsPriceChange, WsPriceChangeMsg},
    rollover,
    types::MarketEvent,
    user_ws::{UserWs, UserWsAuth},
};
use ratatui::{
    layout::Alignment,
    layout::{Constraint, Direction, Layout, Rect},
    style::{Color, Modifier, Style},
    widgets::{Block, Borders, Cell, Paragraph, Row, Table, TableState},
    Frame, Terminal,
};
use serde_json::json;
use std::{
    collections::BTreeMap,
    collections::HashMap,
    collections::HashSet,
    collections::VecDeque,
    io::IsTerminal,
    io::{self, Stdout},
    pin::Pin,
    str::FromStr,
    sync::Arc,
    sync::OnceLock,
    time::Duration,
};
use tokio::io::AsyncWriteExt;
use tokio::sync::RwLock;
use tracing::error;

use polymarket_rs::{
    Address as PmAddress, ApiCreds as PmApiCreds, AuthenticatedClient as PmAuthenticatedClient,
    CreateOrderOptions as PmCreateOrderOptions, OrderArgs as PmOrderArgs,
    OrderBuilder as PmOrderBuilder, OrderType as PmOrderType, PrivateKeySigner, Side as PmSide,
    SignatureType as PmSignatureType, TradingClient as PmTradingClient,
};
use rust_decimal::prelude::FromPrimitive;
use rust_decimal::Decimal;

#[path = "live_console/balance.rs"]
mod balance;
#[path = "live_console/paper.rs"]
mod paper;
#[path = "live_console/trade.rs"]
mod trade;
#[path = "live_console/ui.rs"]
mod ui;
#[path = "live_console/user_ws.rs"]
mod user_ws;
#[path = "live_console/ws.rs"]
mod ws;
#[path = "live_console/strategies/mod.rs"]
mod strategies;

pub(crate) use trade::ProbToxModel;
pub(crate) use user_ws::{funder_address_from_env, signature_type_from_env};
pub(crate) use ws::{key_to_price, price_to_key};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
enum Timeframe {
    M15,
    H1,
}

impl Timeframe {
    fn label(self) -> &'static str {
        match self {
            Timeframe::M15 => "15m",
            Timeframe::H1 => "1h",
        }
    }
}

#[derive(Debug, Clone)]
struct InstrumentSeed {
    symbol: &'static str,
    timeframe: Timeframe,
    // For 15m timestamp slugs, this is the prefix used for deriving current/next slug.
    // Example: "btc-updown-15m".
    timestamp_prefix: Option<&'static str>,
    // For hourly, we select current/next via series_id.
    series_id: Option<&'static str>,
}

const DEFAULT_WATCHLIST: &[InstrumentSeed] = &[
    InstrumentSeed {
        symbol: "BTC",
        timeframe: Timeframe::M15,
        timestamp_prefix: Some("btc-updown-15m"),
        series_id: None,
    },
    InstrumentSeed {
        symbol: "ETH",
        timeframe: Timeframe::M15,
        timestamp_prefix: Some("eth-updown-15m"),
        series_id: None,
    },
    InstrumentSeed {
        symbol: "SOL",
        timeframe: Timeframe::M15,
        timestamp_prefix: Some("sol-updown-15m"),
        series_id: None,
    },
    InstrumentSeed {
        symbol: "XRP",
        timeframe: Timeframe::M15,
        timestamp_prefix: Some("xrp-updown-15m"),
        series_id: None,
    },
    InstrumentSeed {
        symbol: "BTC",
        timeframe: Timeframe::H1,
        timestamp_prefix: None,
        series_id: Some("10114"),
    },
    InstrumentSeed {
        symbol: "ETH",
        timeframe: Timeframe::H1,
        timestamp_prefix: None,
        series_id: Some("10117"),
    },
    InstrumentSeed {
        symbol: "SOL",
        timeframe: Timeframe::H1,
        timestamp_prefix: None,
        series_id: Some("10122"),
    },
    InstrumentSeed {
        symbol: "XRP",
        timeframe: Timeframe::H1,
        timestamp_prefix: None,
        series_id: Some("10123"),
    },
];

#[derive(Debug, Clone)]
struct RunConfig {
    watchlist: Vec<InstrumentSeed>,
    trade_enabled: bool,
    dry_run: bool,
    tradelist: Vec<InstrumentSeed>,
}

impl RunConfig {
    fn from_env() -> Self {
        let watchlist = std::env::var("PM_WATCHLIST")
            .ok()
            .and_then(|s| parse_seed_list(&s).ok())
            .unwrap_or_else(|| DEFAULT_WATCHLIST.to_vec());

        let tradelist = std::env::var("PM_TRADELIST")
            .ok()
            .and_then(|s| parse_seed_list(&s).ok())
            .unwrap_or_else(|| {
                vec![InstrumentSeed {
                    symbol: "BTC",
                    timeframe: Timeframe::M15,
                    timestamp_prefix: Some("btc-updown-15m"),
                    series_id: None,
                }]
            });

        let trade_enabled = std::env::var("PM_TRADE_ENABLED")
            .ok()
            .map(|v| v == "1" || v.eq_ignore_ascii_case("true"))
            .unwrap_or(true);

        let dry_run = std::env::var("PM_DRY_RUN")
            .ok()
            .map(|v| v == "1" || v.eq_ignore_ascii_case("true"))
            .unwrap_or(true);

        Self {
            watchlist,
            trade_enabled,
            dry_run,
            tradelist,
        }
    }
}

fn parse_seed_list(spec: &str) -> Result<Vec<InstrumentSeed>> {
    // Format: "BTC:15m,ETH:1h" (case-insensitive)
    let mut out = Vec::new();
    let spec = spec.trim().trim_matches(|c| c == '"' || c == '\'');
    for part in spec
        .split(',')
        .map(|s| s.trim().trim_matches(|c| c == '"' || c == '\''))
        .filter(|s| !s.is_empty())
    {
        let (sym, tf) = part
            .split_once(':')
            .with_context(|| format!("invalid seed '{part}', expected SYMBOL:TF"))?;
        let symbol = sym.trim().to_uppercase();
        let timeframe = match tf.trim().to_lowercase().as_str() {
            "15m" => Timeframe::M15,
            "1h" | "60m" | "hour" | "hourly" => Timeframe::H1,
            other => anyhow::bail!("invalid timeframe '{other}' in '{part}'"),
        };

        let seed = seed_from_symbol_timeframe(&symbol, timeframe)
            .with_context(|| format!("unsupported market {symbol}:{tf}"))?;
        out.push(seed);
    }
    Ok(out)
}

fn seed_from_symbol_timeframe(symbol: &str, timeframe: Timeframe) -> Result<InstrumentSeed> {
    match (symbol, timeframe) {
        ("BTC", Timeframe::M15) => Ok(InstrumentSeed {
            symbol: "BTC",
            timeframe,
            timestamp_prefix: Some("btc-updown-15m"),
            series_id: None,
        }),
        ("ETH", Timeframe::M15) => Ok(InstrumentSeed {
            symbol: "ETH",
            timeframe,
            timestamp_prefix: Some("eth-updown-15m"),
            series_id: None,
        }),
        ("SOL", Timeframe::M15) => Ok(InstrumentSeed {
            symbol: "SOL",
            timeframe,
            timestamp_prefix: Some("sol-updown-15m"),
            series_id: None,
        }),
        ("XRP", Timeframe::M15) => Ok(InstrumentSeed {
            symbol: "XRP",
            timeframe,
            timestamp_prefix: Some("xrp-updown-15m"),
            series_id: None,
        }),
        ("BTC", Timeframe::H1) => Ok(InstrumentSeed {
            symbol: "BTC",
            timeframe,
            timestamp_prefix: None,
            series_id: Some("10114"),
        }),
        ("ETH", Timeframe::H1) => Ok(InstrumentSeed {
            symbol: "ETH",
            timeframe,
            timestamp_prefix: None,
            series_id: Some("10117"),
        }),
        ("SOL", Timeframe::H1) => Ok(InstrumentSeed {
            symbol: "SOL",
            timeframe,
            timestamp_prefix: None,
            series_id: Some("10122"),
        }),
        ("XRP", Timeframe::H1) => Ok(InstrumentSeed {
            symbol: "XRP",
            timeframe,
            timestamp_prefix: None,
            series_id: Some("10123"),
        }),
        _ => anyhow::bail!("unknown symbol/timeframe {symbol}:{:?}", timeframe),
    }
}

#[derive(Debug, Clone)]
struct OutcomeSnapshot {
    bids: BTreeMap<i64, f64>,
    asks: BTreeMap<i64, f64>,
    metrics: BookMetrics,
}

#[derive(Debug, Clone)]
struct StreamSnapshot {
    symbol: String,
    timeframe: Timeframe,
    slug: String,
    condition_id: Option<String>,
    token_up: Option<String>,
    token_down: Option<String>,
    order_min_size: Option<f64>,
    min_tick_size: Option<f64>,
    end_date: Option<chrono::DateTime<chrono::Utc>>,
    liquidity_clob: Option<f64>,
    up: Option<OutcomeSnapshot>,
    down: Option<OutcomeSnapshot>,
    ws_connected: bool,
    last_msg_at: Option<chrono::DateTime<chrono::Utc>>,
    last_server_latency_ms: Option<i64>,
    last_rtt_ms: Option<i64>,
    last_err: Option<String>,
    decision: String,
}

impl StreamSnapshot {
    fn new(symbol: &str, timeframe: Timeframe) -> Self {
        Self {
            symbol: symbol.to_string(),
            timeframe,
            slug: "".to_string(),
            condition_id: None,
            token_up: None,
            token_down: None,
            order_min_size: None,
            min_tick_size: None,
            end_date: None,
            liquidity_clob: None,
            up: None,
            down: None,
            ws_connected: false,
            last_msg_at: None,
            last_server_latency_ms: None,
            last_rtt_ms: None,
            last_err: None,
            decision: String::new(),
        }
    }
}

#[derive(Debug, Clone)]
struct PositionSnapshot {
    size: f64,
    avg_price: f64,
    updated_at: chrono::DateTime<chrono::Utc>,
}

#[derive(Debug, Clone)]
struct OrderSnapshot {
    order_id: String,
    symbol: String,
    timeframe: Timeframe,
    outcome: String,
    token_id: String,
    market: String,
    side: String,
    price: f64,
    original_size: f64,
    size_matched: f64,
    last_event: String,
    last_update_at: chrono::DateTime<chrono::Utc>,
    // Filled in when orders are created by our strategy/broker (future).
    submitted_at: Option<chrono::DateTime<chrono::Utc>>,
    placed_at: Option<chrono::DateTime<chrono::Utc>>,
    // === Order telemetry fields (for orders.jsonl) ===
    submit_ts_ms: Option<i64>,          // Epoch ms at submit
    best_bid_at_submit: Option<f64>,    // Best bid price at submit
    best_ask_at_submit: Option<f64>,    // Best ask price at submit
    strategy_id: Option<String>,        // Strategy name
    reason_codes: Vec<String>,          // Decision reason codes
}

fn order_remaining(o: &OrderSnapshot) -> f64 {
    (o.original_size - o.size_matched).max(0.0)
}

fn order_is_open(o: &OrderSnapshot) -> bool {
    if order_remaining(o) <= 1e-9 {
        return false;
    }
    let ev = o.last_event.to_uppercase();
    if ev.contains("CANCEL")
        || ev.contains("CANCELL")
        || ev.contains("REJECT")
        || ev.contains("EXPIRE")
        || ev.contains("FAILED")
    {
        return false;
    }
    true
}

fn canon_id(s: &str) -> String {
    s.trim().to_lowercase()
}

type SharedState = Arc<RwLock<HashMap<(String, Timeframe), StreamSnapshot>>>;
type SharedLogs = Arc<RwLock<VecDeque<String>>>;
type SharedPositions = Arc<RwLock<HashMap<String, PositionSnapshot>>>;
type SharedOrders = Arc<RwLock<HashMap<String, OrderSnapshot>>>;
type SharedModels = Arc<RwLock<HashMap<(String, Timeframe), ProbToxModel>>>;
type SharedCash = Arc<RwLock<Option<f64>>>;
type SharedSeenTrades = Arc<RwLock<HashSet<String>>>;
type SharedSeenTradeFills = Arc<RwLock<HashSet<String>>>;
type SharedLatency = Arc<RwLock<LatencyStats>>;

pub(crate) type TradeTick = (String, Timeframe);
pub(crate) type TradeTickTx = tokio::sync::mpsc::UnboundedSender<TradeTick>;
pub(crate) type TradeTickRx = tokio::sync::mpsc::UnboundedReceiver<TradeTick>;

static FILE_LOG_TX: OnceLock<tokio::sync::mpsc::UnboundedSender<String>> = OnceLock::new();
static FILE_JSONL_TX: OnceLock<tokio::sync::mpsc::UnboundedSender<String>> = OnceLock::new();
static ORDERS_JSONL_TX: OnceLock<tokio::sync::mpsc::UnboundedSender<String>> = OnceLock::new();
static ORDERS_JSONL_ENABLED: OnceLock<bool> = OnceLock::new();
static RUN_START_UTC: OnceLock<chrono::DateTime<chrono::Utc>> = OnceLock::new();

fn run_start_utc() -> chrono::DateTime<chrono::Utc> {
    *RUN_START_UTC.get_or_init(chrono::Utc::now)
}

async fn set_decision(state: &SharedState, symbol: &str, tf: Timeframe, decision: String) {
    // Log all strategy decisions to file for debugging
    if !decision.is_empty() {
        if let Some(tx) = FILE_LOG_TX.get() {
            let ts = chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true);
            let _ = tx.send(format!("{ts} [strat] [{} {}] {decision}", symbol, tf.label()));
        }
    }
    let mut g = state.write().await;
    if let Some(s) = g.get_mut(&(symbol.to_string(), tf)) {
        s.decision = decision;
    }
}

fn emit_jsonl_event(event_type: &str, fields: serde_json::Value) {
    let Some(tx) = FILE_JSONL_TX.get() else {
        return;
    };
    let obj = json!({
        "ts": chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true),
        "run_start": run_start_utc().to_rfc3339_opts(chrono::SecondsFormat::Millis, true),
        "event": event_type,
        "data": fields,
    });
    let Ok(line) = serde_json::to_string(&obj) else {
        return;
    };
    let _ = tx.send(line);
}

/// Order lifecycle telemetry event for orders.jsonl
#[derive(Debug, Clone, serde::Serialize)]
pub(crate) struct OrderTelemetry {
    // === Versioning fields ===
    #[serde(skip_serializing_if = "Option::is_none")]
    pub run_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub schema_version: Option<u32>,
    // === Order identification ===
    pub session_id: String,        // condition_id - the real 15-min market identifier
    #[serde(skip_serializing_if = "Option::is_none")]
    pub token_id: Option<String>,  // token_id - UP or DOWN token for this market
    pub strategy_id: String,
    pub order_id: String,
    pub outcome: String,           // UP/DOWN
    pub side: String,              // BUY/SELL
    pub action: String,            // SUBMIT|ACK|FILL|CANCEL|REJECT
    pub submit_ts_ms: i64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ack_ts_ms: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fill_ts_ms: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ack_ms: Option<i64>,       // ack_ts - submit_ts
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fill_ms: Option<i64>,      // fill_ts - submit_ts
    pub fill_pct: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub avg_fill_q: Option<f64>,   // avg fill price (probability 0..1)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub best_bid_q_at_submit: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub best_ask_q_at_submit: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub spread_bps_at_submit: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub slippage_vs_mid_bps: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cancel_reason: Option<String>,
    pub reason_codes: Vec<String>,
}

/// Check if orders.jsonl logging is enabled (ENABLE_ORDERS_JSONL env var)
fn orders_jsonl_enabled() -> bool {
    *ORDERS_JSONL_ENABLED.get_or_init(|| {
        std::env::var("ENABLE_ORDERS_JSONL")
            .ok()
            .map(|v| v == "1" || v.eq_ignore_ascii_case("true"))
            .unwrap_or(false) // Default: disabled for safety
    })
}

/// Get run_id from LOG_DIR env var (e.g., "./logs/runs/20251221_0400_V1" -> "20251221_0400_V1")
fn get_run_id() -> Option<String> {
    static RUN_ID: OnceLock<Option<String>> = OnceLock::new();
    RUN_ID.get_or_init(|| {
        std::env::var("LOG_DIR")
            .ok()
            .and_then(|dir| {
                std::path::Path::new(&dir)
                    .file_name()
                    .and_then(|s| s.to_str())
                    .filter(|s| s.contains("_V"))
                    .map(|s| s.to_string())
            })
    }).clone()
}

/// Emit order telemetry event (non-blocking, safe to fail)
/// Includes sanity checks (warn only, never crash)
pub(crate) fn emit_order_event(telemetry: &OrderTelemetry) {
    // Feature flag check - early return if disabled
    if !orders_jsonl_enabled() {
        return;
    }
    let Some(tx) = ORDERS_JSONL_TX.get() else {
        return;
    };

    // === SANITY CHECKS (non-fatal warnings only) ===
    if let Some(ack_ms) = telemetry.ack_ms {
        if ack_ms < 0 {
            eprintln!("[orders.jsonl] WARN: negative ack_ms={} for order {}", ack_ms, telemetry.order_id);
        }
    }
    if let Some(fill_ms) = telemetry.fill_ms {
        if fill_ms < 0 {
            eprintln!("[orders.jsonl] WARN: negative fill_ms={} for order {}", fill_ms, telemetry.order_id);
        }
    }
    if telemetry.action == "FILL" && telemetry.avg_fill_q.is_none() {
        eprintln!("[orders.jsonl] WARN: FILL event missing avg_fill_q for order {}", telemetry.order_id);
    }
    if telemetry.action == "SUBMIT" && telemetry.best_bid_q_at_submit.is_none() && telemetry.best_ask_q_at_submit.is_none() {
        // Only warn if we expected book snapshot - not always available
        // eprintln!("[orders.jsonl] WARN: SUBMIT missing book snapshot for order {}", telemetry.order_id);
    }

    // Add run_id and schema_version if in versioned run
    let mut enriched = telemetry.clone();
    if enriched.run_id.is_none() {
        enriched.run_id = get_run_id();
    }
    if enriched.run_id.is_some() && enriched.schema_version.is_none() {
        enriched.schema_version = Some(1);
    }

    let Ok(line) = serde_json::to_string(&enriched) else {
        return;
    };
    // Non-blocking send - ignore errors to never affect trading
    let _ = tx.send(line);
}

#[derive(Debug, Clone, Default)]
struct LatencyStats {
    last_post_ms: Option<i64>,
    max_post_ms: Option<i64>,
    last_cancel_req_ms: Option<i64>,
    max_cancel_req_ms: Option<i64>,
    last_cancel_clear_ms: Option<i64>,
    max_cancel_clear_ms: Option<i64>,
    last_place_ms: Option<i64>,
    max_place_ms: Option<i64>,
    last_trade_mined_ms: Option<i64>,
    max_trade_mined_ms: Option<i64>,
    last_trade_confirm_ms: Option<i64>,
    max_trade_confirm_ms: Option<i64>,
}

impl LatencyStats {
    fn bump(slot: &mut Option<i64>, v: i64) {
        if v < 0 {
            return;
        }
        *slot = Some(slot.map(|m| m.max(v)).unwrap_or(v));
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    let _ = dotenvy::dotenv();
    let _ = RUN_START_UTC.set(chrono::Utc::now());
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .with_target(false)
        .init();

    let gamma = GammaClient::new("https://gamma-api.polymarket.com/")?;

    let cfg = RunConfig::from_env();
    let paper_trading = std::env::var("PM_PAPER_TRADING")
        .ok()
        .is_some_and(|v| v == "1" || v.eq_ignore_ascii_case("true"));

    let state: SharedState = Arc::new(RwLock::new(HashMap::new()));
    let logs: SharedLogs = Arc::new(RwLock::new(VecDeque::new()));
    let positions: SharedPositions = Arc::new(RwLock::new(HashMap::new()));
    let orders: SharedOrders = Arc::new(RwLock::new(HashMap::new()));
    let models: SharedModels = Arc::new(RwLock::new(HashMap::new()));
    let cash_usdc: SharedCash = Arc::new(RwLock::new(None));
    let seen_trades: SharedSeenTrades = Arc::new(RwLock::new(HashSet::new()));
    let seen_trade_fills: SharedSeenTradeFills = Arc::new(RwLock::new(HashSet::new()));
    let latencies: SharedLatency = Arc::new(RwLock::new(LatencyStats::default()));

    let (trade_tick_tx, trade_tick_rx): (Option<TradeTickTx>, Option<TradeTickRx>) =
        if cfg.trade_enabled {
            let (tx, rx) = tokio::sync::mpsc::unbounded_channel::<TradeTick>();
            (Some(tx), Some(rx))
        } else {
            (None, None)
        };

    init_file_logging().await;

    // Startup print for orders.jsonl validation
    {
        let log_dir = std::env::var("LOG_DIR").unwrap_or_else(|_| "(not set)".to_string());
        let enable_orders = std::env::var("ENABLE_ORDERS_JSONL").unwrap_or_else(|_| "(not set)".to_string());
        let writer_on = orders_jsonl_enabled();
        let orders_path = if let Ok(dir) = std::env::var("LOG_DIR") {
            format!("{}/orders.jsonl", dir)
        } else {
            std::env::var("PM_ORDERS_JSONL_FILE").unwrap_or_else(|_| "logs/orders.jsonl".to_string())
        };
        eprintln!("┌─────────────────────────────────────────────────────────────┐");
        eprintln!("│ orders.jsonl validation                                     │");
        eprintln!("├─────────────────────────────────────────────────────────────┤");
        eprintln!("│ LOG_DIR:              {:<38}│", log_dir);
        eprintln!("│ ENABLE_ORDERS_JSONL:  {:<38}│", enable_orders);
        eprintln!("│ orders writer:        {:<38}│", if writer_on { "ON" } else { "OFF" });
        eprintln!("│ output file:          {:<38}│", orders_path);
        eprintln!("└─────────────────────────────────────────────────────────────┘");
    }

    {
        let mut guard = state.write().await;
        for seed in &cfg.watchlist {
            guard.insert(
                (seed.symbol.to_string(), seed.timeframe),
                StreamSnapshot::new(seed.symbol, seed.timeframe),
            );
        }
    }

    for seed in cfg.watchlist.clone() {
        let gamma = gamma.clone();
        let state = Arc::clone(&state);
        let logs = Arc::clone(&logs);
        let tick_tx = trade_tick_tx.clone();
        tokio::spawn(async move {
            loop {
                let ws = match MarketWs::new("wss://ws-subscriptions-clob.polymarket.com/ws/market")
                {
                    Ok(ws) => ws,
                    Err(e) => {
                        error!(symbol = seed.symbol, tf = seed.timeframe.label(), err = %e, "ws url invalid");
                        tokio::time::sleep(Duration::from_secs(30)).await;
                        continue;
                    }
                };
                if let Err(e) = ws::stream_runner(
                    gamma.clone(),
                    ws,
                    Arc::clone(&state),
                    Arc::clone(&logs),
                    seed.clone(),
                    tick_tx.clone(),
                )
                .await
                {
                    error!(symbol = seed.symbol, tf = seed.timeframe.label(), err = %e, "stream runner failed");
                    push_log(
                        &logs,
                        format!(
                            "[{} {}] runner error: {e}",
                            seed.symbol,
                            seed.timeframe.label()
                        ),
                    )
                    .await;
                    tokio::time::sleep(Duration::from_secs(5)).await;
                    continue;
                }
                break;
            }
        });
    }

    if !paper_trading {
        let logs = Arc::clone(&logs);
        let orders = Arc::clone(&orders);
        let positions = Arc::clone(&positions);
        let models = Arc::clone(&models);
        let seen_trades = Arc::clone(&seen_trades);
        let seen_trade_fills = Arc::clone(&seen_trade_fills);
        let latencies = Arc::clone(&latencies);
        tokio::spawn(async move {
            let Ok(Some((_signer, creds))) = trade::derive_api_creds_from_env().await else {
                push_log(
                    &logs,
                    "[user-ws] disabled (missing PM_PRIVATE_KEY)".to_string(),
                )
                .await;
                return;
            };
            let auth = UserWsAuth {
                api_key: creds.api_key,
                api_secret: creds.secret,
                api_passphrase: creds.passphrase,
            };
            if let Err(e) = user_ws::user_ws_loop(
                logs,
                orders,
                positions,
                models,
                seen_trades,
                seen_trade_fills,
                latencies,
                auth,
            )
            .await
            {
                eprintln!("user ws loop error: {e:#}");
            }
        });
    }

    if !paper_trading
        && (std::env::var("PM_LIVE_WALLET_ADDRESS").is_ok()
            || std::env::var("PM_FUNDER_ADDRESS").is_ok()
            || std::env::var("PM_PRIVATE_KEY").is_ok())
    {
        let logs = Arc::clone(&logs);
        let cash = Arc::clone(&cash_usdc);
        tokio::spawn(async move {
            if let Err(e) = balance::usdc_balance_loop(cash, logs).await {
                eprintln!("usdc balance loop error: {e:#}");
            }
        });
    }

    if cfg.trade_enabled {
        let state = Arc::clone(&state);
        let logs = Arc::clone(&logs);
        let positions = Arc::clone(&positions);
        let orders = Arc::clone(&orders);
        let models = Arc::clone(&models);
        let tick_rx = trade_tick_rx.expect("trade_enabled implies tick receiver");
        let cash_usdc = Arc::clone(&cash_usdc);
        let latencies = Arc::clone(&latencies);
        let trade_cfg = cfg.clone();
        tokio::spawn(async move {
            if let Err(e) = trade::trade_loop(
                state,
                logs,
                positions,
                orders,
                models,
                trade_cfg,
                tick_rx,
                cash_usdc,
                latencies,
            )
            .await
            {
                // keep it out of the TUI; logs pane will show it.
                eprintln!("trade loop error: {e:#}");
            }
        });
    }

    if io::stdout().is_terminal() {
            ui::run_tui(state, logs, positions, orders, cash_usdc, latencies).await?;
    } else {
        ui::run_plain(state).await?;
    }
    Ok(())
}

async fn push_log(logs: &SharedLogs, line: String) {
    let file_line = line.clone();
    let mut g = logs.write().await;
    if g.len() >= 200 {
        g.pop_front();
    }
    g.push_back(line);

    if let Some(tx) = FILE_LOG_TX.get() {
        let ts = chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true);
        let _ = tx.send(format!("{ts} {file_line}"));
    }
}

async fn init_file_logging() {
    let log_enabled = std::env::var("PM_LOG_TO_FILE")
        .ok()
        .map(|v| v == "1" || v.eq_ignore_ascii_case("true"))
        .unwrap_or(true);

    if log_enabled {
        let path =
            std::env::var("PM_LOG_FILE").unwrap_or_else(|_| "logs/live_console.log".to_string());
        let path = std::path::PathBuf::from(path);
        if let Some(parent) = path.parent() {
            let _ = tokio::fs::create_dir_all(parent).await;
        }

        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<String>();
        if FILE_LOG_TX.set(tx).is_ok() {
            tokio::spawn(async move {
                let mut file = match tokio::fs::OpenOptions::new()
                    .create(true)
                    .append(true)
                    .open(&path)
                    .await
                {
                    Ok(f) => f,
                    Err(e) => {
                        eprintln!("failed to open log file {}: {e}", path.display());
                        return;
                    }
                };

                while let Some(line) = rx.recv().await {
                    if let Err(e) = file.write_all(line.as_bytes()).await {
                        eprintln!("log file write error {}: {e}", path.display());
                        return;
                    }
                    if let Err(e) = file.write_all(b"\n").await {
                        eprintln!("log file write error {}: {e}", path.display());
                        return;
                    }
                }
            });
        }
    }

    let json_enabled = std::env::var("PM_LOG_JSONL")
        .ok()
        .map(|v| v == "1" || v.eq_ignore_ascii_case("true"))
        .unwrap_or(true);

    if json_enabled {
        let path = std::env::var("PM_JSONL_LOG_FILE")
            .unwrap_or_else(|_| "logs/live_console_events.jsonl".to_string());
        let path = std::path::PathBuf::from(path);
        if let Some(parent) = path.parent() {
            let _ = tokio::fs::create_dir_all(parent).await;
        }

        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<String>();
        if FILE_JSONL_TX.set(tx).is_ok() {
            tokio::spawn(async move {
                let mut file = match tokio::fs::OpenOptions::new()
                    .create(true)
                    .append(true)
                    .open(&path)
                    .await
                {
                    Ok(f) => f,
                    Err(e) => {
                        eprintln!("failed to open jsonl log {}: {e}", path.display());
                        return;
                    }
                };

                while let Some(line) = rx.recv().await {
                    if let Err(e) = file.write_all(line.as_bytes()).await {
                        eprintln!("jsonl log write error {}: {e}", path.display());
                        return;
                    }
                    if let Err(e) = file.write_all(b"\n").await {
                        eprintln!("jsonl log write error {}: {e}", path.display());
                        return;
                    }
                }
            });
        }
    }

    // === orders.jsonl - execution telemetry (with A/B test routing) ===
    {
        // LOG_DIR overrides default path for versioned run directories
        let log_dir = std::env::var("LOG_DIR").unwrap_or_else(|_| "logs".to_string());
        let ab_test = std::env::var("AB_TEST")
            .ok()
            .is_some_and(|v| v == "1" || v.eq_ignore_ascii_case("true"));

        let base_path = std::path::PathBuf::from(&log_dir);
        if let Some(parent) = base_path.parent() {
            let _ = tokio::fs::create_dir_all(parent).await;
        }
        let _ = tokio::fs::create_dir_all(&base_path).await;

        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<String>();
        if ORDERS_JSONL_TX.set(tx).is_ok() {
            tokio::spawn(async move {
                // In AB_TEST mode, maintain separate file handles per strategy
                let mut files: std::collections::HashMap<String, tokio::fs::File> = std::collections::HashMap::new();

                // Default file for non-AB mode or unknown strategies
                let default_path = base_path.join("orders.jsonl");
                let mut default_file = match tokio::fs::OpenOptions::new()
                    .create(true)
                    .append(true)
                    .open(&default_path)
                    .await
                {
                    Ok(f) => Some(f),
                    Err(e) => {
                        eprintln!("failed to open orders.jsonl {}: {e}", default_path.display());
                        None
                    }
                };

                while let Some(line) = rx.recv().await {
                    // In AB_TEST mode, route by strategy_id
                    let target_file = if ab_test {
                        // Extract strategy_id from JSON
                        let strategy_id = serde_json::from_str::<serde_json::Value>(&line)
                            .ok()
                            .and_then(|v| v.get("strategy_id")?.as_str().map(|s| s.to_string()));

                        if let Some(ref sid) = strategy_id {
                            // Get or create file handle for this strategy
                            if !files.contains_key(sid) {
                                let path = base_path.join(format!("orders_{}.jsonl", sid));
                                if let Ok(f) = tokio::fs::OpenOptions::new()
                                    .create(true)
                                    .append(true)
                                    .open(&path)
                                    .await
                                {
                                    files.insert(sid.clone(), f);
                                    eprintln!("[AB_TEST] Created {}", path.display());
                                }
                            }
                            files.get_mut(sid)
                        } else {
                            default_file.as_mut()
                        }
                    } else {
                        default_file.as_mut()
                    };

                    if let Some(file) = target_file {
                        if let Err(e) = file.write_all(line.as_bytes()).await {
                            eprintln!("orders.jsonl write error: {e}");
                            continue;
                        }
                        if let Err(e) = file.write_all(b"\n").await {
                            eprintln!("orders.jsonl write error: {e}");
                            continue;
                        }
                        let _ = file.flush().await;
                    }
                }
            });
        }
    }
}

fn parse_ts_utc(s: &str) -> Option<chrono::DateTime<chrono::Utc>> {
    chrono::DateTime::parse_from_rfc3339(s)
        .ok()
        .map(|dt| dt.with_timezone(&chrono::Utc))
        .or_else(|| {
            let raw = s.trim();
            if raw.is_empty() {
                return None;
            }
            let v: i64 = raw.parse().ok()?;
            // Support both seconds and milliseconds epoch timestamps.
            let (sec, nsec) = if v.abs() > 1_000_000_000_000 {
                (v / 1000, ((v % 1000).abs() as u32) * 1_000_000)
            } else {
                (v, 0u32)
            };
            chrono::Utc.timestamp_opt(sec, nsec).single()
        })
}
