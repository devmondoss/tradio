#!/usr/bin/env python3
"""
Analiza thin_below/thin_above en los trades del baseline.
thin_below = True  → LVN debajo del precio → precio cae rapido (BUENO para shorts)
thin_above = True  → LVN arriba del precio → precio puede subir al stop (MALO para shorts)
"""
import json, subprocess, sys, os, urllib.request, urllib.parse, time
from pathlib import Path
from collections import defaultdict, Counter

ROOT = Path(__file__).parent.parent.parent.parent
_env = {}
for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        _env[k.strip()] = v.strip().strip('"').strip("'")
SUPABASE_URL = _env.get('SUPABASE_URL', '')
SUPABASE_KEY = _env.get('SUPABASE_KEY', '')

TABLES = {'BTCUSDT': 'btc_bars', 'ETHUSDT': 'eth_bars', 'SOLUSDT': 'sol_bars'}
STARTS = {'BTCUSDT': 1780676700000, 'ETHUSDT': 1780756260000, 'SOLUSDT': 1780756260000}
DAYS   = 14
COLS   = 'ts_ms,thin_above,thin_below,swing_low_50,swing_high_50'

def sb_fetch(table, start_ms):
    rows, limit, offset = [], 1000, 0
    while True:
        qs = urllib.parse.urlencode({'select': COLS, 'ts_ms': f'gte.{start_ms}',
             'order': 'ts_ms.asc', 'limit': str(limit), 'offset': str(offset)})
        req = urllib.request.Request(f'{SUPABASE_URL}/rest/v1/{table}?{qs}',
              headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'})
        chunk = json.loads(urllib.request.urlopen(req, timeout=30).read())
        rows.extend(chunk)
        if len(chunk) < limit: break
        offset += limit
    return {b['ts_ms']: b for b in rows}

# Correr baseline y obtener trades
SCRIPT = Path(__file__).parent / 'shorts_htf_backtest.py'
print('Cargando trades del baseline...')
r = subprocess.run([sys.executable, str(SCRIPT), '--days', str(DAYS)],
                   capture_output=True, text=True)
data   = json.loads(r.stdout)
trades = data['trades']
print(f'n={len(trades)} trades')

# Cargar barras con thin data
print('Cargando thin data de Supabase...')
thin_data = {}
for sym, table in TABLES.items():
    start_ms = max(STARTS[sym], int((time.time() - DAYS * 86400) * 1000))
    thin_data[sym] = sb_fetch(table, start_ms)
    print(f'  {sym}: {len(thin_data[sym])} barras')

# Cruzar trades con thin data
enriched = []
for t in trades:
    sym   = t['sym']
    ts_ms = t['tsMs']
    bar   = thin_data.get(sym, {}).get(ts_ms, {})
    ta    = str(bar.get('thin_above') or '').lower() == 'true'
    tb    = str(bar.get('thin_below') or '').lower() == 'true'
    sl50  = bar.get('swing_low_50')
    enriched.append({**t, 'thin_above': ta, 'thin_below': tb, 'swing_low_50': sl50})

def stats(ts):
    if not ts: return {'n':0,'wr':0,'avgR':0}
    n    = len(ts)
    wins = sum(1 for t in ts if t['resultR'] > 0)
    avgr = sum(t['resultR'] for t in ts) / n
    return {'n': n, 'wr': round(wins/n*100,1), 'avgR': round(avgr, 3)}

print('\n' + '='*60)
print('  THIN_BELOW (LVN debajo = precio cae rapido = BUENO para shorts)')
print('='*60)
tb_true  = [t for t in enriched if t['thin_below']]
tb_false = [t for t in enriched if not t['thin_below']]
s1 = stats(tb_true);  s2 = stats(tb_false)
print(f'  thin_below=True  n={s1["n"]:>3}  WR={s1["wr"]:>5.1f}%  AvgR={s1["avgR"]:>+.3f}R')
print(f'  thin_below=False n={s2["n"]:>3}  WR={s2["wr"]:>5.1f}%  AvgR={s2["avgR"]:>+.3f}R')
edge = s1['wr'] - s2['wr']
print(f'  Edge thin_below: {edge:>+.1f}pp WR')

print('\n' + '='*60)
print('  THIN_ABOVE (LVN arriba = precio puede subir al stop = MALO)')
print('='*60)
ta_true  = [t for t in enriched if t['thin_above']]
ta_false = [t for t in enriched if not t['thin_above']]
s3 = stats(ta_true);  s4 = stats(ta_false)
print(f'  thin_above=True  n={s3["n"]:>3}  WR={s3["wr"]:>5.1f}%  AvgR={s3["avgR"]:>+.3f}R')
print(f'  thin_above=False n={s4["n"]:>3}  WR={s4["wr"]:>5.1f}%  AvgR={s4["avgR"]:>+.3f}R')
edge2 = s3['wr'] - s4['wr']
print(f'  Edge thin_above: {edge2:>+.1f}pp WR  (esperamos negativo)')

print('\n' + '='*60)
print('  COMBINACIONES (thin_below=T + thin_above=F = setup ideal)')
print('='*60)
combos = {
    'tb=T ta=F (ideal)':  [t for t in enriched if     t['thin_below'] and not t['thin_above']],
    'tb=T ta=T':          [t for t in enriched if     t['thin_below'] and     t['thin_above']],
    'tb=F ta=F':          [t for t in enriched if not t['thin_below'] and not t['thin_above']],
    'tb=F ta=T (peor)':   [t for t in enriched if not t['thin_below'] and     t['thin_above']],
}
for label, ts in combos.items():
    s = stats(ts)
    print(f'  {label:<22} n={s["n"]:>3}  WR={s["wr"]:>5.1f}%  AvgR={s["avgR"]:>+.3f}R')

print('\n' + '='*60)
print('  SWING_LOW_50 como target proxy (vs 2.5R fijo)')
print('='*60)
sl_trades = [t for t in enriched if t.get('swing_low_50')]
if sl_trades:
    dist_r = []
    for t in sl_trades:
        try:
            sl   = float(t['swing_low_50'])
            risk = t['stop'] - t['entry']
            if risk > 0:
                r_to_sl = (t['entry'] - sl) / risk
                dist_r.append(r_to_sl)
        except: pass
    if dist_r:
        dist_r.sort()
        n = len(dist_r)
        print(f'  n trades con swing_low_50: {n}')
        print(f'  Distancia swing_low_50 en R:')
        print(f'    min={dist_r[0]:.2f}R  p25={dist_r[n//4]:.2f}R  median={dist_r[n//2]:.2f}R  p75={dist_r[3*n//4]:.2f}R  max={dist_r[-1]:.2f}R')
        below_25 = sum(1 for d in dist_r if d < 2.5)
        print(f'    Trades donde swing_low_50 < 2.5R: {below_25}/{n} ({below_25/n*100:.0f}%) -> target mas cercano')
        above_25 = sum(1 for d in dist_r if d > 2.5)
        print(f'    Trades donde swing_low_50 > 2.5R: {above_25}/{n} ({above_25/n*100:.0f}%) -> target mas lejano')
print()
