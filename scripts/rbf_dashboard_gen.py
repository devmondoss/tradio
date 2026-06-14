#!/usr/bin/env python3
"""
Genera rbf_backtest_dashboard.html con:
  - Equity curve
  - Trade-by-trade tabla con PnL en dolares
  - Breakdowns por sesion, simbolo, direccion, score
  - Stats cards

Uso: python scripts/rbf_dashboard_gen.py
"""
import json, os, sys, webbrowser
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone

ROOT = Path(__file__).parent.parent
env = {}
for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip().strip('"').strip("'")

import urllib.request, urllib.parse
URL = env.get('SUPABASE_URL', 'https://ztdhvmcisjjyhbqlgkzm.supabase.co')
KEY = env.get('SUPABASE_KEY', '')

CAPITAL  = 450.0
RISK_PCT = 0.02

def fetch(table, params):
    rows, lim, off = [], 1000, 0
    while True:
        qs = urllib.parse.urlencode({**params, 'limit': lim, 'offset': off})
        req = urllib.request.Request(f'{URL}/rest/v1/{table}?{qs}',
            headers={'apikey': KEY, 'Authorization': f'Bearer {KEY}'})
        chunk = json.loads(urllib.request.urlopen(req).read())
        rows.extend(chunk);
        if len(chunk) < lim: break
        off += lim
    return rows

print("Fetching 72 trades...")
raw = fetch('rbf_signals', {'select': '*', 'result_r': 'not.is.null', 'order': 'timestamp_ms.asc'})
print(f"  {len(raw)} trades")

RISK = CAPITAL * RISK_PCT
balance = CAPITAL
trades = []
for i, t in enumerate(raw, 1):
    res_r   = float(t.get('result_r') or 0)
    pnl     = round(res_r * RISK, 2)
    balance = round(balance + pnl, 2)
    ts      = int(t.get('timestamp_ms', 0))
    dt      = datetime.fromtimestamp(ts/1000, tz=timezone.utc).strftime('%m/%d %H:%M')
    trades.append({
        'i':       i,
        'dt':      dt,
        'sym':     t.get('symbol', ''),
        'dir':     t.get('direction', ''),
        'ses':     t.get('session', ''),
        'score':   t.get('confluence_score', ''),
        'r':       res_r,
        'pnl':     pnl,
        'bal':     balance,
        'reason':  t.get('exit_reason') or t.get('status') or '?',
        'entry':   t.get('entry_price', 0),
        'stop':    t.get('stop_price', 0),
        'target':  t.get('target_price', 0),
        'exit_p':  t.get('exit_price', 0),
    })

# --- stats helpers ---
def stats(ts):
    if not ts: return {}
    n = len(ts); wins = [t for t in ts if t['r'] > 0]; losses = [t for t in ts if t['r'] <= 0]
    total_r = sum(t['r'] for t in ts); total_pnl = sum(t['pnl'] for t in ts)
    return {'n': n, 'wins': len(wins), 'losses': len(losses),
            'wr': round(len(wins)/n*100,1), 'total_r': round(total_r,2),
            'avg_r': round(total_r/n,3), 'total_pnl': round(total_pnl,2)}

by_ses = defaultdict(list); by_sym = defaultdict(list); by_dir = defaultdict(list); by_sc = defaultdict(list)
for t in trades:
    by_ses[t['ses']].append(t); by_sym[t['sym']].append(t)
    by_dir[t['dir']].append(t)
    sc = str(t['score']); by_sc[sc].append(t)

g = stats(trades)
peak = CAPITAL; max_dd = 0.0
for t in trades:
    if t['bal'] > peak: peak = t['bal']
    dd = (peak - t['bal']) / peak * 100
    if dd > max_dd: max_dd = dd

# equity curve data
eq_labels = [t['dt'] for t in trades]
eq_values = [t['bal'] for t in trades]
eq_colors = ['#22c55e' if t['r'] > 0 else '#ef4444' for t in trades]

# --- HTML ---
def bar_chart_data(by_dict, metric='total_pnl'):
    labels, vals, colors = [], [], []
    for k in sorted(by_dict):
        s = stats(by_dict[k])
        labels.append(k); v = s[metric]; vals.append(v)
        colors.append('#22c55e' if v >= 0 else '#ef4444')
    return json.dumps(labels), json.dumps(vals), json.dumps(colors)

ses_l, ses_v, ses_c = bar_chart_data(by_ses, 'total_pnl')
sym_l, sym_v, sym_c = bar_chart_data(by_sym, 'total_pnl')
wr_l,  wr_v,  _    = bar_chart_data(by_ses, 'wr')
wr_sym_l, wr_sym_v, _ = bar_chart_data(by_sym, 'wr')

rows_html = ''
for t in trades:
    win = t['r'] > 0
    r_cls = 'win' if win else 'loss'
    flag = '▲' if t['dir'] == 'Long' else '▼'
    dir_cls = 'long' if t['dir'] == 'Long' else 'short'
    rows_html += f"""
    <tr class="{r_cls}">
      <td>{t['i']}</td>
      <td>{t['dt']}</td>
      <td>{t['sym']}</td>
      <td class="{dir_cls}">{flag} {t['dir']}</td>
      <td>{t['ses']}</td>
      <td>{t['score']}</td>
      <td class="{r_cls}">{t['r']:+.2f}R</td>
      <td class="{r_cls}">${t['pnl']:+.2f}</td>
      <td>${t['bal']:.2f}</td>
      <td>{t['reason']}</td>
    </tr>"""

def breakdown_table(by_dict):
    html = '<table class="mini-table"><tr><th>Grupo</th><th>n</th><th>WR</th><th>TotalR</th><th>PnL$</th></tr>'
    for k in sorted(by_dict, key=lambda x: -stats(by_dict[x])['total_pnl']):
        s = stats(by_dict[k])
        cls = 'pos' if s['total_pnl'] >= 0 else 'neg'
        html += f'<tr><td>{k}</td><td>{s["n"]}</td><td>{s["wr"]}%</td>'
        html += f'<td class="{cls}">{s["total_r"]:+.2f}R</td>'
        html += f'<td class="{cls}">${s["total_pnl"]:+.2f}</td></tr>'
    html += '</table>'
    return html

ses_table  = breakdown_table(by_ses)
sym_table  = breakdown_table(by_sym)
dir_table  = breakdown_table(by_dir)
sc_table   = breakdown_table(by_sc)

roi = round((balance - CAPITAL) / CAPITAL * 100, 1)
roi_cls = 'pos' if roi >= 0 else 'neg'
final_pnl = round(balance - CAPITAL, 2)
pnl_cls = 'pos' if final_pnl >= 0 else 'neg'

HTML = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>RBF Backtest — $450</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0f1117; color: #e2e8f0; font-family: 'Segoe UI', monospace; font-size: 13px; }}
  h1   {{ font-size: 20px; font-weight: 700; color: #f8fafc; }}
  h2   {{ font-size: 14px; font-weight: 600; color: #94a3b8; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 10px; }}
  .header {{ background: #1e2130; padding: 16px 24px; border-bottom: 1px solid #2d3348; display: flex; align-items: center; gap: 20px; }}
  .header span {{ color: #64748b; font-size: 12px; }}
  .main {{ padding: 20px 24px; display: flex; flex-direction: column; gap: 20px; }}
  .cards {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; }}
  .card {{ background: #1e2130; border: 1px solid #2d3348; border-radius: 8px; padding: 14px 16px; }}
  .card .label {{ color: #64748b; font-size: 11px; text-transform: uppercase; letter-spacing: .5px; margin-bottom: 4px; }}
  .card .value {{ font-size: 22px; font-weight: 700; }}
  .pos {{ color: #22c55e; }} .neg {{ color: #ef4444; }} .neu {{ color: #f8fafc; }}
  .row2 {{ display: grid; grid-template-columns: 2fr 1fr; gap: 16px; }}
  .row3 {{ display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 16px; }}
  .box {{ background: #1e2130; border: 1px solid #2d3348; border-radius: 8px; padding: 16px; }}
  .chart-wrap {{ position: relative; height: 220px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th {{ background: #0f1117; color: #64748b; font-size: 11px; text-transform: uppercase; letter-spacing: .5px; padding: 8px 10px; text-align: left; position: sticky; top: 0; }}
  td {{ padding: 6px 10px; border-bottom: 1px solid #1e2130; }}
  tr:hover td {{ background: #1e2130; }}
  tr.win td {{ }}
  tr.loss td {{ }}
  .win {{ color: #22c55e; }} .loss {{ color: #ef4444; }}
  .long {{ color: #38bdf8; }} .short {{ color: #f97316; }}
  .table-wrap {{ max-height: 500px; overflow-y: auto; background: #0f1117; border: 1px solid #2d3348; border-radius: 8px; }}
  .mini-table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  .mini-table th {{ background: #0f1117; color: #64748b; font-size: 10px; text-transform: uppercase; padding: 6px 8px; }}
  .mini-table td {{ padding: 5px 8px; border-bottom: 1px solid #1a1f2e; }}
  .mini-table .pos {{ color: #22c55e; }} .mini-table .neg {{ color: #ef4444; }}
</style>
</head>
<body>

<div class="header">
  <h1>RBF Backtest Dashboard</h1>
  <span>72 trades | Jun 2 – Jun 10, 2026 | Capital $450 | Riesgo 2% = $9/trade</span>
</div>

<div class="main">

  <!-- CARDS -->
  <div class="cards">
    <div class="card"><div class="label">Capital Inicial</div><div class="value neu">${CAPITAL:.0f}</div></div>
    <div class="card"><div class="label">Balance Final</div><div class="value {pnl_cls}">${balance:.2f}</div></div>
    <div class="card"><div class="label">PnL Total</div><div class="value {pnl_cls}">${final_pnl:+.2f}</div></div>
    <div class="card"><div class="label">ROI</div><div class="value {roi_cls}">{roi:+.1f}%</div></div>
    <div class="card"><div class="label">Win Rate</div><div class="value neu">{g["wr"]}%</div></div>
    <div class="card"><div class="label">Max Drawdown</div><div class="value neg">{max_dd:.1f}%</div></div>
  </div>
  <div class="cards">
    <div class="card"><div class="label">Trades</div><div class="value neu">{g["n"]}</div></div>
    <div class="card"><div class="label">Wins</div><div class="value pos">{g["wins"]}</div></div>
    <div class="card"><div class="label">Losses</div><div class="value neg">{g["losses"]}</div></div>
    <div class="card"><div class="label">Total R</div><div class="value {pnl_cls}">{g["total_r"]:+.2f}R</div></div>
    <div class="card"><div class="label">Avg R/trade</div><div class="value {pnl_cls}">{g["avg_r"]:+.3f}R</div></div>
    <div class="card"><div class="label">Riesgo/trade</div><div class="value neu">${RISK:.0f}</div></div>
  </div>

  <!-- EQUITY CURVE -->
  <div class="box">
    <h2>Equity Curve</h2>
    <div class="chart-wrap">
      <canvas id="eqChart"></canvas>
    </div>
  </div>

  <!-- PNL POR SESION Y SIMBOLO -->
  <div class="row2">
    <div class="box">
      <h2>PnL por Sesion</h2>
      <div class="chart-wrap" style="height:180px"><canvas id="sesChart"></canvas></div>
    </div>
    <div class="box">
      <h2>PnL por Simbolo</h2>
      <div class="chart-wrap" style="height:180px"><canvas id="symChart"></canvas></div>
    </div>
  </div>

  <!-- WR POR SESION Y SIMBOLO -->
  <div class="row2">
    <div class="box">
      <h2>Win Rate por Sesion</h2>
      <div class="chart-wrap" style="height:180px"><canvas id="wrSesChart"></canvas></div>
    </div>
    <div class="box">
      <h2>Win Rate por Simbolo</h2>
      <div class="chart-wrap" style="height:180px"><canvas id="wrSymChart"></canvas></div>
    </div>
  </div>

  <!-- BREAKDOWNS -->
  <div class="row3">
    <div class="box"><h2>Por Sesion</h2>{ses_table}</div>
    <div class="box"><h2>Por Simbolo</h2>{sym_table}</div>
    <div class="box"><h2>Por Direccion</h2>{dir_table}</div>
    <div class="box"><h2>Por Score</h2>{sc_table}</div>
  </div>

  <!-- TABLA DE TRADES -->
  <div class="box">
    <h2>Trades ({g["n"]})</h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr><th>#</th><th>Fecha</th><th>Symbol</th><th>Dir</th><th>Session</th><th>Score</th><th>R</th><th>PnL $</th><th>Balance</th><th>Razon</th></tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
    </div>
  </div>

</div>

<script>
const chartDefaults = {{
  responsive: true, maintainAspectRatio: false,
  plugins: {{ legend: {{ display: false }}, tooltip: {{ callbacks: {{
    label: ctx => ` ${{ctx.parsed.y >= 0 ? '+' : ''}}${{ctx.parsed.y.toFixed(2)}}`
  }} }} }},
  scales: {{
    x: {{ ticks: {{ color: '#475569', font: {{ size: 10 }} }}, grid: {{ color: '#1e2130' }} }},
    y: {{ ticks: {{ color: '#475569', font: {{ size: 10 }} }}, grid: {{ color: '#1e2130' }} }},
  }}
}};

// Equity curve
new Chart(document.getElementById('eqChart'), {{
  type: 'line',
  data: {{
    labels: {json.dumps(eq_labels)},
    datasets: [{{
      data: {json.dumps(eq_values)},
      borderColor: '#3b82f6', backgroundColor: 'rgba(59,130,246,0.08)',
      borderWidth: 2, pointRadius: 3,
      pointBackgroundColor: {json.dumps(eq_colors)},
      pointBorderColor: 'transparent', fill: true, tension: 0.3,
    }}]
  }},
  options: {{ ...chartDefaults,
    plugins: {{ ...chartDefaults.plugins,
      tooltip: {{ callbacks: {{ label: ctx => ` Balance: $${{ctx.parsed.y.toFixed(2)}}` }} }}
    }},
    scales: {{ ...chartDefaults.scales,
      y: {{ ...chartDefaults.scales.y, ticks: {{ ...chartDefaults.scales.y.ticks,
        callback: v => `$${{v.toFixed(0)}}`
      }} }}
    }}
  }}
}});

function barChart(id, labels, values, colors, prefix='$') {{
  new Chart(document.getElementById(id), {{
    type: 'bar',
    data: {{ labels, datasets: [{{ data: values, backgroundColor: colors, borderRadius: 4 }}] }},
    options: {{ ...chartDefaults,
      plugins: {{ ...chartDefaults.plugins,
        tooltip: {{ callbacks: {{ label: ctx => ` ${{prefix}}${{ctx.parsed.y >= 0 ? '+' : ''}}${{ctx.parsed.y.toFixed(2)}}` }} }}
      }},
      scales: {{ ...chartDefaults.scales,
        y: {{ ...chartDefaults.scales.y, ticks: {{ ...chartDefaults.scales.y.ticks,
          callback: v => `${{prefix}}${{v}}`
        }} }}
      }}
    }}
  }});
}}

barChart('sesChart', {ses_l}, {ses_v}, {ses_c});
barChart('symChart', {sym_l}, {sym_v}, {sym_c});

function wrChart(id, labels, values) {{
  const colors = values.map(v => v >= 50 ? '#22c55e' : v >= 35 ? '#f59e0b' : '#ef4444');
  new Chart(document.getElementById(id), {{
    type: 'bar',
    data: {{ labels, datasets: [{{ data: values, backgroundColor: colors, borderRadius: 4 }}] }},
    options: {{ ...chartDefaults,
      plugins: {{ ...chartDefaults.plugins,
        tooltip: {{ callbacks: {{ label: ctx => ` ${{ctx.parsed.y.toFixed(1)}}%` }} }}
      }},
      scales: {{ ...chartDefaults.scales,
        y: {{ ...chartDefaults.scales.y, min: 0, max: 100,
          ticks: {{ ...chartDefaults.scales.y.ticks, callback: v => `${{v}}%` }}
        }}
      }}
    }}
  }});
}}

wrChart('wrSesChart',  {wr_l},     {wr_v});
wrChart('wrSymChart',  {wr_sym_l}, {wr_sym_v});
</script>
</body>
</html>"""

out = ROOT / 'rbf_backtest_dashboard.html'
out.write_text(HTML, encoding='utf-8')
print(f"Generado -> {out}")
webbrowser.open(str(out))
