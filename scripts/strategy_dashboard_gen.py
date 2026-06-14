#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Genera strategy_dashboard.html — vista unificada de RBF, AMD y BE.

Fuentes de datos:
  RBF : scripts/rbf_pnl_450.csv       (live trades, capital $450)
  AMD : scripts/amd_backtest_results.csv (backtest BTCUSDT 163d)
  BE  : scripts/be_backtest_results.csv  (backtest 5 símbolos 163d)

Uso:
    python scripts/strategy_dashboard_gen.py
    → genera strategy_dashboard.html en la raíz del proyecto
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import csv, json, os
from datetime import datetime, timezone

# ── Lectura de datos ──────────────────────────────────────────────────────────

def load_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as f:
        return list(csv.DictReader(f))

def safe_float(v, default=0.0):
    try: return float(v)
    except: return default

# ── Estadísticas ──────────────────────────────────────────────────────────────

def compute_stats(trades, r_col, bal_col=None, capital=500.0, risk_pct=0.02):
    rs    = [safe_float(t[r_col]) for t in trades]
    n     = len(rs)
    if n == 0:
        return {}
    wins  = sum(1 for r in rs if r > 0)
    total = sum(rs)
    avg   = total / n

    # equity curve
    if bal_col and trades and bal_col in trades[0]:
        equity = [safe_float(t[bal_col]) for t in trades]
        start  = safe_float(trades[0][bal_col]) - safe_float(trades[0].get('pnl_usd', 0))
        equity = [start] + equity
    else:
        bal    = capital
        equity = [bal]
        for r in rs:
            bal += r * bal * risk_pct
            equity.append(round(bal, 2))

    peak   = equity[0]
    max_dd = 0.0
    for v in equity:
        peak   = max(peak, v)
        dd     = (peak - v) / peak * 100
        max_dd = max(max_dd, dd)

    return {
        'n':       n,
        'wins':    wins,
        'wr':      round(wins / n * 100, 1),
        'avg_r':   round(avg, 3),
        'total_r': round(total, 2),
        'start':   round(equity[0], 2),
        'final':   round(equity[-1], 2),
        'gain':    round(equity[-1] - equity[0], 2),
        'gain_pct':round((equity[-1] - equity[0]) / equity[0] * 100, 1),
        'max_dd':  round(max_dd, 2),
        'equity':  equity,
    }

def by_key(trades, key, r_col):
    groups = {}
    for t in trades:
        k = t.get(key, '?')
        groups.setdefault(k, []).append(safe_float(t[r_col]))
    result = []
    for k, rs in sorted(groups.items()):
        n    = len(rs)
        wins = sum(1 for r in rs if r > 0)
        result.append({
            'key': k, 'n': n,
            'wr':  round(wins/n*100) if n else 0,
            'avg': round(sum(rs)/n, 3) if n else 0,
        })
    return result

# ── Tabla de trades → filas HTML ─────────────────────────────────────────────

def trade_rows_rbf(trades):
    rows = ''
    for t in trades:
        r   = safe_float(t.get('result_r', 0))
        clr = '#2ecc71' if r > 0 else '#e74c3c' if r < 0 else '#95a5a6'
        rows += f"""<tr>
            <td>{t.get('fecha','')}</td>
            <td>{t.get('symbol','')}</td>
            <td>{t.get('direction','')}</td>
            <td>{t.get('session','')}</td>
            <td>{t.get('score','')}</td>
            <td style="color:{clr};font-weight:bold">{r:+.2f}R</td>
            <td>{t.get('reason','')}</td>
            <td>${safe_float(t.get('pnl_usd',0)):+.2f}</td>
            <td>${safe_float(t.get('balance',0)):.2f}</td>
        </tr>"""
    return rows

def trade_rows_amd(trades, equity):
    rows = ''
    bal  = equity[0] if equity else 500.0
    for i, t in enumerate(trades):
        r   = safe_float(t.get('result_r', 0))
        clr = '#2ecc71' if r > 0 else '#e74c3c' if r < 0 else '#95a5a6'
        ts  = int(t.get('timestamp_ms', 0))
        dt  = datetime.fromtimestamp(ts/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M') if ts else ''
        bal = equity[i+1] if i+1 < len(equity) else bal
        rows += f"""<tr>
            <td>{dt}</td>
            <td>BTCUSDT</td>
            <td>{t.get('direction','')}</td>
            <td>{t.get('session','')}</td>
            <td>{t.get('spike_direction','')}</td>
            <td>{safe_float(t.get('vr_at_spike',0)):.2f}×</td>
            <td>{safe_float(t.get('rr',0)):.2f}</td>
            <td style="color:{clr};font-weight:bold">{r:+.2f}R</td>
            <td>{t.get('exit','')}</td>
            <td>${bal:.2f}</td>
        </tr>"""
    return rows

def trade_rows_be(trades):
    rows = ''
    for t in trades:
        r   = safe_float(t.get('result_r', 0))
        clr = '#2ecc71' if r > 0 else '#e74c3c' if r < 0 else '#95a5a6'
        rows += f"""<tr>
            <td>{t.get('fecha','')}</td>
            <td>{t.get('symbol','')}</td>
            <td>{t.get('session','')}</td>
            <td>{safe_float(t.get('cvd_flip_ratio',0)):.2f}</td>
            <td>{safe_float(t.get('vr_at_breakout',0)):.2f}×</td>
            <td>{safe_float(t.get('close_loc',0)):.2f}</td>
            <td>{safe_float(t.get('rr',0)):.2f}</td>
            <td style="color:{clr};font-weight:bold">{r:+.2f}R</td>
            <td>{t.get('exit_reason','')}</td>
            <td>${safe_float(t.get('pnl_usd',0)):+.2f}</td>
            <td>${safe_float(t.get('balance_after',0)):.2f}</td>
        </tr>"""
    return rows

# ── Breakdown table HTML ──────────────────────────────────────────────────────

def breakdown_html(items, title):
    rows = ''
    for g in items:
        avg = g['avg']
        clr = '#2ecc71' if avg > 0 else '#e74c3c' if avg < 0 else '#95a5a6'
        rows += f"<tr><td>{g['key']}</td><td>{g['n']}</td><td>{g['wr']}%</td><td style='color:{clr}'>{avg:+.3f}R</td></tr>"
    return f"""
    <div class="breakdown">
      <div class="bk-title">{title}</div>
      <table class="bk-table">
        <thead><tr><th></th><th>n</th><th>WR</th><th>Avg R</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>"""

# ── Generador principal ───────────────────────────────────────────────────────

def main():
    # ── Cargar datos ──────────────────────────────────────────────────────────
    rbf_trades = load_csv('scripts/rbf_pnl_450.csv')
    amd_trades = load_csv('scripts/amd_backtest_results.csv')
    be_trades  = load_csv('scripts/be_backtest_results.csv')

    # AMD: filtrar OPEN (no cerrados)
    amd_trades = [t for t in amd_trades if t.get('exit','') not in ('OPEN', '')]

    # ── Estadísticas ──────────────────────────────────────────────────────────
    rbf_st = compute_stats(rbf_trades, 'result_r', bal_col='balance', capital=450.0)
    amd_st = compute_stats(amd_trades, 'result_r', capital=500.0, risk_pct=0.02)
    be_st  = compute_stats(be_trades,  'result_r', bal_col='balance_after', capital=500.0)

    # ── Datos para Chart.js ───────────────────────────────────────────────────
    rbf_eq_js  = json.dumps(rbf_st.get('equity', []))
    amd_eq_js  = json.dumps(amd_st.get('equity', []))
    be_eq_js   = json.dumps(be_st.get('equity', []))

    # Labels de fechas para cada estrategia
    rbf_labels = json.dumps([t.get('fecha','') for t in rbf_trades])
    be_labels  = json.dumps([t.get('fecha','') for t in be_trades])
    amd_labels = json.dumps([
        datetime.fromtimestamp(int(t.get('timestamp_ms',0))/1000, tz=timezone.utc).strftime('%m/%d %H:%M')
        for t in amd_trades
    ])

    # ── Breakdowns ────────────────────────────────────────────────────────────
    rbf_sess = breakdown_html(by_key(rbf_trades, 'session', 'result_r'), 'Por sesión')
    rbf_sym  = breakdown_html(by_key(rbf_trades, 'symbol',  'result_r'), 'Por símbolo')
    rbf_dir  = breakdown_html(by_key(rbf_trades, 'direction','result_r'), 'Por dirección')

    amd_sess = breakdown_html(by_key(amd_trades, 'session',  'result_r'), 'Por sesión')
    amd_dir  = breakdown_html(by_key(amd_trades, 'direction','result_r'), 'Por dirección')
    amd_spk  = breakdown_html(by_key(amd_trades, 'spike_direction','result_r'), 'Por spike')

    be_sess  = breakdown_html(by_key(be_trades, 'session', 'result_r'), 'Por sesión')
    be_sym   = breakdown_html(by_key(be_trades, 'symbol',  'result_r'), 'Por símbolo')
    be_rsn   = breakdown_html(by_key(be_trades, 'exit_reason','result_r'), 'Por salida')

    # ── Filas de trades ───────────────────────────────────────────────────────
    rbf_rows = trade_rows_rbf(rbf_trades)
    amd_rows = trade_rows_amd(amd_trades, amd_st.get('equity', []))
    be_rows  = trade_rows_be(be_trades)

    # ── Stat card helper ─────────────────────────────────────────────────────
    def stat_card(st, label, note=''):
        gain_clr = '#2ecc71' if st.get('gain',0) >= 0 else '#e74c3c'
        return f"""
        <div class="strategy-card" onclick="switchTab('{label.lower()}')">
          <div class="sc-name">{label}</div>
          <div class="sc-note">{note}</div>
          <div class="sc-row">
            <div class="sc-stat"><div class="sc-val">{st.get('n',0)}</div><div class="sc-lbl">trades</div></div>
            <div class="sc-stat"><div class="sc-val">{st.get('wr',0)}%</div><div class="sc-lbl">WR</div></div>
            <div class="sc-stat"><div class="sc-val">{st.get('avg_r',0):+.3f}R</div><div class="sc-lbl">avg R</div></div>
          </div>
          <div class="sc-pnl" style="color:{gain_clr}">
            ${st.get('start',0):.0f} → ${st.get('final',0):.0f}
            <span class="sc-pct">({st.get('gain_pct',0):+.1f}%)</span>
          </div>
          <div class="sc-dd">Max DD: -{st.get('max_dd',0):.1f}%</div>
        </div>"""

    rbf_card = stat_card(rbf_st, 'RBF', 'Short · London + Overlap · live')
    amd_card = stat_card(amd_st, 'AMD', 'Long + Short · BTC · backtest 163d')
    be_card  = stat_card(be_st,  'BE',  'Short · ETH/BNB/SOL · backtest 163d')

    # ── Stat pills dentro de cada tab ────────────────────────────────────────
    def pills(st):
        g = st.get('gain', 0)
        gclr = '#2ecc71' if g >= 0 else '#e74c3c'
        return f"""
        <div class="pills">
          <div class="pill"><div class="pv">{st.get('n',0)}</div><div class="pl">Trades</div></div>
          <div class="pill"><div class="pv">{st.get('wr',0)}%</div><div class="pl">Win Rate</div></div>
          <div class="pill"><div class="pv">{st.get('avg_r',0):+.3f}R</div><div class="pl">Avg R</div></div>
          <div class="pill"><div class="pv">{st.get('total_r',0):+.1f}R</div><div class="pl">Total R</div></div>
          <div class="pill"><div class="pv" style="color:{gclr}">${g:+.0f}</div><div class="pl">P&L</div></div>
          <div class="pill"><div class="pv" style="color:{gclr}">{st.get('gain_pct',0):+.1f}%</div><div class="pl">Retorno</div></div>
          <div class="pill"><div class="pv" style="color:#e74c3c">-{st.get('max_dd',0):.1f}%</div><div class="pl">Max DD</div></div>
        </div>"""

    now = datetime.now().strftime('%Y-%m-%d %H:%M')

    # ── HTML ──────────────────────────────────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FlowSurface · Strategy Monitor</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ background: #080b10; color: #dde3ec; font-family: 'JetBrains Mono', 'Fira Code', monospace; font-size: 13px; }}

/* ── Header ── */
.header {{ background: #0d1117; border-bottom: 1px solid #1c2333; padding: 16px 24px; display: flex; align-items: center; justify-content: space-between; }}
.header-title {{ font-size: 1.1em; color: #58a6ff; letter-spacing: .08em; font-weight: 700; }}
.header-sub   {{ font-size: .75em; color: #484f58; margin-top: 2px; }}

/* ── Strategy cards ── */
.cards {{ display: flex; gap: 16px; padding: 20px 24px 0; flex-wrap: wrap; }}
.strategy-card {{
  background: #0d1117; border: 1px solid #1c2333; border-radius: 8px;
  padding: 16px 20px; min-width: 220px; flex: 1; cursor: pointer;
  transition: border-color .15s, box-shadow .15s;
}}
.strategy-card:hover {{ border-color: #58a6ff; box-shadow: 0 0 12px rgba(88,166,255,.1); }}
.strategy-card.active {{ border-color: #58a6ff; background: #0e1825; }}
.sc-name {{ font-size: 1.3em; font-weight: 700; color: #58a6ff; letter-spacing: .05em; }}
.sc-note {{ font-size: .7em; color: #484f58; margin: 2px 0 10px; }}
.sc-row  {{ display: flex; gap: 12px; margin-bottom: 8px; }}
.sc-stat {{ text-align: center; }}
.sc-val  {{ font-size: 1.1em; font-weight: 700; color: #e6edf3; }}
.sc-lbl  {{ font-size: .65em; color: #484f58; }}
.sc-pnl  {{ font-size: 1em; font-weight: 700; }}
.sc-pct  {{ font-size: .8em; }}
.sc-dd   {{ font-size: .7em; color: #484f58; margin-top: 2px; }}

/* ── Tab content ── */
.tab-container {{ padding: 20px 24px; }}
.tab {{ display: none; }}
.tab.active {{ display: block; }}

/* ── Pills ── */
.pills {{ display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 16px; }}
.pill  {{ background: #0d1117; border: 1px solid #1c2333; border-radius: 6px; padding: 10px 16px; text-align: center; min-width: 90px; }}
.pv    {{ font-size: 1.15em; font-weight: 700; color: #e6edf3; }}
.pl    {{ font-size: .65em; color: #484f58; margin-top: 2px; }}

/* ── Charts ── */
.chart-wrap {{ background: #0d1117; border: 1px solid #1c2333; border-radius: 8px; padding: 16px; margin-bottom: 16px; }}
.chart-title {{ font-size: .8em; color: #484f58; margin-bottom: 10px; letter-spacing: .05em; }}

/* ── Breakdowns ── */
.breakdowns  {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }}
.breakdown   {{ background: #0d1117; border: 1px solid #1c2333; border-radius: 8px; padding: 12px 16px; min-width: 160px; flex: 1; }}
.bk-title    {{ font-size: .75em; color: #58a6ff; font-weight: 700; margin-bottom: 8px; letter-spacing: .04em; }}
.bk-table    {{ width: 100%; border-collapse: collapse; font-size: .8em; }}
.bk-table th {{ color: #484f58; font-weight: 400; padding: 2px 6px; text-align: left; border-bottom: 1px solid #1c2333; }}
.bk-table td {{ padding: 3px 6px; border-bottom: 1px solid #0d1117; }}

/* ── Trade table ── */
.table-wrap {{ overflow-x: auto; background: #0d1117; border: 1px solid #1c2333; border-radius: 8px; }}
.table-title {{ font-size: .75em; color: #58a6ff; font-weight: 700; padding: 10px 14px 6px; letter-spacing: .04em; }}
table.trades {{ width: 100%; border-collapse: collapse; font-size: .78em; }}
table.trades th {{ background: #080b10; color: #484f58; padding: 6px 10px; text-align: left; position: sticky; top: 0; white-space: nowrap; }}
table.trades td {{ padding: 4px 10px; border-bottom: 1px solid #0d1117; white-space: nowrap; }}
table.trades tr:hover td {{ background: #0e1825; }}

/* ── Section note ── */
.note {{ font-size: .72em; color: #484f58; margin-bottom: 12px; padding: 8px 12px; background: #0d1117; border-left: 2px solid #1c2333; border-radius: 0 4px 4px 0; }}
</style>
</head>
<body>

<div class="header">
  <div>
    <div class="header-title">⬡ FLOWSURFACE · STRATEGY MONITOR</div>
    <div class="header-sub">Actualizado: {now} UTC · 3 estrategias activas</div>
  </div>
</div>

<!-- Strategy comparison cards -->
<div class="cards">
  {rbf_card}
  {amd_card}
  {be_card}
</div>

<!-- Tab content -->
<div class="tab-container">

  <!-- ── RBF ── -->
  <div id="tab-rbf" class="tab active">
    <div class="note">Range Breakout Flow · Short-only · London + LondonNyOverlap · Score ≥ 4 · {rbf_st.get('n',0)} trades live desde jun 2026</div>
    {pills(rbf_st)}
    <div class="chart-wrap">
      <div class="chart-title">EQUITY CURVE — Capital inicial ${rbf_st.get('start',0):.0f}</div>
      <canvas id="rbf-eq" height="70"></canvas>
    </div>
    <div class="breakdowns">{rbf_sess}{rbf_sym}{rbf_dir}</div>
    <div class="table-wrap">
      <div class="table-title">TRADES</div>
      <table class="trades">
        <thead><tr><th>Fecha UTC</th><th>Symbol</th><th>Dir</th><th>Sesión</th><th>Score</th><th>R</th><th>Razón</th><th>PnL</th><th>Balance</th></tr></thead>
        <tbody>{rbf_rows}</tbody>
      </table>
    </div>
  </div>

  <!-- ── AMD ── -->
  <div id="tab-amd" class="tab">
    <div class="note">Accumulation·Manipulation·Distribution · Long + Short · BTCUSDT · Backtest 163d (ene–jun 2026) · n={amd_st.get('n',0)} (bajo frecuencia: ~1 señal/12d)</div>
    {pills(amd_st)}
    <div class="chart-wrap">
      <div class="chart-title">EQUITY CURVE — Capital inicial ${amd_st.get('start',0):.0f} · 2% riesgo/trade</div>
      <canvas id="amd-eq" height="70"></canvas>
    </div>
    <div class="breakdowns">{amd_sess}{amd_dir}{amd_spk}</div>
    <div class="table-wrap">
      <div class="table-title">TRADES</div>
      <table class="trades">
        <thead><tr><th>Fecha UTC</th><th>Symbol</th><th>Dir</th><th>Sesión</th><th>Spike</th><th>VR spike</th><th>RR</th><th>R</th><th>Salida</th><th>Balance</th></tr></thead>
        <tbody>{amd_rows}</tbody>
      </table>
    </div>
  </div>

  <!-- ── BE ── -->
  <div id="tab-be" class="tab">
    <div class="note">Buyer Exhaustion · Short-only · ETH/BNB/SOL/BTC · London + Overlap · win=8 · Backtest 163d (ene–jun 2026) · XRP desactivado (WR=54% avg=-0.025R)</div>
    {pills(be_st)}
    <div class="chart-wrap">
      <div class="chart-title">EQUITY CURVE — Capital inicial ${be_st.get('start',0):.0f} · 2% riesgo/trade</div>
      <canvas id="be-eq" height="70"></canvas>
    </div>
    <div class="breakdowns">{be_sess}{be_sym}{be_rsn}</div>
    <div class="table-wrap">
      <div class="table-title">TRADES</div>
      <table class="trades">
        <thead><tr><th>Fecha UTC</th><th>Symbol</th><th>Sesión</th><th>flip</th><th>VR</th><th>cLoc</th><th>RR</th><th>R</th><th>Razón</th><th>PnL</th><th>Balance</th></tr></thead>
        <tbody>{be_rows}</tbody>
      </table>
    </div>
  </div>

</div>

<script>
// ── Tab switching ──────────────────────────────────────────────────────────
function switchTab(name) {{
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.strategy-card').forEach(c => c.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  const cards = document.querySelectorAll('.strategy-card');
  const map   = {{'rbf':0,'amd':1,'be':2}};
  if (map[name] !== undefined) cards[map[name]].classList.add('active');
}}

// Activar primera card
document.querySelectorAll('.strategy-card')[0].classList.add('active');

// ── Chart factory ──────────────────────────────────────────────────────────
function makeChart(id, data, labels, color) {{
  const ctx = document.getElementById(id);
  if (!ctx) return;
  new Chart(ctx, {{
    type: 'line',
    data: {{
      labels: labels,
      datasets: [{{
        data: data,
        borderColor: color,
        backgroundColor: color.replace(')', ', 0.06)').replace('rgb', 'rgba'),
        pointRadius: 0,
        borderWidth: 1.5,
        tension: 0.3,
        fill: true,
      }}]
    }},
    options: {{
      animation: false,
      plugins: {{ legend: {{ display: false }}, tooltip: {{
        callbacks: {{ label: ctx => '$' + ctx.parsed.y.toFixed(2) }}
      }}}},
      scales: {{
        x: {{ ticks: {{ color:'#484f58', maxTicksLimit: 10 }}, grid: {{ color:'#0d1117' }} }},
        y: {{ ticks: {{ color:'#8b949e', callback: v => '$'+v }}, grid: {{ color:'#1c2333' }} }}
      }}
    }}
  }});
}}

const rbfEq  = {rbf_eq_js};
const amdEq  = {amd_eq_js};
const beEq   = {be_eq_js};
const rbfLbl = {rbf_labels};
const amdLbl = {amd_labels};
const beLbl  = {be_labels};

makeChart('rbf-eq', rbfEq, ['start', ...rbfLbl], 'rgb(88,166,255)');
makeChart('amd-eq', amdEq, ['start', ...amdLbl], 'rgb(63,185,80)');
makeChart('be-eq',  beEq,  ['start', ...beLbl],  'rgb(240,136,62)');
</script>
</body>
</html>"""

    out = 'strategy_dashboard.html'
    with open(out, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'Generado: {out}')
    print(f'  RBF: {rbf_st.get("n",0)} trades  WR={rbf_st.get("wr",0)}%  ${rbf_st.get("start",0):.0f}→${rbf_st.get("final",0):.0f} ({rbf_st.get("gain_pct",0):+.1f}%)')
    print(f'  AMD: {amd_st.get("n",0)} trades  WR={amd_st.get("wr",0)}%  ${amd_st.get("start",0):.0f}→${amd_st.get("final",0):.0f} ({amd_st.get("gain_pct",0):+.1f}%)')
    print(f'  BE:  {be_st.get("n",0)} trades  WR={be_st.get("wr",0)}%  ${be_st.get("start",0):.0f}→${be_st.get("final",0):.0f} ({be_st.get("gain_pct",0):+.1f}%)')

if __name__ == '__main__':
    main()
