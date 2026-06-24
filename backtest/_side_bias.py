"""
_side_bias.py — Desglosa el edge por SIDE (long vs short) en los 3 activos.
Responde: ¿el sistema gana con shorts o solo con longs?
"""
import sys
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


def block(df, label):
    if len(df) == 0:
        print(f"    {label:<20} n=0")
        return
    avgr = df.r.mean()
    wr = (df.r > 0).mean() * 100
    netr = df.r.sum()
    print(f"    {label:<20} avgR {avgr:>+7.3f}  WR {wr:>4.0f}%  netR {netr:>+7.1f}  n {len(df):>4}")


def run(symbol):
    import _listas2 as L2
    from _strategy_ab import run_system
    from _audit_mirror import gen_h21_short

    path = PARQUETS[symbol]
    if not path.exists():
        print(f"SKIP {symbol}: {path} no existe")
        return
    L2.M1 = path
    t = L2.load2(TF, start_ms=0)
    a = L2.A2(t)
    m1 = L2.load_m1_exit(start_ms=0)
    gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]

    df = run_system(a, gens, m1, TF, mode="routed", max_day=4, cooldown=3)
    oos = df[df.oos]

    print(f"\n{'='*60}\n  {symbol}   (OOS, A+B enrutado)\n{'='*60}")
    block(oos, "TOTAL")
    print("  -- por side --")
    block(oos[oos.side == "long"],  "LONG")
    block(oos[oos.side == "short"], "SHORT")
    print("  -- por side x gestion --")
    for sd in ("long", "short"):
        for g in ("fade", "trail"):
            block(oos[(oos.side == sd) & (oos.gestion == g)], f"{sd}/{g}")


if __name__ == "__main__":
    syms = sys.argv[1:] or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    for s in syms:
        run(s)
