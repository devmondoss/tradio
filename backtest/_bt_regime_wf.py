"""
_bt_regime_wf.py — walk-forward de ema5/st2 (M15): ¿estable por cuartos en los 3 activos?
=========================================================================================
Si el edge está concentrado en un periodo, no es robusto. Divido TODO el periodo en 6 bloques
cronológicos y reporto avgR/n por bloque, para ema5/st2 vs el binario actual (sma50) vs M1.
Config optim (stop 0.8, trail 6). OOS marcado aparte.
"""
import sys; from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, "backtest")
import _listas2 as L2
from _strategy_ab import run_system
from _audit_mirror import gen_h21_short

PARQ = {"BTCUSDT": "data/bybit-perp/processed/btcusdt_perp_m1.parquet",
        "ETHUSDT": "E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet",
        "SOLUSDT": "E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"}
TF=15; SYMS=["BTCUSDT","ETHUSDT","SOLUSDT"]
OPT=dict(stop_scale=0.8, trail_atr=6.0, atr_mult=1.0)

def regime_mask(a, ema_p=5, streak=2, exp_thr=1.30):
    c=a.c.astype(float); atr=a.atr.astype(float)
    ema=pd.Series(c).ewm(span=ema_p, adjust=False).mean().values
    atr_ma=pd.Series(atr).rolling(20, min_periods=5).mean().values
    expansion=(atr_ma>0)&(atr/np.where(atr_ma>0,atr_ma,np.nan)>exp_thr)
    bull=c>ema*1.002; bear=c<ema*0.998
    n=len(c); bs=np.zeros(n,int); rs=np.zeros(n,int)
    for i in range(1,n):
        bs[i]=bs[i-1]+1 if bull[i] else 0
        rs[i]=rs[i-1]+1 if bear[i] else 0
    return ~(expansion|(bs>=streak)|(rs>=streak))

DATA={}
for sym in SYMS:
    if not Path(PARQ[sym]).exists(): continue
    L2.M1=PARQ[sym]; a=L2.A2(L2.load2(TF,start_ms=L2.TICK_MS)); m1=L2.load_m1_exit(start_ms=L2.TICK_MS)
    DATA[sym]=(a,m1,[L2.gen_h5(),L2.gen_h21(),gen_h21_short()]); print("cargado",sym,file=sys.stderr)

def wf(df, k=6):
    d=df.sort_values("ts"); blocks=np.array_split(d,k)
    return [(len(b), b.r.mean() if len(b) else 0) for b in blocks]

print("="*92)
print("WALK-FORWARD ema5/st2 en M15 — avgR por bloque cronológico (6 bloques), config optim")
print("="*92)
for sym in SYMS:
    a,m1,gens=DATA[sym]
    cm=regime_mask(a)
    df=run_system(a,gens,m1,TF,mode="routed",chop_mask=cm,**OPT)
    o=df[df.oos]; blocks=wf(df,6)
    print(f"\n{sym}:  total n={len(df)} avgR={df.r.mean():+.3f} | OOS n={len(o)} avgR={o.r.mean():+.3f} WR={100*(o.r>0).mean():.0f}%")
    print("  bloques: "+" | ".join(f"n{n} {r:+.2f}" for n,r in blocks))
    neg=sum(1 for _,r in blocks if r<0)
    print(f"  bloques negativos: {neg}/6  {'ESTABLE' if neg==0 else 'OJO'}")

# comparación de estabilidad: cuartos OOS de ema5/st2 vs sma50 vs M1
print("\n"+"="*92)
print("CUARTOS OOS: ema5/st2 vs sma50(hoy) vs M1(ideal)  — avgR por cuarto OOS")
print("="*92)
def sma50_mask(a):
    c=a.c.astype(float); atr=a.atr.astype(float); sma=pd.Series(c).rolling(50).mean().values
    return ~(np.abs(c-sma)>0.6*atr)
def oos_quarters(df):
    o=df[df.oos].sort_values("ts")
    return [round(b.r.mean(),2) if len(b) else 0 for b in np.array_split(o,4)]
for sym in SYMS:
    a,m1,gens=DATA[sym]
    fast=run_system(a,gens,m1,TF,mode="routed",chop_mask=regime_mask(a),**OPT)
    sma =run_system(a,gens,m1,TF,mode="routed",chop_mask=sma50_mask(a),**OPT)
    ds  =run_system(a,gens,m1,TF,mode="routed",chop_mask=None,**OPT)
    print(f"  {sym}:  ema5/st2 {oos_quarters(fast)}   sma50 {oos_quarters(sma)}   M1 {oos_quarters(ds)}")
