#!/usr/bin/env python3
"""
Backtest Rule v1.3 on BTC 15m sessions

Settings:
- Safety cap: best_ask >= 0.70 -> SKIP
- T1: 30s-90s, edge >= 58%
- T2: 90s-3m -> SKIP
- T3: 3m+, edge >= 52%
- Cancel zone: last 90s
"""

import json
import os
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

# Strategy constants
TIER1_START = 869.0   # T+30s (899 - 30)
TIER2_START = 809.0   # T+90s (899 - 90)
TIER3_START = 719.0   # T+3m (899 - 180)
CANCEL_ZONE = 90.0    # Last 90s
SAFETY_ASK_CAP = 0.70
TIER1_THRESHOLD = 0.58
TIER3_THRESHOLD = 0.52

@dataclass
class Trade:
    session: str
    direction: str  # UP or DOWN
    tier: str       # t1_early or t3_late
    entry_tau: float
    entry_price: float  # mid price at entry
    edge: float
    outcome: Optional[bool] = None  # True = win, False = loss

def get_tau(tick: dict) -> float:
    """Calculate tau (seconds until settlement) from tick."""
    end_unix = tick.get("endUnix", 0)
    t_ms = tick.get("t", 0)
    t_sec = t_ms / 1000.0
    return end_unix - t_sec

def get_mid_prices(tick: dict) -> tuple[Optional[float], Optional[float]]:
    """Get up_mid and down_mid from tick."""
    price = tick.get("price")
    if not price:
        return None, None
    up_mid = price.get("Up")
    down_mid = price.get("Down")
    return up_mid, down_mid

def get_best_ask(tick: dict, direction: str) -> Optional[float]:
    """Get best ask for the given direction."""
    best = tick.get("best")
    if not best:
        return None
    side = best.get(direction)
    if not side:
        return None
    return side.get("ask")

def evaluate_session(session_path: Path) -> Optional[Trade]:
    """
    Evaluate a single session according to Rule v1.3.
    Returns Trade if entry would be made, None if skipped.
    """
    ticks_file = session_path / "ticks.jsonl"
    if not ticks_file.exists():
        return None

    ticks = []
    with open(ticks_file) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    ticks.append(json.loads(line))
                except:
                    continue

    if not ticks:
        return None

    session_name = session_path.name
    trade_taken = False

    for tick in ticks:
        if trade_taken:
            break

        tau = get_tau(tick)

        # Cancel zone - too late
        if tau < CANCEL_ZONE:
            continue

        # Too early - wait for T+30s
        if tau > TIER1_START:
            continue

        up_mid, down_mid = get_mid_prices(tick)
        if up_mid is None or down_mid is None:
            continue

        # Determine tier
        if tau > TIER2_START:
            # T1: 30s-90s
            edge_threshold = TIER1_THRESHOLD
            tier_label = "t1_early"
        elif tau > TIER3_START:
            # T2: 90s-3m -> SKIP
            continue
        else:
            # T3: 3m+
            edge_threshold = TIER3_THRESHOLD
            tier_label = "t3_late"

        # Determine direction
        if up_mid >= down_mid and up_mid >= edge_threshold:
            direction = "Up"
            edge = up_mid
        elif down_mid > up_mid and down_mid >= edge_threshold:
            direction = "Down"
            edge = down_mid
        else:
            continue

        # Safety cap check
        best_ask = get_best_ask(tick, direction)
        if best_ask is not None and best_ask >= SAFETY_ASK_CAP:
            continue

        # Entry signal found
        entry_price = up_mid if direction == "Up" else down_mid

        return Trade(
            session=session_name,
            direction=direction,
            tier=tier_label,
            entry_tau=tau,
            entry_price=entry_price,
            edge=edge,
            outcome=None  # Will be filled after checking final tick
        )

    return None

def get_session_outcome(session_path: Path, direction: str) -> Optional[bool]:
    """
    Determine if the trade won based on the final tick.
    Returns True if win, False if loss, None if unknown.
    """
    ticks_file = session_path / "ticks.jsonl"
    if not ticks_file.exists():
        return None

    last_tick = None
    with open(ticks_file) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    last_tick = json.loads(line)
                except:
                    continue

    if not last_tick:
        return None

    # Check final prices - if direction mid >= 0.5, it's a win
    # Actually, need to check settlement: if price.Up or Down > 0.9 typically means settled
    price = last_tick.get("price")
    if not price:
        return None

    up_final = price.get("Up")
    down_final = price.get("Down")

    if up_final is None or down_final is None:
        return None

    # Settlement: typically one side goes to ~1.0 and other to ~0.0
    # Win if our direction settled high (>=0.9)
    if direction == "Up":
        return up_final >= 0.9
    else:
        return down_final >= 0.9

def main():
    markets_dir = Path("/Users/jumperz/PROJES/JUMP01X/markets_paper")

    # Get all BTC sessions
    sessions = sorted([
        d for d in markets_dir.iterdir()
        if d.is_dir() and d.name.startswith("btc-updown-15m-")
    ])

    print(f"Found {len(sessions)} BTC sessions")
    print("-" * 60)

    trades = []
    skipped = 0

    for session_path in sessions:
        trade = evaluate_session(session_path)
        if trade:
            outcome = get_session_outcome(session_path, trade.direction)
            trade.outcome = outcome
            trades.append(trade)
        else:
            skipped += 1

    # Stats
    total_trades = len(trades)
    wins = sum(1 for t in trades if t.outcome is True)
    losses = sum(1 for t in trades if t.outcome is False)
    unknown = sum(1 for t in trades if t.outcome is None)

    t1_trades = [t for t in trades if t.tier == "t1_early"]
    t3_trades = [t for t in trades if t.tier == "t3_late"]

    t1_wins = sum(1 for t in t1_trades if t.outcome is True)
    t1_total = len([t for t in t1_trades if t.outcome is not None])

    t3_wins = sum(1 for t in t3_trades if t.outcome is True)
    t3_total = len([t for t in t3_trades if t.outcome is not None])

    up_trades = [t for t in trades if t.direction == "Up"]
    down_trades = [t for t in trades if t.direction == "Down"]

    up_wins = sum(1 for t in up_trades if t.outcome is True)
    up_total = len([t for t in up_trades if t.outcome is not None])

    down_wins = sum(1 for t in down_trades if t.outcome is True)
    down_total = len([t for t in down_trades if t.outcome is not None])

    print(f"\n{'='*60}")
    print("RULE v1.3 BACKTEST RESULTS")
    print(f"{'='*60}\n")

    print(f"Total sessions:  {len(sessions)}")
    print(f"Trades taken:    {total_trades}")
    print(f"Skipped:         {skipped}")
    print(f"Trade rate:      {100*total_trades/len(sessions):.1f}%")
    print()

    if total_trades > 0:
        known = wins + losses
        if known > 0:
            print(f"Overall WR:      {wins}/{known} = {100*wins/known:.1f}%")
            print(f"  Wins:          {wins}")
            print(f"  Losses:        {losses}")
            if unknown > 0:
                print(f"  Unknown:       {unknown}")
        print()

        if t1_total > 0:
            print(f"T1 (30-90s):     {t1_wins}/{t1_total} = {100*t1_wins/t1_total:.1f}%")
        if t3_total > 0:
            print(f"T3 (3m+):        {t3_wins}/{t3_total} = {100*t3_wins/t3_total:.1f}%")
        print()

        if up_total > 0:
            print(f"UP trades:       {up_wins}/{up_total} = {100*up_wins/up_total:.1f}%")
        if down_total > 0:
            print(f"DOWN trades:     {down_wins}/{down_total} = {100*down_wins/down_total:.1f}%")
        print()

        # PnL estimate (assuming $1 risk, payout at 2x)
        # Win = +$1 profit, Loss = -$1
        pnl = wins - losses
        print(f"Est PnL ($1/trade): ${pnl:+.0f}")

        # Sample trades
        print(f"\n{'='*60}")
        print("SAMPLE TRADES (first 10)")
        print(f"{'='*60}")
        for t in trades[:10]:
            outcome_str = "WIN" if t.outcome else ("LOSS" if t.outcome is False else "???")
            print(f"  {t.session[-10:]}: {t.tier} {t.direction} @ tau={t.entry_tau:.0f}s edge={t.edge:.1%} -> {outcome_str}")

if __name__ == "__main__":
    main()
