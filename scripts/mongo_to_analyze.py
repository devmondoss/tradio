#!/usr/bin/env python3
"""
Espejo Mongo de `supabase_to_analyze.py`.

Lee `shadow_signals` + `signal_outcomes` de la base **local** Mongo (lo que
escribe la UI), sintetiza los JSONL que espera `analyze_outcomes.py` y lo
ejecuta.

Uso:
    python scripts/mongo_to_analyze.py
    python scripts/mongo_to_analyze.py --dump     # solo escribir JSONLs
    python scripts/mongo_to_analyze.py --limit 5000

Env vars:
    MONGODB_URI    default mongodb://localhost:27017
    MONGODB_DB     default flowsurface
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

# Hacer importable `calibration.core.mongo_db` desde scripts/.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from calibration.core.mongo_db import get_db

SCRIPT_DIR = Path(__file__).parent


def fetch_signals(limit: int):
    """Devuelve documentos de `shadow_signals` ordenados por timestamp asc.
    Se mapea `_id` (ObjectId) a string para join con outcomes."""
    db = get_db()
    cur = db["shadow_signals"].find({}).sort("timestamp_ms", 1).limit(limit)
    out = []
    for d in cur:
        d["id"] = str(d.pop("_id"))
        out.append(d)
    return out


def fetch_outcomes(limit: int):
    """Devuelve documentos de `signal_outcomes`. `signal_id` (ObjectId) → str."""
    db = get_db()
    cur = db["signal_outcomes"].find({}).sort("timestamp_ms", 1).limit(limit)
    out = []
    for d in cur:
        sid = d.get("signal_id")
        d["signal_id"] = str(sid) if sid is not None else None
        d.pop("_id", None)
        out.append(d)
    return out


def build_paper_trades(signals, outcomes):
    """Idéntico al de `supabase_to_analyze.build_paper_trades`, adaptado a
    nuestros campos en Mongo (no hay `resolved_at` ISO; `closed_at_ms` se
    deriva de `timestamp_ms + duration_ms`)."""
    sig_by_id = {s["id"]: s for s in signals}

    by_signal = defaultdict(list)
    for o in outcomes:
        if o.get("signal_id"):
            by_signal[o["signal_id"]].append(o)

    trades = []
    for sig_id, legs in by_signal.items():
        sig = sig_by_id.get(sig_id)
        if sig is None:
            continue  # outcome huérfano

        # Ordenar por close timestamp.
        def close_ms(o):
            return (o.get("timestamp_ms") or 0) + (o.get("duration_ms") or 0)

        legs.sort(key=close_ms)

        entry = sig.get("entry_price") or 0.0
        stop = sig.get("stop_price")
        target = sig.get("target_price")
        risk_unit = abs(entry - (stop or entry))

        total_net = sum(o.get("pnl_net_usd", 0.0) for o in legs)
        total_gross = sum(o.get("pnl_gross_usd", 0.0) for o in legs)
        total_fees = sum(
            (o.get("fee_entry_usd", 0.0) + o.get("fee_exit_usd", 0.0)) for o in legs
        )
        total_fund = sum(o.get("funding_cost_usd", 0.0) for o in legs)

        # Razón final: TARGET_HIT no-parcial pisa todo; INVALIDATED pisa todo; sino el último.
        final_reason = legs[-1].get("close_reason", "?")
        for o in legs:
            if o.get("close_reason") == "TARGET_HIT" and not o.get("is_partial", False):
                final_reason = "TARGET_HIT"
                break
            if o.get("close_reason") == "INVALIDATED":
                final_reason = "INVALIDATED"
                break

        # MFE/MAE: leg con valores no-cero (típicamente la pierna TP1_PARTIAL).
        mfe_r, mae_r = 0.0, 0.0
        for o in legs:
            if (o.get("mfe_r") or 0.0) > 0:
                mfe_r = o["mfe_r"]
                mae_r = o.get("mae_r") or 0.0
                break

        mfe_price = mfe_r * risk_unit
        mae_price = mae_r * risk_unit

        # Mismo sizing nominal que el script Supabase: $3 de riesgo.
        risk_usd = 3.0
        size = risk_usd / risk_unit if risk_unit > 1e-6 else 0.0

        closed_ms = close_ms(legs[-1])

        # INVALIDATED se mapea a STOP_HIT para que el bloque 10 lo cuente como pérdida,
        # pero conservamos el motivo crudo en `close_reason_raw`.
        close_reason_norm = "STOP_HIT" if final_reason == "INVALIDATED" else final_reason

        trades.append({
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
            "intended_entry": entry,
            "closed_at_ms":   closed_ms,
            "delta":          sig.get("taker_imbalance"),
            "obi_l5":         sig.get("obi_l5"),
            "funding_current": sig.get("funding_current"),
        })

    return trades


def build_strategy_signals(signals):
    return [{"score": s.get("score"), "strategy_id": s.get("strategy")} for s in signals]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true",
                    help="Escribir JSONLs a scripts/mongo_export/ sin correr el análisis")
    ap.add_argument("--limit", type=int, default=10000,
                    help="Máximo de documentos a leer por colección (default 10000)")
    args = ap.parse_args()

    print("Leyendo de MongoDB local...")
    signals = fetch_signals(args.limit)
    outcomes = fetch_outcomes(args.limit)
    print(f"  shadow_signals  : {len(signals)}")
    print(f"  signal_outcomes : {len(outcomes)}")

    trades = build_paper_trades(signals, outcomes)
    sigs = build_strategy_signals(signals)

    print(f"\nTrades sintetizados: {len(trades)}")
    for t in trades:
        print(f"  {t['strategy_id']:<35} {t['close_reason']:<14} "
              f"net={t['net_pnl']:+.2f}  regime={t['regime']}")

    write_dir = (SCRIPT_DIR / "mongo_export") if args.dump else None
    if write_dir is not None:
        write_dir.mkdir(exist_ok=True)
        _write_jsonls(write_dir, trades, sigs)
        print(f"\nExportado a: {write_dir}")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        _write_jsonls(tmp, trades, sigs)
        print("\n" + "=" * 70)
        analyze = SCRIPT_DIR / "analyze_outcomes.py"
        subprocess.run([sys.executable, str(analyze), str(tmp)], check=False)


def _write_jsonls(dir_: Path, trades, sigs):
    (dir_ / "paper_trades.jsonl").write_text(
        "\n".join(json.dumps(t) for t in trades), encoding="utf-8")
    (dir_ / "strategy_signals.jsonl").write_text(
        "\n".join(json.dumps(s) for s in sigs), encoding="utf-8")
    # analyze_outcomes acepta estos archivos vacíos.
    (dir_ / "contradictions.jsonl").write_text("", encoding="utf-8")
    (dir_ / "strategy_outcomes.jsonl").write_text("", encoding="utf-8")


if __name__ == "__main__":
    main()
