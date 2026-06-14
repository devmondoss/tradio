#!/usr/bin/env python3
"""
Compara baseline (2.5R fijo) vs VP-target (POC/VAL/LVN de sesion).
"""
import json, subprocess, sys
from pathlib import Path
from collections import defaultdict

SCRIPT = Path(__file__).parent / 'shorts_htf_backtest.py'

def run(days, flag=None):
    cmd = [sys.executable, str(SCRIPT), '--days', str(days)]
    if flag: cmd.append(flag)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print('ERROR:', r.stderr[:400]); return None
    return json.loads(r.stdout)

def stats(trades):
    if not trades: return {'n':0,'wr':0,'avgR':0,'totalR':0}
    n = len(trades); wins = [t for t in trades if t['resultR'] > 0]
    tr = sum(t['resultR'] for t in trades)
    return {'n':n,'wr':round(len(wins)/n*100,1),'avgR':round(tr/n,3),'totalR':round(tr,2)}

def sep(t): print(f'\n{"="*64}\n  {t}\n{"="*64}')

def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 14

    print('Corriendo baseline...', end=' ', flush=True)
    base = run(days)
    print(f'n={base["n"]} WR={base["wr_pct"]}% AvgR={base["avg_r"]}R Equity=${base["equity"]}')

    print('Corriendo VP-target...', end=' ', flush=True)
    vp = run(days, '--vp-target')
    print(f'n={vp["n"]} WR={vp["wr_pct"]}% AvgR={vp["avg_r"]}R Equity=${vp["equity"]}')

    sep('COMPARACION GLOBAL')
    print(f'  {"":26} {"n":>4} {"WR%":>6} {"AvgR":>8} {"TotalR":>8} {"Equity":>8}')
    print('  ' + '-'*60)
    print(f'  {"Baseline (2.5R fijo)":<26} {base["n"]:>4} {base["wr_pct"]:>5.1f}%  {base["avg_r"]:>+7.3f}R  {base["total_r"]:>+7.2f}R  ${base["equity"]:>7.0f}')
    print(f'  {"VP target (POC/VAL/LVN)":<26} {vp["n"]:>4} {vp["wr_pct"]:>5.1f}%  {vp["avg_r"]:>+7.3f}R  {vp["total_r"]:>+7.2f}R  ${vp["equity"]:>7.0f}')
    print(f'  {"Delta":<26} {vp["n"]-base["n"]:>+4} {vp["wr_pct"]-base["wr_pct"]:>+5.1f}pp {vp["avg_r"]-base["avg_r"]:>+8.3f}R           ${vp["equity"]-base["equity"]:>+7.0f}')

    sep('VP LEVEL USADO COMO TARGET')
    by_vp = defaultdict(list)
    for t in vp['trades']: by_vp[t.get('vpLevel','fixed')].append(t)
    print(f'  {"Level":<8} {"n":>4} {"WR%":>6} {"AvgR":>8} {"TotalR":>8}  Descripcion')
    print('  ' + '-'*62)
    desc = {'LVN':'Low Volume Node (vacio de liquidez)',
            'VAL':'Value Area Low (borde inferior VA)',
            'POC':'Point of Control (mayor volumen)',
            'fixed':'Sin nivel VP cercano → 2.5R fijo'}
    for lvl in ['LVN','VAL','POC','fixed']:
        ts = by_vp.get(lvl,[])
        s = stats(ts)
        if s['n'] == 0: continue
        print(f'  {lvl:<8} {s["n"]:>4} {s["wr"]:>5.1f}%  {s["avgR"]:>+7.3f}R  {s["totalR"]:>+7.2f}R  {desc[lvl]}')

    sep('TARGET_R DISTRIBUTION (VP-target)')
    by_tr = defaultdict(list)
    for t in vp['trades']:
        bucket = round(t['targetR'] * 2) / 2
        by_tr[bucket].append(t)
    print(f'  {"TargetR":>7} {"n":>4} {"WR%":>6} {"AvgR":>8} {"TotalR":>8}  VP level')
    print('  ' + '-'*54)
    for tr_val in sorted(by_tr.keys()):
        ts = by_tr[tr_val]
        s  = stats(ts)
        lvls = set(t.get('vpLevel','?') for t in ts)
        print(f'  {tr_val:>6.1f}R  {s["n"]:>4}  {s["wr"]:>5.1f}%  {s["avgR"]:>+7.3f}R  {s["totalR"]:>+7.2f}R  {",".join(sorted(lvls))}')

    sep('EXIT REASONS')
    def by_reason(trades):
        d = defaultdict(list)
        for t in trades: d[t['reason']].append(t)
        return {r: stats(ts) for r, ts in d.items()}
    br = by_reason(base['trades']); vpr = by_reason(vp['trades'])
    reasons = sorted(set(list(br)+list(vpr)))
    print(f'  {"Reason":<18} {"Base n":>6} {"Base AvgR":>10}  |  {"VP n":>5} {"VP AvgR":>9}  {"dn":>4}')
    print('  ' + '-'*65)
    for r in reasons:
        b_ = br.get(r,{'n':0,'avgR':0}); v_ = vpr.get(r,{'n':0,'avgR':0})
        print(f'  {r:<18} {b_["n"]:>6}  {b_["avgR"]:>+9.3f}R  |  {v_["n"]:>5}  {v_["avgR"]:>+8.3f}R  {v_["n"]-b_["n"]:>+4}')

    sep('TRADES CON EXIT REASON DIFERENTE')
    bm = {t['id']:t for t in base['trades']}
    vm = {t['id']:t for t in vp['trades']}
    changed = [(bm[tid], vm[tid]) for tid in vm if tid in bm and bm[tid]['reason'] != vm[tid]['reason']]
    print(f'  Trades cambiados: {len(changed)}')
    if changed:
        print(f'  {"#":>3} {"sym":>5} {"sig":<20} {"targetR":>7} {"base_exit":<16} {"vp_exit":<16} {"dR":>7}')
        print('  '+'-'*80)
        for bt, vt in sorted(changed, key=lambda x: x[1]['resultR']-x[0]['resultR'], reverse=True):
            dr = vt['resultR'] - bt['resultR']
            print(f'  {bt["idx"]:>3} {bt["sym"].replace("USDT",""):>5} {bt["sig"]:<20} {vt["targetR"]:>6.2f}R  {bt["reason"]:<16} {vt["reason"]:<16} {dr:>+7.3f}R')
    print()

if __name__ == '__main__':
    main()
