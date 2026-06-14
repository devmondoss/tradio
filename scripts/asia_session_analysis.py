#!/usr/bin/env python3
"""
Análisis sesión Asia para RBF.
Pregunta clave: ¿XRP (y otros activos) tienen edge en Asia con los mismos setups?

Metodología:
- Simula RBF sobre barras históricas incluyendo Asia
- Compara por sesión y por símbolo
- Usa las mismas calibraciones que el backtest principal
"""
import json, os, sys, time, urllib.request, urllib.parse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
_env = {}
for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        _env[k.strip()] = v.strip().strip('"').strip("'")

URL = _env.get('SUPABASE_URL', '')
KEY = _env.get('SUPABASE_KEY', '')

RANGE_WINDOWS    = [15, 20, 30, 45, 60]
RANGE_MIN_PCT    = 0.08
RANGE_MAX_PCT    = 0.55
VR_MIN           = 3.0
MIN_RANGE_ATR    = 1.5
TRAIL_ATR_K      = 1.2
TIME_STOP_BARS   = 30
COOLDOWN_BARS    = 60
RR_SHORT         = 2.0
BREAKOUT_EXT_MIN = 0.001
VSWAP_MAX_DEV    = 0.003
TRAIL_ACTIVATE_R = 1.75

# Todas las sesiones incluyendo Asia
SESSIONS_NORMAL = {'London', 'LondonNyOverlap', 'NewYork'}
SESSIONS_ASIA   = {'Asia'}
SESSIONS_ALL    = SESSIONS_NORMAL | SESSIONS_ASIA

TABLES = {
    'XRPUSDT': 'xrp_bars',
    'BTCUSDT': 'btc_bars',
    'SOLUSDT': 'sol_bars',
    'ETHUSDT': 'eth_bars',
    'BNBUSDT': 'bnb_bars',
}
BAR_COLS = 'ts_ms,open,high,low,close,volume,bar_delta,vr,atr,session,cvd_slope,obi_l5,vwap,regime'


def sb_fetch(table):
    """Descarga todas las barras con microestructura (cvd_slope NOT NULL)."""
    rows, limit, offset = [], 1000, 0
    while True:
        qs = urllib.parse.urlencode({
            'select': BAR_COLS,
            'cvd_slope': 'not.is.null',
            'order': 'ts_ms.asc',
            'limit': str(limit),
            'offset': str(offset),
        })
        req = urllib.request.Request(
            f'{URL}/rest/v1/{table}?{qs}',
            headers={'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
        )
        chunk = json.loads(urllib.request.urlopen(req, timeout=30).read())
        rows.extend(chunk)
        if len(chunk) < limit:
            break
        offset += limit
    return rows


def simulate(bars, entry, stop, target, atr, is_pre=False):
    time_stop = 15 if is_pre else TIME_STOP_BARS
    best, trailing, trail_stop = entry, False, stop
    for k, b in enumerate(bars):
        eff = trail_stop if trailing else stop
        h, l, c = b['high'], b['low'], b['close']
        if h >= eff:
            return (entry - eff) / abs(stop - entry), k+1
        if l <= target:
            return (entry - target) / abs(stop - entry), k+1
        if l < best:
            best = l
        if (entry - best) / abs(stop - entry) >= TRAIL_ACTIVATE_R and not trailing:
            trailing = True
        if trailing and atr > 0:
            cand = best + TRAIL_ATR_K * atr
            if cand < trail_stop:
                trail_stop = cand
        if k+1 >= time_stop and (entry - c) < 0:
            return (entry - c) / abs(stop - entry), k+1
    return 0.0, len(bars)


def detect(sym, bars, sessions_ok, apply_calibrations=True):
    """Simula RBF con calibraciones activas para el símbolo."""
    trades = []
    last_sig = -COOLDOWN_BARS
    n = len(bars)

    for i in range(COOLDOWN_BARS, n - TIME_STOP_BARS):
        b   = bars[i]
        ses = b.get('session') or ''
        vr  = b.get('vr') or 0
        atr = b.get('atr') or 0
        if ses not in sessions_ok:
            continue
        if atr <= 0:
            continue
        if b.get('vwap') is None:
            continue
        if i - last_sig < COOLDOWN_BARS:
            continue

        vwap = b.get('vwap', 0)
        if vwap and vwap > 0 and (b['close'] - vwap) / vwap < -VSWAP_MAX_DEV:
            continue

        # Calibraciones por símbolo
        if apply_calibrations:
            # expansion gate (BTC/ETH/BNB/XRP no; SOL tampoco tiene suficientes datos)
            if sym not in ('SOLUSDT', 'XRPUSDT'):
                exp_window = bars[max(0, i-25):i]
                exp_count  = sum(1 for x in exp_window if x.get('regime') == 'Expansion')
                if exp_count > 3:
                    continue

            # cum_delta gates
            delta_win = bars[max(0, i-25):i]
            cum_d25   = sum(x.get('bar_delta') or 0 for x in delta_win)
            if sym == 'BNBUSDT' and cum_d25 < -500:
                continue
            if sym == 'BTCUSDT' and cum_d25 > 200:
                continue

        for rw in RANGE_WINDOWS:
            if i < rw + 1:
                continue
            win = bars[i-rw:i]
            hi  = max(x['high'] for x in win)
            lo  = min(x['low']  for x in win)
            rng = hi - lo
            rp  = rng / b['close'] * 100.0
            if rp < RANGE_MIN_PCT or rp > RANGE_MAX_PCT:
                continue
            if rng < MIN_RANGE_ATR * atr:
                continue
            deltas    = [x.get('bar_delta') for x in win]
            cvd_total = sum(d for d in deltas if d is not None)
            if not (all(d is not None for d in deltas) and cvd_total < 0):
                continue

            close = b['close']
            if not (vr >= VR_MIN and close < lo):
                continue
            if (lo - close) / lo < BREAKOUT_EXT_MIN:
                continue

            # CVD in range gate para ETH en Asia también
            if apply_calibrations and sym == 'ETHUSDT' and cvd_total < -700:
                continue

            entry  = close
            stop_p = hi
            target = entry - RR_SHORT * (stop_p - entry)
            sim    = bars[i+1:i+1+TIME_STOP_BARS+30]
            r, dur = simulate(sim, entry, stop_p, target, atr)
            trades.append({
                'sym':     sym,
                'session': ses,
                'r':       round(r, 4),
                'ts_ms':   b['ts_ms'],
                'range_pct': round(rp, 3),
            })
            last_sig = i
            break

    return trades


def stats(trades):
    if not trades:
        return {'n': 0, 'wr': 0, 'avgR': 0, 'pnl': 0, 'wins': 0}
    n    = len(trades)
    wins = sum(1 for t in trades if t['r'] > 0)
    pnl  = sum(t['r'] for t in trades)
    return {
        'n':    n,
        'wins': wins,
        'wr':   round(100 * wins / n, 1),
        'avgR': round(pnl / n, 3),
        'pnl':  round(pnl, 2),
    }


def bar(s):
    symbol = '█' * int(abs(s['pnl']) / 0.5) if s['n'] > 0 else ''
    sign   = '+' if s['pnl'] >= 0 else '-'
    return (f"n={s['n']:<4} WR={s['wr']:>5.1f}%  avgR={s['avgR']:>+.3f}  "
            f"PnL={s['pnl']:>+.2f}R  {sign}{symbol}")


print("Descargando barras…")
all_data = {}
for sym, table in TABLES.items():
    rows = sb_fetch(table)
    all_data[sym] = rows
    days = 0
    if rows:
        span = (rows[-1]['ts_ms'] - rows[0]['ts_ms']) / 86_400_000
        days = round(span, 1)
        asia_bars = sum(1 for r in rows if r.get('session') == 'Asia')
    print(f"  {sym}: {len(rows)} barras ({days} días) — Asia: {asia_bars} barras")

print()
print('═'*70)
print('SESIÓN ASIA vs NORMAL — todos los símbolos')
print('═'*70)
print(f'{"Símbolo":<10} {"Sesión":<22} {"Resultado"}')
print('-'*70)

asia_results  = {}
normal_results = {}

for sym in TABLES:
    bars = all_data[sym]
    if len(bars) < COOLDOWN_BARS + 10:
        print(f'  {sym}: datos insuficientes')
        continue

    t_asia   = detect(sym, bars, SESSIONS_ASIA)
    t_normal = detect(sym, bars, SESSIONS_NORMAL)

    s_asia   = stats(t_asia)
    s_normal = stats(t_normal)

    asia_results[sym]   = s_asia
    normal_results[sym] = s_normal

    print(f'  {sym:<8}  {"London/Overlap/NY":<22} {bar(s_normal)}')
    print(f'  {sym:<8}  {"Asia (00-09 UTC)":<22} {bar(s_asia)}')
    print()

print()
print('═'*70)
print('ZOOM: XRP ASIA — análisis detallado')
print('═'*70)

xrp_bars   = all_data.get('XRPUSDT', [])
xrp_asia   = detect('XRPUSDT', xrp_bars, SESSIONS_ASIA)

if xrp_asia:
    # Por hora UTC (cuándo dentro de Asia)
    print('\nPor hora UTC (trades en Asia):')
    for h in range(0, 9):
        bucket = [t for t in xrp_asia if datetime.fromtimestamp(t['ts_ms']/1000, tz=timezone.utc).hour == h]
        if bucket:
            s = stats(bucket)
            print(f'  {h:02d}:00 UTC  {bar(s)}')

    # Distribución R
    print(f'\nDistribución R:')
    buckets = [
        ('≥ 2R (target)',  lambda r: r >= 2.0),
        ('1–2R',           lambda r: 1.0 <= r < 2.0),
        ('0–1R (BE)',      lambda r: 0.0 <= r < 1.0),
        ('-1–0R (pequeña pérdida)', lambda r: -1.0 <= r < 0.0),
        ('< -1R (stop)',   lambda r: r < -1.0),
    ]
    for label, fn in buckets:
        b2 = [t for t in xrp_asia if fn(t['r'])]
        pct = 100 * len(b2) / len(xrp_asia)
        print(f'  {label:<30} {len(b2):>4} ({pct:.0f}%)')

    print(f'\nTop 5 mejores trades:')
    for t in sorted(xrp_asia, key=lambda x: -x['r'])[:5]:
        dt = datetime.fromtimestamp(t['ts_ms']/1000, tz=timezone.utc)
        print(f"  {dt.strftime('%Y-%m-%d %H:%M UTC')}  {t['r']:>+.2f}R  range={t['range_pct']:.2f}%  ses={t['session']}")

    print(f'\nTop 5 peores trades:')
    for t in sorted(xrp_asia, key=lambda x: x['r'])[:5]:
        dt = datetime.fromtimestamp(t['ts_ms']/1000, tz=timezone.utc)
        print(f"  {dt.strftime('%Y-%m-%d %H:%M UTC')}  {t['r']:>+.2f}R  range={t['range_pct']:.2f}%  ses={t['session']}")
else:
    print('Sin trades Asia para XRP en el periodo analizado.')

print()
print('═'*70)
print('RESUMEN — ¿vale la pena activar Asia?')
print('═'*70)
for sym in TABLES:
    a = asia_results.get(sym)
    n = normal_results.get(sym)
    if not a or not n:
        continue
    verdict = ''
    if a['n'] < 5:
        verdict = '⚠ muestra insuficiente'
    elif a['avgR'] > 0.15 and a['wr'] > 35:
        verdict = '✓ CANDIDATO — edge positivo'
    elif a['avgR'] > 0 and a['wr'] > 30:
        verdict = '~ posible con más datos'
    else:
        verdict = '✗ no recomendado'
    print(f'  {sym:<10}  Asia n={a["n"]}  WR={a["wr"]}%  avgR={a["avgR"]:+.3f}R  → {verdict}')
