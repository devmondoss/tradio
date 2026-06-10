"""
Pre-Breakout Entry Detector.

Concepto: mientras se forma el rango de consolidacion, detectar cuando la
microestructura ya senala la direccion (ANTES de que el precio rompa).

Entrada: barra con DZ extremo + VR alto + delta alineado, todavia DENTRO del rango.
Stop:    estructural = range_high + buffer (para shorts) | range_low - buffer (para longs)
Target:  3x la distancia al stop (RR 3:1)

Comparar vs RBF actual:
  RBF:       entrada en barra de ruptura, stop ATR, target 2:1
  Pre-BK:    entrada dentro del rango, stop estructural, target 3:1
"""

import json, datetime
from collections import defaultdict

RANGE_WINDOWS   = [15, 20, 30, 45, 60]
RANGE_MIN_PCT   = 0.08
RANGE_MAX_PCT   = 0.55
VR_EARLY        = 3.0    # VR minimo para senial temprana
DZ_EARLY        = 3.0    # |DZ| minimo para senial temprana
STOP_BUFFER_ATR = 0.3    # buffer extra sobre range_high (para shorts)
TARGET_RR       = 3.0    # RR objetivo
TIME_STOP_BARS  = 20
MAX_BARS        = 150
COOLDOWN_BARS   = 30
TRAIL_ACTIVATE  = 1.5
TRAIL_ATR_K     = 0.5

def ts(ms):
    return datetime.datetime.fromtimestamp(ms/1000, datetime.timezone.utc).strftime('%d/%m %H:%M')

def sl(s):
    return {'London':'LDN','LondonNyOverlap':'OVR','NewYork':'NY','Asia':'ASI','OffHours':'OFF'}.get(s,s[:3])


def detect_pre_breakout(bars, i):
    """
    Para la barra i, busca si esta DENTRO de algun rango activo
    y si su microestructura indica momentum fuerte en una direccion.

    Retorna (direction, range_high, range_low) o None.
    """
    bar  = bars[i]
    atr  = bar.get('atr') or 0.0
    vr   = bar.get('vr') or 0.0
    dz   = bar.get('dz') or 0.0
    stk  = bar.get('stacked_imb') or 'None'
    obi  = bar.get('obi_l5') or 0.0
    c    = bar['close']
    h    = bar['high']
    l    = bar['low']

    if atr <= 0 or vr < VR_EARLY or abs(dz) < DZ_EARLY:
        return None

    for rw in RANGE_WINDOWS:
        if i < rw + 1:
            continue
        window = bars[i - rw: i]   # barras ANTES de la actual (no incluye la actual)
        rh = max(b['high'] for b in window)
        rl = min(b['low']  for b in window)
        range_pct = (rh - rl) / c * 100.0

        if range_pct < RANGE_MIN_PCT or range_pct > RANGE_MAX_PCT:
            continue

        # La barra actual debe estar DENTRO del rango (no haberlo roto todavia)
        bar_inside = (l >= rl * 0.9995) and (h <= rh * 1.0005)
        bar_near_low  = c <= rl + (rh - rl) * 0.35   # en el tercio inferior
        bar_near_high = c >= rh - (rh - rl) * 0.35   # en el tercio superior

        if not bar_inside:
            continue

        # Condiciones de entrada SHORT: presion vendedora extrema en tercio inferior
        if dz < -DZ_EARLY and bar_near_low:
            confirm = (stk == 'Bearish') or (obi < -0.1)
            if confirm:
                return ('Short', rh, rl, rw)

        # Condiciones de entrada LONG: presion compradora extrema en tercio superior
        if dz > DZ_EARLY and bar_near_high:
            confirm = (stk == 'Bullish') or (obi > 0.1)
            if confirm:
                return ('Long', rh, rl, rw)

    return None


def backtest_symbol(sym, bars):
    trades   = []
    n        = len(bars)
    cooldown = 0
    in_trade = None

    for i in range(60, n):
        bar = bars[i]
        atr = bar.get('atr') or 0.0
        h, l, c = bar['high'], bar['low'], bar['close']

        # gestionar trade activo
        if in_trade is not None:
            t = in_trade
            t['bars_held'] += 1
            cur_atr = atr if atr > 0 else t['atr']

            if t['dir'] == 'Short':
                if l < t['best_ext']: t['best_ext'] = l
                fav_r = (t['entry'] - t['best_ext']) / t['risk']
                if fav_r >= TRAIL_ACTIVATE and not t['trailing']:
                    t['trailing'] = True
                if t['trailing'] and cur_atr > 0:
                    ns = t['best_ext'] + TRAIL_ATR_K * cur_atr
                    if ns < t['stop']: t['stop'] = ns
                stop_hit   = h >= t['stop']
                target_hit = l <= t['target']
            else:
                if h > t['best_ext']: t['best_ext'] = h
                fav_r = (t['best_ext'] - t['entry']) / t['risk']
                if fav_r >= TRAIL_ACTIVATE and not t['trailing']:
                    t['trailing'] = True
                if t['trailing'] and cur_atr > 0:
                    ns = t['best_ext'] - TRAIL_ATR_K * cur_atr
                    if ns > t['stop']: t['stop'] = ns
                stop_hit   = l <= t['stop']
                target_hit = h >= t['target']

            reason = ep = None
            if stop_hit:
                reason = 'TRAIL' if t['trailing'] else 'STOP'
                ep = t['stop']
            elif target_hit:
                reason = 'TARGET'
                ep = t['target']
            elif t['bars_held'] >= TIME_STOP_BARS:
                pnl = (t['entry']-c) if t['dir']=='Short' else (c-t['entry'])
                if pnl < 0:
                    reason = 'TIME'
                    ep = c
            elif t['bars_held'] >= MAX_BARS:
                reason = 'TIMEOUT'
                ep = c

            if reason:
                pnl_raw  = (t['entry']-ep) if t['dir']=='Short' else (ep-t['entry'])
                result_r = pnl_raw / t['risk']
                t.update({'exit_ms': bar['ts_ms'], 'exit_p': ep,
                          'r': round(result_r,4), 'reason': reason,
                          'exit_sess': bar.get('session','?')})
                trades.append(t)
                in_trade = None
                cooldown = COOLDOWN_BARS
            continue

        if cooldown > 0:
            cooldown -= 1
            continue

        if atr <= 0:
            continue

        result = detect_pre_breakout(bars, i)
        if result is None:
            continue

        direction, rh, rl, rw = result

        # Stop estructural: justo por encima del range_high (short) o bajo range_low (long)
        buf = STOP_BUFFER_ATR * atr
        if direction == 'Short':
            stop_price   = rh + buf
            stop_dist    = stop_price - c
            target_price = c - stop_dist * TARGET_RR
        else:
            stop_price   = rl - buf
            stop_dist    = c - stop_price
            target_price = c + stop_dist * TARGET_RR

        if stop_dist <= 0:
            continue

        in_trade = {
            'sym': sym, 'dir': direction,
            'entry_ms': bar['ts_ms'], 'entry': c,
            'stop': stop_price, 'target': target_price,
            'risk': stop_dist, 'atr': atr,
            'range_h': rh, 'range_l': rl, 'range_bars': rw,
            'range_pct': round((rh-rl)/c*100, 3),
            'vr': round(bar.get('vr') or 0, 2),
            'dz': round(bar.get('dz') or 0, 2),
            'obi': round(bar.get('obi_l5') or 0, 2),
            'stk': bar.get('stacked_imb') or 'None',
            'session': bar.get('session','OffHours'),
            'bars_held': 0, 'trailing': False, 'best_ext': c,
            'exit_ms': None, 'exit_p': None, 'r': None,
            'reason': None, 'exit_sess': None,
        }

    return trades


# cargar datos
with open('/tmp/all_bars_full.json') as f:
    raw = json.load(f)

all_trades = []
for sym, bars_raw in raw.items():
    bars = sorted(bars_raw if isinstance(bars_raw, list) else list(bars_raw.values()),
                  key=lambda b: b['ts_ms'])
    t = backtest_symbol(sym, bars)
    all_trades.extend(t)
    print(f'{sym}: {len(bars)} barras -> {len(t)} trades')

closed = [t for t in all_trades if t['r'] is not None]
closed.sort(key=lambda t: t['entry_ms'])

print(f'\nTotal: {len(all_trades)} | Cerrados: {len(closed)}')

if not closed:
    print('Sin trades cerrados')
    raise SystemExit

def stats(lst):
    if not lst: return (0,0,0,0)
    wins  = sum(1 for t in lst if t['r']>0)
    total = sum(t['r'] for t in lst)
    return len(lst), wins/len(lst)*100, total/len(lst), total

# ── ESTADISTICAS ──────────────────────────────────────────────────────────────
print('\n' + '='*80)
print(f'PRE-BREAKOUT ENTRY  |  Stop estructural (range_high/low + 0.3xATR)  |  Target 3:1')
print('='*80)

n,wr,avg_r,tot_r = stats(closed)
print(f'\nGLOBAL: n={n}  WR={wr:.1f}%  AvgR={avg_r:+.3f}  TotalR={tot_r:+.2f}')

print('\n--- Por direccion ---')
by_dir = defaultdict(list)
for t in closed: by_dir[t['dir']].append(t)
for d,lst in sorted(by_dir.items()):
    nn,ww,aa,tt = stats(lst)
    print(f'  {d:8s}: n={nn:3d}  WR={ww:4.0f}%  AvgR={aa:+.3f}  TotalR={tt:+.2f}')

print('\n--- Por sesion ---')
by_sess = defaultdict(list)
for t in closed: by_sess[t['session']].append(t)
for s,lst in sorted(by_sess.items(), key=lambda x: -sum(t['r'] for t in x[1])):
    nn,ww,aa,tt = stats(lst)
    print(f'  {s:22s}: n={nn:3d}  WR={ww:4.0f}%  AvgR={aa:+.3f}  TotalR={tt:+.2f}')

print('\n--- Por razon de salida ---')
by_rsn = defaultdict(list)
for t in closed: by_rsn[t['reason']].append(t)
for r,lst in sorted(by_rsn.items(), key=lambda x:-len(x[1])):
    nn,ww,aa,tt = stats(lst)
    print(f'  {r:10s}: n={nn:3d}  WR={ww:4.0f}%  AvgR={aa:+.3f}  TotalR={tt:+.2f}')

print('\n--- Por DZ tier ---')
for lo,hi,lab in [(3,4,'3-4s'),(4,5,'4-5s'),(5,99,'5+s')]:
    sub = [t for t in closed if lo<=abs(t['dz'])<hi]
    if sub:
        nn,ww,aa,tt = stats(sub)
        print(f'  DZ {lab}: n={nn:3d}  WR={ww:4.0f}%  AvgR={aa:+.3f}')

print('\n--- Por VR tier ---')
for lo,hi,lab in [(3,4,'3-4x'),(4,5,'4-5x'),(5,99,'5x+')]:
    sub = [t for t in closed if lo<=t['vr']<hi]
    if sub:
        nn,ww,aa,tt = stats(sub)
        print(f'  VR {lab}: n={nn:3d}  WR={ww:4.0f}%  AvgR={aa:+.3f}')

# ── COMPARACION DIRECTA ───────────────────────────────────────────────────────
print('\n' + '='*80)
print('COMPARACION DIRECTA')
print('='*80)
days = (max(t['entry_ms'] for t in closed) - min(t['entry_ms'] for t in closed)) / 86400000
days = max(days, 1)
spd  = n / days

print(f'  Pre-Breakout Entry: n={n}  WR={wr:.1f}%  AvgR={avg_r:+.3f}  TotalR={tot_r:+.2f}  ({spd:.1f}/dia)')
print(f'  MF v2 (post-BK):    n=266  WR=37.6%  AvgR=+0.117  TotalR=+31.18  (60.5/dia)')
print(f'  RBF backtest:       n=145  WR=35.9%  AvgR=+0.037  TotalR= +5.29  (33.0/dia)')

for cap_risk in [1.0, 2.0]:
    r_mes = spd * 22 * avg_r
    print(f'\n  Con ${cap_risk:.0f} riesgo/trade:')
    print(f'    Pre-BK:  {r_mes:+.1f}R = ${r_mes*cap_risk:+.0f}/mes')
    print(f'    MF v2:   {60.5*22*0.117:+.1f}R = ${60.5*22*0.117*cap_risk:+.0f}/mes')

# ── TABLA DE TRADES ───────────────────────────────────────────────────────────
print(f'\n{"="*110}')
print('TRADES COMPLETOS')
print(f'{"="*110}')
print(f'{"#":>3} {"Fecha":>10} {"Sym":>8} {"D":>1} {"Ses":>3} {"VR":>4} {"DZ":>5} '
      f'{"Entry":>9} {"Stop":>9} {"Tgt":>9} {"RR":>4} '
      f'{"R%Rng":>6} {"Bars":>4} {"Reason":>6} {"R":>7}')
print('-'*110)

for i,t in enumerate(closed):
    rr_nom = abs(t['entry']-t['target'])/t['risk'] if t['risk']>0 else 0
    # donde entro dentro del rango: 0=low, 1=high
    rng_w = t['range_h'] - t['range_l']
    if rng_w > 0:
        if t['dir'] == 'Short':
            pos_in_range = (t['range_h'] - t['entry']) / rng_w * 100
        else:
            pos_in_range = (t['entry'] - t['range_l']) / rng_w * 100
    else:
        pos_in_range = 50
    print(f'{i+1:>3} {ts(t["entry_ms"]):>10} {t["sym"]:>8} {t["dir"][0]:>1} {sl(t["session"]):>3} '
          f'{t["vr"]:>4.1f} {t["dz"]:>+5.1f} '
          f'{t["entry"]:>9.2f} {t["stop"]:>9.2f} {t["target"]:>9.2f} {rr_nom:>4.1f} '
          f'{pos_in_range:>5.0f}% {t["bars_held"]:>4} {t["reason"]:>6} {t["r"]:>+7.3f}')

with open('/tmp/prebk_results.json','w') as f:
    json.dump(closed, f, indent=2, default=str)
print(f'\nGuardado: /tmp/prebk_results.json')
