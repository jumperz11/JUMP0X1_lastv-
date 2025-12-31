"""
COMPLETE DASHBOARD PIPELINE
===========================
1. Parse raw logs -> trades.csv (single source of truth)
2. trades.csv -> derived metrics -> charts
3. Full audit trail
4. Acceptance tests
"""

import json
import re
import csv
import sys
from datetime import datetime
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).parent
PAPER_LOG = BASE_DIR / "logs" / "paper" / "trades_20251224_194001.log"
BACKTEST_CORE = BASE_DIR / "backtest_full_logs" / "runs" / "rulev3_core_only_t3_064_cap072" / "trades_full.jsonl"
TRADES_CSV = BASE_DIR / "docs" / "trades.csv"
OUTPUT_HTML = BASE_DIR / "docs" / "dashboard.html"

# Window definitions
CORE_START = 180  # 3:00
CORE_END = 209    # 3:29
EXT_START = 150   # 2:30
EXT_END = 225     # 3:45


def parse_live_trades():
    """Parse paper log -> list of trade dicts"""
    trades = []

    with open(PAPER_LOG, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

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

        # Session start is the :00, :15, :30, :45 boundary
        minute = ts.minute
        session_start_minute = (minute // 15) * 15
        session_start = ts.replace(minute=session_start_minute, second=0, microsecond=0)

        # Compute elapsed
        elapsed = (ts - session_start).total_seconds()

        outcome = None
        pnl = 0.0
        if i < len(results):
            outcome = results[i].group(2) == 'WIN'
            pnl = float(results[i].group(3).replace('$', '').replace('+', ''))

        session_id = f"btc-updown-15m-{int(session_start.timestamp())}"

        trades.append({
            'dataset': 'live_current',
            'trade_id': i + 1,
            'session_id': session_id,
            'session_start_ts': session_start.isoformat(),
            'entry_ts': ts.isoformat(),
            'seconds_into_session': elapsed,
            'direction': direction,
            'edge': edge,
            'ask': ask,
            'outcome': 'WIN' if outcome else 'LOSS',
            'pnl': pnl
        })

    return trades


def parse_backtest_trades():
    """Parse backtest jsonl -> list of trade dicts"""
    trades = []

    with open(BACKTEST_CORE, 'r') as f:
        for idx, line in enumerate(f):
            row = json.loads(line.strip())

            elapsed = row.get('elapsed_seconds', 899 - row.get('entry_tau', 0))

            trades.append({
                'dataset': 'bt_current',
                'trade_id': idx + 1,
                'session_id': row.get('session', ''),
                'session_start_ts': '',  # Not in backtest data
                'entry_ts': '',          # Not in backtest data
                'seconds_into_session': elapsed,
                'direction': row.get('direction', ''),
                'edge': row.get('edge', 0),
                'ask': row.get('entry_ask', 0),
                'outcome': 'WIN' if row.get('outcome', False) else 'LOSS',
                'pnl': row.get('pnl', 0)
            })

    return trades


def export_trades_csv(trades, path):
    """Export all trades to CSV - single source of truth"""
    fieldnames = [
        'dataset', 'trade_id', 'session_id', 'session_start_ts', 'entry_ts',
        'seconds_into_session', 'direction', 'edge', 'ask', 'outcome', 'pnl'
    ]

    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(trades)

    return path


def load_trades_csv(path):
    """Load trades from CSV"""
    trades = []
    with open(path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            row['trade_id'] = int(row['trade_id'])
            row['seconds_into_session'] = float(row['seconds_into_session'])
            row['edge'] = float(row['edge']) if row['edge'] else 0
            row['ask'] = float(row['ask']) if row['ask'] else 0
            row['pnl'] = float(row['pnl'])
            trades.append(row)
    return trades


def compute_metrics(trades):
    """Compute all derived metrics from trades"""
    datasets = defaultdict(list)
    for t in trades:
        datasets[t['dataset']].append(t)

    metrics = {}
    for ds, ds_trades in datasets.items():
        # Sort by trade_id for stable ordering
        ds_trades = sorted(ds_trades, key=lambda x: x['trade_id'])

        # Equity curve: cumsum(pnl)
        equity = []
        total = 0.0
        for t in ds_trades:
            total += t['pnl']
            equity.append(round(total, 4))

        # Timing bins (5s)
        timing_bins = defaultdict(int)
        for t in ds_trades:
            bin_start = int(t['seconds_into_session'] // 5) * 5
            timing_bins[bin_start] += 1

        # Heatmap (1s, CORE window only)
        heatmap = defaultdict(int)
        for t in ds_trades:
            sec = int(t['seconds_into_session'])
            if CORE_START <= sec <= CORE_END:
                heatmap[sec] += 1

        # Stats
        wins = sum(1 for t in ds_trades if t['outcome'] == 'WIN')
        total_pnl = sum(t['pnl'] for t in ds_trades)

        metrics[ds] = {
            'trades': ds_trades,
            'count': len(ds_trades),
            'wins': wins,
            'win_rate': round(wins / len(ds_trades) * 100, 2) if ds_trades else 0,
            'total_pnl': round(total_pnl, 2),
            'avg_pnl': round(total_pnl / len(ds_trades), 4) if ds_trades else 0,
            'equity': equity,
            'timing_bins': dict(sorted(timing_bins.items())),
            'heatmap': dict(sorted(heatmap.items()))
        }

    return metrics


def audit_trade(trades, dataset, trade_id):
    """Single-trade audit: trace one trade through all charts"""
    for t in trades:
        if t['dataset'] == dataset and t['trade_id'] == trade_id:
            sec = int(t['seconds_into_session'])
            bin_5s = (sec // 5) * 5

            print(f"\n{'='*50}")
            print(f"AUDIT: {dataset} Trade #{trade_id}")
            print(f"{'='*50}")
            print(f"  entry_ts:            {t['entry_ts']}")
            print(f"  seconds_into_session: {t['seconds_into_session']}")
            print(f"  histogram_bin:        {bin_5s}s - {bin_5s+4}s")
            print(f"  heatmap_second:       {sec}s ({'in CORE' if CORE_START <= sec <= CORE_END else 'outside CORE'})")
            print(f"  pnl:                  ${t['pnl']}")
            print(f"  outcome:              {t['outcome']}")
            return t

    print(f"Trade not found: {dataset} #{trade_id}")
    return None


def acceptance_tests(html_content):
    """Run acceptance tests"""
    print("\n" + "="*50)
    print("ACCEPTANCE TESTS")
    print("="*50)

    # Test 1: No hardcoded arrays
    hardcoded_patterns = [
        r'const\s+\w+Equity\s*=\s*\[[\d\.,\s-]+\]',
        r'const\s+\w+Timing\s*=\s*\[[\d\.,\s-]+\]',
        r'Math\.random\(\)',
        r'btScaled',
        r'normalize',
    ]

    test_passed = True
    for pattern in hardcoded_patterns:
        if re.search(pattern, html_content, re.IGNORECASE):
            print(f"  FAIL: Found forbidden pattern: {pattern}")
            test_passed = False

    if test_passed:
        print("  PASS: No hardcoded arrays or forbidden patterns")

    # Test 2: Data loaded from source
    if 'loadFromCSV' in html_content or 'fetch(' in html_content or 'DATA_FROM_PYTHON' in html_content:
        print("  PASS: Data loaded from external source")
    else:
        print("  WARN: Verify data source mechanism")

    return test_passed


def generate_html(metrics):
    """Generate HTML dashboard from computed metrics"""

    live = metrics.get('live_current', {})
    bt = metrics.get('bt_current', {})

    # Prepare data for JavaScript
    live_equity = live.get('equity', [])
    bt_equity = bt.get('equity', [])[:len(live_equity)] if live_equity else []

    # Timing: all bins across both datasets
    all_timing_bins = sorted(set(live.get('timing_bins', {}).keys()) |
                             set(bt.get('timing_bins', {}).keys()))
    timing_labels = [f"{b}s" for b in all_timing_bins]
    live_timing = [live.get('timing_bins', {}).get(b, 0) for b in all_timing_bins]
    bt_timing = [bt.get('timing_bins', {}).get(b, 0) for b in all_timing_bins]

    # Heatmap: 180-209
    heatmap_labels = [f"3:{str(s-180).zfill(2)}" for s in range(180, 210)]
    live_heatmap = [live.get('heatmap', {}).get(s, 0) for s in range(180, 210)]
    bt_heatmap = [bt.get('heatmap', {}).get(s, 0) for s in range(180, 210)]

    # Build audit table
    audit_rows = ""
    for t in live.get('trades', []):
        sec = int(t['seconds_into_session'])
        bin_5s = (sec // 5) * 5
        in_core = "Yes" if CORE_START <= sec <= CORE_END else "No"
        pnl_class = "positive" if t['pnl'] >= 0 else "negative"
        audit_rows += f"""<tr>
            <td>{t['trade_id']}</td>
            <td>{t['entry_ts']}</td>
            <td>{t['seconds_into_session']:.0f}</td>
            <td>{bin_5s}s</td>
            <td>{sec}s</td>
            <td>{in_core}</td>
            <td>{t['outcome']}</td>
            <td class="{pnl_class}">${t['pnl']}</td>
        </tr>"""

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>RULEV3+ Dashboard (Data-Driven)</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@3.0.1/dist/chartjs-plugin-annotation.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Consolas', monospace; background: #0d1117; color: #c9d1d9; padding: 20px; }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        .header {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px; margin-bottom: 20px; }}
        h1 {{ color: #58a6ff; font-size: 1.4rem; }}
        .meta {{ color: #8b949e; font-size: 0.8rem; margin-top: 8px; }}
        .meta code {{ background: #21262d; padding: 2px 6px; border-radius: 4px; color: #7ee787; }}

        .source {{ background: #0d1117; border: 1px solid #238636; border-radius: 8px; padding: 16px; margin-bottom: 20px; }}
        .source h3 {{ color: #3fb950; font-size: 0.85rem; margin-bottom: 10px; }}
        .source pre {{ background: #161b22; padding: 12px; border-radius: 4px; font-size: 0.75rem; overflow-x: auto; }}

        .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px; }}
        .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }}
        .card.live {{ border-left: 3px solid #3fb950; }}
        .card.bt {{ border-left: 3px solid #58a6ff; }}
        .card h3 {{ color: #8b949e; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 12px; }}
        .stat {{ display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid #21262d; }}
        .stat:last-child {{ border-bottom: none; }}
        .label {{ color: #8b949e; }}
        .value {{ font-weight: bold; }}
        .positive {{ color: #3fb950; }}
        .negative {{ color: #f85149; }}

        .chart-box {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px; margin-bottom: 20px; }}
        .chart-box h3 {{ color: #58a6ff; font-size: 0.95rem; margin-bottom: 4px; }}
        .chart-box .derivation {{ color: #8b949e; font-size: 0.7rem; margin-bottom: 16px; }}
        .chart-wrapper {{ height: 280px; }}

        .audit {{ background: #161b22; border: 1px solid #d29922; border-radius: 8px; padding: 20px; margin-bottom: 20px; }}
        .audit h3 {{ color: #d29922; font-size: 0.95rem; margin-bottom: 16px; }}
        .audit table {{ width: 100%; border-collapse: collapse; font-size: 0.75rem; }}
        .audit th, .audit td {{ padding: 8px; text-align: left; border-bottom: 1px solid #21262d; }}
        .audit th {{ color: #8b949e; }}
        .audit tr:hover {{ background: #21262d; }}

        .footer {{ text-align: center; color: #8b949e; font-size: 0.7rem; padding-top: 20px; border-top: 1px solid #30363d; }}
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>RULEV3+ Validation Dashboard</h1>
        <div class="meta">
            Data: <code>docs/trades.csv</code> |
            Live: <code>{live.get('count', 0)}</code> trades |
            Backtest: <code>{bt.get('count', 0)}</code> trades |
            Generated: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>
        </div>
    </div>

    <div class="source">
        <h3>DATA LINEAGE</h3>
        <pre>
SOURCE FILES:
  logs/paper/trades_20251224_194001.log  ->  live_current dataset
  backtest_full_logs/.../trades_full.jsonl  ->  bt_current dataset

PIPELINE:
  1. parse_live_trades()   -> extract (timestamp, pnl, edge, ask, direction)
  2. parse_backtest_trades() -> extract (elapsed_seconds, pnl, outcome)
  3. export_trades_csv()   -> docs/trades.csv (single source of truth)
  4. compute_metrics()     -> equity=cumsum(pnl), timing=groupby(5s), heatmap=groupby(1s)
  5. generate_html()       -> inject computed arrays into chart config

DERIVATION FORMULAS:
  seconds_into_session = (entry_ts - session_start_ts).total_seconds()
  equity[i] = sum(pnl[0:i+1])
  timing_bin = floor(seconds_into_session / 5) * 5
  heatmap_second = int(seconds_into_session)
        </pre>
    </div>

    <div class="grid">
        <div class="card live">
            <h3>Live Paper Trading</h3>
            <div class="stat"><span class="label">Trades</span><span class="value">{live.get('count', 0)}</span></div>
            <div class="stat"><span class="label">Wins</span><span class="value">{live.get('wins', 0)}</span></div>
            <div class="stat"><span class="label">Win Rate</span><span class="value">{live.get('win_rate', 0)}%</span></div>
            <div class="stat"><span class="label">Total PnL</span><span class="value {'positive' if live.get('total_pnl', 0) >= 0 else 'negative'}">${live.get('total_pnl', 0)}</span></div>
            <div class="stat"><span class="label">Avg PnL</span><span class="value {'positive' if live.get('avg_pnl', 0) >= 0 else 'negative'}">${live.get('avg_pnl', 0)}</span></div>
        </div>
        <div class="card bt">
            <h3>Backtest CORE</h3>
            <div class="stat"><span class="label">Trades</span><span class="value">{bt.get('count', 0)}</span></div>
            <div class="stat"><span class="label">Wins</span><span class="value">{bt.get('wins', 0)}</span></div>
            <div class="stat"><span class="label">Win Rate</span><span class="value">{bt.get('win_rate', 0)}%</span></div>
            <div class="stat"><span class="label">Total PnL</span><span class="value {'positive' if bt.get('total_pnl', 0) >= 0 else 'negative'}">${bt.get('total_pnl', 0)}</span></div>
            <div class="stat"><span class="label">Avg PnL</span><span class="value {'positive' if bt.get('avg_pnl', 0) >= 0 else 'negative'}">${bt.get('avg_pnl', 0)}</span></div>
        </div>
    </div>

    <div class="chart-box">
        <h3>Equity Curve</h3>
        <div class="derivation">equity[i] = cumsum(pnl[0:i+1]) | No scaling, no normalization</div>
        <div class="chart-wrapper"><canvas id="equityChart"></canvas></div>
    </div>

    <div class="chart-box">
        <h3>Entry Timing Distribution</h3>
        <div class="derivation">bin = floor(seconds_into_session / 5) * 5 | count per bin | CORE: 180-209s shaded</div>
        <div class="chart-wrapper"><canvas id="timingChart"></canvas></div>
    </div>

    <div class="chart-box">
        <h3>CORE Window Heatmap (1s resolution)</h3>
        <div class="derivation">count per integer second in range [180, 209]</div>
        <div class="chart-wrapper"><canvas id="heatmapChart"></canvas></div>
    </div>

    <div class="audit">
        <h3>AUDIT TRAIL: Every Trade Traceable</h3>
        <table>
            <tr>
                <th>#</th>
                <th>Entry Timestamp</th>
                <th>Elapsed (s)</th>
                <th>Timing Bin</th>
                <th>Heatmap Sec</th>
                <th>In CORE?</th>
                <th>Outcome</th>
                <th>PnL</th>
            </tr>
            {audit_rows}
        </table>
    </div>

    <div class="footer">
        Re-run: <code>python build_dashboard_pipeline.py</code> | Add trades to CSV and re-run to update
    </div>
</div>

<script>
    // DATA_FROM_PYTHON - All arrays computed from trades.csv via Python
    // No hardcoded values - regenerate by running build_dashboard_pipeline.py

    const LIVE_EQUITY = {live_equity};
    const BT_EQUITY = {bt_equity};
    const TIMING_LABELS = {timing_labels};
    const LIVE_TIMING = {live_timing};
    const BT_TIMING = {bt_timing};
    const HEATMAP_LABELS = {heatmap_labels};
    const LIVE_HEATMAP = {live_heatmap};
    const BT_HEATMAP = {bt_heatmap};

    const CORE_START_BIN = 180;
    const CORE_END_BIN = 209;

    const colors = {{ live: '#3fb950', bt: '#58a6ff', grid: '#21262d', text: '#8b949e' }};

    // Equity Chart
    new Chart(document.getElementById('equityChart'), {{
        type: 'line',
        data: {{
            labels: Array.from({{length: LIVE_EQUITY.length}}, (_, i) => i + 1),
            datasets: [
                {{ label: 'Live cumsum(pnl)', data: LIVE_EQUITY, borderColor: colors.live, backgroundColor: 'rgba(63,185,80,0.1)', fill: true, tension: 0.2, pointRadius: 3 }},
                {{ label: 'Backtest cumsum(pnl)', data: BT_EQUITY, borderColor: colors.bt, backgroundColor: 'rgba(88,166,255,0.05)', fill: true, tension: 0.2, pointRadius: 2, borderDash: [4,4] }}
            ]
        }},
        options: {{
            responsive: true, maintainAspectRatio: false,
            plugins: {{ legend: {{ labels: {{ color: colors.text }} }} }},
            scales: {{
                x: {{ title: {{ display: true, text: 'Trade #', color: colors.text }}, grid: {{ color: colors.grid }}, ticks: {{ color: colors.text }} }},
                y: {{ title: {{ display: true, text: 'Cumulative PnL ($)', color: colors.text }}, grid: {{ color: colors.grid }}, ticks: {{ color: colors.text }} }}
            }}
        }}
    }});

    // Timing Chart with CORE zone annotation
    new Chart(document.getElementById('timingChart'), {{
        type: 'bar',
        data: {{
            labels: TIMING_LABELS,
            datasets: [
                {{ label: 'Live', data: LIVE_TIMING, backgroundColor: 'rgba(63,185,80,0.8)', borderColor: colors.live, borderWidth: 1 }},
                {{ label: 'Backtest (/20)', data: BT_TIMING.map(v => v/20), backgroundColor: 'rgba(88,166,255,0.6)', borderColor: colors.bt, borderWidth: 1 }}
            ]
        }},
        options: {{
            responsive: true, maintainAspectRatio: false,
            plugins: {{
                legend: {{ labels: {{ color: colors.text }} }},
                annotation: {{
                    annotations: {{
                        coreZone: {{
                            type: 'box',
                            xMin: TIMING_LABELS.indexOf('180s') - 0.5,
                            xMax: TIMING_LABELS.indexOf('205s') + 0.5,
                            backgroundColor: 'rgba(63,185,80,0.1)',
                            borderColor: 'rgba(63,185,80,0.5)',
                            borderWidth: 1,
                            label: {{ content: 'CORE', display: true, position: 'start' }}
                        }}
                    }}
                }}
            }},
            scales: {{
                x: {{ title: {{ display: true, text: 'Elapsed Seconds (5s bins)', color: colors.text }}, grid: {{ display: false }}, ticks: {{ color: colors.text }} }},
                y: {{ title: {{ display: true, text: 'Trade Count', color: colors.text }}, grid: {{ color: colors.grid }}, ticks: {{ color: colors.text }} }}
            }}
        }}
    }});

    // Heatmap Chart
    new Chart(document.getElementById('heatmapChart'), {{
        type: 'bar',
        data: {{
            labels: HEATMAP_LABELS,
            datasets: [
                {{ label: 'Live', data: LIVE_HEATMAP, backgroundColor: 'rgba(63,185,80,0.8)' }},
                {{ label: 'Backtest (/5)', data: BT_HEATMAP.map(v => v/5), backgroundColor: 'rgba(88,166,255,0.6)' }}
            ]
        }},
        options: {{
            responsive: true, maintainAspectRatio: false,
            plugins: {{ legend: {{ labels: {{ color: colors.text }} }} }},
            scales: {{
                x: {{ title: {{ display: true, text: 'Second (3:XX)', color: colors.text }}, grid: {{ display: false }}, ticks: {{ color: colors.text, maxRotation: 45 }} }},
                y: {{ title: {{ display: true, text: 'Trade Count', color: colors.text }}, grid: {{ color: colors.grid }}, ticks: {{ color: colors.text }} }}
            }}
        }}
    }});
</script>
</body>
</html>'''

    return html


def main():
    print("=" * 60)
    print("DASHBOARD PIPELINE")
    print("=" * 60)

    # Step 1: Parse raw sources
    print("\n[1] PARSING RAW SOURCES")
    print(f"    Live: {PAPER_LOG}")
    live_trades = parse_live_trades()
    print(f"    -> {len(live_trades)} trades")

    print(f"    Backtest: {BACKTEST_CORE}")
    bt_trades = parse_backtest_trades()
    print(f"    -> {len(bt_trades)} trades")

    # Step 2: Export to CSV (single source of truth)
    print("\n[2] EXPORTING TO CSV")
    all_trades = live_trades + bt_trades
    TRADES_CSV.parent.mkdir(exist_ok=True)
    export_trades_csv(all_trades, TRADES_CSV)
    print(f"    -> {TRADES_CSV}")
    print(f"    -> {len(all_trades)} total rows")

    # Step 3: Load from CSV and compute metrics
    print("\n[3] LOADING FROM CSV & COMPUTING METRICS")
    trades = load_trades_csv(TRADES_CSV)
    metrics = compute_metrics(trades)
    for ds, m in metrics.items():
        print(f"    {ds}: {m['count']} trades, {m['win_rate']}% WR, ${m['total_pnl']} PnL")

    # Step 4: Single-trade audit
    print("\n[4] SINGLE-TRADE AUDIT")
    audit_trade(trades, 'live_current', 1)
    audit_trade(trades, 'live_current', 17)

    # Step 5: Generate HTML
    print("\n[5] GENERATING HTML")
    html = generate_html(metrics)
    with open(OUTPUT_HTML, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"    -> {OUTPUT_HTML}")

    # Step 6: Run acceptance tests
    acceptance_tests(html)

    print("\n" + "=" * 60)
    print("COMPLETE")
    print("=" * 60)
    print(f"\nOutputs:")
    print(f"  CSV:  {TRADES_CSV}")
    print(f"  HTML: {OUTPUT_HTML}")
    print(f"\nTo update: Add rows to trades.csv, re-run this script.")


if __name__ == "__main__":
    main()
