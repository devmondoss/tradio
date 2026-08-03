"""
build_footprint.py — descarga trades + parsea footprint en RUST + mergea al dataset de futuros.
Descarga por mes (controla disco), parser Rust paralelo, borra crudo, combina.
Generalizado a --symbol (BTCUSDT/ETHUSDT/SOLUSDT) — antes hardcodeado a BTCUSDT.

Uso:
    python backtest/build_footprint.py --symbol BTCUSDT --start 2025-01-01 --end 2026-07-06
    python backtest/build_footprint.py --symbol ETHUSDT --start 2025-06-21 --end 2026-07-06 --data-dir E:/bybit-data
    python backtest/build_footprint.py --start ... --end ... --no-merge   # solo cachear
"""
import sys, os, argparse, urllib.request, shutil, subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta
import pandas as pd

ROOT = Path(__file__).parent.parent
RUST = ROOT / "target/release/trades_parser.exe"

# variables globales inicializadas en init_paths()
SYMBOL = "BTCUSDT"
PERP = ROOT / "data/bybit-perp"
TDIR = PERP / "trades_raw"
FCACHE = PERP / "fp_cache_rust"
OUT_FP = PERP / "m1_footprint.parquet"
DATASET = PERP / "processed/btcusdt_perp_m1.parquet"
TRADES_URL = "https://public.bybit.com/trading/BTCUSDT/BTCUSDT{date}.csv.gz"


def init_paths(symbol: str, data_dir: str | None = None):
    global SYMBOL, PERP, TDIR, FCACHE, OUT_FP, DATASET, TRADES_URL
    SYMBOL = symbol.upper()
    slug = symbol.lower().replace("usdt", "")
    dirname = "bybit-perp" if slug == "btc" else f"bybit-perp-{slug}"
    base = Path(data_dir) if data_dir else ROOT / "data"
    PERP = base / dirname
    TDIR = PERP / "trades_raw"
    FCACHE = PERP / "fp_cache_rust"
    OUT_FP = PERP / "m1_footprint.parquet"
    DATASET = PERP / f"processed/{SYMBOL.lower()}_perp_m1.parquet"
    TRADES_URL = f"https://public.bybit.com/trading/{SYMBOL}/{SYMBOL}{{date}}.csv.gz"


def download(date_str):
    TDIR.mkdir(parents=True, exist_ok=True)
    dest = TDIR / f"{SYMBOL}{date_str}.csv.gz"
    cache = FCACHE / f"{date_str}.parquet"
    if dest.exists() or cache.exists():
        return
    try:
        req = urllib.request.Request(TRADES_URL.format(date=date_str), headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f, length=1 << 20)
    except Exception as e:
        print(f"    download FAIL {date_str}: {e}", file=sys.stderr)
        if dest.exists(): dest.unlink()


def run_rust(start, end):
    if not RUST.exists():
        sys.exit(f"Falta {RUST}. Compila: cargo build --release -p trades-parser")
    cmd = [str(RUST), "--trades-dir", str(TDIR), "--cache-dir", str(FCACHE),
           "--out", str(OUT_FP), "--start", start, "--end", end, "--symbol", SYMBOL]
    subprocess.run(cmd, cwd=str(ROOT))


def daterange(a, b):
    d0 = datetime.strptime(a, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    d1 = datetime.strptime(b, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    d = d0
    while d <= d1:
        yield d.strftime("%Y-%m-%d"); d += timedelta(days=1)


def merge_into_dataset():
    files = sorted(FCACHE.glob("*.parquet"))
    if not files:
        sys.exit("Sin caches de footprint.")
    fp = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    fp = fp.sort_values("ts_ms").drop_duplicates("ts_ms").reset_index(drop=True)
    fp.to_parquet(OUT_FP, index=False)
    print(f"  footprint combinado: {len(fp):,} barras, {len(fp.columns)} cols")

    # mergear SOLO las columnas footprint nuevas (no pisar OHLCV/delta existentes).
    # si ya había un merge previo (parcial o viejo), se dropean esas columnas antes
    # de re-mergear para no duplicar con sufijo _fp.
    base = pd.read_parquet(DATASET)
    fp_cols = [c for c in fp.columns if c.startswith("fp_") or c in
               ("n_trades", "max_trade", "plus_ticks", "minus_ticks")]
    base = base.drop(columns=[c for c in fp_cols if c in base.columns])
    merged = base.merge(fp[["ts_ms"] + fp_cols], on="ts_ms", how="left")
    merged.to_parquet(DATASET, index=False)
    cov = merged["fp_poc"].notna().mean() * 100 if "fp_poc" in merged else 0.0
    print(f"  dataset: {len(merged):,} barras, {len(merged.columns)} cols (+{len(fp_cols)} footprint, cobertura {cov:.0f}%)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--start", required=True); ap.add_argument("--end", required=True)
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--no-merge", action="store_true")
    ap.add_argument("--keep-gz", action="store_true")
    args = ap.parse_args()
    init_paths(args.symbol, args.data_dir)
    for d in (TDIR, FCACHE):
        d.mkdir(parents=True, exist_ok=True)

    days = list(daterange(args.start, args.end))
    months: dict[str, list[str]] = {}
    for ds in days:
        months.setdefault(ds[:7], []).append(ds)

    print(f"Footprint pipeline: {SYMBOL} dir={PERP}  {args.start} - {args.end} ({len(days)} dias, {len(months)} meses)")
    for mi, (ym, md) in enumerate(sorted(months.items()), 1):
        print(f"\n=== mes {mi}/{len(months)}: {ym} ===", flush=True)
        for ds in md:
            download(ds)
        run_rust(md[0], md[-1])                    # parser Rust paralelo del mes
        if not args.keep_gz:
            for gz in TDIR.glob("*.csv.gz"):
                gz.unlink()

    if not args.no_merge:
        merge_into_dataset()


if __name__ == "__main__":
    main()
