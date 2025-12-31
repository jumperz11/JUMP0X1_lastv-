#!/usr/bin/env python3
"""
Sweep parameters to find 65% WR
"""

import json
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

@dataclass
class Trade:
    session: str
    direction: str
    tier: str
    entry_tau: float
    edge: float
    outcome: Optional[bool] = None

def load_sessions():
    markets_dir = Path("/Users/jumperz/PROJES/JUMP01X/markets_paper")
    sessions = sorted([
        d for d in markets_dir.iterdir()
        if d.is_dir() and d.name.startswith("btc-updown-15m-")
    ])
    return sessions

def get_tau(tick, end_unix):
    t_ms = tick.get("t", 0)
    return end_unix - (t_ms / 1000.0)

def run_backtest(sessions, t1_thresh, t3_thresh, safety_cap, t1_end_sec=90, t3_start_sec=180):
    TIER1_START = 869.0
    TIER2_START = 899.0 - t1_end_sec
    TIER3_START = 899.0 - t3_start_sec
    CANCEL_ZONE = 90.0

    trades = []

    for session_path in sessions:
        ticks_file = session_path / "ticks.jsonl"
        if not ticks_file.exists():
            continue

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
            continue

        end_unix = ticks[0].get("endUnix", 0)
        trade_taken = False

        for tick in ticks:
            if trade_taken:
                break

            tau = get_tau(tick, end_unix)

            if tau < CANCEL_ZONE or tau > TIER1_START:
                continue

            price = tick.get("price")
            if not price:
                continue
            up_mid = price.get("Up")
            down_mid = price.get("Down")
            if up_mid is None or down_mid is None:
                continue

            if tau > TIER2_START:
                edge_threshold = t1_thresh
                tier = "t1"
            elif tau > TIER3_START:
                continue
            else:
                edge_threshold = t3_thresh
                tier = "t3"

            if up_mid >= down_mid and up_mid >= edge_threshold:
                direction = "Up"
                edge = up_mid
            elif down_mid > up_mid and down_mid >= edge_threshold:
                direction = "Down"
                edge = down_mid
            else:
                continue

            best = tick.get("best", {})
            side = best.get(direction, {})
            ask = side.get("ask")
            if ask is not None and ask >= safety_cap:
                continue

            last_tick = ticks[-1]
            last_price = last_tick.get("price", {})
            up_final = last_price.get("Up")
            down_final = last_price.get("Down")

            if up_final is None or down_final is None:
                outcome = None
            elif direction == "Up":
                outcome = up_final >= 0.9
            else:
                outcome = down_final >= 0.9

            trades.append(Trade(
                session=session_path.name,
                direction=direction,
                tier=tier,
                entry_tau=tau,
                edge=edge,
                outcome=outcome
            ))
            trade_taken = True

    wins = sum(1 for t in trades if t.outcome is True)
    losses = sum(1 for t in trades if t.outcome is False)
    total = wins + losses

    return {
        "trades": total,
        "wins": wins,
        "losses": losses,
        "wr": wins / total if total > 0 else 0,
        "pnl": wins - losses
    }

def main():
    import sys
    print("Loading sessions...", flush=True)
    sessions = load_sessions()
    print(f"Loaded {len(sessions)} sessions\n", flush=True)

    print("=" * 70)
    print("PARAMETER SWEEP - Finding 65% WR")
    print("=" * 70)

    results = []

    print("\n--- T1 Threshold Sweep (T3=52%) ---")
    for t1 in [0.58, 0.60, 0.62, 0.64, 0.66, 0.68, 0.70]:
        r = run_backtest(sessions, t1, 0.52, 0.70)
        print(f"T1={t1:.0%}: {r['wins']}/{r['trades']} = {r['wr']:.1%} WR, PnL=${r['pnl']}")
        results.append(("T1", t1, r))

    print("\n--- T3 Threshold Sweep (T1=58%) ---")
    for t3 in [0.52, 0.55, 0.58, 0.60, 0.62, 0.64, 0.66]:
        r = run_backtest(sessions, 0.58, t3, 0.70)
        print(f"T3={t3:.0%}: {r['wins']}/{r['trades']} = {r['wr']:.1%} WR, PnL=${r['pnl']}")
        results.append(("T3", t3, r))

    print("\n--- Safety Cap Sweep (T1=58%, T3=52%) ---")
    for cap in [0.55, 0.58, 0.60, 0.62, 0.64, 0.66, 0.68, 0.70]:
        r = run_backtest(sessions, 0.58, 0.52, cap)
        print(f"Cap={cap:.0%}: {r['wins']}/{r['trades']} = {r['wr']:.1%} WR, PnL=${r['pnl']}")
        results.append(("Cap", cap, r))

    print("\n--- T3 Only (disable T1) ---")
    for t3 in [0.52, 0.54, 0.56, 0.58, 0.60]:
        r = run_backtest(sessions, 1.0, t3, 0.70)
        print(f"T3-only={t3:.0%}: {r['wins']}/{r['trades']} = {r['wr']:.1%} WR, PnL=${r['pnl']}")
        results.append(("T3-only", t3, r))

    print("\n--- High Edge Only ---")
    for thresh in [0.62, 0.64, 0.66, 0.68, 0.70, 0.72, 0.74]:
        r = run_backtest(sessions, thresh, thresh, 0.70)
        print(f"Both={thresh:.0%}: {r['wins']}/{r['trades']} = {r['wr']:.1%} WR, PnL=${r['pnl']}")
        results.append(("Both", thresh, r))

    print("\n" + "=" * 70)
    print("CONFIGS WITH 65%+ WR (min 50 trades):")
    print("=" * 70)
    found = False
    for name, val, r in results:
        if r['wr'] >= 0.65 and r['trades'] >= 50:
            print(f"{name}={val:.0%}: {r['wins']}/{r['trades']} = {r['wr']:.1%} WR, PnL=${r['pnl']}")
            found = True

    if not found:
        print("None found with 65%+ WR and 50+ trades")
        print("\nClosest options (sorted by WR):")
        sorted_results = sorted(results, key=lambda x: x[2]['wr'], reverse=True)
        for name, val, r in sorted_results[:8]:
            if r['trades'] >= 20:
                print(f"{name}={val:.0%}: {r['wins']}/{r['trades']} = {r['wr']:.1%} WR, PnL=${r['pnl']}")

if __name__ == "__main__":
    main()
