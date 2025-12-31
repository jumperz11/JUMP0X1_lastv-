//! Replay Realism CLI
//!
//! Measures whether verified edge survives realistic execution:
//! - Latency delay (250ms, 500ms, 1000ms)
//! - Slippage (+1, +2, +3 ticks from best ask)
//!
//! This is offline replay only. No live trading changes.
//!
//! Usage:
//!   cargo run --bin replay_realism -- --inspect --ticks <orders.jsonl>
//!   cargo run --bin replay_realism -- \
//!       --verified logs/runs/XXX/verified_trades.jsonl \
//!       --ticks logs/runs/XXX/orders_rule_v1.jsonl \
//!       --out logs/runs/XXX/realism_replay.csv

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs::File;
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::path::PathBuf;

// ============================================================================
// Configuration
// ============================================================================

/// Latency scenarios to test (milliseconds)
const LATENCY_SCENARIOS: [i64; 3] = [250, 500, 1000];

/// Slippage scenarios to test (number of ticks worse than best ask)
const SLIPPAGE_SCENARIOS: [i32; 3] = [1, 2, 3];

/// Tick size (price increment)
const TICK_SIZE: f64 = 0.01;

/// Maximum time window to search for a tick after latency delay (ms)
const MAX_TICK_SEARCH_WINDOW_MS: i64 = 5000;

// ============================================================================
// CLI Arguments
// ============================================================================

#[derive(Debug)]
struct Args {
    verified: Option<PathBuf>,
    ticks: PathBuf,
    output: Option<PathBuf>,
    inspect: bool,
}

fn parse_args() -> Result<Args> {
    let args: Vec<String> = std::env::args().collect();

    let mut verified: Option<PathBuf> = None;
    let mut ticks: Option<PathBuf> = None;
    let mut output: Option<PathBuf> = None;
    let mut inspect = false;

    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--verified" | "-v" => {
                i += 1;
                verified = Some(PathBuf::from(&args[i]));
            }
            "--ticks" | "-t" => {
                i += 1;
                ticks = Some(PathBuf::from(&args[i]));
            }
            "--out" | "-o" => {
                i += 1;
                output = Some(PathBuf::from(&args[i]));
            }
            "--inspect" => {
                inspect = true;
            }
            "--help" | "-h" => {
                print_help();
                std::process::exit(0);
            }
            arg if !arg.starts_with('-') && ticks.is_none() => {
                ticks = Some(PathBuf::from(arg));
            }
            _ => {
                eprintln!("Unknown argument: {}", args[i]);
                print_help();
                std::process::exit(1);
            }
        }
        i += 1;
    }

    let ticks = ticks.context("--ticks <file> is required")?;

    Ok(Args { verified, ticks, output, inspect })
}

fn print_help() {
    println!(r#"
Replay Realism - Measure edge survival under latency and slippage

USAGE:
    replay_realism --verified <verified.jsonl> --ticks <orders.jsonl> --out <replay.csv>
    replay_realism --inspect --ticks <orders.jsonl>

OPTIONS:
    -v, --verified <FILE>   Verified trades file (from verify_settlements)
    -t, --ticks <FILE>      Tick stream (orders_*.jsonl with timestamps + prices)
    -o, --out <FILE>        Output CSV file for scenario grid
    --inspect               Print tick file schema and sample rows
    -h, --help              Print help

SCENARIOS:
    Latency:  250ms, 500ms, 1000ms delay from original submit
    Slippage: +1, +2, +3 ticks worse than best ask

EXAMPLES:
    # Inspect tick data format
    cargo run --bin replay_realism -- --inspect --ticks logs/runs/XXX/orders_rule_v1.jsonl

    # Run replay analysis
    cargo run --bin replay_realism -- \
        --verified logs/runs/XXX/verified_trades.jsonl \
        --ticks logs/runs/XXX/orders_rule_v1.jsonl \
        --out logs/runs/XXX/realism_replay.csv
"#);
}

// ============================================================================
// Data Structures
// ============================================================================

/// Verified trade from verify_settlements output
#[derive(Debug, Deserialize)]
struct VerifiedTrade {
    order_id: String,
    outcome: String,              // "UP" or "DOWN"
    #[allow(dead_code)]
    side: String,
    entry_price: f64,
    fill_ts_ms: i64,
    status: String,               // "VERIFIED", "PENDING", etc.
    winner: Option<String>,       // Ground truth
    win_bool: Option<bool>,
    #[allow(dead_code)]
    pnl: Option<f64>,
}

/// Tick record from orders log
#[derive(Debug, Deserialize)]
struct TickRecord {
    submit_ts_ms: Option<i64>,
    fill_ts_ms: Option<i64>,
    outcome: Option<String>,
    best_ask_q_at_submit: Option<f64>,
    #[allow(dead_code)]
    action: Option<String>,
}

/// Processed tick for lookup
#[derive(Debug, Clone)]
struct Tick {
    ts_ms: i64,
    outcome: String,  // "UP" or "DOWN"
    best_ask: f64,
}

/// Result for a single scenario
#[derive(Debug, Clone, Default)]
struct ScenarioResult {
    latency_ms: i64,
    slippage_ticks: i32,
    n_total: usize,
    n_used: usize,
    n_missed: usize,
    wins: usize,
    losses: usize,
    total_pnl: f64,
}

impl ScenarioResult {
    fn win_rate(&self) -> f64 {
        if self.n_used == 0 { 0.0 } else { (self.wins as f64 / self.n_used as f64) * 100.0 }
    }

    fn ev_per_trade(&self) -> f64 {
        if self.n_used == 0 { 0.0 } else { self.total_pnl / self.n_used as f64 }
    }
}

/// Full replay results
#[derive(Debug, Serialize)]
struct ReplayOutput {
    summary: ReplaySummary,
    scenarios: Vec<ScenarioRow>,
}

#[derive(Debug, Serialize)]
struct ReplaySummary {
    verified_trades: usize,
    tick_count: usize,
    latency_scenarios: Vec<i64>,
    slippage_scenarios: Vec<i32>,
}

#[derive(Debug, Serialize)]
struct ScenarioRow {
    latency_ms: i64,
    slippage_ticks: i32,
    n_used: usize,
    n_missed: usize,
    win_rate: f64,
    ev_per_trade: f64,
    total_pnl: f64,
}

// ============================================================================
// Readers
// ============================================================================

fn read_verified_trades(path: &PathBuf) -> Result<Vec<VerifiedTrade>> {
    let file = File::open(path).with_context(|| format!("Cannot open {}", path.display()))?;
    let reader = BufReader::new(file);

    let mut trades = Vec::new();
    for (line_num, line) in reader.lines().enumerate() {
        let line = line.with_context(|| format!("Read error at line {}", line_num + 1))?;
        if line.trim().is_empty() {
            continue;
        }
        match serde_json::from_str::<VerifiedTrade>(&line) {
            Ok(trade) => {
                // Only use VERIFIED trades with ground truth
                if trade.status == "VERIFIED" && trade.winner.is_some() {
                    trades.push(trade);
                }
            }
            Err(e) => {
                eprintln!("WARN: Line {} parse error: {}", line_num + 1, e);
            }
        }
    }

    Ok(trades)
}

fn read_ticks(path: &PathBuf) -> Result<Vec<Tick>> {
    let file = File::open(path).with_context(|| format!("Cannot open {}", path.display()))?;
    let reader = BufReader::new(file);

    let mut ticks = Vec::new();
    for line in reader.lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }

        if let Ok(rec) = serde_json::from_str::<TickRecord>(&line) {
            // Get timestamp (prefer submit_ts_ms, fallback to fill_ts_ms)
            let ts_ms = rec.submit_ts_ms.or(rec.fill_ts_ms);

            // Only include records with valid timestamp, outcome, and price
            if let (Some(ts), Some(outcome), Some(ask)) = (ts_ms, &rec.outcome, rec.best_ask_q_at_submit) {
                if ask > 0.0 && ask < 1.0 {
                    ticks.push(Tick {
                        ts_ms: ts,
                        outcome: outcome.to_uppercase(),
                        best_ask: ask,
                    });
                }
            }
        }
    }

    // Sort by timestamp for binary search
    ticks.sort_by_key(|t| t.ts_ms);

    Ok(ticks)
}

// ============================================================================
// Tick Lookup
// ============================================================================

/// Find the first tick at or after target_ts for the given outcome
fn find_tick_after<'a>(ticks: &'a [Tick], target_ts: i64, outcome: &str, max_window_ms: i64) -> Option<&'a Tick> {
    // Binary search to find starting position
    let start_idx = ticks.partition_point(|t| t.ts_ms < target_ts);

    let deadline = target_ts + max_window_ms;

    // Scan forward to find matching outcome within window
    for tick in ticks[start_idx..].iter() {
        if tick.ts_ms > deadline {
            return None;  // Past the search window
        }
        if tick.outcome == outcome {
            return Some(tick);
        }
    }

    None
}

// ============================================================================
// Replay Logic
// ============================================================================

fn compute_realistic_pnl(entry_price: f64, win: bool) -> f64 {
    if win {
        1.0 - entry_price  // Win: payout $1, profit is $1 - entry
    } else {
        -entry_price       // Loss: lose entry price
    }
}

fn run_replay(trades: &[VerifiedTrade], ticks: &[Tick]) -> BTreeMap<(i64, i32), ScenarioResult> {
    let mut results: BTreeMap<(i64, i32), ScenarioResult> = BTreeMap::new();

    // Initialize all scenarios
    for &latency in &LATENCY_SCENARIOS {
        for &slippage in &SLIPPAGE_SCENARIOS {
            results.insert((latency, slippage), ScenarioResult {
                latency_ms: latency,
                slippage_ticks: slippage,
                n_total: trades.len(),
                ..Default::default()
            });
        }
    }

    // Process each trade
    for trade in trades {
        let submit_ts = trade.fill_ts_ms;  // Use fill_ts as submit time
        let outcome = &trade.outcome;
        let winner = trade.winner.as_ref().unwrap();
        let is_win = trade.win_bool.unwrap_or(false);

        for &latency in &LATENCY_SCENARIOS {
            // Find tick at (submit_ts + latency)
            let target_ts = submit_ts + latency;

            match find_tick_after(ticks, target_ts, outcome, MAX_TICK_SEARCH_WINDOW_MS) {
                Some(tick) => {
                    // Found a tick - apply slippage scenarios
                    for &slippage in &SLIPPAGE_SCENARIOS {
                        let realistic_price = (tick.best_ask + (slippage as f64 * TICK_SIZE))
                            .min(0.99);  // Cap at 0.99

                        // Determine win/loss based on ORIGINAL winner (ground truth doesn't change)
                        let realistic_win = outcome == winner;
                        let pnl = compute_realistic_pnl(realistic_price, realistic_win);

                        let result = results.get_mut(&(latency, slippage)).unwrap();
                        result.n_used += 1;
                        if realistic_win {
                            result.wins += 1;
                        } else {
                            result.losses += 1;
                        }
                        result.total_pnl += pnl;
                    }
                }
                None => {
                    // No tick found - mark all slippage scenarios as missed
                    for &slippage in &SLIPPAGE_SCENARIOS {
                        let result = results.get_mut(&(latency, slippage)).unwrap();
                        result.n_missed += 1;
                    }
                }
            }
        }
    }

    results
}

// ============================================================================
// Output
// ============================================================================

fn print_scenario_grid(results: &BTreeMap<(i64, i32), ScenarioResult>) {
    println!("\n╔════════════════════════════════════════════════════════════════════════════════════╗");
    println!("║                           REALISM REPLAY RESULTS                                   ║");
    println!("╠════════════════════════════════════════════════════════════════════════════════════╣");
    println!("║  Latency │ Slippage │  Used │ Missed │ Win Rate │  EV/Trade  │  Total PnL         ║");
    println!("╠════════════════════════════════════════════════════════════════════════════════════╣");

    for (&(latency, slippage), result) in results.iter() {
        let ev_sign = if result.ev_per_trade() >= 0.0 { "+" } else { "" };
        let pnl_sign = if result.total_pnl >= 0.0 { "+" } else { "" };

        println!(
            "║  {:>5}ms │  +{} tick │   {:>3} │    {:>3} │   {:>5.1}% │  {}${:<7.4} │  {}${:<7.2}         ║",
            latency,
            slippage,
            result.n_used,
            result.n_missed,
            result.win_rate(),
            ev_sign,
            result.ev_per_trade().abs(),
            pnl_sign,
            result.total_pnl.abs(),
        );
    }

    println!("╚════════════════════════════════════════════════════════════════════════════════════╝\n");
}

fn write_csv(results: &BTreeMap<(i64, i32), ScenarioResult>, path: &PathBuf) -> Result<()> {
    let file = File::create(path).with_context(|| format!("Cannot create {}", path.display()))?;
    let mut writer = BufWriter::new(file);

    // Header
    writeln!(writer, "latency_ms,slippage_ticks,n_used,n_missed,win_rate,ev_per_trade,total_pnl")?;

    // Data rows
    for (&(latency, slippage), result) in results.iter() {
        writeln!(
            writer,
            "{},{},{},{},{:.4},{:.6},{:.4}",
            latency,
            slippage,
            result.n_used,
            result.n_missed,
            result.win_rate(),
            result.ev_per_trade(),
            result.total_pnl,
        )?;
    }

    writer.flush()?;
    Ok(())
}

fn print_json_summary(trades: &[VerifiedTrade], ticks: &[Tick], results: &BTreeMap<(i64, i32), ScenarioResult>) {
    let scenarios: Vec<ScenarioRow> = results.iter().map(|(&(latency, slippage), r)| {
        ScenarioRow {
            latency_ms: latency,
            slippage_ticks: slippage,
            n_used: r.n_used,
            n_missed: r.n_missed,
            win_rate: r.win_rate(),
            ev_per_trade: r.ev_per_trade(),
            total_pnl: r.total_pnl,
        }
    }).collect();

    let output = ReplayOutput {
        summary: ReplaySummary {
            verified_trades: trades.len(),
            tick_count: ticks.len(),
            latency_scenarios: LATENCY_SCENARIOS.to_vec(),
            slippage_scenarios: SLIPPAGE_SCENARIOS.to_vec(),
        },
        scenarios,
    };

    println!("{}", serde_json::to_string_pretty(&output).unwrap());
}

// ============================================================================
// Inspect Mode
// ============================================================================

fn inspect_file(path: &PathBuf) -> Result<()> {
    println!("\n=== INSPECT: {} ===\n", path.display());

    let file = File::open(path).with_context(|| format!("Cannot open {}", path.display()))?;
    let reader = BufReader::new(file);

    let mut line_count = 0;
    let mut sample_lines = Vec::new();
    let mut all_keys: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut has_best_ask = 0;
    let mut has_submit_ts = 0;
    let mut has_outcome = 0;

    for line in reader.lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        line_count += 1;

        if sample_lines.len() < 3 {
            sample_lines.push(line.clone());
        }

        // Extract keys and count fields
        if let Ok(obj) = serde_json::from_str::<serde_json::Value>(&line) {
            if let Some(map) = obj.as_object() {
                for key in map.keys() {
                    all_keys.insert(key.clone());
                }
                if map.contains_key("best_ask_q_at_submit") { has_best_ask += 1; }
                if map.contains_key("submit_ts_ms") { has_submit_ts += 1; }
                if map.contains_key("outcome") { has_outcome += 1; }
            }
        }

        if line_count > 1000 {
            break;
        }
    }

    println!("Lines scanned: {} (capped at 1000)", line_count);
    println!("\n--- Detected Keys ---");
    let mut keys: Vec<_> = all_keys.into_iter().collect();
    keys.sort();
    for key in &keys {
        println!("  {}", key);
    }

    println!("\n--- Field Coverage (in first 1000 lines) ---");
    println!("  submit_ts_ms:         {}", has_submit_ts);
    println!("  outcome:              {}", has_outcome);
    println!("  best_ask_q_at_submit: {}", has_best_ask);

    let usable = has_submit_ts.min(has_outcome).min(has_best_ask);
    println!("\n  → Usable tick records: ~{}", usable);

    println!("\n--- Sample Rows ---");
    for (i, line) in sample_lines.iter().enumerate() {
        println!("\n[{}]:", i + 1);
        if let Ok(obj) = serde_json::from_str::<serde_json::Value>(line) {
            println!("{}", serde_json::to_string_pretty(&obj)?);
        } else {
            println!("{}", line);
        }
    }

    Ok(())
}

// ============================================================================
// Main
// ============================================================================

fn main() -> Result<()> {
    let args = parse_args()?;

    // Inspect mode
    if args.inspect {
        return inspect_file(&args.ticks);
    }

    // Replay mode requires verified file
    let verified_path = args.verified.context(
        "--verified <file> is required for replay. Use --inspect to examine tick file."
    )?;

    // Read data
    println!("Reading verified trades from: {}", verified_path.display());
    let trades = read_verified_trades(&verified_path)?;
    println!("  VERIFIED trades loaded: {}", trades.len());

    if trades.is_empty() {
        println!("ERROR: No verified trades found. Cannot run replay.");
        std::process::exit(1);
    }

    println!("Reading tick stream from: {}", args.ticks.display());
    let ticks = read_ticks(&args.ticks)?;
    println!("  Ticks loaded: {}", ticks.len());

    if ticks.is_empty() {
        println!("ERROR: No ticks found. Cannot run replay.");
        std::process::exit(1);
    }

    // Run replay
    println!("\nRunning replay with {} latency × {} slippage scenarios...",
        LATENCY_SCENARIOS.len(), SLIPPAGE_SCENARIOS.len());
    let results = run_replay(&trades, &ticks);

    // Output
    print_scenario_grid(&results);

    if let Some(output_path) = &args.output {
        println!("Writing CSV to: {}", output_path.display());
        write_csv(&results, output_path)?;
    }

    // JSON summary to stdout
    println!("\n--- JSON Summary ---");
    print_json_summary(&trades, &ticks, &results);

    Ok(())
}
