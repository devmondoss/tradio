#!/usr/bin/env python3
"""
Compara baseline (2.5R fijo) vs swing-target (swing_low_50 estructural).
"""
import json, subprocess, sys
from pathlib import Path
from collections import defaultdict

SCRIPT = Path(__file__).parent / 'mtf_shorts_backtest.py'

def run(days, flag=None):
    cmd = [sys.executable, str(SCRIPT), '--days', str(days)]
    if flag:
        cmd.append(flag)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print('ERROR:', r.stderr[:300])
        return None
    return json.loads(r.stdout)

def stats(trades):
    if not trades:
        return {'n':0,'wr':0,'avgR':0,'totalR':0}
    n    = len(trades)
    wins = [t for t in trades if t['resultR'] > 0]
    tr   = sum(t['resultR'] for t in trades)
    return {'n':n,'wr':round(len(wins)/n*100,1),'avgR':round(tr/n,3),'totalR':round(tr,2)}

def by_reason(trades):
    d = defaultdict(list)
    for t in trades: d[t['reason']].append(t)
    return {r: stats(ts) for r, ts in d.items()}

def sep(t):
    print(f'\n{"="*62}\n  {t}\n{"="*62}')

def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 14

    print(f'Corriendo baseline...', end=' ', flush=True)
    base = run(days)
    print(f'n={base["n"]} WR={base["wr_pct"]}% AvgR={base["avg_r"]}R Equity=${base["equity"]}')

    print(f'Corriendo swing-target...', end=' ', flush=True)
    swing = run(days, '--swing-target')
    print(f'n={swing["n"]} WR={swing["wr_pct"]}% AvgR={swing["avg_r"]}R Equity=${swing["equity"]}')

    sep('COMPARACION GLOBAL')
    print(f'  {"":24} {"n":>4} {"WR%":>6} {"AvgR":>8} {"TotalR":>8} {"Equity":>8}')
    print('  ' + '-'*58)
    print(f'  {"Baseline (2.5R fijo)":<24} {base["n"]:>4} {base["wr_pct"]:>5.1f}%  {base["avg_r"]:>+7.3f}R  {base["total_r"]:>+7.2f}R  ${base["equity"]:>7.0f}')
    print(f'  {"Swing target":<24} {swing["n"]:>4} {swing["wr_pct"]:>5.1f}%  {swing["avg_r"]:>+7.3f}R  {swing["total_r"]:>+7.2f}R  ${swing["equity"]:>7.0f}')
    dn=swing["n"]-base["n"]; dwr=swing["wr_pct"]-base["wr_pct"]
    davgr=swing["avg_r"]-base["avg_r"]; deq=swing["equity"]-base["equity"]
    print(f'  {"Delta":<24} {dn:>+4} {dwr:>+5.1f}pp {davgr:>+8.3f}R           ${deq:>+7.0f}')

    sep('EXIT REASONS')
    base_r  = by_reason(base['trades'])
    swing_r = by_reason(swing['trades'])
    all_reasons = sorted(set(list(base_r)+list(swing_r)))
    print(f'  {"Reason":<18} {"Base n":>6} {"Base WR":>7} {"Base AvgR":>9}  |  {"Swing n":>7} {"Swing WR":>8} {"Swing AvgR":>10}')
    print('  ' + '-'*75)
    for r in all_reasons:
        b = base_r.get(r, {'n':0,'wr':0,'avgR':0})
        s = swing_r.get(r, {'n':0,'wr':0,'avgR':0})
        print(f'  {r:<18} {b["n"]:>6}  {b["wr"]:>6.1f}%  {b["avgR"]:>+8.3f}R  |  {s["n"]:>7}  {s["wr"]:>7.1f}%  {s["avgR"]:>+9.3f}R')

    sep('TARGET_R DISTRIBUTION (swing-target)')
    by_tr = defaultdict(list)
    for t in swing['trades']:
        bucket = round(t['targetR'] * 2) / 2  # redondear a 0.5R
        by_tr[bucket].append(t)
    print(f'  {"TargetR":>7} {"n":>4} {"WR%":>6} {"AvgR":>8} {"TotalR":>8}')
    print('  ' + '-'*42)
    for tr_val in sorted(by_tr.keys()):
        s = stats(by_tr[tr_val])
        print(f'  {tr_val:>6.1f}R  {s["n"]:>4}  {s["wr"]:>5.1f}%  {s["avgR"]:>+7.3f}R  {s["totalR"]:>+7.2f}R')

    sep('TRADES DONDE SWING TARGET CAMBIA EL RESULTADO')
    base_map  = {t['id']: t for t in base['trades']}
    swing_map = {t['id']: t for t in swing['trades']}
    changed = []
    for tid, st in swing_map.items():
        bt = base_map.get(tid)
        if bt and bt['reason'] != st['reason']:
            changed.append((bt, st))
    print(f'  Trades con diferente exit reason: {len(changed)}')
    print(f'  {"#":>3} {"sym":>5} {"sig":<22} {"base_reason":<18} {"swing_reason":<18} {"dR":>6}')
    print('  ' + '-'*78)
    for bt, st in sorted(changed, key=lambda x: x[1]['resultR'] - x[0]['resultR'], reverse=True):
        dr = st['resultR'] - bt['resultR']
        print(f'  {bt["idx"]:>3} {bt["sym"].replace("USDT",""):>5} {bt["sig"]:<22} {bt["reason"]:<18} {st["reason"]:<18} {dr:>+6.3f}R')

    print()

if __name__ == '__main__':
    main()
