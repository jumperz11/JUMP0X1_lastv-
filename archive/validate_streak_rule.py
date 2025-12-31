"""
Rule Validation Script: Consecutive Wins Streak Filter
=======================================================
Rule: IF consecutive_wins >= 2 AND trade_direction != prev_winner → BLOCK TRADE

This script validates the rule against existing backtest data WITHOUT modifying anything.
"""

import json
import os
from collections import defaultdict
from pathlib import Path

# Path to RULEV3 backtest trades
# Use baseline_RULEV1 for full dataset (~2046 trades)
TRADES_FILE = Path(__file__).parent / "backtest_full_logs" / "baseline_RULEV1" / "trades_full.jsonl"

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

def compute_streak_labels(trades):
    """
    For each trade, compute:
    - prev_winner: winner direction of immediately previous settled trade
    - consecutive_wins: number of consecutive wins in prev_winner direction
    - would_block: True if rule would block this trade
    """
    labeled_trades = []

    # Track streak state
    prev_winner = None
    consecutive_wins = 0

    for i, trade in enumerate(trades):
        # Get trade direction and result
        direction = trade.get('direction', trade.get('side', ''))
        pnl = trade.get('pnl', 0)

        # Handle outcome - can be bool, string, or missing
        outcome = trade.get('outcome', trade.get('result', None))
        if isinstance(outcome, bool):
            is_win = outcome
        elif isinstance(outcome, str):
            is_win = outcome.upper() == 'WIN' or outcome.upper() == 'TRUE'
        else:
            is_win = pnl > 0

        # Determine winner direction (market outcome)
        winner = trade.get('winner', '')
        if winner:
            market_winner = winner
        elif is_win:
            market_winner = direction
        else:
            # If loss, winner is opposite
            market_winner = 'Down' if direction == 'Up' else 'Up'

        # Apply the rule BEFORE recording result
        # Rule: IF consecutive_wins >= 2 AND trade_direction != prev_winner -> BLOCK
        would_block = False
        if prev_winner is not None and consecutive_wins >= 2:
            if direction != prev_winner:
                would_block = True

        # Record labeled trade
        labeled_trade = {
            **trade,
            'trade_idx': i,
            'computed_prev_winner': prev_winner,
            'computed_consecutive_wins': consecutive_wins,
            'would_block': would_block,
            'is_win': is_win,
            'market_winner': market_winner
        }
        labeled_trades.append(labeled_trade)

        # Update streak state AFTER this trade settles
        if market_winner == prev_winner:
            consecutive_wins += 1
        else:
            consecutive_wins = 1
            prev_winner = market_winner

    return labeled_trades

def analyze_blocked_trades(labeled_trades):
    """Analyze blocked trades in isolation"""
    blocked = [t for t in labeled_trades if t['would_block']]
    kept = [t for t in labeled_trades if not t['would_block']]

    # Blocked trade stats
    blocked_wins = sum(1 for t in blocked if t['is_win'])
    blocked_losses = len(blocked) - blocked_wins
    blocked_pnl = sum(t.get('pnl', 0) for t in blocked)
    blocked_win_rate = blocked_wins / len(blocked) * 100 if blocked else 0
    blocked_avg_pnl = blocked_pnl / len(blocked) if blocked else 0

    # Kept trade stats
    kept_wins = sum(1 for t in kept if t['is_win'])
    kept_losses = len(kept) - kept_wins
    kept_pnl = sum(t.get('pnl', 0) for t in kept)
    kept_win_rate = kept_wins / len(kept) * 100 if kept else 0
    kept_avg_pnl = kept_pnl / len(kept) if kept else 0

    return {
        'blocked': {
            'count': len(blocked),
            'wins': blocked_wins,
            'losses': blocked_losses,
            'win_rate': blocked_win_rate,
            'total_pnl': blocked_pnl,
            'avg_pnl': blocked_avg_pnl,
            'trades': blocked
        },
        'kept': {
            'count': len(kept),
            'wins': kept_wins,
            'losses': kept_losses,
            'win_rate': kept_win_rate,
            'total_pnl': kept_pnl,
            'avg_pnl': kept_avg_pnl
        }
    }

def compute_drawdown(trades):
    """Compute max drawdown from trade PnLs"""
    cumulative = 0
    peak = 0
    max_dd = 0

    for t in trades:
        cumulative += t.get('pnl', 0)
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

    return max_dd

def simulate_with_rule(labeled_trades):
    """Simulate performance with blocked trades removed"""
    kept = [t for t in labeled_trades if not t['would_block']]
    all_trades = labeled_trades

    # Before metrics
    before_pnl = sum(t.get('pnl', 0) for t in all_trades)
    before_wins = sum(1 for t in all_trades if t['is_win'])
    before_win_rate = before_wins / len(all_trades) * 100 if all_trades else 0
    before_dd = compute_drawdown(all_trades)
    before_avg_pnl = before_pnl / len(all_trades) if all_trades else 0

    # After metrics (with rule applied)
    after_pnl = sum(t.get('pnl', 0) for t in kept)
    after_wins = sum(1 for t in kept if t['is_win'])
    after_win_rate = after_wins / len(kept) * 100 if kept else 0
    after_dd = compute_drawdown(kept)
    after_avg_pnl = after_pnl / len(kept) if kept else 0

    return {
        'before': {
            'trade_count': len(all_trades),
            'total_pnl': before_pnl,
            'win_rate': before_win_rate,
            'max_drawdown': before_dd,
            'pnl_per_trade': before_avg_pnl
        },
        'after': {
            'trade_count': len(kept),
            'total_pnl': after_pnl,
            'win_rate': after_win_rate,
            'max_drawdown': after_dd,
            'pnl_per_trade': after_avg_pnl
        },
        'delta': {
            'trade_count': len(kept) - len(all_trades),
            'total_pnl': after_pnl - before_pnl,
            'win_rate': after_win_rate - before_win_rate,
            'max_drawdown': after_dd - before_dd,
            'pnl_per_trade': after_avg_pnl - before_avg_pnl
        }
    }

def analyze_loss_clustering(blocked_trades):
    """Analyze if blocked trades cluster with losses"""
    if not blocked_trades:
        return {}

    # Count consecutive losses in blocked trades
    loss_streaks = []
    current_streak = 0

    for t in blocked_trades:
        if not t['is_win']:
            current_streak += 1
        else:
            if current_streak > 0:
                loss_streaks.append(current_streak)
            current_streak = 0
    if current_streak > 0:
        loss_streaks.append(current_streak)

    return {
        'loss_streaks': loss_streaks,
        'max_loss_streak': max(loss_streaks) if loss_streaks else 0,
        'avg_loss_streak': sum(loss_streaks) / len(loss_streaks) if loss_streaks else 0
    }

def main():
    print("=" * 70)
    print("RULE VALIDATION: Consecutive Wins Streak Filter")
    print("=" * 70)
    print()
    print("Rule: IF consecutive_wins >= 2 AND direction != prev_winner -> BLOCK")
    print()

    # Load trades
    if not TRADES_FILE.exists():
        print(f"ERROR: Trades file not found: {TRADES_FILE}")
        return

    print(f"Loading trades from: {TRADES_FILE.name}")
    trades = load_trades(TRADES_FILE)
    print(f"Loaded {len(trades)} trades")
    print()

    # Label trades with rule criteria
    print("Labeling trades with rule criteria...")
    labeled_trades = compute_streak_labels(trades)

    # Analyze blocked vs kept
    print("Analyzing blocked trades...")
    analysis = analyze_blocked_trades(labeled_trades)

    blocked = analysis['blocked']
    kept = analysis['kept']

    print()
    print("=" * 70)
    print("BLOCKED vs KEPT TRADES")
    print("=" * 70)
    print()
    print(f"{'Metric':<25} {'KEPT':<20} {'BLOCKED':<20}")
    print("-" * 65)
    print(f"{'Trade Count':<25} {kept['count']:<20} {blocked['count']:<20}")
    print(f"{'Wins':<25} {kept['wins']:<20} {blocked['wins']:<20}")
    print(f"{'Losses':<25} {kept['losses']:<20} {blocked['losses']:<20}")
    print(f"{'Win Rate':<25} {kept['win_rate']:<19.1f}% {blocked['win_rate']:<19.1f}%")
    print(f"{'Total PnL':<25} ${kept['total_pnl']:<18.2f} ${blocked['total_pnl']:<18.2f}")
    print(f"{'Avg PnL/Trade':<25} ${kept['avg_pnl']:<18.4f} ${blocked['avg_pnl']:<18.4f}")

    # Simulate with rule
    print()
    print("=" * 70)
    print("BEFORE vs AFTER (with rule applied)")
    print("=" * 70)

    simulation = simulate_with_rule(labeled_trades)
    before = simulation['before']
    after = simulation['after']
    delta = simulation['delta']

    print()
    print(f"{'Metric':<25} {'BEFORE':<18} {'AFTER':<18} {'DELTA':<15}")
    print("-" * 75)
    print(f"{'Trade Count':<25} {before['trade_count']:<18} {after['trade_count']:<18} {delta['trade_count']:<15}")
    print(f"{'Total PnL':<25} ${before['total_pnl']:<17.2f} ${after['total_pnl']:<17.2f} ${delta['total_pnl']:<14.2f}")
    print(f"{'Win Rate':<25} {before['win_rate']:<17.1f}% {after['win_rate']:<17.1f}% {delta['win_rate']:+<14.1f}%")
    print(f"{'Max Drawdown':<25} ${before['max_drawdown']:<17.2f} ${after['max_drawdown']:<17.2f} ${delta['max_drawdown']:<14.2f}")
    print(f"{'PnL per Trade':<25} ${before['pnl_per_trade']:<17.4f} ${after['pnl_per_trade']:<17.4f} ${delta['pnl_per_trade']:<14.4f}")

    # Loss clustering
    print()
    print("=" * 70)
    print("BLOCKED TRADES - LOSS CLUSTERING ANALYSIS")
    print("=" * 70)

    loss_analysis = analyze_loss_clustering(blocked['trades'])
    print()
    print(f"Blocked trades with losses: {blocked['losses']}")
    print(f"Max consecutive losses in blocked set: {loss_analysis.get('max_loss_streak', 0)}")

    # Sample of blocked trades
    print()
    print("=" * 70)
    print("SAMPLE BLOCKED TRADES (first 10)")
    print("=" * 70)
    print()
    print(f"{'#':<4} {'Dir':<6} {'PrevWin':<8} {'ConsecW':<8} {'Result':<8} {'PnL':<12}")
    print("-" * 55)
    for i, t in enumerate(blocked['trades'][:10]):
        result = "WIN" if t['is_win'] else "LOSS"
        pnl = t.get('pnl', 0)
        print(f"{i+1:<4} {t.get('direction', 'N/A'):<6} {t['computed_prev_winner'] or 'N/A':<8} {t['computed_consecutive_wins']:<8} {result:<8} ${pnl:<11.2f}")

    # Conclusion
    print()
    print("=" * 70)
    print("CONCLUSION")
    print("=" * 70)
    print()

    # Decision criteria
    blocked_has_negative_expectancy = blocked['avg_pnl'] < 0
    pnl_improved = delta['total_pnl'] > 0
    drawdown_reduced = delta['max_drawdown'] <= 0
    win_rate_improved = delta['win_rate'] > 0

    print(f"1. Do blocked trades have negative expectancy?")
    print(f"   Blocked Avg PnL: ${blocked['avg_pnl']:.4f}")
    if blocked_has_negative_expectancy:
        print(f"   [Y] YES - Blocked trades lose money on average")
    else:
        print(f"   [N] NO - Blocked trades are profitable")

    print()
    print(f"2. Does skipping them improve PnL?")
    print(f"   PnL Delta: ${delta['total_pnl']:.2f}")
    if pnl_improved:
        print(f"   [Y] YES - Total PnL improved by ${delta['total_pnl']:.2f}")
    else:
        print(f"   [N] NO - Total PnL decreased")

    print()
    print(f"3. Does it reduce drawdown?")
    print(f"   Drawdown Delta: ${delta['max_drawdown']:.2f}")
    if drawdown_reduced:
        print(f"   [Y] YES - Max drawdown reduced by ${abs(delta['max_drawdown']):.2f}")
    else:
        print(f"   [N] NO - Max drawdown increased")

    print()
    print(f"4. Does win rate stay same or improve?")
    print(f"   Win Rate Delta: {delta['win_rate']:+.1f}%")
    if win_rate_improved:
        print(f"   [Y] YES - Win rate improved by {delta['win_rate']:.1f}%")
    else:
        print(f"   [~] NEUTRAL - Win rate slightly decreased")

    # Final verdict
    print()
    print("=" * 70)

    score = sum([blocked_has_negative_expectancy, pnl_improved, drawdown_reduced, win_rate_improved])

    if score >= 3 and blocked_has_negative_expectancy and pnl_improved:
        print("[OK] RULE VALIDATED")
        print("   The rule successfully identifies negative-expectancy trades.")
        print("   Recommendation: IMPLEMENT the streak filter.")
    elif score >= 2:
        print("[??] NEUTRAL / INCONCLUSIVE")
        print("   Mixed results. Consider further testing.")
    else:
        print("[X] RULE REJECTED")
        print("   The rule does not improve performance.")
        print("   Recommendation: DO NOT implement.")

    print("=" * 70)

    return {
        'analysis': analysis,
        'simulation': simulation,
        'score': score
    }

if __name__ == "__main__":
    main()
