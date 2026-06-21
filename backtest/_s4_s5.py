"""
S4 — VWAP Band Reversion  &  S5 — AMD/Power-of-3 session sweep
=============================================================
S4: precio alcanza VWAP de sesión ±2σ → reversión al VWAP. OF: rechazo (delta contra la extensión).
S5: Asia (00-07 UTC) define rango; London (07-10) barre un extremo de Asia (manipulación) → fade hacia
    el extremo opuesto/mid, hold hacia NY (distribución). OF: OI cae en el sweep + delta absorción.
Ambas: base vs base+OF, IS/OOS, fee real, fee-survivable stop. M5.

Uso: python backtest/_s4_s5.py
"""
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
M1 = ROOT / "data/bybit-perp/processed/btcusdt_perp_m1.parquet"
OI = ROOT / "data/bybit-perp/oi_5m.parquet"
OOS_MS = int(pd.Timestamp("2026-03-01", tz="UTC").value // 1_000_000)
FEE_RT = 0.0011; CAP0, RISK = 500.0, 0.01; MINSTOP = 25.0


def atr(h,l,c,n=14):
    pc=np.roll(c,1); pc[0]=c[0]
    tr=np.maximum(h-l,np.maximum(np.abs(h-pc),np.abs(l-pc)))
    o=np.full(len(tr),np.nan); o[n-1]=np.nanmean(tr[:n]); k=1/n
    for i in range(n,len(tr)): o[i]=o[i-1]*(1-k)+tr[i]*k
    return o


def m5(df):
    g=(df.ts_ms.values//300_000)*300_000
    b=pd.DataFrame({"g":g,"o":df.open.values,"h":df.high.values,"l":df.low.values,"c":df.close.values,
                    "v":df.volume.values,"d":df.delta.values})
    return b.groupby("g").agg(open=("o","first"),high=("h","max"),low=("l","min"),close=("c","last"),
                              volume=("v","sum"),delta=("d","sum")).reset_index().rename(columns={"g":"ts_ms"})


def sim(side,entry,stop,tp,h,l,c,i,n,timeout=48):
    risk_px=abs(entry-stop)
    if risk_px<=0: return None
    res=None
    for j in range(i+1,min(i+1+timeout,n)):
        if side=="long":
            if l[j]<=stop: res=-1.0; break
            if h[j]>=tp:  res=(tp-entry)/risk_px; break
        else:
            if h[j]>=stop: res=-1.0; break
            if l[j]<=tp:  res=(entry-tp)/risk_px; break
    if res is None:
        px=c[min(i+timeout,n-1)]; res=((px-entry) if side=="long" else (entry-px))/risk_px
    return res-FEE_RT*entry/risk_px, res>0


def stat(trades,span):
    d=pd.DataFrame(trades); di,do=d[~d.oos],d[d.oos]
    def blk(x,tag,fr):
        if len(x)==0: return f"{tag} n=0"
        return f"{tag} n={len(x):>4} WR={100*x.win.mean():>4.1f}% avgR={x.r.mean():>+.3f} {len(x)/(span*fr):>4.1f}tr/d Sh~{x.r.mean()/(x.r.std()+1e-9):>+.2f}"
    print("   "+blk(di,"IS ",0.66)); print("   "+blk(do,"OOS",0.34))


def run_s4(t,span):
    ts=t.ts_ms.values.astype(np.int64); o,h,l,c=(t[x].values.astype(float) for x in("open","high","low","close"))
    vol,delta=t.volume.values.astype(float),t.delta.values.astype(float); n=len(t); a=atr(h,l,c); S=pd.Series
    tp_=(h+l+c)/3; day=ts//86_400_000
    dfv=pd.DataFrame({"day":day,"pv":tp_*vol,"v":vol,"tp":tp_})
    cum_pv=dfv.groupby("day")["pv"].cumsum().values; cum_v=dfv.groupby("day")["v"].cumsum().values
    vwap=cum_pv/(cum_v+1e-9)
    dev=tp_-vwap
    sig=dfv.assign(dev2=dev**2).groupby("day")["dev2"].cumsum().values
    cnt=dfv.groupby("day").cumcount().values+1
    sd=np.sqrt(sig/cnt)
    dz=(delta-S(delta).rolling(50).mean().shift(1).values)/(S(delta).rolling(50).std().shift(1).values+1e-9)
    hm=(ts//60_000)%1440; sess=((hm>=7*60)&(hm<10*60))|((hm>=13*60)&(hm<17*60))
    for use_of in (False,True):
        tr=[]; next_ok=0
        for i in range(60,n-1):
            if i<next_ok or not sess[i] or not np.isfinite(a[i]) or sd[i]<=0: continue
            up=c[i]>=vwap[i]+2*sd[i]; dn=c[i]<=vwap[i]-2*sd[i]
            if up:
                if use_of and not (dz[i]<=-0.5): continue
                entry=c[i]; stop=entry+max(MINSTOP/1e4*entry,0.5*sd[i]); tp=vwap[i]; side="short"
                if tp>=entry: continue
            elif dn:
                if use_of and not (dz[i]>=0.5): continue
                entry=c[i]; stop=entry-max(MINSTOP/1e4*entry,0.5*sd[i]); tp=vwap[i]; side="long"
                if tp<=entry: continue
            else: continue
            out=sim(side,entry,stop,tp,h,l,c,i,n)
            if out is None: continue
            tr.append({"ts":int(ts[i]),"r":out[0],"win":out[1],"oos":int(ts[i])>=OOS_MS}); next_ok=i+3
        print(f"--- S4 VWAP±2σ OF={'sí' if use_of else 'no'} ---"); stat(tr,span)


def run_s5(t,span):
    ts=t.ts_ms.values.astype(np.int64); o,h,l,c=(t[x].values.astype(float) for x in("open","high","low","close"))
    delta=t.delta.values.astype(float); oi=t.open_interest.values.astype(float); n=len(t); a=atr(h,l,c); S=pd.Series
    dz=(delta-S(delta).rolling(50).mean().shift(1).values)/(S(delta).rolling(50).std().shift(1).values+1e-9)
    oi_drop=oi/S(oi).shift(6).values-1.0
    day=ts//86_400_000; hm=(ts//60_000)%1440
    # rango Asia (00-07) por día
    asia=pd.DataFrame({"day":day,"h":h,"l":l,"hm":hm})
    asia=asia[(asia.hm<7*60)].groupby("day").agg(ah=("h","max"),al=("l","min"))
    ah=asia.ah.to_dict(); al=asia.al.to_dict()
    london=(hm>=7*60)&(hm<11*60)
    for use_of in (False,True):
        tr=[]; done=set()
        for i in range(60,n-1):
            d=int(day[i])
            if not london[i] or d in done or d not in ah or not np.isfinite(a[i]): continue
            sweep_hi=(h[i]>ah[d]) and (c[i]<ah[d]); sweep_lo=(l[i]<al[d]) and (c[i]>al[d])
            if sweep_hi:
                if use_of and not (dz[i]>=1.0 and oi_drop[i]<=-0.005): continue
                entry=c[i]; stop=max(h[i],ah[d])+max(MINSTOP/1e4*entry,0.10*a[i]); tp=al[d]; side="short"
                if tp>=entry: continue
            elif sweep_lo:
                if use_of and not (dz[i]<=-1.0 and oi_drop[i]<=-0.005): continue
                entry=c[i]; stop=min(l[i],al[d])-max(MINSTOP/1e4*entry,0.10*a[i]); tp=ah[d]; side="long"
                if tp<=entry: continue
            else: continue
            out=sim(side,entry,stop,tp,h,l,c,i,n,timeout=120)  # hold hacia NY
            if out is None: continue
            tr.append({"ts":int(ts[i]),"r":out[0],"win":out[1],"oos":int(ts[i])>=OOS_MS}); done.add(d)
        print(f"--- S5 AMD sesión OF={'sí (OI-drop+delta)' if use_of else 'no'} ---"); stat(tr,span)


def main():
    df=pd.read_parquet(M1,columns=["ts_ms","open","high","low","close","volume","delta"]).sort_values("ts_ms")
    df=df[df.ts_ms>=pd.Timestamp("2025-06-19",tz="UTC").value//10**6].reset_index(drop=True)
    t=m5(df); oi=pd.read_parquet(OI).sort_values("ts_ms")
    t=pd.merge_asof(t.sort_values("ts_ms"),oi,on="ts_ms",direction="backward")
    span=(t.ts_ms.max()-t.ts_ms.min())/86_400_000
    print(f"S4/S5 | fee {FEE_RT*1e4:.0f}bps | span {span:.0f}d | stop floor {MINSTOP:.0f}bps\n")
    run_s4(t,span); print(); run_s5(t,span)


if __name__=="__main__":
    main()
