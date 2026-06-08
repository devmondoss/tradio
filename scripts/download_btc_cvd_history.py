#!/usr/bin/env python3
"""
Descarga datos históricos de Binance FAPI (futuros perpetuos) M1 con CVD.

Modos:
  --months N          últimos N meses de klines (bar_delta estimado desde taker_buy_vol)
  --daily             un archivo CSV por día en el rango --start / --end
  --no-aggtrades      solo klines (bar_delta estimado, más rápido)

Salida por defecto: dataset/btc_Xm.parquet  (o .csv con --csv)

Requiere:
    pip install requests pandas tqdm pyarrow
"""

import argparse
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import requests
from tqdm import tqdm

FAPI = "https://fapi.binance.com"
DATASET = Path(__file__).parent.parent / "dataset"

# ---------------------------------------------------------------------------

def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    """Klines con paginación automática."""
    rows = []
    limit = 1500
    cur = start_ms
    while cur < end_ms:
        resp = requests.get(
            f"{FAPI}/fapi/v1/klines",
            params={"symbol": symbol, "interval": interval,
                    "startTime": cur, "endTime": end_ms - 1, "limit": limit},
            timeout=20,
        )
        resp.raise_for_status()
        page = resp.json()
        if not page:
            break
        rows.extend(page)
        cur = page[-1][0] + 1
        if len(page) < limit:
            break
        time.sleep(0.1)
    return rows


def klines_to_df(rows: list) -> pd.DataFrame:
    cols = [
        "ts_open", "open", "high", "low", "close", "volume",
        "ts_close", "quote_volume", "num_trades",
        "taker_buy_base", "taker_buy_quote", "_ignore",
    ]
    df = pd.DataFrame(rows, columns=cols)
    df = df.drop(columns=["_ignore"])
    for c in ["open", "high", "low", "close", "volume",
              "quote_volume", "taker_buy_base", "taker_buy_quote"]:
        df[c] = df[c].astype(float)
    df["ts_ms"] = df["ts_open"].astype("int64")
    df["datetime_utc"] = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)

    # bar_delta estimado: taker_buy - taker_sell
    df["taker_sell_base"] = df["volume"] - df["taker_buy_base"]
    df["bar_delta"] = df["taker_buy_base"] - df["taker_sell_base"]

    # vr = volumen / media móvil 20 barras
    df["vr"] = df["volume"] / df["volume"].rolling(20, min_periods=1).mean()

    # CVD con reset diario
    df["date"] = df["datetime_utc"].dt.date
    df["cvd_session"] = df.groupby("date")["bar_delta"].cumsum()

    # cvd_slope: pendiente lineal sobre ventana 5 barras
    df["cvd_slope"] = (
        df["cvd_session"]
        .rolling(5, min_periods=2)
        .apply(lambda x: (x.iloc[-1] - x.iloc[0]) / (len(x) - 1), raw=False)
    )

    return df[[
        "ts_ms", "datetime_utc", "open", "high", "low", "close",
        "volume", "quote_volume", "num_trades",
        "taker_buy_base", "taker_sell_base", "bar_delta",
        "vr", "cvd_session", "cvd_slope",
    ]]


# ---------------------------------------------------------------------------

def download_range(symbol: str, start_dt: datetime, end_dt: datetime,
                   interval: str = "1m") -> pd.DataFrame:
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms   = int(end_dt.timestamp() * 1000)
    total_min = (end_dt - start_dt).total_seconds() / 60
    print(f"  Descargando {symbol} {interval} | {start_dt.date()} -> {end_dt.date()} (~{int(total_min):,} barras)")
    rows = fetch_klines(symbol, interval, start_ms, end_ms)
    if not rows:
        return pd.DataFrame()
    return klines_to_df(rows)


def save(df: pd.DataFrame, path: Path, as_csv: bool):
    path.parent.mkdir(parents=True, exist_ok=True)
    if as_csv:
        path = path.with_suffix(".csv")
        df.to_csv(path, index=False)
    else:
        path = path.with_suffix(".parquet")
        df.to_parquet(path, index=False)
    size_mb = path.stat().st_size / 1024 / 1024
    print(f"  Guardado: {path}  ({len(df):,} filas, {size_mb:.1f} MB)")
    return path


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol",  default="BTCUSDT")
    parser.add_argument("--months",  type=int, default=3,
                        help="Ultimos N meses")
    parser.add_argument("--daily",   action="store_true",
                        help="Un archivo por dia en rango --start/--end")
    parser.add_argument("--start",   default=None, help="YYYY-MM-DD")
    parser.add_argument("--end",     default=None, help="YYYY-MM-DD (exclusive)")
    parser.add_argument("--interval", default="1m")
    parser.add_argument("--output",  default=None,
                        help="Ruta de salida (default: dataset/<symbol>_Xm)")
    parser.add_argument("--csv",     action="store_true",
                        help="Guardar como CSV en vez de Parquet")
    args = parser.parse_args()

    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)

    if args.daily:
        start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc) if args.start else now - timedelta(days=7)
        end   = datetime.strptime(args.end,   "%Y-%m-%d").replace(tzinfo=timezone.utc) if args.end   else now
        days = (end - start).days
        print(f"Modo diario: {days} dias | {args.symbol} {args.interval}")
        all_dfs = []
        for i in tqdm(range(days), desc="dias"):
            day_start = start + timedelta(days=i)
            day_end   = day_start + timedelta(days=1)
            df = download_range(args.symbol, day_start, day_end, args.interval)
            if not df.empty:
                all_dfs.append(df)
        if not all_dfs:
            print("Sin datos.")
            return
        df_all = pd.concat(all_dfs, ignore_index=True)
        stem = args.output or str(DATASET / f"{args.symbol.lower()}_{days}d")
        save(df_all, Path(stem), args.csv)

    else:
        end   = now
        start = now - timedelta(days=args.months * 30)
        df = download_range(args.symbol, start, end, args.interval)
        if df.empty:
            print("Sin datos.")
            return
        stem = args.output or str(DATASET / f"{args.symbol.lower()}_{args.months}m")
        saved = save(df, Path(stem), args.csv)
        print(f"\nResumen:")
        print(f"  Filas      : {len(df):,}")
        print(f"  Rango      : {df['datetime_utc'].min()} -> {df['datetime_utc'].max()}")
        print(f"  bar_delta  : min={df['bar_delta'].min():.0f}  max={df['bar_delta'].max():.0f}")
        print(f"  cvd_session: min={df['cvd_session'].min():.0f}  max={df['cvd_session'].max():.0f}")


if __name__ == "__main__":
    main()
