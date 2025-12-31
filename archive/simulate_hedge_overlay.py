#!/usr/bin/env python3
"""
Variance-Control Hedge Overlay Simulation
- Base position: $4
- Hedge budget: $1 max
- Hedge triggered on "flip trades" (favorable move then multiple crossings near entry)
"""

import json
from pathlib import Path

# Current settings (RULEV3+ v1.3)
EDGE_THRESHOLD = 0.64
HARD_PRICE_CAP = 0.72
SPREAD_MAX = 0.02
CORE_START = 150
CORE_END = 225

# Position sizing
BASE_POSITION = 4.0
HEDGE_BUDGET = 1.0

# Flip trade definition: crosses entry >= this many times
CROSSING_THRESHOLD = 2

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

def analyze_trade(ticks, entry_tick_idx, direction, entry_price):
    """
    Analyze price path and determine if hedge should trigger.
    Returns crossings count and whether price moved favorably first.
    """
    prices = []
    for tick in ticks[entry_tick_idx:]:
        price = tick.get('price', {})
        current_price = price.get(direction, 0.5)
        prices.append(current_price)

    if not prices:
        return 0, False

    # Check if went favorable first (price > entry)
    went_favorable = any(p > entry_price for p in prices[:len(prices)//2])

    # Count crossings (how many times price crosses entry level)
    crossings = 0
    above_entry = prices[0] > entry_price
    for p in prices[1:]:
        currently_above = p > entry_price
        if currently_above != above_entry:
            crossings += 1
            above_entry = currently_above

    return crossings, went_favorable

def get_hedge_entry_price(tick, hedge_direction):
    """Get the ask price for hedge direction at the tick when hedge triggers."""
    best = tick.get('best', {})
    side = best.get(hedge_direction, {})
    return side.get('ask', 0.5)

def simulate_session(session_path):
    """Simulate session and return trade with hedge decision."""
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
            hedge_direction = 'Down'
        else:
            direction = 'Down'
            edge = down_mid
            side = best.get('Down', {})
            hedge_direction = 'Up'

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

        # Entry found - analyze price path
        crossings, went_favorable = analyze_trade(ticks, i, direction, edge)

        # Determine if this is a flip trade (hedge candidate)
        is_flip_trade = went_favorable and crossings >= CROSSING_THRESHOLD

        # Get hedge entry price (use same tick's opposite side ask)
        hedge_ask = get_hedge_entry_price(tick, hedge_direction)

        # Calculate outcomes
        main_won = (direction == winner)
        hedge_won = (hedge_direction == winner)

        # Base PnL (no hedge)
        if main_won:
            base_pnl = (1.0 - ask) * (BASE_POSITION / ask)
        else:
            base_pnl = -BASE_POSITION

        # Hedged PnL (if flip trade)
        if is_flip_trade:
            # Main position
            if main_won:
                main_pnl = (1.0 - ask) * (BASE_POSITION / ask)
            else:
                main_pnl = -BASE_POSITION

            # Hedge position
            if hedge_won:
                hedge_pnl = (1.0 - hedge_ask) * (HEDGE_BUDGET / hedge_ask)
            else:
                hedge_pnl = -HEDGE_BUDGET

            hedged_pnl = main_pnl + hedge_pnl
        else:
            hedged_pnl = base_pnl  # No hedge applied

        return {
            'session': session_path.name,
            'direction': direction,
            'ask': ask,
            'hedge_ask': hedge_ask,
            'won': main_won,
            'crossings': crossings,
            'went_favorable': went_favorable,
            'is_flip_trade': is_flip_trade,
            'base_pnl': base_pnl,
            'hedged_pnl': hedged_pnl
        }

    return None

def is_alternating_sequence(outcomes):
    if len(outcomes) != 5:
        return False
    pattern = ''.join(['W' if o else 'L' for o in outcomes])
    return pattern in ['WLWLW', 'LWLWL']

def calc_max_drawdown(pnls):
    """Calculate max drawdown from list of PnLs."""
    cumsum = 0
    peak = 0
    max_dd = 0
    for pnl in pnls:
        cumsum += pnl
        if cumsum > peak:
            peak = cumsum
        dd = peak - cumsum
        if dd > max_dd:
            max_dd = dd
    return max_dd

def calc_worst_5_trade_cumulative(pnls):
    """Find worst 5-trade cumulative PnL."""
    if len(pnls) < 5:
        return sum(pnls)
    worst = float('inf')
    for i in range(len(pnls) - 4):
        cum = sum(pnls[i:i+5])
        if cum < worst:
            worst = cum
    return worst

def main():
    markets_dir = Path(__file__).parent / 'markets_paper'
    sessions = sorted([
        d for d in markets_dir.iterdir()
        if d.is_dir() and d.name.startswith('btc-updown-15m-')
    ])

    print(f"Simulating hedge overlay on {len(sessions)} BTC sessions...")
    print()

    # Collect all trades
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

    # Extract PnL series
    base_pnls = [t['base_pnl'] for t in filtered_trades]
    hedged_pnls = [t['hedged_pnl'] for t in filtered_trades]

    # Count flip trades (where hedge was applied)
    flip_trades = [t for t in filtered_trades if t['is_flip_trade']]
    non_flip_trades = [t for t in filtered_trades if not t['is_flip_trade']]

    # Calculate metrics
    base_total = sum(base_pnls)
    hedged_total = sum(hedged_pnls)
    base_avg = base_total / len(base_pnls)
    hedged_avg = hedged_total / len(hedged_pnls)
    base_max_dd = calc_max_drawdown(base_pnls)
    hedged_max_dd = calc_max_drawdown(hedged_pnls)
    base_worst_5 = calc_worst_5_trade_cumulative(base_pnls)
    hedged_worst_5 = calc_worst_5_trade_cumulative(hedged_pnls)

    # Win rates
    base_wins = sum(1 for t in filtered_trades if t['won'])
    base_wr = base_wins / len(filtered_trades) * 100

    # Print results
    print("=" * 75)
    print("  VARIANCE-CONTROL HEDGE OVERLAY SIMULATION")
    print("=" * 75)
    print()
    print("  CONFIGURATION:")
    print(f"    Base position:     ${BASE_POSITION:.2f}")
    print(f"    Hedge budget:      ${HEDGE_BUDGET:.2f} max")
    print(f"    Hedge trigger:     Flip trades (favorable move + {CROSSING_THRESHOLD}+ crossings)")
    print()
    print(f"  TRADE CLASSIFICATION:")
    print(f"    Total trades:      {len(filtered_trades)}")
    print(f"    Flip trades:       {len(flip_trades)} ({len(flip_trades)/len(filtered_trades)*100:.1f}%) - HEDGED")
    print(f"    Non-flip trades:   {len(non_flip_trades)} ({len(non_flip_trades)/len(filtered_trades)*100:.1f}%) - NO HEDGE")
    print()

    # Flip trade breakdown
    flip_wins = sum(1 for t in flip_trades if t['won'])
    flip_losses = len(flip_trades) - flip_wins
    print(f"  FLIP TRADE BREAKDOWN:")
    print(f"    Flip wins:         {flip_wins} (hedge costs ${HEDGE_BUDGET:.2f} per trade)")
    print(f"    Flip losses:       {flip_losses} (hedge recovers partial loss)")
    print()

    print("=" * 75)
    print("  COMPARISON: UNHEDGED vs HEDGED")
    print("=" * 75)
    print(f"  {'Metric':<30} {'Unhedged':>18} {'Hedged':>18}")
    print(f"  {'-'*68}")
    print(f"  {'Average PnL':<30} ${base_avg:>16.4f} ${hedged_avg:>16.4f}")
    print(f"  {'Total PnL':<30} ${base_total:>16.2f} ${hedged_total:>16.2f}")
    print(f"  {'Max Drawdown':<30} ${base_max_dd:>16.2f} ${hedged_max_dd:>16.2f}")
    print(f"  {'Worst 5-Trade Cumulative':<30} ${base_worst_5:>16.2f} ${hedged_worst_5:>16.2f}")
    print()

    # Calculate deltas
    avg_delta = hedged_avg - base_avg
    total_delta = hedged_total - base_total
    dd_delta = hedged_max_dd - base_max_dd
    worst5_delta = hedged_worst_5 - base_worst_5

    print("=" * 75)
    print("  IMPACT OF HEDGE OVERLAY")
    print("=" * 75)
    print(f"  {'Metric':<30} {'Change':>18} {'% Change':>18}")
    print(f"  {'-'*68}")
    print(f"  {'Average PnL':<30} ${avg_delta:>+16.4f} {avg_delta/base_avg*100 if base_avg else 0:>+17.1f}%")
    print(f"  {'Total PnL':<30} ${total_delta:>+16.2f} {total_delta/base_total*100 if base_total else 0:>+17.1f}%")
    print(f"  {'Max Drawdown':<30} ${dd_delta:>+16.2f} {dd_delta/base_max_dd*100 if base_max_dd else 0:>+17.1f}%")
    print(f"  {'Worst 5-Trade Cumulative':<30} ${worst5_delta:>+16.2f} {worst5_delta/abs(base_worst_5)*100 if base_worst_5 else 0:>+17.1f}%")
    print()

    # Detailed analysis: How hedge performed on flip trades
    print("=" * 75)
    print("  HEDGE EFFECTIVENESS ON FLIP TRADES")
    print("=" * 75)

    # PnL impact on flip wins vs flip losses
    flip_win_base_pnl = sum(t['base_pnl'] for t in flip_trades if t['won'])
    flip_win_hedged_pnl = sum(t['hedged_pnl'] for t in flip_trades if t['won'])
    flip_loss_base_pnl = sum(t['base_pnl'] for t in flip_trades if not t['won'])
    flip_loss_hedged_pnl = sum(t['hedged_pnl'] for t in flip_trades if not t['won'])

    print(f"  {'Category':<25} {'Base PnL':>15} {'Hedged PnL':>15} {'Delta':>15}")
    print(f"  {'-'*68}")
    print(f"  {'Flip Wins (hedge cost)':<25} ${flip_win_base_pnl:>13.2f} ${flip_win_hedged_pnl:>13.2f} ${flip_win_hedged_pnl-flip_win_base_pnl:>+13.2f}")
    print(f"  {'Flip Losses (hedge help)':<25} ${flip_loss_base_pnl:>13.2f} ${flip_loss_hedged_pnl:>13.2f} ${flip_loss_hedged_pnl-flip_loss_base_pnl:>+13.2f}")
    print()

    # Calculate hedge cost vs benefit
    hedge_cost_on_wins = flip_win_base_pnl - flip_win_hedged_pnl
    hedge_benefit_on_losses = flip_loss_hedged_pnl - flip_loss_base_pnl
    net_hedge_impact = hedge_benefit_on_losses - hedge_cost_on_wins

    print(f"  HEDGE ECONOMICS:")
    print(f"    Cost on wins (insurance premium):  ${hedge_cost_on_wins:>10.2f}")
    print(f"    Benefit on losses (recovery):      ${hedge_benefit_on_losses:>+10.2f}")
    print(f"    Net hedge impact:                  ${net_hedge_impact:>+10.2f}")
    print()

    # Verdict
    print("=" * 75)
    print("  VERDICT")
    print("=" * 75)
    print()
    if hedged_avg < base_avg:
        print(f"  Average PnL: WORSE with hedge (${avg_delta:+.4f} per trade)")
    else:
        print(f"  Average PnL: BETTER with hedge (+${avg_delta:.4f} per trade)")

    if hedged_max_dd < base_max_dd:
        print(f"  Max Drawdown: IMPROVED with hedge ({dd_delta/base_max_dd*100:+.1f}%)")
    else:
        print(f"  Max Drawdown: WORSE with hedge ({dd_delta/base_max_dd*100:+.1f}%)")

    if hedged_worst_5 > base_worst_5:
        print(f"  Worst 5-Trade: IMPROVED with hedge ({worst5_delta/abs(base_worst_5)*100:+.1f}%)")
    else:
        print(f"  Worst 5-Trade: WORSE with hedge ({worst5_delta/abs(base_worst_5)*100:+.1f}%)")

    print()
    print("=" * 75)

if __name__ == '__main__':
    main()
