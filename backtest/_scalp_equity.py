"""
_scalp_equity.py — equity de sc3 partiendo de $500 (portfolio 3 activos).
================================================================================
Simula la cuenta real: combina los trades sc3 de BTC+ETH+SOL en una sola timeline,
riesgo 1% por trade (convención del proyecto), neto de fee honesto. Reporta:
  · sizing FIJO ($5/trade) vs COMPOUNDING (1% del equity vivo)
  · período completo (IS+IS, optimista) vs OOS-only (proxy forward honesto)
  · final, retorno%, maxDD, n
"""
import sys
import numpy as np, pandas as pd
sys.path.insert(0, "backtest")
from _scalp import load as sc_load, load_m1_exit as sc_m1exit, run_setup, gen_sc3, OOS_MS

UNI = dict(stop_atr=0.5, tol_atr=0.6, rr_cap=2.5, poc_frac_thr=0.0)
VR = {"BTCUSDT": 2.5, "ETHUSDT": 1.5, "SOLUSDT": 2.5}
RISK = 0.01; CAP0 = 500.0


def all_trades():
    rows = []
    for sym in VR:
        cfg = dict(UNI); cfg["vr_thr"] = VR[sym]
        s = sc_load(sym, 5); m1 = sc_m1exit(sym)
        df = run_setup(s, gen_sc3(**cfg), m1, 5, entry_mode="maker", mgmt="fade", timeout_min=240)
        df["sym"] = sym[:3]; rows.append(df)
    return pd.concat(rows, ignore_index=True).sort_values("ts").reset_index(drop=True)


def sim(df, compound):
    cap = CAP0; peak = CAP0; dd = 0.0
    for r in df.r.values:
        risk_usd = cap*RISK if compound else CAP0*RISK
        cap += risk_usd*r
        if cap <= 0: cap = 0.0; break
        peak = max(peak, cap); dd = max(dd, (peak-cap)/peak)
    return cap, 100*dd


def report(df, label):
    span = (df.ts.max()-df.ts.min())/86_400_000
    n = len(df); totR = df.r.sum()
    print(f"\n── {label} ── n={n}  span={span:.0f}d  ΣR={totR:+.0f}  avgR={df.r.mean():+.3f}")
    for comp in (False, True):
        cap, dd = sim(df, comp)
        ret = 100*(cap-CAP0)/CAP0
        ann = ((cap/CAP0)**(365/max(span,1))-1)*100 if cap > 0 else -100
        tag = "COMPOUND 1%" if comp else "FIJO $5   "
        print(f"   {tag}: $500 → ${cap:,.0f}  ({ret:+.0f}%)  maxDD {dd:.0f}%  CAGR {ann:+.0f}%/año")


def main():
    df = all_trades()
    print(f"{'='*70}\n  EQUITY sc3 — $500, riesgo 1%/trade, 3 activos, fee honesto (BACKTEST)\n{'='*70}")
    for sym in VR:
        report(df[df.sym == sym[:3]], sym[:3])
    report(df, "PORTFOLIO 3 activos (completo: IS+OOS)")
    report(df[df.ts >= OOS_MS], "PORTFOLIO 3 activos (OOS-only, forward honesto)")
    print(f"\n  ⚠️  BACKTEST con fill asumido en el nivel. avgR real validado n=44 (≈backtest)")
    print(f"     pero adverse-selection real NO testeado. Compounding = irreal hasta probar fills.")


if __name__ == "__main__":
    main()
