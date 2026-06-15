#!/usr/bin/env python3
"""
MTF DB Context Mining — usa columnas DB + H1 ATR pre-computado
===============================================================
Fixes vs mtf_multibar_mine.py:
  1. H1 ATR pre-computado desde M1 agregado → no hay bug de ATR=0 en barras iniciales
  2. cvd_consec_neg / bars_since_low_vr / prev_bar_delta leídos de DB cuando disponibles
     (acumulan desde 2026-06-15; para barras anteriores se computan desde M1)
  3. session_phase computado desde M1 (contar barras en misma sesion hacia atras)
  4. M5 CVD consecutivo (agrega M1 en M5, cuenta barras M5 consecutivas con CVD < 0)

Features analizadas:
  A. session_phase: minutos desde inicio de sesion al entry (16-45 min = early = mejor)
  B. m5_cvd_consec: barras M5 consecutivas con CVD < 0 (sweet spot 1-2)
  C. cvd_consec_neg: campo DB (o computado) — barras M1 consecutivas con cvd_slope < 0
  D. bars_since_low_vr: campo DB — barras desde ultima compresion de volumen
  E. prev_bar_delta: campo DB — delta de la barra M1 anterior
"""
import json, os, sys, time, urllib.request, urllib.parse
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from pathlib import Path
from collections import defaultdict
import math

ROOT = Path(__file__).parent.parent.parent.parent
_env = {}
for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        _env[k.strip()] = v.strip().strip('"').strip("'")

SUPABASE_URL = _env.get('SUPABASE_URL', '')
SUPABASE_KEY = _env.get('SUPABASE_KEY', '')

DAYS   = 14
STARTS = {'BTCUSDT':1780676700000,'ETHUSDT':1780756260000,
          'SOLUSDT':1780756260000,'BNBUSDT':1780756260000,'XRPUSDT':1780756260000}
TABLES = {'BTCUSDT':'btc_bars','ETHUSDT':'eth_bars','SOLUSDT':'sol_bars',
          'BNBUSDT':'bnb_bars','XRPUSDT':'xrp_bars'}
BAR_COLS = ('ts_ms,open,high,low,close,volume,atr,session,cvd_slope,obi_l5,'
            'vwap,vr,oi_momentum,bar_delta,regime,dz,absorption,'
            'stacked_imb,thin_above,ask_wall,equal_high,cvd_divergence,obi_fast,'
            'cvd_consec_neg,bars_since_low_vr,prev_bar_delta')

MIN_STOP_PCT = 0.30; MAX_STOP_PCT = 0.75
TARGET_R = 2.5; CVD_FLIP_BARS = 5; OBI_FLIP_THR = 0.15
MIN_PROFIT_CVD = 1.0; COOLDOWN = 30; FORWARD_MAX = 1200; FEE_RT = 0.0007

# ── Fetch ─────────────────────────────────────────────────────────────────────

def sb_fetch(table, start_ms):
    rows, limit, offset = [], 1000, 0
    while True:
        qs = urllib.parse.urlencode({'select':BAR_COLS,'ts_ms':f'gte.{start_ms}',
             'order':'ts_ms.asc','limit':str(limit),'offset':str(offset)})
        req = urllib.request.Request(f'{SUPABASE_URL}/rest/v1/{table}?{qs}',
              headers={'apikey':SUPABASE_KEY,'Authorization':f'Bearer {SUPABASE_KEY}'})
        chunk = json.loads(urllib.request.urlopen(req,timeout=30).read())
        rows.extend(chunk)
        if len(chunk)<limit: break
        offset+=limit
    return rows

def binance_d1(symbol):
    url = f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1d&limit=120'
    try:
        req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
        data = json.loads(urllib.request.urlopen(req,timeout=15).read())
        return [float(d[4]) for d in data]
    except: return []

def ema(vals, p):
    k = 2/(p+1); r = [vals[0]]
    for v in vals[1:]: r.append(v*k+r[-1]*(1-k))
    return r

def d1_trend(close, d1_closes):
    if len(d1_closes) < 20: return 'unknown'
    e = ema(d1_closes, 20)[-1]
    if close > e*1.005: return 'bull'
    if close < e*0.995: return 'bear'
    return 'neutral'

# ── H1 ATR pre-computado (FIX del bug incremental) ───────────────────────────

def build_h1_from_m1(m1):
    """Agrega M1 en H1 y calcula ATR(14) completo sobre todo el dataset."""
    buckets = defaultdict(list)
    for b in m1:
        h = b['ts_ms'] // 3_600_000
        buckets[h].append(b)

    h1 = []
    for h in sorted(buckets):
        bs = buckets[h]
        high = max(b['high'] for b in bs)
        low  = min(b['low']  for b in bs)
        h1.append({'h': h, 'high': high, 'low': low, 'range': high - low})

    # ATR simple (media de range de las 14 barras H1 anteriores)
    h1_atr = {}
    for i, bar in enumerate(h1):
        if i < 5:
            h1_atr[bar['h']] = 0.0  # insuficiente historial — señales bloqueadas
        else:
            window = h1[max(0,i-14):i]
            h1_atr[bar['h']] = sum(b['range'] for b in window) / len(window)

    return h1_atr  # dict: hora (ts//3600000) -> atr

def get_h1_high(m1, i):
    """High de la hora H1 actual hasta la barra i."""
    cur_h = m1[i]['ts_ms'] // 3_600_000
    high = m1[i]['high']
    for j in range(i-1, -1, -1):
        if m1[j]['ts_ms'] // 3_600_000 != cur_h:
            break
        high = max(high, m1[j]['high'])
    return high

# ── M5 CVD consecutivo ────────────────────────────────────────────────────────

def build_m5_cvd_index(m1):
    """Para cada ts_ms de M5, cuenta cuantas barras M5 consecutivas previas tienen CVD<0."""
    ms5 = 5 * 60_000
    buckets = defaultdict(list)
    for b in m1:
        bucket = (b['ts_ms'] // ms5) * ms5
        buckets[bucket].append(b)

    # M5 CVD slope = promedio de cvd_slope en las M1 que lo componen
    m5_series = []
    for ts in sorted(buckets):
        bs = buckets[ts]
        cvd_avg = sum(float(b.get('cvd_slope') or 0) for b in bs) / len(bs)
        m5_series.append({'ts_ms': ts, 'cvd_slope': cvd_avg})

    # Pre-computar consec_neg para cada barra M5
    consec = {}
    for i, bar in enumerate(m5_series):
        c = 0
        for j in range(i-1, max(i-20,-1), -1):
            if m5_series[j]['cvd_slope'] < 0:
                c += 1
            else:
                break
        consec[bar['ts_ms']] = c

    return consec

# ── Context features ──────────────────────────────────────────────────────────

def get_session_phase(m1, i):
    """Minutos desde el inicio de la sesion actual (= barras M1 en la misma sesion)."""
    cur_ses = m1[i].get('session', '')
    phase = 0
    for j in range(i-1, max(i-240, -1), -1):
        if m1[j].get('session', '') == cur_ses:
            phase += 1
        else:
            break
    return phase  # en minutos (M1 bars)

def get_cvd_consec_neg(b, m1, i):
    """Usa campo DB si disponible, sino computa desde M1."""
    db_val = b.get('cvd_consec_neg')
    if db_val is not None:
        return int(db_val)
    # Fallback: computar desde M1
    c = 0
    for j in range(i-1, max(i-30, -1), -1):
        if (m1[j].get('cvd_slope') or 0) < 0:
            c += 1
        else:
            break
    return c

def get_bars_since_low_vr(b, m1, i):
    """Usa campo DB si disponible, sino computa desde M1."""
    db_val = b.get('bars_since_low_vr')
    if db_val is not None:
        return int(db_val)
    c = 0
    for j in range(i-1, max(i-100, -1), -1):
        vr_j = float(m1[j].get('vr') or 1.0)
        if vr_j < 0.7:
            break
        c += 1
    return c

def get_prev_bar_delta(b, m1, i):
    """Usa campo DB si disponible, sino toma bar_delta de la barra anterior."""
    db_val = b.get('prev_bar_delta')
    if db_val is not None:
        return float(db_val)
    if i > 0:
        return float(m1[i-1].get('bar_delta') or 0)
    return 0.0

# ── Signal detector (con EXP8) ────────────────────────────────────────────────

def detect_signal(sym, b):
    ses = b.get('session', '')
    if ses in ('OffHours', 'Asia'): return None
    reg = b.get('regime', '')
    if reg == 'TrendDown': return None  # EXP8

    oi    = b.get('oi_momentum'); vr = float(b.get('vr') or 0)
    obif  = float(b.get('obi_fast') or 0); eq = b.get('equal_high')
    abso  = b.get('absorption', '')
    rng   = (b['high'] - b['low']) or 1
    body  = abs(b['close'] - b['open'])
    wick_hi = b['high'] - max(b['close'], b['open'])
    is_shoot  = (wick_hi/rng > 0.45) and (body/rng < 0.40)
    is_london = ses in ('London', 'LondonNyOverlap')
    is_ny     = ses == 'NewYork'
    is_exp    = reg == 'Expansion'
    abs_ask   = abso == 'Ask'
    eq_true   = str(eq).lower() == 'true'
    oi_true   = oi is True or str(oi).lower() == 'true'

    if sym == 'BTCUSDT':
        if not is_london and not is_ny: return None
        if is_shoot and abs_ask and obif < 0: return 'btc:shoot+ask+obi'
        if is_shoot and is_london:            return 'btc:shoot+london'
    elif sym == 'ETHUSDT':
        if is_ny and oi_true and eq_true:     return 'eth:ny+oi+eq'
        if abs_ask and is_london and is_exp:  return 'eth:ask+london+exp'
    elif sym == 'SOLUSDT':
        if is_ny and vr > 4.0 and oi_true:   return 'sol:ny+vr4+oi'
        if is_ny and vr > 4.0 and eq_true:   return 'sol:ny+vr4+eq'
        if eq_true and is_london and is_exp:  return 'sol:eq+london+exp'
    elif sym == 'BNBUSDT':
        if not is_ny: return None
        if eq_true and oi_true: return 'bnb:eq+ny+oi'
        if oi_true:             return 'bnb:oi+ny'
    elif sym == 'XRPUSDT':
        if not is_ny: return None
        if eq_true and oi_true: return 'xrp:eq+ny+oi'
        if abs_ask:             return 'xrp:ask+ny'
        if oi_true:             return 'xrp:oi+ny'
    return None

def simulate(m1, entry_i, entry, stop, risk):
    target = entry - TARGET_R * risk
    cvd_s = obi_s = 0
    for k in range(1, min(FORWARD_MAX, len(m1)-entry_i)):
        mb = m1[entry_i+k]
        if mb['low'] <= target:
            return round(TARGET_R - FEE_RT*entry/risk, 4), 'TP'
        if mb['high'] >= stop:
            return round(-(1.0 + FEE_RT*entry/risk), 4), 'SL'
        cvd_s = cvd_s+1 if (mb.get('cvd_slope') or 0) > 0 else 0
        obi_s = obi_s+1 if (mb.get('obi_fast') or 0) > OBI_FLIP_THR else 0
        curr_r = (entry - mb['close']) / risk
        if cvd_s >= CVD_FLIP_BARS and obi_s >= 1 and curr_r >= MIN_PROFIT_CVD:
            return round((entry-mb['close'])/risk - FEE_RT*entry/risk, 4), 'CVD'
    last = m1[min(entry_i+FORWARD_MAX-1, len(m1)-1)]
    return round((entry-last['close'])/risk - FEE_RT*entry/risk, 4), 'EXP'

# ── Stats ──────────────────────────────────────────────────────────────────────

def stats(ts):
    if not ts: return {'n':0,'wr':0,'avgR':0}
    n = len(ts); wins = [t for t in ts if t['r'] > 0]
    return {'n':n,'wr':round(len(wins)/n*100,1),'avgR':round(sum(t['r'] for t in ts)/n,3)}

def bar_vis(ts):
    w = sum(1 for t in ts if t['r']>0); l = len(ts)-w
    return '#'*w + '.'*l

def sep(t): print(f'\n{"="*72}\n  {t}\n{"="*72}')

def print_table(rows, base_wr, base_avgr):
    print(f'  {"Bucket":<36} {"n":>4} {"WR%":>6} {"dWR":>7} {"AvgR":>8} {"dAvgR":>8}  vis')
    print('  '+'-'*90)
    for (label, ts) in rows:
        if not ts: continue
        s = stats(ts)
        dwr = s['wr']-base_wr; davgr = s['avgR']-base_avgr
        mark = ' <--' if dwr >= 8 and davgr >= 0.15 else ''
        print(f'  {label:<36} {s["n"]:>4} {s["wr"]:>5.1f}%  {dwr:>+6.1f}pp {s["avgR"]:>+7.3f}R {davgr:>+7.3f}R  {bar_vis(ts)}{mark}')

# ── Main ──────────────────────────────────────────────────────────────────────

SYMBOLS = ['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT']

print('Cargando barras M1 + pre-computando H1 ATR...')
all_trades = []

for sym in SYMBOLS:
    print(f'  {sym}...', end=' ', flush=True)
    start_ms = max(STARTS[sym], int((time.time()-DAYS*86400)*1000))
    m1 = sb_fetch(TABLES[sym], start_ms)
    if len(m1) < 300:
        print('insuficiente')
        continue

    d1_closes  = binance_d1(sym)
    h1_atr_map = build_h1_from_m1(m1)        # FIX: pre-computado completo
    m5_consec  = build_m5_cvd_index(m1)      # M5 CVD consec por ts_ms

    last_sig = -COOLDOWN
    sym_trades = []

    for i in range(20, len(m1)-FORWARD_MAX-1):
        if i - last_sig < COOLDOWN: continue
        b = m1[i]
        if not b.get('atr'): continue
        if d1_trend(b['close'], d1_closes) == 'bull': continue

        sig = detect_signal(sym, b)
        if not sig: continue

        # H1 ATR pre-computado — no hay bug de arranque
        cur_h  = b['ts_ms'] // 3_600_000
        h1_atr = h1_atr_map.get(cur_h, 0.0)
        if h1_atr <= 0: continue

        h1_high = get_h1_high(m1, i)
        stop  = h1_high + 0.3*h1_atr
        entry = b['close']
        risk  = stop - entry
        if risk <= 0: continue
        stop_pct = risk/entry*100
        if stop_pct < MIN_STOP_PCT or stop_pct > MAX_STOP_PCT: continue

        r, reason = simulate(m1, i, entry, stop, risk)

        # M5 ts_ms bucket
        ms5 = 5*60_000
        m5_ts = (b['ts_ms']//ms5)*ms5

        sym_trades.append({
            'sym':    sym,
            'sig':    sig,
            'r':      r,
            'reason': reason,
            'session': b.get('session',''),
            'regime':  b.get('regime',''),
            # Feature A: session phase (minutos desde apertura de sesion)
            'session_phase': get_session_phase(m1, i),
            # Feature B: M5 CVD consecutivo
            'm5_cvd_consec': m5_consec.get(m5_ts, 0),
            # Feature C: cvd_consec_neg (DB o computado)
            'cvd_consec_neg': get_cvd_consec_neg(b, m1, i),
            # Feature D: bars_since_low_vr (DB o computado)
            'bars_since_low_vr': get_bars_since_low_vr(b, m1, i),
            # Feature E: prev_bar_delta (DB o computado)
            'prev_bar_delta': get_prev_bar_delta(b, m1, i),
        })
        last_sig = i

    all_trades.extend(sym_trades)
    print(f'n={len(sym_trades)}')

n_total = len(all_trades)
base    = stats(all_trades)
print(f'\nTotal trades: {n_total}  WR={base["wr"]}%  AvgR={base["avgR"]:+.3f}R')
bwr = base['wr']; bavgr = base['avgR']

# ─────────────────────────────────────────────────────────────────────────────
# A. SESSION PHASE
# ─────────────────────────────────────────────────────────────────────────────
sep('A. FASE DE SESION — minutos desde apertura al entry')
print('  Hipotesis: 16-45 min (early) = señales de mayor calidad')
print()
rows_a = [
    ('0-15 min (apertura inmediata)',   [t for t in all_trades if t['session_phase'] <= 15]),
    ('16-45 min (early — CANDIDATO)',   [t for t in all_trades if 16 <= t['session_phase'] <= 45]),
    ('46-90 min (mid)',                 [t for t in all_trades if 46 <= t['session_phase'] <= 90]),
    ('91-150 min (late)',               [t for t in all_trades if 91 <= t['session_phase'] <= 150]),
    ('150+ min (cierre/agotado)',       [t for t in all_trades if t['session_phase'] > 150]),
]
print_table(rows_a, bwr, bavgr)

# ─────────────────────────────────────────────────────────────────────────────
# B. M5 CVD CONSECUTIVO
# ─────────────────────────────────────────────────────────────────────────────
sep('B. M5 CVD CONSECUTIVO NEGATIVO — barras M5 con CVD < 0')
print('  Hipotesis: 1-2 barras = sweet spot; 0 = sin contexto; 5+ = agotado')
print()
rows_b = [
    ('0 barras (sin momentum previo)',   [t for t in all_trades if t['m5_cvd_consec'] == 0]),
    ('1-2 barras (momentum iniciando)', [t for t in all_trades if 1 <= t['m5_cvd_consec'] <= 2]),
    ('3-4 barras (establecido)',        [t for t in all_trades if 3 <= t['m5_cvd_consec'] <= 4]),
    ('5-7 barras (fuerte)',             [t for t in all_trades if 5 <= t['m5_cvd_consec'] <= 7]),
    ('8+ barras (agotado)',             [t for t in all_trades if t['m5_cvd_consec'] >= 8]),
]
print_table(rows_b, bwr, bavgr)

# ─────────────────────────────────────────────────────────────────────────────
# C. CVD CONSEC NEG (M1 o DB)
# ─────────────────────────────────────────────────────────────────────────────
sep('C. CVD CONSEC NEG M1 — barras M1 consecutivas con CVD < 0')
print()
rows_c = [
    ('0 barras',               [t for t in all_trades if t['cvd_consec_neg'] == 0]),
    ('1-3 barras',             [t for t in all_trades if 1 <= t['cvd_consec_neg'] <= 3]),
    ('4-7 barras (medio)',     [t for t in all_trades if 4 <= t['cvd_consec_neg'] <= 7]),
    ('8-15 barras (fuerte)',   [t for t in all_trades if 8 <= t['cvd_consec_neg'] <= 15]),
    ('16+ barras (agotado)',   [t for t in all_trades if t['cvd_consec_neg'] >= 16]),
]
print_table(rows_c, bwr, bavgr)

# ─────────────────────────────────────────────────────────────────────────────
# D. BARS SINCE LOW VR (post-compresion)
# ─────────────────────────────────────────────────────────────────────────────
sep('D. BARRAS DESDE ULTIMA COMPRESION (bars_since_low_vr)')
print('  Hipotesis: 1-5 barras (recien saliendo de coil) = mejor entry')
print()
rows_d = [
    ('1-5 barras (post-compresion)',   [t for t in all_trades if 1 <= t['bars_since_low_vr'] <= 5]),
    ('6-15 barras (expansion activa)', [t for t in all_trades if 6 <= t['bars_since_low_vr'] <= 15]),
    ('16-40 barras (momentum)',        [t for t in all_trades if 16 <= t['bars_since_low_vr'] <= 40]),
    ('41+ barras (sin compresion)',    [t for t in all_trades if t['bars_since_low_vr'] >= 41]),
    ('0 barras (compresion actual)',   [t for t in all_trades if t['bars_since_low_vr'] == 0]),
]
print_table(rows_d, bwr, bavgr)

# ─────────────────────────────────────────────────────────────────────────────
# E. PREV BAR DELTA
# ─────────────────────────────────────────────────────────────────────────────
sep('E. PREV BAR DELTA — delta de la barra M1 anterior a la senal')
print()
def delta_bk(v):
    if v < -500:  return 'muy negativo (<-500)'
    if v < -100:  return 'negativo (-500 a -100)'
    if v < 0:     return 'leve negativo (-100 a 0)'
    if v < 100:   return 'neutro (0 a 100)'
    if v < 500:   return 'positivo (100 a 500)'
    return               'muy positivo (>500)'

by_delta = defaultdict(list)
for t in all_trades:
    by_delta[delta_bk(t['prev_bar_delta'])].append(t)

order_d = ['muy negativo (<-500)','negativo (-500 a -100)','leve negativo (-100 a 0)',
           'neutro (0 a 100)','positivo (100 a 500)','muy positivo (>500)']
rows_e = [(k, by_delta.get(k,[])) for k in order_d]
print_table(rows_e, bwr, bavgr)

# ─────────────────────────────────────────────────────────────────────────────
# COMBINACIONES — cruce de los mejores features
# ─────────────────────────────────────────────────────────────────────────────
sep('COMBINACIONES — cruce de los mejores features (n >= 8)')

combos = [
    ('early + m5_cvd 1-2',
     [t for t in all_trades if 16<=t['session_phase']<=45 and 1<=t['m5_cvd_consec']<=2]),
    ('early + m5_cvd 1-4',
     [t for t in all_trades if 16<=t['session_phase']<=45 and 1<=t['m5_cvd_consec']<=4]),
    ('early (<=90 min)',
     [t for t in all_trades if t['session_phase'] <= 90]),
    ('early + cvd_neg 1-3',
     [t for t in all_trades if 16<=t['session_phase']<=45 and 1<=t['cvd_consec_neg']<=3]),
    ('m5_cvd 1-2 + prev_delta < 0',
     [t for t in all_trades if 1<=t['m5_cvd_consec']<=2 and t['prev_bar_delta']<0]),
    ('m5_cvd 1-2 + post-compresion (<=15 bars)',
     [t for t in all_trades if 1<=t['m5_cvd_consec']<=2 and t['bars_since_low_vr']<=15]),
    ('early + cvd_neg 1+ + prev_delta < 0',
     [t for t in all_trades if 16<=t['session_phase']<=45 and t['cvd_consec_neg']>=1 and t['prev_bar_delta']<0]),
    ('sin early (>90 min) — cuantos son malos',
     [t for t in all_trades if t['session_phase'] > 90]),
    ('m5_cvd 0 (sin momentum) — baseline calidad',
     [t for t in all_trades if t['m5_cvd_consec'] == 0]),
    ('m5_cvd 5+ (agotado)',
     [t for t in all_trades if t['m5_cvd_consec'] >= 5]),
]

print(f'  {"Combinacion":<46} {"n":>4} {"n%":>5} {"WR":>6} {"dWR":>7} {"AvgR":>8} {"dAvgR":>8}  vis')
print('  '+'-'*105)

for label, ts in sorted(combos, key=lambda x: -stats(x[1])['avgR'] if stats(x[1])['n']>=8 else -99):
    s = stats(ts)
    if s['n'] < 5: continue
    dwr   = s['wr']   - bwr
    davgr = s['avgR'] - bavgr
    nret  = s['n'] / n_total if n_total else 0
    mark  = ' <-- EXP10' if dwr >= 8 and davgr >= 0.15 and nret >= 0.15 else ''
    print(f'  {label:<46} {s["n"]:>4} {nret:>4.0%} {s["wr"]:>5.1f}%  {dwr:>+6.1f}pp {s["avgR"]:>+7.3f}R {davgr:>+7.3f}R  {bar_vis(ts)}{mark}')

print()
print(f'  Baseline: n={n_total} WR={bwr}% AvgR={bavgr:+.3f}R')
print()
print('  CRITERIO EXP10: dWR >= +8pp AND dAvgR >= +0.15R AND retencion >= 15%')
print()
