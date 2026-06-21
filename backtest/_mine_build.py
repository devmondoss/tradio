"""
_mine_build.py — dataset para minería de patrones con etiqueta TRIPLE-BARRERA (anti-overfit)
=============================================================================================
Distinto a _ml_build (que etiquetaba retorno a 5/15min): aquí la etiqueta es TRADE-LIKE:
para cada minuto muestreado, mirando solo el futuro de precio (M1 high/low), ¿toca primero la
barrera +B o la barrera -B (bps) dentro de T minutos? label_up=1 si toca +B primero.
Features = panel causal rico del M1 (orderflow, footprint, estructura, derivados OI/funding,
hora). Todo causal (calculado con info ≤ t). Cachea a _mine_dataset.parquet.

Uso: python backtest/_mine_build.py [--B 35] [--T 60] [--step 3]
"""
import argparse
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
M1 = ROOT / "data/bybit-perp/processed/btcusdt_perp_m1.parquet"
OI = ROOT / "data/bybit-perp/oi_5m.parquet"; FUND = ROOT / "data/bybit-perp/funding.parquet"
OUT = ROOT / "data/bybit-perp/_mine_dataset.parquet"
OOS_MS = int(pd.Timestamp("2026-03-01", tz="UTC").value // 1_000_000)


def zscore(x, n):
    s = pd.Series(x)
    return ((x - s.rolling(n).mean().shift(1).values) / (s.rolling(n).std().shift(1).values + 1e-9))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--B", type=float, default=35.0); ap.add_argument("--T", type=int, default=60)
    ap.add_argument("--step", type=int, default=3)
    args = ap.parse_args()
    df = pd.read_parquet(M1).sort_values("ts_ms").reset_index(drop=True)
    df = df[df.ts_ms >= pd.Timestamp("2025-06-19", tz="UTC").value//10**6].reset_index(drop=True)
    n = len(df)
    c = df.close.values.astype(float); h = df.high.values.astype(float); l = df.low.values.astype(float)
    ts = df.ts_ms.values.astype(np.int64)

    # ---- features causales ----
    F = {}
    for col in ["obi5_mean","obi10_mean","obi20_mean","cvd_slope","vpin","spread_mean",
                "fp_buy_imb","fp_sell_imb","fp_delta_top","fp_delta_bot","vr","atr14"]:
        if col in df: F[col] = df[col].values.astype(float)
    F["delta_z"]  = zscore(df.delta.values.astype(float), 150)
    F["vol_z"]    = zscore(df.volume.values.astype(float), 150)
    F["cvd_z"]    = zscore(df.cvd.values.astype(float), 150)
    for k in (5, 15, 60):
        r = np.full(n, np.nan); r[k:] = (c[k:]/c[:-k]-1)*1e4; F[f"ret_{k}"] = r
    F["dist_vwap"] = (c - df.vwap.values.astype(float)) / (df.atr14.values.astype(float)+1e-9) if "vwap" in df else np.zeros(n)
    F["dist_poc"]  = (c - df.vp_poc.values.astype(float)) / (df.atr14.values.astype(float)+1e-9) if "vp_poc" in df else np.zeros(n)
    F["hour"] = ((ts//60_000)%1440)//60
    for b in ["big_trade_bullish","big_trade_bearish","fp_absorb_buy","fp_absorb_sell","sweep_confirmed"]:
        if b in df: F[b] = df[b].astype(float).values

    # derivados
    oi = pd.read_parquet(OI).sort_values("ts_ms"); fund = pd.read_parquet(FUND).sort_values("ts_ms")
    base = pd.DataFrame({"ts_ms": ts}).sort_values("ts_ms")
    base = pd.merge_asof(base, oi, on="ts_ms", direction="backward")
    base = pd.merge_asof(base, fund, on="ts_ms", direction="backward")
    F["oi_chg_30"] = (base.open_interest.values / pd.Series(base.open_interest.values).shift(30).values - 1) * 100
    F["funding"]   = base.funding_rate.values

    # ---- etiqueta triple-barrera (causal hacia adelante) ----
    B = args.B/1e4; T = args.T
    lab = np.full(n, -1, dtype=np.int8)
    up = c*(1+B); dn = c*(1-B)
    for i in range(0, n-1):
        u = up[i]; d = dn[i]; end = min(i+1+T, n)
        hit = 0
        for j in range(i+1, end):
            hu = h[j] >= u; hd = l[j] <= d
            if hu and hd: hit = 1 if (c[j] >= c[i]) else -1; break   # ambas en la barra: usa cierre
            if hu: hit = 1; break
            if hd: hit = -1; break
        lab[i] = 1 if hit == 1 else (0 if hit == -1 else -1)

    d = pd.DataFrame(F); d["label"] = lab; d["ts_ms"] = ts
    d = d.iloc[args.step//2::args.step]                      # submuestreo
    d = d[d.label >= 0]                                       # quita los que no tocaron ninguna barrera (timeout)
    d = d.replace([np.inf,-np.inf], np.nan).dropna()
    d["oos"] = d.ts_ms >= OOS_MS
    d.to_parquet(OUT, index=False)
    feats = [x for x in d.columns if x not in ("label","ts_ms","oos")]
    print(f"[OK] {OUT} | {len(d):,} filas (IS {int((~d.oos).sum()):,}/OOS {int(d.oos.sum()):,}) | "
          f"B={args.B}bps T={args.T}m | P(up) global {d.label.mean():.3f}")
    print(f"feats ({len(feats)}): {feats}")


if __name__ == "__main__":
    main()
