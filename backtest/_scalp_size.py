"""
_scalp_size.py — sizing por CONVICCIÓN (¿sizear más los trades de alta calidad mejora?).
================================================================================
Convicción por trade = nº de niveles en confluencia + intensidad de volumen (vr) +
fuerza de absorción (|delta footprint|/vol). Si la convicción correlaciona con avgR,
sizear proporcional sube el R total / risk-adjusted sin señal nueva.
Compara equity: FLAT (riesgo fijo) vs TIERED (riesgo escalado por convicción).
Uso: python backtest/_scalp_size.py
"""
import sys
import numpy as np, pandas as pd
sys.path.insert(0, "backtest")
from _scalp import load, load_m1_exit, run_sc3, SC3, OOS_MS
from _scalp_more import LONG_LV, SHORT_LV


def conviction(sym):
    df, _ = run_sc3(sym)
    s = load(sym, 5)
    idx = {int(t): k for k, t in enumerate(s.ts)}
    tol = SC3[sym]["tol_atr"]
    allk = list(set(list(LONG_LV.values()) + list(SHORT_LV.values())))
    conf, vrr, absr = [], [], []
    for r in df.itertuples():
        i = idx.get(int(r.ts))
        if i is None: conf.append(np.nan); vrr.append(np.nan); absr.append(np.nan); continue
        atr = s.atr[i]; lvls = [getattr(s, a)[i] for a in allk]
        lvls = [x for x in lvls if np.isfinite(x)]
        nconf = sum(1 for x in lvls if abs(x - r.entry) <= tol*atr)
        vol = getattr(s, "fp_vol", None); fpd = getattr(s, "fp_delta", None)
        dn = abs(fpd[i]/(vol[i]+1e-9)) if (fpd is not None and vol is not None and np.isfinite(vol[i]) and vol[i] > 0) else np.nan
        conf.append(nconf); vrr.append(s.vr[i]); absr.append(dn)
    df = df.assign(conf=conf, vr=vrr, absr=absr).dropna(subset=["conf"])
    return df


def main():
    print("Cargando + convicción por trade...", flush=True)
    allt = []
    for sym in SC3:
        d = conviction(sym); d["sym"] = sym[:3]; allt.append(d)
    a = pd.concat(allt, ignore_index=True)

    print("\n=== avgR por nº de niveles en CONFLUENCIA (¿más confluencia = mejor?) ===")
    for nc, g in a.groupby(a["conf"].clip(upper=4)):
        go = g[g.ts >= OOS_MS]
        print(f"  confluencia {int(nc)}{'+' if nc==4 else ' '}: n {len(g):4} ({100*len(g)/len(a):2.0f}%)  "
              f"avgR {g.r.mean():+.3f}  OOS {go.r.mean():+.3f}  WR {100*(g.r>0).mean():.0f}%")

    print("\n=== avgR por quintil de vr (intensidad de volumen) ===")
    a["qv"] = pd.qcut(a.vr, 5, labels=["Q1","Q2","Q3","Q4","Q5"], duplicates="drop")
    for q, g in a.groupby("qv", observed=True):
        print(f"  {q}: avgR {g.r.mean():+.3f}  OOS {g[g.ts>=OOS_MS].r.mean():+.3f}")

    # TIER de alta convicción: confluencia 4+ O vr en top-20% (lo que rinde +1.0R)
    a = a.sort_values("ts").reset_index(drop=True)
    vr80 = a.vr.quantile(0.80)
    a["hi"] = (a.conf >= 4) | (a.vr >= vr80)
    hi = a[a.hi]; lo = a[~a.hi]
    print(f"\n  TIER alta convicción (conf≥4 o vr≥p80): n {len(hi)} ({100*len(hi)/len(a):.0f}%)  "
          f"avgR {hi.r.mean():+.3f}  OOS {hi[hi.ts>=OOS_MS].r.mean():+.3f}")
    print(f"  resto:                                  n {len(lo)} ({100*len(lo)/len(a):.0f}%)  "
          f"avgR {lo.r.mean():+.3f}  OOS {lo[lo.ts>=OOS_MS].r.mean():+.3f}")

    def eq(weights):
        cap = 500.0; peak = 500.0; dd = 0.0
        for r, w in zip(a.r.values, weights):
            cap += 5*w*r; peak = max(peak, cap); dd = max(dd, (peak-cap)/peak)
        return cap, 100*dd
    print(f"\n=== EQUITY $500 (mismo riesgo total agregado, neto fees) ===")
    for mult in (1.0, 1.5, 2.0, 3.0):
        w = np.where(a.hi.values, mult, 1.0); w = w*(len(a)/w.sum())   # normaliza riesgo total
        cap, dd = eq(w)
        tag = "FLAT" if mult == 1.0 else f"hi×{mult:.0f}"
        print(f"  {tag:<7}: ${cap:,.0f}  maxDD {dd:.0f}%")


if __name__ == "__main__":
    main()
