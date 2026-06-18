"""
_longs_explore.py — buscar un edge de LONG distinto al espejo de shorts.
Solo tenemos data SPOT. El long-mirror revierte; probamos OTROS gatillos +
stops ANCHOS (bajan fee_r) para ver si algo sobrevive la fee real de spot (0.20%).
Mide AvgR neto IS/OOS a fee spot 0.20% y futuros 0.11%.
"""
import pandas as pd, numpy as np
import mtf_longs as L

H1_MS = L.H1_MS
df = pd.read_parquet('data/bybit-spot/processed/btcusdt_m1.parquet').sort_values('ts_ms').reset_index(drop=True)
for c in df.columns:
    if df[c].dtype == object: df[c] = df[c].fillna('')
    elif df[c].dtype == float: df[c] = df[c].fillna(0.0)

h1 = L.resample_h1(df)
h1['atr'] = L.atr14(h1['high'].values, h1['low'].values, h1['close'].values)
h1ctx = {int(r.ts_ms): (float(r.low), float(r.atr)) for r in h1.itertuples()}
rows = df.to_dict('records')
N = len(rows)
OOS = L.OOS_MS


def fb(v): return bool(v) and str(v).lower() not in ('', '0', 'false', 'none', 'nan')


# ── gatillos candidatos (cada uno: func(row)->bool). Todos en sesion london/ovl/ny ──
def g_mirror(r):   # long espejo actual (referencia)
    o, c, h, l = float(r['open']), float(r['close']), float(r['high']), float(r['low'])
    rng = h - l
    if rng <= 0: return False
    wick = (min(c, o) - l) / rng
    val = r.get('vp_val')
    near = val and val > 0 and abs(l - val) / val <= 0.007
    return near and 0.30 < wick < 0.85 and c >= o and (float(r.get('obi10_mean') or 0) > 0.05 or float(r.get('delta') or 0) > 0)

def g_sweep(r):    # barrido de liquidez bajo soporte + recuperacion (equal_low / sweep)
    o, c = float(r['open']), float(r['close'])
    return (fb(r.get('equal_low')) or fb(r.get('sweep_confirmed'))) and c >= o and float(r.get('delta') or 0) > 0

def g_cvddiv(r):   # divergencia CVD alcista (precio baja, CVD sube)
    o, c = float(r['open']), float(r['close'])
    return fb(r.get('cvd_div')) and float(r.get('cvd_slope') or 0) > 0 and c >= o

def g_absbid(r):   # absorcion en bid (compradores absorben venta)
    o, c = float(r['open']), float(r['close'])
    return fb(r.get('abs_bid')) and c >= o and float(r.get('obi10_mean') or 0) > 0

def g_ote(r):      # zona OTE fib (retroceso 62-79) con cierre alcista
    o, c = float(r['open']), float(r['close'])
    return (fb(r.get('ote_62')) or fb(r.get('ote_79')) or fb(r.get('fib_ote'))) and c >= o

def g_stacked(r):  # imbalances alcistas apilados
    o, c = float(r['open']), float(r['close'])
    return str(r.get('stacked_imb')) == 'Bullish' and c >= o

def g_vlnode(r):   # rebote desde low volume node + delta+
    o, c = float(r['open']), float(r['close'])
    return fb(r.get('vp_lvn_below')) and c >= o and float(r.get('delta') or 0) > 0

TRIGGERS = {'mirror VAL': g_mirror, 'sweep/eqlow': g_sweep, 'cvd_div': g_cvddiv,
            'abs_bid': g_absbid, 'OTE fib': g_ote, 'stacked_bull': g_stacked, 'lvn_below': g_vlnode}

STOPS = [('a0.4 [.3-.75]', 0.40, 0.0030, 0.0075),
         ('a0.8 [.5-1.2]',  0.80, 0.0050, 0.0120),
         ('a1.5 [.8-2.5]',  1.50, 0.0080, 0.0250)]


def sim(trigger, atr_mult, mn, mx, target, fee):
    res = []; i = 0
    while i < N:
        row = rows[i]; ts = int(row['ts_ms'])
        if not L.session(ts) or not trigger(row):
            i += 1; continue
        h1d = h1ctx.get((ts // H1_MS) * H1_MS)
        if h1d is None: i += 1; continue
        h1l, h1a = h1d; c = float(row['close'])
        sl = h1l - atr_mult * h1a; d = c - sl
        if d <= 0 or not (mn <= d / c <= mx): i += 1; continue
        tp = c + target * d; oos = ts >= OOS; close_i = None; net = 0.0
        for j in range(i + 1, min(i + 1 + L.FORWARD, N)):
            r = rows[j]
            if r['low'] <= sl: net = -1.0 - fee * c / d; close_i = j; break
            if r['high'] >= tp: net = target - fee * c / d; close_i = j; break
        if close_i is None:
            net = (rows[min(i + L.FORWARD, N - 1)]['close'] - c) / d - fee * c / d
            close_i = min(i + L.FORWARD, N - 1)
        res.append((net, oos)); i = close_i + 1
    return res


def st(res, oos):
    a = np.array([r for r, o in res if o == oos])
    if len(a) == 0: return (0, 0.0, 0.0)
    return (len(a), (a > 0).mean() * 100, a.mean())


print('LONGS — exploracion de gatillos x stop (target fijo 2.5R). AvgR NETO IS/OOS.')
print('Busca: positivo en IS *y* OOS. Marcado @spot0.20% si OOS>0; @fut0.11% aparte.\n')
for tname, trig in TRIGGERS.items():
    print(f'-- {tname} --')
    for sname, am, mn, mx in STOPS:
        for fee, ftag in [(0.0020, 'spot'), (0.0011, 'fut ')]:
            res = sim(trig, am, mn, mx, 2.5, fee)
            (ni, wi, ai) = st(res, False); (no, wo, ao) = st(res, True)
            if ni < 20: continue
            flag = ''
            if ai > 0 and ao > 0: flag = ' <== ROBUSTO+'
            elif ao > 0.05: flag = ' (oos+)'
            print(f'   {sname:<14} {ftag} | IS n={ni:>4} WR={wi:>3.0f}% AvgR={ai:>+6.3f} | OOS n={no:>4} WR={wo:>3.0f}% AvgR={ao:>+6.3f}{flag}')
    print()
