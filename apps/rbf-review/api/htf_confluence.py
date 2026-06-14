#!/usr/bin/env python3
"""
HTF Confluence Score Analysis
==============================
Compara baseline vs dynamic-target y muestra:
1. Distribucion de scores (0-5) con WR/AvgR por bucket
2. Que flags son mas frecuentes en winners vs losers
3. Comparacion baseline vs dynamic-target en equity/WR/AvgR

Uso:
  python htf_confluence.py [--days N]
"""
import json, subprocess, sys
from pathlib import Path
from collections import defaultdict, Counter

SCRIPT = Path(__file__).parent / 'shorts_htf_backtest.py'

def run(days, dynamic=False):
    cmd = [sys.executable, str(SCRIPT), '--days', str(days)]
    if dynamic:
        cmd.append('--dynamic-target')
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print('ERROR:', r.stderr[:300])
        return None
    return json.loads(r.stdout)

def stats(trades):
    if not trades:
        return {'n': 0, 'wr': 0, 'avgR': 0, 'totalR': 0}
    n    = len(trades)
    wins = [t for t in trades if t['resultR'] > 0]
    tr   = sum(t['resultR'] for t in trades)
    return {'n': n, 'wr': round(len(wins)/n*100, 1), 'avgR': round(tr/n, 3), 'totalR': round(tr, 2)}

def sep(title):
    print(f'\n{"="*65}')
    print(f'  {title}')
    print(f'{"="*65}')

def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 14

    print(f'Corriendo baseline ({days}d)...', end=' ', flush=True)
    base = run(days, dynamic=False)
    if not base: sys.exit(1)
    print(f'n={base["n"]} WR={base["wr_pct"]}% AvgR={base["avg_r"]}R')

    print(f'Corriendo dynamic-target ({days}d)...', end=' ', flush=True)
    dyn = run(days, dynamic=True)
    if not dyn: sys.exit(1)
    print(f'n={dyn["n"]} WR={dyn["wr_pct"]}% AvgR={dyn["avg_r"]}R')

    trades = base['trades']

    # ── 1. WR/AvgR por confluence score ─────────────────────────────────────
    sep('1. WR / AvgR POR CONFLUENCE SCORE (baseline)')
    by_score = defaultdict(list)
    for t in trades:
        by_score[t['score']].append(t)

    print(f'  {"Score":<7} {"n":>4} {"WR%":>6} {"AvgR":>8} {"TotalR":>8}  Flags comunes')
    print('  ' + '-'*70)
    for sc in sorted(by_score.keys()):
        ts = by_score[sc]
        s  = stats(ts)
        # flags mas comunes en este bucket
        all_flags = [f for t in ts for f in t.get('confluenceFlags', [])]
        top = ', '.join(f'{k}({v})' for k, v in Counter(all_flags).most_common(3))
        target_shown = {0:'2.0R',1:'2.0R',2:'2.5R',3:'2.5R',4:'3.0R',5:'3.5R'}.get(sc,'?')
        print(f'  {sc}/5 ({target_shown}): {s["n"]:>3}  {s["wr"]:>5.1f}%  {s["avgR"]:>+7.3f}R  {s["totalR"]:>+7.2f}R  [{top}]')

    # ── 2. Flags en winners vs losers ────────────────────────────────────────
    sep('2. FLAGS MAS FRECUENTES: WINNERS vs LOSERS')
    winners = [t for t in trades if t['resultR'] > 0]
    losers  = [t for t in trades if t['resultR'] <= 0]

    win_flags  = Counter(f for t in winners for f in t.get('confluenceFlags', []))
    loss_flags = Counter(f for t in losers  for f in t.get('confluenceFlags', []))
    all_flags  = set(win_flags) | set(loss_flags)

    nw = len(winners); nl = len(losers)
    print(f'  {"Flag":<18} {"Win%":>6} {"Loss%":>7}  Edge')
    print('  ' + '-'*45)
    rows = []
    for flag in all_flags:
        wp = win_flags[flag]  / nw * 100 if nw else 0
        lp = loss_flags[flag] / nl * 100 if nl else 0
        rows.append((flag, wp, lp, wp - lp))
    for flag, wp, lp, edge in sorted(rows, key=lambda x: -x[3]):
        bar = '+' * int(abs(edge)/2) if edge > 0 else '-' * int(abs(edge)/2)
        print(f'  {flag:<18} {wp:>5.1f}%  {lp:>6.1f}%  {edge:>+5.1f}pp  {bar}')

    # ── 3. Score medio en winners vs losers ──────────────────────────────────
    sep('3. SCORE MEDIO')
    sc_win  = [t['score'] for t in winners]
    sc_loss = [t['score'] for t in losers]
    avg_w = sum(sc_win)/len(sc_win) if sc_win else 0
    avg_l = sum(sc_loss)/len(sc_loss) if sc_loss else 0
    print(f'  Score medio winners : {avg_w:.2f}')
    print(f'  Score medio losers  : {avg_l:.2f}')
    print(f'  Diferencia          : {avg_w - avg_l:+.2f}')

    # ── 4. Baseline vs Dynamic Target ────────────────────────────────────────
    sep('4. BASELINE vs DYNAMIC TARGET')
    print(f'  {"":22} {"n":>4} {"WR%":>6} {"AvgR":>8} {"TotalR":>8} {"Equity":>8}')
    print('  ' + '-'*60)
    print(f'  {"Baseline (2.5R fijo)":<22} {base["n"]:>4} {base["wr_pct"]:>5.1f}%  {base["avg_r"]:>+7.3f}R  {base["total_r"]:>+7.2f}R  ${base["equity"]:>7.0f}')
    print(f'  {"Dynamic target":<22} {dyn["n"]:>4} {dyn["wr_pct"]:>5.1f}%  {dyn["avg_r"]:>+7.3f}R  {dyn["total_r"]:>+7.2f}R  ${dyn["equity"]:>7.0f}')
    dn    = dyn['n'] - base['n']
    dwr   = dyn['wr_pct'] - base['wr_pct']
    davgr = dyn['avg_r'] - base['avg_r']
    deq   = dyn['equity'] - base['equity']
    print(f'  {"Delta":<22} {dn:>+4} {dwr:>+5.1f}pp {davgr:>+8.3f}R           ${deq:>+7.0f}')

    # ── 5. Dynamic target por score ──────────────────────────────────────────
    sep('5. DYNAMIC TARGET: WR/AvgR POR SCORE')
    dyn_by_score = defaultdict(list)
    for t in dyn['trades']:
        dyn_by_score[t['score']].append(t)

    print(f'  {"Score":<7} {"target":>6} {"n":>4} {"WR%":>6} {"AvgR":>8}  vs baseline')
    print('  ' + '-'*55)
    for sc in sorted(set(list(by_score.keys()) + list(dyn_by_score.keys()))):
        b_s = stats(by_score.get(sc, []))
        d_s = stats(dyn_by_score.get(sc, []))
        target_r = {0:'2.0R',1:'2.0R',2:'2.5R',3:'2.5R',4:'3.0R',5:'3.5R'}.get(sc,'?')
        davgr = d_s['avgR'] - b_s['avgR'] if b_s['n'] else 0
        print(f'  {sc}/5  {target_r:>6}  {d_s["n"]:>3}  {d_s["wr"]:>5.1f}%  {d_s["avgR"]:>+7.3f}R  (dAvgR={davgr:>+.3f}R)')

    print()

if __name__ == '__main__':
    main()
