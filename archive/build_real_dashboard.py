"""
REAL Validation Dashboard
- All data derived from source files
- No hardcoded values
- Every trade traceable to every chart element
- Re-runnable: add data, run script, charts update
"""

import json
import re
import os
from datetime import datetime
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).parent
PAPER_LOG = BASE_DIR / "logs" / "paper" / "trades_20251224_194001.log"
BACKTEST_CORE = BASE_DIR / "backtest_full_logs" / "runs" / "rulev3_core_only_t3_064_cap072" / "trades_full.jsonl"
OUTPUT = BASE_DIR / "docs" / "validation_dashboard_real.html"


def parse_live_trades():
    """
    SOURCE: logs/paper/trades_20251224_194001.log
    SCHEMA: timestamp, elapsed_seconds, direction, edge, ask, outcome, pnl
    """
    trades = []

    with open(PAPER_LOG, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    # Find SIGNAL entries
    signal_pattern = r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[SIGNAL\] CORE\(T3\) (\w+) edge=([\d.]+) ask=([\d.]+)'
    result_pattern = r'TRADE #(\d+):.*?\n.*?RESULT: (WIN|LOSS) \| PnL: ([\+\-\$\d.]+)'

    signals = list(re.finditer(signal_pattern, content))
    results = list(re.finditer(result_pattern, content))

    for i, signal in enumerate(signals):
        ts_str = signal.group(1)
        direction = signal.group(2)
        edge = float(signal.group(3))
        ask = float(signal.group(4))

        ts = datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')

        # Calculate elapsed seconds into 15-min session
        minute = ts.minute
        second = ts.second
        session_start_minute = (minute // 15) * 15
        elapsed = (minute - session_start_minute) * 60 + second

        outcome = None
        pnl = 0.0
        if i < len(results):
            outcome = results[i].group(2) == 'WIN'
            pnl = float(results[i].group(3).replace('$', '').replace('+', ''))

        trades.append({
            'trade_num': i + 1,
            'timestamp': ts_str,
            'elapsed_seconds': elapsed,
            'direction': direction,
            'edge': edge,
            'ask': ask,
            'outcome': outcome,
            'pnl': pnl
        })

    return trades


def parse_backtest_trades():
    """
    SOURCE: backtest_full_logs/runs/rulev3_core_only_t3_064_cap072/trades_full.jsonl
    SCHEMA: session, direction, entry_tau, elapsed_seconds, edge, entry_ask, outcome, pnl
    """
    trades = []

    with open(BACKTEST_CORE, 'r') as f:
        for idx, line in enumerate(f):
            row = json.loads(line.strip())
            trades.append({
                'trade_num': idx + 1,
                'session': row.get('session', ''),
                'elapsed_seconds': row.get('elapsed_seconds', 899 - row.get('entry_tau', 0)),
                'direction': row.get('direction', ''),
                'edge': row.get('edge', 0),
                'ask': row.get('entry_ask', 0),
                'outcome': row.get('outcome', False),
                'pnl': row.get('pnl', 0)
            })

    return trades


def compute_equity_curve(trades):
    """Cumulative sum of per-trade PnL. No scaling."""
    cumulative = []
    total = 0.0
    for t in trades:
        total += t['pnl']
        cumulative.append(round(total, 4))
    return cumulative


def compute_timing_bins(trades, bin_size=5):
    """
    Group trades by elapsed_seconds into bins.
    Returns: {bin_start: count}
    """
    bins = defaultdict(int)
    for t in trades:
        bin_start = int(t['elapsed_seconds'] // bin_size) * bin_size
        bins[bin_start] += 1
    return dict(sorted(bins.items()))


def compute_heatmap(trades):
    """
    1-second resolution count for CORE window (180-209s).
    Returns: {second: count}
    """
    counts = defaultdict(int)
    for t in trades:
        sec = int(t['elapsed_seconds'])
        if 180 <= sec <= 209:
            counts[sec] += 1
    return dict(sorted(counts.items()))


def build_trade_index(trades):
    """
    Build index: trade_num -> which bins it appears in.
    For audit trail.
    """
    index = {}
    for t in trades:
        sec = int(t['elapsed_seconds'])
        bin_5s = (sec // 5) * 5
        index[t['trade_num']] = {
            'timestamp': t.get('timestamp', t.get('session', '')),
            'elapsed_seconds': t['elapsed_seconds'],
            'timing_bin': f"{bin_5s}s",
            'heatmap_second': f"{sec}s" if 180 <= sec <= 209 else "outside CORE",
            'pnl': t['pnl'],
            'outcome': 'WIN' if t['outcome'] else 'LOSS'
        }
    return index


def generate_html(live_trades, bt_trades):
    """Generate HTML with ALL data derived from source."""

    # Compute everything from raw data
    live_equity = compute_equity_curve(live_trades)
    bt_equity = compute_equity_curve(bt_trades)

    live_timing = compute_timing_bins(live_trades)
    bt_timing = compute_timing_bins(bt_trades)

    live_heatmap = compute_heatmap(live_trades)
    bt_heatmap = compute_heatmap(bt_trades)

    live_index = build_trade_index(live_trades)
    bt_index = build_trade_index(bt_trades[:50])  # First 50 for display

    # Stats from raw data
    live_wins = sum(1 for t in live_trades if t['outcome'])
    live_wr = round(live_wins / len(live_trades) * 100, 2) if live_trades else 0
    live_total_pnl = round(sum(t['pnl'] for t in live_trades), 2)
    live_avg_pnl = round(live_total_pnl / len(live_trades), 4) if live_trades else 0

    bt_wins = sum(1 for t in bt_trades if t['outcome'])
    bt_wr = round(bt_wins / len(bt_trades) * 100, 2) if bt_trades else 0
    bt_total_pnl = round(sum(t['pnl'] for t in bt_trades), 2)
    bt_avg_pnl = round(bt_total_pnl / len(bt_trades), 4) if bt_trades else 0

    # Timing bin labels (union of both datasets)
    all_bins = sorted(set(live_timing.keys()) | set(bt_timing.keys()))
    timing_labels = [f"{b}s" for b in all_bins]
    live_timing_data = [live_timing.get(b, 0) for b in all_bins]
    bt_timing_data = [bt_timing.get(b, 0) for b in all_bins]

    # Heatmap labels (180-209)
    heatmap_labels = [f"3:{str(s-180).zfill(2)}" for s in range(180, 210)]
    live_heatmap_data = [live_heatmap.get(s, 0) for s in range(180, 210)]
    bt_heatmap_data = [bt_heatmap.get(s, 0) for s in range(180, 210)]

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>RULEV3+ Real Validation Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Consolas', 'Monaco', monospace;
            background: #0d1117;
            color: #c9d1d9;
            padding: 20px;
        }}
        .container {{ max-width: 1400px; margin: 0 auto; }}

        .header {{
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 20px;
        }}
        h1 {{ color: #58a6ff; font-size: 1.5rem; margin-bottom: 8px; }}
        .meta {{ color: #8b949e; font-size: 0.85rem; }}
        .meta code {{ background: #21262d; padding: 2px 6px; border-radius: 4px; }}

        .source-box {{
            background: #0d1117;
            border: 1px solid #238636;
            border-radius: 8px;
            padding: 16px;
            margin-bottom: 20px;
        }}
        .source-box h3 {{ color: #3fb950; font-size: 0.9rem; margin-bottom: 12px; }}
        .source-box pre {{
            background: #161b22;
            padding: 12px;
            border-radius: 4px;
            overflow-x: auto;
            font-size: 0.8rem;
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 16px;
            margin-bottom: 20px;
        }}
        .stat-card {{
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 16px;
        }}
        .stat-card h3 {{ color: #8b949e; font-size: 0.75rem; text-transform: uppercase; margin-bottom: 12px; }}
        .stat-card.live {{ border-left: 3px solid #3fb950; }}
        .stat-card.backtest {{ border-left: 3px solid #58a6ff; }}
        .stat-row {{ display: flex; justify-content: space-between; padding: 6px 0; }}
        .stat-label {{ color: #8b949e; }}
        .stat-value {{ font-weight: bold; }}
        .positive {{ color: #3fb950; }}
        .negative {{ color: #f85149; }}

        .chart-box {{
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 20px;
        }}
        .chart-box h3 {{ color: #58a6ff; font-size: 1rem; margin-bottom: 16px; }}
        .chart-wrapper {{ height: 300px; }}

        .audit-box {{
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 20px;
        }}
        .audit-box h3 {{ color: #d29922; font-size: 1rem; margin-bottom: 16px; }}
        .audit-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.8rem;
        }}
        .audit-table th, .audit-table td {{
            padding: 8px;
            text-align: left;
            border-bottom: 1px solid #21262d;
        }}
        .audit-table th {{ color: #8b949e; }}

        .footer {{
            text-align: center;
            color: #8b949e;
            font-size: 0.75rem;
            padding: 20px 0;
            border-top: 1px solid #30363d;
        }}
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>RULEV3+ Real Validation Dashboard</h1>
        <div class="meta">
            Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |
            Live trades: <code>{len(live_trades)}</code> |
            Backtest trades: <code>{len(bt_trades)}</code>
        </div>
    </div>

    <div class="source-box">
        <h3>DATA SOURCES (Verifiable)</h3>
        <pre>
LIVE TRADES:
  File: logs/paper/trades_20251224_194001.log
  Schema: timestamp | elapsed_seconds | direction | edge | ask | outcome | pnl
  Rows: {len(live_trades)}

BACKTEST TRADES:
  File: backtest_full_logs/runs/rulev3_core_only_t3_064_cap072/trades_full.jsonl
  Schema: session | elapsed_seconds | direction | edge | entry_ask | outcome | pnl
  Rows: {len(bt_trades)}

DERIVATION:
  Equity curve: cumsum(pnl) - no scaling, no normalization
  Timing bins: groupby(floor(elapsed_seconds / 5) * 5).count()
  Heatmap: groupby(int(elapsed_seconds)).count() for 180-209s range
        </pre>
    </div>

    <div class="stats-grid">
        <div class="stat-card live">
            <h3>Live Paper Trading (from log file)</h3>
            <div class="stat-row">
                <span class="stat-label">Trades</span>
                <span class="stat-value">{len(live_trades)}</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">Wins</span>
                <span class="stat-value">{live_wins}</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">Win Rate</span>
                <span class="stat-value">{live_wr}%</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">Total PnL</span>
                <span class="stat-value {'positive' if live_total_pnl >= 0 else 'negative'}">${live_total_pnl}</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">Avg PnL</span>
                <span class="stat-value {'positive' if live_avg_pnl >= 0 else 'negative'}">${live_avg_pnl}</span>
            </div>
        </div>

        <div class="stat-card backtest">
            <h3>Backtest (from jsonl file)</h3>
            <div class="stat-row">
                <span class="stat-label">Trades</span>
                <span class="stat-value">{len(bt_trades)}</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">Wins</span>
                <span class="stat-value">{bt_wins}</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">Win Rate</span>
                <span class="stat-value">{bt_wr}%</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">Total PnL</span>
                <span class="stat-value {'positive' if bt_total_pnl >= 0 else 'negative'}">${bt_total_pnl}</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">Avg PnL</span>
                <span class="stat-value {'positive' if bt_avg_pnl >= 0 else 'negative'}">${bt_avg_pnl}</span>
            </div>
        </div>
    </div>

    <div class="chart-box">
        <h3>Equity Curve (cumsum of pnl column, no scaling)</h3>
        <div class="chart-wrapper">
            <canvas id="equityChart"></canvas>
        </div>
    </div>

    <div class="chart-box">
        <h3>Entry Timing Distribution (groupby 5s bins)</h3>
        <div class="chart-wrapper">
            <canvas id="timingChart"></canvas>
        </div>
    </div>

    <div class="chart-box">
        <h3>CORE Window Heatmap (1s resolution, 180-209s)</h3>
        <div class="chart-wrapper">
            <canvas id="heatmapChart"></canvas>
        </div>
    </div>

    <div class="audit-box">
        <h3>AUDIT TRAIL: Live Trade Index (every trade traceable)</h3>
        <table class="audit-table">
            <tr>
                <th>Trade #</th>
                <th>Timestamp</th>
                <th>Elapsed (s)</th>
                <th>Timing Bin</th>
                <th>Heatmap Cell</th>
                <th>Outcome</th>
                <th>PnL</th>
            </tr>
            {''.join(f"""<tr>
                <td>{t}</td>
                <td>{live_index[t]['timestamp']}</td>
                <td>{live_index[t]['elapsed_seconds']}</td>
                <td>{live_index[t]['timing_bin']}</td>
                <td>{live_index[t]['heatmap_second']}</td>
                <td>{live_index[t]['outcome']}</td>
                <td class="{'positive' if live_index[t]['pnl'] >= 0 else 'negative'}">${live_index[t]['pnl']}</td>
            </tr>""" for t in sorted(live_index.keys()))}
        </table>
    </div>

    <div class="footer">
        Re-run <code>python build_real_dashboard.py</code> to update all charts from source files.
    </div>
</div>

<script>
    const colors = {{
        live: '#3fb950',
        backtest: '#58a6ff',
        grid: '#21262d',
        text: '#8b949e'
    }};

    // EQUITY CHART - direct from cumsum(pnl)
    new Chart(document.getElementById('equityChart'), {{
        type: 'line',
        data: {{
            labels: {list(range(1, len(live_equity) + 1))},
            datasets: [{{
                label: 'Live (cumsum)',
                data: {live_equity},
                borderColor: colors.live,
                backgroundColor: 'rgba(63, 185, 80, 0.1)',
                fill: true,
                tension: 0.2,
                pointRadius: 3
            }}, {{
                label: 'Backtest first {len(live_equity)} (cumsum)',
                data: {bt_equity[:len(live_equity)]},
                borderColor: colors.backtest,
                backgroundColor: 'rgba(88, 166, 255, 0.1)',
                fill: true,
                tension: 0.2,
                pointRadius: 2,
                borderDash: [4, 4]
            }}]
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            plugins: {{ legend: {{ labels: {{ color: colors.text }} }} }},
            scales: {{
                x: {{
                    title: {{ display: true, text: 'Trade #', color: colors.text }},
                    grid: {{ color: colors.grid }},
                    ticks: {{ color: colors.text }}
                }},
                y: {{
                    title: {{ display: true, text: 'Cumulative PnL ($)', color: colors.text }},
                    grid: {{ color: colors.grid }},
                    ticks: {{ color: colors.text }}
                }}
            }}
        }}
    }});

    // TIMING CHART - direct from groupby bins
    new Chart(document.getElementById('timingChart'), {{
        type: 'bar',
        data: {{
            labels: {timing_labels},
            datasets: [{{
                label: 'Live',
                data: {live_timing_data},
                backgroundColor: 'rgba(63, 185, 80, 0.8)',
                borderColor: colors.live,
                borderWidth: 1
            }}, {{
                label: 'Backtest (scaled /20)',
                data: {[round(x/20, 1) for x in bt_timing_data]},
                backgroundColor: 'rgba(88, 166, 255, 0.6)',
                borderColor: colors.backtest,
                borderWidth: 1
            }}]
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            plugins: {{ legend: {{ labels: {{ color: colors.text }} }} }},
            scales: {{
                x: {{
                    title: {{ display: true, text: 'Elapsed Seconds (5s bins)', color: colors.text }},
                    grid: {{ display: false }},
                    ticks: {{ color: colors.text }}
                }},
                y: {{
                    title: {{ display: true, text: 'Trade Count', color: colors.text }},
                    grid: {{ color: colors.grid }},
                    ticks: {{ color: colors.text }}
                }}
            }}
        }}
    }});

    // HEATMAP CHART - direct 1s counts
    new Chart(document.getElementById('heatmapChart'), {{
        type: 'bar',
        data: {{
            labels: {heatmap_labels},
            datasets: [{{
                label: 'Live',
                data: {live_heatmap_data},
                backgroundColor: 'rgba(63, 185, 80, 0.8)'
            }}, {{
                label: 'Backtest (scaled /5)',
                data: {[round(x/5, 1) for x in bt_heatmap_data]},
                backgroundColor: 'rgba(88, 166, 255, 0.6)'
            }}]
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            plugins: {{ legend: {{ labels: {{ color: colors.text }} }} }},
            scales: {{
                x: {{
                    title: {{ display: true, text: 'Time (3:XX elapsed)', color: colors.text }},
                    grid: {{ display: false }},
                    ticks: {{ color: colors.text, maxRotation: 45 }}
                }},
                y: {{
                    title: {{ display: true, text: 'Trade Count', color: colors.text }},
                    grid: {{ color: colors.grid }},
                    ticks: {{ color: colors.text }}
                }}
            }}
        }}
    }});
</script>
</body>
</html>'''

    return html


def main():
    print("=" * 60)
    print("REAL Dashboard Generator")
    print("=" * 60)

    print(f"\n[1] Parsing: {PAPER_LOG}")
    live = parse_live_trades()
    print(f"    -> {len(live)} trades")

    print(f"\n[2] Parsing: {BACKTEST_CORE}")
    bt = parse_backtest_trades()
    print(f"    -> {len(bt)} trades")

    print(f"\n[3] Computing equity curves (cumsum, no scaling)...")
    live_eq = compute_equity_curve(live)
    bt_eq = compute_equity_curve(bt)
    print(f"    Live final: ${live_eq[-1] if live_eq else 0}")
    print(f"    BT final: ${bt_eq[-1] if bt_eq else 0}")

    print(f"\n[4] Computing timing bins (groupby 5s)...")
    live_timing = compute_timing_bins(live)
    print(f"    Live bins: {dict(list(live_timing.items())[:5])}...")

    print(f"\n[5] Computing heatmap (1s resolution)...")
    live_heat = compute_heatmap(live)
    print(f"    Live heatmap: {dict(list(live_heat.items())[:5])}...")

    print(f"\n[6] Building audit index...")
    idx = build_trade_index(live)
    print(f"    Trade #1: {idx.get(1, 'N/A')}")

    print(f"\n[7] Generating HTML...")
    html = generate_html(live, bt)

    OUTPUT.parent.mkdir(exist_ok=True)
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"\n{'=' * 60}")
    print(f"SAVED: {OUTPUT}")
    print(f"{'=' * 60}")

    print("\nVERIFICATION:")
    print(f"  Live Trade #1 -> Bin: {idx[1]['timing_bin']}, Heatmap: {idx[1]['heatmap_second']}")
    print(f"  Re-run this script to update charts from source files.")


if __name__ == "__main__":
    main()
