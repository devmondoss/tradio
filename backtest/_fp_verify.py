import pyarrow.parquet as pq, pandas as pd, numpy as np

df = pq.read_table('data/bybit-perp/processed/btcusdt_perp_m15_footprint.parquet').to_pandas()
print('Shape:', df.shape)
print(df[['bar_ts','poc','delta','vol','imb_ratio','n_trades']].describe().round(2))

# Barra con delta mas extremo
row = df.loc[df.delta.abs().idxmax()]
ts  = pd.Timestamp(int(row.bar_ts), unit='ms', tz='UTC')
print(f'\nBarra mas intensa (delta={row.delta:.1f}):')
print(f'  {ts}  poc={row.poc}  vol={row.vol:.1f}  imb={row.imb_ratio:.3f}  n={row.n_trades}')
arr = sorted(zip(row.prices, row.buy, row.sell), key=lambda x: x[1]+x[2], reverse=True)[:5]
print('  Top bins:  price     buy    sell   delta')
for px, b, s in arr:
    print(f'             {px:>8.0f}  {b:>7.2f}  {s:>7.2f}  {b-s:>+7.2f}')
