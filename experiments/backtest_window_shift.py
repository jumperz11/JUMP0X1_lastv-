#!/usr/bin/env python3
"""
RULEV3+ Phase 1 - Window Shift Analysis
========================================
READ-ONLY ANALYSIS - Phase 1 config LOCKED

Tests whether later entry within CORE improves trade quality.

Windows tested:
  - Baseline: 3:00 - 3:29 (Phase 1 reference)
  - Mid CORE: 3:10 - 3:29
  - Late CORE: 3:15 - 3:29

Only timing changes. All other parameters identical.
"""

import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import List
from datetime import datetime

# ============================================================
# LOCKED RULEV3+ CONFIG (Phase 1) - DO NOT MODIFY
# ============================================================
EDGE_THRESHOLD = 0.64
SAFETY_CAP = 0.72
SPREAD_MAX = 0.02
POSITION_SIZE = 5.0

# Test windows (elapsed minutes)
WINDOWS = {
    'BASELINE': (3.0, 3.5),           # 3:00 - 3:29 (Phase 1)
    'MID_CORE': (3.0 + 10/60, 3.5),   # 3:10 - 3:29
    'LATE_CORE': (3.0 + 15/60, 3.5),  # 3:15 - 3:29
}

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
    """Simulate session with Phase 1 gates, specific window."""
    ticks_file = session_path / 'ticks.jsonl'
    if not ticks_file.exists():
        return None

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
        return None

    winner = get_winner(ticks)
    if not winner:
        return None

    for tick in ticks:
        elapsed = get_elapsed_mins(tick)

        # GATE: Window only
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

        # GATE: BAD_BOOK
        if spread < 0 or bid > ask:
            continue

        # GATE: EDGE_GATE (LOCKED >= 0.64)
        if edge < EDGE_THRESHOLD:
            continue

        # GATE: PRICE_GATE (LOCKED <= 0.72)
        if ask > SAFETY_CAP:
            continue

        # GATE: SPREAD_GATE (LOCKED <= 0.02)
        if spread > SPREAD_MAX:
            continue

        # ALL GATES PASSED - Entry
        won = (direction == winner)
        shares = POSITION_SIZE / ask
        pnl = (1.0 - ask) * shares if won else -POSITION_SIZE

        return (ask, spread, won, pnl)

    return None

def run_backtest(markets_dir, window_name, window_start, window_end):
    """Run backtest for specific window."""
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
        trade = simulate_session(session_path, window_start, window_end)

        if trade:
            ask, spread, won, pnl = trade
            result.total_trades += 1

            if won:
                result.wins += 1
            else:
                result.losses += 1

            result.total_pnl += pnl
            result.sum_ask += ask
            result.sum_spread += spread

            running_pnl += pnl
            if running_pnl > peak_pnl:
                peak_pnl = running_pnl
            dd = peak_pnl - running_pnl
            if dd > result.max_drawdown:
                result.max_drawdown = dd

    return result

def safe_div(a, b):
    return a / b if b > 0 else 0

def mins_to_str(mins):
    """Convert decimal minutes to mm:ss format."""
    m = int(mins)
    s = int((mins - m) * 60)
    return f"{m}:{s:02d}"

def main():
    markets_dir = Path(__file__).parent.parent / 'markets_paper'
    log_dir = Path(__file__).parent.parent / 'backtest_full_logs' / 'window_shift'
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = log_dir / f'window_shift_{timestamp}.log'

    print('='*80)
    print('  RULEV3+ PHASE 1 - WINDOW SHIFT ANALYSIS')
    print('  READ-ONLY ANALYSIS - Phase 1 config LOCKED')
    print('='*80)
    print()
    print('  LOCKED CONFIG (unchanged across all tests):')
    print(f'    Edge threshold: >= {EDGE_THRESHOLD}')
    print(f'    Safety cap:     <= {SAFETY_CAP}')
    print(f'    Spread gate:    <= {SPREAD_MAX}')
    print(f'    Position size:  ${POSITION_SIZE:.2f}')
    print(f'    Max trades:     1 per session')
    print(f'    Market:         BTC only')
    print()
    print('  TEST VARIABLE: Entry window start time')
    print()
    print('  Windows:')
    for name, (start, end) in WINDOWS.items():
        print(f'    {name}: {mins_to_str(start)} - {mins_to_str(end)} elapsed')
    print()

    sessions = list(markets_dir.glob('btc-updown-15m-*'))
    print(f'  Dataset: {len(sessions)} BTC sessions')
    print()

    # Run backtests
    results = []
    for name, (start, end) in WINDOWS.items():
        print(f'  Running {name}...')
        result = run_backtest(markets_dir, name, start, end)
        results.append(result)
        wr = safe_div(result.wins * 100, result.total_trades)
        avg_pnl = safe_div(result.total_pnl, result.total_trades)
        print(f'    Trades: {result.total_trades}, WR: {wr:.2f}%, AvgPnL: ${avg_pnl:.4f}')

    print()

    # ================================================================
    # COMPARISON TABLE
    # ================================================================
    baseline = results[0]
    base_trades = baseline.total_trades
    base_avg_pnl = safe_div(baseline.total_pnl, baseline.total_trades)
    base_wr = safe_div(baseline.wins * 100, baseline.total_trades)
    base_dd = baseline.max_drawdown

    print('='*80)
    print('  COMPARISON TABLE')
    print('='*80)
    print()
    print(f"  {'Metric':<20} {'BASELINE':>15} {'MID_CORE':>15} {'LATE_CORE':>15}")
    print(f"  {'Window':<20} {'3:00-3:29':>15} {'3:10-3:29':>15} {'3:15-3:29':>15}")
    print(f"  {'-'*65}")

    # Trades
    print(f"  {'Trades':<20}", end="")
    for r in results:
        print(f" {r.total_trades:>14}", end="")
    print()

    # Trades per session
    print(f"  {'Trades/session':<20}", end="")
    for r in results:
        tr_sess = safe_div(r.total_trades, r.total_sessions) * 100
        print(f" {tr_sess:>13.2f}%", end="")
    print()

    # Win rate
    print(f"  {'Win rate':<20}", end="")
    for r in results:
        wr = safe_div(r.wins * 100, r.total_trades)
        print(f" {wr:>13.2f}%", end="")
    print()

    # AvgPnL
    print(f"  {'AvgPnL':<20}", end="")
    for r in results:
        avg_pnl = safe_div(r.total_pnl, r.total_trades)
        print(f" ${avg_pnl:>13.4f}", end="")
    print()

    # Total PnL
    print(f"  {'Total PnL':<20}", end="")
    for r in results:
        print(f" ${r.total_pnl:>13.2f}", end="")
    print()

    # Max DD
    print(f"  {'Max Drawdown':<20}", end="")
    for r in results:
        print(f" ${r.max_drawdown:>13.2f}", end="")
    print()

    # Avg Ask
    print(f"  {'Avg ask':<20}", end="")
    for r in results:
        avg_ask = safe_div(r.sum_ask, r.total_trades)
        print(f" {avg_ask:>14.4f}", end="")
    print()

    # Avg Spread
    print(f"  {'Avg spread':<20}", end="")
    for r in results:
        avg_spread = safe_div(r.sum_spread, r.total_trades)
        print(f" {avg_spread:>14.4f}", end="")
    print()

    print()

    # ================================================================
    # RELATIVE TO BASELINE
    # ================================================================
    print('='*80)
    print('  RELATIVE TO BASELINE (Phase 1 = 1.00x)')
    print('='*80)
    print()
    print(f"  {'Metric':<20} {'BASELINE':>15} {'MID_CORE':>15} {'LATE_CORE':>15}")
    print(f"  {'-'*65}")

    # Trades ratio
    print(f"  {'Trades':<20}", end="")
    for r in results:
        ratio = safe_div(r.total_trades, base_trades)
        print(f" {ratio:>14.2f}x", end="")
    print()

    # WR delta
    print(f"  {'Win rate delta':<20}", end="")
    for r in results:
        wr = safe_div(r.wins * 100, r.total_trades)
        delta = wr - base_wr
        print(f" {delta:>+13.2f}%", end="")
    print()

    # AvgPnL ratio
    print(f"  {'AvgPnL':<20}", end="")
    for r in results:
        avg_pnl = safe_div(r.total_pnl, r.total_trades)
        ratio = safe_div(avg_pnl, base_avg_pnl)
        print(f" {ratio:>14.2f}x", end="")
    print()

    # DD ratio
    print(f"  {'Max DD':<20}", end="")
    for r in results:
        ratio = safe_div(r.max_drawdown, base_dd)
        print(f" {ratio:>14.2f}x", end="")
    print()

    # PnL/DD ratio
    print(f"  {'PnL/DD ratio':<20}", end="")
    for r in results:
        pnl_dd = safe_div(r.total_pnl, r.max_drawdown)
        print(f" {pnl_dd:>14.2f}x", end="")
    print()

    print()

    # ================================================================
    # TRADE-OFF ANALYSIS
    # ================================================================
    print('='*80)
    print('  TRADE-OFF ANALYSIS')
    print('='*80)
    print()

    mid = results[1]
    late = results[2]

    mid_avg = safe_div(mid.total_pnl, mid.total_trades)
    late_avg = safe_div(late.total_pnl, late.total_trades)
    mid_wr = safe_div(mid.wins * 100, mid.total_trades)
    late_wr = safe_div(late.wins * 100, late.total_trades)

    # Trade frequency cost
    print("  1. TRADE FREQUENCY vs BASELINE")
    mid_loss = base_trades - mid.total_trades
    late_loss = base_trades - late.total_trades
    mid_pct = safe_div(mid_loss, base_trades) * 100
    late_pct = safe_div(late_loss, base_trades) * 100
    print(f"     MID_CORE:  -{mid_loss} trades (-{mid_pct:.1f}%)")
    print(f"     LATE_CORE: -{late_loss} trades (-{late_pct:.1f}%)")
    print()

    # AvgPnL gain
    print("  2. AVGPNL IMPROVEMENT vs BASELINE")
    mid_gain = mid_avg - base_avg_pnl
    late_gain = late_avg - base_avg_pnl
    mid_pct_gain = safe_div(mid_gain, base_avg_pnl) * 100
    late_pct_gain = safe_div(late_gain, base_avg_pnl) * 100
    print(f"     MID_CORE:  +${mid_gain:.4f} (+{mid_pct_gain:.1f}%)")
    print(f"     LATE_CORE: +${late_gain:.4f} (+{late_pct_gain:.1f}%)")
    print()

    # DD comparison
    print("  3. DRAWDOWN vs BASELINE")
    mid_dd_delta = mid.max_drawdown - base_dd
    late_dd_delta = late.max_drawdown - base_dd
    print(f"     MID_CORE:  {mid_dd_delta:+.2f} ({safe_div(mid.max_drawdown, base_dd):.2f}x)")
    print(f"     LATE_CORE: {late_dd_delta:+.2f} ({safe_div(late.max_drawdown, base_dd):.2f}x)")
    print()

    # Net PnL impact
    print("  4. TOTAL PNL vs BASELINE")
    mid_pnl_delta = mid.total_pnl - baseline.total_pnl
    late_pnl_delta = late.total_pnl - baseline.total_pnl
    print(f"     MID_CORE:  ${mid_pnl_delta:+.2f}")
    print(f"     LATE_CORE: ${late_pnl_delta:+.2f}")
    print()

    # ================================================================
    # ANSWERS TO KEY QUESTIONS
    # ================================================================
    print('='*80)
    print('  ANSWERS TO KEY QUESTIONS')
    print('='*80)
    print()

    # Q1: Does later entry improve risk-adjusted performance?
    base_risk_adj = safe_div(baseline.total_pnl, baseline.max_drawdown)
    mid_risk_adj = safe_div(mid.total_pnl, mid.max_drawdown)
    late_risk_adj = safe_div(late.total_pnl, late.max_drawdown)

    print("  Q1: Does later entry improve risk-adjusted performance?")
    print()
    print(f"      PnL/DD Ratios:")
    print(f"        BASELINE:  {base_risk_adj:.2f}x")
    print(f"        MID_CORE:  {mid_risk_adj:.2f}x")
    print(f"        LATE_CORE: {late_risk_adj:.2f}x")
    print()
    if late_risk_adj > base_risk_adj and mid_risk_adj > base_risk_adj:
        print("      ANSWER: YES - Both later windows improve risk-adjusted returns")
    elif late_risk_adj > base_risk_adj or mid_risk_adj > base_risk_adj:
        best = "LATE_CORE" if late_risk_adj > mid_risk_adj else "MID_CORE"
        print(f"      ANSWER: PARTIAL - {best} improves, other does not")
    else:
        print("      ANSWER: NO - Later entry does not improve risk-adjusted performance")
    print()

    # Q2: Is the improvement monotonic?
    print("  Q2: Is the improvement monotonic (later = better)?")
    print()
    print(f"      AvgPnL progression:")
    print(f"        BASELINE:  ${base_avg_pnl:.4f}")
    print(f"        MID_CORE:  ${mid_avg:.4f}")
    print(f"        LATE_CORE: ${late_avg:.4f}")
    print()
    if late_avg > mid_avg > base_avg_pnl:
        print("      ANSWER: YES - AvgPnL improves monotonically with later entry")
    elif late_avg > base_avg_pnl and mid_avg > base_avg_pnl:
        print("      ANSWER: PARTIAL - Both improve but not strictly monotonic")
    else:
        print("      ANSWER: NO - Improvement is not monotonic")
    print()

    # Q3: Source of improvement
    print("  Q3: Is gain from price quality or pure filtering?")
    print()
    base_ask = safe_div(baseline.sum_ask, baseline.total_trades)
    mid_ask = safe_div(mid.sum_ask, mid.total_trades)
    late_ask = safe_div(late.sum_ask, late.total_trades)
    base_spread = safe_div(baseline.sum_spread, baseline.total_trades)
    mid_spread = safe_div(mid.sum_spread, mid.total_trades)
    late_spread = safe_div(late.sum_spread, late.total_trades)

    print(f"      Avg Ask progression:")
    print(f"        BASELINE:  {base_ask:.4f}")
    print(f"        MID_CORE:  {mid_ask:.4f}")
    print(f"        LATE_CORE: {late_ask:.4f}")
    print()
    print(f"      Avg Spread progression:")
    print(f"        BASELINE:  {base_spread:.4f}")
    print(f"        MID_CORE:  {mid_spread:.4f}")
    print(f"        LATE_CORE: {late_spread:.4f}")
    print()

    ask_improves = late_ask < base_ask
    wr_improves = late_wr > base_wr

    if ask_improves and wr_improves:
        print("      ANSWER: BOTH - Lower ask prices AND higher win rate")
        print("               (Price discovery + signal quality)")
    elif wr_improves:
        print("      ANSWER: FILTERING - Higher win rate, similar prices")
        print("               (Later entry filters weak signals)")
    elif ask_improves:
        print("      ANSWER: PRICE QUALITY - Better prices, similar win rate")
        print("               (Later entry gets better fills)")
    else:
        print("      ANSWER: UNCLEAR - No clear improvement pattern")
    print()

    print('='*80)
    print('  CONCLUSION')
    print('='*80)
    print()
    print("  This analysis is INFORMATIONAL ONLY.")
    print("  Phase 1 config remains LOCKED at BASELINE (3:00-3:29).")
    print()
    print("  Key findings:")

    # Summarize findings
    if late_avg > base_avg_pnl:
        gain_pct = safe_div(late_avg - base_avg_pnl, base_avg_pnl) * 100
        cost_pct = safe_div(base_trades - late.total_trades, base_trades) * 100
        print(f"    - Later entry (3:15) improves AvgPnL by {gain_pct:.1f}%")
        print(f"    - But costs {cost_pct:.1f}% of trade opportunities")

    if late.total_pnl < baseline.total_pnl:
        print(f"    - Total PnL DECREASES despite higher AvgPnL")
        print(f"    - Trade volume loss outweighs quality gain")
    else:
        print(f"    - Total PnL increases with later entry")

    print()
    print("  NO CHANGES IMPLEMENTED.")
    print('='*80)

    # Save log
    with open(log_file, 'w') as f:
        f.write(f"RULEV3+ Phase 1 - Window Shift Analysis\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n")
        f.write("="*60 + "\n\n")

        f.write("CONFIG (LOCKED):\n")
        f.write(f"  Edge >= {EDGE_THRESHOLD}\n")
        f.write(f"  Ask <= {SAFETY_CAP}\n")
        f.write(f"  Spread <= {SPREAD_MAX}\n\n")

        f.write("RESULTS:\n")
        for r in results:
            wr = safe_div(r.wins * 100, r.total_trades)
            avg = safe_div(r.total_pnl, r.total_trades)
            f.write(f"  {r.window_name}: {r.total_trades} trades, {wr:.2f}% WR, ${avg:.4f} AvgPnL, ${r.total_pnl:.2f} Total, ${r.max_drawdown:.2f} DD\n")

    print(f"\n  Log saved to: {log_file}")

if __name__ == '__main__':
    main()
