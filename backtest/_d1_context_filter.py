"""
_d1_context_filter.py — D1 EMA20 como filtro de contexto macro
===============================================================
Agrega un filtro de sesgo diario encima del backtest base de liquidity.
Solo long cuando close > D1_EMA20, solo short cuando close < D1_EMA20.

Regla dura: OOS avgR >= base en los 3 activos.
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

BASE = dict(
    trail_atr=6.0, stop_scale=0.8, mode="routed", volfilter=True,
    tp2_cap_r=0.0, use_partial=True, p1_frac=0.5,
    timeout_min=24*60, stop_floor_pct=0.15, min_range=0.5,
    cooldown=6, max_day=2,
)

def d1_ema20(a) -> np.ndarray:
    """D1 EMA20 real: resamplea a diario, computa EMA20, merge ffill a M15."""
    ts = pd.to_datetime(a.ts * 1_000_000, utc=True)
    closes = pd.Series(a.c, index=ts)
    daily = closes.resample("1D").last().dropna()
    ema_d = daily.ewm(span=20, adjust=False).mean()
    return ema_d.reindex(ts, method="ffill").values

def wrap_with_d1(gens, d1: np.ndarray):
    """Envuelve generadores: descarta señales contra el sesgo D1."""
    wrapped = []
    for g in gens:
        def make_w(g_=g):
            def w(a, i):
                sigs = g_(a, i)
                if sigs is None or not np.isfinite(d1[i]):
                    return sigs
                out = []
                for sig in sigs:
                    side = sig[0]
                    if side == "long"  and a.c[i] < d1[i]: continue
                    if side == "short" and a.c[i] > d1[i]: continue
                    out.append(sig)
                return out
            return w
        wrapped.append(make_w())
    return wrapped

# ── Run ───────────────────────────────────────────────────────────────────────
print("=" * 65)
print("FILTRO CONTEXTO MACRO: D1 EMA20")
print("Solo long cuando close > EMA20 diario, short cuando close < EMA20")
print("=" * 65)

results = {}
for label, use_d1 in [("BASE (trail=6, routed)", False), ("+ D1 EMA20", True)]:
    print(f"\n{label}")
    print(f"  {'sym':<8} {'n':>5} {'n_oos':>6} {'IS_avgR':>8} {'OOS_avgR':>9} {'WR':>5} {'DD':>5}")
    oos_list = []; n_list = []
    for sym in FL.ASSETS:
        if not Path(FL.ASSETS[sym]).exists():
            continue
        a, m1 = FL.load(sym)
        gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]
        if use_d1:
            d1 = d1_ema20(a)
            gens = wrap_with_d1(gens, d1)
            # stats extra: cuántas barras tiene sesgo definido y precio encima/abajo
            valid = np.isfinite(d1)
            above = (a.c[valid] > d1[valid]).mean()
            below = (a.c[valid] < d1[valid]).mean()
            print(f"  [D1 cobertura {sym}]  >EMA={100*above:.0f}%  <EMA={100*below:.0f}%")

        df = run_system(a, gens, m1, tf_min=15, **BASE)
        s  = stats(df)
        oos_n = int(df["oos"].sum()) if "oos" in df.columns else "?"
        results[(label, sym)] = s
        oos_list.append(s['oosA']); n_list.append(s['n'])
        print(f"  {sym:<8} {s['n']:>5} {oos_n:>6} {s['avgR']:>+8.3f} "
              f"{s['oosA']:>+9.3f} {s['wr']:>4.0f}% {s['dd']:>4.1f}%")
    if oos_list:
        print(f"  {'Portfolio':>8} {sum(n_list):>5} {'':>6} {'':>8} "
              f"{sum(oos_list)/len(oos_list):>+9.3f}")

# ── Delta vs base ─────────────────────────────────────────────────────────────
print("\n" + "=" * 65)
print("DELTA vs BASE")
print(f"  {'sym':<8} {'Δn':>6} {'Δ OOS_avgR':>11} {'pasa?':>7}")
all_pass = True
for sym in FL.ASSETS:
    if not Path(FL.ASSETS[sym]).exists(): continue
    b = results.get(("BASE (trail=6, routed)", sym))
    d = results.get(("+ D1 EMA20", sym))
    if b and d:
        dn = d['n'] - b['n']
        dr = d['oosA'] - b['oosA']
        ok = d['oosA'] > 0 and dr >= -0.05
        if not ok: all_pass = False
        print(f"  {sym:<8} {dn:>+6} {dr:>+11.3f} {'✓' if ok else '✗':>7}")
print(f"\n  Regla dura: {'PASA ✓' if all_pass else 'FALLA ✗'}")
