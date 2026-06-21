"""
S3 — Liquidity Sweep Reversal (SFP / stop-run) + proxy de liquidación
======================================================================
Edge base: el precio barre un swing high/low previo (toma de BSL/SSL), NO acepta fuera (cierra dentro),
revierte. Fade del barrido hacia el swing opuesto. La capa OF confirma que el barrido fue forzado:
  - volumen en el barrido (VR>=2)  ("toma sin volumen = fake, no operar" — regla de las transcripciones)
  - delta absorción contra el barrido (DZ)
  - PROXY DE LIQUIDACIÓN: OI cae en la ventana del barrido (cierre forzado de stops; Bybit no publica
    liquidaciones históricas, y el spec define la señal justo así: "OI baja en el sweep = stops saltando")

Compara base vs base+OF, IS/OOS, fee real. Y test fee-independiente: ¿la confirmación sube el WR del SFP?

Uso: python backtest/_s3_sweep.py [--K 24] [--rr 2.0]
"""
import argparse
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
M1 = ROOT / "data/bybit-perp/processed/btcusdt_perp_m1.parquet"
OI = ROOT / "data/bybit-perp/oi_5m.parquet"
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
    b=pd.DataFrame({"g":g,"o":df.open.values,"h":df.high.values,"l":df.low.values,"c":df.close.values,
                    "v":df.volume.values,"d":df.delta.values})
    t=b.groupby("g").agg(open=("o","first"),high=("h","max"),low=("l","min"),close=("c","last"),
                         volume=("v","sum"),delta=("d","sum")).reset_index().rename(columns={"g":"ts_ms"})
    oi=pd.read_parquet(OI).sort_values("ts_ms")
    t=pd.merge_asof(t.sort_values("ts_ms"),oi,on="ts_ms",direction="backward")
    return t


def run(df, K, rr, use_of, minstop_bps=25.0, conditional=False):
    t=build(df); ts=t.ts_ms.values.astype(np.int64)
    o,h,l,c=(t[x].values.astype(float) for x in("open","high","low","close"))
    vol,delta=t.volume.values.astype(float),t.delta.values.astype(float)
    oi=t.open_interest.values.astype(float)
    n=len(t); a=atr(h,l,c); S=pd.Series
    sh=S(h).rolling(K).max().shift(1).values   # swing high previo (no incluye barra actual)
    sl=S(l).rolling(K).min().shift(1).values
    vsma=S(vol).rolling(50).mean().shift(1).values
    dz=(delta-S(delta).rolling(50).mean().shift(1).values)/(S(delta).rolling(50).std().shift(1).values+1e-9)
    vr=vol/(vsma+1e-9)
    oi_drop=oi/S(oi).shift(6).values-1.0   # cambio OI en ~30 min (6×5m)
    hm=(ts//60_000)%1440; sess=((hm>=7*60)&(hm<10*60))|((hm>=13*60)&(hm<17*60))
    ok=np.isfinite(a)&np.isfinite(sh)&sess
    trades=[]; cond=[]; next_ok=0; cap=CAP0; peak=CAP0; dd=0.0; risk_amt=CAP0*RISK; cur_mo=-1; timeout=48
    for i in range(K+50,n-1):
        if not ok[i] or i<next_ok: continue
        mo=pd.Timestamp(ts[i],unit="ms").to_period("M").ordinal
        if mo!=cur_mo: risk_amt=cap*RISK; cur_mo=mo
        for side in("long","short"):
            if side=="short":   # barre swing high y cierra dentro
                swept=(h[i]>sh[i]) and (c[i]<sh[i])
                if not swept: continue
                of_ok=(vr[i]>=2.0) and (dz[i]>=1.0) and (oi_drop[i]<=-0.005)   # vol + compra agresiva absorbida + OI cae
                if use_of and not of_ok: continue
                entry=c[i]; stop=max(h[i],sh[i])+0.10*a[i]; stop=max(stop,entry+minstop_bps/1e4*entry)
                risk_px=stop-entry
                if risk_px<=0: continue
                tp=entry-rr*risk_px
            else:                # barre swing low y cierra dentro
                swept=(l[i]<sl[i]) and (c[i]>sl[i])
                if not swept: continue
                of_ok=(vr[i]>=2.0) and (dz[i]<=-1.0) and (oi_drop[i]<=-0.005)
                if use_of and not of_ok: continue
                entry=c[i]; stop=min(l[i],sl[i])-0.10*a[i]; stop=min(stop,entry-minstop_bps/1e4*entry)
                risk_px=entry-stop
                if risk_px<=0: continue
                tp=entry+rr*risk_px
            # conditional fee-independiente: ¿alcanza tp antes que stop?
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
            cond.append({"ts":int(ts[i]),"oos":int(ts[i])>=OOS_MS,"win":res>0,
                         "of":bool((vr[i]>=2.0) and (abs(dz[i])>=1.0) and (oi_drop[i]<=-0.005))})
            r=res-FEE_RT*entry/risk_px; cap+=risk_amt*r; peak=max(peak,cap); dd=max(dd,(peak-cap)/peak)
            trades.append({"ts":int(ts[i]),"r":r,"oos":int(ts[i])>=OOS_MS,"win":res>0}); next_ok=i+3; break
    return (cond if conditional else trades), cap, dd


def report(name,trades,span):
    if not trades: print(f"{name}: sin trades"); return
    d=pd.DataFrame(trades); di,do=d[~d.oos],d[d.oos]
    def blk(x,tag,fr):
        if len(x)==0: return f"{tag} n=0"
        return f"{tag} n={len(x):>4} WR={100*x.win.mean():>4.1f}% avgR={x.r.mean():>+.3f} netR={x.r.sum():>+6.1f} {len(x)/(span*fr):>4.1f}tr/d Sharpe~{x.r.mean()/(x.r.std()+1e-9):>+.2f}"
    print(f"--- {name} ---"); print("   "+blk(di,"IS ",0.66)); print("   "+blk(do,"OOS",0.34))


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--K",type=int,default=24); ap.add_argument("--rr",type=float,default=2.0)
    args=ap.parse_args()
    df=pd.read_parquet(M1,columns=["ts_ms","open","high","low","close","volume","delta"]).sort_values("ts_ms")
    df=df[df.ts_ms>=pd.Timestamp("2025-06-19",tz="UTC").value//10**6].reset_index(drop=True)
    span=(df.ts_ms.max()-df.ts_ms.min())/86_400_000
    print(f"S3 Sweep Reversal (SFP) | K={args.K} ({args.K*5}min swing) | RR {args.rr} | fee {FEE_RT*1e4:.0f}bps | span {span:.0f}d\n")
    for use_of in (False,True):
        tr,cap,dd=run(df,args.K,args.rr,use_of)
        report(f"OF={'sí (VR>=2 + DZ + OI-drop[proxy-liq])' if use_of else 'no (todo SFP)'}",tr,span)
        print(f"   MaxDD {dd*100:.0f}%\n")
    # test fee-independiente
    cond,_,_=run(df,args.K,args.rr,False,conditional=True)
    e=pd.DataFrame(cond); ei,eo=e[~e.oos],e[e.oos]
    def pw(d): return (len(d),100*d.win.mean()) if len(d) else (0,float('nan'))
    print("Test fee-independiente (¿la confirmación OF sube el WR del SFP?):")
    n_i,b_i=pw(ei); n_o,b_o=pw(eo)
    print(f"   BASE          IS {b_i:>4.1f}% (n={n_i})  OOS {b_o:>4.1f}% (n={n_o})")
    di,do=ei[ei.of],eo[eo.of]; ni,wi=pw(di); no,wo=pw(do)
    print(f"   con OF(+liq)  IS {wi:>4.1f}% (n={ni})  OOS {wo:>4.1f}% (n={no})  Δ {wi-b_i:+.1f}/{wo-b_o:+.1f}")


if __name__=="__main__":
    main()
