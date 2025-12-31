#!/usr/bin/env python3
"""
Analyze crossings distribution to calibrate regime thresholds.
"""

import json
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass
from typing import List, Optional

WINDOW_SECONDS = 300
CORE_START_SECS = 150
CORE_END_SECS = 225


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


def compute_crossings(price_buffer: List[PricePoint], current_time: float, move_threshold: float = 0.001) -> int:
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


def analyze_session(session_path: Path, move_threshold: float = 0.001):
    """Analyze crossings at CORE entry time."""
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

    # Build price buffer
    price_buffer: List[PricePoint] = []
    last_record_time = 0.0
    entry_crossings = None
    entry_won = None

    for tick in ticks:
        elapsed_secs = get_elapsed_secs(tick)

        price = tick.get('price')
        if price:
            up_mid = price.get('Up')
            if up_mid is not None and up_mid > 0:
                if elapsed_secs - last_record_time >= 1.0:
                    price_buffer.append(PricePoint(timestamp=elapsed_secs, price=up_mid))
                    last_record_time = elapsed_secs

        # At CORE entry window, check crossings
        if CORE_START_SECS <= elapsed_secs <= CORE_END_SECS and entry_crossings is None:
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

            entry_crossings = compute_crossings(price_buffer, elapsed_secs, move_threshold)
            entry_won = (direction == winner)
            break

    if entry_crossings is None:
        return None

    return {
        "crossings": entry_crossings,
        "won": entry_won
    }


def main():
    markets_dir = Path(__file__).parent.parent / 'markets_paper'

    sessions = sorted([
        d for d in markets_dir.iterdir()
        if d.is_dir() and d.name.startswith('btc-updown-15m-')
    ])

    print(f"Analyzing {len(sessions)} sessions...")
    print()

    # Test different move thresholds
    for move_thresh in [0.001, 0.002, 0.003, 0.005]:
        print(f"\n{'='*70}")
        print(f"  MOVE_THRESHOLD = {move_thresh} ({move_thresh*100:.1f}%)")
        print(f"{'='*70}")

        crossings_dist = defaultdict(lambda: {"total": 0, "wins": 0, "pnl": 0})

        for session_path in sessions:
            result = analyze_session(session_path, move_thresh)
            if result:
                c = result["crossings"]
                crossings_dist[c]["total"] += 1
                if result["won"]:
                    crossings_dist[c]["wins"] += 1
                    crossings_dist[c]["pnl"] += 1.7  # approx win
                else:
                    crossings_dist[c]["pnl"] -= 5.0  # loss

        # Print distribution
        print(f"\n  Crossings Distribution:")
        print(f"  {'Crossings':>10} {'Trades':>10} {'WinRate':>10} {'PnL':>12} {'Regime':>12}")
        print(f"  {'-'*56}")

        sorted_crossings = sorted(crossings_dist.keys())
        for c in sorted_crossings:
            data = crossings_dist[c]
            wr = data["wins"] * 100 / data["total"] if data["total"] > 0 else 0
            regime = "STABLE" if c <= 2 else ("CHOPPY" if c >= 6 else "NEUTRAL")
            print(f"  {c:>10} {data['total']:>10} {wr:>9.1f}% ${data['pnl']:>10.2f} {regime:>12}")

        # Summary by regime
        stable = {"total": 0, "wins": 0, "pnl": 0}
        neutral = {"total": 0, "wins": 0, "pnl": 0}
        choppy = {"total": 0, "wins": 0, "pnl": 0}

        for c, data in crossings_dist.items():
            if c <= 2:
                bucket = stable
            elif c >= 6:
                bucket = choppy
            else:
                bucket = neutral
            bucket["total"] += data["total"]
            bucket["wins"] += data["wins"]
            bucket["pnl"] += data["pnl"]

        print(f"\n  Regime Summary:")
        print(f"  {'Regime':>12} {'Trades':>10} {'WinRate':>10} {'PnL':>12}")
        print(f"  {'-'*46}")
        for name, data in [("STABLE (<=2)", stable), ("NEUTRAL (3-5)", neutral), ("CHOPPY (>=6)", choppy)]:
            wr = data["wins"] * 100 / data["total"] if data["total"] > 0 else 0
            print(f"  {name:>12} {data['total']:>10} {wr:>9.1f}% ${data['pnl']:>10.2f}")


if __name__ == '__main__':
    main()
