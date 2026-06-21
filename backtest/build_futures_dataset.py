"""
build_futures_dataset.py
------------------------
Construye el dataset M1 de BTCUSDT PERPETUAL (Bybit linear) con las MISMAS 81
features que el spot, descargando de forma INCREMENTAL (día a día) y borrando
el crudo tras procesar — el disco en reposo no crece.

Fuentes:
    Trades : https://public.bybit.com/trading/BTCUSDT/BTCUSDT{YYYY-MM-DD}.csv.gz  (oficial, diario)
    OB     : https://quote-saver.bycsi.com/orderbook/linear/BTCUSDT/{date}_BTCUSDT_{ob500|ob200}.data.zip
             ob500 hasta 2025-08-20, ob200 desde 2025-08-21

Reusa:
    parse_orderbook.parse_day  (OrderBook L2 → OBI M1, agnóstico a profundidad)
    compute_spot_features.enrich  (M1 base → 81 features)

Uso:
    python backtest/build_futures_dataset.py --start 2026-05-29 --end 2026-05-31   # test 3 días
    python backtest/build_futures_dataset.py --start 2025-01-01 --end 2026-06-17   # completo
    python backtest/build_futures_dataset.py --start ... --end ... --keep-raw      # no borrar crudo
"""
import gzip
import sys
import os
import argparse
import urllib.request
import shutil
import subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pandas as pd

import compute_spot_features as FEAT   # reusa enrich()

ROOT     = Path(__file__).parent.parent
PERP_DIR = ROOT / "data/bybit-perp"
RAW      = PERP_DIR / "raw"
OB_RAW   = PERP_DIR / "orderbook"            # zips para el parser Rust
CACHE    = PERP_DIR / "cache"                # caches de trades (Python)
OB_CACHE = PERP_DIR / "ob_cache_rust"        # caches de OBI (Rust)
PROC     = PERP_DIR / "processed"
OUT      = PROC / "btcusdt_perp_m1.parquet"
RUST_BIN = ROOT / "target/release/ob_parser.exe"

TRADES_URL = "https://public.bybit.com/trading/BTCUSDT/BTCUSDT{date}.csv.gz"
OB_URL     = "https://quote-saver.bycsi.com/orderbook/linear/BTCUSDT/{date}_BTCUSDT_{depth}.data.zip"
OB500_LAST = "2025-08-20"   # ob500 hasta aquí; ob200 después


def download(url: str, dest: Path, timeout: int = 300) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f, length=1 << 20)
        return True
    except Exception as e:
        print(f"    download FAIL {url.split('/')[-1]}: {e}", file=sys.stderr)
        if dest.exists():
            dest.unlink()
        return False


def parse_trades_fut(gz_path: Path) -> pd.DataFrame:
    """Trades del perpetuo (timestamp en segundos, side Buy/Sell, volumen=size) → M1."""
    with gzip.open(gz_path, "rt", encoding="utf-8", newline="") as f:
        df = pd.read_csv(f, usecols=["timestamp", "price", "size", "side"])
    df["ts_ms"]  = (df["timestamp"].astype("float64") * 1000).astype("int64")
    df["ts_min"] = (df["ts_ms"] // 60_000) * 60_000

    ohlcv = df.groupby("ts_min", sort=True)["price"].agg(open="first", high="max", low="min", close="last")
    vol   = df.groupby("ts_min")["size"].sum().rename("volume")
    buy   = df[df["side"] == "Buy"].groupby("ts_min")["size"].sum().rename("buy_vol")
    sell  = df[df["side"] == "Sell"].groupby("ts_min")["size"].sum().rename("sell_vol")

    bars = pd.concat([ohlcv, vol, buy, sell], axis=1)
    bars["buy_vol"]  = bars["buy_vol"].fillna(0.0)
    bars["sell_vol"] = bars["sell_vol"].fillna(0.0)
    bars["delta"]    = bars["buy_vol"] - bars["sell_vol"]
    bars.index.name  = "ts_ms"
    return bars.reset_index()


def ob_depth_for(date_str: str) -> str:
    return "ob500" if date_str <= OB500_LAST else "ob200"


def download_day(date_str: str) -> bool:
    """Descarga trades (parse Python) + zip OB (lo deja para el parser Rust)."""
    cache_t = CACHE / f"{date_str}_trades.parquet"
    if not cache_t.exists():
        gz = RAW / f"{date_str}_trades.csv.gz"
        if download(TRADES_URL.format(date=date_str), gz):
            try:
                parse_trades_fut(gz).to_parquet(cache_t, index=False)
            finally:
                if gz.exists():
                    gz.unlink()

    # OB: descargar zip (lo parsea Rust después). Saltar si ya hay cache Rust.
    ob_cache = OB_CACHE / f"{date_str}.parquet"
    depth    = ob_depth_for(date_str)
    ob_zip   = OB_RAW / f"{date_str}_BTCUSDT_{depth}.data.zip"
    if not ob_cache.exists() and not ob_zip.exists():
        download(OB_URL.format(date=date_str, depth=depth), ob_zip)

    return cache_t.exists()


def run_rust_ob(start: str, end: str):
    """Parsea todos los zips en OB_RAW con el binario Rust paralelo → caches en OB_CACHE."""
    if not RUST_BIN.exists():
        print(f"  WARN: falta {RUST_BIN}. Compila: cargo +stable-x86_64-pc-windows-gnu build --release -p ob-parser")
        return
    cmd = [str(RUST_BIN), "--ob-dir", str(OB_RAW), "--cache-dir", str(OB_CACHE),
           "--out", str(PERP_DIR / "m1_obi.parquet"), "--start", start, "--end", end]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    subprocess.run(cmd, cwd=str(ROOT), env=env)


def daterange(start: str, end: str):
    d0 = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    d1 = datetime.strptime(end,   "%Y-%m-%d").replace(tzinfo=timezone.utc)
    d = d0
    while d <= d1:
        yield d.strftime("%Y-%m-%d")
        d += timedelta(days=1)


def build_parquet():
    """Combina cachés → merge trades+obi → cvd → enrich() → parquet final."""
    t_files = sorted(CACHE.glob("*_trades.parquet"))
    if not t_files:
        sys.exit("No hay cachés de trades.")
    trades = pd.concat([pd.read_parquet(f) for f in t_files], ignore_index=True)
    trades = trades.sort_values("ts_ms").drop_duplicates("ts_ms", keep="last").reset_index(drop=True)
    trades["cvd"] = trades["delta"].cumsum()

    o_files = sorted(OB_CACHE.glob("*.parquet"))   # caches del parser Rust (YYYY-MM-DD.parquet)
    obi = (pd.concat([pd.read_parquet(f) for f in o_files], ignore_index=True)
           .sort_values("ts_ms").drop_duplicates("ts_ms").reset_index(drop=True)) if o_files else None

    merged = trades.merge(obi, on="ts_ms", how="left") if obi is not None else trades
    merged = merged.sort_values("ts_ms").reset_index(drop=True)

    cov = merged["obi10_mean"].notna().mean() * 100 if "obi10_mean" in merged else 0.0
    print(f"  base: {len(merged):,} barras M1 | cobertura OBI {cov:.1f}%")

    # rellenar columnas OB faltantes para que enrich() no falle
    for c in ["obi5_mean","obi10_mean","obi20_mean","obi10_min","obi10_max","spread_mean",
              "near5_ask","near5_bid","max_ask5","max_bid5","mid_open","mid_close","n_snapshots"]:
        if c not in merged.columns:
            merged[c] = 0.0

    print("  enriqueciendo con 81 features (enrich)...")
    merged = FEAT.enrich(merged, verbose=False, tf="m1")

    PROC.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(OUT, index=False, engine="pyarrow")
    first = pd.Timestamp(merged["ts_ms"].iloc[0],  unit="ms", tz="UTC")
    last  = pd.Timestamp(merged["ts_ms"].iloc[-1], unit="ms", tz="UTC")
    print(f"\n[OK] {OUT}")
    print(f"   {len(merged):,} barras | {len(merged.columns)} columnas | {first} -> {last}")
    print(f"   {OUT.stat().st_size/1e6:.1f} MB | OBI cov {cov:.0f}% | vah no-nulo {merged['vp_vah'].notna().mean()*100:.0f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end",   required=True, help="YYYY-MM-DD")
    ap.add_argument("--delete-zips", action="store_true", help="borrar zips OB al final (default: acumular)")
    ap.add_argument("--no-build", action="store_true", help="solo descargar/parsear, no construir parquet")
    args = ap.parse_args()

    for d in (RAW, OB_RAW, CACHE, OB_CACHE, PROC):
        d.mkdir(parents=True, exist_ok=True)

    days = list(daterange(args.start, args.end))
    print(f"Rango: {args.start} - {args.end}  ({len(days)} dias)")

    # 1. Descargar TODO (trades parse Python + zips OB acumulados, sin borrar)
    for i, ds in enumerate(days, 1):
        download_day(ds)
        if i % 20 == 0 or i == len(days):
            print(f"  descargados {i}/{len(days)} dias ({ds})", flush=True)

    # 2. Parsear TODO el OB con Rust de un tiro (paralelo sobre todos los cores)
    print("Parseando order book con Rust (paralelo, de tiro)...", flush=True)
    run_rust_ob(args.start, args.end)

    # 3. (opcional) borrar zips solo si se pide explicito; por defecto se acumulan
    if args.delete_zips:
        for z in OB_RAW.glob("*.data.zip"):
            z.unlink()
        print("  zips OB borrados.")

    if not args.no_build:
        build_parquet()


if __name__ == "__main__":
    main()
