"""
REGIME ANALYSIS - STEPS 2, 3, 4
================================
RULEV3 Core Only (NO CHANGES to base strategy)
"""

import json
from pathlib import Path
from collections import defaultdict

TRADES_FILE = Path(__file__).parent / "backtest_full_logs" / "runs" / "rulev3_core_only_t3_064_cap072" / "trades_full.jsonl"

def load_trades(filepath):
    trades = []
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    trades.append(json.loads(line))
                except:
                    continue
    return trades

def label_trades(trades):
    """Label each trade with regime and momentum data"""
    labeled = []
    prev_winner = None
    consecutive_same = 0

    for i, trade in enumerate(trades):
        direction = trade.get('direction', '')
        pnl = trade.get('pnl', 0)
        edge = trade.get('edge', 0)
        elapsed = trade.get('elapsed_seconds', trade.get('entry_elapsed', 0))

        outcome = trade.get('outcome', None)
        is_win = outcome if isinstance(outcome, bool) else pnl > 0
        market_winner = direction if is_win else ('Down' if direction == 'Up' else 'Up')

        # Regime labels
        if consecutive_same >= 3:
            market_regime = "TRENDING"
        elif consecutive_same <= 1:
            market_regime = "CHOPPY"
        else:
            market_regime = "NEUTRAL"

        # Momentum
        if prev_winner is None:
            momentum = "FIRST"
        elif direction == prev_winner:
            momentum = "WITH"
        else:
            momentum = "AGAINST"

        labeled.append({
            **trade,
            'idx': i,
            'is_win': is_win,
            'market_winner': market_winner,
            'prev_winner': prev_winner,
            'consecutive_same': consecutive_same,
            'market_regime': market_regime,
            'momentum': momentum,
            'edge': edge,
            'elapsed': elapsed
        })

        # Update state
        if market_winner == prev_winner:
            consecutive_same += 1
        else:
            consecutive_same = 1
            prev_winner = market_winner

    return labeled

def compute_stats(trades):
    if not trades:
        return {'count': 0, 'wins': 0, 'losses': 0, 'win_rate': 0, 'total_pnl': 0, 'avg_pnl': 0}
    wins = sum(1 for t in trades if t['is_win'])
    total_pnl = sum(t.get('pnl', 0) for t in trades)
    return {
        'count': len(trades),
        'wins': wins,
        'losses': len(trades) - wins,
        'win_rate': wins / len(trades) * 100,
        'total_pnl': total_pnl,
        'avg_pnl': total_pnl / len(trades)
    }

def compute_drawdown(trades):
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

def main():
    print("=" * 70)
    print("REGIME ANALYSIS - STEPS 2, 3, 4")
    print("RULEV3 Core Only (1,008 trades)")
    print("=" * 70)

    trades = load_trades(TRADES_FILE)
    labeled = label_trades(trades)
    print(f"Loaded {len(labeled)} trades")

    # =====================================================================
    # STEP 2: CONDITIONAL MOMENTUM BLOCK (TRENDING REGIME ONLY)
    # =====================================================================
    print()
    print("=" * 70)
    print("STEP 2: CONDITIONAL BLOCK (TRENDING REGIME ONLY)")
    print("=" * 70)
    print()
    print("Rule: IF regime==TRENDING AND consecutive_wins>=2 AND direction!=prev_winner -> BLOCK")
    print()

    # Identify blocked trades
    blocked_step2 = []
    kept_step2 = []

    for t in labeled:
        is_trending = t['market_regime'] == 'TRENDING'
        is_against = t['momentum'] == 'AGAINST'
        streak_2plus = t['consecutive_same'] >= 2

        if is_trending and is_against and streak_2plus:
            blocked_step2.append(t)
        else:
            kept_step2.append(t)

    baseline = compute_stats(labeled)
    baseline_dd = compute_drawdown(labeled)

    step2_stats = compute_stats(kept_step2)
    step2_dd = compute_drawdown(kept_step2)
    blocked2_stats = compute_stats(blocked_step2)

    print(f"{'Metric':<20} {'BASELINE':<18} {'AFTER BLOCK':<18} {'DELTA':<15}")
    print("-" * 70)
    print(f"{'Trade Count':<20} {baseline['count']:<18} {step2_stats['count']:<18} {step2_stats['count'] - baseline['count']:<15}")
    print(f"{'Total PnL':<20} ${baseline['total_pnl']:<17.2f} ${step2_stats['total_pnl']:<17.2f} ${step2_stats['total_pnl'] - baseline['total_pnl']:<14.2f}")
    print(f"{'Win Rate':<20} {baseline['win_rate']:<17.1f}% {step2_stats['win_rate']:<17.1f}% {step2_stats['win_rate'] - baseline['win_rate']:+<14.1f}%")
    print(f"{'Max Drawdown':<20} ${baseline_dd:<17.2f} ${step2_dd:<17.2f} ${step2_dd - baseline_dd:<14.2f}")
    print(f"{'PnL per Trade':<20} ${baseline['avg_pnl']:<17.4f} ${step2_stats['avg_pnl']:<17.4f} ${step2_stats['avg_pnl'] - baseline['avg_pnl']:<14.4f}")

    print()
    print(f"Blocked trades: {len(blocked_step2)}")
    print(f"Blocked stats: {blocked2_stats['win_rate']:.1f}% win | ${blocked2_stats['total_pnl']:.2f} PnL | ${blocked2_stats['avg_pnl']:.4f}/trade")

    # =====================================================================
    # STEP 3: EDGE-RAISE ALTERNATIVE (NO BLOCK)
    # =====================================================================
    print()
    print("=" * 70)
    print("STEP 3: EDGE-RAISE ALTERNATIVE (TRENDING REGIME ONLY)")
    print("=" * 70)
    print()
    print("Rule: In TRENDING, if against momentum after streak>=2, require edge >= 0.67 (+0.03)")
    print()

    # Simulate: trades that would have been filtered by higher edge requirement
    filtered_step3 = []
    kept_step3 = []

    for t in labeled:
        is_trending = t['market_regime'] == 'TRENDING'
        is_against = t['momentum'] == 'AGAINST'
        streak_2plus = t['consecutive_same'] >= 2
        edge = t.get('edge', 0)

        if is_trending and is_against and streak_2plus:
            # Would this trade pass the higher edge requirement?
            if edge >= 0.67:
                kept_step3.append(t)
            else:
                filtered_step3.append(t)
        else:
            kept_step3.append(t)

    step3_stats = compute_stats(kept_step3)
    step3_dd = compute_drawdown(kept_step3)
    filtered3_stats = compute_stats(filtered_step3)

    print(f"{'Metric':<20} {'BASELINE':<18} {'EDGE-RAISE':<18} {'DELTA':<15}")
    print("-" * 70)
    print(f"{'Trade Count':<20} {baseline['count']:<18} {step3_stats['count']:<18} {step3_stats['count'] - baseline['count']:<15}")
    print(f"{'Total PnL':<20} ${baseline['total_pnl']:<17.2f} ${step3_stats['total_pnl']:<17.2f} ${step3_stats['total_pnl'] - baseline['total_pnl']:<14.2f}")
    print(f"{'Win Rate':<20} {baseline['win_rate']:<17.1f}% {step3_stats['win_rate']:<17.1f}% {step3_stats['win_rate'] - baseline['win_rate']:+<14.1f}%")
    print(f"{'Max Drawdown':<20} ${baseline_dd:<17.2f} ${step3_dd:<17.2f} ${step3_dd - baseline_dd:<14.2f}")
    print(f"{'PnL per Trade':<20} ${baseline['avg_pnl']:<17.4f} ${step3_stats['avg_pnl']:<17.4f} ${step3_stats['avg_pnl'] - baseline['avg_pnl']:<14.4f}")

    print()
    print(f"Filtered trades (edge < 0.67): {len(filtered_step3)}")
    if filtered_step3:
        print(f"Filtered stats: {filtered3_stats['win_rate']:.1f}% win | ${filtered3_stats['total_pnl']:.2f} PnL | ${filtered3_stats['avg_pnl']:.4f}/trade")

    # Also test with +0.05 edge raise
    print()
    print("Alternative: Require edge >= 0.69 (+0.05)")

    filtered_step3b = []
    kept_step3b = []

    for t in labeled:
        is_trending = t['market_regime'] == 'TRENDING'
        is_against = t['momentum'] == 'AGAINST'
        streak_2plus = t['consecutive_same'] >= 2
        edge = t.get('edge', 0)

        if is_trending and is_against and streak_2plus:
            if edge >= 0.69:
                kept_step3b.append(t)
            else:
                filtered_step3b.append(t)
        else:
            kept_step3b.append(t)

    step3b_stats = compute_stats(kept_step3b)
    step3b_dd = compute_drawdown(kept_step3b)

    print(f"{'Trade Count':<20} {step3b_stats['count']:<18} (filtered: {len(filtered_step3b)})")
    print(f"{'Total PnL':<20} ${step3b_stats['total_pnl']:<17.2f} (delta: ${step3b_stats['total_pnl'] - baseline['total_pnl']:.2f})")
    print(f"{'Max Drawdown':<20} ${step3b_dd:<17.2f} (delta: ${step3b_dd - baseline_dd:.2f})")

    # =====================================================================
    # STEP 4: LOSS CLUSTER CHECK
    # =====================================================================
    print()
    print("=" * 70)
    print("STEP 4: TOP 20 LOSING TRADES ANALYSIS")
    print("=" * 70)
    print()

    # Sort by PnL (ascending = most negative first)
    sorted_by_loss = sorted(labeled, key=lambda x: x.get('pnl', 0))
    top_20_losses = sorted_by_loss[:20]

    print(f"{'#':<4} {'PnL':<10} {'Regime':<12} {'Momentum':<10} {'Streak':<8} {'Edge':<8} {'Direction':<8}")
    print("-" * 68)

    regime_counts = defaultdict(int)
    momentum_counts = defaultdict(int)
    against_trending_count = 0

    for i, t in enumerate(top_20_losses):
        regime = t['market_regime']
        momentum = t['momentum']
        streak = t['consecutive_same']
        edge = t.get('edge', 0)
        direction = t.get('direction', '')
        pnl = t.get('pnl', 0)

        regime_counts[regime] += 1
        momentum_counts[momentum] += 1

        if regime == 'TRENDING' and momentum == 'AGAINST':
            against_trending_count += 1

        marker = "*" if (regime == 'TRENDING' and momentum == 'AGAINST' and streak >= 2) else ""
        print(f"{i+1:<4} ${pnl:<9.2f} {regime:<12} {momentum:<10} {streak:<8} {edge:<8.3f} {direction:<8} {marker}")

    print()
    print("CLUSTERING ANALYSIS:")
    print("-" * 40)
    print(f"By Regime:")
    for r, c in sorted(regime_counts.items(), key=lambda x: -x[1]):
        pct = c / 20 * 100
        print(f"  {r:<15} {c}/20 ({pct:.0f}%)")

    print()
    print(f"By Momentum:")
    for m, c in sorted(momentum_counts.items(), key=lambda x: -x[1]):
        pct = c / 20 * 100
        print(f"  {m:<15} {c}/20 ({pct:.0f}%)")

    print()
    print(f"AGAINST momentum in TRENDING regime: {against_trending_count}/20 ({against_trending_count/20*100:.0f}%)")

    # Count how many match the proposed rule
    rule_matches = sum(1 for t in top_20_losses
                       if t['market_regime'] == 'TRENDING'
                       and t['momentum'] == 'AGAINST'
                       and t['consecutive_same'] >= 2)

    print(f"Matching proposed rule (TRENDING + AGAINST + streak>=2): {rule_matches}/20 ({rule_matches/20*100:.0f}%)")

    # =====================================================================
    # COMPARISON SUMMARY
    # =====================================================================
    print()
    print("=" * 70)
    print("FINAL COMPARISON")
    print("=" * 70)
    print()
    print(f"{'Strategy':<30} {'Trades':<10} {'PnL':<12} {'Win%':<10} {'MaxDD':<10} {'PnL/Trade':<12}")
    print("-" * 84)
    print(f"{'BASELINE RULEV3':<30} {baseline['count']:<10} ${baseline['total_pnl']:<11.2f} {baseline['win_rate']:<9.1f}% ${baseline_dd:<9.2f} ${baseline['avg_pnl']:<11.4f}")
    print(f"{'STEP2: Block in TRENDING':<30} {step2_stats['count']:<10} ${step2_stats['total_pnl']:<11.2f} {step2_stats['win_rate']:<9.1f}% ${step2_dd:<9.2f} ${step2_stats['avg_pnl']:<11.4f}")
    print(f"{'STEP3: Edge+0.03 in TRENDING':<30} {step3_stats['count']:<10} ${step3_stats['total_pnl']:<11.2f} {step3_stats['win_rate']:<9.1f}% ${step3_dd:<9.2f} ${step3_stats['avg_pnl']:<11.4f}")
    print(f"{'STEP3b: Edge+0.05 in TRENDING':<30} {step3b_stats['count']:<10} ${step3b_stats['total_pnl']:<11.2f} {step3b_stats['win_rate']:<9.1f}% ${step3b_dd:<9.2f} ${step3b_stats['avg_pnl']:<11.4f}")

    print()
    print("=" * 70)
    print("CONCLUSIONS (DATA-DRIVEN)")
    print("=" * 70)
    print()

    # Step 2 conclusion
    step2_pnl_delta = step2_stats['total_pnl'] - baseline['total_pnl']
    step2_dd_delta = step2_dd - baseline_dd
    if step2_pnl_delta > 0 and step2_dd_delta <= 0:
        print("STEP 2 (Block in TRENDING): IMPROVES performance")
    elif step2_pnl_delta < 0:
        print(f"STEP 2 (Block in TRENDING): HURTS performance (${step2_pnl_delta:.2f})")
    else:
        print("STEP 2 (Block in TRENDING): NEUTRAL")

    # Step 3 conclusion
    step3_pnl_delta = step3_stats['total_pnl'] - baseline['total_pnl']
    if step3_pnl_delta > 0:
        print(f"STEP 3 (Edge+0.03): IMPROVES performance (+${step3_pnl_delta:.2f})")
    elif step3_pnl_delta < 0:
        print(f"STEP 3 (Edge+0.03): HURTS performance (${step3_pnl_delta:.2f})")
    else:
        print("STEP 3 (Edge+0.03): NEUTRAL")

    # Step 4 conclusion
    if rule_matches >= 10:
        print(f"STEP 4: TOP LOSSES DO cluster in rule conditions ({rule_matches}/20)")
    else:
        print(f"STEP 4: TOP LOSSES do NOT cluster in rule conditions ({rule_matches}/20)")

    print()
    print("=" * 70)

if __name__ == "__main__":
    main()
