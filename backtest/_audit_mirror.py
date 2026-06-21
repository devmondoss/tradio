"""
_audit_mirror.py — #1 del backtest: MIRROR CORTO del POC defendido (gen_h21_short)
=================================================================================
gen_h21 hoy SOLO compra soportes de volumen (long-only) -> cartera ~80% longs.
Espejo: VENDER en resistencias de volumen DEFENDIDAS >=2 veces (precio toca el nivel
desde ABAJO y lo rechaza). Simétrico exacto al long, con target estructural hacia abajo.

Motor honesto: M15, salida M1, filtro vol ON, fills maker 2bps, riesgo fijo. Reusa run_audit.
Compara: BASE (h5+h21) vs h21_short solo vs cartera+mirror. Reporta balance long/short.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _audit_edge import run_audit, summ


def gen_h21_short(K=15, tol=0.002):
    """ESPEJO de gen_h21: POC de resistencia defendido >=2 veces + rechazo -> SHORT.
    touches = nº de máximos previos pegados al nivel (rechazos desde abajo).
    Entrada límite maker en lvl, stop = lvl + 0.6*ATR, target estructural LEJANO abajo."""
    def g(a, i):
        if i < K: return
        win = a.fp_poc[i-K:i]; win = win[np.isfinite(win)]
        if len(win) < 3: return
        lvl = np.median(win)
        touches = np.sum(np.abs(a.h[i-K:i]-lvl)/lvl <= tol)   # toques desde ARRIBA (resistencia)
        if touches >= 2 and a.c[i] < a.c[i-1] and abs(a.h[i]-lvl)/lvl <= tol:  # rechazo + máximo actual pega
            stop = lvl + 0.6*a.atr[i]
            tp1, tp2 = L2.struct_target(a, i, "short", lvl)
            if np.isfinite(tp2): return [("short", lvl, stop, tp1, tp2, "H21s")]
        return
    return g


def balance(df, label):
    if len(df) == 0:
        print(f"{label:<26} SIN TRADES"); return
    nl = (df.side == "long").sum(); ns = (df.side == "short").sum()
    print(f"{label:<26} long {nl:>3} ({100*nl/len(df):>3.0f}%) / short {ns:>3} ({100*ns/len(df):>3.0f}%)")


def main():
    tf = 15
    t = L2.load2(tf, start_ms=L2.TICK_MS)
    a = L2.A2(t)
    m1 = L2.load_m1_exit(start_ms=L2.TICK_MS)
    span = (a.ts.max()-a.ts.min())/86_400_000
    print(f"TF=M{tf} span={span:.0f}d  OOS>=2026-03-01  (motor honesto, salida M1, filtro vol ON)\n")

    print("=== ¿El mirror tiene edge POR SÍ SOLO? ===")
    mir = run_audit(a, [gen_h21_short()], m1, tf)
    summ(mir, "H21s (mirror short)")
    print()

    print("=== Cartera: BASE vs +mirror ===")
    base = run_audit(a, [L2.gen_h5(), L2.gen_h21()], m1, tf)
    full = run_audit(a, [L2.gen_h5(), L2.gen_h21(), gen_h21_short()], m1, tf)
    summ(base, "BASE (h5+h21)")
    summ(full, "+mirror (h5+h21+h21s)")
    print()

    print("=== Balance long/short ===")
    balance(base, "BASE")
    balance(full, "+mirror")
    print()

    # ¿el mirror aguanta fills pesimistas? (la vulnerabilidad real del edge)
    print("=== Mirror bajo fills pesimistas ===")
    for mg in [2.0, 10.0]:
        df = run_audit(a, [gen_h21_short()], m1, tf, margin=mg)
        summ(df, f"H21s fill {mg:.0f}bps")


if __name__ == "__main__":
    main()
