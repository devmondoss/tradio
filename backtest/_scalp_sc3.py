"""
_scalp_sc3.py — calibración fina + generalización del ganador sc3 (absorción maker).
================================================================================
Grid con DECIMALES sobre los 3 activos a la vez. Reporta:
  · mejor config PER-ASSET
  · mejor config UNIFICADA (mismo param en BTC+ETH+SOL, maximiza el PEOR OOS)
  · efecto de rr_cap (scalp vs estructural) y timeout

Uso:
  python backtest/_scalp_sc3.py --tf 5
  python backtest/_scalp_sc3.py --tf 5 --fine     # grid denso
  python backtest/_scalp_sc3.py --tf 1            # scalp puro M1
"""
import argparse, itertools
import numpy as np, pandas as pd
from _scalp import load, load_m1_exit, run_setup, stats, gen_sc3, SETUPS

SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
SH = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "SOLUSDT": "SOL"}


def grid_default(fine):
    if fine:
        return dict(vr_thr=[1.25,1.5,1.75,2.0,2.25,2.5,2.75,3.0],
                    stop_atr=[0.35,0.4,0.45,0.5,0.6],
                    tol_atr=[0.4,0.5,0.6,0.7],
                    rr_cap=[None,1.5,2.0,3.0],
                    poc_frac_thr=[0.0])
    return dict(vr_thr=[1.5,2.0,2.5,3.0],
                stop_atr=[0.4,0.5],
                tol_atr=[0.4,0.6],
                rr_cap=[None,1.5,2.5],
                poc_frac_thr=[0.0])


def combos(grid):
    keys = list(grid)
    for c in itertools.product(*[grid[k] for k in keys]):
        yield dict(zip(keys, c))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument("--fine", action="store_true")
    ap.add_argument("--min_oos", type=int, default=30)
    args = ap.parse_args()

    print(f"Cargando 3 activos M{args.tf}...", flush=True)
    data = {}
    for sym in SYMS:
        data[sym] = (load(sym, args.tf), load_m1_exit(sym))
        print(f"  {SH[sym]} ok ({data[sym][0].n:,} barras)", flush=True)

    grid = grid_default(args.fine)
    ncomb = sum(1 for _ in combos(grid))
    print(f"\nsc3 | M{args.tf} | timeout={args.timeout}m | maker/fade | grid={ncomb} combos × 3 activos\n", flush=True)

    # res[param_key] = {sym: stats}
    res = {}
    for p in combos(grid):
        key = tuple(sorted(p.items()))
        res[key] = {}
        for sym in SYMS:
            s, m1 = data[sym]
            df = run_setup(s, gen_sc3(**p), m1, args.tf, entry_mode="maker", mgmt="fade",
                           timeout_min=args.timeout)
            res[key][sym] = stats(df)

    def pstr(p): return ",".join(f"{k}={v}" for k, v in sorted(dict(p).items()))

    # ── per-asset ───────────────────────────────────────────────────────────
    for sym in SYMS:
        rows = [(k, v[sym]) for k, v in res.items() if v[sym]["n_oos"] >= args.min_oos]
        rows.sort(key=lambda x: x[1]["oosA"], reverse=True)
        print(f"── {SH[sym]} top 5 (por OOS) ──")
        for k, st in rows[:5]:
            print(f"  {pstr(k):<62} n={st['n']:>4} oos={st['n_oos']:>4} WR{st['wr']:4.1f}% "
                  f"IS{st['isA']:+.2f} OOS{st['oosA']:+.2f} DD{st['dd']:4.1f}%")
        print()

    # ── unificada: maximiza el PEOR OOS entre los 3 (con n_oos mínimo en cada uno) ──
    uni = []
    for k, v in res.items():
        if all(v[s]["n_oos"] >= args.min_oos for s in SYMS):
            worst = min(v[s]["oosA"] for s in SYMS)
            mean_oos = np.mean([v[s]["oosA"] for s in SYMS])
            uni.append((k, worst, mean_oos, v))
    uni.sort(key=lambda x: x[1], reverse=True)
    print("══ CONFIG UNIFICADA (mismo param 3 activos, ranking por PEOR OOS) ══")
    print(f"  {'config':<54} {'BTC':>14} {'ETH':>14} {'SOL':>14}  peorOOS meanOOS")
    for k, worst, mo, v in uni[:10]:
        def cell(st): return f"{st['oosA']:+.2f}/{st['isA']:+.2f}({st['n']})"
        print(f"  {pstr(k):<54} {cell(v['BTCUSDT']):>14} {cell(v['ETHUSDT']):>14} "
              f"{cell(v['SOLUSDT']):>14}  {worst:+.2f}  {mo:+.2f}")
    print("\n  (celda = OOS/IS(n) por activo)")


if __name__ == "__main__":
    main()
