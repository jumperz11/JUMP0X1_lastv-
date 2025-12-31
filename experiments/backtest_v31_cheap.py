#!/usr/bin/env python3
"""
RULEV3.1 vs RULEV3.1 + CHEAP Backtest
=====================================
Tests adding an optional CHEAP early entry alongside NORMAL.

TWO INDEPENDENT BUTTONS:

BUTTON 1 - NORMAL (unchanged V3.1):
  - DYNAMIC_EDGE thresholds
  - ask <= 0.72
  - size = $5
  - 1 trade per session

BUTTON 2 - CHEAP (new, additive):
  - ask <= 0.56
  - edge >= 0.60
  - size = $2 (small probe)
  - 1 trade per session
  - INDEPENDENT from NORMAL

Key: CHEAP does not replace NORMAL. Both can fire in same session.
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

# NORMAL entry (V3.1)
NORMAL_SIZE = 5.0

# CHEAP entry (new) - adjusted thresholds based on data analysis
CHEAP_ASK_MAX = 0.60  # Was 0.56 - never happens with edge >= 0.60
CHEAP_EDGE_MIN = 0.58  # Was 0.60 - lowered to find early value
CHEAP_SIZE = 2.0  # Small probe size

# CORE zone: 2:30 - 3:45
CORE_START_SECS = 150
CORE_END_SECS = 225


@dataclass
class Trade:
    session: str
    direction: str
    edge: float
    ask: float
    spread: float
    elapsed_secs: float
    won: bool
    pnl: float
    entry_type: str  # "NORMAL" or "CHEAP"
    size: float


@dataclass
class Result:
    name: str = ""
    total_sessions: int = 0
    normal_trades: int = 0
    cheap_trades: int = 0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    trades: List[Trade] = field(default_factory=list)

    # Breakdown
    normal_wins: int = 0
    normal_losses: int = 0
    normal_pnl: float = 0.0
    cheap_wins: int = 0
    cheap_losses: int = 0
    cheap_pnl: float = 0.0


def get_elapsed_secs(tick):
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


def passes_normal_gates(edge: float, ask: float, spread: float) -> bool:
    """V3.1 NORMAL entry gates (DYNAMIC_EDGE)."""
    # DYNAMIC_EDGE
    if ask <= 0.66:
        required_edge = 0.64
    elif ask <= 0.69:
        required_edge = 0.67
    else:
        required_edge = 0.70

    if edge < required_edge:
        return False
    if ask > SAFETY_CAP:
        return False
    if spread > SPREAD_MAX:
        return False
    return True


def passes_cheap_gates(edge: float, ask: float, spread: float) -> bool:
    """CHEAP early entry gates."""
    if ask > CHEAP_ASK_MAX:
        return False
    if edge < CHEAP_EDGE_MIN:
        return False
    if spread > SPREAD_MAX:
        return False
    return True


def simulate_session(session_path, enable_cheap: bool) -> Tuple[List[Trade], str]:
    """
    Simulate session with NORMAL and optionally CHEAP entries.
    Returns list of trades and winner.
    """
    ticks_file = session_path / 'ticks.jsonl'
    if not ticks_file.exists():
        return [], None

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
        return [], None

    winner = get_winner(ticks)
    if not winner:
        return [], None

    trades = []
    normal_done = False
    cheap_done = False

    for tick in ticks:
        elapsed_secs = get_elapsed_secs(tick)

        # CORE zone only
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
        if ask is None or bid is None:
            continue

        spread = ask - bid
        if spread < 0 or bid > ask:
            continue

        # Try CHEAP entry first (fires at low prices, early in session)
        if enable_cheap and not cheap_done:
            if passes_cheap_gates(edge, ask, spread):
                won = (direction == winner)
                shares = CHEAP_SIZE / ask
                pnl = (1.0 - ask) * shares if won else -CHEAP_SIZE
                trades.append(Trade(
                    session=session_path.name,
                    direction=direction,
                    edge=edge,
                    ask=ask,
                    spread=spread,
                    elapsed_secs=elapsed_secs,
                    won=won,
                    pnl=pnl,
                    entry_type="CHEAP",
                    size=CHEAP_SIZE
                ))
                cheap_done = True

        # Try NORMAL entry (independent of CHEAP)
        if not normal_done:
            if passes_normal_gates(edge, ask, spread):
                won = (direction == winner)
                shares = NORMAL_SIZE / ask
                pnl = (1.0 - ask) * shares if won else -NORMAL_SIZE
                trades.append(Trade(
                    session=session_path.name,
                    direction=direction,
                    edge=edge,
                    ask=ask,
                    spread=spread,
                    elapsed_secs=elapsed_secs,
                    won=won,
                    pnl=pnl,
                    entry_type="NORMAL",
                    size=NORMAL_SIZE
                ))
                normal_done = True

        # Both done
        if normal_done and (cheap_done or not enable_cheap):
            break

    return trades, winner


def run_backtest(markets_dir, enable_cheap: bool) -> Result:
    """Run backtest with or without CHEAP entry."""
    result = Result(name="V3.1 + CHEAP" if enable_cheap else "V3.1 NORMAL")

    sessions = sorted([
        d for d in markets_dir.iterdir()
        if d.is_dir() and d.name.startswith('btc-updown-15m-')
    ])

    result.total_sessions = len(sessions)
    running_pnl = 0.0
    peak_pnl = 0.0

    for session_path in sessions:
        trades, winner = simulate_session(session_path, enable_cheap)

        for trade in trades:
            result.trades.append(trade)
            result.total_trades += 1

            if trade.won:
                result.wins += 1
            else:
                result.losses += 1

            result.total_pnl += trade.pnl
            running_pnl += trade.pnl

            if running_pnl > peak_pnl:
                peak_pnl = running_pnl
            dd = peak_pnl - running_pnl
            if dd > result.max_drawdown:
                result.max_drawdown = dd

            # Breakdown by type
            if trade.entry_type == "NORMAL":
                result.normal_trades += 1
                result.normal_pnl += trade.pnl
                if trade.won:
                    result.normal_wins += 1
                else:
                    result.normal_losses += 1
            else:
                result.cheap_trades += 1
                result.cheap_pnl += trade.pnl
                if trade.won:
                    result.cheap_wins += 1
                else:
                    result.cheap_losses += 1

    return result


def safe_div(a, b):
    return a / b if b > 0 else 0


def print_results(normal_only: Result, with_cheap: Result):
    """Print comparison."""
    print()
    print("=" * 70)
    print("  RULEV3.1 NORMAL vs RULEV3.1 + CHEAP BACKTEST")
    print("=" * 70)
    print()
    print("  NORMAL: V3.1 DYNAMIC_EDGE, $5 size")
    print(f"  CHEAP:  ask <= {CHEAP_ASK_MAX}, edge >= {CHEAP_EDGE_MIN}, ${CHEAP_SIZE} size")
    print()
    print("-" * 70)
    print(f"  {'Metric':<30} {'NORMAL only':>15} {'NORMAL+CHEAP':>15}")
    print("-" * 70)
    print(f"  {'Sessions':<30} {normal_only.total_sessions:>15}")
    print(f"  {'Total trades':<30} {normal_only.total_trades:>15} {with_cheap.total_trades:>15}")
    print(f"  {'  - NORMAL trades':<30} {normal_only.normal_trades:>15} {with_cheap.normal_trades:>15}")
    print(f"  {'  - CHEAP trades':<30} {normal_only.cheap_trades:>15} {with_cheap.cheap_trades:>15}")
    print(f"  {'Wins':<30} {normal_only.wins:>15} {with_cheap.wins:>15}")
    print(f"  {'Losses':<30} {normal_only.losses:>15} {with_cheap.losses:>15}")
    print(f"  {'Win rate (%)':<30} {safe_div(normal_only.wins*100, normal_only.total_trades):>15.2f} {safe_div(with_cheap.wins*100, with_cheap.total_trades):>15.2f}")
    print(f"  {'Total PnL ($)':<30} {normal_only.total_pnl:>15.2f} {with_cheap.total_pnl:>15.2f}")
    print(f"  {'PnL delta ($)':<30} {'':<15} {with_cheap.total_pnl - normal_only.total_pnl:>+15.2f}")
    print(f"  {'Max drawdown ($)':<30} {normal_only.max_drawdown:>15.2f} {with_cheap.max_drawdown:>15.2f}")
    print("-" * 70)

    # CHEAP breakdown
    if with_cheap.cheap_trades > 0:
        print()
        print("  CHEAP ENTRY BREAKDOWN:")
        cheap_wr = safe_div(with_cheap.cheap_wins * 100, with_cheap.cheap_trades)
        cheap_ev = safe_div(with_cheap.cheap_pnl, with_cheap.cheap_trades)
        print(f"    Trades:    {with_cheap.cheap_trades}")
        print(f"    Wins:      {with_cheap.cheap_wins}")
        print(f"    Losses:    {with_cheap.cheap_losses}")
        print(f"    Win Rate:  {cheap_wr:.1f}%")
        print(f"    Total PnL: ${with_cheap.cheap_pnl:+.2f}")
        print(f"    Avg PnL:   ${cheap_ev:+.4f}")

        # Analyze CHEAP trades by ask price
        cheap_trades = [t for t in with_cheap.trades if t.entry_type == "CHEAP"]
        if cheap_trades:
            avg_ask = sum(t.ask for t in cheap_trades) / len(cheap_trades)
            avg_edge = sum(t.edge for t in cheap_trades) / len(cheap_trades)
            print(f"    Avg ask:   ${avg_ask:.4f}")
            print(f"    Avg edge:  {avg_edge:.4f}")

    # Verdict
    print()
    print("=" * 70)
    print("  VERDICT")
    print("=" * 70)

    pnl_diff = with_cheap.total_pnl - normal_only.total_pnl
    extra_trades = with_cheap.total_trades - normal_only.total_trades

    if pnl_diff > 0:
        print(f"  [+] CHEAP adds ${pnl_diff:.2f} PnL with {extra_trades} extra trades")
        if with_cheap.cheap_trades > 0:
            cheap_ev = with_cheap.cheap_pnl / with_cheap.cheap_trades
            print(f"      CHEAP avg: ${cheap_ev:+.4f}/trade")
    elif pnl_diff < 0:
        print(f"  [-] CHEAP costs ${abs(pnl_diff):.2f} - not worth it")
    else:
        print(f"  [=] No significant difference")

    print()
    print("=" * 70)


def main():
    markets_dir = Path(__file__).parent.parent / 'markets_paper'

    if not markets_dir.exists():
        print(f"ERROR: markets_paper not found at {markets_dir}")
        return

    print()
    print("=" * 70)
    print("  LOADING DATA...")
    print("=" * 70)

    sessions = list(markets_dir.glob('btc-updown-15m-*'))
    print(f"  Found {len(sessions)} BTC sessions")

    print("  Running V3.1 NORMAL only...")
    normal_only = run_backtest(markets_dir, enable_cheap=False)

    print("  Running V3.1 NORMAL + CHEAP...")
    with_cheap = run_backtest(markets_dir, enable_cheap=True)

    print_results(normal_only, with_cheap)

    # Save log
    log_dir = Path(__file__).parent.parent / 'backtest_full_logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = log_dir / f'v31_cheap_{timestamp}.log'

    with open(log_file, 'w') as f:
        f.write(f"V3.1 NORMAL vs V3.1 + CHEAP\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n\n")
        f.write(f"CHEAP config: ask <= {CHEAP_ASK_MAX}, edge >= {CHEAP_EDGE_MIN}, size ${CHEAP_SIZE}\n\n")
        f.write(f"NORMAL only:\n")
        f.write(f"  Trades: {normal_only.total_trades}\n")
        f.write(f"  PnL: ${normal_only.total_pnl:.2f}\n\n")
        f.write(f"NORMAL + CHEAP:\n")
        f.write(f"  Trades: {with_cheap.total_trades} (+{with_cheap.cheap_trades} CHEAP)\n")
        f.write(f"  PnL: ${with_cheap.total_pnl:.2f}\n")
        f.write(f"  CHEAP PnL: ${with_cheap.cheap_pnl:.2f}\n")

    print(f"  Log: {log_file}")


if __name__ == '__main__':
    main()
