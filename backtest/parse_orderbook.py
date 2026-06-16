"""
parse_orderbook.py
------------------
Lee los archivos .data.zip del order book L2 Bybit SPOT (200 niveles, ~200ms).
Reconstruye el libro incremental (snapshot + deltas) y calcula OBI por barra M1.

Formato de entrada (JSON Lines dentro del ZIP):
  Primera línea: {"type":"snapshot","ts":...,"data":{"b":[[price,qty],...], "a":[...]}}
  Resto:         {"type":"delta","ts":...,  "data":{"b":[[price,qty],...], "a":[...]}}
  qty = "0" -> eliminar ese nivel del libro

Features por barra M1:
  obi_5   : OBI top 5 niveles  = (bid_qty5 - ask_qty5) / (bid_qty5 + ask_qty5)
  obi_10  : OBI top 10 niveles
  obi_20  : OBI top 20 niveles
  obi_mean: media de OBI_10 durante el minuto (promedio de snapshots ~200ms)
  obi_min / obi_max : rango del OBI_10 dentro del minuto
  spread_bps: spread relativo en puntos básicos = (best_ask - best_bid) / mid * 10000
  mid_open / mid_close: precio mid al inicio y fin del minuto

Uso:
    python backtest/parse_orderbook.py [--date 2025-06-15]

    Sin argumentos: procesa todos los días disponibles.
    Con --date: solo ese día (útil para debug).

Salida:
    data/bybit-spot/processed/m1_obi.parquet
"""

import io
import json
import os
import sys
import zipfile
import argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

OB_DIR   = Path(__file__).parent.parent / "data/bybit-spot/orderbook"
OUT_DIR  = Path(__file__).parent.parent / "data/bybit-spot/processed"
OUT_FILE = OUT_DIR / "m1_obi.parquet"

START_MS = int(datetime(2025, 6, 15, tzinfo=timezone.utc).timestamp() * 1000)
END_MS   = int(datetime(2026, 6, 16, tzinfo=timezone.utc).timestamp() * 1000)


# ── Order book state ──────────────────────────────────────────────────────────

class OrderBook:
    """Mantiene bids y asks como dicts {price_float: qty_float}."""

    __slots__ = ("bids", "asks")

    def __init__(self):
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}

    def apply(self, data: dict, msg_type: str):
        if msg_type == "snapshot":
            self.bids.clear()
            self.asks.clear()

        for price_s, qty_s in data.get("b", []):
            p = float(price_s)
            q = float(qty_s)
            if q == 0.0:
                self.bids.pop(p, None)
            else:
                self.bids[p] = q

        for price_s, qty_s in data.get("a", []):
            p = float(price_s)
            q = float(qty_s)
            if q == 0.0:
                self.asks.pop(p, None)
            else:
                self.asks[p] = q

    def best_bid(self) -> float:
        return max(self.bids) if self.bids else float("nan")

    def best_ask(self) -> float:
        return min(self.asks) if self.asks else float("nan")

    def obi(self, levels: int) -> float:
        """OBI = (sum_bid_qty - sum_ask_qty) / (sum_bid_qty + sum_ask_qty) para top N niveles."""
        top_bids = sorted(self.bids, reverse=True)[:levels]
        top_asks = sorted(self.asks)[:levels]
        b = sum(self.bids[p] for p in top_bids)
        a = sum(self.asks[p] for p in top_asks)
        total = b + a
        return (b - a) / total if total > 0 else 0.0


# ── Per-day parser ────────────────────────────────────────────────────────────

def parse_day(zip_path: Path) -> list[dict]:
    """
    Procesa un archivo .data.zip y devuelve una lista de dicts con snapshots
    de OBI cada ~200ms.  Cada dict: {ts_ms, obi5, obi10, obi20, spread_bps, mid}.
    """
    ob = OrderBook()
    snapshots = []

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            # El zip contiene un solo archivo .data (JSON Lines)
            inner = zf.namelist()[0]
            with zf.open(inner) as raw:
                for line_bytes in raw:
                    line = line_bytes.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    msg_type = msg.get("type")
                    ts_ms    = int(msg.get("ts", 0))
                    data     = msg.get("data", {})

                    if msg_type not in ("snapshot", "delta"):
                        continue

                    ob.apply(data, msg_type)

                    bb = ob.best_bid()
                    ba = ob.best_ask()

                    if not (bb > 0 and ba > 0 and ba > bb):
                        continue

                    mid    = (bb + ba) / 2
                    spread = (ba - bb) / mid * 10_000

                    snapshots.append({
                        "ts_ms":      ts_ms,
                        "obi5":       ob.obi(5),
                        "obi10":      ob.obi(10),
                        "obi20":      ob.obi(20),
                        "spread_bps": spread,
                        "mid":        mid,
                    })
    except (zipfile.BadZipFile, KeyError, IndexError) as e:
        print(f"    WARN {zip_path.name}: {e}", file=sys.stderr)

    return snapshots


def aggregate_to_m1(snapshots: list[dict]) -> pd.DataFrame:
    """Agrupa snapshots ~200ms en barras M1."""
    if not snapshots:
        return pd.DataFrame()

    df = pd.DataFrame(snapshots)
    df["ts_min"] = (df["ts_ms"] // 60_000) * 60_000

    out = df.groupby("ts_min").agg(
        obi5_mean   =("obi5",       "mean"),
        obi10_mean  =("obi10",      "mean"),
        obi20_mean  =("obi20",      "mean"),
        obi10_min   =("obi10",      "min"),
        obi10_max   =("obi10",      "max"),
        spread_mean =("spread_bps", "mean"),
        mid_open    =("mid",        "first"),
        mid_close   =("mid",        "last"),
        n_snapshots =("ts_ms",      "count"),
    ).reset_index().rename(columns={"ts_min": "ts_ms"})

    return out


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Procesar solo este día (YYYY-MM-DD)")
    parser.add_argument("--force", action="store_true", help="Recalcular aunque ya exista el parquet")
    args = parser.parse_args()

    if args.date:
        files = sorted(OB_DIR.glob(f"{args.date}_BTCUSDT_ob200.data.zip"))
    else:
        files = sorted(OB_DIR.glob("*_BTCUSDT_ob200.data.zip"))

    files = [f for f in files if START_MS <= _file_start_ms(f) < END_MS]

    if not files:
        sys.exit(f"No se encontraron archivos OB en {OB_DIR} para el rango pedido.")

    print(f"Días a procesar: {len(files)}")

    all_m1 = []
    for i, f in enumerate(files, 1):
        print(f"  [{i:3}/{len(files)}] {f.name} ...", end=" ", flush=True)
        snaps = parse_day(f)
        m1    = aggregate_to_m1(snaps)
        if not m1.empty:
            all_m1.append(m1)
        print(f"{len(snaps):,} snaps -> {len(m1):,} barras M1")

    if not all_m1:
        sys.exit("No se generaron datos.")

    combined = pd.concat(all_m1, ignore_index=True)
    combined = combined.sort_values("ts_ms").drop_duplicates("ts_ms")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(OUT_FILE, index=False, engine="pyarrow")

    print(f"\nGuardado: {OUT_FILE}")
    print(f"Total barras M1 OBI : {len(combined):,}")
    first_ts = pd.Timestamp(combined["ts_ms"].iloc[0],  unit="ms", tz="UTC")
    last_ts  = pd.Timestamp(combined["ts_ms"].iloc[-1], unit="ms", tz="UTC")
    print(f"Rango               : {first_ts}  ->  {last_ts}")
    print(f"Tamaño parquet      : {OUT_FILE.stat().st_size / 1e6:.1f} MB")


def _file_start_ms(path: Path) -> int:
    """Extrae el timestamp en ms desde el nombre del archivo: YYYY-MM-DD_..."""
    try:
        date_str = path.name[:10]
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0


if __name__ == "__main__":
    main()
