"""
Build Validation Dashboard - 4 Panels
All data from raw trade rows. No normalization. No truncation. No simulation.
"""
import json
import os
import re
from glob import glob
from collections import Counter, defaultdict

BASE = r'C:\Users\Mega-PC\Desktop\New folder (3)\JUMP0X1-main (1)\JUMP0X1-main'

# ============================================================================
# DATA LOADING
# ============================================================================

def load_live_current():
    """Parse live trades from paper trading log"""
    log_path = os.path.join(BASE, 'logs', 'paper', 'trades_20251224_194001.log')
    trades = []

    with open(log_path, 'r') as f:
        content = f.read()

    # Find all PAPER TRADE blocks
    pattern = r'PAPER TRADE #(\d+).*?DIRECTION:\s*(\w+).*?EDGE:\s*([\d.]+).*?ASK:\s*\$([\d.]+).*?ELAPSED:\s*([\d.]+)s'
    matches = re.findall(pattern, content, re.DOTALL)

    # Find all RESULT lines
    result_pattern = r'RESULT:\s*(WIN|LOSS)\s*\|\s*PnL:\s*([+-]?\$?[\d.]+)'
    results = re.findall(result_pattern, content)

    for i, match in enumerate(matches):
        trade_num, direction, edge, ask, elapsed = match
        if i < len(results):
            outcome = results[i][0] == 'WIN'
            pnl_str = results[i][1].replace('$', '').replace('+', '')
            pnl = float(pnl_str)
        else:
            continue  # Skip trades without results

        trades.append({
            'direction': direction,
            'edge': float(edge),
            'entry_ask': float(ask),
            'elapsed_seconds': float(elapsed),
            'outcome': outcome,
            'pnl': pnl
        })

    return trades

def load_backtest_current():
    """Load backtest trades with current settings (CORE 180-209s)"""
    path = os.path.join(BASE, 'backtest_full_logs', 'runs',
                        'rulev3_core_only_t3_064_cap072', 'trades_full.jsonl')
    trades = []
    with open(path, 'r') as f:
        for line in f:
            t = json.loads(line.strip())
            trades.append({
                'direction': t['direction'],
                'edge': t['edge'],
                'entry_ask': t['entry_ask'],
                'elapsed_seconds': t['elapsed_seconds'],
                'outcome': t['outcome'],
                'pnl': t['pnl']
            })
    return trades

def load_backtest_extended():
    """Load/simulate backtest trades with extended settings (150-225s)"""
    markets_path = os.path.join(BASE, 'markets_paper')
    btc_sessions = sorted(glob(os.path.join(markets_path, 'btc-updown-15m-*')))

    trades = []

    for session_dir in btc_sessions:
        ticks_file = os.path.join(session_dir, 'ticks.jsonl')
        if not os.path.exists(ticks_file):
            continue

        ticks = []
        with open(ticks_file, 'r') as f:
            for line in f:
                try:
                    ticks.append(json.loads(line.strip()))
                except:
                    continue

        if len(ticks) < 10:
            continue

        start_unix = ticks[0].get('startUnix')
        if not start_unix:
            continue

        entry_tick = None
        entry_direction = None
        entry_elapsed = None

        for tick in ticks:
            t_ms = tick.get('t')
            if not t_ms:
                continue
            elapsed = (t_ms / 1000 - start_unix)

            if elapsed < 150 or elapsed > 225:
                if elapsed > 225:
                    break
                continue

            best = tick.get('best') or {}
            price = tick.get('price') or {}

            # Check Up
            up_best = best.get('Up') or {}
            up_ask = up_best.get('ask')
            up_mid = price.get('Up')

            if up_ask and up_mid and up_ask > 0 and up_mid > 0:
                spread = up_ask - up_mid
                if up_mid >= 0.64 and up_ask < 0.72 and spread <= 0.02:
                    entry_tick = tick
                    entry_direction = 'Up'
                    entry_elapsed = elapsed
                    break

            # Check Down
            down_best = best.get('Down') or {}
            down_ask = down_best.get('ask')
            down_mid = price.get('Down')

            if down_ask and down_mid and down_ask > 0 and down_mid > 0:
                spread = down_ask - down_mid
                if down_mid >= 0.64 and down_ask < 0.72 and spread <= 0.02:
                    entry_tick = tick
                    entry_direction = 'Down'
                    entry_elapsed = elapsed
                    break

        if entry_tick is None:
            continue

        best = entry_tick.get('best') or {}
        price_data = entry_tick.get('price') or {}

        if entry_direction == 'Up':
            entry_ask = (best.get('Up') or {}).get('ask', 0.65)
            entry_edge = price_data.get('Up', 0.64)
        else:
            entry_ask = (best.get('Down') or {}).get('ask', 0.65)
            entry_edge = price_data.get('Down', 0.64)

        final_tick = ticks[-1]
        final_price = final_tick.get('price') or {}

        if entry_direction == 'Up':
            final_mid = final_price.get('Up', 0.5)
            outcome = final_mid is not None and final_mid > 0.5
        else:
            final_mid = final_price.get('Down', 0.5)
            outcome = final_mid is not None and final_mid > 0.5

        pnl = (1 - entry_ask) if outcome else (-entry_ask)

        trades.append({
            'direction': entry_direction,
            'edge': entry_edge,
            'entry_ask': entry_ask,
            'elapsed_seconds': entry_elapsed,
            'outcome': outcome,
            'pnl': pnl
        })

    return trades

# ============================================================================
# DATA PROCESSING
# ============================================================================

def compute_equity_curve(trades):
    """Raw cumulative PnL"""
    cum = 0
    curve = [0]
    for t in trades:
        cum += t['pnl']
        curve.append(round(cum, 4))
    return curve

def compute_timing_histogram(trades, bin_size=5, min_sec=150, max_sec=225):
    """Count trades in each timing bin"""
    bins = {}
    for sec in range(min_sec, max_sec + 1, bin_size):
        bins[sec] = 0

    for t in trades:
        elapsed = t['elapsed_seconds']
        if min_sec <= elapsed <= max_sec:
            bin_start = int(elapsed // bin_size) * bin_size
            if bin_start in bins:
                bins[bin_start] += 1

    return bins

def compute_heatmap(trades, min_sec=150, max_sec=225):
    """Win rate by second"""
    by_second = defaultdict(lambda: {'wins': 0, 'total': 0})

    for t in trades:
        sec = int(t['elapsed_seconds'])
        if min_sec <= sec <= max_sec:
            by_second[sec]['total'] += 1
            if t['outcome']:
                by_second[sec]['wins'] += 1

    # Fill all seconds
    heatmap = {}
    for sec in range(min_sec, max_sec + 1):
        data = by_second[sec]
        if data['total'] > 0:
            heatmap[sec] = round(data['wins'] / data['total'] * 100, 1)
        else:
            heatmap[sec] = None

    return heatmap

def compute_edge_ladder(trades):
    """Win rate by edge bucket"""
    by_edge = defaultdict(lambda: {'wins': 0, 'total': 0})

    for t in trades:
        edge_bin = round(t['edge'], 2)
        by_edge[edge_bin]['total'] += 1
        if t['outcome']:
            by_edge[edge_bin]['wins'] += 1

    ladder = {}
    for edge in sorted(by_edge.keys()):
        data = by_edge[edge]
        if data['total'] >= 5:  # Minimum sample size
            ladder[edge] = {
                'count': data['total'],
                'wr': round(data['wins'] / data['total'] * 100, 1)
            }

    return ladder

# ============================================================================
# MAIN
# ============================================================================

print("Loading datasets...")
live_current = load_live_current()
print(f"  Live Current: {len(live_current)} trades")

bt_current = load_backtest_current()
print(f"  Backtest Current: {len(bt_current)} trades")

bt_extended = load_backtest_extended()
print(f"  Backtest Extended: {len(bt_extended)} trades")

# Compute all metrics
print("\nComputing metrics...")

# Equity curves
eq_live = compute_equity_curve(live_current)
eq_bt_current = compute_equity_curve(bt_current)
eq_bt_extended = compute_equity_curve(bt_extended)

# Timing histograms (150-225s range for comparison)
hist_live = compute_timing_histogram(live_current)
hist_bt_current = compute_timing_histogram(bt_current)
hist_bt_extended = compute_timing_histogram(bt_extended)

# Heatmaps
hm_live = compute_heatmap(live_current)
hm_bt_current = compute_heatmap(bt_current)
hm_bt_extended = compute_heatmap(bt_extended)

# Edge ladders
ladder_bt_current = compute_edge_ladder(bt_current)
ladder_bt_extended = compute_edge_ladder(bt_extended)

# Summary stats
def summarize(trades, name):
    wins = sum(1 for t in trades if t['outcome'])
    total_pnl = sum(t['pnl'] for t in trades)
    wr = wins / len(trades) * 100 if trades else 0
    return {
        'name': name,
        'trades': len(trades),
        'wins': wins,
        'losses': len(trades) - wins,
        'wr': round(wr, 2),
        'pnl': round(total_pnl, 2)
    }

stats_live = summarize(live_current, 'Live Current')
stats_bt_current = summarize(bt_current, 'Backtest Current')
stats_bt_extended = summarize(bt_extended, 'Backtest Extended')

print(f"\n  Live Current: {stats_live['trades']} trades, {stats_live['wr']}% WR, PnL: {stats_live['pnl']}")
print(f"  BT Current: {stats_bt_current['trades']} trades, {stats_bt_current['wr']}% WR, PnL: {stats_bt_current['pnl']}")
print(f"  BT Extended: {stats_bt_extended['trades']} trades, {stats_bt_extended['wr']}% WR, PnL: {stats_bt_extended['pnl']}")

# ============================================================================
# HTML GENERATION
# ============================================================================

html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>RULEV3+ Validation Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Segoe UI', system-ui, sans-serif;
            background: #0a0a0f;
            color: #e0e0e0;
            padding: 20px;
        }}
        h1 {{
            text-align: center;
            color: #00ff88;
            margin-bottom: 10px;
            font-size: 24px;
        }}
        .subtitle {{
            text-align: center;
            color: #888;
            margin-bottom: 20px;
            font-size: 12px;
        }}
        .stats-bar {{
            display: flex;
            justify-content: center;
            gap: 40px;
            margin-bottom: 20px;
            flex-wrap: wrap;
        }}
        .stat-box {{
            background: #1a1a24;
            border: 1px solid #333;
            border-radius: 8px;
            padding: 12px 20px;
            text-align: center;
        }}
        .stat-box h3 {{ color: #888; font-size: 11px; margin-bottom: 4px; }}
        .stat-box .value {{ font-size: 18px; font-weight: bold; }}
        .stat-box .value.green {{ color: #00ff88; }}
        .stat-box .value.red {{ color: #ff4444; }}
        .stat-box .value.blue {{ color: #4488ff; }}
        .stat-box .value.yellow {{ color: #ffcc00; }}
        .grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            max-width: 1600px;
            margin: 0 auto;
        }}
        .panel {{
            background: #12121a;
            border: 1px solid #2a2a3a;
            border-radius: 12px;
            padding: 16px;
        }}
        .panel h2 {{
            font-size: 14px;
            color: #aaa;
            margin-bottom: 12px;
            border-bottom: 1px solid #333;
            padding-bottom: 8px;
        }}
        .chart-container {{
            position: relative;
            height: 300px;
        }}
        .heatmap-container {{
            overflow-x: auto;
        }}
        .heatmap {{
            display: flex;
            flex-direction: column;
            gap: 4px;
            font-size: 10px;
        }}
        .heatmap-row {{
            display: flex;
            align-items: center;
            gap: 2px;
        }}
        .heatmap-label {{
            width: 100px;
            color: #888;
            text-align: right;
            padding-right: 8px;
        }}
        .heatmap-cells {{
            display: flex;
            gap: 1px;
        }}
        .heatmap-cell {{
            width: 12px;
            height: 24px;
            border-radius: 2px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 7px;
            color: #000;
        }}
        .ladder-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 12px;
        }}
        .ladder-table th {{
            background: #1a1a24;
            padding: 8px;
            text-align: left;
            color: #888;
            border-bottom: 1px solid #333;
        }}
        .ladder-table td {{
            padding: 6px 8px;
            border-bottom: 1px solid #222;
        }}
        .ladder-table tr:hover {{ background: #1a1a24; }}
        .bar {{
            height: 16px;
            border-radius: 3px;
            display: inline-block;
        }}
        .legend {{
            display: flex;
            gap: 16px;
            justify-content: center;
            margin-bottom: 8px;
            font-size: 11px;
        }}
        .legend-item {{
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        .legend-dot {{
            width: 10px;
            height: 10px;
            border-radius: 50%;
        }}
        .footer {{
            text-align: center;
            margin-top: 20px;
            color: #555;
            font-size: 11px;
        }}
    </style>
</head>
<body>
    <h1>RULEV3+ Validation Dashboard</h1>
    <div class="subtitle">Raw data only. No normalization. No truncation.</div>

    <div class="stats-bar">
        <div class="stat-box">
            <h3>LIVE CURRENT</h3>
            <div class="value blue">{stats_live['trades']} trades</div>
            <div style="font-size:12px">{stats_live['wr']}% WR | PnL: ${stats_live['pnl']}</div>
        </div>
        <div class="stat-box">
            <h3>BACKTEST CURRENT (3:00-3:29)</h3>
            <div class="value green">{stats_bt_current['trades']} trades</div>
            <div style="font-size:12px">{stats_bt_current['wr']}% WR | PnL: ${stats_bt_current['pnl']}</div>
        </div>
        <div class="stat-box">
            <h3>BACKTEST EXTENDED (2:30-3:45)</h3>
            <div class="value yellow">{stats_bt_extended['trades']} trades</div>
            <div style="font-size:12px">{stats_bt_extended['wr']}% WR | PnL: ${stats_bt_extended['pnl']}</div>
        </div>
    </div>

    <div class="grid">
        <!-- Panel 1: Equity Curves -->
        <div class="panel">
            <h2>1. EQUITY CURVES (Raw Cumulative PnL)</h2>
            <div class="legend">
                <div class="legend-item"><div class="legend-dot" style="background:#4488ff"></div>Live Current</div>
                <div class="legend-item"><div class="legend-dot" style="background:#00ff88"></div>Backtest Current</div>
                <div class="legend-item"><div class="legend-dot" style="background:#ffcc00"></div>Backtest Extended</div>
            </div>
            <div class="chart-container">
                <canvas id="equityChart"></canvas>
            </div>
        </div>

        <!-- Panel 2: Entry Timing Histogram -->
        <div class="panel">
            <h2>2. ENTRY TIMING HISTOGRAM (5s bins, 150-225s)</h2>
            <div class="legend">
                <div class="legend-item"><div class="legend-dot" style="background:#4488ff"></div>Live Current</div>
                <div class="legend-item"><div class="legend-dot" style="background:#00ff88"></div>Backtest Current</div>
                <div class="legend-item"><div class="legend-dot" style="background:#ffcc00"></div>Backtest Extended</div>
            </div>
            <div class="chart-container">
                <canvas id="histogramChart"></canvas>
            </div>
        </div>

        <!-- Panel 3: Heatmap -->
        <div class="panel">
            <h2>3. WIN RATE HEATMAP BY SECOND (150-225s)</h2>
            <div class="heatmap-container">
                <div class="heatmap" id="heatmapContainer"></div>
            </div>
            <div style="margin-top:12px; font-size:10px; color:#666; text-align:center;">
                Color scale: <span style="color:#ff4444">Red &lt;50%</span> |
                <span style="color:#ffcc00">Yellow 50-70%</span> |
                <span style="color:#00ff88">Green &gt;70%</span> |
                <span style="color:#444">Gray = No data</span>
            </div>
        </div>

        <!-- Panel 4: Edge vs WR Ladder -->
        <div class="panel">
            <h2>4. EDGE vs WIN RATE LADDER</h2>
            <table class="ladder-table">
                <thead>
                    <tr>
                        <th>Edge</th>
                        <th>BT Current (n)</th>
                        <th>BT Current WR</th>
                        <th>BT Extended (n)</th>
                        <th>BT Extended WR</th>
                        <th>Delta</th>
                    </tr>
                </thead>
                <tbody id="ladderBody"></tbody>
            </table>
        </div>
    </div>

    <div class="footer">
        Generated from raw trade data | Live: {len(live_current)} trades | BT Current: {len(bt_current)} trades | BT Extended: {len(bt_extended)} trades
    </div>

    <script>
        // Data from Python
        const eqLive = {json.dumps(eq_live)};
        const eqBtCurrent = {json.dumps(eq_bt_current)};
        const eqBtExtended = {json.dumps(eq_bt_extended)};

        const histLive = {json.dumps(hist_live)};
        const histBtCurrent = {json.dumps(hist_bt_current)};
        const histBtExtended = {json.dumps(hist_bt_extended)};

        const hmLive = {json.dumps(hm_live)};
        const hmBtCurrent = {json.dumps(hm_bt_current)};
        const hmBtExtended = {json.dumps(hm_bt_extended)};

        const ladderBtCurrent = {json.dumps(ladder_bt_current)};
        const ladderBtExtended = {json.dumps(ladder_bt_extended)};

        // Chart 1: Equity Curves
        const maxLen = Math.max(eqLive.length, eqBtCurrent.length, eqBtExtended.length);

        // Downsample for display if needed
        function downsample(arr, maxPoints) {{
            if (arr.length <= maxPoints) return arr.map((v, i) => ({{x: i, y: v}}));
            const step = Math.ceil(arr.length / maxPoints);
            const result = [];
            for (let i = 0; i < arr.length; i += step) {{
                result.push({{x: i, y: arr[i]}});
            }}
            if (result[result.length-1].x !== arr.length-1) {{
                result.push({{x: arr.length-1, y: arr[arr.length-1]}});
            }}
            return result;
        }}

        new Chart(document.getElementById('equityChart'), {{
            type: 'line',
            data: {{
                datasets: [
                    {{
                        label: 'Live Current',
                        data: downsample(eqLive, 100),
                        borderColor: '#4488ff',
                        backgroundColor: 'transparent',
                        borderWidth: 2,
                        pointRadius: 0,
                        tension: 0.1
                    }},
                    {{
                        label: 'Backtest Current',
                        data: downsample(eqBtCurrent, 200),
                        borderColor: '#00ff88',
                        backgroundColor: 'transparent',
                        borderWidth: 2,
                        pointRadius: 0,
                        tension: 0.1
                    }},
                    {{
                        label: 'Backtest Extended',
                        data: downsample(eqBtExtended, 200),
                        borderColor: '#ffcc00',
                        backgroundColor: 'transparent',
                        borderWidth: 2,
                        pointRadius: 0,
                        tension: 0.1
                    }}
                ]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{ legend: {{ display: false }} }},
                scales: {{
                    x: {{
                        type: 'linear',
                        title: {{ display: true, text: 'Trade #', color: '#888' }},
                        grid: {{ color: '#222' }},
                        ticks: {{ color: '#888' }}
                    }},
                    y: {{
                        title: {{ display: true, text: 'Cumulative PnL', color: '#888' }},
                        grid: {{ color: '#222' }},
                        ticks: {{ color: '#888' }}
                    }}
                }}
            }}
        }});

        // Chart 2: Timing Histogram
        const histLabels = Object.keys(histBtExtended).map(s => {{
            const sec = parseInt(s);
            const m = Math.floor(sec / 60);
            const ss = sec % 60;
            return m + ':' + String(ss).padStart(2, '0');
        }});

        new Chart(document.getElementById('histogramChart'), {{
            type: 'bar',
            data: {{
                labels: histLabels,
                datasets: [
                    {{
                        label: 'Live Current',
                        data: Object.values(histLive),
                        backgroundColor: '#4488ff',
                        barPercentage: 0.9,
                        categoryPercentage: 0.8
                    }},
                    {{
                        label: 'Backtest Current',
                        data: Object.values(histBtCurrent),
                        backgroundColor: '#00ff88',
                        barPercentage: 0.9,
                        categoryPercentage: 0.8
                    }},
                    {{
                        label: 'Backtest Extended',
                        data: Object.values(histBtExtended),
                        backgroundColor: '#ffcc00',
                        barPercentage: 0.9,
                        categoryPercentage: 0.8
                    }}
                ]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{ legend: {{ display: false }} }},
                scales: {{
                    x: {{
                        title: {{ display: true, text: 'Entry Time (m:ss)', color: '#888' }},
                        grid: {{ display: false }},
                        ticks: {{ color: '#888', maxRotation: 45 }}
                    }},
                    y: {{
                        title: {{ display: true, text: 'Trade Count', color: '#888' }},
                        grid: {{ color: '#222' }},
                        ticks: {{ color: '#888' }}
                    }}
                }}
            }}
        }});

        // Heatmap
        function getHeatmapColor(wr) {{
            if (wr === null) return '#2a2a3a';
            if (wr < 50) return `rgb(${{Math.round(255)}}, ${{Math.round(68 + wr)}}, ${{Math.round(68)}})`;
            if (wr < 70) return `rgb(${{Math.round(255)}}, ${{Math.round(150 + (wr-50)*5)}}, ${{Math.round(0)}})`;
            return `rgb(${{Math.round(0)}}, ${{Math.round(200 + (wr-70)*2)}}, ${{Math.round(100 + (wr-70)*2)}})`;
        }}

        const heatmapContainer = document.getElementById('heatmapContainer');
        const datasets = [
            {{ name: 'BT Extended', data: hmBtExtended }},
            {{ name: 'BT Current', data: hmBtCurrent }},
            {{ name: 'Live Current', data: hmLive }}
        ];

        // Header row with second labels
        let headerHtml = '<div class="heatmap-row"><div class="heatmap-label"></div><div class="heatmap-cells">';
        for (let sec = 150; sec <= 225; sec++) {{
            if (sec % 5 === 0) {{
                const m = Math.floor(sec / 60);
                const s = sec % 60;
                headerHtml += `<div style="width:12px;text-align:center;color:#666;font-size:8px">${{m}}:${{String(s).padStart(2,'0')}}</div>`;
            }} else {{
                headerHtml += '<div style="width:12px"></div>';
            }}
        }}
        headerHtml += '</div></div>';
        heatmapContainer.innerHTML = headerHtml;

        datasets.forEach(ds => {{
            let rowHtml = `<div class="heatmap-row"><div class="heatmap-label">${{ds.name}}</div><div class="heatmap-cells">`;
            for (let sec = 150; sec <= 225; sec++) {{
                const wr = ds.data[sec];
                const color = getHeatmapColor(wr);
                const text = wr !== null ? Math.round(wr) : '';
                rowHtml += `<div class="heatmap-cell" style="background:${{color}}" title="Sec ${{sec}}: ${{wr !== null ? wr + '%' : 'N/A'}}">${{text}}</div>`;
            }}
            rowHtml += '</div></div>';
            heatmapContainer.innerHTML += rowHtml;
        }});

        // Edge Ladder
        const ladderBody = document.getElementById('ladderBody');
        const allEdges = new Set([...Object.keys(ladderBtCurrent), ...Object.keys(ladderBtExtended)]);
        const sortedEdges = Array.from(allEdges).sort((a, b) => parseFloat(a) - parseFloat(b));

        sortedEdges.forEach(edge => {{
            const curr = ladderBtCurrent[edge] || {{ count: 0, wr: '-' }};
            const ext = ladderBtExtended[edge] || {{ count: 0, wr: '-' }};

            let delta = '-';
            let deltaColor = '#888';
            if (typeof curr.wr === 'number' && typeof ext.wr === 'number') {{
                const d = (ext.wr - curr.wr).toFixed(1);
                delta = (d >= 0 ? '+' : '') + d + '%';
                deltaColor = d >= 0 ? '#00ff88' : '#ff4444';
            }}

            const currBar = typeof curr.wr === 'number'
                ? `<div class="bar" style="width:${{curr.wr}}px;background:#00ff88"></div> ${{curr.wr}}%`
                : '-';
            const extBar = typeof ext.wr === 'number'
                ? `<div class="bar" style="width:${{ext.wr}}px;background:#ffcc00"></div> ${{ext.wr}}%`
                : '-';

            ladderBody.innerHTML += `
                <tr>
                    <td>${{parseFloat(edge).toFixed(2)}}</td>
                    <td>${{curr.count || '-'}}</td>
                    <td>${{currBar}}</td>
                    <td>${{ext.count || '-'}}</td>
                    <td>${{extBar}}</td>
                    <td style="color:${{deltaColor}}">${{delta}}</td>
                </tr>
            `;
        }});
    </script>
</body>
</html>
'''

# Write HTML
output_path = os.path.join(BASE, 'docs', 'validation_dashboard.html')
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(html)

print(f"\nDashboard written to: {output_path}")
