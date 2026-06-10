"""
Analisis de calidad: que tienen en comun los trades que ganan vs los que pierden.

Los trades TRAIL (ganadores) llegaron a 1.5R y el trailing stop los saco en ganancias.
Los trades STOP (perdedores) nunca llegaron a 1.5R y se stopearon en -1R.

Objetivo: encontrar los filtros que eleven el WR a 60-70%.
"""

import json
from collections import defaultdict

with open('/tmp/all_bars_full.json') as f:
    raw_bars = json.load(f)

with open('/tmp/momentum_v2_results.json') as f:
    trades = json.load(f)

# Indexar barras por (sym, ts_ms) para hacer lookup
bar_index = {}
for sym, bars_raw in raw_bars.items():
    bars = sorted(bars_raw if isinstance(bars_raw, list) else list(bars_raw.values()),
                  key=lambda b: b['ts_ms'])
    for b in bars:
        bar_index[(sym, b['ts_ms'])] = b

# Separar ganadores y perdedores
winners = [t for t in trades if t['reason'] == 'TRAIL']
losers  = [t for t in trades if t['reason'] == 'STOP']
times   = [t for t in trades if t['reason'] == 'TIME']

print(f'Trades totales: {len(trades)}')
print(f'  TRAIL (ganadores): {len(winners)} ({len(winners)/len(trades)*100:.0f}%)')
print(f'  STOP  (perdedores):{len(losers)}  ({len(losers)/len(trades)*100:.0f}%)')
print(f'  TIME  (parciales): {len(times)}  ({len(times)/len(trades)*100:.0f}%)')

# Recuperar los features de la barra de entrada para cada trade
def get_bar_features(t):
    key = (t['sym'], t['entry_ms'])
    b   = bar_index.get(key)
    if b is None:
        return None
    atr = b.get('atr') or 0
    return {
        'dz':     b.get('dz') or 0,
        'vr':     b.get('vr') or 0,
        'obi':    b.get('obi_l5') or 0,
        'cvd_s':  (b.get('cvd_slope') or 0) / atr if atr > 0 else 0,
        'vpin':   b.get('vpin') or 0,
        'stk':    b.get('stacked_imb') or 'None',
        'absrp':  b.get('absorption') or 'None',
        'thin_a': 1 if b.get('thin_above') else 0,
        'thin_b': 1 if b.get('thin_below') else 0,
        'session': t['session'],
        'dir':    t['dir'],
        'sym':    t['sym'],
    }

w_feats = [f for t in winners if (f := get_bar_features(t)) is not None]
l_feats = [f for t in losers  if (f := get_bar_features(t)) is not None]

def avg(lst): return sum(lst)/len(lst) if lst else 0

print()
print('='*75)
print('COMPARACION DE FEATURES: GANADORES (TRAIL) vs PERDEDORES (STOP)')
print('='*75)
print(f'  {"Feature":<20}  {"Ganadores":>12}  {"Perdedores":>12}  {"Ratio G/P":>12}  Edge?')
print('-'*75)

num_feats = ['dz','vr','obi','cvd_s','vpin']
for feat in num_feats:
    w_vals  = [abs(f[feat]) for f in w_feats]
    l_vals  = [abs(f[feat]) for f in l_feats]
    w_avg   = avg(w_vals)
    l_avg   = avg(l_vals)
    ratio   = w_avg / l_avg if l_avg > 0 else 0
    edge    = '*** MAYOR EN GANADORES' if ratio > 1.2 else ('*** MAYOR EN PERDEDORES' if ratio < 0.83 else 'similar')
    print(f'  {feat:<20}  {w_avg:>12.3f}  {l_avg:>12.3f}  {ratio:>12.2f}x  {edge}')

# Categoricas
print()
print('  Features categoricas (% de aparicion en cada grupo):')
for feat, vals in [('stk', ['Bullish','Bearish','None']),
                   ('absrp', ['Ask','Bid','None']),
                   ('session', ['London','LondonNyOverlap','NewYork','Asia','OffHours'])]:
    print(f'  {feat}:')
    for v in vals:
        w_pct = sum(1 for f in w_feats if f[feat]==v)/len(w_feats)*100
        l_pct = sum(1 for f in l_feats if f[feat]==v)/len(l_feats)*100
        diff  = w_pct - l_pct
        tag   = ' << GANADORES' if diff > 8 else (' >> PERDEDORES' if diff < -8 else '')
        print(f'    {v:<22}: G={w_pct:4.0f}%  P={l_pct:4.0f}%  diff={diff:>+5.0f}%{tag}')

# ── BUSCAR UMBRAL OPTIMO POR FEATURE ─────────────────────────────────────────
print()
print('='*75)
print('UMBRAL OPTIMO POR FEATURE (maximizar WR)')
print('='*75)

all_trades_with_feat = []
for t in trades:
    f = get_bar_features(t)
    if f is None: continue
    f['won'] = 1 if t['reason'] == 'TRAIL' else 0
    f['r']   = t['r']
    all_trades_with_feat.append(f)

def eval_filter(subset):
    if len(subset) < 5: return (0, 0, 0, 0)
    wins = sum(1 for x in subset if x['won'])
    wr   = wins / len(subset) * 100
    avg_r = sum(x['r'] for x in subset) / len(subset)
    return len(subset), wr, avg_r, wins

print()
print('DZ por umbral (|dz| >= threshold):')
print(f'  {"Umbral":<10} {"n":>5} {"WR%":>6} {"AvgR":>8} {"pct_total":>10}')
for thresh in [2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 6.0]:
    sub = [f for f in all_trades_with_feat if abs(f['dz']) >= thresh]
    nn, wr, ar, _ = eval_filter(sub)
    print(f'  |dz| >= {thresh:<4.1f}  {nn:>5}  {wr:>5.0f}%  {ar:>+8.3f}  ({nn/len(all_trades_with_feat)*100:>5.0f}% del total)')

print()
print('VR por rango:')
print(f'  {"Rango":<12} {"n":>5} {"WR%":>6} {"AvgR":>8}')
for lo, hi in [(3.0,4.0),(4.0,5.0),(5.0,6.0),(6.0,99)]:
    sub = [f for f in all_trades_with_feat if lo <= f['vr'] < hi]
    nn, wr, ar, _ = eval_filter(sub)
    print(f'  VR {lo:.0f}-{hi:.0f}x    {nn:>5}  {wr:>5.0f}%  {ar:>+8.3f}')

print()
print('OBI alineado con la direccion:')
sub_aligned = [f for f in all_trades_with_feat
               if (f['dir']=='Short' and f['obi'] < -0.1) or
                  (f['dir']=='Long'  and f['obi'] > 0.1)]
sub_contra  = [f for f in all_trades_with_feat
               if (f['dir']=='Short' and f['obi'] > 0.1) or
                  (f['dir']=='Long'  and f['obi'] < -0.1)]
nn, wr, ar, _ = eval_filter(sub_aligned)
print(f'  OBI alineado: n={nn}  WR={wr:.0f}%  AvgR={ar:+.3f}')
nn, wr, ar, _ = eval_filter(sub_contra)
print(f'  OBI contrario: n={nn}  WR={wr:.0f}%  AvgR={ar:+.3f}')

print()
print('STK_IMB alineado:')
sub_stk_yes = [f for f in all_trades_with_feat
               if (f['dir']=='Short' and f['stk']=='Bearish') or
                  (f['dir']=='Long'  and f['stk']=='Bullish')]
sub_stk_no  = [f for f in all_trades_with_feat
               if (f['dir']=='Short' and f['stk']=='Bullish') or
                  (f['dir']=='Long'  and f['stk']=='Bearish')]
sub_stk_neu = [f for f in all_trades_with_feat if f['stk']=='None']
nn, wr, ar, _ = eval_filter(sub_stk_yes)
print(f'  STK alineado:  n={nn}  WR={wr:.0f}%  AvgR={ar:+.3f}')
nn, wr, ar, _ = eval_filter(sub_stk_no)
print(f'  STK contrario: n={nn}  WR={wr:.0f}%  AvgR={ar:+.3f}')
nn, wr, ar, _ = eval_filter(sub_stk_neu)
print(f'  STK neutro:    n={nn}  WR={wr:.0f}%  AvgR={ar:+.3f}')

# ── COMBINACIONES DE FILTROS PARA 60%+ WR ────────────────────────────────────
print()
print('='*75)
print('COMBINACIONES: BUSCANDO WR >= 60%')
print('='*75)

combos = [
    ('|DZ|>=4 + VR 4-5x',
     lambda f: abs(f['dz'])>=4 and 4<=f['vr']<5),
    ('|DZ|>=4 + OBI alineado',
     lambda f: abs(f['dz'])>=4 and
               ((f['dir']=='Short' and f['obi']<-0.1) or (f['dir']=='Long' and f['obi']>0.1))),
    ('|DZ|>=4 + STK alineado',
     lambda f: abs(f['dz'])>=4 and
               ((f['dir']=='Short' and f['stk']=='Bearish') or (f['dir']=='Long' and f['stk']=='Bullish'))),
    ('|DZ|>=5 + Short',
     lambda f: abs(f['dz'])>=5 and f['dir']=='Short'),
    ('|DZ|>=4 + Short + London',
     lambda f: abs(f['dz'])>=4 and f['dir']=='Short' and f['session']=='London'),
    ('|DZ|>=3 + VR 4-5x + STK alineado',
     lambda f: abs(f['dz'])>=3 and 4<=f['vr']<5 and
               ((f['dir']=='Short' and f['stk']=='Bearish') or (f['dir']=='Long' and f['stk']=='Bullish'))),
    ('|DZ|>=4 + VR 4-5x + STK alineado',
     lambda f: abs(f['dz'])>=4 and 4<=f['vr']<5 and
               ((f['dir']=='Short' and f['stk']=='Bearish') or (f['dir']=='Long' and f['stk']=='Bullish'))),
    ('|DZ|>=4 + VR 4-5x + OBI alineado',
     lambda f: abs(f['dz'])>=4 and 4<=f['vr']<5 and
               ((f['dir']=='Short' and f['obi']<-0.2) or (f['dir']=='Long' and f['obi']>0.2))),
    ('|DZ|>=3 + OBI alineado + STK alineado',
     lambda f: abs(f['dz'])>=3 and
               ((f['dir']=='Short' and f['obi']<-0.1 and f['stk']=='Bearish') or
                (f['dir']=='Long'  and f['obi']> 0.1 and f['stk']=='Bullish'))),
    ('|DZ|>=4 + OBI alineado + STK alineado',
     lambda f: abs(f['dz'])>=4 and
               ((f['dir']=='Short' and f['obi']<-0.1 and f['stk']=='Bearish') or
                (f['dir']=='Long'  and f['obi']> 0.1 and f['stk']=='Bullish'))),
    ('London + Short + |DZ|>=3',
     lambda f: f['session']=='London' and f['dir']=='Short' and abs(f['dz'])>=3),
    ('London/Asia + Short + |DZ|>=4 + STK Bearish',
     lambda f: f['session'] in ('London','Asia') and f['dir']=='Short'
               and abs(f['dz'])>=4 and f['stk']=='Bearish'),
    ('VR 4-5x + |DZ|>=4 + Short',
     lambda f: 4<=f['vr']<5 and abs(f['dz'])>=4 and f['dir']=='Short'),
    ('VR 4-5x + |DZ|>=4 + Short + OBI neg',
     lambda f: 4<=f['vr']<5 and abs(f['dz'])>=4 and f['dir']=='Short' and f['obi']<-0.1),
]

print(f'  {"Filtro":<45} {"n":>5} {"WR%":>6} {"AvgR":>8} {"n/dia":>7}  calidad')
print('-'*85)
for name, cond in combos:
    sub = [f for f in all_trades_with_feat if cond(f)]
    nn, wr, ar, _ = eval_filter(sub)
    spd = nn / 4.4
    quality = '*** TARGET' if wr >= 60 else ('** CERCA' if wr >= 55 else ('* OK' if wr >= 50 else ''))
    print(f'  {name:<45} {nn:>5} {wr:>5.0f}%  {ar:>+8.3f}  {spd:>5.1f}/dia  {quality}')

# ── EL SETUP DE ALTA CALIDAD FINAL ───────────────────────────────────────────
print()
print('='*75)
print('SETUP DE ALTA CALIDAD: todos los filtros juntos')
print('='*75)

hq = [f for f in all_trades_with_feat
      if abs(f['dz']) >= 4
      and 4 <= f['vr'] < 6
      and ((f['dir']=='Short' and f['stk']=='Bearish' and f['obi'] < -0.1) or
           (f['dir']=='Long'  and f['stk']=='Bullish' and f['obi'] >  0.1))]

nn, wr, ar, _ = eval_filter(hq)
print(f'  |DZ|>=4 + VR 4-6x + STK alineado + OBI alineado')
print(f'  n={nn}  WR={wr:.0f}%  AvgR={ar:+.3f}  ({nn/4.4:.1f}/dia)')
print()
print('  Por sesion:')
by_sess = defaultdict(list)
for f in hq: by_sess[f['session']].append(f)
for s in ['London','LondonNyOverlap','NewYork','Asia','OffHours']:
    lst = by_sess.get(s,[])
    if len(lst) < 2: continue
    ww = sum(x['won'] for x in lst)/len(lst)*100
    aa = sum(x['r'] for x in lst)/len(lst)
    print(f'    {s:<22}: n={len(lst):3d}  WR={ww:4.0f}%  AvgR={aa:>+6.3f}')
print()
print('  Por direccion:')
by_dir = defaultdict(list)
for f in hq: by_dir[f['dir']].append(f)
for d in ['Short','Long']:
    lst = by_dir.get(d,[])
    if not lst: continue
    ww = sum(x['won'] for x in lst)/len(lst)*100
    aa = sum(x['r'] for x in lst)/len(lst)
    print(f'    {d:<8}: n={len(lst):3d}  WR={ww:4.0f}%  AvgR={aa:>+6.3f}')
