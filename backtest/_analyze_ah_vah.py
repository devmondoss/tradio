import pandas as pd, numpy as np

trades = pd.read_csv('exports/basics_trades.csv')
m1 = pd.read_parquet('data/bybit-spot/processed/btcusdt_m1.parquet')
cols = ['ts_ms','open','high','low','close','obi10_mean','obi10_min','delta','cvd_slope',
        'stacked_imb','regime','sweep_confirmed','abs_ask','abs_bid','cvd_div',
        'big_trade_bearish','thin_below','bid_wall','ask_wall','vp_vah','asian_high']
m1 = m1[[c for c in cols if c in m1.columns]]
m = trades.merge(m1, on='ts_ms', how='left')
m['win'] = m['result_r'] > 0
m['rng'] = m['high'] - m['low']
m['wick_pct'] = (m['high'] - m[['close','open']].max(axis=1)) / m['rng']
m['hour'] = pd.to_datetime(m['ts_ms'], unit='ms', utc=True).dt.hour

ah = m[m['level'] == 'AH+VAH'].copy()
rest = m[m['level'] != 'AH+VAH'].copy()

print(f"=== AH+VAH vs resto ===")
print(f"AH+VAH:  n={len(ah):>4}  WR={ah['win'].mean()*100:.1f}%  AvgR={ah['result_r'].mean():+.3f}")
print(f"Resto:   n={len(rest):>4}  WR={rest['win'].mean()*100:.1f}%  AvgR={rest['result_r'].mean():+.3f}")

print(f"\n=== Por sesion (AH+VAH) ===")
for sess in ['overlap','ny']:
    sub = ah[ah['session']==sess]
    if len(sub)<5: continue
    print(f"  {sess:<10}  n={len(sub):>3}  WR={sub['win'].mean()*100:.1f}%  AvgR={sub['result_r'].mean():+.3f}")

print(f"\n=== Por hora UTC (AH+VAH) ===")
for h in range(12, 20):
    sub = ah[ah['hour']==h]
    if len(sub)<5: continue
    wr = sub['win'].mean()*100
    avg = sub['result_r'].mean()
    flag = ' <<' if wr < 40 else (' **' if wr > 50 else '')
    print(f"  {h:02d}h UTC  n={len(sub):>3}  WR={wr:>5.1f}%  AvgR={avg:>+.3f}{flag}")

print(f"\n=== IS vs OOS (AH+VAH) ===")
print(f"  IS:  n={len(ah[~ah['oos']]):>3}  WR={ah[~ah['oos']]['win'].mean()*100:.1f}%  AvgR={ah[~ah['oos']]['result_r'].mean():+.3f}")
print(f"  OOS: n={len(ah[ah['oos']]):>3}  WR={ah[ah['oos']]['win'].mean()*100:.1f}%  AvgR={ah[ah['oos']]['result_r'].mean():+.3f}")

print(f"\n=== OBI en AH+VAH ===")
obi = ah['obi10_mean'].fillna(0)
bins = [-1,-0.30,-0.15,-0.05,0,1]; lbls = ['<-0.30','-0.30--0.15','-0.15--0.05','-0.05-0','0+']
ah2 = ah.copy(); ah2['ob'] = pd.cut(obi, bins=bins, labels=lbls)
for b in lbls:
    sub = ah2[ah2['ob']==b]
    if len(sub)<5: continue
    print(f"  OBI {b:<14}  n={len(sub):>3}  WR={sub['win'].mean()*100:.1f}%  AvgR={sub['result_r'].mean():+.3f}")

print(f"\n=== Wick en AH+VAH ===")
bins2=[0.30,0.50,0.70,0.85]; lbls2=['30-50%','50-70%','70-85%']
ah2['wb']=pd.cut(ah['wick_pct'],bins=bins2,labels=lbls2)
for b in lbls2:
    sub = ah2[ah2['wb']==b]
    if len(sub)<5: continue
    print(f"  wick {b}  n={len(sub):>3}  WR={sub['win'].mean()*100:.1f}%  AvgR={sub['result_r'].mean():+.3f}")

print(f"\n=== Features booleanos en AH+VAH ===")
for f in ['sweep_confirmed','abs_ask','abs_bid','cvd_div','big_trade_bearish','thin_below','bid_wall']:
    if f not in ah.columns: continue
    t = ah[ah[f]==True]; ff = ah[ah[f]!=True]
    if len(t)<5 or len(ff)<5: continue
    print(f"  {f:<22}  CON n={len(t):>3} WR={t['win'].mean()*100:.0f}% AvgR={t['result_r'].mean():+.3f}  |  SIN n={len(ff):>3} WR={ff['win'].mean()*100:.0f}% AvgR={ff['result_r'].mean():+.3f}")

print(f"\n=== Regime en AH+VAH ===")
for reg in ah['reason'].value_counts().index if 'regime' not in ah.columns else ah.get('regime','').value_counts().index:
    pass
if 'regime' in m1.columns:
    m2 = trades.merge(m1[['ts_ms','regime']], on='ts_ms', how='left')
    m2['win'] = m2['result_r']>0
    ah_r = m2[m2['level']=='AH+VAH']
    for reg in ['TrendUp','Expansion','Chop','TrendDown']:
        sub = ah_r[ah_r['regime']==reg]
        if len(sub)<5: continue
        print(f"  {reg:<12}  n={len(sub):>3}  WR={sub['win'].mean()*100:.1f}%  AvgR={sub['result_r'].mean():+.3f}")
