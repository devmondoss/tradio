import pandas as pd, numpy as np

trades = pd.read_csv('exports/basics_trades.csv')
m1 = pd.read_parquet('data/bybit-spot/processed/btcusdt_m1.parquet')
cols = ['ts_ms','open','high','low','close','volume','obi10_mean','obi10_min','obi10_max',
        'delta','cvd_slope','stacked_imb','regime','vr','dz','sweep_confirmed',
        'abs_ask','abs_bid','cvd_div','big_trade_bearish','vp_poc','vp_vah','vp_val',
        'weekly_high','asian_high','prev_day_high','thin_below','bid_wall','ask_wall']
m1 = m1[[c for c in cols if c in m1.columns]]
m = trades.merge(m1, on='ts_ms', how='left')

m['win'] = m['result_r'] > 0
m['rng'] = m['high'] - m['low']
m['wick_pct'] = (m['high'] - m[['close','open']].max(axis=1)) / m['rng']

wh = m[m['level'].str.contains('WH', na=False)].copy()
base = m[~m['level'].str.contains('WH', na=False)].copy()

print(f"=== WH+VAH vs resto ===")
print(f"WH niveles:  n={len(wh):>4}  WR={wh['win'].mean()*100:.1f}%  AvgR={wh['result_r'].mean():+.3f}")
print(f"Sin WH:      n={len(base):>4}  WR={base['win'].mean()*100:.1f}%  AvgR={base['result_r'].mean():+.3f}")

print(f"\n=== Por sesion (trades con WH) ===")
for sess in ['overlap','ny']:
    sub = wh[wh['session']==sess]
    if len(sub)<3: continue
    print(f"  {sess:<10}  n={len(sub):>3}  WR={sub['win'].mean()*100:.1f}%  AvgR={sub['result_r'].mean():+.3f}")

print(f"\n=== Hora UTC (trades con WH) ===")
wh['hour'] = pd.to_datetime(wh['ts_ms'], unit='ms', utc=True).dt.hour
for h in sorted(wh['hour'].unique()):
    sub = wh[wh['hour']==h]
    if len(sub)<3: continue
    print(f"  {h:02d}:00 UTC  n={len(sub):>3}  WR={sub['win'].mean()*100:.1f}%  AvgR={sub['result_r'].mean():+.3f}")

print(f"\n=== Wick size en WH vs sin WH ===")
for label, df_ in [('WH trades', wh), ('Sin WH', base)]:
    bins = [0.30, 0.50, 0.70, 0.85]
    lbls = ['30-50%','50-70%','70-85%']
    df_2 = df_.copy(); df_2['wb'] = pd.cut(df_2['wick_pct'], bins=bins, labels=lbls)
    print(f"  {label}:")
    for b in lbls:
        sub = df_2[df_2['wb']==b]
        if len(sub)<3: continue
        print(f"    wick {b}  n={len(sub):>3}  WR={sub['win'].mean()*100:.1f}%  AvgR={sub['result_r'].mean():+.3f}")

print(f"\n=== OBI en WH vs sin WH ===")
for label, df_ in [('WH trades', wh), ('Sin WH', base)]:
    obi = df_['obi10_mean'].fillna(0)
    bins = [-1,-0.30,-0.05,0,1]; lbls = ['<-0.30','-0.30--0.05','-0.05-0','0+']
    df_2 = df_.copy(); df_2['ob'] = pd.cut(obi, bins=bins, labels=lbls)
    print(f"  {label}:")
    for b in lbls:
        sub = df_2[df_2['ob']==b]
        if len(sub)<3: continue
        print(f"    OBI {b:<14}  n={len(sub):>3}  WR={sub['win'].mean()*100:.1f}%  AvgR={sub['result_r'].mean():+.3f}")

print(f"\n=== Features booleanos en WH (presencia vs resultado) ===")
bool_feats = ['sweep_confirmed','abs_ask','abs_bid','cvd_div','big_trade_bearish','thin_below']
for f in bool_feats:
    if f not in wh.columns: continue
    sub_t = wh[wh[f]==True]
    sub_f = wh[wh[f]!=True]
    if len(sub_t)<3 or len(sub_f)<3: continue
    print(f"  {f:<22}  CON: n={len(sub_t):>2} WR={sub_t['win'].mean()*100:.0f}% AvgR={sub_t['result_r'].mean():+.3f}  |  SIN: n={len(sub_f):>2} WR={sub_f['win'].mean()*100:.0f}% AvgR={sub_f['result_r'].mean():+.3f}")

print(f"\n=== IS vs OOS en WH ===")
wh_is  = wh[~wh['oos']]
wh_oos = wh[wh['oos']]
print(f"  IS:  n={len(wh_is):>3}  WR={wh_is['win'].mean()*100:.1f}%  AvgR={wh_is['result_r'].mean():+.3f}")
print(f"  OOS: n={len(wh_oos):>3}  WR={wh_oos['win'].mean()*100:.1f}%  AvgR={wh_oos['result_r'].mean():+.3f}")
