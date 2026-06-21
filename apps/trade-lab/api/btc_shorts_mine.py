#!/usr/bin/env python3
"""
BTC Shorts — Data Mining de Patrones
======================================
Enfoque data science:
  1. Fetcha todas las barras BTC con microestructura disponible
  2. Para cada shooting star en London/NY (D1 no bull), simula el outcome
  3. Extrae todos los features disponibles en btc_bars
  4. Mina todas las combinaciones de features (1, 2 y 3 features)
  5. Reporta las que tienen WR >= MIN_WR y AvgR >= MIN_AVG_R con n >= MIN_N

Uso:
  python btc_shorts_mine.py [--days N] [--min-n N] [--min-wr N] [--min-avgr N]
"""
import json, time, urllib.request, urllib.parse, argparse
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict
from itertools import combinations

# ── Config ────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).parent.parent.parent.parent
env = {l.split('=')[0]: l.split('=',1)[1]
       for l in (ROOT/'.env').read_text(encoding='utf-8').splitlines()
       if '=' in l and not l.startswith('#')}
URL = env['SUPABASE_URL']
KEY = env['SUPABASE_KEY']

FEE_RT        = 0.0007
TARGET_R      = 2.5
MIN_STOP_PCT  = 0.30
MAX_STOP_PCT  = 0.75
COOLDOWN_M1   = 30
FORWARD_M1    = 1200
CVD_FLIP_BARS = 5
OBI_FLIP_THR  = 0.15
MIN_PROFIT_CVD= 1.0

MICRO_START_MS = 1780676700000   # 2026-06-05 16:25 UTC

COLS = ('ts_ms,open,high,low,close,volume,atr,session,cvd_slope,obi_l5,'
        'vwap,vr,oi_momentum,bar_delta,regime,dz,absorption,'
        'stacked_imb,equal_high,equal_low,obi_fast,vpin,cvd_divergence,'
        'swing_high_50,swing_low_50,prev_day_high,prev_day_low,'
        'asian_high,asian_low,thin_above,thin_below,ask_wall,bid_wall,'
        'liq_ratio,sweep_confirmed,vp_poc,vp_vah,vp_val,vp_lvn_below,'
        'big_trade_bearish,big_trade_bullish,'
        'obi_min_intrabar,obi_max_intrabar')

# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch(start_ms):
    rows, limit, offset = [], 1000, 0
    while True:
        qs = urllib.parse.urlencode({
            'select': COLS, 'ts_ms': f'gte.{start_ms}',
            'order': 'ts_ms.asc', 'limit': str(limit), 'offset': str(offset),
        })
        req = urllib.request.Request(f'{URL}/rest/v1/btc_bars?{qs}',
            headers={'apikey': KEY, 'Authorization': f'Bearer {KEY}'})
        chunk = json.loads(urllib.request.urlopen(req, timeout=30).read())
        rows.extend(chunk)
        if len(chunk) < limit: break
        offset += limit
    return rows

def binance_d1():
    req = urllib.request.Request(
        'https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1d&limit=120',
        headers={'User-Agent': 'Mozilla/5.0'})
    data = json.loads(urllib.request.urlopen(req, timeout=15).read())
    return [{'ts_ms': int(d[0]), 'close': float(d[4])} for d in data]

# ── Indicadores ───────────────────────────────────────────────────────────────

def ema(vals, p):
    k = 2/(p+1); r = [vals[0]]
    for v in vals[1:]: r.append(v*k + r[-1]*(1-k))
    return r

def build_h1(m1):
    bkt = defaultdict(list)
    for b in m1: bkt[(b['ts_ms']//3_600_000)*3_600_000].append(b)
    h1 = []
    for ts in sorted(bkt):
        bs = bkt[ts]
        h1.append({'ts_ms': ts, 'open': bs[0]['open'],
                   'high': max(b['high'] for b in bs),
                   'low':  min(b['low']  for b in bs),
                   'close': bs[-1]['close']})
    return h1

def atr14(bars):
    trs = []
    for i, b in enumerate(bars):
        pc = bars[i-1]['close'] if i > 0 else b['open']
        trs.append(max(b['high']-b['low'], abs(b['high']-pc), abs(b['low']-pc)))
    a = [sum(trs[:14])/14]*14; k = 1/14
    for i in range(14, len(trs)): a.append(a[-1]*(1-k) + trs[i]*k)
    return a

def d1_trend_map(m1):
    d1 = binance_d1()
    e  = ema([b['close'] for b in d1], 20)
    trend = {}
    for i, b in enumerate(d1):
        c, ev = b['close'], e[i]
        trend[b['ts_ms']] = 'bull' if c > ev*1.005 else ('bear' if c < ev*0.995 else 'neutral')
    out = {}
    for b in m1:
        day = (b['ts_ms']//86_400_000)*86_400_000
        out[b['ts_ms']] = trend.get(day, 'unknown')
    return out

# ── Simulación ────────────────────────────────────────────────────────────────

def simulate(m1, ei, entry, stop, risk):
    target = entry - TARGET_R * risk
    cs = os = 0
    for k in range(1, min(FORWARD_M1, len(m1)-ei)):
        mb = m1[ei+k]; h, l = mb['high'], mb['low']
        if l <= target:
            return round(TARGET_R - FEE_RT*entry/risk, 4), 'TAKE_PROFIT'
        if h >= stop:
            return round(-(1.0 + FEE_RT*entry/risk), 4), 'STOP_LOSS'
        cr = (entry - mb['close'])/risk
        cv = mb.get('cvd_slope') or 0
        of = mb.get('obi_fast') or 0
        cs = cs+1 if cv > 0 else 0
        os = os+1 if of > OBI_FLIP_THR else 0
        if cs >= CVD_FLIP_BARS and os >= 1 and cr >= MIN_PROFIT_CVD:
            return round((entry-mb['close'])/risk - FEE_RT*entry/risk, 4), 'CVD_EXHAUSTION'
    last = m1[min(ei+FORWARD_M1-1, len(m1)-1)]
    return round((entry-last['close'])/risk - FEE_RT*entry/risk, 4), 'EXPIRED'

# ── Extracción de features ────────────────────────────────────────────────────

def extract_features(b):
    """
    Retorna dict de features binarios (True/False) para una barra.
    Cada feature es una hipótesis sobre condición de mercado en el momento de entrada.
    """
    ses   = b.get('session', '')
    vwap  = b.get('vwap') or 0
    poc   = b.get('vp_poc') or 0
    vah   = b.get('vp_vah') or 0
    val   = b.get('vp_val') or 0
    lvn   = b.get('vp_lvn_below') or 0
    ah    = b.get('asian_high') or 0
    al    = b.get('asian_low') or 0
    pdh   = b.get('prev_day_high') or 0
    pdl   = b.get('prev_day_low') or 0
    close = b['close']
    tol   = 0.002   # 0.2% tolerancia para "en nivel"

    def near(price, level):
        return level > 0 and abs(price - level)/level <= tol

    def above(price, level):
        return level > 0 and price > level

    return {
        # Sesión
        'london':           ses in ('London', 'LondonNyOverlap'),
        'ny':               ses == 'NewYork',
        'london_ny_over':   ses == 'LondonNyOverlap',

        # Orderflow
        'oi':               b.get('oi_momentum') is True or str(b.get('oi_momentum')).lower() == 'true',
        'abs_ask':          b.get('absorption', '') == 'Ask',
        'stk_bear':         b.get('stacked_imb', '') == 'Bearish',
        'obi_neg':          (b.get('obi_fast') or 0) < -0.10,
        'obi_strong_neg':   (b.get('obi_fast') or 0) < -0.20,
        'cvd_neg':          (b.get('cvd_slope') or 0) < 0,
        'cvd_bear_div':     b.get('cvd_divergence', '') == 'BearishAbsorption',
        'dz_neg':           (b.get('dz') or 0) < -0.5,
        'delta_neg':        (b.get('bar_delta') or 0) < 0,
        'vpin_high':        (b.get('vpin') or 0) > 0.6,
        'ask_wall':         b.get('ask_wall') is True or str(b.get('ask_wall')).lower() == 'true',

        # VWAP
        'below_vwap':       vwap > 0 and close < vwap,
        'above_vwap':       vwap > 0 and close > vwap,
        'near_vwap':        near(close, vwap),

        # Volume Profile
        'above_poc':        above(close, poc),
        'near_poc':         near(close, poc),
        'above_vah':        above(close, vah),
        'near_vah':         near(close, vah),
        'above_val':        above(close, val),
        'lvn_below':        lvn > 0 and lvn < close,

        # Niveles estructurales
        'at_asian_high':    near(close, ah),
        'above_asian_high': above(close, ah),
        'below_asian_high': ah > 0 and close < ah,
        'at_pdh':           near(close, pdh),
        'above_pdh':        above(close, pdh),
        'below_pdh':        pdh > 0 and close < pdh,

        # Contexto de zona
        'thin_above':       b.get('thin_above') is True or str(b.get('thin_above')).lower() == 'true',
        'sweep_confirmed':  b.get('sweep_confirmed') is True or str(b.get('sweep_confirmed')).lower() == 'true',
        'equal_high':       b.get('equal_high') is True or str(b.get('equal_high')).lower() == 'true',

        # Régimen
        'expansion':        b.get('regime', '') == 'Expansion',
        'trend_down':       b.get('regime', '') == 'TrendDown',
        'trend_up':         b.get('regime', '') == 'TrendUp',
        'chop':             b.get('regime', '') == 'Chop',

        # VR
        'vr_high':          (b.get('vr') or 0) > 2.0,
        'vr_very_high':     (b.get('vr') or 0) > 4.0,

        # Big Trade (footprint: volumen >2.5x media de la barra)
        'big_trade_bearish': b.get('big_trade_bearish') is True or str(b.get('big_trade_bearish')).lower() == 'true',
        'big_trade_bullish': b.get('big_trade_bullish') is True or str(b.get('big_trade_bullish')).lower() == 'true',

        # OBI intrabar (min/max de 6 muestras cada 10s — contexto dentro de la vela)
        # Captura presión vendedora real durante el wick, no solo al cierre
        'obi_min_neg20':  (b.get('obi_min_intrabar') or 0) < -0.20,
        'obi_min_neg30':  (b.get('obi_min_intrabar') or 0) < -0.30,
        'obi_min_neg40':  (b.get('obi_min_intrabar') or 0) < -0.40,
        'obi_max_pos20':  (b.get('obi_max_intrabar') or 0) > 0.20,
        'obi_range_wide': ((b.get('obi_max_intrabar') or 0) - (b.get('obi_min_intrabar') or 0)) > 0.30,
    }

# ── Mining ────────────────────────────────────────────────────────────────────

def stats(results):
    if not results: return None
    wins = [r for r in results if r > 0]
    return {
        'n':    len(results),
        'wr':   round(len(wins)/len(results)*100, 1),
        'avgr': round(sum(results)/len(results), 3),
        'totalr': round(sum(results), 2),
    }

def mine(records, min_n, min_wr, min_avgr):
    """
    records: lista de {'result_r': float, 'features': dict}
    Prueba combinaciones de 1, 2 y 3 features y retorna las que tienen edge.
    """
    feature_names = list(records[0]['features'].keys())
    found = []

    for depth in [1, 2, 3]:
        for combo in combinations(feature_names, depth):
            # Filtrar registros donde TODOS los features del combo son True
            subset = [r for r in records if all(r['features'][f] for f in combo)]
            if len(subset) < min_n:
                continue
            s = stats([r['result_r'] for r in subset])
            if s['wr'] >= min_wr and s['avgr'] >= min_avgr:
                found.append({
                    'pattern': '+'.join(combo),
                    'depth':   depth,
                    **s,
                })

    return sorted(found, key=lambda x: (-x['wr'], -x['avgr']))

# ── Drill-down ────────────────────────────────────────────────────────────────

def drill(records, required_features):
    """
    Analiza un subset (todos los features=True) y muestra qué features
    adicionales separan winners de losers dentro de ese grupo.
    """
    subset = [r for r in records if all(r['features'].get(f) for f in required_features)]
    if not subset:
        print('  Sin registros con esos features.')
        return

    wins   = [r for r in subset if r['result_r'] > 0]
    losses = [r for r in subset if r['result_r'] <= 0]
    n      = len(subset)
    wr     = len(wins)/n*100 if n else 0
    avgr   = sum(r['result_r'] for r in subset)/n if n else 0

    print(f'\n{"="*65}')
    print(f'  DRILL: {"+".join(required_features)}')
    print(f'{"="*65}')
    print(f'  n={n}  WR={wr:.1f}%  AvgR={avgr:+.3f}R')

    print(f'\n  Trades:')
    for r in subset:
        mark = 'W' if r['result_r'] > 0 else 'L'
        ses  = 'L' if r['features'].get('london') and not r['features'].get('london_ny_over') \
               else ('LNY' if r['features'].get('london_ny_over') else 'NY')
        print(f'  [{mark}] {r["ts"]} [{ses}] {r["result_r"]:+.3f}R  {r["reason"]}')

    print(f'\n  Feature split — Winners vs Losers (diferencia):')
    print(f'  {"Feature":<30} {"W_pct":>7} {"L_pct":>7} {"Delta":>8}')
    print(f'  {"-"*58}')
    feat_names = list(subset[0]['features'].keys()) if subset else []
    diffs = []
    for f in feat_names:
        if f in required_features: continue
        wp = sum(1 for r in wins   if r['features'].get(f))/len(wins)*100   if wins   else 0
        lp = sum(1 for r in losses if r['features'].get(f))/len(losses)*100 if losses else 0
        diffs.append((f, wp, lp, wp - lp))
    for f, wp, lp, d in sorted(diffs, key=lambda x: -abs(x[3])):
        if abs(d) < 10: break  # solo mostrar diferencias significativas
        bar = ('+' if d > 0 else '-') * min(int(abs(d)/8), 8)
        print(f'  {f:<30} {wp:>6.0f}%  {lp:>6.0f}%  {d:>+7.1f}pp  {bar}')

    print(f'\n  Filtros adicionales que suben WR (min_n=3, WR>=65%):')
    found = []
    for f in feat_names:
        if f in required_features: continue
        sub2 = [r for r in subset if r['features'].get(f)]
        if len(sub2) < 3: continue
        wr2  = sum(1 for r in sub2 if r['result_r'] > 0)/len(sub2)*100
        avg2 = sum(r['result_r'] for r in sub2)/len(sub2)
        if wr2 >= 65:
            found.append((f, len(sub2), wr2, avg2))
    if found:
        for f, n2, wr2, avg2 in sorted(found, key=lambda x: -x[2]):
            print(f'  + {f:<30} n={n2:>3}  WR={wr2:.1f}%  AvgR={avg2:+.3f}R')
    else:
        print('  (ninguna con n>=3)')


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days',    type=int,   default=9)
    parser.add_argument('--min-n',   type=int,   default=5)
    parser.add_argument('--min-wr',  type=float, default=55.0)
    parser.add_argument('--min-avgr',type=float, default=0.30)
    parser.add_argument('--drill',    type=str,   default='',
                        help='Features separadas por coma para drill-down. Ej: oi,delta_neg')
    parser.add_argument('--sessions', type=str,   default='',
                        help='Sesiones a incluir. Ej: NewYork  o  London,LondonNyOverlap')
    args = parser.parse_args()

    start_ms = max(MICRO_START_MS, int((time.time() - args.days*86400)*1000))

    print(f'Cargando BTCUSDT — {args.days}d...')
    m1 = fetch(start_ms)
    print(f'  {len(m1)} barras M1')

    print('  Calculando D1 trend...')
    d1t = d1_trend_map(m1)

    h1     = build_h1(m1)
    h1_atr = atr14(h1)
    h1_map = {hb['ts_ms']: (hb['high'], h1_atr[i])
              for i, hb in enumerate(h1) if i < len(h1_atr)}

    records    = []
    last_sig_i = -COOLDOWN_M1

    print('  Simulando outcomes...')
    for i in range(10, len(m1)-FORWARD_M1-1):
        b = m1[i]
        if not b.get('cvd_slope'):
            continue
        if d1t.get(b['ts_ms']) == 'bull':
            continue

        ses = b.get('session', '')
        if ses not in ('London', 'LondonNyOverlap', 'NewYork'):
            continue

        # Shooting star
        rng  = (b['high']-b['low']) or 1e-10
        body = abs(b['close']-b['open'])
        wh   = b['high'] - max(b['close'], b['open'])
        if not ((wh/rng) > 0.45 and (body/rng) < 0.40):
            continue

        # Filtrar por sesión según --sessions arg
        allowed = [s.strip() for s in args.sessions.split(',')] if args.sessions else ['London','LondonNyOverlap','NewYork']
        if ses not in allowed:
            continue

        # Stop estructural H1
        h1_ts = (b['ts_ms']//3_600_000)*3_600_000
        if h1_ts not in h1_map:
            continue
        h1_high, atr_val = h1_map[h1_ts]
        if atr_val <= 0:
            continue

        stop  = h1_high + 0.3*atr_val
        entry = b['close']
        risk  = stop - entry
        if risk <= 0:
            continue

        sp = risk/entry*100
        if not (MIN_STOP_PCT <= sp <= MAX_STOP_PCT):
            continue

        # Sin cooldown aquí — queremos TODOS los shooting stars para minear
        # (el cooldown lo aplicará el sistema live)
        result_r, reason = simulate(m1, i, entry, stop, risk)
        feats = extract_features(b)

        records.append({
            'ts':       datetime.fromtimestamp(b['ts_ms']/1000, tz=timezone.utc).strftime('%m-%d %H:%M'),
            'result_r': result_r,
            'reason':   reason,
            'features': feats,
        })

    n_total = len(records)
    wins    = [r for r in records if r['result_r'] > 0]
    avg_r   = sum(r['result_r'] for r in records)/n_total if n_total else 0

    print(f'\n{"="*65}')
    print(f'  UNIVERSO DE SHOOTING STARS (sin cooldown)')
    print(f'{"="*65}')
    print(f'  Total: n={n_total}  WR={len(wins)/n_total*100:.1f}%  AvgR={avg_r:+.3f}R')

    print(f'\n{"="*65}')
    print(f'  MINING — min_n={args.min_n}  min_WR={args.min_wr}%  min_AvgR={args.min_avgr}R')
    print(f'{"="*65}')

    found = mine(records, args.min_n, args.min_wr, args.min_avgr)

    if not found:
        print('  Sin patrones con edge en estos parámetros.')
        print('  Prueba reducir --min-wr o --min-n')
    else:
        print(f'  {"Patron":<55} {"n":>4} {"WR":>6} {"AvgR":>8} {"TotalR":>8}')
        print(f'  {"-"*85}')
        for p in found:
            marker = '<-- EDGE' if p['wr'] >= 60 and p['avgr'] >= 0.50 else ''
            print(f'  {p["pattern"]:<55} {p["n"]:>4} {p["wr"]:>5.1f}% {p["avgr"]:>+8.3f}R {p["totalr"]:>+7.2f}R  {marker}')

    # Baseline de cada feature individual para referencia
    print(f'\n{"="*65}')
    print(f'  FEATURES INDIVIDUALES (referencia)')
    print(f'{"="*65}')
    print(f'  {"Feature":<30} {"n_true":>6} {"WR_true":>8} {"WR_false":>9} {"Delta":>7}')
    print(f'  {"-"*60}')
    feat_names = list(records[0]['features'].keys()) if records else []
    feat_stats = []
    for f in feat_names:
        true_rs  = [r['result_r'] for r in records if r['features'][f]]
        false_rs = [r['result_r'] for r in records if not r['features'][f]]
        if not true_rs or not false_rs: continue
        wr_t = sum(1 for r in true_rs  if r > 0)/len(true_rs)*100
        wr_f = sum(1 for r in false_rs if r > 0)/len(false_rs)*100
        feat_stats.append((f, len(true_rs), wr_t, wr_f, wr_t-wr_f))

    for f, nt, wrt, wrf, delta in sorted(feat_stats, key=lambda x: -x[4]):
        bar = '+' * int(abs(delta)/3) if delta > 0 else '-' * int(abs(delta)/3)
        print(f'  {f:<30} {nt:>6} {wrt:>7.1f}% {wrf:>8.1f}% {delta:>+6.1f}pp  {bar}')

    # Drill-down opcional
    if args.drill:
        feats = [f.strip() for f in args.drill.split(',') if f.strip()]
        drill(records, feats)

if __name__ == '__main__':
    main()
