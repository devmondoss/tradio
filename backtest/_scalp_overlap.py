"""
_scalp_overlap.py — ¿sc3 es ADITIVO o REDUNDANTE vs el liquidity A+B?
================================================================================
Corre ambos sistemas en los 3 activos y mide el solape temporal+direccional:
  · liquidity A+B (H5+H21+H21s, config per-asset) en M15
  · sc3 (absorción maker) en M5, config unificada
Un trade sc3 es REDUNDANTE si en su mismo bucket M15 (±1) hay un trade liquidity
del MISMO lado. Reporta: % redundante, avgR de sc3-único vs sc3-solapado, y el
portfolio combinado (liquidity + sc3-únicos) vs liquidity solo.

Uso: python backtest/_scalp_overlap.py
"""
import sys
import numpy as np, pandas as pd
sys.path.insert(0, "backtest")
import _listas2 as L2
from _audit_mirror import gen_h21_short
from backtest_footprint_full import run_ab, ASSETS as LIQ
from _scalp import load as sc_load, load_m1_exit as sc_m1exit, run_setup, gen_sc3, OOS_MS

UNI = dict(vr_thr=2.5, stop_atr=0.5, tol_atr=0.6, rr_cap=2.5, poc_frac_thr=0.0)
VR = {"BTCUSDT": 2.5, "ETHUSDT": 1.5, "SOLUSDT": 2.5}
SYMS = [("BTCUSDT", "BTC"), ("ETHUSDT", "ETH"), ("SOLUSDT", "SOL")]
M15 = 900_000


def liq_trades(sym):
    cfg = LIQ[{"BTCUSDT": "BTC", "ETHUSDT": "ETH", "SOLUSDT": "SOL"}[sym]]
    L2.M1 = cfg["m1_path"]
    a = L2.A2(L2.load2(15, start_ms=0)); m1 = L2.load_m1_exit(start_ms=0)
    gens = ([L2.gen_h5()] if cfg["use_h5"] else []) + [L2.gen_h21(), gen_h21_short()]
    df = run_ab(a, gens, m1, timeout_min=cfg["timeout"], margin=cfg["margin"])
    return df.rename(columns={"bar_ts": "ts"})[["ts", "side", "lvl", "r", "oos"]]


def sc3_trades(sym):
    cfg = dict(UNI); cfg["vr_thr"] = VR[sym]
    s = sc_load(sym, 5); m1 = sc_m1exit(sym)
    return run_setup(s, gen_sc3(**cfg), m1, 5, entry_mode="maker", mgmt="fade", timeout_min=240)


def avg(df, col="r"):
    return df[col].mean() if len(df) else float("nan")


def main():
    print(f"{'='*70}\n  SOLAPE sc3 (absorción M5) vs liquidity A+B (M15)\n{'='*70}")
    agg_liq, agg_sc3u = [], []
    for sym, sh in SYMS:
        liq = liq_trades(sym); sc3 = sc3_trades(sym)
        # buckets M15 con lado, para liquidity (±1 bucket de tolerancia)
        liq_keys = set()
        for r in liq.itertuples():
            b = int(r.ts)//M15
            for db in (-1, 0, 1):
                liq_keys.add((b+db, r.side))
        sc3 = sc3.copy()
        sc3["dup"] = [ (int(t)//M15, sd) in liq_keys for t, sd in zip(sc3.ts, sc3.side) ]
        uni = sc3[~sc3.dup]; ov = sc3[sc3.dup]
        liq_oos = liq[liq.oos]; sc3_oos = sc3[sc3.oos]; uni_oos = uni[uni.oos]; ov_oos = ov[ov.oos]
        print(f"\n── {sh} ──")
        print(f"  liquidity: n={len(liq):>4} (oos {len(liq_oos):>3})  avgR {avg(liq):+.3f}  OOS {avg(liq_oos):+.3f}")
        print(f"  sc3 total: n={len(sc3):>4} (oos {len(sc3_oos):>3})  avgR {avg(sc3):+.3f}  OOS {avg(sc3_oos):+.3f}")
        print(f"    · solapado (mismo M15±1, mismo lado): {len(ov):>4} ({100*len(ov)/max(len(sc3),1):.0f}%)  "
              f"avgR {avg(ov):+.3f}  OOS {avg(ov_oos):+.3f}")
        print(f"    · ÚNICO (sc3 aporta nuevo):           {len(uni):>4} ({100*len(uni)/max(len(sc3),1):.0f}%)  "
              f"avgR {avg(uni):+.3f}  OOS {avg(uni_oos):+.3f}")
        # portfolio combinado: liquidity + sc3 únicos
        comb = pd.concat([liq[["ts","side","r","oos"]], uni[["ts","side","r","oos"]]], ignore_index=True)
        comb_oos = comb[comb.oos]
        print(f"  COMBINADO (liq + sc3-único): n={len(comb):>4} (oos {len(comb_oos):>3})  "
              f"avgR {avg(comb):+.3f}  OOS {avg(comb_oos):+.3f}   [liq OOS solo {avg(liq_oos):+.3f}]")
        agg_liq.append(liq_oos); agg_sc3u.append(uni_oos)

    # global
    L = pd.concat(agg_liq); U = pd.concat(agg_sc3u)
    print(f"\n{'='*70}\n  GLOBAL OOS (3 activos)")
    print(f"  liquidity:   n={len(L):>4}  avgR {avg(L):+.3f}")
    print(f"  sc3-únicos:  n={len(U):>4}  avgR {avg(U):+.3f}")
    print(f"  → sc3 aporta {len(U)} trades OOS nuevos a {avg(U):+.3f}R "
          f"({'ADITIVO' if avg(U) > 0.1 else 'marginal/redundante'})")


if __name__ == "__main__":
    main()
