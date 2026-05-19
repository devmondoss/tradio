"""
session_filter_analysis.py — Segmenta señales Lab por sesión de trading.

Compara win rate Asia vs London vs NY para validar que el SessionGate
elimina señales malas de Asia.

Uso:
  python scripts/analysis/session_filter_analysis.py
  python scripts/analysis/session_filter_analysis.py --days 14
  python scripts/analysis/session_filter_analysis.py --strategy VwapRejection
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from collections import defaultdict

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from supabase import create_client
    from tabulate import tabulate
except ImportError:
    print("ERROR: pip install supabase python-dotenv tabulate")
    sys.exit(1)

SESSION_ORDER = ["Asia", "London", "NY", "LateNY", "Unknown"]


def classify_session(timestamp_ms: int) -> str:
    """Classify UTC timestamp into trading session."""
    dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    hour = dt.hour
    if 0 <= hour < 8:
        return "Asia"
    elif 8 <= hour < 13:
        return "London"
    elif 13 <= hour < 20:
        return "NY"
    elif 20 <= hour < 24:
        return "LateNY"
    return "Unknown"


def get_client():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL y SUPABASE_KEY requeridos")
        sys.exit(1)
    return create_client(url, key)


def analyze(days: int = 7, strategy_filter: str | None = None):
    client = get_client()
    since_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)

    q = (
        client.table("lab_signals")
        .select("id,strategy_id,status,timestamp_ms,snapshot,confidence,rr,side")
        .eq("status", "ShadowSignal")
        .gte("timestamp_ms", since_ms)
    )
    if strategy_filter:
        q = q.eq("strategy_id", strategy_filter)
    signals = q.execute().data

    if not signals:
        print(f"Sin señales ShadowSignal en los últimos {days} días.")
        return

    # Get outcomes
    sig_ids = [s["id"] for s in signals]
    outcomes: dict[str, dict] = {}
    chunk = 200
    for i in range(0, len(sig_ids), chunk):
        rows = (
            client.table("lab_outcomes")
            .select("signal_id,final_status,mfe,mae,outcome_5m,outcome_15m")
            .in_("signal_id", sig_ids[i:i+chunk])
            .execute()
            .data
        )
        for r in rows:
            outcomes[r["signal_id"]] = r

    # Group by session × strategy
    stats: dict[tuple[str, str], dict] = defaultdict(lambda: {
        "total": 0, "wins": 0, "stops": 0, "ttl": 0, "open": 0,
        "r5m": [], "r15m": [], "mfe": [], "mae": [], "conf": [],
    })

    for s in signals:
        # Session: prefer snapshot field, fall back to timestamp classification
        snap = s.get("snapshot") or {}
        session = snap.get("session") or classify_session(s["timestamp_ms"])
        strat = s["strategy_id"]
        key = (session, strat)
        st = stats[key]
        st["total"] += 1
        if s.get("confidence") is not None:
            st["conf"].append(s["confidence"])

        o = outcomes.get(s["id"])
        if not o:
            st["open"] += 1
            continue

        fs = o.get("final_status") or "StillOpen"
        if fs == "TargetHit":
            st["wins"] += 1
        elif fs == "StopHit":
            st["stops"] += 1
        elif fs and fs.startswith("TtlExpired"):
            st["ttl"] += 1
        else:
            st["open"] += 1

        if o.get("outcome_5m") and o["outcome_5m"].get("r_achieved") is not None:
            st["r5m"].append(o["outcome_5m"]["r_achieved"])
        if o.get("outcome_15m") and o["outcome_15m"].get("r_achieved") is not None:
            st["r15m"].append(o["outcome_15m"]["r_achieved"])
        if o.get("mfe") is not None:
            st["mfe"].append(o["mfe"])
        if o.get("mae") is not None:
            st["mae"].append(o["mae"])

    print(f"\n{'='*80}")
    print(f"  SESSION FILTER ANALYSIS — últimos {days} días")
    if strategy_filter:
        print(f"  Estrategia: {strategy_filter}")
    print(f"{'='*80}\n")

    # --- Summary by session (all strategies) ---
    session_totals: dict[str, dict] = defaultdict(lambda: {
        "total": 0, "wins": 0, "closed": 0, "r5m": [], "r15m": [],
    })
    for (session, strat), st in stats.items():
        ss = session_totals[session]
        ss["total"] += st["total"]
        ss["wins"] += st["wins"]
        ss["closed"] += st["wins"] + st["stops"] + st["ttl"]
        ss["r5m"].extend(st["r5m"])
        ss["r15m"].extend(st["r15m"])

    session_rows = []
    for sess in SESSION_ORDER:
        if sess not in session_totals:
            continue
        ss = session_totals[sess]
        wr = ss["wins"] / ss["closed"] if ss["closed"] else None
        avg_r5m = sum(ss["r5m"]) / len(ss["r5m"]) if ss["r5m"] else None
        avg_r15m = sum(ss["r15m"]) / len(ss["r15m"]) if ss["r15m"] else None
        session_rows.append([
            sess,
            ss["total"],
            ss["closed"],
            f"{wr:.0%}" if wr is not None else "—",
            f"{avg_r5m:+.2f}" if avg_r5m is not None else "—",
            f"{avg_r15m:+.2f}" if avg_r15m is not None else "—",
        ])

    print("  Por sesión (todas las estrategias):")
    print(tabulate(
        session_rows,
        headers=["Session", "Total", "Closed", "WR", "R@5m", "R@15m"],
        tablefmt="rounded_outline",
    ))

    # SessionGate validation
    asia = session_totals.get("Asia", {})
    london = session_totals.get("London", {})
    ny = session_totals.get("NY", {})

    asia_wr = asia["wins"] / asia["closed"] if asia.get("closed") else None
    london_wr = london["wins"] / london["closed"] if london.get("closed") else None
    ny_wr = ny["wins"] / ny["closed"] if ny.get("closed") else None

    best_wr = max(w for w in [london_wr, ny_wr] if w is not None) if (london_wr or ny_wr) else None
    if asia_wr is not None and best_wr is not None:
        gap = best_wr - asia_wr
        if gap > 0.10:
            print(f"\n  ⚠  Asia WR {asia_wr:.0%} vs mejor sesión {best_wr:.0%} (gap {gap:.0%})")
            print(f"     → SessionGate RECOMENDADO para filtrar Asia")
        elif gap > 0.05:
            print(f"\n  ~  Asia WR {asia_wr:.0%} vs mejor sesión {best_wr:.0%} (gap {gap:.0%})")
            print(f"     → Asia marginalmente peor — monitorear")
        else:
            print(f"\n  ✓  Asia WR {asia_wr:.0%} comparable con otras sesiones ({best_wr:.0%})")
            print(f"     → SessionGate no necesario por ahora")

    # --- Detail by session × strategy ---
    print(f"\n  Por sesión × estrategia:\n")
    detail_rows = []
    for sess in SESSION_ORDER:
        for (session, strat), st in sorted(stats.items(), key=lambda x: x[0][1]):
            if session != sess:
                continue
            n_closed = st["wins"] + st["stops"] + st["ttl"]
            wr = st["wins"] / n_closed if n_closed else None
            avg_conf = sum(st["conf"]) / len(st["conf"]) if st["conf"] else None
            avg_r5m = sum(st["r5m"]) / len(st["r5m"]) if st["r5m"] else None
            detail_rows.append([
                sess,
                strat,
                st["total"],
                n_closed,
                f"{wr:.0%}" if wr is not None else "—",
                f"{avg_conf:.2f}" if avg_conf is not None else "—",
                f"{avg_r5m:+.2f}" if avg_r5m is not None else "—",
            ])

    if detail_rows:
        print(tabulate(
            detail_rows,
            headers=["Session", "Strategy", "Total", "Closed", "WR", "Conf", "R@5m"],
            tablefmt="simple",
        ))

    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Análisis por sesión del Strategy Lab")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--strategy", type=str, default=None)
    args = parser.parse_args()
    analyze(days=args.days, strategy_filter=args.strategy)
