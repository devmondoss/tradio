"""
Análisis de frecuencia BE — todas las sesiones, cooldown reducido.
Objetivo: cuántas señales/día/símbolo podemos generar con base amplia.
Microestructura NO disponible en 730d → se usa como gate en live, no aquí.
"""
import sys, time, itertools, math
sys.path.insert(0, 'apps/rbf-review/api')
import be_backtest_script as bt
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

# ── Configuración expanded ────────────────────────────────────────────────────
bt.SESSIONS_OK    = {'Asia', 'London', 'LondonNyOverlap', 'NewYork'}
bt.TIME_STOP_BARS = 9999
COOLDOWN          = 15    # reducido de 60 → más señales
RANGE_W           = bt.RANGE_WINDOW  # 8

def detect_expanded(sym, bars):
    trades, last_sig = [], -COOLDOWN
    n = len(bars)
    for i in range(COOLDOWN, n - 40):
        b = bars[i]
        ses = b.get('session') or ''
        if ses not in bt.SESSIONS_OK: continue
        if b.get('bar_delta') is None: continue
        if i - last_sig < COOLDOWN: continue
        win = bars[i - RANGE_W : i]
        if len(win) < RANGE_W: continue
        hi  = max(x['high'] for x in win)
        lo  = min(x['low']  for x in win)
        rng = hi - lo
        rp  = rng / b['close'] * 100.0
        if rp < bt.RANGE_MIN_PCT or rp > bt.RANGE_MAX_PCT: continue
        deltas = [x.get('bar_delta') for x in win]
        if any(d is None for d in deltas): continue
        range_cvd = sum(deltas)
        if range_cvd <= 0: continue
        pre_cvd = sum(deltas[-bt.PRE_CVD_BARS:])
        if pre_cvd >= 0: continue
        flip = abs(pre_cvd) / range_cvd
        if flip < bt.CVD_FLIP_RATIO_MIN: continue
        close = b['close']
        if close >= lo: continue
        vol_hist = [bars[j]['volume'] for j in range(max(0, i - bt.VR_WINDOW), i)]
        avg_vol  = sum(vol_hist) / len(vol_hist) if vol_hist else 1.0
        vr       = b['volume'] / avg_vol if avg_vol > 0 else 0.0
        if vr < bt.VR_MIN: continue
        if (b.get('bar_delta') or 0) >= 0: continue
        bh, bl, bo = b['high'], b['low'], b['open']
        bar_rng = bh - bl
        if bar_rng > 0:
            if (close - bl)          / bar_rng > bt.CLOSE_LOC_MAX: continue
            if abs(close - bo)       / bar_rng < bt.BEAR_BODY_MIN: continue
            if (bh - max(bo, close)) / bar_rng > bt.UPPER_WICK_MAX: continue
        stop_p = max(hi, b['high'])
        risk   = stop_p - close
        if risk < 1e-8: continue
        target = close - 2 * risk
        if b['low'] <= target: continue

        hour = datetime.fromtimestamp(b['ts_ms'] / 1000, tz=timezone.utc).hour
        sim  = bars[i + 1 : i + 1 + 350]
        r, reason, dur, exit_ms = bt.simulate(sim, close, stop_p, target, ses)

        trades.append({
            'resultR': round(r, 4),
            'win':     1 if r > 0 else 0,
            'session': ses,
            'hour':    hour,
            'ts_ms':   b['ts_ms'],
            'pnl':     round(r * bt.RISK_USD, 2),
        })
        last_sig = i
    return trades

# ── Cargar y procesar ─────────────────────────────────────────────────────────
start_ms = int((time.time() - 730 * 86400) * 1000)
sb_first = bt.sb_earliest_ms()

sym_trades = {}
def proc(item):
    sym, tbl = item
    return sym, detect_expanded(sym, bt.load_bars(sym, tbl, start_ms, sb_first))

with ThreadPoolExecutor(max_workers=5) as ex:
    for res in as_completed({ex.submit(proc, item): item for item in bt.TABLES.items()}):
        sym, trades = res.result()
        sym_trades[sym] = trades

# ── Stats globales ────────────────────────────────────────────────────────────
all_trades = [t for ts in sym_trades.values() for t in ts]
all_trades.sort(key=lambda t: t['ts_ms'])

first_ts = all_trades[0]['ts_ms']
last_ts  = all_trades[-1]['ts_ms']
total_days = (last_ts - first_ts) / 86400000

print()
print('=' * 70)
print(f'  TOTAL  n={len(all_trades)}  dias={total_days:.0f}')
print(f'  Frecuencia: {len(all_trades)/total_days:.2f} trades/dia  |  {len(all_trades)/total_days*30:.1f} trades/mes')
print('=' * 70)

RISK = bt.RISK_USD
def stats(g):
    if not g: return 0, 0.0, 0.0, 0.0
    wr  = sum(t['win'] for t in g) / len(g)
    ar  = sum(t['resultR'] for t in g) / len(g)
    ev  = wr * 2 - (1 - wr)
    pnl = sum(t['pnl'] for t in g)
    return wr * 100, ar, ev, pnl

# Por símbolo
print()
print('  Por simbolo:')
for sym in ['BTCUSDT','ETHUSDT','BNBUSDT','SOLUSDT','XRPUSDT']:
    ts = sym_trades.get(sym, [])
    wr, ar, ev, pnl = stats(ts)
    freq = len(ts) / total_days
    print(f'    {sym:<10}  n={len(ts):4d}  {freq:.2f}/d  WR={wr:3.0f}%  EV={ev:+.3f}  PnL=${pnl:+.1f}')

# Por sesion
print()
print('  Por sesion:')
for ses in ['Asia', 'London', 'LondonNyOverlap', 'NewYork']:
    g = [t for t in all_trades if t['session'] == ses]
    if not g: continue
    wr, ar, ev, pnl = stats(g)
    freq = len(g) / total_days
    print(f'    {ses:<20}  n={len(g):4d}  {freq:.2f}/d  WR={wr:3.0f}%  EV={ev:+.3f}  PnL=${pnl:+.1f}')

# Frecuencia por hora UTC
print()
print('  Señales por hora UTC (todas sesiones):')
by_hour = defaultdict(list)
for t in all_trades:
    by_hour[t['hour']].append(t)
for h in sorted(by_hour):
    g = by_hour[h]
    wr, ar, ev, pnl = stats(g)
    bar = '#' * int(len(g) / total_days * 20)
    print(f'    {h:02d}:00  n={len(g):4d}  {len(g)/total_days:.2f}/d  WR={wr:3.0f}%  EV={ev:+.3f}  {bar}')

# Distribución mensual
print()
print('  Distribucion mensual:')
by_month = defaultdict(list)
for t in all_trades:
    dt = datetime.fromtimestamp(t['ts_ms']/1000, tz=timezone.utc)
    by_month[dt.strftime('%Y-%m')].append(t)

eq = 500.0
for month in sorted(by_month):
    g = by_month[month]
    wr, ar, ev, pnl = stats(g)
    eq += pnl
    bar = '+' * min(int(pnl / 5), 20) if pnl > 0 else '-' * min(int(abs(pnl) / 5), 20)
    print(f'    {month}  n={len(g):3d}  WR={wr:3.0f}%  PnL=${pnl:+6.1f}  eq=${eq:7.2f}  {bar}')

print()
print(f'  Equity final: ${eq:.2f}  retorno: {(eq-500)/500*100:.1f}%')
print(f'  Retorno anual: {(eq-500)/500*100/total_days*365:.1f}%')
print('=' * 70)

# Resumen por sesion × simbolo
print()
print('  WR por sesion x simbolo (n minimo 10):')
print(f'  {"":20}', end='')
for ses in ['Asia','London','LondonNyOverlap','NewYork']:
    print(f'  {ses[:8]:>10}', end='')
print()
for sym in ['BTCUSDT','ETHUSDT','BNBUSDT','SOLUSDT','XRPUSDT']:
    ts = sym_trades.get(sym, [])
    print(f'  {sym:<20}', end='')
    for ses in ['Asia','London','LondonNyOverlap','NewYork']:
        g = [t for t in ts if t['session'] == ses]
        if len(g) < 5:
            print(f'  {"n<5":>10}', end='')
        else:
            wr, _, ev, _ = stats(g)
            print(f'  {wr:8.0f}%({len(g)})', end='')
    print()
