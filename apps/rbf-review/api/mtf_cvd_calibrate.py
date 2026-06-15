#!/usr/bin/env python3
"""
Calibracion CVD_EXHAUSTION — 3 variantes vs baseline
A: barras 5 -> 8
B: min profit 1.0R -> 1.5R
C: ambos (barras=8, min_profit=1.5R)
"""
import json, subprocess, sys, importlib, types
from pathlib import Path
from collections import defaultdict

SCRIPT = Path(__file__).parent / 'mtf_shorts_backtest.py'
DAYS   = 14

def run_with_params(cvd_bars, min_profit):
    """Parchea las constantes en memoria y corre el backtest."""
    src = SCRIPT.read_text(encoding='utf-8')
    src = src.replace(f'CVD_FLIP_BARS  = 5',    f'CVD_FLIP_BARS  = {cvd_bars}')
    src = src.replace(f'MIN_PROFIT_CVD = 1.0',   f'MIN_PROFIT_CVD = {min_profit}')
    # escribir a un temp file para ejecutar
    tmp = Path(__file__).parent / '_tmp_backtest.py'
    tmp.write_text(src, encoding='utf-8')
    r = subprocess.run([sys.executable, str(tmp), '--days', str(DAYS)],
                       capture_output=True, text=True)
    tmp.unlink(missing_ok=True)
    if r.returncode != 0:
        print('ERROR:', r.stderr[:300]); return None
    return json.loads(r.stdout)

def stats(trades):
    if not trades: return {'n':0,'wr':0,'avgR':0,'totalR':0}
    n = len(trades); wins = [t for t in trades if t['resultR'] > 0]
    tr = sum(t['resultR'] for t in trades)
    return {'n':n,'wr':round(len(wins)/n*100,1),'avgR':round(tr/n,3),'totalR':round(tr,2)}

def by_reason(trades):
    d = defaultdict(list)
    for t in trades: d[t['reason']].append(t)
    return d

def sep(t): print(f'\n{"="*70}\n  {t}\n{"="*70}')

variants = [
    ('Baseline   (bars=5, minR=1.0)', 5,  1.0),
    ('A: bars=8  (bars=8, minR=1.0)', 8,  1.0),
    ('B: minR=1.5 (bars=5, minR=1.5)',5,  1.5),
    ('C: ambos   (bars=8, minR=1.5)', 8,  1.5),
]

print('Corriendo 4 variantes...\n')
results = []
for label, bars, minr in variants:
    print(f'  {label}...', end=' ', flush=True)
    d = run_with_params(bars, minr)
    if d is None: continue
    results.append((label, bars, minr, d))
    print(f'n={d["n"]} WR={d["wr_pct"]}% AvgR={d["avg_r"]}R Equity=${d["equity"]}')

sep('COMPARACION GLOBAL')
print(f'  {"Variante":<34} {"n":>4} {"WR%":>6} {"AvgR":>8} {"TotalR":>8} {"Equity":>9}')
print('  ' + '-'*72)
base_eq = None
for label, bars, minr, d in results:
    if base_eq is None: base_eq = d['equity']
    deq = d['equity'] - base_eq
    marker = f' ({deq:>+.0f}$)' if deq != 0 else ''
    print(f'  {label:<34} {d["n"]:>4} {d["wr_pct"]:>5.1f}%  {d["avg_r"]:>+7.3f}R  {d["total_r"]:>+7.2f}R  ${d["equity"]:>7.0f}{marker}')

sep('EXIT REASONS POR VARIANTE')
all_reasons = set()
reason_data = {}
for label, bars, minr, d in results:
    br = by_reason(d['trades'])
    reason_data[label] = br
    all_reasons.update(br.keys())

print(f'  {"Reason":<20}', end='')
for label, *_ in results:
    short = label.split('(')[0].strip()
    print(f'  {short:>16}', end='')
print()
print('  ' + '-'*85)

for reason in sorted(all_reasons):
    print(f'  {reason:<20}', end='')
    for label, bars, minr, d in results:
        ts = reason_data[label].get(reason, [])
        s  = stats(ts)
        if s['n'] == 0:
            print(f'  {"---":>16}', end='')
        else:
            print(f'  {s["n"]:>2}t {s["wr"]:>4.0f}% {s["avgR"]:>+6.2f}R', end='')
    print()

sep('CVD_EXHAUSTION: cuantos trades conservamos y que R hacen')
print(f'  {"Variante":<34} {"n_CVD":>6} {"WR_CVD":>8} {"AvgR_CVD":>10} {"n_SL":>6} {"AvgR_SL":>9}')
print('  ' + '-'*75)
for label, bars, minr, d in results:
    cvd = [t for t in d['trades'] if t['reason'] == 'CVD_EXHAUSTION']
    sl  = [t for t in d['trades'] if t['reason'] == 'STOP_LOSS']
    sc  = stats(cvd); ss = stats(sl)
    print(f'  {label:<34} {sc["n"]:>6} {sc["wr"]:>7.0f}%  {sc["avgR"]:>+9.3f}R {ss["n"]:>6} {ss["avgR"]:>+8.3f}R')

sep('TRADES QUE CAMBIAN DE RAZON (baseline vs cada variante)')
base_trades = {t['id']: t for t in results[0][3]['trades']}
for label, bars, minr, d in results[1:]:
    changed = []
    for t in d['trades']:
        bt = base_trades.get(t['id'])
        if bt and bt['reason'] != t['reason']:
            changed.append((bt, t))
    print(f'\n  {label} — {len(changed)} trades cambiados:')
    if changed:
        print(f'    {"#":>3} {"sym":>5} {"base_reason":<18} {"new_reason":<18} {"base_R":>8} {"new_R":>8} {"dR":>7}')
        print('    ' + '-'*72)
        for bt, nt in sorted(changed, key=lambda x: x[1]['resultR']-x[0]['resultR'], reverse=True):
            dr = nt['resultR'] - bt['resultR']
            print(f'    {bt["idx"]:>3} {bt["sym"].replace("USDT",""):>5} {bt["reason"]:<18} {nt["reason"]:<18} {bt["resultR"]:>+7.3f}R {nt["resultR"]:>+7.3f}R {dr:>+6.3f}R')

print()
