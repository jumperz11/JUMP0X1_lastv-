//! Event Ordering Analyzer (formerly Lead/Lag)
//!
//! Analyzes price tick data to find which source tends to move first.
//! HONEST VERSION: Reports resolution-aware metrics, not fake precision.
//!
//! Key insight: With ~130ms avg tick spacing, we CANNOT claim sub-100ms leads.
//! We CAN claim: "Source X moves first in Y% of events within resolution window"
//!
//! Usage: cargo run --release --bin lead_lag_analyzer -- ./logs/price_ticks_*.jsonl [threshold]

use anyhow::Result;
use serde::Deserialize;
use std::collections::HashMap;
use std::fs::File;
use std::io::{BufRead, BufReader};

#[derive(Debug, Deserialize)]
struct PriceTick {
    ts_us: i64,
    source: String,
    #[allow(dead_code)]
    symbol: String,
    price: f64,
    #[allow(dead_code)]
    volume: Option<f64>,
}

#[derive(Debug, Default)]
struct SourceStats {
    tick_count: usize,
    move_count: usize,
    first_mover_count: usize,
    tick_intervals: Vec<i64>,  // For resolution calculation
}

fn median(vals: &mut Vec<i64>) -> i64 {
    if vals.is_empty() { return 0; }
    vals.sort();
    vals[vals.len() / 2]
}

fn percentile(vals: &mut Vec<i64>, p: f64) -> i64 {
    if vals.is_empty() { return 0; }
    vals.sort();
    let idx = ((vals.len() as f64) * p) as usize;
    vals[idx.min(vals.len() - 1)]
}

fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 2 {
        eprintln!("Usage: {} <price_ticks.jsonl> [threshold_usd]", args[0]);
        eprintln!("  threshold_usd: minimum price move (default: 17.6 = 0.02% of $88k)");
        return Ok(());
    }

    let filename = &args[1];
    let threshold = args.get(2)
        .and_then(|s| s.parse::<f64>().ok())
        .unwrap_or(17.6);  // Default to 0.02% of ~$88k

    println!("╔═══════════════════════════════════════════════════════════════╗");
    println!("║          EVENT ORDERING ANALYZER v2.0 (Honest)                ║");
    println!("║                                                               ║");
    println!("║  Reports: WHO moves first, not fake ms precision              ║");
    println!("║  Resolution-aware: won't claim what data can't support        ║");
    println!("╚═══════════════════════════════════════════════════════════════╝");
    println!();
    println!("File: {}", filename);
    println!("Move threshold: ${:.2} (~0.02% at $88k)", threshold);
    println!();

    // Load all ticks
    let file = File::open(filename)?;
    let reader = BufReader::new(file);

    let mut ticks: Vec<PriceTick> = Vec::new();
    for line in reader.lines() {
        let line = line?;
        if let Ok(tick) = serde_json::from_str::<PriceTick>(&line) {
            ticks.push(tick);
        }
    }

    println!("Loaded {} ticks", ticks.len());

    // Sort by timestamp
    ticks.sort_by_key(|t| t.ts_us);

    // Calculate duration
    let duration_mins = if ticks.len() >= 2 {
        (ticks.last().unwrap().ts_us - ticks.first().unwrap().ts_us) as f64 / 60_000_000.0
    } else { 0.0 };
    println!("Duration: {:.1} minutes", duration_mins);

    // Group by source and calculate tick intervals
    let mut stats: HashMap<String, SourceStats> = HashMap::new();
    let mut last_ts: HashMap<String, i64> = HashMap::new();
    let mut last_price: HashMap<String, f64> = HashMap::new();

    for tick in &ticks {
        let stat = stats.entry(tick.source.clone()).or_default();
        stat.tick_count += 1;

        if let Some(prev_ts) = last_ts.get(&tick.source) {
            let interval = tick.ts_us - prev_ts;
            if interval > 0 && interval < 10_000_000 {  // < 10s, ignore gaps
                stat.tick_intervals.push(interval);
            }
        }
        last_ts.insert(tick.source.clone(), tick.ts_us);
    }

    // Calculate and display tick resolution per source
    println!("\n=== TICK RESOLUTION (determines what we can claim) ===");
    println!("{:<18} {:>10} {:>12} {:>12} {:>12}", "SOURCE", "TICKS", "MEDIAN_MS", "P75_MS", "P95_MS");
    println!("{}", "-".repeat(70));

    let mut global_intervals: Vec<i64> = Vec::new();
    for (source, stat) in stats.iter_mut() {
        let median_ms = median(&mut stat.tick_intervals.clone()) / 1000;
        let p75_ms = percentile(&mut stat.tick_intervals.clone(), 0.75) / 1000;
        let p95_ms = percentile(&mut stat.tick_intervals.clone(), 0.95) / 1000;
        global_intervals.extend(stat.tick_intervals.iter());

        println!("{:<18} {:>10} {:>12} {:>12} {:>12}",
            source, stat.tick_count, median_ms, p75_ms, p95_ms);
    }

    let global_resolution_ms = median(&mut global_intervals.clone()) / 1000;
    let resolution_floor_ms = global_resolution_ms.max(50);  // At least 50ms floor
    println!();
    println!("Global median resolution: {} ms", global_resolution_ms);
    println!("Resolution floor for claims: {} ms", resolution_floor_ms);
    println!("→ We CANNOT reliably claim leads < {} ms", resolution_floor_ms);

    // Detect significant moves
    println!("\n=== EVENT DETECTION ===");

    #[derive(Debug)]
    struct MoveEvent {
        ts_us: i64,
        source: String,
        #[allow(dead_code)]
        from: f64,
        #[allow(dead_code)]
        to: f64,
        direction: i8,  // 1 = UP, -1 = DOWN
    }

    let mut moves: Vec<MoveEvent> = Vec::new();
    last_price.clear();

    for tick in &ticks {
        let prev = last_price.get(&tick.source).copied().unwrap_or(tick.price);
        let delta = tick.price - prev;

        if delta.abs() >= threshold {
            moves.push(MoveEvent {
                ts_us: tick.ts_us,
                source: tick.source.clone(),
                from: prev,
                to: tick.price,
                direction: if delta > 0.0 { 1 } else { -1 },
            });
            stats.get_mut(&tick.source).unwrap().move_count += 1;
        }
        last_price.insert(tick.source.clone(), tick.price);
    }

    println!("Significant moves detected: {}", moves.len());
    if duration_mins > 0.0 {
        println!("Events per hour: {:.1}", moves.len() as f64 * 60.0 / duration_mins);
    }

    // Cluster moves into events (within resolution window)
    // An "event" is a cluster of moves from different sources within the resolution window
    println!("\n=== EVENT CLUSTERING ===");

    let cluster_window_us = (resolution_floor_ms * 2 * 1000) as i64;  // 2x resolution
    let cooldown_us = 3_000_000;  // 3 second cooldown between events

    println!("Cluster window: {} ms (2x resolution)", resolution_floor_ms * 2);
    println!("Cooldown between events: {} ms", cooldown_us / 1000);

    let mut events: Vec<Vec<&MoveEvent>> = Vec::new();
    let mut last_event_ts: i64 = 0;

    for mv in &moves {
        // Skip if within cooldown of last event
        if mv.ts_us - last_event_ts < cooldown_us && !events.is_empty() {
            // Add to current event if same cluster window
            if let Some(current_event) = events.last_mut() {
                if let Some(first) = current_event.first() {
                    if mv.ts_us - first.ts_us <= cluster_window_us {
                        current_event.push(mv);
                        continue;
                    }
                }
            }
            continue;  // Skip - within cooldown but not in cluster
        }

        // Start new event
        events.push(vec![mv]);
        last_event_ts = mv.ts_us;
    }

    println!("Events after clustering: {}", events.len());

    // Count first movers per event
    for event in &events {
        if let Some(first_move) = event.first() {
            stats.get_mut(&first_move.source).unwrap().first_mover_count += 1;
        }
    }

    // Results
    println!("\n=== RESULTS (Honest) ===");
    println!();
    println!("{:<18} {:>8} {:>12} {:>15} {:>12}",
        "SOURCE", "MOVES", "FIRST_MOVER", "FIRST_MOVER_%", "CONFIDENCE");
    println!("{}", "-".repeat(75));

    let total_events = events.len();
    let mut sources: Vec<_> = stats.iter().collect();
    sources.sort_by(|a, b| b.1.first_mover_count.cmp(&a.1.first_mover_count));

    for (source, stat) in &sources {
        let pct = if total_events > 0 {
            100.0 * stat.first_mover_count as f64 / total_events as f64
        } else { 0.0 };

        // Confidence based on sample size
        let confidence = if stat.first_mover_count >= 20 {
            "HIGH"
        } else if stat.first_mover_count >= 5 {
            "MEDIUM"
        } else {
            "LOW"
        };

        println!("{:<18} {:>8} {:>12} {:>14.1}% {:>12}",
            source, stat.move_count, stat.first_mover_count, pct, confidence);
    }

    // Honest interpretation
    println!("\n=== HONEST INTERPRETATION ===");
    println!();

    if let Some((leader, stat)) = sources.first() {
        let pct = 100.0 * stat.first_mover_count as f64 / total_events.max(1) as f64;

        println!("LEADER: {} ({:.0}% of events)", leader, pct);
        println!();
        println!("What this means:");
        println!("  ✓ {} tends to reflect price moves FIRST", leader);
        println!("  ✓ Within a ~{}ms resolution window", resolution_floor_ms * 2);
        println!("  ✓ Based on {} clustered events", total_events);
        println!();
        println!("What this does NOT mean:");
        println!("  ✗ We cannot claim sub-{}ms precision", resolution_floor_ms);
        println!("  ✗ This is event ORDERING, not latency measurement");
        println!("  ✗ Your edge is STRUCTURAL, not speed-based");
    }

    println!();
    println!("For Polymarket:");
    println!("  → Watch the leader for direction signals");
    println!("  → Your real edge is oracle staleness (seconds), not tick latency (ms)");
    println!("  → Focus on dislocation PERSISTENCE, not sub-100ms races");

    Ok(())
}
