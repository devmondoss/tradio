"""
_signal_volume_sweep.py — más señales sin degradar avgR
========================================================
Sweep cooldown × max_day. Métrica: OOS avgR (no bajar de base), n_oos (subir).
Regla dura: los 3 activos deben pasar. Base: cooldown=6, max_day=2.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import numpy as np
import pandas as pd
import featurelab as FL
import _listas2 as L2
from _audit_mirror import gen_h21_short
from _strategy_ab import run_system, stats

GENS = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]
BASE = dict(trail_atr=6.0, stop_scale=0.8, mode="routed", volfilter=True,
            tp2_cap_r=0.0, use_partial=True, p1_frac=0.5,
            timeout_min=24*60, stop_floor_pct=0.15, min_range=0.5)

COOLDOWNS  = [2, 3, 4, 6]
MAX_DAYS   = [2, 3, 4, 6]

BASE_CD, BASE_MD = 6, 2

def run_combo(cd, md):
    rows = []
    for sym in FL.ASSETS:
        if not Path(FL.ASSETS[sym]).exists(): continue
        a, m1 = FL.load(sym)
        df = run_system(a, GENS, m1, tf_min=15, cooldown=cd, max_day=md, **BASE)
        s  = stats(df)
        rows.append(dict(sym=sym, n=s['n'], n_oos=s['oosN'] and len(df[df.oos]),
                         avgR=s['avgR'], oosR=s['oosA'], wr=s['wr'], dd=s['dd']))
    return rows

# Base
print("Calculando base (cd=6, md=2)...")
base_rows = run_combo(BASE_CD, BASE_MD)
base = {r['sym']: r for r in base_rows}
print(f"{'sym':<8} {'n':>5} {'n_oos':>6} {'IS_avgR':>8} {'OOS_avgR':>9} {'WR':>5} {'DD':>5}")
for r in base_rows:
    print(f"{r['sym']:<8} {r['n']:>5} {r['n_oos']:>6} {r['avgR']:>+8.3f} {r['oosR']:>+9.3f} {r['wr']:>4.0f}% {r['dd']:>4.1f}%")
base_oos_avg = sum(r['oosR'] for r in base_rows) / len(base_rows)
print(f"  Portfolio OOS: {base_oos_avg:+.3f}\n")

# Sweep
print(f"{'cd':>4} {'md':>4} | {'sym':<8} {'n':>5} {'n_oos':>6} {'IS_avgR':>8} {'OOS_avgR':>9} {'Δn':>5} {'ΔR':>6} {'DD':>5}")
print("-" * 80)

results = {}
for cd in COOLDOWNS:
    for md in MAX_DAYS:
        if cd == BASE_CD and md == BASE_MD: continue
        rows = run_combo(cd, md)
        combo_oos = []
        passes = True
        for r in rows:
            b = base[r['sym']]
            delta_n = r['n_oos'] - b['n_oos']
            delta_r = r['oosR'] - b['oosR']
            marker = ""
            if r['oosR'] < b['oosR'] - 0.05:   # degradación >0.05R = falla
                passes = False; marker = " ✗"
            print(f"{cd:>4} {md:>4} | {r['sym']:<8} {r['n']:>5} {r['n_oos']:>6} "
                  f"{r['avgR']:>+8.3f} {r['oosR']:>+9.3f} "
                  f"{delta_n:>+5} {delta_r:>+6.3f} {r['dd']:>4.1f}%{marker}")
            combo_oos.append(r['oosR'])
        avg_oos = sum(combo_oos) / len(combo_oos)
        label = "✓ PASA" if passes else "✗ falla"
        print(f"          {'Portfolio OOS':>20}  {avg_oos:>+9.3f}  [{label}]\n")
        results[(cd, md)] = (passes, avg_oos, sum(r['n_oos'] for r in rows))

# Resumen
print("=" * 60)
print("COMBOS QUE PASAN REGLA DURA (OOS no baja >0.05R en ningún activo):")
print(f"{'cd':>4} {'md':>4} | {'OOS avg':>8} {'n_oos total':>12} {'vs base':>8}")
for (cd, md), (ok, oos, n) in sorted(results.items(), key=lambda x: -x[1][1]):
    if ok:
        base_n = sum(r['n_oos'] for r in base_rows)
        print(f"{cd:>4} {md:>4} | {oos:>+8.3f} {n:>12} {n-base_n:>+8}")
