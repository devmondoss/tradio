"""Diagnóstico SOL: ¿dónde se pierde el edge IS->OOS?"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _listas import OOS_MS
from _audit_mirror import gen_h21_short
from _strategy_ab import run_system

L2.M1 = Path("E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet")
TF = 15

t  = L2.load2(TF, start_ms=0)
a  = L2.A2(t)
m1 = L2.load_m1_exit(start_ms=0)
gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]

df = run_system(a, gens, m1, TF, mode="routed", max_day=4, cooldown=3)
i  = df[~df.oos]
o  = df[df.oos]

oos_start = pd.Timestamp(OOS_MS, unit='ms').date()
oos_end   = pd.Timestamp(o.ts.max(), unit='ms').date()
is_start  = pd.Timestamp(i.ts.min(), unit='ms').date()
is_end    = pd.Timestamp(i.ts.max(), unit='ms').date()

print(f"IS:  {is_start} -> {is_end}  ({len(i)} trades, avgR {i.r.mean():+.3f})")
print(f"OOS: {oos_start} -> {oos_end}  ({len(o)} trades, avgR {o.r.mean():+.3f})")

# Desglose por gestion
print("\n--- Por gestion ---")
for period, sub, lbl in [(i, i, "IS"), (o, o, "OOS")]:
    for g in ["fade", "trail"]:
        sg = sub[sub.gestion == g]
        if len(sg):
            print(f"  {lbl} {g:<6}: n={len(sg):>3}  avgR={sg.r.mean():>+.3f}  WR={sg.r.gt(0).mean():.0%}")

# Desglose por kind (generador)
print("\n--- Por generador (OOS) ---")
for k in o.kind.unique() if "kind" in o.columns else []:
    sk = o[o.kind == k]
    print(f"  {k:<20}: n={len(sk):>3}  avgR={sk.r.mean():>+.3f}  WR={sk.r.gt(0).mean():.0%}")

# Evolucion mensual OOS
print("\n--- Mensual OOS ---")
o2 = o.copy()
o2["month"] = pd.to_datetime(o2.ts, unit="ms").dt.to_period("M")
mo = o2.groupby("month").agg(n=("r","count"), avgR=("r","mean"), wr=("r", lambda x: (x>0).mean()*100)).reset_index()
for _, row in mo.iterrows():
    bar = "#" * int(max(0, row.avgR * 10))
    print(f"  {row.month}  n={int(row.n):>3}  avgR={row.avgR:>+.3f}  WR={row.wr:>4.0f}%  {bar}")

# Regimen IS vs OOS
print("\n--- Regimen barras IS vs OOS ---")
reg_i = pd.Series(a.reg[a.ts < OOS_MS])
reg_o = pd.Series(a.reg[a.ts >= OOS_MS])
for lbl, reg in [("IS", reg_i), ("OOS", reg_o)]:
    vc = reg.value_counts(normalize=True)
    trend_pct = (1 - vc.get("Chop", 0)) * 100
    print(f"  {lbl}: Chop={vc.get('Chop',0):.1%}  Trend/Exp={trend_pct:.1f}%  "
          f"(TrendUp={vc.get('TrendUp',0):.1%} TrendDown={vc.get('TrendDown',0):.1%} Exp={vc.get('Expansion',0):.1%})")

# Trail IS vs OOS
print("\n--- Trail performance IS vs OOS ---")
t_is  = i[i.gestion=="trail"]
t_oos = o[o.gestion=="trail"]
print(f"  IS  trail: n={len(t_is):>3}  avgR={t_is.r.mean():>+.3f}  max={t_is.r.max():>+.2f}")
print(f"  OOS trail: n={len(t_oos):>3}  avgR={t_oos.r.mean():>+.3f}  max={t_oos.r.max():>+.2f}")
if len(t_oos):
    print(f"  OOS trail dist: <0={( t_oos.r<0).mean():.0%}  0-1={(t_oos.r.between(0,1)).mean():.0%}  >1R={(t_oos.r>1).mean():.0%}  >3R={(t_oos.r>3).mean():.0%}")
