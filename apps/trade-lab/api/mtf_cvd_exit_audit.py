#!/usr/bin/env python3
"""
CVD_EXHAUSTION: salida optima o prematura?
Para cada trade que salio por CVD_EXHAUSTION, mira que hizo el precio
en los siguientes 30 y 60 barras M1. Cuanto R dejamos en la mesa?
"""
import json, subprocess, sys, urllib.request, urllib.parse, time
from pathlib import Path
from collections import defaultdict

ROOT   = Path(__file__).parent.parent.parent.parent
_env   = {}
for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1); _env[k.strip()] = v.strip().strip('"').strip("'")
SUPABASE_URL = _env['SUPABASE_URL']
SUPABASE_KEY = _env['SUPABASE_KEY']

SCRIPT = Path(__file__).parent / 'mtf_shorts_backtest.py'
TABLES = {'BTCUSDT':'btc_bars','ETHUSDT':'eth_bars','SOLUSDT':'sol_bars'}
STARTS = {'BTCUSDT':1780676700000,'ETHUSDT':1780756260000,'SOLUSDT':1780756260000}
DAYS   = 14
LOOK_FORWARD = 60  # barras M1 a mirar despues del exit

def sb_fetch(table, start_ms, cols='ts_ms,open,high,low,close'):
    rows, limit, offset = [], 1000, 0
    while True:
        qs = urllib.parse.urlencode({'select':cols,'ts_ms':f'gte.{start_ms}',
             'order':'ts_ms.asc','limit':str(limit),'offset':str(offset)})
        req = urllib.request.Request(f'{SUPABASE_URL}/rest/v1/{table}?{qs}',
              headers={'apikey':SUPABASE_KEY,'Authorization':f'Bearer {SUPABASE_KEY}'})
        chunk = json.loads(urllib.request.urlopen(req, timeout=30).read())
        rows.extend(chunk)
        if len(chunk) < limit: break
        offset += limit
    return rows

def sep(t): print(f'\n{"="*68}\n  {t}\n{"="*68}')

# ── Cargar trades ─────────────────────────────────────────────────────────────
print('Corriendo baseline...', end=' ', flush=True)
r = subprocess.run([sys.executable, str(SCRIPT), '--days', str(DAYS)],
                   capture_output=True, text=True)
data   = json.loads(r.stdout)
trades = data['trades']
print(f'n={len(trades)} WR={data["wr_pct"]}% AvgR={data["avg_r"]}R')

cvd_trades = [t for t in trades if t['reason'] == 'CVD_EXHAUSTION']
print(f'CVD_EXHAUSTION trades: {len(cvd_trades)}')

# ── Cargar barras completas por simbolo (para el look-forward) ────────────────
print('Cargando barras...', end=' ', flush=True)
all_bars = {}
for sym, table in TABLES.items():
    start_ms = max(STARTS[sym], int((time.time() - DAYS*86400)*1000))
    bars = sb_fetch(table, start_ms)
    # indexar por ts_ms para lookup rapido
    all_bars[sym] = {b['ts_ms']: i for i, b in enumerate(bars)}
    all_bars[sym + '_list'] = bars
print('OK')

# ── Analizar cada CVD_EXHAUSTION ──────────────────────────────────────────────
results = []
for t in cvd_trades:
    sym      = t['sym']
    entry    = t['entry']
    stop     = t['stop']
    risk     = stop - entry  # positivo para short
    exit_px  = t['exit']
    captured = t['resultR']
    dur_min  = t.get('durationMin', 0)

    # exit timestamp: entry + duration
    exit_ms  = t['tsMs'] + dur_min * 60_000
    bar_list = all_bars.get(sym + '_list', [])
    bar_idx  = all_bars.get(sym, {})

    # encontrar indice del bar de exit (o el siguiente)
    idx = bar_idx.get(exit_ms)
    if idx is None:
        # buscar el bar mas cercano despues del exit
        for ms, i in sorted(bar_idx.items()):
            if ms >= exit_ms:
                idx = i; break

    if idx is None or idx + 1 >= len(bar_list):
        results.append({**t, 'low_30':None,'low_60':None,'extra_r_30':None,'extra_r_60':None,'continued':None})
        continue

    # barras post-exit
    post = bar_list[idx+1 : idx+1+LOOK_FORWARD]
    if not post:
        results.append({**t, 'low_30':None,'low_60':None,'extra_r_30':None,'extra_r_60':None,'continued':None})
        continue

    low_30 = min(b['low'] for b in post[:30]) if len(post) >= 30 else min(b['low'] for b in post)
    low_60 = min(b['low'] for b in post)

    # R adicional que hubieramos capturado si hubieramos aguantado
    # para un short: ganamos mas si precio baja mas (exit_px - low) / risk
    extra_r_30 = (exit_px - low_30) / risk if risk > 0 else 0
    extra_r_60 = (exit_px - low_60) / risk if risk > 0 else 0

    # precio continuo bajando mas de 0.5R despues de nuestra salida?
    continued = extra_r_30 > 0.5

    results.append({
        **t,
        'low_30'     : low_30,
        'low_60'     : low_60,
        'extra_r_30' : round(extra_r_30, 3),
        'extra_r_60' : round(extra_r_60, 3),
        'continued'  : continued,
        'total_available': round(captured + extra_r_30, 3),
    })

valid = [r for r in results if r['extra_r_30'] is not None]

sep(f'CVD_EXHAUSTION AUDIT — {len(cvd_trades)} trades')
continued    = [r for r in valid if r['continued']]
not_continued= [r for r in valid if not r['continued']]
avg_captured = sum(r['resultR'] for r in valid) / len(valid)
avg_extra_30 = sum(r['extra_r_30'] for r in valid) / len(valid)
avg_extra_60 = sum(r['extra_r_60'] for r in valid) / len(valid)

print(f'  Salimos bien (precio no continuó >0.5R) : {len(not_continued)} trades ({len(not_continued)/len(valid)*100:.0f}%)')
print(f'  Salimos pronto (precio continuó >0.5R)  : {len(continued)} trades ({len(continued)/len(valid)*100:.0f}%)')
print()
print(f'  AvgR capturado                           : {avg_captured:>+.3f}R')
print(f'  AvgR adicional en 30 barras post-exit    : {avg_extra_30:>+.3f}R')
print(f'  AvgR adicional en 60 barras post-exit    : {avg_extra_60:>+.3f}R')
print(f'  R total disponible (si aguantabamos 30b) : {avg_captured+avg_extra_30:>+.3f}R')

sep('DISTRIBUCION: R extra dejado en la mesa (30 barras)')
buckets = [
    ('precio reboto  (<0R extra)',   lambda x: x < 0),
    ('precio lateral (0-0.3R)',      lambda x: 0 <= x < 0.3),
    ('precio bajó leve (0.3-0.5R)',  lambda x: 0.3 <= x < 0.5),
    ('precio bajó mod (0.5-1.0R)',   lambda x: 0.5 <= x < 1.0),
    ('precio bajó fuerte (>1.0R)',   lambda x: x >= 1.0),
]
print(f'  {"Escenario":<32} {"n":>4} {"AvgR capturado":>15} {"AvgR extra":>11}')
print('  ' + '-'*65)
for label, cond in buckets:
    ts = [r for r in valid if cond(r['extra_r_30'])]
    if not ts: continue
    ac = sum(r['resultR'] for r in ts) / len(ts)
    ae = sum(r['extra_r_30'] for r in ts) / len(ts)
    print(f'  {label:<32} {len(ts):>4} {ac:>+14.3f}R {ae:>+10.3f}R')

sep('TRADES DONDE DEJAMOS MAS R EN LA MESA')
print(f'  {"#":>3} {"sym":>5} {"session":<20} {"captured":>9} {"extra_30":>9} {"extra_60":>9} {"total_avail":>12}  veredicto')
print('  ' + '-'*95)
for r in sorted(valid, key=lambda x: -x['extra_r_30'])[:20]:
    verdict = 'SALIO PRONTO' if r['extra_r_30'] > 0.5 else ('OK reboto' if r['extra_r_30'] < 0 else 'OK lateral')
    print(f'  {r["idx"]:>3} {r["sym"].replace("USDT",""):>5} {r.get("session","?"):<20} {r["resultR"]:>+8.3f}R {r["extra_r_30"]:>+8.3f}R {r["extra_r_60"]:>+8.3f}R {r["total_available"]:>+11.3f}R  {verdict}')

sep('TRADES DONDE SALIMOS BIEN (precio reboto despues)')
print(f'  {"#":>3} {"sym":>5} {"session":<20} {"captured":>9} {"extra_30":>9}  veredicto')
print('  ' + '-'*70)
for r in sorted([x for x in valid if x['extra_r_30'] < 0.1], key=lambda x: x['extra_r_30'])[:15]:
    verdict = 'PERFECTO reboto' if r['extra_r_30'] < 0 else 'lateral'
    print(f'  {r["idx"]:>3} {r["sym"].replace("USDT",""):>5} {r.get("session","?"):<20} {r["resultR"]:>+8.3f}R {r["extra_r_30"]:>+8.3f}R  {verdict}')

sep('RESUMEN: VALE LA PENA AGUANTAR MAS?')
pct_continued = len(continued) / len(valid) * 100
pct_fine      = len(not_continued) / len(valid) * 100
print(f'  En el {pct_continued:.0f}% de CVD exits el precio CONTINUO bajando >0.5R')
print(f'  En el {pct_fine:.0f}% de CVD exits el precio reboto o fue lateral (salida correcta)')
print()
if pct_continued > 50:
    print('  CONCLUSION: CVD_EXHAUSTION sale DEMASIADO PRONTO — vale la pena calibrar condicion de salida')
elif pct_continued > 30:
    print('  CONCLUSION: CVD_EXHAUSTION es suboptimo en ~1/3 casos — hay margen de mejora')
else:
    print('  CONCLUSION: CVD_EXHAUSTION es una buena salida — el precio suele rebotar despues')

print()
