"""
_scalp_target.py — ¿cómo conviene poner el TARGET de sc3? estructural vs RR vs ATR.
================================================================================
Mismo disparador/entrada/stop que sc3; varía SOLO el target:
  struct : próximo nivel VP, clipeado a tmult×riesgo (lo de hoy)
  rr     : entry ± tmult×riesgo  (RR fijo puro)
  atr    : entry ± tmult×ATR     (ATR puro, independiente del stop)
Gestión fade (parcial 50% en tp1 → BE → tp2). Reporta por activo IS/OOS/WR/n.
Uso: python backtest/_scalp_target.py
"""
import sys
import numpy as np, pandas as pd
sys.path.insert(0, "backtest")
from _scalp import (load as sc_load, load_m1_exit as sc_m1exit, run_setup, stats,
                    struct_tp, _clip_rr, OOS_MS)

VR = {"BTCUSDT": 2.5, "ETHUSDT": 1.5, "SOLUSDT": 2.5}
SYMS = list(VR)
STOP_ATR = 0.5; TOL_ATR = 0.6


def _target(s, i, side, lvl, stop, risk, atr, mode, tmult):
    if mode == "struct":
        tp1, tp2 = struct_tp(s, i, side, lvl)
        return _clip_rr(s, i, side, lvl, stop, tp1, tp2, tmult)
    if mode == "rr":
        far = tmult*risk
        tp2 = lvl + far if side == "long" else lvl - far
        tp1 = lvl + 0.5*far if side == "long" else lvl - 0.5*far
        return tp1, tp2
    if mode == "atr":
        far = tmult*atr
        tp2 = lvl + far if side == "long" else lvl - far
        tp1 = lvl + 0.5*far if side == "long" else lvl - 0.5*far
        return tp1, tp2
    return None, np.nan


def gen_tgt(vr_thr, mode, tmult):
    def g(s, i):
        out = []
        if s.vr[i] < vr_thr: return out
        fpd = getattr(s, "fp_delta", None); atr = s.atr[i]
        for lvl in (s.vp_val[i], s.vp_poc[i]):
            if not np.isfinite(lvl) or lvl >= s.c[i]: continue
            if abs(s.l[i]-lvl) <= TOL_ATR*atr and s.c[i] > lvl and (fpd is None or fpd[i] < 0):
                stop = lvl - STOP_ATR*atr; mr = 0.0015*lvl
                if abs(lvl-stop) < mr: stop = lvl-mr
                risk = lvl-stop
                tp1, tp2 = _target(s, i, "long", lvl, stop, risk, atr, mode, tmult)
                if np.isfinite(tp2): out.append(("long", lvl, stop, tp1, tp2, "tgt")); break
        for lvl in (s.vp_vah[i], s.vp_poc[i]):
            if not np.isfinite(lvl) or lvl <= s.c[i]: continue
            if abs(s.h[i]-lvl) <= TOL_ATR*atr and s.c[i] < lvl and (fpd is None or fpd[i] > 0):
                stop = lvl + STOP_ATR*atr; mr = 0.0015*lvl
                if abs(lvl-stop) < mr: stop = lvl+mr
                risk = stop-lvl
                tp1, tp2 = _target(s, i, "short", lvl, stop, risk, atr, mode, tmult)
                if np.isfinite(tp2): out.append(("short", lvl, stop, tp1, tp2, "tgt")); break
        return out
    return g


def main():
    print("Cargando 3 activos M5...", flush=True)
    data = {sym: (sc_load(sym, 5), sc_m1exit(sym)) for sym in SYMS}
    GRID = [("struct", m) for m in (1.5, 2.0, 2.5, 3.0, 99)] + \
           [("rr", m) for m in (1.0, 1.5, 2.0, 2.5, 3.0)] + \
           [("atr", m) for m in (1.0, 1.5, 2.0, 2.5, 3.0)]
    print(f"\n  {'target':<14} | {'BTC IS/OOS WR':>20} {'ETH IS/OOS WR':>20} {'SOL IS/OOS WR':>20} | peorOOS")
    print("  " + "-"*92)
    last_mode = None
    for mode, tmult in GRID:
        if last_mode and mode != last_mode: print("  " + "·"*92)
        last_mode = mode
        cells = []; worst = 99
        for sym in SYMS:
            s, m1 = data[sym]
            df = run_setup(s, gen_tgt(VR[sym], mode, tmult), m1, 5, entry_mode="maker",
                           mgmt="fade", timeout_min=240, stop_floor_pct=0.0, min_rr=0.5)
            st = stats(df); worst = min(worst, st["oosA"])
            cells.append(f"{st['isA']:+.2f}/{st['oosA']:+.2f}/{st['wr']:.0f}%({st['n']})")
        tag = f"{mode} {tmult}" if tmult != 99 else f"{mode} noclip"
        star = "  ←" if worst >= 0.50 else ""
        print(f"  {tag:<14} | {cells[0]:>20} {cells[1]:>20} {cells[2]:>20} | {worst:+.2f}{star}")
    print("\n  celda = IS/OOS avgR / WR (n).  peorOOS = el peor de los 3 (regla dura).  hoy = struct 2.5")


if __name__ == "__main__":
    main()
