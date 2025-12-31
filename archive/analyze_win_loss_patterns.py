#!/usr/bin/env python3
"""
Analyze win/loss patterns from live trading log
"""

# Data extracted from live log
trades = [
    {"time": "01:00:01", "result": "WIN",  "pnl": 1.70,  "winner": "UP",   "we_bet": "UP"},
    {"time": "01:15:01", "result": "LOSS", "pnl": -4.00, "winner": "DOWN", "we_bet": "UP"},
    {"time": "01:30:02", "result": "WIN",  "pnl": 1.79,  "winner": "DOWN", "we_bet": "DOWN"},
    {"time": "02:00:01", "result": "WIN",  "pnl": 2.15,  "winner": "UP",   "we_bet": "UP"},
    {"time": "02:30:02", "result": "WIN",  "pnl": 1.63,  "winner": "UP",   "we_bet": "UP"},
    {"time": "02:45:02", "result": "LOSS", "pnl": -4.00, "winner": "UP",   "we_bet": "DOWN"},
    {"time": "03:00:02", "result": "WIN",  "pnl": 1.63,  "winner": "UP",   "we_bet": "UP"},
    {"time": "03:15:01", "result": "WIN",  "pnl": 1.88,  "winner": "UP",   "we_bet": "UP"},
    {"time": "03:30:02", "result": "WIN",  "pnl": 1.97,  "winner": "UP",   "we_bet": "UP"},
    {"time": "03:45:02", "result": "WIN",  "pnl": 0.00,  "winner": "UP",   "we_bet": "UP"},
    {"time": "04:00:01", "result": "WIN",  "pnl": 2.06,  "winner": "DOWN", "we_bet": "DOWN"},
    {"time": "04:15:02", "result": "WIN",  "pnl": 2.06,  "winner": "DOWN", "we_bet": "DOWN"},
    {"time": "05:45:01", "result": "WIN",  "pnl": 1.45,  "winner": "UP",   "we_bet": "UP"},
    {"time": "06:30:01", "result": "WIN",  "pnl": 2.15,  "winner": "DOWN", "we_bet": "DOWN"},
    {"time": "06:45:02", "result": "WIN",  "pnl": 1.63,  "winner": "DOWN", "we_bet": "DOWN"},
    {"time": "07:00:02", "result": "WIN",  "pnl": 1.45,  "winner": "UP",   "we_bet": "UP"},
    {"time": "07:15:01", "result": "WIN",  "pnl": 2.06,  "winner": "UP",   "we_bet": "UP"},
    {"time": "07:30:02", "result": "LOSS", "pnl": -4.00, "winner": "DOWN", "we_bet": "UP"},
    {"time": "07:45:02", "result": "LOSS", "pnl": -4.00, "winner": "UP",   "we_bet": "DOWN"},
    {"time": "08:15:01", "result": "WIN",  "pnl": 2.06,  "winner": "UP",   "we_bet": "UP"},
    {"time": "09:00:01", "result": "WIN",  "pnl": 1.71,  "winner": "DOWN", "we_bet": "DOWN"},
    {"time": "09:45:01", "result": "WIN",  "pnl": 0.00,  "winner": "DOWN", "we_bet": "DOWN"},
    {"time": "10:00:02", "result": "WIN",  "pnl": 2.15,  "winner": "UP",   "we_bet": "UP"},
    {"time": "11:00:02", "result": "WIN",  "pnl": 1.97,  "winner": "UP",   "we_bet": "UP"},
    {"time": "12:15:02", "result": "WIN",  "pnl": 2.15,  "winner": "DOWN", "we_bet": "DOWN"},
    {"time": "12:45:01", "result": "LOSS", "pnl": -2.23, "winner": "DOWN", "we_bet": "UP"},
    {"time": "13:00:01", "result": "WIN",  "pnl": 1.79,  "winner": "DOWN", "we_bet": "DOWN"},
    {"time": "13:15:02", "result": "WIN",  "pnl": 2.15,  "winner": "UP",   "we_bet": "UP"},
    {"time": "13:30:02", "result": "LOSS", "pnl": -4.00, "winner": "UP",   "we_bet": "DOWN"},
    {"time": "13:45:01", "result": "LOSS", "pnl": -4.00, "winner": "UP",   "we_bet": "DOWN"},
    {"time": "14:00:02", "result": "WIN",  "pnl": 2.15,  "winner": "DOWN", "we_bet": "DOWN"},
    {"time": "14:15:02", "result": "LOSS", "pnl": -4.00, "winner": "UP",   "we_bet": "DOWN"},
]

print("=" * 75)
print("  SYSTEM FAILURE vs SUCCESS PATTERN ANALYSIS")
print("=" * 75)
print()

# Separate wins and losses
wins = [t for t in trades if t['result'] == 'WIN']
losses = [t for t in trades if t['result'] == 'LOSS']

print(f"  Total trades: {len(trades)}")
print(f"  Wins: {len(wins)} ({len(wins)/len(trades)*100:.1f}%)")
print(f"  Losses: {len(losses)} ({len(losses)/len(trades)*100:.1f}%)")
print()

# Pattern 1: Consecutive losses
print("=" * 75)
print("  PATTERN 1: CONSECUTIVE LOSSES")
print("=" * 75)
print()

consec_losses = []
current_streak = []
for t in trades:
    if t['result'] == 'LOSS':
        current_streak.append(t)
    else:
        if len(current_streak) >= 2:
            consec_losses.append(current_streak.copy())
        current_streak = []
if len(current_streak) >= 2:
    consec_losses.append(current_streak)

for streak in consec_losses:
    print(f"  Back-to-back losses at: {streak[0]['time']} - {streak[-1]['time']}")
    for t in streak:
        print(f"    {t['time']}: We bet {t['we_bet']}, BTC went {t['winner']}")
    print()

# Pattern 2: Direction mismatch analysis
print("=" * 75)
print("  PATTERN 2: WHAT HAPPENED BEFORE LOSSES")
print("=" * 75)
print()

for i, t in enumerate(trades):
    if t['result'] == 'LOSS':
        prev_winner = trades[i-1]['winner'] if i > 0 else "N/A"
        prev_result = trades[i-1]['result'] if i > 0 else "N/A"

        # Was there a direction flip?
        if i > 0:
            flipped = "YES" if trades[i-1]['winner'] != t['winner'] else "NO"
        else:
            flipped = "N/A"

        print(f"  LOSS at {t['time']}:")
        print(f"    We bet: {t['we_bet']}, BTC went: {t['winner']}")
        print(f"    Previous session: {prev_winner} ({prev_result})")
        print(f"    Direction flipped from prev: {flipped}")
        print()

# Pattern 3: When do wins cluster?
print("=" * 75)
print("  PATTERN 3: WIN STREAKS (3+ consecutive)")
print("=" * 75)
print()

win_streaks = []
current_streak = []
for t in trades:
    if t['result'] == 'WIN':
        current_streak.append(t)
    else:
        if len(current_streak) >= 3:
            win_streaks.append(current_streak.copy())
        current_streak = []
if len(current_streak) >= 3:
    win_streaks.append(current_streak)

for streak in win_streaks:
    directions = [t['winner'] for t in streak]
    up_count = directions.count('UP')
    down_count = directions.count('DOWN')

    print(f"  {len(streak)}-win streak: {streak[0]['time']} - {streak[-1]['time']}")
    print(f"    BTC direction: {up_count} UP, {down_count} DOWN")

    # Was BTC trending or choppy?
    switches = sum(1 for i in range(1, len(directions)) if directions[i] != directions[i-1])
    if switches <= 1:
        print(f"    BTC behavior: TRENDING (only {switches} direction switch)")
    else:
        print(f"    BTC behavior: MIXED ({switches} direction switches)")

    total_pnl = sum(t['pnl'] for t in streak)
    print(f"    Total PnL: ${total_pnl:.2f}")
    print()

# Pattern 4: Loss timing analysis
print("=" * 75)
print("  PATTERN 4: LOSS TIMING (Hour of day)")
print("=" * 75)
print()

from collections import defaultdict
hour_stats = defaultdict(lambda: {'wins': 0, 'losses': 0})

for t in trades:
    hour = int(t['time'].split(':')[0])
    if t['result'] == 'WIN':
        hour_stats[hour]['wins'] += 1
    else:
        hour_stats[hour]['losses'] += 1

print(f"  {'Hour':<10} {'Wins':<8} {'Losses':<8} {'Win Rate':<10}")
print(f"  {'-'*40}")
for hour in sorted(hour_stats.keys()):
    stats = hour_stats[hour]
    total = stats['wins'] + stats['losses']
    wr = stats['wins'] / total * 100 if total > 0 else 0
    marker = " ← WEAK" if wr < 70 else ""
    print(f"  {hour:02d}:00     {stats['wins']:<8} {stats['losses']:<8} {wr:.1f}%{marker}")

# Pattern 5: Direction prediction accuracy
print()
print("=" * 75)
print("  PATTERN 5: DIRECTION PREDICTION ACCURACY")
print("=" * 75)
print()

up_predictions = [t for t in trades if t['we_bet'] == 'UP']
down_predictions = [t for t in trades if t['we_bet'] == 'DOWN']

up_correct = sum(1 for t in up_predictions if t['result'] == 'WIN')
down_correct = sum(1 for t in down_predictions if t['result'] == 'WIN')

print(f"  When we bet UP:   {up_correct}/{len(up_predictions)} correct ({up_correct/len(up_predictions)*100:.1f}%)")
print(f"  When we bet DOWN: {down_correct}/{len(down_predictions)} correct ({down_correct/len(down_predictions)*100:.1f}%)")
print()

# Pattern 6: After a flip
print("=" * 75)
print("  PATTERN 6: PERFORMANCE AFTER BTC DIRECTION FLIP")
print("=" * 75)
print()

after_flip_trades = []
after_same_trades = []

for i in range(1, len(trades)):
    if trades[i-1]['winner'] != trades[i]['winner']:
        after_flip_trades.append(trades[i])
    else:
        after_same_trades.append(trades[i])

flip_wins = sum(1 for t in after_flip_trades if t['result'] == 'WIN')
same_wins = sum(1 for t in after_same_trades if t['result'] == 'WIN')

print(f"  After BTC flipped direction:")
print(f"    Trades: {len(after_flip_trades)}")
print(f"    Win rate: {flip_wins/len(after_flip_trades)*100:.1f}%")
print()
print(f"  After BTC continued same direction:")
print(f"    Trades: {len(after_same_trades)}")
print(f"    Win rate: {same_wins/len(after_same_trades)*100:.1f}%")
print()

# Summary
print("=" * 75)
print("  SUMMARY: WHEN SYSTEM FAILS vs SUCCEEDS")
print("=" * 75)
print()
print("  SYSTEM FAILS WHEN:")
print("    1. BTC is choppy (direction flipping rapidly)")
print("    2. Back-to-back losses often follow trend exhaustion")
print("    3. Late afternoon hours (13:00-14:00) showed weakness")
print()
print("  SYSTEM SUCCEEDS WHEN:")
print("    1. BTC has directional conviction (trending)")
print("    2. Even through reversals - if clean, system catches it")
print("    3. Early morning hours (03:00-07:00) were strongest")
print()
print("=" * 75)
