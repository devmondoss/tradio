"""
_cvd_div.py — Divergencia CVD como PATRON multi-barra, SIN lookahead.
Ventana previa [i-N, i-1] (no incluye barra de entrada).
Divergencia favorable:
  long  -> precio cae (slope<0) pero CVD sube (slope>0)  = acumulacion oculta
  short -> precio sube (slope>0) pero CVD cae  (slope<0)  = distribucion oculta
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _strategy_ab import run_system
from _audit_mirror import gen_h21_short

PARQUETS = {
    "BTCUSDT": Path("data/bybit-perp/processed/btcusdt_perp_m1.parquet"),
    "ETHUSDT": Path("E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet"),
    "SOLUSDT": Path("E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"),
}
TF = 15

def slope(arr, i0, i1):
    y = arr[i0:i1]
    if len(y) < 2: return 0.0
    x = np.arange(len(y))
    return np.polyfit(x, (y - y.mean()) / (y.std() + 1e-9), 1)[0]

def run(sym):
    L2.M1 = PARQUETS[sym].resolve()
    t = L2.load2(TF, start_ms=0); a = L2.A2(t); m1 = L2.load_m1_exit(start_ms=0)
    df = run_system(a, [L2.gen_h5(), L2.gen_h21(), gen_h21_short()], m1, TF,
                    mode="routed", max_day=4, cooldown=3)
    df = df[df.oos].reset_index(drop=True)
    close = np.asarray(a.c, float); cvd = np.asarray(a.cvd, float)
    base = df.r.mean()
    print(f"\n{'='*64}\n  {sym}   baseline OOS: avgR {base:+.3f}  WR {(df.r>0).mean()*100:.0f}%  n {len(df)}\n{'='*64}")
    print(f"  {'ventana':>7} {'DIV-FAVOR (avgR/WR/n)':>22} {'resto':>18} {'div EN-CONTRA':>20}")
    for N in [3, 4, 6, 8]:
        fav = np.zeros(len(df), bool); contra = np.zeros(len(df), bool)
        for k, row in df.iterrows():
            i = int(row.bar); i0 = i - N
            if i0 < 0: continue
            ps = slope(close, i0, i); cs = slope(cvd, i0, i)
            if row.side == "long":
                if ps < 0 and cs > 0: fav[k] = True
                if ps < 0 and cs < 0: contra[k] = True
            else:
                if ps > 0 and cs < 0: fav[k] = True
                if ps > 0 and cs > 0: contra[k] = True
        f, rest, c = df[fav], df[~fav], df[contra]
        def fmt(d): return f"{d.r.mean():+6.3f}/{(d.r>0).mean()*100:3.0f}%/{len(d):>3}"
        print(f"  {N:>5}b  {fmt(f):>22} {fmt(rest):>18} {fmt(c):>20}")

if __name__ == "__main__":
    syms = sys.argv[1:] or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    for s in syms:
        run(s)
    print("\n  Si DIV-FAVOR > baseline en LOS 3 activos -> senal real, generaliza.")
    print("  Si solo en BTC -> ruido de muestra.")
