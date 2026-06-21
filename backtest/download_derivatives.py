"""
download_derivatives.py — capa de DERIVADOS Bybit perp (lo que nunca tuvimos)
==============================================================================
Open Interest (5min) + Funding rate (8h) vía API v5 de Bybit. Información ORTOGONAL al
precio y al order book (posicionamiento apalancado) — candidata a tener el alfa que la
micro de precio/libro no tenía (estudio M1: predictividad ~0).

Guarda en TRADIO_PERP (portátil, ej. E:\\Tonnio):
  oi_5m.parquet   : ts_ms, open_interest
  funding.parquet : ts_ms, funding_rate

Uso: TRADIO_PERP=E:/tradio-data/bybit-perp python backtest/download_derivatives.py \
        --start 2025-06-19 --end 2026-06-18
"""
import os, sys, json, time, argparse, urllib.request, urllib.parse
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd

ROOT = Path(__file__).parent.parent
PERP = Path(os.environ.get("TRADIO_PERP", str(ROOT / "data/bybit-perp")))
BASE = "https://api.bybit.com"


def get(path, params, retries=4):
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    for k in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                j = json.loads(r.read())
            if j.get("retCode") != 0:
                raise IOError(f"retCode {j.get('retCode')}: {j.get('retMsg')}")
            return j["result"]
        except Exception as e:
            if k == retries - 1:
                print(f"    API FAIL {path}: {e}", file=sys.stderr); return None
            time.sleep(1.5 * (k + 1))


def ms(d): return int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def fetch_oi(start_ms, end_ms):
    """OI 5min. Ventanas de 200×5min para no exceder limit. Newest-first por ventana."""
    step = 200 * 5 * 60_000  # 1000 min
    rows = {}; cur = start_ms; n = 0
    while cur < end_ms:
        w_end = min(cur + step, end_ms)
        res = get("/v5/market/open-interest", {
            "category": "linear", "symbol": "BTCUSDT", "intervalTime": "5min",
            "startTime": cur, "endTime": w_end, "limit": 200})
        if res:
            for it in res.get("list", []):
                t = int(it["timestamp"]); rows[t] = float(it["openInterest"])
        cur = w_end; n += 1
        if n % 50 == 0: print(f"    OI ventana {n} ({datetime.fromtimestamp(cur/1000, timezone.utc):%Y-%m-%d})", flush=True)
        time.sleep(0.08)
    return pd.DataFrame(sorted(rows.items()), columns=["ts_ms", "open_interest"])


def fetch_funding(start_ms, end_ms):
    step = 200 * 8 * 3_600_000  # 200×8h
    rows = {}; cur = start_ms
    while cur < end_ms:
        w_end = min(cur + step, end_ms)
        res = get("/v5/market/funding/history", {
            "category": "linear", "symbol": "BTCUSDT",
            "startTime": cur, "endTime": w_end, "limit": 200})
        if res:
            for it in res.get("list", []):
                t = int(it["fundingRateTimestamp"]); rows[t] = float(it["fundingRate"])
        cur = w_end; time.sleep(0.08)
    return pd.DataFrame(sorted(rows.items()), columns=["ts_ms", "funding_rate"])


def cov(df, start_ms, end_ms):
    if df.empty: return "VACÍO"
    a = datetime.fromtimestamp(df.ts_ms.min()/1000, timezone.utc).strftime("%Y-%m-%d")
    b = datetime.fromtimestamp(df.ts_ms.max()/1000, timezone.utc).strftime("%Y-%m-%d")
    return f"{len(df):,} pts | {a} → {b}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True); ap.add_argument("--end", required=True)
    args = ap.parse_args()
    PERP.mkdir(parents=True, exist_ok=True)
    s, e = ms(args.start), ms(args.end)
    print(f"Derivados BTCUSDT {args.start} → {args.end} → {PERP}")

    print("  Funding (8h)...", flush=True)
    fund = fetch_funding(s, e)
    fund.to_parquet(PERP / "funding.parquet", index=False)
    print(f"    funding: {cov(fund, s, e)}")

    print("  Open Interest (5min)...", flush=True)
    oi = fetch_oi(s, e)
    oi.to_parquet(PERP / "oi_5m.parquet", index=False)
    print(f"    OI: {cov(oi, s, e)}")
    if not oi.empty:
        retain_days = (e - oi.ts_ms.min()) / 86_400_000
        print(f"    (retención OI 5min observada: ~{retain_days:.0f} días hacia atrás)")
    print("\n[OK] derivados en", PERP)


if __name__ == "__main__":
    main()
