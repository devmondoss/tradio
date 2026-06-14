#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys, io, csv
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from collections import defaultdict

setups = []
with open('scripts/_conscious_bt.csv', encoding='utf-8') as f:
    for row in csv.DictReader(f):
        if row['direction'] != 'Short':
            continue
        result = float(row['result_r'])
        flags  = [f for f in row['flags'].split('|') if f] if row['flags'] else []
        setups.append({
            'result':   result,
            'flags':    flags,
            'score':    float(row['score']),
            'exp_n':    int(row['expansion_n']),
            'vr':       float(row['vr_brk']),
            'cum_d':    float(row['cum_delta']),
            'last5':    float(row['last5_delta']),
            'dz':       float(row['dz_dir']),
            'obi':      float(row['obi_brk']),
            'ext':      float(row['ext_pct']),
            'pre_ok':   row['pre_cvd_ok'] == 'True',
            'sess':     row['session'],
            'sym':      row['sym'],
            'ts':       row['ts'],
        })

n_all  = len(setups)
wr_all = sum(1 for s in setups if s['result'] > 0) / n_all * 100
avg_all = sum(s['result'] for s in setups) / n_all

print('=' * 70)
print(f'INVESTIGACION SCORE — {n_all} Shorts  (baseline WR={wr_all:.0f}%  AvgR={avg_all:+.3f})')
print('=' * 70)

# ── 1. Impacto de cada flag individual ────────────────────────────────────────
print('\n[1] IMPACTO DE CADA FLAG vs BASELINE')
flag_stats = defaultdict(lambda: {'wins': 0, 'total': 0, 'sum_r': 0.0})
for s in setups:
    for f in s['flags']:
        flag_stats[f]['total'] += 1
        flag_stats[f]['sum_r'] += s['result']
        if s['result'] > 0:
            flag_stats[f]['wins'] += 1

print(f"  {'Flag':<34}  {'n':>4}  {'WR':>5}  {'AvgR':>7}  {'delta':>8}")
print(f"  {'-'*65}")
for flag, st in sorted(flag_stats.items(), key=lambda x: x[1]['sum_r'] / max(x[1]['total'], 1), reverse=True):
    n   = st['total']
    wr  = st['wins'] / n * 100 if n else 0
    avg = st['sum_r'] / n if n else 0
    delta = avg - avg_all
    marker = '++' if delta > 0.2 else ('--' if delta < -0.2 else '  ')
    print(f"  {marker} {flag:<32}  {n:>4}  {wr:>4.0f}%  {avg:>+7.3f}  {delta:>+8.3f}")

# ── 2. Que pasa cuando un flag MALO aparece? ──────────────────────────────────
print('\n[2] FLAGS NEGATIVOS — setups que los tienen vs los que no')
bad_flags = ['COMPRADORES_DOMINAN', 'PRE_CVD_CONTRA', 'DZ_EXTREMO', 'VPIN_TOXICO', 'STACKED_CONTRA']
for bf in bad_flags:
    con    = [s for s in setups if bf in s['flags']]
    sin    = [s for s in setups if bf not in s['flags']]
    if not con:
        continue
    wr_con = sum(1 for s in con if s['result'] > 0) / len(con) * 100
    wr_sin = sum(1 for s in sin if s['result'] > 0) / len(sin) * 100
    avg_con = sum(s['result'] for s in con) / len(con)
    avg_sin = sum(s['result'] for s in sin) / len(sin)
    print(f"  {bf}")
    print(f"    CON ({len(con):>2}): WR={wr_con:.0f}%  AvgR={avg_con:+.3f}")
    print(f"    SIN ({len(sin):>2}): WR={wr_sin:.0f}%  AvgR={avg_sin:+.3f}")

# ── 3. Por que score alto = peor resultado? ───────────────────────────────────
print('\n[3] SCORE ALTO VS BAJO — que tienen en comun los score>=6 que pierden?')
hi_lose = [s for s in setups if s['score'] >= 6 and s['result'] < 0]
hi_win  = [s for s in setups if s['score'] >= 6 and s['result'] > 0]

def avg_field(lst, field):
    if not lst: return 0.0
    return sum(s[field] for s in lst) / len(lst)

print(f"  Score>=6 ganadores (n={len(hi_win)})  vs  perdedores (n={len(hi_lose)})")
for field in ['exp_n', 'vr', 'dz', 'obi', 'cum_d', 'last5', 'ext']:
    v_w = avg_field(hi_win,  field)
    v_l = avg_field(hi_lose, field)
    print(f"    {field:<12}: ganadores={v_w:>10.2f}  perdedores={v_l:>10.2f}  delta={v_l-v_w:>+10.2f}")

print()
print('  Perdedores score>=6 detalle:')
for s in hi_lose:
    print(f"    {s['ts']}  {s['sym']:<8}  {s['sess']:<20}  R={s['result']:+.2f}  "
          f"exp={s['exp_n']}  vr={s['vr']:.1f}  dz={s['dz']:.2f}  {' '.join(s['flags'])[:60]}")

# ── 4. Variables continuas vs resultado ───────────────────────────────────────
print('\n[4] VARIABLES CONTINUAS — correlacion con outcome')

def bucket_analysis(setups, field, buckets, label):
    print(f"  {label}")
    for lo, hi, tag in buckets:
        lst = [s for s in setups if lo <= s[field] < hi]
        if not lst: continue
        wr  = sum(1 for s in lst if s['result'] > 0) / len(lst) * 100
        avg = sum(s['result'] for s in lst) / len(lst)
        print(f"    {tag:<25}: n={len(lst):>3}  WR={wr:4.0f}%  AvgR={avg:+.3f}")

bucket_analysis(setups, 'exp_n',
    [(0,1,'exp=0'), (1,2,'exp=1'), (2,4,'exp=2-3'), (4,10,'exp=4+'), (10,100,'exp=10+')],
    'expansion_n:')

bucket_analysis(setups, 'vr',
    [(0,3,'vr<3x (nuestro min)'), (3,4,'vr 3-4x'), (4,6,'vr 4-6x'), (6,100,'vr>6x')],
    'VR en breakout:')

bucket_analysis(setups, 'dz',
    [(-99,0,'dz<0 (contra)'), (0,0.5,'dz 0-0.5'), (0.5,1.5,'dz 0.5-1.5'), (1.5,3,'dz 1.5-3'), (3,99,'dz>3 extremo')],
    'dz_dir:')

bucket_analysis(setups, 'ext',
    [(0,0.05,'ext<0.05%'), (0.05,0.1,'ext 0.05-0.1%'), (0.1,0.2,'ext 0.1-0.2%'), (0.2,99,'ext>0.2%')],
    'Extension breakout %:')

bucket_analysis(setups, 'obi',
    [(-1,-0.1,'obi <- 0.1 (bear fuerte)'), (-0.1,-0.05,'obi -0.1 a -0.05'), (-0.05,0.05,'obi neutro'), (0.05,1,'obi>0.05 (bull = malo para short)')],
    'OBI en breakout:')

# ── 5. La pregunta clave: existe un combinacion que discrimine? ───────────────
print('\n[5] MEJORES COMBINACIONES (Shorts, expansion_n <= 1)')
filtered = [s for s in setups if s['exp_n'] <= 1]
print(f"  Base filtrada expansion<=1: n={len(filtered)}  WR={sum(1 for s in filtered if s['result']>0)/len(filtered)*100:.0f}%")
print()

for vr_min in [2.0, 3.0, 4.0]:
    lst = [s for s in filtered if s['vr'] >= vr_min]
    if not lst: continue
    wr  = sum(1 for s in lst if s['result']>0)/len(lst)*100
    avg = sum(s['result'] for s in lst)/len(lst)
    print(f"  + vr>={vr_min}x       : n={len(lst):>3}  WR={wr:.0f}%  AvgR={avg:+.3f}")

for dz_min in [0.5, 1.0]:
    lst = [s for s in filtered if s['dz'] >= dz_min]
    if not lst: continue
    wr  = sum(1 for s in lst if s['result']>0)/len(lst)*100
    avg = sum(s['result'] for s in lst)/len(lst)
    print(f"  + dz>={dz_min}         : n={len(lst):>3}  WR={wr:.0f}%  AvgR={avg:+.3f}")

for pre_ok in [True]:
    lst = [s for s in filtered if s['pre_ok'] == pre_ok]
    if not lst: continue
    wr  = sum(1 for s in lst if s['result']>0)/len(lst)*100
    avg = sum(s['result'] for s in lst)/len(lst)
    print(f"  + pre_cvd_ok          : n={len(lst):>3}  WR={wr:.0f}%  AvgR={avg:+.3f}")

# Combo: expansion<=1 + vr>=3 + pre_cvd_ok
lst = [s for s in filtered if s['vr'] >= 3.0 and s['pre_ok']]
if lst:
    wr  = sum(1 for s in lst if s['result']>0)/len(lst)*100
    avg = sum(s['result'] for s in lst)/len(lst)
    print(f"  + vr>=3 + pre_cvd_ok  : n={len(lst):>3}  WR={wr:.0f}%  AvgR={avg:+.3f}")

# Combo: expansion<=1 + vr>=3 + pre_cvd_ok + dz>=0.5
lst = [s for s in filtered if s['vr'] >= 3.0 and s['pre_ok'] and s['dz'] >= 0.5]
if lst:
    wr  = sum(1 for s in lst if s['result']>0)/len(lst)*100
    avg = sum(s['result'] for s in lst)/len(lst)
    print(f"  + vr>=3 + pre + dz>=0.5: n={len(lst):>3}  WR={wr:.0f}%  AvgR={avg:+.3f}")

print('\nListo.')
