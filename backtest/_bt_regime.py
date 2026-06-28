"""
_bt_regime.py — ¿existe un detector de régimen mejor que la columna 'regime'? (ataca el gap de SOL)
==================================================================================================
El backtest rutea con la columna 'regime' del dataset; el binario Rust usa |precio-sma50|>0.6*atr.
Barro el umbral de ese detector (chop_mask on-the-fly) con la config YA optimizada (stop 0.8, trail 6),
regla dura. Si un umbral mejora OOS en LOS 3 -> detector real mejor. Si solo SOL -> overfit, rechazar.
"""
import sys; from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, "backtest")
import _listas2 as L2
from _strategy_ab import run_system, is_chop
from _audit_mirror import gen_h21_short

PARQ = {"BTCUSDT": "data/bybit-perp/processed/btcusdt_perp_m1.parquet",
        "ETHUSDT": "E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet",
        "SOLUSDT": "E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"}
TF=15
OPT=dict(stop_scale=0.8, trail_atr=6.0, atr_mult=1.0)   # config ya optimizada

def metr(df):
    o=df[df.oos]; i=df[~df.oos]
    cap=500.0;peak=500.0;dd=0.0
    for r in o.sort_values("ts").r.values: cap+=5*r;peak=max(peak,cap);dd=max(dd,(peak-cap)/peak)
    return dict(isA=i.r.mean() if len(i) else 0, oosA=o.r.mean() if len(o) else 0,
                gap=(i.r.mean()-o.r.mean()) if len(i) and len(o) else 0, dd=100*dd,
                wr=100*(o.r>0).mean() if len(o) else 0, n=len(df))

DATA={}
for sym in ["BTCUSDT","ETHUSDT","SOLUSDT"]:
    if not Path(PARQ[sym]).exists(): continue
    L2.M1=PARQ[sym]; a=L2.A2(L2.load2(TF,start_ms=L2.TICK_MS)); m1=L2.load_m1_exit(start_ms=L2.TICK_MS)
    # sma50 y distancia normalizada por atr (causal: sma de cierres hasta i-1)
    c=a.c.astype(float); sma=pd.Series(c).rolling(50).mean().shift(1).values
    dist=np.abs(c-sma)/np.where(a.atr>0,a.atr,np.nan)
    DATA[sym]=(a,m1,[L2.gen_h5(),L2.gen_h21(),gen_h21_short()],dist)
    print("cargado",sym,file=sys.stderr)

def run(sym, chop_mask=None):
    a,m1,gens,_=DATA[sym]
    return metr(run_system(a,gens,m1,TF,mode="routed",chop_mask=chop_mask,**OPT))

print("="*92)
print("DETECTOR DE RÉGIMEN — base (columna 'regime') vs |precio-sma50|>thr*atr  [config optim]")
print("="*92)
print(f"  {'detector':<22}{'BTC OOS':>10}{'ETH OOS':>10}{'SOL OOS':>10}{'SOL gap':>9}{'SOL DD':>8}")
# base: columna regime del dataset
base={sym:run(sym,None) for sym in DATA}
print(f"  {'regime (dataset)':<22}"+"".join(f"{base[s]['oosA']:>+10.3f}" for s in ['BTCUSDT','ETHUSDT','SOLUSDT'])
      +f"{base['SOLUSDT']['gap']:>+9.3f}{base['SOLUSDT']['dd']:>7.1f}%")

for thr in [0.4,0.6,0.8,1.0,1.2]:
    res={}
    for sym in DATA:
        a,m1,gens,dist=DATA[sym]
        chop = dist <= thr     # chop si cerca de la media; trend si lejos
        res[sym]=run(sym,chop)
    okA = all(res[s]['oosA']>base[s]['oosA'] for s in res)
    tag = "✅ los 3" if okA else ("~SOL solo" if res['SOLUSDT']['oosA']>base['SOLUSDT']['oosA'] else "")
    print(f"  sma_dist<={thr:<14}"+"".join(f"{res[s]['oosA']:>+10.3f}" for s in ['BTCUSDT','ETHUSDT','SOLUSDT'])
          +f"{res['SOLUSDT']['gap']:>+9.3f}{res['SOLUSDT']['dd']:>7.1f}%  {tag}")

print("\n  base ref (config optim, ruteo dataset): "+" ".join(f"{s[:3]} {base[s]['oosA']:+.2f}(gap{base[s]['gap']:+.2f})" for s in base))
print("  Regla: un umbral solo se adopta si mejora OOS en los 3 (no solo SOL).")
