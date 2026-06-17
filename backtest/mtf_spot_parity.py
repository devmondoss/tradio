"""
MTF Spot parity harness.

Compara los backtests canonicos Python contra el detector Rust live:

    python backtest/mtf_spot_parity.py --mode all
    python backtest/mtf_spot_parity.py --mode shorts --days 90

El harness genera NDJSON de contexto con los mismos niveles/H1/ATR que usa Python,
ejecuta `cargo run -p monitor --bin mtf_spot_parity`, y compara entradas, salidas,
stops, targets, razones y R bruto.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd

import mtf_spot_backtest as shorts
import mtf_spot_longs_backtest as longs

ROOT = Path(__file__).resolve().parent.parent
EXPORTS_DIR = ROOT / "exports" / "parity"


def clean_num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v == 0.0:
        return None
    return v


def context_rows(df: pd.DataFrame, direction: str) -> list[dict[str, Any]]:
    h1 = shorts.resample_ohlc(df, shorts.H1_MS)
    h1["atr"] = shorts.atr14(h1["high"].values, h1["low"].values, h1["close"].values)
    h1_ctx = {
        int(r.ts_ms): (float(r.high), float(r.low), float(r.atr))
        for r in h1.itertuples()
    }

    rows: list[dict[str, Any]] = []
    for r in df.to_dict("records"):
        ts = int(r["ts_ms"])
        h1_high, h1_low, h1_atr = h1_ctx.get((ts // shorts.H1_MS) * shorts.H1_MS, (None, None, None))
        rows.append(
            {
                "ts_ms": ts,
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "cvd_slope": clean_num(r.get("cvd_slope")),
                "obi10_mean": float(r.get("obi10_mean") or 0.0),
                "delta": float(r.get("delta") or 0.0),
                "vp_vah": clean_num(r.get("vp_vah")),
                "vp_val": clean_num(r.get("vp_val")),
                "h1_high": h1_high,
                "h1_low": h1_low,
                "h1_atr": h1_atr,
                "prev_day_high": clean_num(r.get("prev_day_high")),
                "prev_day_low": clean_num(r.get("prev_day_low")),
                "asian_high": clean_num(r.get("asian_high")),
                "asian_low": clean_num(r.get("asian_low")),
                "weekly_high": clean_num(r.get("weekly_high")),
                "weekly_low": clean_num(r.get("weekly_low")),
                "_direction": direction,
            }
        )
    return rows


def expected_trades(mode: str, df: pd.DataFrame) -> list[dict[str, Any]]:
    module = shorts if mode == "shorts" else longs
    trades, _capital = module.simulate(df)
    out: list[dict[str, Any]] = []
    for t in trades:
        out.append(
            {
                "ts_ms": int(t["tsMs"]),
                "direction": t["dir"],
                "strategy": "mtf_spot_shorts_v4" if mode == "shorts" else "mtf_spot_longs_v1",
                "session": t["session"],
                "level": t["htf"]["level"],
                "entry": round(float(t["entry"]), 3),
                "stop": round(float(t["stop"]), 3),
                "target": round(float(t["target"]), 3),
                "exit_price": round(float(t["exit"]), 3),
                "reason": t["reason"],
                "gross_r": round(float(t["resultR"]), 3),
                "duration_bars": int(t["durationMin"]),
            }
        )
    return out


def write_contexts(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            row = {k: v for k, v in row.items() if not k.startswith("_")}
            f.write(json.dumps(row, separators=(",", ":")) + "\n")


def run_rust(mode: str, ctx_path: Path) -> list[dict[str, Any]]:
    cmd = [
        "cargo",
        "+1.95.0-x86_64-pc-windows-gnu",
        "run",
        "--quiet",
        "-p",
        "monitor",
        "--bin",
        "mtf_spot_parity",
        "--",
        mode,
        str(ctx_path),
    ]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(
            "Rust parity runner failed\n"
            f"cmd: {' '.join(cmd)}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )
    return json.loads(proc.stdout)


def close_enough(a: Any, b: Any, tol: float) -> bool:
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return a == b


def compare(mode: str, expected: list[dict[str, Any]], actual: list[dict[str, Any]]) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    fields = [
        ("ts_ms", 0),
        ("direction", 0),
        ("strategy", 0),
        ("session", 0),
        ("level", 0),
        ("entry", 0.02),
        ("stop", 0.05),
        ("target", 0.05),
        ("exit_price", 0.05),
        ("reason", 0),
        ("gross_r", 0.002),
        ("duration_bars", 0),
    ]

    if len(expected) != len(actual):
        mismatches.append({"kind": "count", "expected": len(expected), "actual": len(actual)})

    for i, (e, a) in enumerate(zip(expected, actual), start=1):
        for field, tol in fields:
            ok = e.get(field) == a.get(field) if tol == 0 else close_enough(e.get(field), a.get(field), float(tol))
            if not ok:
                mismatches.append(
                    {
                        "kind": "field",
                        "trade": i,
                        "field": field,
                        "expected": e.get(field),
                        "actual": a.get(field),
                    }
                )
                break
        if len(mismatches) >= 20:
            break

    return {
        "mode": mode,
        "expected_n": len(expected),
        "actual_n": len(actual),
        "ok": not mismatches,
        "mismatches": mismatches,
    }


def run_mode(mode: str, days: int, keep: bool) -> dict[str, Any]:
    module = shorts if mode == "shorts" else longs
    df = module.load_data(days)
    ctx_path = EXPORTS_DIR / f"mtf_spot_{mode}_contexts.ndjson"
    write_contexts(ctx_path, context_rows(df, mode))

    expected = expected_trades(mode, df)
    actual = run_rust(mode, ctx_path)
    result = compare(mode, expected, actual)
    result["contexts"] = str(ctx_path)

    if not keep and result["ok"]:
        ctx_path.unlink(missing_ok=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["shorts", "longs", "all"], default="all")
    parser.add_argument("--days", type=int, default=0, help="0 = full local dataset")
    parser.add_argument("--keep", action="store_true", help="keep generated NDJSON contexts")
    args = parser.parse_args()

    modes = ["shorts", "longs"] if args.mode == "all" else [args.mode]
    results = []
    failed = False
    for mode in modes:
        result = run_mode(mode, args.days, args.keep)
        results.append(result)
        failed = failed or not result["ok"]

    print(json.dumps({"ok": not failed, "results": results}, indent=2))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
