import pandas as pd, numpy as np

df = pd.read_csv('exports/basics_trades.csv')
m1 = pd.read_parquet('data/bybit-spot/processed/btcusdt_m1.parquet')[['ts_ms','high','vp_vah','prev_day_high','asian_high']]
m = df.merge(m1, on='ts_ms', how='left')

LEVEL_TOL = 0.004

def best_level(row):
    for col in ['vp_vah', 'prev_day_high', 'asian_high']:
        v = row.get(col)
        if v and v > 0 and abs(row['high'] - v) / v <= LEVEL_TOL:
            return float(v)
    return None

m['level_px'] = m.apply(best_level, axis=1)
m['entry_dist_pct'] = (m['level_px'] - m['entry']) / m['level_px'] * 100

m2 = m.dropna(subset=['entry_dist_pct'])
m2 = m2[m2['entry_dist_pct'] >= 0].copy()

bins   = [0, 0.10, 0.20, 0.30, 0.40, 0.60, 2.0]
labels = ['0-0.10%', '0.10-0.20%', '0.20-0.30%', '0.30-0.40%', '0.40-0.60%', '0.60%+']
m2['bucket'] = pd.cut(m2['entry_dist_pct'], bins=bins, labels=labels)

print(f"{'Dist entry vs nivel':<14}  {'n':>5}  {'WR':>6}  {'AvgR':>7}  {'MFE_avg':>8}")
print('-'*50)
for b in labels:
    sub = m2[m2['bucket'] == b]
    if len(sub) < 5:
        continue
    wr  = (sub['result_r'] > 0).mean() * 100
    avg = sub['result_r'].mean()
    mfe = sub['mfe_r'].mean() if 'mfe_r' in sub.columns else float('nan')
    print(f"  {b:<14}  {len(sub):>5}  {wr:>5.1f}%  {avg:>+.3f}   {mfe:>7.2f}R")

print(f"\nTotal con nivel identificado: {len(m2)} de {len(df)}")
print(f"Dist promedio entry vs nivel: {m2['entry_dist_pct'].mean():.3f}%")
print(f"Dist mediana:                 {m2['entry_dist_pct'].median():.3f}%")
