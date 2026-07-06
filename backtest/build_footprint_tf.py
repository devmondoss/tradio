"""
build_footprint_tf.py - footprint historico multi-timeframe desde ticks crudos
Input:  raw_trades/{date}.parquet  (ts_ms, price, size, side)
Output: processed/{sym}_perp_{tf}_footprint.parquet  por cada TF pedido

Uso:
  python backtest/build_footprint_tf.py --symbol BTCUSDT --tf 1 5 60
  python backtest/build_footprint_tf.py --symbol ETHUSDT --tf 1 5 15 60
"""
import sys, warnings, argparse
from pathlib import Path
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

warnings.filterwarnings("ignore")

ROOT = Path(__file__).parent.parent

SYMBOLS = {
    "BTCUSDT": dict(
        tick_dir = ROOT / "data/bybit-perp/raw_trades",
        out_dir  = ROOT / "data/bybit-perp/processed",
        bin      = 5.0,
    ),
    "ETHUSDT": dict(
        tick_dir = Path("E:/bybit-data/bybit-perp-eth/raw_trades"),
        out_dir  = Path("E:/bybit-data/bybit-perp-eth/processed"),
        bin      = 1.0,
    ),
    "SOLUSDT": dict(
        tick_dir = Path("E:/bybit-data/bybit-perp-sol/raw_trades"),
        out_dir  = Path("E:/bybit-data/bybit-perp-sol/processed"),
        bin      = 0.1,
    ),
}

PA_SCHEMA = pa.schema([
    pa.field("bar_ts",    pa.int64()),
    pa.field("poc",       pa.float64()),
    pa.field("delta",     pa.float64()),
    pa.field("vol",       pa.float64()),
    pa.field("imb_ratio", pa.float64()),
    pa.field("n_trades",  pa.int64()),
    pa.field("prices",    pa.list_(pa.float64())),
    pa.field("buy",       pa.list_(pa.float64())),
    pa.field("sell",      pa.list_(pa.float64())),
])


def process_day(path, tf_ms, fp_bin):
    df = pq.read_table(path, columns=["ts_ms","price","size","side"]).to_pandas()
    if len(df) == 0:
        return pd.DataFrame()
    df["size"]   = df["size"].astype("float64")
    df["is_buy"] = df["side"] == "Buy"
    df["bar_ts"] = (df["ts_ms"] // tf_ms) * tf_ms
    df["bin_px"] = (df["price"] / fp_bin).round() * fp_bin

    buy  = df[df.is_buy].groupby(["bar_ts","bin_px"], sort=True)["size"].sum()
    sell = df[~df.is_buy].groupby(["bar_ts","bin_px"], sort=True)["size"].sum()
    fp   = pd.DataFrame({"buy": buy, "sell": sell}).fillna(0.0)
    n_by_bar = df.groupby("bar_ts").size()

    rows = []
    for bar_ts, g in fp.groupby(level=0):
        g      = g.reset_index(level=0, drop=True).sort_index()
        bins   = g.index.values.astype("float64")
        buy_v  = g["buy"].values
        sell_v = g["sell"].values
        vol_v  = buy_v + sell_v
        tb     = float(buy_v.sum());  ts2 = float(sell_v.sum())
        tv     = tb + ts2;            d   = tb - ts2
        rows.append({
            "bar_ts":    int(bar_ts),
            "poc":       float(bins[np.argmax(vol_v)]),
            "delta":     round(d, 6),
            "vol":       round(tv, 6),
            "imb_ratio": round(d/tv, 6) if tv > 0 else 0.0,
            "n_trades":  int(n_by_bar.get(bar_ts, 0)),
            "prices":    bins.tolist(),
            "buy":       [round(x,6) for x in buy_v],
            "sell":      [round(x,6) for x in sell_v],
        })
    return pd.DataFrame(rows)


def build_tf(sym, tf_min, cfg):
    tf_ms    = tf_min * 60_000
    tf_label = {1:"m1",5:"m5",15:"m15",60:"h1"}.get(tf_min, f"m{tf_min}")
    sym_l    = sym.lower().replace("usdt","") + "usdt"
    out_path = cfg["out_dir"] / f"{sym_l}_perp_{tf_label}_footprint.parquet"

    if out_path.exists():
        sz = out_path.stat().st_size / 1e6
        print(f"  [{tf_label}] ya existe ({sz:.1f} MB) -- saltando")
        return

    files = sorted(cfg["tick_dir"].glob("[0-9]*.parquet"))
    if not files:
        print(f"  [{tf_label}] sin ticks en {cfg['tick_dir']}"); return

    print(f"  [{tf_label}] procesando {len(files)} dias...", flush=True)
    all_bars = []
    for i, f in enumerate(files, 1):
        try:
            bars = process_day(f, tf_ms, cfg["bin"])
            if len(bars): all_bars.append(bars)
        except Exception as e:
            print(f"    ERROR {f.stem}: {e}", file=sys.stderr)
        if i % 60 == 0 or i == len(files):
            n = sum(len(b) for b in all_bars)
            print(f"    [{i:>3}/{len(files)}] {f.stem}  acum={n:,} barras", flush=True)

    if not all_bars:
        print(f"  [{tf_label}] sin datos"); return

    df = pd.concat(all_bars, ignore_index=True).sort_values("bar_ts").reset_index(drop=True)
    cfg["out_dir"].mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pydict({c: df[c].tolist() for c in df.columns}, schema=PA_SCHEMA)
    pq.write_table(table, out_path, compression="zstd")
    print(f"  [{tf_label}] OK -- {len(df):,} barras  {out_path.stat().st_size/1e6:.1f} MB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT", choices=list(SYMBOLS.keys()))
    ap.add_argument("--tf", nargs="+", type=int, default=[1,5,15,60])
    args = ap.parse_args()
    cfg = SYMBOLS[args.symbol]
    if not cfg["tick_dir"].exists():
        print(f"ERROR: {cfg['tick_dir']} no existe"); sys.exit(1)
    print(f"\n{args.symbol}  bin=${cfg['bin']}  TFs={args.tf}")
    for tf in args.tf:
        build_tf(args.symbol, tf, cfg)
    print("Listo.")

if __name__ == "__main__":
    main()
