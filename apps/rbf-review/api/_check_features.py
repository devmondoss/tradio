#!/usr/bin/env python3
"""Verifica qué % de los trades del baseline tienen cada feature poblado."""
import json, subprocess, sys, os
from pathlib import Path
from collections import Counter, defaultdict

ROOT = Path(__file__).parent.parent.parent.parent
_env = {}
for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        _env[k.strip()] = v.strip().strip('"').strip("'")

import urllib.request, urllib.parse, time

SUPABASE_URL = _env.get('SUPABASE_URL', '')
SUPABASE_KEY = _env.get('SUPABASE_KEY', '')

TABLES = {'BTCUSDT': 'btc_bars', 'ETHUSDT': 'eth_bars', 'SOLUSDT': 'sol_bars'}
STARTS = {'BTCUSDT': 1780676700000, 'ETHUSDT': 1780756260000, 'SOLUSDT': 1780756260000}
DAYS = 14

EXTRA_COLS = ('ts_ms,dz,stacked_imb,thin_above,bar_delta,obi_l5,obi_fast,'
              'session,cvd_slope,absorption,regime,equal_high,vr,oi_momentum,vpin')

def sb_fetch(table, start_ms):
    rows, limit, offset = [], 1000, 0
    while True:
        qs = urllib.parse.urlencode({'select': EXTRA_COLS, 'ts_ms': f'gte.{start_ms}',
             'order': 'ts_ms.asc', 'limit': str(limit), 'offset': str(offset)})
        req = urllib.request.Request(f'{SUPABASE_URL}/rest/v1/{table}?{qs}',
              headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'})
        chunk = json.loads(urllib.request.urlopen(req, timeout=30).read())
        rows.extend(chunk)
        if len(chunk) < limit: break
        offset += limit
    return rows

features = ['dz', 'stacked_imb', 'thin_above', 'bar_delta', 'obi_l5', 'obi_fast', 'cvd_slope']
totals = {f: {'populated': 0, 'null': 0, 'values': Counter()} for f in features}
total_bars = 0

for sym, table in TABLES.items():
    start_ms = max(STARTS[sym], int((time.time() - DAYS * 86400) * 1000))
    print(f'Fetching {sym}...', end=' ', flush=True)
    bars = sb_fetch(table, start_ms)
    print(f'{len(bars)} barras')
    total_bars += len(bars)

    for b in bars:
        for f in features:
            v = b.get(f)
            if v is None or v == '' or v == 'null':
                totals[f]['null'] += 1
            else:
                totals[f]['populated'] += 1
                if f in ('stacked_imb', 'thin_above', 'absorption', 'regime'):
                    totals[f]['values'][str(v)] += 1
                elif f == 'bar_delta':
                    try:
                        fv = float(v)
                        bucket = 'neg' if fv < -50 else 'pos' if fv > 50 else 'near0'
                        totals[f]['values'][bucket] += 1
                    except: pass
                elif f in ('dz', 'obi_l5', 'obi_fast', 'cvd_slope'):
                    try:
                        fv = float(v)
                        bucket = 'neg' if fv < 0 else 'pos'
                        totals[f]['values'][bucket] += 1
                    except: pass

print(f'\nTotal barras analizadas: {total_bars}\n')
print(f'{"Feature":<14} {"Poblado%":>9}  {"Poblado":>7}  {"Null":>7}  Valores frecuentes')
print('-' * 75)
for f in features:
    t = totals[f]
    pop = t['populated']
    null = t['null']
    total = pop + null
    pct = pop / total * 100 if total else 0
    vals = dict(t['values'].most_common(4))
    print(f'{f:<14} {pct:>8.1f}%  {pop:>7}  {null:>7}  {vals}')
