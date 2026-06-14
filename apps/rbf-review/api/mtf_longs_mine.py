#!/usr/bin/env python3
"""
MTF Longs — Mining de patrones desde cero.
Espejo exacto de los shorts: D1 bull/neutral, H1 structural low,
M1 rejection abajo, stop H1_low - 0.3*ATR.
"""
import json, sys, time, urllib.request, urllib.parse
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

DAYS       = 14
FEE_RT     = 0.0007
FORWARD_M1 = 1200
MIN_STOP   = 0.30
MAX_STOP   = 0.75
CVD_BARS   = 5
OBI_THR    = 0.15
MIN_PROFIT = 1.0
TARGET_R   = 2.5
COOLDOWN   = 30

SYMS = {
    'BTCUSDT': {'table':'btc_bars','start':1780676700000,'sessions':('London','LondonNyOverlap','NewYork')},
    'ETHUSDT': {'table':'eth_bars','start':1780756260000,'sessions':('London','LondonNyOverlap','NewYork')},
    'SOLUSDT': {'table':'sol_bars','start':1780756260000,'sessions':('London','LondonNyOverlap','NewYork')},
    'BNBUSDT': {'table':'bnb_bars','start':1780756260000,'sessions':('NewYork',)},
    'XRPUSDT': {'table':'xrp_bars','start':1780756260000,'sessions':('NewYork',)},
}

BAR_COLS = ('ts_ms,open,high,low,close,volume,atr,session,cvd_slope,obi_l5,'
            'vwap,vr,oi_momentum,bar_delta,regime,dz,absorption,'
            'swing_low_50,swing_high_50,stacked_imb,thin_below,bid_wall,'
            'equal_high,cvd_divergence,obi_fast')

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

def build_h4(m1):
    """H4 = buckets de 4 horas UTC."""
    buckets = defaultdict(list)
    for b in m1:
        h4_ts = (b['ts_ms'] // (4*3_600_000)) * (4*3_600_000)
        buckets[h4_ts].append(b)
    h4 = []
    for ts in sorted(buckets):
        bs = buckets[ts]
        h4.append({'ts_ms':ts,'open':bs[0]['open'],'high':max(b['high'] for b in bs),
                   'low':min(b['low'] for b in bs),'close':bs[-1]['close']})
    return h4

def h4_trend_series(m1):
    """Devuelve dict ts_ms -> trend H4 para cada barra M1."""
    h4 = build_h4(m1)
    if len(h4) < 20:
        return {}
    closes  = [b['close'] for b in h4]
    h4_ema  = ema(closes, 20)
    # mapa h4_ts -> trend
    h4_map = {}
    for i, b in enumerate(h4):
        c, e = b['close'], h4_ema[i]
        if c > e*1.005:   h4_map[b['ts_ms']] = 'bull'
        elif c < e*0.995: h4_map[b['ts_ms']] = 'bear'
        else:             h4_map[b['ts_ms']] = 'neutral'
    # mapear cada M1 al H4 correspondiente
    result = {}
    for b in m1:
        h4_ts = (b['ts_ms'] // (4*3_600_000)) * (4*3_600_000)
        result[b['ts_ms']] = h4_map.get(h4_ts, 'unknown')
    return result

def simulate_long(m1, entry_i, entry, stop, risk):
    """Simula un long desde entry_i. Stop abajo, target arriba."""
    target = entry + TARGET_R * risk
    cvd_neg_streak = 0  # CVD negativo = compradores se agotan
    obi_neg_streak = 0
    for k in range(1, min(FORWARD_M1, len(m1)-entry_i)):
        mb = m1[entry_i+k]
        # TP: precio sube al target
        if mb['high'] >= target:
            fee = FEE_RT * entry / risk
            return round(TARGET_R - fee, 4), 'TAKE_PROFIT'
        # SL: precio cae al stop
        if mb['low'] <= stop:
            fee = FEE_RT * entry / risk
            return round(-(1.0 + fee), 4), 'STOP_LOSS'
        curr_r   = (mb['close'] - entry) / risk
        cvd      = mb.get('cvd_slope') or 0
        obif     = mb.get('obi_fast') or mb.get('obi_l5') or 0
        # CVD exhaustion para longs: CVD se vuelve negativo (vendedores entran)
        if cvd < 0: cvd_neg_streak += 1
        else:       cvd_neg_streak  = 0
        if obif < -OBI_THR: obi_neg_streak += 1
        else:               obi_neg_streak  = 0
        if cvd_neg_streak >= CVD_BARS and obi_neg_streak >= 1 and curr_r >= MIN_PROFIT:
            fee = FEE_RT * entry / risk
            return round((mb['close'] - entry) / risk - fee, 4), 'CVD_EXHAUSTION'
    last = m1[min(entry_i+FORWARD_M1, len(m1)-1)]
    fee = FEE_RT * entry / risk
    return round((last['close'] - entry) / risk - fee, 4), 'EXPIRED'

def stats(trades):
    if not trades: return {'n':0,'wr':0,'avgR':0,'totalR':0}
    n = len(trades); wins = [t for t in trades if t['r'] > 0]
    tr = sum(t['r'] for t in trades)
    return {'n':n,'wr':round(len(wins)/n*100,1),'avgR':round(tr/n,3),'totalR':round(tr,2)}

def sep(t): print(f'\n{"="*68}\n  {t}\n{"="*68}')

def get_long_patterns(b, lows_50):
    """Patrones candidatos para LONGS — espejo de los shorts."""
    ses   = b.get('session','')
    oi    = b.get('oi_momentum')
    obi   = float(b.get('obi_l5') or 0)
    obif  = float(b.get('obi_fast') or 0)
    vr    = float(b.get('vr') or 0)
    reg   = b.get('regime','')
    dz    = float(b.get('dz') or 0)
    cvd   = float(b.get('cvd_slope') or 0)
    abso  = b.get('absorption','')
    stk   = b.get('stacked_imb','')
    vpin  = float(b.get('vpin') or 0)
    rng   = (b['high']-b['low']) or 1
    body  = abs(b['close']-b['open'])
    wick_lo = min(b['close'],b['open']) - b['low']   # mecha abajo = potencial compra
    bull    = b['close'] > b['open']

    # equal_low: low actual dentro de 0.03% del minimo de las 50 barras previas
    eq_low = False
    if lows_50:
        min50 = min(lows_50)
        eq_low = abs(b['low'] - min50) / min50 <= 0.0003 if min50 > 0 else False

    is_hammer   = wick_lo/rng > 0.45 and body/rng < 0.40   # espejo del shooting star
    is_london   = ses in ('London','LondonNyOverlap')
    is_ny       = ses == 'NewYork'
    is_exp      = reg == 'Expansion'
    abs_bid     = abso == 'Bid'                              # espejo de Ask
    stk_bull    = stk == 'Bullish'                           # espejo de Bearish
    oi_true     = oi is True or str(oi).lower() == 'true'

    return [
        ('hammer+london',       is_hammer and is_london),
        ('hammer+ny',           is_hammer and is_ny),
        ('hammer+bid+obi',      is_hammer and abs_bid and obif > 0),
        ('hammer+stacked',      is_hammer and stk_bull),
        ('bid+london+exp',      abs_bid and is_london and is_exp),
        ('bid+ny',              abs_bid and is_ny),
        ('eq_low+london+exp',   eq_low and is_london and is_exp),
        ('eq_low+ny+oi',        eq_low and is_ny and oi_true),
        ('eq_low+london',       eq_low and is_london),
        ('stacked_bull+london', stk_bull and is_london),
        ('stacked_bull+ny',     stk_bull and is_ny),
        ('stacked_bull+oi',     stk_bull and oi_true),
        ('oi+london',           oi_true and is_london),
        ('oi+ny',               oi_true and is_ny),
        ('hammer+dz_buy',       is_hammer and dz > 0.3),
        ('hammer+obi_pos',      is_hammer and obi > 0.15),
        ('vr_high+london',      vr > 3.0 and is_london),
        ('vr_high+ny+oi',       vr > 3.0 and is_ny and oi_true),
    ]

# ── Main ──────────────────────────────────────────────────────────────────────
all_sym_results = {}

for sym, cfg in SYMS.items():
    sep(f'MINING LONGS — {sym}')
    start_ms = max(cfg['start'], int((time.time()-DAYS*86400)*1000))

    print(f'  Cargando {sym}...', end=' ', flush=True)
    m1 = sb_fetch(cfg['table'], start_ms)
    print(f'{len(m1)} barras')

    if len(m1) < 200:
        print('  Insuficientes datos'); continue

    h1     = build_h1(m1)
    h1_atr = atr_series(h1)

    # Contexto H4 — mucho más responsivo que D1
    h4_trend = h4_trend_series(m1)
    bull_neutral = sum(1 for b in m1 if h4_trend.get(b['ts_ms']) in ('bull','neutral'))
    bear_bars    = sum(1 for b in m1 if h4_trend.get(b['ts_ms']) == 'bear')
    print(f'  Contexto H4 — bull/neutral: {bull_neutral} barras ({bull_neutral/len(m1)*100:.0f}%) | bear: {bear_bars} barras ({bear_bars/len(m1)*100:.0f}%)')

    pattern_trades = defaultdict(list)
    last_sig = {}

    for i in range(50, len(m1)-FORWARD_M1-1):
        b   = m1[i]
        ses = b.get('session','')
        if ses in ('OffHours','Asia'): continue
        if ses not in cfg['sessions']: continue
        if not b.get('atr'): continue

        # H4: solo bull o neutral — más responsivo que D1
        trend = h4_trend.get(b['ts_ms'], 'unknown')
        if trend == 'bear': continue

        # H1 low y ATR para stop
        h1_ts = (b['ts_ms']//3_600_000)*3_600_000
        h1_i  = next((j for j,hb in enumerate(h1) if hb['ts_ms']==h1_ts), None)
        if h1_i is None: continue
        h1b   = h1[h1_i]
        h1_a  = h1_atr[h1_i] if h1_i < len(h1_atr) else 0
        if h1_a <= 0: continue

        # Stop estructural: H1_low - 0.3*ATR (espejo de H1_high + 0.3*ATR)
        stop_price  = h1b['low'] - 0.3 * h1_a
        entry_price = b['close']
        risk = entry_price - stop_price
        if risk <= 0: continue
        stop_pct = risk / entry_price * 100
        if stop_pct < MIN_STOP or stop_pct > MAX_STOP: continue

        # Últimos 50 lows para equal_low
        lows_50 = [m1[j]['low'] for j in range(max(0,i-50), i)]

        for pat_name, matched in get_long_patterns(b, lows_50):
            if not matched: continue
            if i - last_sig.get(pat_name, -COOLDOWN) < COOLDOWN: continue
            last_sig[pat_name] = i

            r, reason = simulate_long(m1, i, entry_price, stop_price, risk)
            dt = datetime.fromtimestamp(b['ts_ms']/1000, tz=timezone.utc)
            pattern_trades[pat_name].append({
                'r':r,'reason':reason,'session':ses,
                'hour':dt.hour,'sym':sym,'ts':dt.strftime('%m-%d %H:%M')
            })

    if not pattern_trades:
        print('  Sin candidatos con el filtro D1 bull/neutral'); continue

    print(f'\n  {"Patron":<22} {"n":>4} {"WR%":>6} {"AvgR":>8} {"TotalR":>8}  Reasons')
    print('  ' + '-'*75)

    viable = []
    for pat, trades in sorted(pattern_trades.items(), key=lambda x: -stats(x[1])['n']):
        s = stats(trades)
        if s['n'] == 0: continue
        by_r = defaultdict(int)
        for t in trades: by_r[t['reason']] += 1
        reason_str = '  '.join(f'{r}={n}' for r,n in sorted(by_r.items(), key=lambda x:-x[1]))
        marker = ' <-- EDGE' if s['wr'] >= 55 and s['avgR'] >= 0.3 and s['n'] >= 5 else ''
        print(f'  {pat:<22} {s["n"]:>4} {s["wr"]:>5.1f}%  {s["avgR"]:>+7.3f}R  {s["totalR"]:>+7.2f}R  {reason_str}{marker}')
        if s['wr'] >= 55 and s['avgR'] >= 0.3 and s['n'] >= 5:
            viable.append((pat, s, trades))

    all_sym_results[sym] = viable

    if viable:
        print(f'\n  PATRONES CON EDGE — breakdown por sesion:')
        for pat, s_global, trades in viable[:6]:
            by_ses = defaultdict(list)
            for t in trades: by_ses[t['session']].append(t)
            print(f'\n    {pat}  (n={s_global["n"]} WR={s_global["wr"]}% AvgR={s_global["avgR"]:+.3f}R)')
            for ses_name, ts in sorted(by_ses.items(), key=lambda x: -len(x[1])):
                sg = stats(ts)
                print(f'      {ses_name:<22} n={sg["n"]:>3}  WR={sg["wr"]:>5.1f}%  AvgR={sg["avgR"]:>+.3f}R')

    print()

sep('RESUMEN GLOBAL — PATRONES CON EDGE POR SIMBOLO')
for sym, viable in all_sym_results.items():
    if not viable:
        print(f'  {sym:<10} — sin patrones con edge suficiente')
        continue
    for pat, s, _ in viable:
        print(f'  {sym:<10} {pat:<22} n={s["n"]:>3}  WR={s["wr"]:>5.1f}%  AvgR={s["avgR"]:>+.3f}R')
print()
