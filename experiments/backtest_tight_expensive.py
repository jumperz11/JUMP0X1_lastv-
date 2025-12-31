#!/usr/bin/env python3
"""
Backtest: Tight Expensive Bucket
================================
Compare ask>0.69 edge requirement:
  - V3.1 (current): edge >= 0.70
  - V3.1b (tight):  edge >= 0.72

All other buckets unchanged:
  - ask <= 0.66 → edge >= 0.64
  - ask <= 0.69 → edge >= 0.67
"""

import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from datetime import datetime

# ============================================================
# CONFIG
# ============================================================
SAFETY_CAP = 0.72
SPREAD_MAX = 0.02
POSITION_SIZE = 5.0
CORE_START_SECS = 150
CORE_END_SECS = 225


@dataclass
class Trade:
    session: str
    direction: str
    edge: float
    ask: float
    spread: float
    won: bool
    pnl: float
    bucket: str  # "<=0.66", "0.67-0.69", ">0.69"


@dataclass
class Result:
    version: str = ""
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    trades: List[Trade] = field(default_factory=list)
    # By bucket
    bucket_stats: dict = field(default_factory=dict)


def get_elapsed_secs(tick) -> float:
    mins_left = tick.get('minutesLeft', 15)
    return (15 - mins_left) * 60


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


def get_bucket(ask: float) -> str:
    if ask <= 0.66:
        return "<=0.66"
    elif ask <= 0.69:
        return "0.67-0.69"
    else:
        return ">0.69"


def passes_v31_gates(edge: float, ask: float, spread: float) -> Tuple[bool, str]:
    """V3.1 current: ask>0.69 → edge>=0.70"""
    if ask <= 0.66:
        required_edge = 0.64
    elif ask <= 0.69:
        required_edge = 0.67
    else:
        required_edge = 0.70  # Current

    if edge < required_edge:
        return False, f"edge {edge:.3f} < {required_edge}"
    if ask > SAFETY_CAP:
        return False, "ask > safety_cap"
    if spread > SPREAD_MAX:
        return False, "spread > max"
    return True, ""


def passes_v31b_gates(edge: float, ask: float, spread: float) -> Tuple[bool, str]:
    """V3.1b tight: ask>0.69 → edge>=0.72"""
    if ask <= 0.66:
        required_edge = 0.64
    elif ask <= 0.69:
        required_edge = 0.67
    else:
        required_edge = 0.72  # TIGHTER

    if edge < required_edge:
        return False, f"edge {edge:.3f} < {required_edge}"
    if ask > SAFETY_CAP:
        return False, "ask > safety_cap"
    if spread > SPREAD_MAX:
        return False, "spread > max"
    return True, ""


def simulate_session(session_path: Path) -> Tuple[Optional[Trade], Optional[Trade], Optional[str]]:
    """Simulate with V3.1 and V3.1b rules."""
    ticks_file = session_path / 'ticks.jsonl'
    if not ticks_file.exists():
        return None, None, None

    ticks = []
    with open(ticks_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    ticks.append(json.loads(line))
                except:
                    continue

    if not ticks:
        return None, None, None

    winner = get_winner(ticks)
    if not winner:
        return None, None, None

    v31_trade = None
    v31b_trade = None

    for tick in ticks:
        elapsed_secs = get_elapsed_secs(tick)

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
        if spread < 0 or bid > ask:
            continue

        bucket = get_bucket(ask)

        # V3.1 (current)
        if v31_trade is None:
            passes, _ = passes_v31_gates(edge, ask, spread)
            if passes:
                won = (direction == winner)
                shares = POSITION_SIZE / ask
                pnl = (1.0 - ask) * shares if won else -POSITION_SIZE
                v31_trade = Trade(
                    session=session_path.name,
                    direction=direction,
                    edge=edge,
                    ask=ask,
                    spread=spread,
                    won=won,
                    pnl=pnl,
                    bucket=bucket
                )

        # V3.1b (tight)
        if v31b_trade is None:
            passes, _ = passes_v31b_gates(edge, ask, spread)
            if passes:
                won = (direction == winner)
                shares = POSITION_SIZE / ask
                pnl = (1.0 - ask) * shares if won else -POSITION_SIZE
                v31b_trade = Trade(
                    session=session_path.name,
                    direction=direction,
                    edge=edge,
                    ask=ask,
                    spread=spread,
                    won=won,
                    pnl=pnl,
                    bucket=bucket
                )

        if v31_trade and v31b_trade:
            break

    return v31_trade, v31b_trade, winner


def run_backtest(markets_dir: Path):
    """Run backtest for both versions."""
    v31 = Result(version="V3.1 (edge>=0.70)")
    v31b = Result(version="V3.1b (edge>=0.72)")

    # Initialize bucket stats
    for r in [v31, v31b]:
        r.bucket_stats = {
            "<=0.66": {"trades": 0, "wins": 0, "pnl": 0.0},
            "0.67-0.69": {"trades": 0, "wins": 0, "pnl": 0.0},
            ">0.69": {"trades": 0, "wins": 0, "pnl": 0.0},
        }

    sessions = sorted([
        d for d in markets_dir.iterdir()
        if d.is_dir() and d.name.startswith('btc-updown-15m-')
    ])

    v31_running = 0.0
    v31_peak = 0.0
    v31b_running = 0.0
    v31b_peak = 0.0

    # Track trades V3.1 took but V3.1b skipped
    skipped_trades = []

    for i, session_path in enumerate(sessions):
        if (i + 1) % 500 == 0:
            print(f"  Processing {i+1}/{len(sessions)}...")

        v31_trade, v31b_trade, _ = simulate_session(session_path)

        if v31_trade:
            v31.total_trades += 1
            v31.trades.append(v31_trade)
            if v31_trade.won:
                v31.wins += 1
            else:
                v31.losses += 1
            v31.total_pnl += v31_trade.pnl

            # Bucket stats
            b = v31_trade.bucket
            v31.bucket_stats[b]["trades"] += 1
            if v31_trade.won:
                v31.bucket_stats[b]["wins"] += 1
            v31.bucket_stats[b]["pnl"] += v31_trade.pnl

            # Drawdown
            v31_running += v31_trade.pnl
            if v31_running > v31_peak:
                v31_peak = v31_running
            dd = v31_peak - v31_running
            if dd > v31.max_drawdown:
                v31.max_drawdown = dd

            # Track if V3.1b skipped
            if v31b_trade is None:
                skipped_trades.append(v31_trade)

        if v31b_trade:
            v31b.total_trades += 1
            v31b.trades.append(v31b_trade)
            if v31b_trade.won:
                v31b.wins += 1
            else:
                v31b.losses += 1
            v31b.total_pnl += v31b_trade.pnl

            # Bucket stats
            b = v31b_trade.bucket
            v31b.bucket_stats[b]["trades"] += 1
            if v31b_trade.won:
                v31b.bucket_stats[b]["wins"] += 1
            v31b.bucket_stats[b]["pnl"] += v31b_trade.pnl

            # Drawdown
            v31b_running += v31b_trade.pnl
            if v31b_running > v31b_peak:
                v31b_peak = v31b_running
            dd = v31b_peak - v31b_running
            if dd > v31b.max_drawdown:
                v31b.max_drawdown = dd

    return v31, v31b, skipped_trades, len(sessions)


def safe_div(a, b):
    return a / b if b > 0 else 0


def print_results(v31: Result, v31b: Result, skipped: List[Trade], total_sessions: int):
    print()
    print("=" * 75)
    print("  TIGHT EXPENSIVE BUCKET BACKTEST")
    print("=" * 75)
    print()
    print("  Change: ask > 0.69 -> edge >= 0.72 (was 0.70)")
    print()
    print("-" * 75)
    print(f"  {'Metric':<30} {'V3.1 (0.70)':>18} {'V3.1b (0.72)':>18}")
    print("-" * 75)
    print(f"  {'Sessions':<30} {total_sessions:>18}")
    print(f"  {'Total Trades':<30} {v31.total_trades:>18} {v31b.total_trades:>18}")
    print(f"  {'Trades Removed':<30} {'':<18} {v31.total_trades - v31b.total_trades:>18}")
    print(f"  {'Wins':<30} {v31.wins:>18} {v31b.wins:>18}")
    print(f"  {'Losses':<30} {v31.losses:>18} {v31b.losses:>18}")
    wr31 = safe_div(v31.wins * 100, v31.total_trades)
    wr31b = safe_div(v31b.wins * 100, v31b.total_trades)
    print(f"  {'Win Rate (%)':<30} {wr31:>17.2f}% {wr31b:>17.2f}%")
    print(f"  {'Total PnL ($)':<30} {v31.total_pnl:>18.2f} {v31b.total_pnl:>18.2f}")
    pnl_diff = v31b.total_pnl - v31.total_pnl
    print(f"  {'PnL Change ($)':<30} {'':<18} {pnl_diff:>+18.2f}")
    avg31 = safe_div(v31.total_pnl, v31.total_trades)
    avg31b = safe_div(v31b.total_pnl, v31b.total_trades)
    print(f"  {'PnL/Trade ($)':<30} {avg31:>18.4f} {avg31b:>18.4f}")
    print(f"  {'Max Drawdown ($)':<30} {v31.max_drawdown:>18.2f} {v31b.max_drawdown:>18.2f}")
    dd_improvement = v31.max_drawdown - v31b.max_drawdown
    print(f"  {'DD Improvement ($)':<30} {'':<18} {dd_improvement:>+18.2f}")
    print("-" * 75)

    # Bucket breakdown
    print()
    print("  PNL BY ASK BUCKET:")
    print()
    print(f"  {'Bucket':<12} {'V3.1 Trades':>12} {'V3.1 PnL':>12} {'V3.1b Trades':>14} {'V3.1b PnL':>12} {'Change':>10}")
    print(f"  {'-'*74}")

    for bucket in ["<=0.66", "0.67-0.69", ">0.69"]:
        t31 = v31.bucket_stats[bucket]["trades"]
        p31 = v31.bucket_stats[bucket]["pnl"]
        t31b = v31b.bucket_stats[bucket]["trades"]
        p31b = v31b.bucket_stats[bucket]["pnl"]
        change = p31b - p31
        print(f"  {bucket:<12} {t31:>12} ${p31:>10.2f} {t31b:>14} ${p31b:>10.2f} ${change:>+8.2f}")

    # Skipped trades analysis
    print()
    print("  SKIPPED TRADES (>0.69 bucket, edge 0.70-0.72):")
    skipped_expensive = [t for t in skipped if t.bucket == ">0.69"]
    if skipped_expensive:
        s_wins = sum(1 for t in skipped_expensive if t.won)
        s_losses = len(skipped_expensive) - s_wins
        s_pnl = sum(t.pnl for t in skipped_expensive)
        s_wr = safe_div(s_wins * 100, len(skipped_expensive))
        print(f"    Count:        {len(skipped_expensive)}")
        print(f"    Wins:         {s_wins}")
        print(f"    Losses:       {s_losses}")
        print(f"    Win Rate:     {s_wr:.1f}%")
        print(f"    Total PnL:    ${s_pnl:+.2f}")
        if s_pnl < 0:
            print(f"    -> CORRECTLY avoided ${abs(s_pnl):.2f} in losses")
        else:
            print(f"    -> INCORRECTLY skipped ${s_pnl:.2f} in profits")
    else:
        print("    None")

    # Verdict
    print()
    print("=" * 75)
    print("  VERDICT")
    print("=" * 75)

    if pnl_diff > 0:
        print(f"  [OK] V3.1b (tight) WINS")
        print(f"     +${pnl_diff:.2f} PnL improvement")
        print(f"     +{wr31b - wr31:.2f}% win rate")
        print(f"     ${dd_improvement:.2f} less drawdown")
        print(f"     {v31.total_trades - v31b.total_trades} trades removed from expensive bucket")
    elif pnl_diff < 0:
        print(f"  [WORSE] V3.1b underperforms by ${abs(pnl_diff):.2f}")
    else:
        print(f"  [--] No significant difference")

    print()
    print("=" * 75)


def main():
    markets_dir = Path(__file__).parent.parent / 'markets_paper'

    if not markets_dir.exists():
        print(f"ERROR: markets_paper not found")
        return

    print()
    print("=" * 75)
    print("  LOADING DATA...")
    print("=" * 75)

    sessions = list(markets_dir.glob('btc-updown-15m-*'))
    print(f"  Found {len(sessions)} sessions")
    print("  Running backtest...")
    print()

    v31, v31b, skipped, total = run_backtest(markets_dir)
    print_results(v31, v31b, skipped, total)

    # Save log
    log_dir = Path(__file__).parent.parent / 'backtest_full_logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = log_dir / f'tight_expensive_{ts}.log'

    with open(log_file, 'w') as f:
        f.write(f"Tight Expensive Bucket Backtest\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n\n")
        f.write(f"V3.1 (edge>=0.70): {v31.total_trades} trades, ${v31.total_pnl:.2f} PnL\n")
        f.write(f"V3.1b (edge>=0.72): {v31b.total_trades} trades, ${v31b.total_pnl:.2f} PnL\n")
        f.write(f"Change: ${v31b.total_pnl - v31.total_pnl:+.2f}\n")

    print(f"  Log saved: {log_file}")


if __name__ == '__main__':
    main()
