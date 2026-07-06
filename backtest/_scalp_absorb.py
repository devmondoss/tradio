"""
_scalp_absorb.py — ¿el DOM real refina el trigger de absorción de sc3?
================================================================================
absorption_ratio = volumen AGRESOR absorbido (footprint) / profundidad PASIVA del
libro en el lado que absorbe (near5 del DOM, ya procesado por crates/ob_parser):
  long  (absorción en soporte): sell_v / near5_bid   ← venta agresora comida por el bid
  short (absorción en resist.): buy_v  / near5_ask   ← compra agresora comida por el ask
Hipótesis: ratio alto = absorción real fuerte = reversal mejor.

Solo ETH/SOL (tienen DOM 365d real; BTC solo 6d → near5 mayormente vacío).
Quintiles de avgR por ratio + test como filtro sobre sc3.

Uso: python backtest/_scalp_absorb.py
"""
import sys
import numpy as np, pandas as pd
sys.path.insert(0, "backtest")
from _scalp import load as sc_load, load_m1_exit as sc_m1exit, run_setup, gen_sc3, OOS_MS

UNI = dict(vr_thr=2.5, stop_atr=0.5, tol_atr=0.6, rr_cap=2.5, poc_frac_thr=0.0)
VR = {"ETHUSDT": 1.5, "SOLUSDT": 2.5}


def analyze(sym):
    sh = sym[:3]
    cfg = dict(UNI); cfg["vr_thr"] = VR[sym]
    s = sc_load(sym, 5); m1 = sc_m1exit(sym)
    df = run_setup(s, gen_sc3(**cfg), m1, 5, entry_mode="maker", mgmt="fade", timeout_min=240)
    idx = {int(t): k for k, t in enumerate(s.ts)}

    ratio, depth, aggr = [], [], []
    for r in df.itertuples():
        i = idx.get(int(r.ts))
        if i is None: ratio.append(np.nan); depth.append(np.nan); aggr.append(np.nan); continue
        bv = float(getattr(s, "buy_v")[i]) if hasattr(s, "buy_v") else np.nan
        sv = float(getattr(s, "sell_v")[i]) if hasattr(s, "sell_v") else np.nan
        j = max(i-1, 0)  # profundidad ANTES de la absorción (causal)
        nb = float(s.near5_bid[j]); na = float(s.near5_ask[j])
        if r.side == "long":
            d = nb; a = sv
        else:
            d = na; a = bv
        depth.append(d); aggr.append(a)
        ratio.append(a/d if (np.isfinite(a) and np.isfinite(d) and d > 0) else np.nan)
    df = df.assign(absorb_ratio=ratio, depth=depth, aggr=aggr)
    df = df.dropna(subset=["absorb_ratio"])
    cov = len(df)
    print(f"\n{'='*64}\n  {sh}  (sc3 trades con DOM near5: {cov})\n{'='*64}")
    if cov < 50:
        print("  cobertura insuficiente"); return

    # quintiles por absorb_ratio
    df["q"] = pd.qcut(df.absorb_ratio, 5, labels=["Q1(↓abs)","Q2","Q3","Q4","Q5(↑abs)"])
    print("  Quintil absorb_ratio:     avgR     OOS      WR    n")
    for q, g in df.groupby("q", observed=True):
        go = g[g.oos]
        print(f"    {str(q):<12} {g.r.mean():+.3f}  {go.r.mean() if len(go) else float('nan'):+.3f}  "
              f"{100*(g.r>0).mean():4.0f}%  {len(g):>4}")
    corr = df[["absorb_ratio","r"]].corr().iloc[0,1]
    print(f"  Pearson(absorb_ratio, r) = {corr:+.3f}")

    # filtro: top mitad por ratio
    med = df.absorb_ratio.median()
    base = df; hi = df[df.absorb_ratio >= med]
    bo, ho = base[base.oos], hi[hi.oos]
    print(f"\n  baseline:        n={len(base):>4} OOS avgR {bo.r.mean():+.3f} (n_oos {len(bo)})")
    print(f"  ratio≥mediana:   n={len(hi):>4} OOS avgR {ho.r.mean():+.3f} (n_oos {len(ho)})  "
          f"Δ {ho.r.mean()-bo.r.mean():+.3f}")
    # filtro fino: top 30%
    q70 = df.absorb_ratio.quantile(0.70); top = df[df.absorb_ratio >= q70]; to = top[top.oos]
    print(f"  ratio≥p70:       n={len(top):>4} OOS avgR {to.r.mean():+.3f} (n_oos {len(to)})  "
          f"Δ {to.r.mean()-bo.r.mean():+.3f}")


def main():
    print("¿El DOM real (profundidad pasiva near5) refina el trigger de absorción de sc3?")
    for sym in ("ETHUSDT", "SOLUSDT"):
        analyze(sym)


if __name__ == "__main__":
    main()
