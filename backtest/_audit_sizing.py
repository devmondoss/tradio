"""
_audit_sizing.py — ¿Risk-weighting por componente bate al equal-weight? (honesto: pesos IS -> OOS)
=================================================================================================
Componentes con distinto edge. Tilt por Sharpe/avgR IS puede mejorar... o sobreajustar. Test honesto:
los pesos se calculan SOLO con IS y se aplican a OOS. Si el equal-weight gana OOS -> no tiltar.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _audit_edge import run_audit
from _audit_mirror import gen_h21_short

COMPS = ["H5", "H21", "H21s"]


def sharpe(r):  # Sharpe simple por-trade (proporcional; sirve para comparar/pesar)
    return r.mean()/(r.std()+1e-9)*np.sqrt(len(r)) if len(r) > 1 else 0.0


def main():
    tf = 15
    t = L2.load2(tf, start_ms=L2.TICK_MS); a = L2.A2(t)
    m1 = L2.load_m1_exit(start_ms=L2.TICK_MS)
    df = run_audit(a, [L2.gen_h5(), L2.gen_h21(), gen_h21_short()], m1, tf, honest_fee=True)
    di, do = df[~df.oos], df[df.oos]

    print("=== Perfil por componente ===")
    isw = {}
    for c in COMPS:
        si = di[di.kind == c]; so = do[do.kind == c]
        isw[c] = max(sharpe(si.r), 0.0)
        print(f"  {c:<5} | IS avgR {si.r.mean():+.3f} Sh {sharpe(si.r):+.2f} n{len(si):>4} "
              f"| OOS avgR {so.r.mean():+.3f} Sh {sharpe(so.r):+.2f} n{len(so):>4}")

    # pesos por Sharpe IS, normalizados a media 1 (mantiene presupuesto de riesgo comparable)
    s = np.array([isw[c] for c in COMPS]); w_sh = dict(zip(COMPS, s/s.mean()))
    # pesos por avgR IS
    av = np.array([max(di[di.kind == c].r.mean(), 0) for c in COMPS]); w_av = dict(zip(COMPS, av/av.mean()))
    print(f"\n  pesos Sharpe-IS: " + " ".join(f"{c}×{w_sh[c]:.2f}" for c in COMPS))
    print(f"  pesos avgR-IS:   " + " ".join(f"{c}×{w_av[c]:.2f}" for c in COMPS))

    def port(d, w):
        # netR ponderado: cada trade aporta w_c·r ; avgR ponderado normaliza por n total
        rr = d.r.values * d.kind.map(w).values
        return rr.sum(), rr.sum()/len(d), rr.mean()/(rr.std()+1e-9)*np.sqrt(len(d))

    print("\n=== OOS: equal-weight vs tilts (pesos derivados SOLO de IS) ===")
    for name, w in [("equal-weight", {c: 1.0 for c in COMPS}), ("tilt Sharpe-IS", w_sh), ("tilt avgR-IS", w_av)]:
        nr, ar, sh = port(do, w)
        print(f"  {name:<16} OOS netR {nr:+6.1f} | avgR {ar:+.3f} | Sharpe {sh:+.2f}")

    print("\n  (Si equal-weight ≈ o > tilts en OOS -> el tilt sobreajusta IS; mantener equal-weight.)")


if __name__ == "__main__":
    main()
