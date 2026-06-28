"""
_bt_regime_fast.py — barrido COMPLETO de detectores de régimen en M15 (EMA corta)
==================================================================================
Objetivo: encontrar la mejor config de régimen computable en M15 (sin ir a M1).
Barre EMA período × racha (× expansión). Config ya optimizada (stop 0.8, trail 6).
Referencias: detector actual del binario (sma50) y el ideal (regime M1 del dataset).
Criterio anti-overfit: MAXIMIN (el peor de los 3 activos) — robustez cross-activo.
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

def oosA(df): o=df[df.oos]; return o.r.mean() if len(o) else 0
def dd(df):
    o=df[df.oos]; cap=500.0;peak=500.0;m=0.0
    for r in o.sort_values("ts").r.values: cap+=5*r;peak=max(peak,cap);m=max(m,(peak-cap)/peak)
    return 100*m

def regime_mask(a, ema_p, streak, exp_thr):
    c=a.c.astype(float); atr=a.atr.astype(float)
    ema=pd.Series(c).ewm(span=ema_p, adjust=False).mean().values
    atr_ma=pd.Series(atr).rolling(20, min_periods=5).mean().values
    expansion=(atr_ma>0)&(atr/np.where(atr_ma>0,atr_ma,np.nan)>exp_thr) if exp_thr>0 else np.zeros(len(c),bool)
    bull=c>ema*1.002; bear=c<ema*0.998
    n=len(c); bs=np.zeros(n,int); rs=np.zeros(n,int)
    for i in range(1,n):
        bs[i]=bs[i-1]+1 if bull[i] else 0
        rs[i]=rs[i-1]+1 if bear[i] else 0
    return ~(expansion|(bs>=streak)|(rs>=streak))

def sma50_mask(a):   # replica el is_trend ACTUAL del binario
    c=a.c.astype(float); atr=a.atr.astype(float)
    sma=pd.Series(c).rolling(50).mean().values
    return ~(np.abs(c-sma)>0.6*atr)

DATA={}
for sym in SYMS:
    if not Path(PARQ[sym]).exists(): continue
    L2.M1=PARQ[sym]; a=L2.A2(L2.load2(TF,start_ms=L2.TICK_MS)); m1=L2.load_m1_exit(start_ms=L2.TICK_MS)
    DATA[sym]=(a,m1,[L2.gen_h5(),L2.gen_h21(),gen_h21_short()]); print("cargado",sym,file=sys.stderr)

def runmask(maskfn):
    out={}
    for sym in SYMS:
        a,m1,gens=DATA[sym]
        cm = None if maskfn=="DATASET" else maskfn(a)
        df=run_system(a,gens,m1,TF,mode="routed",chop_mask=cm,**OPT)
        out[sym]=(oosA(df),dd(df), (np.mean(cm) if cm is not None else np.mean([str(x).lower() in ("chop","range","balance","consolidation") for x in a.reg])))
    return out

# referencias
M1=runmask("DATASET"); SMA=runmask(sma50_mask)
print("="*100)
print("BARRIDO DETECTOR DE RÉGIMEN EN M15 — OOS avgR (config optim). Referencias arriba.")
print("="*100)
def fmt(r): return "".join(f"{r[s][0]:>+8.2f}" for s in SYMS)
print(f"  {'REF: M1 (ideal)':<26}{fmt(M1)}   maximin={min(M1[s][0] for s in SYMS):+.2f}")
print(f"  {'REF: sma50 (binario hoy)':<26}{fmt(SMA)}   maximin={min(SMA[s][0] for s in SYMS):+.2f}")
print("  "+"-"*98)
print(f"  {'config (ema/racha/exp)':<26}{'BTC':>8}{'ETH':>8}{'SOL':>8}{'maximin':>9}{'minDD':>7}{'chopBTC':>9}")

rows=[]
for ema_p in [2,3,4,5,6,8,10,13]:
    for streak in [2,3,4]:
        r=runmask(lambda a,e=ema_p,s=streak: regime_mask(a,e,s,1.30))
        mm=min(r[s][0] for s in SYMS); mdd=max(r[s][1] for s in SYMS)
        rows.append((f"ema{ema_p}/st{streak}/x1.3", r, mm, mdd))
# variantes de expansión sobre la mejor EMA region
for exp in [0.0, 1.2, 1.5]:
    r=runmask(lambda a,ex=exp: regime_mask(a,3,3,ex))
    mm=min(r[s][0] for s in SYMS); mdd=max(r[s][1] for s in SYMS)
    rows.append((f"ema3/st3/x{exp}", r, mm, mdd))

for name,r,mm,mdd in sorted(rows,key=lambda x:-x[2]):
    star=" <<<" if mm>=min(M1[s][0] for s in SYMS)-0.10 else ""
    print(f"  {name:<26}{r['BTCUSDT'][0]:>+8.2f}{r['ETHUSDT'][0]:>+8.2f}{r['SOLUSDT'][0]:>+8.2f}{mm:>+9.2f}{mdd:>6.0f}%{100*r['BTCUSDT'][2]:>8.0f}%{star}")

print("\n  maximin = peor activo (más alto = más robusto). '<<<' = a <0.10 del ideal M1.")
print("  El binario hoy (sma50) es el piso a batir. M1 es el techo.")
