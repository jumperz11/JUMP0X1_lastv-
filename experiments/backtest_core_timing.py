#!/usr/bin/env python3
"""
RULEV3+ Phase 1 - CORE Window Timing Analysis
==============================================
READ-ONLY ANALYSIS - Does not change Phase 1 config

Tests where inside CORE (3:00-3:29) the edge lives:
  A: Early CORE (3:00 - 3:10)
  B: Mid CORE   (3:10 - 3:20)
  C: Late CORE  (3:20 - 3:29)

All Phase 1 gates remain LOCKED:
  - Edge >= 0.64
  - Ask <= 0.72
  - Spread <= 0.02
  - Max 1 trade per session
"""

import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
from datetime import datetime

# ============================================================
# LOCKED RULEV3+ CONFIG (Phase 1) - DO NOT MODIFY
# ============================================================
EDGE_THRESHOLD = 0.64
SAFETY_CAP = 0.72
SPREAD_MAX = 0.02
POSITION_SIZE = 5.0

# Micro-windows to test (elapsed minutes)
WINDOWS = {
    'A_EARLY': (3.0, 3.0 + 10/60),      # 3:00 - 3:10
    'B_MID':   (3.0 + 10/60, 3.0 + 20/60),  # 3:10 - 3:20
    'C_LATE':  (3.0 + 20/60, 3.5),      # 3:20 - 3:29
}

@dataclass
class Trade:
    session: str
    direction: str
    edge: float
    ask: float
    bid: float
    spread: float
    elapsed_mins: float
    won: bool
    pnl: float

@dataclass
class Result:
    window_name: str = ""
    window_start: float = 0.0
    window_end: float = 0.0
    total_sessions: int = 0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    sum_ask: float = 0.0
    sum_spread: float = 0.0
    trades: List[Trade] = field(default_factory=list)

def get_elapsed_mins(tick):
    return 15.0 - tick.get('minutesLeft', 15)

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

def simulate_session(session_path, window_start, window_end):
    """Simulate session with Phase 1 gates, restricted to micro-window."""
    ticks_file = session_path / 'ticks.jsonl'
    if not ticks_file.exists():
        return None, None

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
        return None, None

    winner = get_winner(ticks)
    if not winner:
        return None, None

    for tick in ticks:
        elapsed = get_elapsed_mins(tick)

        # GATE: Micro-window only
        if elapsed < window_start or elapsed >= window_end:
            continue

        price = tick.get('price')
        best = tick.get('best')
        if not price or not best:
            continue

        up_mid = price.get('Up')
        down_mid = price.get('Down')
        if up_mid is None or down_mid is None:
            continue

        # Direction selection (unchanged)
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

        # GATE: BAD_BOOK (Phase 1)
        if spread < 0 or bid > ask:
            continue

        # GATE: EDGE_GATE (Phase 1 - LOCKED)
        if edge < EDGE_THRESHOLD:
            continue

        # GATE: PRICE_GATE (Phase 1 - LOCKED)
        if ask > SAFETY_CAP:
            continue

        # GATE: SPREAD_GATE (Phase 1 - LOCKED)
        if spread > SPREAD_MAX:
            continue

        # ALL GATES PASSED - Entry
        won = (direction == winner)
        shares = POSITION_SIZE / ask
        pnl = (1.0 - ask) * shares if won else -POSITION_SIZE

        trade = Trade(
            session=session_path.name,
            direction=direction,
            edge=edge,
            ask=ask,
            bid=bid,
            spread=spread,
            elapsed_mins=elapsed,
            won=won,
            pnl=pnl
        )
        return trade, winner

    return None, winner

def run_backtest(markets_dir, window_name, window_start, window_end):
    """Run backtest for specific micro-window."""
    result = Result(
        window_name=window_name,
        window_start=window_start,
        window_end=window_end
    )

    sessions = sorted([
        d for d in markets_dir.iterdir()
        if d.is_dir() and d.name.startswith('btc-updown-15m-')
    ])

    result.total_sessions = len(sessions)
    running_pnl = 0.0
    peak_pnl = 0.0

    for session_path in sessions:
        trade, _ = simulate_session(session_path, window_start, window_end)

        if trade:
            result.total_trades += 1
            result.trades.append(trade)

            if trade.won:
                result.wins += 1
            else:
                result.losses += 1

            result.total_pnl += trade.pnl
            result.sum_ask += trade.ask
            result.sum_spread += trade.spread

            running_pnl += trade.pnl
            if running_pnl > peak_pnl:
                peak_pnl = running_pnl
            dd = peak_pnl - running_pnl
            if dd > result.max_drawdown:
                result.max_drawdown = dd

    return result

def print_result(r):
    """Print result table for a single window."""
    def safe_div(a, b):
        return a / b if b > 0 else 0

    print(f"\n{'='*60}")
    print(f"  {r.window_name}: {r.window_start:.2f} - {r.window_end:.2f} mins elapsed")
    print(f"{'='*60}")
    print(f"  {'Metric':<25} {'Value':>15}")
    print(f"  {'-'*40}")
    print(f"  {'Total sessions':<25} {r.total_sessions:>15}")
    print(f"  {'Total trades':<25} {r.total_trades:>15}")
    print(f"  {'Trades per session':<25} {safe_div(r.total_trades, r.total_sessions):>15.4f}")
    print(f"  {'Wins':<25} {r.wins:>15}")
    print(f"  {'Losses':<25} {r.losses:>15}")
    print(f"  {'Win rate (%)':<25} {safe_div(r.wins * 100, r.total_trades):>15.2f}")
    print(f"  {'AvgPnL per trade ($)':<25} {safe_div(r.total_pnl, r.total_trades):>15.4f}")
    print(f"  {'Total PnL ($)':<25} {r.total_pnl:>15.2f}")
    print(f"  {'Max drawdown ($)':<25} {r.max_drawdown:>15.2f}")
    print(f"  {'Avg ask at entry':<25} {safe_div(r.sum_ask, r.total_trades):>15.4f}")
    print(f"  {'Avg spread at entry':<25} {safe_div(r.sum_spread, r.total_trades):>15.4f}")

def print_comparison(results):
    """Print comparison table."""
    def safe_div(a, b):
        return a / b if b > 0 else 0

    print(f"\n{'='*70}")
    print(f"  COMPARISON TABLE")
    print(f"{'='*70}")
    print(f"  {'Metric':<20} {'Early (A)':<15} {'Mid (B)':<15} {'Late (C)':<15}")
    print(f"  {'-'*60}")

    a, b, c = results

    print(f"  {'Trades':<20} {a.total_trades:<15} {b.total_trades:<15} {c.total_trades:<15}")
    print(f"  {'Win rate (%)':<20} {safe_div(a.wins*100, a.total_trades):<15.2f} {safe_div(b.wins*100, b.total_trades):<15.2f} {safe_div(c.wins*100, c.total_trades):<15.2f}")
    print(f"  {'AvgPnL ($)':<20} {safe_div(a.total_pnl, a.total_trades):<15.4f} {safe_div(b.total_pnl, b.total_trades):<15.4f} {safe_div(c.total_pnl, c.total_trades):<15.4f}")
    print(f"  {'Total PnL ($)':<20} {a.total_pnl:<15.2f} {b.total_pnl:<15.2f} {c.total_pnl:<15.2f}")
    print(f"  {'Max DD ($)':<20} {a.max_drawdown:<15.2f} {b.max_drawdown:<15.2f} {c.max_drawdown:<15.2f}")
    print(f"  {'Avg ask':<20} {safe_div(a.sum_ask, a.total_trades):<15.4f} {safe_div(b.sum_ask, b.total_trades):<15.4f} {safe_div(c.sum_ask, c.total_trades):<15.4f}")

def main():
    markets_dir = Path(__file__).parent.parent / 'markets_paper'
    log_dir = Path(__file__).parent.parent / 'backtest_full_logs' / 'core_timing'
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = log_dir / f'core_timing_{timestamp}.log'

    print('='*70)
    print('  RULEV3+ PHASE 1 - CORE WINDOW TIMING ANALYSIS')
    print('  READ-ONLY ANALYSIS - Phase 1 config unchanged')
    print('='*70)
    print()
    print('  LOCKED CONFIG:')
    print(f'    Edge threshold: >= {EDGE_THRESHOLD}')
    print(f'    Safety cap:     <= {SAFETY_CAP}')
    print(f'    Spread gate:    <= {SPREAD_MAX}')
    print(f'    Position size:  ${POSITION_SIZE:.2f}')
    print()
    print('  MICRO-WINDOWS:')
    for name, (start, end) in WINDOWS.items():
        mins_s = int(start)
        secs_s = int((start - mins_s) * 60)
        mins_e = int(end)
        secs_e = int((end - mins_e) * 60)
        print(f'    {name}: {mins_s}:{secs_s:02d} - {mins_e}:{secs_e:02d}')
    print()

    sessions = list(markets_dir.glob('btc-updown-15m-*'))
    print(f'  Loaded {len(sessions)} BTC sessions')
    print()

    # Run backtests for each window
    results = []
    for name, (start, end) in WINDOWS.items():
        print(f'  Running {name}...')
        result = run_backtest(markets_dir, name, start, end)
        results.append(result)
        print(f'    Trades: {result.total_trades}')

    # Print individual results
    for r in results:
        print_result(r)

    # Print comparison
    print_comparison(results)

    # Interpretation
    print(f"\n{'='*70}")
    print("  INTERPRETATION")
    print(f"{'='*70}")

    # Find best window
    best = max(results, key=lambda r: r.total_pnl / r.total_trades if r.total_trades > 0 else 0)
    worst = min(results, key=lambda r: r.total_pnl / r.total_trades if r.total_trades > 0 else 0)

    def wr(r):
        return r.wins * 100 / r.total_trades if r.total_trades > 0 else 0
    def avg_pnl(r):
        return r.total_pnl / r.total_trades if r.total_trades > 0 else 0

    print()
    print(f"  1. WHERE IS PERFORMANCE STRONGEST?")
    print(f"     {best.window_name} has highest AvgPnL: ${avg_pnl(best):.4f}")
    print()
    print(f"  2. DOES EDGE DECAY OR IMPROVE OVER CORE?")
    early_pnl = avg_pnl(results[0])
    late_pnl = avg_pnl(results[2])
    if early_pnl > late_pnl:
        print(f"     Edge DECAYS: Early ${early_pnl:.4f} > Late ${late_pnl:.4f}")
    elif late_pnl > early_pnl:
        print(f"     Edge IMPROVES: Late ${late_pnl:.4f} > Early ${early_pnl:.4f}")
    else:
        print(f"     Edge STABLE: Early ${early_pnl:.4f} ~ Late ${late_pnl:.4f}")
    print()
    print(f"  3. ARE RESULTS MATERIALLY DIFFERENT?")
    wr_range = max(wr(r) for r in results) - min(wr(r) for r in results)
    pnl_range = max(avg_pnl(r) for r in results) - min(avg_pnl(r) for r in results)

    if wr_range > 5 or pnl_range > 0.10:
        print(f"     YES - Material difference detected")
        print(f"     Win rate range: {wr_range:.2f}%")
        print(f"     AvgPnL range: ${pnl_range:.4f}")
    else:
        print(f"     NO - Results statistically similar")
        print(f"     Win rate range: {wr_range:.2f}%")
        print(f"     AvgPnL range: ${pnl_range:.4f}")

    print()
    print(f"  NOTE: This is analysis only. Phase 1 config remains LOCKED.")
    print(f"{'='*70}")

    # Save to log file
    with open(log_file, 'w') as f:
        f.write(f"RULEV3+ Phase 1 - CORE Window Timing Analysis\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n")
        f.write(f"="*60 + "\n\n")
        for r in results:
            f.write(f"{r.window_name}: {r.window_start:.2f}-{r.window_end:.2f} mins\n")
            f.write(f"  Trades: {r.total_trades}\n")
            f.write(f"  Win rate: {wr(r):.2f}%\n")
            f.write(f"  AvgPnL: ${avg_pnl(r):.4f}\n")
            f.write(f"  Total PnL: ${r.total_pnl:.2f}\n")
            f.write(f"  Max DD: ${r.max_drawdown:.2f}\n")
            f.write("\n")

    print(f"\n  Log saved to: {log_file}")

if __name__ == '__main__':
    main()
