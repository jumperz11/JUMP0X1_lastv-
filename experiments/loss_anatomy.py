#!/usr/bin/env python3
"""
LOSS ANATOMY ANALYSIS
=====================
Comprehensive analysis of losing trades:
- Phase 1: Loss Autopsy (MFE, MAE, crossings, time analysis)
- Phase 2: Entry Quality Score (EQS)
- Phase 3: Loss Shaping Rules Testing
- Phase 4: Payoff Symmetry Analysis
- Phase 5: Failure Mode Check
"""

import json
import statistics
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from collections import defaultdict

# ============================================================
# CURRENT PRODUCTION CONFIG (post-sweep)
# ============================================================
CONFIG = {
    "ask_cap": 0.68,
    "spread_cap": 0.02,
    "ask_cut1": 0.66,
    "ask_cut2": 0.69,
    "edge1": 0.64,
    "edge2": 0.67,
    "edge3": 0.70,  # Not used since ask_cap=0.68
    "max_trades_per_session": 1,
}

POSITION_SIZE = 5.0
CORE_START_SECS = 150  # 2:30
CORE_END_SECS = 225    # 3:45


@dataclass
class TickData:
    """Single tick of market data."""
    elapsed_secs: float
    up_mid: float
    down_mid: float
    up_ask: float
    up_bid: float
    down_ask: float
    down_bid: float
    edge: float
    edge_dir: str


@dataclass
class TradeAnalysis:
    """Comprehensive trade analysis data."""
    session_id: str
    direction: str
    won: bool
    pnl: float

    # Entry conditions
    entry_ask: float
    entry_bid: float
    entry_edge: float
    entry_spread: float
    entry_elapsed: float
    entry_bucket: str  # "<=0.66", "0.67-0.68", ">0.68"

    # Price path analysis
    mfe: float = 0.0  # Max Favorable Excursion (best mid we saw after entry)
    mae: float = 0.0  # Max Adverse Excursion (worst mid we saw after entry)
    mfe_time: float = 0.0  # When MFE occurred
    mae_time: float = 0.0  # When MAE occurred

    # Crossing analysis
    crossings: int = 0  # Direction changes after entry
    ever_went_green: bool = False  # Did trade ever have positive unrealized P&L?
    time_in_green: float = 0.0  # Seconds spent with positive unrealized P&L
    time_in_red: float = 0.0  # Seconds spent with negative unrealized P&L

    # Edge evolution
    edge_at_entry: float = 0.0
    edge_at_peak: float = 0.0
    edge_at_trough: float = 0.0
    edge_at_settlement: float = 0.0

    # Classification
    loss_type: str = ""  # "never_green", "gave_back", "late_flip", "structural"


def load_ticks(session_path: Path) -> List[TickData]:
    """Load and parse ticks from a session."""
    ticks_file = session_path / 'ticks.jsonl'
    if not ticks_file.exists():
        return []

    ticks = []
    with open(ticks_file, 'r') as f:
        for line in f:
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                mins_left = data.get('minutesLeft', 15)
                elapsed = (15 - mins_left) * 60

                price = data.get('price', {})
                best = data.get('best', {})

                up_mid = price.get('Up', 0.5)
                down_mid = price.get('Down', 0.5)

                up_side = best.get('Up', {})
                down_side = best.get('Down', {})

                up_ask = up_side.get('ask', 0)
                up_bid = up_side.get('bid', 0)
                down_ask = down_side.get('ask', 0)
                down_bid = down_side.get('bid', 0)

                if up_mid >= down_mid:
                    edge = up_mid
                    edge_dir = 'Up'
                else:
                    edge = down_mid
                    edge_dir = 'Down'

                ticks.append(TickData(
                    elapsed_secs=elapsed,
                    up_mid=up_mid,
                    down_mid=down_mid,
                    up_ask=up_ask if up_ask else 0,
                    up_bid=up_bid if up_bid else 0,
                    down_ask=down_ask if down_ask else 0,
                    down_bid=down_bid if down_bid else 0,
                    edge=edge,
                    edge_dir=edge_dir
                ))
            except:
                continue

    return ticks


def get_winner(ticks: List[TickData]) -> Optional[str]:
    """Determine session winner from final tick."""
    if not ticks:
        return None
    final = ticks[-1]
    if final.up_mid >= 0.90:
        return 'Up'
    elif final.down_mid >= 0.90:
        return 'Down'
    return None


def get_bucket(ask: float) -> str:
    """Classify ask price into buckets."""
    if ask <= 0.66:
        return "<=0.66"
    elif ask <= 0.68:
        return "0.67-0.68"
    else:
        return ">0.68"


def simulate_trade(ticks: List[TickData], session_id: str) -> Optional[TradeAnalysis]:
    """Simulate a trade and compute full analysis."""
    if not ticks:
        return None

    winner = get_winner(ticks)
    if not winner:
        return None

    # Find entry point
    entry_tick = None
    entry_idx = None

    for i, tick in enumerate(ticks):
        if tick.elapsed_secs < CORE_START_SECS or tick.elapsed_secs > CORE_END_SECS:
            continue

        if tick.edge_dir == 'Up':
            ask = tick.up_ask
            bid = tick.up_bid
        else:
            ask = tick.down_ask
            bid = tick.down_bid

        if not ask or ask <= 0 or not bid or bid <= 0:
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

        if tick.edge < required_edge:
            continue
        if ask > CONFIG["ask_cap"]:
            continue
        if spread > CONFIG["spread_cap"]:
            continue

        entry_tick = tick
        entry_idx = i
        break

    if not entry_tick:
        return None

    # Get entry values
    direction = entry_tick.edge_dir
    if direction == 'Up':
        entry_ask = entry_tick.up_ask
        entry_bid = entry_tick.up_bid
        entry_mid = entry_tick.up_mid
    else:
        entry_ask = entry_tick.down_ask
        entry_bid = entry_tick.down_bid
        entry_mid = entry_tick.down_mid

    spread = entry_ask - entry_bid
    won = (direction == winner)
    shares = POSITION_SIZE / entry_ask
    pnl = (1.0 - entry_ask) * shares if won else -POSITION_SIZE

    # Create analysis
    analysis = TradeAnalysis(
        session_id=session_id,
        direction=direction,
        won=won,
        pnl=pnl,
        entry_ask=entry_ask,
        entry_bid=entry_bid,
        entry_edge=entry_tick.edge,
        entry_spread=spread,
        entry_elapsed=entry_tick.elapsed_secs,
        entry_bucket=get_bucket(entry_ask),
        edge_at_entry=entry_tick.edge,
    )

    # Analyze price path after entry
    mfe_mid = entry_mid  # Start at entry
    mae_mid = entry_mid
    last_dir = direction
    crossings = 0
    time_in_green = 0.0
    time_in_red = 0.0
    ever_green = False
    last_elapsed = entry_tick.elapsed_secs

    for tick in ticks[entry_idx + 1:]:
        if direction == 'Up':
            current_mid = tick.up_mid
        else:
            current_mid = tick.down_mid

        # Track MFE/MAE
        if current_mid > mfe_mid:
            mfe_mid = current_mid
            analysis.mfe_time = tick.elapsed_secs
        if current_mid < mae_mid:
            mae_mid = current_mid
            analysis.mae_time = tick.elapsed_secs

        # Track edge
        if current_mid > analysis.edge_at_peak:
            analysis.edge_at_peak = current_mid
        if current_mid < analysis.edge_at_trough or analysis.edge_at_trough == 0:
            analysis.edge_at_trough = current_mid

        # Crossings (direction changes in edge)
        current_dir = tick.edge_dir
        if current_dir != last_dir:
            crossings += 1
            last_dir = current_dir

        # Time in green/red (unrealized P&L)
        # Green = current_mid > entry_ask (we'd be profitable if we sold now)
        # Note: This is simplified - actual P&L depends on exit liquidity
        dt = tick.elapsed_secs - last_elapsed
        if current_mid > entry_ask:
            time_in_green += dt
            ever_green = True
        else:
            time_in_red += dt
        last_elapsed = tick.elapsed_secs

    # Final tick edge
    if ticks:
        final = ticks[-1]
        if direction == 'Up':
            analysis.edge_at_settlement = final.up_mid
        else:
            analysis.edge_at_settlement = final.down_mid

    # Calculate MFE/MAE as percentage moves from entry
    analysis.mfe = (mfe_mid - entry_mid) / entry_mid * 100 if entry_mid > 0 else 0
    analysis.mae = (mae_mid - entry_mid) / entry_mid * 100 if entry_mid > 0 else 0
    analysis.crossings = crossings
    analysis.ever_went_green = ever_green
    analysis.time_in_green = time_in_green
    analysis.time_in_red = time_in_red

    # Classify loss type
    if not won:
        if not ever_green:
            analysis.loss_type = "never_green"
        elif time_in_green > time_in_red:
            analysis.loss_type = "gave_back"  # Was winning, lost it
        elif crossings >= 5:
            analysis.loss_type = "whipsaw"  # Too many reversals
        else:
            analysis.loss_type = "late_flip"  # Market flipped against us late

    return analysis


def compute_entry_quality_score(t: TradeAnalysis) -> float:
    """
    Compute Entry Quality Score (EQS) based on observable inputs at entry.

    Components (each 0-1, higher = better):
    1. Edge margin: How far edge exceeds threshold
    2. Price percentile: Lower ask = better
    3. Spread quality: Tighter spread = better
    4. Timing: Earlier in window = more time for edge to express
    """
    # 1. Edge margin (0-1)
    # Required edge depends on ask bucket
    if t.entry_ask <= 0.66:
        required = 0.64
    elif t.entry_ask <= 0.68:
        required = 0.67
    else:
        required = 0.70

    edge_margin = (t.entry_edge - required)
    edge_score = min(1.0, max(0.0, edge_margin / 0.10))  # 0.10 above threshold = 1.0

    # 2. Price percentile (0-1)
    # Lower ask = better. Range: 0.60-0.68 mapped to 1.0-0.0
    price_score = 1.0 - (t.entry_ask - 0.60) / 0.08
    price_score = min(1.0, max(0.0, price_score))

    # 3. Spread quality (0-1)
    # Tighter spread = better. Range: 0.00-0.02 mapped to 1.0-0.0
    spread_score = 1.0 - t.entry_spread / 0.02
    spread_score = min(1.0, max(0.0, spread_score))

    # 4. Timing score (0-1)
    # Earlier in window = better. Window: 150-225s
    time_pct = (t.entry_elapsed - CORE_START_SECS) / (CORE_END_SECS - CORE_START_SECS)
    timing_score = 1.0 - time_pct
    timing_score = min(1.0, max(0.0, timing_score))

    # Weighted combination
    # Edge and price are most important (from prior analysis)
    weights = {
        'edge': 0.35,
        'price': 0.35,
        'spread': 0.15,
        'timing': 0.15,
    }

    eqs = (
        weights['edge'] * edge_score +
        weights['price'] * price_score +
        weights['spread'] * spread_score +
        weights['timing'] * timing_score
    )

    return eqs


def main():
    markets_dir = Path(__file__).parent.parent / 'markets_paper'

    print("=" * 100)
    print("  LOSS ANATOMY ANALYSIS")
    print("  Comprehensive analysis of losing trades for loss shaping")
    print("=" * 100)
    print()

    # Load all sessions
    print("Loading sessions...")
    sessions = sorted([
        d for d in markets_dir.iterdir()
        if d.is_dir() and d.name.startswith('btc-updown-15m-')
    ])
    print(f"  Found {len(sessions)} sessions")

    # Simulate all trades
    print("Simulating trades...")
    trades: List[TradeAnalysis] = []

    for i, session_path in enumerate(sessions):
        if (i + 1) % 500 == 0:
            print(f"  {i+1}/{len(sessions)}...")

        ticks = load_ticks(session_path)
        trade = simulate_trade(ticks, session_path.name)
        if trade:
            # Compute EQS
            trade_with_eqs = trade
            trades.append(trade_with_eqs)

    print(f"  Total trades: {len(trades)}")

    wins = [t for t in trades if t.won]
    losses = [t for t in trades if not t.won]

    print(f"  Wins: {len(wins)} ({100*len(wins)/len(trades):.1f}%)")
    print(f"  Losses: {len(losses)} ({100*len(losses)/len(trades):.1f}%)")
    print()

    # ============================================================
    # PHASE 1: LOSS AUTOPSY
    # ============================================================
    print("=" * 100)
    print("  PHASE 1: LOSS AUTOPSY")
    print("=" * 100)
    print()

    # Loss size distribution
    loss_pnls = [t.pnl for t in losses]
    print("LOSS SIZE DISTRIBUTION:")
    print(f"  Count:      {len(losses)}")
    print(f"  Total:      ${sum(loss_pnls):.2f}")
    print(f"  Mean:       ${statistics.mean(loss_pnls):.2f}")
    print(f"  Median:     ${statistics.median(loss_pnls):.2f}")
    print(f"  Std Dev:    ${statistics.stdev(loss_pnls):.2f}" if len(loss_pnls) > 1 else "  Std Dev:    N/A")
    print(f"  Min:        ${min(loss_pnls):.2f}")
    print(f"  Max:        ${max(loss_pnls):.2f}")
    print()

    # Losses by bucket
    print("LOSSES BY ENTRY PRICE BUCKET:")
    print(f"  {'Bucket':<12} {'Count':>8} {'Pct':>8} {'AvgPnL':>10} {'TotalPnL':>12}")
    print(f"  {'-'*50}")

    by_bucket = defaultdict(list)
    for t in losses:
        by_bucket[t.entry_bucket].append(t)

    for bucket in ["<=0.66", "0.67-0.68", ">0.68"]:
        grp = by_bucket.get(bucket, [])
        if grp:
            pct = 100 * len(grp) / len(losses)
            avg_pnl = statistics.mean([t.pnl for t in grp])
            total_pnl = sum([t.pnl for t in grp])
            print(f"  {bucket:<12} {len(grp):>8} {pct:>7.1f}% ${avg_pnl:>8.2f} ${total_pnl:>10.2f}")
        else:
            print(f"  {bucket:<12} {'0':>8} {'0.0':>7}% {'N/A':>10} {'$0.00':>12}")
    print()

    # Losses by entry timing
    print("LOSSES BY ENTRY TIMING:")
    print(f"  {'Window':<15} {'Count':>8} {'AvgPnL':>10} {'AvgMAE':>10}")
    print(f"  {'-'*50}")

    early_losses = [t for t in losses if t.entry_elapsed < 180]  # < 3:00
    mid_losses = [t for t in losses if 180 <= t.entry_elapsed < 210]  # 3:00-3:30
    late_losses = [t for t in losses if t.entry_elapsed >= 210]  # 3:30+

    for name, grp in [("Early (<3:00)", early_losses), ("Mid (3:00-3:30)", mid_losses), ("Late (3:30+)", late_losses)]:
        if grp:
            avg_pnl = statistics.mean([t.pnl for t in grp])
            avg_mae = statistics.mean([t.mae for t in grp])
            print(f"  {name:<15} {len(grp):>8} ${avg_pnl:>8.2f} {avg_mae:>9.2f}%")
    print()

    # Losses by type (never_green, gave_back, late_flip, whipsaw)
    print("LOSSES BY TYPE:")
    print(f"  {'Type':<15} {'Count':>8} {'Pct':>8} {'AvgPnL':>10} {'Description':<30}")
    print(f"  {'-'*80}")

    type_desc = {
        "never_green": "Never had positive unrealized P&L",
        "gave_back": "Was winning, gave it all back",
        "late_flip": "Market flipped against late",
        "whipsaw": "Too many direction changes",
    }

    by_type = defaultdict(list)
    for t in losses:
        by_type[t.loss_type].append(t)

    for loss_type in ["never_green", "gave_back", "late_flip", "whipsaw"]:
        grp = by_type.get(loss_type, [])
        if grp:
            pct = 100 * len(grp) / len(losses)
            avg_pnl = statistics.mean([t.pnl for t in grp])
            desc = type_desc.get(loss_type, "")
            print(f"  {loss_type:<15} {len(grp):>8} {pct:>7.1f}% ${avg_pnl:>8.2f} {desc:<30}")
    print()

    # Ever went green analysis
    print("LOSSES: EVER WENT GREEN vs NEVER GREEN:")
    went_green = [t for t in losses if t.ever_went_green]
    never_green = [t for t in losses if not t.ever_went_green]

    print(f"  Ever went green:  {len(went_green):>6} ({100*len(went_green)/len(losses):.1f}%)")
    if went_green:
        print(f"    Avg time in green: {statistics.mean([t.time_in_green for t in went_green]):.1f}s")
        print(f"    Avg MFE:           {statistics.mean([t.mfe for t in went_green]):.2f}%")

    print(f"  Never went green: {len(never_green):>6} ({100*len(never_green)/len(losses):.1f}%)")
    if never_green:
        print(f"    Avg MAE:           {statistics.mean([t.mae for t in never_green]):.2f}%")
    print()

    # MFE/MAE analysis for losses
    print("MFE/MAE ANALYSIS (LOSSES ONLY):")
    print(f"  {'Metric':<20} {'Mean':>10} {'Median':>10} {'Min':>10} {'Max':>10}")
    print(f"  {'-'*60}")

    mfes = [t.mfe for t in losses]
    maes = [t.mae for t in losses]

    print(f"  {'MFE (%)':<20} {statistics.mean(mfes):>9.2f}% {statistics.median(mfes):>9.2f}% {min(mfes):>9.2f}% {max(mfes):>9.2f}%")
    print(f"  {'MAE (%)':<20} {statistics.mean(maes):>9.2f}% {statistics.median(maes):>9.2f}% {min(maes):>9.2f}% {max(maes):>9.2f}%")
    print()

    # Crossings analysis
    print("CROSSINGS ANALYSIS (LOSSES ONLY):")
    print(f"  {'Crossings':<12} {'Count':>8} {'AvgPnL':>10} {'AvgMAE':>10}")
    print(f"  {'-'*45}")

    by_crossings = defaultdict(list)
    for t in losses:
        bucket = f"{min(t.crossings, 10)}+" if t.crossings >= 10 else str(t.crossings)
        by_crossings[bucket].append(t)

    for c in sorted(by_crossings.keys(), key=lambda x: int(x.replace('+', ''))):
        grp = by_crossings[c]
        avg_pnl = statistics.mean([t.pnl for t in grp])
        avg_mae = statistics.mean([t.mae for t in grp])
        print(f"  {c:<12} {len(grp):>8} ${avg_pnl:>8.2f} {avg_mae:>9.2f}%")
    print()

    # ============================================================
    # PHASE 2: ENTRY QUALITY SCORE
    # ============================================================
    print("=" * 100)
    print("  PHASE 2: ENTRY QUALITY SCORE (EQS)")
    print("=" * 100)
    print()

    # Compute EQS for all trades
    for t in trades:
        t.eqs = compute_entry_quality_score(t)

    # Bucket by EQS decile
    print("PERFORMANCE BY EQS DECILE:")
    print(f"  {'Decile':<10} {'Count':>8} {'WinRate':>10} {'AvgPnL':>10} {'TotalPnL':>12} {'AvgMAE':>10}")
    print(f"  {'-'*70}")

    sorted_by_eqs = sorted(trades, key=lambda t: t.eqs)
    decile_size = len(trades) // 10

    for d in range(10):
        start = d * decile_size
        end = start + decile_size if d < 9 else len(trades)
        decile_trades = sorted_by_eqs[start:end]

        wins_d = sum(1 for t in decile_trades if t.won)
        wr = 100 * wins_d / len(decile_trades) if decile_trades else 0
        avg_pnl = statistics.mean([t.pnl for t in decile_trades]) if decile_trades else 0
        total_pnl = sum([t.pnl for t in decile_trades])
        avg_mae = statistics.mean([t.mae for t in decile_trades]) if decile_trades else 0

        min_eqs = min(t.eqs for t in decile_trades) if decile_trades else 0
        max_eqs = max(t.eqs for t in decile_trades) if decile_trades else 0

        decile_label = f"D{d+1} ({min_eqs:.2f}-{max_eqs:.2f})"
        print(f"  {decile_label:<18} {len(decile_trades):>4} {wr:>9.1f}% ${avg_pnl:>8.2f} ${total_pnl:>10.2f} {avg_mae:>9.2f}%")
    print()

    # Key insight: Do better entries lose less?
    print("KEY INSIGHT: DO BETTER ENTRIES LOSE LESS?")
    top_20_pct = sorted_by_eqs[int(len(trades)*0.8):]
    bottom_20_pct = sorted_by_eqs[:int(len(trades)*0.2)]

    top_losses = [t for t in top_20_pct if not t.won]
    bottom_losses = [t for t in bottom_20_pct if not t.won]

    if top_losses and bottom_losses:
        top_avg_loss = statistics.mean([t.pnl for t in top_losses])
        bottom_avg_loss = statistics.mean([t.pnl for t in bottom_losses])
        top_wr = 100 * sum(1 for t in top_20_pct if t.won) / len(top_20_pct)
        bottom_wr = 100 * sum(1 for t in bottom_20_pct if t.won) / len(bottom_20_pct)

        print(f"  Top 20% EQS:    WR={top_wr:.1f}%, AvgLoss=${top_avg_loss:.2f}, LossCount={len(top_losses)}")
        print(f"  Bottom 20% EQS: WR={bottom_wr:.1f}%, AvgLoss=${bottom_avg_loss:.2f}, LossCount={len(bottom_losses)}")
        print(f"  Loss Improvement: ${top_avg_loss - bottom_avg_loss:+.2f} per losing trade")
    print()

    # ============================================================
    # PHASE 3: LOSS SHAPING RULES
    # ============================================================
    print("=" * 100)
    print("  PHASE 3: LOSS SHAPING RULES")
    print("=" * 100)
    print()

    # Baseline stats
    total_pnl = sum(t.pnl for t in trades)
    total_wins = len(wins)
    total_losses_count = len(losses)
    avg_win = statistics.mean([t.pnl for t in wins]) if wins else 0
    avg_loss = statistics.mean([t.pnl for t in losses]) if losses else 0

    print("BASELINE (Hold to Settlement):")
    print(f"  Trades:     {len(trades)}")
    print(f"  Win Rate:   {100*total_wins/len(trades):.1f}%")
    print(f"  Avg Win:    ${avg_win:.2f}")
    print(f"  Avg Loss:   ${avg_loss:.2f}")
    print(f"  Total PnL:  ${total_pnl:.2f}")
    print(f"  Win/Loss Ratio: {abs(avg_win/avg_loss):.2f}x")
    print()

    # Test Rule 1: Exit if never went green after X seconds
    print("RULE TEST: Exit if never went green after N seconds")
    print("(Hypothetical - simulating early exit for 'never_green' losses)")
    print(f"  {'Threshold':<12} {'ExitCount':>10} {'AvgSaved':>12} {'TotalSaved':>12}")
    print(f"  {'-'*50}")

    for threshold in [60, 90, 120, 180, 240]:
        # Count losses that never went green with enough time
        would_exit = [t for t in losses if not t.ever_went_green and t.entry_elapsed + threshold < 900]
        if would_exit:
            # Assume we exit at threshold with MAE-based loss (simplified)
            # In reality, this needs tick-by-tick simulation
            # Here we estimate: if we exited earlier, loss would be proportionally smaller
            # This is a ROUGH estimate - actual requires full simulation
            avg_saved = statistics.mean([-t.pnl * 0.3 for t in would_exit])  # Rough 30% save estimate
            total_saved = sum([-t.pnl * 0.3 for t in would_exit])
            print(f"  {threshold}s{'':<8} {len(would_exit):>10} ${avg_saved:>10.2f} ${total_saved:>10.2f}")
    print()
    print("  NOTE: These are rough estimates. Actual implementation needs tick simulation.")
    print()

    # Test Rule 2: Skip trades with low EQS
    print("RULE TEST: Skip trades with EQS below threshold")
    print(f"  {'EQS_Min':<10} {'Trades':>8} {'Skipped':>8} {'WR':>8} {'AvgPnL':>10} {'TotalPnL':>12}")
    print(f"  {'-'*60}")

    for eqs_min in [0.2, 0.3, 0.4, 0.5]:
        filtered = [t for t in trades if t.eqs >= eqs_min]
        skipped = len(trades) - len(filtered)
        if filtered:
            wr = 100 * sum(1 for t in filtered if t.won) / len(filtered)
            avg_pnl = statistics.mean([t.pnl for t in filtered])
            total = sum([t.pnl for t in filtered])
            print(f"  >={eqs_min:<8.2f} {len(filtered):>8} {skipped:>8} {wr:>7.1f}% ${avg_pnl:>8.2f} ${total:>10.2f}")
    print()

    # ============================================================
    # PHASE 4: PAYOFF SYMMETRY
    # ============================================================
    print("=" * 100)
    print("  PHASE 4: PAYOFF SYMMETRY")
    print("=" * 100)
    print()

    # Current payoff distribution
    print("CURRENT PAYOFF DISTRIBUTION:")
    all_pnls = [t.pnl for t in trades]
    win_pnls = [t.pnl for t in wins]
    loss_pnls = [t.pnl for t in losses]

    print(f"  Win Distribution:")
    print(f"    Mean:   ${statistics.mean(win_pnls):.2f}")
    print(f"    Median: ${statistics.median(win_pnls):.2f}")
    print(f"    Std:    ${statistics.stdev(win_pnls):.2f}" if len(win_pnls) > 1 else "    Std:    N/A")

    print(f"  Loss Distribution:")
    print(f"    Mean:   ${statistics.mean(loss_pnls):.2f}")
    print(f"    Median: ${statistics.median(loss_pnls):.2f}")
    print(f"    Std:    ${statistics.stdev(loss_pnls):.2f}" if len(loss_pnls) > 1 else "    Std:    N/A")
    print()

    # Tail analysis
    print("TAIL ANALYSIS:")
    sorted_losses = sorted(loss_pnls)
    n = len(sorted_losses)

    worst_5pct = sorted_losses[:max(1, int(n * 0.05))]
    worst_10pct = sorted_losses[:max(1, int(n * 0.10))]
    worst_25pct = sorted_losses[:max(1, int(n * 0.25))]

    print(f"  Worst 5%:  Count={len(worst_5pct)}, AvgLoss=${statistics.mean(worst_5pct):.2f}, Total=${sum(worst_5pct):.2f}")
    print(f"  Worst 10%: Count={len(worst_10pct)}, AvgLoss=${statistics.mean(worst_10pct):.2f}, Total=${sum(worst_10pct):.2f}")
    print(f"  Worst 25%: Count={len(worst_25pct)}, AvgLoss=${statistics.mean(worst_25pct):.2f}, Total=${sum(worst_25pct):.2f}")
    print()

    # Symmetry target
    print("SYMMETRY TARGET:")
    print(f"  Current:  1 Loss = {abs(statistics.mean(loss_pnls)/statistics.mean(win_pnls)):.2f} Wins")
    print(f"  Target:   1 Loss = 1.0 Wins")
    print(f"  Gap:      Need to reduce avg loss by ${abs(statistics.mean(loss_pnls)) - statistics.mean(win_pnls):.2f}")
    print()

    # ============================================================
    # PHASE 5: FAILURE MODES
    # ============================================================
    print("=" * 100)
    print("  PHASE 5: FAILURE MODE CHECK")
    print("=" * 100)
    print()

    print("POTENTIAL FAILURE MODES:")
    print()
    print("1. EQS FILTER (skip EQS < 0.4):")
    eqs_filtered = [t for t in trades if t.eqs >= 0.4]
    eqs_skipped = [t for t in trades if t.eqs < 0.4]
    if eqs_skipped:
        skipped_wins = sum(1 for t in eqs_skipped if t.won)
        skipped_pnl = sum(t.pnl for t in eqs_skipped)
        print(f"   - Skips {len(eqs_skipped)} trades ({100*len(eqs_skipped)/len(trades):.1f}%)")
        print(f"   - Skipped PnL: ${skipped_pnl:.2f}")
        print(f"   - Skipped had {100*skipped_wins/len(eqs_skipped):.1f}% win rate")
        if skipped_pnl > 0:
            print(f"   - WARNING: Would skip ${skipped_pnl:.2f} in PROFITS")
        else:
            print(f"   - OK: Would skip ${abs(skipped_pnl):.2f} in LOSSES")
    print()

    print("2. NEVER-GREEN EXIT RULE:")
    never_green_full = [t for t in trades if not t.ever_went_green]
    never_green_wins = [t for t in never_green_full if t.won]
    print(f"   - {len(never_green_full)} trades never went green")
    print(f"   - Of those, {len(never_green_wins)} were WINS ({100*len(never_green_wins)/len(never_green_full):.1f}%)")
    if never_green_wins:
        print(f"   - WARNING: Early exit would kill ${sum(t.pnl for t in never_green_wins):.2f} in wins")
    print()

    print("3. TIGHT SPREAD FILTER (spread < 0.01):")
    tight_spread = [t for t in trades if t.entry_spread < 0.01]
    if tight_spread:
        ts_wr = 100 * sum(1 for t in tight_spread if t.won) / len(tight_spread)
        ts_pnl = sum(t.pnl for t in tight_spread)
        print(f"   - {len(tight_spread)} trades with spread < 0.01")
        print(f"   - Win rate: {ts_wr:.1f}%")
        print(f"   - Total PnL: ${ts_pnl:.2f}")
    else:
        print(f"   - No trades found with spread < 0.01")
    print()

    # ============================================================
    # FINAL RECOMMENDATIONS
    # ============================================================
    print("=" * 100)
    print("  FINAL RECOMMENDATIONS")
    print("=" * 100)
    print()

    print("RECOMMENDED ADJUSTMENTS (data-backed):")
    print()

    # Check if EQS filter helps
    baseline_pnl = sum(t.pnl for t in trades)
    filtered_04 = [t for t in trades if t.eqs >= 0.4]
    filtered_pnl = sum(t.pnl for t in filtered_04)

    if filtered_pnl > baseline_pnl:
        print("1. [RECOMMENDED] EQS >= 0.4 filter")
        print(f"   PnL improvement: ${filtered_pnl - baseline_pnl:+.2f}")
        print(f"   Trade reduction: {len(trades) - len(filtered_04)}")
    else:
        print("1. [REJECTED] EQS filter hurts PnL")
        print(f"   Would lose: ${baseline_pnl - filtered_pnl:.2f}")
    print()

    # Check never-green pattern
    if len(never_green_wins) / len(never_green_full) < 0.3:
        print("2. [INVESTIGATE] Never-green early exit")
        print(f"   Only {100*len(never_green_wins)/len(never_green_full):.1f}% of never-green trades win")
        print("   Needs tick-by-tick simulation to validate")
    else:
        print("2. [REJECTED] Never-green exit would kill too many wins")
    print()

    print("RULES EXPLICITLY REJECTED:")
    print("- Global kill switch (destroys edge per sweep)")
    print("- Time-based stops (arbitrary, not data-backed)")
    print("- Discretionary exits (introduces path dependency)")
    print()

    print("=" * 100)
    print("  ANALYSIS COMPLETE")
    print("=" * 100)


if __name__ == '__main__':
    main()
