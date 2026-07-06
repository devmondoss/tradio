"""
_universal_confluence.py — busqueda de confluencia universal (3/3 activos)
==========================================================================
Tests 5-12: candidatos que tienen cobertura en BTC/ETH/SOL.
Foco en senales que aplican sin importar liquidez del activo.
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

BASE = dict(
    trail_atr=6.0, stop_scale=0.8, mode="routed", volfilter=True,
    tp2_cap_r=0.0, use_partial=True, p1_frac=0.5,
    timeout_min=24*60, stop_floor_pct=0.15, min_range=0.5,
    cooldown=6, max_day=2,
)

def get(a, col):
    arr = getattr(a, col, None)
    return np.zeros(a.n) if arr is None else np.array(arr, dtype=float)

def wrap(gens, cl, cs):
    out = []
    for g in gens:
        def make(g_=g):
            def w(a, i):
                sigs = g_(a, i)
                if not sigs: return sigs
                return [s for s in sigs if (s[0]=="long" and cl(a,i)) or (s[0]=="short" and cs(a,i))]
            return w
        out.append(make())
    return out

def run_test(label, cl, cs, base_res):
    oos_vals, ok_all = [], True
    rows = []
    for sym in FL.ASSETS:
        if not Path(FL.ASSETS[sym]).exists(): continue
        a, m1 = FL.load(sym)
        gens = wrap([L2.gen_h5(), L2.gen_h21(), gen_h21_short()], cl, cs)
        try:
            df = run_system(a, gens, m1, tf_min=15, **BASE)
            s = stats(df) if len(df) > 0 else dict(n=0,avgR=0,oosA=0,wr=0,dd=0)
        except Exception:
            s = dict(n=0, avgR=0, oosA=0, wr=0, dd=0)
        b  = base_res[sym]
        dr = s['oosA'] - b['oosA']
        ok = s['n'] > 0 and s['oosA'] > 0 and dr >= -0.05
        if not ok: ok_all = False
        oos_vals.append(s['oosA'])
        rows.append((sym, s['n'], s['avgR'], s['oosA'], s['wr'], s['dd'], dr, ok))

    avg_oos = sum(oos_vals)/len(oos_vals) if oos_vals else 0
    badge = "PASA" if ok_all else "FALLA"
    print(f"\n  {label:<34} {badge}   portfolio_OOS={avg_oos:+.3f}")
    for sym, n, isr, oosr, wr, dd, dr, ok in rows:
        print(f"    {sym:<8} n={n:>4} IS={isr:>+.3f} OOS={oosr:>+.3f} "
              f"WR={wr:>3.0f}% DD={dd:>4.1f}% d={dr:>+.3f} {'V' if ok else 'X'}")
    return ok_all, avg_oos

# ── Base ──────────────────────────────────────────────────────────────────────
print("=" * 70)
print("BASE  (trail=6, routed, sin confluencia)")
base_res = {}
for sym in FL.ASSETS:
    if not Path(FL.ASSETS[sym]).exists(): continue
    a, m1 = FL.load(sym)
    df = run_system(a, [L2.gen_h5(), L2.gen_h21(), gen_h21_short()], m1, tf_min=15, **BASE)
    s  = stats(df)
    base_res[sym] = s
    print(f"  {sym}: n={s['n']} IS={s['avgR']:+.3f} OOS={s['oosA']:+.3f} WR={s['wr']:.0f}% DD={s['dd']:.1f}%")
avg_b = sum(v['oosA'] for v in base_res.values()) / len(base_res)
print(f"  Portfolio OOS: {avg_b:+.3f}")

print("\n" + "=" * 70)
print("CANDIDATOS UNIVERSALES")
results = {}

# ── T5: H4 alineado (h4_bearish: False=bull, True=bear) ─────────────────────
results['T5_h4'] = run_test(
    "T5: H4 alineado",
    lambda a, i: not bool(get(a, 'h4_bearish')[i]),   # long: H4 alcista
    lambda a, i: bool(get(a, 'h4_bearish')[i]),         # short: H4 bajista
    base_res)

# ── T6: VR > 1.0 en barra de entrada (volumen sobre promedio) ────────────────
results['T6_vr10'] = run_test(
    "T6: VR > 1.0 (vol sobre promedio)",
    lambda a, i: float(getattr(a,'vr',np.zeros(a.n))[i]) > 1.0,
    lambda a, i: float(getattr(a,'vr',np.zeros(a.n))[i]) > 1.0,
    base_res)

# ── T7: VR > 1.5 (mismo umbral que SC3) ─────────────────────────────────────
results['T7_vr15'] = run_test(
    "T7: VR > 1.5 (umbral SC3)",
    lambda a, i: float(getattr(a,'vr',np.zeros(a.n))[i]) > 1.5,
    lambda a, i: float(getattr(a,'vr',np.zeros(a.n))[i]) > 1.5,
    base_res)

# ── T8: Cierre fuerte en direccion del trade ──────────────────────────────────
# Long: la vela cierra en el 40% superior de su rango (compradores dominan la barra)
# Short: la vela cierra en el 40% inferior de su rango
def strong_close_long(a, i):
    rng = a.h[i] - a.l[i]
    return rng > 0 and (a.c[i] - a.l[i]) / rng >= 0.6

def strong_close_short(a, i):
    rng = a.h[i] - a.l[i]
    return rng > 0 and (a.h[i] - a.c[i]) / rng >= 0.6

results['T8_strong_close'] = run_test(
    "T8: cierre fuerte en direccion (40% extremo)",
    strong_close_long, strong_close_short, base_res)

# ── T9: Rango expansivo en barra anterior (momentum llegando al nivel) ────────
# La vela i-1 tenia rango > 1.5x ATR: impulso que trajo precio al nivel
def expansion_prior(a, i):
    if i < 1: return False
    rng = a.h[i-1] - a.l[i-1]
    return rng > 1.5 * a.atr[i]

results['T9_expansion_prior'] = run_test(
    "T9: expansion en barra anterior (>1.5 ATR)",
    expansion_prior, expansion_prior, base_res)

# ── T10: Multi-nivel (precio cerca de 2+ niveles estructurales) ──────────────
# Idea: el nivel es mas fuerte cuando coincide con PDH/PDL o weekly
def near_pdh_pdl(a, i, tol_pct=0.003):
    """True si precio esta dentro del 0.3% de PDH o PDL"""
    pdh = getattr(a, 'prev_day_high', np.zeros(a.n))
    pdl = getattr(a, 'prev_day_low',  np.zeros(a.n))
    p   = a.c[i]
    if pdh is None or pdl is None: return False
    return (abs(p - pdh[i]) / p < tol_pct) or (abs(p - pdl[i]) / p < tol_pct)

def near_weekly(a, i, tol_pct=0.003):
    wh = getattr(a, 'weekly_high', np.zeros(a.n))
    wl = getattr(a, 'weekly_low',  np.zeros(a.n))
    p  = a.c[i]
    return (abs(p - wh[i]) / p < tol_pct) or (abs(p - wl[i]) / p < tol_pct)

def multi_level_long(a, i):
    return near_pdh_pdl(a, i) or near_weekly(a, i)

def multi_level_short(a, i):
    return near_pdh_pdl(a, i) or near_weekly(a, i)

results['T10_multilevel'] = run_test(
    "T10: multi-nivel (PDH/PDL o weekly en 0.3%)",
    multi_level_long, multi_level_short, base_res)

# ── T11: H4 alineado OR VR>1.5 (contexto macro O confirmacion de volumen) ────
results['T11_h4_or_vr'] = run_test(
    "T11: H4 alineado OR VR>1.5",
    lambda a, i: (not bool(get(a,'h4_bearish')[i])) or float(getattr(a,'vr',np.zeros(a.n))[i])>1.5,
    lambda a, i: bool(get(a,'h4_bearish')[i]) or float(getattr(a,'vr',np.zeros(a.n))[i])>1.5,
    base_res)

# ── T12: Wick rejection en direccion ─────────────────────────────────────────
# Long: mecha inferior > 40% del rango total (precio fue abajo y rechazó)
# Short: mecha superior > 40% del rango total
def wick_long(a, i):
    rng = a.h[i] - a.l[i]
    if rng <= 0: return False
    body_bot = min(a.o[i], a.c[i])
    lower_wick = body_bot - a.l[i]
    return lower_wick / rng >= 0.4

def wick_short(a, i):
    rng = a.h[i] - a.l[i]
    if rng <= 0: return False
    body_top = max(a.o[i], a.c[i])
    upper_wick = a.h[i] - body_top
    return upper_wick / rng >= 0.4

results['T12_wick'] = run_test(
    "T12: wick rejection >= 40% del rango",
    wick_long, wick_short, base_res)

# ── Resumen ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("RESUMEN FINAL")
print(f"  BASE portfolio OOS: {avg_b:+.3f}")
print()
passed = [(k, v) for k, v in results.items() if v[0]]
failed = [(k, v) for k, v in results.items() if not v[0]]
for k, (ok, oos) in sorted(results.items(), key=lambda x: -x[1][1]):
    tag = "PASA" if ok else "FALLA"
    print(f"  {k:<25} {tag}  OOS={oos:+.3f}  vs base {oos-avg_b:>+.3f}")
