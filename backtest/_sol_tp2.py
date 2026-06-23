"""
_sol_tp2.py — Test variantes de TP2 mas conservador para SOL.
Problema: fade WR=71% OOS pero avgR=+0.571 porque el 50% restante
no llega al target lejano (VAH/swing/PDH/weekly).
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _listas import OOS_MS, FEE_MAKER, FEE_TAKER
from _audit_mirror import gen_h21_short
from _strategy_ab import run_system, is_chop

L2.M1 = Path("E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet")
TF = 15
MK, TK = FEE_MAKER/2, FEE_TAKER/2
TRAIL_ATR = 4.0
EFF_RISK = 5.0 * 0.65 * 0.85   # $2.76 efectivo

t  = L2.load2(TF, start_ms=0)
a  = L2.A2(t)
m1 = L2.load_m1_exit(start_ms=0)
m1ts, m1h, m1l, m1c = m1
gens_fn = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]


def run_tp2(tp2_mode="current", tp2_r=None):
    """
    tp2_mode:
      "current"  — tp2 original (nivel mas lejano)
      "tp1"      — tp2 = tp1 (nivel mas cercano, todo cierra ahi)
      "fixed_r"  — tp2 = entry ± tp2_r * risk
      "frac"     — tp2 = entry ± tp2_r * (original_tp2 - entry)
    """
    bar_ms = TF * 60_000
    atr_med = pd.Series(a.atr).rolling(500, min_periods=50).median().shift(1).values
    trades = []

    for g in gens_fn:
        cool = 0; dcount = {}
        for i in range(60, a.n - 1):
            if i < cool or a.atr[i] <= 0: continue
            if not (np.isfinite(atr_med[i]) and a.atr[i] > atr_med[i]): continue
            d = int(a.day[i])
            if dcount.get(d, 0) >= 4: continue
            for side, lvl, stop, tp1_orig, tp2_orig, kind in (g(a, i) or []):
                if not np.isfinite([lvl, stop, tp2_orig]).all(): continue
                ref = a.c[i - 1]
                if side == "long"  and not (lvl < ref): continue
                if side == "short" and not (lvl > ref): continue
                if side == "long"  and not (a.l[i] <= lvl * (1 - 2e-4)): continue
                if side == "short" and not (a.h[i] >= lvl * (1 + 2e-4)): continue
                entry = lvl; atr0 = a.atr[i]
                risk = abs(entry - stop)
                if risk <= 0: continue

                # Ajustar tp2 segun el modo
                if tp2_mode == "tp1":
                    tp2 = tp1_orig if np.isfinite(tp1_orig) else tp2_orig
                    tp1 = None  # sin parcial, todo al tp2 ajustado
                elif tp2_mode == "fixed_r":
                    tp2 = entry + tp2_r * risk if side == "long" else entry - tp2_r * risk
                    tp1 = tp1_orig
                elif tp2_mode == "frac":
                    dist = abs(tp2_orig - entry)
                    tp2 = (entry + tp2_r * dist) if side == "long" else (entry - tp2_r * dist)
                    tp1 = tp1_orig
                else:  # current
                    tp2 = tp2_orig; tp1 = tp1_orig

                if abs(tp2 - entry) / risk < 1.2: continue

                chop_here = is_chop(a.reg[i])
                j0   = np.searchsorted(m1ts, a.ts[i] + bar_ms)
                jend = np.searchsorted(m1ts, a.ts[i] + bar_ms + 24 * 60 * 60_000)

                if chop_here:  # FADE
                    cur = stop; realized = 0.0; rem = 1.0; f1 = False
                    p1 = 0.5 if tp1 and np.isfinite(tp1) else 0.0
                    reason = "timeout"
                    for j in range(j0, min(jend, len(m1ts))):
                        if side == "long":
                            if m1l[j] <= cur:
                                realized += rem * ((cur - entry) / risk)
                                reason = "be" if f1 else "stop"; break
                            if not f1 and p1 and m1h[j] >= tp1:
                                realized += p1 * ((tp1 - entry) / risk)
                                rem -= p1; f1 = True; cur = entry
                            if m1h[j] >= tp2:
                                realized += rem * ((tp2 - entry) / risk)
                                reason = "target"; break
                        else:
                            if m1h[j] >= cur:
                                realized += rem * ((entry - cur) / risk)
                                reason = "be" if f1 else "stop"; break
                            if not f1 and p1 and m1l[j] <= tp1:
                                realized += p1 * ((entry - tp1) / risk)
                                rem -= p1; f1 = True; cur = entry
                            if m1l[j] <= tp2:
                                realized += rem * ((entry - tp2) / risk)
                                reason = "target"; break
                    else:
                        jj = min(jend, len(m1ts)) - 1
                        if jj <= j0: continue
                        px = m1c[jj]
                        realized += rem * (((px-entry) if side=="long" else (entry-px)) / risk)
                    exit_f = MK if reason == "target" else TK
                    fee_r = (MK + (MK*p1 if f1 else 0) + exit_f*rem) * entry / risk
                    res = realized - fee_r; gestion = "fade"
                else:  # TRAIL
                    fee_r = (MK + TK) * entry / risk
                    best = entry; trail = stop; res = None
                    for j in range(j0, min(jend, len(m1ts))):
                        if side == "long":
                            best = max(best, m1h[j])
                            trail = max(trail, best - TRAIL_ATR * atr0)
                            if m1l[j] <= trail: res = (trail - entry)/risk - fee_r; break
                        else:
                            best = min(best, m1l[j])
                            trail = min(trail, best + TRAIL_ATR * atr0)
                            if m1h[j] >= trail: res = (entry - trail)/risk - fee_r; break
                    if res is None:
                        jj = min(jend, len(m1ts)) - 1
                        if jj <= j0: continue
                        px = m1c[jj]
                        res = ((px-entry) if side=="long" else (entry-px))/risk - fee_r
                    gestion = "trail"

                trades.append(dict(ts=int(a.ts[i]), r=res, gestion=gestion,
                                   oos=int(a.ts[i]) >= OOS_MS, kind=kind))
                cool = i + 3; dcount[d] = dcount.get(d, 0) + 1; break

    return pd.DataFrame(trades)


def stats(df, label):
    if df.empty: return
    o = df[df.oos]; i = df[~df.oos]
    if len(o) == 0: return
    days_o = max(1, (o.ts.max() - o.ts.min()) / 86_400_000)
    pnl = EFF_RISK * o.r.sum() * 365 / days_o
    sh  = o.r.mean() / (o.r.std() + 1e-9) * np.sqrt(252 * len(o)/days_o)
    cap = 500.0; peak = 500.0; dd = 0.0
    for r in o.sort_values("ts").r.values:
        cap += EFF_RISK * r; peak = max(peak, cap); dd = max(dd, (peak-cap)/peak)
    # Mensual mayo
    o2 = o.copy(); o2["m"] = pd.to_datetime(o2.ts, unit="ms").dt.to_period("M")
    may = o2[o2.m == "2026-05"]
    gap = o.r.mean() - (i.r.mean() if len(i) else 0)
    print(f"  {label:<28} IS={i.r.mean():>+.3f}({len(i):>3}) "
          f"OOS={o.r.mean():>+.3f}({len(o):>3}) "
          f"WR={100*(o.r>0).mean():.0f}% "
          f"DD={100*dd:.1f}% Sh={sh:>+.1f} "
          f"PnL=${pnl:>5,.0f} "
          f"gap={gap:>+.3f} "
          f"may={may.r.mean():>+.3f}({len(may)})")


print("SOL — variantes de TP2 (max_day=4 cool=3)")
print(f"  {'Config':<28} {'IS':>10} {'OOS':>12} {'WR':>4} {'DD':>5} {'Sh':>5} {'PnL':>7} {'gap':>7} {'mayo'}")
print("  " + "-"*110)

configs = [
    ("current (tp2=lejos)",     "current", None),
    ("tp2=tp1 (cerca, sin parc)","tp1",    None),
    ("fixed 1.5R",              "fixed_r", 1.5),
    ("fixed 2.0R",              "fixed_r", 2.0),
    ("fixed 2.5R",              "fixed_r", 2.5),
    ("frac 50% dist",           "frac",    0.5),
    ("frac 65% dist",           "frac",    0.65),
    ("frac 80% dist",           "frac",    0.80),
]

for label, mode, param in configs:
    df = run_tp2(tp2_mode=mode, tp2_r=param)
    stats(df, label)
