import pandas as pd
import numpy as np

df = pd.read_parquet('data/bybit-spot/processed/btcusdt_m1.parquet').sort_values('ts_ms').reset_index(drop=True)
n = len(df)

print('=== Cobertura de nuevos features ===')
for col in ['fib_ote_london', 'ote_rejection', 'body_below_vwap', 'body_below_poc']:
    if col in df.columns:
        v = df[col].astype(bool)
        pct = v.sum() / n * 100
        print(f'  {col:<25}: {v.sum():>7,} verdaderos ({pct:.1f}%)')
    else:
        print(f'  {col}: NO EXISTE')

for col in ['london_sweep_h', 'ote_62', 'ote_79']:
    if col in df.columns:
        v = df[col]
        valid = v.notna().sum()
        print(f'  {col:<25}: {valid:>7,} validos ({valid/n*100:.1f}%),  mean={v.mean():.2f}')
    else:
        print(f'  {col}: NO EXISTE')

print()
print('=== Sweep London > Asian High ===')
lon_h  = df['london_sweep_h'].values
asia_h = df['asian_high'].values
swept  = (lon_h > asia_h)
print(f'  Barras con sweep:    {swept.sum():,} ({swept.sum()/n*100:.1f}%)')
print(f'  london_sweep_h NaN:  {np.isnan(lon_h).sum():,}')
print(f'  asian_high NaN:      {np.isnan(asia_h).sum():,}')

# Chequeo de valores por hora del dia
print()
print('=== fib_ote_london por sesion ===')
ote = df['fib_ote_london'].astype(bool)
for sess in ['Asia', 'London', 'Overlap', 'NewYork', 'OffHours']:
    m = (df['session'] == sess) & ote
    print(f'  {sess:<10}: {m.sum():,}')

print()
print('=== Muestra de barras con fib_ote_london=True ===')
sample = df[ote].head(5)[['ts_ms','session','close','asian_low','asian_high',
                            'london_sweep_h','ote_62','ote_79','fib_ote_london']]
for _, row in sample.iterrows():
    ts = pd.Timestamp(row['ts_ms'], unit='ms', tz='UTC')
    print(f'  {ts}  sess={row["session"]}  close={row["close"]:.2f}  '
          f'ote_79={row["ote_79"]:.2f}  ote_62={row["ote_62"]:.2f}  '
          f'sweep_h={row["london_sweep_h"]:.2f}  asian_l={row["asian_low"]:.2f}')
