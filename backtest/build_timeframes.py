"""
build_timeframes.py — resamplea el M1 procesado a M5 / M15 / H1 / H4 (causal)
=============================================================================
Lee processed/btcusdt_perp_m1.parquet (114 cols) y produce btcusdt_perp_{m5,m15,h1,h4}.parquet.
Agregación correcta por tipo de columna + RECÁLCULO nativo de los indicadores TF-dependientes
(ATR, EMA, VWAP, VR, DZ, swings, cvd_slope) en cada marco. Todo causal.

Salida: data/bybit-perp/processed/ y copia a share_dataset/.
Uso: python backtest/build_timeframes.py
"""
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
M1 = ROOT / "data/bybit-perp/processed/btcusdt_perp_m1.parquet"
OUTP = ROOT / "data/bybit-perp/processed"
SHARE = ROOT / "share_dataset"
TFS = {"m5": 5, "m15": 15, "h1": 60, "h4": 240}

# --- reglas de agregación explícitas ---
SUM   = ["volume","buy_vol","sell_vol","delta","n_trades","plus_ticks","minus_ticks",
         "fp_buy_imb","fp_sell_imb","fp_stack_buy","fp_stack_sell"]
MEAN  = ["obi5_mean","obi10_mean","obi20_mean","obi_range","spread_mean","n_snapshots",
         "near5_ask","near5_bid","max_ask5","max_bid5","fp_delta_top","fp_delta_bot",
         "fp_sell_dom","fp_n_levels","obi10_min","obi10_max"]
FIRST = ["open","mid_open"]
MAXC  = ["high","max_trade"]
MINC  = ["low"]
# todo lo demás numérico/string → LAST ; todo bool → ANY ; los recalculados se sobreescriben


def ema(x, n):
    a = 2/(n+1); o = np.empty(len(x)); o[0] = x[0]
    for i in range(1, len(x)): o[i] = a*x[i] + (1-a)*o[i-1]
    return o


def atr(h, l, c, n=14):
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h-l, np.maximum(np.abs(h-pc), np.abs(l-pc)))
    o = np.full(len(tr), np.nan)
    if len(tr) >= n: o[n-1] = np.nanmean(tr[:n])
    k = 1/n
    for i in range(n, len(tr)): o[i] = o[i-1]*(1-k) + tr[i]*k
    return o


def resample(df, tf_min):
    f = tf_min * 60_000
    g = (df["ts_ms"].values // f) * f
    bool_cols = [c for c in df.columns if df[c].dtype == bool]
    aggmap = {}
    for c in df.columns:
        if c == "ts_ms": continue
        if c in SUM: aggmap[c] = "sum"
        elif c in MEAN: aggmap[c] = "mean"
        elif c in FIRST: aggmap[c] = "first"
        elif c in MAXC: aggmap[c] = "max"
        elif c in MINC: aggmap[c] = "min"
        elif c in bool_cols: aggmap[c] = "any"
        else: aggmap[c] = "last"
    out = df.groupby(g).agg(aggmap)
    out.insert(0, "ts_ms", out.index.values.astype(np.int64))
    out = out.reset_index(drop=True)

    # --- recálculo nativo de indicadores TF-dependientes ---
    h, l, c = out["high"].values, out["low"].values, out["close"].values
    o = out["open"].values; vol = out["volume"].values; delta = out["delta"].values
    n = len(out); S = pd.Series
    out["atr14"] = atr(h, l, c)
    out["ema20"] = ema(c, 20)
    # VWAP de sesión (reset diario 00:00 UTC)
    tp = (h + l + c) / 3; day = out["ts_ms"].values // 86_400_000
    cpv = pd.DataFrame({"d": day, "x": tp*vol}).groupby("d")["x"].cumsum().values
    cv  = pd.DataFrame({"d": day, "x": vol}).groupby("d")["x"].cumsum().values
    out["vwap"] = cpv / (cv + 1e-9)
    # VR (volume ratio) y DZ (delta z-score) nativos
    out["vr"] = vol / (S(vol).rolling(50, min_periods=10).mean().shift(1).values + 1e-9)
    out["dz"] = (delta - S(delta).rolling(50, min_periods=10).mean().shift(1).values) / \
                (S(delta).rolling(50, min_periods=10).std().shift(1).values + 1e-9)
    # swings nativos (50 barras) y cvd_slope
    out["swing_high_50"] = S(h).rolling(50, min_periods=1).max().values
    out["swing_low_50"]  = S(l).rolling(50, min_periods=1).min().values
    cvd = out["cvd"].values
    out["cvd_slope"] = cvd - S(cvd).shift(5).values
    return out


def main():
    print("Cargando M1...")
    df = pd.read_parquet(M1).sort_values("ts_ms").reset_index(drop=True)
    print(f"M1: {len(df):,} filas, {df.shape[1]} cols")
    SHARE.mkdir(exist_ok=True)
    for name, mins in TFS.items():
        t = resample(df, mins)
        p1 = OUTP / f"btcusdt_perp_{name}.parquet"
        p2 = SHARE / f"btcusdt_perp_{name}.parquet"
        t.to_parquet(p1, index=False); t.to_parquet(p2, index=False)
        d0 = pd.Timestamp(t.ts_ms.min(), unit="ms").date(); d1 = pd.Timestamp(t.ts_ms.max(), unit="ms").date()
        print(f"  {name.upper():<3}: {len(t):>7,} barras | {d0} → {d1} | {p1.name} ({p1.stat().st_size//1024:,} KB)")
    print("\n[OK] M5/M15/H1/H4 en processed/ y share_dataset/")


if __name__ == "__main__":
    main()
