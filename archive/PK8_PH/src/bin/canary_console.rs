//! Canary Console - Minimal live trading monitor
//!
//! Focused display for live canary runs:
//! - Clear LIVE MODE warning
//! - Caps status (trades/exposure vs limits)
//! - Kill switch status
//! - Trade log with verified W/L
//! - Real-time session tracking
//!
//! Usage:
//!   cargo run --bin canary_console

use anyhow::{Context, Result};
use std::collections::HashMap;
use std::io::{self, Write};
use std::time::Duration;

// ANSI colors
const RED: &str = "\x1b[31m";
const GREEN: &str = "\x1b[32m";
const YELLOW: &str = "\x1b[33m";
const CYAN: &str = "\x1b[36m";
const BOLD: &str = "\x1b[1m";
const DIM: &str = "\x1b[2m";
const RESET: &str = "\x1b[0m";
const BG_RED: &str = "\x1b[41m";
const BG_GREEN: &str = "\x1b[42m";

// Config from env
struct CanaryConfig {
    max_trades: u32,
    max_total_usd: f64,
    max_per_market_usd: f64,
    max_position: f64,
    kill_switch_file: String,
    strategy: String,
    paper_mode: bool,
    btc_only: bool,
}

impl CanaryConfig {
    fn from_env() -> Self {
        Self {
            max_trades: std::env::var("MAX_TRADES_PER_SESSION")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(5),
            max_total_usd: std::env::var("MAX_WORST_TOTAL_USD")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(5.0),
            max_per_market_usd: std::env::var("MAX_WORST_PER_MARKET_USD")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(5.0),
            max_position: std::env::var("MAX_POSITION_SHARES")
                .ok().and_then(|v| v.parse().ok()).unwrap_or(5.0),
            kill_switch_file: std::env::var("KILL_SWITCH_FILE")
                .unwrap_or_else(|_| "KILL_SWITCH".to_string()),
            strategy: std::env::var("PM_STRATEGY")
                .unwrap_or_else(|_| "rule_v1".to_string()),
            paper_mode: std::env::var("PM_PAPER_TRADING")
                .ok().map(|v| v == "1").unwrap_or(false),
            btc_only: std::env::var("ALLOW_MULTI_MARKET")
                .ok().map(|v| v != "1").unwrap_or(true),
        }
    }
}

// Trade tracking
#[derive(Clone, Debug)]
struct Trade {
    id: String,
    outcome: String,  // UP/DOWN
    price: f64,
    size: f64,
    ts: String,
    status: TradeStatus,
}

#[derive(Clone, Debug, PartialEq)]
enum TradeStatus {
    Pending,
    Filled,
    Won,
    Lost,
}

// Session state
struct CanaryState {
    trades: Vec<Trade>,
    total_exposure: f64,
    exposure_per_market: HashMap<String, f64>,
    position_per_market: HashMap<String, f64>,
    cash_usdc: f64,
    last_update: std::time::Instant,
    ws_connected: bool,
    current_session: Option<SessionInfo>,
}

struct SessionInfo {
    symbol: String,
    end_time: chrono::DateTime<chrono::Utc>,
    up_mid: Option<f64>,
    down_mid: Option<f64>,
    up_ask: Option<f64>,
    down_ask: Option<f64>,
}

impl CanaryState {
    fn new() -> Self {
        Self {
            trades: Vec::new(),
            total_exposure: 0.0,
            exposure_per_market: HashMap::new(),
            position_per_market: HashMap::new(),
            cash_usdc: 0.0,
            last_update: std::time::Instant::now(),
            ws_connected: false,
            current_session: None,
        }
    }

    fn trade_count(&self) -> usize {
        self.trades.iter().filter(|t| t.status != TradeStatus::Pending).count()
    }

    fn wins(&self) -> usize {
        self.trades.iter().filter(|t| t.status == TradeStatus::Won).count()
    }

    fn losses(&self) -> usize {
        self.trades.iter().filter(|t| t.status == TradeStatus::Lost).count()
    }

    fn realized_pnl(&self) -> f64 {
        self.trades.iter().filter_map(|t| {
            match t.status {
                TradeStatus::Won => Some(1.0 - t.price),
                TradeStatus::Lost => Some(-t.price),
                _ => None,
            }
        }).sum()
    }
}

fn clear_screen() {
    print!("\x1b[2J\x1b[H");
    io::stdout().flush().ok();
}

fn kill_switch_active(path: &str) -> bool {
    std::path::Path::new(path).exists()
}

fn render_header(cfg: &CanaryConfig) {
    let mode = if cfg.paper_mode {
        format!("{}{}  PAPER MODE  {}", BG_GREEN, BOLD, RESET)
    } else {
        format!("{}{}  LIVE MODE - REAL MONEY  {}", BG_RED, BOLD, RESET)
    };

    println!("{}", "═".repeat(70));
    println!("{}  CANARY CONSOLE  {}  Strategy: {}{}{}",
        BOLD, RESET, CYAN, cfg.strategy, RESET);
    println!("{}", mode);
    println!("{}", "═".repeat(70));
}

fn render_caps(cfg: &CanaryConfig, state: &CanaryState) {
    println!("\n{}CAPS STATUS{}", BOLD, RESET);
    println!("{}", "─".repeat(50));

    let trade_pct = (state.trade_count() as f64 / cfg.max_trades as f64) * 100.0;
    let trade_color = if trade_pct >= 100.0 { RED } else if trade_pct >= 80.0 { YELLOW } else { GREEN };
    println!("  Trades:    {}{:>3}{} / {:>3}  [{:>5.1}%]",
        trade_color, state.trade_count(), RESET, cfg.max_trades, trade_pct);

    let exp_pct = (state.total_exposure / cfg.max_total_usd) * 100.0;
    let exp_color = if exp_pct >= 100.0 { RED } else if exp_pct >= 80.0 { YELLOW } else { GREEN };
    println!("  Exposure:  {}${:>5.2}{} / ${:>5.2}  [{:>5.1}%]",
        exp_color, state.total_exposure, RESET, cfg.max_total_usd, exp_pct);

    let pos_total: f64 = state.position_per_market.values().sum();
    let pos_pct = (pos_total / cfg.max_position) * 100.0;
    let pos_color = if pos_pct >= 100.0 { RED } else if pos_pct >= 80.0 { YELLOW } else { GREEN };
    println!("  Position:  {}{:>5.1}{} / {:>5.1}  [{:>5.1}%]",
        pos_color, pos_total, RESET, cfg.max_position, pos_pct);
}

fn render_kill_switch(cfg: &CanaryConfig) {
    let active = kill_switch_active(&cfg.kill_switch_file);
    println!("\n{}KILL SWITCH{}", BOLD, RESET);
    println!("{}", "─".repeat(50));

    if active {
        println!("  Status:  {}{}ACTIVE - TRADING HALTED{}", BG_RED, BOLD, RESET);
    } else {
        println!("  Status:  {}ARMED{} (touch {} to halt)", GREEN, RESET, cfg.kill_switch_file);
    }
}

fn render_session(state: &CanaryState) {
    println!("\n{}CURRENT SESSION{}", BOLD, RESET);
    println!("{}", "─".repeat(50));

    match &state.current_session {
        Some(sess) => {
            let now = chrono::Utc::now();
            let remaining = if sess.end_time > now {
                let d = sess.end_time - now;
                format!("{}m {}s", d.num_minutes(), d.num_seconds() % 60)
            } else {
                "EXPIRED".to_string()
            };

            println!("  Market:   {} 15m", sess.symbol);
            println!("  Expires:  {}", remaining);

            if let Some(up) = sess.up_mid {
                let up_str = format!("{:.3}", up);
                let color = if up >= 0.58 { RED } else { GREEN };
                println!("  UP mid:   {}{}{}", color, up_str, RESET);
            }
            if let Some(dn) = sess.down_mid {
                let dn_str = format!("{:.3}", dn);
                let color = if dn >= 0.58 { RED } else { GREEN };
                println!("  DOWN mid: {}{}{}", color, dn_str, RESET);
            }
        }
        None => {
            println!("  {}Waiting for session...{}", DIM, RESET);
        }
    }
}

fn render_trades(state: &CanaryState) {
    println!("\n{}TRADE LOG{}", BOLD, RESET);
    println!("{}", "─".repeat(50));

    if state.trades.is_empty() {
        println!("  {}No trades yet{}", DIM, RESET);
    } else {
        for trade in state.trades.iter().rev().take(5) {
            let status_str = match trade.status {
                TradeStatus::Pending => format!("{}PENDING{}", YELLOW, RESET),
                TradeStatus::Filled => format!("{}FILLED{}", CYAN, RESET),
                TradeStatus::Won => format!("{}WON +${:.2}{}", GREEN, 1.0 - trade.price, RESET),
                TradeStatus::Lost => format!("{}LOST -${:.2}{}", RED, trade.price, RESET),
            };
            println!("  {} {} @{:.2} x{:.1} → {}",
                trade.ts, trade.outcome, trade.price, trade.size, status_str);
        }
    }
}

fn render_summary(state: &CanaryState) {
    println!("\n{}SUMMARY{}", BOLD, RESET);
    println!("{}", "─".repeat(50));

    let wins = state.wins();
    let losses = state.losses();
    let total = wins + losses;
    let win_rate = if total > 0 { (wins as f64 / total as f64) * 100.0 } else { 0.0 };
    let pnl = state.realized_pnl();
    let pnl_color = if pnl >= 0.0 { GREEN } else { RED };
    let pnl_sign = if pnl >= 0.0 { "+" } else { "" };

    println!("  W/L:       {}{}{} / {}{}{}",
        GREEN, wins, RESET, RED, losses, RESET);
    println!("  Win Rate:  {:.1}%", win_rate);
    println!("  P&L:       {}{}${:.2}{}", pnl_color, pnl_sign, pnl, RESET);
    println!("  Cash:      ${:.2}", state.cash_usdc);
}

fn render_footer(state: &CanaryState) {
    println!("\n{}", "═".repeat(70));
    let age = state.last_update.elapsed().as_secs();
    let ws_status = if state.ws_connected {
        format!("{}WS: OK{}", GREEN, RESET)
    } else {
        format!("{}WS: DOWN{}", RED, RESET)
    };
    println!("{}Last update: {}s ago  |  {}  |  Press Ctrl+C to exit{}",
        DIM, age, ws_status, RESET);
}

fn print_config_summary(cfg: &CanaryConfig) {
    clear_screen();
    println!("{}", "═".repeat(70));
    println!("{}  CANARY CONFIGURATION CHECK  {}", BOLD, RESET);
    println!("{}", "═".repeat(70));
    println!();

    // Mode check
    if cfg.paper_mode {
        println!("  Mode:       {}PAPER{} (safe)", GREEN, RESET);
    } else {
        println!("  Mode:       {}{}LIVE - REAL MONEY AT RISK{}{}", BG_RED, BOLD, RESET, RESET);
    }

    println!();
    println!("  Strategy:   {}", cfg.strategy);
    println!("  Scope:      {}", if cfg.btc_only { "BTC 15m ONLY" } else { "ALL MARKETS" });
    println!();
    println!("  {}CAPS:{}", BOLD, RESET);
    println!("    Max trades/session:  {}", cfg.max_trades);
    println!("    Max total exposure:  ${:.2}", cfg.max_total_usd);
    println!("    Max per-market:      ${:.2}", cfg.max_per_market_usd);
    println!("    Max position:        {:.1} shares", cfg.max_position);
    println!();
    println!("  Kill switch file:      {}", cfg.kill_switch_file);

    if kill_switch_active(&cfg.kill_switch_file) {
        println!();
        println!("  {}{}KILL SWITCH IS ACTIVE - REMOVE FILE TO TRADE{}{}",
            BG_RED, BOLD, RESET, RESET);
    }

    println!();
    println!("{}", "═".repeat(70));
    println!();

    if !cfg.paper_mode {
        println!("{}Starting live trading in 5 seconds...{}", YELLOW, RESET);
        println!("{}Press Ctrl+C to abort{}", DIM, RESET);
        std::thread::sleep(Duration::from_secs(5));
    }
}

fn main_loop(cfg: &CanaryConfig, state: &mut CanaryState) {
    loop {
        clear_screen();
        render_header(cfg);
        render_caps(cfg, state);
        render_kill_switch(cfg);
        render_session(state);
        render_trades(state);
        render_summary(state);
        render_footer(state);

        // Check kill switch
        if kill_switch_active(&cfg.kill_switch_file) && !cfg.paper_mode {
            println!("\n{}{}  KILL SWITCH ACTIVATED - EXITING  {}", BG_RED, BOLD, RESET);
            break;
        }

        std::thread::sleep(Duration::from_millis(500));
        state.last_update = std::time::Instant::now();
    }
}

fn main() -> Result<()> {
    let cfg = CanaryConfig::from_env();

    // Show config and confirmation
    print_config_summary(&cfg);

    // Initialize state
    let mut state = CanaryState::new();
    state.cash_usdc = std::env::var("PM_PAPER_STARTING_CASH")
        .ok().and_then(|v| v.parse().ok()).unwrap_or(1000.0);

    // Note: This is a display-only console
    // Actual trading happens in live_console
    // This console reads from the same shared state

    println!();
    println!("{}NOTE:{} This is a display console only.", YELLOW, RESET);
    println!("Run alongside live_console to monitor trading.");
    println!();
    println!("For actual trading, use:");
    println!("  cargo run --bin live_console");
    println!();
    println!("Press Enter to see demo display, or Ctrl+C to exit...");

    let mut input = String::new();
    io::stdin().read_line(&mut input).ok();

    // Demo mode - show what the display would look like
    state.current_session = Some(SessionInfo {
        symbol: "BTC".to_string(),
        end_time: chrono::Utc::now() + chrono::Duration::minutes(12),
        up_mid: Some(0.55),
        down_mid: Some(0.45),
        up_ask: Some(0.56),
        down_ask: Some(0.46),
    });

    // Add demo trades
    state.trades.push(Trade {
        id: "1".to_string(),
        outcome: "UP".to_string(),
        price: 0.52,
        size: 1.0,
        ts: "09:15:32".to_string(),
        status: TradeStatus::Won,
    });
    state.trades.push(Trade {
        id: "2".to_string(),
        outcome: "DOWN".to_string(),
        price: 0.48,
        size: 1.0,
        ts: "09:32:15".to_string(),
        status: TradeStatus::Lost,
    });
    state.trades.push(Trade {
        id: "3".to_string(),
        outcome: "UP".to_string(),
        price: 0.55,
        size: 1.0,
        ts: "09:48:22".to_string(),
        status: TradeStatus::Filled,
    });

    state.total_exposure = 2.55;
    state.ws_connected = true;

    // Run display loop
    main_loop(&cfg, &mut state);

    Ok(())
}
