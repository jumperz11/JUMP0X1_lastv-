#!/usr/bin/env python3
"""
Analyze where cheap prices occur in the session timeline.
"""

import json
from pathlib import Path
from collections import defaultdict

SPREAD_MAX = 0.02

def get_elapsed_secs(tick):
    mins_left = tick.get('minutesLeft', 15)
    return (15 - mins_left) * 60

def analyze_session(session_path):
    """Find cheap prices and their timing."""
    ticks_file = session_path / 'ticks.jsonl'
    if not ticks_file.exists():
        return []

    results = []
    with open(ticks_file, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                tick = json.loads(line)
            except:
                continue

            elapsed = get_elapsed_secs(tick)
            price = tick.get('price')
            best = tick.get('best')
            if not price or not best:
                continue

            up_mid = price.get('Up')
            down_mid = price.get('Down')
            if up_mid is None or down_mid is None:
                continue

            # Direction
            if up_mid >= down_mid:
                edge = up_mid
                side = best.get('Up', {})
            else:
                edge = down_mid
                side = best.get('Down', {})

            ask = side.get('ask')
            bid = side.get('bid')
            if ask is None or bid is None:
                continue

            spread = ask - bid
            if spread < 0 or spread > SPREAD_MAX:
                continue

            results.append({
                'elapsed': elapsed,
                'ask': ask,
                'edge': edge,
                'spread': spread
            })

    return results


def main():
    markets_dir = Path(__file__).parent.parent / 'markets_paper'
    sessions = sorted([
        d for d in markets_dir.iterdir()
        if d.is_dir() and d.name.startswith('btc-updown-15m-')
    ])

    print(f"Analyzing {len(sessions)} sessions...")
    print()

    # Collect all ask prices by zone
    zones = {
        'EARLY (0:00-2:29)': (0, 149),
        'CORE (2:30-3:45)': (150, 225),
        'DEAD (3:46-4:59)': (226, 299),
        'LATE (5:00+)': (300, 900),
    }

    zone_data = defaultdict(list)
    cheap_opportunities = []

    for session_path in sessions:
        ticks = analyze_session(session_path)
        for t in ticks:
            for zone_name, (start, end) in zones.items():
                if start <= t['elapsed'] <= end:
                    zone_data[zone_name].append(t)
                    if t['ask'] <= 0.56 and t['edge'] >= 0.60:
                        cheap_opportunities.append({
                            'session': session_path.name,
                            'zone': zone_name,
                            **t
                        })
                    break

    # Print zone statistics
    print("=" * 70)
    print("  ASK PRICE DISTRIBUTION BY ZONE")
    print("=" * 70)
    print()

    for zone_name in zones.keys():
        data = zone_data[zone_name]
        if not data:
            print(f"  {zone_name}: No data")
            continue

        asks = [t['ask'] for t in data]
        min_ask = min(asks)
        max_ask = max(asks)
        avg_ask = sum(asks) / len(asks)

        # Count cheap prices
        cheap = sum(1 for a in asks if a <= 0.56)
        very_cheap = sum(1 for a in asks if a <= 0.54)

        print(f"  {zone_name}:")
        print(f"    Ticks: {len(data)}")
        print(f"    Ask range: {min_ask:.4f} - {max_ask:.4f}")
        print(f"    Avg ask: {avg_ask:.4f}")
        print(f"    Cheap (<=0.56): {cheap} ({cheap*100/len(data):.1f}%)")
        print(f"    Very cheap (<=0.54): {very_cheap} ({very_cheap*100/len(data):.1f}%)")
        print()

    # Print CHEAP opportunities
    print("=" * 70)
    print("  CHEAP ENTRY OPPORTUNITIES (ask <= 0.56, edge >= 0.60)")
    print("=" * 70)
    print()
    print(f"  Total opportunities: {len(cheap_opportunities)}")

    if cheap_opportunities:
        # By zone
        by_zone = defaultdict(list)
        for opp in cheap_opportunities:
            by_zone[opp['zone']].append(opp)

        for zone_name in zones.keys():
            opps = by_zone[zone_name]
            if opps:
                print(f"\n  {zone_name}: {len(opps)} opportunities")
                # Sample
                for opp in opps[:3]:
                    print(f"    {opp['session']}: ask={opp['ask']:.4f} edge={opp['edge']:.4f} @{opp['elapsed']:.0f}s")
                if len(opps) > 3:
                    print(f"    ... and {len(opps)-3} more")


if __name__ == '__main__':
    main()
