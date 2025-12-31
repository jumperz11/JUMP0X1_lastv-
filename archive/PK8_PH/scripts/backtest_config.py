#!/usr/bin/env python3
"""
Backtest RULE_V1.2 config on all markets_paper sessions.

Config (V1.2 - Updated 2025-12-22):
- Tier 1 (T+30s to T+45s): 58% threshold -> ENTER
- Tier 2 (T+45s to T+3m): SKIP (bleeds money despite 56% WR)
- Tier 3 (T+3m+): 52% threshold -> ENTER
- NO price gate

Key insight: Middle = danger zone. Early OR very late only.
"""

import os
import json
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Tuple
import sys

# Config constants
TIER1_START_MINS = 0.5    # T+30s (14.5 mins left)
TIER2_START_MINS = 0.75   # T+45s (14.25 mins left)
TIER3_START_MINS = 3.0    # T+3m (12 mins left)
CANCEL_ZONE_MINS = 1.5    # Last 90s

# V1.2 thresholds (T2 SKIPPED)
TIER1_THRESHOLD = 0.58    # Early window
TIER2_SKIP = True         # T2 skipped entirely
TIER3_THRESHOLD = 0.52    # Late, market decided

MAX_ENTRY_PRICE = 1.00    # DISABLED

@dataclass
class Trade:
    session: str
    direction: str  # UP or DOWN
    entry_price: float
    entry_time_mins: float  # minutes into session
    tier: str
    winner: str
    won: bool
    pnl: float

def get_winner(final_tick) -> Optional[str]:
    """Determine winner from final tick."""
    price_data = final_tick.get('price') if final_tick else None
    if not price_data:
        return None
    up_price = price_data.get('Up', 0.5)
    if up_price >= 0.95:
        return 'UP'
    elif up_price <= 0.05:
        return 'DOWN'
    return None

def simulate_session(session_path: Path) -> Tuple[Optional[Trade], dict]:
    """Simulate trading on one session."""
    ticks_file = session_path / 'ticks.jsonl'
    if not ticks_file.exists():
        return None, {'skip': 'no_ticks'}

    ticks = []
    with open(ticks_file) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    ticks.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    if not ticks:
        return None, {'skip': 'empty_ticks'}

    # Get winner from final tick
    winner = get_winner(ticks[-1])
    if not winner:
        return None, {'skip': 'no_winner'}

    # Session duration is 15 mins = 900s
    entry_signal = None

    for tick in ticks:
        mins_left = tick.get('minutesLeft', 15)
        mins_in = 15.0 - mins_left

        # Skip too early (before T+30s = 0.5 mins in)
        if mins_in < TIER1_START_MINS:
            continue

        # Skip cancel zone (last 1.5 mins)
        if mins_left < CANCEL_ZONE_MINS:
            continue

        # Get prices
        price_data = tick.get('price')
        if not price_data:
            continue
        up_price = price_data.get('Up')
        down_price = price_data.get('Down')

        if up_price is None or down_price is None:
            continue

        # Determine tier and threshold (V1.2: T2 skipped)
        if mins_in < TIER2_START_MINS:  # T+30s to T+45s
            threshold = TIER1_THRESHOLD
            tier = 'T1'
        elif mins_in < TIER3_START_MINS:  # T+45s to T+3m
            # T2 SKIP - market efficient, bleeds money
            continue
        else:  # T+3m+
            threshold = TIER3_THRESHOLD
            tier = 'T3'

        # Check for signal
        direction = None
        edge = 0
        ask_price = 0

        if up_price >= threshold and up_price > down_price:
            direction = 'UP'
            edge = up_price
            # Estimate ask = mid + half spread (assume ~0.01 spread)
            ask_price = up_price + 0.01
        elif down_price >= threshold and down_price > up_price:
            direction = 'DOWN'
            edge = down_price
            ask_price = down_price + 0.01

        if direction:
            # Apply price gate
            if ask_price >= MAX_ENTRY_PRICE:
                # Record that we would have entered but gate blocked
                if entry_signal is None:
                    entry_signal = {
                        'blocked': True,
                        'direction': direction,
                        'ask': ask_price,
                        'mins_in': mins_in,
                        'tier': tier
                    }
                continue

            # Entry signal!
            won = (direction == winner)
            pnl = (1.0 - ask_price) if won else -ask_price

            return Trade(
                session=session_path.name,
                direction=direction,
                entry_price=ask_price,
                entry_time_mins=mins_in,
                tier=tier,
                winner=winner,
                won=won,
                pnl=pnl
            ), {'entry': True}

    # No entry
    if entry_signal and entry_signal.get('blocked'):
        return None, {'skip': 'gate_blocked', 'would_have': entry_signal}

    return None, {'skip': 'no_signal'}

def main():
    # Try relative first, then absolute path
    markets_dir = Path('../markets_paper')
    if not markets_dir.exists():
        markets_dir = Path('/Users/jumperz/PROJES/JUMP01X/markets_paper')

    if not markets_dir.exists():
        print(f"Error: {markets_dir} not found")
        sys.exit(1)

    sessions = sorted([d for d in markets_dir.iterdir() if d.is_dir() and d.name.startswith('btc-')])

    print(f"Found {len(sessions)} BTC sessions")
    print(f"\nConfig (V1.2):")
    print(f"  Tier 1 (T+30s-45s): {TIER1_THRESHOLD*100:.0f}% threshold -> ENTER")
    print(f"  Tier 2 (T+45s-3m):  SKIP")
    print(f"  Tier 3 (T+3m+):     {TIER3_THRESHOLD*100:.0f}% threshold -> ENTER")
    print(f"  Gate: DISABLED")
    print()

    trades = []
    stats = {
        'total': 0,
        'no_signal': 0,
        'gate_blocked': 0,
        'no_winner': 0,
        'entries': 0
    }
    gate_blocked_would_win = 0
    gate_blocked_would_lose = 0

    for session in sessions:
        stats['total'] += 1
        trade, info = simulate_session(session)

        if trade:
            trades.append(trade)
            stats['entries'] += 1
        else:
            reason = info.get('skip', 'unknown')
            if reason == 'no_signal':
                stats['no_signal'] += 1
            elif reason == 'gate_blocked':
                stats['gate_blocked'] += 1
                # Track if gate-blocked trade would have won
                would_have = info.get('would_have', {})
                if would_have:
                    # Need to check winner
                    final_tick_path = session / 'ticks.jsonl'
                    if final_tick_path.exists():
                        with open(final_tick_path) as f:
                            lines = f.readlines()
                            if lines:
                                final = json.loads(lines[-1])
                                winner = get_winner(final)
                                if winner == would_have['direction']:
                                    gate_blocked_would_win += 1
                                else:
                                    gate_blocked_would_lose += 1
            elif reason == 'no_winner':
                stats['no_winner'] += 1

    # Analyze trades
    wins = [t for t in trades if t.won]
    losses = [t for t in trades if not t.won]

    print("=" * 60)
    print("BACKTEST RESULTS")
    print("=" * 60)
    print(f"\nSessions analyzed: {stats['total']}")
    print(f"  - No signal (below threshold): {stats['no_signal']}")
    print(f"  - Gate blocked: {stats['gate_blocked']}")
    print(f"  - No clear winner: {stats['no_winner']}")
    print(f"  - Entries: {stats['entries']}")

    if trades:
        win_rate = len(wins) / len(trades) * 100
        total_pnl = sum(t.pnl for t in trades)
        avg_win = sum(t.pnl for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t.pnl for t in losses) / len(losses) if losses else 0

        print(f"\n{'='*60}")
        print("TRADE STATS")
        print(f"{'='*60}")
        print(f"Total trades: {len(trades)}")
        print(f"Wins: {len(wins)} ({win_rate:.1f}%)")
        print(f"Losses: {len(losses)} ({100-win_rate:.1f}%)")
        print(f"Total P&L: ${total_pnl:.2f}")
        print(f"Avg Win: ${avg_win:.2f}")
        print(f"Avg Loss: ${avg_loss:.2f}")

        # By tier
        print(f"\n{'='*60}")
        print("BY TIER")
        print(f"{'='*60}")
        for tier in ['T1', 'T2', 'T3']:
            tier_trades = [t for t in trades if t.tier == tier]
            if tier_trades:
                tier_wins = [t for t in tier_trades if t.won]
                tier_wr = len(tier_wins) / len(tier_trades) * 100
                tier_pnl = sum(t.pnl for t in tier_trades)
                print(f"{tier}: {len(tier_trades)} trades, {len(tier_wins)}W/{len(tier_trades)-len(tier_wins)}L ({tier_wr:.1f}%), P&L: ${tier_pnl:.2f}")

        # By entry price bucket
        print(f"\n{'='*60}")
        print("BY ENTRY PRICE")
        print(f"{'='*60}")
        buckets = [
            (0.50, 0.53, "$0.50-0.53"),
            (0.53, 0.55, "$0.53-0.55"),
            (0.55, 0.58, "$0.55-0.58"),
        ]
        for low, high, label in buckets:
            bucket_trades = [t for t in trades if low <= t.entry_price < high]
            if bucket_trades:
                bucket_wins = [t for t in bucket_trades if t.won]
                bucket_wr = len(bucket_wins) / len(bucket_trades) * 100
                bucket_pnl = sum(t.pnl for t in bucket_trades)
                print(f"{label}: {len(bucket_trades)} trades, {bucket_wr:.1f}% WR, P&L: ${bucket_pnl:.2f}")

    # Gate analysis
    if stats['gate_blocked'] > 0:
        print(f"\n{'='*60}")
        print("GATE ANALYSIS (blocked entries)")
        print(f"{'='*60}")
        print(f"Gate blocked: {stats['gate_blocked']} entries")
        print(f"  Would have won: {gate_blocked_would_win}")
        print(f"  Would have lost: {gate_blocked_would_lose}")
        if gate_blocked_would_win + gate_blocked_would_lose > 0:
            blocked_wr = gate_blocked_would_win / (gate_blocked_would_win + gate_blocked_would_lose) * 100
            print(f"  Blocked WR: {blocked_wr:.1f}%")
            if blocked_wr > win_rate if trades else 0:
                print(f"  --> Gate is HURTING (blocking winners)")
            else:
                print(f"  --> Gate is HELPING (blocking losers)")

if __name__ == '__main__':
    main()
