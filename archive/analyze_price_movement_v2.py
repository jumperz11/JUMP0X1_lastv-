#!/usr/bin/env python3
"""
Price Movement Analysis V2: Better classification
- Early-Right -> Late-Flip: Won despite reversal OR Lost after being ahead
- Clean Resolution: Straight wins or straight losses
"""

import json
from pathlib import Path

EDGE_THRESHOLD = 0.64
HARD_PRICE_CAP = 0.72
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

def analyze_price_path(ticks, entry_tick_idx, direction, entry_price):
    """
    Analyze full price path after entry.
    Returns: dict with price movement metrics
    """
    prices_after_entry = []

    for tick in ticks[entry_tick_idx:]:
        price = tick.get('price', {})
        current_price = price.get(direction, 0.5)
        prices_after_entry.append(current_price)

    if not prices_after_entry:
        return None

    # Key metrics
    max_price = max(prices_after_entry)
    min_price = min(prices_after_entry)
    final_price = prices_after_entry[-1]

    # Was in the money at any point? (price > entry for our direction)
    was_winning = max_price > entry_price

    # Max gain (how far ahead we got)
    max_gain_pct = ((max_price - entry_price) / entry_price * 100) if entry_price > 0 else 0

    # Max drawdown from peak (how far it fell from best point)
    if max_price > entry_price:
        drawdown_from_peak = (max_price - min_price) / (max_price - entry_price) * 100 if max_price > entry_price else 0
    else:
        drawdown_from_peak = 0

    # Did price cross entry multiple times? (volatility indicator)
    crossings = 0
    above_entry = prices_after_entry[0] > entry_price
    for p in prices_after_entry[1:]:
        currently_above = p > entry_price
        if currently_above != above_entry:
            crossings += 1
            above_entry = currently_above

    return {
        'max_price': max_price,
        'min_price': min_price,
        'final_price': final_price,
        'was_winning': was_winning,
        'max_gain_pct': max_gain_pct,
        'drawdown_from_peak': drawdown_from_peak,
        'crossings': crossings,
        'volatility': max_price - min_price
    }

def simulate_session(session_path):
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
        if spread < 0 or bid > ask:
            continue
        if edge < EDGE_THRESHOLD:
            continue
        if ask > HARD_PRICE_CAP:
            continue
        if spread > SPREAD_MAX:
            continue

        won = (direction == winner)
        pnl = (1.0 - ask) * (4.0 / ask) if won else -4.0

        path = analyze_price_path(ticks, i, direction, edge)
        if not path:
            continue

        return {
            'session': session_path.name,
            'direction': direction,
            'edge': edge,
            'ask': ask,
            'won': won,
            'pnl': pnl,
            'path': path
        }

    return None

def is_alternating_sequence(outcomes):
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

    all_trades = []
    for session_path in sessions:
        trade = simulate_session(session_path)
        if trade:
            all_trades.append(trade)

    print(f"Total CORE trades: {len(all_trades)}")

    # Exclude alternating sequences
    alternating_indices = set()
    outcomes = [t['won'] for t in all_trades]
    for i in range(len(outcomes) - 4):
        window = outcomes[i:i+5]
        if is_alternating_sequence(window):
            for j in range(i, i + 5):
                alternating_indices.add(j)

    filtered_trades = [t for i, t in enumerate(all_trades) if i not in alternating_indices]
    print(f"After excluding alternating sequences: {len(filtered_trades)}")
    print()

    # Classify using more intuitive criteria
    # Early-Right -> Late-Flip: Was winning (max_gain >= 5%) then had significant reversal
    # Clean Resolution: Minimal volatility, straight path to outcome

    GAIN_THRESHOLD = 5.0  # Was ahead by at least 5%
    VOLATILITY_THRESHOLD = 0.10  # Price range threshold

    early_right_late_flip = []  # Was winning, then price reversed significantly
    clean_wins = []             # Won without major volatility
    clean_losses = []           # Lost without ever being ahead
    comeback_wins = []          # Was behind, then recovered to win

    for trade in filtered_trades:
        path = trade['path']
        was_winning = path['was_winning']
        max_gain = path['max_gain_pct']
        crossings = path['crossings']
        volatility = path['volatility']
        won = trade['won']

        # Classify based on price behavior
        if was_winning and max_gain >= GAIN_THRESHOLD:
            if not won:
                # Was ahead by 5%+, then lost - classic "Late-Flip"
                early_right_late_flip.append(trade)
            elif crossings >= 2 or volatility > VOLATILITY_THRESHOLD:
                # Won but with volatility - survived the flip
                early_right_late_flip.append(trade)
            else:
                # Won cleanly from strong position
                clean_wins.append(trade)
        else:
            if won:
                if crossings >= 2:
                    # Won after volatility - comeback
                    comeback_wins.append(trade)
                else:
                    clean_wins.append(trade)
            else:
                clean_losses.append(trade)

    # Stats helper
    def calc_stats(trades, name):
        if not trades:
            return {'name': name, 'count': 0, 'wins': 0, 'losses': 0, 'win_rate': 0,
                    'total_pnl': 0, 'avg_pnl': 0, 'avg_gain': 0, 'avg_crossings': 0}
        wins = sum(1 for t in trades if t['won'])
        losses = len(trades) - wins
        total_pnl = sum(t['pnl'] for t in trades)
        avg_pnl = total_pnl / len(trades)
        win_rate = wins / len(trades) * 100
        avg_gain = sum(t['path']['max_gain_pct'] for t in trades) / len(trades)
        avg_crossings = sum(t['path']['crossings'] for t in trades) / len(trades)
        avg_ask = sum(t['ask'] for t in trades) / len(trades)

        return {
            'name': name, 'count': len(trades), 'wins': wins, 'losses': losses,
            'win_rate': win_rate, 'total_pnl': total_pnl, 'avg_pnl': avg_pnl,
            'avg_gain': avg_gain, 'avg_crossings': avg_crossings, 'avg_ask': avg_ask
        }

    flip_stats = calc_stats(early_right_late_flip, "Early-Right->Late-Flip")
    clean_w_stats = calc_stats(clean_wins, "Clean Wins")
    clean_l_stats = calc_stats(clean_losses, "Clean Losses")
    comeback_stats = calc_stats(comeback_wins, "Comeback Wins")

    # Combine clean resolution
    clean_all = clean_wins + clean_losses
    clean_stats = calc_stats(clean_all, "Clean Resolution (Combined)")

    print("=" * 75)
    print("  PRICE MOVEMENT ANALYSIS: Early-Right->Late-Flip vs Clean Resolution")
    print("=" * 75)
    print()
    print("  Classification:")
    print("    Early-Right->Late-Flip: Was ahead 5%+, then saw reversal/volatility")
    print("    Clean Resolution: Moved steadily to outcome without major reversals")
    print()

    # Main comparison
    print("=" * 75)
    print("  MAIN COMPARISON")
    print("=" * 75)
    print(f"  {'Metric':<25} {'Early-Right->Flip':>22} {'Clean Resolution':>22}")
    print(f"  {'-'*70}")
    print(f"  {'Trades':<25} {flip_stats['count']:>22} {clean_stats['count']:>22}")
    print(f"  {'% of Total':<25} {flip_stats['count']/len(filtered_trades)*100:>21.1f}% {clean_stats['count']/len(filtered_trades)*100:>21.1f}%")
    print(f"  {'Wins':<25} {flip_stats['wins']:>22} {clean_stats['wins']:>22}")
    print(f"  {'Losses':<25} {flip_stats['losses']:>22} {clean_stats['losses']:>22}")
    print(f"  {'Win Rate':<25} {flip_stats['win_rate']:>21.2f}% {clean_stats['win_rate']:>21.2f}%")
    print(f"  {'Avg PnL':<25} ${flip_stats['avg_pnl']:>20.4f} ${clean_stats['avg_pnl']:>20.4f}")
    print(f"  {'Total PnL':<25} ${flip_stats['total_pnl']:>20.2f} ${clean_stats['total_pnl']:>20.2f}")
    print(f"  {'Avg Max Gain %':<25} {flip_stats['avg_gain']:>21.1f}% {clean_stats['avg_gain']:>21.1f}%")
    print(f"  {'Avg Entry Crossings':<25} {flip_stats['avg_crossings']:>22.2f} {clean_stats['avg_crossings']:>22.2f}")
    print(f"  {'Avg Entry Price':<25} {flip_stats['avg_ask']:>22.4f} {clean_stats['avg_ask']:>22.4f}")
    print()

    # Detailed breakdown
    print("=" * 75)
    print("  DETAILED BREAKDOWN")
    print("=" * 75)
    print(f"  {'Category':<25} {'Count':>10} {'Win Rate':>12} {'Avg PnL':>12} {'Total PnL':>14}")
    print(f"  {'-'*70}")
    print(f"  {'Early-Right->Late-Flip':<25} {flip_stats['count']:>10} {flip_stats['win_rate']:>11.1f}% ${flip_stats['avg_pnl']:>10.4f} ${flip_stats['total_pnl']:>12.2f}")
    print(f"  {'Clean Wins':<25} {clean_w_stats['count']:>10} {clean_w_stats['win_rate']:>11.1f}% ${clean_w_stats['avg_pnl']:>10.4f} ${clean_w_stats['total_pnl']:>12.2f}")
    print(f"  {'Clean Losses':<25} {clean_l_stats['count']:>10} {clean_l_stats['win_rate']:>11.1f}% ${clean_l_stats['avg_pnl']:>10.4f} ${clean_l_stats['total_pnl']:>12.2f}")
    print(f"  {'Comeback Wins':<25} {comeback_stats['count']:>10} {comeback_stats['win_rate']:>11.1f}% ${comeback_stats['avg_pnl']:>10.4f} ${comeback_stats['total_pnl']:>12.2f}")
    print()

    # Key insights
    print("=" * 75)
    print("  KEY INSIGHTS")
    print("=" * 75)
    print()

    total = len(filtered_trades)
    flip_pct = flip_stats['count'] / total * 100
    clean_pct = clean_stats['count'] / total * 100

    print(f"  1. VOLATILITY DISTRIBUTION:")
    print(f"     {flip_stats['count']} trades ({flip_pct:.1f}%) showed significant volatility (Early-Right->Flip)")
    print(f"     {clean_stats['count']} trades ({clean_pct:.1f}%) resolved cleanly")
    print()

    print(f"  2. WIN RATE BY CATEGORY:")
    print(f"     Early-Right->Flip: {flip_stats['win_rate']:.1f}% (volatile but often wins)")
    print(f"     Clean Resolution:  {clean_stats['win_rate']:.1f}%")
    print()

    print(f"  3. PROFITABILITY:")
    if flip_stats['avg_pnl'] > clean_stats['avg_pnl']:
        diff = flip_stats['avg_pnl'] - clean_stats['avg_pnl']
        print(f"     Volatile trades MORE profitable: ${flip_stats['avg_pnl']:.4f} vs ${clean_stats['avg_pnl']:.4f}")
        print(f"     Difference: +${diff:.4f} per trade")
    else:
        diff = clean_stats['avg_pnl'] - flip_stats['avg_pnl']
        print(f"     Clean trades MORE profitable: ${clean_stats['avg_pnl']:.4f} vs ${flip_stats['avg_pnl']:.4f}")
        print(f"     Difference: +${diff:.4f} per trade")
    print()

    print(f"  4. PRICE PATH CHARACTERISTICS:")
    print(f"     Flip trades: {flip_stats['avg_gain']:.1f}% avg peak gain, {flip_stats['avg_crossings']:.1f} avg crossings")
    print(f"     Clean trades: {clean_stats['avg_gain']:.1f}% avg peak gain, {clean_stats['avg_crossings']:.1f} avg crossings")
    print()

    # Special analysis: Flip wins vs Flip losses
    flip_wins = [t for t in early_right_late_flip if t['won']]
    flip_losses = [t for t in early_right_late_flip if not t['won']]

    if flip_wins and flip_losses:
        print("=" * 75)
        print("  FLIP TRADE DEEP DIVE: Wins vs Losses")
        print("=" * 75)
        fw = calc_stats(flip_wins, "Flip Wins")
        fl = calc_stats(flip_losses, "Flip Losses")
        print(f"  {'Metric':<25} {'Flip Wins':>18} {'Flip Losses':>18}")
        print(f"  {'-'*60}")
        print(f"  {'Count':<25} {fw['count']:>18} {fl['count']:>18}")
        print(f"  {'Avg Max Gain %':<25} {fw['avg_gain']:>17.1f}% {fl['avg_gain']:>17.1f}%")
        print(f"  {'Avg Entry Crossings':<25} {fw['avg_crossings']:>18.2f} {fl['avg_crossings']:>18.2f}")
        print(f"  {'Avg Entry Price':<25} {fw['avg_ask']:>18.4f} {fl['avg_ask']:>18.4f}")
        print()

        print(f"  INSIGHT: Flip losses had {fl['avg_gain']:.1f}% max gain before reversing")
        print(f"           Could explore trailing stops at ~{fl['avg_gain']*0.7:.1f}% gain")

    print()
    print("=" * 75)

if __name__ == '__main__':
    main()
