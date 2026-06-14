#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
"""
Lista todos los trades RBF cerrados con datos para revisión visual en Binance.

Uso:
    python scripts/rbf_trade_list.py
    python scripts/rbf_trade_list.py --symbol BTCUSDT
    python scripts/rbf_trade_list.py --session London
    python scripts/rbf_trade_list.py --direction Long
    python scripts/rbf_trade_list.py --loser      # solo trades negativos
    python scripts/rbf_trade_list.py --winner     # solo trades positivos

Genera URL de Binance para ir directo al gráfico de 1m en la barra de entrada.
"""

import os
import sys
import urllib.request
import urllib.parse
import json
from datetime import datetime, timezone

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://ztdhvmcisjjyhbqlgkzm.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

# ── helpers ───────────────────────────────────────────────────────────────────

def fetch_all(table, params):
    rows = []
    limit = 1000
    offset = 0
    while True:
        p = dict(params)
        p["limit"] = str(limit)
        p["offset"] = str(offset)
        qs = urllib.parse.urlencode(p)
        url = f"{SUPABASE_URL}/rest/v1/{table}?{qs}"
        req = urllib.request.Request(url, headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
        })
        with urllib.request.urlopen(req) as r:
            chunk = json.loads(r.read())
        rows.extend(chunk)
        if len(chunk) < limit:
            break
        offset += limit
    return rows


def binance_url(symbol: str, ts_ms: int) -> str:
    """
    Abre Binance en chart 1m, centrado en la barra de entrada.
    Binance acepta el param 'time' en formato: YYYY-MM-DDTHH:MM:SS
    """
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    # Restar 15 minutos para ver contexto previo
    from datetime import timedelta
    dt_context = dt - timedelta(minutes=15)
    ts_str = dt_context.strftime("%Y-%m-%dT%H:%M:%S")
    pair = symbol.replace("USDT", "_USDT")
    return f"https://www.binance.com/en/futures/BTCDOM?symbol={symbol}&interval=1m"


def ms_to_str(ms: int) -> str:
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def r_label(r: float) -> str:
    if r >= 1.0:
        return f"WIN  +{r:.2f}R"
    elif r > 0:
        return f"PART +{r:.2f}R"
    else:
        return f"LOSS  {r:.2f}R"


def parse_args():
    args = sys.argv[1:]
    filters = {}
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--symbol":
            filters["symbol"] = args[i+1]; i += 2
        elif a == "--session":
            filters["session"] = args[i+1]; i += 2
        elif a == "--direction":
            filters["direction"] = args[i+1]; i += 2
        elif a == "--loser":
            filters["loser"] = True; i += 1
        elif a == "--winner":
            filters["winner"] = True; i += 1
        else:
            i += 1
    return filters


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    if not SUPABASE_KEY:
        print("ERROR: SUPABASE_KEY no definida")
        sys.exit(1)

    filters = parse_args()

    params = {
        "select": "*",
        "result_r": "not.is.null",
        "order": "timestamp_ms.asc",
    }
    if "symbol" in filters:
        params["symbol"] = f"eq.{filters['symbol']}"

    rows = fetch_all("rbf_signals", params)

    # Aplicar filtros post-fetch
    if "session" in filters:
        rows = [r for r in rows if (r.get("session") or "").lower() == filters["session"].lower()]
    if "direction" in filters:
        rows = [r for r in rows if (r.get("direction") or "").lower() == filters["direction"].lower()]
    if filters.get("loser"):
        rows = [r for r in rows if (r.get("result_r") or 0) < 0]
    if filters.get("winner"):
        rows = [r for r in rows if (r.get("result_r") or 0) > 0]

    if not rows:
        print("Sin trades con esos filtros.")
        return

    # ── Encabezado ──
    filter_desc = " | ".join(f"{k}={v}" for k, v in filters.items()) or "todos"
    print(f"\n{'='*72}")
    print(f"  RBF TRADES -- {filter_desc}  ({len(rows)} trades)")
    print(f"{'='*72}\n")

    total_r = 0.0
    wins = 0

    for i, t in enumerate(rows, 1):
        sym       = t.get("symbol", "?")
        direction = t.get("direction", "?")
        session   = t.get("session", "?")
        score     = t.get("confluence_score", "?")
        entry_p   = t.get("entry_price") or 0
        stop_p    = t.get("stop_price") or 0
        target_p  = t.get("target_price") or 0
        exit_p    = t.get("exit_price") or 0
        result_r  = t.get("result_r") or 0
        reason    = t.get("exit_reason") or t.get("status") or "?"
        entry_ms  = t.get("timestamp_ms") or 0
        closed_at = t.get("closed_at") or ""

        total_r += result_r
        if result_r > 0:
            wins += 1

        entry_str = ms_to_str(entry_ms) if entry_ms else "?"
        exit_str  = closed_at[:16].replace("T", " ") + " UTC" if closed_at else "?"

        if entry_ms:
            dt_entry = datetime.fromtimestamp(entry_ms / 1000, tz=timezone.utc)
            binance_time = dt_entry.strftime("%Y-%m-%dT%H:%M:%S")
        else:
            binance_time = ""

        print(f"-- #{i:02d} -----------------------------------------------------------")
        print(f"  {r_label(result_r)}  {sym} {direction.upper()} | {session} | score={score} | {reason}")
        print(f"  Entrada : {entry_str}  @ {entry_p:.4f}")
        print(f"  Stop    : {stop_p:.4f}   Target: {target_p:.4f}   Exit: {exit_p:.4f}")
        dist_stop   = abs(entry_p - stop_p)
        dist_target = abs(target_p - entry_p)
        rr = dist_target / dist_stop if dist_stop > 1e-10 else 0
        print(f"  Risk    : {dist_stop:.4f}  RR configurado: {rr:.2f}:1")
        print(f"  Salida  : {exit_str}")
        print(f"  Binance : https://www.binance.com/futures/{sym}?interval=1m")
        if binance_time:
            print(f"  Momento : {binance_time} UTC  (busca esta barra en el chart)")
        print()

    # ── Resumen ──
    wr = wins / len(rows) * 100 if rows else 0
    avg_r = total_r / len(rows) if rows else 0
    print(f"{'='*70}")
    print(f"  RESUMEN: n={len(rows)}  WR={wr:.0f}%  Total={total_r:+.2f}R  Avg={avg_r:+.2f}R")
    print(f"{'='*72}\n")


if __name__ == "__main__":
    main()
