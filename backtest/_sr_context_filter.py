"""
_sr_context_filter.py — S/R como CONTEXTO del v2 (no como entrada nueva)
=========================================================================
Idea: solo tomar trades del v2 cuando el nivel de entrada coincide con
un nivel S/R estructural cercano (PDH/PDL, weekly H/L, round number).
Eso es confluencia, no una entrada nueva independiente.
Regla dura: OOS > 0 en los 3 activos. Bonus si WR sube.
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
from _listas import OOS_MS

PARAMS = dict(
    trail_atr=6.0, stop_scale=0.8, mode="routed", volfilter=True,
    cooldown=6, max_day=2, min_range=0.0,
    use_partial=True, p1_frac=0.5, stop_floor_pct=0.15,
    tp2_cap_r=0.0, timeout_min=24*60,
)

ROUND_CONFIG = {"BTCUSDT":[1000,5000], "ETHUSDT":[100,500], "SOLUSDT":[10,50]}

def near_sr(a, i, sym, tol):
    """True si el nivel de entrada está dentro de tol% de algún S/R estructural."""
    p = float(a.c[i])

    # PDH/PDL
    pdh = getattr(a, 'prev_day_high', None)
    pdl = getattr(a, 'prev_day_low',  None)
    if pdh is not None and abs(p - float(pdh[i]))/p < tol: return True
    if pdl is not None and abs(p - float(pdl[i]))/p < tol: return True

    # Weekly H/L
    wh = getattr(a, 'weekly_high', None)
    wl = getattr(a, 'weekly_low',  None)
    if wh is not None and abs(p - float(wh[i]))/p < tol: return True
    if wl is not None and abs(p - float(wl[i]))/p < tol: return True

    # Round numbers
    for m in ROUND_CONFIG.get(sym, [1000]):
        nr = round(p/m)*m
        if nr > 0 and abs(p - nr)/p < tol: return True

    return False

def wrap_sr_context(gens, sym, tol):
    """Envuelve generadores: solo dispara si hay S/R cercano."""
    out = []
    for g in gens:
        def make(g_=g):
            def w(a, i):
                if not near_sr(a, i, sym, tol): return None
                return g_(a, i)
            return w
        out.append(make())
    return out

def run_suite(sym, tol):
    a, m1 = FL.load(sym)
    base_gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]
    gens = wrap_sr_context(base_gens, sym, tol)
    df = run_system(a, gens, m1, tf_min=15, **PARAMS)
    if len(df) == 0:
        return dict(n=0, avgR=0, oosA=0, wr=0, dd=0), df
    return stats(df), df

# ── Base sin filtro ───────────────────────────────────────────────────────────
print("=" * 65)
print("BASE (sin filtro S/R)")
base_res = {}
for sym in FL.ASSETS:
    if not Path(FL.ASSETS[sym]).exists(): continue
    a, m1 = FL.load(sym)
    df = run_system(a, [L2.gen_h5(), L2.gen_h21(), gen_h21_short()], m1, tf_min=15, **PARAMS)
    s = stats(df)
    base_res[sym] = s
    oos = df[df.oos]
    print(f"  {sym:<8} n={s['n']:>4}  n_oos={len(oos):>3}  avgR={s['oosA']:>+.3f}  "
          f"WR={s['wr']:>3.0f}%  net_R={oos.r.sum():>+.0f}R")
b_port = sum(v['oosA'] for v in base_res.values()) / len(base_res)
print(f"  Portfolio OOS: {b_port:+.3f}")

# ── Sweep de tolerancias ──────────────────────────────────────────────────────
print("\n" + "=" * 65)
print("FILTRO S/R CONTEXTO — sweep de tolerancia")
print(f"  (solo entra si nivel VP está dentro de X% de PDH/PDL/Weekly/Round)")
print()

results = {}
for tol in [0.003, 0.005, 0.008, 0.01, 0.015, 0.02]:
    oos_vals = []; ok = True
    rows = []
    for sym in [s for s in FL.ASSETS if Path(FL.ASSETS[s]).exists()]:
        s, df = run_suite(sym, tol)
        oos = df[df.oos] if len(df) > 0 else pd.DataFrame()
        oos_r = s['oosA']
        oos_vals.append(oos_r)
        if oos_r <= 0: ok = False
        rows.append((sym, s['n'], len(oos), oos_r, s['wr'], oos.r.sum() if len(oos)>0 else 0))

    port = sum(oos_vals)/len(oos_vals) if oos_vals else 0
    results[tol] = (ok, port, rows)

    n_total  = sum(r[1] for r in rows)
    nr_total = sum(r[5] for r in rows)
    wr_avg   = sum(r[4] for r in rows) / len(rows)
    pct_base = 100 * n_total / sum(base_res[s]['n'] for s in base_res)

    print(f"  tol={tol*100:.1f}%  port_OOS={port:>+.3f}  "
          f"n={n_total:>4} ({pct_base:>3.0f}% del base)  "
          f"WR={wr_avg:>3.0f}%  net_R={nr_total:>+.0f}R  "
          f"{'PASA' if ok else 'FALLA'}")

# ── Detalle del mejor ─────────────────────────────────────────────────────────
best_tol = max((tol for tol, (ok,_,_) in results.items() if ok),
               key=lambda t: results[t][1], default=None)

if best_tol:
    print(f"\n{'='*65}")
    print(f"MEJOR: tol={best_tol*100:.1f}%")
    print(f"  {'sym':<8} {'n':>5} {'n_oos':>6} {'OOS_R':>8} {'WR':>5} {'net_R':>8}  delta_n  delta_OOS")
    ok, port, rows = results[best_tol]
    for sym, n, n_oos, oos_r, wr, net_r in rows:
        b = base_res.get(sym, {})
        dn = n - b.get('n', 0)
        dr = oos_r - b.get('oosA', 0)
        print(f"  {sym:<8} {n:>5} {n_oos:>6} {oos_r:>+8.3f} {wr:>4.0f}% {net_r:>+8.0f}R  "
              f"{dn:>+6}  {dr:>+8.3f}")
    print(f"\n  Portfolio OOS: {port:+.3f}  (base: {b_port:+.3f}  delta: {port-b_port:+.3f})")

# ── Resumen final ─────────────────────────────────────────────────────────────
print(f"\n{'='*65}")
print("RESUMEN")
print(f"  {'tol':>6}  {'port_OOS':>9}  {'delta':>7}  {'WR':>5}  Regla")
for tol in sorted(results):
    ok, port, rows = results[tol]
    wr = sum(r[4] for r in rows)/len(rows)
    delta = port - b_port
    print(f"  {tol*100:>5.1f}%  {port:>+9.3f}  {delta:>+7.3f}  {wr:>4.0f}%  {'PASA' if ok else 'FALLA'}")
print(f"  {'BASE':>6}  {b_port:>+9.3f}  {'':>7}  {sum(v['wr'] for v in base_res.values())/len(base_res):>4.0f}%")
