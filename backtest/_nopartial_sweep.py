"""
_nopartial_sweep.py — ¿v3 sin parcial (all-in "de corrido") con RR cap? Sweep de tp2_cap_r.
==========================================================================================
Hipótesis del usuario: el parcial 50%@tp1 + BE es basura; el trade debe ir entero a un
target capeado (estilo SC3, rr_cap=3.0). La reconstrucción tick 2026-06 dijo "keep partial"
PERO eso fue sin cap de RR y sin filtros v2 (H1+dist+IFVG). Se re-evalúa como combo.

Config base: fade-only + H1 + dist>0.5ATR + IFVG + stop_scale=0.8 (= v3 prod).
Variante: use_partial=False, tp2_cap_r ∈ sweep (0 = target estructural sin cap).
Regla dura: mejora IS y OOS en LOS 3 símbolos vs v3-con-parcial.

Uso: python backtest/_nopartial_sweep.py coarse
     python backtest/_nopartial_sweep.py fine --center 2.0
"""
import sys, argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import numpy as np, pandas as pd
from _v3_parity import run_cfg, SYMS

V3 = dict(mode="fade", use_h1=True, use_dist=True, use_ifvg=True, stop_scale=0.8)


def dd_pct(g):
    cap = 500.0; peak = 500.0; dd = 0.0
    for r in g.sort_values("ts").r.values:
        cap += 5 * r; peak = max(peak, cap); dd = max(dd, (peak - cap) / peak)
    return 100 * dd


def eval_one(sym, use_partial, cap):
    df = run_cfg(sym, use_partial=use_partial, tp2_cap_r=cap, **V3)
    o = df[df.oos]; i = df[~df.oos]
    return dict(
        isA=i.r.mean(), isN=i.r.sum(), oosA=o.r.mean(), oosN=o.r.sum(),
        wr=100 * (o.r > 0).mean(), dd=dd_pct(o), n=len(o))


def sweep(caps, label):
    base = {s: eval_one(s, True, 0.0) for s in SYMS}
    print(f"\n== baseline v3 CON parcial (sin cap) ==")
    for s in SYMS:
        b = base[s]
        print(f"  {s}: IS {b['isA']:+.3f}  OOS {b['oosA']:+.3f} (netR {b['oosN']:+.0f}, WR {b['wr']:.0f}%, DD {b['dd']:.1f}%, n={b['n']})")
    print(f"\n== {label}: SIN parcial, all-in a tp2 capeado ==")
    print(f"  {'cap':>5} | " + " | ".join(f"{s[:3]} IS/OOS(netR,DD)" for s in SYMS) + " | regla dura vs v3")
    best = None
    for cap in caps:
        res = {s: eval_one(s, False, cap) for s in SYMS}
        hard = all(res[s]["isA"] > base[s]["isA"] and res[s]["oosA"] > base[s]["oosA"] for s in SYMS)
        pos = all(res[s]["isA"] > 0 and res[s]["oosA"] > 0 for s in SYMS)
        cells = " | ".join(
            f"{res[s]['isA']:+.2f}/{res[s]['oosA']:+.2f} ({res[s]['oosN']:+.0f},{res[s]['dd']:.1f}%)" for s in SYMS)
        tag = "SUPERA v3" if hard else ("valida" if pos else "MUERTA")
        print(f"  {cap:>5.2f} | {cells} | {tag}")
        score = sum(res[s]["oosA"] for s in SYMS)
        if pos and (best is None or score > best[1]):
            best = (cap, score)
    if best:
        print(f"\n  mejor cap válido por sum(OOS avgR): {best[0]:.2f} (suma {best[1]:+.2f})")
    return best


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["coarse", "fine"])
    ap.add_argument("--center", type=float, default=2.0)
    args = ap.parse_args()
    if args.stage == "coarse":
        sweep([1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 0.0], "COARSE (0=sin cap)")
    else:
        c = args.center
        caps = [round(c + d, 1) for d in np.arange(-0.5, 0.51, 0.1)]
        sweep(caps, f"FINE alrededor de {c}")
