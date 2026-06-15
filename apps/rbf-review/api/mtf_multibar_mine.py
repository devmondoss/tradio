#!/usr/bin/env python3
"""
MTF Multi-Bar Context Mining — M1 vs M5 vs M15
================================================
Entry signals siguen siendo M1 (mismo detector, mismo stop).
El CONTEXTO pre-senal se mide en M5 y M15 agregados desde M1.

M1 = 60s de ruido. M5 = estructura de 25 min. M15 = estructura de 75 min.

Compara los mismos trades con contexto computado en 3 timeframes:
  - CTX_M1:  barras M1 crudas (baseline del analisis anterior)
  - CTX_M5:  agrega 5 M1 en una barra M5
  - CTX_M15: agrega 15 M1 en una barra M15

Features por timeframe:
  - cvd_consec_neg: barras TF consecutivas con CVD acumulado < 0
  - cum_delta_3:    delta acumulado de las 3 barras TF previas
  - vr_max_5:       VR maximo en las 5 barras TF previas (zona de expansion)
  - session_phase:  minutos desde inicio de sesion (igual en todos los TF)
"""
import json, os, sys, time, urllib.request, urllib.parse
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from pathlib import Path
from collections import defaultdict

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
            'stacked_imb,thin_above,ask_wall,equal_high,cvd_divergence,obi_fast')

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
    except:
        return []

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

def atr_h1(h1_history):
    if not h1_history: return 0.0
    return sum(c['high']-c['low'] for c in h1_history) / len(h1_history)

# ── Agregar M1 en TF superior ─────────────────────────────────────────────────

def build_higher_tf(m1, tf_minutes):
    """Agrega barras M1 en velas de tf_minutes minutos.
    Retorna lista de dicts con cvd_slope (promedio), bar_delta (suma), vr (max), session.
    """
    ms_per_bar = tf_minutes * 60_000
    buckets = defaultdict(list)
    for b in m1:
        bucket = (b['ts_ms'] // ms_per_bar) * ms_per_bar
        buckets[bucket].append(b)

    bars = []
    for ts in sorted(buckets):
        bs = buckets[ts]
        cvd_vals   = [float(b.get('cvd_slope') or 0) for b in bs]
        delta_vals = [float(b.get('bar_delta') or 0) for b in bs]
        vr_vals    = [float(b.get('vr') or 1.0)       for b in bs]
        obi_vals   = [float(b.get('obi_l5') or 0)     for b in bs]
        bars.append({
            'ts_ms':     ts,
            'cvd_slope': sum(cvd_vals) / len(cvd_vals),  # CVD slope promedio
            'cum_delta': sum(delta_vals),                  # delta acumulado
            'vr_max':    max(vr_vals),                     # maximo VR
            'obi_mean':  sum(obi_vals) / len(obi_vals),   # OBI medio
            'session':   bs[-1].get('session',''),
            'm1_count':  len(bs),
        })
    return bars

def make_tf_index(tf_bars):
    """Indice ts_ms -> posicion en la lista para busqueda rapida."""
    return {b['ts_ms']: i for i, b in enumerate(tf_bars)}

# ── Context features en un TF dado ───────────────────────────────────────────

def compute_tf_context(tf_bars, tf_idx, signal_ts_ms, tf_minutes, m1_i, m1):
    """Computa features de contexto para la senal en signal_ts_ms usando tf_bars."""
    ms_per_bar = tf_minutes * 60_000
    bar_ts = (signal_ts_ms // ms_per_bar) * ms_per_bar
    pos = tf_idx.get(bar_ts)

    if pos is None or pos < 3:
        return None  # no hay suficiente contexto

    # Barras TF anteriores a la senal (excluye la barra actual, incompleta)
    prev = tf_bars[max(0, pos-10):pos]

    if not prev:
        return None

    # 1. CVD consecutivo negativo
    cvd_consec = 0
    for b in reversed(prev):
        if b['cvd_slope'] < 0:
            cvd_consec += 1
        else:
            break

    # 2. Delta acumulado de las 3 barras TF previas
    cum_delta_3 = sum(b['cum_delta'] for b in prev[-3:])

    # 3. VR maximo de las 5 barras TF previas (zona de expansion reciente)
    vr_max_5 = max((b['vr_max'] for b in prev[-5:]), default=1.0)

    # 4. OBI medio de las 3 barras TF previas
    obi_mean_3 = sum(b['obi_mean'] for b in prev[-3:]) / len(prev[-3:]) if prev else 0

    # 5. Barra TF anterior: delta positivo o negativo
    prev_bar_delta_tf = prev[-1]['cum_delta'] if prev else 0

    # 6. Minutos dentro de la sesion (desde M1 igual para todos los TF)
    cur_ses = m1[m1_i].get('session', '')
    session_phase = 0
    for j in range(m1_i-1, max(m1_i-180, -1), -1):
        if m1[j].get('session','') == cur_ses:
            session_phase += 1
        else:
            break

    return {
        'cvd_consec':       cvd_consec,
        'cum_delta_3':      cum_delta_3,
        'vr_max_5':         vr_max_5,
        'obi_mean_3':       obi_mean_3,
        'prev_delta_tf':    prev_bar_delta_tf,
        'session_phase':    session_phase,
    }

# ── Signal detector (con EXP8) ────────────────────────────────────────────────

def detect_signal(sym, b):
    ses   = b.get('session','')
    if ses in ('OffHours','Asia'): return None
    reg   = b.get('regime','')
    if reg == 'TrendDown': return None

    oi    = b.get('oi_momentum'); vr = b.get('vr') or 0
    obif  = b.get('obi_fast') or 0; eq = b.get('equal_high')
    abso  = b.get('absorption','')
    rng   = (b['high']-b['low']) or 1
    body  = abs(b['close']-b['open'])
    wick_hi = b['high'] - max(b['close'],b['open'])
    is_shoot = (wick_hi/rng > 0.45) and (body/rng < 0.40)
    is_london = ses in ('London','LondonNyOverlap'); is_ny = ses == 'NewYork'
    is_exp = reg == 'Expansion'; abs_ask = abso == 'Ask'
    eq_true = str(eq).lower() == 'true'
    oi_true = oi is True or str(oi).lower() == 'true'

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
    target = entry - TARGET_R * risk; cvd_s = obi_s = 0
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

# ── Stats helpers ─────────────────────────────────────────────────────────────

def sep(t): print(f'\n{"="*70}\n  {t}\n{"="*70}')
def stats(ts):
    if not ts: return {'n':0,'wr':0,'avgR':0}
    n = len(ts); wins = [t for t in ts if t['r'] > 0]
    return {'n':n,'wr':round(len(wins)/n*100,1),'avgR':round(sum(t['r'] for t in ts)/n,3)}

def bar_vis(ts):
    w = sum(1 for t in ts if t['r']>0); l = len(ts)-w
    return '#'*w + '.'*l

def print_bucket_table(title, buckets_ordered, trades_by_bucket):
    print(f'  {"Bucket":<32} {"n":>4} {"WR%":>6} {"AvgR":>8}  vis')
    print('  ' + '-'*70)
    for bk in buckets_ordered:
        ts = trades_by_bucket.get(bk, [])
        if not ts: continue
        s = stats(ts)
        print(f'  {bk:<32} {s["n"]:>4} {s["wr"]:>5.1f}%  {s["avgR"]:>+7.3f}R  {bar_vis(ts)}')

# ── Main ──────────────────────────────────────────────────────────────────────

SYMBOLS = ['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT']
TFS = [('M5', 5), ('M15', 15)]

print('Cargando barras M1...')
all_trades = []

for sym in SYMBOLS:
    print(f'  {sym}...', end=' ', flush=True)
    start_ms = max(STARTS[sym], int((time.time()-DAYS*86400)*1000))
    m1 = sb_fetch(TABLES[sym], start_ms)
    if len(m1) < 300: print('insuficiente'); continue

    d1_closes = binance_d1(sym)

    # Pre-construir M5 y M15 para todo el dataset
    tf_data = {}
    for tf_name, tf_min in TFS:
        bars = build_higher_tf(m1, tf_min)
        tf_data[tf_name] = (bars, make_tf_index(bars), tf_min)

    # H1 ATR incremental
    h1_history = []; h1_cur = {}

    last_sig = -COOLDOWN
    sym_trades = []

    for i in range(20, len(m1)-FORWARD_MAX-1):
        if i - last_sig < COOLDOWN: continue
        b = m1[i]
        if not b.get('atr'): continue
        if d1_trend(b['close'], d1_closes) == 'bull': continue

        sig = detect_signal(sym, b)
        if not sig: continue

        # H1 bucket
        bar_h = b['ts_ms']//3_600_000
        if not h1_cur or h1_cur.get('h') != bar_h:
            if h1_cur:
                h1_history.append(h1_cur)
                if len(h1_history) > 14: h1_history.pop(0)
            h1_cur = {'h':bar_h,'high':b['high'],'low':b['low']}
        else:
            h1_cur['high'] = max(h1_cur['high'], b['high'])
            h1_cur['low']  = min(h1_cur['low'],  b['low'])

        h1_high = h1_cur['high']; h1_atr = atr_h1(h1_history)
        if h1_atr <= 0: continue

        stop = h1_high + 0.3*h1_atr; entry = b['close']; risk = stop - entry
        if risk <= 0: continue
        stop_pct = risk/entry*100
        if stop_pct < MIN_STOP_PCT or stop_pct > MAX_STOP_PCT: continue

        r, reason = simulate(m1, i, entry, stop, risk)

        # Contexto en cada TF
        ctx = {'m5': None, 'm15': None}
        for tf_name, (tf_bars, tf_idx, tf_min) in tf_data.items():
            ctx[tf_name.lower()] = compute_tf_context(
                tf_bars, tf_idx, b['ts_ms'], tf_min, i, m1)

        if ctx['m5'] is None and ctx['m15'] is None:
            continue  # no hay contexto — descartar

        sym_trades.append({
            'sym': sym, 'sig': sig, 'r': r, 'reason': reason,
            'session': b.get('session',''), 'regime': b.get('regime',''),
            'm5': ctx['m5'], 'm15': ctx['m15'],
        })
        last_sig = i

    all_trades.extend(sym_trades)
    print(f'n={len(sym_trades)}')

# Filtrar trades con contexto M5 y M15 disponible
trades_m5  = [t for t in all_trades if t['m5']  is not None]
trades_m15 = [t for t in all_trades if t['m15'] is not None]

print(f'\nTotal trades con contexto: {len(all_trades)}')
print(f'  Con M5:  {len(trades_m5)}')
print(f'  Con M15: {len(trades_m15)}')
base = stats(all_trades)
print(f'  Baseline: WR={base["wr"]}% AvgR={base["avgR"]:+.3f}R')

# ─────────────────────────────────────────────────────────────────────────────
# ANALISIS 1 — CVD CONSECUTIVO por TF
# ─────────────────────────────────────────────────────────────────────────────
for tf_name, trades_tf, label in [
    ('M5',  trades_m5,  'M5  (1 barra = 5 min de estructura)'),
    ('M15', trades_m15, 'M15 (1 barra = 15 min de estructura)'),
]:
    sep(f'CVD CONSECUTIVO NEGATIVO — {label}')
    print(f'  (barras {tf_name} seguidas con CVD acumulado < 0 antes de la senal)')
    by = defaultdict(list)
    def cvd_bk(v):
        if v == 0:  return '0 barras (sin momentum)'
        if v <= 2:  return '1-2 barras'
        if v <= 4:  return '3-4 barras (establecido)'
        if v <= 7:  return '5-7 barras (fuerte)'
        return             '8+ barras (extendido/agotado)'
    for t in trades_tf:
        by[cvd_bk(t[tf_name.lower()]['cvd_consec'])].append(t)
    order = ['0 barras (sin momentum)','1-2 barras','3-4 barras (establecido)',
             '5-7 barras (fuerte)','8+ barras (extendido/agotado)']
    print_bucket_table('', order, by)

# ─────────────────────────────────────────────────────────────────────────────
# ANALISIS 2 — DELTA ACUMULADO DE LAS 3 BARRAS TF PREVIAS
# ─────────────────────────────────────────────────────────────────────────────
for tf_name, trades_tf, label in [
    ('M5',  trades_m5,  'M5  — delta acumulado de los 15 min previos'),
    ('M15', trades_m15, 'M15 — delta acumulado de los 45 min previos'),
]:
    sep(f'DELTA ACUMULADO 3 BARRAS PREVIAS — {label}')
    print(f'  (suma del delta de las 3 barras {tf_name} anteriores a la senal)')
    by = defaultdict(list)
    def delta_bk(v):
        if v < -500:  return 'muy negativo (<-500) — vendedores dominando'
        if v < -100:  return 'negativo (-500 a -100)'
        if v <  0:    return 'leve negativo (-100 a 0)'
        if v <  100:  return 'neutro (0 a +100)'
        if v <  500:  return 'positivo (+100 a +500)'
        return               'muy positivo (>+500) — compradores dominando'
    for t in trades_tf:
        by[delta_bk(t[tf_name.lower()]['cum_delta_3'])].append(t)
    order = ['muy negativo (<-500) — vendedores dominando','negativo (-500 a -100)',
             'leve negativo (-100 a 0)','neutro (0 a +100)',
             'positivo (+100 a +500)','muy positivo (>+500) — compradores dominando']
    print_bucket_table('', order, by)

# ─────────────────────────────────────────────────────────────────────────────
# ANALISIS 3 — FASE DE SESION (igual para todos los TF)
# ─────────────────────────────────────────────────────────────────────────────
sep('FASE DE SESION al entry — minutos desde apertura')
by = defaultdict(list)
def phase_bk(v):
    if v <= 15:  return '0-15 min (apertura)'
    if v <= 45:  return '16-45 min (early)'
    if v <= 90:  return '46-90 min (mid)'
    if v <= 150: return '91-150 min (late)'
    return              '150+ min (cierre)'
for t in trades_m5:
    by[phase_bk(t['m5']['session_phase'])].append(t)
order = ['0-15 min (apertura)','16-45 min (early)','46-90 min (mid)',
         '91-150 min (late)','150+ min (cierre)']
print_bucket_table('', order, by)

# ─────────────────────────────────────────────────────────────────────────────
# ANALISIS 4 — VR MAXIMO EN BARRAS TF PREVIAS (zona de expansion)
# ─────────────────────────────────────────────────────────────────────────────
for tf_name, trades_tf, label in [
    ('M5',  trades_m5,  'M5'),
    ('M15', trades_m15, 'M15'),
]:
    sep(f'VR MAXIMO 5 BARRAS PREVIAS — {tf_name}')
    print(f'  (si hubo expansion de volumen reciente antes de la senal)')
    by = defaultdict(list)
    def vr_bk(v):
        if v < 1.5:  return '<1.5 (compresion — coil activo)'
        if v < 2.5:  return '1.5-2.5 (volumen normal)'
        if v < 4.0:  return '2.5-4.0 (expansion moderada)'
        return              '4.0+ (expansion fuerte)'
    for t in trades_tf:
        by[vr_bk(t[tf_name.lower()]['vr_max_5'])].append(t)
    order = ['<1.5 (compresion — coil activo)','1.5-2.5 (volumen normal)',
             '2.5-4.0 (expansion moderada)','4.0+ (expansion fuerte)']
    print_bucket_table('', order, by)

# ─────────────────────────────────────────────────────────────────────────────
# ANALISIS 5 — COMPARATIVO FINAL: mejores filtros M5 vs M15
# ─────────────────────────────────────────────────────────────────────────────
sep('COMPARATIVO — Mejores filtros por TF (n >= 8, ordenado por AvgR)')
print(f'  {"Filtro":<38} {"n":>4} {"n%":>5} {"WR":>6} {"dWR":>7} {"AvgR":>8} {"dAvgR":>8}')
print('  ' + '-'*88)

base_wr  = base['wr']
base_avr = base['avgR']
base_n   = base['n']

candidates = []

for tf_name, trades_tf in [('M5', trades_m5), ('M15', trades_m15)]:
    key = tf_name.lower()
    tf_candidates = [
        (f'{tf_name}: cvd 3-4 barras',  [t for t in trades_tf if 3 <= t[key]['cvd_consec'] <= 4]),
        (f'{tf_name}: cvd 3-7 barras',  [t for t in trades_tf if 3 <= t[key]['cvd_consec'] <= 7]),
        (f'{tf_name}: cum_delta < -100', [t for t in trades_tf if t[key]['cum_delta_3'] < -100]),
        (f'{tf_name}: cum_delta < -500', [t for t in trades_tf if t[key]['cum_delta_3'] < -500]),
        (f'{tf_name}: early session',    [t for t in trades_tf if 16 <= t[key]['session_phase'] <= 45]),
        (f'{tf_name}: vr_max < 1.5',     [t for t in trades_tf if t[key]['vr_max_5'] < 1.5]),
        (f'{tf_name}: cvd3+ + delta<0',  [t for t in trades_tf if t[key]['cvd_consec']>=3 and t[key]['cum_delta_3']<0]),
        (f'{tf_name}: cvd3+ + early',    [t for t in trades_tf if t[key]['cvd_consec']>=3 and 16<=t[key]['session_phase']<=90]),
        (f'{tf_name}: delta<0 + early',  [t for t in trades_tf if t[key]['cum_delta_3']<0 and 16<=t[key]['session_phase']<=90]),
    ]
    candidates.extend(tf_candidates)

candidates.sort(key=lambda x: -stats(x[1])['avgR'] if stats(x[1])['n']>=8 else -99)

for label, ts in candidates:
    s = stats(ts)
    if s['n'] < 8: continue
    dwr   = s['wr']   - base_wr
    davgr = s['avgR'] - base_avr
    nret  = s['n'] / base_n
    mark  = ' <-- CANDIDATO' if dwr >= 5 and davgr >= 0.10 and nret >= 0.30 else ''
    print(f'  {label:<38} {s["n"]:>4} {nret:>4.0%} {s["wr"]:>5.1f}% {dwr:>+6.1f}pp {s["avgR"]:>+7.3f}R {davgr:>+7.3f}R{mark}')

print()
print(f'  Baseline (con EXP8): n={base_n} WR={base_wr}% AvgR={base_avr:+.3f}R')
print()
