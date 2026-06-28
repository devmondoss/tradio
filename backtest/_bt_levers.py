"""
_bt_levers.py — diagnóstico de puntos débiles + sweep de palancas ESTRUCTURALES (anti-overfit)
==============================================================================================
Optimiza solo en histórico (parquets hasta 06-19/22), deja el paper reciente como forward.
Regla dura: una palanca solo cuenta si mejora OOS avgR en LOS 3 activos sin disparar el DD.
Mira métricas OOS (no IS). Cada palanca se barre 1×1 contra la base.
"""
import sys; from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, "backtest")
import _listas2 as L2
from _strategy_ab import run_system
from _audit_mirror import gen_h21_short
from _listas import FEE_MAKER, FEE_TAKER

PARQ = {"BTCUSDT": "data/bybit-perp/processed/btcusdt_perp_m1.parquet",
        "ETHUSDT": "E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet",
        "SOLUSDT": "E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"}
TF = 15
MK, TK = FEE_MAKER/2, FEE_TAKER/2

def summ(df):
    o = df[df.oos]; i = df[~df.oos]
    def dd(d):
        cap=500.0;peak=500.0;mdd=0.0
        for r in d.sort_values("ts").r.values:
            cap+=5*r;peak=max(peak,cap);mdd=max(mdd,(peak-cap)/peak)
        return 100*mdd
    def sh(d): return d.r.mean()/(d.r.std()+1e-9)*np.sqrt(len(d)) if len(d)>1 else 0
    return dict(n=len(df), oosn=len(o),
                isA=i.r.mean() if len(i) else 0, oosA=o.r.mean() if len(o) else 0,
                oosN=o.r.sum() if len(o) else 0, wr=100*(o.r>0).mean() if len(o) else 0,
                dd=dd(o) if len(o) else 0, sh=sh(o) if len(o) else 0)

# cargar cada activo una vez
DATA={}
for sym in ["BTCUSDT","ETHUSDT","SOLUSDT"]:
    if not Path(PARQ[sym]).exists(): print("SKIP",sym); continue
    L2.M1 = PARQ[sym]
    a = L2.A2(L2.load2(TF, start_ms=L2.TICK_MS))
    m1 = L2.load_m1_exit(start_ms=L2.TICK_MS)
    DATA[sym] = (a, m1, [L2.gen_h5(), L2.gen_h21(), gen_h21_short()])
    print(f"cargado {sym}", file=sys.stderr)

# ---- BASE ----
BASE={}
for sym,(a,m1,gens) in DATA.items():
    df=run_system(a,gens,m1,TF,mode="routed",atr_mult=1.0)
    BASE[sym]=(df,summ(df))

print("\n"+"="*94)
print("DIAGNÓSTICO BASE (A+B routed, high_vol, M15) — métricas OOS")
print("="*94)
print(f"  {'sym':<8}{'n':>5}{'OOSn':>6}{'IS avgR':>9}{'OOS avgR':>10}{'gap':>7}{'WR':>6}{'DD%':>7}{'Sh':>6}")
for sym,(df,s) in BASE.items():
    print(f"  {sym:<8}{s['n']:>5}{s['oosn']:>6}{s['isA']:>+9.3f}{s['oosA']:>+10.3f}{s['isA']-s['oosA']:>+7.3f}{s['wr']:>5.0f}%{s['dd']:>6.1f}%{s['sh']:>+6.1f}")

print("\n  Fuga por fees y desglose por reason (OOS):")
for sym,(df,s) in BASE.items():
    o=df[df.oos].copy(); o["feeR"]=(MK+TK)*o.entry/o.risk
    by=o.groupby("reason").agg(n=("r","size"),avgR=("r","mean")).round(3)
    print(f"   {sym}: feeR medio={o.feeR.mean():.3f}  | "+" ".join(f"{k}:n{v.n}/avgR{v.avgR:+.2f}" for k,v in by.iterrows()))

# ---- SWEEP de palancas ----
LEVERS = {
  "stop_scale":  [0.6, 0.7, 0.8, 0.9],
  "trail_atr":   [5.0, 6.0, 7.0, 8.0],
  "atr_mult":    [1.2, 1.5],
  "timeout_min": [48*60, 72*60],
}
def verdict(res):
    """mejora OOS avgR en los 3 vs base, y DD no empeora >2pp en ninguno."""
    okA = all(res[s]['oosA'] > BASE[s][1]['oosA'] for s in res)
    okDD = all(res[s]['dd'] <= BASE[s][1]['dd']+2.0 for s in res)
    return okA, okDD

print("\n"+"="*94)
print("SWEEP DE PALANCAS (Δ vs base en OOS avgR; regla dura = mejora en los 3)")
print("="*94)
for lever, vals in LEVERS.items():
    print(f"\n[{lever}]  base OOS: "+" ".join(f"{s[:3]} {BASE[s][1]['oosA']:+.2f}" for s in BASE))
    for v in vals:
        res={}
        for sym,(a,m1,gens) in DATA.items():
            kw={"atr_mult":1.0}; kw[lever]=v
            df=run_system(a,gens,m1,TF,mode="routed",**kw)
            res[sym]=summ(df)
        okA,okDD=verdict(res)
        deltas=" ".join(f"{s[:3]} {res[s]['oosA']:+.2f}({res[s]['oosA']-BASE[s][1]['oosA']:+.2f})" for s in res)
        dds=" ".join(f"{s[:3]}DD{res[s]['dd']:.0f}" for s in res)
        tag = "✅ PASA" if (okA and okDD) else ("~ mejora avgR pero DD" if okA else "❌")
        print(f"  {lever}={v:<7} {deltas}  | {dds}  {tag}")

# ---- COMBINACIONES de ganadoras + walk-forward de estabilidad ----
print("\n"+"="*94)
print("COMBINACIONES (stop_scale + trail_atr) y estabilidad walk-forward (4 cuartos IS)")
print("="*94)
COMBOS = [
    dict(stop_scale=0.8, trail_atr=6.0),
    dict(stop_scale=0.8, trail_atr=8.0),
    dict(stop_scale=0.7, trail_atr=6.0),
]
def wf(df):
    """avgR por cuarto cronológico de TODO el periodo (estabilidad)."""
    d=df.sort_values("ts"); q=np.array_split(d,4)
    return [round(x.r.mean(),2) for x in q]
for cfg in COMBOS:
    print(f"\n  combo {cfg}:")
    allok=True
    for sym,(a,m1,gens) in DATA.items():
        kw={"atr_mult":1.0,**cfg}
        df=run_system(a,gens,m1,TF,mode="routed",**kw); s=summ(df)
        b=BASE[sym][1]
        ok = s['oosA']>b['oosA'] and s['dd']<=b['dd']+2.0
        allok&=ok
        print(f"    {sym:<8} OOS {s['oosA']:+.3f} (base {b['oosA']:+.3f}, {s['oosA']-b['oosA']:+.3f}) "
              f"DD{s['dd']:.0f}% Sh{s['sh']:+.1f} WF{wf(df)} {'✅' if ok else '❌'}")
    print(f"    -> {'PASA en los 3' if allok else 'NO pasa'}")

print("\n(base ref OOS A+B: BTC +1.82 · ETH +1.37 · SOL +0.93)")
