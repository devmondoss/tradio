"""
_sol_regime_fix.py — Diagnostica el gap IS→OOS de SOL y prueba detectores de régimen alternativos.

Hipótesis: SOL tiene 14% trend bars vs 5% BTC → trail routing con alta varianza → gap IS→OOS.
Test:
  A) fade-only           — elimina trail completamente
  B) expansion-only trail — trail solo en Expansion (1.3%), fade en TrendUp/TrendDown
  C) routed original     — baseline actual
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _listas import OOS_MS, FEE_MAKER, FEE_TAKER
from _audit_mirror import gen_h21_short
from _strategy_ab import run_system, stats, is_chop

L2.M1 = Path("E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet")
TF = 15

print("Cargando SOL...")
t  = L2.load2(TF, start_ms=0)
a  = L2.A2(t)
m1 = L2.load_m1_exit(start_ms=0)
gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]

n_days = (a.ts[-1] - a.ts[0]) / 86_400_000
print(f"SOL M{TF}: {a.n:,} barras | {pd.Timestamp(a.ts[0],unit='ms').date()} -> {pd.Timestamp(a.ts[-1],unit='ms').date()} ({n_days:.0f}d)")

# Distribución de régimen
reg_series = pd.Series(a.reg)
print("\nDist. régimen:")
vc = reg_series.value_counts()
for k, v in vc.items():
    print(f"  {k:<15} {v:>6}  ({v/len(reg_series)*100:.1f}%)")

# Split IS/OOS
is_mask  = a.ts < OOS_MS
oos_mask = a.ts >= OOS_MS
print(f"\nIS:  {is_mask.sum()} barras  OOS: {oos_mask.sum()} barras")

# ── Opción B: trail solo para Expansion ─────────────────────────────────────
def is_chop_strict(reg):
    """Trail SOLO en Expansion. TrendUp/TrendDown → fade también."""
    return str(reg).lower() not in ("expansion",)

chop_strict = np.array([is_chop_strict(r) for r in a.reg])

# ── Tests ────────────────────────────────────────────────────────────────────
configs = [
    ("routed (original)",       "routed",  None),
    ("fade-only",               "fade",    None),
    ("expansion-only trail",    "routed",  chop_strict),
    ("trail-only",              "trail",   None),
]

print(f"\n{'Config':<28} {'IS avgR':>8} {'OOS avgR':>9} {'OOS WR':>7} {'DD%':>6} {'Sharpe':>7} {'n_IS':>6} {'n_OOS':>6} {'gap':>7}")
print("-"*95)

for label, mode, mask in configs:
    df = run_system(a, gens, m1, TF, mode=mode, chop_mask=mask)
    if df.empty:
        print(f"  {label:<26} — sin trades")
        continue
    s = stats(df)
    is_avg  = df[~df.oos].r.mean() if len(df[~df.oos]) else 0
    n_is    = len(df[~df.oos])
    n_oos   = len(df[df.oos])
    gap     = s['oosA'] - is_avg
    print(f"  {label:<26} {is_avg:>+8.3f} {s['oosA']:>+9.3f} {s['wr']:>6.0f}% {s['dd']:>5.1f}% {s['sharpe']:>+7.1f} {n_is:>6} {n_oos:>6} {gap:>+7.3f}")

# ── Desglose trail vs fade por periodo ──────────────────────────────────────
print("\n── Desglose routed por gestion ──")
df_r = run_system(a, gens, m1, TF, mode="routed")
for oos_flag, label in [(False, "IS"), (True, "OOS")]:
    sub = df_r[df_r.oos == oos_flag]
    for g in ["fade", "trail"]:
        sg = sub[sub.gestion == g]
        if len(sg):
            print(f"  {label} {g:<6}: n={len(sg):>4}  avgR={sg.r.mean():>+.3f}  WR={sg.r.gt(0).mean():>4.0%}")

print("\n── Trail routes: % barras IS vs OOS ──")
is_regs  = pd.Series(a.reg[is_mask])
oos_regs = pd.Series(a.reg[oos_mask])
for period, series in [("IS", is_regs), ("OOS", oos_regs)]:
    trend_pct = (~series.apply(is_chop)).mean() * 100
    print(f"  {period}: {trend_pct:.1f}% barras clasificadas como trend")
