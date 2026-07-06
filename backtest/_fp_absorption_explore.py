"""
_fp_absorption_explore.py
Exploración de la señal de absorción footprint como estrategia independiente.
Hipótesis: "esfuerzo vs resultado" (Wyckoff) — volumen alto pero precio no se mueve
en la dirección del esfuerzo = absorción de la contraparte.
"""
import pandas as pd
import numpy as np
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent

cols = ['ts_ms','open','high','low','close','volume','delta',
        'fp_absorb_buy','fp_absorb_sell','fp_stack_buy','fp_stack_sell',
        'fp_delta_top','fp_delta_bot','fp_sell_imb','fp_buy_imb',
        'abs_bid','abs_ask','vr','atr14','cvd_slope',
        'vp_poc','vp_vah','vp_val','prev_day_high','prev_day_low',
        'swing_high_50','swing_low_50','regime']

df = pd.read_parquet(ROOT / 'data/bybit-perp/processed/btcusdt_perp_m1.parquet', columns=cols)
df = df[df.ts_ms >= 1_750_291_200_000].reset_index(drop=True)

# ABSORCION REAL: esfuerzo vs resultado
vol_ma = df.volume.rolling(50, min_periods=10).mean()
df['vol_rel'] = df.volume / (vol_ma + 1e-9)
delta_std = df.delta.rolling(50, min_periods=10).std()

# Bull absorption: volumen alto + delta negativo PERO precio cerro arriba
df['bull_absorb'] = (
    (df.delta < 0) &
    (df.close >= df.open) &
    (df.vol_rel > 1.5)
)
df['bull_absorb_strong'] = (
    (df.delta < -delta_std) &
    (df.close > df.open) &
    (df.vol_rel > 2.0)
)

# Bear absorption: volumen alto + delta positivo PERO precio cerro abajo
df['bear_absorb'] = (
    (df.delta > 0) &
    (df.close <= df.open) &
    (df.vol_rel > 1.5)
)
df['bear_absorb_strong'] = (
    (df.delta > delta_std) &
    (df.close < df.open) &
    (df.vol_rel > 2.0)
)

# Near key level (0.3% tolerancia)
tol = 0.003
for lv in ['vp_poc','vp_vah','vp_val','prev_day_high','prev_day_low']:
    df[f'near_{lv}'] = df[lv].notna() & (abs(df.close - df[lv]) / df.close < tol)
df['near_any'] = df[['near_vp_poc','near_vp_vah','near_vp_val',
                      'near_prev_day_high','near_prev_day_low']].any(axis=1)

df['atr_med'] = df.atr14.rolling(500*15, min_periods=100).median()
df['vol_filter'] = df.atr14 > df.atr_med


def fwd_stats(mask, direction='long', n_fwd=15):
    fr = (df.close.shift(-n_fwd) - df.close) / df.atr14
    vals_raw = fr[mask].dropna()
    if len(vals_raw) < 20:
        return None
    vals = vals_raw if direction == 'long' else -vals_raw
    return len(vals), vals.mean(), (vals > 0).mean()


print('Absorcion real (esfuerzo vs resultado) — forward return 15 barras M1:')
print(f'{"Condicion":<45} {"n":>7}  {"avgATR":>7}  {"WR":>5}  dir')
print('-'*75)

tests = [
    ('bull_absorb (solo)',               df.bull_absorb,                                        'long'),
    ('bull_absorb + near nivel',         df.bull_absorb & df.near_any,                          'long'),
    ('bull_absorb + nivel + vol',        df.bull_absorb & df.near_any & df.vol_filter,          'long'),
    ('bull_absorb_strong',               df.bull_absorb_strong,                                 'long'),
    ('bull_absorb_strong + nivel',       df.bull_absorb_strong & df.near_any,                   'long'),
    ('bull_absorb_strong+nivel+vol',     df.bull_absorb_strong & df.near_any & df.vol_filter,   'long'),
    ('bear_absorb (solo)',               df.bear_absorb,                                        'short'),
    ('bear_absorb + near nivel',         df.bear_absorb & df.near_any,                          'short'),
    ('bear_absorb + nivel + vol',        df.bear_absorb & df.near_any & df.vol_filter,          'short'),
    ('bear_absorb_strong',               df.bear_absorb_strong,                                 'short'),
    ('bear_absorb_strong + nivel',       df.bear_absorb_strong & df.near_any,                   'short'),
    ('bear_absorb_strong+nivel+vol',     df.bear_absorb_strong & df.near_any & df.vol_filter,   'short'),
]

for lbl, mask, d in tests:
    r = fwd_stats(mask, d, 15)
    if r:
        n, avg, wr = r
        print(f'{lbl:<45} {n:>7,}  {avg:>+.3f}  {100*wr:.0f}%  {d}')

print()
print('Frecuencia:')
print(f'  bull_absorb:        {df.bull_absorb.sum():,} ({100*df.bull_absorb.mean():.1f}%)')
print(f'  bull_absorb_strong: {df.bull_absorb_strong.sum():,} ({100*df.bull_absorb_strong.mean():.1f}%)')
print(f'  bear_absorb:        {df.bear_absorb.sum():,} ({100*df.bear_absorb.mean():.1f}%)')
print(f'  bear_absorb_strong: {df.bear_absorb_strong.sum():,} ({100*df.bear_absorb_strong.mean():.1f}%)')

# Ventana temporal multiple
print()
print('Forward ATR por ventana (bull_absorb_strong + nivel + vol):')
mask_best = df.bull_absorb_strong & df.near_any & df.vol_filter
for fwd in [1, 5, 15, 30, 60, 120]:
    r = fwd_stats(mask_best, 'long', fwd)
    if r:
        n, avg, wr = r
        print(f'  fwd={fwd:>4} barras M1 ({fwd}min):  avg={avg:>+.3f}ATR  WR={100*wr:.0f}%  n={n:,}')

mask_bear = df.bear_absorb_strong & df.near_any & df.vol_filter
print()
print('Forward ATR por ventana (bear_absorb_strong + nivel + vol):')
for fwd in [1, 5, 15, 30, 60, 120]:
    r = fwd_stats(mask_bear, 'short', fwd)
    if r:
        n, avg, wr = r
        print(f'  fwd={fwd:>4} barras M1 ({fwd}min):  avg={avg:>+.3f}ATR  WR={100*wr:.0f}%  n={n:,}')
