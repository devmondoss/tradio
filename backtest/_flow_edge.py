"""
_flow_edge.py — ¿El footprint/CVD/delta a M15 separa winners de losers?
Mismo metodo con que matamos el OBI, pero a horizonte M15 (donde la fisica cierra).
Features de flujo leidas en la barra de senal (bar=i) del backtest.
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))

import _listas2 as L2
from _strategy_ab import run_system
from _audit_mirror import gen_h21_short

L2.M1 = Path("data/bybit-perp/processed/btcusdt_perp_m1.parquet").resolve()
TF = 15
t = L2.load2(TF, start_ms=0); a = L2.A2(t); m1 = L2.load_m1_exit(start_ms=0)
df = run_system(a, [L2.gen_h5(), L2.gen_h21(), gen_h21_short()], m1, TF,
                mode="routed", max_day=4, cooldown=3)
df = df[df.oos].reset_index(drop=True)   # OOS
LAG = int(sys.argv[1]) if len(sys.argv) > 1 else 0   # 0=barra entrada, 1=barra previa (sin lookahead)
b = df.bar.values - LAG
sign = np.where(df.side.values == "long", 1.0, -1.0)
print(f"### LAG={LAG} ({'barra de entrada (con posible lookahead intrabar)' if LAG==0 else 'barra PREVIA — sin lookahead, accionable'})\n")

# --- features alineadas al lado del trade ---
def col(name): return np.asarray(getattr(a, name), float)[b]
feat = {
    "delta_aligned":     sign * col("delta"),
    "dz_aligned":        sign * col("dz"),
    "cvd_slope_aligned": sign * col("cvd_slope"),
    "vr (volumen)":      col("vr"),
}
# absorcion como soporte/resistencia a favor del lado
ab_sell = np.asarray(a.fp_absorb_sell, bool)[b]   # vendedores absorbidos -> bullish
ab_buy  = np.asarray(a.fp_absorb_buy, bool)[b]     # compradores absorbidos -> bearish
absorb_fav = np.where(df.side.values == "long", ab_sell, ab_buy)
cvd_div = np.asarray(a.cvd_div, bool)[b]

r = df.r.values
w = r > 0

print(f"BTC OOS trades: {len(df)}  | winners {w.sum()} losers {(~w).sum()}\n")
print("== Separacion winner vs loser (dif~0 => no informa) ==")
print(f"  {'feature':<20} {'win mean':>10} {'lose mean':>10} {'dif':>9} {'corr con R':>11}")
for name, x in feat.items():
    x = np.nan_to_num(x)
    dif = x[w].mean() - x[~w].mean()
    cc = np.corrcoef(x, r)[0, 1]
    print(f"  {name:<20} {x[w].mean():>+10.4f} {x[~w].mean():>+10.4f} {dif:>+9.4f} {cc:>+11.4f}")

print("\n== Flags booleanos: avgR con flag ON vs OFF ==")
for name, mask in [("absorcion a favor", absorb_fav), ("cvd_div", cvd_div)]:
    on, off = df[mask], df[~mask]
    print(f"  {name:<18} ON : avgR {on.r.mean():>+7.3f} WR {(on.r>0).mean()*100:>3.0f}% n {len(on):>4}   "
          f"OFF: avgR {off.r.mean():>+7.3f} WR {(off.r>0).mean()*100:>3.0f}% n {len(off):>4}")

print("\n== Filtro continuo: avgR si feature a favor (>0) vs en contra (<=0) ==")
for name, x in feat.items():
    if "volumen" in name: continue
    x = np.nan_to_num(x)
    fav, unf = df[x > 0], df[x <= 0]
    print(f"  {name:<20} FAVOR: avgR {fav.r.mean():>+7.3f} WR {(fav.r>0).mean()*100:>3.0f}% n {len(fav):>4}   "
          f"CONTRA: avgR {unf.r.mean():>+7.3f} n {len(unf):>4}")
print(f"\n  baseline TODOS: avgR {df.r.mean():+.3f}  WR {(df.r>0).mean()*100:.0f}%  n {len(df)}")
