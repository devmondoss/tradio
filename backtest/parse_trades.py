"""
parse_trades.py
---------------
Lee los archivos de trades mensuales Bybit SPOT BTCUSDT (CSV.GZ) y produce
barras M1: open, high, low, close, volume, buy_vol, sell_vol, delta, cvd.

Cache incremental: cada CSV.GZ se parsea una sola vez y se guarda en
    processed/trades_cache/YYYY-MM.parquet
Re-runs saltan meses ya cacheados.

Rango de datos: 2025-06-15 → 2026-01-20 (inclusive)

Uso:
    python backtest/parse_trades.py              # procesa todo lo pendiente
    python backtest/parse_trades.py --rebuild    # borra caché y reprocesa todo
"""

import gzip
import io
import sys
import argparse
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd

TRADES_DIR = Path(__file__).parent.parent / "data/bybit-spot/trades"
PROC_DIR   = Path(__file__).parent.parent / "data/bybit-spot/processed"
CACHE_DIR  = PROC_DIR / "trades_cache"
OUT_FILE   = PROC_DIR / "m1_trades.parquet"

START_MS = int(datetime(2025, 6, 15,  tzinfo=timezone.utc).timestamp() * 1000)
END_MS   = int(datetime(2026, 6, 16,  tzinfo=timezone.utc).timestamp() * 1000)  # 15 inclusive

# Meses a procesar
MONTHS = [
    "2025-06", "2025-07", "2025-08", "2025-09",
    "2025-10", "2025-11", "2025-12",
    "2026-01", "2026-02", "2026-03", "2026-04", "2026-05",
]


def parse_month(path: Path) -> pd.DataFrame:
    """Lee un CSV.GZ de trades mensuales → barras M1 filtradas al rango."""
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        content = f.read()

    df = pd.read_csv(
        io.StringIO(content),
        names=["id", "timestamp", "price", "volume", "side", "_extra"],
        header=0,
        dtype={"timestamp": "int64", "price": "float64", "volume": "float64"},
        usecols=["timestamp", "price", "volume", "side"],
    )
    df["side"] = df["side"].astype("category")

    # Filtrar al rango global
    df = df[(df["timestamp"] >= START_MS) & (df["timestamp"] < END_MS)]
    if df.empty:
        return pd.DataFrame()

    # Agregar a barras M1
    df["ts_min"] = (df["timestamp"] // 60_000) * 60_000

    ohlcv = df.groupby("ts_min", sort=True)["price"].agg(
        open="first", high="max", low="min", close="last"
    )
    vol  = df.groupby("ts_min")["volume"].sum().rename("volume")
    buy  = df[df["side"] == "buy"].groupby("ts_min")["volume"].sum().rename("buy_vol")
    sell = df[df["side"] == "sell"].groupby("ts_min")["volume"].sum().rename("sell_vol")

    bars = pd.concat([ohlcv, vol, buy, sell], axis=1)
    bars["buy_vol"]  = bars["buy_vol"].fillna(0.0)
    bars["sell_vol"] = bars["sell_vol"].fillna(0.0)
    bars["delta"]    = bars["buy_vol"] - bars["sell_vol"]
    bars.index.name  = "ts_ms"
    return bars.reset_index()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild", action="store_true", help="Borrar caché y reprocesar todo")
    args = parser.parse_args()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if args.rebuild:
        for f in CACHE_DIR.glob("*.parquet"):
            f.unlink()
        print("Caché borrada.")

    pending = []
    skipped = 0
    for ym in MONTHS:
        cache_file = CACHE_DIR / f"{ym}.parquet"
        src_file   = TRADES_DIR / f"BTCUSDT-{ym}.csv.gz"

        if not src_file.exists():
            print(f"  SKIP {src_file.name} (no descargado)")
            continue
        if cache_file.exists():
            skipped += 1
            continue
        pending.append((ym, src_file, cache_file))

    print(f"Meses en rango : {len(MONTHS)}  |  ya cacheados: {skipped}  |  pendientes: {len(pending)}")

    for i, (ym, src, cache) in enumerate(pending, 1):
        print(f"  [{i}/{len(pending)}] {src.name} ...", end=" ", flush=True)
        bars = parse_month(src)
        if bars.empty:
            print("sin datos en rango")
            continue
        bars.to_parquet(cache, index=False, engine="pyarrow")
        print(f"{len(bars):,} barras M1  -> {cache.name}")

    # Combinar todos los meses cacheados
    cache_files = sorted(CACHE_DIR.glob("*.parquet"))
    if not cache_files:
        sys.exit("No hay datos en cache.")

    print(f"\nCombinando {len(cache_files)} meses...", end=" ", flush=True)
    combined = pd.concat([pd.read_parquet(f) for f in cache_files], ignore_index=True)
    combined = combined.sort_values("ts_ms").drop_duplicates("ts_ms", keep="last").reset_index(drop=True)

    # CVD acumulado global
    combined["cvd"] = combined["delta"].cumsum()

    combined.to_parquet(OUT_FILE, index=False, engine="pyarrow")

    first = pd.Timestamp(combined["ts_ms"].iloc[0],  unit="ms", tz="UTC")
    last  = pd.Timestamp(combined["ts_ms"].iloc[-1], unit="ms", tz="UTC")
    print(f"{len(combined):,} barras M1")
    print(f"Rango   : {first}  ->  {last}")
    print(f"Guardado: {OUT_FILE}  ({OUT_FILE.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
