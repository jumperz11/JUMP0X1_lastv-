#!/usr/bin/env python3
"""
Deep analysis of crossings vs outcomes with proper PnL.
"""

import json
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass
from typing import List

WINDOW_SECONDS = 300
CORE_START_SECS = 150
CORE_END_SECS = 225
POSITION_SIZE = 5.0


@dataclass
class PricePoint:
    timestamp: float
    price: float


def get_elapsed_secs(tick) -> float:
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


def compute_crossings(price_buffer, current_time, move_threshold=0.001):
    if len(price_buffer) < 10:
        return 0

    window_start = current_time - WINDOW_SECONDS
    window_points = [p for p in price_buffer if p.timestamp >= window_start]

    if len(window_points) < 10:
        return 0

    last_direction = None
    last_anchor = window_points[0].price
    crossings = 0

    for point in window_points[1:]:
        move = point.price - last_anchor

        if abs(move) >= move_threshold:
            current_direction = "UP" if move > 0 else "DOWN"

            if last_direction is not None and current_direction != last_direction:
                crossings += 1

            last_direction = current_direction
            last_anchor = point.price

    return crossings


def analyze_session(session_path):
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

    price_buffer = []
    last_record_time = 0.0

    for tick in ticks:
        elapsed_secs = get_elapsed_secs(tick)

        price = tick.get('price')
        if price:
            up_mid = price.get('Up')
            if up_mid is not None and up_mid > 0:
                if elapsed_secs - last_record_time >= 1.0:
                    price_buffer.append(PricePoint(timestamp=elapsed_secs, price=up_mid))
                    last_record_time = elapsed_secs

        if CORE_START_SECS <= elapsed_secs <= CORE_END_SECS:
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
            if ask is None or bid is None or ask <= 0:
                continue

            spread = ask - bid
            if spread < 0 or bid > ask:
                continue

            # V3.1 gates
            if ask <= 0.66:
                req = 0.64
            elif ask <= 0.69:
                req = 0.67
            else:
                req = 0.70

            if edge < req or ask > 0.72 or spread > 0.02:
                continue

            crossings = compute_crossings(price_buffer, elapsed_secs)
            won = (direction == winner)
            shares = POSITION_SIZE / ask
            pnl = (1.0 - ask) * shares if won else -POSITION_SIZE

            return {
                "crossings": crossings,
                "won": won,
                "pnl": pnl,
                "ask": ask
            }

    return None


def main():
    markets_dir = Path(__file__).parent.parent / 'markets_paper'

    sessions = sorted([
        d for d in markets_dir.iterdir()
        if d.is_dir() and d.name.startswith('btc-updown-15m-')
    ])

    print(f"Analyzing {len(sessions)} sessions with ACTUAL PnL...")

    # Collect data
    trades_by_crossings = defaultdict(list)

    for session_path in sessions:
        result = analyze_session(session_path)
        if result:
            trades_by_crossings[result["crossings"]].append(result)

    print()
    print("=" * 80)
    print("  CROSSINGS DISTRIBUTION WITH ACTUAL PNL")
    print("=" * 80)
    print()
    print(f"  {'Cross':>6} {'Trades':>8} {'Wins':>6} {'Losses':>6} {'WinRate':>8} {'PnL':>12} {'AvgPnL':>10}")
    print(f"  {'-'*66}")

    total_by_regime = {
        "STABLE": {"trades": 0, "wins": 0, "pnl": 0},
        "NEUTRAL": {"trades": 0, "wins": 0, "pnl": 0},
        "CHOPPY": {"trades": 0, "wins": 0, "pnl": 0},
    }

    for c in sorted(trades_by_crossings.keys()):
        trades = trades_by_crossings[c]
        wins = sum(1 for t in trades if t["won"])
        losses = len(trades) - wins
        total_pnl = sum(t["pnl"] for t in trades)
        avg_pnl = total_pnl / len(trades) if trades else 0
        wr = wins * 100 / len(trades) if trades else 0

        regime = "STABLE" if c <= 2 else ("CHOPPY" if c >= 6 else "NEUTRAL")
        marker = " *" if c >= 10 else ""  # mark high crossings

        print(f"  {c:>6} {len(trades):>8} {wins:>6} {losses:>6} {wr:>7.1f}% ${total_pnl:>10.2f} ${avg_pnl:>8.4f}{marker}")

        total_by_regime[regime]["trades"] += len(trades)
        total_by_regime[regime]["wins"] += wins
        total_by_regime[regime]["pnl"] += total_pnl

    # Regime summary
    print()
    print("=" * 80)
    print("  REGIME SUMMARY")
    print("=" * 80)
    print()
    print(f"  {'Regime':<15} {'Trades':>8} {'Wins':>8} {'WinRate':>10} {'TotalPnL':>12} {'AvgPnL':>10}")
    print(f"  {'-'*65}")

    for regime in ["STABLE", "NEUTRAL", "CHOPPY"]:
        data = total_by_regime[regime]
        wr = data["wins"] * 100 / data["trades"] if data["trades"] > 0 else 0
        avg = data["pnl"] / data["trades"] if data["trades"] > 0 else 0
        thresholds = "<=2" if regime == "STABLE" else (">=6" if regime == "CHOPPY" else "3-5")
        print(f"  {regime} ({thresholds})"[:15].ljust(15) + f" {data['trades']:>8} {data['wins']:>8} {wr:>9.1f}% ${data['pnl']:>10.2f} ${avg:>8.4f}")

    # High crossings analysis (10+)
    high_crossings = [t for c, trades in trades_by_crossings.items() if c >= 10 for t in trades]
    if high_crossings:
        wins = sum(1 for t in high_crossings if t["won"])
        pnl = sum(t["pnl"] for t in high_crossings)
        wr = wins * 100 / len(high_crossings)
        print()
        print(f"  HIGH CHOP (>=10): {len(high_crossings)} trades, {wr:.1f}% WR, ${pnl:.2f} PnL")

    # Very high crossings (12+)
    very_high = [t for c, trades in trades_by_crossings.items() if c >= 12 for t in trades]
    if very_high:
        wins = sum(1 for t in very_high if t["won"])
        pnl = sum(t["pnl"] for t in very_high)
        wr = wins * 100 / len(very_high)
        print(f"  VERY HIGH CHOP (>=12): {len(very_high)} trades, {wr:.1f}% WR, ${pnl:.2f} PnL")

    # Test different CHOPPY thresholds
    print()
    print("=" * 80)
    print("  TESTING DIFFERENT CHOPPY THRESHOLDS")
    print("=" * 80)
    print()

    all_trades = [t for trades in trades_by_crossings.values() for t in trades]
    total_pnl = sum(t["pnl"] for t in all_trades)

    for threshold in [6, 8, 10, 12, 14]:
        filtered = [t for c, trades in trades_by_crossings.items() if c < threshold for t in trades]
        skipped = [t for c, trades in trades_by_crossings.items() if c >= threshold for t in trades]

        if filtered:
            f_wins = sum(1 for t in filtered if t["won"])
            f_pnl = sum(t["pnl"] for t in filtered)
            f_wr = f_wins * 100 / len(filtered)
        else:
            f_wr = f_pnl = 0

        s_pnl = sum(t["pnl"] for t in skipped) if skipped else 0
        s_losses = sum(1 for t in skipped if not t["won"])

        print(f"  CHOPPY >= {threshold}: Skip {len(skipped)} trades, keep {len(filtered)}")
        print(f"     Kept: {f_wr:.1f}% WR, ${f_pnl:.2f} PnL")
        print(f"     Skipped PnL: ${s_pnl:.2f} (would avoid {s_losses} losses)")
        print(f"     Change: ${f_pnl - total_pnl:+.2f}")
        print()


if __name__ == '__main__':
    main()
