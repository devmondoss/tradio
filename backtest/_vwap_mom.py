"""
_vwap_mom.py — estrategia explícita de MOMENTUM-VWAP en M5 (el único signal OOS de la minería)
==============================================================================================
La minería anti-overfit dejó UNA señal real OOS: posición extrema vs VWAP de sesión → continuación
(precio bajo VWAP sigue bajando / sobre VWAP sigue subiendo). ~58-62% direccional a ±80bps/3h, pero
el edge crudo (~13 bps) vive en la línea del fee taker (11 bps). Test: ¿con fills MAKER (~4 bps RT)
cruza a rentable, robusto IS/OOS? M5 (M1→M5; M1 es débil en volumen). Barreras ±B bps target/stop.

Señal causal: dist = (close − VWAP_sesión)/ATR. Short si dist ≤ tercil bajo (rolling); long si ≥ tercil alto.
Entrada al cierre (taker) o límite a favor (maker, fill conservador). Exit: barrera ±B bps o timeout.

Uso: python backtest/_vwap_mom.py [--B 80] [--T 36] [--mode quantile|atr] [--th 1.5]
"""
import argparse
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
M1 = ROOT / "data/bybit-perp/processed/btcusdt_perp_m1.parquet"
OOS_MS = int(pd.Timestamp("2026-03-01", tz="UTC").value // 1_000_000)
CAP0, RISK = 500.0, 0.01


def atr(h,l,c,n=14):
    pc=np.roll(c,1); pc[0]=c[0]
    tr=np.maximum(h-l,np.maximum(np.abs(h-pc),np.abs(l-pc)))
    o=np.full(len(tr),np.nan); o[n-1]=np.nanmean(tr[:n]); k=1/n
    for i in range(n,len(tr)): o[i]=o[i-1]*(1-k)+tr[i]*k
    return o


def m5(df):
    g=(df.ts_ms.values//300_000)*300_000
    b=pd.DataFrame({"g":g,"o":df.open.values,"h":df.high.values,"l":df.low.values,"c":df.close.values,"v":df.volume.values})
    return b.groupby("g").agg(open=("o","first"),high=("h","max"),low=("l","min"),close=("c","last"),volume=("v","sum")).reset_index().rename(columns={"g":"ts_ms"})


def run(df, B, T, mode, th, fee_rt, sess_only):
    t=m5(df); ts=t.ts_ms.values.astype(np.int64)
    o,h,l,c,vol=(t[x].values.astype(float) for x in ("open","high","low","close","volume"))
    n=len(t); a=atr(h,l,c); S=pd.Series
    tp_=(h+l+c)/3; day=ts//86_400_000
    cum_pv=pd.DataFrame({"day":day,"pv":tp_*vol}).groupby("day")["pv"].cumsum().values
    cum_v=pd.DataFrame({"day":day,"v":vol}).groupby("day")["v"].cumsum().values
    vwap=cum_pv/(cum_v+1e-9)
    dist=(c-vwap)/(a+1e-9)
    qlo=S(dist).rolling(300,min_periods=60).quantile(0.33).shift(1).values
    qhi=S(dist).rolling(300,min_periods=60).quantile(0.67).shift(1).values
    hm=(ts//60_000)%1440; sess=((hm>=7*60)&(hm<10*60))|((hm>=12*60)&(hm<16*60))
    Bf=B/1e4
    trades=[]; next_ok=0; cap=CAP0; peak=CAP0; dd=0.0; risk_amt=CAP0*RISK; cur_mo=-1
    for i in range(60,n-1):
        if i<next_ok or not np.isfinite(a[i]) or not np.isfinite(qlo[i]): continue
        if sess_only and not sess[i]: continue
        mo=pd.Timestamp(ts[i],unit="ms").to_period("M").ordinal
        if mo!=cur_mo: risk_amt=cap*RISK; cur_mo=mo
        if mode=="quantile":
            short = dist[i] <= qlo[i]; long = dist[i] >= qhi[i]
        else:
            short = dist[i] <= -th; long = dist[i] >= th
        if short:
            side="short"; entry=c[i]; stop=entry*(1+Bf); tp=entry*(1-Bf)
        elif long:
            side="long"; entry=c[i]; stop=entry*(1-Bf); tp=entry*(1+Bf)
        else: continue
        risk_px=abs(entry-stop); res=None
        for j in range(i+1,min(i+1+T,n)):
            if side=="long":
                if l[j]<=stop: res=-1.0; break
                if h[j]>=tp:   res=1.0;  break
            else:
                if h[j]>=stop: res=-1.0; break
                if l[j]<=tp:   res=1.0;  break
        if res is None:
            px=c[min(i+T,n-1)]; res=((px-entry) if side=="long" else (entry-px))/risk_px
        r=res-fee_rt*entry/risk_px; cap+=risk_amt*r; peak=max(peak,cap); dd=max(dd,(peak-cap)/peak)
        trades.append({"ts":int(ts[i]),"side":side,"r":r,"oos":int(ts[i])>=OOS_MS,"win":res>0}); next_ok=i+T//2
    return trades,cap,dd


def report(name,trades,span):
    if not trades: print(f"--- {name} ---\n   sin trades\n"); return
    d=pd.DataFrame(trades); di,do=d[~d.oos],d[d.oos]
    def blk(x,tag,fr):
        if len(x)==0: return f"{tag} n=0"
        return f"{tag} n={len(x):>5} WR={100*x.win.mean():>4.1f}% avgR={x.r.mean():>+.3f} netR={x.r.sum():>+7.1f} {len(x)/(span*fr):>4.1f}tr/d Sharpe~{x.r.mean()/(x.r.std()+1e-9):>+.2f}"
    print(f"--- {name} ---"); print("   "+blk(di,"IS ",0.66)); print("   "+blk(do,"OOS",0.34))


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--B",type=float,default=80.0); ap.add_argument("--T",type=int,default=36)
    ap.add_argument("--mode",choices=["quantile","atr"],default="quantile"); ap.add_argument("--th",type=float,default=1.5)
    ap.add_argument("--allhours",action="store_true")
    args=ap.parse_args()
    df=pd.read_parquet(M1,columns=["ts_ms","open","high","low","close","volume"]).sort_values("ts_ms")
    df=df[df.ts_ms>=pd.Timestamp("2025-06-19",tz="UTC").value//10**6].reset_index(drop=True)
    span=(df.ts_ms.max()-df.ts_ms.min())/86_400_000
    print(f"VWAP-momentum M5 | barrera ±{args.B:.0f}bps T={args.T*5}min | señal {args.mode} | sesión={'no' if args.allhours else 'London+NY'} | span {span:.0f}d\n")
    for fee_bps, lbl in [(11.0,"TAKER 11bps"), (4.0,"MAKER 4bps"), (2.0,"MAKER VIP 2bps")]:
        tr,cap,dd=run(df,args.B,args.T,args.mode,args.th,fee_bps/1e4,not args.allhours)
        report(f"fee {lbl}",tr,span); print(f"   $500 -> ${cap:,.0f} | MaxDD {dd*100:.0f}%\n")


if __name__=="__main__":
    main()
