#!/usr/bin/env python3
"""
RULEV3+ Phase 1 - Trade Frequency Variants Analysis
====================================================
READ-ONLY ANALYSIS - Phase 1 config LOCKED

Tests variants to increase trade frequency while preserving positive expectancy.

Variants:
  V0: BASELINE (Phase 1 locked)
  V1: EDGE_THRESHOLD = 0.62 (lowered from 0.64)
  V2: CORE window 2:30-3:45 (extended from 3:00-3:29)
  V3: MAX_TRADES = 2 per session (2nd trade: edge>=0.66, spread<=0.015)
  V4: SPREAD_MAX = 0.025 (raised from 0.02)

Markets: BTC, ETH, BTC+ETH combined
"""

import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Tuple, Optional
from datetime import datetime

# ============================================================
# PHASE 1 BASELINE (LOCKED - reference only)
# ============================================================
BASELINE = {
    'EDGE_THRESHOLD': 0.64,
    'SAFETY_CAP': 0.72,
    'SPREAD_MAX': 0.02,
    'POSITION_SIZE': 5.0,
    'CORE_START': 3.0,      # 3:00
    'CORE_END': 3.5,        # 3:30 (actually 3:29)
    'MAX_TRADES': 1,
}

# Variants to test
VARIANTS = {
    'V0_BASELINE': {
        **BASELINE,
        'description': 'Phase 1 locked config'
    },
    'V1_EDGE_062': {
        **BASELINE,
        'EDGE_THRESHOLD': 0.62,
        'description': 'Edge threshold lowered to 0.62'
    },
    'V2_WINDOW_EXT': {
        **BASELINE,
        'CORE_START': 2.5,      # 2:30
        'CORE_END': 3.75,       # 3:45
        'description': 'Window extended to 2:30-3:45'
    },
    'V3_MAX_2': {
        **BASELINE,
        'MAX_TRADES': 2,
        'SECOND_EDGE': 0.66,
        'SECOND_SPREAD': 0.015,
        'description': 'Max 2 trades (2nd: edge>=0.66, spread<=0.015)'
    },
    'V4_SPREAD_025': {
        **BASELINE,
        'SPREAD_MAX': 0.025,
        'description': 'Spread gate raised to 0.025'
    },
}

MARKETS = ['btc', 'eth']

@dataclass
class Trade:
    session: str
    market: str
    direction: str
    edge: float
    ask: float
    spread: float
    won: bool
    pnl: float
    trade_num: int = 1

@dataclass
class Result:
    variant: str = ""
    market: str = ""
    description: str = ""
    total_sessions: int = 0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    sum_ask: float = 0.0
    sum_spread: float = 0.0
    trades: List[Trade] = field(default_factory=list)

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

def simulate_session(session_path, market, config):
    """Simulate session with given config."""
    ticks_file = session_path / 'ticks.jsonl'
    if not ticks_file.exists():
        return []

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
        return []

    winner = get_winner(ticks)
    if not winner:
        return []

    trades = []
    session_trade_count = 0
    max_trades = config.get('MAX_TRADES', 1)

    for tick in ticks:
        if session_trade_count >= max_trades:
            break

        elapsed = get_elapsed_mins(tick)

        # GATE: Window
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

        # GATE: BAD_BOOK
        if spread < 0 or bid > ask:
            continue

        # Determine thresholds based on trade number
        if session_trade_count == 0:
            # First trade uses standard thresholds
            edge_threshold = config['EDGE_THRESHOLD']
            spread_max = config['SPREAD_MAX']
        else:
            # Second trade uses stricter thresholds (V3 only)
            edge_threshold = config.get('SECOND_EDGE', config['EDGE_THRESHOLD'])
            spread_max = config.get('SECOND_SPREAD', config['SPREAD_MAX'])

        # GATE: EDGE_GATE
        if edge < edge_threshold:
            continue

        # GATE: PRICE_GATE
        if ask > config['SAFETY_CAP']:
            continue

        # GATE: SPREAD_GATE
        if spread > spread_max:
            continue

        # ALL GATES PASSED - Entry
        session_trade_count += 1
        won = (direction == winner)
        shares = config['POSITION_SIZE'] / ask
        pnl = (1.0 - ask) * shares if won else -config['POSITION_SIZE']

        trades.append(Trade(
            session=session_path.name,
            market=market,
            direction=direction,
            edge=edge,
            ask=ask,
            spread=spread,
            won=won,
            pnl=pnl,
            trade_num=session_trade_count
        ))

    return trades

def run_backtest(markets_dir, market, variant_name, config):
    """Run backtest for specific market and variant."""
    result = Result(
        variant=variant_name,
        market=market.upper(),
        description=config.get('description', '')
    )

    prefix = f'{market}-updown-15m-'
    sessions = sorted([
        d for d in markets_dir.iterdir()
        if d.is_dir() and d.name.startswith(prefix)
    ])

    result.total_sessions = len(sessions)
    running_pnl = 0.0
    peak_pnl = 0.0

    for session_path in sessions:
        trades = simulate_session(session_path, market, config)

        for trade in trades:
            result.total_trades += 1
            result.trades.append(trade)

            if trade.won:
                result.wins += 1
            else:
                result.losses += 1

            result.total_pnl += trade.pnl
            result.sum_ask += trade.ask
            result.sum_spread += trade.spread

            running_pnl += trade.pnl
            if running_pnl > peak_pnl:
                peak_pnl = running_pnl
            dd = peak_pnl - running_pnl
            if dd > result.max_drawdown:
                result.max_drawdown = dd

    return result

def combine_results(btc_result, eth_result, variant_name, config):
    """Combine BTC and ETH results into portfolio."""
    result = Result(
        variant=variant_name,
        market="BTC+ETH",
        description=config.get('description', ''),
        total_sessions=btc_result.total_sessions + eth_result.total_sessions
    )

    # Combine trades chronologically
    all_trades = btc_result.trades + eth_result.trades
    all_trades.sort(key=lambda t: t.session.split('-')[-1])

    running_pnl = 0.0
    peak_pnl = 0.0

    for trade in all_trades:
        result.total_trades += 1
        result.trades.append(trade)

        if trade.won:
            result.wins += 1
        else:
            result.losses += 1

        result.total_pnl += trade.pnl
        result.sum_ask += trade.ask
        result.sum_spread += trade.spread

        running_pnl += trade.pnl
        if running_pnl > peak_pnl:
            peak_pnl = running_pnl
        dd = peak_pnl - running_pnl
        if dd > result.max_drawdown:
            result.max_drawdown = dd

    return result

def safe_div(a, b):
    return a / b if b > 0 else 0

def main():
    markets_dir = Path(__file__).parent.parent / 'markets_paper'
    log_dir = Path(__file__).parent.parent / 'backtest_full_logs' / 'frequency_variants'
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = log_dir / f'frequency_variants_{timestamp}.log'

    print('='*90)
    print('  RULEV3+ PHASE 1 - TRADE FREQUENCY VARIANTS ANALYSIS')
    print('  READ-ONLY ANALYSIS - Phase 1 config LOCKED')
    print('='*90)
    print()
    print('  BASELINE (Phase 1):')
    print(f'    Edge >= {BASELINE["EDGE_THRESHOLD"]}')
    print(f'    Ask <= {BASELINE["SAFETY_CAP"]}')
    print(f'    Spread <= {BASELINE["SPREAD_MAX"]}')
    print(f'    Window: 3:00-3:29')
    print(f'    Max trades: 1/session')
    print()
    print('  VARIANTS:')
    for name, config in VARIANTS.items():
        print(f'    {name}: {config["description"]}')
    print()

    # Run all backtests
    all_results = {}

    for variant_name, config in VARIANTS.items():
        print(f'  Running {variant_name}...')
        all_results[variant_name] = {}

        for market in MARKETS:
            result = run_backtest(markets_dir, market, variant_name, config)
            all_results[variant_name][market] = result
            wr = safe_div(result.wins * 100, result.total_trades)
            print(f'    {market.upper()}: {result.total_trades} trades, {wr:.1f}% WR, ${result.total_pnl:.2f}')

        # Combined
        combined = combine_results(
            all_results[variant_name]['btc'],
            all_results[variant_name]['eth'],
            variant_name, config
        )
        all_results[variant_name]['combined'] = combined
        wr = safe_div(combined.wins * 100, combined.total_trades)
        print(f'    COMBINED: {combined.total_trades} trades, {wr:.1f}% WR, ${combined.total_pnl:.2f}')

    print()

    # Get baseline metrics for comparison
    base_btc = all_results['V0_BASELINE']['btc']
    base_eth = all_results['V0_BASELINE']['eth']
    base_combined = all_results['V0_BASELINE']['combined']

    # ================================================================
    # BTC RESULTS
    # ================================================================
    print('='*90)
    print('  BTC RESULTS')
    print('='*90)
    print()
    print(f"  {'Variant':<15} {'Trades':>8} {'Tr/Sess':>8} {'WinRate':>8} {'AvgPnL':>10} {'TotalPnL':>12} {'MaxDD':>10}")
    print(f"  {'-'*75}")

    for variant_name in VARIANTS.keys():
        r = all_results[variant_name]['btc']
        tr_sess = safe_div(r.total_trades, r.total_sessions) * 100
        wr = safe_div(r.wins * 100, r.total_trades)
        avg_pnl = safe_div(r.total_pnl, r.total_trades)
        marker = " *" if variant_name == 'V0_BASELINE' else ""
        print(f"  {variant_name:<15} {r.total_trades:>8} {tr_sess:>7.1f}% {wr:>7.2f}% ${avg_pnl:>9.4f} ${r.total_pnl:>11.2f} ${r.max_drawdown:>9.2f}{marker}")

    print()

    # ================================================================
    # ETH RESULTS
    # ================================================================
    print('='*90)
    print('  ETH RESULTS')
    print('='*90)
    print()
    print(f"  {'Variant':<15} {'Trades':>8} {'Tr/Sess':>8} {'WinRate':>8} {'AvgPnL':>10} {'TotalPnL':>12} {'MaxDD':>10}")
    print(f"  {'-'*75}")

    for variant_name in VARIANTS.keys():
        r = all_results[variant_name]['eth']
        tr_sess = safe_div(r.total_trades, r.total_sessions) * 100
        wr = safe_div(r.wins * 100, r.total_trades)
        avg_pnl = safe_div(r.total_pnl, r.total_trades)
        marker = " *" if variant_name == 'V0_BASELINE' else ""
        print(f"  {variant_name:<15} {r.total_trades:>8} {tr_sess:>7.1f}% {wr:>7.2f}% ${avg_pnl:>9.4f} ${r.total_pnl:>11.2f} ${r.max_drawdown:>9.2f}{marker}")

    print()

    # ================================================================
    # COMBINED (BTC+ETH) RESULTS
    # ================================================================
    print('='*90)
    print('  COMBINED (BTC+ETH) RESULTS')
    print('='*90)
    print()
    print(f"  {'Variant':<15} {'Trades':>8} {'Tr/Sess':>8} {'WinRate':>8} {'AvgPnL':>10} {'TotalPnL':>12} {'MaxDD':>10}")
    print(f"  {'-'*75}")

    for variant_name in VARIANTS.keys():
        r = all_results[variant_name]['combined']
        tr_sess = safe_div(r.total_trades, r.total_sessions) * 100
        wr = safe_div(r.wins * 100, r.total_trades)
        avg_pnl = safe_div(r.total_pnl, r.total_trades)
        marker = " *" if variant_name == 'V0_BASELINE' else ""
        print(f"  {variant_name:<15} {r.total_trades:>8} {tr_sess:>7.1f}% {wr:>7.2f}% ${avg_pnl:>9.4f} ${r.total_pnl:>11.2f} ${r.max_drawdown:>9.2f}{marker}")

    print()

    # ================================================================
    # RELATIVE TO BASELINE
    # ================================================================
    print('='*90)
    print('  RELATIVE TO BASELINE (BTC)')
    print('='*90)
    print()
    print(f"  {'Variant':<15} {'Trades':>12} {'AvgPnL':>12} {'TotalPnL':>12} {'MaxDD':>12}")
    print(f"  {'-'*60}")

    base_avg = safe_div(base_btc.total_pnl, base_btc.total_trades)

    for variant_name in VARIANTS.keys():
        r = all_results[variant_name]['btc']
        avg_pnl = safe_div(r.total_pnl, r.total_trades)

        trades_rel = safe_div(r.total_trades, base_btc.total_trades)
        avgpnl_rel = safe_div(avg_pnl, base_avg) if base_avg != 0 else 0
        pnl_rel = safe_div(r.total_pnl, base_btc.total_pnl) if base_btc.total_pnl != 0 else 0
        dd_rel = safe_div(r.max_drawdown, base_btc.max_drawdown) if base_btc.max_drawdown != 0 else 0

        if variant_name == 'V0_BASELINE':
            print(f"  {variant_name:<15} {'1.00x':>12} {'1.00x':>12} {'1.00x':>12} {'1.00x':>12}")
        else:
            print(f"  {variant_name:<15} {trades_rel:>11.2f}x {avgpnl_rel:>11.2f}x {pnl_rel:>11.2f}x {dd_rel:>11.2f}x")

    print()

    # ================================================================
    # QUALIFICATION CHECK
    # ================================================================
    print('='*90)
    print('  QUALIFICATION CHECK')
    print('='*90)
    print()
    print('  Criteria:')
    print('    - AvgPnL > 0')
    print(f'    - MaxDD < 2x Phase 1 (< ${base_btc.max_drawdown * 2:.2f} for BTC)')
    print()

    # Check each variant
    qualified = []

    for variant_name in VARIANTS.keys():
        if variant_name == 'V0_BASELINE':
            continue

        print(f"  {variant_name}:")

        for market_key in ['btc', 'eth', 'combined']:
            r = all_results[variant_name][market_key]
            avg_pnl = safe_div(r.total_pnl, r.total_trades)

            if market_key == 'btc':
                base = base_btc
            elif market_key == 'eth':
                base = base_eth
            else:
                base = base_combined

            dd_limit = base.max_drawdown * 2

            passes_avgpnl = avg_pnl > 0
            passes_dd = r.max_drawdown < dd_limit

            status = "PASS" if (passes_avgpnl and passes_dd) else "FAIL"
            reason = []
            if not passes_avgpnl:
                reason.append(f"AvgPnL={avg_pnl:.4f}<=0")
            if not passes_dd:
                reason.append(f"DD={r.max_drawdown:.2f}>={dd_limit:.2f}")

            trade_gain = r.total_trades - base.total_trades
            trade_pct = safe_div(trade_gain, base.total_trades) * 100

            label = market_key.upper()
            if passes_avgpnl and passes_dd:
                print(f"    {label:<8} {status} | +{trade_gain} trades (+{trade_pct:.1f}%) | AvgPnL=${avg_pnl:.4f} | DD=${r.max_drawdown:.2f}")
                qualified.append((variant_name, market_key, r.total_trades, trade_gain, avg_pnl, r.max_drawdown))
            else:
                print(f"    {label:<8} {status} | {', '.join(reason)}")

        print()

    # ================================================================
    # RANKING
    # ================================================================
    print('='*90)
    print('  QUALIFIED VARIANTS (sorted by trade count)')
    print('='*90)
    print()

    if qualified:
        # Sort by trade count descending
        qualified.sort(key=lambda x: x[2], reverse=True)

        print(f"  {'Rank':<6} {'Variant':<15} {'Market':<10} {'Trades':>8} {'Gain':>10} {'AvgPnL':>10} {'MaxDD':>10}")
        print(f"  {'-'*75}")

        for i, (variant, market, trades, gain, avg_pnl, dd) in enumerate(qualified, 1):
            print(f"  {i:<6} {variant:<15} {market.upper():<10} {trades:>8} {'+' + str(gain):>10} ${avg_pnl:>9.4f} ${dd:>9.2f}")

        print()

        # Best option
        best = qualified[0]
        print(f"  HIGHEST TRADE COUNT: {best[0]} on {best[1].upper()}")
        print(f"    Trades: {best[2]} (+{best[3]} vs baseline)")
        print(f"    AvgPnL: ${best[4]:.4f}")
        print(f"    MaxDD: ${best[5]:.2f}")
    else:
        print("  No variants qualified under the given criteria.")

    print()
    print('='*90)
    print('  NOTE: This is analysis only. Phase 1 config remains LOCKED.')
    print('='*90)

    # Save log
    with open(log_file, 'w') as f:
        f.write(f"RULEV3+ Phase 1 - Trade Frequency Variants Analysis\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n")
        f.write("="*60 + "\n\n")

        for variant_name in VARIANTS.keys():
            f.write(f"\n{variant_name}:\n")
            for market_key in ['btc', 'eth', 'combined']:
                r = all_results[variant_name][market_key]
                wr = safe_div(r.wins * 100, r.total_trades)
                avg = safe_div(r.total_pnl, r.total_trades)
                f.write(f"  {market_key.upper()}: {r.total_trades} trades, {wr:.2f}% WR, ${avg:.4f} AvgPnL, ${r.total_pnl:.2f} Total, ${r.max_drawdown:.2f} DD\n")

    print(f"\n  Log saved to: {log_file}")

if __name__ == '__main__':
    main()
