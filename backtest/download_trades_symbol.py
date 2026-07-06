"""
download_trades_symbol.py — descarga ticks (raw_trades) para cualquier símbolo Bybit perp
==========================================================================================
Fuente: https://public.bybit.com/trading/{SYMBOL}/{SYMBOL}{date}.csv.gz

Uso:
  python backtest/download_trades_symbol.py --symbol ETHUSDT --out-dir E:/bybit-data/bybit-perp-eth/raw_trades --start 2025-06-21 --end 2026-06-20
  python backtest/download_trades_symbol.py --symbol SOLUSDT --out-dir E:/bybit-data/bybit-perp-sol/raw_trades --start 2025-06-21 --end 2026-06-20

Idempotente: saltea días ya descargados.
"""
import sys, os, gzip, argparse, shutil, time
from pathlib import Path
from datetime import datetime, timezone, timedelta
import pandas as pd

ROOT    = Path(__file__).parent.parent
TMP_DIR = ROOT / "data" / "_trades_tmp"

TRADES_URL = "https://public.bybit.com/trading/{sym}/{sym}{date}.csv.gz"


def download(url, dest, timeout=600, retries=3):
    import urllib.request
    for k in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                clen = int(r.headers.get("Content-Length", 0))
                with open(dest, "wb") as f:
                    shutil.copyfileobj(r, f, length=1 << 20)
            got = dest.stat().st_size
            if clen and got != clen:
                raise IOError(f"size mismatch {got}!={clen}")
            return True, got
        except Exception as e:
            if dest.exists(): dest.unlink()
            if k == retries - 1:
                print(f"    FAIL {url.split('/')[-1]}: {e}", file=sys.stderr)
                return False, 0
            time.sleep(2 * (k + 1))
    return False, 0


def parse_ticks(gz_path):
    with gzip.open(gz_path, "rt", encoding="utf-8", newline="") as f:
        cols = pd.read_csv(f, nrows=0).columns.tolist()
    use = [c for c in ["timestamp", "price", "size", "side", "tickDirection"] if c in cols]
    with gzip.open(gz_path, "rt", encoding="utf-8", newline="") as f:
        df = pd.read_csv(f, usecols=use)
    df["ts_ms"] = (df["timestamp"].astype("float64") * 1000).round().astype("int64")
    df = df.sort_values("ts_ms", kind="stable").reset_index(drop=True)
    out = pd.DataFrame({
        "ts_ms": df["ts_ms"].astype("int64"),
        "price": df["price"].astype("float64"),
        "size":  df["size"].astype("float32"),
        "side":  df["side"].astype("category"),
    })
    if "tickDirection" in df:
        out["tick_dir"] = df["tickDirection"].astype("category")
    return out


def daterange(s, e):
    d0 = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    d1 = datetime.strptime(e, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    d = d0
    while d <= d1:
        yield d.strftime("%Y-%m-%d")
        d += timedelta(days=1)


def process_day(sym, ds, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tpq = out_dir / f"{ds}.parquet"
    if tpq.exists():
        return ds, "cached"

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    gz = TMP_DIR / f"{sym}_{ds}.csv.gz"
    url = TRADES_URL.format(sym=sym, date=ds)
    ok, by = download(url, gz)
    if not ok:
        return ds, "dl_fail"
    try:
        t = parse_ticks(gz)
        t.to_parquet(tpq, index=False, engine="pyarrow", compression="zstd")
        return ds, f"ok ({len(t):,} ticks, {by/1e6:.1f}MB gz)"
    except Exception as e:
        return ds, f"parse_fail:{e}"
    finally:
        if gz.exists():
            gz.unlink()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol",  required=True, help="ej. ETHUSDT")
    ap.add_argument("--out-dir", required=True, help="directorio de salida para los .parquet")
    ap.add_argument("--start",   required=True, help="YYYY-MM-DD")
    ap.add_argument("--end",     required=True, help="YYYY-MM-DD")
    ap.add_argument("--workers", type=int, default=3, help="días en paralelo")
    args = ap.parse_args()

    days = list(daterange(args.start, args.end))
    out  = Path(args.out_dir)
    already = sum(1 for d in days if (out / f"{d}.parquet").exists())
    print(f"Symbol: {args.symbol}  |  {args.start}->{args.end}  ({len(days)} dias)")
    print(f"Salida: {out}")
    print(f"Ya descargados: {already}  |  Pendientes: {len(days)-already}  |  Workers: {args.workers}")

    if args.workers > 1:
        import concurrent.futures as cf
        done = 0
        with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(process_day, args.symbol, ds, args.out_dir): ds for ds in days}
            for fut in cf.as_completed(futs):
                ds, status = fut.result()
                done += 1
                if done % 10 == 0 or done == len(days) or "fail" in status:
                    print(f"  [{done:>3}/{len(days)}] {ds} -> {status}", flush=True)
    else:
        for i, ds in enumerate(days, 1):
            _, status = process_day(args.symbol, ds, args.out_dir)
            if i % 10 == 0 or i == len(days) or "fail" in status:
                print(f"  [{i:>3}/{len(days)}] {ds} -> {status}", flush=True)

    # resumen final
    parquets = list(out.glob("*.parquet"))
    total_gb = sum(p.stat().st_size for p in parquets) / 1e9
    ok_days  = sum(1 for d in days if (out / f"{d}.parquet").exists())
    print(f"\n  OK {ok_days}/{len(days)} dias  |  {total_gb:.2f} GB en disco  |  {out}")


if __name__ == "__main__":
    main()
