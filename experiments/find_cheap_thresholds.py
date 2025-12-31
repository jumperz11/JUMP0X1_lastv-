#!/usr/bin/env python3
"""
Find what CHEAP thresholds actually produce trades.
"""

import json
from pathlib import Path
from collections import defaultdict

SPREAD_MAX = 0.02
CORE_START = 150
CORE_END = 225

def get_elapsed_secs(tick):
    mins_left = tick.get('minutesLeft', 15)
    return (15 - mins_left) * 60

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

def analyze_session(session_path):
    """Find first valid entry at various ask thresholds."""
    ticks_file = session_path / 'ticks.jsonl'
    if not ticks_file.exists():
        return None, None

    ticks = []
    entries = []

    with open(ticks_file, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                tick = json.loads(line)
                ticks.append(tick)
            except:
                continue

    winner = get_winner(ticks)
    if not winner:
        return None, None

    for tick in ticks:
        elapsed = get_elapsed_secs(tick)
        if elapsed < CORE_START or elapsed > CORE_END:
            continue

        price = tick.get('price')
        best = tick.get('best')
        if not price or not best:
            continue

        up_mid = price.get('Up')
        down_mid = price.get('Down')
        if up_mid is None or down_mid is None:
            continue

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
        if spread < 0 or spread > SPREAD_MAX:
            continue

        won = direction == winner
        entries.append({
            'elapsed': elapsed,
            'ask': ask,
            'edge': edge,
            'spread': spread,
            'direction': direction,
            'won': won
        })

    return entries, winner


def test_threshold(sessions_data, ask_max, edge_min):
    """Test a specific threshold combination."""
    trades = 0
    wins = 0
    pnl = 0.0
    size = 2.0

    for entries, winner in sessions_data:
        if not entries:
            continue

        # Find first entry that passes
        for e in entries:
            if e['ask'] <= ask_max and e['edge'] >= edge_min:
                trades += 1
                if e['won']:
                    wins += 1
                    pnl += (1.0 - e['ask']) * (size / e['ask'])
                else:
                    pnl -= size
                break

    return trades, wins, pnl


def main():
    markets_dir = Path(__file__).parent.parent / 'markets_paper'
    sessions = sorted([
        d for d in markets_dir.iterdir()
        if d.is_dir() and d.name.startswith('btc-updown-15m-')
    ])

    print(f"Loading {len(sessions)} sessions...")

    # Collect data
    sessions_data = []
    for sp in sessions:
        entries, winner = analyze_session(sp)
        sessions_data.append((entries, winner))

    print("Done.\n")

    # Test various thresholds
    print("=" * 70)
    print("  CHEAP THRESHOLD ANALYSIS (CORE zone only)")
    print("=" * 70)
    print()
    print(f"  {'ask_max':<10} {'edge_min':<10} {'trades':>8} {'WR%':>8} {'PnL':>10}")
    print("-" * 70)

    # Different ask thresholds
    for ask_max in [0.54, 0.56, 0.58, 0.60, 0.62, 0.64]:
        for edge_min in [0.54, 0.56, 0.58, 0.60, 0.62, 0.64]:
            trades, wins, pnl = test_threshold(sessions_data, ask_max, edge_min)
            if trades > 0:
                wr = wins * 100 / trades
                print(f"  {ask_max:<10.2f} {edge_min:<10.2f} {trades:>8} {wr:>7.1f}% ${pnl:>9.2f}")

    # Find the "edge >= ask" pattern (asking for value)
    print()
    print("=" * 70)
    print("  VALUE PATTERN: edge >= ask + margin")
    print("=" * 70)
    print()

    for ask_max in [0.58, 0.60, 0.62]:
        for margin in [0.00, 0.02, 0.04]:
            trades = 0
            wins = 0
            pnl = 0.0
            size = 2.0

            for entries, winner in sessions_data:
                if not entries:
                    continue
                for e in entries:
                    if e['ask'] <= ask_max and e['edge'] >= e['ask'] + margin:
                        trades += 1
                        if e['won']:
                            wins += 1
                            pnl += (1.0 - e['ask']) * (size / e['ask'])
                        else:
                            pnl -= size
                        break

            if trades > 0:
                wr = wins * 100 / trades
                print(f"  ask <= {ask_max}, edge >= ask + {margin}: {trades} trades, {wr:.1f}% WR, ${pnl:.2f}")


if __name__ == '__main__':
    main()
