#!/usr/bin/env python3
"""
RULEV3+ Phase 1 - Adversarial Gauntlet Backtest
================================================
READ-ONLY ANALYSIS - Does not change Phase 1 config

Red-team robustness testing:
1. Latency + Worst-Case Fill
2. Tick Drop / Disconnect
3. CORE Window Shift
4. Spread Trap Stability
5. Fee Sensitivity
"""

import json
import random
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple
from datetime import datetime

# ============================================================
# LOCKED RULEV3+ CONFIG (Phase 1) - DO NOT MODIFY
# ============================================================
EDGE_THRESHOLD = 0.64
SAFETY_CAP = 0.72
SPREAD_MAX = 0.02
POSITION_SIZE = 5.0
CORE_START_MINS = 3.0
CORE_END_MINS = 3.5

random.seed(42)  # Reproducibility

@dataclass
class Result:
    name: str = ""
    total_sessions: int = 0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    sum_ask: float = 0.0
    sum_spread: float = 0.0

    def wr(self):
        return self.wins * 100 / self.total_trades if self.total_trades > 0 else 0
    def avg_pnl(self):
        return self.total_pnl / self.total_trades if self.total_trades > 0 else 0
    def avg_ask(self):
        return self.sum_ask / self.total_trades if self.total_trades > 0 else 0
    def avg_spread(self):
        return self.sum_spread / self.total_trades if self.total_trades > 0 else 0
    def tps(self):
        return self.total_trades / self.total_sessions if self.total_sessions > 0 else 0

def load_sessions(markets_dir):
    """Load all BTC sessions."""
    sessions = {}
    session_dirs = sorted([
        d for d in markets_dir.iterdir()
        if d.is_dir() and d.name.startswith('btc-updown-15m-')
    ])

    for session_dir in session_dirs:
        ticks_file = session_dir / 'ticks.jsonl'
        if not ticks_file.exists():
            continue
        ticks = []
        with open(ticks_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        ticks.append(json.loads(line))
                    except:
                        continue
        if ticks:
            sessions[session_dir.name] = ticks
    return sessions

def get_elapsed_mins(tick):
    return 15.0 - tick.get('minutesLeft', 15)

def get_elapsed_secs(tick):
    return (15.0 - tick.get('minutesLeft', 15)) * 60

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

def check_gates(tick, core_start=CORE_START_MINS, core_end=CORE_END_MINS):
    """Check Phase 1 gates. Returns (valid, direction, edge, ask, bid, spread) or (False, ...)"""
    elapsed = get_elapsed_mins(tick)

    # GATE: CORE zone
    if elapsed < core_start or elapsed >= core_end:
        return False, None, 0, 0, 0, 0

    price = tick.get('price')
    best = tick.get('best')
    if not price or not best:
        return False, None, 0, 0, 0, 0

    up_mid = price.get('Up')
    down_mid = price.get('Down')
    if up_mid is None or down_mid is None:
        return False, None, 0, 0, 0, 0

    # Direction
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
        return False, None, 0, 0, 0, 0

    spread = ask - bid

    # GATE: BAD_BOOK
    if spread < 0 or bid > ask:
        return False, None, 0, 0, 0, 0

    # GATE: EDGE
    if edge < EDGE_THRESHOLD:
        return False, None, 0, 0, 0, 0

    # GATE: PRICE
    if ask > SAFETY_CAP:
        return False, None, 0, 0, 0, 0

    # GATE: SPREAD
    if spread > SPREAD_MAX:
        return False, None, 0, 0, 0, 0

    return True, direction, edge, ask, bid, spread

def calc_pnl(ask, won):
    shares = POSITION_SIZE / ask
    return (1.0 - ask) * shares if won else -POSITION_SIZE

# ============================================================
# BASELINE
# ============================================================
def run_baseline(sessions):
    """Run Phase 1 baseline."""
    result = Result(name="BASELINE")
    result.total_sessions = len(sessions)

    running_pnl = 0.0
    peak_pnl = 0.0

    for session_name, ticks in sessions.items():
        winner = get_winner(ticks)
        if not winner:
            continue

        for tick in ticks:
            valid, direction, edge, ask, bid, spread = check_gates(tick)
            if not valid:
                continue

            won = (direction == winner)
            pnl = calc_pnl(ask, won)

            result.total_trades += 1
            if won:
                result.wins += 1
            else:
                result.losses += 1
            result.total_pnl += pnl
            result.sum_ask += ask
            result.sum_spread += spread

            running_pnl += pnl
            peak_pnl = max(peak_pnl, running_pnl)
            result.max_drawdown = max(result.max_drawdown, peak_pnl - running_pnl)
            break  # Max 1 trade per session

    return result

# ============================================================
# VARIANT 1: LATENCY + WORST-CASE FILL
# ============================================================
def run_latency_test(sessions, delay_ms):
    """Simulate latency with worst-case fill."""
    result = Result(name=f"LATENCY_{delay_ms}ms")
    result.total_sessions = len(sessions)

    running_pnl = 0.0
    peak_pnl = 0.0
    delay_secs = delay_ms / 1000.0

    for session_name, ticks in sessions.items():
        winner = get_winner(ticks)
        if not winner:
            continue

        traded = False
        for i, tick in enumerate(ticks):
            if traded:
                break

            valid, direction, edge, ask, bid, spread = check_gates(tick)
            if not valid:
                continue

            # Signal triggered - find worst ask in delay window
            signal_time = get_elapsed_secs(tick)
            worst_ask = ask
            found_tick_in_window = False

            for j in range(i, len(ticks)):
                future_tick = ticks[j]
                future_time = get_elapsed_secs(future_tick)

                if future_time > signal_time + delay_secs:
                    break

                # Check if this tick has valid book for our direction
                best = future_tick.get('best', {})
                side = best.get(direction, {})
                future_ask = side.get('ask')

                if future_ask is not None:
                    found_tick_in_window = True
                    worst_ask = max(worst_ask, future_ask)

            if not found_tick_in_window:
                continue  # Skip trade - no tick in window

            # Fill at worst ask
            if worst_ask > SAFETY_CAP:
                continue  # Would exceed safety cap

            won = (direction == winner)
            pnl = calc_pnl(worst_ask, won)

            result.total_trades += 1
            if won:
                result.wins += 1
            else:
                result.losses += 1
            result.total_pnl += pnl
            result.sum_ask += worst_ask
            result.sum_spread += spread

            running_pnl += pnl
            peak_pnl = max(peak_pnl, running_pnl)
            result.max_drawdown = max(result.max_drawdown, peak_pnl - running_pnl)
            traded = True

    return result

# ============================================================
# VARIANT 2: TICK DROP / DISCONNECT
# ============================================================
def run_tick_drop_test(sessions, drop_pct):
    """Simulate random tick drops."""
    result = Result(name=f"DROP_{int(drop_pct*100)}%")
    result.total_sessions = len(sessions)

    running_pnl = 0.0
    peak_pnl = 0.0

    for session_name, ticks in sessions.items():
        winner = get_winner(ticks)
        if not winner:
            continue

        # Drop ticks randomly
        filtered_ticks = [t for t in ticks if random.random() > drop_pct]

        for tick in filtered_ticks:
            valid, direction, edge, ask, bid, spread = check_gates(tick)
            if not valid:
                continue

            won = (direction == winner)
            pnl = calc_pnl(ask, won)

            result.total_trades += 1
            if won:
                result.wins += 1
            else:
                result.losses += 1
            result.total_pnl += pnl
            result.sum_ask += ask
            result.sum_spread += spread

            running_pnl += pnl
            peak_pnl = max(peak_pnl, running_pnl)
            result.max_drawdown = max(result.max_drawdown, peak_pnl - running_pnl)
            break

    return result

def run_blackout_test(sessions, blackout_secs):
    """Simulate blackout periods."""
    result = Result(name=f"BLACKOUT_{blackout_secs}s")
    result.total_sessions = len(sessions)

    running_pnl = 0.0
    peak_pnl = 0.0

    for session_name, ticks in sessions.items():
        winner = get_winner(ticks)
        if not winner:
            continue

        # Random blackout start time (within CORE window)
        blackout_start = random.uniform(180, 210 - blackout_secs)  # 3:00-3:30 minus blackout
        blackout_end = blackout_start + blackout_secs

        for tick in ticks:
            elapsed_secs = get_elapsed_secs(tick)

            # Skip if in blackout
            if blackout_start <= elapsed_secs < blackout_end:
                continue

            valid, direction, edge, ask, bid, spread = check_gates(tick)
            if not valid:
                continue

            won = (direction == winner)
            pnl = calc_pnl(ask, won)

            result.total_trades += 1
            if won:
                result.wins += 1
            else:
                result.losses += 1
            result.total_pnl += pnl
            result.sum_ask += ask
            result.sum_spread += spread

            running_pnl += pnl
            peak_pnl = max(peak_pnl, running_pnl)
            result.max_drawdown = max(result.max_drawdown, peak_pnl - running_pnl)
            break

    return result

# ============================================================
# VARIANT 3: CORE WINDOW SHIFT
# ============================================================
def run_window_shift_test(sessions, shift_secs):
    """Shift CORE window boundaries."""
    shift_mins = shift_secs / 60.0
    new_start = CORE_START_MINS + shift_mins
    new_end = CORE_END_MINS + shift_mins

    result = Result(name=f"SHIFT_{shift_secs:+d}s")
    result.total_sessions = len(sessions)

    running_pnl = 0.0
    peak_pnl = 0.0

    for session_name, ticks in sessions.items():
        winner = get_winner(ticks)
        if not winner:
            continue

        for tick in ticks:
            valid, direction, edge, ask, bid, spread = check_gates(tick, new_start, new_end)
            if not valid:
                continue

            won = (direction == winner)
            pnl = calc_pnl(ask, won)

            result.total_trades += 1
            if won:
                result.wins += 1
            else:
                result.losses += 1
            result.total_pnl += pnl
            result.sum_ask += ask
            result.sum_spread += spread

            running_pnl += pnl
            peak_pnl = max(peak_pnl, running_pnl)
            result.max_drawdown = max(result.max_drawdown, peak_pnl - running_pnl)
            break

    return result

# ============================================================
# VARIANT 4: SPREAD TRAP STABILITY
# ============================================================
def run_spread_trap_test(sessions, n_consecutive):
    """Require N consecutive ticks with spread <= 0.02."""
    result = Result(name=f"SPREAD_TRAP_N{n_consecutive}")
    result.total_sessions = len(sessions)

    running_pnl = 0.0
    peak_pnl = 0.0

    for session_name, ticks in sessions.items():
        winner = get_winner(ticks)
        if not winner:
            continue

        consecutive_good = 0
        traded = False

        for tick in ticks:
            if traded:
                break

            valid, direction, edge, ask, bid, spread = check_gates(tick)

            if valid and spread <= SPREAD_MAX:
                consecutive_good += 1
            else:
                consecutive_good = 0
                continue

            if consecutive_good >= n_consecutive:
                won = (direction == winner)
                pnl = calc_pnl(ask, won)

                result.total_trades += 1
                if won:
                    result.wins += 1
                else:
                    result.losses += 1
                result.total_pnl += pnl
                result.sum_ask += ask
                result.sum_spread += spread

                running_pnl += pnl
                peak_pnl = max(peak_pnl, running_pnl)
                result.max_drawdown = max(result.max_drawdown, peak_pnl - running_pnl)
                traded = True

    return result

# ============================================================
# VARIANT 5: FEE SENSITIVITY
# ============================================================
def run_fee_test(sessions, fee_per_trade):
    """Add per-trade fee."""
    result = Result(name=f"FEE_${fee_per_trade:.2f}")
    result.total_sessions = len(sessions)

    running_pnl = 0.0
    peak_pnl = 0.0

    for session_name, ticks in sessions.items():
        winner = get_winner(ticks)
        if not winner:
            continue

        for tick in ticks:
            valid, direction, edge, ask, bid, spread = check_gates(tick)
            if not valid:
                continue

            won = (direction == winner)
            pnl = calc_pnl(ask, won) - fee_per_trade  # Subtract fee

            result.total_trades += 1
            if won:
                result.wins += 1
            else:
                result.losses += 1
            result.total_pnl += pnl
            result.sum_ask += ask
            result.sum_spread += spread

            running_pnl += pnl
            peak_pnl = max(peak_pnl, running_pnl)
            result.max_drawdown = max(result.max_drawdown, peak_pnl - running_pnl)
            break

    return result

# ============================================================
# OUTPUT
# ============================================================
def print_table(title, results):
    """Print comparison table."""
    print(f"\n{'='*80}")
    print(f"  {title}")
    print(f"{'='*80}")
    print(f"  {'Variant':<20} {'Trades':>8} {'WR%':>8} {'AvgPnL':>10} {'TotalPnL':>12} {'MaxDD':>10}")
    print(f"  {'-'*70}")
    for r in results:
        status = "+" if r.total_pnl > 0 else "-"
        print(f"  {r.name:<20} {r.total_trades:>8} {r.wr():>8.2f} ${r.avg_pnl():>9.4f} ${r.total_pnl:>11.2f} ${r.max_drawdown:>9.2f} {status}")

def main():
    markets_dir = Path(__file__).parent.parent / 'markets_paper'
    log_dir = Path(__file__).parent.parent / 'backtest_full_logs' / 'adversarial'
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    print('='*80)
    print('  RULEV3+ PHASE 1 - ADVERSARIAL GAUNTLET BACKTEST')
    print('  READ-ONLY ANALYSIS - Phase 1 config unchanged')
    print('='*80)
    print()
    print('  LOCKED CONFIG:')
    print(f'    Edge >= {EDGE_THRESHOLD}, Ask <= {SAFETY_CAP}, Spread <= {SPREAD_MAX}')
    print(f'    CORE: {CORE_START_MINS}-{CORE_END_MINS} mins, $5 size, 1 trade/session')
    print()

    # Load all sessions once
    print('  Loading sessions...')
    sessions = load_sessions(markets_dir)
    print(f'  Loaded {len(sessions)} BTC sessions')
    print()

    all_results = []

    # ============================================================
    # BASELINE
    # ============================================================
    print('  Running BASELINE...')
    baseline = run_baseline(sessions)
    all_results.append(baseline)
    print(f'    Trades: {baseline.total_trades}, WR: {baseline.wr():.2f}%, PnL: ${baseline.total_pnl:.2f}')

    # ============================================================
    # VARIANT 1: LATENCY
    # ============================================================
    print('\n  Running VARIANT 1: Latency + Worst-Case Fill...')
    latency_results = [baseline]
    for delay in [250, 500, 1000, 2000]:
        print(f'    Testing {delay}ms delay...')
        r = run_latency_test(sessions, delay)
        latency_results.append(r)
        all_results.append(r)
    print_table("VARIANT 1: LATENCY + WORST-CASE FILL", latency_results)

    # ============================================================
    # VARIANT 2: TICK DROP
    # ============================================================
    print('\n  Running VARIANT 2: Tick Drop / Disconnect...')
    drop_results = [baseline]

    # Random drops
    for drop_pct in [0.30, 0.60]:
        print(f'    Testing {int(drop_pct*100)}% drop...')
        r = run_tick_drop_test(sessions, drop_pct)
        drop_results.append(r)
        all_results.append(r)

    # Blackouts
    for blackout in [3, 5, 10]:
        print(f'    Testing {blackout}s blackout...')
        r = run_blackout_test(sessions, blackout)
        drop_results.append(r)
        all_results.append(r)

    print_table("VARIANT 2: TICK DROP / DISCONNECT", drop_results)

    # ============================================================
    # VARIANT 3: WINDOW SHIFT
    # ============================================================
    print('\n  Running VARIANT 3: CORE Window Shift...')
    shift_results = [baseline]
    for shift in [-2, -1, 1, 2, 5]:
        print(f'    Testing {shift:+d}s shift...')
        r = run_window_shift_test(sessions, shift)
        shift_results.append(r)
        all_results.append(r)
    print_table("VARIANT 3: CORE WINDOW SHIFT", shift_results)

    # ============================================================
    # VARIANT 4: SPREAD TRAP
    # ============================================================
    print('\n  Running VARIANT 4: Spread Trap Stability...')
    spread_results = [baseline]
    for n in [1, 2, 3]:
        print(f'    Testing N={n} consecutive...')
        r = run_spread_trap_test(sessions, n)
        spread_results.append(r)
        all_results.append(r)
    print_table("VARIANT 4: SPREAD TRAP STABILITY", spread_results)

    # ============================================================
    # VARIANT 5: FEE SENSITIVITY
    # ============================================================
    print('\n  Running VARIANT 5: Fee Sensitivity...')
    fee_results = [baseline]
    for fee in [0.01, 0.02, 0.05]:
        print(f'    Testing ${fee:.2f} fee...')
        r = run_fee_test(sessions, fee)
        fee_results.append(r)
        all_results.append(r)
    print_table("VARIANT 5: FEE SENSITIVITY", fee_results)

    # ============================================================
    # SURVIVABILITY SUMMARY
    # ============================================================
    print(f"\n{'='*80}")
    print("  SURVIVABILITY SUMMARY")
    print(f"{'='*80}")

    positive = [r for r in all_results if r.total_pnl > 0]
    negative = [r for r in all_results if r.total_pnl <= 0]

    print(f"\n  POSITIVE (PnL > 0): {len(positive)}/{len(all_results)}")
    print(f"  {'-'*40}")
    for r in sorted(positive, key=lambda x: x.total_pnl, reverse=True):
        print(f"    {r.name:<25} ${r.total_pnl:>10.2f}")

    print(f"\n  NEGATIVE (PnL <= 0): {len(negative)}/{len(all_results)}")
    print(f"  {'-'*40}")
    for r in sorted(negative, key=lambda x: x.total_pnl):
        print(f"    {r.name:<25} ${r.total_pnl:>10.2f}")

    # Sensitivity analysis
    print(f"\n  SENSITIVITY ANALYSIS")
    print(f"  {'-'*40}")

    # Latency impact
    latency_drop = baseline.total_pnl - latency_results[-1].total_pnl  # 2000ms
    latency_pct = (latency_drop / baseline.total_pnl) * 100 if baseline.total_pnl > 0 else 0

    # Tick drop impact
    drop_impact = baseline.total_pnl - drop_results[2].total_pnl  # 60% drop
    drop_pct = (drop_impact / baseline.total_pnl) * 100 if baseline.total_pnl > 0 else 0

    # Window shift impact (worst)
    worst_shift = min(shift_results, key=lambda x: x.total_pnl)
    shift_impact = baseline.total_pnl - worst_shift.total_pnl
    shift_pct = (shift_impact / baseline.total_pnl) * 100 if baseline.total_pnl > 0 else 0

    print(f"    Latency (2000ms):      -{latency_pct:.1f}% PnL impact")
    print(f"    Tick Drop (60%):       -{drop_pct:.1f}% PnL impact")
    print(f"    Window Shift (worst):  -{shift_pct:.1f}% PnL impact ({worst_shift.name})")

    # Biggest driver
    impacts = [
        ("Latency", latency_pct),
        ("Tick Drop", drop_pct),
        ("Window Shift", shift_pct)
    ]
    biggest = max(impacts, key=lambda x: x[1])
    print(f"\n    BIGGEST SENSITIVITY: {biggest[0]} ({biggest[1]:.1f}% impact)")

    # Kill conditions
    print(f"\n  KILL CONDITIONS (flips to negative)")
    print(f"  {'-'*40}")
    if negative:
        for r in negative:
            print(f"    {r.name}")
    else:
        print(f"    None - strategy survives all tests")

    print(f"\n{'='*80}")
    print("  ANALYSIS COMPLETE - Phase 1 config unchanged")
    print(f"{'='*80}")

    # Save to log
    log_file = log_dir / f'adversarial_{timestamp}.log'
    with open(log_file, 'w') as f:
        f.write(f"RULEV3+ Phase 1 - Adversarial Gauntlet\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n")
        f.write(f"Sessions: {len(sessions)}\n\n")

        f.write("RESULTS:\n")
        for r in all_results:
            status = "PASS" if r.total_pnl > 0 else "FAIL"
            f.write(f"{r.name}: {r.total_trades} trades, {r.wr():.2f}% WR, ${r.total_pnl:.2f} PnL [{status}]\n")

    print(f"\n  Log saved to: {log_file}")

if __name__ == '__main__':
    main()
