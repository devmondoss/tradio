import pandas as pd, numpy as np

CAPITAL = 500.0; RISK_PCT = 0.02; FEE_RT = 0.0007
TARGET_R = 2.0; FORWARD = 1200; MIN_STOP = 0.003; MAX_STOP = 0.0075
H1_MS = 3_600_000; H4_MS = 14_400_000; D1_MS = 86_400_000
OOS_MS = int(pd.Timestamp('2026-03-01', tz='UTC').value // 1_000_000)
LEVEL_TOL = 0.007

def ema_calc(v, n):
    k = 2/(n+1); o = np.empty(len(v)); o[0] = v[0]
    for i in range(1, len(v)): o[i] = v[i]*k + o[i-1]*(1-k)
    return o

def atr14(h, l, c):
    tr = np.maximum(h-l, np.maximum(np.abs(h-np.roll(c,1)), np.abs(l-np.roll(c,1)))); tr[0]=h[0]-l[0]
    o = np.empty_like(tr); o[:14] = tr[:14].mean(); k = 1/14
    for i in range(14, len(tr)): o[i] = o[i-1]*(1-k) + tr[i]*k
    return o

def resamp(df, f):
    df = df.copy(); df['tf'] = (df['ts_ms']//f)*f
    return df.groupby('tf', sort=True).agg(
        open=('open','first'), high=('high','max'),
        low=('low','min'), close=('close','last')
    ).reset_index().rename(columns={'tf':'ts_ms'})

def session(ts):
    hm = (ts//60_000) % 1440
    if 12*60 <= hm < 16*60: return 'overlap'
    if 16*60 <= hm < 20*60: return 'ny'
    return ''

def simulate(df, h4_ema=False, h4_no_hh=False):
    # H1 para stop
    h1 = resamp(df, H1_MS)
    h1['atr'] = atr14(h1['high'].values, h1['low'].values, h1['close'].values)
    h1_ctx = {int(r.ts_ms): (float(r.high), float(r.atr)) for r in h1.itertuples()}

    # H4 para sesgo de dirección
    h4 = resamp(df, H4_MS)
    h4['ema20'] = ema_calc(h4['close'].values, 20)
    h4['prev_high'] = h4['high'].shift(1)
    # h4_ctx: ts -> (close, ema20, high, prev_high)
    h4_ctx = {}
    for r in h4.itertuples():
        h4_ctx[int(r.ts_ms)] = (float(r.close), float(r.ema20), float(r.high), float(r.prev_high) if not np.isnan(r.prev_high) else 9e9)

    rows = df.to_dict('records'); trades = []; in_t = False
    ep = sl = tp = dist = 0.0; t_start = t_entry = 0; t_lbl = ''; cvd_streak = 0; cap = CAPITAL

    for i, row in enumerate(rows):
        ts = int(row['ts_ms'])

        if in_t:
            obi = float(row.get('obi10_mean') or 0); cs = float(row.get('cvd_slope') or 0)
            cvd_streak = (cvd_streak+1) if cs > 0 else 0
            cur_r = (ep - row['close'])/dist; reason = None; exit_px = 0.0
            if row['high'] >= sl: reason, exit_px = 'stop', sl
            elif row['low'] <= tp: reason, exit_px = 'target', tp
            elif i - t_start >= FORWARD: reason, exit_px = 'timeout', row['close']
            elif cvd_streak >= 5 and obi > 0.15 and cur_r >= 1.0: reason, exit_px = 'cvd_exit', row['close']
            if reason:
                pnl_r = (ep - exit_px)/dist; risk_usd = cap*RISK_PCT
                pnl_usd = risk_usd*pnl_r - cap*RISK_PCT*FEE_RT
                trades.append({'ts_ms': t_entry, 'result_r': round(pnl_r,3), 'oos': ts>=OOS_MS, 'level': t_lbl})
                cap += pnl_usd; in_t = False; cvd_streak = 0
            continue

        sess = session(ts)
        if not sess: continue

        # ── Filtro H4 ──────────────────────────────────────────────────────────
        h4_ts = (ts // H4_MS) * H4_MS
        h4d = h4_ctx.get(h4_ts)
        if h4d is None: continue
        h4_close, h4_ema20, h4_high, h4_prev_high = h4d

        if h4_ema and h4_close >= h4_ema20: continue      # H4 bajista: close < EMA20
        if h4_no_hh and h4_high > h4_prev_high: continue  # sin HH en H4

        # ── Nivel ──────────────────────────────────────────────────────────────
        high = row['high']; levels = []
        for k, col in [('PDH','prev_day_high'),('AH','asian_high'),('WH','weekly_high'),('VAH','vp_vah')]:
            v = row.get(col)
            if v and v > 0 and abs(high - v)/v <= LEVEL_TOL: levels.append(k)
        if not levels: continue
        lbl = '+'.join(levels)
        if 'VAH' not in lbl: continue
        parts = lbl.split('+')
        if len(parts) >= 3 and 'PDH' in parts and 'AH' in parts: continue
        if 'PDH' in parts and 'VAH' in parts and len(parts) == 2: continue
        if 'WH' in parts and (ts//3_600_000)%24 == 15: continue
        if lbl == 'AH+VAH' and float(row.get('obi10_mean') or 0) < -0.15: continue

        # ── Rechazo ────────────────────────────────────────────────────────────
        c, o, h, l = float(row['close']), float(row['open']), float(row['high']), float(row['low'])
        rng = h - l
        if rng <= 0: continue
        wick_pct = (h - max(c,o))/rng
        if not (0.30 < wick_pct < 0.85) or c > o: continue

        # ── Flujo ──────────────────────────────────────────────────────────────
        obi = float(row.get('obi10_mean') or 0); delta = float(row.get('delta') or 0)
        if obi >= -0.05 and delta >= 0: continue

        # ── Stop H1 ────────────────────────────────────────────────────────────
        h1d = h1_ctx.get((ts//H1_MS)*H1_MS)
        if h1d is None: continue
        h1h, h1a = h1d; sl_ = h1h + 0.40*h1a; d = sl_ - row['close']
        if d <= 0: continue
        sp = d/row['close']
        if not (MIN_STOP <= sp <= MAX_STOP): continue

        in_t = True; ep = row['close']; sl = sl_; dist = d; tp = ep - TARGET_R*dist
        t_start = i; t_entry = ts; t_lbl = lbl; cvd_streak = 0

    return trades, cap

print('Cargando M1...')
df = pd.read_parquet('data/bybit-spot/processed/btcusdt_m1.parquet').sort_values('ts_ms').reset_index(drop=True)
for c in df.columns:
    if df[c].dtype == object: df[c] = df[c].fillna('')
    elif df[c].dtype == float: df[c] = df[c].fillna(0.0)

configs = [
    (False, False, 'BASE (sin filtro H4)'),
    (True,  False, 'H4 close < EMA20'),
    (False, True,  'H4 sin HH'),
    (True,  True,  'H4 EMA20 + sin HH'),
]

print(f"\n{'Config':<28}  {'n_tot':>6}  {'WR_IS':>6}  {'n_IS':>5}  {'WR_OOS':>6}  {'n_OOS':>5}  {'AvgR_OOS':>9}  {'tpd_OOS':>8}  {'Capital':>10}")
print('-'*110)

for h4e, h4h, label in configs:
    trades, cap = simulate(df, h4_ema=h4e, h4_no_hh=h4h)
    ist = [t for t in trades if not t['oos']]; oot = [t for t in trades if t['oos']]
    def s(ts):
        if not ts: return 0, 0.0, 0.0
        n = len(ts); w = sum(1 for t in ts if t['result_r'] > 0)
        return n, w/n*100, sum(t['result_r'] for t in ts)/n
    ni, wi, _ = s(ist); no, wo, ao = s(oot)
    print(f"{label:<28}  {ni+no:>6}  {wi:>5.1f}%  {ni:>5}  {wo:>5.1f}%  {no:>5}  {ao:>+.3f}      {no/90:>5.1f}    ${cap:>9,.0f}")

print('\n-- Por nivel (BASE) -----------------------------------------')
trades_base, _ = simulate(df)
oot_base = [t for t in trades_base if t['oos']]
from collections import defaultdict
by_lbl = defaultdict(list)
for t in oot_base: by_lbl[t['level']].append(t['result_r'])
for lbl in sorted(by_lbl, key=lambda x: -len(by_lbl[x])):
    rs = by_lbl[lbl]; n = len(rs); w = sum(1 for r in rs if r>0)
    print(f"  {lbl:<20}  n={n:>3}  WR={w/n*100:>5.1f}%  AvgR={sum(rs)/n:>+.3f}")
