#!/usr/bin/env python3
"""
Análisis profundo de ETH para encontrar su mejor calibración.
Usa TODOS los trades live de rbf_signals + eth_bars.
"""
import json, os, urllib.request, urllib.parse
from pathlib import Path

ROOT = Path(__file__).parent.parent
_env = {}
for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        _env[k.strip()] = v.strip().strip('"').strip("'")

URL = _env.get('SUPABASE_URL', '')
KEY = _env.get('SUPABASE_KEY', '')

def sb(table, params):
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        f'{URL}/rest/v1/{table}?{qs}',
        headers={'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
    )
    return json.loads(urllib.request.urlopen(req, timeout=30).read())

# ── Todos los trades ETH cerrados ─────────────────────────────────────────────
trades = sb('rbf_signals', {
    'select': '*',
    'symbol': 'eq.ETHUSDT',
    'result_r': 'not.is.null',
    'order': 'timestamp_ms.asc',
    'limit': '500',
})

print(f'ETH trades cerrados: {len(trades)}')
if not trades:
    print('Sin datos')
    exit()

wins   = [t for t in trades if t['result_r'] >= 1.0]
losses = [t for t in trades if t['result_r'] < 0]
be     = [t for t in trades if 0 <= t['result_r'] < 1.0]
print(f'  Wins (≥1R): {len(wins)}  Losses (<0): {len(losses)}  BE: {len(be)}')
print(f'  WR: {len(wins)}/{len(trades)} = {100*len(wins)/len(trades):.0f}%')
print(f'  avgR: {sum(t["result_r"] for t in trades)/len(trades):+.3f}R')
print(f'  PnL total: {sum(t["result_r"] for t in trades):+.2f}R')

def avg(lst, key):
    vals = [t.get(key) for t in lst if t.get(key) is not None]
    return sum(vals)/len(vals) if vals else None

def fmt(v): return f'{v:.3f}' if v is not None else 'N/A'

print()
print('═'*60)
print('ANÁLISIS WINS vs LOSSES')
print('═'*60)

fields = [
    ('vr_at_breakout',      'VR en breakout'),
    ('range_pct',           'Tamaño rango %'),
    ('range_bars',          'Duración rango (barras)'),
    ('cvd_in_range',        'CVD en rango'),
    ('obi_at_entry',        'OBI al entrar'),
    ('confluence_score',    'Confluence score'),
    ('price_vs_vwap_pct',   'Precio vs VWAP %'),
    ('oi_delta_pct',        'OI delta %'),
    ('cvd_divergence_bars', 'CVD divergence bars'),
    ('bar_displacement',    'Bar displacement'),
    ('absorption_score',    'Absorption score'),
    ('signal_score_v2',     'Signal score v2'),
    ('bars_held',           'Barras en trade'),
]

print(f'{"Campo":<25} {"WINS":>10} {"LOSSES":>10} {"DELTA":>10}')
print('-'*60)
for key, label in fields:
    w = avg(wins,   key)
    l = avg(losses, key)
    d = (w - l) if (w is not None and l is not None) else None
    marker = ' ◄' if (d is not None and abs(d) > 0.5) else ''
    print(f'{label:<25} {fmt(w):>10} {fmt(l):>10} {fmt(d):>10}{marker}')

print()
print('═'*60)
print('POR SESIÓN')
print('═'*60)
for ses in ['London', 'LondonNyOverlap', 'NewYork']:
    st = [t for t in trades if t.get('session') == ses]
    if not st: continue
    w = sum(1 for t in st if t['result_r'] >= 1.0)
    a = sum(t['result_r'] for t in st) / len(st)
    p = sum(t['result_r'] for t in st)
    print(f'  {ses:<22}  n={len(st):<3}  WR={w}/{len(st)} ({100*w//len(st) if st else 0}%)  avgR={a:+.3f}  PnL={p:+.2f}R')

print()
print('═'*60)
print('POR REGIME MACRO')
print('═'*60)
regimes = set(t.get('macro_regime') for t in trades)
for reg in sorted(r for r in regimes if r):
    st = [t for t in trades if t.get('macro_regime') == reg]
    w  = sum(1 for t in st if t['result_r'] >= 1.0)
    a  = sum(t['result_r'] for t in st)/len(st)
    print(f'  {reg:<20}  n={len(st):<3}  WR={w}/{len(st)} ({100*w//len(st) if st else 0}%)  avgR={a:+.3f}R')

print()
print('═'*60)
print('CONFLUENCE FLAGS (wins vs losses)')
print('═'*60)
from collections import Counter
win_flags  = Counter(f for t in wins   for f in (t.get('confluence_flags') or []))
loss_flags = Counter(f for t in losses for f in (t.get('confluence_flags') or []))
all_flags  = set(win_flags) | set(loss_flags)
print(f'{"Flag":<30} {"en WINS":>8} {"en LOSSES":>10} {"ratio W/L":>10}')
print('-'*60)
for flag in sorted(all_flags):
    w_pct = win_flags[flag]  / max(len(wins),1)  * 100
    l_pct = loss_flags[flag] / max(len(losses),1) * 100
    ratio = w_pct / l_pct if l_pct > 0 else 999
    marker = ' ◄◄' if ratio > 1.5 or ratio < 0.5 else ''
    print(f'{flag:<30} {w_pct:>7.0f}%  {l_pct:>9.0f}%  {ratio:>9.2f}x{marker}')

print()
print('═'*60)
print('VR TIERS')
print('═'*60)
for tier, label in [(1,'1.5-2x'),(2,'2-4x'),(3,'4x+')]:
    st = [t for t in trades if t.get('vr_tier') == tier]
    if not st: continue
    w = sum(1 for t in st if t['result_r'] >= 1.0)
    a = sum(t['result_r'] for t in st)/len(st)
    print(f'  VR tier {tier} ({label})  n={len(st):<3}  WR={w}/{len(st)} ({100*w//len(st) if st else 0}%)  avgR={a:+.3f}R')

print()
print('═'*60)
print('OBI BUCKETS (discriminador clave para ETH)')
print('═'*60)
buckets = [
    ('OBI muy negativo (<-0.1)', lambda t: (t.get('obi_at_entry') or 0) < -0.1),
    ('OBI negativo (-0.1 a -0.03)', lambda t: -0.1 <= (t.get('obi_at_entry') or 0) < -0.03),
    ('OBI neutro (-0.03 a +0.03)', lambda t: -0.03 <= (t.get('obi_at_entry') or 0) <= 0.03),
    ('OBI positivo (>+0.03)',      lambda t: (t.get('obi_at_entry') or 0) > 0.03),
]
for label, fn in buckets:
    st = [t for t in trades if t.get('obi_at_entry') is not None and fn(t)]
    if not st: continue
    w = sum(1 for t in st if t['result_r'] >= 1.0)
    a = sum(t['result_r'] for t in st)/len(st)
    print(f'  {label:<35}  n={len(st):<3}  WR={w}/{len(st)} ({100*w//len(st) if st else 0}%)  avgR={a:+.3f}R')

print()
print('═'*60)
print('RANGE PCT BUCKETS')
print('═'*60)
for lo, hi, label in [(0,0.15,'micro (<0.15%)'),(0.15,0.25,'pequeño (0.15-0.25%)'),(0.25,0.4,'normal (0.25-0.4%)'),(0.4,1,'grande (>0.4%)')]:
    st = [t for t in trades if t.get('range_pct') is not None and lo <= t['range_pct']*100 < hi]
    if not st: continue
    w = sum(1 for t in st if t['result_r'] >= 1.0)
    a = sum(t['result_r'] for t in st)/len(st)
    print(f'  {label:<30}  n={len(st):<3}  WR={w}/{len(st)} ({100*w//len(st) if st else 0}%)  avgR={a:+.3f}R')

print()
print('═'*60)
print('IS_PRE_BREAKOUT')
print('═'*60)
for is_pre in [False, True]:
    st = [t for t in trades if bool(t.get('is_pre_breakout')) == is_pre]
    if not st: continue
    w = sum(1 for t in st if t['result_r'] >= 1.0)
    a = sum(t['result_r'] for t in st)/len(st)
    mode = 'Pre-breakout' if is_pre else 'Post-breakout'
    print(f'  {mode:<20}  n={len(st):<3}  WR={w}/{len(st)} ({100*w//len(st) if st else 0}%)  avgR={a:+.3f}R')

print()
print('═'*60)
print('COMBINACIONES GANADORAS (subsets con WR > 40%)')
print('═'*60)
# Probar combinaciones de filtros
filters = [
    ('session=London',     lambda t: t.get('session') == 'London'),
    ('session=Overlap',    lambda t: t.get('session') == 'LondonNyOverlap'),
    ('session=NY',         lambda t: t.get('session') == 'NewYork'),
    ('obi<-0.03',          lambda t: (t.get('obi_at_entry') or 0) < -0.03),
    ('obi<-0.05',          lambda t: (t.get('obi_at_entry') or 0) < -0.05),
    ('obi<0',              lambda t: (t.get('obi_at_entry') or 0) < 0),
    ('vr>=3',              lambda t: (t.get('vr_at_breakout') or 0) >= 3.0),
    ('vr>=4',              lambda t: (t.get('vr_at_breakout') or 0) >= 4.0),
    ('score>=2',           lambda t: (t.get('confluence_score') or 0) >= 2),
    ('score>=3',           lambda t: (t.get('confluence_score') or 0) >= 3),
    ('range<0.25%',        lambda t: (t.get('range_pct') or 0) < 0.0025),
    ('range>0.2%',         lambda t: (t.get('range_pct') or 0) > 0.002),
    ('post-breakout',      lambda t: not t.get('is_pre_breakout')),
    ('pre-breakout',       lambda t: bool(t.get('is_pre_breakout'))),
]

# Probar todos los filtros solos y pares
results = []
for name1, fn1 in filters:
    st = [t for t in trades if fn1(t)]
    if len(st) < 3: continue
    w = sum(1 for t in st if t['result_r'] >= 1.0)
    a = sum(t['result_r'] for t in st)/len(st)
    if w/len(st) >= 0.35 or a > 0.2:
        results.append((name1, len(st), w, a))

for name1, fn1 in filters:
    for name2, fn2 in filters:
        if name1 >= name2: continue
        st = [t for t in trades if fn1(t) and fn2(t)]
        if len(st) < 3: continue
        w = sum(1 for t in st if t['result_r'] >= 1.0)
        a = sum(t['result_r'] for t in st)/len(st)
        if w/len(st) >= 0.40 or a > 0.3:
            results.append((f'{name1} + {name2}', len(st), w, a))

results.sort(key=lambda x: -x[3])
print(f'{"Filtro":<45} {"n":>4} {"WR":>8} {"avgR":>8}')
print('-'*70)
for name, n, w, a in results[:20]:
    print(f'{name:<45} {n:>4} {w}/{n} ({100*w//n}%)  {a:+.3f}R')
