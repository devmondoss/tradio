"""
_filters.py — ¿Qué FEATURE distingue un buen nivel de liquidez de uno malo?
============================================================================
Ablación: cartera base (fade área-valor + POC order-block + POC defendido) vs base + cada
filtro de abstención. Pregunta correcta: NO "¿qué predice dirección?" sino "¿cuándo proveer
liquidez y cuándo abstenerse?". Mide el efecto en OOS (avgR, WR, n, MaxDD).
Uso: python backtest/_filters.py [--tf 5]
"""
import argparse
import numpy as np, pandas as pd
import _listas2 as L2

OOS_MS=L2.OOS_MS; CAP0=L2.CAP0; RISK=L2.RISK

def maxdd(rs):
    cap=CAP0; peak=CAP0; dd=0
    for r in rs: cap+=CAP0*RISK*r; peak=max(peak,cap); dd=max(dd,(peak-cap)/peak)
    return dd*100

def oos_metrics(tr):
    df=pd.DataFrame(tr); do=df[df.ts>=OOS_MS]
    if len(do)==0: return (0,0,0,0)
    return (len(do), 100*(do.r>0).mean(), do.r.mean(), maxdd(do.sort_values("ts").r.values))

def portfolio(a, timeout, filt):
    tr=[]
    for gen in (L2.gen_h5(), L2.gen_h21()):
        tr += L2.run_level(a, gen, timeout, "maker", cooldown=6, max_day=2,
                           min_rr=1.2, fill_margin_bps=2.0, filt=filt)
    return tr

# ---- filtros (cada uno: (a,i,side)->bool, True = tomar el trade) ----
def f_regime_range(a,i,side):  return a.reg[i] in ("Chop","chop","Range","range")
def f_regime_trend(a,i,side):  return a.reg[i] not in ("Chop","chop","Range","range")
def f_obi_support(a,i,side):
    o=a.obi10_mean[i]
    if not np.isfinite(o): return True
    return (o>0.05) if side=="long" else (o<-0.05)      # libro a favor del fade
def f_obi_against(a,i,side):
    o=a.obi10_mean[i]
    if not np.isfinite(o): return True
    return (o<-0.05) if side=="long" else (o>0.05)
def f_absorb(a,i,side):       return a.abs_bid[i] if side=="long" else a.abs_ask[i]
def f_cvd_div(a,i,side):      return bool(a.cvd_div[i])
def f_no_vpin(a,i,side):
    return (not np.isfinite(a.vpin[i])) or a.vpin[i] < 0.6   # evitar flujo tóxico alto

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--tf",type=int,default=5); args=ap.parse_args()
    print(f"Cargando M{args.tf}..."); t=L2.load2(args.tf); a=L2.A2(t); timeout=int(8*60/args.tf)
    # precomputar medianas causales para filtros de nivel/vol
    sp=pd.Series(a.spread_mean); sp_med=sp.rolling(500,min_periods=50).median().shift(1).values
    at=pd.Series(a.atr); at_med=at.rolling(500,min_periods=50).median().shift(1).values
    def f_spread_tight(a,i,side):
        return (not np.isfinite(sp_med[i])) or (a.spread_mean[i] <= sp_med[i])
    def f_low_vol(a,i,side):
        return (not np.isfinite(at_med[i])) or (a.atr[i] <= at_med[i])
    def f_high_vol(a,i,side):
        return np.isfinite(at_med[i]) and (a.atr[i] > at_med[i])
    def f_session_liq(a,i,side):
        hm=(a.ts[i]//60_000)%1440; return (7*60<=hm<21*60)   # London+NY

    FILTERS=[("BASE (sin filtro)",None),
             ("régimen RANGO",f_regime_range),
             ("régimen TENDENCIA",f_regime_trend),
             ("spread estrecho",f_spread_tight),
             ("vol BAJA (atr<med)",f_low_vol),
             ("vol ALTA (atr>med)",f_high_vol),
             ("OBI a favor del fade",f_obi_support),
             ("OBI en contra",f_obi_against),
             ("absorción en nivel",f_absorb),
             ("divergencia CVD",f_cvd_div),
             ("VPIN bajo (no tóxico)",f_no_vpin),
             ("sesión London+NY",f_session_liq)]
    print(f"\n{'filtro':<26} {'n':>5} {'WR':>5} {'avgR':>7} {'MaxDD':>6}   (OOS, cartera POC-OB + POC-defendido)")
    base_n=None
    for name,f in FILTERS:
        n,wr,ar,dd=oos_metrics(portfolio(a,timeout,f))
        if base_n is None: base_n=n
        keep=f"{100*n/base_n:.0f}%" if base_n else "-"
        print(f"{name:<26} {n:>5} {wr:>4.0f}% {ar:>+7.3f} {dd:>5.0f}%  trades={keep}")

if __name__=="__main__": main()
