"""
build_footprint_m15.py — construye footprint histórico M15 desde ticks crudos
==============================================================================
Input:  data/bybit-perp/raw_trades/{date}.parquet  (ts_ms, price, size, side)
Output: data/bybit-perp/processed/btcusdt_perp_m15_footprint.parquet

Schema de salida (una fila por barra M15):
  bar_ts    int64   — timestamp inicio barra (ms UTC)
  poc       float64 — precio con mayor volumen total en la barra
  delta     float64 — buy_vol - sell_vol total de la barra
  vol       float64 — buy_vol + sell_vol total
  imb_ratio float64 — delta / vol  (−1→+1)
  n_trades  int64   — número de ticks
  prices    list    — precios de bins ($5) con actividad
  buy       list    — volumen comprador por bin
  sell      list    — volumen vendedor por bin

Uso: python -X utf8 backtest/build_footprint_m15.py [--symbol BTCUSDT]
"""
import sys, warnings, argparse
from pathlib import Path
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

warnings.filterwarnings("ignore")

ROOT   = Path(__file__).parent.parent
TF_MS  = 15 * 60_000   # 15 minutos en ms
BIN    =  5.0           # $5 por nivel — igual que el Rust monitor

SYMBOLS = {
    "BTCUSDT": dict(
        tick_dir = ROOT / "data/bybit-perp/raw_trades",
        out_path = ROOT / "data/bybit-perp/processed/btcusdt_perp_m15_footprint.parquet",
        bin      = 5.0,
    ),
    "ETHUSDT": dict(
        tick_dir = Path("E:/bybit-data/bybit-perp-eth/raw_trades"),
        out_path = Path("E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m15_footprint.parquet"),
        bin      = 1.0,   # ETH: bins de $1
    ),
    "SOLUSDT": dict(
        tick_dir = Path("E:/bybit-data/bybit-perp-sol/raw_trades"),
        out_path = Path("E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m15_footprint.parquet"),
        bin      = 0.1,   # SOL: bins de $0.10
    ),
}


def process_day(path: Path, fp_bin: float) -> pd.DataFrame:
    """Un archivo diario de ticks → DataFrame con footprint por barra M15."""
    df = pq.read_table(path, columns=["ts_ms", "price", "size", "side"]).to_pandas()
    if len(df) == 0:
        return pd.DataFrame()

    df["size"]    = df["size"].astype("float32")
    df["is_buy"]  = (df["side"] == "Buy")
    df["bar_ts"]  = (df["ts_ms"] // TF_MS) * TF_MS
    df["bin_px"]  = (df["price"] / fp_bin).round() * fp_bin

    # Volumen buy/sell por (bar, bin)
    buy  = df[df.is_buy].groupby(["bar_ts", "bin_px"])["size"].sum()
    sell = df[~df.is_buy].groupby(["bar_ts", "bin_px"])["size"].sum()
    fp   = pd.DataFrame({"buy": buy, "sell": sell}).fillna(0.0)
    fp["vol"] = fp["buy"] + fp["sell"]

    # Agregar por barra
    rows = []
    for bar_ts, g in fp.groupby(level=0):
        g = g.reset_index(level=0, drop=True).sort_index()  # index = bin_px
        bins    = g.index.values.astype("float64")
        buy_v   = g["buy"].values.astype("float64")
        sell_v  = g["sell"].values.astype("float64")
        vol_v   = g["vol"].values.astype("float64")
        poc_px  = float(bins[np.argmax(vol_v)])
        tot_buy  = float(buy_v.sum())
        tot_sell = float(sell_v.sum())
        tot_vol  = tot_buy + tot_sell
        delta    = tot_buy - tot_sell
        rows.append({
            "bar_ts":    int(bar_ts),
            "poc":       poc_px,
            "delta":     round(delta, 4),
            "vol":       round(tot_vol, 4),
            "imb_ratio": round(delta / tot_vol, 4) if tot_vol > 0 else 0.0,
            "n_trades":  int(len(df[df.bar_ts == bar_ts])),
            "prices":    bins.tolist(),
            "buy":       [round(x, 4) for x in buy_v],
            "sell":      [round(x, 4) for x in sell_v],
        })

    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT", choices=list(SYMBOLS.keys()))
    args = ap.parse_args()

    cfg      = SYMBOLS[args.symbol]
    tick_dir = cfg["tick_dir"]
    out_path = cfg["out_path"]
    fp_bin   = cfg["bin"]

    if not tick_dir.exists():
        print(f"ERROR: {tick_dir} no existe"); sys.exit(1)

    files = sorted(tick_dir.glob("[0-9]*.parquet"))
    print(f"{args.symbol} — {len(files)} dias de ticks  bin=${fp_bin}")
    print(f"Salida: {out_path}")

    all_bars = []
    for i, f in enumerate(files, 1):
        day = f.stem
        try:
            bars = process_day(f, fp_bin)
            if len(bars) > 0:
                all_bars.append(bars)
        except Exception as e:
            print(f"  ERROR {day}: {e}", file=sys.stderr)

        if i % 30 == 0 or i == len(files):
            n_bars = sum(len(b) for b in all_bars)
            print(f"  [{i:>3}/{len(files)}] {day}  acum={n_bars:,} barras", flush=True)

    if not all_bars:
        print("Sin datos"); sys.exit(1)

    df = pd.concat(all_bars, ignore_index=True)
    df = df.sort_values("bar_ts").reset_index(drop=True)

    # Guardar con schema explícito para las listas
    schema = pa.schema([
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

    out_path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pydict(
        {col: df[col].tolist() for col in df.columns},
        schema=schema,
    )
    pq.write_table(table, out_path, compression="zstd")

    size_mb = out_path.stat().st_size / 1e6
    print(f"\nFINAL: {len(df):,} barras M15  |  {size_mb:.1f} MB  |  {out_path.name}")
    print(f"  delta range: [{df.delta.min():.1f}, {df.delta.max():.1f}]")
    print(f"  imb_ratio:   [{df.imb_ratio.min():.3f}, {df.imb_ratio.max():.3f}]")
    print(f"  poc sample:  {df.poc.iloc[0]:.2f} ... {df.poc.iloc[-1]:.2f}")


if __name__ == "__main__":
    main()
