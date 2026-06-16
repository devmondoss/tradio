"""
parse_trades.py
---------------
Lee los archivos de trades mensuales Bybit SPOT BTCUSDT (CSV.GZ) y produce
un parquet con barras M1: open, high, low, close, volume, buy_vol, sell_vol, delta, cvd.

Uso:
    python backtest/parse_trades.py

Salida:
    data/bybit-spot/processed/m1_trades.parquet
"""

import gzip
import csv
import io
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd
import numpy as np

TRADES_DIR = Path(__file__).parent.parent / "data/bybit-spot/trades"
OUT_DIR    = Path(__file__).parent.parent / "data/bybit-spot/processed"
OUT_FILE   = OUT_DIR / "m1_trades.parquet"

# Jun 15 2025 00:00:00 UTC  →  Jun 15 2026 00:00:00 UTC
START_MS = int(datetime(2025, 6, 15, tzinfo=timezone.utc).timestamp() * 1000)
END_MS   = int(datetime(2026, 6, 16, tzinfo=timezone.utc).timestamp() * 1000)


def read_trades_file(path: Path) -> pd.DataFrame:
    """Lee un CSV.GZ de trades. Columnas: id, timestamp, price, volume, side."""
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        content = f.read()

    # El CSV tiene 6 columnas en los datos pero solo 5 encabezados.
    # Especificamos los nombres explícitamente para evitar el warning de pandas.
    df = pd.read_csv(
        io.StringIO(content),
        names=["id", "timestamp", "price", "volume", "side", "_extra"],
        header=0,
        dtype={"timestamp": "int64", "price": "float64", "volume": "float64"},
        usecols=["timestamp", "price", "volume", "side"],
    )
    df["side"] = df["side"].astype("category")
    return df


def agg_to_m1(df: pd.DataFrame) -> pd.DataFrame:
    """Agrega ticks a barras de 1 minuto."""
    # Truncar al minuto (floor a 60 segundos)
    df["ts_min"] = (df["timestamp"] // 60_000) * 60_000

    # OHLCV + volumen direccional
    grp = df.groupby("ts_min", sort=True)

    ohlcv = grp["price"].agg(
        open="first",
        high="max",
        low="min",
        close="last",
    )
    vol   = grp["volume"].sum().rename("volume")
    buy   = df[df["side"] == "buy"].groupby("ts_min")["volume"].sum().rename("buy_vol")
    sell  = df[df["side"] == "sell"].groupby("ts_min")["volume"].sum().rename("sell_vol")

    bars = pd.concat([ohlcv, vol, buy, sell], axis=1)
    bars["buy_vol"]  = bars["buy_vol"].fillna(0.0)
    bars["sell_vol"] = bars["sell_vol"].fillna(0.0)
    bars["delta"]    = bars["buy_vol"] - bars["sell_vol"]
    bars.index.name  = "ts_ms"
    return bars


def main():
    files = sorted(TRADES_DIR.glob("BTCUSDT-*.csv.gz"))
    if not files:
        sys.exit(f"No se encontraron archivos en {TRADES_DIR}")

    print(f"Archivos encontrados: {len(files)}")

    chunks = []
    for f in files:
        print(f"  leyendo {f.name} ...", end=" ", flush=True)
        df = read_trades_file(f)

        # Filtrar al rango de interés
        mask = (df["timestamp"] >= START_MS) & (df["timestamp"] < END_MS)
        df   = df[mask]
        if df.empty:
            print("(fuera de rango, skip)")
            continue

        bars = agg_to_m1(df)
        chunks.append(bars)
        print(f"{len(bars):,} barras M1")

    if not chunks:
        sys.exit("No hay datos en el rango pedido.")

    combined = pd.concat(chunks).sort_index()

    # Eliminar duplicados de solapamiento entre meses (el primer tick del mes nuevo
    # puede coincidir con el último minuto del mes anterior)
    combined = combined[~combined.index.duplicated(keep="last")]

    # CVD acumulado (reset diario sería más útil pero empezamos con global)
    combined["cvd"] = combined["delta"].cumsum()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    combined.reset_index().to_parquet(OUT_FILE, index=False, engine="pyarrow")

    print(f"\nGuardado: {OUT_FILE}")
    print(f"Total barras M1 : {len(combined):,}")
    print(f"Rango            : {combined.index[0]}  ->  {combined.index[-1]}")
    first_ts = pd.Timestamp(combined.index[0], unit="ms", tz="UTC")
    last_ts  = pd.Timestamp(combined.index[-1], unit="ms", tz="UTC")
    print(f"                 : {first_ts}  →  {last_ts}")
    print(f"Tamaño parquet   : {OUT_FILE.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
