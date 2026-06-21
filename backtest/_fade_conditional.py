"""
_fade_conditional.py — ¿el ORDERFLOW separa los fades buenos de los malos? (test de la tesis, fee-independiente)
================================================================================================================
Aísla la pregunta del usuario: el edge base es estructural (fade de extremo de rango); el orderflow es
CONFIRMACIÓN. ¿Mejora la probabilidad de que el fade funcione?

En cada TOQUE de extremo de rango (M5, causal), etiqueta el outcome binario:
  WIN  = el precio alcanza el extremo OPUESTO antes de aceptar BREAK_BPS más allá del extremo (timeout 4h)
  LOSS = rompe el extremo (acepta fuera) antes de rotar
Luego reporta P(WIN) base y CONDICIONADA a cada señal de orderflow, IS y OOS. Si el orderflow sirve,
P(WIN | señal) debe superar a P(WIN) base de forma consistente IS↔OOS. Sin fee, sin stop: pura señal.

Uso: python backtest/_fade_conditional.py
"""
import os
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
M1 = ROOT / "data/bybit-perp/processed/btcusdt_perp_m1.parquet"
OOS_MS = int(pd.Timestamp("2026-03-01", tz="UTC").value // 1_000_000)
W, TOL, BREAK_BPS, TIMEOUT = 20, 0.25, 20.0, 48


def atr(h, l, c, n=14):
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    out = np.full(len(tr), np.nan); out[n-1] = np.nanmean(tr[:n]); k = 1.0/n
    for i in range(n, len(tr)): out[i] = out[i-1]*(1-k) + tr[i]*k
    return out


def main():
    df = pd.read_parquet(M1, columns=["ts_ms","open","high","low","close","volume","delta","cvd"]).sort_values("ts_ms")
    df = df[df.ts_ms >= pd.Timestamp("2025-06-19", tz="UTC").value//10**6].reset_index(drop=True)
    g = (df.ts_ms.values//300_000)*300_000
    b = pd.DataFrame({"g":g,"o":df.open.values,"h":df.high.values,"l":df.low.values,
                      "c":df.close.values,"v":df.volume.values,"d":df.delta.values,"cv":df.cvd.values})
    a = b.groupby("g").agg(open=("o","first"),high=("h","max"),low=("l","min"),close=("c","last"),
                           volume=("v","sum"),delta=("d","sum"),cvd=("cv","last")).reset_index().rename(columns={"g":"ts_ms"})
    ts=a.ts_ms.values.astype(np.int64); o,h,l,c=(a[x].values.astype(float) for x in("open","high","low","close"))
    vol,delta,cvd=a.volume.values.astype(float),a.delta.values.astype(float),a.cvd.values.astype(float)
    n=len(a); at=atr(h,l,c); S=pd.Series
    rhigh=S(h).rolling(W).max().shift(1).values; rlow=S(l).rolling(W).min().shift(1).values
    rsize=rhigh-rlow; drift=np.abs(c-S(c).shift(W).values)
    vsma=S(vol).rolling(50).mean().shift(1).values
    dz=(delta-S(delta).rolling(50).mean().shift(1).values)/(S(delta).rolling(50).std().shift(1).values+1e-9)
    vr=vol/(vsma+1e-9)
    cvd_lo=S(cvd).rolling(W).min().shift(1).values; cvd_hi=S(cvd).rolling(W).max().shift(1).values
    hm=(ts//60_000)%1440; sess=((hm>=7*60)&(hm<10*60))|((hm>=13*60)&(hm<17*60))
    range_ok=np.isfinite(at)&(rsize>=0.8*at)&(rsize<=3.5*at)&(drift<0.5*rsize)&sess

    rows=[]
    for i in range(W+50,n-1):
        if not range_ok[i]: continue
        for side in("long","short"):
            if side=="long":
                if not (l[i]<=rlow[i]+TOL*at[i]): continue
                lvl=rlow[i]; opp=rhigh[i]; brk=lvl-BREAK_BPS/1e4*c[i]
                absorb=(dz[i]<=-1.0)&(vr[i]>=1.5)&(c[i]>rlow[i])
                bigflip=dz[i]<=-1.5
                cvddiv=(l[i]<=cvd_lo[i]) and False  # placeholder, precio low vs cvd abajo
                cvddiv=(c[i]<=rlow[i]+TOL*at[i]) and (cvd[i]>=cvd_lo[i])  # precio en low pero CVD no hace nuevo low
            else:
                if not (h[i]>=rhigh[i]-TOL*at[i]): continue
                lvl=rhigh[i]; opp=rlow[i]; brk=lvl+BREAK_BPS/1e4*c[i]
                absorb=(dz[i]>=1.0)&(vr[i]>=1.5)&(c[i]<rhigh[i])
                bigflip=dz[i]>=1.5
                cvddiv=(c[i]>=rhigh[i]-TOL*at[i]) and (cvd[i]<=cvd_hi[i])
            win=None
            for j in range(i+1,min(i+1+TIMEOUT,n)):
                if side=="long":
                    if c[j]<=brk: win=0; break
                    if h[j]>=opp: win=1; break
                else:
                    if c[j]>=brk: win=0; break
                    if l[j]<=opp: win=1; break
            if win is None: continue  # ni rotó ni rompió en el timeout → descartar (no decidió)
            rows.append((int(ts[i]),side,int(win),bool(absorb),bool(bigflip),bool(cvddiv),
                         bool(vr[i]>=2.0),bool(abs(dz[i])>=2.0)))
    e=pd.DataFrame(rows,columns=["ts","side","win","absorb","bigflip","cvddiv","vr2","dz2"])
    e["oos"]=e.ts>=OOS_MS
    ei,eo=e[~e.oos],e[e.oos]
    def pw(d):
        return (len(d),100*d.win.mean()) if len(d) else (0,float('nan'))
    print(f"Toques de extremo decididos: {len(e):,} | IS {len(ei):,} / OOS {len(eo):,}")
    print(f"WIN = alcanza extremo opuesto antes de aceptar {BREAK_BPS:.0f}bps fuera (timeout 4h). Sin fee.\n")
    n_i,b_i=pw(ei); n_o,b_o=pw(eo)
    print(f'{"condición":<26} {"n_is":>6} {"WR_is":>6} {"n_oos":>6} {"WR_oos":>7}  Δvs-base(IS/OOS)')
    print("-"*78)
    print(f'{"BASE (todos los toques)":<26} {n_i:>6} {b_i:>5.1f}% {n_o:>6} {b_o:>6.1f}%   —')
    for col,lbl in [("absorb","absorción (DZ<-1,VR>1.5,reclaim)"),("bigflip","delta fuerte (|DZ|>=1.5)"),
                    ("cvddiv","CVD divergencia"),("vr2","VR>=2"),("dz2","|DZ|>=2")]:
        di,do=ei[ei[col]],eo[eo[col]]
        ni,wi=pw(di); no,wo=pw(do)
        print(f'{lbl:<26} {ni:>6} {wi:>5.1f}% {no:>6} {wo:>6.1f}%   {wi-b_i:>+5.1f}/{wo-b_o:>+5.1f}')
    # combos contra: ausencia de señal
    print("\n(Si el orderflow confirma el edge, Δ debe ser POSITIVO y consistente IS↔OOS.)")


if __name__ == "__main__":
    main()
