"""
_fabio_lab.py — laboratorio del modelo de continuación (Direction→Location→Aggression).
================================================================================
Carga los 3 activos UNA vez, después barre cada dimensión por separado (secuencial,
no cartesiano completo — protocolo del proyecto: un eje por vez, se fija el mejor y
se pasa al siguiente). Regla dura: peorOOS de los 3 activos > 0 para "pasar".

Ejes, en orden:
  1. Management  : trail_atr [2,3,4,6,8] vs fixed (target estructural)
  2. Stop        : stop_atr [0.3,0.5,0.8,1.2]
  3. Location    : qué niveles (val/poc/ema20, solos o combinados) + tol_atr
  4. Aggression  : vr_thr + delta_min
  5. Direction   : AND (H1+H4) vs OR vs H1-solo vs H4-solo

Uso: python backtest/_fabio_lab.py
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parent))
import _scalp as SC
from _scalp import struct_tp, run_setup, stats, ASSETS
from _fabio_continuation import attach_htf

SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
TF = 15
HOLDOUT_MS = int(pd.Timestamp("2026-05-01", tz="UTC").value // 1_000_000)  # nunca se toca durante el tuning

_CACHE = {}


def get_s(sym):
    if sym not in _CACHE:
        s = SC.load(sym, TF)
        attach_htf(s, sym, TF)
        m1 = SC.load_m1_exit(sym)
        _CACHE[sym] = (s, m1)
    return _CACHE[sym]


def make_gen(direction="and", loc_l=("vp_val", "vp_poc", "ema20"), loc_s=("vp_vah", "vp_poc", "ema20"),
             tol_atr=0.5, stop_atr=0.5, vr_thr=1.5, delta_min=0.05):
    def dir_bull(s, i):
        if direction == "and": return (not s.h1_bearish[i]) and (not s.h4_bearish[i])
        if direction == "or":  return (not s.h1_bearish[i]) or (not s.h4_bearish[i])
        if direction == "h1":  return not s.h1_bearish[i]
        if direction == "h4":  return not s.h4_bearish[i]
        return True

    def dir_bear(s, i):
        if direction == "and": return s.h1_bearish[i] and s.h4_bearish[i]
        if direction == "or":  return s.h1_bearish[i] or s.h4_bearish[i]
        if direction == "h1":  return s.h1_bearish[i]
        if direction == "h4":  return s.h4_bearish[i]
        return True

    def g(s, i):
        out = []
        bull, bear = dir_bull(s, i), dir_bear(s, i)
        if not (bull or bear): return out
        if s.vr[i] < vr_thr: return out
        atr = s.atr[i]
        fpd = getattr(s, "fp_delta", None)
        if bull:
            for key in loc_l:
                lvl = getattr(s, key)[i]
                if not np.isfinite(lvl) or lvl >= s.c[i]: continue
                if abs(s.l[i] - lvl) <= tol_atr * atr and s.c[i] > lvl:
                    ok = (fpd[i] >= delta_min * s.volume[i]) if fpd is not None else (s.delta[i] >= delta_min * s.volume[i])
                    if not ok: continue
                    stop = lvl - stop_atr * atr
                    tp1, tp2 = struct_tp(s, i, "long", lvl)
                    if np.isfinite(tp2): out.append(("long", lvl, stop, tp1, tp2, "fabio")); break
        if bear:
            for key in loc_s:
                lvl = getattr(s, key)[i]
                if not np.isfinite(lvl) or lvl <= s.c[i]: continue
                if abs(s.h[i] - lvl) <= tol_atr * atr and s.c[i] < lvl:
                    ok = (fpd[i] <= -delta_min * s.volume[i]) if fpd is not None else (s.delta[i] <= -delta_min * s.volume[i])
                    if not ok: continue
                    stop = lvl + stop_atr * atr
                    tp1, tp2 = struct_tp(s, i, "short", lvl)
                    if np.isfinite(tp2): out.append(("short", lvl, stop, tp1, tp2, "fabio")); break
        return out
    return g


def run_combo_dfs(gen_kwargs, mgmt="trail", trail_atr=4.0):
    """Corre y devuelve los trades CRUDOS por símbolo (sin filtrar) — se filtra después."""
    dfs = {}
    for sym in SYMS:
        s, m1 = get_s(sym)
        g = make_gen(**gen_kwargs)
        dfs[sym] = run_setup(s, g, m1, TF, entry_mode="maker", mgmt=mgmt, trail_atr=trail_atr,
                             timeout_min=24 * 60, cooldown=6, max_day=4)
    return dfs


def run_combo(gen_kwargs, mgmt="trail", trail_atr=4.0):
    """Para TUNING: solo ve datos < HOLDOUT_MS (el holdout no se toca hasta el final)."""
    dfs = run_combo_dfs(gen_kwargs, mgmt, trail_atr)
    per = {}
    for sym in SYMS:
        df = dfs[sym]
        train = df[df.ts < HOLDOUT_MS] if len(df) else df
        per[sym] = stats(train)
    return per


def holdout_stats(gen_kwargs, mgmt="trail", trail_atr=4.0):
    """Evaluación ÚNICA sobre el holdout nunca visto (ts >= HOLDOUT_MS)."""
    dfs = run_combo_dfs(gen_kwargs, mgmt, trail_atr)
    per = {}
    for sym in SYMS:
        df = dfs[sym]
        hold = df[df.ts >= HOLDOUT_MS] if len(df) else df
        st = stats(hold)
        st["holdout_avgR"] = hold.r.mean() if len(hold) else float("nan")
        st["holdout_n"] = len(hold)
        st["holdout_wr"] = 100 * (hold.r > 0).mean() if len(hold) else float("nan")
        per[sym] = st
    return per


MIN_N = 40   # piso de muestra por activo en TRAIN — descalifica combos que colapsan frecuencia


def report(label, per, min_n=MIN_N):
    cells = []
    worst = 99
    min_n_seen = min(per[sym]["n"] for sym in SYMS)
    for sym in SYMS:
        st = per[sym]
        oos = st["oosA"] if st["n_oos"] > 0 else -99
        worst = min(worst, oos)
        cells.append(f"{sym[:3]} n{st['n']:>4} IS{st['isA']:+.2f} OOS{oos:+.2f} DD{st['dd']:4.1f}%")
    disqualified = min_n_seen < min_n
    if disqualified:
        mark = f"  <-- DESCALIFICADO (n<{min_n})"
        score = -99
    else:
        mark = "  <-- PASA" if worst > 0 else ""
        score = worst
    print(f"  {label:<38} | {cells[0]} | {cells[1]} | {cells[2]} | peorOOS {worst:+.3f}{mark}")
    return score


def main():
    print(f"Cargando {len(SYMS)} activos M{TF}...", flush=True)
    for sym in SYMS: get_s(sym)

    base = dict(direction="and", loc_l=("vp_val", "vp_poc", "ema20"), loc_s=("vp_vah", "vp_poc", "ema20"),
                tol_atr=0.5, stop_atr=0.5, vr_thr=1.5, delta_min=0.05)

    # ── 1. Management ──────────────────────────────────────────────────────
    print("\n== 1. MANAGEMENT (trail_atr vs fixed) ==")
    best_mgmt, best_trail, best_worst = "trail", 4.0, -99
    for ta in (2.0, 3.0, 4.0, 6.0, 8.0):
        per = run_combo(base, mgmt="trail", trail_atr=ta)
        w = report(f"trail_atr={ta}", per)
        if w > best_worst: best_worst, best_mgmt, best_trail = w, "trail", ta
    per = run_combo(base, mgmt="fixed")
    w = report("fixed (target estructural)", per)
    if w > best_worst: best_worst, best_mgmt, best_trail = w, "fixed", 4.0
    print(f"  >> mejor: mgmt={best_mgmt} trail_atr={best_trail} (peorOOS {best_worst:+.3f})")

    # ── 2. Stop ─────────────────────────────────────────────────────────────
    print("\n== 2. STOP (stop_atr) ==")
    best_stop, best_worst2 = base["stop_atr"], -99
    for sa in (0.3, 0.5, 0.8, 1.2, 1.8):
        gk = dict(base, stop_atr=sa)
        per = run_combo(gk, mgmt=best_mgmt, trail_atr=best_trail)
        w = report(f"stop_atr={sa}", per)
        if w > best_worst2: best_worst2, best_stop = w, sa
    print(f"  >> mejor: stop_atr={best_stop} (peorOOS {best_worst2:+.3f})")

    # ── 3. Location ────────────────────────────────────────────────────────
    print("\n== 3. LOCATION (niveles + tol_atr) ==")
    loc_variants = [
        ("val+poc+ema", ("vp_val", "vp_poc", "ema20"), ("vp_vah", "vp_poc", "ema20")),
        ("solo val/vah", ("vp_val",), ("vp_vah",)),
        ("solo poc", ("vp_poc",), ("vp_poc",)),
        ("solo ema20", ("ema20",), ("ema20",)),
        ("val+poc (sin ema)", ("vp_val", "vp_poc"), ("vp_vah", "vp_poc")),
    ]
    best_loc, best_worst3 = loc_variants[0], -99
    for name, ll, ls in loc_variants:
        gk = dict(base, stop_atr=best_stop, loc_l=ll, loc_s=ls)
        per = run_combo(gk, mgmt=best_mgmt, trail_atr=best_trail)
        w = report(f"loc={name}", per)
        if w > best_worst3: best_worst3, best_loc = w, (name, ll, ls)
    print(f"  >> mejor: loc={best_loc[0]} (peorOOS {best_worst3:+.3f})")

    best_worst4 = -99; best_tol = base["tol_atr"]
    for tol in (0.3, 0.5, 0.8, 1.2):
        gk = dict(base, stop_atr=best_stop, loc_l=best_loc[1], loc_s=best_loc[2], tol_atr=tol)
        per = run_combo(gk, mgmt=best_mgmt, trail_atr=best_trail)
        w = report(f"tol_atr={tol}", per)
        if w > best_worst4: best_worst4, best_tol = w, tol
    print(f"  >> mejor: tol_atr={best_tol} (peorOOS {best_worst4:+.3f})")

    # ── 4. Aggression ─────────────────────────────────────────────────────
    print("\n== 4. AGGRESSION (vr_thr + delta_min) ==")
    best_vr, best_worst5 = base["vr_thr"], -99
    for vr in (1.0, 1.5, 2.0, 2.5, 3.0):
        gk = dict(base, stop_atr=best_stop, loc_l=best_loc[1], loc_s=best_loc[2], tol_atr=best_tol, vr_thr=vr)
        per = run_combo(gk, mgmt=best_mgmt, trail_atr=best_trail)
        w = report(f"vr_thr={vr}", per)
        if w > best_worst5: best_worst5, best_vr = w, vr
    print(f"  >> mejor: vr_thr={best_vr} (peorOOS {best_worst5:+.3f})")

    best_dm, best_worst6 = base["delta_min"], -99
    for dm in (0.0, 0.05, 0.15, 0.3, 0.5):
        gk = dict(base, stop_atr=best_stop, loc_l=best_loc[1], loc_s=best_loc[2], tol_atr=best_tol,
                  vr_thr=best_vr, delta_min=dm)
        per = run_combo(gk, mgmt=best_mgmt, trail_atr=best_trail)
        w = report(f"delta_min={dm}", per)
        if w > best_worst6: best_worst6, best_dm = w, dm
    print(f"  >> mejor: delta_min={best_dm} (peorOOS {best_worst6:+.3f})")

    # ── 5. Direction ───────────────────────────────────────────────────────
    print("\n== 5. DIRECTION (estrictez HTF) ==")
    best_dir, best_worst7 = base["direction"], -99
    for d in ("and", "or", "h1", "h4"):
        gk = dict(base, stop_atr=best_stop, loc_l=best_loc[1], loc_s=best_loc[2], tol_atr=best_tol,
                  vr_thr=best_vr, delta_min=best_dm, direction=d)
        per = run_combo(gk, mgmt=best_mgmt, trail_atr=best_trail)
        w = report(f"direction={d}", per)
        if w > best_worst7: best_worst7, best_dir = w, d
    print(f"  >> mejor: direction={best_dir} (peorOOS {best_worst7:+.3f})")

    # ── Combo final (tuning, sigue sin tocar el holdout) ─────────────────────
    print("\n== COMBO FINAL (mejor de cada eje, medido en TRAIN < 2026-05-01) ==")
    final_gk = dict(direction=best_dir, loc_l=best_loc[1], loc_s=best_loc[2], tol_atr=best_tol,
                     stop_atr=best_stop, vr_thr=best_vr, delta_min=best_dm)
    per = run_combo(final_gk, mgmt=best_mgmt, trail_atr=best_trail)
    report("FINAL (train)", per)
    print(f"\n  mgmt={best_mgmt} trail_atr={best_trail} stop_atr={best_stop} loc={best_loc[0]} "
          f"tol_atr={best_tol} vr_thr={best_vr} delta_min={best_dm} direction={best_dir}")

    # ── HOLDOUT — se toca UNA sola vez, acá ───────────────────────────────────
    print(f"\n== HOLDOUT REAL (2026-05-01 -> 2026-07-06, nunca visto durante el tuning) ==")
    hper = holdout_stats(final_gk, mgmt=best_mgmt, trail_atr=best_trail)
    worst_h = 99
    for sym in SYMS:
        st = hper[sym]
        worst_h = min(worst_h, st["holdout_avgR"] if st["holdout_n"] > 0 else -99)
        print(f"  {sym:<10} n={st['holdout_n']:>3}  avgR={st['holdout_avgR']:+.3f}  WR={st['holdout_wr']:.0f}%")
    mark = "  <-- PASA (edge real, no artefacto de tuning)" if worst_h > 0 else "  <-- NO PASA (era overfitting)"
    print(f"\n  peor holdout: {worst_h:+.3f}{mark}")


if __name__ == "__main__":
    main()
