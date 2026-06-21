"""
_strategy_ab_v2.py — A+B con detectores de régimen PROBADOS (ER / ADX / Choppiness / compuesto)
==============================================================================================
Re-enruta el sistema A+B con los detectores de la literatura en vez de la columna 'regime' tosca.
fade en RANGO, trailing en TENDENCIA. Backtest 365d. Encuentra el mejor detector/umbral.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _audit_mirror import gen_h21_short
from _strategy_ab import run_system, stats, line
from _regime_detectors import efficiency_ratio, adx, choppiness, shift1

TF = 15


def main():
    t = L2.load2(TF, start_ms=L2.TICK_MS); a = L2.A2(t)
    m1 = L2.load_m1_exit(start_ms=L2.TICK_MS)
    gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]

    # indicadores causales (shift 1: conocidos ANTES de la barra de entrada)
    er = shift1(efficiency_ratio(a.c, N=10))
    adx_ = shift1(adx(a.h, a.l, a.c, 14))
    chop = shift1(choppiness(a.h, a.l, a.c, 14))
    print("SISTEMA A+B v2 · detectores de régimen probados · 365d\n")
    print(f"cobertura: ER {np.isfinite(er).mean()*100:.0f}% · ADX {np.isfinite(adx_).mean()*100:.0f}% · CHOP {np.isfinite(chop).mean()*100:.0f}%\n")

    # baselines
    line("A sola (todo fade)", stats(run_system(a, gens, m1, TF, mode="fade")))
    line("B sola (todo trail)", stats(run_system(a, gens, m1, TF, mode="trail")))
    line("A+B (regime tosco)", stats(run_system(a, gens, m1, TF, mode="routed")))
    print()

    # chop_mask = True donde FADE (rango). Trailing donde el detector dice TENDENCIA.
    configs = []
    for thr in [0.30, 0.40, 0.50]:
        configs.append((f"ER trend>{thr}", er < thr))          # ER alto=trend -> trail; bajo=fade
    for thr in [20, 25, 30]:
        configs.append((f"ADX trend>{thr}", adx_ < thr))        # ADX alto=trend
    for thr in [38.2, 50]:
        configs.append((f"CHOP trend<{thr}", chop > thr))       # CHOP bajo=trend
    # compuesto: tendencia si ≥2 de 3 votan tendencia
    trend_votes = (np.nan_to_num(er) >= 0.40).astype(int) + (np.nan_to_num(adx_) >= 25).astype(int) + (np.nan_to_num(chop) <= 38.2).astype(int)
    configs.append(("COMPUESTO ≥2/3 trend", trend_votes < 2))

    best = None; bestkey = None; bo = -1e9
    for name, chop_mask in configs:
        s = stats(run_system(a, gens, m1, TF, mode="routed", chop_mask=chop_mask))
        line(f"  {name}", s)
        if s["oosN"] > bo: bo = s["oosN"]; best = s; bestkey = name

    print(f"\n  Mejor por OOS netR: {bestkey}  (OOS avgR {best['oosA']:+.3f}, netR {best['oosN']:+.1f}, DD {best['dd']:.1f}%, Sh {best['sharpe']:+.1f})")


if __name__ == "__main__":
    main()
