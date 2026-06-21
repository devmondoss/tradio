"""
TOP-DOWN multi-timeframe (4H → 1H → 5m) — SMC/ICT con orderflow como confirmación 5m
=====================================================================================
La "escalera top-down" de las imágenes/transcripciones, fiel y anidada:
  4H  = DIRECCIÓN  : sesgo macro (EMA20 4H). Solo se opera a favor.
  1H  = ESTRUCTURA + LIQUIDEZ : zona OB/FVG alineada con el sesgo 4H (setup).
  5m  = ENTRADA + CONFIRMACIÓN : el precio entra en la zona 1H en sesión y dispara confirmación 5m
        (engulfing / rechazo) + ORDERFLOW (pico de volumen + absorción: delta a favor del rechazo).
Regla: sin alineación 4H↔1H = no setup; sin confirmación 5m = no entrada.
SL detrás de la zona; TP = RR estructural. Causal, fee real (11 bps), IS/OOS. Compara:
  A solo-estructura (4H+1H+entrada 5m simple)   vs   B +confirmación orderflow 5m.

Uso: python backtest/_topdown.py [--rr 2.0]
"""
import argparse
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
M1 = ROOT / "data/bybit-perp/processed/btcusdt_perp_m1.parquet"
OOS_MS = int(pd.Timestamp("2026-03-01", tz="UTC").value // 1_000_000)
FEE_RT = 0.0011; CAP0, RISK = 500.0, 0.01


def ema(x, n):
    a = 2/(n+1); o = np.empty_like(x); o[0] = x[0]
    for i in range(1, len(x)): o[i] = a*x[i] + (1-a)*o[i-1]
    return o


def rs(df, ms):
    g = (df.ts_ms.values // ms) * ms
    b = pd.DataFrame({"g": g, "o": df.open.values, "h": df.high.values, "l": df.low.values,
                      "c": df.close.values, "v": df.volume.values, "d": df.delta.values})
    return b.groupby("g").agg(open=("o","first"), high=("h","max"), low=("l","min"), close=("c","last"),
                              volume=("v","sum"), delta=("d","sum")).reset_index().rename(columns={"g":"ts_ms"})


def atr(h,l,c,n=14):
    pc=np.roll(c,1); pc[0]=c[0]
    tr=np.maximum(h-l,np.maximum(np.abs(h-pc),np.abs(l-pc)))
    o=np.full(len(tr),np.nan); o[n-1]=np.nanmean(tr[:n]); k=1/n
    for i in range(n,len(tr)): o[i]=o[i-1]*(1-k)+tr[i]*k
    return o


def build_zones_1h(h1):
    """Zonas 1H = FVG (3 velas). Bull FVG: low[k] > high[k-2] (gap alcista) → zona [high[k-2], low[k]].
       Bear FVG: high[k] < low[k-2] → zona [high[k], low[k-2]]. Cada zona activa hasta ser llenada."""
    H,L = h1.high.values, h1.low.values; ts = h1.ts_ms.values.astype(np.int64)
    zones = []  # (t_create, dir, zlo, zhi)
    for k in range(2, len(h1)):
        if L[k] > H[k-2]:   zones.append((ts[k], +1, H[k-2], L[k]))     # demanda (bull FVG)
        elif H[k] < L[k-2]: zones.append((ts[k], -1, H[k], L[k-2]))     # oferta (bear FVG)
    return zones


def run(df, rr, use_of):
    m5 = rs(df, 300_000); h1 = rs(df, 3_600_000); h4 = rs(df, 14_400_000)
    # sesgo 4H (EMA20, causal: usar barra 4H cerrada anterior)
    h4e = ema(h4.close.values, 20)
    h4_bias = np.where(h4.close.values > h4e, 1, -1)
    h4map = {int(h4.ts_ms.values[i]): h4_bias[i] for i in range(len(h4))}
    h4_ts = h4.ts_ms.values.astype(np.int64)
    # estructura 1H (EMA20)
    h1e = ema(h1.close.values, 20)
    h1map_e = {int(h1.ts_ms.values[i]): (h1.close.values[i], h1e[i]) for i in range(len(h1))}
    zones = build_zones_1h(h1)
    zarr = np.array([(z[0], z[1], z[2], z[3]) for z in zones], dtype=float) if zones else np.empty((0,4))

    ts = m5.ts_ms.values.astype(np.int64)
    o,h,l,c = (m5[x].values.astype(float) for x in ("open","high","low","close"))
    vol, delta = m5.volume.values.astype(float), m5.delta.values.astype(float)
    n = len(m5); a = atr(h,l,c); S = pd.Series
    vsma = S(vol).rolling(50).mean().shift(1).values
    vr = vol/(vsma+1e-9)
    hm = (ts//60_000)%1440; sess = ((hm>=7*60)&(hm<10*60))|((hm>=12*60)&(hm<16*60))

    def bias4(t):     # sesgo del último 4H cerrado antes de t
        b = (t//14_400_000)*14_400_000 - 14_400_000
        return h4map.get(int(b), 0)
    def struct1(t):
        b = (t//3_600_000)*3_600_000 - 3_600_000
        v = h1map_e.get(int(b));
        return 0 if v is None else (1 if v[0]>v[1] else -1)

    trades=[]; next_ok=0; cap=CAP0; peak=CAP0; dd=0.0; risk_amt=CAP0*RISK; cur_mo=-1; timeout=96
    for i in range(60, n-1):
        if i<next_ok or not sess[i] or not np.isfinite(a[i]): continue
        mo=pd.Timestamp(ts[i],unit="ms").to_period("M").ordinal
        if mo!=cur_mo: risk_amt=cap*RISK; cur_mo=mo
        b4 = bias4(ts[i]);
        if b4==0 or struct1(ts[i])!=b4: continue        # 4H↔1H deben alinear
        # ¿precio dentro de una zona 1H activa, creada antes, alineada con el sesgo, no llenada aún?
        if len(zarr)==0: continue
        m = (zarr[:,0] < ts[i]) & (zarr[:,1]==b4) & (zarr[:,0] > ts[i]-7*86_400_000)
        if not m.any(): continue
        cand = zarr[m]
        # zona cuya banda contiene el precio actual
        inside = (l[i] <= cand[:,3]) & (h[i] >= cand[:,2])
        if not inside.any(): continue
        z = cand[inside][-1]  # zona más reciente
        zlo, zhi = z[2], z[3]
        if b4==1:   # long en demanda
            engulf = (c[i]>o[i]) and (c[i]>h[i-1]) and ((c[i]-o[i])>0.5*(h[i]-l[i]+1e-9))
            reject = (l[i] - min(o[i],c[i]))  # mecha inferior
            wick = (min(o[i],c[i]) - l[i]) > 0.5*(h[i]-l[i]+1e-9) and c[i]>o[i]
            trig = engulf or wick
            of_ok = (vr[i]>=1.5) and (delta[i]>0)         # pico de volumen + compra (absorción de venta en demanda)
            if not trig or (use_of and not of_ok): continue
            entry=c[i]; stop=zlo-0.20*a[i];
            if stop>=entry: continue
            tp=entry+rr*(entry-stop); side="long"
        else:       # short en oferta
            engulf = (c[i]<o[i]) and (c[i]<l[i-1]) and ((o[i]-c[i])>0.5*(h[i]-l[i]+1e-9))
            wick = (h[i]-max(o[i],c[i])) > 0.5*(h[i]-l[i]+1e-9) and c[i]<o[i]
            trig = engulf or wick
            of_ok = (vr[i]>=1.5) and (delta[i]<0)
            if not trig or (use_of and not of_ok): continue
            entry=c[i]; stop=zhi+0.20*a[i]
            if stop<=entry: continue
            tp=entry-rr*(stop-entry); side="short"
        risk_px=abs(entry-stop)
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
        trades.append({"ts":int(ts[i]),"side":side,"r":r,"oos":int(ts[i])>=OOS_MS,"win":res>0}); next_ok=i+6
    return trades,cap,dd


def report(name,trades,span):
    if not trades: print(f"--- {name} ---\n   sin trades\n"); return
    d=pd.DataFrame(trades); di,do=d[~d.oos],d[d.oos]
    def blk(x,tag,fr):
        if len(x)==0: return f"{tag} n=0"
        return f"{tag} n={len(x):>4} WR={100*x.win.mean():>4.1f}% avgR={x.r.mean():>+.3f} netR={x.r.sum():>+6.1f} {len(x)/(span*fr):>4.2f}tr/d Sharpe~{x.r.mean()/(x.r.std()+1e-9):>+.2f}"
    print(f"--- {name} ---"); print("   "+blk(di,"IS ",0.66)); print("   "+blk(do,"OOS",0.34))


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--rr",type=float,default=2.0); args=ap.parse_args()
    df=pd.read_parquet(M1,columns=["ts_ms","open","high","low","close","volume","delta"]).sort_values("ts_ms")
    df=df[df.ts_ms>=pd.Timestamp("2025-06-19",tz="UTC").value//10**6].reset_index(drop=True)
    span=(df.ts_ms.max()-df.ts_ms.min())/86_400_000
    print(f"TOP-DOWN 4H→1H→5m | zona 1H=FVG | RR {args.rr} | fee {FEE_RT*1e4:.0f}bps | span {span:.0f}d\n")
    for use_of in (False,True):
        tr,cap,dd=run(df,args.rr,use_of)
        report(f"{'B +confirmación orderflow 5m (VR+delta)' if use_of else 'A solo estructura (4H+1H+trigger 5m)'}",tr,span)
        print(f"   $500 -> ${cap:,.0f} | MaxDD {dd*100:.0f}%\n")


if __name__=="__main__":
    main()
