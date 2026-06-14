#!/usr/bin/env python3
"""
ADN DEL TRADE GANADOR — MTF Shorts v2
Que tienen en comun los winners grandes (2R+) vs winners chicos vs losers.
"""
import json, subprocess, sys, urllib.request, urllib.parse, time
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone

ROOT   = Path(__file__).parent.parent.parent.parent
_env   = {}
for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1); _env[k.strip()] = v.strip().strip('"').strip("'")
SUPABASE_URL = _env['SUPABASE_URL']
SUPABASE_KEY = _env['SUPABASE_KEY']

SCRIPT = Path(__file__).parent / 'shorts_mtf_backtest.py'
TABLES = {'BTCUSDT':'btc_bars','ETHUSDT':'eth_bars','SOLUSDT':'sol_bars'}
STARTS = {'BTCUSDT':1780676700000,'ETHUSDT':1780756260000,'SOLUSDT':1780756260000}
DAYS   = 14

ENRICH_COLS = ('ts_ms,thin_above,thin_below,dz,obi_l5,obi_fast,absorption,'
               'stacked_imb,cvd_divergence,bar_delta,vr,oi_momentum,regime,vwap,close')

def sb_fetch(table, start_ms):
    rows, limit, offset = [], 1000, 0
    while True:
        qs = urllib.parse.urlencode({'select':ENRICH_COLS,'ts_ms':f'gte.{start_ms}',
             'order':'ts_ms.asc','limit':str(limit),'offset':str(offset)})
        req = urllib.request.Request(f'{SUPABASE_URL}/rest/v1/{table}?{qs}',
              headers={'apikey':SUPABASE_KEY,'Authorization':f'Bearer {SUPABASE_KEY}'})
        chunk = json.loads(urllib.request.urlopen(req, timeout=30).read())
        rows.extend(chunk)
        if len(chunk) < limit: break
        offset += limit
    return {b['ts_ms']: b for b in rows}

def sep(t): print(f'\n{"="*66}\n  {t}\n{"="*66}')

def stats(trades):
    if not trades: return {'n':0,'wr':0,'avgR':0,'totalR':0}
    n = len(trades); wins = [t for t in trades if t['resultR'] > 0]
    tr = sum(t['resultR'] for t in trades)
    return {'n':n,'wr':round(len(wins)/n*100,1),'avgR':round(tr/n,3),'totalR':round(tr,2)}

def rate(sub, total):
    return f'{len(sub)/len(total)*100:.0f}%' if total else 'n/a'

# ── Cargar trades ─────────────────────────────────────────────────────────────
print('Corriendo baseline...', end=' ', flush=True)
r = subprocess.run([sys.executable, str(SCRIPT), '--days', str(DAYS)],
                   capture_output=True, text=True)
if r.returncode != 0:
    print('ERROR:', r.stderr[:400]); sys.exit(1)
data   = json.loads(r.stdout)
trades = data['trades']
print(f'n={len(trades)} WR={data["wr_pct"]}% AvgR={data["avg_r"]}R Equity=${data["equity"]}')

# ── Enriquecer ────────────────────────────────────────────────────────────────
print('Cargando bar data...', end=' ', flush=True)
bar_data = {}
for sym, table in TABLES.items():
    start_ms = max(STARTS[sym], int((time.time() - DAYS*86400)*1000))
    bar_data[sym] = sb_fetch(table, start_ms)
print('OK')

enriched = []
for t in trades:
    bar = bar_data.get(t['sym'], {}).get(t['tsMs'], {})
    dt  = datetime.fromtimestamp(t['tsMs']/1000, tz=timezone.utc)
    # distancia vwap (precio por encima del vwap = buen short)
    vwap  = float(bar.get('vwap') or 0)
    close = float(bar.get('close') or t['entry'])
    vwap_dev = (close - vwap) / close * 100 if vwap else None
    e = {
        **t,
        'hour_utc'   : dt.hour,
        'thin_above' : str(bar.get('thin_above','')).lower() == 'true',
        'thin_below' : str(bar.get('thin_below','')).lower() == 'true',
        'dz'         : float(bar.get('dz') or 0),
        'obi_l5'     : float(bar.get('obi_l5') or 0),
        'obi_fast'   : float(bar.get('obi_fast') or 0),
        'absorption' : str(bar.get('absorption','')),
        'cvd_div'    : str(bar.get('cvd_divergence','')),
        'bar_delta'  : float(bar.get('bar_delta') or 0),
        'vr'         : float(bar.get('vr') or 0),
        'oi_momentum': bar.get('oi_momentum'),
        'regime'     : str(bar.get('regime','')),
        'vwap_dev'   : vwap_dev,
    }
    enriched.append(e)

# Segmentos
big_win  = [t for t in enriched if t['resultR'] >= 2.0]
med_win  = [t for t in enriched if 0.5 <= t['resultR'] < 2.0]
small_win= [t for t in enriched if 0.0 < t['resultR'] < 0.5]
losers   = [t for t in enriched if t['resultR'] <= 0]
winners  = [t for t in enriched if t['resultR'] > 0]

sep('SEGMENTOS DE RESULTADO')
print(f'  {"Segmento":<22} {"n":>4} {"AvgR":>8} {"TotalR":>9}  % del total')
print('  ' + '-'*55)
segs = [('Big winners (>=2R)', big_win), ('Med winners (0.5-2R)', med_win),
        ('Small winners (<0.5R)', small_win), ('Losers (<=0)', losers)]
for label, ts in segs:
    if not ts: continue
    s = stats(ts)
    print(f'  {label:<22} {s["n"]:>4} {s["avgR"]:>+7.3f}R {s["totalR"]:>+9.2f}R  {rate(ts,enriched)}')

sep('DONDE ESTA EL DINERO')
print(f'  Big winners  ({len(big_win)} trades): {sum(t["resultR"] for t in big_win):>+.2f}R total')
print(f'  Med winners  ({len(med_win)} trades): {sum(t["resultR"] for t in med_win):>+.2f}R total')
print(f'  Small winners({len(small_win)} trades): {sum(t["resultR"] for t in small_win):>+.2f}R total')
print(f'  Losers       ({len(losers)} trades): {sum(t["resultR"] for t in losers):>+.2f}R total')

# ── COMPARACION DE FEATURES: big winners vs losers ────────────────────────────
sep('FEATURE RATES: Big Winners vs Losers')
print(f'  {"Feature":<28} {"BigWin%":>8} {"Loser%":>8}  Edge')
print('  ' + '-'*52)

def feat_rate(group, key, condition):
    if not group: return 0.0
    return sum(1 for t in group if condition(t.get(key))) / len(group) * 100

feats = [
    ('thin_above=T',   'thin_above',   lambda v: v is True or str(v).lower()=='true'),
    ('thin_below=T',   'thin_below',   lambda v: v is True or str(v).lower()=='true'),
    ('oi_momentum=T',  'oi_momentum',  lambda v: v is True or str(v)=='True'),
    ('score>=1',       'score',        lambda v: (v or 0) >= 1),
    ('score>=2',       'score',        lambda v: (v or 0) >= 2),
    ('dz<0',           'dz',           lambda v: float(v or 0) < 0),
    ('dz<-0.5',        'dz',           lambda v: float(v or 0) < -0.5),
    ('obi_l5<0',       'obi_l5',       lambda v: float(v or 0) < 0),
    ('obi_l5<-0.15',   'obi_l5',       lambda v: float(v or 0) < -0.15),
    ('obi_fast>0.15',  'obi_fast',     lambda v: float(v or 0) > 0.15),
    ('vwap_dev>0',     'vwap_dev',     lambda v: v is not None and float(v) > 0),
    ('vwap_dev>0.1%',  'vwap_dev',     lambda v: v is not None and float(v) > 0.1),
    ('regime=TrendDown','regime',      lambda v: 'Down' in str(v)),
    ('absorption=Bear','absorption',   lambda v: 'Bear' in str(v) or 'Sell' in str(v)),
    ('cvd_div=Bear',   'cvd_div',      lambda v: 'Bear' in str(v)),
]

for label, key, cond in feats:
    bw = feat_rate(big_win, key, cond)
    lo = feat_rate(losers,  key, cond)
    edge = bw - lo
    marker = ' <--' if abs(edge) >= 15 else ''
    print(f'  {label:<28} {bw:>7.0f}% {lo:>7.0f}%  {edge:>+5.1f}pp{marker}')

# ── SCORE por segmento ────────────────────────────────────────────────────────
sep('SCORE POR SEGMENTO')
print(f'  {"Score":<8} {"n":>4} {"AvgR":>8}  BigWin  MedWin  Small  Loser')
print('  ' + '-'*58)
by_score = defaultdict(list)
for t in enriched: by_score[t.get('score',0)].append(t)
for sc in sorted(by_score.keys()):
    ts   = by_score[sc]
    s    = stats(ts)
    bw   = sum(1 for t in ts if t['resultR'] >= 2.0)
    mw   = sum(1 for t in ts if 0.5 <= t['resultR'] < 2.0)
    sw   = sum(1 for t in ts if 0.0 < t['resultR'] < 0.5)
    lo   = sum(1 for t in ts if t['resultR'] <= 0)
    bar  = '#'*bw + '+'*mw + '.'*sw + 'x'*lo
    print(f'  {sc:<8} {s["n"]:>4} {s["avgR"]:>+7.3f}R  {bw:>5}   {mw:>5}  {sw:>5}  {lo:>5}  {bar}')

# ── SESSION por segmento ──────────────────────────────────────────────────────
sep('SESSION POR SEGMENTO')
print(f'  {"Session":<20} {"n":>4} {"WR%":>6} {"AvgR":>8}  BigWin  Loser')
print('  ' + '-'*58)
by_ses = defaultdict(list)
for t in enriched: by_ses[t.get('session','?')].append(t)
for ses, ts in sorted(by_ses.items(), key=lambda x: -len(x[1])):
    s  = stats(ts)
    bw = sum(1 for t in ts if t['resultR'] >= 2.0)
    lo = sum(1 for t in ts if t['resultR'] <= 0)
    print(f'  {ses:<20} {s["n"]:>4} {s["wr"]:>5.1f}%  {s["avgR"]:>+7.3f}R  {bw:>5}   {lo:>5}')

# ── REGIME por segmento ───────────────────────────────────────────────────────
sep('REGIME DE MERCADO')
print(f'  {"Regime":<16} {"n":>4} {"WR%":>6} {"AvgR":>8}  BigWin  Loser')
print('  ' + '-'*52)
by_reg = defaultdict(list)
for t in enriched: by_reg[t.get('regime','?')].append(t)
for reg, ts in sorted(by_reg.items(), key=lambda x: -len(x[1])):
    s  = stats(ts)
    bw = sum(1 for t in ts if t['resultR'] >= 2.0)
    lo = sum(1 for t in ts if t['resultR'] <= 0)
    print(f'  {reg:<16} {s["n"]:>4} {s["wr"]:>5.1f}%  {s["avgR"]:>+7.3f}R  {bw:>5}   {lo:>5}')

# ── VWAP deviation ────────────────────────────────────────────────────────────
sep('DISTANCIA AL VWAP EN ENTRY (precio > vwap = buen short)')
def vwap_bucket(v):
    if v is None: return 'sin vwap'
    if v >  0.3: return '>+0.3% (muy extendido)'
    if v >  0.1: return '>+0.1% (extendido)'
    if v >  0.0: return '>0     (sobre vwap)'
    if v > -0.1: return '<0     (bajo vwap leve)'
    return              '<-0.1% (bajo vwap)'
by_vwap = defaultdict(list)
for t in enriched: by_vwap[vwap_bucket(t.get('vwap_dev'))].append(t)
print(f'  {"VWAP dev":<26} {"n":>4} {"WR%":>6} {"AvgR":>8}  BigWin  Loser')
print('  ' + '-'*60)
order = ['>+0.3% (muy extendido)','>+0.1% (extendido)','>0     (sobre vwap)','<0     (bajo vwap leve)','<-0.1% (bajo vwap)','sin vwap']
for bk in order:
    ts = by_vwap.get(bk, [])
    if not ts: continue
    s  = stats(ts)
    bw = sum(1 for t in ts if t['resultR'] >= 2.0)
    lo = sum(1 for t in ts if t['resultR'] <= 0)
    print(f'  {bk:<26} {s["n"]:>4} {s["wr"]:>5.1f}%  {s["avgR"]:>+7.3f}R  {bw:>5}   {lo:>5}')

# ── TOP 10 TRADES ─────────────────────────────────────────────────────────────
sep('TOP 10 MEJORES TRADES (ADN del trade ideal)')
print(f'  {"#":>3} {"sym":>5} {"session":<20} {"h":>3} {"score":>5} {"regime":<12} {"vwap%":>6} {"dz":>6} {"obi":>6} {"reason":<16} {"R":>7}')
print('  ' + '-'*105)
for t in sorted(enriched, key=lambda x: -x['resultR'])[:10]:
    vd = f'{t["vwap_dev"]:>+.2f}' if t.get('vwap_dev') is not None else '  n/a'
    print(f'  {t["idx"]:>3} {t["sym"].replace("USDT",""):>5} {t.get("session","?"):<20} {t["hour_utc"]:>3}h {t.get("score","?"):>5} {t.get("regime","?"):<12} {vd:>6} {t["dz"]:>+6.2f} {t["obi_l5"]:>+6.3f} {t["reason"]:<16} {t["resultR"]:>+7.3f}R')

print()
