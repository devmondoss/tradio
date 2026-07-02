"""
_tp1_rr_sweep.py — ¿tp1 (parcial 50%) a RR FIJO en vez de estructural?
======================================================================
Hoy tp1 = nivel VP opuesto más cercano → RR flotante (mediana ~6R, rango 0.2-44R).
Test: tp1 = entry ± k*risk (k fijo), tp2 estructural intacto, resto = v3 prod
(fade + H1 + dist>0.5ATR + IFVG + stop_scale 0.8, parcial 50% → BE → tp2).

El wrapper replica stop_scale+stop_floor de run_system para calcular el risk FINAL
antes de fijar tp1; luego run_system re-aplica lo mismo (idempotente sobre el stop
ya que pasamos el stop original y él lo escala — por eso acá pre-calculamos igual).
Si tp1 fijo queda fuera de (entry, tp2) run_system lo anula → all-in estructural.

Uso: python backtest/_tp1_rr_sweep.py 0.5 1.0 1.5 2.0 3.0
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import numpy as np, pandas as pd
from _v3_parity import run_cfg, SYMS

V3 = dict(mode="fade", use_h1=True, use_dist=True, use_ifvg=True, stop_scale=0.8)
STOP_SCALE = 0.8
STOP_FLOOR_PCT = 0.15


def final_risk(entry, stop, side):
    s = entry - STOP_SCALE * (entry - stop)
    mr = STOP_FLOOR_PCT / 100.0 * entry
    if abs(entry - s) < mr:
        s = entry - mr if side == "long" else entry + mr
    return abs(entry - s)


def make_tp1_override(k):
    """Devuelve un transformador de niveles: tp1 = entry ± k*risk_final."""
    def tx(levels):
        out = []
        for side, lvl, stop, tp1, tp2, kind in levels:
            r = final_risk(lvl, stop, side)
            t1 = lvl + k * r if side == "long" else lvl - k * r
            out.append((side, lvl, stop, t1, tp2, kind))
        return out
    return tx


def dd_pct(g):
    cap = 500.0; peak = 500.0; dd = 0.0
    for r in g.sort_values("ts").r.values:
        cap += 5 * r; peak = max(peak, cap); dd = max(dd, (peak - cap) / peak)
    return 100 * dd


def run_with_tx(sym, tx):
    # envolver los generadores DESPUÉS de los filtros de run_cfg → monkeypatch simple:
    # run_cfg construye gens internamente; replicamos su lógica mínima aquí.
    from _v3_parity import load, make_filter, wrap
    import _listas2 as L2
    from _strategy_ab import run_system
    from _audit_mirror import gen_h21_short
    from _ict_ifvg import gen_ifvg
    a, m1 = load(sym)
    gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short(), gen_ifvg()]
    f = make_filter(True, True)
    gens = wrap(gens, f)
    if tx is not None:
        def wrap_tx(g):
            def w(a_, i_):
                lv = g(a_, i_) or []
                return tx(lv)
            return w
        gens = [wrap_tx(g) for g in gens]
    return run_system(a, gens, m1, 15, mode="fade", stop_scale=0.8,
                      max_day=4, cooldown=3, min_range=0.0)


def report(label, df):
    o = df[df.oos]; i = df[~df.oos]
    return (f"{label:>12} | IS {i.r.mean():+.3f} | OOS {o.r.mean():+.3f} "
            f"(netR {o.r.sum():+5.0f}, WR {100*(o.r>0).mean():.0f}%, DD {dd_pct(o):.1f}%, n={len(o)})")


if __name__ == "__main__":
    ks = [float(x) for x in sys.argv[1:]] or [0.5, 1.0, 1.5, 2.0, 3.0]
    for sym in SYMS:
        print(f"\n===== {sym} =====")
        base = run_with_tx(sym, None)
        print(report("estructural", base))
        for k in ks:
            df = run_with_tx(sym, make_tp1_override(k))
            print(report(f"tp1={k}R", df))
