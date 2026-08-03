"""
_decay_check.py — mide decaimiento temporal del edge, mes a mes, para liquidity (A fade) y SC3.
No reentrena nada: corre las configs canónicas ya validadas y bucketiza los trades por mes de ts.
Uso:
    python backtest/_decay_check.py --symbols ETHUSDT SOLUSDT
    python backtest/_decay_check.py --symbols BTCUSDT ETHUSDT SOLUSDT
"""
import sys
import argparse
from pathlib import Path
import numpy as np, pandas as pd

sys.path.insert(0, str(Path(__file__).parent))


def monthly(df, label):
    if df is None or len(df) == 0:
        print(f"  {label}: sin trades"); return
    d = df.copy()
    d["month"] = pd.to_datetime(d.ts, unit="ms").dt.to_period("M")
    g = d.groupby("month").agg(n=("r", "size"), avgR=("r", "mean"),
                                wr=("r", lambda x: 100*(x > 0).mean())).reset_index()
    print(f"\n  {label}")
    print(f"  {'mes':<9} {'n':>5} {'avgR':>8} {'WR':>6}")
    for _, row in g.iterrows():
        print(f"  {str(row.month):<9} {row.n:>5} {row.avgR:>+8.3f} {row.wr:>5.0f}%")
    half = len(d) // 2
    d_sorted = d.sort_values("ts")
    first, second = d_sorted.iloc[:half], d_sorted.iloc[half:]
    print(f"  -- primera mitad avgR {first.r.mean():+.3f} (n={len(first)})  |  "
          f"segunda mitad avgR {second.r.mean():+.3f} (n={len(second)})")


def liquidity_trades(symbol):
    import _listas2 as L2
    from _strategy_ab import run_system
    from _audit_mirror import gen_h21_short
    PARQUETS = {
        "BTCUSDT": Path("data/bybit-perp/processed/btcusdt_perp_m1.parquet"),
        "ETHUSDT": Path("E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet"),
        "SOLUSDT": Path("E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"),
    }
    L2.M1 = PARQUETS[symbol]
    TF = 15
    t = L2.load2(TF, start_ms=0); a = L2.A2(t)
    m1 = L2.load_m1_exit(start_ms=0)
    gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]
    return run_system(a, gens, m1, TF, mode="fade", max_day=4, cooldown=3)


def sc3_trades(symbol):
    import _scalp as SC
    df, _ = SC.run_sc3_htf(symbol)
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=["ETHUSDT", "SOLUSDT"])
    args = ap.parse_args()

    for sym in args.symbols:
        print(f"\n{'='*70}\n  {sym}\n{'='*70}")
        try:
            liq = liquidity_trades(sym)
            monthly(liq, f"{sym} — liquidity A (fade-only, M15)")
        except Exception as e:
            print(f"  liquidity ERROR: {e}")
        try:
            sc3 = sc3_trades(sym)
            monthly(sc3, f"{sym} — SC3 (M5, HTF filter)")
        except Exception as e:
            print(f"  SC3 ERROR: {e}")


if __name__ == "__main__":
    main()
