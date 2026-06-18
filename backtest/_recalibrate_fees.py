"""
_recalibrate_fees.py — re-calibracion bajo fee REAL de Bybit spot (0.20% round-trip).
Busca si existe config (stop width x target) con edge NETO positivo y robusto IS+OOS.
Mide AvgR neto equal-weight (el edge puro, sin sizing). fee_r = FEE_RT * ep/dist.
"""
import pandas as pd, numpy as np
import mtf_basics as M, mtf_longs as L

df = pd.read_parquet(M.DATA_M1).sort_values('ts_ms').reset_index(drop=True)
for c in df.columns:
    if df[c].dtype == object: df[c] = df[c].fillna('')
    elif df[c].dtype == float: df[c] = df[c].fillna(0.0)

H1_MS = M.H1_MS
h1 = M.resamp(df, H1_MS)
h1['atr'] = M.atr14(h1['high'].values, h1['low'].values, h1['close'].values)
h1ctx = {int(r.ts_ms): (float(r.high), float(r.low), float(r.atr)) for r in h1.itertuples()}
rows = df.to_dict('records')
N = len(rows)


def sim(direction, atr_mult, min_stop, max_stop, target, fee_rt):
    in_t = False; ep = sl = tp = dist = 0.0; t_start = 0; t_oos = False; cvd = 0
    res = []
    for i, row in enumerate(rows):
        ts = int(row['ts_ms'])
        if in_t:
            cs = float(row.get('cvd_slope') or 0); obi = float(row.get('obi10_mean') or 0)
            if direction == 'short':
                cvd = (cvd + 1) if cs > 0 else 0
                cur_r = (ep - row['close']) / dist
                hit_stop = row['high'] >= sl; hit_tp = row['low'] <= tp
                cvd_exit = cvd >= 3 and obi > 0.15 and cur_r >= 1.0
            else:
                cvd = (cvd + 1) if cs < 0 else 0
                cur_r = (row['close'] - ep) / dist
                hit_stop = row['low'] <= sl; hit_tp = row['high'] >= tp
                cvd_exit = cvd >= 3 and obi < -0.15 and cur_r >= 1.0
            reason = None; px = 0.0
            if hit_stop: reason, px = 'stop', sl
            elif hit_tp: reason, px = 'tp', tp
            elif i - t_start >= M.FORWARD: reason, px = 'to', row['close']
            elif cvd_exit: reason, px = 'cvd', row['close']
            if reason:
                gross = (ep - px) / dist if direction == 'short' else (px - ep) / dist
                net = gross - fee_rt * ep / dist
                res.append((net, t_oos)); in_t = False; cvd = 0
            continue

        sess = M.session(ts)
        if sess not in ('london', 'overlap', 'ny'): continue
        h1d = h1ctx.get((ts // H1_MS) * H1_MS)
        if h1d is None: continue
        h1h, h1l, h1a = h1d
        c, o, h, l = float(row['close']), float(row['open']), float(row['high']), float(row['low'])
        rng = h - l
        if rng <= 0: continue

        if direction == 'short':
            ok, lbl = M.active_level(row)
            if not ok or 'VAH' not in lbl: continue
            p = lbl.split('+')
            if len(p) >= 3 and 'PDH' in p and 'AH' in p: continue
            if 'PDH' in p and 'VAH' in p and len(p) == 2: continue
            if 'WH' in p and (ts // 3_600_000) % 24 == 15: continue
            if lbl == 'AH+VAH' and float(row.get('obi10_mean') or 0) < -0.15: continue
            wick = (h - max(c, o)) / rng
            if not (0.30 < wick < 0.85) or c > o: continue
            if not (float(row.get('obi10_mean') or 0) < -0.05 or float(row.get('delta') or 0) < 0): continue
            sl_ = h1h + atr_mult * h1a; d = sl_ - c
        else:
            low = l; levels = []
            for k, col in [('PDL', 'prev_day_low'), ('AL', 'asian_low'), ('WL', 'weekly_low'), ('VAL', 'vp_val')]:
                v = row.get(col)
                if v and v > 0 and abs(low - v) / v <= L.LEVEL_TOL: levels.append(k)
            if 'VAL' not in levels: continue
            p = '+'.join(levels).split('+')
            if len(p) >= 3 and 'PDL' in p and 'AL' in p: continue
            if bool(row.get('bid_wall')): continue
            wick = (min(c, o) - l) / rng
            if not (0.30 < wick < 0.85) or c < o: continue
            if float(row.get('obi10_mean') or 0) <= 0.05 and float(row.get('delta') or 0) <= 0: continue
            sl_ = h1l - atr_mult * h1a; d = c - sl_

        if d <= 0: continue
        if not (min_stop <= d / c <= max_stop): continue
        in_t = True; ep = c; sl = sl_; dist = d; t_start = i; t_oos = ts >= M.OOS_MS; cvd = 0
        tp = ep - target * dist if direction == 'short' else ep + target * dist
    return res


def summarize(res):
    is_r = [r for r, o in res if not o]; oo_r = [r for r, o in res if o]
    def s(x):
        if not x: return (0, 0.0, 0.0)
        return (len(x), sum(1 for v in x if v > 0) / len(x) * 100, sum(x) / len(x))
    return s(is_r), s(oo_r)


STOPS = [('actual a0.4 [.30-.75]', 0.40, 0.0030, 0.0075),
         ('ancho a0.8 [.50-1.2]',  0.80, 0.0050, 0.0120),
         ('amplio a1.2 [.75-2.0]',  1.20, 0.0075, 0.0200)]
TARGETS = [1.5, 2.0, 3.0, 4.0]
FEE = 0.0020   # spot basico real

for direction in ('short', 'long'):
    print(f'\n{"="*78}\n{direction.upper()}  — fee REAL 0.20% — AvgR NETO equal-weight (edge puro)\n{"="*78}')
    print(f'{"stop":<22} {"tgt":>4} | {"IS  n":>6} {"WR":>5} {"AvgR":>7} | {"OOS n":>6} {"WR":>5} {"AvgR":>7}')
    for sname, am, mn, mx in STOPS:
        for tg in TARGETS:
            res = sim(direction, am, mn, mx, tg, FEE)
            (ni, wi, ai), (no, wo, ao) = summarize(res)
            flag = ' <== +' if (ao > 0 and ai > 0) else ''
            print(f'{sname:<22} {tg:>4.1f} | {ni:>6} {wi:>4.0f}% {ai:>+7.3f} | {no:>6} {wo:>4.0f}% {ao:>+7.3f}{flag}')
