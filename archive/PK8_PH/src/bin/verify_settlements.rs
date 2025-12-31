//! Settlement Verification CLI
//!
//! Converts paper fill assumptions into verified wins/losses using actual settlement data.
//! This is post-hoc audit infrastructure - it does NOT modify strategy or execution logic.
//!
//! Usage:
//!   cargo run --bin verify_settlements -- --input logs/runs/XXX/orders_rule_v1.jsonl \
//!       --sessions logs/runs/XXX/multi_signal_sessions_btc.jsonl \
//!       --output logs/runs/XXX/verified_trades.jsonl
//!
//!   cargo run --bin verify_settlements -- --inspect logs/runs/XXX/orders_rule_v1.jsonl

use anyhow::{Context, Result};
use chrono::{DateTime, TimeZone, Utc};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs::File;
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::path::PathBuf;

// ============================================================================
// CLI Arguments
// ============================================================================

#[derive(Debug)]
struct Args {
    input: PathBuf,
    sessions: Option<PathBuf>,
    output: Option<PathBuf>,
    inspect: bool,
    cache: Option<PathBuf>,
}

fn parse_args() -> Result<Args> {
    let args: Vec<String> = std::env::args().collect();

    let mut input: Option<PathBuf> = None;
    let mut sessions: Option<PathBuf> = None;
    let mut output: Option<PathBuf> = None;
    let mut inspect = false;
    let mut cache: Option<PathBuf> = None;

    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--input" | "-i" => {
                i += 1;
                input = Some(PathBuf::from(&args[i]));
            }
            "--sessions" | "-s" => {
                i += 1;
                sessions = Some(PathBuf::from(&args[i]));
            }
            "--output" | "-o" => {
                i += 1;
                output = Some(PathBuf::from(&args[i]));
            }
            "--inspect" => {
                inspect = true;
            }
            "--cache" | "-c" => {
                i += 1;
                cache = Some(PathBuf::from(&args[i]));
            }
            "--help" | "-h" => {
                print_help();
                std::process::exit(0);
            }
            arg if !arg.starts_with('-') && input.is_none() => {
                input = Some(PathBuf::from(arg));
            }
            _ => {
                eprintln!("Unknown argument: {}", args[i]);
                print_help();
                std::process::exit(1);
            }
        }
        i += 1;
    }

    let input = input.context("--input <file> is required")?;

    Ok(Args { input, sessions, output, inspect, cache })
}

fn print_help() {
    println!(r#"
Settlement Verifier - Convert paper fills into verified wins/losses

USAGE:
    verify_settlements --input <fills.jsonl> --sessions <sessions.jsonl> --output <verified.jsonl>
    verify_settlements --inspect <fills.jsonl>

OPTIONS:
    -i, --input <FILE>      Input fill log (JSONL from paper broker)
    -s, --sessions <FILE>   Session data with settlement (multi_signal_sessions_*.jsonl)
    -o, --output <FILE>     Output verified trades (JSONL)
    --inspect               Print schema and sample rows, then exit
    -c, --cache <FILE>      Cache file for settlement data (optional)
    -h, --help              Print help

EXAMPLES:
    # Inspect fill log schema
    cargo run --bin verify_settlements -- --inspect logs/runs/XXX/orders_rule_v1.jsonl

    # Verify settlements
    cargo run --bin verify_settlements -- \
        --input logs/runs/XXX/orders_rule_v1.jsonl \
        --sessions logs/runs/XXX/multi_signal_sessions_btc.jsonl \
        --output logs/runs/XXX/verified_trades.jsonl
"#);
}

// ============================================================================
// Data Structures
// ============================================================================

/// Raw fill record from orders_*.jsonl
#[derive(Debug, Deserialize)]
struct FillRecord {
    run_id: Option<String>,
    session_id: Option<String>,          // condition_id (hex) or token_id (decimal)
    strategy_id: Option<String>,
    order_id: Option<String>,
    outcome: Option<String>,              // "up" or "down"
    side: Option<String>,                 // "BUY" or "SELL"
    action: Option<String>,               // "FILL", "PARTIAL_FILL", etc.
    submit_ts_ms: Option<i64>,
    fill_ts_ms: Option<i64>,
    fill_pct: Option<f64>,
    avg_fill_q: Option<f64>,              // Entry price (0-1)
    best_ask_q_at_submit: Option<f64>,
    #[serde(default)]
    reason_codes: Vec<String>,
}

/// Session record from multi_signal_sessions_*.jsonl
#[derive(Debug, Deserialize)]
struct SessionRecord {
    session_id: Option<i64>,              // Epoch seconds (session start)
    symbol: Option<String>,
    winner: Option<String>,               // "UP" or "DOWN" (ground truth)
    #[serde(default)]
    snapshots: Vec<serde_json::Value>,    // Not needed for verification
}

/// Verified trade output
#[derive(Debug, Serialize)]
struct VerifiedTrade {
    // Original fill data
    run_id: String,
    order_id: String,
    strategy_id: String,
    outcome: String,                      // What we bet on: "UP" or "DOWN"
    side: String,
    entry_price: f64,
    fill_ts_ms: i64,

    // Session matching
    session_id: i64,                      // Matched session epoch
    session_start: String,                // ISO8601

    // Verification result
    status: VerifyStatus,
    winner: Option<String>,               // Actual winner: "UP" or "DOWN"
    win_bool: Option<bool>,
    pnl: Option<f64>,                     // +payout or -entry_price
    verified_at: String,
}

#[derive(Debug, Clone, Copy, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
enum VerifyStatus {
    Verified,
    Pending,
    Unverifiable,
}

/// Summary statistics
#[derive(Debug, Default)]
struct VerifySummary {
    total_fills: usize,
    verified: usize,
    pending: usize,
    unverifiable: usize,
    wins: usize,
    losses: usize,
    total_pnl: f64,
    sum_win_pnl: f64,
    sum_loss_pnl: f64,
}

// ============================================================================
// Reader: Fill Logs
// ============================================================================

fn read_fills(path: &PathBuf) -> Result<Vec<FillRecord>> {
    let file = File::open(path).with_context(|| format!("Cannot open {}", path.display()))?;
    let reader = BufReader::new(file);

    let mut fills = Vec::new();
    for (line_num, line) in reader.lines().enumerate() {
        let line = line.with_context(|| format!("Read error at line {}", line_num + 1))?;
        if line.trim().is_empty() {
            continue;
        }
        match serde_json::from_str::<FillRecord>(&line) {
            Ok(rec) => fills.push(rec),
            Err(e) => {
                // Skip malformed lines but warn
                eprintln!("WARN: Line {} parse error: {}", line_num + 1, e);
            }
        }
    }

    Ok(fills)
}

/// Filter to only FILL events (completed trades)
fn filter_completed_fills(fills: Vec<FillRecord>) -> Vec<FillRecord> {
    fills.into_iter()
        .filter(|f| {
            f.action.as_deref() == Some("FILL") &&
            f.fill_pct.map(|p| p >= 99.0).unwrap_or(false) &&  // 100% filled
            f.avg_fill_q.is_some() &&
            f.fill_ts_ms.is_some()
        })
        .collect()
}

// ============================================================================
// Reader: Session Settlements
// ============================================================================

/// Map of session_id (epoch seconds) -> winner ("UP" or "DOWN")
fn read_sessions(path: &PathBuf) -> Result<HashMap<i64, String>> {
    let file = File::open(path).with_context(|| format!("Cannot open {}", path.display()))?;
    let reader = BufReader::new(file);

    let mut sessions = HashMap::new();
    for (line_num, line) in reader.lines().enumerate() {
        let line = line.with_context(|| format!("Read error at line {}", line_num + 1))?;
        if line.trim().is_empty() {
            continue;
        }

        // Skip RUN_START events
        if line.contains("\"event\":") {
            continue;
        }

        match serde_json::from_str::<SessionRecord>(&line) {
            Ok(rec) => {
                if let (Some(sid), Some(winner)) = (rec.session_id, rec.winner) {
                    sessions.insert(sid, winner.to_uppercase());
                }
            }
            Err(e) => {
                eprintln!("WARN: Session line {} parse error: {}", line_num + 1, e);
            }
        }
    }

    Ok(sessions)
}

// ============================================================================
// Resolver: Match Fills to Sessions
// ============================================================================

const SESSION_DURATION_SECS: i64 = 899;  // 14m59s

/// Find which session a fill belongs to based on fill timestamp
fn find_session_for_fill(fill_ts_ms: i64, sessions: &HashMap<i64, String>) -> Option<(i64, String)> {
    let fill_ts_secs = fill_ts_ms / 1000;

    // Sessions are keyed by start time. A fill belongs to session S if:
    // S <= fill_ts < S + SESSION_DURATION_SECS
    for (&session_start, winner) in sessions.iter() {
        let session_end = session_start + SESSION_DURATION_SECS;
        if fill_ts_secs >= session_start && fill_ts_secs < session_end {
            return Some((session_start, winner.clone()));
        }
    }

    None
}

// ============================================================================
// Verifier Core
// ============================================================================

fn verify_fills(
    fills: Vec<FillRecord>,
    sessions: &HashMap<i64, String>,
) -> (Vec<VerifiedTrade>, VerifySummary) {
    let mut verified_trades = Vec::new();
    let mut summary = VerifySummary::default();
    let now = Utc::now().to_rfc3339();

    for fill in fills {
        summary.total_fills += 1;

        // Extract required fields
        let run_id = fill.run_id.unwrap_or_default();
        let order_id = fill.order_id.unwrap_or_default();
        let strategy_id = fill.strategy_id.unwrap_or_default();
        let outcome = fill.outcome.clone().unwrap_or_default().to_uppercase();
        let side = fill.side.unwrap_or_default();
        let entry_price = fill.avg_fill_q.unwrap_or(0.0);
        let fill_ts_ms = fill.fill_ts_ms.unwrap_or(0);

        // Validate required fields
        if outcome.is_empty() || fill_ts_ms == 0 || entry_price <= 0.0 {
            summary.unverifiable += 1;
            verified_trades.push(VerifiedTrade {
                run_id,
                order_id,
                strategy_id,
                outcome,
                side,
                entry_price,
                fill_ts_ms,
                session_id: 0,
                session_start: String::new(),
                status: VerifyStatus::Unverifiable,
                winner: None,
                win_bool: None,
                pnl: None,
                verified_at: now.clone(),
            });
            continue;
        }

        // Try to match to a session
        match find_session_for_fill(fill_ts_ms, sessions) {
            Some((session_id, winner)) => {
                let win_bool = outcome == winner;
                let pnl = if win_bool {
                    1.0 - entry_price  // Win: payout is $1, profit is $1 - entry
                } else {
                    -entry_price       // Loss: lose entry price
                };

                if win_bool {
                    summary.wins += 1;
                    summary.sum_win_pnl += pnl;
                } else {
                    summary.losses += 1;
                    summary.sum_loss_pnl += pnl;
                }
                summary.total_pnl += pnl;
                summary.verified += 1;

                let session_start = Utc.timestamp_opt(session_id, 0)
                    .single()
                    .map(|dt| dt.to_rfc3339())
                    .unwrap_or_default();

                verified_trades.push(VerifiedTrade {
                    run_id,
                    order_id,
                    strategy_id,
                    outcome,
                    side,
                    entry_price,
                    fill_ts_ms,
                    session_id,
                    session_start,
                    status: VerifyStatus::Verified,
                    winner: Some(winner),
                    win_bool: Some(win_bool),
                    pnl: Some(pnl),
                    verified_at: now.clone(),
                });
            }
            None => {
                // No matching session found - mark as pending
                summary.pending += 1;
                verified_trades.push(VerifiedTrade {
                    run_id,
                    order_id,
                    strategy_id,
                    outcome,
                    side,
                    entry_price,
                    fill_ts_ms,
                    session_id: 0,
                    session_start: String::new(),
                    status: VerifyStatus::Pending,
                    winner: None,
                    win_bool: None,
                    pnl: None,
                    verified_at: now.clone(),
                });
            }
        }
    }

    (verified_trades, summary)
}

// ============================================================================
// Writer
// ============================================================================

fn write_verified(trades: &[VerifiedTrade], path: &PathBuf) -> Result<()> {
    let file = File::create(path).with_context(|| format!("Cannot create {}", path.display()))?;
    let mut writer = BufWriter::new(file);

    for trade in trades {
        let line = serde_json::to_string(trade)?;
        writeln!(writer, "{}", line)?;
    }

    writer.flush()?;
    Ok(())
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

    for line in reader.lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        line_count += 1;

        if sample_lines.len() < 3 {
            sample_lines.push(line.clone());
        }

        // Extract keys from JSON
        if let Ok(obj) = serde_json::from_str::<serde_json::Value>(&line) {
            if let Some(map) = obj.as_object() {
                for key in map.keys() {
                    all_keys.insert(key.clone());
                }
            }
        }

        // Stop after scanning enough for schema
        if line_count > 1000 {
            break;
        }
    }

    println!("Total lines scanned: {} (capped at 1000)", line_count);
    println!("\n--- Detected Keys ---");
    let mut keys: Vec<_> = all_keys.into_iter().collect();
    keys.sort();
    for key in &keys {
        println!("  {}", key);
    }

    println!("\n--- Sample Rows (first 3) ---");
    for (i, line) in sample_lines.iter().enumerate() {
        println!("\n[{}]:", i + 1);
        if let Ok(obj) = serde_json::from_str::<serde_json::Value>(line) {
            println!("{}", serde_json::to_string_pretty(&obj)?);
        } else {
            println!("{}", line);
        }
    }

    // Count action types if this looks like a fill log
    println!("\n--- Action Breakdown ---");
    let file = File::open(path)?;
    let reader = BufReader::new(file);
    let mut action_counts: HashMap<String, usize> = HashMap::new();

    for line in reader.lines().take(10000) {
        if let Ok(line) = line {
            if let Ok(obj) = serde_json::from_str::<serde_json::Value>(&line) {
                if let Some(action) = obj.get("action").and_then(|v| v.as_str()) {
                    *action_counts.entry(action.to_string()).or_insert(0) += 1;
                }
            }
        }
    }

    let mut actions: Vec<_> = action_counts.into_iter().collect();
    actions.sort_by(|a, b| b.1.cmp(&a.1));
    for (action, count) in actions {
        println!("  {}: {}", action, count);
    }

    Ok(())
}

// ============================================================================
// Summary Report
// ============================================================================

fn print_summary(summary: &VerifySummary) {
    println!("\n╔══════════════════════════════════════════════════════════════╗");
    println!("║               SETTLEMENT VERIFICATION REPORT                 ║");
    println!("╠══════════════════════════════════════════════════════════════╣");
    println!("║  Total Fills Processed:     {:>6}                           ║", summary.total_fills);
    println!("╠══════════════════════════════════════════════════════════════╣");
    println!("║  VERIFIED:                  {:>6}                           ║", summary.verified);
    println!("║  PENDING (no session):      {:>6}                           ║", summary.pending);
    println!("║  UNVERIFIABLE (bad data):   {:>6}                           ║", summary.unverifiable);
    println!("╠══════════════════════════════════════════════════════════════╣");

    if summary.verified > 0 {
        let win_rate = (summary.wins as f64 / summary.verified as f64) * 100.0;
        let avg_win = if summary.wins > 0 { summary.sum_win_pnl / summary.wins as f64 } else { 0.0 };
        let avg_loss = if summary.losses > 0 { summary.sum_loss_pnl / summary.losses as f64 } else { 0.0 };
        let expectancy = summary.total_pnl / summary.verified as f64;

        println!("║  VERIFIED METRICS (ground truth):                            ║");
        println!("║    Wins:                  {:>6}                             ║", summary.wins);
        println!("║    Losses:                {:>6}                             ║", summary.losses);
        println!("║    Win Rate:              {:>6.1}%                            ║", win_rate);
        println!("║    Avg Win:              ${:>6.3}                            ║", avg_win);
        println!("║    Avg Loss:             ${:>6.3}                            ║", avg_loss);
        println!("║    Expectancy/Trade:     ${:>6.3}                            ║", expectancy);
        println!("║    Total P&L:            ${:>6.2}                            ║", summary.total_pnl);
    } else {
        println!("║  No verified trades to compute metrics.                      ║");
    }

    println!("╚══════════════════════════════════════════════════════════════╝\n");
}

// ============================================================================
// Main
// ============================================================================

fn main() -> Result<()> {
    let args = parse_args()?;

    // Inspect mode
    if args.inspect {
        return inspect_file(&args.input);
    }

    // Verify mode requires sessions file
    let sessions_path = args.sessions.context(
        "--sessions <file> is required for verification. Use --inspect to examine file schema."
    )?;

    // Read fills
    println!("Reading fills from: {}", args.input.display());
    let all_fills = read_fills(&args.input)?;
    println!("  Total records: {}", all_fills.len());

    let fills = filter_completed_fills(all_fills);
    println!("  Completed fills (FILL, 100%): {}", fills.len());

    // Read sessions
    println!("Reading sessions from: {}", sessions_path.display());
    let sessions = read_sessions(&sessions_path)?;
    println!("  Sessions with winners: {}", sessions.len());

    // Verify
    println!("\nVerifying settlements...");
    let (verified_trades, summary) = verify_fills(fills, &sessions);

    // Write output
    if let Some(output_path) = &args.output {
        println!("Writing verified trades to: {}", output_path.display());
        write_verified(&verified_trades, output_path)?;
    }

    // Print summary
    print_summary(&summary);

    Ok(())
}
