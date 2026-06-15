#!/usr/bin/env python3
"""
BTC VWAP Rastro — Backtest de target dinámico
===============================================
Compara target fijo 2.5R vs target dinámico = London VWAP close del día anterior.

Concepto:
  Cuando London cierra (~12:00 UTC), su VWAP queda como nivel institucional.
  En la siguiente sesión, ese precio es un "imán" natural — target más conservador
  pero estadísticamente más probable de tocarse que un 2.5R fijo.

Salida:
  - Base: resultado con target fijo 2.5R (btc_shorts_v1.py actual)
  - Rastro: resultado con target = prev_london_vwap (si está entre entry y 2.5R)
  - Comparación de WR, AvgR, equity
"""
import json, os, urllib.request, urllib.parse
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

ROOT = Path(__file__).parent.parent.parent.parent
_env = {}
for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        _env[k.strip()] = v.strip().strip('"').strip("'")

URL = _env['SUPABASE_URL']
KEY = _env['SUPABASE_KEY']

# ── Params ────────────────────────────────────────────────────────────────────
CAPITAL_INIT  = 500.0
RISK_PCT      = 0.02
FEE_RT        = 0.0007
TARGET_R      = 2.5
MIN_STOP_PCT  = 0.30
MAX_STOP_PCT  = 0.75
COOLDOWN_M1   = 30
FORWARD_M1    = 1200
CVD_FLIP_BARS = 5
OBI_FLIP_THR  = 0.15
MIN_PROFIT_CVD= 1.0

# London session UTC hours (07:00–12:00 UTC)
LONDON_START_H = 7
LONDON_END_H   = 12

COLS = ('ts_ms,open,high,low,close,volume,atr,session,cvd_slope,obi_l5,'
        'vwap,vr,oi_momentum,bar_delta,regime,dz,absorption,'
        'stacked_imb,equal_high,obi_fast,sweep_confirmed,'
        'asian_high,asian_low,prev_day_high,prev_day_low,'
        'thin_above,ask_wall,vp_poc,vp_vah')

# ── Fetch ─────────────────────────────────────────────────────────────────────

MICRO_START_MS = 1780676700000  # 2026-06-05 16:25 UTC — inicio de microestructura

def fetch(days=30):
    import time
    start_ms = max(int((time.time() - days * 86400) * 1000), MICRO_START_MS)
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

# ── VWAP Rastro: London VWAP close por día UTC ────────────────────────────────

def build_london_vwap_map(m1):
    """
    Para cada barra M1, calcula el prev_london_vwap = VWAP del cierre de London del día anterior.
    London = barras con hora UTC entre 07:00 y 12:00 (exclusive).
    """
    # Agrupar por día UTC
    by_day = defaultdict(list)
    for b in m1:
        day = b['ts_ms'] // 86_400_000
        by_day[day].append(b)

    # Calcular London VWAP close para cada día
    london_vwap_close = {}  # day -> vwap al final de London
    for day, bars in sorted(by_day.items()):
        cum_pv = cum_vol = 0.0
        last_vwap = None
        for b in sorted(bars, key=lambda x: x['ts_ms']):
            hour = (b['ts_ms'] // 3_600_000) % 24
            if LONDON_START_H <= hour < LONDON_END_H:
                vol = b.get('volume') or 0
                if vol > 0:
                    typical = (b['high'] + b['low'] + b['close']) / 3.0
                    cum_pv  += typical * vol
                    cum_vol += vol
                    if cum_vol > 0:
                        last_vwap = cum_pv / cum_vol
        if last_vwap:
            london_vwap_close[day] = last_vwap

    # Para cada barra, asignar el London VWAP close del día ANTERIOR
    prev_london_vwap = {}
    for b in m1:
        day = b['ts_ms'] // 86_400_000
        prev_london_vwap[b['ts_ms']] = london_vwap_close.get(day - 1)

    return prev_london_vwap, london_vwap_close

# ── Señal (igual que btc_shorts_v1.py) ───────────────────────────────────────

def detect_signal(b):
    ses = b.get('session', '')
    if ses not in ('London', 'LondonNyOverlap', 'NewYork'):
        return False, ''
    rng     = (b['high'] - b['low']) or 1e-10
    body    = abs(b['close'] - b['open'])
    wick_hi = b['high'] - max(b['close'], b['open'])
    if not ((wick_hi / rng) > 0.45 and (body / rng) < 0.40):
        return False, ''
    close = b['close']
    tol   = 0.002
    sweep = b.get('sweep_confirmed') is True or str(b.get('sweep_confirmed')).lower() == 'true'
    pdh   = b.get('prev_day_high') or 0
    ah    = b.get('asian_high') or 0
    if sweep: return False, ''
    if pdh > 0 and (close > pdh or abs(close - pdh)/pdh <= tol): return False, ''
    if ah > 0 and abs(close - ah)/ah <= tol: return False, ''
    vwap      = b.get('vwap') or 0
    oi        = b.get('oi_momentum') is True or str(b.get('oi_momentum')).lower() == 'true'
    below_vwap = vwap > 0 and close < vwap
    below_ah  = ah > 0 and close < ah
    stk_bear  = b.get('stacked_imb', '') == 'Bearish'
    delta_neg = (b.get('bar_delta') or 0) < 0
    abs_ask   = b.get('absorption', '') == 'Ask'
    if oi and below_vwap and below_ah: return True, 'btc:oi+bvwap+bah'
    if oi and delta_neg and below_vwap: return True, 'btc:oi+delta_neg+bvwap'
    if oi and stk_bear: return True, 'btc:oi+stk_bear'
    if oi and below_vwap: return True, 'btc:oi+bvwap'
    if abs_ask: return True, 'btc:abs_ask'
    if below_vwap and below_ah and stk_bear: return True, 'btc:bvwap+bah+stk'
    return False, ''

# ── Simulación dual: fijo 2.5R vs rastro ─────────────────────────────────────

def simulate_dual(m1, ei, entry, stop, risk, rastro):
    """
    Simula el mismo trade con dos targets:
      - fixed:  entry - 2.5R * risk
      - rastro: prev_london_vwap (si existe y está entre entry y fixed TP)
    Retorna (fixed_r, fixed_reason, rastro_r, rastro_reason, rastro_used)
    """
    fixed_tp  = entry - TARGET_R * risk
    # rastro solo aplica si está entre entry y fixed_tp (es decir, más cerca)
    use_rastro = rastro is not None and fixed_tp < rastro < entry
    rastro_tp = rastro if use_rastro else fixed_tp

    fixed_r = fixed_reason = None
    rast_r  = rast_reason  = None

    cs = os = 0
    for k in range(1, min(FORWARD_M1, len(m1)-ei)):
        mb = m1[ei+k]
        h, l = mb['high'], mb['low']

        # Fixed TP/SL
        if fixed_r is None:
            if l <= fixed_tp:
                fixed_r = round(TARGET_R - FEE_RT*entry/risk, 4)
                fixed_reason = 'TAKE_PROFIT'
            elif h >= stop:
                fixed_r = round(-(1.0 + FEE_RT*entry/risk), 4)
                fixed_reason = 'STOP_LOSS'

        # Rastro TP/SL
        if rast_r is None:
            if l <= rastro_tp:
                if use_rastro:
                    # R alcanzado = (entry - rastro) / risk
                    gross = (entry - rastro_tp) / risk
                    rast_r = round(gross - FEE_RT*entry/risk, 4)
                    rast_reason = 'RASTRO_TP'
                else:
                    rast_r = round(TARGET_R - FEE_RT*entry/risk, 4)
                    rast_reason = 'TAKE_PROFIT'
            elif h >= stop:
                rast_r = round(-(1.0 + FEE_RT*entry/risk), 4)
                rast_reason = 'STOP_LOSS'

        # CVD exhaustion — aplica a ambos escenarios
        cr = (entry - mb['close'])/risk
        cv = mb.get('cvd_slope') or 0
        of = mb.get('obi_fast') or 0
        cs = cs+1 if cv > 0 else 0
        os = os+1 if of > OBI_FLIP_THR else 0
        cvd_exit = cs >= CVD_FLIP_BARS and os >= 1 and cr >= MIN_PROFIT_CVD
        if fixed_r is None and cvd_exit:
            fixed_r = round((entry-mb['close'])/risk - FEE_RT*entry/risk, 4)
            fixed_reason = 'CVD_EXHAUSTION'
        if rast_r is None and cvd_exit:
            rast_r = round((entry-mb['close'])/risk - FEE_RT*entry/risk, 4)
            rast_reason = 'CVD_EXHAUSTION'

        if fixed_r is not None and rast_r is not None:
            break

    if fixed_r is None:
        last = m1[min(ei+FORWARD_M1-1, len(m1)-1)]
        fixed_r = round((entry-last['close'])/risk - FEE_RT*entry/risk, 4)
        fixed_reason = 'EXPIRED'
    if rast_r is None:
        last = m1[min(ei+FORWARD_M1-1, len(m1)-1)]
        rast_r = round((entry-last['close'])/risk - FEE_RT*entry/risk, 4)
        rast_reason = 'EXPIRED'

    return fixed_r, fixed_reason, rast_r, rast_reason, use_rastro

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=30)
    args = ap.parse_args()

    print(f'Fetching {args.days}d of BTC M1 bars...')
    m1 = fetch(args.days)
    print(f'  {len(m1)} barras')

    print('Building D1 trend...')
    d1_trend = d1_trend_map(m1)

    print('Building H1 ATR...')
    h1 = build_h1(m1)
    h1_atr_vals = atr14(h1)
    h1_atr_map = {h1[i]['ts_ms']: h1_atr_vals[i] for i in range(len(h1))}

    print('Reconstructing London VWAP rastro...')
    prev_lv_map, london_close_map = build_london_vwap_map(m1)

    # Stats por día para ver cuántos días tenemos rastro
    days_with_rastro = len(london_close_map)
    print(f'  London VWAP calculado para {days_with_rastro} días')
    for day, vwap in sorted(london_close_map.items())[-5:]:
        dt = datetime.fromtimestamp(day*86400, tz=timezone.utc).strftime('%Y-%m-%d')
        print(f'  {dt}: London VWAP close = {vwap:.2f}')

    # Backtest
    last_sig_i   = -COOLDOWN_M1
    fixed_trades  = []
    rastro_trades = []

    for i in range(10, len(m1) - FORWARD_M1 - 1):
        if i - last_sig_i < COOLDOWN_M1:
            continue

        b = m1[i]
        if not b.get('cvd_slope'):   # solo barras con microestructura completa
            continue

        # D1 trend
        if d1_trend.get(b['ts_ms'], 'bull') == 'bull':
            continue

        ok, sig = detect_signal(b)
        if not ok:
            continue

        # Stop estructural H1 (igual que btc_shorts_v1.py)
        h1_ts = (b['ts_ms'] // 3_600_000) * 3_600_000
        h1_i  = next((j for j, hb in enumerate(h1) if hb['ts_ms'] == h1_ts), None)
        if h1_i is None:
            continue
        h1_atr = h1_atr_map.get(h1_ts, 0)
        if h1_atr <= 0:
            continue

        entry = b['close']
        stop  = h1[h1_i]['high'] + 0.3 * h1_atr
        risk  = stop - entry
        if risk <= 0:
            continue

        stop_pct = risk / entry * 100
        if not (MIN_STOP_PCT <= stop_pct <= MAX_STOP_PCT):
            continue

        rastro = prev_lv_map.get(b['ts_ms'])
        last_sig_i = i

        fr, frsn, rr, rrsn, used_rastro = simulate_dual(m1, i, entry, stop, risk, rastro)

        dt = datetime.fromtimestamp(b['ts_ms']//1000, tz=timezone.utc).strftime('%m-%d %H:%M')
        fixed_trades.append({'r': fr, 'reason': frsn, 'sig': sig, 'dt': dt,
                             'rastro': rastro, 'entry': entry})
        rastro_trades.append({'r': rr, 'reason': rrsn, 'sig': sig, 'dt': dt,
                              'used_rastro': used_rastro, 'rastro': rastro, 'entry': entry})

    # ── Resultados ────────────────────────────────────────────────────────────
    def stats(trades, label):
        n = len(trades)
        if n == 0:
            print(f'{label}: sin trades')
            return
        wins = [t for t in trades if t['r'] > 0]
        total_r = sum(t['r'] for t in trades)
        wr = len(wins)/n*100
        avg_r = total_r/n
        # Equity simple (no compounding para comparación limpia)
        cap = CAPITAL_INIT
        for t in trades:
            cap += t['r'] * CAPITAL_INIT * RISK_PCT
        print(f'\n{label}')
        print(f'  n={n}  WR={wr:.1f}%  AvgR={avg_r:+.3f}R  TotalR={total_r:+.2f}R  ${CAPITAL_INIT:.0f}->${cap:.0f}')
        reasons = {}
        for t in trades:
            reasons[t['reason']] = reasons.get(t['reason'], 0) + 1
        print(f'  exits: {reasons}')

    print('\n' + '='*60)
    print(f'BACKTEST {args.days}d — BTC SHORTS VWAP RASTRO')
    print('='*60)

    stats(fixed_trades,  'BASE (target fijo 2.5R)')
    stats(rastro_trades, 'RASTRO (prev London VWAP si entre entry y 2.5R)')

    # Detalle: cuántos trades usaron el rastro
    used = [t for t in rastro_trades if t['used_rastro']]
    not_used = [t for t in rastro_trades if not t['used_rastro']]
    print(f'\n  Trades con rastro disponible: {len(used)}/{len(rastro_trades)}')

    if used:
        wins_used = [t for t in used if t['r'] > 0]
        avg_r_used = sum(t['r'] for t in used)/len(used)
        print(f'  WR con rastro: {len(wins_used)/len(used)*100:.1f}%  AvgR={avg_r_used:+.3f}R')

        # Trade por trade comparación donde rastro difiere de fixed
        print('\n  Diferencias (rastro vs fijo):')
        print(f'  {"Fecha":12} {"Entry":>9} {"Rastro":>9} {"Gap%":>6} {"Fixed":>8} {"Rastro":>8}')
        print(f'  {"-"*12} {"-"*9} {"-"*9} {"-"*6} {"-"*8} {"-"*8}')
        for f, r in zip([t for t in fixed_trades if t['dt'] in {x['dt'] for x in used}],
                        used):
            gap = (r['entry'] - r['rastro']) / r['entry'] * 100
            diff = '+' if r['r'] > f['r'] else ('-' if r['r'] < f['r'] else '=')
            print(f'  {r["dt"]:12} {r["entry"]:9.2f} {r["rastro"]:9.2f} {gap:5.2f}% '
                  f'{f["r"]:+7.3f}R {r["r"]:+7.3f}R {diff}')

if __name__ == '__main__':
    main()
