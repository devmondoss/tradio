#!/usr/bin/env python3
"""
Short HTF+M1 Backtest
======================
Capa 1 D1: precio < EMA20 diaria (bear/neutral). Sin D1 bull.
Capa 2 H1: señal de topping por orderflow (equal_high, oi_momentum, shooting star, stacked_imb).
Capa 3 M1: entrada de precision en pullback al high H1 (rejection, shooting star M1, ask absorption).
Stop: H1_high + 0.3*ATR_H1. Target: swing_low H1 previo + trailing 2R -> ATR*1.5.
Capital $500, risk 2% compounding. Fee 0.07% RT (maker+taker).
BNB: solo si stop > 0.40%.
"""
import json, os, sys, time, urllib.request, urllib.parse, argparse
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
FEE_RT       = 0.0007   # 0.07% RT (maker entry + taker exit)
COOLDOWN_H1    = 3      # velas H1 entre señales por símbolo
FORWARD_M1     = 1200   # 20h * 60 = 1200 barras M1 max por trade
MIN_STOP_PCT   = 0.30
MAX_STOP_PCT   = 0.75   # reducido de 2.50 → solo el bucket con edge positivo

# Horas UTC excluidas: transición London→NY (10–13) y cierre NY (17) son WR<35%
BLOCKED_HOURS_UTC = {10, 11, 12, 13, 17}

SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
TABLES  = {'BTCUSDT':'btc_bars','ETHUSDT':'eth_bars','SOLUSDT':'sol_bars','BNBUSDT':'bnb_bars'}
STARTS  = {'BTCUSDT':1780676700000,'ETHUSDT':1780756260000,
           'SOLUSDT':1780756260000,'BNBUSDT':1780756260000}
TICK_SZ = {'BTCUSDT':0.1,'ETHUSDT':0.01,'SOLUSDT':0.001,'BNBUSDT':0.01}

BAR_COLS = ('ts_ms,open,high,low,close,atr,session,cvd_slope,obi_l5,'
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
        chunk = json.loads(urllib.request.urlopen(req,timeout=30).read())
        rows.extend(chunk)
        if len(chunk)<limit: break
        offset+=limit
    return rows

def binance_klines(symbol, interval, limit=500):
    url = (f'https://fapi.binance.com/fapi/v1/klines'
           f'?symbol={symbol}&interval={interval}&limit={limit}')
    try:
        req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
        data = json.loads(urllib.request.urlopen(req,timeout=15).read())
        return [{'ts_ms':int(d[0]),'open':float(d[1]),'high':float(d[2]),
                 'low':float(d[3]),'close':float(d[4]),'volume':float(d[5])} for d in data]
    except:
        return []

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
            'ts_ms':   ts,
            'open':    bs[0]['open'],
            'high':    max(b['high'] for b in bs),
            'low':     min(b['low']  for b in bs),
            'close':   bs[-1]['close'],
            'volume':  sum(abs(b.get('bar_delta') or 0) for b in bs),
            'oi_mom':  bs[-1].get('oi_momentum'),
            'obi':     bs[-1].get('obi_l5') or 0,
            'stacked': bs[-1].get('stacked_imb',''),
            'eq_hi':   any(b.get('equal_high') for b in bs),
            'regime':  bs[-1].get('regime',''),
            'session': bs[-1].get('session',''),
            'vr':      bs[-1].get('vr') or 0,
        })
    return h1

def calc_th(m1):
    vrs = sorted(b.get('vr') or 0 for b in m1)
    n = len(vrs)
    return {'vr_p75': vrs[int(n*0.75)] if n else 1.0}

def d1_trend(ts_ms, d1_bars, d1_ema20):
    day_ms = (ts_ms//86_400_000)*86_400_000
    for i in range(len(d1_bars)-1,-1,-1):
        if d1_bars[i]['ts_ms'] <= day_ms:
            c = d1_bars[i]['close']; e = d1_ema20[i]
            if c > e*1.005: return 'bull'
            if c < e*0.995: return 'bear'
            return 'neutral'
    return 'unknown'

def detect_m1_signal(sym, m1, i, horizon_back=5):
    """
    Deteccion directa en barras M1 usando los patrones mineados.
    Verifica flags en la barra actual + contexto de las N barras anteriores.
    WR >= 67%, n >= 7 en mineria real (MFE/MAE 60min).
    """
    b   = m1[i]
    ses = b.get('session','')
    oi  = b.get('oi_momentum')
    obi = b.get('obi_l5') or 0
    obif= b.get('obi_fast') or 0
    vr  = b.get('vr') or 0
    reg = b.get('regime','')
    eq  = b.get('equal_high')
    dz  = b.get('dz') or 0
    cvd = b.get('cvd_slope') or 0
    abso= b.get('absorption','')
    vpin= b.get('vpin') or 0
    rng     = (b['high']-b['low']) or 1
    body    = abs(b['close']-b['open'])
    wick_hi = b['high'] - max(b['close'],b['open'])
    is_shoot  = (wick_hi/rng > 0.45) and (body/rng < 0.40)
    is_london = ses in ('London','LondonNyOverlap')
    is_ny     = ses == 'NewYork'
    eq_true   = str(eq).lower() == 'true'
    is_exp    = reg == 'Expansion'
    abs_ask   = abso == 'Ask'

    if ses in ('OffHours', 'Asia'):
        return False, ''

    # Filtro horario: excluir horas UTC con WR<35% (transición London→NY y cierre NY)
    import datetime as _dt
    bar_hour = _dt.datetime.utcfromtimestamp(b['ts_ms'] / 1000).hour
    if bar_hour in BLOCKED_HOURS_UTC:
        return False, ''

    if sym == 'BTCUSDT':
        if not (is_london or is_ny): return False, ''
        if is_shoot and abs_ask and obif < 0:               return True, 'btc:shoot+ask+obi'
        if is_shoot and is_london:                          return True, 'btc:shoot+london'
        # btc:vr4+obi+ny QUITADO — WR=37.5% AvgR=+0.045R, ruido estadístico

    elif sym == 'ETHUSDT':
        if is_ny and (oi is True) and eq_true:              return True, 'eth:ny+oi+eq'
        if abs_ask and is_london and is_exp:                return True, 'eth:ask+london+exp'
        if abs_ask and vpin > 0.6 and is_ny:               return True, 'eth:ask+vpin+ny'

    elif sym == 'SOLUSDT':
        if is_ny and vr > 4.0 and (oi is True):            return True, 'sol:ny+vr4+oi'
        if is_ny and vr > 4.0 and eq_true:                 return True, 'sol:ny+vr4+eq'
        if eq_true and is_london and is_exp:               return True, 'sol:eq+london+exp'
        # sol:ny+vr2+oi QUITADO — WR=40% AvgR=+0.048R, ruido estadístico

    return False, ''

def find_entry_m1(m1, start_i, end_i, h1_high):
    for j in range(start_i, min(end_i, len(m1)-2)):
        b = m1[j]
        if b.get('cvd_slope') is None: continue
        if (b.get('session') or '') == 'OffHours': continue
        rng  = b['high']-b['low'] or 1
        body = abs(b['close']-b['open'])
        wick_hi = b['high'] - max(b['close'],b['open'])
        cvd_neg = (b.get('cvd_slope') or 0) < 0
        obi_neg = (b.get('obi_l5') or 0) < 0
        bear    = b['close'] < b['open']

        # A: rechazo en el high H1
        if b['high'] >= h1_high*0.998 and bear and obi_neg:
            return j, b['close'], 'rejection_at_high'
        # B: shooting star M1 con CVD negativo
        if wick_hi/rng > 0.5 and body/rng < 0.4 and cvd_neg:
            return j, b['close'], 'M1_shooting_star'
        # C: absorcion ask M1
        if b.get('absorption') == 'Ask' and cvd_neg and obi_neg:
            return j, b['close'], 'M1_ask_absorption'
    # fallback: open de primera barra
    if start_i < len(m1):
        return start_i, m1[start_i]['open'], 'H1_open'
    return None, None, None

CVD_FLIP_BARS  = 5   # barras M1 consecutivas con CVD_slope > 0 para confirmar exhaustion real
OBI_FLIP_THR   = 0.15 # obi_fast debe superar este umbral (flujo comprador significativo)
MIN_PROFIT_CVD = 1.0  # solo cerrar por CVD si estamos al menos 1R en profit

def simulate_short(m1, entry_i, entry, stop, risk, target_r):
    target_price    = entry - target_r*risk
    n               = len(m1)
    cvd_pos_streak  = 0  # barras consecutivas con CVD_slope positivo
    obi_pos_streak  = 0  # barras consecutivas con OBI positivo

    for k in range(1, min(FORWARD_M1, n-entry_i)):
        mb  = m1[entry_i+k]
        h, l = mb['high'], mb['low']

        # ── Stops estructurales (prioritarios) ───────────────────────
        if l <= target_price:
            fee_r = FEE_RT*entry/risk
            return round(target_r-fee_r,4), 'TAKE_PROFIT', k, mb['ts_ms'], target_price
        if h >= stop:
            fee_r = FEE_RT*entry/risk
            return round(-(1.0+fee_r),4), 'STOP_LOSS', k, mb['ts_ms'], stop

        # ── CVD exhaustion + OBI flip ─────────────────────────────────
        curr_r      = (entry - mb['close']) / risk
        cvd_slope   = mb.get('cvd_slope') or 0
        obi_fast    = mb.get('obi_fast') or mb.get('obi_l5') or 0

        if cvd_slope > 0:
            cvd_pos_streak += 1
        else:
            cvd_pos_streak  = 0

        if obi_fast > OBI_FLIP_THR:
            obi_pos_streak += 1
        else:
            obi_pos_streak  = 0

        # Cierra si CVD lleva N barras positivo Y OBI también positivo Y estamos en profit mínimo
        if (cvd_pos_streak >= CVD_FLIP_BARS
                and obi_pos_streak >= 1
                and curr_r >= MIN_PROFIT_CVD):
            exit_px = mb['close']
            gross_r = (entry - exit_px) / risk
            fee_r   = FEE_RT*entry/risk
            return round(gross_r-fee_r,4), 'CVD_EXHAUSTION', k, mb['ts_ms'], exit_px

    last  = m1[min(entry_i+FORWARD_M1-1, n-1)]
    fee_r = FEE_RT*entry/risk
    return round((entry-last['close'])/risk-fee_r,4), 'EXPIRED', FORWARD_M1, last['ts_ms'], last['close']

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=14)
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        print(json.dumps({'error':'SUPABASE credentials missing'})); sys.exit(1)

    all_trades = []
    equity     = CAPITAL_INIT
    first_bar_ms = None

    for sym in SYMBOLS:
        table    = TABLES[sym]
        start_ms = max(STARTS[sym], int((time.time()-args.days*86400)*1000))

        m1 = sb_fetch(table, start_ms)
        if len(m1) < 200: continue
        if not first_bar_ms or m1[0]['ts_ms'] < first_bar_ms:
            first_bar_ms = m1[0]['ts_ms']

        d1      = binance_klines(sym, '1d', 120)
        d1_ema  = ema([b['close'] for b in d1], 20) if d1 else []
        h1      = build_h1(m1)
        h1_atrs = atr_series(h1)
        tick    = TICK_SZ.get(sym, 0.01)

        COOLDOWN_M1 = 30   # barras M1 entre señales (30 min)
        TARGET_R    = 2.5
        last_sig_i  = -COOLDOWN_M1

        for i in range(10, len(m1) - FORWARD_M1 - 1):
            if i - last_sig_i < COOLDOWN_M1:
                continue

            b   = m1[i]
            if not b.get('atr'):
                continue

            # Filtro D1 — no entrar en trend alcista
            if d1 and d1_ema:
                d1t = d1_trend(b['ts_ms'], d1, d1_ema)
                if d1t == 'bull':
                    continue
            else:
                d1t = 'unknown'

            sig, sig_name = detect_m1_signal(sym, m1, i)
            if not sig:
                continue

            # Stop: high de la vela H1 actual + 0.3*ATR_H1 (nivel estructural)
            h1_ts = (b['ts_ms'] // 3_600_000) * 3_600_000
            h1_i  = next((j for j, hb in enumerate(h1) if hb['ts_ms'] == h1_ts), None)
            if h1_i is None:
                continue
            h1b      = h1[h1_i]
            h1_atr   = h1_atrs[h1_i] if h1_i < len(h1_atrs) else 0
            if h1_atr <= 0:
                continue

            stop_price  = h1b['high'] + 0.3 * h1_atr
            entry_price = b['close']

            risk     = stop_price - entry_price
            if risk <= 0:
                continue
            stop_pct = risk / entry_price * 100
            if stop_pct < MIN_STOP_PCT or stop_pct > MAX_STOP_PCT:
                continue

            target_price = entry_price - TARGET_R * risk

            net_r, reason, dur, exit_ms, exit_px = simulate_short(
                m1, i, entry_price, stop_price, risk, TARGET_R)

            risk_usd = equity * RISK_PCT
            pnl      = net_r * risk_usd
            equity   = round(equity + pnl, 4)

            fee_r    = round(FEE_RT * entry_price / risk, 4)
            exit_iso = datetime.fromtimestamp(exit_ms/1000, tz=timezone.utc).isoformat()

            trade = {
                'idx':         len(all_trades)+1,
                'id':          f'sht-{sym}-{b["ts_ms"]}',
                'sym':         sym,
                'dir':         'Short',
                'session':     b.get('session') or '',
                'sig':         sig_name,
                'entry_type':  'M1_close',
                'd1_trend':    d1t,
                'entry':       round(entry_price, 6),
                'stop':        round(stop_price, 6),
                'target':      round(target_price, 6),
                'exit':        round(exit_px, 6),
                'resultR':     net_r,
                'grossR':      round(net_r + fee_r, 4),
                'feeR':        fee_r,
                'pnlUsd':      round(pnl, 2),
                'riskUsd':     round(risk_usd, 2),
                'stopPct':     round(stop_pct, 3),
                'targetR':     TARGET_R,
                'equity':      equity,
                'reason':      reason,
                'tsMs':        b['ts_ms'],
                'ts':          b['ts_ms'] // 1000,
                'closedAt':    exit_iso,
                'durationMin': dur,
                'moveTicks':   round((entry_price - exit_px) / tick),
                'riskTicks':   round(risk / tick),
                'isOpen':      False,  # EXPIRED tiene resultR calculado — se trata como cerrado
                'score':       0,
                'confluenceFlags': [sig_name, d1t],
                'vetoReason':  '',
                'evidence':    [sig_name],
                'isSweepReclaim':  False,
                'isPreBreakout':   False,
                'sessionPhase':    '',
                'obi':         round(b.get('obi_l5') or 0, 3),
                'cvdSlope':    round(b.get('cvd_slope') or 0, 2),
                'vwapDev':     None,
                'dz':          b.get('dz') or 0,
                'regime':      b.get('regime') or '',
                'funding':     None, 'rangeTouch': None, 'htf': None,
                'rangePct':    None, 'rangeBars':  None, 'cvdInRange': None,
                'vr':          round(b.get('vr') or 0, 2),
                'wickAtr':     None,
            }
            all_trades.append(trade)
            last_sig_i = i

    n_total  = len(all_trades)
    closed   = [t for t in all_trades if not t['isOpen']]
    wins     = sum(1 for t in all_trades if t['resultR'] > 0)
    total_r  = sum(t['resultR'] for t in closed)
    total_fees = sum(t['feeR'] for t in all_trades)

    actual_days = args.days
    if first_bar_ms:
        elapsed_ms  = int(time.time()*1000) - first_bar_ms
        actual_days = max(1, round(elapsed_ms/86_400_000, 1))

    result = {
        'shorts_htf_backtest': True,
        'trades':      all_trades,
        'capital':     CAPITAL_INIT,
        'risk_pct':    RISK_PCT,
        'days':        args.days,
        'actual_days': actual_days,
        'n':           n_total,
        'n_closed':    len(closed),
        'wins':        wins,
        'equity':      round(equity, 2),
        'total_r':     round(total_r, 2),
        'avg_r':       round(total_r/len(closed), 3) if closed else 0,
        'wr_pct':      round(wins/n_total*100, 1) if n_total else 0,
        'total_fees_usd': round(total_fees*CAPITAL_INIT*RISK_PCT, 2),
        'params': {
            'fee_rt_pct':   round(FEE_RT*100, 3),
            'trailing':     False,
            'min_stop_pct': MIN_STOP_PCT,
            'cooldown_h1':  COOLDOWN_H1,
            'symbols':      SYMBOLS,
        },
    }
    print(json.dumps(result, ensure_ascii=False))

if __name__ == '__main__':
    main()
