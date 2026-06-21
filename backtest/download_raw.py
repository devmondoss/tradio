"""
download_raw.py — descarga PULCRA de microestructura cruda Bybit perp (BTCUSDT)
================================================================================
Recupera lo que el pipeline viejo agregaba-y-borraba. Esta vez CONSERVA:
  - raw_trades/{date}.parquet : ticks a RESOLUCIÓN COMPLETA (ts_ms, price, size, side, tick_dir)
  - ob_1s/{date}.parquet      : order book derivado a 1s (mid, microprice, spread, OBI L5/10/25,
                                profundidad top-25, tamaños al mejor) — rico, no solo M1 OBI
El OB crudo (.zip, 368MB/día) se procesa en STREAMING y se borra (disco no aguanta el año).
Opcional: --keep-ob-raw conserva los N zips más recientes.

Fuentes (archivos históricos permanentes):
  Trades : https://public.bybit.com/trading/BTCUSDT/BTCUSDT{date}.csv.gz
  OB     : https://quote-saver.bycsi.com/orderbook/linear/BTCUSDT/{date}_BTCUSDT_{ob500|ob200}.data.zip

Pulcritud: descarga idempotente+resumible, valida Content-Length y descompresión, dedup,
gaps temporales, y un manifest auditable (_manifest.parquet) con una fila por día.

Uso:
  python backtest/download_raw.py --start 2026-06-15 --end 2026-06-17           # smoke 3 días
  python backtest/download_raw.py --start 2025-06-19 --end 2026-06-18           # 1 año
  python backtest/download_raw.py --start ... --end ... --no-ob                 # solo ticks
"""
import sys, os, gzip, zipfile, argparse, urllib.request, shutil, time
from pathlib import Path
from datetime import datetime, timezone, timedelta
import pandas as pd

try:
    import orjson as _json
    def jloads(b): return _json.loads(b)
except Exception:
    import json as _json
    def jloads(b): return _json.loads(b)

ROOT     = Path(__file__).parent.parent
PERP     = Path(os.environ.get("TRADIO_PERP", str(ROOT / "data/bybit-perp")))  # raíz portátil (ej. D:\ANTHONY)
RAW_T    = PERP / "raw_trades"      # ticks full-res (CONSERVAR)
OB_1S    = PERP / "ob_1s"           # OB derivado 1s (CONSERVAR)
OB_TMP   = ROOT / "data/_ob_tmp"    # zips OB en tránsito — scratch LOCAL (C:), no al USB
MANIFEST = PERP / "_manifest.parquet"

TRADES_URL = "https://public.bybit.com/trading/BTCUSDT/BTCUSDT{date}.csv.gz"
OB_URL     = "https://quote-saver.bycsi.com/orderbook/linear/BTCUSDT/{date}_BTCUSDT_{depth}.data.zip"
OB500_LAST = "2025-08-20"


def ob_depth_for(d): return "ob500" if d <= OB500_LAST else "ob200"


def download(url, dest, timeout=600, retries=3):
    """Descarga con reintentos; valida Content-Length. Devuelve (ok, bytes)."""
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
                print(f"    DOWNLOAD FAIL {url.split('/')[-1]}: {e}", file=sys.stderr)
                return False, 0
            time.sleep(2 * (k + 1))
    return False, 0


# ── ticks ────────────────────────────────────────────────────────────────────
def parse_ticks(gz_path):
    """CSV.gz de trades → DataFrame tick-by-tick full-res."""
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
    if "tickDirection" in df: out["tick_dir"] = df["tickDirection"].astype("category")
    return out


def tick_quality(t, date_str):
    d0 = int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    d1 = d0 + 86_400_000
    ts = t["ts_ms"].values
    inrange = ((ts >= d0) & (ts < d1)).mean() * 100
    mono = bool((ts[1:] >= ts[:-1]).all())
    minute_cov = pd.Series((ts // 60_000)).nunique() / 1440 * 100
    return {"n_trades": len(t), "ts_in_day_pct": round(float(inrange), 2),
            "monotonic": mono, "minute_cov_pct": round(float(minute_cov), 1)}


# ── order book → 1s ────────────────────────────────────────────────────────────
class Book:
    __slots__ = ("b", "a")
    def __init__(self): self.b = {}; self.a = {}
    def apply(self, data, t):
        if t == "snapshot": self.b.clear(); self.a.clear()
        for p, q in data.get("b", []):
            p = float(p); q = float(q)
            if q == 0.0: self.b.pop(p, None)
            else: self.b[p] = q
        for p, q in data.get("a", []):
            p = float(p); q = float(q)
            if q == 0.0: self.a.pop(p, None)
            else: self.a[p] = q
    def features(self):
        if not self.b or not self.a: return None
        bb = max(self.b); ba = min(self.a)
        if not (ba > bb > 0): return None
        tb = sorted(self.b, reverse=True)[:25]; ta = sorted(self.a)[:25]
        if len(tb) < 5 or len(ta) < 5: return None
        def s(d, ks): return sum(d[p] for p in ks)
        b5, a5 = s(self.b, tb[:5]), s(self.a, ta[:5])
        b10, a10 = s(self.b, tb[:10]), s(self.a, ta[:10])
        b25, a25 = s(self.b, tb), s(self.a, ta)
        bsz, asz = self.b[bb], self.a[ba]
        mid = (bb + ba) / 2
        micro = (ba * bsz + bb * asz) / (bsz + asz) if (bsz + asz) > 0 else mid
        def obi(b, a): return (b - a) / (b + a) if (b + a) > 0 else 0.0
        return (bb, ba, mid, micro, (ba - bb) / mid * 1e4, bsz, asz,
                obi(b5, a5), obi(b10, a10), obi(b25, a25), b25, a25)


def parse_ob_1s(zip_path):
    """ZIP L2 (snapshot+deltas ~200ms) → DataFrame 1 fila/segundo (estado fin-de-segundo)."""
    book = Book(); rows = []; cur_sec = -1; n_upd = 0; last_feat = None
    with zipfile.ZipFile(zip_path, "r") as zf:
        inner = zf.namelist()[0]
        with zf.open(inner) as raw:
            for line in raw:
                line = line.strip()
                if not line: continue
                try: msg = jloads(line)
                except Exception: continue
                mt = msg.get("type")
                if mt not in ("snapshot", "delta"): continue
                ts = int(msg.get("ts", 0)); sec = ts // 1000
                if cur_sec == -1: cur_sec = sec
                if sec != cur_sec:
                    if last_feat is not None:
                        rows.append((cur_sec * 1000, n_upd) + last_feat)
                    cur_sec = sec; n_upd = 0
                book.apply(msg.get("data", {}), mt); n_upd += 1
                f = book.features()
                if f is not None: last_feat = f
            if last_feat is not None:
                rows.append((cur_sec * 1000, n_upd) + last_feat)
    if not rows: return pd.DataFrame()
    return pd.DataFrame(rows, columns=[
        "ts_ms", "n_upd", "bb", "ba", "mid", "microprice", "spread_bps", "bid_sz1", "ask_sz1",
        "obi5", "obi10", "obi25", "depth_bid25", "depth_ask25"])


def daterange(s, e):
    d0 = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    d1 = datetime.strptime(e, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    d = d0
    while d <= d1:
        yield d.strftime("%Y-%m-%d"); d += timedelta(days=1)


def load_manifest():
    if MANIFEST.exists(): return pd.read_parquet(MANIFEST).set_index("date").to_dict("index")
    return {}


def process_day(ds, no_ob=False, ob_archive=None):
    """Procesa un día completo (ticks + OB→1s). Idempotente. Devuelve rec dict.
    ob_archive: si se pasa un directorio, los zips OB se CONSERVAN ahí (re-derivables)
    y se reutilizan si ya existen (no re-descarga). Si es None, se borran tras parsear."""
    rec = {"date": ds, "ts": int(time.time())}
    tpq = RAW_T / f"{ds}.parquet"
    if tpq.exists():
        rec["tick_status"] = "cached"
    else:
        gz = OB_TMP / f"{ds}_trades.csv.gz"
        ok, by = download(TRADES_URL.format(date=ds), gz)
        if ok:
            try:
                t = parse_ticks(gz); q = tick_quality(t, ds)
                t.to_parquet(tpq, index=False, engine="pyarrow", compression="zstd")
                rec.update({"tick_status": "ok", "tick_bytes_gz": by, **q})
            except Exception as e:
                rec["tick_status"] = f"parse_fail:{e}"
            finally:
                if gz.exists(): gz.unlink()
        else:
            rec["tick_status"] = "dl_fail"
    if not no_ob:
        opq = OB_1S / f"{ds}.parquet"
        if opq.exists():
            rec["ob_status"] = "cached"
        else:
            depth = ob_depth_for(ds)
            zdir = Path(ob_archive) if ob_archive else OB_TMP
            z = zdir / f"{ds}_BTCUSDT_{depth}.data.zip"
            if z.exists():                       # archivado: reusar, no re-descargar
                ok, by = True, z.stat().st_size
            else:
                ok, by = download(OB_URL.format(date=ds, depth=depth), z)
            if ok:
                try:
                    ob = parse_ob_1s(z)
                    if ob.empty:
                        rec["ob_status"] = "empty"
                    else:
                        ob.to_parquet(opq, index=False, engine="pyarrow", compression="zstd")
                        rec.update({"ob_status": "ok", "ob_bytes_zip": by,
                                    "ob_sec_cov_pct": round(len(ob) / 86400 * 100, 1)})
                except Exception as e:
                    rec["ob_status"] = f"parse_fail:{e}"
                finally:
                    if z.exists() and not ob_archive: z.unlink()   # solo borrar si NO archivamos
            else:
                rec["ob_status"] = "dl_fail"
    return rec


def main():
    import concurrent.futures as cf
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True); ap.add_argument("--end", required=True)
    ap.add_argument("--no-ob", action="store_true", help="solo ticks")
    ap.add_argument("--ob-archive", default=None, help="directorio para CONSERVAR los zips OB crudos (ej. D:/tradio_raw/ob_zips)")
    ap.add_argument("--workers", type=int, default=1, help="días en paralelo (descarga IO + parse CPU)")
    args = ap.parse_args()
    for d in (RAW_T, OB_1S, OB_TMP): d.mkdir(parents=True, exist_ok=True)
    if args.ob_archive:
        Path(args.ob_archive).mkdir(parents=True, exist_ok=True)
        print(f"OB crudo se CONSERVA en: {args.ob_archive}")

    days = list(daterange(args.start, args.end))
    print(f"Rango: {args.start} → {args.end} ({len(days)} días) | OB: {'no' if args.no_ob else 'sí'} | workers: {args.workers}")
    rows = []; done = 0
    if args.workers > 1:
        with cf.ProcessPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(process_day, ds, args.no_ob, args.ob_archive): ds for ds in days}
            for fut in cf.as_completed(futs):
                rec = fut.result(); rows.append(rec); done += 1
                if done % 5 == 0 or done == len(days):
                    print(f"  [{done}/{len(days)}] {rec['date']} | tick={rec.get('tick_status')} ob={rec.get('ob_status')}", flush=True)
    else:
        for i, ds in enumerate(days, 1):
            rec = process_day(ds, args.no_ob, args.ob_archive); rows.append(rec)
            if i % 5 == 0 or i == len(days):
                print(f"  [{i}/{len(days)}] {ds} | tick={rec.get('tick_status')} ob={rec.get('ob_status')}", flush=True)

    # MANIFEST (merge)
    new = pd.DataFrame(rows)
    if MANIFEST.exists():
        old = pd.read_parquet(MANIFEST)
        new = pd.concat([old[~old["date"].isin(new["date"])], new], ignore_index=True)
    new = new.sort_values("date").reset_index(drop=True)
    new.to_parquet(MANIFEST, index=False)

    # REPORTE
    print("\n=== REPORTE ===")
    done = new[new["date"].isin(days)]
    print(f"  ticks ok/cached: {done['tick_status'].isin(['ok','cached']).sum()}/{len(days)}")
    if not args.no_ob:
        print(f"  ob    ok/cached: {done['ob_status'].isin(['ok','cached']).sum()}/{len(days)}")
        if "ob_sec_cov_pct" in done:
            print(f"  ob cobertura/seg media: {done['ob_sec_cov_pct'].mean():.1f}%")
    if "minute_cov_pct" in done:
        print(f"  ticks cobertura/min media: {done['minute_cov_pct'].mean():.1f}% | monotónico: {done.get('monotonic', pd.Series([True])).all()}")
    rt = sum(p.stat().st_size for p in RAW_T.glob('*.parquet')) / 1e9
    ob = sum(p.stat().st_size for p in OB_1S.glob('*.parquet')) / 1e9
    print(f"  disco: raw_trades {rt:.2f} GB | ob_1s {ob:.2f} GB | manifest {MANIFEST}")


if __name__ == "__main__":
    main()
