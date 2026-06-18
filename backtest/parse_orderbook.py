"""
parse_orderbook.py
------------------
Lee los archivos .data.zip del order book L2 Bybit SPOT (200 niveles, ~200ms).
Reconstruye el libro incremental (snapshot + deltas) y calcula OBI por barra M1.

Cache incremental: cada ZIP se parsea una sola vez y se guarda en
    processed/ob_cache/YYYY-MM-DD.parquet
Re-runs saltan archivos ya cacheados — no se re-unzipea nada.

Rango de datos: 2025-06-15 → 2026-01-20 (inclusive)

Uso:
    python backtest/parse_orderbook.py              # procesa todo lo pendiente
    python backtest/parse_orderbook.py --date 2025-07-01   # solo ese día
    python backtest/parse_orderbook.py --rebuild    # borra caché y reprocesa todo
"""

import json
import sys
import zipfile
import argparse
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd

OB_DIR    = Path(__file__).parent.parent / "data/bybit-spot/orderbook"
PROC_DIR  = Path(__file__).parent.parent / "data/bybit-spot/processed"
CACHE_DIR = PROC_DIR / "ob_cache"
OUT_FILE  = PROC_DIR / "m1_obi.parquet"

START_MS = int(datetime(2025, 6, 15, tzinfo=timezone.utc).timestamp() * 1000)
END_MS   = int(datetime(2026, 1, 21, tzinfo=timezone.utc).timestamp() * 1000)  # 20 inclusive


class OrderBook:
    __slots__ = ("bids", "asks")

    def __init__(self):
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}

    def apply(self, data: dict, msg_type: str):
        if msg_type == "snapshot":
            self.bids.clear()
            self.asks.clear()
        for p_s, q_s in data.get("b", []):
            p, q = float(p_s), float(q_s)
            if q == 0.0:
                self.bids.pop(p, None)
            else:
                self.bids[p] = q
        for p_s, q_s in data.get("a", []):
            p, q = float(p_s), float(q_s)
            if q == 0.0:
                self.asks.pop(p, None)
            else:
                self.asks[p] = q

    def best_bid(self) -> float:
        return max(self.bids) if self.bids else float("nan")

    def best_ask(self) -> float:
        return min(self.asks) if self.asks else float("nan")

    def obi(self, levels: int) -> float:
        top_b = sorted(self.bids, reverse=True)[:levels]
        top_a = sorted(self.asks)[:levels]
        b = sum(self.bids[p] for p in top_b)
        a = sum(self.asks[p] for p in top_a)
        total = b + a
        return (b - a) / total if total > 0 else 0.0

    def near_liquidity(self, near: int = 5) -> tuple[float, float, float, float]:
        """
        Devuelve valores CONTINUOS de los `near` niveles más cercanos al mid:
          near_ask : volumen total de los `near` asks más baratos
          near_bid : volumen total de los `near` bids más caros
          max_ask  : volumen máximo en un solo nivel ask de los `near`
          max_bid  : volumen máximo en un solo nivel bid de los `near`

        Los thresholds de thin/wall se calculan en compute_spot_features.py
        usando percentiles rolling — no se binariza aquí.
        """
        bb = self.best_bid()
        ba = self.best_ask()
        if not (bb > 0 and ba > 0 and ba > bb):
            return 0.0, 0.0, 0.0, 0.0

        top_b = sorted(self.bids, reverse=True)[:near]
        top_a = sorted(self.asks)[:near]

        if len(top_b) < near or len(top_a) < near:
            return 0.0, 0.0, 0.0, 0.0

        near_ask = sum(self.asks[p] for p in top_a)
        near_bid = sum(self.bids[p] for p in top_b)
        max_ask  = max(self.asks[p] for p in top_a)
        max_bid  = max(self.bids[p] for p in top_b)

        return near_ask, near_bid, max_ask, max_bid


def _file_date_ms(path: Path) -> int:
    try:
        dt = datetime.strptime(path.name[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0


def parse_day(zip_path: Path) -> pd.DataFrame:
    """Parsea un ZIP → DataFrame con barras M1 de OBI. Devuelve vacío si falla."""
    ob = OrderBook()
    snapshots = []

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
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
                    if msg_type not in ("snapshot", "delta"):
                        continue

                    ts_ms = int(msg.get("ts", 0))
                    ob.apply(msg.get("data", {}), msg_type)

                    bb = ob.best_bid()
                    ba = ob.best_ask()
                    if not (bb > 0 and ba > 0 and ba > bb):
                        continue

                    mid = (bb + ba) / 2
                    na, nb, ma, mb = ob.near_liquidity(near=5)
                    snapshots.append({
                        "ts_ms":      ts_ms,
                        "obi5":       ob.obi(5),
                        "obi10":      ob.obi(10),
                        "obi20":      ob.obi(20),
                        "spread_bps": (ba - bb) / mid * 10_000,
                        "mid":        mid,
                        "near5_ask":  na,
                        "near5_bid":  nb,
                        "max_ask5":   ma,
                        "max_bid5":   mb,
                    })
    except (zipfile.BadZipFile, KeyError, IndexError) as e:
        print(f"    WARN {zip_path.name}: {e}", file=sys.stderr)
        return pd.DataFrame()

    if not snapshots:
        return pd.DataFrame()

    df = pd.DataFrame(snapshots)
    df["ts_min"] = (df["ts_ms"] // 60_000) * 60_000

    m1 = df.groupby("ts_min").agg(
        obi5_mean   =("obi5",       "mean"),
        obi10_mean  =("obi10",      "mean"),
        obi20_mean  =("obi20",      "mean"),
        obi10_min   =("obi10",      "min"),
        obi10_max   =("obi10",      "max"),
        spread_mean =("spread_bps", "mean"),
        mid_open    =("mid",        "first"),
        mid_close   =("mid",        "last"),
        n_snapshots =("ts_ms",      "count"),
        # Liquidez inmediata (near-5 niveles): valores continuos
        # Los booleans thin/wall se computan con percentiles rolling en compute_spot_features.py
        near5_ask   =("near5_ask",  "mean"),
        near5_bid   =("near5_bid",  "mean"),
        max_ask5    =("max_ask5",   "mean"),
        max_bid5    =("max_bid5",   "mean"),
    ).reset_index().rename(columns={"ts_min": "ts_ms"})

    return m1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date",    help="Procesar solo YYYY-MM-DD")
    parser.add_argument("--rebuild", action="store_true", help="Borrar caché y reprocesar todo")
    args = parser.parse_args()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if args.rebuild:
        for f in CACHE_DIR.glob("*.parquet"):
            f.unlink()
        print("Caché borrada.")

    if args.date:
        files = sorted(OB_DIR.glob(f"{args.date}_BTCUSDT_ob200.data.zip"))
    else:
        files = sorted(OB_DIR.glob("*_BTCUSDT_ob200.data.zip"))

    files = [f for f in files if START_MS <= _file_date_ms(f) < END_MS]

    if not files:
        sys.exit(f"No hay archivos OB en {OB_DIR} para el rango 2025-06-15 → 2026-01-20.")

    pending   = [f for f in files if not (CACHE_DIR / f"{f.name[:10]}.parquet").exists()]
    cached    = len(files) - len(pending)

    print(f"Días en rango : {len(files)}  |  ya cacheados: {cached}  |  pendientes: {len(pending)}")

    for i, f in enumerate(pending, 1):
        cache_file = CACHE_DIR / f"{f.name[:10]}.parquet"
        print(f"  [{i:3}/{len(pending)}] {f.name[:10]} ...", end=" ", flush=True)
        m1 = parse_day(f)
        if m1.empty:
            print("sin datos")
            continue
        m1.to_parquet(cache_file, index=False, engine="pyarrow")
        print(f"{len(m1):,} barras M1  -> {cache_file.name}")

    # Combinar todos los archivos de cache en el rango
    cache_files = sorted(CACHE_DIR.glob("*.parquet"))
    if not cache_files:
        sys.exit("No hay datos en cache.")

    print(f"\nCombinando {len(cache_files)} archivos de cache...", end=" ", flush=True)
    combined = pd.concat([pd.read_parquet(f) for f in cache_files], ignore_index=True)
    combined = combined.sort_values("ts_ms").drop_duplicates("ts_ms").reset_index(drop=True)

    combined.to_parquet(OUT_FILE, index=False, engine="pyarrow")

    first = pd.Timestamp(combined["ts_ms"].iloc[0],  unit="ms", tz="UTC")
    last  = pd.Timestamp(combined["ts_ms"].iloc[-1], unit="ms", tz="UTC")
    print(f"{len(combined):,} barras M1")
    print(f"Rango   : {first}  ->  {last}")
    print(f"Guardado: {OUT_FILE}  ({OUT_FILE.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
