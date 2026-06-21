#!/usr/bin/env python3
"""
BTC Shorts — MTF v1
====================
Backtest dedicado solo a BTCUSDT. Punto de partida limpio para experimentar.

Estructura:
  Filtro D1  : precio < D1 EMA20 (no entrar en trend alcista)
  Filtro fund: bloquea ExtremeLong y ElevatedShort
  Señal M1   : shooting star en sesión London/NY
  Stop       : H1_high + 0.30 × ATR_H1 (estructural)
  Target     : 2.5R fijo
  Exit       : TP | SL | CVD exhaustion (5 barras + OBI flip + profit ≥1R)

Uso:
  python btc_shorts_v1.py [--days N]

Patrones actuales:
  btc:shoot+london    — shooting star en London (WR≈48%, el dominante)
  btc:shoot+ask+obi   — shooting star + absorción Ask + OBI < 0
"""
import json, os, sys, time, urllib.request, urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

# ── Config ────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).parent.parent.parent.parent
_env = {}
for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        _env[k.strip()] = v.strip().strip('"').strip("'")

SUPABASE_URL = _env.get('SUPABASE_URL', '')
SUPABASE_KEY = _env.get('SUPABASE_KEY', '')

CAPITAL_INIT  = 500.0
RISK_PCT      = 0.02
FEE_RT        = 0.0007    # 0.07% round-trip
TARGET_R      = 2.5
MIN_STOP_PCT  = 0.30
MAX_STOP_PCT  = 0.75
COOLDOWN_M1   = 30        # barras entre señales
FORWARD_M1    = 1200      # 20h máximo por trade
CVD_FLIP_BARS = 5
OBI_FLIP_THR  = 0.15
MIN_PROFIT_CVD= 1.0

BAR_COLS = ('ts_ms,open,high,low,close,volume,atr,session,cvd_slope,obi_l5,'
            'vwap,vr,oi_momentum,bar_delta,regime,dz,absorption,'
            'stacked_imb,equal_high,obi_fast,sweep_confirmed,'
            'asian_high,asian_low,prev_day_high,prev_day_low,'
            'thin_above,ask_wall,vp_poc,vp_vah,'
            'big_trade_bearish,big_trade_bullish,'
            'obi_min_intrabar,obi_max_intrabar')

# ── Fetch ─────────────────────────────────────────────────────────────────────

def sb_fetch(start_ms):
    rows, limit, offset = [], 1000, 0
    while True:
        qs = urllib.parse.urlencode({
            'select': BAR_COLS,
            'ts_ms':  f'gte.{start_ms}',
            'order':  'ts_ms.asc',
            'limit':  str(limit),
            'offset': str(offset),
        })
        req = urllib.request.Request(
            f'{SUPABASE_URL}/rest/v1/btc_bars?{qs}',
            headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
        )
        chunk = json.loads(urllib.request.urlopen(req, timeout=30).read())
        rows.extend(chunk)
        if len(chunk) < limit:
            break
        offset += limit
    return rows

def binance_d1(limit=120):
    url = ('https://fapi.binance.com/fapi/v1/klines'
           '?symbol=BTCUSDT&interval=1d&limit=' + str(limit))
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        data = json.loads(urllib.request.urlopen(req, timeout=15).read())
        return [{'ts_ms': int(d[0]), 'close': float(d[4])} for d in data]
    except Exception:
        return []

# ── Indicadores auxiliares ────────────────────────────────────────────────────

def ema(vals, p):
    k = 2 / (p + 1)
    r = [vals[0]]
    for v in vals[1:]:
        r.append(v * k + r[-1] * (1 - k))
    return r

def atr_series(bars, period=14):
    trs = []
    for i, b in enumerate(bars):
        pc = bars[i - 1]['close'] if i > 0 else b['open']
        trs.append(max(b['high'] - b['low'],
                       abs(b['high'] - pc),
                       abs(b['low'] - pc)))
    atrs = [sum(trs[:period]) / period] * period
    k = 1 / period
    for i in range(period, len(trs)):
        atrs.append(atrs[-1] * (1 - k) + trs[i] * k)
    return atrs

def build_h1(m1):
    buckets = defaultdict(list)
    for b in m1:
        buckets[(b['ts_ms'] // 3_600_000) * 3_600_000].append(b)
    h1 = []
    for ts in sorted(buckets):
        bs = buckets[ts]
        h1.append({
            'ts_ms': ts,
            'open':  bs[0]['open'],
            'high':  max(b['high'] for b in bs),
            'low':   min(b['low']  for b in bs),
            'close': bs[-1]['close'],
        })
    return h1

def d1_trend(ts_ms, d1_bars, d1_ema20):
    day_ms = (ts_ms // 86_400_000) * 86_400_000
    for i in range(len(d1_bars) - 1, -1, -1):
        if d1_bars[i]['ts_ms'] <= day_ms:
            c, e = d1_bars[i]['close'], d1_ema20[i]
            if c > e * 1.005: return 'bull'
            if c < e * 0.995: return 'bear'
            return 'neutral'
    return 'unknown'

# ── Detección de señal ────────────────────────────────────────────────────────

def detect_signal(b):
    """
    Retorna (True, nombre) o (False, '').
    Patrones derivados del data mining (btc_shorts_mine.py):
      Positivos: oi, below_vwap, below_asian_high, stk_bear, delta_neg
      Negativos: at_pdh, at_asian_high, above_pdh, sweep_confirmed
    """
    ses = b.get('session', '')
    if ses not in ('London', 'LondonNyOverlap', 'NewYork'):
        return False, ''

    rng     = (b['high'] - b['low']) or 1e-10
    body    = abs(b['close'] - b['open'])
    wick_hi = b['high'] - max(b['close'], b['open'])
    is_shoot = (wick_hi / rng) > 0.45 and (body / rng) < 0.40
    if not is_shoot:
        return False, ''

    close = b['close']
    tol   = 0.002

    # Filtros negativos globales (mining mostró WR ≤ 26%)
    sweep = b.get('sweep_confirmed') is True or str(b.get('sweep_confirmed')).lower() == 'true'
    pdh   = b.get('prev_day_high') or 0
    ah    = b.get('asian_high') or 0
    if sweep:
        return False, ''
    if pdh > 0 and (close > pdh or abs(close - pdh)/pdh <= tol):
        return False, ''   # at/above PDH → WR 15-21%
    if ah > 0 and abs(close - ah)/ah <= tol:
        return False, ''   # at Asian High exacto → WR 25.8%

    # Features positivos
    vwap      = b.get('vwap') or 0
    oi        = b.get('oi_momentum') is True or str(b.get('oi_momentum')).lower() == 'true'
    below_vwap = vwap > 0 and close < vwap
    below_ah  = ah > 0 and close < ah
    stk_bear  = b.get('stacked_imb', '') == 'Bearish'
    delta_neg = (b.get('bar_delta') or 0) < 0
    abs_ask   = b.get('absorption', '') == 'Ask'
    bt_bear   = b.get('big_trade_bearish') is True or str(b.get('big_trade_bearish')).lower() == 'true'

    # Patrones universales (todas las sesiones — validar split session con 200+ trades)
    # A0: Big Trade bearish + OI → vendedor institucional en el wick (pendiente de validar)
    if bt_bear and oi:
        return True, 'btc:bt_bear+oi'

    # A: OI + below_vwap + below_asian_high → WR=88.9%, AvgR=+1.555R, n=18
    if oi and below_vwap and below_ah:
        return True, 'btc:oi+bvwap+bah'

    # B: OI + delta_neg + below_vwap → drill: above_vwap = 100% de losses
    if oi and delta_neg and below_vwap:
        return True, 'btc:oi+delta_neg+bvwap'

    # C: OI + stk_bear → WR=72.4%, AvgR=+1.054R, n=29 (mayor sample)
    if oi and stk_bear:
        return True, 'btc:oi+stk_bear'

    # D: OI + below_vwap → WR=73.9%, AvgR=+1.119R, n=23
    if oi and below_vwap:
        return True, 'btc:oi+bvwap'

    # E: absorción Ask → WR=64.3%, n=14
    if abs_ask:
        return True, 'btc:abs_ask'

    # F: below_vwap + below_asian_high + stk_bear → WR=65.4%, n=52 (sin OI — más frecuente)
    if below_vwap and below_ah and stk_bear:
        return True, 'btc:bvwap+bah+stk'

    return False, ''

# ── Simulación de trade ───────────────────────────────────────────────────────

def simulate(m1, entry_i, entry, stop, risk):
    target = entry - TARGET_R * risk
    cvd_streak = 0
    obi_streak = 0

    for k in range(1, min(FORWARD_M1, len(m1) - entry_i)):
        mb = m1[entry_i + k]
        h, l = mb['high'], mb['low']

        if l <= target:
            fee_r = FEE_RT * entry / risk
            return round(TARGET_R - fee_r, 4), 'TAKE_PROFIT', k, mb['ts_ms']

        if h >= stop:
            fee_r = FEE_RT * entry / risk
            return round(-(1.0 + fee_r), 4), 'STOP_LOSS', k, mb['ts_ms']

        curr_r   = (entry - mb['close']) / risk
        cvd      = mb.get('cvd_slope') or 0
        obi_fast = mb.get('obi_fast') or mb.get('obi_l5') or 0

        if cvd > 0:      cvd_streak += 1
        else:            cvd_streak  = 0
        if obi_fast > OBI_FLIP_THR: obi_streak += 1
        else:                        obi_streak  = 0

        if cvd_streak >= CVD_FLIP_BARS and obi_streak >= 1 and curr_r >= MIN_PROFIT_CVD:
            exit_px = mb['close']
            fee_r   = FEE_RT * entry / risk
            return round((entry - exit_px) / risk - fee_r, 4), 'CVD_EXHAUSTION', k, mb['ts_ms']

    last  = m1[min(entry_i + FORWARD_M1 - 1, len(m1) - 1)]
    fee_r = FEE_RT * entry / risk
    return round((entry - last['close']) / risk - fee_r, 4), 'EXPIRED', FORWARD_M1, last['ts_ms']

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=9)
    args = parser.parse_args()

    # Microestructura disponible desde 2026-06-05 16:25 UTC
    MICRO_START_MS = 1780676700000
    start_ms = max(MICRO_START_MS, int((time.time() - args.days * 86400) * 1000))

    print(f'Cargando BTCUSDT — últimos {args.days}d...')
    m1 = sb_fetch(start_ms)
    print(f'  {len(m1)} barras M1')

    d1_bars = binance_d1()
    d1_ema  = ema([b['close'] for b in d1_bars], 20) if d1_bars else []

    h1      = build_h1(m1)
    h1_atrs = atr_series(h1)

    equity     = CAPITAL_INIT
    trades     = []
    last_sig_i = -COOLDOWN_M1

    for i in range(10, len(m1) - FORWARD_M1 - 1):
        if i - last_sig_i < COOLDOWN_M1:
            continue

        b = m1[i]
        if not b.get('cvd_slope'):   # solo barras con microestructura
            continue

        # Filtro D1 — no entrar en uptrend
        if d1_bars and d1_ema:
            if d1_trend(b['ts_ms'], d1_bars, d1_ema) == 'bull':
                continue

        fired, sig_name = detect_signal(b)
        if not fired:
            continue

        # Stop estructural H1
        h1_ts = (b['ts_ms'] // 3_600_000) * 3_600_000
        h1_i  = next((j for j, hb in enumerate(h1) if hb['ts_ms'] == h1_ts), None)
        if h1_i is None:
            continue
        h1_atr = h1_atrs[h1_i] if h1_i < len(h1_atrs) else 0
        if h1_atr <= 0:
            continue

        stop  = h1[h1_i]['high'] + 0.3 * h1_atr
        entry = b['close']
        risk  = stop - entry
        if risk <= 0:
            continue

        stop_pct = risk / entry * 100
        if not (MIN_STOP_PCT <= stop_pct <= MAX_STOP_PCT):
            continue

        last_sig_i = i
        risk_usd   = equity * RISK_PCT
        result_r, reason, dur, exit_ms = simulate(m1, i, entry, stop, risk)
        pnl = result_r * risk_usd
        equity += pnl

        trades.append({
            'ts':       datetime.fromtimestamp(b['ts_ms'] / 1000, tz=timezone.utc).strftime('%m-%d %H:%M'),
            'sig':      sig_name,
            'session':  b.get('session', ''),
            'entry':    round(entry, 1),
            'stop_pct': round(stop_pct, 2),
            'result_r': result_r,
            'reason':   reason,
            'dur_min':  dur,
        })

    # ── Reporte ───────────────────────────────────────────────────────────────
    n   = len(trades)
    if n == 0:
        print('Sin trades.')
        return

    wins   = [t for t in trades if t['result_r'] > 0]
    avg_r  = sum(t['result_r'] for t in trades) / n
    wr     = len(wins) / n * 100
    total_r = sum(t['result_r'] for t in trades)

    print(f'\n{"="*60}')
    print(f'  BTC Shorts v1 — {args.days}d')
    print(f'{"="*60}')
    print(f'  n={n}  WR={wr:.1f}%  AvgR={avg_r:+.3f}R  TotalR={total_r:+.2f}R')
    print(f'  Equity: ${CAPITAL_INIT:.0f} -> ${equity:.0f}  ({equity-CAPITAL_INIT:+.0f})')

    # Por patrón
    by_sig = defaultdict(list)
    for t in trades:
        by_sig[t['sig']].append(t['result_r'])
    print(f'\n  Por patron:')
    for sig, rs in sorted(by_sig.items(), key=lambda x: -len(x[1])):
        w = [r for r in rs if r > 0]
        print(f'    {sig:<28} n={len(rs):>3}  WR={len(w)/len(rs)*100:.1f}%  AvgR={sum(rs)/len(rs):+.3f}R')

    # Por sesión
    by_ses = defaultdict(list)
    for t in trades:
        by_ses[t['session']].append(t['result_r'])
    print(f'\n  Por sesion:')
    for ses, rs in sorted(by_ses.items(), key=lambda x: -len(x[1])):
        w = [r for r in rs if r > 0]
        print(f'    {ses:<20} n={len(rs):>3}  WR={len(w)/len(rs)*100:.1f}%  AvgR={sum(rs)/len(rs):+.3f}R')

    # Exit reasons
    by_reason = defaultdict(int)
    for t in trades:
        by_reason[t['reason']] += 1
    print(f'\n  Exit reasons:')
    for r, c in sorted(by_reason.items(), key=lambda x: -x[1]):
        print(f'    {r:<20} {c:>3} ({c/n*100:.1f}%)')

    # Lista de trades
    print(f'\n  Trades:')
    print(f'  {"Fecha":<12} {"Señal":<28} {"Ses":<18} {"Entry":>8} {"Stop%":>6} {"R":>7}  Razón')
    print(f'  {"-"*90}')
    for t in trades:
        print(f'  {t["ts"]:<12} {t["sig"]:<28} {t["session"]:<18} '
              f'{t["entry"]:>8.1f} {t["stop_pct"]:>5.2f}% {t["result_r"]:>+7.3f}R  {t["reason"]}')

    print()

if __name__ == '__main__':
    main()
