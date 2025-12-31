#!/usr/bin/env python3
"""
RULEV3+ Phase 1 - Market Classification Test
=============================================
READ-ONLY ANALYSIS - Phase 1 config LOCKED

Applies exact BTC-optimized Phase 1 rules to multiple markets.
Classification only - no tuning, no changes, no recommendations.

Markets: BTC (baseline), ETH, SOL, XRP
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
CORE_START = 3.0       # 3:00 elapsed
CORE_END = 3.5         # 3:29 elapsed (actually 3:30)

MARKETS = ['btc', 'eth', 'sol', 'xrp']

@dataclass
class Trade:
    session: str
    market: str
    direction: str
    edge: float
    ask: float
    spread: float
    won: bool
    pnl: float

@dataclass
class Result:
    market: str = ""
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

def simulate_session(session_path, market):
    """Simulate session with EXACT Phase 1 gates."""
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

        # GATE: CORE window only (3:00-3:29)
        if elapsed < CORE_START or elapsed >= CORE_END:
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

        # ALL GATES PASSED - Entry (max 1 per session)
        won = (direction == winner)
        shares = POSITION_SIZE / ask
        pnl = (1.0 - ask) * shares if won else -POSITION_SIZE

        return Trade(
            session=session_path.name,
            market=market,
            direction=direction,
            edge=edge,
            ask=ask,
            spread=spread,
            won=won,
            pnl=pnl
        )

    return None

def run_backtest(markets_dir, market):
    """Run backtest for specific market with Phase 1 rules."""
    result = Result(market=market.upper())

    prefix = f'{market}-updown-15m-'
    sessions = sorted([
        d for d in markets_dir.iterdir()
        if d.is_dir() and d.name.startswith(prefix)
    ])

    result.total_sessions = len(sessions)
    running_pnl = 0.0
    peak_pnl = 0.0

    for session_path in sessions:
        trade = simulate_session(session_path, market)

        if trade:
            result.total_trades += 1
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

def safe_div(a, b):
    return a / b if b > 0 else 0

def main():
    markets_dir = Path(__file__).parent.parent / 'markets_paper'
    log_dir = Path(__file__).parent.parent / 'backtest_full_logs' / 'market_classification'
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = log_dir / f'classification_{timestamp}.log'

    print('='*80)
    print('  RULEV3+ PHASE 1 - MARKET CLASSIFICATION TEST')
    print('  READ-ONLY ANALYSIS - Phase 1 config LOCKED')
    print('='*80)
    print()
    print('  LOCKED CONFIG (applied identically to all markets):')
    print(f'    Edge threshold: >= {EDGE_THRESHOLD}')
    print(f'    Safety cap:     <= {SAFETY_CAP}')
    print(f'    Spread gate:    <= {SPREAD_MAX}')
    print(f'    Position size:  ${POSITION_SIZE:.2f}')
    print(f'    Window:         CORE only (3:00-3:29)')
    print(f'    Max trades:     1 per session')
    print()

    # Run backtests
    results = {}
    for market in MARKETS:
        prefix = f'{market}-updown-15m-'
        count = len([d for d in markets_dir.iterdir() if d.is_dir() and d.name.startswith(prefix)])
        print(f'  Testing {market.upper()}... ({count} sessions)')
        result = run_backtest(markets_dir, market)
        results[market] = result
        wr = safe_div(result.wins * 100, result.total_trades)
        avg_pnl = safe_div(result.total_pnl, result.total_trades)
        print(f'    Trades: {result.total_trades}, WR: {wr:.2f}%, PnL: ${result.total_pnl:.2f}')
    print()

    # ================================================================
    # RAW RESULTS TABLE
    # ================================================================
    print('='*80)
    print('  1. RAW RESULTS TABLE')
    print('='*80)
    print()
    print(f"  {'Market':<8} {'Sessions':>10} {'Trades':>8} {'Tr/Sess':>8} {'WinRate':>8} {'AvgPnL':>10} {'TotalPnL':>12} {'MaxDD':>10} {'AvgAsk':>8} {'AvgSprd':>8}")
    print(f"  {'-'*90}")

    for market in MARKETS:
        r = results[market]
        tr_sess = safe_div(r.total_trades, r.total_sessions) * 100
        wr = safe_div(r.wins * 100, r.total_trades)
        avg_pnl = safe_div(r.total_pnl, r.total_trades)
        avg_ask = safe_div(r.sum_ask, r.total_trades)
        avg_spread = safe_div(r.sum_spread, r.total_trades)

        print(f"  {market.upper():<8} {r.total_sessions:>10} {r.total_trades:>8} {tr_sess:>7.1f}% {wr:>7.2f}% ${avg_pnl:>9.4f} ${r.total_pnl:>11.2f} ${r.max_drawdown:>9.2f} {avg_ask:>8.4f} {avg_spread:>8.4f}")

    print()

    # ================================================================
    # RELATIVE PERFORMANCE VS BTC
    # ================================================================
    btc = results['btc']
    btc_trades = btc.total_trades
    btc_avg_pnl = safe_div(btc.total_pnl, btc.total_trades)
    btc_max_dd = btc.max_drawdown

    print('='*80)
    print('  2. RELATIVE PERFORMANCE VS BTC (baseline = 1.00x)')
    print('='*80)
    print()
    print(f"  {'Market':<8} {'Trades':>12} {'AvgPnL':>12} {'MaxDD':>12} {'TotalPnL':>12}")
    print(f"  {'-'*60}")

    for market in MARKETS:
        r = results[market]
        avg_pnl = safe_div(r.total_pnl, r.total_trades)

        trades_rel = safe_div(r.total_trades, btc_trades)
        avgpnl_rel = safe_div(avg_pnl, btc_avg_pnl) if btc_avg_pnl != 0 else 0
        maxdd_rel = safe_div(r.max_drawdown, btc_max_dd) if btc_max_dd != 0 else 0
        totpnl_rel = safe_div(r.total_pnl, btc.total_pnl) if btc.total_pnl != 0 else 0

        if market == 'btc':
            print(f"  {market.upper():<8} {'1.00x':>12} {'1.00x':>12} {'1.00x':>12} {'1.00x':>12}  (baseline)")
        else:
            print(f"  {market.upper():<8} {trades_rel:>11.2f}x {avgpnl_rel:>11.2f}x {maxdd_rel:>11.2f}x {totpnl_rel:>11.2f}x")

    print()

    # ================================================================
    # CLASSIFICATION
    # ================================================================
    print('='*80)
    print('  3. MARKET CLASSIFICATION')
    print('='*80)
    print()

    btc_wr = safe_div(btc.wins * 100, btc.total_trades)

    classifications = {}
    for market in MARKETS:
        r = results[market]
        wr = safe_div(r.wins * 100, r.total_trades)
        avg_pnl = safe_div(r.total_pnl, r.total_trades)

        # Classification criteria
        similar_wr = abs(wr - btc_wr) <= 3.0
        similar_or_better_avg = avg_pnl >= btc_avg_pnl * 0.8
        acceptable_dd = r.max_drawdown <= btc_max_dd * 1.2
        profitable = r.total_pnl > 0
        low_avg = avg_pnl < 0.10
        high_dd = r.max_drawdown > btc_max_dd * 1.5

        if market == 'btc':
            classification = "BASELINE (reference)"
            reason = "BTC-optimized Phase 1 ruleset source"
        elif similar_wr and similar_or_better_avg and acceptable_dd:
            classification = "NATIVE FIT"
            reason = f"WR within 3%, AvgPnL >= 0.8x, DD <= 1.2x"
        elif profitable and not high_dd:
            classification = "EDGE EXISTS, NEEDS TIMING"
            reason = f"Profitable but {'lower AvgPnL' if not similar_or_better_avg else 'higher DD'}"
        else:
            classification = "STRUCTURAL MISFIT"
            reasons = []
            if not profitable:
                reasons.append("negative PnL")
            if low_avg:
                reasons.append("low AvgPnL")
            if high_dd:
                reasons.append("excessive DD")
            reason = ", ".join(reasons) if reasons else "poor overall fit"

        classifications[market] = classification

        print(f"  {market.upper()}:")
        print(f"    Classification: {classification}")
        print(f"    Reason: {reason}")
        print(f"    WR: {wr:.2f}% (BTC: {btc_wr:.2f}%)")
        print(f"    AvgPnL: ${avg_pnl:.4f} (BTC: ${btc_avg_pnl:.4f})")
        print(f"    MaxDD: ${r.max_drawdown:.2f} (BTC: ${btc_max_dd:.2f})")
        print()

    # ================================================================
    # SUMMARY TABLE
    # ================================================================
    print('='*80)
    print('  4. CLASSIFICATION SUMMARY')
    print('='*80)
    print()
    print(f"  {'Market':<8} {'Classification':<30} {'Profitable':>12} {'WR':>8} {'DD Ratio':>10}")
    print(f"  {'-'*70}")

    for market in MARKETS:
        r = results[market]
        wr = safe_div(r.wins * 100, r.total_trades)
        dd_ratio = safe_div(r.max_drawdown, btc_max_dd)
        profitable = "YES" if r.total_pnl > 0 else "NO"
        print(f"  {market.upper():<8} {classifications[market]:<30} {profitable:>12} {wr:>7.2f}% {dd_ratio:>9.2f}x")

    print()
    print('='*80)
    print('  NOTE: This is classification only. Phase 1 remains BTC-only.')
    print('  No parameter changes recommended or implemented.')
    print('='*80)

    # Save to log
    with open(log_file, 'w') as f:
        f.write(f"RULEV3+ Phase 1 - Market Classification Test\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n")
        f.write(f"="*60 + "\n\n")

        f.write("LOCKED CONFIG:\n")
        f.write(f"  Edge >= {EDGE_THRESHOLD}\n")
        f.write(f"  Ask <= {SAFETY_CAP}\n")
        f.write(f"  Spread <= {SPREAD_MAX}\n")
        f.write(f"  CORE: 3:00-3:29\n")
        f.write(f"  Max trades: 1/session\n\n")

        f.write("RAW RESULTS:\n")
        for market in MARKETS:
            r = results[market]
            wr = safe_div(r.wins * 100, r.total_trades)
            avg_pnl = safe_div(r.total_pnl, r.total_trades)
            f.write(f"  {market.upper()}: {r.total_trades} trades, {wr:.2f}% WR, ${avg_pnl:.4f} AvgPnL, ${r.total_pnl:.2f} Total, ${r.max_drawdown:.2f} DD\n")

        f.write("\nCLASSIFICATIONS:\n")
        for market in MARKETS:
            f.write(f"  {market.upper()}: {classifications[market]}\n")

    print(f"\n  Log saved to: {log_file}")

if __name__ == '__main__':
    main()
