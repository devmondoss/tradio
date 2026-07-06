"""
_touches_test.py — min_touches sweep en gen_h21 (anti-spoofing)
=================================================================
Hipótesis: exigir más toques al POC defendido filtra spoofers y niveles débiles.
Gen H21 base: touches >= 2 en ventana K=15 barras (3.75h en M15).
Test: min_touches ∈ {2, 3, 4, 5}.

Protocolo: run_system con STD de featurelab (mode=routed, volfilter, cooldown=3, etc.)
Regla dura: OOS avgR > base(t=2) en los 3 activos simultáneamente.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import numpy as np
import featurelab as FL
import _listas2 as L2
from _strategy_ab import run_system

STD = dict(**FL.STD)

def gen_h21_t(min_touches=2, K=15, tol=0.002):
    def g(a, i):
        if i < K: return
        win = a.fp_poc[i-K:i]; win = win[np.isfinite(win)]
        if len(win) < 3: return
        lvl = np.median(win)
        touches = np.sum(np.abs(a.l[i-K:i] - lvl) / lvl <= tol)
        if touches >= min_touches and a.c[i] > a.c[i-1] and abs(a.l[i] - lvl) / lvl <= tol:
            stop = lvl - 0.6 * a.atr[i]
            tp1, tp2 = L2.struct_target(a, i, "long", lvl)
            if np.isfinite(tp2):
                return [("long", lvl, stop, tp1, tp2, f"H21t{min_touches}")]
        return
    return g

def gen_h21s_t(min_touches=2, K=15, tol=0.002):
    def g(a, i):
        if i < K: return
        win = a.fp_poc[i-K:i]; win = win[np.isfinite(win)]
        if len(win) < 3: return
        lvl = np.median(win)
        touches = np.sum(np.abs(a.h[i-K:i] - lvl) / lvl <= tol)
        if touches >= min_touches and a.c[i] < a.c[i-1] and abs(a.h[i] - lvl) / lvl <= tol:
            stop = lvl + 0.6 * a.atr[i]
            tp1, tp2 = L2.struct_target(a, i, "short", lvl)
            if np.isfinite(tp2):
                return [("short", lvl, stop, tp1, tp2, f"H21st{min_touches}")]
        return
    return g

def run_for(sym, min_touches):
    a, m1 = FL.load(sym)
    tf = STD["tf_min"]
    params = {k: v for k, v in STD.items() if k != "tf_min"}
    gens = [L2.gen_h5(), gen_h21_t(min_touches), gen_h21s_t(min_touches)]
    df = run_system(a, gens, m1, tf, **params)
    if df.empty:
        return 0, float("nan"), 0, float("nan"), float("nan")
    i_ = df[~df.oos]; o_ = df[df.oos]
    return (len(i_), i_.r.mean() if len(i_) else float("nan"),
            len(o_), o_.r.mean() if len(o_) else float("nan"),
            100*(o_.r > 0).mean() if len(o_) else float("nan"))

if __name__ == "__main__":
    SYMS = [s for s in FL.ASSETS if Path(FL.ASSETS[s]).exists()]
    KTHR = [2, 3, 4, 5]
    all_res = {s: {} for s in SYMS}

    for sym in SYMS:
        print(f"\n{'─'*64}")
        print(f"  {sym}")
        print(f"{'─'*64}")
        print(f"  {'touches':>9}  {'IS n':>5} {'IS avgR':>8}   {'OOS n':>5} {'OOS avgR':>9} {'OOS WR':>7}")
        base_oos = None
        for t in KTHR:
            n_is, avg_is, n_oos, avg_oos, wr_oos = run_for(sym, t)
            all_res[sym][t] = (n_is, avg_is, n_oos, avg_oos, wr_oos)
            tag   = " ←BASE" if t == 2 else ""
            delta = f"  Δ{avg_oos - base_oos:+.3f}" if base_oos is not None and (avg_oos == avg_oos) else ""
            if t == 2: base_oos = avg_oos
            print(f"  touches>={t}    {n_is:>5} {avg_is:>+8.3f}   {n_oos:>5} {avg_oos:>+9.3f} {wr_oos:>6.1f}%{tag}{delta}")

    print(f"\n{'='*64}")
    print("VEREDICTO — ¿más toques mejora el edge?")
    print(f"{'='*64}")
    for t in [3, 4, 5]:
        v_oos  = {s: all_res[s][t][3] for s in SYMS}
        b_oos  = {s: all_res[s][2][3] for s in SYMS}
        pos    = all(v > 0 for v in v_oos.values() if v == v)
        beats  = all(v_oos[s] > b_oos[s] for s in SYMS if v_oos[s] == v_oos[s])
        print(f"  touches>={t}: OOS>0: {'✅' if pos else '❌'}   Bate base: {'✅' if beats else '❌'}")
        for s in SYMS:
            b, v = b_oos[s], v_oos[s]
            print(f"    {s}: base={b:+.3f} → t={t}: {v:+.3f}  Δ={v-b:+.3f}  n_oos={all_res[s][t][2]}")
