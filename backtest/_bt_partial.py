"""
_bt_partial.py — ¿parcial 50%@tp1 vs correr la posición ENTERA al target?
==========================================================================
Lo que reveló la reconstrucción tick-a-tick: el binario corre la posición entera y eso
da más avgR. Acá lo testeo en los 365d, 3 activos, IS/OOS, regla dura (A+B routeado, M15).
use_partial=True (spec actual) vs False (entera). HIGH_VOL_ONLY=on (atr_mult=1.0).
"""
import sys; from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, "backtest")
import _listas2 as L2
from _strategy_ab import run_system, stats
from _audit_mirror import gen_h21_short

PARQ = {"BTCUSDT": "data/bybit-perp/processed/btcusdt_perp_m1.parquet",
        "ETHUSDT": "E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet",
        "SOLUSDT": "E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"}
TF = 15

def line(tag, st):
    print(f"  {tag:<26} n={st['n']:>4} WR{st['wr']:4.0f}% avgR{st['avgR']:+.3f} "
          f"OOS avgR{st['oosA']:+.3f} netR{st['oosN']:+6.1f} DD{st['dd']:4.1f}% Sh{st['sharpe']:+.1f}")

def main():
    res = {}
    for sym in ["BTCUSDT","ETHUSDT","SOLUSDT"]:
        if not Path(PARQ[sym]).exists(): print("SKIP",sym); continue
        L2.M1 = PARQ[sym]
        a = L2.A2(L2.load2(TF, start_ms=L2.TICK_MS))
        m1 = L2.load_m1_exit(start_ms=L2.TICK_MS)
        gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]
        print(f"\n=== {sym} · M{TF} · A+B routed · high_vol ===")
        cp = run_system(a, gens, m1, TF, mode="routed", atr_mult=1.0, use_partial=True)
        ce = run_system(a, gens, m1, TF, mode="routed", atr_mult=1.0, use_partial=False)
        line("parcial 50%@tp1 (actual)", stats(cp))
        line("posición ENTERA (no parc)", stats(ce))
        # solo fades (donde el cambio aplica)
        fp = cp[cp.gestion=="fade"]; fe = ce[ce.gestion=="fade"]
        print(f"    [solo fades] parcial: avgR{fp.r.mean():+.3f} WR{100*(fp.r>0).mean():.0f}% OOS{fp[fp.oos].r.mean():+.3f}"
              f"  |  entera: avgR{fe.r.mean():+.3f} WR{100*(fe.r>0).mean():.0f}% OOS{fe[fe.oos].r.mean():+.3f}")
        res[sym] = (stats(cp), stats(ce))

    print("\n" + "="*70)
    print("VEREDICTO regla dura (OOS avgR debe mejorar en LOS 3):")
    allbetter = True
    for sym,(p,e) in res.items():
        d = e['oosA']-p['oosA']
        better = d>0
        allbetter &= better
        print(f"  {sym}: parcial OOS {p['oosA']:+.3f} -> entera {e['oosA']:+.3f}  ({d:+.3f}) {'MEJORA' if better else 'PEOR'}")
    print(f"\n  >>> Correr ENTERA {'PASA' if allbetter else 'NO PASA'} la regla dura")
    print("  (ref M15 OOS A+B parcial: BTC +1.82 · ETH +1.37 · SOL +0.93)")

if __name__=="__main__": main()
