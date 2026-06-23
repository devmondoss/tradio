"""
backtest_multiasset.py — Corre el sistema A+B (misma config que BTC) en ETH y SOL.
Monkeypatcha _listas2.M1 para reutilizar load2() y A2() sin tocar nada.

Uso:
    python backtest/backtest_multiasset.py
    python backtest/backtest_multiasset.py --symbols ETHUSDT SOLUSDT
"""
import sys
import argparse
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

ROOT = Path(__file__).parent.parent

PARQUETS = {
    "BTCUSDT": ROOT / "data/bybit-perp/processed/btcusdt_perp_m1.parquet",
    "ETHUSDT": Path("E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet"),
    "SOLUSDT": Path("E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"),
}

TF = 15


def run_for_symbol(symbol: str, tp2_cap_r: float = 0.0):
    import _listas2 as L2
    from _strategy_ab import run_system, stats
    from _audit_mirror import gen_h21_short

    path = PARQUETS[symbol]
    if not path.exists():
        print(f"  SKIP {symbol}: parquet no encontrado en {path}")
        return None

    L2.M1 = path

    print(f"\n{'='*62}")
    print(f"  {symbol}  —  {path.name}")
    if tp2_cap_r > 0:
        print(f"  tp2_cap_r={tp2_cap_r} (target maximo {tp2_cap_r}R por trade)")
    print(f"{'='*62}")

    t   = L2.load2(TF, start_ms=0)
    a   = L2.A2(t)
    m1  = L2.load_m1_exit(start_ms=0)

    n_days = (a.ts[-1] - a.ts[0]) / 86_400_000
    print(f"  Barras M{TF}: {a.n:,}  |  {pd.Timestamp(a.ts[0],unit='ms').date()} -> {pd.Timestamp(a.ts[-1],unit='ms').date()}  ({n_days:.0f}d)")
    print(f"  Precio: {a.c.min():.2f} - {a.c.max():.2f}  |  ATR mediana: {np.nanmedian(a.atr):.2f}  ({np.nanmedian(a.atr)/np.nanmedian(a.c)*100:.2f}% del precio)")

    gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]

    print(f"\n  {'Sistema':<22} {'avgR':>8} {'OOS avgR':>9} {'WR':>6} {'OOS netR':>9} {'DD%':>6} {'Sharpe':>7} {'n':>5}")
    print("  " + "-"*74)

    results = {}
    for label, mode in [("A sola (fade)", "fade"), ("B sola (trail)", "trail"), ("A+B enrutado", "routed")]:
        s = stats(run_system(a, gens, m1, TF, mode=mode, max_day=4, cooldown=3, tp2_cap_r=tp2_cap_r))
        results[label] = s
        mark = " <--" if label == "A+B enrutado" else ""
        print(f"  {label:<22} {s['avgR']:>+8.3f} {s['oosA']:>+9.3f} {s['wr']:>5.0f}% {s['oosN']:>+9.1f} {s['dd']:>5.1f}% {s['sharpe']:>+7.1f} {s['n']:>5}{mark}")

    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    args = ap.parse_args()

    all_results = {}
    for sym in args.symbols:
        r = run_for_symbol(sym)
        if r:
            all_results[sym] = r

    if len(all_results) > 1:
        print(f"\n\n{'='*62}")
        print("  RESUMEN — A+B enrutado (mismos params, sin reoptimizar)")
        print(f"{'='*62}")
        print(f"  {'Simbolo':<10} {'avgR':>8} {'OOS avgR':>9} {'WR':>6} {'OOS netR':>9} {'DD%':>6} {'Sharpe':>7} {'n':>5}")
        print("  " + "-"*62)
        for sym, res in all_results.items():
            s = res.get("A+B enrutado", {})
            if s:
                print(f"  {sym:<10} {s['avgR']:>+8.3f} {s['oosA']:>+9.3f} {s['wr']:>5.0f}% {s['oosN']:>+9.1f} {s['dd']:>5.1f}% {s['sharpe']:>+7.1f} {s['n']:>5}")
        print()
        print("  Referencia BTC OOS: avgR +1.82, WR ~65%, DD 8.5%, Sharpe 6.7")


if __name__ == "__main__":
    main()
