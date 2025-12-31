#!/usr/bin/env python3
"""
RULEV3.1 vs RULEV3.2 Comparison Backtest
=========================================
Compares V3.1 (dynamic edge only) against V3.2 (dynamic edge + regime modifier).

RULEV3.1:
  - DYNAMIC_EDGE:
    - ask <= 0.66 -> edge >= 0.64
    - ask <= 0.69 -> edge >= 0.67
    - else        -> edge >= 0.70

RULEV3.2 (new):
  - Same DYNAMIC_EDGE base
  - REGIME MODIFIER: if CHOPPY (crossings >= 6), add +0.03 to required edge

CROSSINGS = direction reversals in 5min window (price moved >= 0.1% then reversed)
"""

import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from datetime import datetime
from collections import deque

# ============================================================
# SHARED CONFIG
# ============================================================
SAFETY_CAP = 0.72
SPREAD_MAX = 0.02
POSITION_SIZE = 5.0

# CORE zone: 2:30 - 3:45 (150s - 225s elapsed)
CORE_START_SECS = 150
CORE_END_SECS = 225

# Regime detection parameters (matching btc_trend_tracker.py)
WINDOW_SECONDS = 300  # 5 minutes
MOVE_THRESHOLD = 0.001  # 0.1% move threshold for crossing
CHOPPY_THRESHOLD = 6  # crossings >= 6 = CHOPPY
STABLE_THRESHOLD = 2  # crossings <= 2 = STABLE
REGIME_MODIFIER = 0.03  # Add to edge gate when CHOPPY


@dataclass
class PricePoint:
    """A single price observation for regime detection."""
    timestamp: float
    price: float


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
    version: str
    regime: str = "NEUTRAL"
    crossings: int = 0
    skip_reason: str = ""


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
    # Regime breakdown
    trades_stable: int = 0
    trades_neutral: int = 0
    trades_choppy: int = 0
    wins_stable: int = 0
    wins_neutral: int = 0
    wins_choppy: int = 0
    pnl_stable: float = 0.0
    pnl_neutral: float = 0.0
    pnl_choppy: float = 0.0
    # Skip tracking
    skips_regime: int = 0


def get_elapsed_secs(tick) -> float:
    """Convert minutesLeft to elapsed seconds."""
    mins_left = tick.get('minutesLeft', 15)
    return (15 - mins_left) * 60


def get_winner(ticks) -> Optional[str]:
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


def compute_crossings(price_buffer: List[PricePoint], current_time: float) -> int:
    """
    Count direction reversals in the last 5 minutes.
    Matches btc_trend_tracker.py logic.
    """
    if len(price_buffer) < 10:
        return 0

    window_start = current_time - WINDOW_SECONDS
    window_points = [p for p in price_buffer if p.timestamp >= window_start]

    if len(window_points) < 10:
        return 0

    last_direction = None
    last_anchor = window_points[0].price
    crossings = 0

    for point in window_points[1:]:
        move = point.price - last_anchor

        if abs(move) >= MOVE_THRESHOLD:
            current_direction = "UP" if move > 0 else "DOWN"

            if last_direction is not None and current_direction != last_direction:
                crossings += 1

            last_direction = current_direction
            last_anchor = point.price

    return crossings


def get_regime(crossings: int) -> str:
    """Classify regime based on crossings."""
    if crossings >= CHOPPY_THRESHOLD:
        return "CHOPPY"
    elif crossings <= STABLE_THRESHOLD:
        return "STABLE"
    else:
        return "NEUTRAL"


def get_dynamic_edge(ask: float) -> float:
    """Get base required edge from V3.1 dynamic gate."""
    if ask <= 0.66:
        return 0.64
    elif ask <= 0.69:
        return 0.67
    else:
        return 0.70


def passes_v31_gates(edge: float, ask: float, spread: float) -> Tuple[bool, str]:
    """Check RULEV3.1 gates (dynamic edge, no regime modifier)."""
    required_edge = get_dynamic_edge(ask)

    if edge < required_edge:
        return False, f"DYNAMIC_EDGE: edge {edge:.3f} < {required_edge}"
    if ask > SAFETY_CAP:
        return False, f"ask {ask:.3f} > {SAFETY_CAP}"
    if spread > SPREAD_MAX:
        return False, f"spread {spread:.3f} > {SPREAD_MAX}"
    return True, ""


def passes_v32_gates(edge: float, ask: float, spread: float, regime: str, crossings: int) -> Tuple[bool, str]:
    """Check RULEV3.2 gates (dynamic edge + regime modifier)."""
    required_edge = get_dynamic_edge(ask)

    # V3.2: Add regime modifier
    if regime == "CHOPPY":
        required_edge += REGIME_MODIFIER

    if edge < required_edge:
        if regime == "CHOPPY":
            return False, f"EDGE+REGIME: edge {edge:.3f} < {required_edge:.3f} (base+{REGIME_MODIFIER}) crossings={crossings}"
        return False, f"DYNAMIC_EDGE: edge {edge:.3f} < {required_edge}"
    if ask > SAFETY_CAP:
        return False, f"ask {ask:.3f} > {SAFETY_CAP}"
    if spread > SPREAD_MAX:
        return False, f"spread {spread:.3f} > {SPREAD_MAX}"
    return True, ""


def simulate_session(session_path: Path) -> Tuple[Optional[Trade], Optional[Trade], Optional[str]]:
    """
    Simulate session with both V3.1 and V3.2 rules.
    Returns (v31_trade, v32_trade, winner).
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

    v31_trade = None
    v32_trade = None

    # Build price buffer for regime detection (simulating rolling window)
    price_buffer: List[PricePoint] = []
    last_record_time = 0.0

    for tick in ticks:
        elapsed_secs = get_elapsed_secs(tick)

        # Get UP token mid price for regime tracking
        price = tick.get('price')
        if price:
            up_mid = price.get('Up')
            if up_mid is not None and up_mid > 0:
                # Rate limit to ~1 per second (simulate live behavior)
                if elapsed_secs - last_record_time >= 1.0:
                    price_buffer.append(PricePoint(
                        timestamp=elapsed_secs,
                        price=up_mid
                    ))
                    last_record_time = elapsed_secs

        # GATE: CORE zone only
        if elapsed_secs < CORE_START_SECS or elapsed_secs > CORE_END_SECS:
            continue

        if not price:
            continue

        best = tick.get('best')
        if not best:
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

        # BAD_BOOK gate
        if spread < 0 or bid > ask:
            continue

        # Compute regime at this moment
        crossings = compute_crossings(price_buffer, elapsed_secs)
        regime = get_regime(crossings)

        # Check V3.1 gates
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
                    version="V3.1",
                    regime=regime,
                    crossings=crossings
                )

        # Check V3.2 gates
        if v32_trade is None:
            passes, reason = passes_v32_gates(edge, ask, spread, regime, crossings)
            if passes:
                won = (direction == winner)
                shares = POSITION_SIZE / ask
                pnl = (1.0 - ask) * shares if won else -POSITION_SIZE
                v32_trade = Trade(
                    session=session_path.name,
                    direction=direction,
                    edge=edge,
                    ask=ask,
                    bid=bid,
                    spread=spread,
                    elapsed_secs=elapsed_secs,
                    won=won,
                    pnl=pnl,
                    version="V3.2",
                    regime=regime,
                    crossings=crossings
                )
            else:
                # Track V3.1 trade that V3.2 skipped due to regime
                if v31_trade and "REGIME" in reason:
                    v31_trade.skip_reason = reason

        # Both versions have traded
        if v31_trade and v32_trade:
            break

    return v31_trade, v32_trade, winner


def run_backtest(markets_dir: Path, max_sessions: int = 0):
    """Run backtest for both versions."""
    v31_result = Result(version="RULEV3.1")
    v32_result = Result(version="RULEV3.2")

    sessions = sorted([
        d for d in markets_dir.iterdir()
        if d.is_dir() and d.name.startswith('btc-updown-15m-')
    ])

    if max_sessions > 0:
        sessions = sessions[:max_sessions]

    v31_result.total_sessions = len(sessions)
    v32_result.total_sessions = len(sessions)

    v31_running_pnl = 0.0
    v31_peak = 0.0
    v32_running_pnl = 0.0
    v32_peak = 0.0

    # Track trades V3.1 took but V3.2 skipped
    v31_only_trades = []

    for i, session_path in enumerate(sessions):
        if (i + 1) % 500 == 0:
            print(f"  Processing session {i + 1}/{len(sessions)}...")

        v31_trade, v32_trade, winner = simulate_session(session_path)

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

            # Track regime stats
            if v31_trade.regime == "STABLE":
                v31_result.trades_stable += 1
                if v31_trade.won:
                    v31_result.wins_stable += 1
                v31_result.pnl_stable += v31_trade.pnl
            elif v31_trade.regime == "CHOPPY":
                v31_result.trades_choppy += 1
                if v31_trade.won:
                    v31_result.wins_choppy += 1
                v31_result.pnl_choppy += v31_trade.pnl
            else:
                v31_result.trades_neutral += 1
                if v31_trade.won:
                    v31_result.wins_neutral += 1
                v31_result.pnl_neutral += v31_trade.pnl

            # Track if V3.2 skipped
            if v32_trade is None and v31_trade.skip_reason:
                v32_result.skips_regime += 1
                v31_only_trades.append(v31_trade)

        if v32_trade:
            v32_result.total_trades += 1
            v32_result.trades.append(v32_trade)
            if v32_trade.won:
                v32_result.wins += 1
            else:
                v32_result.losses += 1
            v32_result.total_pnl += v32_trade.pnl
            v32_result.sum_ask += v32_trade.ask
            v32_result.sum_spread += v32_trade.spread
            v32_running_pnl += v32_trade.pnl
            if v32_running_pnl > v32_peak:
                v32_peak = v32_running_pnl
            dd = v32_peak - v32_running_pnl
            if dd > v32_result.max_drawdown:
                v32_result.max_drawdown = dd

            # Track regime stats
            if v32_trade.regime == "STABLE":
                v32_result.trades_stable += 1
                if v32_trade.won:
                    v32_result.wins_stable += 1
                v32_result.pnl_stable += v32_trade.pnl
            elif v32_trade.regime == "CHOPPY":
                v32_result.trades_choppy += 1
                if v32_trade.won:
                    v32_result.wins_choppy += 1
                v32_result.pnl_choppy += v32_trade.pnl
            else:
                v32_result.trades_neutral += 1
                if v32_trade.won:
                    v32_result.wins_neutral += 1
                v32_result.pnl_neutral += v32_trade.pnl

    return v31_result, v32_result, v31_only_trades


def safe_div(a, b):
    return a / b if b > 0 else 0


def print_results(v31: Result, v32: Result, v31_only: List[Trade]):
    """Print comparison results."""
    print()
    print("=" * 75)
    print("  RULEV3.1 vs RULEV3.2 BACKTEST COMPARISON")
    print("=" * 75)
    print()
    print("  RULEV3.1: Dynamic edge gate (price-based)")
    print("  RULEV3.2: Dynamic edge + REGIME MODIFIER (+0.03 when CHOPPY)")
    print()
    print("-" * 75)
    print(f"  {'Metric':<35} {'RULEV3.1':>15} {'RULEV3.2':>15}")
    print("-" * 75)
    print(f"  {'Sessions analyzed':<35} {v31.total_sessions:>15}")
    print(f"  {'Total trades':<35} {v31.total_trades:>15} {v32.total_trades:>15}")
    trade_reduction = (v31.total_trades - v32.total_trades) / v31.total_trades * 100 if v31.total_trades else 0
    print(f"  {'Trade reduction':<35} {'':<15} {trade_reduction:>14.1f}%")
    print(f"  {'Wins':<35} {v31.wins:>15} {v32.wins:>15}")
    print(f"  {'Losses':<35} {v31.losses:>15} {v32.losses:>15}")
    print(f"  {'Win rate (%)':<35} {safe_div(v31.wins * 100, v31.total_trades):>15.2f} {safe_div(v32.wins * 100, v32.total_trades):>15.2f}")
    print(f"  {'Total PnL ($)':<35} {v31.total_pnl:>15.2f} {v32.total_pnl:>15.2f}")
    pnl_diff = v32.total_pnl - v31.total_pnl
    print(f"  {'PnL improvement ($)':<35} {'':<15} {pnl_diff:>+15.2f}")
    print(f"  {'Avg PnL per trade ($)':<35} {safe_div(v31.total_pnl, v31.total_trades):>15.4f} {safe_div(v32.total_pnl, v32.total_trades):>15.4f}")
    print(f"  {'Max drawdown ($)':<35} {v31.max_drawdown:>15.2f} {v32.max_drawdown:>15.2f}")
    print("-" * 75)

    # Regime breakdown for V3.1
    print()
    print("  RULEV3.1 TRADES BY REGIME:")
    print(f"    {'Regime':<12} {'Trades':>8} {'Wins':>8} {'WinRate':>10} {'PnL':>12}")
    print(f"    {'-'*50}")

    for regime, trades, wins, pnl in [
        ("STABLE", v31.trades_stable, v31.wins_stable, v31.pnl_stable),
        ("NEUTRAL", v31.trades_neutral, v31.wins_neutral, v31.pnl_neutral),
        ("CHOPPY", v31.trades_choppy, v31.wins_choppy, v31.pnl_choppy),
    ]:
        if trades > 0:
            wr = wins * 100 / trades
            print(f"    {regime:<12} {trades:>8} {wins:>8} {wr:>9.1f}% ${pnl:>10.2f}")

    # Skipped trades analysis
    print()
    print("  V3.2 REGIME SKIPS (trades V3.1 took but V3.2 blocked due to CHOPPY):")
    print(f"    Total skips:         {v32.skips_regime}")

    if v31_only:
        v31_only_wins = sum(1 for t in v31_only if t.won)
        v31_only_losses = sum(1 for t in v31_only if not t.won)
        v31_only_pnl = sum(t.pnl for t in v31_only)
        print(f"    Skipped wins:        {v31_only_wins}")
        print(f"    Skipped losses:      {v31_only_losses}")
        print(f"    Skipped PnL:         ${v31_only_pnl:+.2f}")

        if v31_only_pnl < 0:
            print(f"    -> V3.2 CORRECTLY avoided ${abs(v31_only_pnl):.2f} in losses!")
        else:
            print(f"    -> V3.2 INCORRECTLY skipped ${v31_only_pnl:.2f} in profits")

        # Show breakdown by outcome
        if v31_only_losses > 0:
            avg_loss_avoided = sum(t.pnl for t in v31_only if not t.won) / v31_only_losses
            print(f"    Avg loss avoided:    ${avg_loss_avoided:.2f}")

    # Verdict
    print()
    print("=" * 75)
    print("  VERDICT")
    print("=" * 75)

    wr_31 = safe_div(v31.wins * 100, v31.total_trades)
    wr_32 = safe_div(v32.wins * 100, v32.total_trades)
    wr_diff = wr_32 - wr_31
    dd_improvement = v31.max_drawdown - v32.max_drawdown

    if pnl_diff > 0:
        print(f"  [OK] RULEV3.2 WINS")
        print(f"     +${pnl_diff:.2f} PnL improvement")
        print(f"     {wr_diff:+.2f}% win rate change")
        print(f"     ${dd_improvement:.2f} less max drawdown")
        print(f"     {trade_reduction:.1f}% fewer trades (CHOPPY filtered)")
    elif pnl_diff < 0:
        print(f"  [WORSE] RULEV3.2 UNDERPERFORMS")
        print(f"     ${pnl_diff:.2f} PnL degradation")
        print(f"     The +0.03 modifier may be too aggressive")
        print(f"     Consider tuning CHOPPY_THRESHOLD or REGIME_MODIFIER")
    else:
        print(f"  [--] NO SIGNIFICANT DIFFERENCE")

    # Recommendation
    print()
    if v31.pnl_choppy < 0:
        print(f"  KEY INSIGHT: V3.1 CHOPPY trades have ${v31.pnl_choppy:.2f} PnL")
        print(f"  -> Regime filtering is JUSTIFIED")
    else:
        print(f"  KEY INSIGHT: V3.1 CHOPPY trades have ${v31.pnl_choppy:+.2f} PnL")
        print(f"  -> Regime filtering may be OVERLY AGGRESSIVE")

    print()
    print("=" * 75)


def main():
    import sys

    markets_dir = Path(__file__).parent.parent / 'markets_paper'

    if not markets_dir.exists():
        print(f"ERROR: markets_paper directory not found at {markets_dir}")
        return

    # Parse optional max sessions argument
    max_sessions = 0
    if len(sys.argv) > 1:
        try:
            max_sessions = int(sys.argv[1])
        except:
            pass

    print()
    print("=" * 75)
    print("  LOADING DATA...")
    print("=" * 75)

    sessions = list(markets_dir.glob('btc-updown-15m-*'))
    print(f"  Found {len(sessions)} BTC sessions")

    if max_sessions > 0:
        print(f"  Limiting to first {max_sessions} sessions")

    if len(sessions) == 0:
        print("  ERROR: No sessions found!")
        return

    print("  Running backtest...")
    print()

    v31_result, v32_result, v31_only = run_backtest(markets_dir, max_sessions)

    print_results(v31_result, v32_result, v31_only)

    # Save results
    log_dir = Path(__file__).parent.parent / 'backtest_full_logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = log_dir / f'v32_comparison_{timestamp}.log'

    with open(log_file, 'w') as f:
        f.write(f"RULEV3.1 vs RULEV3.2 Comparison\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n")
        f.write(f"Sessions: {v31_result.total_sessions}\n\n")

        f.write(f"RULEV3.1:\n")
        f.write(f"  Trades: {v31_result.total_trades}\n")
        f.write(f"  Wins: {v31_result.wins}\n")
        f.write(f"  Win Rate: {safe_div(v31_result.wins * 100, v31_result.total_trades):.2f}%\n")
        f.write(f"  Total PnL: ${v31_result.total_pnl:.2f}\n")
        f.write(f"  Max DD: ${v31_result.max_drawdown:.2f}\n\n")

        f.write(f"  By Regime:\n")
        f.write(f"    STABLE:  {v31_result.trades_stable} trades, ${v31_result.pnl_stable:.2f}\n")
        f.write(f"    NEUTRAL: {v31_result.trades_neutral} trades, ${v31_result.pnl_neutral:.2f}\n")
        f.write(f"    CHOPPY:  {v31_result.trades_choppy} trades, ${v31_result.pnl_choppy:.2f}\n\n")

        f.write(f"RULEV3.2:\n")
        f.write(f"  Trades: {v32_result.total_trades}\n")
        f.write(f"  Wins: {v32_result.wins}\n")
        f.write(f"  Win Rate: {safe_div(v32_result.wins * 100, v32_result.total_trades):.2f}%\n")
        f.write(f"  Total PnL: ${v32_result.total_pnl:.2f}\n")
        f.write(f"  Max DD: ${v32_result.max_drawdown:.2f}\n\n")

        f.write(f"V3.2 Regime Skips: {v32_result.skips_regime}\n")

        if v31_only:
            skip_pnl = sum(t.pnl for t in v31_only)
            f.write(f"Skipped Trade PnL: ${skip_pnl:+.2f}\n")

    print(f"  Log saved to: {log_file}")


if __name__ == '__main__':
    main()
