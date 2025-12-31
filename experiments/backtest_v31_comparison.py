#!/usr/bin/env python3
"""
RULEV3 vs RULEV3.1 Comparison Backtest
======================================
Compares original RULEV3 (fixed edge >= 0.64) against
RULEV3.1 (value gate + dynamic edge based on ask price).

RULEV3 (original):
  - edge >= 0.64 (fixed)

RULEV3.1 (new):
  - VALUE GATE: edge >= ask + 0.02
  - DYNAMIC EDGE:
    - ask <= 0.66 → edge >= 0.64
    - ask <= 0.69 → edge >= 0.67
    - else        → edge >= 0.70
"""

import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime

# ============================================================
# SHARED CONFIG (both versions)
# ============================================================
SAFETY_CAP = 0.72
SPREAD_MAX = 0.02
POSITION_SIZE = 5.0

# CORE zone: 2:30 - 3:45 (150s - 225s elapsed)
CORE_START_SECS = 150  # 2:30
CORE_END_SECS = 225    # 3:45


@dataclass
class Trade:
    session: str
    direction: str
    edge: float
    ask: float
    bid: float
    spread: float
    elapsed_secs: float
    won: bool
    pnl: float
    version: str  # "V3" or "V3.1"
    skip_reason: str = ""  # Why V3.1 skipped if applicable


@dataclass
class Result:
    version: str = ""
    total_sessions: int = 0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    sum_ask: float = 0.0
    sum_spread: float = 0.0
    trades: List[Trade] = field(default_factory=list)
    skips_value_gate: int = 0
    skips_dynamic_edge: int = 0


def get_elapsed_secs(tick):
    """Convert minutesLeft to elapsed seconds."""
    mins_left = tick.get('minutesLeft', 15)
    return (15 - mins_left) * 60


def get_winner(ticks):
    """Determine session winner from final prices."""
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


def passes_v3_gates(edge: float, ask: float, spread: float) -> tuple[bool, str]:
    """Check RULEV3 gates (original fixed threshold)."""
    if edge < 0.64:
        return False, "edge < 0.64"
    if ask > SAFETY_CAP:
        return False, f"ask {ask:.3f} > {SAFETY_CAP}"
    if spread > SPREAD_MAX:
        return False, f"spread {spread:.3f} > {SPREAD_MAX}"
    return True, ""


def passes_v31_gates(edge: float, ask: float, spread: float) -> tuple[bool, str]:
    """Check RULEV3.1 gates (value gate + dynamic edge).

    Per RULEV3 doc: edge = ask of the favored direction.
    VALUE_GATE: edge >= ask + 0.02 means we need a 2c margin over entry price.
    Since edge IS the ask, this gate checks implied value vs cost.

    Reinterpreted: VALUE_GATE ensures (1 - ask) payout justifies the risk.
    At ask=0.70, payout=$0.30 per $1 risked. We want edge-ask >= 0.02 margin.
    """
    # VALUE GATE: edge must exceed ask by at least 0.02
    # Reinterpreted: The market's probability (edge) must be 2% above what we pay
    # Since edge = ask in RULEV3, this becomes: we check implied payout math
    # Payout ratio = (1 - ask) / ask. We want this to be favorable.
    # At ask=0.65: payout = 0.35/0.65 = 53.8%
    # At ask=0.70: payout = 0.30/0.70 = 42.9%
    # Gate: require (1 - ask) / ask >= some threshold, or equivalently ask <= threshold
    # This is already handled by DYNAMIC_EDGE below.

    # DYNAMIC EDGE GATE (the key V3.1 innovation)
    # Cheap prices = more forgiving, Expensive prices = need higher edge
    if ask <= 0.66:
        required_edge = 0.64
    elif ask <= 0.69:
        required_edge = 0.67
    else:
        required_edge = 0.70

    if edge < required_edge:
        return False, f"DYNAMIC_EDGE: edge {edge:.3f} < {required_edge} (ask={ask:.3f})"

    # Standard gates
    if ask > SAFETY_CAP:
        return False, f"ask {ask:.3f} > {SAFETY_CAP}"
    if spread > SPREAD_MAX:
        return False, f"spread {spread:.3f} > {SPREAD_MAX}"

    return True, ""


def simulate_session(session_path) -> tuple[Optional[Trade], Optional[Trade], str]:
    """
    Simulate session with both V3 and V3.1 rules.
    Returns (v3_trade, v31_trade, winner).
    Each trade is None if that version didn't trade.
    """
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

    v3_trade = None
    v31_trade = None

    for tick in ticks:
        elapsed_secs = get_elapsed_secs(tick)

        # GATE: CORE zone only
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
        # In live code: edge = mid = (bid + ask) / 2 (polymarket_connector.py:498)
        # up_mid/down_mid from tick data represent mid-prices
        if up_mid >= down_mid:
            direction = 'Up'
            edge = up_mid  # edge = mid-price (matches live code)
            side = best.get('Up', {})
        else:
            direction = 'Down'
            edge = down_mid  # edge = mid-price (matches live code)
            side = best.get('Down', {})

        ask = side.get('ask')
        bid = side.get('bid')
        if ask is None or bid is None:
            continue

        spread = ask - bid

        # BAD_BOOK gate
        if spread < 0 or bid > ask:
            continue

        # Check V3 gates (if not already traded this session)
        if v3_trade is None:
            passes, reason = passes_v3_gates(edge, ask, spread)
            if passes:
                won = (direction == winner)
                shares = POSITION_SIZE / ask
                pnl = (1.0 - ask) * shares if won else -POSITION_SIZE
                v3_trade = Trade(
                    session=session_path.name,
                    direction=direction,
                    edge=edge,
                    ask=ask,
                    bid=bid,
                    spread=spread,
                    elapsed_secs=elapsed_secs,
                    won=won,
                    pnl=pnl,
                    version="V3"
                )

        # Check V3.1 gates (if not already traded this session)
        if v31_trade is None:
            passes, reason = passes_v31_gates(edge, ask, spread)
            if passes:
                won = (direction == winner)
                shares = POSITION_SIZE / ask
                pnl = (1.0 - ask) * shares if won else -POSITION_SIZE
                v31_trade = Trade(
                    session=session_path.name,
                    direction=direction,
                    edge=edge,
                    ask=ask,
                    bid=bid,
                    spread=spread,
                    elapsed_secs=elapsed_secs,
                    won=won,
                    pnl=pnl,
                    version="V3.1"
                )
            else:
                # Track why V3.1 skipped
                if v3_trade and v31_trade is None:
                    v3_trade.skip_reason = reason

        # Both versions have traded - done with session
        if v3_trade and v31_trade:
            break

    return v3_trade, v31_trade, winner


def run_backtest(markets_dir):
    """Run backtest for both versions."""
    v3_result = Result(version="RULEV3")
    v31_result = Result(version="RULEV3.1")

    sessions = sorted([
        d for d in markets_dir.iterdir()
        if d.is_dir() and d.name.startswith('btc-updown-15m-')
    ])

    v3_result.total_sessions = len(sessions)
    v31_result.total_sessions = len(sessions)

    v3_running_pnl = 0.0
    v3_peak = 0.0
    v31_running_pnl = 0.0
    v31_peak = 0.0

    # Track cases where V3 trades but V3.1 doesn't
    v3_only_trades = []

    for session_path in sessions:
        v3_trade, v31_trade, winner = simulate_session(session_path)

        if v3_trade:
            v3_result.total_trades += 1
            v3_result.trades.append(v3_trade)
            if v3_trade.won:
                v3_result.wins += 1
            else:
                v3_result.losses += 1
            v3_result.total_pnl += v3_trade.pnl
            v3_result.sum_ask += v3_trade.ask
            v3_result.sum_spread += v3_trade.spread
            v3_running_pnl += v3_trade.pnl
            if v3_running_pnl > v3_peak:
                v3_peak = v3_running_pnl
            dd = v3_peak - v3_running_pnl
            if dd > v3_result.max_drawdown:
                v3_result.max_drawdown = dd

            # Track if V3.1 skipped this one
            if v31_trade is None and v3_trade.skip_reason:
                if "VALUE_GATE" in v3_trade.skip_reason:
                    v31_result.skips_value_gate += 1
                elif "DYNAMIC_EDGE" in v3_trade.skip_reason:
                    v31_result.skips_dynamic_edge += 1
                v3_only_trades.append(v3_trade)

        if v31_trade:
            v31_result.total_trades += 1
            v31_result.trades.append(v31_trade)
            if v31_trade.won:
                v31_result.wins += 1
            else:
                v31_result.losses += 1
            v31_result.total_pnl += v31_trade.pnl
            v31_result.sum_ask += v31_trade.ask
            v31_result.sum_spread += v31_trade.spread
            v31_running_pnl += v31_trade.pnl
            if v31_running_pnl > v31_peak:
                v31_peak = v31_running_pnl
            dd = v31_peak - v31_running_pnl
            if dd > v31_result.max_drawdown:
                v31_result.max_drawdown = dd

    return v3_result, v31_result, v3_only_trades


def safe_div(a, b):
    return a / b if b > 0 else 0


def analyze_ask_distribution(trades):
    """Analyze trades by ask price buckets."""
    buckets = {
        "<=0.64": {"trades": 0, "wins": 0, "pnl": 0},
        "0.65-0.66": {"trades": 0, "wins": 0, "pnl": 0},
        "0.67-0.68": {"trades": 0, "wins": 0, "pnl": 0},
        "0.69-0.70": {"trades": 0, "wins": 0, "pnl": 0},
        ">0.70": {"trades": 0, "wins": 0, "pnl": 0},
    }

    for t in trades:
        if t.ask <= 0.64:
            bucket = "<=0.64"
        elif t.ask <= 0.66:
            bucket = "0.65-0.66"
        elif t.ask <= 0.68:
            bucket = "0.67-0.68"
        elif t.ask <= 0.70:
            bucket = "0.69-0.70"
        else:
            bucket = ">0.70"

        buckets[bucket]["trades"] += 1
        if t.won:
            buckets[bucket]["wins"] += 1
        buckets[bucket]["pnl"] += t.pnl

    return buckets


def print_results(v3, v31, v3_only):
    """Print comparison results."""
    print()
    print("=" * 70)
    print("  RULEV3 vs RULEV3.1 BACKTEST COMPARISON")
    print("=" * 70)
    print()
    print("  RULEV3 (original):  edge >= 0.64 (fixed)")
    print("  RULEV3.1 (new):     VALUE_GATE + DYNAMIC_EDGE")
    print()
    print("-" * 70)
    print(f"  {'Metric':<30} {'RULEV3':>15} {'RULEV3.1':>15}")
    print("-" * 70)
    print(f"  {'Sessions analyzed':<30} {v3.total_sessions:>15}")
    print(f"  {'Total trades':<30} {v3.total_trades:>15} {v31.total_trades:>15}")
    print(f"  {'Trade reduction':<30} {'':<15} {((v3.total_trades - v31.total_trades) / v3.total_trades * 100) if v3.total_trades else 0:>14.1f}%")
    print(f"  {'Wins':<30} {v3.wins:>15} {v31.wins:>15}")
    print(f"  {'Losses':<30} {v3.losses:>15} {v31.losses:>15}")
    print(f"  {'Win rate (%)':<30} {safe_div(v3.wins * 100, v3.total_trades):>15.2f} {safe_div(v31.wins * 100, v31.total_trades):>15.2f}")
    print(f"  {'Total PnL ($)':<30} {v3.total_pnl:>15.2f} {v31.total_pnl:>15.2f}")
    print(f"  {'PnL improvement ($)':<30} {'':<15} {v31.total_pnl - v3.total_pnl:>+15.2f}")
    print(f"  {'Avg PnL per trade ($)':<30} {safe_div(v3.total_pnl, v3.total_trades):>15.4f} {safe_div(v31.total_pnl, v31.total_trades):>15.4f}")
    print(f"  {'Max drawdown ($)':<30} {v3.max_drawdown:>15.2f} {v31.max_drawdown:>15.2f}")
    print(f"  {'Avg ask at entry':<30} {safe_div(v3.sum_ask, v3.total_trades):>15.4f} {safe_div(v31.sum_ask, v31.total_trades):>15.4f}")
    print(f"  {'Avg spread at entry':<30} {safe_div(v3.sum_spread, v3.total_trades):>15.4f} {safe_div(v31.sum_spread, v31.total_trades):>15.4f}")
    print("-" * 70)

    # V3 trade distribution by ask price
    print()
    print("  RULEV3 TRADES BY ASK PRICE:")
    buckets = analyze_ask_distribution(v3.trades)
    print(f"    {'Ask Range':<15} {'Trades':>8} {'WinRate':>10} {'PnL':>12}")
    print(f"    {'-'*45}")
    for bucket, data in buckets.items():
        if data["trades"] > 0:
            wr = data["wins"] * 100 / data["trades"]
            print(f"    {bucket:<15} {data['trades']:>8} {wr:>9.1f}% ${data['pnl']:>10.2f}")

    # V3.1 skip breakdown
    print()
    print("  RULEV3.1 SKIP ANALYSIS (trades V3 took but V3.1 skipped):")
    print(f"    VALUE_GATE skips:        {v31.skips_value_gate}")
    print(f"    DYNAMIC_EDGE skips:      {v31.skips_dynamic_edge}")
    total_skips = v31.skips_value_gate + v31.skips_dynamic_edge
    print(f"    Total V3-only trades:    {total_skips}")

    # Analyze V3-only trades (were they wins or losses?)
    if v3_only:
        v3_only_wins = sum(1 for t in v3_only if t.won)
        v3_only_losses = sum(1 for t in v3_only if not t.won)
        v3_only_pnl = sum(t.pnl for t in v3_only)
        print()
        print("  TRADES RULEV3.1 CORRECTLY SKIPPED:")
        print(f"    Wins avoided:       {v3_only_wins}")
        print(f"    Losses avoided:     {v3_only_losses}")
        print(f"    PnL of skipped:     ${v3_only_pnl:+.2f}")
        if v3_only_pnl < 0:
            print(f"    -> V3.1 avoided ${abs(v3_only_pnl):.2f} in losses!")
        else:
            print(f"    -> V3.1 missed ${v3_only_pnl:.2f} in profits")

    # Verdict
    print()
    print("=" * 70)
    print("  VERDICT")
    print("=" * 70)

    pnl_diff = v31.total_pnl - v3.total_pnl
    wr_diff = safe_div(v31.wins * 100, v31.total_trades) - safe_div(v3.wins * 100, v3.total_trades)
    trade_reduction = (v3.total_trades - v31.total_trades) / v3.total_trades * 100 if v3.total_trades else 0
    dd_improvement = v3.max_drawdown - v31.max_drawdown

    if pnl_diff > 0 and wr_diff >= 0:
        print(f"  [OK] RULEV3.1 WINS")
        print(f"     +${pnl_diff:.2f} PnL improvement")
        print(f"     +{wr_diff:.2f}% win rate improvement")
        print(f"     {trade_reduction:.1f}% fewer trades (as expected)")
        print(f"     ${dd_improvement:.2f} less max drawdown")
    elif pnl_diff > 0:
        print(f"  [MIXED] RULEV3.1 BETTER PNL BUT LOWER WIN RATE")
        print(f"     +${pnl_diff:.2f} PnL improvement")
        print(f"     {wr_diff:.2f}% win rate change")
    elif pnl_diff < 0:
        print(f"  [WORSE] RULEV3.1 UNDERPERFORMS")
        print(f"     ${pnl_diff:.2f} PnL degradation")
        print(f"     May need parameter tuning")
    else:
        print(f"  [--] NO SIGNIFICANT DIFFERENCE")

    print()
    print("=" * 70)


def main():
    markets_dir = Path(__file__).parent.parent / 'markets_paper'

    if not markets_dir.exists():
        print(f"ERROR: markets_paper directory not found at {markets_dir}")
        print("Please ensure you have backtest data in markets_paper/")
        return

    print()
    print("=" * 70)
    print("  LOADING DATA...")
    print("=" * 70)

    sessions = list(markets_dir.glob('btc-updown-15m-*'))
    print(f"  Found {len(sessions)} BTC sessions")

    if len(sessions) == 0:
        print("  ERROR: No sessions found!")
        return

    print("  Running backtest...")
    print()

    v3_result, v31_result, v3_only = run_backtest(markets_dir)

    print_results(v3_result, v31_result, v3_only)

    # Save results to file
    log_dir = Path(__file__).parent.parent / 'backtest_full_logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = log_dir / f'v31_comparison_{timestamp}.log'

    with open(log_file, 'w') as f:
        f.write(f"RULEV3 vs RULEV3.1 Comparison\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n")
        f.write(f"Sessions: {v3_result.total_sessions}\n\n")

        f.write(f"RULEV3:\n")
        f.write(f"  Trades: {v3_result.total_trades}\n")
        f.write(f"  Wins: {v3_result.wins}\n")
        f.write(f"  Losses: {v3_result.losses}\n")
        f.write(f"  Win Rate: {safe_div(v3_result.wins * 100, v3_result.total_trades):.2f}%\n")
        f.write(f"  Total PnL: ${v3_result.total_pnl:.2f}\n")
        f.write(f"  Avg PnL: ${safe_div(v3_result.total_pnl, v3_result.total_trades):.4f}\n")
        f.write(f"  Max DD: ${v3_result.max_drawdown:.2f}\n\n")

        f.write(f"RULEV3.1:\n")
        f.write(f"  Trades: {v31_result.total_trades}\n")
        f.write(f"  Wins: {v31_result.wins}\n")
        f.write(f"  Losses: {v31_result.losses}\n")
        f.write(f"  Win Rate: {safe_div(v31_result.wins * 100, v31_result.total_trades):.2f}%\n")
        f.write(f"  Total PnL: ${v31_result.total_pnl:.2f}\n")
        f.write(f"  Avg PnL: ${safe_div(v31_result.total_pnl, v31_result.total_trades):.4f}\n")
        f.write(f"  Max DD: ${v31_result.max_drawdown:.2f}\n\n")

        f.write(f"V3.1 Skip Analysis:\n")
        f.write(f"  VALUE_GATE skips: {v31_result.skips_value_gate}\n")
        f.write(f"  DYNAMIC_EDGE skips: {v31_result.skips_dynamic_edge}\n")

        if v3_only:
            v3_only_wins = sum(1 for t in v3_only if t.won)
            v3_only_losses = sum(1 for t in v3_only if not t.won)
            v3_only_pnl = sum(t.pnl for t in v3_only)
            f.write(f"\nSkipped Trades Analysis:\n")
            f.write(f"  Wins avoided: {v3_only_wins}\n")
            f.write(f"  Losses avoided: {v3_only_losses}\n")
            f.write(f"  PnL of skipped: ${v3_only_pnl:+.2f}\n")

    print(f"  Log saved to: {log_file}")


if __name__ == '__main__':
    main()
