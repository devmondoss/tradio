"""
score_decay.py — Robustez de thresholds: varía ±20% los campos del snapshot jsonb
y mide cambio en win rate. Si el resultado cambia drásticamente → threshold sobreajustado.

Uso:
  python scripts/analysis/score_decay.py
  python scripts/analysis/score_decay.py --strategy VwapRejection --field obi_l5
  python scripts/analysis/score_decay.py --strategy LiquidityMagnet --field fast_slope
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# supabase es opcional (solo si ANALYTICS_BACKEND=supabase); pymongo se carga
# desde calibration.core.mongo_db. tabulate sigue siendo necesario.
try:
    from tabulate import tabulate
except ImportError:
    print("ERROR: pip install tabulate (pymongo ya está en calibration/requirements.txt)")
    sys.exit(1)

NUMERIC_FIELDS = [
    "obi_l5", "obi_l10", "cvd_slope", "taker_imbalance",
    "fast_slope", "vwap_distance_atr", "rr", "confidence",
    "oi_delta", "funding_rate", "spread_bps",
]


def get_client():
    """Default backend Mongo; setea ANALYTICS_BACKEND=supabase para forzar Supabase."""
    from pathlib import Path as _P
    sys.path.insert(0, str(_P(__file__).resolve().parent.parent.parent))
    from calibration.core.mongo_db import analytics_client
    return analytics_client()


def analyze_field(rows_with_outcomes, field: str, n_buckets: int = 10):
    """Bucket rows by field value and compute win rate per bucket."""
    buckets: dict[float, list[str]] = {}
    for row, outcome in rows_with_outcomes:
        val = None
        # field can be top-level or in snapshot jsonb
        if field in row:
            val = row[field]
        elif row.get("snapshot") and field in row["snapshot"]:
            val = row["snapshot"][field]
        if val is None:
            continue
        bucket = round(val, 2)
        if bucket not in buckets:
            buckets[bucket] = []
        buckets[bucket].append(outcome)

    if not buckets:
        print(f"  No hay datos para el campo '{field}'")
        return

    # Sort and compute per-bucket win rate
    sorted_buckets = sorted(buckets.items())
    table_rows = []
    for val, outcomes in sorted_buckets:
        total = len(outcomes)
        wins = outcomes.count("TargetHit")
        wr = wins / total if total else 0
        table_rows.append([f"{val:.2f}", total, wins, f"{wr:.0%}"])

    print(f"\n  Field: {field} — {len(rows_with_outcomes)} señales con outcome")
    print(tabulate(table_rows, headers=["value", "n", "wins", "WR"], tablefmt="simple"))

    # Detect if win rate is monotone / correlated
    wrs = [int(r[2]) / int(r[1]) for r in table_rows if int(r[1]) >= 3]
    if len(wrs) >= 3:
        # Simple monotone check
        increasing = all(wrs[i] <= wrs[i+1] for i in range(len(wrs)-1))
        decreasing = all(wrs[i] >= wrs[i+1] for i in range(len(wrs)-1))
        if increasing:
            print(f"  → Win rate CRECIENTE con {field} (relación positiva)")
        elif decreasing:
            print(f"  → Win rate DECRECIENTE con {field} (relación negativa)")
        else:
            rng = max(wrs) - min(wrs)
            if rng > 0.20:
                print(f"  → Rango WR {rng:.0%} — relación no-lineal, cuidado con threshold")
            else:
                print(f"  → WR estable ({rng:.0%} rango) — threshold robusto")


def run(strategy: str | None, field: str | None, days: int = 30):
    client = get_client()
    since_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)

    q = (
        client.table("lab_signals")
        .select("id,strategy_id,status,rr,confidence,snapshot")
        .eq("status", "ShadowSignal")
        .gte("timestamp_ms", since_ms)
    )
    if strategy:
        q = q.eq("strategy_id", strategy)
    signals = q.execute().data

    if not signals:
        print("Sin señales ShadowSignal en el período.")
        return

    # Get outcomes
    sig_ids = [s["id"] for s in signals]
    outcomes: dict[str, str] = {}
    chunk = 200
    for i in range(0, len(sig_ids), chunk):
        rows = (
            client.table("lab_outcomes")
            .select("signal_id,final_status")
            .in_("signal_id", sig_ids[i:i+chunk])
            .execute()
            .data
        )
        for r in rows:
            if r.get("final_status"):
                outcomes[r["signal_id"]] = r["final_status"]

    rows_with_outcomes = [
        (s, outcomes[s["id"]]) for s in signals if s["id"] in outcomes
    ]
    print(f"\n  {strategy or 'ALL'} — {len(signals)} señales, {len(rows_with_outcomes)} con outcome")

    fields_to_check = [field] if field else NUMERIC_FIELDS
    for f in fields_to_check:
        analyze_field(rows_with_outcomes, f)

    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Robustez de thresholds del Lab")
    parser.add_argument("--strategy", type=str, default=None)
    parser.add_argument("--field", type=str, default=None)
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()
    run(strategy=args.strategy, field=args.field, days=args.days)
