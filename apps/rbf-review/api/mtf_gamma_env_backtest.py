#!/usr/bin/env python3
"""
MTF Gamma Environment Filter Backtest
=======================================
Toma el backtest MTF shorts base y agrega dos capas de contexto macro:

  1. Funding Rate Regime  — clasifica cada barra en ExtremeLong/ElevatedLong/
                            Neutral/ElevatedShort/ExtremeShort usando ventana
                            rolling de 21 muestras (7 dias a 8h).

  2. OI Absolute Trend    — compara OI actual vs OI hace 4h.
                            Growing / Flat / Declining.

Corre 4 variantes y compara:
  A. Base (sin filtro extra)
  B. Block ExtremeShort funding (squeezes inminentes → no shortear)
  C. Solo cuando OI no explota hacia arriba (Flat o Declining)
  D. A+B combinado

Output: tabla de comparacion + breakdown por regimen de funding y zona OI.
"""
import json, os, sys, time, urllib.request, urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).parent.parent.parent.parent
_env = {}
for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        _env[k.strip()] = v.strip().strip('"').strip("'")

SUPABASE_URL = _env.get('SUPABASE_URL', os.environ.get('SUPABASE_URL', ''))
SUPABASE_KEY = _env.get('SUPABASE_KEY', os.environ.get('SUPABASE_KEY', ''))

CAPITAL_INIT = 500.0
RISK_PCT     = 0.02
FEE_RT       = 0.0007
FORWARD_M1   = 1200
MIN_STOP_PCT = 0.30
MAX_STOP_PCT = 0.75
COOLDOWN_M1  = 30
CVD_FLIP_BARS  = 5
OBI_FLIP_THR   = 0.15
MIN_PROFIT_CVD = 1.0

SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT']
TABLES  = {'BTCUSDT':'btc_bars','ETHUSDT':'eth_bars','SOLUSDT':'sol_bars',
           'BNBUSDT':'bnb_bars','XRPUSDT':'xrp_bars'}
STARTS  = {'BTCUSDT':1780676700000,'ETHUSDT':1780756260000,
           'SOLUSDT':1780756260000,'BNBUSDT':1780756260000,'XRPUSDT':1780756260000}
TICK_SZ = {'BTCUSDT':0.1,'ETHUSDT':0.01,'SOLUSDT':0.001,'BNBUSDT':0.01,'XRPUSDT':0.0001}

BAR_COLS = ('ts_ms,open,high,low,close,volume,atr,session,cvd_slope,obi_l5,'
            'vwap,vr,oi_momentum,bar_delta,regime,dz,absorption,'
            'swing_low_50,swing_high_50,prev_day_high,prev_day_low,'
            'stacked_imb,thin_above,ask_wall,equal_high,cvd_divergence,obi_fast')

# ── Data fetching ──────────────────────────────────────────────────────────────

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

def binance_get(url):
    try:
        req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
        return json.loads(urllib.request.urlopen(req, timeout=15).read())
    except Exception as e:
        print(f'[WARN] Binance fetch failed: {url[:80]} → {e}', file=sys.stderr)
        return []

def fetch_funding_history(symbol, limit=200):
    """Retorna lista [{ts_ms, rate}] ordenada asc. Cada sample = 1 periodo de 8h."""
    url = (f'https://fapi.binance.com/fapi/v1/fundingRate'
           f'?symbol={symbol}&limit={limit}')
    data = binance_get(url)
    if not data:
        return []
    return [{'ts_ms': int(d['fundingTime']), 'rate': float(d['fundingRate'])}
            for d in data]

def fetch_oi_history(symbol, period='1h', limit=500):
    """Retorna lista [{ts_ms, oi_usd}] ordenada asc."""
    url = (f'https://fapi.binance.com/futures/data/openInterestHist'
           f'?symbol={symbol}&period={period}&limit={limit}')
    data = binance_get(url)
    if not data:
        return []
    return [{'ts_ms': int(d['timestamp']), 'oi_usd': float(d['sumOpenInterestValue'])}
            for d in data]

# ── Funding regime (replica la logica de FundingTracker Rust) ──────────────────

FUNDING_WINDOW = 21  # 7 dias * 3 periodos de 8h

def classify_funding_regime(samples_window, current_rate):
    """
    samples_window: lista de rates (float) de los ultimos FUNDING_WINDOW periodos.
    current_rate: el rate actual.
    Retorna: 'ExtremeLong'|'ElevatedLong'|'Neutral'|'ElevatedShort'|'ExtremeShort'
    """
    if not samples_window:
        return 'Neutral'
    n = len(samples_window)
    avg = sum(samples_window) / n
    rank = sum(1 for r in samples_window if r < current_rate)
    pct  = rank / n * 100.0
    above_avg = avg > 0 and current_rate >= avg * 1.3

    if current_rate > 0 and pct > 85.0:
        return 'ExtremeLong'
    if current_rate > 0 and (pct > 65.0 or above_avg):
        return 'ElevatedLong'
    if current_rate < 0 and pct < 15.0:
        return 'ExtremeShort'
    if current_rate < 0 and pct < 35.0:
        return 'ElevatedShort'
    return 'Neutral'

def build_funding_series(funding_hist, m1_bars):
    """
    Para cada barra M1, calcula el regimen de funding vigente.
    Retorna dict {ts_ms: regime_str}.
    """
    if not funding_hist:
        return {}
    # Ordenar por ts
    sorted_f = sorted(funding_hist, key=lambda x: x['ts_ms'])
    result = {}
    f_idx  = 0
    n_f    = len(sorted_f)

    for b in m1_bars:
        ts = b['ts_ms']
        # Avanzar hasta el ultimo funding sample <= ts
        while f_idx + 1 < n_f and sorted_f[f_idx + 1]['ts_ms'] <= ts:
            f_idx += 1
        # Ventana rolling de FUNDING_WINDOW muestras antes de este punto
        window_start = max(0, f_idx - FUNDING_WINDOW + 1)
        window = [s['rate'] for s in sorted_f[window_start:f_idx + 1]]
        if not window:
            result[ts] = 'Neutral'
        else:
            current = sorted_f[f_idx]['rate']
            result[ts] = classify_funding_regime(window[:-1], current)  # window sin current
    return result

# ── OI trend ──────────────────────────────────────────────────────────────────

OI_LOOKBACK_H = 4   # comparar OI actual vs 4h atras
OI_GROW_THR   = 0.015   # +1.5% en 4h = Growing
OI_DEC_THR    = -0.005  # -0.5% en 4h = Declining

def build_oi_series(oi_hist, m1_bars):
    """
    Para cada barra M1, calcula la tendencia de OI absoluto:
    'Growing' | 'Flat' | 'Declining'
    Retorna dict {ts_ms: oi_trend_str}.
    """
    if not oi_hist:
        return {}
    sorted_oi = sorted(oi_hist, key=lambda x: x['ts_ms'])
    result = {}
    oi_idx = 0
    n_oi   = len(sorted_oi)

    for b in m1_bars:
        ts = b['ts_ms']
        while oi_idx + 1 < n_oi and sorted_oi[oi_idx + 1]['ts_ms'] <= ts:
            oi_idx += 1

        # Buscar la muestra de 4h atras
        lookback_ms = OI_LOOKBACK_H * 3_600_000
        target_past = ts - lookback_ms
        past_idx = oi_idx
        while past_idx > 0 and sorted_oi[past_idx]['ts_ms'] > target_past:
            past_idx -= 1

        oi_now  = sorted_oi[oi_idx]['oi_usd']
        oi_past = sorted_oi[past_idx]['oi_usd']

        if oi_past <= 0:
            result[ts] = 'Flat'
            continue

        chg = (oi_now - oi_past) / oi_past
        if chg >= OI_GROW_THR:
            result[ts] = 'Growing'
        elif chg <= OI_DEC_THR:
            result[ts] = 'Declining'
        else:
            result[ts] = 'Flat'

    return result

# ── Helpers (identicos al backtest base) ─────────────────────────────────────

def ema(vals, p):
    k = 2/(p+1); r = [vals[0]]
    for v in vals[1:]: r.append(v*k+r[-1]*(1-k))
    return r

def atr_series(bars, period=14):
    trs = []
    for i,b in enumerate(bars):
        pc = bars[i-1]['close'] if i>0 else b['open']
        trs.append(max(b['high']-b['low'], abs(b['high']-pc), abs(b['low']-pc)))
    atrs = [sum(trs[:period])/period]*period
    k = 1/period
    for i in range(period,len(trs)):
        atrs.append(atrs[-1]*(1-k)+trs[i]*k)
    return atrs

def build_h1(m1):
    buckets = defaultdict(list)
    for b in m1:
        buckets[(b['ts_ms']//3_600_000)*3_600_000].append(b)
    h1 = []
    for ts in sorted(buckets):
        bs = buckets[ts]
        h1.append({
            'ts_ms':  ts,
            'open':   bs[0]['open'],
            'high':   max(b['high'] for b in bs),
            'low':    min(b['low']  for b in bs),
            'close':  bs[-1]['close'],
        })
    return h1

def d1_trend(ts_ms, d1_bars, d1_ema20):
    day_ms = (ts_ms//86_400_000)*86_400_000
    for i in range(len(d1_bars)-1,-1,-1):
        if d1_bars[i]['ts_ms'] <= day_ms:
            c = d1_bars[i]['close']; e = d1_ema20[i]
            if c > e*1.005: return 'bull'
            if c < e*0.995: return 'bear'
            return 'neutral'
    return 'unknown'

def binance_klines(symbol, interval, limit=500):
    url = (f'https://fapi.binance.com/fapi/v1/klines'
           f'?symbol={symbol}&interval={interval}&limit={limit}')
    data = binance_get(url)
    if not data: return []
    return [{'ts_ms':int(d[0]),'open':float(d[1]),'high':float(d[2]),
             'low':float(d[3]),'close':float(d[4]),'volume':float(d[5])} for d in data]

def detect_m1_signal(sym, m1, i):
    b   = m1[i]
    ses = b.get('session','')
    oi  = b.get('oi_momentum')
    obi = b.get('obi_l5') or 0
    obif= b.get('obi_fast') or 0
    vr  = b.get('vr') or 0
    reg = b.get('regime','')
    eq  = b.get('equal_high')
    abso= b.get('absorption','')
    vpin= b.get('vpin') or 0
    rng  = (b['high']-b['low']) or 1
    body = abs(b['close']-b['open'])
    wick_hi = b['high'] - max(b['close'],b['open'])
    is_shoot  = (wick_hi/rng > 0.45) and (body/rng < 0.40)
    is_london = ses in ('London','LondonNyOverlap')
    is_ny     = ses == 'NewYork'
    eq_true   = str(eq).lower() == 'true'
    is_exp    = reg == 'Expansion'
    abs_ask   = abso == 'Ask'

    if ses in ('OffHours', 'Asia'):
        return False, ''

    if sym == 'BTCUSDT':
        if not (is_london or is_ny): return False, ''
        if is_shoot and abs_ask and obif < 0:   return True, 'btc:shoot+ask+obi'
        if is_shoot and is_london:              return True, 'btc:shoot+london'
    elif sym == 'ETHUSDT':
        if is_ny and (oi is True) and eq_true:  return True, 'eth:ny+oi+eq'
        if abs_ask and is_london and is_exp:    return True, 'eth:ask+london+exp'
        if abs_ask and vpin > 0.6 and is_ny:   return True, 'eth:ask+vpin+ny'
    elif sym == 'SOLUSDT':
        if is_ny and vr > 4.0 and (oi is True): return True, 'sol:ny+vr4+oi'
        if is_ny and vr > 4.0 and eq_true:      return True, 'sol:ny+vr4+eq'
        if eq_true and is_london and is_exp:    return True, 'sol:eq+london+exp'
    elif sym == 'BNBUSDT':
        if not is_ny: return False, ''
        if eq_true and (oi is True):            return True, 'bnb:eq+ny+oi'
        if oi is True:                          return True, 'bnb:oi+ny'
    elif sym == 'XRPUSDT':
        if not is_ny: return False, ''
        if eq_true and (oi is True):            return True, 'xrp:eq+ny+oi'
        if abs_ask:                             return True, 'xrp:ask+ny'
        if oi is True:                          return True, 'xrp:oi+ny'

    return False, ''

def simulate_short(m1, entry_i, entry, stop, risk, target_r=2.5):
    target_price = entry - target_r * risk
    n = len(m1)
    cvd_pos_streak = 0
    obi_pos_streak = 0

    for k in range(1, min(FORWARD_M1, n - entry_i)):
        mb = m1[entry_i + k]
        h, l = mb['high'], mb['low']

        if l <= target_price:
            fee_r = FEE_RT * entry / risk
            return round(target_r - fee_r, 4), 'TP'
        if h >= stop:
            fee_r = FEE_RT * entry / risk
            return round(-(1.0 + fee_r), 4), 'SL'

        curr_r    = (entry - mb['close']) / risk
        cvd_slope = mb.get('cvd_slope') or 0
        obi_fast  = mb.get('obi_fast') or mb.get('obi_l5') or 0

        if cvd_slope > 0: cvd_pos_streak += 1
        else:             cvd_pos_streak  = 0
        if obi_fast > OBI_FLIP_THR: obi_pos_streak += 1
        else:                        obi_pos_streak  = 0

        if (cvd_pos_streak >= CVD_FLIP_BARS
                and obi_pos_streak >= 1
                and curr_r >= MIN_PROFIT_CVD):
            fee_r = FEE_RT * entry / risk
            return round((entry - mb['close']) / risk - fee_r, 4), 'CVD'

    last  = m1[min(entry_i + FORWARD_M1 - 1, n - 1)]
    fee_r = FEE_RT * entry / risk
    return round((entry - last['close']) / risk - fee_r, 4), 'EXP'

def calc_confluence(b):
    score = 0
    if b.get('stacked_imb') == 'Bearish': score += 1
    if str(b.get('thin_above') or '').lower() == 'false': score += 1
    try:
        if float(b.get('bar_delta') or 0) < -50: score += 1
    except: pass
    try:
        if float(b.get('obi_l5') or 0) < -0.2: score += 1
    except: pass
    try:
        if float(b.get('dz') or 0) < -0.5: score += 1
    except: pass
    if b.get('oi_momentum') is True or str(b.get('oi_momentum')).lower() == 'true':
        score += 1
    return score

# ── Long signal detection (de mtf_longs_backtest.py) ─────────────────────────

LONG_SYMBOLS = ['ETHUSDT', 'SOLUSDT']
LONG_TABLES  = {'ETHUSDT': 'eth_bars', 'SOLUSDT': 'sol_bars'}
LONG_BAR_COLS= ('ts_ms,open,high,low,close,volume,atr,session,cvd_slope,obi_l5,'
                'vwap,vr,oi_momentum,bar_delta,regime,dz,absorption,'
                'stacked_imb,thin_below,equal_low,obi_fast')

def build_h4(m1):
    buckets = defaultdict(list)
    for b in m1:
        h4_ts = (b['ts_ms'] // (4*3_600_000)) * (4*3_600_000)
        buckets[h4_ts].append(b)
    h4 = []
    for ts in sorted(buckets):
        bs = buckets[ts]
        h4.append({'ts_ms': ts, 'open': bs[0]['open'],
                   'high': max(b['high'] for b in bs),
                   'low':  min(b['low']  for b in bs),
                   'close': bs[-1]['close']})
    return h4

def h4_trend_series(m1):
    h4 = build_h4(m1)
    if len(h4) < 20:
        return {}
    closes = [b['close'] for b in h4]
    h4_ema = ema(closes, 20)
    h4_map = {}
    for i, b in enumerate(h4):
        c, e = b['close'], h4_ema[i]
        if c > e*1.005:    h4_map[b['ts_ms']] = 'bull'
        elif c < e*0.995:  h4_map[b['ts_ms']] = 'bear'
        else:              h4_map[b['ts_ms']] = 'neutral'
    result = {}
    for b in m1:
        h4_ts = (b['ts_ms'] // (4*3_600_000)) * (4*3_600_000)
        result[b['ts_ms']] = h4_map.get(h4_ts, 'unknown')
    return result

def detect_m1_signal_long(sym, m1, i):
    b    = m1[i]
    ses  = b.get('session', '')
    oi   = b.get('oi_momentum')
    obi  = b.get('obi_l5') or 0
    dz   = b.get('dz') or 0
    reg  = b.get('regime', '')
    abso = b.get('absorption', '')
    stk  = b.get('stacked_imb', '')
    eq   = b.get('equal_low')
    rng  = (b['high'] - b['low']) or 1
    body = abs(b['close'] - b['open'])
    wick_lo = min(b['close'], b['open']) - b['low']
    is_hammer   = (wick_lo/rng > 0.45) and (body/rng < 0.40)
    is_stk_bull = stk == 'Bullish'
    is_london   = ses in ('London', 'LondonNyOverlap')
    is_ny       = ses == 'NewYork'
    is_exp      = reg == 'Expansion'
    obi_pos     = obi > 0.2
    dz_buy      = dz > 0.5
    oi_true     = oi is True or str(oi).lower() == 'true'
    eq_true     = eq is True or str(eq).lower() == 'true'

    if ses in ('OffHours', 'Asia'):
        return False, ''

    if sym == 'ETHUSDT':
        if is_stk_bull and is_london:        return True, 'eth:stacked_bull+london'
        if is_stk_bull and is_ny:            return True, 'eth:stacked_bull+ny'
        if is_hammer and dz_buy:             return True, 'eth:hammer+dz_buy'
        if eq_true and is_london and is_exp: return True, 'eth:eq_low+london+exp'
        if oi_true and is_ny:               return True, 'eth:oi+ny'
    elif sym == 'SOLUSDT':
        if is_hammer and is_london:          return True, 'sol:hammer+london'
        if is_stk_bull and is_london:        return True, 'sol:stacked_bull+london'
        if is_hammer and obi_pos:            return True, 'sol:hammer+obi_pos'
        if oi_true and is_london:           return True, 'sol:oi+london'
        if eq_true and is_london:           return True, 'sol:eq_low+london'
    return False, ''

def simulate_long(m1, entry_i, entry, stop, risk, target_r=2.5):
    target_price   = entry + target_r * risk
    n              = len(m1)
    cvd_neg_streak = 0
    obi_neg_streak = 0
    for k in range(1, min(FORWARD_M1, n - entry_i)):
        mb   = m1[entry_i + k]
        h, l = mb['high'], mb['low']
        if h >= target_price:
            fee_r = FEE_RT * entry / risk
            return round(target_r - fee_r, 4), 'TP'
        if l <= stop:
            fee_r = FEE_RT * entry / risk
            return round(-(1.0 + fee_r), 4), 'SL'
        curr_r    = (mb['close'] - entry) / risk
        cvd_slope = mb.get('cvd_slope') or 0
        obi_fast  = mb.get('obi_fast') or mb.get('obi_l5') or 0
        if cvd_slope < 0: cvd_neg_streak += 1
        else:             cvd_neg_streak  = 0
        if obi_fast < -OBI_FLIP_THR: obi_neg_streak += 1
        else:                         obi_neg_streak  = 0
        if (cvd_neg_streak >= CVD_FLIP_BARS
                and obi_neg_streak >= 1
                and curr_r >= MIN_PROFIT_CVD):
            fee_r = FEE_RT * entry / risk
            return round((mb['close'] - entry) / risk - fee_r, 4), 'CVD'
    last  = m1[min(entry_i + FORWARD_M1 - 1, n - 1)]
    fee_r = FEE_RT * entry / risk
    return round((last['close'] - entry) / risk - fee_r, 4), 'EXP'

# ── Run one pass of the backtest, returns list of trade dicts ─────────────────

def run_shorts(days=14):
    import time as _time
    all_trades = []
    for sym in SYMBOLS:
        table    = TABLES[sym]
        start_ms = max(STARTS[sym], int((_time.time()-days*86400)*1000))
        print(f'  [SHORT] Loading {sym}...', file=sys.stderr)
        m1 = sb_fetch(table, start_ms)
        if len(m1) < 200: continue

        d1     = binance_klines(sym, '1d', 120)
        d1_ema = ema([b['close'] for b in d1], 20) if d1 else []
        print(f'  [SHORT] Fetching funding+OI for {sym}...', file=sys.stderr)
        funding_map = build_funding_series(fetch_funding_history(sym, 200), m1)
        oi_map      = build_oi_series(fetch_oi_history(sym, '1h', 500), m1)

        h1      = build_h1(m1)
        h1_atrs = atr_series(h1)
        last_i  = -COOLDOWN_M1

        for i in range(10, len(m1) - FORWARD_M1 - 1):
            if i - last_i < COOLDOWN_M1: continue
            b = m1[i]
            if not b.get('atr'): continue
            d1t = d1_trend(b['ts_ms'], d1, d1_ema) if d1 and d1_ema else 'unknown'
            if d1t == 'bull': continue
            sig, sig_name = detect_m1_signal(sym, m1, i)
            if not sig: continue
            h1_ts = (b['ts_ms'] // 3_600_000) * 3_600_000
            h1_i  = next((j for j, hb in enumerate(h1) if hb['ts_ms'] == h1_ts), None)
            if h1_i is None: continue
            h1b    = h1[h1_i]
            h1_atr = h1_atrs[h1_i] if h1_i < len(h1_atrs) else 0
            if h1_atr <= 0: continue
            stop_price  = h1b['high'] + 0.3 * h1_atr
            entry_price = b['close']
            risk        = stop_price - entry_price
            if risk <= 0: continue
            stop_pct = risk / entry_price * 100
            if stop_pct < MIN_STOP_PCT or stop_pct > MAX_STOP_PCT: continue
            TARGET_R = 3.5 if calc_confluence(b) >= 3 else 2.5
            net_r, reason = simulate_short(m1, i, entry_price, stop_price, risk, TARGET_R)
            all_trades.append({
                'sym': sym, 'dir': 'Short', 'sig': sig_name,
                'net_r': net_r, 'reason': reason,
                'funding': funding_map.get(b['ts_ms'], 'Neutral'),
                'oi_trend': oi_map.get(b['ts_ms'], 'Flat'),
                'ts_ms': b['ts_ms'],
            })
            last_i = i
    return all_trades

def run_longs(days=14):
    import time as _time
    all_trades = []
    for sym in LONG_SYMBOLS:
        table    = LONG_TABLES[sym]
        start_ms = max(STARTS.get(sym, 0), int((_time.time()-days*86400)*1000))
        print(f'  [LONG]  Loading {sym}...', file=sys.stderr)
        # longs usan columnas distintas (thin_below, equal_low)
        rows, limit, offset = [], 1000, 0
        while True:
            qs = urllib.parse.urlencode({'select': LONG_BAR_COLS, 'ts_ms': f'gte.{start_ms}',
                 'order': 'ts_ms.asc', 'limit': str(limit), 'offset': str(offset)})
            req = urllib.request.Request(f'{SUPABASE_URL}/rest/v1/{table}?{qs}',
                  headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'})
            chunk = json.loads(urllib.request.urlopen(req, timeout=30).read())
            rows.extend(chunk)
            if len(chunk) < limit: break
            offset += limit
        m1 = rows
        if len(m1) < 200: continue

        h4_map  = h4_trend_series(m1)
        print(f'  [LONG]  Fetching funding+OI for {sym}...', file=sys.stderr)
        funding_map = build_funding_series(fetch_funding_history(sym, 200), m1)
        oi_map      = build_oi_series(fetch_oi_history(sym, '1h', 500), m1)

        h1      = build_h1(m1)
        h1_atrs = atr_series(h1)
        last_i  = -COOLDOWN_M1

        for i in range(10, len(m1) - FORWARD_M1 - 1):
            if i - last_i < COOLDOWN_M1: continue
            b = m1[i]
            if not b.get('atr'): continue
            h4t = h4_map.get(b['ts_ms'], 'unknown')
            if h4t == 'bear': continue
            sig, sig_name = detect_m1_signal_long(sym, m1, i)
            if not sig: continue
            h1_ts = (b['ts_ms'] // 3_600_000) * 3_600_000
            h1_i  = next((j for j, hb in enumerate(h1) if hb['ts_ms'] == h1_ts), None)
            if h1_i is None: continue
            h1b    = h1[h1_i]
            h1_atr = h1_atrs[h1_i] if h1_i < len(h1_atrs) else 0
            if h1_atr <= 0: continue
            stop_price  = h1b['low'] - 0.3 * h1_atr
            entry_price = b['close']
            risk        = entry_price - stop_price
            if risk <= 0: continue
            stop_pct = risk / entry_price * 100
            if stop_pct < MIN_STOP_PCT or stop_pct > MAX_STOP_PCT: continue
            net_r, reason = simulate_long(m1, i, entry_price, stop_price, risk, 2.5)
            all_trades.append({
                'sym': sym, 'dir': 'Long', 'sig': sig_name,
                'net_r': net_r, 'reason': reason,
                'funding': funding_map.get(b['ts_ms'], 'Neutral'),
                'oi_trend': oi_map.get(b['ts_ms'], 'Flat'),
                'ts_ms': b['ts_ms'],
            })
            last_i = i
    return all_trades

# ── Stats helpers ──────────────────────────────────────────────────────────────

def stats(trades):
    if not trades:
        return 0, 0.0, 0.0
    n    = len(trades)
    wins = sum(1 for t in trades if t['net_r'] > 0)
    wr   = wins / n * 100
    avg  = sum(t['net_r'] for t in trades) / n
    return n, wr, avg

def calc_equity(trades):
    eq = CAPITAL_INIT
    for t in sorted(trades, key=lambda x: x['ts_ms']):
        eq = round(eq + t['net_r'] * eq * RISK_PCT, 4)
    return eq

def row(label, trades, base_n, days=14):
    n, wr, avg = stats(trades)
    pct  = n / base_n * 100 if base_n else 0
    eq   = calc_equity(trades)
    pnl  = eq - CAPITAL_INIT
    tpd  = n / days if days else 0
    sign = '+' if pnl >= 0 else ''
    return (f'  {label:<40} n={n:>3} ({pct:>5.1f}%)  WR={wr:>5.1f}%  '
            f'AvgR={avg:>+.3f}R  PnL={sign}${pnl:>+.0f}  {tpd:.1f}t/d')

# ── Main ──────────────────────────────────────────────────────────────────────

def equity_curve(trades):
    eq = CAPITAL_INIT
    curve = []
    for t in sorted(trades, key=lambda x: x['ts_ms']):
        pnl = t['net_r'] * eq * RISK_PCT
        eq  = round(eq + pnl, 2)
        curve.append((t, round(pnl, 2), eq))
    return curve

def print_comparison(label_a, trades_a, label_b, trades_b, days):
    ca = equity_curve(trades_a)
    cb = equity_curve(trades_b)
    na, nra, avga = stats(trades_a)
    nb, nrb, avgb = stats(trades_b)
    eq_a = ca[-1][2] if ca else CAPITAL_INIT
    eq_b = cb[-1][2] if cb else CAPITAL_INIT

    removed = [t for t in trades_a if t not in trades_b]

    print(f'\n  {"":30} {label_a:<22} {label_b}')
    print(f'  {"Trades":30} {na:<22} {nb}')
    print(f'  {"Trades/dia":30} {na/days:<22.1f} {nb/days:.1f}')
    print(f'  {"Win Rate":30} {nra:<22.1f}% {nrb:.1f}%')
    print(f'  {"AvgR":30} {avga:<+22.3f}R {avgb:+.3f}R')
    print(f'  {"Equity final ($500 base)":30} ${eq_a:<21.0f} ${eq_b:.0f}')
    print(f'  {"PnL":30} ${eq_a-CAPITAL_INIT:<+21.0f} ${eq_b-CAPITAL_INIT:+.0f}')
    print(f'  {"Trades removidos":30} --                     {len(removed)}')
    if removed:
        wins_rem  = sum(1 for t in removed if t['net_r'] > 0)
        r_rem     = sum(t['net_r'] for t in removed)
        print(f'  {"  WR trades removidos":30} --                     {wins_rem}/{len(removed)} ({wins_rem/len(removed)*100:.0f}%)')
        print(f'  {"  AvgR trades removidos":30} --                     {r_rem/len(removed):+.3f}R')

def print_section(title, trades, days, bad_funding_short, bad_funding_long):
    """Imprime tabla base vs filtro para un conjunto de trades (short o long)."""
    if not trades:
        print(f'  [SIN DATOS]')
        return
    base_n = len(trades)
    bad    = bad_funding_short if trades[0]['dir'] == 'Short' else bad_funding_long
    filt   = [t for t in trades if t['funding'] not in bad]

    nb, wrb, avgb = stats(trades)
    nf, wrf, avgf = stats(filt)
    eqb = calc_equity(trades)
    eqf = calc_equity(filt)
    removed = [t for t in trades if t not in filt]
    wr_rem  = sum(1 for t in removed if t['net_r'] > 0) / len(removed) * 100 if removed else 0
    avg_rem = sum(t['net_r'] for t in removed) / len(removed) if removed else 0

    W = 56
    print(f'\n  {title}')
    print(f'  {"-"*W}')
    print(f'  {"":28} {"BASE":>12}  {"+ Filtro":>12}')
    print(f'  {"Trades":28} {nb:>12}  {nf:>12}')
    print(f'  {"Trades/dia":28} {nb/days:>12.1f}  {nf/days:>12.1f}')
    print(f'  {"Win Rate":28} {wrb:>11.1f}%  {wrf:>11.1f}%')
    print(f'  {"AvgR":28} {avgb:>+11.3f}R  {avgf:>+11.3f}R')
    print(f'  {"PnL ($500, 2%R)":28} ${eqb-CAPITAL_INIT:>+10.0f}  ${eqf-CAPITAL_INIT:>+10.0f}')
    print(f'  {"Equity final":28} ${eqb:>+10.0f}  ${eqf:>+10.0f}')
    if removed:
        print(f'  {"-"*W}')
        print(f'  {"Trades removidos":28} {"":12}  {len(removed):>12}')
        print(f'  {"  WR removidos":28} {"":12}  {wr_rem:>11.1f}%')
        print(f'  {"  AvgR removidos":28} {"":12}  {avg_rem:>+11.3f}R')
        print(f'  {"  PnL removidos":28} {"":12}  ${(eqf-eqb):>+10.0f}')

    # Por simbolo
    print(f'  {"Por simbolo":28}   base n  WR    PnL  |  filt n  WR    PnL')
    syms = sorted(set(t['sym'] for t in trades))
    for sym in syms:
        b_sym = [t for t in trades if t['sym'] == sym]
        f_sym = [t for t in filt   if t['sym'] == sym]
        nb2, wrb2, _ = stats(b_sym)
        nf2, wrf2, _ = stats(f_sym) if f_sym else (0, 0, 0)
        eqb2 = calc_equity(b_sym)
        eqf2 = calc_equity(f_sym)
        print(f'  {sym:<28} {nb2:>6}  {wrb2:>4.0f}%  ${eqb2-CAPITAL_INIT:>+4.0f}  |  '
              f'{nf2:>6}  {wrf2:>4.0f}%  ${eqf2-CAPITAL_INIT:>+4.0f}')

def main():
    import argparse, time as _time
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=14)
    args = parser.parse_args()

    print(f'\nLoading data ({args.days} days)...', file=sys.stderr)
    shorts = run_shorts(args.days)
    longs  = run_longs(args.days)
    trades = shorts + longs

    if not shorts and not longs:
        print('No trades found.')
        return

    # Para shorts: bloquear ExtremeLong (arbitrageurs) y ElevatedShort (squeeze)
    BAD_FUNDING_SHORT = ('ExtremeLong', 'ElevatedShort')
    # Para longs: bloquear ExtremeLong (longs sobrecargados) y ElevatedShort (mercado cayendo)
    # Nota: para longs ElevatedShort = mercado bajista, peligroso ir largo
    BAD_FUNDING_LONG  = ('ExtremeLong', 'ElevatedShort')

    d = args.days
    W = 70
    print(f'\n{"="*W}')
    print(f'  MTF GAMMA ENVIRONMENT FILTER — SHORTS + LONGS ({d}d)')
    print(f'  Filtro: block funding ExtremeLong + ElevatedShort')
    print(f'  Logica SHORT: ExtremeLong = arbitrageurs sostienen precio')
    print(f'                ElevatedShort = squeeze inminente')
    print(f'  Logica LONG:  ExtremeLong = longs sobrecargados, reversal risk')
    print(f'                ElevatedShort = mercado bajista, peligroso ir largo')
    print(f'{"="*W}')

    W = 60

    # ── SHORTS ─────────────────────────────────────────────────────────────────
    print(f'\n{"="*W}')
    print(f'  SHORTS — breakdown por funding')
    print(f'  {"Regime":<20} {"n":>4}  {"WR":>6}  {"AvgR":>8}  {"PnL":>8}')
    print(f'  {"-"*50}')
    for regime in ['ExtremeLong','ElevatedLong','Neutral','ElevatedShort','ExtremeShort']:
        bucket = [t for t in shorts if t['funding'] == regime]
        if bucket:
            n, wr, avg = stats(bucket)
            eq = calc_equity(bucket)
            print(f'  {regime:<20} {n:>4}  {wr:>5.1f}%  {avg:>+8.3f}R  ${eq-CAPITAL_INIT:>+6.0f}')

    print(f'\n  SHORTS — filtros y PnL resultante:')
    print(f'  {"Variante":<42} {"n":>4}  {"WR":>6}  {"AvgR":>8}  {"PnL":>8}')
    print(f'  {"-"*68}')
    short_variants = [
        ('BASE',                                          shorts),
        ('Block ExtremeLong+ElevatedShort',
            [t for t in shorts if t['funding'] not in ('ExtremeLong','ElevatedShort')]),
        ('Block ExtremeLong only',
            [t for t in shorts if t['funding'] != 'ExtremeLong']),
        ('Block ElevatedShort only',
            [t for t in shorts if t['funding'] != 'ElevatedShort']),
        ('Solo ElevatedLong+Neutral+ExtremeShort',
            [t for t in shorts if t['funding'] in ('ElevatedLong','Neutral','ExtremeShort')]),
    ]
    for label, ts in short_variants:
        n, wr, avg = stats(ts)
        eq = calc_equity(ts)
        marker = ' <--' if label == 'BASE' else ''
        print(f'  {label:<42} {n:>4}  {wr:>5.1f}%  {avg:>+8.3f}R  ${eq-CAPITAL_INIT:>+6.0f}{marker}')

    # ── LONGS ──────────────────────────────────────────────────────────────────
    print(f'\n{"="*W}')
    print(f'  LONGS — breakdown por funding')
    print(f'  {"Regime":<20} {"n":>4}  {"WR":>6}  {"AvgR":>8}  {"PnL":>8}')
    print(f'  {"-"*50}')
    for regime in ['ExtremeLong','ElevatedLong','Neutral','ElevatedShort','ExtremeShort']:
        bucket = [t for t in longs if t['funding'] == regime]
        if bucket:
            n, wr, avg = stats(bucket)
            eq = calc_equity(bucket)
            print(f'  {regime:<20} {n:>4}  {wr:>5.1f}%  {avg:>+8.3f}R  ${eq-CAPITAL_INIT:>+6.0f}')
        else:
            print(f'  {regime:<20}    0     --        --        --')

    print(f'\n  LONGS — espejo: que filtro mejora el PnL?')
    print(f'  {"Variante":<42} {"n":>4}  {"WR":>6}  {"AvgR":>8}  {"PnL":>8}')
    print(f'  {"-"*68}')
    long_variants = [
        ('BASE',                                          longs),
        ('Block Neutral (peor zona)',
            [t for t in longs if t['funding'] != 'Neutral']),
        ('Block ExtremeShort (espejo ExtremeLong)',
            [t for t in longs if t['funding'] != 'ExtremeShort']),
        ('Block Neutral+ExtremeShort',
            [t for t in longs if t['funding'] not in ('Neutral','ExtremeShort')]),
        ('Solo ElevatedShort+ExtremeLong',
            [t for t in longs if t['funding'] in ('ElevatedShort','ExtremeLong')]),
        ('Solo ElevatedShort+ExtremeLong+ExtremeShort',
            [t for t in longs if t['funding'] in ('ElevatedShort','ExtremeLong','ExtremeShort')]),
    ]
    for label, ts in long_variants:
        if not ts:
            print(f'  {label:<42}    0     --        --        --')
            continue
        n, wr, avg = stats(ts)
        eq = calc_equity(ts)
        marker = ' <--' if label == 'BASE' else ''
        print(f'  {label:<42} {n:>4}  {wr:>5.1f}%  {avg:>+8.3f}R  ${eq-CAPITAL_INIT:>+6.0f}{marker}')

    # ── Resumen combinado con mejor filtro de cada lado ─────────────────────────
    best_short_filt = [t for t in shorts if t['funding'] not in ('ExtremeLong','ElevatedShort')]
    best_long_filt  = [t for t in longs  if t['funding'] not in ('Neutral','ExtremeShort')]

    all_base = sorted(shorts + longs,             key=lambda x: x['ts_ms'])
    all_filt = sorted(best_short_filt + best_long_filt, key=lambda x: x['ts_ms'])

    nb, wrb, avgb = stats(all_base)
    nf, wrf, avgf = stats(all_filt)
    eqb = calc_equity(all_base)
    eqf = calc_equity(all_filt)

    print(f'\n{"="*W}')
    print(f'  COMBINADO — SHORT filter + LONG filter (mejor de cada lado)')
    print(f'  Short: block ExtremeLong+ElevatedShort')
    print(f'  Long:  block Neutral+ExtremeShort')
    print(f'  {"":28} {"BASE":>12}  {"Filtrado":>12}')
    print(f'  {"Trades":28} {nb:>12}  {nf:>12}')
    print(f'  {"Win Rate":28} {wrb:>11.1f}%  {wrf:>11.1f}%')
    print(f'  {"AvgR":28} {avgb:>+11.3f}R  {avgf:>+11.3f}R')
    print(f'  {"PnL ($500, 2%R)":28} ${eqb-CAPITAL_INIT:>+10.0f}  ${eqf-CAPITAL_INIT:>+10.0f}')
    print(f'{"="*W}\n')

if __name__ == '__main__':
    main()
