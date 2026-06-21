"""
_audit_offset_compound.py — (a) barrido de offset de profundidad  (b) compounding sin retiros
=============================================================================================
(a) El markout mostró −0.8 bps de selección adversa los primeros 30s. ¿Colocar el límite más
    profundo (1-2 bps adentro) mejora el neto? Mejor entrada vs menos fills — se mide el trade-off.
(b) El app usa riesgo FIJO $5/trade (sin compounding) a propósito (comparar por avgR). Aquí, a
    petición: equity con compounding 1% del capital por trade, SIN retiros. (Con caveat: el
    compounding infla y depende del ORDEN de los trades; mirar avgR/WR para juzgar el edge.)
Cartera completa h5+h21+mirror, fee HONESTO, M15, salida M1.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _audit_edge import run_audit, summ
from _audit_mirror import gen_h21_short


def compound_equity(df, risk_frac=0.01, cap0=500.0):
    """Equity con compounding: cada trade arriesga risk_frac del capital actual. Sin retiros."""
    d = df.sort_values("ts")
    eq = cap0
    for r in d.r.values:
        eq *= (1 + risk_frac*r)
        if eq <= 0: return 0.0
    return eq


def main():
    tf = 15
    t = L2.load2(tf, start_ms=L2.TICK_MS)
    a = L2.A2(t)
    m1 = L2.load_m1_exit(start_ms=L2.TICK_MS)
    span = (a.ts.max()-a.ts.min())/86_400_000
    gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]
    print(f"TF=M{tf} span={span:.0f}d  cartera h5+h21+mirror  fee HONESTO\n")

    # ===== (a) BARRIDO DE OFFSET DE PROFUNDIDAD =====
    print("=== (a) Offset de profundidad (colocar el límite N bps más adentro) ===")
    print("    (mejor precio de entrada vs menos fills — neto con fee honesto)\n")
    base_n = None
    for off in [0.0, 1.0, 2.0, 3.0, 5.0, 10.0]:
        df = run_audit(a, gens, m1, tf, honest_fee=True, entry_offset_bps=off)
        if base_n is None: base_n = len(df)
        fill_pct = 100*len(df)/base_n
        o = df[df.oos]
        print(f"  offset {off:>4.0f}bps | n={len(df):>4} (fills {fill_pct:>3.0f}%) | WR {100*(df.r>0).mean():4.1f}% "
              f"| avgR {df.r.mean():+.3f} | netR {df.r.sum():+6.1f} | OOS avgR {o.r.mean():+.3f} netR {o.r.sum():+6.1f}")

    # ===== (b) COMPOUNDING SIN RETIROS =====
    print("\n=== (b) Compounding sin retiros (riesgo 1% del capital por trade, $500 inicial) ===")
    base = run_audit(a, gens, m1, tf, honest_fee=True)
    di, do = base[~base.oos], base[base.oos]
    days_full = (base.ts.max()-base.ts.min())/86_400_000
    days_oos = (do.ts.max()-do.ts.min())/86_400_000 if len(do) else 1

    fixed_full = 500 + 5*base.r.sum()       # riesgo fijo $5 (lo que muestra el app)
    comp_full = compound_equity(base)
    comp_oos = compound_equity(do)
    print(f"  TODO {days_full:.0f}d  n={len(base)}  netR {base.r.sum():+.0f}  avgR {base.r.mean():+.3f}")
    print(f"    riesgo FIJO $5/trade:   $500 -> ${fixed_full:,.0f}   (+{100*(fixed_full-500)/500:.0f}%)")
    print(f"    COMPOUNDING 1%/trade:   $500 -> ${comp_full:,.0f}   (×{comp_full/500:.1f})")
    print(f"  OOS  {days_oos:.0f}d  n={len(do)}  netR {do.r.sum():+.0f}  avgR {do.r.mean():+.3f}")
    print(f"    COMPOUNDING 1%/trade:   $500 -> ${comp_oos:,.0f}   (×{comp_oos/500:.1f} en {days_oos:.0f}d)")

    # sensibilidad al orden (el compounding depende de la secuencia): peor/mejor de barajas simples
    print("\n  Sensibilidad al ORDEN (compounding no es order-invariant):")
    rs = base.sort_values("ts").r.values
    asc = 500.0; desc = 500.0
    for r in np.sort(rs): asc *= (1+0.01*r)            # peores primero
    for r in np.sort(rs)[::-1]: desc *= (1+0.01*r)     # mejores primero
    print(f"    real (cronológico) ${comp_full:,.0f}  |  peores-primero ${asc:,.0f}  |  mejores-primero ${desc:,.0f}")

    # riesgo de ruina: ¿algún trade individual hunde el capital? (peor R)
    print(f"\n  Peor trade individual: {base.r.min():+.1f}R  -> con 1% arriesgado = {100*0.01*base.r.min():+.1f}% del capital")
    print(f"  (con riesgo 1%, un -1R = -1% capital; el sistema NO usa apalancamiento que cause ruina por trade)")


if __name__ == "__main__":
    main()
