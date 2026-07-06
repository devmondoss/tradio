"""
_sweep_explore.py — Stop Sweep Fade (ICT / Smart Money)
Hipotesis: precio barre igual_high/swing_high (trigea stops de retail),
cierra de vuelta al rango en 1-3 barras -> fade de la trampa.
"""
import pandas as pd
import numpy as np
from pathlib import Path

ROOT = Path(__file__).parent.parent

cols = ['ts_ms','open','high','low','close','volume','delta',
        'equal_high','equal_low','sweep_confirmed','pdh_sweep','equal_high_sweep',
        'swing_high_50','swing_low_50','atr14','vr','regime','cvd_slope',
        'vp_poc','vp_vah','vp_val','prev_day_high','prev_day_low']

df = pd.read_parquet(ROOT / 'data/bybit-perp/processed/btcusdt_perp_m1.parquet', columns=cols)
df = df[df.ts_ms >= 1_750_291_200_000].reset_index(drop=True)
print(f'Barras M1: {len(df):,}  ({pd.to_datetime(df.ts_ms.min(),unit="ms").date()} -> {pd.to_datetime(df.ts_ms.max(),unit="ms").date()})')

# ── Columnas sweep existentes ────────────────────────────────────────────────
print('\n--- Columnas sweep en el dataset ---')
for c in ['equal_high','equal_low','sweep_confirmed','pdh_sweep','equal_high_sweep']:
    print(f'{c:<22} True={df[c].sum():,} ({100*df[c].mean():.1f}%)')

# ── Construir sweep desde cero (mas control) ─────────────────────────────────
# Swing high en ventana 20 barras
SWING_WIN = 20
df['sh20'] = df.high.rolling(SWING_WIN, center=True).max()
df['sl20'] = df.low.rolling(SWING_WIN, center=True).min()
df['is_swing_high'] = (df.high == df.sh20) & (df.high.shift(1) < df.high) & (df.high.shift(-1) < df.high)
df['is_swing_low']  = (df.low  == df.sl20) & (df.low.shift(1)  > df.low)  & (df.low.shift(-1)  > df.low)

# Ultimo swing high/low antes de la barra actual
df['last_sh'] = df.high.where(df.is_swing_high).ffill()
df['last_sl'] = df.low.where(df.is_swing_low).ffill()

# SWEEP BEARISH: precio barre swing high (wick sube por encima) pero CIERRA debajo
#   = los stops de longs fueron tocados, pero el mercado no sostuvo -> trampa
df['sweep_sh'] = (
    (df.high > df.last_sh) &         # wick supera el swing high previo
    (df.close < df.last_sh) &        # cierra de vuelta debajo
    (df.last_sh > 0)
)

# SWEEP BULLISH: precio barre swing low pero cierra arriba
df['sweep_sl'] = (
    (df.low < df.last_sl) &
    (df.close > df.last_sl) &
    (df.last_sl > 0)
)

# Version con equal_high del dataset
df['sweep_eq_high'] = (
    df.equal_high_sweep |             # ya calculado en el dataset
    (df.equal_high.shift(1) & (df.high > df.close.shift(1)) & (df.close < df.high))
)

# ── ATR filter ───────────────────────────────────────────────────────────────
df['atr_med'] = df.atr14.rolling(500*15, min_periods=100).median()
df['vol_ok']  = df.atr14 > df.atr_med

# ── Forward returns ──────────────────────────────────────────────────────────
def fwd_stats(mask, direction, n_fwd):
    fr = (df.close.shift(-n_fwd) - df.close) / df.atr14
    vals_raw = fr[mask].dropna()
    if len(vals_raw) < 10:
        return None
    vals = vals_raw if direction == 'long' else -vals_raw
    return len(vals), vals.mean(), (vals > 0).mean()

print('\n--- Forward return en multiples ventanas (direction=short para bear sweep) ---')
print(f'{"Condicion":<45} {"n":>6}  {"1m":>6}  {"5m":>6}  {"15m":>6}  {"30m":>6}  {"60m":>6}')
print('-'*85)

tests = [
    # Bear sweeps (short)
    ('sweep_sh (bear, solo)',              df.sweep_sh,                          'short'),
    ('sweep_sh + vol_ok',                  df.sweep_sh & df.vol_ok,              'short'),
    ('sweep_sh + vol_ok + vr>1',           df.sweep_sh & df.vol_ok & (df.vr>1), 'short'),
    ('equal_high_sweep (dataset)',         df.equal_high_sweep,                  'short'),
    ('equal_high_sweep + vol_ok',          df.equal_high_sweep & df.vol_ok,      'short'),
    ('sweep_confirmed (dataset)',          df.sweep_confirmed,                   'short'),
    # Bull sweeps (long)
    ('sweep_sl (bull, solo)',              df.sweep_sl,                          'long'),
    ('sweep_sl + vol_ok',                  df.sweep_sl & df.vol_ok,              'long'),
    ('sweep_sl + vol_ok + vr>1',           df.sweep_sl & df.vol_ok & (df.vr>1), 'long'),
]

for lbl, mask, d in tests:
    row = [lbl, mask.sum()]
    for fwd in [1, 5, 15, 30, 60]:
        r = fwd_stats(mask, d, fwd)
        row.append(f'{r[1]:+.2f}' if r else '  --  ')
    n = mask.sum()
    if n < 5:
        print(f'{lbl:<45} {n:>6}  (muy pocos)')
        continue
    avgs = []
    for fwd in [1, 5, 15, 30, 60]:
        r = fwd_stats(mask, d, fwd)
        avgs.append(f'{r[1]:>+.3f}' if r else '   -- ')
    print(f'{lbl:<45} {n:>6,}  {" ".join(avgs)}')

# ── Inspeccion de ejemplos bear sweep ────────────────────────────────────────
print('\n--- Ejemplos sweep_sh ---')
ex = df[df.sweep_sh & df.vol_ok].head(10)[['ts_ms','open','high','low','close','last_sh','atr14','vr']]
ex['ts'] = pd.to_datetime(ex.ts_ms, unit='ms')
ex['sweep_pct'] = (ex.high - ex.last_sh) / ex.last_sh * 100
print(ex[['ts','open','high','low','close','last_sh','sweep_pct','vr']].to_string(index=False))

# ── Desglose mensual del mejor ────────────────────────────────────────────────
print('\n--- Desglose mensual sweep_sh + vol_ok (short, fwd=15m) ---')
mask_best = df.sweep_sh & df.vol_ok
df2 = df[mask_best].copy()
df2['fwd15'] = (df.close.shift(-15) - df.close)[mask_best].values
df2['month'] = pd.to_datetime(df2.ts_ms, unit='ms').dt.to_period('M')
for m, g in df2.groupby('month'):
    fr = -g.fwd15.dropna() / df2.atr14[g.index].values[:len(g.fwd15.dropna())]
    if len(fr) < 3: continue
    print(f'  {m}  n={len(fr):>4}  avg={fr.mean():>+.3f}ATR  WR={100*(fr>0).mean():.0f}%')
