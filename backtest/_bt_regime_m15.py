"""
_bt_regime_m15.py — VALIDA el port: ¿compute_regime computado en M15 (como lo hará el binario)
replica el ruteo validado (que viene del regime M1 del dataset)?
=============================================================================================
Riesgo: el dataset computa regime en M1 (EMA20=20min) y agrega a M15. El binario lo computará
en barras M15 (EMA20=5h). Acá replico EXACTO la lógica Rust en M15 y la comparo contra el ruteo
con la columna 'regime' del dataset, config ya optimizada (stop 0.8, trail 6), regla dura.
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
TF=15
OPT=dict(stop_scale=0.8, trail_atr=6.0, atr_mult=1.0)

def oosA(df): o=df[df.oos]; return o.r.mean() if len(o) else 0
def dd(df):
    o=df[df.oos]; cap=500.0;peak=500.0;m=0.0
    for r in o.sort_values("ts").r.values: cap+=5*r;peak=max(peak,cap);m=max(m,(peak-cap)/peak)
    return 100*m

def regime_m15(a):
    """Replica EXACTA de is_trend (Rust) sobre barras M15. Devuelve chop_mask (True=chop=fade)."""
    c=a.c.astype(float); atr=a.atr.astype(float)
    ema=pd.Series(c).ewm(span=20, adjust=False).mean().values          # EMA20 sobre M15
    atr_ma=pd.Series(atr).rolling(20, min_periods=5).mean().values     # MA20 del ATR
    expansion = (atr_ma>0) & (atr/np.where(atr_ma>0,atr_ma,np.nan) > 1.30)
    bull = c > ema*1.002
    bear = c < ema*0.998
    n=len(c); bs=np.zeros(n,int); rs=np.zeros(n,int)
    for i in range(1,n):
        bs[i]=bs[i-1]+1 if bull[i] else 0
        rs[i]=rs[i-1]+1 if bear[i] else 0
    trend = expansion | (bs>=3) | (rs>=3)
    return ~trend   # chop_mask

DATA={}
for sym in ["BTCUSDT","ETHUSDT","SOLUSDT"]:
    if not Path(PARQ[sym]).exists(): continue
    L2.M1=PARQ[sym]; a=L2.A2(L2.load2(TF,start_ms=L2.TICK_MS)); m1=L2.load_m1_exit(start_ms=L2.TICK_MS)
    DATA[sym]=(a,m1,[L2.gen_h5(),L2.gen_h21(),gen_h21_short()]); print("cargado",sym,file=sys.stderr)

print("="*88)
print("PORT M15 vs ruteo validado (columna regime dataset) — config optim, OOS avgR")
print("="*88)
print(f"  {'activo':<8}{'dataset regime':>16}{'port M15':>12}{'Δ':>8}{'DD ds':>8}{'DD m15':>8}{'%chop ds':>10}{'%chop m15':>11}")
allok=True
for sym in ["BTCUSDT","ETHUSDT","SOLUSDT"]:
    a,m1,gens=DATA[sym]
    base=run_system(a,gens,m1,TF,mode="routed",chop_mask=None,**OPT)          # dataset regime
    chop=regime_m15(a)
    port=run_system(a,gens,m1,TF,mode="routed",chop_mask=chop,**OPT)          # port M15
    # %chop del dataset
    ds_chop=np.mean([str(x).lower() in ("chop","range","balance","consolidation") for x in a.reg])
    m15_chop=np.mean(chop)
    bA,pA=oosA(base),oosA(port); ok=pA>=bA-0.10   # tolera -0.10 (mismo orden)
    allok&= (pA>=bA-0.10)
    print(f"  {sym:<8}{bA:>+16.3f}{pA:>+12.3f}{pA-bA:>+8.3f}{dd(base):>7.1f}%{dd(port):>7.1f}%{100*ds_chop:>9.0f}%{100*m15_chop:>10.0f}%")
print()
print("  Veredicto: si el port M15 queda ~igual o mejor que el dataset en los 3 -> el binario sirve.")
print("  Si queda muy por debajo -> hay que computar regime en M1 (más trabajo) y NO deployar este port.")
