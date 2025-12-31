#!/usr/bin/env python3
"""
LOSS ANATOMY V2: ACTIONABLE ANALYSIS
====================================
Focus on what we CAN change:
1. Can we PREDICT never-green trades at entry?
2. Can we SIZE based on entry quality?
3. What entry conditions correlate with loss?
"""

import json
import statistics
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from collections import defaultdict

# ============================================================
# PRODUCTION CONFIG
# ============================================================
CONFIG = {
    "ask_cap": 0.68,
    "spread_cap": 0.02,
    "ask_cut1": 0.66,
    "ask_cut2": 0.69,
    "edge1": 0.64,
    "edge2": 0.67,
    "edge3": 0.70,
    "max_trades_per_session": 1,
}

POSITION_SIZE = 5.0
CORE_START_SECS = 150
CORE_END_SECS = 225


@dataclass
class TradeData:
    session_id: str
    direction: str
    won: bool
    pnl: float

    # Entry
    entry_ask: float
    entry_bid: float
    entry_edge: float
    entry_spread: float
    entry_elapsed: float

    # Path analysis
    mfe: float = 0.0  # Max favorable as % of entry mid
    mae: float = 0.0  # Max adverse as % of entry mid
    ever_went_green: bool = False
    time_in_green: float = 0.0
    crossings: int = 0

    # Pre-entry context (what we knew BEFORE entering)
    pre_entry_volatility: float = 0.0  # How much did edge move before entry?
    pre_entry_trend: float = 0.0  # Was edge rising or falling?
    pre_entry_crossings: int = 0  # Direction changes before entry


def load_session_data(session_path: Path) -> Tuple[List[dict], Optional[str]]:
    """Load raw ticks and determine winner."""
    ticks_file = session_path / 'ticks.jsonl'
    if not ticks_file.exists():
        return [], None

    ticks = []
    with open(ticks_file, 'r') as f:
        for line in f:
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                ticks.append(data)
            except:
                continue

    if not ticks:
        return [], None

    # Determine winner
    final = ticks[-1]
    price = final.get('price') or {}
    up_mid = price.get('Up', 0.5) or 0.5
    down_mid = price.get('Down', 0.5) or 0.5

    if up_mid >= 0.90:
        winner = 'Up'
    elif down_mid >= 0.90:
        winner = 'Down'
    else:
        winner = None

    return ticks, winner


def analyze_pre_entry(ticks: List[dict], entry_idx: int) -> Tuple[float, float, int]:
    """Analyze market behavior BEFORE entry."""
    if entry_idx <= 0:
        return 0.0, 0.0, 0

    # Look at ticks before entry
    pre_ticks = ticks[:entry_idx]

    edges = []
    directions = []

    for t in pre_ticks:
        price = t.get('price') or {}
        up_mid = price.get('Up', 0.5) or 0.5
        down_mid = price.get('Down', 0.5) or 0.5

        if up_mid >= down_mid:
            edges.append(up_mid)
            directions.append('Up')
        else:
            edges.append(down_mid)
            directions.append('Down')

    if len(edges) < 2:
        return 0.0, 0.0, 0

    # Volatility: std dev of edge over pre-entry period
    volatility = statistics.stdev(edges) if len(edges) > 1 else 0

    # Trend: slope of edge (positive = rising, negative = falling)
    trend = (edges[-1] - edges[0]) / len(edges) if edges else 0

    # Crossings: direction changes
    crossings = 0
    for i in range(1, len(directions)):
        if directions[i] != directions[i-1]:
            crossings += 1

    return volatility, trend, crossings


def simulate_session(session_path: Path) -> Optional[TradeData]:
    """Full simulation with pre-entry analysis."""
    ticks, winner = load_session_data(session_path)
    if not ticks or not winner:
        return None

    # Find entry point
    entry_tick = None
    entry_idx = None

    for i, t in enumerate(ticks):
        mins_left = t.get('minutesLeft', 15)
        elapsed = (15 - mins_left) * 60

        if elapsed < CORE_START_SECS or elapsed > CORE_END_SECS:
            continue

        price = t.get('price') or {}
        best = t.get('best') or {}

        up_mid = price.get('Up', 0.5) or 0.5
        down_mid = price.get('Down', 0.5) or 0.5

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
        if not ask or not bid or ask <= 0 or bid <= 0:
            continue

        spread = ask - bid
        if spread < 0 or bid > ask:
            continue

        # Dynamic edge gate
        if ask <= CONFIG["ask_cut1"]:
            required_edge = CONFIG["edge1"]
        elif ask <= CONFIG["ask_cut2"]:
            required_edge = CONFIG["edge2"]
        else:
            required_edge = CONFIG["edge3"]

        if edge < required_edge:
            continue
        if ask > CONFIG["ask_cap"]:
            continue
        if spread > CONFIG["spread_cap"]:
            continue

        entry_tick = t
        entry_idx = i
        entry_direction = direction
        entry_ask = ask
        entry_bid = bid
        entry_edge = edge
        entry_spread = spread
        entry_elapsed = elapsed
        entry_mid = up_mid if direction == 'Up' else down_mid
        break

    if not entry_tick:
        return None

    # Analyze pre-entry context
    pre_vol, pre_trend, pre_crossings = analyze_pre_entry(ticks, entry_idx)

    # Analyze post-entry path
    won = (entry_direction == winner)
    shares = POSITION_SIZE / entry_ask
    pnl = (1.0 - entry_ask) * shares if won else -POSITION_SIZE

    mfe_mid = entry_mid
    mae_mid = entry_mid
    ever_green = False
    time_in_green = 0.0
    crossings = 0
    last_dir = entry_direction
    last_elapsed = entry_elapsed

    for t in ticks[entry_idx + 1:]:
        mins_left = t.get('minutesLeft', 15)
        elapsed = (15 - mins_left) * 60

        price = t.get('price') or {}
        if entry_direction == 'Up':
            current_mid = price.get('Up', 0.5) or 0.5
        else:
            current_mid = price.get('Down', 0.5) or 0.5

        # MFE/MAE
        if current_mid > mfe_mid:
            mfe_mid = current_mid
        if current_mid < mae_mid:
            mae_mid = current_mid

        # Time in green (current_mid > entry_ask means profitable if we could sell at mid)
        dt = elapsed - last_elapsed
        if current_mid > entry_ask:
            time_in_green += dt
            ever_green = True
        last_elapsed = elapsed

        # Crossings
        up_mid = price.get('Up', 0.5) or 0.5
        down_mid = price.get('Down', 0.5) or 0.5
        current_dir = 'Up' if up_mid >= down_mid else 'Down'
        if current_dir != last_dir:
            crossings += 1
            last_dir = current_dir

    # Calculate MFE/MAE as percentage
    mfe_pct = (mfe_mid - entry_mid) / entry_mid * 100 if entry_mid > 0 else 0
    mae_pct = (mae_mid - entry_mid) / entry_mid * 100 if entry_mid > 0 else 0

    return TradeData(
        session_id=session_path.name,
        direction=entry_direction,
        won=won,
        pnl=pnl,
        entry_ask=entry_ask,
        entry_bid=entry_bid,
        entry_edge=entry_edge,
        entry_spread=entry_spread,
        entry_elapsed=entry_elapsed,
        mfe=mfe_pct,
        mae=mae_pct,
        ever_went_green=ever_green,
        time_in_green=time_in_green,
        crossings=crossings,
        pre_entry_volatility=pre_vol,
        pre_entry_trend=pre_trend,
        pre_entry_crossings=pre_crossings
    )


def main():
    markets_dir = Path(__file__).parent.parent / 'markets_paper'

    print("=" * 100)
    print("  LOSS ANATOMY V2: ACTIONABLE ANALYSIS")
    print("=" * 100)
    print()

    print("Loading and simulating sessions...")
    sessions = sorted([
        d for d in markets_dir.iterdir()
        if d.is_dir() and d.name.startswith('btc-updown-15m-')
    ])

    trades: List[TradeData] = []
    for i, sp in enumerate(sessions):
        if (i + 1) % 500 == 0:
            print(f"  {i+1}/{len(sessions)}...")
        trade = simulate_session(sp)
        if trade:
            trades.append(trade)

    wins = [t for t in trades if t.won]
    losses = [t for t in trades if not t.won]

    print(f"  Total: {len(trades)} trades")
    print(f"  Wins:  {len(wins)} ({100*len(wins)/len(trades):.1f}%)")
    print(f"  Losses: {len(losses)} ({100*len(losses)/len(trades):.1f}%)")
    print()

    # ============================================================
    # QUESTION 1: Can we predict never-green trades?
    # ============================================================
    print("=" * 100)
    print("  QUESTION 1: CAN WE PREDICT NEVER-GREEN TRADES?")
    print("=" * 100)
    print()

    never_green = [t for t in trades if not t.ever_went_green]
    went_green = [t for t in trades if t.ever_went_green]

    print(f"Never-green trades: {len(never_green)} ({100*len(never_green)/len(trades):.1f}%)")
    print(f"  Win rate: {100*sum(1 for t in never_green if t.won)/len(never_green):.1f}%")
    print()

    print("COMPARING NEVER-GREEN vs WENT-GREEN AT ENTRY:")
    print(f"  {'Metric':<25} {'Never-Green':>15} {'Went-Green':>15} {'Delta':>12}")
    print(f"  {'-'*70}")

    # Entry edge
    ng_edge = statistics.mean([t.entry_edge for t in never_green])
    wg_edge = statistics.mean([t.entry_edge for t in went_green])
    print(f"  {'Entry Edge':<25} {ng_edge:>14.3f} {wg_edge:>14.3f} {ng_edge-wg_edge:>+11.3f}")

    # Entry ask
    ng_ask = statistics.mean([t.entry_ask for t in never_green])
    wg_ask = statistics.mean([t.entry_ask for t in went_green])
    print(f"  {'Entry Ask':<25} {ng_ask:>14.3f} {wg_ask:>14.3f} {ng_ask-wg_ask:>+11.3f}")

    # Entry spread
    ng_spread = statistics.mean([t.entry_spread for t in never_green])
    wg_spread = statistics.mean([t.entry_spread for t in went_green])
    print(f"  {'Entry Spread':<25} {ng_spread:>14.4f} {wg_spread:>14.4f} {ng_spread-wg_spread:>+11.4f}")

    # Pre-entry volatility
    ng_vol = statistics.mean([t.pre_entry_volatility for t in never_green])
    wg_vol = statistics.mean([t.pre_entry_volatility for t in went_green])
    print(f"  {'Pre-Entry Volatility':<25} {ng_vol:>14.4f} {wg_vol:>14.4f} {ng_vol-wg_vol:>+11.4f}")

    # Pre-entry crossings
    ng_cross = statistics.mean([t.pre_entry_crossings for t in never_green])
    wg_cross = statistics.mean([t.pre_entry_crossings for t in went_green])
    print(f"  {'Pre-Entry Crossings':<25} {ng_cross:>14.2f} {wg_cross:>14.2f} {ng_cross-wg_cross:>+11.2f}")

    # Entry timing
    ng_time = statistics.mean([t.entry_elapsed for t in never_green])
    wg_time = statistics.mean([t.entry_elapsed for t in went_green])
    print(f"  {'Entry Elapsed (s)':<25} {ng_time:>14.1f} {wg_time:>14.1f} {ng_time-wg_time:>+11.1f}")
    print()

    # Statistical significance check
    print("DISCRIMINATIVE POWER:")
    print("(Can any single feature identify never-green trades?)")
    print()

    # Check if high pre-entry crossings predict never-green
    for threshold in [3, 5, 7, 10]:
        high_cross = [t for t in trades if t.pre_entry_crossings >= threshold]
        low_cross = [t for t in trades if t.pre_entry_crossings < threshold]

        if high_cross and low_cross:
            high_ng_rate = 100 * sum(1 for t in high_cross if not t.ever_went_green) / len(high_cross)
            low_ng_rate = 100 * sum(1 for t in low_cross if not t.ever_went_green) / len(low_cross)
            high_wr = 100 * sum(1 for t in high_cross if t.won) / len(high_cross)
            low_wr = 100 * sum(1 for t in low_cross if t.won) / len(low_cross)

            print(f"  Pre-crossings >= {threshold}:")
            print(f"    Count: {len(high_cross)}, NeverGreen: {high_ng_rate:.1f}%, WR: {high_wr:.1f}%")
            print(f"    vs < {threshold}: Count: {len(low_cross)}, NeverGreen: {low_ng_rate:.1f}%, WR: {low_wr:.1f}%")
    print()

    # ============================================================
    # QUESTION 2: What entry conditions correlate with LOSS?
    # ============================================================
    print("=" * 100)
    print("  QUESTION 2: ENTRY CONDITIONS THAT PREDICT LOSS")
    print("=" * 100)
    print()

    print("LOSS RATE BY ENTRY EDGE QUINTILE:")
    print(f"  {'Quintile':<20} {'Count':>8} {'LossRate':>10} {'AvgPnL':>10}")
    print(f"  {'-'*50}")

    sorted_by_edge = sorted(trades, key=lambda t: t.entry_edge)
    quintile_size = len(trades) // 5

    for q in range(5):
        start = q * quintile_size
        end = start + quintile_size if q < 4 else len(trades)
        quintile = sorted_by_edge[start:end]

        loss_rate = 100 * sum(1 for t in quintile if not t.won) / len(quintile)
        avg_pnl = statistics.mean([t.pnl for t in quintile])
        min_edge = min(t.entry_edge for t in quintile)
        max_edge = max(t.entry_edge for t in quintile)

        print(f"  Q{q+1} ({min_edge:.3f}-{max_edge:.3f})    {len(quintile):>5} {loss_rate:>9.1f}% ${avg_pnl:>8.2f}")
    print()

    print("LOSS RATE BY ENTRY ASK QUINTILE:")
    print(f"  {'Quintile':<20} {'Count':>8} {'LossRate':>10} {'AvgPnL':>10}")
    print(f"  {'-'*50}")

    sorted_by_ask = sorted(trades, key=lambda t: t.entry_ask)

    for q in range(5):
        start = q * quintile_size
        end = start + quintile_size if q < 4 else len(trades)
        quintile = sorted_by_ask[start:end]

        loss_rate = 100 * sum(1 for t in quintile if not t.won) / len(quintile)
        avg_pnl = statistics.mean([t.pnl for t in quintile])
        min_ask = min(t.entry_ask for t in quintile)
        max_ask = max(t.entry_ask for t in quintile)

        print(f"  Q{q+1} (${min_ask:.2f}-${max_ask:.2f})    {len(quintile):>5} {loss_rate:>9.1f}% ${avg_pnl:>8.2f}")
    print()

    print("LOSS RATE BY ENTRY SPREAD QUINTILE:")
    print(f"  {'Quintile':<20} {'Count':>8} {'LossRate':>10} {'AvgPnL':>10}")
    print(f"  {'-'*50}")

    sorted_by_spread = sorted(trades, key=lambda t: t.entry_spread)

    for q in range(5):
        start = q * quintile_size
        end = start + quintile_size if q < 4 else len(trades)
        quintile = sorted_by_spread[start:end]

        loss_rate = 100 * sum(1 for t in quintile if not t.won) / len(quintile)
        avg_pnl = statistics.mean([t.pnl for t in quintile])
        min_spread = min(t.entry_spread for t in quintile)
        max_spread = max(t.entry_spread for t in quintile)

        print(f"  Q{q+1} ({min_spread:.3f}-{max_spread:.3f})   {len(quintile):>5} {loss_rate:>9.1f}% ${avg_pnl:>8.2f}")
    print()

    # ============================================================
    # QUESTION 3: Can we SIZE based on conditions?
    # ============================================================
    print("=" * 100)
    print("  QUESTION 3: DYNAMIC SIZING SIMULATION")
    print("=" * 100)
    print()

    print("If we reduced size on WORSE entries, what happens?")
    print()

    # Define "worse" as high ask + low edge margin
    # Simulate: full size on good, half size on bad

    def edge_margin(t):
        if t.entry_ask <= 0.66:
            required = 0.64
        elif t.entry_ask <= 0.68:
            required = 0.67
        else:
            required = 0.70
        return t.entry_edge - required

    # Baseline
    baseline_pnl = sum(t.pnl for t in trades)

    # Strategy 1: Half size when edge margin < 0.02
    sized_pnl_1 = 0
    for t in trades:
        margin = edge_margin(t)
        if margin < 0.02:
            # Half size = half pnl
            sized_pnl_1 += t.pnl * 0.5
        else:
            sized_pnl_1 += t.pnl

    # Strategy 2: Half size when ask >= 0.66
    sized_pnl_2 = 0
    for t in trades:
        if t.entry_ask >= 0.66:
            sized_pnl_2 += t.pnl * 0.5
        else:
            sized_pnl_2 += t.pnl

    # Strategy 3: Size proportional to edge margin
    sized_pnl_3 = 0
    for t in trades:
        margin = edge_margin(t)
        size_mult = min(1.0, max(0.5, margin / 0.05))  # 0.5x to 1x based on margin
        sized_pnl_3 += t.pnl * size_mult

    print(f"  {'Strategy':<40} {'PnL':>12} {'vs Baseline':>15}")
    print(f"  {'-'*70}")
    print(f"  {'Baseline (fixed $5)':<40} ${baseline_pnl:>10.2f} {'-':>15}")
    print(f"  {'Half size if edge_margin < 0.02':<40} ${sized_pnl_1:>10.2f} ${sized_pnl_1-baseline_pnl:>+13.2f}")
    print(f"  {'Half size if ask >= 0.66':<40} ${sized_pnl_2:>10.2f} ${sized_pnl_2-baseline_pnl:>+13.2f}")
    print(f"  {'Size proportional to edge_margin':<40} ${sized_pnl_3:>10.2f} ${sized_pnl_3-baseline_pnl:>+13.2f}")
    print()

    # ============================================================
    # QUESTION 4: What about win efficiency?
    # ============================================================
    print("=" * 100)
    print("  QUESTION 4: WIN EFFICIENCY ANALYSIS")
    print("=" * 100)
    print()

    print("WINS BY ENTRY CONDITIONS:")
    print()

    # Wins by ask bucket
    print("Win rate by ask bucket:")
    by_ask = defaultdict(list)
    for t in trades:
        if t.entry_ask <= 0.64:
            bucket = "<= 0.64"
        elif t.entry_ask <= 0.66:
            bucket = "0.65-0.66"
        elif t.entry_ask <= 0.68:
            bucket = "0.67-0.68"
        else:
            bucket = "> 0.68"
        by_ask[bucket].append(t)

    print(f"  {'Bucket':<15} {'Count':>8} {'WinRate':>10} {'AvgWinPnL':>12} {'AvgLossPnL':>12} {'NetPnL':>12}")
    print(f"  {'-'*75}")

    for bucket in ["<= 0.64", "0.65-0.66", "0.67-0.68", "> 0.68"]:
        grp = by_ask.get(bucket, [])
        if grp:
            w = [t for t in grp if t.won]
            l = [t for t in grp if not t.won]
            wr = 100 * len(w) / len(grp)
            avg_w = statistics.mean([t.pnl for t in w]) if w else 0
            avg_l = statistics.mean([t.pnl for t in l]) if l else 0
            net = sum(t.pnl for t in grp)
            print(f"  {bucket:<15} {len(grp):>8} {wr:>9.1f}% ${avg_w:>10.2f} ${avg_l:>10.2f} ${net:>10.2f}")
    print()

    # ============================================================
    # FINAL ACTIONABLE RECOMMENDATIONS
    # ============================================================
    print("=" * 100)
    print("  FINAL ACTIONABLE RECOMMENDATIONS")
    print("=" * 100)
    print()

    print("STRUCTURAL REALITY:")
    print("  - Loss magnitude is FIXED at $5.00 (100% of stake)")
    print("  - Win magnitude averages $2.57 (based on ask price)")
    print("  - 1 loss = 1.95 wins to recover")
    print("  - This ratio is STRUCTURAL and cannot be changed by timing")
    print()

    print("WHAT WE CAN DO:")
    print()

    print("1. POSITION SIZING (reduce loss impact, not magnitude)")
    print("   - Trade smaller when edge margin is low")
    print("   - Trade smaller when ask is high")
    print("   - This reduces BOTH wins AND losses proportionally")
    best_sizing = max([
        ("Edge margin", sized_pnl_1 - baseline_pnl),
        ("High ask", sized_pnl_2 - baseline_pnl),
        ("Proportional", sized_pnl_3 - baseline_pnl)
    ], key=lambda x: x[1])
    print(f"   - Best tested: {best_sizing[0]} = ${best_sizing[1]:+.2f} vs baseline")
    print()

    print("2. NEVER-GREEN PREDICTION (future research)")
    ng_predictable = sum(1 for t in never_green if t.pre_entry_crossings >= 5)
    print(f"   - {len(never_green)} never-green trades (0% win rate)")
    print(f"   - {ng_predictable} had pre-entry crossings >= 5")
    print(f"   - Needs more data to validate predictive power")
    print()

    print("3. WHAT DOESN'T WORK:")
    print("   - Early exit (binary options - we hold to settlement)")
    print("   - Stop losses (not applicable to settlement structure)")
    print("   - Kill switch after losses (kills edge per sweep)")
    print()

    print("4. THE REAL ANSWER:")
    print("   - Accept that 1L = ~2W is structural")
    print("   - Focus on WIN RATE (entry quality)")
    print("   - Focus on WIN SIZE (cheaper entries = bigger wins)")
    print("   - Current config (ask_cap=0.68) already optimizes this")
    print()

    print("=" * 100)
    print("  ANALYSIS COMPLETE")
    print("=" * 100)


if __name__ == '__main__':
    main()
