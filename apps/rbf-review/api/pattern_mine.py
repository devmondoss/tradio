#!/usr/bin/env python3
"""
Mineria de patrones para shorts M1.
Para cada combinacion de flags, mide MFE/MAE promedio en 60 barras M1.
MFE short = % max caida desde entry (ganancia). MAE = % max subida (perdida).
WR = % de barras donde MFE > MAE (el trade hubiera ganado).
"""
import json, urllib.request, urllib.parse, itertools
from pathlib import Path

_env = {}
for line in (Path(__file__).parent.parent.parent.parent / '.env').read_text(encoding='utf-8').splitlines():
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        _env[k.strip()] = v.strip().strip('"').strip("'")

URL = _env['SUPABASE_URL']
KEY = _env['SUPABASE_KEY']

def fetch_all(table, start_ms):
    rows, limit, offset = [], 1000, 0
    cols = ('ts_ms,open,high,low,close,session,cvd_slope,obi_l5,obi_fast,'
            'vr,oi_momentum,bar_delta,regime,dz,absorption,stacked_imb,'
            'equal_high,cvd_divergence,atr,vpin,swing_high_50,thin_above')
    while True:
        qs = urllib.parse.urlencode({
            'select': cols, 'ts_ms': f'gte.{start_ms}',
            'order': 'ts_ms.asc', 'limit': str(limit), 'offset': str(offset)
        })
        req = urllib.request.Request(
            f'{URL}/rest/v1/{table}?{qs}',
            headers={'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
        )
        chunk = json.loads(urllib.request.urlopen(req, timeout=30).read())
        rows.extend(chunk)
        if len(chunk) < limit:
            break
        offset += limit
    return rows

def get_mfe_mae(bars, i, horizon):
    entry = bars[i]['close']
    lows  = [bars[j]['low']  for j in range(i+1, min(i+1+horizon, len(bars)))]
    highs = [bars[j]['high'] for j in range(i+1, min(i+1+horizon, len(bars)))]
    if not lows:
        return None, None
    mfe = (entry - min(lows))  / entry * 100
    mae = (max(highs) - entry) / entry * 100
    return round(mfe, 4), round(mae, 4)

def extract_flags(b):
    cvd   = b.get('cvd_slope') or 0
    obi   = b.get('obi_l5')   or 0
    obif  = b.get('obi_fast') or 0
    vr    = b.get('vr')        or 0
    oi    = b.get('oi_momentum')
    stk   = b.get('stacked_imb', '')
    ses   = b.get('session', '')
    reg   = b.get('regime', '')
    abso  = b.get('absorption', '')
    eq    = b.get('equal_high')
    vpin  = b.get('vpin') or 0
    dz    = b.get('dz') or 0
    rng   = (b['high'] - b['low']) or 1
    body  = abs(b['close'] - b['open'])
    wick_hi = b['high'] - max(b['close'], b['open'])
    bear  = b['close'] < b['open']
    return {
        'cvd_neg':     cvd < 0,
        'cvd_strong':  cvd < -8,
        'obi_neg':     obi < 0,
        'obi_strong':  obi < -0.15,
        'obif_neg':    obif < 0,
        'vr_high':     vr > 2.0,
        'vr_vhigh':    vr > 4.0,
        'oi_true':     oi is True,
        'stk_bear':    stk == 'Bearish',
        'ses_london':  ses in ('London', 'LondonNyOverlap'),
        'ses_ny':      ses == 'NewYork',
        'ses_asia':    ses == 'Asia',
        'reg_down':    reg == 'TrendDown',
        'reg_exp':     reg == 'Expansion',
        'dz_pos':      dz > 0,
        'abs_ask':     abso == 'Ask',
        'eq_hi':       str(eq).lower() == 'true',
        'vpin_high':   vpin > 0.6,
        'bear_body':   bear,
        'shoot_star':  (wick_hi / rng > 0.45) and (body / rng < 0.4),
    }

def analyze(bars, sym, horizon=60):
    n = len(bars)
    flags_list = [extract_flags(b) for b in bars]
    flag_names = list(flags_list[0].keys())

    def score_combo(idxs):
        mfes = []
        for i in idxs:
            mfe, mae = get_mfe_mae(bars, i, horizon)
            if mfe is not None:
                mfes.append((mfe, mae))
        if not mfes:
            return None
        avg_mfe = sum(x[0] for x in mfes) / len(mfes)
        avg_mae = sum(x[1] for x in mfes) / len(mfes)
        wr      = sum(1 for x in mfes if x[0] > x[1]) / len(mfes) * 100
        mfe_med = sorted(x[0] for x in mfes)[len(mfes)//2]
        mae_med = sorted(x[1] for x in mfes)[len(mfes)//2]
        return {
            'n':       len(mfes),
            'mfe':     round(avg_mfe, 3),
            'mae':     round(avg_mae, 3),
            'mfe_med': round(mfe_med, 3),
            'mae_med': round(mae_med, 3),
            'wr':      round(wr, 1),
            'edge':    round(avg_mfe - avg_mae, 3),
        }

    results = []

    # Singles
    for f1 in flag_names:
        idxs = [i for i, fl in enumerate(flags_list) if fl.get(f1) and i + horizon < n]
        if len(idxs) < 6:
            continue
        s = score_combo(idxs)
        if s:
            results.append({'flags': [f1], **s})

    # Top singles como semillas para pares
    top_singles = sorted(
        [r for r in results if r['n'] >= 6],
        key=lambda x: (-x['wr'], -x['edge'])
    )[:14]
    top_flag_set = [r['flags'][0] for r in top_singles]

    for f1, f2 in itertools.combinations(top_flag_set, 2):
        idxs = [i for i, fl in enumerate(flags_list)
                if fl.get(f1) and fl.get(f2) and i + horizon < n]
        if len(idxs) < 4:
            continue
        s = score_combo(idxs)
        if s:
            results.append({'flags': [f1, f2], **s})

    # Top pares -> triples
    top_pairs = sorted(
        [r for r in results if len(r['flags']) == 2 and r['n'] >= 4],
        key=lambda x: (-x['wr'], -x['edge'])
    )[:10]

    for pr in top_pairs:
        for f3 in top_flag_set:
            if f3 in pr['flags']:
                continue
            combo = pr['flags'] + [f3]
            idxs = [i for i, fl in enumerate(flags_list)
                    if all(fl.get(f) for f in combo) and i + horizon < n]
            if len(idxs) < 3:
                continue
            s = score_combo(idxs)
            if s:
                results.append({'flags': combo, **s})

    return results

STARTS = {
    'btc_bars': 1780676700000,
    'eth_bars': 1780756260000,
    'sol_bars': 1780756260000,
}
SYM = {'btc_bars': 'BTC', 'eth_bars': 'ETH', 'sol_bars': 'SOL'}
HORIZON = 60  # barras M1

print('=== MINERIA DE PATRONES SHORT M1 ===')
print(f'Horizonte: {HORIZON} barras M1 (~1h)\n')
print(f'  {"Flags":<42} {"n":>5}  {"MFE%":>6}  {"MAE%":>6}  {"WR%":>6}  {"Edge":>6}  {"MFEmed":>7}')
print()

for tbl, start in STARTS.items():
    sym = SYM[tbl]
    bars = fetch_all(tbl, start)
    print(f'--- {sym} ({len(bars)} barras M1) ---')
    results = analyze(bars, sym, HORIZON)
    top = sorted(results, key=lambda x: (-x['wr'], -x['edge']))[:25]
    for r in top:
        flags_str = ' + '.join(r['flags'])
        print(f'  {flags_str:<42} {r["n"]:>5}  {r["mfe"]:>6.3f}  {r["mae"]:>6.3f}  '
              f'{r["wr"]:>5.1f}%  {r["edge"]:>+6.3f}  {r["mfe_med"]:>7.3f}')
    print()
