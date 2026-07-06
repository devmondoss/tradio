"""
_lab_tf.py — barrido de TEMPORALIDADES (M5→H4) de las zonas de volumen (A+B).
============================================================================
Mismo motor A+B routeado, entrada maker en nivel de volumen, salida M1 (honesto).
Solo varía el TF de decisión. Reporta OOS por activo: avgR / WR / n / DD / Sharpe.
(Sin FVG — ya descartado.) Correr: python backtest/_lab_tf.py
"""
import sys; from pathlib import Path
import numpy as np
sys.path.insert(0, "backtest")
import _listas2 as L2
from _strategy_ab import run_system, stats
from _audit_mirror import gen_h21_short

PARQ = {"BTCUSDT": "data/bybit-perp/processed/btcusdt_perp_m1.parquet",
        "ETHUSDT": "E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet",
        "SOLUSDT": "E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"}
TFS = [5, 15, 30, 60, 120, 240]


def label(tf):
    return f"M{tf}" if tf < 60 else f"H{tf//60}"


def main():
    for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
        if not Path(PARQ[sym]).exists(): print("SKIP", sym); continue
        L2.M1 = PARQ[sym]
        m1 = L2.load_m1_exit(start_ms=L2.TICK_MS)   # M1 para salidas, una vez por activo
        print(f"\n=== {sym} ===")
        print(f"  {'TF':<5} {'n':>5} {'WR':>5} {'avgR':>7} {'OOS_n':>6} {'OOS_avgR':>9} {'OOS_netR':>9} {'DD':>6} {'Sh':>6}")
        for tf in TFS:
            a = L2.A2(L2.load2(tf, start_ms=L2.TICK_MS))
            df = run_system(a, [L2.gen_h5(), L2.gen_h21(), gen_h21_short()], m1, tf, mode="routed")
            if not len(df):
                print(f"  {label(tf):<5} sin trades"); continue
            st = stats(df); o = df[df.oos]
            print(f"  {label(tf):<5} {st['n']:>5} {st['wr']:>4.0f}% {st['avgR']:>+7.3f} "
                  f"{len(o):>6} {st['oosA']:>+9.3f} {st['oosN']:>+9.1f} {st['dd']:>5.1f}% {st['sharpe']:>+6.1f}")


if __name__ == "__main__":
    main()
