#!/usr/bin/env python3
"""
RULEV3+ Phase 1 - Cross-Market Backtest
========================================
READ-ONLY ANALYSIS - Does not change Phase 1 config

Compares BTC vs ETH performance with identical parameters:
  - Edge >= 0.64
  - Ask <= 0.72
  - Spread <= 0.02
  - CORE-only (3:00-3:29 elapsed)
  - Max 1 trade per session

Purpose: Determine if edge is market-structural or BTC-specific.
"""

import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
from datetime import datetime

# ============================================================
# LOCKED RULEV3+ CONFIG (Phase 1) - DO NOT MODIFY
# ============================================================
EDGE_THRESHOLD = 0.64
SAFETY_CAP = 0.72
SPREAD_MAX = 0.02
POSITION_SIZE = 5.0
CORE_START = 3.0       # 3:00 elapsed
CORE_END = 3.5         # 3:30 elapsed (29 seconds)

MARKETS = ['btc', 'eth']

@dataclass
class Trade:
    session: str
    market: str
    direction: str
    edge: float
    ask: float
    bid: float
    spread: float
    elapsed_mins: float
    won: bool
    pnl: float

@dataclass
class Result:
    market: str = ""
    total_sessions: int = 0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    sum_ask: float = 0.0
    sum_spread: float = 0.0
    sum_edge: float = 0.0
    trades: List[Trade] = field(default_factory=list)
    pnl_curve: List[float] = field(default_factory=list)

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

def simulate_session(session_path, market):
    """Simulate session with Phase 1 gates, CORE-only."""
    ticks_file = session_path / 'ticks.jsonl'
    if not ticks_file.exists():
        return None, None

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
        return None, None

    winner = get_winner(ticks)
    if not winner:
        return None, None

    for tick in ticks:
        elapsed = get_elapsed_mins(tick)

        # GATE: CORE window only
        if elapsed < CORE_START or elapsed >= CORE_END:
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

        # GATE: EDGE_GATE (Phase 1 - LOCKED)
        if edge < EDGE_THRESHOLD:
            continue

        # GATE: PRICE_GATE (Phase 1 - LOCKED)
        if ask > SAFETY_CAP:
            continue

        # GATE: SPREAD_GATE (Phase 1 - LOCKED)
        if spread > SPREAD_MAX:
            continue

        # ALL GATES PASSED - Entry
        won = (direction == winner)
        shares = POSITION_SIZE / ask
        pnl = (1.0 - ask) * shares if won else -POSITION_SIZE

        trade = Trade(
            session=session_path.name,
            market=market,
            direction=direction,
            edge=edge,
            ask=ask,
            bid=bid,
            spread=spread,
            elapsed_mins=elapsed,
            won=won,
            pnl=pnl
        )
        return trade, winner

    return None, winner

def run_backtest(markets_dir, market):
    """Run backtest for specific market."""
    result = Result(market=market.upper())

    prefix = f'{market}-updown-15m-'
    sessions = sorted([
        d for d in markets_dir.iterdir()
        if d.is_dir() and d.name.startswith(prefix)
    ])

    result.total_sessions = len(sessions)
    running_pnl = 0.0
    peak_pnl = 0.0

    for session_path in sessions:
        trade, _ = simulate_session(session_path, market)

        if trade:
            result.total_trades += 1
            result.trades.append(trade)

            if trade.won:
                result.wins += 1
            else:
                result.losses += 1

            result.total_pnl += trade.pnl
            result.sum_ask += trade.ask
            result.sum_spread += trade.spread
            result.sum_edge += trade.edge

            running_pnl += trade.pnl
            result.pnl_curve.append(running_pnl)

            if running_pnl > peak_pnl:
                peak_pnl = running_pnl
            dd = peak_pnl - running_pnl
            if dd > result.max_drawdown:
                result.max_drawdown = dd

    return result

def safe_div(a, b):
    return a / b if b > 0 else 0

def print_market_result(r):
    """Print result table for a single market."""
    print(f"\n{'='*60}")
    print(f"  {r.market} MARKET RESULTS")
    print(f"{'='*60}")
    print(f"  {'Metric':<25} {'Value':>15}")
    print(f"  {'-'*40}")
    print(f"  {'Total sessions':<25} {r.total_sessions:>15}")
    print(f"  {'Total trades':<25} {r.total_trades:>15}")
    print(f"  {'Trades per session':<25} {safe_div(r.total_trades, r.total_sessions)*100:>14.2f}%")
    print(f"  {'Wins':<25} {r.wins:>15}")
    print(f"  {'Losses':<25} {r.losses:>15}")
    print(f"  {'Win rate (%)':<25} {safe_div(r.wins * 100, r.total_trades):>15.2f}")
    print(f"  {'AvgPnL per trade ($)':<25} {safe_div(r.total_pnl, r.total_trades):>15.4f}")
    print(f"  {'Total PnL ($)':<25} {r.total_pnl:>15.2f}")
    print(f"  {'Max drawdown ($)':<25} {r.max_drawdown:>15.2f}")
    print(f"  {'Avg edge at entry':<25} {safe_div(r.sum_edge, r.total_trades):>15.4f}")
    print(f"  {'Avg ask at entry':<25} {safe_div(r.sum_ask, r.total_trades):>15.4f}")
    print(f"  {'Avg spread at entry':<25} {safe_div(r.sum_spread, r.total_trades):>15.4f}")

def print_comparison(results):
    """Print side-by-side comparison."""
    btc, eth = results

    print(f"\n{'='*70}")
    print(f"  CROSS-MARKET COMPARISON")
    print(f"{'='*70}")
    print(f"  {'Metric':<25} {'BTC':>15} {'ETH':>15} {'Delta':>12}")
    print(f"  {'-'*67}")

    # Trade frequency
    btc_freq = safe_div(btc.total_trades, btc.total_sessions) * 100
    eth_freq = safe_div(eth.total_trades, eth.total_sessions) * 100
    print(f"  {'Sessions':<25} {btc.total_sessions:>15} {eth.total_sessions:>15} {'-':>12}")
    print(f"  {'Trades':<25} {btc.total_trades:>15} {eth.total_trades:>15} {eth.total_trades - btc.total_trades:>+12}")
    print(f"  {'Trade frequency (%)':<25} {btc_freq:>15.2f} {eth_freq:>15.2f} {eth_freq - btc_freq:>+12.2f}")

    print(f"  {'-'*67}")

    # Win rate
    btc_wr = safe_div(btc.wins * 100, btc.total_trades)
    eth_wr = safe_div(eth.wins * 100, eth.total_trades)
    print(f"  {'Win rate (%)':<25} {btc_wr:>15.2f} {eth_wr:>15.2f} {eth_wr - btc_wr:>+12.2f}")

    # AvgPnL
    btc_avg = safe_div(btc.total_pnl, btc.total_trades)
    eth_avg = safe_div(eth.total_pnl, eth.total_trades)
    print(f"  {'AvgPnL ($)':<25} {btc_avg:>15.4f} {eth_avg:>15.4f} {eth_avg - btc_avg:>+12.4f}")

    # Total PnL
    print(f"  {'Total PnL ($)':<25} {btc.total_pnl:>15.2f} {eth.total_pnl:>15.2f} {eth.total_pnl - btc.total_pnl:>+12.2f}")

    print(f"  {'-'*67}")

    # Max DD
    print(f"  {'Max Drawdown ($)':<25} {btc.max_drawdown:>15.2f} {eth.max_drawdown:>15.2f} {eth.max_drawdown - btc.max_drawdown:>+12.2f}")

    # Avg metrics
    btc_edge = safe_div(btc.sum_edge, btc.total_trades)
    eth_edge = safe_div(eth.sum_edge, eth.total_trades)
    print(f"  {'Avg edge':<25} {btc_edge:>15.4f} {eth_edge:>15.4f} {eth_edge - btc_edge:>+12.4f}")

    btc_ask = safe_div(btc.sum_ask, btc.total_trades)
    eth_ask = safe_div(eth.sum_ask, eth.total_trades)
    print(f"  {'Avg ask':<25} {btc_ask:>15.4f} {eth_ask:>15.4f} {eth_ask - btc_ask:>+12.4f}")

    btc_spread = safe_div(btc.sum_spread, btc.total_trades)
    eth_spread = safe_div(eth.sum_spread, eth.total_trades)
    print(f"  {'Avg spread':<25} {btc_spread:>15.4f} {eth_spread:>15.4f} {eth_spread - btc_spread:>+12.4f}")

def print_portfolio(results):
    """Print combined portfolio results."""
    btc, eth = results

    # Combine trades chronologically by session timestamp
    all_trades = btc.trades + eth.trades
    # Sort by session name (which includes timestamp)
    all_trades.sort(key=lambda t: t.session.split('-')[-1])

    total_trades = len(all_trades)
    wins = sum(1 for t in all_trades if t.won)
    losses = total_trades - wins
    total_pnl = sum(t.pnl for t in all_trades)

    # Calculate combined drawdown
    running_pnl = 0.0
    peak_pnl = 0.0
    max_dd = 0.0
    for t in all_trades:
        running_pnl += t.pnl
        if running_pnl > peak_pnl:
            peak_pnl = running_pnl
        dd = peak_pnl - running_pnl
        if dd > max_dd:
            max_dd = dd

    print(f"\n{'='*60}")
    print(f"  COMBINED PORTFOLIO (BTC + ETH)")
    print(f"{'='*60}")
    print(f"  {'Metric':<25} {'Value':>15}")
    print(f"  {'-'*40}")
    print(f"  {'Total trades':<25} {total_trades:>15}")
    print(f"  {'Wins':<25} {wins:>15}")
    print(f"  {'Losses':<25} {losses:>15}")
    print(f"  {'Win rate (%)':<25} {safe_div(wins * 100, total_trades):>15.2f}")
    print(f"  {'AvgPnL per trade ($)':<25} {safe_div(total_pnl, total_trades):>15.4f}")
    print(f"  {'Total PnL ($)':<25} {total_pnl:>15.2f}")
    print(f"  {'Max drawdown ($)':<25} {max_dd:>15.2f}")
    print(f"  {'-'*40}")
    print(f"  {'BTC contribution ($)':<25} {btc.total_pnl:>15.2f}")
    print(f"  {'ETH contribution ($)':<25} {eth.total_pnl:>15.2f}")

    return total_trades, wins, total_pnl, max_dd

def print_conclusion(results, portfolio_stats):
    """Print analysis conclusion."""
    btc, eth = results
    total_trades, wins, total_pnl, max_dd = portfolio_stats

    btc_wr = safe_div(btc.wins * 100, btc.total_trades)
    eth_wr = safe_div(eth.wins * 100, eth.total_trades)
    btc_avg = safe_div(btc.total_pnl, btc.total_trades)
    eth_avg = safe_div(eth.total_pnl, eth.total_trades)

    print(f"\n{'='*70}")
    print(f"  CONCLUSION: IS EDGE MARKET-STRUCTURAL OR BTC-SPECIFIC?")
    print(f"{'='*70}")

    # Analyze differences
    wr_diff = abs(btc_wr - eth_wr)
    avg_diff = abs(btc_avg - eth_avg)

    print()
    print(f"  1. WIN RATE COMPARISON")
    print(f"     BTC: {btc_wr:.2f}%  |  ETH: {eth_wr:.2f}%  |  Diff: {wr_diff:.2f}%")
    if wr_diff < 3:
        print(f"     >> SIMILAR: Win rates within 3% tolerance")
    elif btc_wr > eth_wr:
        print(f"     >> BTC STRONGER: {wr_diff:.2f}% higher win rate")
    else:
        print(f"     >> ETH STRONGER: {wr_diff:.2f}% higher win rate")

    print()
    print(f"  2. AVGPNL COMPARISON")
    print(f"     BTC: ${btc_avg:.4f}  |  ETH: ${eth_avg:.4f}  |  Diff: ${avg_diff:.4f}")
    if avg_diff < 0.05:
        print(f"     >> SIMILAR: AvgPnL within $0.05 tolerance")
    elif btc_avg > eth_avg:
        print(f"     >> BTC STRONGER: ${avg_diff:.4f} higher per trade")
    else:
        print(f"     >> ETH STRONGER: ${avg_diff:.4f} higher per trade")

    print()
    print(f"  3. TRADE FREQUENCY")
    btc_freq = safe_div(btc.total_trades, btc.total_sessions) * 100
    eth_freq = safe_div(eth.total_trades, eth.total_sessions) * 100
    print(f"     BTC: {btc_freq:.2f}%  |  ETH: {eth_freq:.2f}%")
    if abs(btc_freq - eth_freq) < 5:
        print(f"     >> SIMILAR: Both markets trigger at similar rates")
    elif btc_freq > eth_freq:
        print(f"     >> BTC MORE ACTIVE: {btc_freq - eth_freq:.2f}% more trades")
    else:
        print(f"     >> ETH MORE ACTIVE: {eth_freq - btc_freq:.2f}% more trades")

    print()
    print(f"  4. DRAWDOWN COMPARISON")
    print(f"     BTC: ${btc.max_drawdown:.2f}  |  ETH: ${eth.max_drawdown:.2f}")
    if btc.max_drawdown < eth.max_drawdown:
        print(f"     >> BTC SAFER: Lower max drawdown")
    elif eth.max_drawdown < btc.max_drawdown:
        print(f"     >> ETH SAFER: Lower max drawdown")
    else:
        print(f"     >> SIMILAR: Same drawdown profile")

    print()
    print(f"  5. PORTFOLIO BENEFIT")
    individual_max_dd = max(btc.max_drawdown, eth.max_drawdown)
    if max_dd < individual_max_dd:
        print(f"     Combined DD (${max_dd:.2f}) < Worst Individual (${individual_max_dd:.2f})")
        print(f"     >> DIVERSIFICATION HELPS: {((individual_max_dd - max_dd) / individual_max_dd * 100):.1f}% DD reduction")
    else:
        print(f"     Combined DD (${max_dd:.2f}) >= Individual DDs")
        print(f"     >> LIMITED DIVERSIFICATION BENEFIT")

    # Final verdict
    print()
    print(f"  {'-'*67}")
    print()

    both_positive = btc.total_pnl > 0 and eth.total_pnl > 0
    both_above_70 = btc_wr >= 70 and eth_wr >= 70

    if both_positive and both_above_70 and wr_diff < 5:
        print(f"  VERDICT: EDGE IS MARKET-STRUCTURAL")
        print(f"  ")
        print(f"  The RULEV3+ edge works on BOTH markets with similar characteristics:")
        print(f"  - Both markets profitable (BTC: ${btc.total_pnl:.2f}, ETH: ${eth.total_pnl:.2f})")
        print(f"  - Both win rates above 70% (BTC: {btc_wr:.2f}%, ETH: {eth_wr:.2f}%)")
        print(f"  - Parameters transfer without modification")
        print(f"  ")
        print(f"  This suggests the edge captures a structural inefficiency in")
        print(f"  Polymarket's updown prediction markets, not BTC-specific behavior.")
    elif both_positive:
        print(f"  VERDICT: EDGE IS TRANSFERABLE (with differences)")
        print(f"  ")
        print(f"  Both markets are profitable but with notable differences:")
        if btc_wr > eth_wr:
            print(f"  - BTC has stronger win rate ({btc_wr:.2f}% vs {eth_wr:.2f}%)")
        else:
            print(f"  - ETH has stronger win rate ({eth_wr:.2f}% vs {btc_wr:.2f}%)")
        print(f"  - Parameters work on both but may not be optimal for ETH")
    elif btc.total_pnl > 0 and eth.total_pnl <= 0:
        print(f"  VERDICT: EDGE IS BTC-SPECIFIC")
        print(f"  ")
        print(f"  Only BTC is profitable. ETH does not respond to same parameters.")
        print(f"  The edge may be capturing BTC-specific market microstructure.")
    elif eth.total_pnl > 0 and btc.total_pnl <= 0:
        print(f"  VERDICT: EDGE IS ETH-SPECIFIC (unexpected)")
        print(f"  ")
        print(f"  Only ETH is profitable. Parameters may need BTC-specific tuning.")
    else:
        print(f"  VERDICT: EDGE NOT CONFIRMED")
        print(f"  ")
        print(f"  Neither market shows positive PnL with these parameters.")

    print()
    print(f"{'='*70}")

def main():
    markets_dir = Path(__file__).parent.parent / 'markets_paper'
    log_dir = Path(__file__).parent.parent / 'backtest_full_logs' / 'cross_market'
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = log_dir / f'cross_market_{timestamp}.log'

    print('='*70)
    print('  RULEV3+ PHASE 1 - CROSS-MARKET BACKTEST')
    print('  READ-ONLY ANALYSIS - Phase 1 config unchanged')
    print('='*70)
    print()
    print('  LOCKED CONFIG:')
    print(f'    Edge threshold: >= {EDGE_THRESHOLD}')
    print(f'    Safety cap:     <= {SAFETY_CAP}')
    print(f'    Spread gate:    <= {SPREAD_MAX}')
    print(f'    Position size:  ${POSITION_SIZE:.2f}')
    print(f'    Window:         CORE only ({CORE_START:.2f}-{CORE_END:.2f} mins)')
    print()

    # Run backtests
    results = []
    for market in MARKETS:
        prefix = f'{market}-updown-15m-'
        count = len([d for d in markets_dir.iterdir() if d.is_dir() and d.name.startswith(prefix)])
        print(f'  Running {market.upper()}... ({count} sessions)')
        result = run_backtest(markets_dir, market)
        results.append(result)
        print(f'    Trades: {result.total_trades}, WR: {safe_div(result.wins*100, result.total_trades):.2f}%, PnL: ${result.total_pnl:.2f}')

    # Print individual results
    for r in results:
        print_market_result(r)

    # Print comparison
    print_comparison(results)

    # Print portfolio
    portfolio_stats = print_portfolio(results)

    # Print conclusion
    print_conclusion(results, portfolio_stats)

    # Save to log file
    with open(log_file, 'w') as f:
        f.write(f"RULEV3+ Phase 1 - Cross-Market Backtest\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n")
        f.write(f"="*60 + "\n\n")

        for r in results:
            f.write(f"{r.market}:\n")
            f.write(f"  Sessions: {r.total_sessions}\n")
            f.write(f"  Trades: {r.total_trades}\n")
            f.write(f"  Win rate: {safe_div(r.wins*100, r.total_trades):.2f}%\n")
            f.write(f"  AvgPnL: ${safe_div(r.total_pnl, r.total_trades):.4f}\n")
            f.write(f"  Total PnL: ${r.total_pnl:.2f}\n")
            f.write(f"  Max DD: ${r.max_drawdown:.2f}\n")
            f.write("\n")

        total_trades, wins, total_pnl, max_dd = portfolio_stats
        f.write(f"PORTFOLIO:\n")
        f.write(f"  Total trades: {total_trades}\n")
        f.write(f"  Win rate: {safe_div(wins*100, total_trades):.2f}%\n")
        f.write(f"  Total PnL: ${total_pnl:.2f}\n")
        f.write(f"  Max DD: ${max_dd:.2f}\n")

    print(f"\n  Log saved to: {log_file}")

if __name__ == '__main__':
    main()
