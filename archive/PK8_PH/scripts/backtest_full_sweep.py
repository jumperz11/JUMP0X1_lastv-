#!/usr/bin/env python3
"""
Comprehensive backtest sweep - all configurations tested.
Includes T2 skip variants (V1.2 style).
"""

import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Tuple, List, Dict
import sys

@dataclass
class Config:
    name: str
    t1_threshold: float
    t2_threshold: float  # Set to 1.0 to effectively skip T2
    t3_threshold: float
    gate: float
    skip_t2: bool = False  # Explicit T2 skip flag
    t1_start_mins: float = 0.5   # T+30s
    t2_start_mins: float = 0.75  # T+45s
    t3_start_mins: float = 3.0   # T+3m

@dataclass
class Trade:
    direction: str
    entry_price: float
    tier: str
    won: bool
    pnl: float
    mins_in: float

@dataclass
class TierStats:
    trades: int = 0
    wins: int = 0
    pnl: float = 0.0

    @property
    def wr(self) -> float:
        return self.wins / self.trades * 100 if self.trades else 0

    @property
    def losses(self) -> int:
        return self.trades - self.wins

@dataclass
class Result:
    config: Config
    trades: int
    wins: int
    pnl: float
    blocked: int
    blocked_would_win: int
    tier_stats: Dict[str, TierStats] = field(default_factory=dict)

    @property
    def wr(self) -> float:
        return self.wins / self.trades * 100 if self.trades else 0

    @property
    def losses(self) -> int:
        return self.trades - self.wins

def get_winner(final_tick) -> Optional[str]:
    price_data = final_tick.get('price') if final_tick else None
    if not price_data:
        return None
    up_price = price_data.get('Up', 0.5)
    if up_price >= 0.95:
        return 'UP'
    elif up_price <= 0.05:
        return 'DOWN'
    return None

def simulate_session(session_path: Path, config: Config) -> Tuple[Optional[Trade], dict]:
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
                except:
                    continue

    if not ticks:
        return None, {'skip': 'empty_ticks'}

    winner = get_winner(ticks[-1])
    if not winner:
        return None, {'skip': 'no_winner'}

    blocked_info = None

    for tick in ticks:
        mins_left = tick.get('minutesLeft', 15)
        mins_in = 15.0 - mins_left

        if mins_in < config.t1_start_mins:
            continue
        if mins_left < 1.5:  # Cancel zone
            continue

        price_data = tick.get('price')
        if not price_data:
            continue
        up_price = price_data.get('Up')
        down_price = price_data.get('Down')
        if up_price is None or down_price is None:
            continue

        # Determine tier
        if mins_in < config.t2_start_mins:
            threshold = config.t1_threshold
            tier = 'T1'
        elif mins_in < config.t3_start_mins:
            # T2 skip check
            if config.skip_t2:
                continue
            threshold = config.t2_threshold
            tier = 'T2'
        else:
            threshold = config.t3_threshold
            tier = 'T3'

        direction = None
        edge = 0

        if up_price >= threshold and up_price > down_price:
            direction = 'UP'
            edge = up_price
        elif down_price >= threshold and down_price > up_price:
            direction = 'DOWN'
            edge = down_price

        if direction:
            ask_price = edge + 0.01

            if ask_price >= config.gate:
                if blocked_info is None:
                    blocked_info = {'would_win': direction == winner, 'tier': tier}
                continue

            won = (direction == winner)
            pnl = (1.0 - ask_price) if won else -ask_price

            return Trade(
                direction=direction,
                entry_price=ask_price,
                tier=tier,
                won=won,
                pnl=pnl,
                mins_in=mins_in
            ), {'entry': True}

    if blocked_info:
        return None, {'skip': 'gate', **blocked_info}
    return None, {'skip': 'no_signal'}

def run_backtest(sessions: List[Path], config: Config) -> Result:
    trades = []
    blocked = 0
    blocked_would_win = 0
    tier_stats = {'T1': TierStats(), 'T2': TierStats(), 'T3': TierStats()}

    for session in sessions:
        trade, info = simulate_session(session, config)
        if trade:
            trades.append(trade)
            tier_stats[trade.tier].trades += 1
            tier_stats[trade.tier].pnl += trade.pnl
            if trade.won:
                tier_stats[trade.tier].wins += 1
        elif info.get('skip') == 'gate':
            blocked += 1
            if info.get('would_win'):
                blocked_would_win += 1

    wins = len([t for t in trades if t.won])
    pnl = sum(t.pnl for t in trades)

    return Result(
        config=config,
        trades=len(trades),
        wins=wins,
        pnl=pnl,
        blocked=blocked,
        blocked_would_win=blocked_would_win,
        tier_stats=tier_stats
    )

def main():
    markets_dir = Path('/Users/jumperz/PROJES/JUMP01X/markets_paper')
    sessions = sorted([d for d in markets_dir.iterdir() if d.is_dir() and d.name.startswith('btc-')])

    print(f"=" * 120)
    print(f"COMPREHENSIVE BACKTEST SWEEP - {len(sessions)} BTC sessions")
    print(f"=" * 120)

    # Define ALL configs to test
    configs = [
        # ============ HISTORICAL VERSIONS ============
        Config("V1.0 ORIGINAL (64/55/52, $0.58 gate)", 0.64, 0.55, 0.52, 0.58),
        Config("V1.1 NO GATE (58/55/52, no gate)", 0.58, 0.55, 0.52, 1.00),
        Config("V1.2 SKIP T2 (58/--/52, no gate)", 0.58, 1.00, 0.52, 1.00, skip_t2=True),

        # ============ GATE VARIATIONS ============
        Config("Gate $0.55", 0.64, 0.55, 0.52, 0.55),
        Config("Gate $0.60", 0.64, 0.55, 0.52, 0.60),
        Config("Gate $0.62", 0.64, 0.55, 0.52, 0.62),
        Config("Gate $0.65", 0.64, 0.55, 0.52, 0.65),
        Config("Gate $0.70", 0.64, 0.55, 0.52, 0.70),

        # ============ T1 THRESHOLD VARIATIONS ============
        Config("T1=55% (55/55/52, no gate)", 0.55, 0.55, 0.52, 1.00),
        Config("T1=56% (56/55/52, no gate)", 0.56, 0.55, 0.52, 1.00),
        Config("T1=57% (57/55/52, no gate)", 0.57, 0.55, 0.52, 1.00),
        Config("T1=58% (58/55/52, no gate)", 0.58, 0.55, 0.52, 1.00),
        Config("T1=60% (60/55/52, no gate)", 0.60, 0.55, 0.52, 1.00),
        Config("T1=62% (62/55/52, no gate)", 0.62, 0.55, 0.52, 1.00),

        # ============ T2 SKIP VARIATIONS ============
        Config("SKIP T2, T1=55% (55/--/52)", 0.55, 1.00, 0.52, 1.00, skip_t2=True),
        Config("SKIP T2, T1=56% (56/--/52)", 0.56, 1.00, 0.52, 1.00, skip_t2=True),
        Config("SKIP T2, T1=57% (57/--/52)", 0.57, 1.00, 0.52, 1.00, skip_t2=True),
        Config("SKIP T2, T1=58% (58/--/52)", 0.58, 1.00, 0.52, 1.00, skip_t2=True),
        Config("SKIP T2, T1=60% (60/--/52)", 0.60, 1.00, 0.52, 1.00, skip_t2=True),
        Config("SKIP T2, T1=62% (62/--/52)", 0.62, 1.00, 0.52, 1.00, skip_t2=True),

        # ============ T3 THRESHOLD VARIATIONS (with T2 skip) ============
        Config("SKIP T2, T3=50% (58/--/50)", 0.58, 1.00, 0.50, 1.00, skip_t2=True),
        Config("SKIP T2, T3=51% (58/--/51)", 0.58, 1.00, 0.51, 1.00, skip_t2=True),
        Config("SKIP T2, T3=52% (58/--/52)", 0.58, 1.00, 0.52, 1.00, skip_t2=True),
        Config("SKIP T2, T3=53% (58/--/53)", 0.58, 1.00, 0.53, 1.00, skip_t2=True),
        Config("SKIP T2, T3=54% (58/--/54)", 0.58, 1.00, 0.54, 1.00, skip_t2=True),
        Config("SKIP T2, T3=55% (58/--/55)", 0.58, 1.00, 0.55, 1.00, skip_t2=True),

        # ============ AGGRESSIVE ============
        Config("All 52% (52/52/52, no gate)", 0.52, 0.52, 0.52, 1.00),
        Config("All 52% SKIP T2 (52/--/52)", 0.52, 1.00, 0.52, 1.00, skip_t2=True),
        Config("All 55% (55/55/55, no gate)", 0.55, 0.55, 0.55, 1.00),
        Config("All 55% SKIP T2 (55/--/55)", 0.55, 1.00, 0.55, 1.00, skip_t2=True),

        # ============ CONSERVATIVE ============
        Config("All 60% (60/60/60, no gate)", 0.60, 0.60, 0.60, 1.00),
        Config("All 60% SKIP T2 (60/--/60)", 0.60, 1.00, 0.60, 1.00, skip_t2=True),
        Config("All 65% (65/65/65, no gate)", 0.65, 0.65, 0.65, 1.00),

        # ============ T3 ONLY (late only) ============
        Config("T3 ONLY @ 52%", 0.99, 0.99, 0.52, 1.00),
        Config("T3 ONLY @ 55%", 0.99, 0.99, 0.55, 1.00),
        Config("T3 ONLY @ 50%", 0.99, 0.99, 0.50, 1.00),

        # ============ T1 ONLY (early only) ============
        Config("T1 ONLY @ 55%", 0.55, 0.99, 0.99, 1.00),
        Config("T1 ONLY @ 58%", 0.58, 0.99, 0.99, 1.00),
        Config("T1 ONLY @ 60%", 0.60, 0.99, 0.99, 1.00),

        # ============ OPTIMAL SEARCH (fine-tuned) ============
        Config("OPTIMAL? (57/--/51)", 0.57, 1.00, 0.51, 1.00, skip_t2=True),
        Config("OPTIMAL? (56/--/51)", 0.56, 1.00, 0.51, 1.00, skip_t2=True),
        Config("OPTIMAL? (58/--/51)", 0.58, 1.00, 0.51, 1.00, skip_t2=True),
        Config("OPTIMAL? (57/--/52)", 0.57, 1.00, 0.52, 1.00, skip_t2=True),
    ]

    print(f"\nRunning {len(configs)} configurations...\n")

    results = []
    for i, config in enumerate(configs):
        result = run_backtest(sessions, config)
        results.append(result)
        # Progress indicator
        if (i + 1) % 10 == 0:
            print(f"  Processed {i+1}/{len(configs)} configs...")

    # Sort by P&L
    results.sort(key=lambda r: r.pnl, reverse=True)

    # ============ MAIN RESULTS TABLE ============
    print("\n" + "=" * 120)
    print("RESULTS RANKED BY P&L")
    print("=" * 120)
    print(f"{'Rank':<5} {'Config':<40} {'Trades':>7} {'Wins':>6} {'WR%':>7} {'P&L':>12} {'Blk':>6} {'BlkWin%':>8}")
    print("-" * 120)

    for i, r in enumerate(results):
        blk_wr = r.blocked_would_win / r.blocked * 100 if r.blocked else 0
        pnl_str = f"${r.pnl:+.2f}"
        rank_marker = ""
        if i == 0:
            rank_marker = " BEST"
        elif i == len(results) - 1:
            rank_marker = " WORST"
        elif "V1.0" in r.config.name:
            rank_marker = " <-- OLD"
        elif "V1.2" in r.config.name:
            rank_marker = " <-- CURRENT"
        print(f"{i+1:<5} {r.config.name:<40} {r.trades:>7} {r.wins:>6} {r.wr:>6.1f}% {pnl_str:>12} {r.blocked:>6} {blk_wr:>7.1f}%{rank_marker}")

    # ============ TOP 10 DETAILED ============
    print("\n" + "=" * 120)
    print("TOP 10 DETAILED BREAKDOWN")
    print("=" * 120)

    for i, r in enumerate(results[:10]):
        print(f"\n#{i+1}: {r.config.name}")
        print(f"    Total: {r.trades} trades, {r.wins}W/{r.losses}L, {r.wr:.1f}% WR, P&L: ${r.pnl:+.2f}")
        print(f"    Blocked: {r.blocked} (would have won: {r.blocked_would_win})")
        print(f"    By Tier:")
        for tier in ['T1', 'T2', 'T3']:
            ts = r.tier_stats[tier]
            if ts.trades > 0:
                print(f"      {tier}: {ts.trades} trades, {ts.wins}W/{ts.losses}L ({ts.wr:.1f}%), P&L: ${ts.pnl:+.2f}")
            else:
                print(f"      {tier}: -- (skipped or no entries)")

    # ============ VERSION COMPARISON ============
    print("\n" + "=" * 120)
    print("VERSION COMPARISON (V1.0 vs V1.1 vs V1.2)")
    print("=" * 120)

    versions = ["V1.0", "V1.1", "V1.2"]
    for v in versions:
        for r in results:
            if v in r.config.name:
                print(f"\n{r.config.name}")
                print(f"  Trades: {r.trades}, WR: {r.wr:.1f}%, P&L: ${r.pnl:+.2f}")
                print(f"  Blocked: {r.blocked} (would win: {r.blocked_would_win})")
                for tier in ['T1', 'T2', 'T3']:
                    ts = r.tier_stats[tier]
                    if ts.trades > 0:
                        print(f"    {tier}: {ts.trades} @ {ts.wr:.1f}% WR, ${ts.pnl:+.2f}")
                break

    # ============ T2 SKIP ANALYSIS ============
    print("\n" + "=" * 120)
    print("T2 SKIP ANALYSIS")
    print("=" * 120)

    skip_results = [r for r in results if r.config.skip_t2]
    no_skip_results = [r for r in results if not r.config.skip_t2 and r.blocked == 0]  # No gate, no skip

    if skip_results and no_skip_results:
        skip_avg_pnl = sum(r.pnl for r in skip_results) / len(skip_results)
        no_skip_avg_pnl = sum(r.pnl for r in no_skip_results) / len(no_skip_results)
        print(f"Average P&L with T2 SKIP: ${skip_avg_pnl:+.2f} ({len(skip_results)} configs)")
        print(f"Average P&L with T2 ACTIVE: ${no_skip_avg_pnl:+.2f} ({len(no_skip_results)} configs)")
        print(f"Difference: ${skip_avg_pnl - no_skip_avg_pnl:+.2f}")

    # ============ BEST SKIP T2 ============
    print("\n" + "=" * 120)
    print("BEST T2 SKIP CONFIGURATIONS")
    print("=" * 120)

    skip_sorted = sorted(skip_results, key=lambda r: r.pnl, reverse=True)
    for i, r in enumerate(skip_sorted[:5]):
        print(f"#{i+1}: {r.config.name}")
        print(f"     Trades: {r.trades}, WR: {r.wr:.1f}%, P&L: ${r.pnl:+.2f}")
        ts1, ts3 = r.tier_stats['T1'], r.tier_stats['T3']
        print(f"     T1: {ts1.trades} @ {ts1.wr:.1f}% = ${ts1.pnl:+.2f} | T3: {ts3.trades} @ {ts3.wr:.1f}% = ${ts3.pnl:+.2f}")

    # ============ SUMMARY ============
    print("\n" + "=" * 120)
    print("SUMMARY")
    print("=" * 120)

    best = results[0]
    v10 = next((r for r in results if "V1.0" in r.config.name), None)
    v12 = next((r for r in results if "V1.2" in r.config.name), None)

    print(f"\nBEST OVERALL: {best.config.name}")
    print(f"  {best.trades} trades, {best.wr:.1f}% WR, P&L: ${best.pnl:+.2f}")

    if v10:
        print(f"\nV1.0 (old): P&L ${v10.pnl:+.2f}")
    if v12:
        print(f"V1.2 (current): P&L ${v12.pnl:+.2f}")
    if best and v12:
        delta = best.pnl - v12.pnl
        print(f"\nBest vs V1.2: ${delta:+.2f} difference")
        if delta > 1.0:
            print(f"  -> Consider switching to: {best.config.name}")
        else:
            print(f"  -> V1.2 is close to optimal")

if __name__ == '__main__':
    main()
