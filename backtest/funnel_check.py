import pandas as pd, numpy as np

m1  = pd.read_parquet('data/bybit-spot/processed/btcusdt_m1.parquet').sort_values('ts_ms').reset_index(drop=True)
m5  = pd.read_parquet('data/bybit-spot/processed/btcusdt_m5.parquet').sort_values('ts_ms').reset_index(drop=True)
m15 = pd.read_parquet('data/bybit-spot/processed/btcusdt_m15.parquet').sort_values('ts_ms').reset_index(drop=True)

M5_MS  = 5  * 60_000
M15_MS = 15 * 60_000
H1_MS  = 60 * 60_000
D1_MS  = 86_400_000

ZONE_FEATS = ['near_weekly_high','near_asian_high','near_pdh','near_bearish_fvg',
              'near_bearish_ob','equal_high','fib_ote','above_poc','val_near']

def mk_ema(v, n):
    k = 2/(n+1)
    o = np.empty(len(v)); o[0] = v[0]
    for i in range(1, len(v)):
        o[i] = v[i]*k + o[i-1]*(1-k)
    return o

def mk_atr(h, l, c):
    tr = np.maximum(h-l, np.maximum(np.abs(h-np.roll(c,1)), np.abs(l-np.roll(c,1))))
    tr[0] = h[0]-l[0]
    o = np.empty_like(tr); o[:14] = tr[:14].mean(); k = 1/14
    for i in range(14, len(tr)):
        o[i] = o[i-1]*(1-k) + tr[i]*k
    return o

def resamp(df, f):
    df = df.copy()
    df['tf'] = (df['ts_ms'] // f) * f
    return df.groupby('tf', sort=True).agg(
        open=('open','first'), high=('high','max'),
        low=('low','min'),    close=('close','last')
    ).reset_index().rename(columns={'tf':'ts_ms'})

h1 = resamp(m1, H1_MS)
h1['atr'] = mk_atr(h1['high'].values, h1['low'].values, h1['close'].values)
d1 = resamp(m1, D1_MS)
d1['ema20'] = mk_ema(d1['close'].values, 20)

h1_ctx  = {int(r.ts_ms): (float(r.high), float(r.atr)) for r in h1.itertuples()}
d1_ctx  = {int(r.ts_ms): float(r.ema20) for r in d1.itertuples()}
m5_ctx  = {int(r.ts_ms): r._asdict()   for r in m5.itertuples()}
m15_ctx = {int(r.ts_ms): r._asdict()   for r in m15.itertuples()}

total = a_sess = a_d1 = a_reg = a_zone = a_setup = a_trig = a_stop = 0

for row in m1.to_dict('records'):
    ts  = int(row['ts_ms'])
    hm  = (ts // 60_000) % 1440
    total += 1

    if not (7*60 <= hm < 20*60):
        continue
    a_sess += 1

    d1_ts  = (ts // D1_MS) * D1_MS - D1_MS
    d1e    = d1_ctx.get(d1_ts)
    if d1e is None or row['close'] > d1e * 1.005:
        continue
    a_d1 += 1

    if str(row.get('regime') or '') == 'TrendDown':
        continue
    a_reg += 1

    m15r = m15_ctx.get((ts // M15_MS) * M15_MS - M15_MS)
    if m15r is None:
        continue
    if not any(m15r.get(f, False) for f in ZONE_FEATS):
        continue
    a_zone += 1

    m5r = m5_ctx.get((ts // M5_MS) * M5_MS - M5_MS)
    if m5r is None:
        continue
    stk  = str(m5r.get('stacked_imb') or '')
    obi5 = float(m5r.get('obi10_mean') or 0)
    c1 = bool(m5r.get('pdh_sweep') or m5r.get('equal_high_sweep') or m5r.get('sweep_confirmed'))
    c2 = bool(m5r.get('abs_ask')) and stk == 'Bearish'
    c3 = bool(m5r.get('abs_ask')) and obi5 < -0.08
    c4 = bool(m5r.get('big_trade_bearish')) and bool(m5r.get('cvd_div'))
    c5 = bool(m5r.get('displacement_bear'))
    c6 = bool(m5r.get('ote_rejection'))
    c7 = bool(m5r.get('cvd_div')) and stk == 'Bearish'
    if not any([c1, c2, c3, c4, c5, c6, c7]):
        continue
    a_setup += 1

    rng   = row['high'] - row['low']
    is_ss = False
    if rng > 0:
        wu = row['high'] - max(row['close'], row['open'])
        bd = abs(row['close'] - row['open'])
        is_ss = (wu/rng >= 0.45) and (bd/rng <= 0.35)
    obi = float(row.get('obi10_mean') or 0)
    dz  = float(row.get('dz') or 0)
    cs  = float(row.get('cvd_slope') or 0)
    t1  = bool(row.get('abs_ask')) and obi < -0.05
    t2  = dz < -0.5 and obi < -0.05
    t3  = is_ss and cs < 0
    t4  = bool(row.get('big_trade_bearish')) and cs < 0 and obi < 0
    if not any([t1, t2, t3, t4]):
        continue
    a_trig += 1

    h1d = h1_ctx.get((ts // H1_MS) * H1_MS)
    if h1d is None:
        continue
    h1h, h1a = h1d
    d   = h1h + 0.30*h1a - row['close']
    if d <= 0:
        continue
    sp  = d / row['close']
    if not (0.003 <= sp <= 0.0075):
        continue
    a_stop += 1

days = 366
print(f"Total barras M1:           {total:>8,}")
print(f"Despues sesion (7-20 UTC): {a_sess:>8,}  eliminadas: {total-a_sess:,}")
print(f"Despues D1 macro:          {a_d1:>8,}  eliminadas: {a_sess-a_d1:,}")
print(f"Despues regime:            {a_reg:>8,}  eliminadas: {a_d1-a_reg:,}")
print(f"Despues zona M15:          {a_zone:>8,}  eliminadas: {a_reg-a_zone:,}")
print(f"Despues setup M5:          {a_setup:>8,}  eliminadas: {a_zone-a_setup:,}")
print(f"Despues trigger M1:        {a_trig:>8,}  eliminadas: {a_setup-a_trig:,}")
print(f"Despues stop H1 valido:    {a_stop:>8,}  eliminadas: {a_trig-a_stop:,}")
print()
print(f"Señales brutas (sin cooldown): {a_stop:,} en {days} dias = {a_stop/days:.0f}/dia")
print(f"Con cooldown 30 barras:        ~{a_stop//30:,} trades reales = ~{a_stop//30/days:.1f}/dia")
