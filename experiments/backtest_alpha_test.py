#!/usr/bin/env python3
"""
RULEV3+ Phase 1 Backtest - Spread Gate (No Alpha)
Tests: Remove alpha gate, add spread <= 0.02 gate
"""

import json
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

# ============================================================
# LOCKED RULEV3+ CONFIG (Phase 1)
# ============================================================
EDGE_THRESHOLD = 0.64
SAFETY_CAP = 0.72       # ask <= 0.72
SPREAD_MAX = 0.02       # spread <= 0.02 (using > for skip)
POSITION_SIZE = 5.0
CORE_START_MINS = 3.0
CORE_END_MINS = 3.5

@dataclass
class Trade:
    session: str
    direction: str
    edge: float
    ask: float
    bid: float
    spread: float
    won: bool
    pnl: float

@dataclass
class Result:
    total_sessions: int = 0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    sum_ask: float = 0.0
    sum_edge: float = 0.0
    sum_spread: float = 0.0
    bad_book_skips: int = 0
    spread_gate_skips: int = 0

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

def simulate_session(session_path, result):
    """Simulate session with Phase 1 gates (spread gate, no alpha)."""
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

        # GATE 1: CORE zone only (3:00 - 3:29)
        if elapsed < CORE_START_MINS or elapsed >= CORE_END_MINS:
            continue

        price = tick.get('price')
        best = tick.get('best')
        if not price or not best:
            continue

        up_mid = price.get('Up')
        down_mid = price.get('Down')
        if up_mid is None or down_mid is None:
            continue

        # Determine direction
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

        # GATE 2: BAD_BOOK sanity check
        if spread < 0 or bid > ask:
            result.bad_book_skips += 1
            continue

        # GATE 3: EDGE_GATE - edge >= threshold
        if edge < EDGE_THRESHOLD:
            continue

        # GATE 4: PRICE_GATE - ask <= safety_cap
        if ask > SAFETY_CAP:
            continue

        # GATE 5: SPREAD_GATE - spread <= 0.02 (skip if spread > 0.02)
        if spread > SPREAD_MAX:
            result.spread_gate_skips += 1
            continue

        # ALL GATES PASSED - Entry
        won = (direction == winner)
        shares = POSITION_SIZE / ask
        pnl = (1.0 - ask) * shares if won else -POSITION_SIZE

        return Trade(
            session=session_path.name,
            direction=direction,
            edge=edge,
            ask=ask,
            bid=bid,
            spread=spread,
            won=won,
            pnl=pnl
        )

    return None

def run_backtest(markets_dir):
    """Run Phase 1 backtest with spread gate."""
    result = Result()

    sessions = sorted([
        d for d in markets_dir.iterdir()
        if d.is_dir() and d.name.startswith('btc-updown-15m-')
    ])

    result.total_sessions = len(sessions)
    running_pnl = 0.0
    peak_pnl = 0.0

    for session_path in sessions:
        trade = simulate_session(session_path, result)

        if trade:
            result.total_trades += 1
            if trade.won:
                result.wins += 1
            else:
                result.losses += 1

            result.total_pnl += trade.pnl
            result.sum_ask += trade.ask
            result.sum_edge += trade.edge
            result.sum_spread += trade.spread

            running_pnl += trade.pnl
            if running_pnl > peak_pnl:
                peak_pnl = running_pnl
            dd = peak_pnl - running_pnl
            if dd > result.max_drawdown:
                result.max_drawdown = dd

    return result

def main():
    markets_dir = Path(__file__).parent.parent / 'markets_paper'

    print('=' * 70)
    print('RULEV3+ PHASE 1 BACKTEST - SPREAD GATE (NO ALPHA)')
    print('=' * 70)
    print()
    print('CONFIG:')
    print(f'  Zone:           CORE only ({CORE_START_MINS}-{CORE_END_MINS} mins)')
    print(f'  Edge threshold: >= {EDGE_THRESHOLD}')
    print(f'  Safety cap:     <= {SAFETY_CAP}')
    print(f'  Spread gate:    <= {SPREAD_MAX}')
    print(f'  Alpha gate:     REMOVED')
    print(f'  Position size:  ${POSITION_SIZE:.2f}')
    print()

    # Load sessions
    sessions = sorted([d for d in markets_dir.iterdir() if d.is_dir() and d.name.startswith('btc-updown-15m-')])
    print(f'Loaded BTC sessions: {len(sessions)}')
    print()

    # Run backtest
    print('Running backtest...')
    r = run_backtest(markets_dir)
    print(f'Backtest complete. Trades: {r.total_trades}')
    print()

    # Helper functions
    def avg(s, n):
        return s / n if n > 0 else 0
    def wr(w, t):
        return 100 * w / t if t > 0 else 0
    def ev(pnl, t):
        return pnl / t if t > 0 else 0
    def tps(t, s):
        return t / s if s > 0 else 0

    print('=' * 70)
    print('RESULTS')
    print('=' * 70)
    print()
    print(f"{'Metric':<30} {'Value':>20}")
    print('-' * 50)
    print(f"{'Total sessions':<30} {r.total_sessions:>20d}")
    print(f"{'Total trades':<30} {r.total_trades:>20d}")
    print(f"{'Trades per session':<30} {tps(r.total_trades, r.total_sessions):>20.4f}")
    print(f"{'Avg ask at entry':<30} {avg(r.sum_ask, r.total_trades):>20.4f}")
    print(f"{'Avg edge at entry':<30} {avg(r.sum_edge, r.total_trades):>20.4f}")
    print(f"{'Avg spread at entry':<30} {avg(r.sum_spread, r.total_trades):>20.4f}")
    print(f"{'Wins':<30} {r.wins:>20d}")
    print(f"{'Losses':<30} {r.losses:>20d}")
    print(f"{'Win rate (%)':<30} {wr(r.wins, r.total_trades):>20.2f}")
    print(f"{'EV per trade ($)':<30} {ev(r.total_pnl, r.total_trades):>20.4f}")
    print(f"{'Total PnL ($)':<30} {r.total_pnl:>20.2f}")
    print(f"{'Max drawdown ($)':<30} {r.max_drawdown:>20.2f}")
    print()
    print(f"{'BAD_BOOK skips':<30} {r.bad_book_skips:>20d}")
    print(f"{'SPREAD_GATE skips':<30} {r.spread_gate_skips:>20d}")
    print()

    # Summary
    print('=' * 70)
    print('SUMMARY')
    print('=' * 70)
    print()
    if r.total_trades == 0:
        print('NO TRADES - Check gates')
    elif ev(r.total_pnl, r.total_trades) < 0:
        print(f'EV NEGATIVE: ${ev(r.total_pnl, r.total_trades):.4f} per trade')
        print('Strategy is losing money on expectation')
    else:
        print(f'EV POSITIVE: ${ev(r.total_pnl, r.total_trades):.4f} per trade')
        print(f'Win Rate: {wr(r.wins, r.total_trades):.1f}%')
        print(f'Total PnL: ${r.total_pnl:.2f} over {r.total_trades} trades')
    print()

if __name__ == '__main__':
    main()
