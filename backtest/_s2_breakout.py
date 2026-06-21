"""
S2 — Real Breakout Continuation · momentum (opuesto a S1)
=========================================================
Edge base: ruptura REAL de un rango/extremo previo → continuación a favor del break. La capa OF es el
gate "ruptura real vs fakeout" del spec (#11/#61): VR>=4 + |DZ|>=2 + CVD a favor + (OI sube). Sin OF se
entra a TODA ruptura de rango (incluye fakeouts). Compara base vs base+OF para ver si el OF filtra
fakeouts mejor que el azar, neto de fee. Causal, fee real, IS/OOS.

Entrada: cierre fuera del rango (W previo). Stop = otro lado del rango / vuelta dentro. Target = R fijo
(continuación). Sesiones líquidas. M5.

Uso: python backtest/_s2_breakout.py [--rr 2.0]
"""
import argparse
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
M1 = ROOT / "data/bybit-perp/processed/btcusdt_perp_m1.parquet"
OOS_MS = int(pd.Timestamp("2026-03-01", tz="UTC").value // 1_000_000)
FEE_RT = 0.0011; CAP0, RISK = 500.0, 0.01; W = 20


def atr(h,l,c,n=14):
    pc=np.roll(c,1); pc[0]=c[0]
    tr=np.maximum(h-l,np.maximum(np.abs(h-pc),np.abs(l-pc)))
    o=np.full(len(tr),np.nan); o[n-1]=np.nanmean(tr[:n]); k=1/n
    for i in range(n,len(tr)): o[i]=o[i-1]*(1-k)+tr[i]*k
    return o


def resample_m5(df):
    g=(df.ts_ms.values//300_000)*300_000
    b=pd.DataFrame({"g":g,"o":df.open.values,"h":df.high.values,"l":df.low.values,"c":df.close.values,
                    "v":df.volume.values,"d":df.delta.values,"cv":df.cvd.values})
    return b.groupby("g").agg(open=("o","first"),high=("h","max"),low=("l","min"),close=("c","last"),
                              volume=("v","sum"),delta=("d","sum"),cvd=("cv","last")).reset_index().rename(columns={"g":"ts_ms"})


def run(df, rr, use_of, minstop_bps=25.0):
    t=resample_m5(df); ts=t.ts_ms.values.astype(np.int64)
    o,h,l,c=(t[x].values.astype(float) for x in("open","high","low","close"))
    vol,delta,cvd=t.volume.values.astype(float),t.delta.values.astype(float),t.cvd.values.astype(float)
    n=len(t); a=atr(h,l,c); S=pd.Series
    rhigh=S(h).rolling(W).max().shift(1).values; rlow=S(l).rolling(W).min().shift(1).values
    rsize=rhigh-rlow; drift=np.abs(c-S(c).shift(W).values)
    vsma=S(vol).rolling(50).mean().shift(1).values
    dz=(delta-S(delta).rolling(50).mean().shift(1).values)/(S(delta).rolling(50).std().shift(1).values+1e-9)
    vr=vol/(vsma+1e-9)
    cvd_up=cvd>S(cvd).shift(3).values; cvd_dn=cvd<S(cvd).shift(3).values
    hm=(ts//60_000)%1440; sess=((hm>=7*60)&(hm<10*60))|((hm>=13*60)&(hm<17*60))
    range_ok=np.isfinite(a)&(rsize>=0.8*a)&(rsize<=3.5*a)&(drift<0.5*rsize)&sess
    trades=[]; next_ok=0; cap=CAP0; peak=CAP0; dd=0.0; risk_amt=CAP0*RISK; cur_mo=-1; timeout=48
    for i in range(W+50,n-1):
        if i<next_ok or not range_ok[i]: continue
        mo=pd.Timestamp(ts[i],unit="ms").to_period("M").ordinal
        if mo!=cur_mo: risk_amt=cap*RISK; cur_mo=mo
        for side in("long","short"):
            if side=="long":
                brk = c[i] > rhigh[i]                          # cierre por encima del rango
                if not brk: continue
                if use_of and not (vr[i]>=4 and dz[i]>=2 and cvd_up[i]): continue
                entry=c[i]; stop=min(rhigh[i], entry-minstop_bps/1e4*entry); risk_px=entry-stop
                if risk_px<=0: continue
                tp=entry+rr*risk_px
            else:
                brk = c[i] < rlow[i]
                if not brk: continue
                if use_of and not (vr[i]>=4 and dz[i]<=-2 and cvd_dn[i]): continue
                entry=c[i]; stop=max(rlow[i], entry+minstop_bps/1e4*entry); risk_px=stop-entry
                if risk_px<=0: continue
                tp=entry-rr*risk_px
            res=None
            for j in range(i+1,min(i+1+timeout,n)):
                if side=="long":
                    if l[j]<=stop: res=-1.0; break
                    if h[j]>=tp:   res=rr;  break
                else:
                    if h[j]>=stop: res=-1.0; break
                    if l[j]<=tp:   res=rr;  break
            if res is None:
                px=c[min(i+timeout,n-1)]; res=((px-entry) if side=="long" else (entry-px))/risk_px
            r=res-FEE_RT*entry/risk_px; cap+=risk_amt*r; peak=max(peak,cap); dd=max(dd,(peak-cap)/peak)
            trades.append({"ts":int(ts[i]),"r":r,"oos":int(ts[i])>=OOS_MS,"win":res>0}); next_ok=i+3; break
    return trades,cap,dd


def report(name,trades,span):
    if not trades: print(f"{name}: sin trades"); return
    d=pd.DataFrame(trades); di,do=d[~d.oos],d[d.oos]
    def blk(x,tag,fr):
        if len(x)==0: return f"{tag} n=0"
        return f"{tag} n={len(x):>4} WR={100*x.win.mean():>4.1f}% avgR={x.r.mean():>+.3f} netR={x.r.sum():>+6.1f} {len(x)/(span*fr):>4.1f}tr/d Sharpe~{x.r.mean()/(x.r.std()+1e-9):>+.2f}"
    print(f"--- {name} ---"); print("   "+blk(di,"IS ",0.66)); print("   "+blk(do,"OOS",0.34))


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--rr",type=float,default=2.0); args=ap.parse_args()
    df=pd.read_parquet(M1,columns=["ts_ms","open","high","low","close","volume","delta","cvd"]).sort_values("ts_ms")
    df=df[df.ts_ms>=pd.Timestamp("2025-06-19",tz="UTC").value//10**6].reset_index(drop=True)
    span=(df.ts_ms.max()-df.ts_ms.min())/86_400_000
    print(f"S2 Breakout Continuation | RR {args.rr} | fee {FEE_RT*1e4:.0f}bps | span {span:.0f}d\n")
    for use_of in (False,True):
        tr,cap,dd=run(df,args.rr,use_of)
        report(f"OF={'sí (VR>=4,|DZ|>=2,CVD)' if use_of else 'no (toda ruptura)'}",tr,span)
        print(f"   MaxDD {dd*100:.0f}%\n")


if __name__=="__main__":
    main()
