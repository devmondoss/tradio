#!/usr/bin/env python3
"""
Descarga shadow_signals + signal_outcomes de Supabase, los une y genera
los archivos JSONL que necesita analyze_outcomes.py, luego lo ejecuta.

Uso:
  python supabase_to_analyze.py
  python supabase_to_analyze.py --dump   # solo escribe JSONLs sin correr el analisis
"""

import json
import os
import sys
import subprocess
import tempfile
import urllib.request
from collections import defaultdict
from pathlib import Path

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://ztdhvmcisjjyhbqlgkzm.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

SCRIPT_DIR = Path(__file__).parent


def fetch(table, params="limit=1000&order=timestamp_ms.asc"):
    url = f"{SUPABASE_URL}/rest/v1/{table}?{params}"
    req = urllib.request.Request(url, headers={
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    })
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def resolved_at_ms(iso_str):
    """ISO8601 -> epoch ms (approx)."""
    import datetime
    # '2026-05-19T11:45:00.923946+00:00'
    dt = datetime.datetime.fromisoformat(iso_str.replace("+00:00", "+00:00"))
    return int(dt.timestamp() * 1000)


def build_paper_trades(signals, outcomes):
    """
    Une signal_outcomes con shadow_signals.
    Para señales con TP1_PARTIAL + segundo cierre (TARGET_HIT / STOP_HIT_BE):
      - agrega pnl_net y fees de ambas rows
      - close_reason = razón del segundo cierre
      - mfe/mae del TP1_PARTIAL leg (tiene los valores reales)
    Para señales con un solo outcome: directa.
    """
    sig_by_id = {s["id"]: s for s in signals}

    # Agrupar outcomes por signal_id
    by_signal = defaultdict(list)
    for o in outcomes:
        by_signal[o["signal_id"]].append(o)

    trades = []
    for sig_id, legs in by_signal.items():
        sig = sig_by_id.get(sig_id)
        if sig is None:
            continue  # señal no en DB (intrabar-only)

        # Ordenar por resolved_at
        legs.sort(key=lambda o: o.get("resolved_at", ""))

        entry  = sig.get("entry_price") or 0.0
        stop   = sig.get("stop_price")
        target = sig.get("target_price")
        risk_unit = abs(entry - (stop or entry))

        # Agregar PnL neto y fees de todas las legs
        total_net   = sum(o.get("pnl_net_usd", 0.0) for o in legs)
        total_gross = sum(o.get("pnl_gross_usd", 0.0) for o in legs)
        total_fees  = sum(
            (o.get("fee_entry_usd", 0.0) + o.get("fee_exit_usd", 0.0))
            for o in legs
        )
        total_fund  = sum(o.get("funding_cost_usd", 0.0) for o in legs)

        # close_reason: si hay INVALIDATED usa esa; si hay TARGET_HIT con is_partial=False; sino el último
        final_reason = legs[-1].get("close_reason", "?")
        for o in legs:
            if o.get("close_reason") == "TARGET_HIT" and not o.get("is_partial", False):
                final_reason = "TARGET_HIT"
                break
            if o.get("close_reason") == "INVALIDATED":
                final_reason = "INVALIDATED"
                break

        # MFE/MAE: usar el leg con valores no-cero (típicamente TP1_PARTIAL)
        mfe_r, mae_r = 0.0, 0.0
        for o in legs:
            if (o.get("mfe_r") or 0.0) > 0:
                mfe_r = o["mfe_r"]
                mae_r = o.get("mae_r") or 0.0
                break

        mfe_price = mfe_r * risk_unit
        mae_price = mae_r * risk_unit

        # size: inferir de pnl_gross_usd / (entry - close_price) si posible, o usar fixed
        # Más simple: usar el size de referencia 1% de $300 / risk_unit
        # El monitor usa leverage=10, capital~300, risk=1% => risk_usd=3
        risk_usd = 3.0
        size = risk_usd / risk_unit if risk_unit > 1e-6 else 0.0

        # closed_at_ms desde el último leg
        last_resolved = legs[-1].get("resolved_at", "")
        closed_ms = resolved_at_ms(last_resolved) if last_resolved else 0

        # close_reason normalizado: INVALIDATED -> STOP_HIT para que block10 lo cuente
        # pero guardamos el motivo real aparte
        close_reason_norm = final_reason
        if final_reason == "INVALIDATED":
            close_reason_norm = "STOP_HIT"  # cuenta como pérdida en bloque 10

        trade = {
            "strategy_id":    sig.get("strategy", "unknown"),
            "regime":         sig.get("regime_combined", "Unknown"),
            "score":          sig.get("score"),
            "side":           sig.get("side", "?"),
            "entry_price":    entry,
            "stop_price":     stop,
            "target_price":   target,
            "size":           round(size, 6),
            "close_reason":   close_reason_norm,
            "close_reason_raw": final_reason,
            "net_pnl":        round(total_net, 4),
            "gross_pnl":      round(total_gross, 4),
            "fees_paid":      round(total_fees, 4),
            "funding_paid":   round(total_fund, 4),
            "mfe":            round(mfe_price, 4),
            "mae":            round(mae_price, 4),
            "spread_bps":     sig.get("spread_bps"),
            "intended_entry": entry,  # sin datos reales -> slippage=0
            "closed_at_ms":   closed_ms,
            # microstructure: usar taker_imbalance como proxy de delta
            "delta":          sig.get("taker_imbalance"),
            "obi_l5":         sig.get("obi_l5"),
            "funding_current": sig.get("funding_current"),
        }
        trades.append(trade)

    return trades


def build_strategy_signals(signals):
    return [{"score": s.get("score"), "strategy_id": s.get("strategy")} for s in signals]


def main():
    dump_only = "--dump" in sys.argv

    print("Descargando datos de Supabase...")
    signals  = fetch("shadow_signals")
    outcomes = fetch("signal_outcomes", "limit=1000&order=timestamp_ms.asc")
    print(f"  shadow_signals  : {len(signals)}")
    print(f"  signal_outcomes : {len(outcomes)}")

    trades  = build_paper_trades(signals, outcomes)
    sigs    = build_strategy_signals(signals)

    print(f"\nTrades sintetizados: {len(trades)}")
    for t in trades:
        print(f"  {t['strategy_id']:<35} {t['close_reason']:<14} net={t['net_pnl']:+.2f}  regime={t['regime']}")

    if dump_only:
        out_dir = SCRIPT_DIR / "supabase_export"
        out_dir.mkdir(exist_ok=True)
        (out_dir / "paper_trades.jsonl").write_text(
            "\n".join(json.dumps(t) for t in trades), encoding="utf-8")
        (out_dir / "strategy_signals.jsonl").write_text(
            "\n".join(json.dumps(s) for s in sigs), encoding="utf-8")
        (out_dir / "contradictions.jsonl").write_text("", encoding="utf-8")
        (out_dir / "strategy_outcomes.jsonl").write_text("", encoding="utf-8")
        print(f"\nExportado a: {out_dir}")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "paper_trades.jsonl").write_text(
            "\n".join(json.dumps(t) for t in trades), encoding="utf-8")
        (tmp / "strategy_signals.jsonl").write_text(
            "\n".join(json.dumps(s) for s in sigs), encoding="utf-8")
        (tmp / "contradictions.jsonl").write_text("", encoding="utf-8")
        (tmp / "strategy_outcomes.jsonl").write_text("", encoding="utf-8")

        print("\n" + "=" * 70)
        analyze_script = SCRIPT_DIR / "analyze_outcomes.py"
        subprocess.run([sys.executable, str(analyze_script), str(tmp)], check=False)


if __name__ == "__main__":
    main()
