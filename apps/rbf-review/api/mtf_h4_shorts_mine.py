#!/usr/bin/env python3
"""
MTF Shorts — Mining de patrones bajo filtro H4 EMA20.

Objetivo: encontrar señales M1 que mantengan WR≥55% / AvgR≥+0.30R
dentro de ventanas H4 bear/neutral, aumentando la frecuencia de trades
respecto al baseline H4 (n=55 en 14d).

Metodología:
  - Filtro macro: H4 EMA20 bear o neutral (precio ≤ EMA20×1.005)
  - Stop estructural: H1_high + 0.3×ATR_H1, con 0.30% < stop_pct < 0.75%
  - Horizonte: simulate_short real (CVD exhaustion + TAKE_PROFIT 2.5R + SL)
  - Cooldown: 30 barras M1 por patrón (no por símbolo, para capturar máx señales)
  - Umbral edge: WR ≥ 55% AND AvgR ≥ +0.30R AND n ≥ 4
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
MIN_N      = 4      # mínimo de trades para considerar un patrón

SYMS = {
    'BTCUSDT': {'table':'btc_bars', 'start':1780676700000},
    'ETHUSDT': {'table':'eth_bars', 'start':1780756260000},
    'SOLUSDT': {'table':'sol_bars', 'start':1780756260000},
    'BNBUSDT': {'table':'bnb_bars', 'start':1780756260000},
    'XRPUSDT': {'table':'xrp_bars', 'start':1780756260000},
}

BAR_COLS = ('ts_ms,open,high,low,close,volume,atr,session,cvd_slope,obi_l5,'
            'vwap,vr,oi_momentum,bar_delta,regime,dz,absorption,vpin,'
            'swing_low_50,swing_high_50,stacked_imb,thin_above,ask_wall,'
            'equal_high,cvd_divergence,obi_fast')

# ── Helpers ───────────────────────────────────────────────────────────────────

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

def h4_trend_series(m1):
    buckets = defaultdict(list)
    for b in m1:
        h4_ts = (b['ts_ms'] // (4*3_600_000)) * (4*3_600_000)
        buckets[h4_ts].append(b)
    h4 = []
    for ts in sorted(buckets):
        bs = buckets[ts]
        h4.append({'ts_ms':ts,'close':bs[-1]['close']})
    if len(h4) < 20:
        return {}
    closes = [b['close'] for b in h4]
    h4_ema = ema(closes, 20)
    h4_map = {}
    for i, b in enumerate(h4):
        c, e = b['close'], h4_ema[i]
        if c > e*1.005:   h4_map[b['ts_ms']] = 'bull'
        elif c < e*0.995: h4_map[b['ts_ms']] = 'bear'
        else:             h4_map[b['ts_ms']] = 'neutral'
    result = {}
    for b in m1:
        h4_ts = (b['ts_ms'] // (4*3_600_000)) * (4*3_600_000)
        result[b['ts_ms']] = h4_map.get(h4_ts, 'unknown')
    return result

def simulate_short(m1, entry_i, entry, stop, risk):
    target = entry - TARGET_R * risk
    cvd_pos = obi_pos = 0
    for k in range(1, min(FORWARD_M1, len(m1)-entry_i)):
        mb = m1[entry_i+k]
        if mb['low'] <= target:
            return round(TARGET_R - FEE_RT*entry/risk, 4), 'TAKE_PROFIT'
        if mb['high'] >= stop:
            return round(-(1.0 + FEE_RT*entry/risk), 4), 'STOP_LOSS'
        curr_r = (entry - mb['close']) / risk
        cvd  = mb.get('cvd_slope') or 0
        obif = mb.get('obi_fast') or mb.get('obi_l5') or 0
        cvd_pos  = cvd_pos+1 if cvd > 0 else 0
        obi_pos  = obi_pos+1 if obif > OBI_THR else 0
        if cvd_pos >= CVD_BARS and obi_pos >= 1 and curr_r >= MIN_PROFIT:
            return round((entry - mb['close'])/risk - FEE_RT*entry/risk, 4), 'CVD_EXHAUSTION'
    last = m1[min(entry_i+FORWARD_M1, len(m1)-1)]
    return round((entry - last['close'])/risk - FEE_RT*entry/risk, 4), 'EXPIRED'

def stats(trades):
    if not trades: return {'n':0,'wr':0,'avgR':0,'totalR':0}
    n = len(trades); wins = sum(1 for t in trades if t['r'] > 0)
    tr = sum(t['r'] for t in trades)
    return {'n':n,'wr':round(wins/n*100,1),'avgR':round(tr/n,3),'totalR':round(tr,2)}

def sep(t): print(f'\n{"="*70}\n  {t}\n{"="*70}')

# ── Candidatos de patrones ────────────────────────────────────────────────────

def get_short_patterns(b, highs_50):
    """
    Genera todos los candidatos de patrones para shorts.
    Retorna lista de (nombre, condicion_bool).
    Los patrones actuales (baseline D1) están marcados con [BASE].
    Los nuevos candidatos están sin marca.
    """
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
    vpin_ = float(b.get('vpin') or 0)
    bdelta= float(b.get('bar_delta') or 0)
    cvd_div = b.get('cvd_divergence','')

    rng     = (b['high'] - b['low']) or 1
    body    = abs(b['close'] - b['open'])
    wick_hi = b['high'] - max(b['close'], b['open'])
    wick_lo = min(b['close'], b['open']) - b['low']
    bear_bar = b['close'] < b['open']

    # equal_high: high actual dentro del 0.03% del máximo de las 50 barras previas
    eq_hi = False
    if highs_50:
        max50 = max(highs_50)
        eq_hi = (abs(b['high'] - max50) / max50 <= 0.0003) if max50 > 0 else False

    is_shoot   = (wick_hi/rng > 0.45) and (body/rng < 0.40)
    is_london  = ses in ('London','LondonNyOverlap')
    is_ny      = ses == 'NewYork'
    is_exp     = reg == 'Expansion'
    is_comp    = reg == 'Compression'
    abs_ask    = abso == 'Ask'
    stk_bear   = stk == 'Bearish'
    oi_true    = oi is True or str(oi).lower() == 'true'

    # Flags derivados
    obi_neg    = obi  < -0.15
    obi_neg_s  = obif < -0.15         # OBI fast negativo
    cvd_neg    = cvd  < -0.10
    dz_sell    = dz   < -0.5
    dz_sell_s  = dz   < -1.0          # dz fuerte
    high_vr    = vr   > 4.0
    mod_vr     = vr   > 2.0
    bear_delta = bdelta < -50
    vpin_hi    = vpin_ > 0.60
    bear_div   = cvd_div == 'BearishAbsorption'

    return [
        # ── BASELINE (patrones existentes, bajo D1, ahora probados bajo H4) ──
        # BTC
        ('[BASE] shoot+ask+obi',     is_shoot and abs_ask and obif < 0),
        ('[BASE] shoot+london',      is_shoot and is_london),
        # ETH
        ('[BASE] ny+oi+eq',          is_ny and oi_true and eq_hi),
        ('[BASE] ask+london+exp',    abs_ask and is_london and is_exp),
        ('[BASE] ask+vpin+ny',       abs_ask and vpin_hi and is_ny),
        # SOL
        ('[BASE] ny+vr4+oi',         is_ny and high_vr and oi_true),
        ('[BASE] ny+vr4+eq',         is_ny and high_vr and eq_hi),
        ('[BASE] eq+london+exp',     eq_hi and is_london and is_exp),
        # BNB/XRP
        ('[BASE] eq+ny+oi',          eq_hi and is_ny and oi_true),
        ('[BASE] oi+ny',             oi_true and is_ny),
        ('[BASE] ask+ny',            abs_ask and is_ny),

        # ── NUEVOS CANDIDATOS ────────────────────────────────────────────────

        # Shooting star con variantes de confirmación
        ('shoot+obi_neg',            is_shoot and obi_neg),
        ('shoot+cvd_neg',            is_shoot and cvd_neg),
        ('shoot+dz_sell',            is_shoot and dz_sell),
        ('shoot+stk_bear',           is_shoot and stk_bear),
        ('shoot+oi',                 is_shoot and oi_true),
        ('shoot+ny',                 is_shoot and is_ny),
        ('shoot+ny+oi',              is_shoot and is_ny and oi_true),
        ('shoot+london+oi',          is_shoot and is_london and oi_true),
        ('shoot+bear_bar',           is_shoot and bear_bar),
        ('shoot+bear_bar+obi',       is_shoot and bear_bar and obi_neg),

        # Equal high (doble techo) — el más confiable en baseline
        ('eq+oi',                    eq_hi and oi_true),
        ('eq+london',                eq_hi and is_london),
        ('eq+ny',                    eq_hi and is_ny),
        ('eq+stk_bear',              eq_hi and stk_bear),
        ('eq+obi_neg',               eq_hi and obi_neg),
        ('eq+cvd_neg',               eq_hi and cvd_neg),
        ('eq+vr4',                   eq_hi and high_vr),
        ('eq+dz_sell',               eq_hi and dz_sell),
        ('eq+ny+cvd',                eq_hi and is_ny and cvd_neg),
        ('eq+london+oi',             eq_hi and is_london and oi_true),

        # Stacked imbalance bajista
        ('stk_bear+london',          stk_bear and is_london),
        ('stk_bear+ny',              stk_bear and is_ny),
        ('stk_bear+oi',              stk_bear and oi_true),
        ('stk_bear+obi',             stk_bear and obi_neg),
        ('stk_bear+cvd',             stk_bear and cvd_neg),
        ('stk_bear+london+oi',       stk_bear and is_london and oi_true),
        ('stk_bear+ny+oi',           stk_bear and is_ny and oi_true),
        ('stk_bear+exp',             stk_bear and is_exp),

        # Ask absorption (institucional vendiendo)
        ('ask+obi',                  abs_ask and obi_neg),
        ('ask+cvd',                  abs_ask and cvd_neg),
        ('ask+dz',                   abs_ask and dz_sell),
        ('ask+oi',                   abs_ask and oi_true),
        ('ask+stk_bear',             abs_ask and stk_bear),
        ('ask+london',               abs_ask and is_london),
        ('ask+ny+oi',                abs_ask and is_ny and oi_true),
        ('ask+london+oi',            abs_ask and is_london and oi_true),
        ('ask+vpin',                 abs_ask and vpin_hi),
        ('ask+exp',                  abs_ask and is_exp),

        # OI momentum — señal institucional directa
        ('oi+london',                oi_true and is_london),
        ('oi+exp',                   oi_true and is_exp),
        ('oi+stk_bear',              oi_true and stk_bear),
        ('oi+obi_neg',               oi_true and obi_neg),
        ('oi+dz_sell',               oi_true and dz_sell),
        ('oi+vr4+london',            oi_true and high_vr and is_london),
        ('oi+cvd_neg',               oi_true and cvd_neg),

        # Volume ratio alto — expansión de volumen = momentum
        ('vr4+london',               high_vr and is_london),
        ('vr4+ny',                   high_vr and is_ny),
        ('vr4+obi_neg',              high_vr and obi_neg),
        ('vr4+stk_bear',             high_vr and stk_bear),
        ('vr4+cvd_neg',              high_vr and cvd_neg),
        ('vr4+ask',                  high_vr and abs_ask),

        # OBI fast — presión en libro inmediata
        ('obif_neg+london',          obi_neg_s and is_london),
        ('obif_neg+ny',              obi_neg_s and is_ny),
        ('obif_neg+oi',              obi_neg_s and oi_true),
        ('obif_neg+eq',              obi_neg_s and eq_hi),
        ('obif_neg+stk',             obi_neg_s and stk_bear),

        # CVD divergencia bajista (precio sube pero CVD cae)
        ('bear_div+london',          bear_div and is_london),
        ('bear_div+ny',              bear_div and is_ny),
        ('bear_div+oi',              bear_div and oi_true),
        ('bear_div+eq',              bear_div and eq_hi),

        # Regime Expansion — fase de mayor rango y dirección
        ('exp+oi+ny',                is_exp and oi_true and is_ny),
        ('exp+stk+london',           is_exp and stk_bear and is_london),
        ('exp+obi+london',           is_exp and obi_neg and is_london),
        ('exp+vr4',                  is_exp and high_vr),
        ('exp+ask+oi',               is_exp and abs_ask and oi_true),

        # DZ score (desviación del CVD respecto a su media)
        ('dz_sell+london',           dz_sell and is_london),
        ('dz_sell+ny',               dz_sell and is_ny),
        ('dz_sell+oi',               dz_sell and oi_true),
        ('dz_sell_s+london',         dz_sell_s and is_london),
        ('dz_sell_s+ny',             dz_sell_s and is_ny),

        # Bar delta negativo — más vendedores que compradores en la vela
        ('bear_delta+london',        bear_delta and is_london),
        ('bear_delta+ny',            bear_delta and is_ny),
        ('bear_delta+oi',            bear_delta and oi_true),
        ('bear_delta+stk',           bear_delta and stk_bear),

        # Combos de 3 factores independientes al baseline
        ('stk+oi+obi',               stk_bear and oi_true and obi_neg),
        ('stk+eq+ny',                stk_bear and eq_hi and is_ny),
        ('stk+eq+london',            stk_bear and eq_hi and is_london),
        ('shoot+eq+ny',              is_shoot and eq_hi and is_ny),
        ('shoot+stk+london',         is_shoot and stk_bear and is_london),
        ('ask+eq+london',            abs_ask and eq_hi and is_london),
        ('ask+eq+ny',                abs_ask and eq_hi and is_ny),
        ('vr4+eq+ny',                high_vr and eq_hi and is_ny),
        ('oi+eq+exp',                oi_true and eq_hi and is_exp),
    ]

# ── Main ──────────────────────────────────────────────────────────────────────

all_sym_results = {}

for sym, cfg in SYMS.items():
    sep(f'MINING SHORTS H4 — {sym}')
    start_ms = max(cfg['start'], int((time.time()-DAYS*86400)*1000))

    print(f'  Cargando {sym}...', end=' ', flush=True)
    m1 = sb_fetch(cfg['table'], start_ms)
    print(f'{len(m1)} barras')

    if len(m1) < 200:
        print('  Insuficientes datos'); continue

    h1      = build_h1(m1)
    h1_atr  = atr_series(h1)
    h4_map  = h4_trend_series(m1)

    bear_neutral = sum(1 for b in m1 if h4_map.get(b['ts_ms']) in ('bear','neutral'))
    bull_bars    = sum(1 for b in m1 if h4_map.get(b['ts_ms']) == 'bull')
    print(f'  H4 bear/neutral: {bear_neutral} barras ({bear_neutral/len(m1)*100:.0f}%) | bull (bloqueado): {bull_bars} ({bull_bars/len(m1)*100:.0f}%)')

    pattern_trades = defaultdict(list)
    last_sig = {}  # cooldown por patrón (no por símbolo)

    for i in range(50, len(m1)-FORWARD_M1-1):
        b   = m1[i]
        ses = b.get('session','')
        if ses in ('OffHours','Asia'): continue
        if not b.get('atr'): continue

        # Filtro H4: solo bear o neutral
        if h4_map.get(b['ts_ms'],'unknown') == 'bull': continue

        # Stop estructural H1
        h1_ts = (b['ts_ms']//3_600_000)*3_600_000
        h1_i  = next((j for j,hb in enumerate(h1) if hb['ts_ms']==h1_ts), None)
        if h1_i is None: continue
        h1b  = h1[h1_i]
        h1_a = h1_atr[h1_i] if h1_i < len(h1_atr) else 0
        if h1_a <= 0: continue

        stop_price  = h1b['high'] + 0.3 * h1_a
        entry_price = b['close']
        risk = stop_price - entry_price
        if risk <= 0: continue
        stop_pct = risk / entry_price * 100
        if stop_pct < MIN_STOP or stop_pct > MAX_STOP: continue

        highs_50 = [m1[j]['high'] for j in range(max(0,i-50), i)]

        for pat_name, matched in get_short_patterns(b, highs_50):
            if not matched: continue
            if i - last_sig.get(pat_name, -COOLDOWN) < COOLDOWN: continue
            last_sig[pat_name] = i

            r, reason = simulate_short(m1, i, entry_price, stop_price, risk)
            dt = datetime.fromtimestamp(b['ts_ms']/1000, tz=timezone.utc)
            pattern_trades[pat_name].append({
                'r':r, 'reason':reason, 'session':ses,
                'hour':dt.hour, 'sym':sym, 'ts':dt.strftime('%m-%d %H:%M'),
                'stop_pct':round(stop_pct,3),
            })

    if not pattern_trades:
        print('  Sin candidatos'); continue

    # Ordenar por AvgR desc, mostrar solo los que tengan n >= MIN_N
    rows = []
    for pat, trades in pattern_trades.items():
        s = stats(trades)
        if s['n'] < MIN_N: continue
        rows.append((pat, s, trades))
    rows.sort(key=lambda x: -x[1]['avgR'])

    print(f'\n  {"Patron":<28} {"n":>4} {"WR%":>6} {"AvgR":>8} {"TotalR":>8}  Reasons')
    print('  ' + '-'*80)

    viable = []
    for pat, s, trades in rows:
        by_r = defaultdict(int)
        for t in trades: by_r[t['reason']] += 1
        reason_str = '  '.join(f'{r}={n}' for r,n in sorted(by_r.items(), key=lambda x:-x[1]))
        is_edge = s['wr'] >= 55 and s['avgR'] >= 0.30
        marker = ' <-- EDGE' if is_edge else ''
        tag = '  [BASE]' if '[BASE]' in pat else ''
        clean = pat.replace('[BASE] ','')
        print(f'  {clean:<28} {s["n"]:>4} {s["wr"]:>5.1f}%  {s["avgR"]:>+7.3f}R  {s["totalR"]:>+7.2f}R  {reason_str}{tag}{marker}')
        if is_edge:
            viable.append((pat, s, trades))

    all_sym_results[sym] = viable

    if viable:
        print(f'\n  EDGE PATTERNS — breakdown por sesion:')
        for pat, sg, trades in viable[:8]:
            by_ses = defaultdict(list)
            for t in trades: by_ses[t['session']].append(t)
            clean = pat.replace('[BASE] ','')
            is_new = '[BASE]' not in pat
            label = f"{'[NEW] ' if is_new else '[BASE] '}{clean}"
            print(f'\n    {label}  (n={sg["n"]} WR={sg["wr"]}% AvgR={sg["avgR"]:+.3f}R)')
            for ses_name, ts2 in sorted(by_ses.items(), key=lambda x: -len(x[1])):
                sg2 = stats(ts2)
                if sg2['n'] < 2: continue
                print(f'      {ses_name:<22} n={sg2["n"]:>3}  WR={sg2["wr"]:>5.1f}%  AvgR={sg2["avgR"]:>+.3f}R')
    print()

# ── Resumen global ────────────────────────────────────────────────────────────

sep('RESUMEN -- PATRONES CON EDGE (WR>=55% AvgR>=+0.30R n>=4)')
print(f'  {"Simbolo":<10} {"Patron":<28} {"n":>4} {"WR%":>6} {"AvgR":>8} {"TotalR":>8}  Tipo')
print('  ' + '-'*80)
for sym, viable in all_sym_results.items():
    if not viable:
        print(f'  {sym:<10} — sin patrones con edge suficiente')
        continue
    for pat, s, _ in sorted(viable, key=lambda x: -x[1]['avgR']):
        tipo = 'BASE' if '[BASE]' in pat else 'NUEVO'
        clean = pat.replace('[BASE] ','')
        print(f'  {sym:<10} {clean:<28} {s["n"]:>4} {s["wr"]:>5.1f}%  {s["avgR"]:>+7.3f}R  {s["totalR"]:>+7.2f}R  {tipo}')
print()

sep('NUEVOS patrones con edge (no en baseline)')
found_new = False
for sym, viable in all_sym_results.items():
    for pat, s, trades in viable:
        if '[BASE]' not in pat:
            found_new = True
            by_ses = defaultdict(list)
            for t in trades: by_ses[t['session']].append(t)
            ses_str = '  '.join(
                f"{ses}:{stats(ts2)['n']}t WR={stats(ts2)['wr']}%"
                for ses, ts2 in sorted(by_ses.items(), key=lambda x:-len(x[1]))
                if stats(ts2)['n'] >= 2
            )
            print(f'  {sym:<10} {pat:<28} n={s["n"]:>3}  WR={s["wr"]:>5.1f}%  AvgR={s["avgR"]:>+.3f}R  |  {ses_str}')
if not found_new:
    print('  Ninguno encontrado con los criterios actuales')
print()
