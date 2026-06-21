"""
_consolidated.py — Estrategia unificada LIQUIDITY PROVISION (H1 + H5 + H21)
==========================================================================
Las 3 ganadoras del catálogo son la misma idea: órdenes LÍMITE MAKER en niveles de volumen/valor.
  H1  = fade del área-valor del día previo (VAH/VAL/POC) — V1+V3, open_hour=12
  H5  = límite en el POC del Order Block previo
  H21 = límite en un POC defendido repetidamente
Se agrupan a nivel de CARTERA (riesgo fijo $500@1%) y se reporta el conjunto. Cada señal entra
sólo si no hay otra del mismo nivel activa (dedup por cooldown global). Maker 4bps, salida H17
(tape) opcional, stress de selección adversa (margen al atravesar).

Supuesto de cartera: riesgo fijo por trade; concurrencia limitada por cooldown global (proxy de
"una posición a la vez por nivel"). Métricas: equity, MaxDD, Sharpe, por trimestre/mes, IS/OOS.

Uso:  python backtest/_consolidated.py [--tf 5] [--margin 2] [--tape] [--capN 2]
"""
import argparse
from pathlib import Path
import numpy as np, pandas as pd
import _listas as L1
import _listas2 as L2

OOS_MS = L1.OOS_MS; CAP0, RISK = L1.CAP0, L1.RISK; FEE_MAKER = L1.FEE_MAKER

def h1_trades(tf, margin, volfilter):
    """Fade del área-valor (config fijada: bordes/POC del día previo, maker-límite, stop=max)."""
    full0=int(pd.Timestamp('2025-01-01',tz='UTC').value//1_000_000)
    t=L1.load(tf, open_hour=12, start_ms=full0); a=L1.A(t); timeout=int(8*60/tf)
    tr=L1.h1(a, FEE_MAKER, timeout, stop_mode="max", trigger="close", min_rr=1.5,
             sub1b=False, entry_mode="maker_limit", variants=("V1","V3"),
             fill_margin_bps=margin, vol_high_only=volfilter)
    for x in tr: x["src"]="area_valor"
    return tr

def h5_h21_trades(tf, margin, capN, tape, volfilter):
    """POC del order block + POC defendido (maker-límite)."""
    full0=int(pd.Timestamp('2025-01-01',tz='UTC').value//1_000_000)
    t=L2.load2(tf, start_ms=full0); a=L2.A2(t); timeout=int(8*60/tf)
    # filtro de volatilidad causal (ATR > mediana móvil 500)
    atr_med = pd.Series(a.atr).rolling(500, min_periods=50).median().shift(1).values
    f_vol = (lambda a,i,side: bool(np.isfinite(atr_med[i]) and a.atr[i]>atr_med[i])) if volfilter else None
    out=[]
    for src,gen in (("poc_orderblock",L2.gen_h5()),("poc_defendido",L2.gen_h21())):
        tr=L2.run_level(a, gen, timeout, "maker", cooldown=6, max_day=capN,
                        min_rr=1.2, fill_margin_bps=margin, exit_tape=tape, filt=f_vol)
        for x in tr: x["src"]=src
        out+=tr
    return out

def metrics(d, tag):
    if len(d)==0: print(f"{tag}: n=0"); return
    cap=CAP0; peak=CAP0; dd=0.0
    for r in d.r.values:
        cap += CAP0*RISK*r; peak=max(peak,cap); dd=max(dd,(peak-cap)/peak)
    days=(d.ts.max()-d.ts.min())/86_400_000
    shp=d.r.mean()/(d.r.std()+1e-9)*np.sqrt(len(d)/max(days/365*252,1e-9))
    print(f"{tag}: n={len(d):>4} días={(d.ts//86_400_000).nunique():>3} WR={100*(d.r>0).mean():>4.1f}% "
          f"avgR={d.r.mean():>+.3f} netR={d.r.sum():>+6.1f} $500->${cap:>6.0f} MaxDD={dd*100:>4.1f}% "
          f"Sharpe={shp:>+4.2f} {len(d)/max(days,1):.2f}tr/d")

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--tf",type=int,default=5)
    ap.add_argument("--margin",type=float,default=2.0,help="bps selección adversa")
    ap.add_argument("--capN",type=int,default=2,help="max trades/día por sub-estrategia")
    ap.add_argument("--tape",action="store_true",help="salida dinámica H17")
    ap.add_argument("--volfilter",action="store_true",help="solo operar con ATR>mediana (vol alta)")
    args=ap.parse_args()
    print(f"Construyendo cartera consolidada (tf=M{args.tf}, margen adverso {args.margin:.0f}bps, "
          f"cap {args.capN}/d, salida {'tape' if args.tape else 'TP/stop'}, "
          f"filtro vol={'ON' if args.volfilter else 'OFF'})...\n")
    tr = h1_trades(args.tf, args.margin, args.volfilter) + \
         h5_h21_trades(args.tf, args.margin, args.capN, args.tape, args.volfilter)
    df=pd.DataFrame(tr).sort_values("ts").reset_index(drop=True)
    print("#### CARTERA CONSOLIDADA — H1+H5+H21 (liquidity provision maker) ####\n")
    metrics(df, "TODO 2025-01+")
    metrics(df[df.ts<OOS_MS], "IS  <2026-03")
    metrics(df[df.ts>=OOS_MS], "OOS >=2026-03")
    print("\n  por fuente (OOS):")
    do=df[df.ts>=OOS_MS]
    for s in ("area_valor","poc_orderblock","poc_defendido"):
        sub=do[do.src==s]
        if len(sub): print(f"    {s:<16}: n={len(sub):>3} avgR={sub.r.mean():+.3f} WR={100*(sub.r>0).mean():.0f}%")
    print("\n  por trimestre (toda la cartera):")
    df["q"]=pd.to_datetime(df.ts,unit='ms',utc=True).dt.to_period('Q').astype(str)
    for q,sub in df.groupby("q"):
        cap=CAP0+sum(CAP0*RISK*sub.r);
        print(f"    {q}: n={len(sub):>3} avgR={sub.r.mean():+.3f} netR={sub.r.sum():>+6.1f}")

if __name__=="__main__": main()
