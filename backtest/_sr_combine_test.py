"""
_sr_combine_test.py — Combinacion S/R nuevos con base v2_h1_ifvg
=================================================================
Tests:
  A. S/R standalone con detalle anual completo (weekly, round, pdh)
  B. v2_h1_ifvg solo (base)
  C. v2 + weekly H/L
  D. v2 + round numbers virgin
  E. v2 + todos los S/R nuevos
  F. POC virgin (flip gen_h21: 0 toques en vez de 2+)
  G. v2 + POC virgin (reemplazo de gen_h21)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import numpy as np
import pandas as pd
import featurelab as FL
import _listas2 as L2
from _strategy_ab import run_system, stats
from _listas import OOS_MS

# ── Params identicos al binario Rust v2 ──────────────────────────────────────
PARAMS = dict(
    trail_atr=6.0, stop_scale=0.8, mode="routed", volfilter=True,
    cooldown=6, max_day=2, min_range=0.0,
    use_partial=True, p1_frac=0.5, stop_floor_pct=0.15,
    tp2_cap_r=0.0, timeout_min=24*60,
)

TOUCH_TOL          = 0.002
STOP_FRAC          = 0.5
MIN_RR             = 1.2
ENTRY_ABOVE_OFFSET = 0.001

def get(a, col):
    arr = getattr(a, col, None)
    return None if arr is None else np.array(arr, dtype=float)

def struct_target(side, lvl, a, i):
    candidates = []
    for col in ['vp_vah','vp_val','swing_high_50','swing_low_50',
                'prev_day_high','prev_day_low','weekly_high','weekly_low']:
        arr = get(a, col)
        if arr is None: continue
        v = float(arr[i])
        if not np.isfinite(v) or v <= 0: continue
        if side == "long"  and v > lvl * 1.002: candidates.append(v)
        if side == "short" and v < lvl * 0.998: candidates.append(v)
    if not candidates: return None, None
    tp2 = min(candidates) if side == "long" else max(candidates)
    risk = STOP_FRAC * a.atr[i]
    if risk <= 0 or abs(tp2 - lvl) / risk < MIN_RR: return None, None
    tp1_cands = [v for v in candidates if v != tp2 and
                 (v < tp2 if side=="long" else v > tp2)]
    tp1 = (min(tp1_cands) if side=="long" else max(tp1_cands)) if tp1_cands else None
    return tp1, tp2

def is_virgin(a, i, lvl, lookback=20):
    for j in range(max(0, i-lookback), i):
        if abs(a.l[j] - lvl)/lvl <= TOUCH_TOL: return False
        if abs(a.h[j] - lvl)/lvl <= TOUCH_TOL: return False
    return True

def n_touches(a, i, lvl, lookback=20):
    n = 0
    for j in range(max(0, i-lookback), i):
        if abs(a.l[j] - lvl)/lvl <= TOUCH_TOL or abs(a.h[j] - lvl)/lvl <= TOUCH_TOL:
            n += 1
    return n

def signal(a, i, side, lvl, kind, virgin_req=False):
    if not np.isfinite(lvl) or lvl <= 0: return []
    if side == "long":
        if not (a.l[i] <= lvl * (1 + TOUCH_TOL)): return []
        if a.c[i] <= lvl: return []
    else:
        if not (a.h[i] >= lvl * (1 - TOUCH_TOL)): return []
        if a.c[i] >= lvl: return []
    if virgin_req and not is_virgin(a, i, lvl): return []
    stop = lvl - STOP_FRAC*a.atr[i] if side=="long" else lvl + STOP_FRAC*a.atr[i]
    tp1, tp2 = struct_target(side, lvl, a, i)
    if tp2 is None: return []
    return [(side, lvl, stop, tp1, tp2, kind)]

# ── Generadores S/R nuevos ────────────────────────────────────────────────────

ROUND_CONFIG = {"BTCUSDT":[1000,5000],"ETHUSDT":[100,500],"SOLUSDT":[10,50]}

def gen_weekly_hl():
    def g(a, i):
        wh = get(a, 'weekly_high'); wl = get(a, 'weekly_low')
        sigs = []
        if wl is not None:
            lvl = float(wl[i]) * (1 + ENTRY_ABOVE_OFFSET)
            sigs += signal(a, i, "long",  lvl, "wl")
        if wh is not None:
            lvl = float(wh[i]) * (1 - ENTRY_ABOVE_OFFSET)
            sigs += signal(a, i, "short", lvl, "wh")
        return sigs or None
    return g

def gen_pdh_pdl(virgin=False):
    def g(a, i):
        pdh = get(a, 'prev_day_high'); pdl = get(a, 'prev_day_low')
        sigs = []
        if pdl is not None: sigs += signal(a, i, "long",  float(pdl[i]), "pdl", virgin)
        if pdh is not None: sigs += signal(a, i, "short", float(pdh[i]), "pdh", virgin)
        return sigs or None
    return g

def gen_round(sym, virgin=False):
    mults = ROUND_CONFIG.get(sym, [1000])
    def g(a, i):
        p = float(a.c[i]); sigs = []
        for m in mults:
            nr = round(p/m)*m
            if nr <= 0: continue
            side = "long" if p > nr else "short"
            sigs += signal(a, i, side, nr, f"rnd{m}", virgin)
        return sigs or None
    return g

# ── POC virgin (flip gen_h21: 1er toque, no 2+) ──────────────────────────────

def gen_poc_virgin(lookback=15):
    """Como gen_h21 pero exige 0-1 toques del POC (nivel intacto)."""
    def g(a, i):
        if i < lookback: return None
        lvl = float(a.vp_poc[i]) if hasattr(a,'vp_poc') else float(a.fp_poc[i-1])
        if not np.isfinite(lvl) or lvl <= 0: return None
        t = n_touches(a, i, lvl, lookback)
        if t > 1: return None   # ya fue tocado 2+ veces = agotado
        # Mismo criterio de toque que gen_h21: low toca el POC
        if not (abs(a.l[i] - lvl)/lvl <= TOUCH_TOL): return None
        if not (a.c[i] > a.c[i-1]): return None   # cierre alcista
        stop = lvl - 0.6*a.atr[i]
        tp1, tp2 = struct_target("long", lvl, a, i)
        if tp2 is None: return None
        return [("long", lvl, stop, tp1, tp2, "POCv")]
    return g

def gen_poc_virgin_short(lookback=15):
    """Espejo short del POC virgin."""
    def g(a, i):
        if i < lookback: return None
        lvl = float(a.vp_poc[i]) if hasattr(a,'vp_poc') else float(a.fp_poc[i-1])
        if not np.isfinite(lvl) or lvl <= 0: return None
        t = n_touches(a, i, lvl, lookback)
        if t > 1: return None
        if not (abs(a.h[i] - lvl)/lvl <= TOUCH_TOL): return None
        if not (a.c[i] < a.c[i-1]): return None
        stop = lvl + 0.6*a.atr[i]
        tp1, tp2 = struct_target("short", lvl, a, i)
        if tp2 is None: return None
        return [("short", lvl, stop, tp1, tp2, "POCvs")]
    return g

# Importar v2 generators
try:
    from _parity_h1_ifvg import (
        gen_ifvg, f_h1, f_dist,
        gen_h5     as gen_h5_v2,
        gen_h21    as gen_h21_v2,
        gen_h21_short as gen_h21_short_v2,
    )
    HAS_V2 = True
except Exception as e:
    print(f"[warn] no pude importar v2 generators: {e}")
    from _listas2 import gen_h5, gen_h21
    from _audit_mirror import gen_h21_short
    HAS_V2 = False

def get_v2_gens():
    if HAS_V2:
        return [gen_h5_v2(), gen_h21_v2(), gen_h21_short_v2()]
    return [L2.gen_h5(), L2.gen_h21()]

# ── Runner con detalle trimestral ─────────────────────────────────────────────

def quarterly(df):
    if len(df) == 0: return
    df = df.copy()
    df['dt'] = pd.to_datetime(df.ts * 1_000_000, utc=True)
    df['q']  = df.dt.dt.to_period('Q')
    for q, g in df.groupby('q'):
        oos = int(g.oos.sum())
        r = g.r.mean(); wr = 100*(g.r>0).mean()
        print(f"    {q}  n={len(g):>4}  avgR={r:>+.3f}  WR={wr:>3.0f}%  "
              f"{'[OOS ' + str(oos) + ']' if oos > 0 else ''}")

def run_suite(label, sym, gens):
    a, m1 = FL.load(sym)
    df = run_system(a, gens, m1, tf_min=15, **PARAMS)
    if len(df) == 0:
        return dict(n=0, avgR=0, oosA=0, wr=0, dd=0), df
    return stats(df), df

def print_results(label, results):
    print(f"\n{'='*68}")
    print(f"  {label}")
    print(f"  {'sym':<8} {'n':>5} {'IS_R':>7} {'OOS_R':>8} {'WR':>5} {'DD':>5}")
    oos_vals = []; ok = True
    for sym, (s, _) in results.items():
        oos_r = s['oosA']
        oos_vals.append(oos_r)
        if oos_r <= 0: ok = False
        print(f"  {sym:<8} {s['n']:>5} {s['avgR']:>+7.3f} {oos_r:>+8.3f} "
              f"{s['wr']:>4.0f}% {s['dd']:>4.1f}%")
    port = sum(oos_vals)/len(oos_vals) if oos_vals else 0
    print(f"  {'Portfolio':>8}         {port:>+8.3f}  {'PASA' if ok else 'FALLA'}")
    return ok, port

# ── SUITE DE TESTS ────────────────────────────────────────────────────────────

print("=" * 68)
print("TEST COMPLETO: S/R + COMBINACIONES")
print("=" * 68)

SYMS = [s for s in FL.ASSETS if Path(FL.ASSETS[s]).exists()]

# ── A: S/R standalone (detail year) ──────────────────────────────────────────
print("\n\n=== A. STANDALONE S/R (detalle trimestral) ===")

for label, make_gens in [
    ("A1. Weekly H/L",         lambda sym: [gen_weekly_hl()]),
    ("A2. PDH/PDL virgin",     lambda sym: [gen_pdh_pdl(virgin=True)]),
    ("A3. Round numbers virg.", lambda sym: [gen_round(sym, virgin=True)]),
]:
    print(f"\n--- {label} ---")
    for sym in SYMS:
        a, m1 = FL.load(sym)
        df = run_system(a, make_gens(sym), m1, tf_min=15, **PARAMS)
        if len(df) == 0:
            print(f"  {sym}: n=0"); continue
        s = stats(df)
        oos = df[df.oos]
        print(f"  {sym}  n={s['n']}  IS={s['avgR']:+.3f}  OOS={s['oosA']:+.3f}  "
              f"WR={s['wr']:.0f}%  DD={s['dd']:.1f}%  n_oos={len(oos)}")
        quarterly(df)

# ── B: v2 base ────────────────────────────────────────────────────────────────
print("\n\n=== B. v2_h1_ifvg BASE ===")
b_res = {}
for sym in SYMS:
    a, m1 = FL.load(sym)
    df = run_system(a, get_v2_gens(), m1, tf_min=15, **PARAMS)
    s = stats(df)
    b_res[sym] = (s, df)
    oos = df[df.oos]
    print(f"  {sym}  n={s['n']}  IS={s['avgR']:+.3f}  OOS={s['oosA']:+.3f}  n_oos={len(oos)}")
b_port = sum(b_res[s][0]['oosA'] for s in SYMS)/len(SYMS)
print(f"  Portfolio OOS: {b_port:+.3f}")

# ── C: v2 + Weekly ────────────────────────────────────────────────────────────
print("\n\n=== C. v2 + Weekly H/L ===")
c_res = {}
for sym in SYMS:
    a, m1 = FL.load(sym)
    gens = get_v2_gens() + [gen_weekly_hl()]
    df = run_system(a, gens, m1, tf_min=15, **PARAMS)
    s = stats(df)
    c_res[sym] = (s, df)
    b = b_res[sym][0]
    print(f"  {sym}  n={s['n']} (+{s['n']-b['n']})  IS={s['avgR']:+.3f}  "
          f"OOS={s['oosA']:+.3f} (d={s['oosA']-b['oosA']:+.3f})")
c_port = sum(c_res[s][0]['oosA'] for s in SYMS)/len(SYMS)
print(f"  Portfolio OOS: {c_port:+.3f}  (d={c_port-b_port:+.3f})")

# ── D: v2 + Round virgin ──────────────────────────────────────────────────────
print("\n\n=== D. v2 + Round numbers virgin ===")
d_res = {}
for sym in SYMS:
    a, m1 = FL.load(sym)
    gens = get_v2_gens() + [gen_round(sym, virgin=True)]
    df = run_system(a, gens, m1, tf_min=15, **PARAMS)
    s = stats(df)
    d_res[sym] = (s, df)
    b = b_res[sym][0]
    print(f"  {sym}  n={s['n']} (+{s['n']-b['n']})  IS={s['avgR']:+.3f}  "
          f"OOS={s['oosA']:+.3f} (d={s['oosA']-b['oosA']:+.3f})")
d_port = sum(d_res[s][0]['oosA'] for s in SYMS)/len(SYMS)
print(f"  Portfolio OOS: {d_port:+.3f}  (d={d_port-b_port:+.3f})")

# ── E: v2 + todos S/R nuevos ──────────────────────────────────────────────────
print("\n\n=== E. v2 + Weekly + Round virgin + PDH/PDL virgin ===")
e_res = {}
for sym in SYMS:
    a, m1 = FL.load(sym)
    gens = get_v2_gens() + [
        gen_weekly_hl(),
        gen_round(sym, virgin=True),
        gen_pdh_pdl(virgin=True),
    ]
    df = run_system(a, gens, m1, tf_min=15, **PARAMS)
    s = stats(df)
    e_res[sym] = (s, df)
    b = b_res[sym][0]
    oos = df[df.oos]
    print(f"  {sym}  n={s['n']} (+{s['n']-b['n']})  IS={s['avgR']:+.3f}  "
          f"OOS={s['oosA']:+.3f} (d={s['oosA']-b['oosA']:+.3f})  n_oos={len(oos)}")
e_port = sum(e_res[s][0]['oosA'] for s in SYMS)/len(SYMS)
print(f"  Portfolio OOS: {e_port:+.3f}  (d={e_port-b_port:+.3f})")

# ── F: POC virgin standalone ──────────────────────────────────────────────────
print("\n\n=== F. POC virgin standalone (flip gen_h21: 0-1 toques) ===")
f_res = {}
for sym in SYMS:
    a, m1 = FL.load(sym)
    gens = [gen_poc_virgin(), gen_poc_virgin_short()]
    df = run_system(a, gens, m1, tf_min=15, **PARAMS)
    s = stats(df) if len(df) > 0 else dict(n=0,avgR=0,oosA=0,wr=0,dd=0)
    f_res[sym] = (s, df)
    oos = df[df.oos] if len(df) > 0 else pd.DataFrame()
    print(f"  {sym}  n={s['n']}  IS={s['avgR']:+.3f}  OOS={s['oosA']:+.3f}  "
          f"WR={s['wr']:.0f}%  n_oos={len(oos)}")
f_port = sum(f_res[s][0]['oosA'] for s in SYMS)/len(SYMS)
print(f"  Portfolio OOS: {f_port:+.3f}  {'PASA' if all(f_res[s][0]['oosA']>0 for s in SYMS) else 'FALLA'}")

# ── G: v2 con POC virgin en lugar de gen_h21 ─────────────────────────────────
print("\n\n=== G. v2 reemplazando gen_h21 por POC virgin ===")
g_res = {}
for sym in SYMS:
    a, m1 = FL.load(sym)
    # Reemplazar gen_h21 + gen_h21_short por versiones virgin
    if HAS_V2:
        gens = [gen_h5_v2(), gen_poc_virgin(), gen_poc_virgin_short()]
    else:
        gens = [L2.gen_h5(), gen_poc_virgin(), gen_poc_virgin_short()]
    df = run_system(a, gens, m1, tf_min=15, **PARAMS)
    s = stats(df) if len(df) > 0 else dict(n=0,avgR=0,oosA=0,wr=0,dd=0)
    g_res[sym] = (s, df)
    b = b_res[sym][0]
    print(f"  {sym}  n={s['n']}  IS={s['avgR']:+.3f}  "
          f"OOS={s['oosA']:+.3f} (d={s['oosA']-b['oosA']:+.3f})")
g_port = sum(g_res[s][0]['oosA'] for s in SYMS)/len(SYMS)
print(f"  Portfolio OOS: {g_port:+.3f}  (d={g_port-b_port:+.3f})  "
      f"{'PASA' if all(g_res[s][0]['oosA']>0 for s in SYMS) else 'FALLA'}")

# ── RESUMEN FINAL ─────────────────────────────────────────────────────────────
print("\n\n" + "="*68)
print("RESUMEN COMPARATIVO")
print(f"  {'Config':<38} {'Port OOS':>9}  {'Delta':>7}  Regla")
rows = [
    ("A1. Weekly H/L standalone",     sum(run_system(FL.load(s)[0],[gen_weekly_hl()],FL.load(s)[1],tf_min=15,**PARAMS).pipe(lambda df: [stats(df)['oosA']] if len(df)>0 else [0])[-1] for s in SYMS)/len(SYMS), 0),
]
# Use pre-computed results
final_rows = [
    ("B. v2 base",             b_port, 0),
    ("C. v2 + Weekly",         c_port, c_port-b_port),
    ("D. v2 + Round virgin",   d_port, d_port-b_port),
    ("E. v2 + Weekly+Round+PDH",e_port,e_port-b_port),
    ("F. POC virgin standalone",f_port, 0),
    ("G. v2 + POC virgin",     g_port, g_port-b_port),
]
for label, oos, delta in sorted(final_rows, key=lambda x: -x[1]):
    ok_all = True  # simplified
    print(f"  {label:<38} {oos:>+9.3f}  {delta:>+7.3f}")
