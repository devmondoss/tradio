#!/usr/bin/env python3
"""
Power Hour Stats — análisis de señales AMD y RBF por hora UTC.

Lee amd_signals y rbf_signals de Supabase y agrupa por hora UTC
para identificar qué horas tienen mejor WR y avg_R.

Fabio Valentini: "Power Hour" = las horas donde el mercado explota.
Para NQ/crypto: London Open (08-09 UTC), Overlap (13-14 UTC), NY Open.

Uso:
    python scripts/power_hour_stats.py
    python scripts/power_hour_stats.py --days 30
    python scripts/power_hour_stats.py --table rbf    # solo RBF
    python scripts/power_hour_stats.py --table amd    # solo AMD

Requiere:
    SUPABASE_URL y SUPABASE_KEY como variables de entorno.
"""

import os
import sys
import json
import urllib.request
import urllib.parse
from collections import defaultdict
from datetime import datetime, timezone

# ── Config ─────────────────────────────────────────────────────────────────────

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

# ── Supabase client ─────────────────────────────────────────────────────────────

def supabase_get(table: str, params: dict) -> list:
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL y SUPABASE_KEY requeridos", file=sys.stderr)
        sys.exit(1)
    qs = urllib.parse.urlencode(params)
    url = f"{SUPABASE_URL}/rest/v1/{table}?{qs}"
    req = urllib.request.Request(url, headers={
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
    })
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

# ── Helpers ─────────────────────────────────────────────────────────────────────

def hour_utc(timestamp_ms: int) -> int:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).hour

SESSION_LABELS = {
    (0, 7):   "Asia",
    (8, 12):  "London",
    (13, 16): "Overlap",
    (17, 22): "NY",
    (23, 23): "Asia-Pre",
}

def session_label(hour: int) -> str:
    for (h_start, h_end), label in SESSION_LABELS.items():
        if h_start <= hour <= h_end:
            return label
    return "Other"

def print_table(title: str, rows: list[dict], total_n: int):
    print(f"\n{'='*62}")
    print(f"  {title}  (n={total_n} señales con outcome)")
    print(f"{'='*62}")
    print(f"  {'Hora':>5}  {'Sesión':<10}  {'n':>4}  {'WR%':>6}  {'avgR':>7}  {'sumR':>7}")
    print(f"  {'-'*55}")
    for r in rows:
        bar = "█" * min(int(r["wr"] / 5), 20)
        marker = "⭐" if r["wr"] >= 60 and r["n"] >= 3 else "  "
        print(
            f"  {r['hour']:>02}:00  {r['session']:<10}  {r['n']:>4}  "
            f"{r['wr']:>5.1f}%  {r['avg_r']:>+7.2f}  {r['sum_r']:>+7.2f}  {marker}"
        )
    print()

# ── AMD analysis ────────────────────────────────────────────────────────────────

def analyze_amd(days: int):
    rows = supabase_get("amd_signals", {
        "select": "timestamp_ms,quality_score,session_cvd",
        "order":  "timestamp_ms.desc",
        "limit":  "2000",
    })
    # AMD no tiene outcome en la DB aún (shadow mode) — solo analizar distribución por hora
    by_hour = defaultdict(list)
    for r in rows:
        h = hour_utc(r["timestamp_ms"])
        by_hour[h].append(r)

    print(f"\n{'='*62}")
    print(f"  AMD Signals — distribución horaria (n={len(rows)}, shadow mode)")
    print(f"{'='*62}")
    print(f"  {'Hora':>5}  {'Sesión':<10}  {'n':>4}  {'avg_score':>9}  {'has_CVD%':>8}")
    print(f"  {'-'*55}")
    all_hours = sorted(by_hour.keys())
    for h in all_hours:
        sigs = by_hour[h]
        scores = [s.get("quality_score") or 0 for s in sigs]
        has_cvd = sum(1 for s in sigs if abs(s.get("session_cvd") or 0) > 500)
        avg_score = sum(scores) / len(scores) if scores else 0
        cvd_pct   = has_cvd / len(sigs) * 100 if sigs else 0
        marker = "⭐" if avg_score >= 6.0 and len(sigs) >= 3 else "  "
        print(
            f"  {h:>02}:00  {session_label(h):<10}  {len(sigs):>4}  "
            f"{avg_score:>9.2f}  {cvd_pct:>7.1f}%  {marker}"
        )

# ── RBF analysis ────────────────────────────────────────────────────────────────

def analyze_rbf(days: int):
    rows = supabase_get("rbf_signals", {
        "select":  "timestamp_ms,direction,session,confluence_score,confluence_flags,"
                   "veto_reason,outcome_r,exit_reason",
        "order":   "timestamp_ms.desc",
        "limit":   "2000",
    })

    # Solo señales con outcome real (excluir vetadas y sin cerrar)
    closed = [r for r in rows if r.get("outcome_r") is not None and not r.get("veto_reason")]
    all_sigs = [r for r in rows if not r.get("veto_reason")]

    by_hour_closed: dict[int, list] = defaultdict(list)
    for r in closed:
        h = hour_utc(r["timestamp_ms"])
        by_hour_closed[h].append(r)

    by_hour_all: dict[int, list] = defaultdict(list)
    for r in all_sigs:
        h = hour_utc(r["timestamp_ms"])
        by_hour_all[h].append(r)

    table_rows = []
    for h in sorted(by_hour_closed.keys()):
        sigs = by_hour_closed[h]
        n    = len(sigs)
        wins = sum(1 for s in sigs if (s.get("outcome_r") or 0) > 0)
        wr   = wins / n * 100 if n else 0
        rs   = [(s.get("outcome_r") or 0) for s in sigs]
        avg_r = sum(rs) / n if n else 0
        sum_r = sum(rs)
        table_rows.append({
            "hour": h, "session": session_label(h),
            "n": n, "wr": wr, "avg_r": avg_r, "sum_r": sum_r,
        })

    table_rows.sort(key=lambda r: (-r["wr"], -r["n"]))
    print_table("RBF por Hora UTC — señales con outcome", table_rows, len(closed))

    # Tabla por dirección × hora
    print(f"\n  {'Hora':>5}  {'Long WR%':>8}  {'Long n':>6}  {'Short WR%':>9}  {'Short n':>7}")
    print(f"  {'-'*48}")
    all_hours = sorted(set(list(by_hour_closed.keys())))
    for h in all_hours:
        sigs = by_hour_closed[h]
        longs  = [s for s in sigs if s.get("direction") == "Long"]
        shorts = [s for s in sigs if s.get("direction") == "Short"]
        def wr_str(grp):
            if not grp: return "  —  "
            w = sum(1 for s in grp if (s.get("outcome_r") or 0) > 0)
            return f"{w/len(grp)*100:.1f}%"
        print(f"  {h:>02}:00  {wr_str(longs):>8}  {len(longs):>6}  "
              f"{wr_str(shorts):>9}  {len(shorts):>7}")

    # Por score de confluencia
    print(f"\n  {'Score':>6}  {'n':>4}  {'WR%':>6}  {'avgR':>7}")
    print(f"  {'-'*30}")
    by_score: dict[int, list] = defaultdict(list)
    for r in closed:
        s = r.get("confluence_score") or 0
        by_score[s].append(r)
    for sc in sorted(by_score.keys()):
        grp = by_score[sc]
        wins = sum(1 for s in grp if (s.get("outcome_r") or 0) > 0)
        wr   = wins / len(grp) * 100 if grp else 0
        avgr = sum(s.get("outcome_r") or 0 for s in grp) / len(grp) if grp else 0
        print(f"  {sc:>6}  {len(grp):>4}  {wr:>5.1f}%  {avgr:>+7.2f}")

    # Top flags
    print(f"\n  Top confluence flags en señales ganadas vs perdidas:")
    flag_wins:   dict[str, int] = defaultdict(int)
    flag_losses: dict[str, int] = defaultdict(int)
    for r in closed:
        flags = r.get("confluence_flags") or []
        if isinstance(flags, str):
            try: flags = json.loads(flags)
            except Exception: flags = []
        is_win = (r.get("outcome_r") or 0) > 0
        for f in flags:
            if is_win: flag_wins[f]   += 1
            else:      flag_losses[f] += 1
    all_flags = set(flag_wins) | set(flag_losses)
    print(f"  {'Flag':<20}  {'Wins':>5}  {'Loss':>5}  {'Win%':>6}")
    print(f"  {'-'*42}")
    for f in sorted(all_flags):
        w = flag_wins.get(f, 0)
        l = flag_losses.get(f, 0)
        tot = w + l
        pct = w / tot * 100 if tot else 0
        print(f"  {f:<20}  {w:>5}  {l:>5}  {pct:>5.1f}%")

# ── Main ────────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Power Hour Stats — AMD y RBF")
    parser.add_argument("--days",  type=int, default=30, help="Días a analizar (default: 30)")
    parser.add_argument("--table", choices=["amd", "rbf", "all"], default="all")
    args = parser.parse_args()

    if args.table in ("rbf", "all"):
        analyze_rbf(args.days)
    if args.table in ("amd", "all"):
        analyze_amd(args.days)

if __name__ == "__main__":
    main()
