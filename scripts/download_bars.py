#!/usr/bin/env python3
"""
Descarga todas las tablas *_bars de Supabase y las guarda como CSV en dataset/.

Tablas: btc_bars, eth_bars, bnb_bars, sol_bars

Uso:
    python scripts/download_bars.py
    python scripts/download_bars.py --symbols BTC ETH   # solo esos
    python scripts/download_bars.py --days 30            # últimos N días
"""

import csv
import json
import os
import sys
import argparse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Cargar .env si existe
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://ztdhvmcisjjyhbqlgkzm.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

DATASET_DIR = Path(__file__).parent.parent / "dataset"

SYMBOLS = ["BTC", "ETH", "BNB", "SOL"]


def table_for(symbol: str) -> str:
    return f"{symbol.lower()}usdt_bars" if symbol.upper() != "BTC" else "btc_bars"


def table_for_symbol(symbol: str) -> str:
    return f"{symbol.lower()}_bars"


def fetch_all(table: str, extra_params: str = "") -> list:
    rows = []
    limit = 1000
    offset = 0
    while True:
        params = f"order=ts_ms.asc&limit={limit}&offset={offset}"
        if extra_params:
            params = f"{extra_params}&{params}"
        url = f"{SUPABASE_URL}/rest/v1/{table}?{params}"
        req = urllib.request.Request(url, headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req) as resp:
                page = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            print(f"  ERROR HTTP {e.code}: {body[:200]}")
            return rows
        rows.extend(page)
        print(f"  [{table}] offset={offset} -> +{len(page)} filas (total: {len(rows)})")
        if len(page) < limit:
            break
        offset += limit
    return rows


def rows_to_csv(rows: list, path: Path):
    if not rows:
        print(f"  Sin datos — no se escribe {path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)
    size_kb = path.stat().st_size / 1024
    print(f"  Guardado: {path}  ({len(rows)} filas, {size_kb:.1f} KB)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS,
                        help="Símbolos a descargar, ej: BTC ETH")
    parser.add_argument("--days", type=int, default=None,
                        help="Últimos N días solamente")
    args = parser.parse_args()

    DATASET_DIR.mkdir(exist_ok=True)

    extra = ""
    if args.days:
        since_ms = int((datetime.now(timezone.utc) - timedelta(days=args.days)).timestamp() * 1000)
        extra = f"ts_ms=gte.{since_ms}"
        print(f"Filtrando: últimos {args.days} días (desde ts_ms={since_ms})\n")

    for sym in args.symbols:
        table = table_for_symbol(sym)
        print(f"\n=== {table} ===")
        rows = fetch_all(table, extra)
        if rows:
            # Añadir columna datetime legible
            for r in rows:
                ts = r.get("ts_ms")
                if ts:
                    r["datetime_utc"] = datetime.fromtimestamp(
                        ts / 1000, tz=timezone.utc
                    ).strftime("%Y-%m-%d %H:%M:%S")
        suffix = f"_last{args.days}d" if args.days else "_full"
        out_path = DATASET_DIR / f"{table}{suffix}.csv"
        rows_to_csv(rows, out_path)

    print(f"\nDataset guardado en: {DATASET_DIR.resolve()}")


if __name__ == "__main__":
    main()
