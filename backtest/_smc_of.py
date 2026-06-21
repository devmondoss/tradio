"""
SMC + Orderflow confluence (síntesis de la investigación web: MarketTrace + Buildix + TradingFinder)
====================================================================================================
Framework concreto y unánime de las fuentes para BTC perp:
  ESTRUCTURA (SMC): zona en swing low/high alineada con Volume Profile (POC/VAL).
  CONFIRMACIÓN ORDERFLOW (regla clave: requerir ≥2 de):
    - CVD divergencia (precio LL pero CVD HL / precio HH pero CVD LH)
    - OBI imbalance (bid-heavy ≥ +0.30 para long / offer-heavy ≤ −0.30 para short)
    - funding extremo (< −0.02% para long / > +0.05% para short)
  Stop bajo el swing (−ATR) / VAL; target = swing opuesto previo.
Test: estructura-sola vs +1 confirmación vs +≥2 (la confluencia que las fuentes dicen obligatoria).
Causal, fee real 11 bps, IS/OOS, M5. "Sin orderflow que confirme, el patrón es un dibujo."

Uso: python backtest/_smc_of.py [--K 12] [--rr 2.0]
"""
import argparse
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
M1 = ROOT / "data/bybit-perp/processed/btcusdt_perp_m1.parquet"
FUND = ROOT / "data/bybit-perp/funding.parquet"
OOS_MS = int(pd.Timestamp("2026-03-01", tz="UTC").value // 1_000_000)
FEE_RT = 0.0011; CAP0, RISK = 500.0, 0.01


def atr(h,l,c,n=14):
    pc=np.roll(c,1); pc[0]=c[0]
    tr=np.maximum(h-l,np.maximum(np.abs(h-pc),np.abs(l-pc)))
    o=np.full(len(tr),np.nan); o[n-1]=np.nanmean(tr[:n]); k=1/n
    for i in range(n,len(tr)): o[i]=o[i-1]*(1-k)+tr[i]*k
    return o


def build(df):
    g=(df.ts_ms.values//300_000)*300_000
    cols={"g":g,"o":df.open.values,"h":df.high.values,"l":df.low.values,"c":df.close.values,
          "v":df.volume.values,"d":df.delta.values,"cv":df.cvd.values,
          "obi":df.obi10_mean.values,"poc":df.vp_poc.values,"val":df.vp_val.values,"vah":df.vp_vah.values}
    b=pd.DataFrame(cols)
    t=b.groupby("g").agg(open=("o","first"),high=("h","max"),low=("l","min"),close=("c","last"),
                         volume=("v","sum"),delta=("d","sum"),cvd=("cv","last"),obi=("obi","mean"),
                         poc=("poc","last"),val=("val","last"),vah=("vah","last")).reset_index().rename(columns={"g":"ts_ms"})
    fund=pd.read_parquet(FUND).sort_values("ts_ms")
    t=pd.merge_asof(t.sort_values("ts_ms"),fund,on="ts_ms",direction="backward")
    return t


def run(df, K, rr, req):
    t=build(df); ts=t.ts_ms.values.astype(np.int64)
    o,h,l,c=(t[x].values.astype(float) for x in("open","high","low","close"))
    cvd=t.cvd.values.astype(float); obi=t.obi.values.astype(float)
    val=t.val.values.astype(float); vah=t.vah.values.astype(float); fund=t.funding_rate.values.astype(float)
    n=len(t); a=atr(h,l,c); S=pd.Series
    new_lo=l<=S(l).rolling(K).min().shift(1).values     # nuevo mínimo de K
    new_hi=h>=S(h).rolling(K).max().shift(1).values
    cvd_div_bull=cvd>S(cvd).shift(K).values             # precio LL pero CVD arriba
    cvd_div_bear=cvd<S(cvd).shift(K).values
    prior_hi=S(h).rolling(K).max().shift(1).values; prior_lo=S(l).rolling(K).min().shift(1).values
    hm=(ts//60_000)%1440; sess=((hm>=7*60)&(hm<10*60))|((hm>=12*60)&(hm<16*60))
    tol=0.5  # cercanía a VAL/VAH en ATR
    trades=[]; next_ok=0; cap=CAP0; peak=CAP0; dd=0.0; risk_amt=CAP0*RISK; cur_mo=-1; timeout=48
    for i in range(K+50,n-1):
        if i<next_ok or not sess[i] or not np.isfinite(a[i]): continue
        mo=pd.Timestamp(ts[i],unit="ms").to_period("M").ordinal
        if mo!=cur_mo: risk_amt=cap*RISK; cur_mo=mo
        # LONG: swing low alineado con VAL/POC
        near_val = np.isfinite(val[i]) and (abs(l[i]-val[i])<=tol*a[i] or abs(l[i]-t.poc.values[i])<=tol*a[i])
        if new_lo[i] and near_val:
            conf = int(cvd_div_bull[i]) + int(obi[i]>=0.30) + int(fund[i]<-0.0002)
            if conf>=req:
                entry=c[i]; stop=l[i]-0.3*a[i]; tp=prior_hi[i]
                if stop<entry<tp and (tp-entry)/(entry-stop)>=1.0:
                    took=sim(trades,"long",entry,stop,tp,h,l,c,i,timeout,n,ts,rr)
                    if took:
                        cap+=risk_amt*trades[-1]["r"]; peak=max(peak,cap); dd=max(dd,(peak-cap)/peak); next_ok=i+4; continue
        # SHORT: swing high alineado con VAH/POC
        near_vah = np.isfinite(vah[i]) and (abs(h[i]-vah[i])<=tol*a[i] or abs(h[i]-t.poc.values[i])<=tol*a[i])
        if new_hi[i] and near_vah:
            conf = int(cvd_div_bear[i]) + int(obi[i]<=-0.30) + int(fund[i]>0.0005)
            if conf>=req:
                entry=c[i]; stop=h[i]+0.3*a[i]; tp=prior_lo[i]
                if tp<entry<stop and (entry-tp)/(stop-entry)>=1.0:
                    took=sim(trades,"short",entry,stop,tp,h,l,c,i,timeout,n,ts,rr)
                    if took:
                        cap+=risk_amt*trades[-1]["r"]; peak=max(peak,cap); dd=max(dd,(peak-cap)/peak); next_ok=i+4
    return trades,cap,dd


def sim(trades,side,entry,stop,tp,h,l,c,i,timeout,n,ts,rr_cap):
    risk_px=abs(entry-stop); rr=abs(tp-entry)/risk_px
    if rr>rr_cap*2: tp = entry + (rr_cap*risk_px if side=="long" else -rr_cap*risk_px); rr=rr_cap
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
    r=res-FEE_RT*entry/risk_px
    trades.append({"ts":int(ts[i]),"side":side,"r":r,"oos":int(ts[i])>=OOS_MS,"win":res>0}); return True


def report(name,trades,span):
    if not trades: print(f"--- {name} ---\n   sin trades\n"); return
    d=pd.DataFrame(trades); di,do=d[~d.oos],d[d.oos]
    def blk(x,tag,fr):
        if len(x)==0: return f"{tag} n=0"
        return f"{tag} n={len(x):>4} WR={100*x.win.mean():>4.1f}% avgR={x.r.mean():>+.3f} netR={x.r.sum():>+6.1f} {len(x)/(span*fr):>4.2f}tr/d Sharpe~{x.r.mean()/(x.r.std()+1e-9):>+.2f}"
    print(f"--- {name} ---"); print("   "+blk(di,"IS ",0.66)); print("   "+blk(do,"OOS",0.34))


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--K",type=int,default=12); ap.add_argument("--rr",type=float,default=2.0)
    args=ap.parse_args()
    df=pd.read_parquet(M1,columns=["ts_ms","open","high","low","close","volume","delta","cvd","obi10_mean","vp_poc","vp_val","vp_vah"]).sort_values("ts_ms")
    df=df[df.ts_ms>=pd.Timestamp("2025-06-19",tz="UTC").value//10**6].reset_index(drop=True)
    span=(df.ts_ms.max()-df.ts_ms.min())/86_400_000
    print(f"SMC+Orderflow confluence (web) | M5 | K={args.K} | RR cap {args.rr} | fee {FEE_RT*1e4:.0f}bps | span {span:.0f}d\n")
    for req,lbl in [(0,"estructura sola (swing+VP, sin OF)"),(1,"+1 confirmación OF"),(2,"+≥2 confirmaciones OF (regla de las fuentes)")]:
        tr,cap,dd=run(df,args.K,args.rr,req)
        report(lbl,tr,span); print(f"   $500 -> ${cap:,.0f} | MaxDD {dd*100:.0f}%\n")


if __name__=="__main__":
    main()
