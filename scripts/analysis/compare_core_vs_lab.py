"""
compare_core_vs_lab.py — Cruza shadow_signals (Core) con lab_signals (Lab).

Detecta:
  - Señales Lab y Core en dirección opuesta dentro de 60s (divergencia)
  - Señales Lab que anticipan correctamente lo que el Core también detectó
  - Win rate Lab cuando Core señala la misma dirección (confirmación)

Uso:
  python scripts/analysis/compare_core_vs_lab.py
  python scripts/analysis/compare_core_vs_lab.py --days 14 --window_ms 60000
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


def get_client():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL y SUPABASE_KEY requeridos")
        sys.exit(1)
    return create_client(url, key)


def compare(days: int = 7, window_ms: int = 60_000):
    client = get_client()
    since_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)

    # Core signals
    core = (
        client.table("shadow_signals")
        .select("id,timestamp_ms,strategy,side,score,entry_price")
        .eq("action", "ShadowSignal")
        .gte("timestamp_ms", since_ms)
        .order("timestamp_ms")
        .execute()
        .data
    )

    # Lab shadow signals only
    lab = (
        client.table("lab_signals")
        .select("id,strategy_id,timestamp_ms,action,confidence,rr")
        .eq("status", "ShadowSignal")
        .gte("timestamp_ms", since_ms)
        .order("timestamp_ms")
        .execute()
        .data
    )

    print(f"\n  Core signals: {len(core)}  |  Lab signals: {len(lab)}")
    if not core or not lab:
        print("  Sin suficientes datos para comparar.")
        return

    # Cross-match within window_ms
    pairs = []
    j_start = 0
    for c in core:
        ct = c["timestamp_ms"]
        for j in range(j_start, len(lab)):
            lt = lab[j]["timestamp_ms"]
            if lt < ct - window_ms:
                j_start = j
                continue
            if lt > ct + window_ms:
                break
            pairs.append((c, lab[j]))

    print(f"  Pares Core↔Lab dentro de {window_ms//1000}s: {len(pairs)}\n")

    # Categorize
    confirmed = []   # same direction
    divergent = []   # opposite direction

    for c, l in pairs:
        c_side = (c.get("side") or "").lower()
        l_side = (l.get("action") or "").lower()
        # action in lab_signals is stored as Side string: "Long"/"Short"
        if c_side == l_side:
            confirmed.append((c, l))
        elif c_side and l_side and c_side != l_side:
            divergent.append((c, l))

    print(f"  Confirmaciones (mismo lado): {len(confirmed)}")
    print(f"  Divergencias (lados opuestos): {len(divergent)}")

    # Divergences by Lab strategy
    div_by_strat: dict[str, int] = defaultdict(int)
    for _, l in divergent:
        div_by_strat[l["strategy_id"]] += 1
    if div_by_strat:
        print("\n  Divergencias por estrategia Lab:")
        for s, n in sorted(div_by_strat.items(), key=lambda x: -x[1]):
            print(f"    {s}: {n}")

    # Confirmed: check if Core trade won
    if confirmed:
        # Get Core outcomes
        core_ids = [c["id"] for c, _ in confirmed]
        outcomes = {}
        chunk = 200
        for i in range(0, len(core_ids), chunk):
            rows = (
                client.table("signal_outcomes")
                .select("signal_id,close_reason,r_multiple")
                .in_("signal_id", core_ids[i:i+chunk])
                .eq("is_partial", False)
                .execute()
                .data
            )
            for r in rows:
                outcomes[r["signal_id"]] = r

        wins = sum(1 for c, _ in confirmed if outcomes.get(c["id"], {}).get("close_reason") == "TARGET_HIT")
        resolved = sum(1 for c, _ in confirmed if c["id"] in outcomes)
        if resolved:
            print(f"\n  Win rate Core cuando Lab confirma: {wins}/{resolved} = {wins/resolved:.0%}")
            avg_r = sum(outcomes[c["id"]]["r_multiple"] for c, _ in confirmed if c["id"] in outcomes) / resolved
            print(f"  R promedio en esos trades: {avg_r:+.2f}R")

    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compara Core vs Lab")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--window_ms", type=int, default=60_000, help="Ventana en ms para match")
    args = parser.parse_args()
    compare(days=args.days, window_ms=args.window_ms)
