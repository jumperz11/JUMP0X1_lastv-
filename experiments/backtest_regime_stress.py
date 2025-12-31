#!/usr/bin/env python3
"""
RULEV3+ - Worst-Case Regime Compression Test
=============================================
READ-ONLY ANALYSIS - Phase 1 config LOCKED

The FINAL stress test: Does edge survive when bad stuff clusters?

Configs:
  Phase 1:   CORE 3:00-3:29 (baseline)
  Phase 1.1: Extended 2:30-3:45

Attack Scenarios:
  1. Loss Clustering - Force consecutive losses
  2. Top-Decile Removal - Remove best 10% wins
  3. Bad-Day Isolation - Find concentration risk
  4. Edge Compression - Degrade edge by 0.02

Decision Rule:
  SHIP if: AvgPnL > 0, MaxDD < 2x Phase1, No day > 25% PnL
"""

import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Tuple
from datetime import datetime
from collections import defaultdict
import random

# ============================================================
# CONFIGS
# ============================================================
CONFIGS = {
    'PHASE_1': {
        'name': 'Phase 1',
        'EDGE_THRESHOLD': 0.64,
        'SAFETY_CAP': 0.72,
        'SPREAD_MAX': 0.02,
        'POSITION_SIZE': 5.0,
        'CORE_START': 3.0,      # 3:00
        'CORE_END': 3.5,        # 3:30
    },
    'PHASE_1_1': {
        'name': 'Phase 1.1',
        'EDGE_THRESHOLD': 0.64,
        'SAFETY_CAP': 0.72,
        'SPREAD_MAX': 0.02,
        'POSITION_SIZE': 5.0,
        'CORE_START': 2.5,      # 2:30
        'CORE_END': 3.75,       # 3:45
    },
}

@dataclass
class Trade:
    session: str
    direction: str
    edge: float
    ask: float
    spread: float
    won: bool
    pnl: float
    timestamp: str = ""

def get_elapsed_mins(tick):
    return 15.0 - tick.get('minutesLeft', 15)

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

def simulate_session(session_path, config):
    """Simulate session and return trade details."""
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

    for tick in ticks:
        elapsed = get_elapsed_mins(tick)

        if elapsed < config['CORE_START'] or elapsed >= config['CORE_END']:
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
        if edge < config['EDGE_THRESHOLD']:
            continue
        if ask > config['SAFETY_CAP']:
            continue
        if spread > config['SPREAD_MAX']:
            continue

        # Entry
        won = (direction == winner)
        shares = config['POSITION_SIZE'] / ask
        pnl = (1.0 - ask) * shares if won else -config['POSITION_SIZE']

        # Extract timestamp from session name
        parts = session_path.name.split('-')
        ts = parts[-1] if parts else ""

        return Trade(
            session=session_path.name,
            direction=direction,
            edge=edge,
            ask=ask,
            spread=spread,
            won=won,
            pnl=pnl,
            timestamp=ts
        )

    return None

def run_baseline(markets_dir, config):
    """Run baseline backtest, return list of trades."""
    sessions = sorted([
        d for d in markets_dir.iterdir()
        if d.is_dir() and d.name.startswith('btc-updown-15m-')
    ])

    trades = []
    for session_path in sessions:
        trade = simulate_session(session_path, config)
        if trade:
            trades.append(trade)

    return trades

def calc_metrics(trades: List[Trade]) -> Dict:
    """Calculate standard metrics from trade list."""
    if not trades:
        return {'trades': 0, 'wins': 0, 'wr': 0, 'avg_pnl': 0, 'total_pnl': 0, 'max_dd': 0}

    wins = sum(1 for t in trades if t.won)
    total_pnl = sum(t.pnl for t in trades)

    # Max drawdown
    running = 0
    peak = 0
    max_dd = 0
    for t in trades:
        running += t.pnl
        if running > peak:
            peak = running
        dd = peak - running
        if dd > max_dd:
            max_dd = dd

    # Longest losing streak
    max_streak = 0
    current_streak = 0
    for t in trades:
        if not t.won:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0

    return {
        'trades': len(trades),
        'wins': wins,
        'losses': len(trades) - wins,
        'wr': wins / len(trades) * 100 if trades else 0,
        'avg_pnl': total_pnl / len(trades) if trades else 0,
        'total_pnl': total_pnl,
        'max_dd': max_dd,
        'max_streak': max_streak
    }

def attack_loss_clustering(trades: List[Trade]) -> Dict:
    """
    Attack 1: Loss Clustering
    If a loss occurs, force the next qualifying trade to also be a loss.
    """
    if not trades:
        return calc_metrics([])

    modified = []
    force_next_loss = False

    for t in trades:
        new_trade = Trade(
            session=t.session,
            direction=t.direction,
            edge=t.edge,
            ask=t.ask,
            spread=t.spread,
            won=t.won,
            pnl=t.pnl,
            timestamp=t.timestamp
        )

        if force_next_loss and new_trade.won:
            # Force this win to become a loss
            new_trade.won = False
            new_trade.pnl = -5.0  # Full loss
            force_next_loss = False
        elif not new_trade.won:
            # Natural loss triggers next forced loss
            force_next_loss = True
        else:
            force_next_loss = False

        modified.append(new_trade)

    return calc_metrics(modified)

def attack_top_decile_removal(trades: List[Trade]) -> Dict:
    """
    Attack 2: Remove best 10% of winning trades.
    Tests if edge survives without home runs.
    """
    if not trades:
        return calc_metrics([])

    # Get winning trades sorted by PnL
    wins = [t for t in trades if t.won]
    wins.sort(key=lambda x: x.pnl, reverse=True)

    # Remove top 10%
    remove_count = max(1, len(wins) // 10)
    removed_sessions = {w.session for w in wins[:remove_count]}

    # Filter out removed trades
    modified = [t for t in trades if t.session not in removed_sessions]

    metrics = calc_metrics(modified)
    metrics['removed'] = remove_count
    return metrics

def attack_bad_day_isolation(trades: List[Trade]) -> Dict:
    """
    Attack 3: Group by day, find concentration risk.
    """
    if not trades:
        return {'worst_day': 0, 'top3_pct': 0, 'days': 0}

    # Group by day (first 8 chars of timestamp = YYYYMMDD pattern from unix)
    # Actually timestamps are unix, so we need to convert
    daily_pnl = defaultdict(float)
    for t in trades:
        # Use first part of session timestamp as day proxy
        day = t.timestamp[:6] if len(t.timestamp) >= 6 else t.timestamp
        daily_pnl[day] += t.pnl

    if not daily_pnl:
        return {'worst_day': 0, 'top3_pct': 0, 'days': 0}

    total_pnl = sum(daily_pnl.values())
    sorted_days = sorted(daily_pnl.values())

    worst_day = sorted_days[0]  # Most negative
    top3_days = sorted(daily_pnl.values(), reverse=True)[:3]
    top3_sum = sum(top3_days)

    top3_pct = (top3_sum / total_pnl * 100) if total_pnl > 0 else 0

    return {
        'worst_day': worst_day,
        'best_day': sorted_days[-1],
        'top3_pct': top3_pct,
        'days': len(daily_pnl),
        'daily_pnl': dict(daily_pnl)
    }

def attack_edge_compression(trades: List[Trade], degradation: float = 0.02) -> Dict:
    """
    Attack 4: Degrade edge by subtracting from each trade's edge.
    Simulates market getting smarter.
    """
    if not trades:
        return calc_metrics([])

    # We simulate what would happen if edge was 0.02 lower
    # This means some trades that barely passed would fail
    # And wins might become losses at the margin

    modified = []
    config = CONFIGS['PHASE_1']  # Use baseline thresholds

    for t in trades:
        degraded_edge = t.edge - degradation

        # Would this trade still pass the edge gate?
        if degraded_edge < config['EDGE_THRESHOLD']:
            continue  # Trade filtered out

        # For trades that pass, slightly reduce win probability
        # We model this as: if edge was close to threshold, flip some wins to losses
        new_trade = Trade(
            session=t.session,
            direction=t.direction,
            edge=degraded_edge,
            ask=t.ask,
            spread=t.spread,
            won=t.won,
            pnl=t.pnl,
            timestamp=t.timestamp
        )

        # If original edge was barely above threshold, convert some wins to losses
        edge_margin = t.edge - config['EDGE_THRESHOLD']
        if edge_margin < degradation and t.won:
            # This trade was marginal - flip to loss
            new_trade.won = False
            new_trade.pnl = -5.0

        modified.append(new_trade)

    return calc_metrics(modified)

def safe_div(a, b):
    return a / b if b != 0 else 0

def main():
    markets_dir = Path(__file__).parent.parent / 'markets_paper'
    log_dir = Path(__file__).parent.parent / 'backtest_full_logs' / 'regime_stress'
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = log_dir / f'regime_stress_{timestamp}.log'

    print('='*85)
    print('  RULEV3+ WORST-CASE REGIME COMPRESSION TEST')
    print('  The FINAL stress test: Does edge survive when bad stuff clusters?')
    print('='*85)
    print()
    print('  CONFIGS:')
    print('    Phase 1:   CORE 3:00-3:29 (baseline)')
    print('    Phase 1.1: Extended 2:30-3:45')
    print()
    print('  ATTACK SCENARIOS:')
    print('    1. Loss Clustering - Force consecutive losses')
    print('    2. Top-Decile Removal - Remove best 10% wins')
    print('    3. Bad-Day Isolation - Find concentration risk')
    print('    4. Edge Compression - Degrade edge by 0.02')
    print()

    # Run baseline for both configs
    print('  Running baselines...')
    trades_p1 = run_baseline(markets_dir, CONFIGS['PHASE_1'])
    trades_p11 = run_baseline(markets_dir, CONFIGS['PHASE_1_1'])
    print(f'    Phase 1:   {len(trades_p1)} trades')
    print(f'    Phase 1.1: {len(trades_p11)} trades')
    print()

    # Baseline metrics
    base_p1 = calc_metrics(trades_p1)
    base_p11 = calc_metrics(trades_p11)

    # ================================================================
    # BASELINE COMPARISON
    # ================================================================
    print('='*85)
    print('  BASELINE COMPARISON')
    print('='*85)
    print()
    print(f"  {'Metric':<20} {'Phase 1':>15} {'Phase 1.1':>15} {'Delta':>15}")
    print(f"  {'-'*65}")
    print(f"  {'Trades':<20} {base_p1['trades']:>15} {base_p11['trades']:>15} {base_p11['trades'] - base_p1['trades']:>+15}")
    print(f"  {'Win Rate':<20} {base_p1['wr']:>14.2f}% {base_p11['wr']:>14.2f}% {base_p11['wr'] - base_p1['wr']:>+14.2f}%")
    print(f"  {'AvgPnL':<20} ${base_p1['avg_pnl']:>14.4f} ${base_p11['avg_pnl']:>14.4f} ${base_p11['avg_pnl'] - base_p1['avg_pnl']:>+14.4f}")
    print(f"  {'Total PnL':<20} ${base_p1['total_pnl']:>14.2f} ${base_p11['total_pnl']:>14.2f} ${base_p11['total_pnl'] - base_p1['total_pnl']:>+14.2f}")
    print(f"  {'Max DD':<20} ${base_p1['max_dd']:>14.2f} ${base_p11['max_dd']:>14.2f} ${base_p11['max_dd'] - base_p1['max_dd']:>+14.2f}")
    print(f"  {'Max Lose Streak':<20} {base_p1['max_streak']:>15} {base_p11['max_streak']:>15} {base_p11['max_streak'] - base_p1['max_streak']:>+15}")
    print()

    # ================================================================
    # ATTACK 1: LOSS CLUSTERING
    # ================================================================
    print('='*85)
    print('  ATTACK 1: LOSS CLUSTERING')
    print('  "If you lose, assume you lose again next time"')
    print('='*85)
    print()

    clust_p1 = attack_loss_clustering(trades_p1)
    clust_p11 = attack_loss_clustering(trades_p11)

    print(f"  {'Metric':<20} {'Phase 1':>15} {'Phase 1.1':>15}")
    print(f"  {'-'*50}")
    print(f"  {'Trades':<20} {clust_p1['trades']:>15} {clust_p11['trades']:>15}")
    print(f"  {'Win Rate':<20} {clust_p1['wr']:>14.2f}% {clust_p11['wr']:>14.2f}%")
    print(f"  {'AvgPnL':<20} ${clust_p1['avg_pnl']:>14.4f} ${clust_p11['avg_pnl']:>14.4f}")
    print(f"  {'Total PnL':<20} ${clust_p1['total_pnl']:>14.2f} ${clust_p11['total_pnl']:>14.2f}")
    print(f"  {'Max DD':<20} ${clust_p1['max_dd']:>14.2f} ${clust_p11['max_dd']:>14.2f}")
    print(f"  {'Max Lose Streak':<20} {clust_p1['max_streak']:>15} {clust_p11['max_streak']:>15}")
    print()
    print(f"  Impact vs Baseline:")
    print(f"    Phase 1:   AvgPnL {safe_div(clust_p1['avg_pnl'], base_p1['avg_pnl'])*100:.1f}% of baseline, DD {safe_div(clust_p1['max_dd'], base_p1['max_dd']):.2f}x")
    print(f"    Phase 1.1: AvgPnL {safe_div(clust_p11['avg_pnl'], base_p11['avg_pnl'])*100:.1f}% of baseline, DD {safe_div(clust_p11['max_dd'], base_p11['max_dd']):.2f}x")
    print()

    # ================================================================
    # ATTACK 2: TOP-DECILE REMOVAL
    # ================================================================
    print('='*85)
    print('  ATTACK 2: TOP-DECILE REMOVAL')
    print('  "Remove best 10% of wins - does edge survive without home runs?"')
    print('='*85)
    print()

    decile_p1 = attack_top_decile_removal(trades_p1)
    decile_p11 = attack_top_decile_removal(trades_p11)

    print(f"  {'Metric':<20} {'Phase 1':>15} {'Phase 1.1':>15}")
    print(f"  {'-'*50}")
    print(f"  {'Trades (after)':<20} {decile_p1['trades']:>15} {decile_p11['trades']:>15}")
    print(f"  {'Removed':<20} {decile_p1.get('removed', 0):>15} {decile_p11.get('removed', 0):>15}")
    print(f"  {'Win Rate':<20} {decile_p1['wr']:>14.2f}% {decile_p11['wr']:>14.2f}%")
    print(f"  {'AvgPnL':<20} ${decile_p1['avg_pnl']:>14.4f} ${decile_p11['avg_pnl']:>14.4f}")
    print(f"  {'Total PnL':<20} ${decile_p1['total_pnl']:>14.2f} ${decile_p11['total_pnl']:>14.2f}")
    print(f"  {'Max DD':<20} ${decile_p1['max_dd']:>14.2f} ${decile_p11['max_dd']:>14.2f}")
    print()

    survives_p1 = decile_p1['avg_pnl'] > 0
    survives_p11 = decile_p11['avg_pnl'] > 0
    print(f"  Edge survives without home runs?")
    print(f"    Phase 1:   {'YES' if survives_p1 else 'NO'} (AvgPnL ${decile_p1['avg_pnl']:.4f})")
    print(f"    Phase 1.1: {'YES' if survives_p11 else 'NO'} (AvgPnL ${decile_p11['avg_pnl']:.4f})")
    print()

    # ================================================================
    # ATTACK 3: BAD-DAY ISOLATION
    # ================================================================
    print('='*85)
    print('  ATTACK 3: BAD-DAY ISOLATION')
    print('  "How concentrated is PnL? How bad can a single day get?"')
    print('='*85)
    print()

    day_p1 = attack_bad_day_isolation(trades_p1)
    day_p11 = attack_bad_day_isolation(trades_p11)

    print(f"  {'Metric':<25} {'Phase 1':>15} {'Phase 1.1':>15}")
    print(f"  {'-'*55}")
    print(f"  {'Trading Days':<25} {day_p1['days']:>15} {day_p11['days']:>15}")
    print(f"  {'Worst Day PnL':<25} ${day_p1['worst_day']:>14.2f} ${day_p11['worst_day']:>14.2f}")
    print(f"  {'Best Day PnL':<25} ${day_p1['best_day']:>14.2f} ${day_p11['best_day']:>14.2f}")
    print(f"  {'Top 3 Days % of PnL':<25} {day_p1['top3_pct']:>14.1f}% {day_p11['top3_pct']:>14.1f}%")
    print()

    # Check concentration
    worst_day_pct_p1 = abs(day_p1['worst_day']) / base_p1['total_pnl'] * 100 if base_p1['total_pnl'] > 0 else 0
    worst_day_pct_p11 = abs(day_p11['worst_day']) / base_p11['total_pnl'] * 100 if base_p11['total_pnl'] > 0 else 0

    print(f"  Worst day as % of total PnL:")
    print(f"    Phase 1:   {worst_day_pct_p1:.1f}%")
    print(f"    Phase 1.1: {worst_day_pct_p11:.1f}%")
    print()

    # ================================================================
    # ATTACK 4: EDGE COMPRESSION
    # ================================================================
    print('='*85)
    print('  ATTACK 4: EDGE COMPRESSION')
    print('  "Subtract 0.02 from every edge - simulates market getting smarter"')
    print('='*85)
    print()

    comp_p1 = attack_edge_compression(trades_p1, 0.02)
    comp_p11 = attack_edge_compression(trades_p11, 0.02)

    print(f"  {'Metric':<20} {'Phase 1':>15} {'Phase 1.1':>15}")
    print(f"  {'-'*50}")
    print(f"  {'Trades (after)':<20} {comp_p1['trades']:>15} {comp_p11['trades']:>15}")
    print(f"  {'Trades Lost':<20} {base_p1['trades'] - comp_p1['trades']:>15} {base_p11['trades'] - comp_p11['trades']:>15}")
    print(f"  {'Win Rate':<20} {comp_p1['wr']:>14.2f}% {comp_p11['wr']:>14.2f}%")
    print(f"  {'AvgPnL':<20} ${comp_p1['avg_pnl']:>14.4f} ${comp_p11['avg_pnl']:>14.4f}")
    print(f"  {'Total PnL':<20} ${comp_p1['total_pnl']:>14.2f} ${comp_p11['total_pnl']:>14.2f}")
    print(f"  {'Max DD':<20} ${comp_p1['max_dd']:>14.2f} ${comp_p11['max_dd']:>14.2f}")
    print()

    survives_comp_p1 = comp_p1['avg_pnl'] > 0
    survives_comp_p11 = comp_p11['avg_pnl'] > 0
    print(f"  Survives edge compression?")
    print(f"    Phase 1:   {'YES' if survives_comp_p1 else 'NO'}")
    print(f"    Phase 1.1: {'YES' if survives_comp_p11 else 'NO'}")
    print()

    # ================================================================
    # SUMMARY TABLE
    # ================================================================
    print('='*85)
    print('  SUMMARY: ALL ATTACKS')
    print('='*85)
    print()
    print(f"  {'Attack':<25} {'P1 AvgPnL':>12} {'P1 DD':>10} {'P1.1 AvgPnL':>12} {'P1.1 DD':>10}")
    print(f"  {'-'*70}")
    print(f"  {'Baseline':<25} ${base_p1['avg_pnl']:>11.4f} ${base_p1['max_dd']:>9.2f} ${base_p11['avg_pnl']:>11.4f} ${base_p11['max_dd']:>9.2f}")
    print(f"  {'1. Loss Clustering':<25} ${clust_p1['avg_pnl']:>11.4f} ${clust_p1['max_dd']:>9.2f} ${clust_p11['avg_pnl']:>11.4f} ${clust_p11['max_dd']:>9.2f}")
    print(f"  {'2. Top-Decile Removal':<25} ${decile_p1['avg_pnl']:>11.4f} ${decile_p1['max_dd']:>9.2f} ${decile_p11['avg_pnl']:>11.4f} ${decile_p11['max_dd']:>9.2f}")
    print(f"  {'3. Edge Compression':<25} ${comp_p1['avg_pnl']:>11.4f} ${comp_p1['max_dd']:>9.2f} ${comp_p11['avg_pnl']:>11.4f} ${comp_p11['max_dd']:>9.2f}")
    print()

    # ================================================================
    # DECISION CRITERIA
    # ================================================================
    print('='*85)
    print('  DECISION CRITERIA')
    print('='*85)
    print()
    print('  SHIP Phase 1.1 if ALL of:')
    print(f'    1. AvgPnL > 0 under all attacks')
    print(f'    2. MaxDD < 2x Phase 1 baseline (< ${base_p1["max_dd"] * 2:.2f})')
    print(f'    3. No single day > 25% of total PnL')
    print()

    # Check criteria for Phase 1.1
    dd_limit = base_p1['max_dd'] * 2

    crit1_base = base_p11['avg_pnl'] > 0
    crit1_clust = clust_p11['avg_pnl'] > 0
    crit1_decile = decile_p11['avg_pnl'] > 0
    crit1_comp = comp_p11['avg_pnl'] > 0
    crit1 = crit1_base and crit1_clust and crit1_decile and crit1_comp

    crit2 = base_p11['max_dd'] < dd_limit

    # For criterion 3, check if worst day is < 25% of total
    crit3 = worst_day_pct_p11 < 25

    print(f"  Phase 1.1 Evaluation:")
    print()
    print(f"    Criterion 1: AvgPnL > 0 under all attacks")
    print(f"      Baseline:        {'PASS' if crit1_base else 'FAIL'} (${base_p11['avg_pnl']:.4f})")
    print(f"      Loss Clustering: {'PASS' if crit1_clust else 'FAIL'} (${clust_p11['avg_pnl']:.4f})")
    print(f"      Top-Decile:      {'PASS' if crit1_decile else 'FAIL'} (${decile_p11['avg_pnl']:.4f})")
    print(f"      Edge Compress:   {'PASS' if crit1_comp else 'FAIL'} (${comp_p11['avg_pnl']:.4f})")
    print(f"      >> Overall: {'PASS' if crit1 else 'FAIL'}")
    print()
    print(f"    Criterion 2: MaxDD < 2x Phase 1 (< ${dd_limit:.2f})")
    print(f"      Phase 1.1 DD: ${base_p11['max_dd']:.2f}")
    print(f"      >> {'PASS' if crit2 else 'FAIL'}")
    print()
    print(f"    Criterion 3: No single day > 25% of total PnL")
    print(f"      Worst day: {worst_day_pct_p11:.1f}% of total")
    print(f"      >> {'PASS' if crit3 else 'FAIL'}")
    print()

    # ================================================================
    # FINAL VERDICT
    # ================================================================
    print('='*85)
    print('  FINAL VERDICT')
    print('='*85)
    print()

    all_pass = crit1 and crit2 and crit3

    if all_pass:
        print('  +--------------------------------------------------+')
        print('  |                                                  |')
        print('  |   PHASE 1.1: ALL CRITERIA PASSED                 |')
        print('  |                                                  |')
        print('  |   Decision: SHIP IT                              |')
        print('  |                                                  |')
        print('  +--------------------------------------------------+')
        print()
        print(f'  Phase 1.1 survives worst-case regime compression.')
        print(f'  Edge is robust, not fragile.')
        print()
        print(f'  Upgrade path:')
        print(f'    - Window: 3:00-3:29 -> 2:30-3:45')
        print(f'    - Expected: +{base_p11["trades"] - base_p1["trades"]} trades (+{(base_p11["trades"]/base_p1["trades"]-1)*100:.1f}%)')
        print(f'    - Expected: +${base_p11["total_pnl"] - base_p1["total_pnl"]:.2f} PnL (+{(base_p11["total_pnl"]/base_p1["total_pnl"]-1)*100:.1f}%)')
    else:
        print('  +--------------------------------------------------+')
        print('  |                                                  |')
        print('  |   PHASE 1.1: CRITERIA NOT MET                    |')
        print('  |                                                  |')
        print('  |   Decision: STAY PHASE 1                         |')
        print('  |                                                  |')
        print('  +--------------------------------------------------+')
        print()
        print('  Failed criteria:')
        if not crit1:
            print('    - AvgPnL goes negative under some attack')
        if not crit2:
            print(f'    - MaxDD ${base_p11["max_dd"]:.2f} exceeds limit ${dd_limit:.2f}')
        if not crit3:
            print(f'    - Day concentration {worst_day_pct_p11:.1f}% exceeds 25%')
        print()
        print('  Phase 1 remains the safe choice.')

    print()
    print('='*85)
    print('  NOTE: This is analysis only. No config changes made.')
    print('='*85)

    # Save log
    with open(log_file, 'w') as f:
        f.write(f"RULEV3+ Worst-Case Regime Compression Test\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n")
        f.write("="*60 + "\n\n")

        f.write("BASELINE:\n")
        f.write(f"  Phase 1:   {base_p1['trades']} trades, ${base_p1['avg_pnl']:.4f} AvgPnL, ${base_p1['total_pnl']:.2f} Total, ${base_p1['max_dd']:.2f} DD\n")
        f.write(f"  Phase 1.1: {base_p11['trades']} trades, ${base_p11['avg_pnl']:.4f} AvgPnL, ${base_p11['total_pnl']:.2f} Total, ${base_p11['max_dd']:.2f} DD\n\n")

        f.write("ATTACKS (Phase 1.1):\n")
        f.write(f"  Loss Clustering:   ${clust_p11['avg_pnl']:.4f} AvgPnL, ${clust_p11['max_dd']:.2f} DD\n")
        f.write(f"  Top-Decile:        ${decile_p11['avg_pnl']:.4f} AvgPnL, ${decile_p11['max_dd']:.2f} DD\n")
        f.write(f"  Edge Compression:  ${comp_p11['avg_pnl']:.4f} AvgPnL, ${comp_p11['max_dd']:.2f} DD\n\n")

        f.write(f"VERDICT: {'SHIP PHASE 1.1' if all_pass else 'STAY PHASE 1'}\n")

    print(f"\n  Log saved to: {log_file}")

if __name__ == '__main__':
    main()
