#!/usr/bin/env python3
"""
RULEV3.1 BRUTE-FORCE PARAMETER SWEEP
=====================================
Senior Quant Engineering Analysis

Exhaustive grid search over all numeric parameters.
No premature optimization. No bias toward safety.
Truth over comfort.

Author: Claude (Quant Engineer Mode)
Date: 2025-12-31
"""

import json
import csv
import time
import itertools
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Tuple, Any
from datetime import datetime
from collections import defaultdict
import statistics

# ============================================================
# PARAMETER GRID DEFINITION
# ============================================================

PARAM_GRID = {
    # Price caps
    "ask_cap": [0.68, 0.70, 0.72, 0.74, 0.76],

    # Spread filter
    "spread_cap": [0.010, 0.015, 0.020, 0.025, 0.030],

    # Dynamic edge bucket cutoffs
    "ask_cut1": [0.64, 0.65, 0.66, 0.67, 0.68],
    "ask_cut2": [0.67, 0.68, 0.69, 0.70, 0.71],

    # Edge thresholds per bucket
    "edge1": [0.58, 0.60, 0.62, 0.64, 0.66],
    "edge2": [0.61, 0.63, 0.65, 0.67, 0.69],
    "edge3": [0.64, 0.66, 0.68, 0.70, 0.72],

    # Session controls
    "max_trades_per_session": [1, 2, 3],

    # Kill switch (consecutive losses)
    "kill_switch_L": [2, 3, 4, 5, 999],  # 999 = disabled
}

# Baseline RULEV3.1 for comparison
BASELINE = {
    "ask_cap": 0.72,
    "spread_cap": 0.02,
    "ask_cut1": 0.66,
    "ask_cut2": 0.69,
    "edge1": 0.64,
    "edge2": 0.67,
    "edge3": 0.70,
    "max_trades_per_session": 1,
    "kill_switch_L": 3,
}

# Fixed parameters (not swept)
POSITION_SIZE = 5.0
CORE_START_SECS = 150  # 2:30
CORE_END_SECS = 225    # 3:45

# ============================================================
# DATA STRUCTURES
# ============================================================

@dataclass
class Trade:
    session_id: str
    direction: str
    edge: float
    ask: float
    bid: float
    spread: float
    elapsed_secs: float
    won: bool
    pnl: float
    day: str  # YYYY-MM-DD for day-level analysis


@dataclass
class BacktestResult:
    # Parameters
    ask_cap: float = 0.0
    spread_cap: float = 0.0
    ask_cut1: float = 0.0
    ask_cut2: float = 0.0
    edge1: float = 0.0
    edge2: float = 0.0
    edge3: float = 0.0
    max_trades_per_session: int = 1
    kill_switch_L: int = 3

    # Core metrics
    total_sessions: int = 0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    win_rate: float = 0.0
    avg_pnl_per_trade: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_trades: int = 0  # trades in worst drawdown

    # Behavioral metrics
    pass_rate: float = 0.0  # % of sessions that produced a trade
    avg_ask_paid: float = 0.0
    avg_spread: float = 0.0
    avg_edge_at_entry: float = 0.0
    pnl_per_100_trades: float = 0.0
    dd_per_100_trades: float = 0.0

    # Stability metrics
    num_days: int = 0
    pnl_stddev: float = 0.0  # daily PnL stddev
    worst_day_pnl: float = 0.0
    best_day_pnl: float = 0.0
    sharpe_like: float = 0.0  # avg daily PnL / stddev

    # Kill switch metrics
    kill_switch_activations: int = 0
    trades_after_kill: int = 0  # trades that would have happened after kill
    pnl_saved_by_kill: float = 0.0

    # Bucket breakdown
    bucket1_trades: int = 0
    bucket1_pnl: float = 0.0
    bucket2_trades: int = 0
    bucket2_pnl: float = 0.0
    bucket3_trades: int = 0
    bucket3_pnl: float = 0.0

    # Time sensitivity
    early_core_trades: int = 0  # 2:30-3:00
    early_core_pnl: float = 0.0
    late_core_trades: int = 0   # 3:00-3:45
    late_core_pnl: float = 0.0

    # Deltas vs baseline
    delta_pnl: float = 0.0
    delta_trades: int = 0
    delta_win_rate: float = 0.0
    delta_max_dd: float = 0.0

    # Efficiency scores
    pnl_efficiency: float = 0.0  # PnL per unit of drawdown
    trade_efficiency: float = 0.0  # PnL per trade
    risk_adjusted_return: float = 0.0


# ============================================================
# DATA LOADING
# ============================================================

def load_sessions(markets_dir: Path) -> List[Tuple[Path, str]]:
    """Load all session paths with their dates."""
    sessions = []
    for d in sorted(markets_dir.iterdir()):
        if d.is_dir() and d.name.startswith('btc-updown-15m-'):
            # Extract timestamp from name
            try:
                ts = int(d.name.split('-')[-1])
                dt = datetime.fromtimestamp(ts)
                day = dt.strftime('%Y-%m-%d')
            except:
                day = "unknown"
            sessions.append((d, day))
    return sessions


def load_ticks(session_path: Path) -> List[dict]:
    """Load ticks from session."""
    ticks_file = session_path / 'ticks.jsonl'
    if not ticks_file.exists():
        return []

    ticks = []
    with open(ticks_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    ticks.append(json.loads(line))
                except:
                    continue
    return ticks


def get_winner(ticks: List[dict]) -> Optional[str]:
    """Determine session winner."""
    if not ticks:
        return None
    final = ticks[-1]
    price = final.get('price')
    if not price:
        return None
    up_mid = price.get('Up', 0.5)
    down_mid = price.get('Down', 0.5)
    if up_mid >= 0.90:
        return 'Up'
    elif down_mid >= 0.90:
        return 'Down'
    return None


def get_elapsed_secs(tick: dict) -> float:
    """Convert minutesLeft to elapsed seconds."""
    mins_left = tick.get('minutesLeft', 15)
    return (15 - mins_left) * 60


# ============================================================
# BACKTEST ENGINE
# ============================================================

def run_single_backtest(
    sessions: List[Tuple[Path, str]],
    params: dict,
    preloaded_data: dict = None
) -> BacktestResult:
    """
    Run backtest with given parameters.

    Args:
        sessions: List of (session_path, day_str) tuples
        params: Parameter dictionary
        preloaded_data: Optional preloaded tick data for speed

    Returns:
        BacktestResult with all metrics
    """
    result = BacktestResult(**params)
    result.total_sessions = len(sessions)

    trades: List[Trade] = []
    daily_pnl: Dict[str, float] = defaultdict(float)

    # Kill switch state
    consecutive_losses = 0
    kill_active = False
    trades_after_kill_list = []

    # Track opportunities
    opportunities = 0

    for session_path, day in sessions:
        # Load ticks
        if preloaded_data and session_path.name in preloaded_data:
            ticks = preloaded_data[session_path.name]
        else:
            ticks = load_ticks(session_path)

        if not ticks:
            continue

        winner = get_winner(ticks)
        if not winner:
            continue

        opportunities += 1
        session_trades = 0

        for tick in ticks:
            elapsed_secs = get_elapsed_secs(tick)

            # GATE 1: ZONE
            if elapsed_secs < CORE_START_SECS or elapsed_secs > CORE_END_SECS:
                continue

            price = tick.get('price')
            best = tick.get('best')
            if not price or not best:
                continue

            up_mid = price.get('Up')
            down_mid = price.get('Down')
            if up_mid is None or down_mid is None:
                continue

            # Direction selection
            if up_mid >= down_mid:
                direction = 'Up'
                edge = up_mid
                side = best.get('Up', {})
            else:
                direction = 'Down'
                edge = down_mid
                side = best.get('Down', {})

            ask = side.get('ask')
            bid = side.get('bid')
            if ask is None or bid is None or ask <= 0:
                continue

            spread = ask - bid

            # GATE 2: BOOK
            if spread < 0 or bid > ask:
                continue

            # GATE 3: SESSION_CAP
            if session_trades >= params["max_trades_per_session"]:
                continue

            # GATE 4: DYNAMIC_EDGE
            if ask <= params["ask_cut1"]:
                required_edge = params["edge1"]
                bucket = 1
            elif ask <= params["ask_cut2"]:
                required_edge = params["edge2"]
                bucket = 2
            else:
                required_edge = params["edge3"]
                bucket = 3

            if edge < required_edge:
                continue

            # GATE 5: HARD_PRICE
            if ask > params["ask_cap"]:
                continue

            # GATE 6: SPREAD
            if spread > params["spread_cap"]:
                continue

            # ALL GATES PASSED - Execute trade
            won = (direction == winner)
            shares = POSITION_SIZE / ask
            pnl = (1.0 - ask) * shares if won else -POSITION_SIZE

            trade = Trade(
                session_id=session_path.name,
                direction=direction,
                edge=edge,
                ask=ask,
                bid=bid,
                spread=spread,
                elapsed_secs=elapsed_secs,
                won=won,
                pnl=pnl,
                day=day
            )

            # Kill switch logic
            if kill_active:
                trades_after_kill_list.append(trade)
            else:
                trades.append(trade)
                session_trades += 1
                daily_pnl[day] += pnl

                # Update kill switch state
                if won:
                    consecutive_losses = 0
                else:
                    consecutive_losses += 1
                    if consecutive_losses >= params["kill_switch_L"]:
                        kill_active = True
                        result.kill_switch_activations += 1

            # Only one trade per session (break after first valid trade)
            if session_trades >= params["max_trades_per_session"]:
                break

    # Compute metrics
    if not trades:
        return result

    result.total_trades = len(trades)
    result.wins = sum(1 for t in trades if t.won)
    result.losses = result.total_trades - result.wins
    result.total_pnl = sum(t.pnl for t in trades)
    result.win_rate = result.wins / result.total_trades * 100 if result.total_trades > 0 else 0
    result.avg_pnl_per_trade = result.total_pnl / result.total_trades if result.total_trades > 0 else 0

    # Pass rate
    result.pass_rate = result.total_trades / opportunities * 100 if opportunities > 0 else 0

    # Averages
    result.avg_ask_paid = sum(t.ask for t in trades) / len(trades)
    result.avg_spread = sum(t.spread for t in trades) / len(trades)
    result.avg_edge_at_entry = sum(t.edge for t in trades) / len(trades)

    # Per-100 metrics
    result.pnl_per_100_trades = result.total_pnl / result.total_trades * 100 if result.total_trades > 0 else 0

    # Max drawdown
    running_pnl = 0.0
    peak = 0.0
    max_dd = 0.0
    dd_start_idx = 0
    max_dd_trades = 0

    for i, t in enumerate(trades):
        running_pnl += t.pnl
        if running_pnl > peak:
            peak = running_pnl
            dd_start_idx = i
        dd = peak - running_pnl
        if dd > max_dd:
            max_dd = dd
            max_dd_trades = i - dd_start_idx + 1

    result.max_drawdown = max_dd
    result.max_drawdown_trades = max_dd_trades
    result.dd_per_100_trades = max_dd / result.total_trades * 100 if result.total_trades > 0 else 0

    # Daily metrics
    if daily_pnl:
        daily_values = list(daily_pnl.values())
        result.num_days = len(daily_values)
        result.worst_day_pnl = min(daily_values)
        result.best_day_pnl = max(daily_values)

        if len(daily_values) > 1:
            result.pnl_stddev = statistics.stdev(daily_values)
            avg_daily = statistics.mean(daily_values)
            if result.pnl_stddev > 0:
                result.sharpe_like = avg_daily / result.pnl_stddev

    # Kill switch metrics
    result.trades_after_kill = len(trades_after_kill_list)
    result.pnl_saved_by_kill = -sum(t.pnl for t in trades_after_kill_list)  # Negative if we avoided losses

    # Bucket breakdown
    for t in trades:
        if t.ask <= params["ask_cut1"]:
            result.bucket1_trades += 1
            result.bucket1_pnl += t.pnl
        elif t.ask <= params["ask_cut2"]:
            result.bucket2_trades += 1
            result.bucket2_pnl += t.pnl
        else:
            result.bucket3_trades += 1
            result.bucket3_pnl += t.pnl

    # Time sensitivity
    for t in trades:
        if t.elapsed_secs < 180:  # Before 3:00
            result.early_core_trades += 1
            result.early_core_pnl += t.pnl
        else:
            result.late_core_trades += 1
            result.late_core_pnl += t.pnl

    # Efficiency scores
    if result.max_drawdown > 0:
        result.pnl_efficiency = result.total_pnl / result.max_drawdown
    result.trade_efficiency = result.avg_pnl_per_trade
    if result.pnl_stddev > 0:
        result.risk_adjusted_return = result.total_pnl / result.pnl_stddev

    return result


def compute_deltas(result: BacktestResult, baseline: BacktestResult):
    """Compute deltas vs baseline."""
    result.delta_pnl = result.total_pnl - baseline.total_pnl
    result.delta_trades = result.total_trades - baseline.total_trades
    result.delta_win_rate = result.win_rate - baseline.win_rate
    result.delta_max_dd = result.max_drawdown - baseline.max_drawdown


# ============================================================
# GRID SEARCH
# ============================================================

def generate_param_combinations(grid: dict) -> List[dict]:
    """Generate all parameter combinations from grid."""
    keys = list(grid.keys())
    values = [grid[k] for k in keys]

    combinations = []
    for combo in itertools.product(*values):
        params = dict(zip(keys, combo))

        # Filter invalid combinations
        # ask_cut1 must be < ask_cut2
        if params["ask_cut1"] >= params["ask_cut2"]:
            continue
        # edge1 <= edge2 <= edge3 (monotonic)
        if not (params["edge1"] <= params["edge2"] <= params["edge3"]):
            continue

        combinations.append(params)

    return combinations


def run_grid_search(markets_dir: Path, output_dir: Path, max_combos: int = 0):
    """
    Run exhaustive grid search.

    Args:
        markets_dir: Path to markets_paper directory
        output_dir: Path to save results
        max_combos: Limit combinations for testing (0 = no limit)
    """
    print("=" * 80)
    print("  RULEV3.1 BRUTE-FORCE PARAMETER SWEEP")
    print("=" * 80)
    print()

    # Load sessions
    print("Loading sessions...")
    sessions = load_sessions(markets_dir)
    print(f"  Found {len(sessions)} sessions")

    # Preload all tick data for speed
    print("Preloading tick data...")
    preloaded = {}
    for i, (path, day) in enumerate(sessions):
        if (i + 1) % 500 == 0:
            print(f"    Loaded {i+1}/{len(sessions)}...")
        preloaded[path.name] = load_ticks(path)
    print(f"  Preloaded {len(preloaded)} sessions")

    # Generate combinations
    print("Generating parameter combinations...")
    combinations = generate_param_combinations(PARAM_GRID)
    print(f"  Generated {len(combinations)} valid combinations")

    if max_combos > 0:
        combinations = combinations[:max_combos]
        print(f"  Limited to {max_combos} for testing")

    # Run baseline first
    print()
    print("Running baseline (RULEV3.1)...")
    baseline = run_single_backtest(sessions, BASELINE, preloaded)
    print(f"  Baseline: {baseline.total_trades} trades, ${baseline.total_pnl:.2f} PnL, {baseline.win_rate:.1f}% WR")

    # Run all combinations
    print()
    print(f"Running {len(combinations)} backtests...")
    results: List[BacktestResult] = []
    start_time = time.time()

    for i, params in enumerate(combinations):
        if (i + 1) % 100 == 0:
            elapsed = time.time() - start_time
            rate = (i + 1) / elapsed
            eta = (len(combinations) - i - 1) / rate if rate > 0 else 0
            print(f"  {i+1}/{len(combinations)} ({rate:.1f}/sec, ETA {eta:.0f}s)")

        result = run_single_backtest(sessions, params, preloaded)
        compute_deltas(result, baseline)
        results.append(result)

    elapsed = time.time() - start_time
    print(f"  Completed in {elapsed:.1f}s ({len(combinations)/elapsed:.1f} combos/sec)")

    # Sort by total PnL descending
    results.sort(key=lambda r: r.total_pnl, reverse=True)

    # Save results
    print()
    print("Saving results...")
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    csv_file = output_dir / f'sweep_results_{timestamp}.csv'

    # Write CSV
    if results:
        fieldnames = list(asdict(results[0]).keys())
        with open(csv_file, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in results:
                writer.writerow(asdict(r))

    print(f"  Saved to {csv_file}")

    # Print summary
    print()
    print("=" * 80)
    print("  RESULTS SUMMARY")
    print("=" * 80)

    print()
    print("TOP 10 BY TOTAL PNL:")
    print("-" * 120)
    print(f"{'Rank':<5} {'PnL':>10} {'Trades':>8} {'WR%':>7} {'MaxDD':>8} {'PnL/Tr':>8} {'Sharpe':>8} | ask_cap spread edge1/2/3 cuts")
    print("-" * 120)

    for i, r in enumerate(results[:10]):
        print(f"{i+1:<5} ${r.total_pnl:>8.2f} {r.total_trades:>8} {r.win_rate:>6.1f}% ${r.max_drawdown:>6.2f} ${r.avg_pnl_per_trade:>6.4f} {r.sharpe_like:>7.3f} | "
              f"{r.ask_cap:.2f} {r.spread_cap:.3f} {r.edge1:.2f}/{r.edge2:.2f}/{r.edge3:.2f} {r.ask_cut1:.2f}/{r.ask_cut2:.2f}")

    print()
    print("TOP 10 BY SHARPE-LIKE RATIO (min 100 trades):")
    print("-" * 120)

    sharpe_sorted = sorted([r for r in results if r.total_trades >= 100], key=lambda r: r.sharpe_like, reverse=True)
    for i, r in enumerate(sharpe_sorted[:10]):
        print(f"{i+1:<5} ${r.total_pnl:>8.2f} {r.total_trades:>8} {r.win_rate:>6.1f}% ${r.max_drawdown:>6.2f} ${r.avg_pnl_per_trade:>6.4f} {r.sharpe_like:>7.3f} | "
              f"{r.ask_cap:.2f} {r.spread_cap:.3f} {r.edge1:.2f}/{r.edge2:.2f}/{r.edge3:.2f} {r.ask_cut1:.2f}/{r.ask_cut2:.2f}")

    print()
    print("TOP 10 BY PNL EFFICIENCY (PnL/MaxDD, min 100 trades):")
    print("-" * 120)

    eff_sorted = sorted([r for r in results if r.total_trades >= 100 and r.max_drawdown > 0],
                        key=lambda r: r.pnl_efficiency, reverse=True)
    for i, r in enumerate(eff_sorted[:10]):
        print(f"{i+1:<5} ${r.total_pnl:>8.2f} {r.total_trades:>8} {r.win_rate:>6.1f}% ${r.max_drawdown:>6.2f} eff={r.pnl_efficiency:>6.2f} | "
              f"{r.ask_cap:.2f} {r.spread_cap:.3f} {r.edge1:.2f}/{r.edge2:.2f}/{r.edge3:.2f}")

    print()
    print("LOWEST DRAWDOWN (min $400 PnL):")
    print("-" * 120)

    dd_sorted = sorted([r for r in results if r.total_pnl >= 400], key=lambda r: r.max_drawdown)
    for i, r in enumerate(dd_sorted[:10]):
        print(f"{i+1:<5} ${r.total_pnl:>8.2f} {r.total_trades:>8} {r.win_rate:>6.1f}% ${r.max_drawdown:>6.2f} | "
              f"{r.ask_cap:.2f} {r.spread_cap:.3f} {r.edge1:.2f}/{r.edge2:.2f}/{r.edge3:.2f}")

    # Baseline comparison
    print()
    print("=" * 80)
    print("  BASELINE COMPARISON (RULEV3.1)")
    print("=" * 80)
    print()
    print(f"  Baseline PnL:       ${baseline.total_pnl:.2f}")
    print(f"  Baseline Trades:    {baseline.total_trades}")
    print(f"  Baseline WR:        {baseline.win_rate:.1f}%")
    print(f"  Baseline MaxDD:     ${baseline.max_drawdown:.2f}")
    print()

    better_pnl = sum(1 for r in results if r.total_pnl > baseline.total_pnl)
    better_sharpe = sum(1 for r in results if r.sharpe_like > baseline.sharpe_like and r.total_trades >= 100)
    better_eff = sum(1 for r in results if r.pnl_efficiency > baseline.pnl_efficiency and r.total_trades >= 100)

    print(f"  Combos with better PnL:     {better_pnl}/{len(results)} ({better_pnl/len(results)*100:.1f}%)")
    print(f"  Combos with better Sharpe:  {better_sharpe}/{len(results)} ({better_sharpe/len(results)*100:.1f}%)")
    print(f"  Combos with better Eff:     {better_eff}/{len(results)} ({better_eff/len(results)*100:.1f}%)")

    return results, baseline


# ============================================================
# PARAMETER SENSITIVITY ANALYSIS
# ============================================================

def analyze_parameter_sensitivity(results: List[BacktestResult], baseline: BacktestResult):
    """Analyze which parameters have the most impact."""
    print()
    print("=" * 80)
    print("  PARAMETER SENSITIVITY ANALYSIS")
    print("=" * 80)

    # Group results by each parameter value
    param_names = ["ask_cap", "spread_cap", "ask_cut1", "ask_cut2",
                   "edge1", "edge2", "edge3", "max_trades_per_session", "kill_switch_L"]

    for param in param_names:
        print()
        print(f"  {param.upper()}:")
        print(f"  {'-'*60}")

        # Group by parameter value
        by_value = defaultdict(list)
        for r in results:
            val = getattr(r, param)
            by_value[val].append(r)

        print(f"  {'Value':<10} {'Count':>8} {'AvgPnL':>10} {'AvgWR':>8} {'AvgDD':>10} {'BestPnL':>10}")

        for val in sorted(by_value.keys()):
            group = by_value[val]
            avg_pnl = sum(r.total_pnl for r in group) / len(group)
            avg_wr = sum(r.win_rate for r in group) / len(group)
            avg_dd = sum(r.max_drawdown for r in group) / len(group)
            best_pnl = max(r.total_pnl for r in group)

            marker = " <-- baseline" if val == getattr(baseline, param) else ""
            print(f"  {val:<10} {len(group):>8} ${avg_pnl:>8.2f} {avg_wr:>7.1f}% ${avg_dd:>8.2f} ${best_pnl:>8.2f}{marker}")


def analyze_bucket_contribution(results: List[BacktestResult]):
    """Analyze which price buckets contribute most to PnL."""
    print()
    print("=" * 80)
    print("  BUCKET CONTRIBUTION ANALYSIS")
    print("=" * 80)
    print()

    # For top 50 results by PnL
    top_50 = results[:50]

    print("  TOP 50 CONFIGS - BUCKET BREAKDOWN:")
    print(f"  {'Config':>6} {'TotalPnL':>10} | {'B1 Tr':>6} {'B1 PnL':>10} | {'B2 Tr':>6} {'B2 PnL':>10} | {'B3 Tr':>6} {'B3 PnL':>10}")
    print(f"  {'-'*90}")

    for i, r in enumerate(top_50[:20]):
        print(f"  {i+1:>6} ${r.total_pnl:>8.2f} | {r.bucket1_trades:>6} ${r.bucket1_pnl:>8.2f} | "
              f"{r.bucket2_trades:>6} ${r.bucket2_pnl:>8.2f} | {r.bucket3_trades:>6} ${r.bucket3_pnl:>8.2f}")

    # Aggregate
    print()
    print("  AGGREGATE (TOP 50):")
    avg_b1_pnl = sum(r.bucket1_pnl for r in top_50) / len(top_50)
    avg_b2_pnl = sum(r.bucket2_pnl for r in top_50) / len(top_50)
    avg_b3_pnl = sum(r.bucket3_pnl for r in top_50) / len(top_50)
    print(f"    Bucket 1 avg PnL: ${avg_b1_pnl:.2f}")
    print(f"    Bucket 2 avg PnL: ${avg_b2_pnl:.2f}")
    print(f"    Bucket 3 avg PnL: ${avg_b3_pnl:.2f}")


def analyze_time_sensitivity(results: List[BacktestResult]):
    """Analyze early vs late CORE performance."""
    print()
    print("=" * 80)
    print("  TIME-OF-DAY SENSITIVITY")
    print("=" * 80)
    print()

    # For top 50 results
    top_50 = results[:50]

    early_pnl_total = sum(r.early_core_pnl for r in top_50)
    late_pnl_total = sum(r.late_core_pnl for r in top_50)
    early_trades = sum(r.early_core_trades for r in top_50)
    late_trades = sum(r.late_core_trades for r in top_50)

    print(f"  TOP 50 CONFIGS:")
    print(f"    Early CORE (2:30-3:00): {early_trades} trades, ${early_pnl_total:.2f} PnL")
    print(f"    Late CORE (3:00-3:45):  {late_trades} trades, ${late_pnl_total:.2f} PnL")

    if early_trades > 0:
        print(f"    Early PnL/trade: ${early_pnl_total/early_trades:.4f}")
    if late_trades > 0:
        print(f"    Late PnL/trade:  ${late_pnl_total/late_trades:.4f}")


def analyze_kill_switch_impact(results: List[BacktestResult]):
    """Analyze kill switch effectiveness."""
    print()
    print("=" * 80)
    print("  KILL SWITCH ANALYSIS")
    print("=" * 80)
    print()

    # Group by kill_switch_L
    by_kill = defaultdict(list)
    for r in results:
        by_kill[r.kill_switch_L].append(r)

    print(f"  {'KillL':>6} {'AvgPnL':>10} {'AvgAct':>8} {'AvgSaved':>10} {'BestPnL':>10}")
    print(f"  {'-'*50}")

    for k in sorted(by_kill.keys()):
        group = by_kill[k]
        avg_pnl = sum(r.total_pnl for r in group) / len(group)
        avg_act = sum(r.kill_switch_activations for r in group) / len(group)
        avg_saved = sum(r.pnl_saved_by_kill for r in group) / len(group)
        best_pnl = max(r.total_pnl for r in group)

        label = "disabled" if k == 999 else str(k)
        print(f"  {label:>6} ${avg_pnl:>8.2f} {avg_act:>7.1f} ${avg_saved:>8.2f} ${best_pnl:>8.2f}")


# ============================================================
# MAIN
# ============================================================

def main():
    import sys

    markets_dir = Path(__file__).parent.parent / 'markets_paper'
    output_dir = Path(__file__).parent.parent / 'sweep_results'

    if not markets_dir.exists():
        print(f"ERROR: {markets_dir} not found")
        return

    # Optional: limit combinations for testing
    max_combos = 0
    if len(sys.argv) > 1:
        try:
            max_combos = int(sys.argv[1])
        except:
            pass

    results, baseline = run_grid_search(markets_dir, output_dir, max_combos)

    if results:
        analyze_parameter_sensitivity(results, baseline)
        analyze_bucket_contribution(results)
        analyze_time_sensitivity(results)
        analyze_kill_switch_impact(results)

    print()
    print("=" * 80)
    print("  SWEEP COMPLETE")
    print("=" * 80)


if __name__ == '__main__':
    main()
