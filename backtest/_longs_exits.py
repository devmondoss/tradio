"""
_longs_exits.py — Paso 1 para LONGS: subir AvgR via SALIDAS.
Espejo de _shorts_exits.py. Re-simulacion honesta (1 pos a la vez) @ fee futuros 0.11%.
Compara baseline (regime+CVD) vs target fijo sin CVD, BE, trailing, parcial.
"""
import pandas as pd, numpy as np
import mtf_longs as L

FEE = 0.0011
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


def entry_at(i, row, ts):
    if not L.session(ts): return None
    low = row['low']; levels = []
    for k, col in [('PDL', 'prev_day_low'), ('AL', 'asian_low'), ('WL', 'weekly_low'), ('VAL', 'vp_val')]:
        v = row.get(col)
        if v and v > 0 and abs(low - v) / v <= L.LEVEL_TOL: levels.append(k)
    if 'VAL' not in levels: return None
    p = '+'.join(levels).split('+')
    if len(p) >= 3 and 'PDL' in p and 'AL' in p: return None
    if bool(row.get('bid_wall')): return None
    c, o, h, l = float(row['close']), float(row['open']), float(row['high']), float(row['low'])
    rng = h - l
    if rng <= 0: return None
    if not (0.30 < (min(c, o) - l) / rng < 0.85) or c < o: return None
    if float(row.get('obi10_mean') or 0) <= 0.05 and float(row.get('delta') or 0) <= 0: return None
    h1d = h1ctx.get((ts // H1_MS) * H1_MS)
    if h1d is None: return None
    h1l, h1a = h1d
    sl = h1l - 0.40 * h1a; d = c - sl
    if d <= 0: return None
    if not (L.MIN_STOP <= d / c <= L.MAX_STOP): return None
    return c, sl, d, ts >= L.OOS_MS, str(row.get('regime') or '')


def run(policy):
    res = []; i = 0
    while i < N:
        ts = int(rows[i]['ts_ms'])
        e = entry_at(i, rows[i], ts)
        if e is None: i += 1; continue
        ep, sl0, dist, oos, reg = e
        st = {'ep': ep, 'sl': sl0, 'dist': dist, 'reg': reg, 'best_r': 0.0,
              'cvd': 0, 'partial': 0.0, 'got_partial': False}
        net = 0.0; close_i = None
        for j in range(i + 1, min(i + 1 + L.FORWARD, N)):
            r = rows[j]
            cur_r = (r['close'] - ep) / dist
            st['best_r'] = max(st['best_r'], (r['high'] - ep) / dist)
            cs = float(r.get('cvd_slope') or 0); obi = float(r.get('obi10_mean') or 0)
            st['cvd'] = (st['cvd'] + 1) if cs < 0 else 0
            out = policy(st, r, cur_r, obi)
            if out:
                net = st['partial'] + out[1] - FEE * ep / dist
                close_i = j; break
        if close_i is None:
            g = (rows[min(i + L.FORWARD, N - 1)]['close'] - ep) / dist
            net = st['partial'] + (0.5 if st['got_partial'] else 1.0) * g - FEE * ep / dist
            close_i = min(i + L.FORWARD, N - 1)
        res.append((net, oos)); i = close_i + 1
    return res


def make_regime(cvd_exit=True):
    def pol(st, r, cur_r, obi):
        tgt = 1.5 if st['reg'] == 'Chop' else (3.0 if st['reg'] == 'Expansion' else 2.0)
        if r['low'] <= st['sl']: return 'stop', (st['sl'] - st['ep']) / st['dist']
        if r['high'] >= st['ep'] + tgt * st['dist']: return 'tp', tgt
        if cvd_exit and st['cvd'] >= 3 and obi < -0.15 and cur_r >= 1.0: return 'cvd', cur_r
        return None
    return pol

def make_fixed(tgt, cvd_exit=False):
    def pol(st, r, cur_r, obi):
        if r['low'] <= st['sl']: return 'stop', (st['sl'] - st['ep']) / st['dist']
        if r['high'] >= st['ep'] + tgt * st['dist']: return 'tp', tgt
        if cvd_exit and st['cvd'] >= 3 and obi < -0.15 and cur_r >= 1.0: return 'cvd', cur_r
        return None
    return pol

def make_be(tgt, be_at=1.0):
    def pol(st, r, cur_r, obi):
        if st['best_r'] >= be_at and st['sl'] < st['ep']: st['sl'] = st['ep']
        if r['low'] <= st['sl']: return 'stop', (st['sl'] - st['ep']) / st['dist']
        if r['high'] >= st['ep'] + tgt * st['dist']: return 'tp', tgt
        return None
    return pol

def make_partial(partial_at=1.0, runner_tgt=3.0):
    def pol(st, r, cur_r, obi):
        if not st['got_partial'] and st['best_r'] >= partial_at:
            st['partial'] = 0.5 * partial_at; st['got_partial'] = True; st['sl'] = st['ep']
        if r['low'] <= st['sl']:
            half = 0.5 if st['got_partial'] else 1.0
            return 'stop', half * (st['sl'] - st['ep']) / st['dist']
        if r['high'] >= st['ep'] + runner_tgt * st['dist']:
            half = 0.5 if st['got_partial'] else 1.0
            return 'tp', half * runner_tgt
        return None
    return pol


def stats(res):
    def s(a):
        a = np.array(a)
        if len(a) == 0: return (0, 0.0, 0.0, 0.0, 0.0)
        w = a[a > 0]; l = a[a <= 0]
        return (len(a), len(w) / len(a) * 100, a.mean(),
                w.mean() if len(w) else 0.0, l.mean() if len(l) else 0.0)
    return s([r for r, o in res if not o]), s([r for r, o in res if o])


if __name__ == '__main__':
    POLICIES = {
        'BASELINE regime+CVD': make_regime(True),
        'regime SIN CVD':      make_regime(False),
        'fijo 2R + CVD':       make_fixed(2.0, True),
        'fijo 2R SIN CVD':     make_fixed(2.0, False),
        'fijo 2.5R SIN CVD':   make_fixed(2.5, False),
        'fijo 3R SIN CVD':     make_fixed(3.0, False),
        'BE@1R + tgt 3R':      make_be(3.0, 1.0),
        'parcial 1R+run 3R':   make_partial(1.0, 3.0),
    }
    print(f'LONGS — Paso 1: salidas @ fee futuros {FEE*100:.2f}% — result NETO equal-weight')
    print(f'{"politica":<22} | {"IS n":>5} {"WR":>4} {"AvgR":>7} {"TotR":>6} | {"OOS n":>5} {"WR":>4} {"AvgR":>7} {"TotR":>6}')
    print('-' * 100)
    for name, pol in POLICIES.items():
        (ni, wi, ai, _, _), (no, wo, ao, _, _) = stats(run(pol))
        flag = ' <==' if ao > 0.08 else ''
        print(f'{name:<22} | {ni:>5} {wi:>3.0f}% {ai:>+7.3f} {ni*ai:>+6.0f} | {no:>5} {wo:>3.0f}% {ao:>+7.3f} {no*ao:>+6.0f}{flag}')
