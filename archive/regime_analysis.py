"""
REGIME ANALYSIS - RULEV3 Core Only (No Changes)
================================================
Analyze existing backtest trades by market regime.
NO filtering, NO blocking, NO parameter changes.

RULEV3 Settings (LOCKED):
- Mode: core_only
- T3 edge threshold: 0.64
- Price cap: 0.72
"""

import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime

# RULEV3 core_only backtest (1,008 trades)
TRADES_FILE = Path(__file__).parent / "backtest_full_logs" / "runs" / "rulev3_core_only_t3_064_cap072" / "trades_full.jsonl"

def load_trades(filepath):
    """Load trades from JSONL file"""
    trades = []
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    trades.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return trades

def label_trades_with_regime(trades):
    """
    Label each trade with:
    - Market regime (TRENDING vs MEAN_REVERTING based on recent streak)
    - Session timing (EARLY vs LATE based on elapsed time)
    - Momentum alignment (WITH vs AGAINST prev_winner)
    """
    labeled_trades = []

    # Track streak state
    prev_winner = None
    consecutive_same_direction = 0
    recent_winners = []  # Last N winners for regime detection

    for i, trade in enumerate(trades):
        # Get trade info
        direction = trade.get('direction', '')
        pnl = trade.get('pnl', 0)
        elapsed = trade.get('elapsed_seconds', trade.get('entry_elapsed', 0))
        session = trade.get('session', '')

        # Determine outcome
        outcome = trade.get('outcome', None)
        if isinstance(outcome, bool):
            is_win = outcome
        else:
            is_win = pnl > 0

        # Determine market winner
        if is_win:
            market_winner = direction
        else:
            market_winner = 'Down' if direction == 'Up' else 'Up'

        # === REGIME LABELING (before updating state) ===

        # 1. Session timing regime
        if elapsed <= 200:
            session_regime = "EARLY"
        elif elapsed <= 400:
            session_regime = "MID"
        else:
            session_regime = "LATE"

        # 2. Market regime based on recent streak
        if consecutive_same_direction >= 3:
            market_regime = "TRENDING"
        elif consecutive_same_direction <= 1:
            market_regime = "CHOPPY"
        else:
            market_regime = "NEUTRAL"

        # Alternative: Use recent win distribution
        if len(recent_winners) >= 5:
            up_count = sum(1 for w in recent_winners[-5:] if w == 'Up')
            if up_count >= 4 or up_count <= 1:
                trend_regime = "TRENDING"
            else:
                trend_regime = "MEAN_REVERTING"
        else:
            trend_regime = "UNKNOWN"

        # 3. Momentum alignment
        if prev_winner is None:
            momentum = "FIRST"
        elif direction == prev_winner:
            momentum = "WITH"
        else:
            momentum = "AGAINST"

        # Record labeled trade
        labeled_trade = {
            **trade,
            'trade_idx': i,
            'is_win': is_win,
            'market_winner': market_winner,
            'prev_winner': prev_winner,
            'consecutive_same_dir': consecutive_same_direction,
            'session_regime': session_regime,
            'market_regime': market_regime,
            'trend_regime': trend_regime,
            'momentum': momentum,
            'elapsed': elapsed
        }
        labeled_trades.append(labeled_trade)

        # Update streak state AFTER this trade
        if market_winner == prev_winner:
            consecutive_same_direction += 1
        else:
            consecutive_same_direction = 1
            prev_winner = market_winner

        recent_winners.append(market_winner)

    return labeled_trades

def compute_stats(trades, label=""):
    """Compute stats for a subset of trades"""
    if not trades:
        return {
            'count': 0,
            'wins': 0,
            'losses': 0,
            'win_rate': 0,
            'total_pnl': 0,
            'avg_pnl': 0
        }

    wins = sum(1 for t in trades if t['is_win'])
    losses = len(trades) - wins
    total_pnl = sum(t.get('pnl', 0) for t in trades)

    return {
        'count': len(trades),
        'wins': wins,
        'losses': losses,
        'win_rate': wins / len(trades) * 100,
        'total_pnl': total_pnl,
        'avg_pnl': total_pnl / len(trades)
    }

def print_stats_table(stats_dict, title):
    """Print a formatted stats table"""
    print()
    print(f"{'='*70}")
    print(title)
    print(f"{'='*70}")
    print()
    print(f"{'Regime':<20} {'Count':<8} {'Wins':<8} {'Losses':<8} {'Win%':<10} {'TotalPnL':<12} {'AvgPnL':<10}")
    print("-" * 76)

    for regime, stats in stats_dict.items():
        print(f"{regime:<20} {stats['count']:<8} {stats['wins']:<8} {stats['losses']:<8} "
              f"{stats['win_rate']:<9.1f}% ${stats['total_pnl']:<11.2f} ${stats['avg_pnl']:<9.4f}")

def main():
    print("=" * 70)
    print("REGIME ANALYSIS - RULEV3 Core Only")
    print("=" * 70)
    print()
    print("Settings (LOCKED - NO CHANGES):")
    print("  Mode: core_only")
    print("  T3 threshold: 0.64")
    print("  Price cap: 0.72")
    print()

    # Load trades
    if not TRADES_FILE.exists():
        print(f"ERROR: Trades file not found: {TRADES_FILE}")
        return

    print(f"Loading trades from: {TRADES_FILE.name}")
    trades = load_trades(TRADES_FILE)
    print(f"Loaded {len(trades)} trades")

    # Label trades
    print("Labeling trades with regime data...")
    labeled = label_trades_with_regime(trades)

    # === OVERALL STATS ===
    overall = compute_stats(labeled, "ALL")
    print()
    print(f"OVERALL: {overall['count']} trades | {overall['win_rate']:.1f}% win | ${overall['total_pnl']:.2f} PnL | ${overall['avg_pnl']:.4f}/trade")

    # === SESSION REGIME ANALYSIS ===
    session_regimes = defaultdict(list)
    for t in labeled:
        session_regimes[t['session_regime']].append(t)

    session_stats = {r: compute_stats(trades) for r, trades in session_regimes.items()}
    print_stats_table(session_stats, "SESSION TIMING REGIME")

    # === MARKET REGIME ANALYSIS (Streak-based) ===
    market_regimes = defaultdict(list)
    for t in labeled:
        market_regimes[t['market_regime']].append(t)

    market_stats = {r: compute_stats(trades) for r, trades in market_regimes.items()}
    print_stats_table(market_stats, "MARKET REGIME (Streak-based)")

    # === TREND REGIME ANALYSIS (Recent distribution) ===
    trend_regimes = defaultdict(list)
    for t in labeled:
        trend_regimes[t['trend_regime']].append(t)

    trend_stats = {r: compute_stats(trades) for r, trades in trend_regimes.items()}
    print_stats_table(trend_stats, "TREND REGIME (5-trade lookback)")

    # === MOMENTUM ANALYSIS ===
    momentum_types = defaultdict(list)
    for t in labeled:
        momentum_types[t['momentum']].append(t)

    momentum_stats = {r: compute_stats(trades) for r, trades in momentum_types.items()}
    print_stats_table(momentum_stats, "MOMENTUM ALIGNMENT (Overall)")

    # === MOMENTUM WITHIN EACH SESSION REGIME ===
    print()
    print("=" * 70)
    print("MOMENTUM BREAKDOWN BY SESSION REGIME")
    print("=" * 70)

    for session_regime in ['EARLY', 'MID', 'LATE']:
        regime_trades = session_regimes.get(session_regime, [])
        if not regime_trades:
            continue

        print(f"\n--- {session_regime} Session ({len(regime_trades)} trades) ---")

        with_momentum = [t for t in regime_trades if t['momentum'] == 'WITH']
        against_momentum = [t for t in regime_trades if t['momentum'] == 'AGAINST']

        with_stats = compute_stats(with_momentum)
        against_stats = compute_stats(against_momentum)

        print(f"{'Type':<15} {'Count':<8} {'Win%':<10} {'TotalPnL':<12} {'AvgPnL':<10}")
        print("-" * 55)
        print(f"{'WITH':<15} {with_stats['count']:<8} {with_stats['win_rate']:<9.1f}% ${with_stats['total_pnl']:<11.2f} ${with_stats['avg_pnl']:<9.4f}")
        print(f"{'AGAINST':<15} {against_stats['count']:<8} {against_stats['win_rate']:<9.1f}% ${against_stats['total_pnl']:<11.2f} ${against_stats['avg_pnl']:<9.4f}")

        # Delta
        if with_stats['count'] > 0 and against_stats['count'] > 0:
            pnl_delta = against_stats['avg_pnl'] - with_stats['avg_pnl']
            wr_delta = against_stats['win_rate'] - with_stats['win_rate']
            print(f"{'DELTA (A-W)':<15} {'':<8} {wr_delta:+<9.1f}% ${pnl_delta * against_stats['count']:<11.2f} ${pnl_delta:<+9.4f}")

    # === MOMENTUM WITHIN EACH MARKET REGIME ===
    print()
    print("=" * 70)
    print("MOMENTUM BREAKDOWN BY MARKET REGIME")
    print("=" * 70)

    for market_regime in ['TRENDING', 'NEUTRAL', 'CHOPPY']:
        regime_trades = market_regimes.get(market_regime, [])
        if not regime_trades:
            continue

        print(f"\n--- {market_regime} Market ({len(regime_trades)} trades) ---")

        with_momentum = [t for t in regime_trades if t['momentum'] == 'WITH']
        against_momentum = [t for t in regime_trades if t['momentum'] == 'AGAINST']

        with_stats = compute_stats(with_momentum)
        against_stats = compute_stats(against_momentum)

        print(f"{'Type':<15} {'Count':<8} {'Win%':<10} {'TotalPnL':<12} {'AvgPnL':<10}")
        print("-" * 55)
        print(f"{'WITH':<15} {with_stats['count']:<8} {with_stats['win_rate']:<9.1f}% ${with_stats['total_pnl']:<11.2f} ${with_stats['avg_pnl']:<9.4f}")
        print(f"{'AGAINST':<15} {against_stats['count']:<8} {against_stats['win_rate']:<9.1f}% ${against_stats['total_pnl']:<11.2f} ${against_stats['avg_pnl']:<9.4f}")

        if with_stats['count'] > 0 and against_stats['count'] > 0:
            pnl_delta = against_stats['avg_pnl'] - with_stats['avg_pnl']
            wr_delta = against_stats['win_rate'] - with_stats['win_rate']
            better = "AGAINST" if pnl_delta > 0 else "WITH"
            print(f"{'BETTER':<15} {better}")

    # === MOMENTUM WITHIN TREND REGIME ===
    print()
    print("=" * 70)
    print("MOMENTUM BREAKDOWN BY TREND REGIME (5-trade lookback)")
    print("=" * 70)

    for trend_regime in ['TRENDING', 'MEAN_REVERTING', 'UNKNOWN']:
        regime_trades = trend_regimes.get(trend_regime, [])
        if not regime_trades:
            continue

        print(f"\n--- {trend_regime} ({len(regime_trades)} trades) ---")

        with_momentum = [t for t in regime_trades if t['momentum'] == 'WITH']
        against_momentum = [t for t in regime_trades if t['momentum'] == 'AGAINST']

        with_stats = compute_stats(with_momentum)
        against_stats = compute_stats(against_momentum)

        print(f"{'Type':<15} {'Count':<8} {'Win%':<10} {'TotalPnL':<12} {'AvgPnL':<10}")
        print("-" * 55)
        print(f"{'WITH':<15} {with_stats['count']:<8} {with_stats['win_rate']:<9.1f}% ${with_stats['total_pnl']:<11.2f} ${with_stats['avg_pnl']:<9.4f}")
        print(f"{'AGAINST':<15} {against_stats['count']:<8} {against_stats['win_rate']:<9.1f}% ${against_stats['total_pnl']:<11.2f} ${against_stats['avg_pnl']:<9.4f}")

        if with_stats['count'] > 0 and against_stats['count'] > 0:
            pnl_delta = against_stats['avg_pnl'] - with_stats['avg_pnl']
            better = "AGAINST" if pnl_delta > 0 else "WITH"
            print(f"{'BETTER':<15} {better} (by ${abs(pnl_delta):.4f}/trade)")

    # === SUMMARY ===
    print()
    print("=" * 70)
    print("SUMMARY: WITH vs AGAINST MOMENTUM")
    print("=" * 70)
    print()

    with_all = [t for t in labeled if t['momentum'] == 'WITH']
    against_all = [t for t in labeled if t['momentum'] == 'AGAINST']

    with_stats = compute_stats(with_all)
    against_stats = compute_stats(against_all)

    print(f"WITH momentum:    {with_stats['count']} trades | {with_stats['win_rate']:.1f}% win | ${with_stats['total_pnl']:.2f} | ${with_stats['avg_pnl']:.4f}/trade")
    print(f"AGAINST momentum: {against_stats['count']} trades | {against_stats['win_rate']:.1f}% win | ${against_stats['total_pnl']:.2f} | ${against_stats['avg_pnl']:.4f}/trade")

    if with_stats['avg_pnl'] > against_stats['avg_pnl']:
        print(f"\n>>> WITH momentum is MORE profitable by ${with_stats['avg_pnl'] - against_stats['avg_pnl']:.4f}/trade")
    else:
        print(f"\n>>> AGAINST momentum is MORE profitable by ${against_stats['avg_pnl'] - with_stats['avg_pnl']:.4f}/trade")

    print()
    print("=" * 70)
    print("NOTE: This is analysis only. No trades were filtered or blocked.")
    print("=" * 70)

if __name__ == "__main__":
    main()
