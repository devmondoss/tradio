"""
_analyze_microstructure.py
¿Qué features de microestructura predicen wins vs losses?
Sobre los trades actuales de basics_trades.csv.
"""
import pandas as pd, numpy as np

trades = pd.read_csv('exports/basics_trades.csv')
m1 = pd.read_parquet('data/bybit-spot/processed/btcusdt_m1.parquet')

MICRO_COLS = [
    'obi5_mean','obi10_mean','obi20_mean','obi10_min','obi10_max',
    'delta','cvd_slope','stacked_imb','abs_ask','abs_bid',
    'cvd_div','cvd_consec_neg','cvd_consec_pos','prev_bar_delta',
    'big_trade_bearish','big_trade_bullish','vpin',
    'thin_above','thin_below','ask_wall','bid_wall',
    'regime','sweep_confirmed','spread_mean',
    'near5_ask','near5_bid','obi_range',
    'vp_poc','vp_val','vp_lvn_below',
]
cols = ['ts_ms'] + [c for c in MICRO_COLS if c in m1.columns]
m = trades.merge(m1[cols], on='ts_ms', how='left')
m['win'] = m['result_r'] > 0
oot = m[m['oos'] == True].copy()

print(f"Trades OOS: {len(oot)}  WR base: {oot['win'].mean()*100:.1f}%\n")

# ── Features booleanos ────────────────────────────────────────────────────────
BOOL_FEATS = ['stacked_imb','abs_ask','abs_bid','cvd_div','sweep_confirmed',
              'big_trade_bearish','big_trade_bullish','thin_above','thin_below',
              'ask_wall','bid_wall']

print(f"{'Feature':<22}  {'n_SI':>5}  {'WR_SI':>6}  {'AvgR_SI':>8}  {'n_NO':>5}  {'WR_NO':>6}  {'AvgR_NO':>8}  {'delta_WR':>9}")
print('-'*90)
results = []
for f in BOOL_FEATS:
    if f not in oot.columns: continue
    yes = oot[oot[f] == True]
    no  = oot[oot[f] != True]
    if len(yes) < 10 or len(no) < 10: continue
    wr_y = yes['win'].mean()*100; ar_y = yes['result_r'].mean()
    wr_n = no['win'].mean()*100;  ar_n = no['result_r'].mean()
    delta = wr_y - wr_n
    results.append((f, len(yes), wr_y, ar_y, len(no), wr_n, ar_n, delta))

results.sort(key=lambda x: -abs(x[7]))
for f, ny, wy, ay, nn, wn, an, d in results:
    arrow = '>>' if d > 5 else ('<<' if d < -5 else '  ')
    print(f"{f:<22}  {ny:>5}  {wy:>5.1f}%  {ay:>+.3f}     {nn:>5}  {wn:>5.1f}%  {an:>+.3f}     {d:>+.1f}pp {arrow}")

# ── Regime ────────────────────────────────────────────────────────────────────
print(f"\n{'Regime':<14}  {'n':>4}  {'WR':>6}  {'AvgR':>8}")
print('-'*38)
if 'regime' in oot.columns:
    for reg, grp in oot.groupby('regime'):
        if len(grp) < 10: continue
        print(f"  {str(reg):<12}  {len(grp):>4}  {grp['win'].mean()*100:>5.1f}%  {grp['result_r'].mean():>+.3f}")

# ── Features continuos — bucketeado ──────────────────────────────────────────
print(f"\n=== OBI10 min dentro de la barra ===")
if 'obi10_min' in oot.columns:
    bins = [-1,-0.40,-0.25,-0.10,0,1]; lbls=['<-0.40','-0.40/-0.25','-0.25/-0.10','-0.10/0','0+']
    oot2 = oot.copy(); oot2['ob'] = pd.cut(oot['obi10_min'].fillna(0), bins=bins, labels=lbls)
    for b in lbls:
        sub = oot2[oot2['ob']==b]
        if len(sub)<10: continue
        print(f"  obi10_min {b:<14}  n={len(sub):>3}  WR={sub['win'].mean()*100:>5.1f}%  AvgR={sub['result_r'].mean():>+.3f}")

print(f"\n=== CVD slope en barra de entrada ===")
if 'cvd_slope' in oot.columns:
    bins=[-5,-0.5,-0.1,0,0.1,5]; lbls=['<-0.5','-0.5/-0.1','-0.1/0','0/0.1','>0.1']
    oot2['cs'] = pd.cut(oot['cvd_slope'].fillna(0), bins=bins, labels=lbls)
    for b in lbls:
        sub = oot2[oot2['cs']==b]
        if len(sub)<10: continue
        print(f"  cvd_slope {b:<12}  n={len(sub):>3}  WR={sub['win'].mean()*100:>5.1f}%  AvgR={sub['result_r'].mean():>+.3f}")

print(f"\n=== CVD consecutivas negativas antes de entrada ===")
if 'cvd_consec_neg' in oot.columns:
    for v in [0,1,2,3,4,5]:
        sub = oot[oot['cvd_consec_neg'] >= v]
        if len(sub)<10: continue
        print(f"  cvd_consec_neg >= {v}  n={len(sub):>3}  WR={sub['win'].mean()*100:>5.1f}%  AvgR={sub['result_r'].mean():>+.3f}")

print(f"\n=== VPIN ===")
if 'vpin' in oot.columns:
    bins=[0,0.3,0.5,0.7,1.0]; lbls=['0-0.3','0.3-0.5','0.5-0.7','0.7-1.0']
    oot2['vp'] = pd.cut(oot['vpin'].fillna(0), bins=bins, labels=lbls)
    for b in lbls:
        sub = oot2[oot2['vp']==b]
        if len(sub)<10: continue
        print(f"  vpin {b:<10}  n={len(sub):>3}  WR={sub['win'].mean()*100:>5.1f}%  AvgR={sub['result_r'].mean():>+.3f}")

print(f"\n=== OBI range (max-min en la barra) ===")
if 'obi_range' in oot.columns:
    med = oot['obi_range'].median()
    hi = oot[oot['obi_range'] > med]; lo = oot[oot['obi_range'] <= med]
    print(f"  obi_range > {med:.2f} (alta volatilidad OB)  n={len(hi):>3}  WR={hi['win'].mean()*100:>5.1f}%  AvgR={hi['result_r'].mean():>+.3f}")
    print(f"  obi_range <= {med:.2f} (baja volatilidad OB)  n={len(lo):>3}  WR={lo['win'].mean()*100:>5.1f}%  AvgR={lo['result_r'].mean():>+.3f}")

print(f"\n=== thin_below: liquidez delgada bajo el entry (target mas lejos) ===")
if 'thin_below' in oot.columns and 'vp_lvn_below' in oot.columns:
    tb = oot[oot['thin_below']==True]; lvn = oot[oot['vp_lvn_below']>0]
    if len(tb)>5: print(f"  thin_below=True   n={len(tb):>3}  WR={tb['win'].mean()*100:>5.1f}%  AvgR={tb['result_r'].mean():>+.3f}  MFE={tb['mfe_r'].mean():.2f}R")
    if len(lvn)>5: print(f"  vp_lvn_below>0    n={len(lvn):>3}  WR={lvn['win'].mean()*100:>5.1f}%  AvgR={lvn['result_r'].mean():>+.3f}  MFE={lvn['mfe_r'].mean():.2f}R")
    base_mfe = oot['mfe_r'].mean()
    print(f"  BASE mfe_r        n={len(oot):>3}  WR={oot['win'].mean()*100:>5.1f}%  MFE={base_mfe:.2f}R  (si MFE alto = target corto)")

print(f"\n=== near5_ask (depth en ask lado, proxy de resistencia encima) ===")
if 'near5_ask' in oot.columns:
    med = oot['near5_ask'].median()
    hi = oot[oot['near5_ask'] > med]; lo = oot[oot['near5_ask'] <= med]
    print(f"  near5_ask alto (>{med:.1f})  n={len(hi):>3}  WR={hi['win'].mean()*100:>5.1f}%  AvgR={hi['result_r'].mean():>+.3f}")
    print(f"  near5_ask bajo (<={med:.1f})  n={len(lo):>3}  WR={lo['win'].mean()*100:>5.1f}%  AvgR={lo['result_r'].mean():>+.3f}")
