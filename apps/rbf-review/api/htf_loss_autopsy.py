#!/usr/bin/env python3
"""
AUTOPSIA DE PERDIDAS — HTF Shorts v2
Compara ganadores vs perdedores en cada feature para encontrar
qué distingue un SL del resto.
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

SCRIPT = Path(__file__).parent / 'shorts_htf_backtest.py'
TABLES = {'BTCUSDT':'btc_bars','ETHUSDT':'eth_bars','SOLUSDT':'sol_bars'}
STARTS = {'BTCUSDT':1780676700000,'ETHUSDT':1780756260000,'SOLUSDT':1780756260000}
DAYS   = 14

ENRICH_COLS = ('ts_ms,thin_above,thin_below,dz,obi_l5,obi_fast,absorption,'
               'stacked_imb,cvd_divergence,bar_delta,vr,oi_momentum,regime')

def sb_fetch(table, start_ms):
    rows, limit, offset = [], 1000, 0
    while True:
        qs = urllib.parse.urlencode({'select':ENRICH_COLS,'ts_ms':f'gte.{start_ms}',
             'order':'ts_ms.asc','limit':str(limit),'offset':str(offset)})
        req = urllib.request.Request(f'{SUPABASE_URL}/rest/v1/{table}?{qs}',
              headers={'apikey':SUPABASE_KEY,'Authorization':f'Bearer {SUPABASE_KEY}'})
        chunk = json.loads(urllib.request.urlopen(req, timeout=30).read())
        rows.extend(chunk);
        if len(chunk) < limit: break
        offset += limit
    return {b['ts_ms']: b for b in rows}

def sep(t): print(f'\n{"="*66}\n  {t}\n{"="*66}')

def pct(a, b): return f'{a/b*100:.1f}%' if b else 'n/a'

def stats(trades):
    if not trades: return {'n':0,'wr':0,'avgR':0,'totalR':0}
    n = len(trades); wins = [t for t in trades if t['resultR'] > 0]
    tr = sum(t['resultR'] for t in trades)
    return {'n':n,'wr':round(len(wins)/n*100,1),'avgR':round(tr/n,3),'totalR':round(tr,2)}

def feature_edge(trades, key, true_label='True'):
    """Compara WR/AvgR cuando feature=True vs False."""
    yes = [t for t in trades if str(t.get(key,'')).lower() in ('true','1','yes')]
    no  = [t for t in trades if t not in yes]
    sy = stats(yes); sn = stats(no)
    return sy, sn

def bucket_edge(trades, key, buckets):
    """Agrupa trades por valor discreto de key."""
    groups = defaultdict(list)
    for t in trades:
        v = t.get(key, None)
        groups[v].append(t)
    return {k: stats(v) for k, v in sorted(groups.items(), key=lambda x: str(x[0]))}

# ── 1. Cargar trades ──────────────────────────────────────────────────────────
print('Corriendo baseline...', end=' ', flush=True)
r = subprocess.run([sys.executable, str(SCRIPT), '--days', str(DAYS)],
                   capture_output=True, text=True, env={**__import__('os').environ, 'PYTHONIOENCODING':'utf-8'})
if r.returncode != 0:
    print('ERROR:', r.stderr[:400]); sys.exit(1)
data   = json.loads(r.stdout)
trades = data['trades']
print(f'n={len(trades)} WR={data["wr_pct"]}% AvgR={data["avg_r"]}R Equity=${data["equity"]}')

# ── 2. Enriquecer con datos de Supabase ───────────────────────────────────────
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
    e   = {
        **t,
        'hour_utc'    : dt.hour,
        'thin_above'  : str(bar.get('thin_above','')).lower() == 'true',
        'thin_below'  : str(bar.get('thin_below','')).lower() == 'true',
        'dz'          : float(bar.get('dz') or 0),
        'obi_l5'      : float(bar.get('obi_l5') or 0),
        'obi_fast'    : float(bar.get('obi_fast') or 0),
        'absorption'  : str(bar.get('absorption','')),
        'stacked_imb' : str(bar.get('stacked_imb','')),
        'cvd_div'     : str(bar.get('cvd_divergence','')),
        'bar_delta'   : float(bar.get('bar_delta') or 0),
        'vr'          : float(bar.get('vr') or 0),
        'oi_momentum' : bar.get('oi_momentum'),
        'regime'      : str(bar.get('regime','')),
        'is_win'      : t['resultR'] > 0,
        'is_sl'       : t['reason'] == 'STOP_LOSS',
    }
    enriched.append(e)

winners = [t for t in enriched if t['is_win']]
losers  = [t for t in enriched if not t['is_win']]
sl_only = [t for t in enriched if t['is_sl']]

# ── 3. AUTOPSIA OVERVIEW ─────────────────────────────────────────────────────
sep('OVERVIEW')
print(f'  Total trades : {len(enriched)}')
print(f'  Ganadores    : {len(winners)} ({pct(len(winners),len(enriched))})')
print(f'  Perdedores   : {len(losers)}  ({pct(len(losers),len(enriched))})')
print(f'  SL hits      : {len(sl_only)} ({pct(len(sl_only),len(enriched))})')
by_reason = defaultdict(list)
for t in enriched: by_reason[t['reason']].append(t)
print(f'\n  {"Reason":<20} {"n":>4} {"WR%":>6} {"AvgR":>8}')
print('  ' + '-'*42)
for r_name, ts in sorted(by_reason.items(), key=lambda x: -len(x[1])):
    s = stats(ts)
    print(f'  {r_name:<20} {s["n"]:>4} {s["wr"]:>5.1f}%  {s["avgR"]:>+7.3f}R')

# ── 4. FEATURE COMPARISON: ganadores vs perdedores ────────────────────────────
sep('FEATURES: GANADORES vs PERDEDORES')
print(f'  {"Feature":<22} {"Win n":>5} {"Win WR":>7} {"Win AvgR":>9} | {"Los n":>5} {"Los WR":>7} {"Los AvgR":>9}  Edge WR')
print('  ' + '-'*80)

bool_feats = ['thin_above', 'thin_below']
for feat in bool_feats:
    w_yes = [t for t in winners if t.get(feat)]
    w_no  = [t for t in winners if not t.get(feat)]
    l_yes = [t for t in losers  if t.get(feat)]
    l_no  = [t for t in losers  if not t.get(feat)]
    rate_w = len(w_yes) / len(winners) * 100 if winners else 0
    rate_l = len(l_yes) / len(losers)  * 100 if losers  else 0
    edge   = rate_w - rate_l
    marker = ' <--' if abs(edge) >= 10 else ''
    print(f'  {feat+"=True":<22} {len(w_yes):>5} ({rate_w:>4.0f}%)        | {len(l_yes):>5} ({rate_l:>4.0f}%)         {edge:>+5.1f}pp{marker}')

# ── 5. SCORE en ganadores vs perdedores ──────────────────────────────────────
sep('SCORE (confluence 0-5) EN GANADORES vs PERDEDORES')
print(f'  {"Score":<8} {"n":>4} {"WR%":>6} {"AvgR":>8} {"SL%":>6}  ganadores/perdedores')
print('  ' + '-'*55)
by_score = defaultdict(list)
for t in enriched: by_score[t.get('score', '?')].append(t)
for sc in sorted(by_score.keys(), key=lambda x: (x is None, x)):
    ts = by_score[sc]
    s  = stats(ts)
    sl_pct = sum(1 for t in ts if t['is_sl']) / len(ts) * 100
    bar_w = '#' * sum(1 for t in ts if t['is_win'])
    bar_l = '.' * sum(1 for t in ts if not t['is_win'])
    print(f'  {str(sc):<8} {s["n"]:>4} {s["wr"]:>5.1f}%  {s["avgR"]:>+7.3f}R {sl_pct:>5.1f}%  {bar_w}{bar_l}')

# ── 6. HORA UTC en perdedores ─────────────────────────────────────────────────
sep('HORA UTC: DONDE ESTAN LAS PERDIDAS')
print(f'  {"Hora":>5} {"n":>4} {"WR%":>6} {"AvgR":>8} {"n_SL":>5}  vis')
print('  ' + '-'*50)
by_hour = defaultdict(list)
for t in enriched: by_hour[t['hour_utc']].append(t)
for h in sorted(by_hour.keys()):
    ts   = by_hour[h]; s = stats(ts)
    n_sl = sum(1 for t in ts if t['is_sl'])
    bar  = '#' * sum(1 for t in ts if t['is_win']) + '.' * sum(1 for t in ts if not t['is_win'])
    print(f'  {h:>5}h {s["n"]:>4} {s["wr"]:>5.1f}%  {s["avgR"]:>+7.3f}R {n_sl:>5}  {bar}')

# ── 7. SIMBOLO en perdedores ──────────────────────────────────────────────────
sep('SIMBOLO')
print(f'  {"Sym":<8} {"n":>4} {"WR%":>6} {"AvgR":>8} {"n_SL":>5}')
print('  ' + '-'*38)
by_sym = defaultdict(list)
for t in enriched: by_sym[t['sym']].append(t)
for sym, ts in sorted(by_sym.items()):
    s    = stats(ts)
    n_sl = sum(1 for t in ts if t['is_sl'])
    print(f'  {sym.replace("USDT",""):<8} {s["n"]:>4} {s["wr"]:>5.1f}%  {s["avgR"]:>+7.3f}R {n_sl:>5}')

# ── 8. DZ (delta z-score) en perdedores ──────────────────────────────────────
sep('DZ (Delta Z-score): ganadores vs perdedores')
def dz_bucket(v):
    if v < -1.0: return 'dz<-1.0 (venta fuerte)'
    if v < -0.5: return 'dz<-0.5 (venta mod)'
    if v <  0.0: return 'dz<0    (venta leve)'
    if v <  0.5: return 'dz<0.5  (neutro)'
    return               'dz>=0.5 (compra)'
by_dz = defaultdict(list)
for t in enriched: by_dz[dz_bucket(t['dz'])].append(t)
print(f'  {"DZ bucket":<26} {"n":>4} {"WR%":>6} {"AvgR":>8} {"n_SL":>5}')
print('  ' + '-'*55)
order = ['dz<-1.0 (venta fuerte)','dz<-0.5 (venta mod)','dz<0    (venta leve)','dz<0.5  (neutro)','dz>=0.5 (compra)']
for bk in order:
    ts = by_dz.get(bk, [])
    if not ts: continue
    s = stats(ts); n_sl = sum(1 for t in ts if t['is_sl'])
    print(f'  {bk:<26} {s["n"]:>4} {s["wr"]:>5.1f}%  {s["avgR"]:>+7.3f}R {n_sl:>5}')

# ── 9. OBI en perdedores ──────────────────────────────────────────────────────
sep('OBI_L5 EN GANADORES vs PERDEDORES')
def obi_bucket(v):
    if v < -0.30: return 'obi<-0.30 (ask dom fuerte)'
    if v < -0.15: return 'obi<-0.15 (ask dom mod)'
    if v <  0.00: return 'obi<0     (ask leve)'
    if v <  0.15: return 'obi<0.15  (neutro)'
    return               'obi>=0.15 (bid dom)'
by_obi = defaultdict(list)
for t in enriched: by_obi[obi_bucket(t['obi_l5'])].append(t)
print(f'  {"OBI bucket":<28} {"n":>4} {"WR%":>6} {"AvgR":>8} {"n_SL":>5}')
print('  ' + '-'*58)
obi_order = ['obi<-0.30 (ask dom fuerte)','obi<-0.15 (ask dom mod)','obi<0     (ask leve)','obi<0.15  (neutro)','obi>=0.15 (bid dom)']
for bk in obi_order:
    ts = by_obi.get(bk, [])
    if not ts: continue
    s = stats(ts); n_sl = sum(1 for t in ts if t['is_sl'])
    print(f'  {bk:<28} {s["n"]:>4} {s["wr"]:>5.1f}%  {s["avgR"]:>+7.3f}R {n_sl:>5}')

# ── 10. AUTOPSIA INDIVIDUAL DE CADA PERDIDA ───────────────────────────────────
sep('AUTOPSIA INDIVIDUAL: TODOS LOS PERDEDORES')
print(f'  {"#":>3} {"sym":>5} {"session":<9} {"h":>3} {"score":>5} {"ta":>4} {"tb":>4} {"dz":>6} {"obi":>6} {"reason":<18} {"R":>7}  flags')
print('  ' + '-'*95)
for t in sorted(losers, key=lambda x: x['resultR']):
    flags = t.get('confluenceFlags', [])
    flag_str = ','.join(flags) if flags else '-'
    ta = 'T' if t['thin_above'] else 'F'
    tb = 'T' if t['thin_below'] else 'F'
    h  = t['hour_utc']
    print(f'  {t["idx"]:>3} {t["sym"].replace("USDT",""):>5} {t.get("session","?"):<9} {h:>3}h {t.get("score","?"):>5} {ta:>4} {tb:>4} {t["dz"]:>+6.2f} {t["obi_l5"]:>+6.3f} {t["reason"]:<18} {t["resultR"]:>+7.3f}R  {flag_str}')

print()
