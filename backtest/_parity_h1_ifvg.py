"""
_parity_h1_ifvg.py — Test de paridad Python backtest vs Rust binary
====================================================================
Compara señales generadas por el Python (con H1+dist+IFVG) vs el comportamiento
esperado del Rust modificado. Corre el backtest con los parámetros exactos del
binario y verifica que los números son coherentes.

Parámetros del Rust (de levels.rs):
  trail_atr=6.0, stop_scale=0.8, tf_min=15, cooldown=6, max_day=2
  H1_BARS=4, DIST_MIN=0.5, IFVG_K=60, IFVG_TOL=0.0015, IFVG_MIN_GAP=0.15

OJO: el backtest usa cooldown=3/max_day=4 (STD featurelab) mientras el Rust usa
cooldown=6/max_day=2 — se reportan AMBAS configs para ver el gap.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import numpy as np
import featurelab as FL
import _listas2 as L2
from _strategy_ab import run_system
from _audit_mirror import gen_h21_short
from _ict_ifvg import gen_ifvg

def f_h1(a, bar, side, entry):
    if bar < 4: return True
    return (float(a.c[bar]) > float(a.c[bar-4])) if side=='long' else (float(a.c[bar]) < float(a.c[bar-4]))

def f_dist(a, bar, side, entry):
    return abs(float(a.c[bar]) - float(entry)) >= 0.5 * float(a.atr[bar])

filt = lambda a,i,s,e: f_h1(a,i,s,e) and f_dist(a,i,s,e)

def wrap(gens, f):
    out = []
    for gen in gens:
        def make(g, fn):
            def w(a, i):
                r = g(a, i)
                if not r: return r
                return [t for t in r if fn(a, i, t[0], t[1])] or None
            return w
        out.append(make(gen, f))
    return out

# Params del Rust
RUST_PARAMS = dict(trail_atr=6.0, stop_scale=0.8, mode='routed',
                   volfilter=True, cooldown=6, max_day=2, tf_min=15,
                   min_range=0.0)
# STD featurelab (cooldown=3, max_day=4)
STD_PARAMS  = dict(**{k:v for k,v in FL.STD.items() if k!='tf_min'},
                   trail_atr=6.0, stop_scale=0.8)

oos_days = 109

print("="*80)
print("PARIDAD: Python con filtros H1+dist+IFVG — params Rust vs STD featurelab")
print("="*80)

for label, params in [("Rust params (cd=6, mx=2)", RUST_PARAMS),
                       ("STD featurelab (cd=3, mx=4)", STD_PARAMS)]:
    tf = params.pop('tf_min', 15)
    print(f"\n── {label} ──")
    print(f"{'activo':<8} {'n_oos':>6} {'t/d':>5} {'avgR':>7} {'WR':>5} {'R/año':>7} {'by_kind'}")
    tot_rpya = 0
    for sym in FL.ASSETS:
        if not Path(FL.ASSETS[sym]).exists(): continue
        a, m1 = FL.load(sym)
        gens = wrap([L2.gen_h5(), L2.gen_h21(), gen_h21_short(), gen_ifvg()], filt)
        df = run_system(a, gens, m1, tf, **params)
        o = df[df.oos]
        if len(o) == 0: continue
        avgr = o.r.mean(); wr = 100*(o.r>0).mean()
        rpya = o.r.sum()/oos_days*365
        tot_rpya += rpya
        wins = o[o.r>0]; loss = o[o.r<=0]
        avgw = wins.r.mean() if len(wins) else 0
        avgl = loss.r.mean() if len(loss) else 0
        rr   = avgw/abs(avgl) if avgl else 0
        print(f"{sym:<8} {len(o):>6} {len(o)/oos_days:>5.1f} {avgr:>+7.3f} {wr:>4.0f}% {rpya:>+7.0f}  avgW={avgw:>+.3f} avgL={avgl:>+.3f} RR={rr:.2f}")
    print(f"  PORTFOLIO total R/año: {tot_rpya:>+.0f}")
    params['tf_min'] = tf  # restore

print("""
Diferencias esperadas Rust vs backtest:
  cooldown 6 vs 3  → menos trades (Rust más conservador)
  max_day  2 vs 4  → menos trades por día (Rust más conservador)
  HIGH_VOL_ONLY    → Rust puede tener env=false (ambos modos testeados)
  Fill simulation  → Rust: precio cruza nivel en M1/tick; Python: M1 bars
""")
