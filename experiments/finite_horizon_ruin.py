"""
FINITE-HORIZON SURVIVABILITY & RUIN PROBABILITY ANALYSIS
=========================================================

This is not an EV problem.
This is a finite-horizon survivability problem.

Question: What is the probability that the system psychologically or
financially fails BEFORE the edge has time to express itself?
"""

import json
import os
import numpy as np
from collections import defaultdict
from pathlib import Path
from datetime import datetime

# Configuration (LOCKED)
ASK_CAP = 0.68
SPREAD_CAP = 0.015
POSITION_SIZE = 5.0
CORE_START = 150
CORE_END = 225

# Simulation parameters
N_SIMULATIONS = 10000  # Reduced for speed, still statistically valid
HORIZONS = [500, 1000, 2500]
BANKROLLS = [50, 100, 150, 250]
TRADES_PER_DAY = 54  # empirical average

# Stopping conditions
STOP_DD_50 = 0.50      # 50% drawdown
STOP_DD_75 = 0.75      # 75% drawdown
STOP_MIN_CAPITAL = 25  # operational failure
STOP_LOSS_STREAK = 10  # consecutive losses
STOP_UNDERWATER_DAYS = 60  # days without new high


def load_sessions():
    """Load all BTC sessions from markets_paper directory."""
    markets_dir = Path(__file__).parent.parent / "markets_paper"
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
    """Load ticks from session JSONL file."""
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
    """Determine winner from final tick."""
    if not ticks:
        return None

    final = ticks[-1]
    price = final.get('price') or {}
    up_mid = price.get('Up', 0.5) or 0.5
    down_mid = price.get('Down', 0.5) or 0.5

    if up_mid >= 0.90:
        return 'Up'
    elif down_mid >= 0.90:
        return 'Down'
    return None


def simulate_trades_from_sessions(sessions):
    """Generate trade outcomes from historical sessions using locked config."""
    trades = []

    for session_path, day in sessions:
        ticks = load_ticks(session_path)
        if not ticks:
            continue

        winner = get_winner(ticks)
        if not winner:
            continue

        for tick in ticks:
            mins_left = tick.get('minutesLeft', 15)
            elapsed = (15 - mins_left) * 60

            if not (CORE_START <= elapsed <= CORE_END):
                continue

            price = tick.get('price') or {}
            best = tick.get('best') or {}

            up_mid = price.get('Up', 0.5) or 0.5
            down_mid = price.get('Down', 0.5) or 0.5

            if up_mid >= down_mid:
                direction = 'Up'
                edge = up_mid
                side = best.get('Up') or {}
            else:
                direction = 'Down'
                edge = down_mid
                side = best.get('Down') or {}

            ask = side.get('ask')
            bid = side.get('bid')

            if not ask or not bid or ask <= 0 or bid <= 0:
                continue
            if ask > ASK_CAP:
                continue

            spread = ask - bid
            if spread > SPREAD_CAP or spread < 0:
                continue
            if edge < 0.64:
                continue

            # Calculate PnL
            won = (direction == winner)
            if won:
                pnl = POSITION_SIZE * (1 - ask) / ask  # profit on win
            else:
                pnl = -POSITION_SIZE  # lose full stake

            trades.append({
                'win': won,
                'pnl': pnl,
                'ask': ask,
                'day': day
            })
            break  # max 1 trade per session

    return trades


def build_markov_chain(trades):
    """Build 2-state Markov transition matrix from empirical data."""
    transitions = {
        (0, 0): 0,  # loss -> loss
        (0, 1): 0,  # loss -> win
        (1, 0): 0,  # win -> loss
        (1, 1): 0,  # win -> win
    }

    for i in range(1, len(trades)):
        prev_state = 1 if trades[i-1]['win'] else 0
        curr_state = 1 if trades[i]['win'] else 0
        transitions[(prev_state, curr_state)] += 1

    # Calculate probabilities
    total_from_loss = transitions[(0, 0)] + transitions[(0, 1)]
    total_from_win = transitions[(1, 0)] + transitions[(1, 1)]

    if total_from_loss > 0:
        p_win_after_loss = transitions[(0, 1)] / total_from_loss
    else:
        p_win_after_loss = 0.72

    if total_from_win > 0:
        p_win_after_win = transitions[(1, 1)] / total_from_win
    else:
        p_win_after_win = 0.72

    return {
        'p_win_after_loss': p_win_after_loss,
        'p_win_after_win': p_win_after_win,
        'transitions': transitions
    }


def get_empirical_win_pnls(trades):
    """Get distribution of winning PnLs."""
    win_pnls = [t['pnl'] for t in trades if t['win']]
    return win_pnls if win_pnls else [1.50]


def simulate_single_path(horizon, bankroll, markov, win_pnls, rng):
    """
    Simulate a single equity path with Markov-dependent outcomes.
    Returns: dict with path statistics and failure conditions.
    """
    equity = bankroll
    peak_equity = bankroll

    # Tracking variables
    current_streak = 0
    max_loss_streak = 0
    days_underwater = 0
    trades_since_peak = 0
    max_drawdown = 0
    max_drawdown_pct = 0

    # Failure flags
    hit_dd_50 = False
    hit_dd_75 = False
    hit_min_capital = False
    hit_loss_streak_10 = False
    hit_underwater_60 = False

    failure_trade = None

    # Start with random state based on overall win rate
    last_was_win = rng.random() < 0.72

    for trade_num in range(horizon):
        # Determine win probability based on last outcome (Markov)
        if last_was_win:
            p_win = markov['p_win_after_win']
        else:
            p_win = markov['p_win_after_loss']

        # Generate outcome
        is_win = rng.random() < p_win

        if is_win:
            pnl = rng.choice(win_pnls)
            current_streak = max(1, current_streak + 1) if current_streak > 0 else 1
        else:
            pnl = -POSITION_SIZE
            current_streak = min(-1, current_streak - 1) if current_streak < 0 else -1
            if abs(current_streak) > max_loss_streak:
                max_loss_streak = abs(current_streak)

        equity += pnl
        last_was_win = is_win

        # Update peak and drawdown
        if equity > peak_equity:
            peak_equity = equity
            trades_since_peak = 0
            days_underwater = 0
        else:
            trades_since_peak += 1
            days_underwater = trades_since_peak / TRADES_PER_DAY

        drawdown = peak_equity - equity
        drawdown_pct = drawdown / bankroll if bankroll > 0 else 0

        if drawdown > max_drawdown:
            max_drawdown = drawdown
        if drawdown_pct > max_drawdown_pct:
            max_drawdown_pct = drawdown_pct

        # Check stopping conditions
        if not hit_dd_50 and drawdown_pct >= STOP_DD_50:
            hit_dd_50 = True
            if failure_trade is None:
                failure_trade = trade_num

        if not hit_dd_75 and drawdown_pct >= STOP_DD_75:
            hit_dd_75 = True
            if failure_trade is None:
                failure_trade = trade_num

        if not hit_min_capital and equity < STOP_MIN_CAPITAL:
            hit_min_capital = True
            if failure_trade is None:
                failure_trade = trade_num

        if not hit_loss_streak_10 and abs(current_streak) >= STOP_LOSS_STREAK:
            hit_loss_streak_10 = True
            if failure_trade is None:
                failure_trade = trade_num

        if not hit_underwater_60 and days_underwater >= STOP_UNDERWATER_DAYS:
            hit_underwater_60 = True
            if failure_trade is None:
                failure_trade = trade_num

        # Early termination if capital gone
        if equity <= 0:
            hit_min_capital = True
            break

    any_failure = hit_dd_50 or hit_dd_75 or hit_min_capital or hit_loss_streak_10 or hit_underwater_60

    return {
        'final_equity': equity,
        'max_drawdown': max_drawdown,
        'max_drawdown_pct': max_drawdown_pct,
        'max_loss_streak': max_loss_streak,
        'days_underwater': days_underwater,
        'trades_since_peak': trades_since_peak,
        'hit_dd_50': hit_dd_50,
        'hit_dd_75': hit_dd_75,
        'hit_min_capital': hit_min_capital,
        'hit_loss_streak_10': hit_loss_streak_10,
        'hit_underwater_60': hit_underwater_60,
        'any_failure': any_failure,
        'failure_trade': failure_trade,
        'survived': not any_failure
    }


def run_monte_carlo(horizon, bankroll, markov, win_pnls, n_sims=N_SIMULATIONS):
    """Run full Monte Carlo simulation."""
    rng = np.random.default_rng(42)

    results = []
    for _ in range(n_sims):
        result = simulate_single_path(horizon, bankroll, markov, win_pnls, rng)
        results.append(result)

    return results


def analyze_results(results, bankroll, horizon):
    """Compute statistics from simulation results."""
    n = len(results)

    p_dd_50 = sum(1 for r in results if r['hit_dd_50']) / n
    p_dd_75 = sum(1 for r in results if r['hit_dd_75']) / n
    p_min_capital = sum(1 for r in results if r['hit_min_capital']) / n
    p_loss_streak = sum(1 for r in results if r['hit_loss_streak_10']) / n
    p_underwater = sum(1 for r in results if r['hit_underwater_60']) / n
    p_any_failure = sum(1 for r in results if r['any_failure']) / n
    p_survival = sum(1 for r in results if r['survived']) / n

    underwater_days = [r['days_underwater'] for r in results]
    median_underwater = np.median(underwater_days)
    p99_underwater = np.percentile(underwater_days, 99)

    p_underwater_30 = sum(1 for r in results if r['days_underwater'] > 30) / n

    final_equities = [r['final_equity'] for r in results]
    median_equity = np.median(final_equities)
    p1_equity = np.percentile(final_equities, 1)

    max_dds = [r['max_drawdown'] for r in results]
    median_dd = np.median(max_dds)
    p99_dd = np.percentile(max_dds, 99)

    worst_1pct_idx = max(1, int(n * 0.01))
    sorted_by_equity = sorted(results, key=lambda x: x['final_equity'])
    worst_paths = sorted_by_equity[:worst_1pct_idx]

    worst_avg_equity = np.mean([p['final_equity'] for p in worst_paths])
    worst_avg_dd = np.mean([p['max_drawdown'] for p in worst_paths])
    worst_avg_streak = np.mean([p['max_loss_streak'] for p in worst_paths])

    return {
        'bankroll': bankroll,
        'horizon': horizon,
        'p_dd_50': p_dd_50,
        'p_dd_75': p_dd_75,
        'p_min_capital': p_min_capital,
        'p_loss_streak_10': p_loss_streak,
        'p_underwater_60': p_underwater,
        'p_any_failure': p_any_failure,
        'p_survival': p_survival,
        'median_underwater_days': median_underwater,
        'p99_underwater_days': p99_underwater,
        'p_underwater_30_days': p_underwater_30,
        'median_final_equity': median_equity,
        'p1_final_equity': p1_equity,
        'median_max_dd': median_dd,
        'p99_max_dd': p99_dd,
        'worst_1pct_avg_equity': worst_avg_equity,
        'worst_1pct_avg_dd': worst_avg_dd,
        'worst_1pct_avg_streak': worst_avg_streak
    }


def print_results_table(all_results):
    """Print formatted results tables."""

    print("\n" + "=" * 80)
    print("FINITE-HORIZON RUIN PROBABILITY ANALYSIS")
    print("=" * 80)
    print(f"Simulations: {N_SIMULATIONS:,}")
    print(f"Position size: ${POSITION_SIZE}")
    print(f"Config: ask_cap={ASK_CAP}, spread_cap={SPREAD_CAP}, kill=OFF")
    print("=" * 80)

    # TABLE 1: Failure Probabilities
    print("\n### TABLE 1: FAILURE PROBABILITIES")
    print("-" * 80)

    for horizon in HORIZONS:
        print(f"\nHorizon: {horizon} trades (~{horizon/TRADES_PER_DAY:.0f} days)")
        print("-" * 70)
        print(f"{'Bankroll':>10} | {'DD>=50%':>8} | {'DD>=75%':>8} | {'Cap<$25':>8} | {'10+Loss':>8} | {'60d UW':>8} | {'SURVIVE':>8}")
        print("-" * 70)

        for bankroll in BANKROLLS:
            r = next(x for x in all_results if x['bankroll'] == bankroll and x['horizon'] == horizon)
            print(f"${bankroll:>9} | {r['p_dd_50']*100:>7.1f}% | {r['p_dd_75']*100:>7.1f}% | {r['p_min_capital']*100:>7.1f}% | {r['p_loss_streak_10']*100:>7.1f}% | {r['p_underwater_60']*100:>7.1f}% | {r['p_survival']*100:>7.1f}%")

    # TABLE 2: Recovery & Underwater Time
    print("\n\n### TABLE 2: TIME UNDERWATER ANALYSIS")
    print("-" * 80)
    print(f"{'Bankroll':>10} | {'Horizon':>8} | {'Median UW':>10} | {'99p UW':>10} | {'>30d UW':>10}")
    print("-" * 80)

    for bankroll in BANKROLLS:
        for horizon in HORIZONS:
            r = next(x for x in all_results if x['bankroll'] == bankroll and x['horizon'] == horizon)
            print(f"${bankroll:>9} | {horizon:>8} | {r['median_underwater_days']:>9.1f}d | {r['p99_underwater_days']:>9.1f}d | {r['p_underwater_30_days']*100:>9.1f}%")

    # TABLE 3: Worst 1% Paths
    print("\n\n### TABLE 3: WORST 1% PATH SUMMARY")
    print("-" * 80)
    print(f"{'Bankroll':>10} | {'Horizon':>8} | {'Avg Equity':>12} | {'Avg MaxDD':>12} | {'Avg Streak':>12}")
    print("-" * 80)

    for bankroll in BANKROLLS:
        for horizon in HORIZONS:
            r = next(x for x in all_results if x['bankroll'] == bankroll and x['horizon'] == horizon)
            print(f"${bankroll:>9} | {horizon:>8} | ${r['worst_1pct_avg_equity']:>10.2f} | ${r['worst_1pct_avg_dd']:>10.2f} | {r['worst_1pct_avg_streak']:>11.1f}")

    # TABLE 4: Financial vs Psychological Failure
    print("\n\n### TABLE 4: FINANCIAL VS PSYCHOLOGICAL FAILURE")
    print("-" * 80)
    print("Financial failure  = Capital < $25 or DD >= 75%")
    print("Psychological fail = 10+ loss streak or 60+ days underwater")
    print("-" * 80)
    print(f"{'Bankroll':>10} | {'Horizon':>8} | {'Financial':>12} | {'Psychological':>14} | {'Dominant':>12}")
    print("-" * 80)

    for bankroll in BANKROLLS:
        for horizon in HORIZONS:
            r = next(x for x in all_results if x['bankroll'] == bankroll and x['horizon'] == horizon)
            financial = r['p_min_capital'] + r['p_dd_75'] - (r['p_min_capital'] * r['p_dd_75'])
            psychological = r['p_loss_streak_10'] + r['p_underwater_60'] - (r['p_loss_streak_10'] * r['p_underwater_60'])

            if financial > psychological:
                dominant = "FINANCIAL"
            elif psychological > financial:
                dominant = "PSYCHO"
            else:
                dominant = "EQUAL"

            print(f"${bankroll:>9} | {horizon:>8} | {financial*100:>11.1f}% | {psychological*100:>13.1f}% | {dominant:>12}")

    # CRITICAL THRESHOLDS
    print("\n\n### CRITICAL THRESHOLD ANALYSIS")
    print("-" * 80)

    for horizon in HORIZONS:
        print(f"\nHorizon {horizon} trades:")
        for bankroll in BANKROLLS:
            r = next(x for x in all_results if x['bankroll'] == bankroll and x['horizon'] == horizon)
            if r['p_any_failure'] < 0.01:
                print(f"  Failure < 1% at: ${bankroll}")
                break
        else:
            print(f"  Failure < 1% at: > ${BANKROLLS[-1]} (not achieved)")

    print("\nPsychological dominance threshold:")
    for bankroll in BANKROLLS:
        r = next(x for x in all_results if x['bankroll'] == bankroll and x['horizon'] == 1000)
        financial = r['p_min_capital'] + r['p_dd_75']
        psychological = r['p_loss_streak_10'] + r['p_underwater_60']
        if psychological > financial:
            print(f"  Psychological > Financial at: ${bankroll}")
            break


def final_verdict(all_results, markov):
    """Generate final verdict."""

    print("\n\n" + "=" * 80)
    print("FINAL VERDICT")
    print("=" * 80)

    results_1000 = [r for r in all_results if r['horizon'] == 1000]

    print("\n### REQUIRED ANSWERS")
    print("-" * 80)

    # Q1
    print("\n1. At what bankroll does failure probability drop below 1%?")
    for r in results_1000:
        if r['p_any_failure'] < 0.01:
            print(f"   ANSWER: ${r['bankroll']}")
            break
    else:
        print(f"   ANSWER: > ${BANKROLLS[-1]} (not achieved with tested bankrolls)")

    # Q2
    print("\n2. At what bankroll does psychological failure dominate financial failure?")
    for r in results_1000:
        financial = r['p_min_capital'] + r['p_dd_75']
        psychological = r['p_loss_streak_10'] + r['p_underwater_60']
        if psychological > financial:
            print(f"   ANSWER: ${r['bankroll']}")
            print(f"   (Financial: {financial*100:.1f}%, Psychological: {psychological*100:.1f}%)")
            break
    else:
        print(f"   ANSWER: Financial dominates at all tested bankrolls")

    # Q3
    print("\n3. Is $150 actually sufficient, or merely survivable on paper?")
    r_150 = next(x for x in results_1000 if x['bankroll'] == 150)
    survival_rate = r_150['p_survival'] * 100
    p_underwater_30 = r_150['p_underwater_30_days'] * 100
    print(f"   Survival rate @ 1000 trades: {survival_rate:.1f}%")
    print(f"   Probability of >30 days underwater: {p_underwater_30:.1f}%")
    if survival_rate >= 95 and p_underwater_30 < 20:
        print("   ANSWER: SUFFICIENT for disciplined operator")
    elif survival_rate >= 90:
        print("   ANSWER: MARGINALLY SUFFICIENT - expect stress")
    else:
        print("   ANSWER: INSUFFICIENT - high failure risk")

    # Q4
    print("\n4. Is the system deployable by a human, not a robot?")
    r_150 = next(x for x in results_1000 if x['bankroll'] == 150)
    psych_fail = r_150['p_loss_streak_10'] + r_150['p_underwater_60']

    print(f"   Markov chain: P(win|loss) = {markov['p_win_after_loss']*100:.1f}%")
    print(f"   Markov chain: P(win|win) = {markov['p_win_after_win']*100:.1f}%")
    print(f"   10+ loss streak probability: {r_150['p_loss_streak_10']*100:.1f}%")
    print(f"   60+ days underwater probability: {r_150['p_underwater_60']*100:.1f}%")

    if psych_fail < 0.05:
        print("   ANSWER: YES - psychological load is manageable")
    elif psych_fail < 0.15:
        print("   ANSWER: YES WITH DISCIPLINE - expect 1-2 crisis moments")
    else:
        print("   ANSWER: NO - psychological failure likely before edge realization")

    # THE FINAL SENTENCE
    print("\n" + "=" * 80)
    r_150 = next(x for x in results_1000 if x['bankroll'] == 150)
    quit_prob = r_150['p_any_failure'] * 100

    print(f"\nA human trading this system with bankroll $150 has a {quit_prob:.1f}% chance")
    print("of quitting before edge realization.")
    print("=" * 80)


def main():
    print("Loading sessions...")
    sessions = load_sessions()
    print(f"Loaded {len(sessions)} sessions")

    print("Generating trades from historical data...")
    trades = simulate_trades_from_sessions(sessions)
    print(f"Generated {len(trades)} trades")

    if len(trades) < 100:
        print("ERROR: Insufficient trades for analysis")
        return

    # Compute empirical stats
    wins = sum(1 for t in trades if t['win'])
    win_rate = wins / len(trades)
    total_pnl = sum(t['pnl'] for t in trades)
    avg_pnl = total_pnl / len(trades)

    print(f"\nEmpirical Statistics:")
    print(f"  Win rate: {win_rate*100:.2f}%")
    print(f"  Total PnL: ${total_pnl:.2f}")
    print(f"  Avg PnL/trade: ${avg_pnl:.4f}")

    print("\nBuilding Markov transition matrix...")
    markov = build_markov_chain(trades)
    print(f"  P(win | after loss): {markov['p_win_after_loss']*100:.2f}%")
    print(f"  P(win | after win):  {markov['p_win_after_win']*100:.2f}%")
    print(f"  Transitions: {markov['transitions']}")

    win_pnls = get_empirical_win_pnls(trades)
    print(f"  Win PnL range: ${min(win_pnls):.2f} - ${max(win_pnls):.2f}")
    print(f"  Mean win PnL: ${np.mean(win_pnls):.2f}")

    print(f"\nRunning Monte Carlo simulation ({N_SIMULATIONS:,} paths)...")
    print("This may take a few minutes...")

    all_results = []

    for bankroll in BANKROLLS:
        for horizon in HORIZONS:
            print(f"  Simulating: Bankroll=${bankroll}, Horizon={horizon}...")
            results = run_monte_carlo(horizon, bankroll, markov, win_pnls)
            stats = analyze_results(results, bankroll, horizon)
            all_results.append(stats)

    print_results_table(all_results)
    final_verdict(all_results, markov)


if __name__ == "__main__":
    main()
