#!/usr/bin/env python3
"""
Calibracion BNB y XRP — exploracion de patrones desde cero.
Escanea todos los candidatos de señal y simula cada combinacion
para encontrar cuales tienen edge real (como hicimos con BTC/ETH/SOL).
"""
import json, os, sys, time, urllib.request, urllib.parse
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone

ROOT = Path(__file__).parent.parent.parent.parent
_env = {}
for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1); _env[k.strip()] = v.strip().strip('"').strip("'")
SUPABASE_URL = _env['SUPABASE_URL']
SUPABASE_KEY = _env['SUPABASE_KEY']

DAYS        = 14
CAPITAL     = 500.0
RISK_PCT    = 0.02
FEE_RT      = 0.0007
FORWARD_M1  = 1200
MIN_STOP    = 0.30
MAX_STOP    = 0.75
CVD_BARS    = 5
MIN_PROFIT  = 1.0
OBI_THR     = 0.15
TARGET_R    = 2.5
COOLDOWN    = 30

SYMS = {
    'BNBUSDT': {'table':'bnb_bars', 'start':1780756260000, 'tick':0.01},
    'XRPUSDT': {'table':'xrp_bars', 'start':1780756260000, 'tick':0.0001},
}

BAR_COLS = ('ts_ms,open,high,low,close,volume,atr,session,cvd_slope,obi_l5,'
            'vwap,vr,oi_momentum,bar_delta,regime,dz,absorption,'
            'swing_low_50,swing_high_50,prev_day_high,prev_day_low,'
            'stacked_imb,thin_above,ask_wall,equal_high,cvd_divergence,obi_fast')

def sb_fetch(table, start_ms):
    rows, limit, offset = [], 1000, 0
    while True:
        qs = urllib.parse.urlencode({'select':BAR_COLS,'ts_ms':f'gte.{start_ms}',
             'order':'ts_ms.asc','limit':str(limit),'offset':str(offset)})
        req = urllib.request.Request(f'{SUPABASE_URL}/rest/v1/{table}?{qs}',
              headers={'apikey':SUPABASE_KEY,'Authorization':f'Bearer {SUPABASE_KEY}'})
        chunk = json.loads(urllib.request.urlopen(req, timeout=30).read())
        rows.extend(chunk)
        if len(chunk) < limit: break
        offset += limit
    return rows

def binance_klines(symbol, interval, limit=200):
    url = (f'https://fapi.binance.com/fapi/v1/klines'
           f'?symbol={symbol}&interval={interval}&limit={limit}')
    try:
        req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
        data = json.loads(urllib.request.urlopen(req, timeout=15).read())
        return [{'ts_ms':int(d[0]),'open':float(d[1]),'high':float(d[2]),
                 'low':float(d[3]),'close':float(d[4])} for d in data]
    except: return []

def ema(vals, p):
    k = 2/(p+1); r = [vals[0]]
    for v in vals[1:]: r.append(v*k+r[-1]*(1-k))
    return r

def atr_series(bars, period=14):
    trs = []
    for i, b in enumerate(bars):
        pc = bars[i-1]['close'] if i > 0 else b['open']
        trs.append(max(b['high']-b['low'], abs(b['high']-pc), abs(b['low']-pc)))
    atrs = [sum(trs[:period])/period]*period
    k = 1/period
    for i in range(period, len(trs)):
        atrs.append(atrs[-1]*(1-k)+trs[i]*k)
    return atrs

def build_h1(m1):
    buckets = defaultdict(list)
    for b in m1:
        buckets[(b['ts_ms']//3_600_000)*3_600_000].append(b)
    h1 = []
    for ts in sorted(buckets):
        bs = buckets[ts]
        h1.append({'ts_ms':ts,'open':bs[0]['open'],'high':max(b['high'] for b in bs),
                   'low':min(b['low'] for b in bs),'close':bs[-1]['close']})
    return h1

def d1_trend(ts_ms, d1, d1_ema):
    day_ms = (ts_ms//86_400_000)*86_400_000
    for i in range(len(d1)-1, -1, -1):
        if d1[i]['ts_ms'] <= day_ms:
            c, e = d1[i]['close'], d1_ema[i]
            if c > e*1.005: return 'bull'
            if c < e*0.995: return 'bear'
            return 'neutral'
    return 'unknown'

def simulate(m1, entry_i, entry, stop, risk):
    target = entry - TARGET_R * risk
    cvd_streak = 0; obi_streak = 0
    for k in range(1, min(FORWARD_M1, len(m1)-entry_i)):
        mb = m1[entry_i+k]
        if mb['low'] <= target:
            fee = FEE_RT*entry/risk
            return round(TARGET_R-fee, 4), 'TAKE_PROFIT'
        if mb['high'] >= stop:
            fee = FEE_RT*entry/risk
            return round(-(1.0+fee), 4), 'STOP_LOSS'
        curr_r = (entry - mb['close']) / risk
        cvd    = mb.get('cvd_slope') or 0
        obif   = mb.get('obi_fast') or mb.get('obi_l5') or 0
        if cvd > 0: cvd_streak += 1
        else:       cvd_streak  = 0
        if obif > OBI_THR: obi_streak += 1
        else:              obi_streak  = 0
        if cvd_streak >= CVD_BARS and obi_streak >= 1 and curr_r >= MIN_PROFIT:
            fee = FEE_RT*entry/risk
            return round((entry-mb['close'])/risk-fee, 4), 'CVD_EXHAUSTION'
    return round((entry-m1[min(entry_i+FORWARD_M1, len(m1)-1)]['close'])/risk - FEE_RT*entry/risk, 4), 'EXPIRED'

def stats(trades):
    if not trades: return {'n':0,'wr':0,'avgR':0,'totalR':0}
    n = len(trades); wins = [t for t in trades if t['r'] > 0]
    tr = sum(t['r'] for t in trades)
    return {'n':n,'wr':round(len(wins)/n*100,1),'avgR':round(tr/n,3),'totalR':round(tr,2)}

def sep(t): print(f'\n{"="*68}\n  {t}\n{"="*68}')

# ── Definicion de patrones candidatos ─────────────────────────────────────────
def get_patterns(b):
    """Devuelve lista de (pattern_name, match) para el bar b."""
    ses   = b.get('session','')
    oi    = b.get('oi_momentum')
    obi   = float(b.get('obi_l5') or 0)
    obif  = float(b.get('obi_fast') or 0)
    vr    = float(b.get('vr') or 0)
    reg   = b.get('regime','')
    eq    = str(b.get('equal_high','')).lower() == 'true'
    dz    = float(b.get('dz') or 0)
    cvd   = float(b.get('cvd_slope') or 0)
    abso  = b.get('absorption','')
    stk   = b.get('stacked_imb','')
    rng   = (b['high']-b['low']) or 1
    body  = abs(b['close']-b['open'])
    wick  = b['high'] - max(b['close'],b['open'])
    bear  = b['close'] < b['open']

    is_shoot  = wick/rng > 0.45 and body/rng < 0.40
    is_london = ses in ('London','LondonNyOverlap')
    is_ny     = ses == 'NewYork'
    is_exp    = reg == 'Expansion'
    abs_ask   = abso == 'Ask'
    stk_bear  = stk == 'Bearish'
    oi_true   = oi is True or str(oi).lower() == 'true'

    return [
        ('shoot+london',       is_shoot and is_london),
        ('shoot+ny',           is_shoot and is_ny),
        ('shoot+ask+obi',      is_shoot and abs_ask and obif < 0),
        ('shoot+stacked',      is_shoot and stk_bear),
        ('ask+london+exp',     abs_ask and is_london and is_exp),
        ('ask+ny',             abs_ask and is_ny),
        ('eq+london+exp',      eq and is_london and is_exp),
        ('eq+ny+oi',           eq and is_ny and oi_true),
        ('eq+london',          eq and is_london),
        ('stacked+london',     stk_bear and is_london),
        ('stacked+ny',         stk_bear and is_ny),
        ('stacked+oi',         stk_bear and oi_true),
        ('oi+london',          oi_true and is_london),
        ('oi+ny',              oi_true and is_ny),
        ('shoot+dz',           is_shoot and dz < -0.3),
        ('shoot+obi_neg',      is_shoot and obi < -0.15),
        ('vr_high+london',     vr > 3.0 and is_london),
        ('vr_high+ny+oi',      vr > 3.0 and is_ny and oi_true),
    ]

# ── Main ──────────────────────────────────────────────────────────────────────
for sym, cfg in SYMS.items():
    sep(f'ANALISIS {sym}')
    start_ms = max(cfg['start'], int((time.time()-DAYS*86400)*1000))

    print(f'  Cargando {sym}...', end=' ', flush=True)
    m1 = sb_fetch(cfg['table'], start_ms)
    print(f'{len(m1)} barras')

    if len(m1) < 200:
        print('  Insuficientes datos'); continue

    d1     = binance_klines(sym, '1d', 120)
    d1_ema = ema([b['close'] for b in d1], 20) if d1 else []
    h1     = build_h1(m1)
    h1_atr = atr_series(h1)

    # Escanear candidatos
    pattern_trades = defaultdict(list)
    last_sig = {p: -COOLDOWN for p in [x[0] for x in get_patterns(m1[0])]}

    for i in range(10, len(m1)-FORWARD_M1-1):
        b   = m1[i]
        ses = b.get('session','')
        if ses in ('OffHours','Asia'): continue
        if not b.get('atr'): continue

        if d1 and d1_ema:
            if d1_trend(b['ts_ms'], d1, d1_ema) == 'bull': continue

        h1_ts = (b['ts_ms']//3_600_000)*3_600_000
        h1_i  = next((j for j,hb in enumerate(h1) if hb['ts_ms']==h1_ts), None)
        if h1_i is None: continue
        h1b    = h1[h1_i]
        h1_a   = h1_atr[h1_i] if h1_i < len(h1_atr) else 0
        if h1_a <= 0: continue

        stop  = h1b['high'] + 0.3*h1_a
        entry = b['close']
        risk  = stop - entry
        if risk <= 0: continue
        stop_pct = risk/entry*100
        if stop_pct < MIN_STOP or stop_pct > MAX_STOP: continue

        hour = datetime.fromtimestamp(b['ts_ms']/1000, tz=timezone.utc).hour

        for pat_name, matched in get_patterns(b):
            if not matched: continue
            if i - last_sig.get(pat_name, -COOLDOWN) < COOLDOWN: continue
            last_sig[pat_name] = i

            r, reason = simulate(m1, i, entry, stop, risk)
            dt = datetime.fromtimestamp(b['ts_ms']/1000, tz=timezone.utc)
            pattern_trades[pat_name].append({
                'r':r,'reason':reason,'session':ses,'hour':hour,
                'sym':sym,'ts':dt.strftime('%m-%d %H:%M')
            })

    # Resultados por patron
    print(f'\n  {"Patron":<22} {"n":>4} {"WR%":>6} {"AvgR":>8} {"TotalR":>8}  Reasons')
    print('  ' + '-'*72)

    viable = []
    for pat, trades in sorted(pattern_trades.items(), key=lambda x: -len(x[1])):
        s = stats(trades)
        if s['n'] == 0: continue
        by_r = defaultdict(int)
        for t in trades: by_r[t['reason']] += 1
        reason_str = '  '.join(f'{r}={n}' for r,n in sorted(by_r.items(), key=lambda x:-x[1]))
        marker = ' <-- EDGE' if s['wr'] >= 55 and s['avgR'] >= 0.3 and s['n'] >= 5 else ''
        print(f'  {pat:<22} {s["n"]:>4} {s["wr"]:>5.1f}%  {s["avgR"]:>+7.3f}R  {s["totalR"]:>+7.2f}R  {reason_str}{marker}')
        if s['wr'] >= 55 and s['avgR'] >= 0.3 and s['n'] >= 5:
            viable.append((pat, s))

    # Session breakdown de los mejores patrones
    if viable:
        print(f'\n  PATRONES CON EDGE — breakdown por sesion:')
        for pat, s_global in viable[:5]:
            trades = pattern_trades[pat]
            by_ses = defaultdict(list)
            for t in trades: by_ses[t['session']].append(t)
            print(f'\n    {pat} (global: n={s_global["n"]} WR={s_global["wr"]}% AvgR={s_global["avgR"]:+.3f}R)')
            for ses, ts in sorted(by_ses.items(), key=lambda x: -len(x[1])):
                sg = stats(ts)
                print(f'      {ses:<22} n={sg["n"]:>3}  WR={sg["wr"]:>5.1f}%  AvgR={sg["avgR"]:>+.3f}R')

    print()
