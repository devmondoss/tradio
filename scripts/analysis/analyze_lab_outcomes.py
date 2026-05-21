"""
analyze_lab_outcomes.py — Reporte de performance del Strategy Lab por estrategia.

Requiere:
  pip install supabase python-dotenv tabulate

Variables de entorno (o .env en la raíz del repo):
  SUPABASE_URL   — https://[PROJECT].supabase.co
  SUPABASE_KEY   — service_role key

Uso:
  python scripts/analysis/analyze_lab_outcomes.py
  python scripts/analysis/analyze_lab_outcomes.py --days 14
  python scripts/analysis/analyze_lab_outcomes.py --strategy VwapRejection
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


def get_client():
    """Default backend Mongo; setea ANALYTICS_BACKEND=supabase para forzar Supabase."""
    from pathlib import Path as _P
    sys.path.insert(0, str(_P(__file__).resolve().parent.parent.parent))
    from calibration.core.mongo_db import analytics_client
    return analytics_client()


def analyze(days: int = 7, strategy_filter: str | None = None):
    client = get_client()
    since_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)

    # --- Señales por estrategia y estado ---
    q = (
        client.table("lab_signals")
        .select("strategy_id,status,block_reason,missing_data,confidence,rr,side")
        .gte("timestamp_ms", since_ms)
    )
    if strategy_filter:
        q = q.eq("strategy_id", strategy_filter)
    signals = q.execute().data

    if not signals:
        print(f"No hay señales en los últimos {days} días.")
        return

    # Agregar por estrategia
    from collections import defaultdict
    stats: dict[str, dict] = defaultdict(lambda: {
        "total": 0, "shadow": 0, "observed": 0, "asleep": 0,
        "blocked": 0, "block_reasons": defaultdict(int),
        "missing_fields": defaultdict(int), "rr_vals": [], "conf_vals": [],
    })

    for s in signals:
        sid = s["strategy_id"]
        st = stats[sid]
        st["total"] += 1
        status = s["status"]
        if status == "ShadowSignal":
            st["shadow"] += 1
            if s.get("rr"):
                st["rr_vals"].append(s["rr"])
        elif status == "Observed":
            st["observed"] += 1
        elif status == "Asleep":
            st["asleep"] += 1
            for field in (s.get("missing_data") or []):
                st["missing_fields"][field] += 1
        elif status == "Blocked":
            st["blocked"] += 1
            br = s.get("block_reason") or "unknown"
            st["block_reasons"][br] += 1
        if s.get("confidence") is not None:
            st["conf_vals"].append(s["confidence"])

    # --- Outcomes ---
    signal_ids = [s["id"] for s in signals if s.get("id")]
    outcomes_by_signal: dict[str, dict] = {}
    if signal_ids:
        chunk = 200
        for i in range(0, len(signal_ids), chunk):
            batch_ids = signal_ids[i:i+chunk]
            rows = (
                client.table("lab_outcomes")
                .select("signal_id,final_status,mfe,mae,outcome_5m,outcome_15m,outcome_ttl")
                .in_("signal_id", batch_ids)
                .execute()
                .data
            )
            for r in rows:
                outcomes_by_signal[r["signal_id"]] = r

    # Merge outcomes into stats
    signal_map = {s["id"]: s for s in signals if s.get("id")}
    outcome_stats: dict[str, dict] = defaultdict(lambda: {
        "target_hit": 0, "stop_hit": 0, "ttl": 0, "open": 0,
        "r5m": [], "r15m": [], "mfe": [], "mae": [],
    })
    for oid, o in outcomes_by_signal.items():
        sig = signal_map.get(oid)
        if not sig:
            continue
        sid = sig["strategy_id"]
        os_ = outcome_stats[sid]
        fs = o.get("final_status") or "StillOpen"
        if fs == "TargetHit":
            os_["target_hit"] += 1
        elif fs == "StopHit":
            os_["stop_hit"] += 1
        elif fs and fs.startswith("TtlExpired"):
            os_["ttl"] += 1
        else:
            os_["open"] += 1
        if o.get("outcome_5m") and o["outcome_5m"].get("r_achieved") is not None:
            os_["r5m"].append(o["outcome_5m"]["r_achieved"])
        if o.get("outcome_15m") and o["outcome_15m"].get("r_achieved") is not None:
            os_["r15m"].append(o["outcome_15m"]["r_achieved"])
        if o.get("mfe") is not None:
            os_["mfe"].append(o["mfe"])
        if o.get("mae") is not None:
            os_["mae"].append(o["mae"])

    # --- Imprimir reporte ---
    print(f"\n{'='*70}")
    print(f"  STRATEGY LAB — últimos {days} días — {len(signals)} señales totales")
    print(f"{'='*70}\n")

    rows = []
    for sid, st in sorted(stats.items()):
        os_ = outcome_stats[sid]
        n_closed = os_["target_hit"] + os_["stop_hit"] + os_["ttl"]
        wr = os_["target_hit"] / n_closed if n_closed else None
        avg_r5m = sum(os_["r5m"]) / len(os_["r5m"]) if os_["r5m"] else None
        avg_r15m = sum(os_["r15m"]) / len(os_["r15m"]) if os_["r15m"] else None
        avg_mfe = sum(os_["mfe"]) / len(os_["mfe"]) if os_["mfe"] else None
        avg_mae = sum(os_["mae"]) / len(os_["mae"]) if os_["mae"] else None
        avg_rr = sum(st["rr_vals"]) / len(st["rr_vals"]) if st["rr_vals"] else None
        rows.append([
            sid,
            st["total"],
            st["shadow"],
            st["observed"],
            st["asleep"],
            st["blocked"],
            f"{wr:.0%}" if wr is not None else "—",
            f"{avg_r5m:+.2f}" if avg_r5m is not None else "—",
            f"{avg_r15m:+.2f}" if avg_r15m is not None else "—",
            f"{avg_mfe:.2f}" if avg_mfe is not None else "—",
            f"{avg_mae:.2f}" if avg_mae is not None else "—",
            f"{avg_rr:.1f}" if avg_rr is not None else "—",
        ])

    headers = ["Strategy", "Total", "Signal", "Obs", "Asleep", "Block",
               "WR", "R@5m", "R@15m", "MFE", "MAE", "RR"]
    print(tabulate(rows, headers=headers, tablefmt="rounded_outline"))

    # Detalle de bloqueos y missing_data
    for sid, st in sorted(stats.items()):
        if st["blocked"] and st["block_reasons"]:
            print(f"\n  {sid} bloqueos: {dict(st['block_reasons'])}")
        if st["asleep"] and st["missing_fields"]:
            top = sorted(st["missing_fields"].items(), key=lambda x: -x[1])[:5]
            print(f"  {sid} missing_data: {top}")

    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Análisis del Strategy Lab")
    parser.add_argument("--days", type=int, default=7, help="Ventana de análisis en días")
    parser.add_argument("--strategy", type=str, default=None, help="Filtrar por strategy_id")
    args = parser.parse_args()
    analyze(days=args.days, strategy_filter=args.strategy)
