"""
_scalp_more.py — palancas de N (frecuencia) y avgR (calidad) sobre sc3.
================================================================================
Mide la frontera N ↔ avgR de varias variantes en los 3 activos (OOS honesto):
  MÁS N    : más niveles de entrada (+PDH/PDL/weekly/swing), max_day↑, cooldown↓, vr↓
  MEJOR R  : confluencia (≥2 niveles juntos), absorción más fuerte (vr↑+delta), chop-only
Uso: python backtest/_scalp_more.py
"""
import sys
import numpy as np, pandas as pd
sys.path.insert(0, "backtest")
from _scalp import (load as sc_load, load_m1_exit as sc_m1exit, run_setup, stats,
                    struct_tp, _clip_rr, OOS_MS)

VR = {"BTCUSDT": 2.5, "ETHUSDT": 1.5, "SOLUSDT": 2.5}
LONG_LV  = {"val":"vp_val","poc":"vp_poc","pdl":"prev_day_low","wl":"weekly_low","swl":"swing_low_50"}
SHORT_LV = {"vah":"vp_vah","poc":"vp_poc","pdh":"prev_day_high","wh":"weekly_high","swh":"swing_high_50"}


def gen_sc3x(vr_thr, stop_atr=0.5, tol_atr=0.6, rr_cap=2.5, longk=("val","poc"),
             shortk=("vah","poc"), delta_gate=True, confluence=0, chop_only=False,
             delta_min=0.0, confirm=False, divlen=0, block_ct=False):
    """sc3 ampliado: niveles configurables + filtros de calidad.
    confirm  (#3): exige vela de giro (c>o long / c<o short) = delta-flip causal.
    divlen   (#4): exige divergencia delta multi-barra (precio nuevo extremo W pero delta decelera).
    block_ct (#5): bloquea fadear contra tendencia fuerte (TrendDown long / TrendUp short)."""
    TREND_DN = ("trenddown",); TREND_UP = ("trendup",)
    def g(s, i):
        out = []
        if s.vr[i] < vr_thr: return out
        if chop_only and str(s.reg[i]).lower() not in ("chop","range","balance","consolidation"):
            return out
        fpd = getattr(s, "fp_delta", None)
        atr = s.atr[i]; reg = str(s.reg[i]).lower()
        all_lv = [getattr(s, a)[i] for a in set(list(LONG_LV.values())+list(SHORT_LV.values()))]
        all_lv = [x for x in all_lv if np.isfinite(x)]
        def conf_ok(lvl):
            if confluence <= 0: return True
            return sum(1 for x in all_lv if abs(x-lvl) <= tol_atr*atr) >= confluence
        def div_ok(side):   # #4: precio nuevo extremo W pero delta del bar menos extremo que W atrás
            if divlen <= 0 or i < divlen or fpd is None: return True
            if side == "long":
                return s.l[i] <= np.nanmin(s.l[i-divlen:i]) and np.isfinite(fpd[i-divlen]) and fpd[i] > fpd[i-divlen]
            return s.h[i] >= np.nanmax(s.h[i-divlen:i]) and np.isfinite(fpd[i-divlen]) and fpd[i] < fpd[i-divlen]
        # LONG: absorción en soporte
        for key in longk:
            lvl = getattr(s, LONG_LV[key])[i]
            if not np.isfinite(lvl) or lvl >= s.c[i]: continue
            if abs(s.l[i]-lvl) <= tol_atr*atr and s.c[i] > lvl:
                if delta_gate and (fpd is None or fpd[i] >= -delta_min*s.volume[i]): continue
                if confirm and not (s.c[i] > s.o[i]): continue          # #3
                if block_ct and reg in TREND_DN: continue               # #5
                if not div_ok("long"): continue                        # #4
                if not conf_ok(lvl): continue
                stop = lvl-stop_atr*atr; tp1, tp2 = struct_tp(s, i, "long", lvl)
                tp1, tp2 = _clip_rr(s, i, "long", lvl, stop, tp1, tp2, rr_cap)
                if np.isfinite(tp2): out.append(("long", lvl, stop, tp1, tp2, "sc3x")); break
        for key in shortk:
            lvl = getattr(s, SHORT_LV[key])[i]
            if not np.isfinite(lvl) or lvl <= s.c[i]: continue
            if abs(s.h[i]-lvl) <= tol_atr*atr and s.c[i] < lvl:
                if delta_gate and (fpd is None or fpd[i] <= delta_min*s.volume[i]): continue
                if confirm and not (s.c[i] < s.o[i]): continue          # #3
                if block_ct and reg in TREND_UP: continue               # #5
                if not div_ok("short"): continue                       # #4
                if not conf_ok(lvl): continue
                stop = lvl+stop_atr*atr; tp1, tp2 = struct_tp(s, i, "short", lvl)
                tp1, tp2 = _clip_rr(s, i, "short", lvl, stop, tp1, tp2, rr_cap)
                if np.isfinite(tp2): out.append(("short", lvl, stop, tp1, tp2, "sc3x")); break
        return out
    return g


# variantes: (label, gen_kwargs, run_kwargs)
ALLK_L = ("val","poc","pdl","wl","swl"); ALLK_S = ("vah","poc","pdh","wh","swh")
VARIANTS = [
    ("V0 baseline (vp, 3/día)",        dict(), dict(max_day=3, cooldown=6)),
    ("V1 +niveles (todos)",            dict(longk=ALLK_L, shortk=ALLK_S), dict(max_day=3, cooldown=6)),
    ("V2 +niveles +6/día +cd3",        dict(longk=ALLK_L, shortk=ALLK_S), dict(max_day=6, cooldown=3)),
    ("V3 +niveles +10/día +cd2 +vr↓",  dict(longk=ALLK_L, shortk=ALLK_S, vr_thr=1.5), dict(max_day=10, cooldown=2)),
    ("Q1 confluencia≥2 (calidad)",     dict(longk=ALLK_L, shortk=ALLK_S, confluence=2), dict(max_day=6, cooldown=3)),
    ("Q2 absorción fuerte (vr3+chop)", dict(vr_thr=3.0, chop_only=True), dict(max_day=3, cooldown=6)),
]


def run_variant(data, gkw, rkw):
    per = {}
    for sym, (s, m1) in data.items():
        gk = dict(gkw); gk.setdefault("vr_thr", VR[sym])
        df = run_setup(s, gen_sc3x(**gk), m1, 5, entry_mode="maker", mgmt="fade",
                       timeout_min=240, **rkw)
        per[sym] = (df, stats(df))
    return per


def main():
    print("Cargando 3 activos M5...", flush=True)
    data = {}
    for sym in VR:
        data[sym] = (sc_load(sym, 5), sc_m1exit(sym))
    spans = {sym: (data[sym][0].ts.max()-data[sym][0].ts.min())/86_400_000 for sym in VR}
    print(f"\n{'variante':<34} | {'BTC N/d OOS':>16} {'ETH N/d OOS':>16} {'SOL N/d OOS':>16} | tot/d OOSμ")
    print("-"*100)
    for label, gkw, rkw in VARIANTS:
        per = run_variant(data, gkw, rkw)
        cells = []; tot_npd = 0; oos_list = []; is_list = []
        worst_oos = 99
        for sym in VR:
            df, st = per[sym]; npd = st["n"]/spans[sym]; tot_npd += npd
            cells.append(f"{npd:4.1f}/d {st['oosA']:+.2f}")
            worst_oos = min(worst_oos, st["oosA"])
            o = df[df.ts >= OOS_MS]; ii = df[df.ts < OOS_MS]
            if len(o): oos_list.append(o.r)
            if len(ii): is_list.append(ii.r)
        oosmu = pd.concat(oos_list).mean() if oos_list else float("nan")
        ismu = pd.concat(is_list).mean() if is_list else float("nan")
        print(f"{label:<34} | {cells[0]:>16} {cells[1]:>16} {cells[2]:>16} | "
              f"{tot_npd:4.1f}/d  IS{ismu:+.2f} OOS{oosmu:+.2f}  peorOOS{worst_oos:+.2f}")
    print("\n  celda = trades/día · OOS avgR.  tot/d = suma 3 activos.  peorOOS = el peor de los 3 (regla dura).")


if __name__ == "__main__":
    main()
