"""
_shorts_exits.py — Paso 1: subir AvgR de SHORTS via SALIDAS (volumen ~intacto).
Re-simulacion honesta (1 posicion a la vez) a fee de FUTUROS 0.11%.
Compara: baseline regime, target fijo, breakeven@1R, trailing, parcial+runner.
Valida IS y OOS por separado. result NETO (gross - fee_r), fee_r = FEE*ep/dist.
"""
import pandas as pd, numpy as np
import mtf_basics as M

FEE = 0.0011  # futuros round-trip
H1_MS = M.H1_MS

df = pd.read_parquet(M.DATA_M1).sort_values('ts_ms').reset_index(drop=True)
for c in df.columns:
    if df[c].dtype == object: df[c] = df[c].fillna('')
    elif df[c].dtype == float: df[c] = df[c].fillna(0.0)

h1 = M.resamp(df, H1_MS)
h1['atr'] = M.atr14(h1['high'].values, h1['low'].values, h1['close'].values)
h1ctx = {int(r.ts_ms): (float(r.high), float(r.atr)) for r in h1.itertuples()}
rows = df.to_dict('records')
N = len(rows)


def entry_at(i, row, ts):
    """Replica deteccion de mtf_basics shorts. Devuelve (ep,sl,dist,oos) o None."""
    sess = M.session(ts)
    if sess not in ('london', 'overlap', 'ny'): return None
    ok, lbl = M.active_level(row)
    if not ok or 'VAH' not in lbl: return None
    p = lbl.split('+')
    if len(p) >= 3 and 'PDH' in p and 'AH' in p: return None
    if 'PDH' in p and 'VAH' in p and len(p) == 2: return None
    if 'WH' in p and (ts // 3_600_000) % 24 == 15: return None
    if lbl == 'AH+VAH' and float(row.get('obi10_mean') or 0) < -0.15: return None
    if not M.rejection(row) or not M.flow_bearish(row): return None
    h1d = h1ctx.get((ts // H1_MS) * H1_MS)
    if h1d is None: return None
    h1h, h1a = h1d
    sl = h1h + 0.40 * h1a; c = float(row['close']); d = sl - c
    if d <= 0: return None
    if not (M.MIN_STOP <= d / c <= M.MAX_STOP): return None
    return c, sl, d, ts >= M.OOS_MS, str(row.get('regime') or '')


def run(policy):
    """policy: func(state, row) -> ('reason', exit_px) o None. Devuelve lista (net_r, oos)."""
    res = []
    i = 0
    while i < N:
        ts = int(rows[i]['ts_ms'])
        e = entry_at(i, rows[i], ts)
        if e is None:
            i += 1; continue
        ep, sl0, dist, oos, reg = e
        # estado del trade
        st = {'ep': ep, 'sl': sl0, 'dist': dist, 'reg': reg, 'i0': i,
              'best_r': 0.0, 'cvd': 0, 'partial': 0.0, 'got_partial': False}
        close_i = None; net = 0.0
        for j in range(i + 1, min(i + 1 + M.FORWARD, N)):
            r = rows[j]
            cur_r = (ep - r['close']) / dist
            fav_r = (ep - r['low']) / dist          # mejor punto de la barra
            st['best_r'] = max(st['best_r'], fav_r)
            cs = float(r.get('cvd_slope') or 0); obi = float(r.get('obi10_mean') or 0)
            st['cvd'] = (st['cvd'] + 1) if cs > 0 else 0
            out = policy(st, r, cur_r, obi)
            if out:
                reason, gross_extra = out
                net = st['partial'] + gross_extra - FEE * ep / dist
                close_i = j; break
        if close_i is None:
            gross = (ep - rows[min(i + M.FORWARD, N - 1)]['close']) / dist
            net = st['partial'] + (0.5 if st['got_partial'] else 1.0) * gross - FEE * ep / dist
            close_i = min(i + M.FORWARD, N - 1)
        res.append((net, oos))
        i = close_i + 1
    return res


# ── politicas ───────────────────────────────────────────────────────────────
def make_regime(cvd_exit=True):
    def pol(st, r, cur_r, obi):
        tgt = 1.5 if st['reg'] == 'Chop' else (3.0 if st['reg'] == 'Expansion' else 2.0)
        if r['high'] >= st['sl']: return 'stop', (st['ep'] - st['sl']) / st['dist']
        if r['low'] <= st['ep'] - tgt * st['dist']: return 'tp', tgt
        if cvd_exit and st['cvd'] >= 3 and obi > 0.15 and cur_r >= 1.0: return 'cvd', cur_r
        return None
    return pol

def make_fixed(tgt, cvd_exit=False):
    def pol(st, r, cur_r, obi):
        if r['high'] >= st['sl']: return 'stop', (st['ep'] - st['sl']) / st['dist']
        if r['low'] <= st['ep'] - tgt * st['dist']: return 'tp', tgt
        if cvd_exit and st['cvd'] >= 3 and obi > 0.15 and cur_r >= 1.0: return 'cvd', cur_r
        return None
    return pol

def make_be(tgt, be_at=1.0):
    def pol(st, r, cur_r, obi):
        if st['best_r'] >= be_at and st['sl'] > st['ep']:
            st['sl'] = st['ep']                      # mover a breakeven
        if r['high'] >= st['sl']: return 'stop', (st['ep'] - st['sl']) / st['dist']
        if r['low'] <= st['ep'] - tgt * st['dist']: return 'tp', tgt
        return None
    return pol

def make_trail(activate=1.0, gap=1.0):
    def pol(st, r, cur_r, obi):
        # trailing: una vez best_r>=activate, stop = nivel (best_r - gap)
        if st['best_r'] >= activate:
            trail_r = st['best_r'] - gap
            trail_px = st['ep'] - trail_r * st['dist']
            if trail_px < st['sl']: st['sl'] = trail_px   # short: stop baja hacia el precio
        if r['high'] >= st['sl']: return 'stop', (st['ep'] - st['sl']) / st['dist']
        return None
    return pol

def make_partial(partial_at=1.0, runner_tgt=3.0):
    def pol(st, r, cur_r, obi):
        if not st['got_partial'] and st['best_r'] >= partial_at:
            st['partial'] = 0.5 * partial_at; st['got_partial'] = True; st['sl'] = st['ep']
        if r['high'] >= st['sl']:
            half = 0.5 if st['got_partial'] else 1.0
            return 'stop', half * (st['ep'] - st['sl']) / st['dist']
        if r['low'] <= st['ep'] - runner_tgt * st['dist']:
            half = 0.5 if st['got_partial'] else 1.0
            return 'tp', half * runner_tgt
        return None
    return pol


POLICIES = {
    'BASELINE regime+CVD':   make_regime(),
    'fijo 2R':               make_fixed(2.0),
    'fijo 3R':               make_fixed(3.0),
    'BE@1R + tgt 3R':        make_be(3.0, 1.0),
    'BE@1R + tgt 4R':        make_be(4.0, 1.0),
    'trail act1R gap1R':     make_trail(1.0, 1.0),
    'trail act1.5R gap1R':   make_trail(1.5, 1.0),
    'parcial 1R+run 3R':     make_partial(1.0, 3.0),
    'parcial 1R+run 4R':     make_partial(1.0, 4.0),
}


def stats(res):
    is_r = np.array([r for r, o in res if not o]); oo_r = np.array([r for r, o in res if o])
    def s(a):
        if len(a) == 0: return (0, 0.0, 0.0, 0.0, 0.0)
        w = a[a > 0]; l = a[a <= 0]
        return (len(a), len(w) / len(a) * 100, a.mean(),
                w.mean() if len(w) else 0.0, l.mean() if len(l) else 0.0)
    return s(is_r), s(oo_r)


print(f'SHORTS — Paso 1: salidas @ fee futuros {FEE*100:.2f}% — result NETO equal-weight')
print(f'{"politica":<22} | {"IS n":>5} {"WR":>4} {"AvgR":>7} {"win":>6} {"loss":>6} | {"OOS n":>5} {"WR":>4} {"AvgR":>7} {"win":>6} {"loss":>6}')
print('-' * 120)
for name, pol in POLICIES.items():
    (ni, wi, ai, wwi, lli), (no, wo, ao, wwo, llo) = stats(run(pol))
    flag = ' <==' if ao > 0.10 else ''
    print(f'{name:<22} | {ni:>5} {wi:>3.0f}% {ai:>+7.3f} {wwi:>+6.2f} {lli:>+6.2f} | '
          f'{no:>5} {wo:>3.0f}% {ao:>+7.3f} {wwo:>+6.2f} {llo:>+6.2f}{flag}')
