#!/usr/bin/env python3
"""
Price Movement Analysis: Early-Right -> Late-Flip vs Clean Resolution
Excludes trades in alternating 5-trade sequences (WLWLW/LWLWL)
"""

import json
from pathlib import Path
from collections import defaultdict

# Current settings (RULEV3+ v1.3)
EDGE_THRESHOLD = 0.64
HARD_PRICE_CAP = 0.72
SPREAD_MAX = 0.02
CORE_START = 150  # 2:30 elapsed
CORE_END = 225    # 3:45 elapsed

# Thresholds for price movement classification
FAVORABLE_MOVE_THRESHOLD = 0.05  # 5% favorable move
REVERSAL_THRESHOLD = 0.10        # 10% reversal from peak

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

def analyze_price_movement(ticks, entry_tick_idx, direction):
    """
    Analyze price movement after entry.
    Returns: (peak_favorable_move, max_reversal_from_peak, final_outcome)
    """
    entry_tick = ticks[entry_tick_idx]
    price = entry_tick.get('price', {})
    entry_price = price.get(direction, 0.5)

    peak_price = entry_price
    peak_favorable_move = 0.0
    max_reversal_from_peak = 0.0

    # Analyze all ticks after entry
    for tick in ticks[entry_tick_idx + 1:]:
        price = tick.get('price', {})
        current_price = price.get(direction, 0.5)

        # Calculate favorable move (price going up for our direction)
        favorable_move = (current_price - entry_price) / entry_price if entry_price > 0 else 0

        if current_price > peak_price:
            peak_price = current_price
            peak_favorable_move = max(peak_favorable_move, favorable_move)

        # Calculate reversal from peak
        if peak_price > entry_price:
            reversal = (peak_price - current_price) / (peak_price - entry_price) if (peak_price - entry_price) > 0 else 0
            max_reversal_from_peak = max(max_reversal_from_peak, reversal)

    return peak_favorable_move, max_reversal_from_peak

def simulate_session(session_path):
    """Simulate session and return trade info with price movement data."""
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

    for i, tick in enumerate(ticks):
        elapsed = get_elapsed_secs(tick)

        # CORE window only
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

        # Direction selection
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

        # Gates
        if spread < 0 or bid > ask:
            continue
        if edge < EDGE_THRESHOLD:
            continue
        if ask > HARD_PRICE_CAP:
            continue
        if spread > SPREAD_MAX:
            continue

        # Entry found
        won = (direction == winner)
        pnl = (1.0 - ask) * (4.0 / ask) if won else -4.0

        # Analyze price movement
        peak_favorable, max_reversal = analyze_price_movement(ticks, i, direction)

        return {
            'session': session_path.name,
            'direction': direction,
            'edge': edge,
            'ask': ask,
            'won': won,
            'pnl': pnl,
            'peak_favorable_move': peak_favorable,
            'max_reversal_from_peak': max_reversal,
            'elapsed': elapsed
        }

    return None

def is_alternating_sequence(outcomes):
    """Check if 5-trade sequence is alternating (WLWLW or LWLWL)."""
    if len(outcomes) != 5:
        return False
    pattern = ''.join(['W' if o else 'L' for o in outcomes])
    return pattern in ['WLWLW', 'LWLWL']

def main():
    markets_dir = Path(__file__).parent / 'markets_paper'

    sessions = sorted([
        d for d in markets_dir.iterdir()
        if d.is_dir() and d.name.startswith('btc-updown-15m-')
    ])

    print(f"Analyzing {len(sessions)} BTC sessions...")
    print()

    # Collect all trades
    all_trades = []
    for session_path in sessions:
        trade = simulate_session(session_path)
        if trade:
            all_trades.append(trade)

    print(f"Total CORE trades: {len(all_trades)}")

    # Identify alternating sequence trades
    alternating_indices = set()
    outcomes = [t['won'] for t in all_trades]

    for i in range(len(outcomes) - 4):
        window = outcomes[i:i+5]
        if is_alternating_sequence(window):
            for j in range(i, i + 5):
                alternating_indices.add(j)

    print(f"Trades in alternating sequences: {len(alternating_indices)}")

    # Filter out alternating sequence trades
    filtered_trades = [t for i, t in enumerate(all_trades) if i not in alternating_indices]
    print(f"Trades after exclusion: {len(filtered_trades)}")
    print()

    # Classify trades
    early_right_late_flip = []  # Favorable move then reversal
    clean_resolution = []        # Clean win or clean loss

    for trade in filtered_trades:
        peak_fav = trade['peak_favorable_move']
        max_rev = trade['max_reversal_from_peak']

        # Early-Right -> Late-Flip: Had favorable move then significant reversal
        if peak_fav >= FAVORABLE_MOVE_THRESHOLD and max_rev >= REVERSAL_THRESHOLD:
            early_right_late_flip.append(trade)
        else:
            clean_resolution.append(trade)

    # Calculate statistics
    def calc_stats(trades, name):
        if not trades:
            return {'name': name, 'count': 0}

        wins = sum(1 for t in trades if t['won'])
        losses = len(trades) - wins
        total_pnl = sum(t['pnl'] for t in trades)
        avg_pnl = total_pnl / len(trades)
        win_rate = wins / len(trades) * 100

        win_pnls = [t['pnl'] for t in trades if t['won']]
        loss_pnls = [t['pnl'] for t in trades if not t['won']]

        avg_win = sum(win_pnls) / len(win_pnls) if win_pnls else 0
        avg_loss = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0

        avg_peak_fav = sum(t['peak_favorable_move'] for t in trades) / len(trades)
        avg_reversal = sum(t['max_reversal_from_peak'] for t in trades) / len(trades)
        avg_ask = sum(t['ask'] for t in trades) / len(trades)

        return {
            'name': name,
            'count': len(trades),
            'wins': wins,
            'losses': losses,
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'avg_pnl': avg_pnl,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'avg_peak_favorable': avg_peak_fav,
            'avg_max_reversal': avg_reversal,
            'avg_ask': avg_ask
        }

    flip_stats = calc_stats(early_right_late_flip, "Early-Right -> Late-Flip")
    clean_stats = calc_stats(clean_resolution, "Clean Resolution")

    # Print comparison
    print("=" * 70)
    print("  PRICE MOVEMENT ANALYSIS: Early-Right->Late-Flip vs Clean Resolution")
    print("=" * 70)
    print()
    print(f"  Classification Criteria:")
    print(f"    Early-Right -> Late-Flip: Peak favorable move >= {FAVORABLE_MOVE_THRESHOLD*100:.0f}%")
    print(f"                              AND reversal from peak >= {REVERSAL_THRESHOLD*100:.0f}%")
    print(f"    Clean Resolution: All other trades")
    print()
    print("=" * 70)
    print(f"  {'Metric':<25} {'Early-Right->Flip':>20} {'Clean Resolution':>20}")
    print(f"  {'-'*65}")
    print(f"  {'Trades':<25} {flip_stats['count']:>20} {clean_stats['count']:>20}")
    print(f"  {'% of Total':<25} {flip_stats['count']/len(filtered_trades)*100:>19.1f}% {clean_stats['count']/len(filtered_trades)*100:>19.1f}%")
    print(f"  {'Wins':<25} {flip_stats.get('wins', 0):>20} {clean_stats.get('wins', 0):>20}")
    print(f"  {'Losses':<25} {flip_stats.get('losses', 0):>20} {clean_stats.get('losses', 0):>20}")
    print(f"  {'Win Rate':<25} {flip_stats.get('win_rate', 0):>19.2f}% {clean_stats.get('win_rate', 0):>19.2f}%")
    print(f"  {'Avg PnL':<25} ${flip_stats.get('avg_pnl', 0):>18.4f} ${clean_stats.get('avg_pnl', 0):>18.4f}")
    print(f"  {'Avg Win':<25} ${flip_stats.get('avg_win', 0):>18.4f} ${clean_stats.get('avg_win', 0):>18.4f}")
    print(f"  {'Avg Loss':<25} ${flip_stats.get('avg_loss', 0):>18.4f} ${clean_stats.get('avg_loss', 0):>18.4f}")
    print(f"  {'Total PnL':<25} ${flip_stats.get('total_pnl', 0):>18.2f} ${clean_stats.get('total_pnl', 0):>18.2f}")
    print(f"  {'Avg Peak Favorable':<25} {flip_stats.get('avg_peak_favorable', 0)*100:>19.2f}% {clean_stats.get('avg_peak_favorable', 0)*100:>19.2f}%")
    print(f"  {'Avg Max Reversal':<25} {flip_stats.get('avg_max_reversal', 0)*100:>19.2f}% {clean_stats.get('avg_max_reversal', 0)*100:>19.2f}%")
    print(f"  {'Avg Entry Price':<25} {flip_stats.get('avg_ask', 0):>20.4f} {clean_stats.get('avg_ask', 0):>20.4f}")
    print()

    # Key insights
    print("=" * 70)
    print("  KEY INSIGHTS")
    print("=" * 70)
    print()

    if flip_stats['count'] > 0 and clean_stats['count'] > 0:
        wr_diff = clean_stats['win_rate'] - flip_stats['win_rate']
        pnl_diff = clean_stats['avg_pnl'] - flip_stats['avg_pnl']

        print(f"  1. TRADE DISTRIBUTION:")
        print(f"     {flip_stats['count']} trades ({flip_stats['count']/len(filtered_trades)*100:.1f}%) showed early favorable move then reversal")
        print(f"     {clean_stats['count']} trades ({clean_stats['count']/len(filtered_trades)*100:.1f}%) had clean resolution")
        print()

        print(f"  2. WIN RATE COMPARISON:")
        if wr_diff > 0:
            print(f"     Clean Resolution outperforms by {wr_diff:.2f}% win rate")
        elif wr_diff < 0:
            print(f"     Early-Right->Flip outperforms by {-wr_diff:.2f}% win rate")
        else:
            print(f"     Win rates are equal")
        print()

        print(f"  3. PROFITABILITY:")
        if pnl_diff > 0:
            print(f"     Clean Resolution: ${clean_stats['avg_pnl']:.4f} avg PnL (better by ${pnl_diff:.4f})")
        elif pnl_diff < 0:
            print(f"     Early-Right->Flip: ${flip_stats['avg_pnl']:.4f} avg PnL (better by ${-pnl_diff:.4f})")
        print()

        print(f"  4. PRICE BEHAVIOR:")
        print(f"     Flip trades: {flip_stats['avg_peak_favorable']*100:.1f}% avg peak favorable, {flip_stats['avg_max_reversal']*100:.1f}% avg reversal")
        print(f"     Clean trades: {clean_stats['avg_peak_favorable']*100:.1f}% avg peak favorable, {clean_stats['avg_max_reversal']*100:.1f}% avg reversal")

    print()
    print("=" * 70)

if __name__ == '__main__':
    main()
