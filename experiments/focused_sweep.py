#!/usr/bin/env python3
"""
FOCUSED PARAMETER SWEEP
=======================
Target the parameters that matter most based on prior analysis.
"""

import json
import csv
import time
import itertools
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from collections import defaultdict
import statistics

# ============================================================
# FOCUSED PARAMETER GRID
# ============================================================

# Fixed at baseline (low sensitivity from prior analysis)
FIXED_PARAMS = {
    "ask_cut1": 0.66,
    "ask_cut2": 0.69,
    "edge1": 0.64,
    "edge2": 0.67,
    "max_trades_per_session": 1,
}

# Sweep these (high sensitivity)
SWEEP_GRID = {
    "ask_cap": [0.68, 0.70, 0.72, 0.74],
    "spread_cap": [0.015, 0.020, 0.025, 0.030],
    "edge3": [0.66, 0.68, 0.70, 0.72, 0.74],
    "kill_switch_L": [3, 5, 999],
}

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

POSITION_SIZE = 5.0
CORE_START_SECS = 150
CORE_END_SECS = 225


@dataclass
class Trade:
    session_id: str
    direction: str
    edge: float
    ask: float
    spread: float
    elapsed_secs: float
    won: bool
    pnl: float
    day: str


@dataclass
class Result:
    ask_cap: float = 0.0
    spread_cap: float = 0.0
    edge3: float = 0.0
    kill_switch_L: int = 3

    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    win_rate: float = 0.0
    avg_pnl_per_trade: float = 0.0
    max_drawdown: float = 0.0

    sharpe_like: float = 0.0
    pnl_efficiency: float = 0.0

    bucket3_trades: int = 0
    bucket3_pnl: float = 0.0

    kill_activations: int = 0
    pnl_saved: float = 0.0

    delta_pnl: float = 0.0
    delta_trades: int = 0


def load_sessions(markets_dir):
    sessions = []
    for d in sorted(markets_dir.iterdir()):
        if d.is_dir() and d.name.startswith('btc-updown-15m-'):
            try:
                ts = int(d.name.split('-')[-1])
                dt = datetime.fromtimestamp(ts)
                day = dt.strftime('%Y-%m-%d')
            except:
                day = "unknown"
            sessions.append((d, day))
    return sessions


def load_ticks(session_path):
    ticks_file = session_path / 'ticks.jsonl'
    if not ticks_file.exists():
        return []
    ticks = []
    with open(ticks_file, 'r') as f:
        for line in f:
            if line.strip():
                try:
                    ticks.append(json.loads(line))
                except:
                    pass
    return ticks


def get_winner(ticks):
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


def run_backtest(sessions, params, preloaded):
    result = Result(
        ask_cap=params["ask_cap"],
        spread_cap=params["spread_cap"],
        edge3=params["edge3"],
        kill_switch_L=params["kill_switch_L"]
    )

    trades = []
    daily_pnl = defaultdict(float)
    consecutive_losses = 0
    kill_active = False
    trades_after_kill = []

    for session_path, day in sessions:
        ticks = preloaded.get(session_path.name, [])
        if not ticks:
            continue

        winner = get_winner(ticks)
        if not winner:
            continue

        traded = False

        for tick in ticks:
            mins_left = tick.get('minutesLeft', 15)
            elapsed = (15 - mins_left) * 60

            if elapsed < CORE_START_SECS or elapsed > CORE_END_SECS:
                continue

            price = tick.get('price')
            best = tick.get('best')
            if not price or not best:
                continue

            up_mid = price.get('Up')
            down_mid = price.get('Down')
            if up_mid is None or down_mid is None:
                continue

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
            if not ask or not bid or ask <= 0:
                continue

            spread = ask - bid
            if spread < 0 or bid > ask:
                continue

            # GATES
            if traded:
                continue

            # Dynamic edge
            if ask <= FIXED_PARAMS["ask_cut1"]:
                req_edge = FIXED_PARAMS["edge1"]
            elif ask <= FIXED_PARAMS["ask_cut2"]:
                req_edge = FIXED_PARAMS["edge2"]
            else:
                req_edge = params["edge3"]

            if edge < req_edge:
                continue
            if ask > params["ask_cap"]:
                continue
            if spread > params["spread_cap"]:
                continue

            # Execute
            won = (direction == winner)
            shares = POSITION_SIZE / ask
            pnl = (1.0 - ask) * shares if won else -POSITION_SIZE

            trade = Trade(session_path.name, direction, edge, ask, spread, elapsed, won, pnl, day)

            if kill_active:
                trades_after_kill.append(trade)
            else:
                trades.append(trade)
                traded = True
                daily_pnl[day] += pnl

                if won:
                    consecutive_losses = 0
                else:
                    consecutive_losses += 1
                    if consecutive_losses >= params["kill_switch_L"]:
                        kill_active = True
                        result.kill_activations += 1

            break

    if not trades:
        return result

    result.total_trades = len(trades)
    result.wins = sum(1 for t in trades if t.won)
    result.losses = result.total_trades - result.wins
    result.total_pnl = sum(t.pnl for t in trades)
    result.win_rate = result.wins / result.total_trades * 100
    result.avg_pnl_per_trade = result.total_pnl / result.total_trades

    # Max drawdown
    running = 0
    peak = 0
    max_dd = 0
    for t in trades:
        running += t.pnl
        if running > peak:
            peak = running
        dd = peak - running
        if dd > max_dd:
            max_dd = dd
    result.max_drawdown = max_dd

    # Sharpe-like
    if daily_pnl:
        vals = list(daily_pnl.values())
        if len(vals) > 1:
            std = statistics.stdev(vals)
            avg = statistics.mean(vals)
            if std > 0:
                result.sharpe_like = avg / std

    # Efficiency
    if max_dd > 0:
        result.pnl_efficiency = result.total_pnl / max_dd

    # Bucket 3
    for t in trades:
        if t.ask > FIXED_PARAMS["ask_cut2"]:
            result.bucket3_trades += 1
            result.bucket3_pnl += t.pnl

    # Kill switch savings
    result.pnl_saved = -sum(t.pnl for t in trades_after_kill)

    return result


def main():
    markets_dir = Path(__file__).parent.parent / 'markets_paper'
    output_dir = Path(__file__).parent.parent / 'sweep_results'

    print("=" * 80)
    print("  FOCUSED PARAMETER SWEEP")
    print("=" * 80)

    # Load data
    print("\nLoading sessions...")
    sessions = load_sessions(markets_dir)
    print(f"  Found {len(sessions)} sessions")

    print("Preloading ticks...")
    preloaded = {}
    for path, day in sessions:
        preloaded[path.name] = load_ticks(path)
    print(f"  Preloaded {len(preloaded)} sessions")

    # Generate combinations
    keys = list(SWEEP_GRID.keys())
    values = [SWEEP_GRID[k] for k in keys]
    combinations = [dict(zip(keys, combo)) for combo in itertools.product(*values)]
    print(f"\nRunning {len(combinations)} combinations...")

    # Run baseline
    baseline = run_backtest(sessions, BASELINE, preloaded)
    print(f"Baseline: {baseline.total_trades} trades, ${baseline.total_pnl:.2f} PnL")

    # Run all
    results = []
    start = time.time()

    for i, sweep_params in enumerate(combinations):
        params = {**FIXED_PARAMS, **sweep_params}

        if (i + 1) % 20 == 0:
            elapsed = time.time() - start
            print(f"  {i+1}/{len(combinations)} ({elapsed:.1f}s)")

        r = run_backtest(sessions, params, preloaded)
        r.delta_pnl = r.total_pnl - baseline.total_pnl
        r.delta_trades = r.total_trades - baseline.total_trades
        results.append(r)

    elapsed = time.time() - start
    print(f"\nCompleted in {elapsed:.1f}s")

    # Sort by PnL
    results.sort(key=lambda r: r.total_pnl, reverse=True)

    # Save CSV
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    csv_file = output_dir / f'focused_sweep_{ts}.csv'

    with open(csv_file, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(results[0]).keys()))
        writer.writeheader()
        for r in results:
            writer.writerow(asdict(r))
    print(f"\nSaved to {csv_file}")

    # Print results
    print()
    print("=" * 100)
    print("  TOP 20 BY PNL")
    print("=" * 100)
    print(f"{'#':<3} {'ask_cap':>8} {'spread':>8} {'edge3':>8} {'kill':>6} | {'Trades':>7} {'WR%':>7} {'PnL':>10} {'MaxDD':>8} {'Eff':>6} {'B3 PnL':>10}")
    print("-" * 100)

    for i, r in enumerate(results[:20]):
        kill_str = "off" if r.kill_switch_L == 999 else str(r.kill_switch_L)
        print(f"{i+1:<3} {r.ask_cap:>8.2f} {r.spread_cap:>8.3f} {r.edge3:>8.2f} {kill_str:>6} | "
              f"{r.total_trades:>7} {r.win_rate:>6.1f}% ${r.total_pnl:>8.2f} ${r.max_drawdown:>6.2f} {r.pnl_efficiency:>5.2f} ${r.bucket3_pnl:>8.2f}")

    # Best by efficiency
    print()
    print("=" * 100)
    print("  TOP 10 BY PNL EFFICIENCY (PnL/MaxDD)")
    print("=" * 100)

    eff_sorted = sorted([r for r in results if r.total_trades >= 100 and r.max_drawdown > 0],
                        key=lambda r: r.pnl_efficiency, reverse=True)

    for i, r in enumerate(eff_sorted[:10]):
        kill_str = "off" if r.kill_switch_L == 999 else str(r.kill_switch_L)
        print(f"{i+1:<3} {r.ask_cap:>8.2f} {r.spread_cap:>8.3f} {r.edge3:>8.2f} {kill_str:>6} | "
              f"{r.total_trades:>7} {r.win_rate:>6.1f}% ${r.total_pnl:>8.2f} ${r.max_drawdown:>6.2f} eff={r.pnl_efficiency:>5.2f}")

    # Parameter sensitivity
    print()
    print("=" * 100)
    print("  PARAMETER SENSITIVITY")
    print("=" * 100)

    for param in ["ask_cap", "spread_cap", "edge3", "kill_switch_L"]:
        print(f"\n  {param.upper()}:")
        by_val = defaultdict(list)
        for r in results:
            by_val[getattr(r, param)].append(r)

        print(f"  {'Value':<10} {'Count':>6} {'AvgPnL':>10} {'AvgDD':>10} {'AvgEff':>8}")
        for val in sorted(by_val.keys()):
            grp = by_val[val]
            avg_pnl = sum(r.total_pnl for r in grp) / len(grp)
            avg_dd = sum(r.max_drawdown for r in grp) / len(grp)
            avg_eff = sum(r.pnl_efficiency for r in grp) / len(grp)
            marker = " <-- baseline" if val == BASELINE.get(param) else ""
            print(f"  {val:<10} {len(grp):>6} ${avg_pnl:>8.2f} ${avg_dd:>8.2f} {avg_eff:>7.2f}{marker}")

    # Bucket 3 analysis
    print()
    print("=" * 100)
    print("  BUCKET 3 (ask > 0.69) ANALYSIS")
    print("=" * 100)

    by_edge3 = defaultdict(list)
    for r in results:
        by_edge3[r.edge3].append(r)

    print(f"\n  {'edge3':<8} {'B3 Trades':>10} {'B3 PnL':>12} {'B3 PnL/Tr':>12}")
    for e3 in sorted(by_edge3.keys()):
        grp = by_edge3[e3]
        total_b3_trades = sum(r.bucket3_trades for r in grp)
        total_b3_pnl = sum(r.bucket3_pnl for r in grp)
        avg_b3_per_trade = total_b3_pnl / total_b3_trades if total_b3_trades > 0 else 0
        print(f"  {e3:<8.2f} {total_b3_trades:>10} ${total_b3_pnl:>10.2f} ${avg_b3_per_trade:>10.4f}")

    print()
    print("=" * 100)
    print("  COMPLETE")
    print("=" * 100)


if __name__ == '__main__':
    main()
