#!/usr/bin/env python3
"""
Multi-Signal Data Analyzer

Analyzes recorded session data to find predictive signals.
Run after collecting data with multi_signal_recorder.

Usage:
    python3 analyze_data.py ./logs BTC
    python3 analyze_data.py ./test_logs BTC --verbose
"""

import json
import sys
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Optional, Tuple
import statistics

def load_sessions(log_dir: str, symbol: str) -> List[dict]:
    """Load all sessions from JSONL file."""
    filename = Path(log_dir) / f"multi_signal_sessions_{symbol.lower()}.jsonl"

    if not filename.exists():
        print(f"Error: File not found: {filename}")
        return []

    sessions = []
    with open(filename) as f:
        for line in f:
            try:
                session = json.loads(line)
                if session.get('winner'):
                    sessions.append(session)
            except json.JSONDecodeError:
                continue

    return sessions


def analyze_price_correlation(sessions: List[dict]) -> Dict[str, Dict[str, float]]:
    """Analyze price direction correlation with final winner."""

    price_sources = [
        'binance_spot', 'binance_futures', 'coinbase', 'bybit',
        'kraken', 'okx', 'chainlink_rtds', 'pyth'
    ]

    # Skip T+0 and T+14m59s (baseline)
    checkpoints = [
        'T+15s', 'T+30s', 'T+45s', 'T+60s', 'T+90s', 'T+2m', 'T+3m',
        'T+5m', 'T+7m', 'T+10m', 'T+12m', 'T+13m', 'T+14m', 'T+14m30s', 'T+14m45s'
    ]

    results = {}

    for checkpoint in checkpoints:
        results[checkpoint] = {}

        for source in price_sources:
            correct = 0
            total = 0

            for session in sessions:
                snapshots = {s['checkpoint']: s for s in session['snapshots']}

                t0 = snapshots.get('T+0')
                cp = snapshots.get(checkpoint)
                winner = session.get('winner')

                if not (t0 and cp and winner):
                    continue

                p0 = t0.get(source)
                p1 = cp.get(source)

                if p0 is not None and p1 is not None:
                    predicted = 'UP' if p1 > p0 else 'DOWN'
                    total += 1
                    if predicted == winner:
                        correct += 1

            if total > 0:
                results[checkpoint][source] = 100.0 * correct / total
            else:
                results[checkpoint][source] = None

    return results


def analyze_sentiment_correlation(sessions: List[dict]) -> Dict[str, Dict[str, float]]:
    """Analyze sentiment signal correlation with final winner."""

    checkpoints = [
        'T+0', 'T+30s', 'T+60s', 'T+2m', 'T+5m', 'T+10m', 'T+14m'
    ]

    results = {}

    for checkpoint in checkpoints:
        results[checkpoint] = {}

        # Funding rate: negative = UP
        correct, total = 0, 0
        for session in sessions:
            snapshots = {s['checkpoint']: s for s in session['snapshots']}
            cp = snapshots.get(checkpoint)
            winner = session.get('winner')

            if cp and winner:
                fr = cp.get('funding_rate')
                if fr is not None:
                    predicted = 'UP' if fr < 0 else 'DOWN'
                    total += 1
                    if predicted == winner:
                        correct += 1

        results[checkpoint]['funding_rate'] = 100.0 * correct / total if total > 0 else None

        # Long/Short ratio: <1 = UP
        correct, total = 0, 0
        for session in sessions:
            snapshots = {s['checkpoint']: s for s in session['snapshots']}
            cp = snapshots.get(checkpoint)
            winner = session.get('winner')

            if cp and winner:
                ls = cp.get('long_short_ratio')
                if ls is not None:
                    predicted = 'UP' if ls < 1 else 'DOWN'
                    total += 1
                    if predicted == winner:
                        correct += 1

        results[checkpoint]['long_short_ratio'] = 100.0 * correct / total if total > 0 else None

        # Orderbook imbalance: positive = UP
        correct, total = 0, 0
        for session in sessions:
            snapshots = {s['checkpoint']: s for s in session['snapshots']}
            cp = snapshots.get(checkpoint)
            winner = session.get('winner')

            if cp and winner:
                ob = cp.get('orderbook_imbalance')
                if ob is not None:
                    predicted = 'UP' if ob > 0 else 'DOWN'
                    total += 1
                    if predicted == winner:
                        correct += 1

        results[checkpoint]['orderbook_imbalance'] = 100.0 * correct / total if total > 0 else None

        # CVD: positive = UP
        correct, total = 0, 0
        for session in sessions:
            snapshots = {s['checkpoint']: s for s in session['snapshots']}
            cp = snapshots.get(checkpoint)
            winner = session.get('winner')

            if cp and winner:
                cvd = cp.get('cvd')
                if cvd is not None:
                    predicted = 'UP' if cvd > 0 else 'DOWN'
                    total += 1
                    if predicted == winner:
                        correct += 1

        results[checkpoint]['cvd'] = 100.0 * correct / total if total > 0 else None

        # Liquidations: more long liqs = DOWN
        correct, total = 0, 0
        for session in sessions:
            snapshots = {s['checkpoint']: s for s in session['snapshots']}
            cp = snapshots.get(checkpoint)
            winner = session.get('winner')

            if cp and winner:
                long_liqs = cp.get('long_liquidations', 0) or 0
                short_liqs = cp.get('short_liquidations', 0) or 0
                if long_liqs > 0 or short_liqs > 0:
                    predicted = 'DOWN' if long_liqs > short_liqs else 'UP'
                    total += 1
                    if predicted == winner:
                        correct += 1

        results[checkpoint]['liquidations'] = 100.0 * correct / total if total > 0 else None

    return results


def find_best_signals(price_corr: dict, sentiment_corr: dict) -> List[Tuple[str, str, float]]:
    """Find the best predictive signals at each checkpoint."""

    best_signals = []

    all_checkpoints = set(price_corr.keys()) | set(sentiment_corr.keys())

    for checkpoint in sorted(all_checkpoints, key=lambda x: parse_checkpoint(x)):
        best_source = None
        best_corr = 50.0

        # Check price sources
        if checkpoint in price_corr:
            for source, corr in price_corr[checkpoint].items():
                if corr is not None and corr > best_corr:
                    best_corr = corr
                    best_source = source

        # Check sentiment sources
        if checkpoint in sentiment_corr:
            for source, corr in sentiment_corr[checkpoint].items():
                if corr is not None and corr > best_corr:
                    best_corr = corr
                    best_source = source

        if best_source and best_corr > 50.0:
            best_signals.append((checkpoint, best_source, best_corr))

    return best_signals


def parse_checkpoint(cp: str) -> int:
    """Convert checkpoint string to seconds for sorting."""
    if cp == 'T+0':
        return 0

    cp = cp.replace('T+', '')

    if 'm' in cp and 's' in cp:
        # T+14m30s format
        parts = cp.replace('s', '').split('m')
        return int(parts[0]) * 60 + int(parts[1])
    elif 'm' in cp:
        return int(cp.replace('m', '')) * 60
    elif 's' in cp:
        return int(cp.replace('s', ''))

    return 0


def print_results(sessions: List[dict], price_corr: dict, sentiment_corr: dict, verbose: bool = False):
    """Print analysis results."""

    print("=" * 80)
    print("MULTI-SIGNAL CORRELATION ANALYSIS")
    print("=" * 80)
    print()
    print(f"Sessions analyzed: {len(sessions)}")

    # Count winners
    up_wins = sum(1 for s in sessions if s.get('winner') == 'UP')
    down_wins = len(sessions) - up_wins
    print(f"UP wins: {up_wins} ({100*up_wins/len(sessions):.1f}%)")
    print(f"DOWN wins: {down_wins} ({100*down_wins/len(sessions):.1f}%)")
    print()

    # Price correlation table
    print("=" * 80)
    print("PRICE SOURCE DIRECTION CORRELATION")
    print("=" * 80)
    print()

    price_sources = ['binance_spot', 'binance_futures', 'coinbase', 'bybit',
                     'kraken', 'okx', 'chainlink_rtds', 'pyth']

    header = f"{'CHECKPOINT':<12}"
    for src in price_sources:
        short_name = src.replace('binance_', 'BIN_').replace('_spot', 'S').replace('_futures', 'F')
        short_name = short_name.replace('chainlink_rtds', 'CHAIN').upper()[:7]
        header += f" {short_name:>7}"
    print(header)
    print("-" * (12 + 8 * len(price_sources)))

    for checkpoint in sorted(price_corr.keys(), key=parse_checkpoint):
        row = f"{checkpoint:<12}"
        for src in price_sources:
            corr = price_corr[checkpoint].get(src)
            if corr is not None:
                # Highlight good correlations
                if corr >= 55:
                    row += f" {corr:>6.1f}%"
                else:
                    row += f" {corr:>6.1f}%"
            else:
                row += f" {'N/A':>7}"
        print(row)

    print()

    # Sentiment correlation table
    print("=" * 80)
    print("SENTIMENT SIGNAL CORRELATION")
    print("=" * 80)
    print()

    sentiment_sources = ['funding_rate', 'long_short_ratio', 'orderbook_imbalance', 'cvd', 'liquidations']

    header = f"{'CHECKPOINT':<12}"
    for src in sentiment_sources:
        short_name = src.replace('_', ' ').title()[:8]
        header += f" {short_name:>8}"
    print(header)
    print("-" * (12 + 9 * len(sentiment_sources)))

    for checkpoint in sorted(sentiment_corr.keys(), key=parse_checkpoint):
        row = f"{checkpoint:<12}"
        for src in sentiment_sources:
            corr = sentiment_corr[checkpoint].get(src)
            if corr is not None:
                row += f" {corr:>7.1f}%"
            else:
                row += f" {'N/A':>8}"
        print(row)

    print()

    # Best signals
    print("=" * 80)
    print("BEST PREDICTORS BY CHECKPOINT")
    print("=" * 80)
    print()

    best_signals = find_best_signals(price_corr, sentiment_corr)

    for checkpoint, source, corr in best_signals:
        stars = "*" * int((corr - 50) / 2)  # More stars for better correlation
        print(f"  {checkpoint:<12} {source:<20} {corr:>5.1f}% {stars}")

    print()

    # Recommendations
    print("=" * 80)
    print("RECOMMENDATIONS")
    print("=" * 80)
    print()

    # Find earliest checkpoint with >55% accuracy
    early_signals = [(cp, src, corr) for cp, src, corr in best_signals
                     if corr >= 55 and parse_checkpoint(cp) <= 300]

    if early_signals:
        print(f"TRADEABLE SIGNALS (>55% before T+5m):")
        for cp, src, corr in early_signals:
            print(f"  - {src} at {cp}: {corr:.1f}%")
        print()

        best_early = max(early_signals, key=lambda x: x[2])
        print(f"BEST EARLY SIGNAL: {best_early[1]} at {best_early[0]} ({best_early[2]:.1f}%)")
        print(f"  -> You have {(900 - parse_checkpoint(best_early[0])) // 60} minutes to trade")
    else:
        print("No signals with >55% accuracy found before T+5m")
        print("Need more data or different signal sources")

    print()

    if len(sessions) < 50:
        print("WARNING: Only {len(sessions)} sessions analyzed.")
        print("Need 50+ sessions for statistically significant results.")
        print("Run the recorder for at least 12 hours.")


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 analyze_data.py <log_dir> <symbol> [--verbose]")
        print()
        print("Example:")
        print("  python3 analyze_data.py ./logs BTC")
        print("  python3 analyze_data.py ./test_logs ETH --verbose")
        sys.exit(1)

    log_dir = sys.argv[1]
    symbol = sys.argv[2].upper()
    verbose = '--verbose' in sys.argv

    print(f"Loading sessions from {log_dir} for {symbol}...")
    sessions = load_sessions(log_dir, symbol)

    if not sessions:
        print("No sessions with winners found.")
        print("Run the recorder longer to collect complete sessions.")
        sys.exit(1)

    print(f"Loaded {len(sessions)} sessions")
    print()

    print("Analyzing price correlation...")
    price_corr = analyze_price_correlation(sessions)

    print("Analyzing sentiment correlation...")
    sentiment_corr = analyze_sentiment_correlation(sessions)

    print()
    print_results(sessions, price_corr, sentiment_corr, verbose)


if __name__ == '__main__':
    main()
