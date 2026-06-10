"""
FASE 5: Reporte diario de performance de RBF y AMD.

Genera un resumen de las últimas 24h (o el número de días especificado):
- Señales disparadas (abiertas + cerradas)
- WR y AvgR del día
- P&L paper estimado ($50 capital, 10x apalancamiento)
- Comparación contra baseline histórico
- Tabla de trades cerrados

Uso:
    python scripts/daily_report.py
    python scripts/daily_report.py --days 7

Requiere:
    pip install pandas requests
Env:
    SUPABASE_URL  SUPABASE_KEY
"""
import argparse
import json
import os
import urllib.request
from datetime import datetime, timezone, timedelta

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://ztdhvmcisjjyhbqlgkzm.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
CAPITAL = 50.0
LEVERAGE = 10.0

def fetch(table: str, since_ms: int) -> list[dict]:
    url = (
        f"{SUPABASE_URL}/rest/v1/{table}"
        f"?timestamp_ms=gte.{since_ms}"
        f"&select=*&limit=500&order=timestamp_ms.desc"
    )
    req = urllib.request.Request(
        url,
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

def r_to_pnl(r: float, capital: float = CAPITAL, leverage: float = LEVERAGE) -> float:
    return r * (capital * leverage * 0.01)  # 1% risk per trade at $50 × 10× = $5 risk

def signal_summary(rows: list[dict], name: str) -> dict:
    total = len(rows)
    closed = [r for r in rows if r.get("result_r") is not None]
    open_  = [r for r in rows if r.get("result_r") is None]
    if not closed:
        return {"name": name, "total": total, "closed": 0, "open": len(open_),
                "wr": None, "avg_r": None, "total_r": None, "est_pnl": None}
    results = [float(r["result_r"]) for r in closed]
    wins = [r for r in results if r > 0]
    wr = len(wins) / len(results)
    avg_r = sum(results) / len(results)
    total_r = sum(results)
    est_pnl = sum(r_to_pnl(r) for r in results)
    return {"name": name, "total": total, "closed": len(closed), "open": len(open_),
            "wr": wr, "avg_r": avg_r, "total_r": total_r, "est_pnl": est_pnl,
            "trades": closed}

def print_report(days: int):
    since_dt = datetime.now(timezone.utc) - timedelta(days=days)
    since_ms = int(since_dt.timestamp() * 1000)
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    print(f"\n{'='*60}")
    print(f"  DAILY REPORT — {now_str}")
    print(f"  Ventana: últimas {days * 24}h  |  Capital: ${CAPITAL}  Leverage: {LEVERAGE}×")
    print(f"{'='*60}\n")

    rbf_rows = fetch("rbf_signals", since_ms)
    amd_rows = fetch("amd_signals", since_ms)

    for rows, name in [(rbf_rows, "RBF"), (amd_rows, "AMD")]:
        s = signal_summary(rows, name)
        print(f"┌─ {name} ──────────────────────────────────────────────────")
        print(f"│  Señales:   {s['total']:3d}  (cerradas={s['closed']} open={s['open']})")
        if s["wr"] is not None:
            wr_bar = "█" * int(s["wr"] * 20)
            print(f"│  WR:        {s['wr']:.1%}  {wr_bar}")
            print(f"│  AvgR:     {s['avg_r']:+.3f}R")
            print(f"│  TotalR:   {s['total_r']:+.3f}R")
            print(f"│  Est P&L:  ${s['est_pnl']:+.2f}")
        else:
            print(f"│  Sin trades cerrados en la ventana")
        print("│")

        if s.get("trades"):
            print(f"│  Trades cerrados:")
            for t in sorted(s["trades"], key=lambda x: x["timestamp_ms"])[-10:]:
                ts = datetime.fromtimestamp(t["timestamp_ms"] / 1000, tz=timezone.utc)
                r = float(t["result_r"])
                sym = t.get("symbol", "?")[:7]
                direction = t.get("direction", "?")[:5]
                exit_r = t.get("exit_reason", "?")[:10]
                score = t.get("signal_score_v2")
                score_str = f"sv2={score:.2f}" if score is not None else ""
                icon = "✓" if r > 0 else "✗"
                print(f"│    {icon} {ts.strftime('%H:%M')} {sym} {direction} "
                      f"{r:+.2f}R [{exit_r}] {score_str}")
        print("└" + "─"*58 + "\n")

    # ── Alertas ──────────────────────────────────────────────────────────────
    all_rows = rbf_rows + amd_rows
    all_closed = [r for r in all_rows if r.get("result_r") is not None]
    if len(all_closed) >= 5:
        results = [float(r["result_r"]) for r in all_closed]
        consecutive_losses = 0
        for r in sorted(all_closed, key=lambda x: x["timestamp_ms"], reverse=True):
            if float(r["result_r"]) < 0:
                consecutive_losses += 1
            else:
                break
        if consecutive_losses >= 3:
            print(f"⚠️  ALERTA: {consecutive_losses} pérdidas consecutivas recientes")
        total_r_day = sum(results)
        if total_r_day < -3.0:
            print(f"⚠️  ALERTA: Drawdown del día = {total_r_day:.2f}R (umbral -3R)")

    print(f"\n[generado: {now_str}]")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=1)
    args = parser.parse_args()
    print_report(args.days)
