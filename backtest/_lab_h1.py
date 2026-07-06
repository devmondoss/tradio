"""
_lab_h1.py — estrategia en H1: zonas de volumen (POC) + FVG. ¿H1 es más sólido?
==============================================================================
Mismo motor A+B, pero TF=60 (H1) en vez de M15. Prueba: niveles de volumen
(gen_h5/h21/mirror) base, +FVG, y FVG solo — en los 3 activos, IS/OOS, regla dura.
Salidas siempre simuladas en M1 (honesto). Compara contra el M15 conocido.
Correr: python backtest/_lab_h1.py
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
OOS_MS = __import__("_listas").OOS_MS


def gen_fvg(W=20, buf=0.25, min_gap=0.1):
    """FVG de 3 velas sin rellenar (en H1 = huecos más grandes/sólidos)."""
    def g(a, i):
        atr = a.atr[i]
        if atr <= 0 or i < 4: return []
        ref = a.c[i-1]
        for k in range(i-1, max(2, i-W)-1, -1):
            gb, gt = a.h[k-2], a.l[k]
            if gt - gb > min_gap*atr:
                seg = a.l[k+1:i]
                if (seg.size == 0 or np.nanmin(seg) > gt) and a.l[i] <= gt and gt < ref:
                    tp1, tp2 = L2.struct_target(a, i, "long", gt)
                    if np.isfinite(tp2) and gb-buf*atr < gt < tp2:
                        return [("long", gt, gb-buf*atr, tp1, tp2, "fvg")]
            gt2, gb2 = a.l[k-2], a.h[k]
            if gt2 - gb2 > min_gap*atr:
                seg = a.h[k+1:i]
                if (seg.size == 0 or np.nanmax(seg) < gb2) and a.h[i] >= gb2 and gb2 > ref:
                    tp1, tp2 = L2.struct_target(a, i, "short", gb2)
                    if np.isfinite(tp2) and tp2 < gb2 < gt2+buf*atr:
                        return [("short", gb2, gt2+buf*atr, tp1, tp2, "fvg")]
        return []
    return g


def line(tag, st):
    print(f"  {tag:<22} n={st['n']:>4} WR{st['wr']:4.0f}% avgR{st['avgR']:+.3f} "
          f"OOS n={st['n']and len([1])and '':<0}avgR{st['oosA']:+.3f} netR{st['oosN']:+6.1f} DD{st['dd']:4.1f}% Sh{st['sharpe']:+.1f}")


def run_tf(symbol, tf):
    L2.M1 = PARQ[symbol]
    a = L2.A2(L2.load2(tf, start_ms=L2.TICK_MS))
    m1 = L2.load_m1_exit(start_ms=L2.TICK_MS)
    base = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]
    print(f"\n=== {symbol} · H{tf//60 if tf>=60 else 0 or tf}min (TF={tf}) ===")
    b = run_system(a, base, m1, tf, mode="routed")
    line("volumen base", stats(b))
    c = run_system(a, base + [gen_fvg()], m1, tf, mode="routed")
    line("volumen + FVG", stats(c))
    f = run_system(a, [gen_fvg()], m1, tf, mode="routed")
    line("FVG solo", stats(f))


def main():
    for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
        if not Path(PARQ[sym]).exists(): print("SKIP", sym); continue
        run_tf(sym, 60)   # H1
    print("\n(ref M15 OOS A+B: BTC +1.82 · ETH +1.37 · SOL +0.93)")


if __name__ == "__main__":
    main()
