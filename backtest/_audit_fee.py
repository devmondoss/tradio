"""
_audit_fee.py — Cierre honesto de la re-auditoría: fee TAKER en salidas a mercado
=================================================================================
El motor cobra maker (4bps RT) a TODO. Pero stop/breakeven/timeout salen a mercado = taker
(11bps RT). Solo entrada + tp1 + target son límite (maker). Mide cuánto cuesta esa honestidad.
Cartera completa: h5 + h21 + mirror (gen_h21_short).
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _audit_edge import run_audit, summ
from _audit_mirror import gen_h21_short


def reason_mix(df, label):
    if len(df) == 0: return
    vc = df.reason.value_counts(normalize=True)*100
    parts = " ".join(f"{k}={vc.get(k,0):.0f}%" for k in ("target", "breakeven", "stop", "timeout"))
    taker_share = 100*(~df.reason.eq("target")).mean()
    print(f"  {label:<20} {parts}   | salidas a mercado(taker)={taker_share:.0f}%")


def main():
    tf = 15
    t = L2.load2(tf, start_ms=L2.TICK_MS)
    a = L2.A2(t)
    m1 = L2.load_m1_exit(start_ms=L2.TICK_MS)
    span = (a.ts.max()-a.ts.min())/86_400_000
    gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]
    print(f"TF=M{tf} span={span:.0f}d  cartera h5+h21+mirror  (maker 4bps RT / taker 11bps RT)\n")

    flat = run_audit(a, gens, m1, tf, honest_fee=False)
    hon = run_audit(a, gens, m1, tf, honest_fee=True)

    print("=== Mezcla de salidas (de dónde sale el coste taker) ===")
    reason_mix(flat, "TODO")
    reason_mix(flat[flat.oos], "OOS")
    print()

    print("=== Fee FLAT-maker (motor actual) vs fee HONESTO (taker en stop/BE/timeout) ===")
    summ(flat, "flat-maker (actual)")
    summ(hon, "honesto (taker mercado)")
    print(f"\n  delta avgR (todo): {hon.r.mean()-flat.r.mean():+.3f}   delta OOS avgR: "
          f"{hon[hon.oos].r.mean()-flat[flat.oos].r.mean():+.3f}")
    print(f"  delta netR OOS: {hon[hon.oos].r.sum()-flat[flat.oos].r.sum():+.1f} "
          f"({100*(hon[hon.oos].r.sum()-flat[flat.oos].r.sum())/flat[flat.oos].r.sum():+.0f}%)")

    print("\n=== Coste honesto por componente (OOS) ===")
    for k in ("H5", "H21", "H21s"):
        f = flat[(flat.kind == k) & flat.oos]; h = hon[(hon.kind == k) & hon.oos]
        if len(f): print(f"  {k:<5} OOS avgR  flat {f.r.mean():+.3f} -> honesto {h.r.mean():+.3f}  ({h.r.mean()-f.r.mean():+.3f})")

    print("\n=== Combinado MÁS pesimista: honesto + fills 10bps + piso stop 0.20% ===")
    worst = run_audit(a, gens, m1, tf, honest_fee=True, margin=10.0, stop_floor_pct=0.20)
    summ(worst, "peor caso realista")


if __name__ == "__main__":
    main()
