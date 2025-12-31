//! Backtester for Polymarket 15-minute binary options
//!
//! Reads tick data from markets_paper/ and simulates strategy execution.
//! Outputs JSON summary compatible with optimizer.rs

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::{
    collections::HashMap,
    fs::File,
    io::{BufRead, BufReader},
    path::PathBuf,
};

/// Tick data format from ticks.jsonl
#[derive(Debug, Deserialize)]
struct Tick {
    ts: String,
    t: i64,
    slug: String,
    symbol: String,
    variant: String,
    #[serde(rename = "startUnix")]
    start_unix: i64,
    #[serde(rename = "endUnix")]
    end_unix: i64,
    best: Option<BestQuotes>,
    price: Option<PriceQuotes>,
    #[serde(rename = "minutesLeft")]
    minutes_left: Option<f64>,
}

#[derive(Debug, Deserialize)]
struct BestQuotes {
    #[serde(rename = "Up")]
    up: Option<BidAsk>,
    #[serde(rename = "Down")]
    down: Option<BidAsk>,
}

#[derive(Debug, Deserialize)]
struct BidAsk {
    bid: Option<f64>,
    ask: Option<f64>,
}

#[derive(Debug, Deserialize)]
struct PriceQuotes {
    #[serde(rename = "Up")]
    up: Option<f64>,
    #[serde(rename = "Down")]
    down: Option<f64>,
}

/// Strategy configuration from environment
#[derive(Debug, Clone)]
struct StrategyConfig {
    tau0_seconds: f64,      // Entry window (first N seconds)
    stop_new_seconds: f64,  // Exit window (last N seconds)
    spread_threshold: f64,  // Max combined ask price
    max_one_sided: f64,     // Max shares on one side without balance
    base_qty: f64,          // Base quantity per side
    min_price: f64,         // Min price we'll bid
    max_price: f64,         // Max price we'll bid
    price_buffer: f64,      // Price buffer below mid
}

impl StrategyConfig {
    fn from_env() -> Self {
        let get_f = |key: &str, default: f64| -> f64 {
            std::env::var(key)
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(default)
        };

        Self {
            tau0_seconds: get_f("PM_TAU0_SECONDS", 120.0),
            stop_new_seconds: get_f("PM_STOP_NEW_SECONDS", 30.0),
            spread_threshold: get_f("PM_SPREAD_THRESHOLD", 0.98),
            max_one_sided: get_f("PM_MAX_ONE_SIDED", 6.0),
            base_qty: get_f("PM_BASE_QTY", 5.0),
            min_price: get_f("PM_MIN_PRICE", 0.40),
            max_price: get_f("PM_MAX_PRICE", 0.55),
            price_buffer: get_f("PM_PRICE_BUFFER", 0.02),
        }
    }
}

/// Position tracker for a single market
#[derive(Debug, Default)]
struct Position {
    up_shares: f64,
    up_cost: f64,
    down_shares: f64,
    down_cost: f64,
}

impl Position {
    fn total_shares(&self) -> f64 {
        self.up_shares + self.down_shares
    }

    fn is_balanced(&self) -> bool {
        (self.up_shares - self.down_shares).abs() < 0.5
    }
}

/// Trade record
#[derive(Debug, Clone)]
struct Trade {
    side: String, // "UP" or "DOWN"
    qty: f64,
    price: f64,
}

/// Result of simulating one market
#[derive(Debug)]
struct MarketResult {
    slug: String,
    trades: Vec<Trade>,
    pnl: f64,
    up_won: bool,
    position: Position,
}

/// Output format for optimizer
#[derive(Debug, Serialize)]
struct BacktestSummary {
    markets: i64,
    pnl_total: f64,
    win_rate: f64,
    trades_total: i64,
}

fn main() -> Result<()> {
    let _ = dotenvy::dotenv();

    // Parse args
    let args: Vec<String> = std::env::args().collect();
    let mut summary_json = false;
    let mut no_plots = false;
    let mut limit_markets: Option<usize> = None;
    let mut verbose = false;

    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--summary-json" => summary_json = true,
            "--no-plots" => no_plots = true,
            "--verbose" | "-v" => verbose = true,
            "--limit-markets" => {
                i += 1;
                limit_markets = Some(args[i].parse().context("--limit-markets value")?);
            }
            "--help" | "-h" => {
                eprintln!("Usage: backtest [--summary-json] [--no-plots] [--limit-markets N] [--verbose]");
                eprintln!("\nReads tick data from PK8GA_MARKETS_DIR (default: ../markets_paper)");
                eprintln!("Strategy config via PM_* environment variables");
                return Ok(());
            }
            _ => {}
        }
        i += 1;
    }

    let _ = no_plots; // Not implemented yet

    // Load config
    let cfg = StrategyConfig::from_env();

    // Find markets directory
    let markets_dir = std::env::var("PK8GA_MARKETS_DIR")
        .unwrap_or_else(|_| "../markets_paper".to_string());
    let markets_path = PathBuf::from(&markets_dir);

    if !markets_path.exists() {
        anyhow::bail!("Markets directory not found: {}", markets_path.display());
    }

    // Collect market directories
    let mut market_dirs: Vec<PathBuf> = std::fs::read_dir(&markets_path)?
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| p.is_dir() && p.join("ticks.jsonl").exists())
        .collect();

    market_dirs.sort();

    if let Some(limit) = limit_markets {
        market_dirs.truncate(limit);
    }

    if verbose {
        eprintln!("Config: {:?}", cfg);
        eprintln!("Markets to process: {}", market_dirs.len());
    }

    // Process each market
    let mut results: Vec<MarketResult> = Vec::new();

    for market_dir in &market_dirs {
        match simulate_market(market_dir, &cfg, verbose) {
            Ok(result) => results.push(result),
            Err(e) => {
                if verbose {
                    eprintln!("Error processing {}: {}", market_dir.display(), e);
                }
            }
        }
    }

    // Calculate summary stats
    let markets_processed = results.len() as i64;
    let trades_total: i64 = results.iter().map(|r| r.trades.len() as i64).sum();
    let pnl_total: f64 = results.iter().map(|r| r.pnl).sum();

    // Win rate: markets where we had trades and made money
    let markets_with_trades: Vec<&MarketResult> = results.iter()
        .filter(|r| !r.trades.is_empty())
        .collect();
    let wins = markets_with_trades.iter().filter(|r| r.pnl > 0.0).count();
    let win_rate = if markets_with_trades.is_empty() {
        0.0
    } else {
        (wins as f64 / markets_with_trades.len() as f64) * 100.0
    };

    if summary_json {
        let summary = BacktestSummary {
            markets: markets_processed,
            pnl_total,
            win_rate,
            trades_total,
        };
        println!("{}", serde_json::to_string(&summary)?);
    } else {
        // Detailed output
        println!("\n=== BACKTEST RESULTS ===");
        println!("Markets processed: {}", markets_processed);
        println!("Markets with trades: {}", markets_with_trades.len());
        println!("Total trades: {}", trades_total);
        println!("Win rate: {:.1}%", win_rate);
        println!("Total PnL: ${:.2}", pnl_total);
        println!("Avg PnL per market (traded): ${:.2}",
            if markets_with_trades.is_empty() { 0.0 }
            else { pnl_total / markets_with_trades.len() as f64 });

        // Show some individual results
        if verbose {
            println!("\n=== SAMPLE RESULTS ===");
            for r in results.iter().take(10) {
                if !r.trades.is_empty() {
                    println!("{}: {} trades, PnL=${:.2}, won={}",
                        r.slug, r.trades.len(), r.pnl, r.up_won);
                }
            }
        }
    }

    Ok(())
}

fn simulate_market(market_dir: &PathBuf, cfg: &StrategyConfig, verbose: bool) -> Result<MarketResult> {
    let ticks_path = market_dir.join("ticks.jsonl");
    let file = File::open(&ticks_path)?;
    let reader = BufReader::new(file);

    let mut position = Position::default();
    let mut trades: Vec<Trade> = Vec::new();
    let mut last_tick: Option<Tick> = None;
    let mut slug = String::new();

    // Track market timing
    let mut market_duration_seconds: f64 = 900.0; // Default 15 min

    // Realistic trading: limit trades and space them out
    let mut last_trade_ts: i64 = 0;
    let min_trade_interval_ms: i64 = 5000; // Min 5 seconds between trades
    let max_trades_per_side: f64 = 20.0;   // Max 20 trades per side

    for line in reader.lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }

        let tick: Tick = match serde_json::from_str(&line) {
            Ok(t) => t,
            Err(_) => continue,
        };

        if slug.is_empty() {
            slug = tick.slug.clone();
            market_duration_seconds = (tick.end_unix - tick.start_unix) as f64;
        }

        // Calculate tau (seconds remaining)
        let tau_seconds = tick.minutes_left.unwrap_or(0.0) * 60.0;

        // Check trade interval
        if tick.t - last_trade_ts < min_trade_interval_ms {
            last_tick = Some(tick);
            continue;
        }

        // Check max position
        if position.up_shares >= max_trades_per_side * cfg.base_qty
            && position.down_shares >= max_trades_per_side * cfg.base_qty {
            last_tick = Some(tick);
            continue;
        }

        // Try to trade
        if let Some(trade) = try_trade(&tick, tau_seconds, market_duration_seconds, &position, cfg) {
            // Update position
            match trade.side.as_str() {
                "UP" => {
                    position.up_shares += trade.qty;
                    position.up_cost += trade.qty * trade.price;
                }
                "DOWN" => {
                    position.down_shares += trade.qty;
                    position.down_cost += trade.qty * trade.price;
                }
                _ => {}
            }
            trades.push(trade);
            last_trade_ts = tick.t;
        }

        last_tick = Some(tick);
    }

    // Determine outcome from final tick
    let up_won = if let Some(ref tick) = last_tick {
        if let Some(ref price) = tick.price {
            price.up.unwrap_or(0.0) > 0.5
        } else if let Some(ref best) = tick.best {
            best.up.as_ref().and_then(|b| b.bid).unwrap_or(0.0) > 0.5
        } else {
            false
        }
    } else {
        false
    };

    // Calculate PnL
    // Settlement: winning side pays $1 per share, losing side pays $0
    let pnl = if up_won {
        // UP won: UP shares worth $1, DOWN shares worth $0
        (position.up_shares * 1.0 - position.up_cost) + (0.0 - position.down_cost)
    } else {
        // DOWN won: DOWN shares worth $1, UP shares worth $0
        (0.0 - position.up_cost) + (position.down_shares * 1.0 - position.down_cost)
    };

    if verbose && !trades.is_empty() {
        eprintln!(
            "{}: {} trades, up={:.0} down={:.0}, up_won={}, pnl=${:.2}",
            slug, trades.len(), position.up_shares, position.down_shares, up_won, pnl
        );
    }

    Ok(MarketResult {
        slug,
        trades,
        pnl,
        up_won,
        position,
    })
}

fn try_trade(
    tick: &Tick,
    tau_seconds: f64,
    market_duration: f64,
    position: &Position,
    cfg: &StrategyConfig,
) -> Option<Trade> {
    // 1. EXIT WINDOW CHECK - last N seconds, no new trades
    if tau_seconds < cfg.stop_new_seconds {
        return None;
    }

    // 2. ENTRY WINDOW CHECK - only first N seconds
    let entry_cutoff = market_duration - cfg.tau0_seconds;
    if tau_seconds < entry_cutoff {
        return None;
    }

    // 3. QUOTE AVAILABILITY
    let best = tick.best.as_ref()?;
    let up_bid = best.up.as_ref()?.bid?;
    let up_ask = best.up.as_ref()?.ask?;
    let down_bid = best.down.as_ref()?.bid?;
    let down_ask = best.down.as_ref()?.ask?;

    // Need valid quotes
    if up_ask <= 0.0 || down_ask <= 0.0 {
        return None;
    }

    // 4. SPREAD CHECK
    let spread = up_ask + down_ask;
    if spread >= cfg.spread_threshold {
        return None;
    }

    // Calculate mid prices
    let up_mid = (up_bid + up_ask) / 2.0;
    let down_mid = (down_bid + down_ask) / 2.0;

    // 5. POSITION BALANCE CHECK
    let imbalance = (position.up_shares - position.down_shares).abs();

    // Determine which side to buy
    let (side, qty, price) = if imbalance >= cfg.max_one_sided {
        // Too imbalanced - only buy lagging side
        if position.up_shares > position.down_shares {
            // Buy DOWN to catch up
            let price = (down_mid - cfg.price_buffer).max(cfg.min_price).min(cfg.max_price);
            if price < cfg.min_price || price > cfg.max_price {
                return None;
            }
            ("DOWN", cfg.base_qty, price)
        } else {
            // Buy UP to catch up
            let price = (up_mid - cfg.price_buffer).max(cfg.min_price).min(cfg.max_price);
            if price < cfg.min_price || price > cfg.max_price {
                return None;
            }
            ("UP", cfg.base_qty, price)
        }
    } else {
        // Buy both sides, prioritizing lagging side
        // For simplicity in backtest, alternate or buy the one with better price
        let up_price = (up_mid - cfg.price_buffer).max(cfg.min_price).min(cfg.max_price);
        let down_price = (down_mid - cfg.price_buffer).max(cfg.min_price).min(cfg.max_price);

        // Buy the cheaper one that's within our price range
        if up_price >= cfg.min_price && up_price <= cfg.max_price
            && (position.up_shares <= position.down_shares || down_price < cfg.min_price || down_price > cfg.max_price)
        {
            ("UP", cfg.base_qty, up_price)
        } else if down_price >= cfg.min_price && down_price <= cfg.max_price {
            ("DOWN", cfg.base_qty, down_price)
        } else {
            return None;
        }
    };

    Some(Trade {
        side: side.to_string(),
        qty,
        price,
    })
}
