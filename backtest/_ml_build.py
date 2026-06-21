"""
_ml_build.py — dataset multivariante causal (panel 1s) para el test capstone de edge direccional
================================================================================================
Los EVENTOS univariados no predicen > fee (probado en _event_predict). Aquí el límite superior honesto:
¿un modelo con TODAS las features causales del libro+flujo+derivados predice la dirección futura
mejor que el azar OOS, y su decil más confiado bate el fee (11 bps)?

Construye, por día: panel 1s (ob_1s ⨝ ticks/seg), features causales (OBI multinivel, micro-lean, flujos
de delta/vol multi-ventana, momentum, big-trade, tick-imb, profundidad), etiqueta fwd 5m/15m (bps),
mergea OI/funding. Muestrea 1 fila cada STEP s para acotar tamaño. Cachea a _ml_dataset.parquet.

Uso: python backtest/_ml_build.py [--step 15] [--max-days 0]
"""
import os, argparse
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
PERP = Path(os.environ.get("TRADIO_PERP", str(ROOT / "data/bybit-perp")))
RAW_T = PERP / "raw_trades"; OB_1S = PERP / "ob_1s"
OUT = PERP / "_ml_dataset.parquet"
OOS_MS = int(pd.Timestamp("2026-03-01", tz="UTC").value // 1_000_000)


def panel_1s(date):
    t = pd.read_parquet(RAW_T / f"{date}.parquet")
    o = pd.read_parquet(OB_1S / f"{date}.parquet")
    sec = (t["ts_ms"].values // 1000)
    buy = t["side"].values == "Buy"
    td = t["tick_dir"].values if "tick_dir" in t else np.array([""] * len(t))
    g = pd.DataFrame({
        "sec": sec, "size": t["size"].values.astype(float),
        "bsz": np.where(buy, t["size"].values, 0.0).astype(float),
        "ssz": np.where(~buy, t["size"].values, 0.0).astype(float),
        "plus": (td == "PlusTick").astype(float), "minus": (td == "MinusTick").astype(float),
    })
    agg = g.groupby("sec").agg(vol=("size", "sum"), buy_vol=("bsz", "sum"), sell_vol=("ssz", "sum"),
                               n_trades=("size", "size"), max_trade=("size", "max"),
                               plus=("plus", "sum"), minus=("minus", "sum"))
    agg["delta"] = agg["buy_vol"] - agg["sell_vol"]
    o = o.copy(); o["sec"] = o["ts_ms"].values // 1000
    p = o.set_index("sec").join(agg, how="left")
    for c in ["vol", "buy_vol", "sell_vol", "n_trades", "max_trade", "plus", "minus", "delta"]:
        p[c] = p[c].fillna(0.0)
    p["mid"] = p["mid"].ffill()
    return p.reset_index(drop=True)


def feats(p):
    S = pd.Series
    mid = p["mid"].values.astype(float)
    spread = p["spread_bps"].values.astype(float)
    micro = p["microprice"].values.astype(float)
    delta = p["delta"].values; vol = p["vol"].values; mxt = p["max_trade"].values
    plus = p["plus"].values; minus = p["minus"].values
    def rs(x, k): return S(x).rolling(k, min_periods=max(5, k//4)).sum().values
    def rm(x, k): return S(x).rolling(k, min_periods=max(5, k//4)).mean().values
    def rstd(x, k): return S(x).rolling(k, min_periods=max(5, k//4)).std().values + 1e-9
    def ret(k):
        o = np.full(len(mid), np.nan); o[k:] = (mid[k:] / mid[:-k] - 1) * 1e4; return o
    F = {}
    F["obi5"] = p["obi5"].values; F["obi10"] = p["obi10"].values; F["obi25"] = p["obi25"].values
    F["micro_lean"] = (micro - mid) * 1e4 / (spread * mid + 1e-9)
    F["spread"] = spread
    F["depth_imb"] = (p["depth_bid25"].values - p["depth_ask25"].values) / \
                     (p["depth_bid25"].values + p["depth_ask25"].values + 1e-9)
    for k in (10, 30, 60, 300):
        F[f"delta_{k}"] = rs(delta, k) / (rstd(delta, 1800) * np.sqrt(k))
        F[f"vol_{k}"] = rs(vol, k) / (rm(vol, 1800) * k + 1e-9)
        F[f"ret_{k}"] = ret(k)
    F["bigtrade"] = (mxt - rm(mxt, 1800)) / (rstd(mxt, 1800))
    F["tickimb_60"] = (rs(plus, 60) - rs(minus, 60)) / (rs(plus, 60) + rs(minus, 60) + 1e-9)
    F["obi_chg_60"] = p["obi10"].values - S(p["obi10"].values).shift(60).values
    # labels forward (bps)
    f5 = np.full(len(mid), np.nan); f5[:-300] = (mid[300:] / mid[:-300] - 1) * 1e4
    f15 = np.full(len(mid), np.nan); f15[:-900] = (mid[900:] / mid[:-900] - 1) * 1e4
    d = pd.DataFrame(F)
    d["fwd_5m"] = f5; d["fwd_15m"] = f15
    d["ts_ms"] = p["ts_ms"].values
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=int, default=15)
    ap.add_argument("--max-days", type=int, default=0)
    args = ap.parse_args()
    dates = sorted(set(p.stem for p in RAW_T.glob("*.parquet")) & set(p.stem for p in OB_1S.glob("*.parquet")))
    if args.max_days: dates = dates[:args.max_days]
    print(f"Días: {len(dates)} ({dates[0]}→{dates[-1]}) | step {args.step}s")
    out = []
    for i, dte in enumerate(dates, 1):
        try:
            d = feats(panel_1s(dte))
            d = d.iloc[args.step // 2::args.step]            # submuestreo
            d = d.dropna()
            out.append(d)
        except Exception as e:
            print(f"  WARN {dte}: {e}")
        if i % 30 == 0 or i == len(dates):
            print(f"  {i}/{len(dates)} | filas {sum(len(x) for x in out):,}", flush=True)
    df = pd.concat(out, ignore_index=True)
    # merge OI/funding causal
    oi = pd.read_parquet(PERP / "oi_5m.parquet").sort_values("ts_ms")
    fund = pd.read_parquet(PERP / "funding.parquet").sort_values("ts_ms")
    df = df.sort_values("ts_ms")
    df = pd.merge_asof(df, oi, on="ts_ms", direction="backward")
    df = pd.merge_asof(df, fund, on="ts_ms", direction="backward")
    df["oi_chg_1h"] = df["open_interest"].pct_change().fillna(0.0)  # aprox (orden temporal global)
    df["funding"] = df["funding_rate"].fillna(0.0)
    df["oos"] = df["ts_ms"] >= OOS_MS
    df.drop(columns=["open_interest", "funding_rate"], inplace=True)
    df.to_parquet(OUT, index=False)
    print(f"\n[OK] {OUT} | {len(df):,} filas | IS {int((~df.oos).sum()):,} / OOS {int(df.oos.sum()):,}")
    print("cols:", [c for c in df.columns if c not in ("ts_ms", "oos")])


if __name__ == "__main__":
    main()
