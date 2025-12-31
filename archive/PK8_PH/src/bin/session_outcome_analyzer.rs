//! Session Outcome Analyzer v1.0
//!
//! CLI tool to analyze session outcomes from multi_signal_recorder data.
//! Computes signal accuracy, EV, and identifies tradeable edges.
//!
//! Usage:
//!   session_outcome_analyzer <RUN_DIR>
//!   session_outcome_analyzer ./logs/runs/20251221_0400_V1
//!

use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs::File;
use std::io::{BufRead, BufReader};

// =============================================================================
// ANSI COLORS
// =============================================================================

const GREEN: &str = "\x1b[32m";
const RED: &str = "\x1b[31m";
const YELLOW: &str = "\x1b[33m";
const CYAN: &str = "\x1b[36m";
const BOLD: &str = "\x1b[1m";
const DIM: &str = "\x1b[2m";
const RESET: &str = "\x1b[0m";

// =============================================================================
// DATA STRUCTURES (must match multi_signal_recorder output)
// =============================================================================

#[derive(Debug, Clone, Serialize, Deserialize)]
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
    // RTDS fields (v2)
    #[serde(default)]
    rtds_ts_ms: Option<u128>,
    #[serde(default)]
    rtds_age_ms: Option<u128>,
    #[serde(default)]
    rtds_frozen_ms: Option<u128>,
    #[serde(default)]
    disloc_vs_rtds: Option<f64>,
    #[serde(default)]
    disloc_vs_rtds_bps: Option<f64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct Session {
    session_id: i64,
    start_time: String,
    symbol: String,
    snapshots: Vec<SignalSnapshot>,
    winner: Option<String>,
    // Versioning fields
    #[serde(default)]
    run_id: Option<String>,
    #[serde(default)]
    schema_version: Option<u32>,
    // EV fields
    #[serde(default)]
    assumed_q: Option<f64>,
    #[serde(default)]
    q_entry: Option<f64>,
    #[serde(default)]
    ev_if_correct: Option<f64>,
    #[serde(default)]
    ev_if_wrong: Option<f64>,
    #[serde(default)]
    realized_return_pct: Option<f64>,
    #[serde(default)]
    realized_pnl_usd: Option<f64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct RunEvent {
    event: Option<String>,
    run_id: Option<String>,
    start_time: Option<String>,
    end_time: Option<String>,
    reason: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct RunConfig {
    version: String,
    run_id: Option<String>,
    start_time: String,
    symbol: String,
    move_threshold_pct: f64,
    move_window_ms: u128,
    cooldown_ms: u128,
    dislocation_threshold_usd: f64,
    dislocation_threshold_pct: f64,
    dislocation_persist_secs: i64,
    epsilon_flat_pct: f64,
    staleness_ms: u128,
    outlier_pct: f64,
    assumed_q: f64,
    checkpoints: Vec<String>,
}

// =============================================================================
// ANALYSIS STRUCTURES
// =============================================================================

#[derive(Debug, Default)]
struct SignalStats {
    correct: u32,
    total: u32,
}

impl SignalStats {
    fn accuracy(&self) -> f64 {
        if self.total == 0 {
            0.0
        } else {
            100.0 * self.correct as f64 / self.total as f64
        }
    }

    fn ev_at_q(&self, q: f64) -> f64 {
        // Binary option EV:
        // win_rate * R_win + lose_rate * R_loss
        // R_win = (1-q)/q, R_loss = -1
        if self.total == 0 {
            return 0.0;
        }
        let win_rate = self.correct as f64 / self.total as f64;
        let lose_rate = 1.0 - win_rate;
        let r_win = (1.0 - q) / q;
        let r_loss = -1.0;
        (win_rate * r_win + lose_rate * r_loss) * 100.0 // percentage
    }
}

const CHECKPOINTS: &[&str] = &[
    "T+0", "T+15s", "T+30s", "T+45s", "T+60s", "T+90s", "T+2m", "T+3m",
    "T+5m", "T+7m", "T+10m", "T+12m", "T+13m", "T+14m", "T+14m30s", "T+14m45s", "T+14m59s",
];

// =============================================================================
// MAIN ANALYSIS
// =============================================================================

fn analyze_run(run_dir: &str) -> Result<()> {
    // Find sessions file
    let sessions_path = format!("{}/multi_signal_sessions_btc.jsonl", run_dir);
    let config_path = format!("{}/config.json", run_dir);

    // Try to load config
    let config: Option<RunConfig> = File::open(&config_path)
        .ok()
        .and_then(|f| serde_json::from_reader(f).ok());

    // Load sessions
    let file = File::open(&sessions_path)?;
    let reader = BufReader::new(file);

    let mut sessions: Vec<Session> = Vec::new();
    let mut run_start: Option<RunEvent> = None;
    let mut run_end: Option<RunEvent> = None;

    for line in reader.lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }

        // Try to parse as event first
        if let Ok(event) = serde_json::from_str::<RunEvent>(&line) {
            if event.event.as_deref() == Some("RUN_START") {
                run_start = Some(event);
                continue;
            }
            if event.event.as_deref() == Some("RUN_END") {
                run_end = Some(event);
                continue;
            }
        }

        // Parse as session
        if let Ok(session) = serde_json::from_str::<Session>(&line) {
            if session.winner.is_some() {
                sessions.push(session);
            }
        }
    }

    // Header
    println!();
    println!("{}{}========================================================================{}", CYAN, BOLD, RESET);
    println!("{}{}  SESSION OUTCOME ANALYZER v1.0{}", CYAN, BOLD, RESET);
    println!("{}{}========================================================================{}", CYAN, BOLD, RESET);
    println!();

    // Run info
    let run_id = config.as_ref().and_then(|c| c.run_id.clone())
        .or_else(|| run_start.as_ref().and_then(|e| e.run_id.clone()))
        .unwrap_or_else(|| "(unknown)".to_string());

    println!("{}RUN:{} {}", BOLD, RESET, run_id);
    println!("{}DIR:{} {}", BOLD, RESET, run_dir);

    if let Some(ref cfg) = config {
        println!("{}SYMBOL:{} {}", BOLD, RESET, cfg.symbol);
        println!("{}START:{} {}", BOLD, RESET, &cfg.start_time[..19]);
    }

    if let Some(ref end) = run_end {
        if let Some(ref end_time) = end.end_time {
            println!("{}END:{} {} ({})", BOLD, RESET, &end_time[..19], end.reason.as_deref().unwrap_or("unknown"));
        }
    }

    println!("{}SESSIONS:{} {} with winners", BOLD, RESET, sessions.len());
    println!();

    if sessions.is_empty() {
        println!("{}No complete sessions found. Run longer to collect data.{}", YELLOW, RESET);
        return Ok(());
    }

    // Winner distribution
    let up_wins = sessions.iter().filter(|s| s.winner.as_deref() == Some("UP")).count();
    let down_wins = sessions.len() - up_wins;
    let up_pct = 100.0 * up_wins as f64 / sessions.len() as f64;

    println!("{}------------------------------------------------------------------------{}", DIM, RESET);
    println!("{}OUTCOME DISTRIBUTION{}", BOLD, RESET);
    println!("{}------------------------------------------------------------------------{}", DIM, RESET);
    println!("  UP:   {:>3} ({:>5.1}%)", up_wins, up_pct);
    println!("  DOWN: {:>3} ({:>5.1}%)", down_wins, 100.0 - up_pct);
    println!();

    // Signal accuracy by checkpoint
    println!("{}------------------------------------------------------------------------{}", DIM, RESET);
    println!("{}SIGNAL ACCURACY BY CHECKPOINT{}", BOLD, RESET);
    println!("{}------------------------------------------------------------------------{}", DIM, RESET);
    println!();

    // Build accuracy table
    let assumed_q = config.as_ref().map(|c| c.assumed_q).unwrap_or(0.55);
    println!("{}Assumed entry q = {:.2} (R_win = {:+.1}%, R_loss = -100%){}", DIM, assumed_q, (1.0 - assumed_q) / assumed_q * 100.0, RESET);
    println!();

    println!("{:>11} │ {:>7} {:>7} {:>7} {:>7} │ {:>7} {:>7} │ {:>7}",
        "CHECKPOINT", "BIN_SP", "CB", "BYBIT", "CL_RTDS", "CVD", "OB", "EV@q");
    println!("────────────┼─────────────────────────────────┼─────────────────┼────────");

    // Track best signals
    let mut best_signal = ("", "", 0.0f64);

    for checkpoint in CHECKPOINTS.iter().skip(1) {  // Skip T+0
        let mut bin_stats = SignalStats::default();
        let mut cb_stats = SignalStats::default();
        let mut bybit_stats = SignalStats::default();
        let mut cl_stats = SignalStats::default();
        let mut cvd_stats = SignalStats::default();
        let mut ob_stats = SignalStats::default();

        for session in &sessions {
            let snapshots: HashMap<_, _> = session.snapshots.iter()
                .map(|s| (s.checkpoint.as_str(), s))
                .collect();

            let t0 = snapshots.get("T+0");
            let cp = snapshots.get(*checkpoint);
            let winner = session.winner.as_deref().unwrap_or("");

            if let (Some(start), Some(current)) = (t0, cp) {
                // Binance spot direction
                if let (Some(p0), Some(p1)) = (start.binance_spot, current.binance_spot) {
                    let predicted = if p1 > p0 { "UP" } else { "DOWN" };
                    bin_stats.total += 1;
                    if predicted == winner { bin_stats.correct += 1; }
                }

                // Coinbase direction
                if let (Some(p0), Some(p1)) = (start.coinbase, current.coinbase) {
                    let predicted = if p1 > p0 { "UP" } else { "DOWN" };
                    cb_stats.total += 1;
                    if predicted == winner { cb_stats.correct += 1; }
                }

                // Bybit direction
                if let (Some(p0), Some(p1)) = (start.bybit, current.bybit) {
                    let predicted = if p1 > p0 { "UP" } else { "DOWN" };
                    bybit_stats.total += 1;
                    if predicted == winner { bybit_stats.correct += 1; }
                }

                // Chainlink RTDS direction
                if let (Some(p0), Some(p1)) = (start.chainlink_rtds, current.chainlink_rtds) {
                    let predicted = if p1 > p0 { "UP" } else { "DOWN" };
                    cl_stats.total += 1;
                    if predicted == winner { cl_stats.correct += 1; }
                }

                // CVD direction
                if let Some(cvd) = current.cvd {
                    let predicted = if cvd > 0.0 { "UP" } else { "DOWN" };
                    cvd_stats.total += 1;
                    if predicted == winner { cvd_stats.correct += 1; }
                }

                // OB imbalance direction
                if let Some(ob) = current.orderbook_imbalance {
                    let predicted = if ob > 0.0 { "UP" } else { "DOWN" };
                    ob_stats.total += 1;
                    if predicted == winner { ob_stats.correct += 1; }
                }
            }
        }

        // Format accuracy with color
        let fmt_acc = |stats: &SignalStats| -> String {
            if stats.total == 0 {
                format!("{}  N/A  {}", DIM, RESET)
            } else {
                let acc = stats.accuracy();
                if acc >= 55.0 {
                    format!("{}{:>5.1}%*{}", GREEN, acc, RESET)
                } else if acc >= 50.0 {
                    format!("{:>6.1}%", acc)
                } else {
                    format!("{}{:>6.1}%{}", RED, acc, RESET)
                }
            }
        };

        // Calculate combined EV (using binance as primary signal for now)
        let ev = bin_stats.ev_at_q(assumed_q);
        let ev_str = if bin_stats.total == 0 {
            format!("{}  N/A  {}", DIM, RESET)
        } else if ev > 0.0 {
            format!("{}{:>+5.1}%{}", GREEN, ev, RESET)
        } else {
            format!("{}{:>+5.1}%{}", RED, ev, RESET)
        };

        // Track best signal
        for (name, stats) in [("BIN", &bin_stats), ("CVD", &cvd_stats), ("OB", &ob_stats)] {
            let ev = stats.ev_at_q(assumed_q);
            if ev > best_signal.2 && stats.total >= 5 {
                best_signal = (checkpoint, name, ev);
            }
        }

        println!("{:>11} │ {} {} {} {} │ {} {} │ {}",
            checkpoint,
            fmt_acc(&bin_stats),
            fmt_acc(&cb_stats),
            fmt_acc(&bybit_stats),
            fmt_acc(&cl_stats),
            fmt_acc(&cvd_stats),
            fmt_acc(&ob_stats),
            ev_str);
    }

    println!();
    println!("{}* = >55% accuracy (potential edge){}", GREEN, RESET);

    // Best signal summary
    if best_signal.2 > 0.0 {
        println!();
        println!("{}------------------------------------------------------------------------{}", DIM, RESET);
        println!("{}BEST SIGNAL{}", BOLD, RESET);
        println!("{}------------------------------------------------------------------------{}", DIM, RESET);
        println!("  {} @ {} with EV = {:+.1}% per trade at q={:.2}",
            best_signal.1, best_signal.0, best_signal.2, assumed_q);
    }

    // Dislocation analysis (if available)
    let sessions_with_disloc: Vec<_> = sessions.iter()
        .filter(|s| s.snapshots.iter().any(|snap| snap.disloc_vs_rtds.is_some()))
        .collect();

    if !sessions_with_disloc.is_empty() {
        println!();
        println!("{}------------------------------------------------------------------------{}", DIM, RESET);
        println!("{}DISLOCATION ANALYSIS{}", BOLD, RESET);
        println!("{}------------------------------------------------------------------------{}", DIM, RESET);

        let mut high_disloc_sessions = 0;
        let mut high_disloc_correct = 0;

        for session in &sessions_with_disloc {
            // Check T+60s for high dislocation
            if let Some(snap) = session.snapshots.iter().find(|s| s.checkpoint == "T+60s") {
                if let Some(disloc) = snap.disloc_vs_rtds {
                    if disloc.abs() >= 25.0 {
                        high_disloc_sessions += 1;
                        // Did price continue in dislocation direction?
                        let disloc_dir = if disloc > 0.0 { "UP" } else { "DOWN" };
                        if session.winner.as_deref() == Some(disloc_dir) {
                            high_disloc_correct += 1;
                        }
                    }
                }
            }
        }

        if high_disloc_sessions > 0 {
            let disloc_acc = 100.0 * high_disloc_correct as f64 / high_disloc_sessions as f64;
            println!("  Sessions with disloc >= $25 at T+60s: {}", high_disloc_sessions);
            println!("  Dislocation direction predicted winner: {:.1}%", disloc_acc);
        } else {
            println!("  {}No high-dislocation sessions found{}", DIM, RESET);
        }
    }

    // EV summary
    println!();
    println!("{}------------------------------------------------------------------------{}", DIM, RESET);
    println!("{}EV SUMMARY (at q = {:.2}){}", BOLD, assumed_q, RESET);
    println!("{}------------------------------------------------------------------------{}", DIM, RESET);
    println!("  R_win  = (1 - q) / q = {:+.1}%", (1.0 - assumed_q) / assumed_q * 100.0);
    println!("  R_loss = -100%");
    println!("  Break-even accuracy = {:.1}%", assumed_q * 100.0);
    println!();
    println!("  To be profitable at q={:.2}, signal accuracy must exceed {:.1}%", assumed_q, assumed_q * 100.0);

    println!();
    println!("{}========================================================================{}", CYAN, RESET);

    Ok(())
}

fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().collect();

    if args.len() < 2 {
        println!("{}SESSION OUTCOME ANALYZER v1.0{}", BOLD, RESET);
        println!();
        println!("Analyze session outcomes from multi_signal_recorder data.");
        println!();
        println!("Usage:");
        println!("  {} <RUN_DIR>", args[0]);
        println!();
        println!("Example:");
        println!("  {} ./logs/runs/20251221_0400_V1", args[0]);
        return Ok(());
    }

    let run_dir = &args[1];
    analyze_run(run_dir)
}
