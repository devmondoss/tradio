import pandas as pd, numpy as np

trades = pd.read_csv('exports/basics_trades.csv')
m1 = pd.read_parquet('data/bybit-spot/processed/btcusdt_m1.parquet')
m1 = m1[['ts_ms','open','high','low','close','volume','obi10_mean','delta','cvd_slope','obi10_max','obi10_min']]

m = trades.merge(m1, on='ts_ms', how='left')
m['win'] = m['result_r'] > 0

# features de calidad de la barra de rechazo
m['rng']      = m['high'] - m['low']
m['wick_up']  = m['high'] - m[['close','open']].max(axis=1)
m['body']     = (m['open'] - m['close']).clip(lower=0)
m['wick_pct'] = m['wick_up'] / m['rng']          # % del rango que es mecha superior
m['body_pct'] = m['body'] / m['rng']              # % del rango que es cuerpo
m['obi']      = m['obi10_mean'].fillna(0)
m['delta']    = m['delta'].fillna(0)
m['obi_min']  = m['obi10_min'].fillna(0)          # OBI mas negativo de la barra

def bucket_analysis(col, bins, labels, df=m):
    df2 = df.dropna(subset=[col]).copy()
    df2['bucket'] = pd.cut(df2[col], bins=bins, labels=labels)
    print(f"\n--- {col} ---")
    print(f"  {'Bucket':<16}  {'n':>5}  {'WR':>6}  {'AvgR':>7}")
    for b in labels:
        sub = df2[df2['bucket']==b]
        if len(sub) < 10: continue
        wr = sub['win'].mean()*100
        avg = sub['result_r'].mean()
        print(f"  {b:<16}  {len(sub):>5}  {wr:>5.1f}%  {avg:>+.3f}")

bucket_analysis('wick_pct',
    [0.30, 0.40, 0.50, 0.60, 0.70, 0.85, 1.01],
    ['30-40%','40-50%','50-60%','60-70%','70-85%','85-100%'])

bucket_analysis('body_pct',
    [0, 0.05, 0.10, 0.20, 0.35, 0.60, 1.01],
    ['0-5%','5-10%','10-20%','20-35%','35-60%','60%+'])

bucket_analysis('obi',
    [-1.0, -0.30, -0.15, -0.05, 0.0, 0.5],
    ['<-0.30','-0.30--0.15','-0.15--0.05','-0.05-0','0+'])

bucket_analysis('obi_min',
    [-1.01, -0.70, -0.50, -0.30, -0.15, 0.0],
    ['<-0.70','-0.70--0.50','-0.50--0.30','-0.30--0.15','>-0.15'])

# delta direction
print("\n--- delta direction ---")
for label, mask in [('delta < 0 (vendedor)', m['delta']<0), ('delta >= 0 (comprador)', m['delta']>=0)]:
    sub = m[mask]
    if len(sub)<5: continue
    print(f"  {label:<28}  n={len(sub):>4}  WR={sub['win'].mean()*100:>5.1f}%  AvgR={sub['result_r'].mean():>+.3f}")
