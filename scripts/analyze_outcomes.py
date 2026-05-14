#!/usr/bin/env python3
"""
Analyze strategy outcomes from shadow_events/strategy_outcomes.jsonl.

Reads from %APPDATA%/flowsurface/shadow_events/strategy_outcomes.jsonl
and prints per-detector and aggregate stats:
  - Signal count
  - Win rate (MFE > MAE)
  - Mean MFE / Mean MAE
  - MFE/MAE ratio
  - Mean entry confidence
"""

import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean


def load_outcomes(path: Path) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def analyze(records: list[dict]) -> None:
    if not records:
        print("No records found.")
        return

    by_detector: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        det = r.get("detector", "unknown")
        by_detector[det].append(r)

    def stats(group: list[dict]) -> dict:
        mfe_vals = [r["max_favorable"] for r in group if r.get("max_favorable") is not None]
        mae_vals = [r["max_adverse"]   for r in group if r.get("max_adverse")   is not None]
        conf_vals = [r["confidence"]   for r in group if r.get("confidence")    is not None]

        wins = sum(
            1 for r in group
            if r.get("max_favorable") is not None and r.get("max_adverse") is not None
            and r["max_favorable"] > r["max_adverse"]
        )
        total_with_outcome = sum(
            1 for r in group
            if r.get("max_favorable") is not None and r.get("max_adverse") is not None
        )

        return {
            "count":       len(group),
            "with_outcome": total_with_outcome,
            "win_rate":    wins / total_with_outcome if total_with_outcome else None,
            "mean_mfe":    mean(mfe_vals) if mfe_vals else None,
            "mean_mae":    mean(mae_vals) if mae_vals else None,
            "mfe_mae":     mean(mfe_vals) / mean(mae_vals)
                           if mfe_vals and mae_vals and mean(mae_vals) > 0 else None,
            "mean_conf":   mean(conf_vals) if conf_vals else None,
        }

    col_w = 26
    print(f"\n{'Detector':<{col_w}} {'Count':>6} {'W/Outcome':>10} {'WinRate':>8} "
          f"{'MeanMFE':>9} {'MeanMAE':>9} {'MFE/MAE':>8} {'AvgConf':>8}")
    print("-" * (col_w + 62))

    all_records: list[dict] = []
    for det in sorted(by_detector.keys()):
        group = by_detector[det]
        all_records.extend(group)
        s = stats(group)
        wr  = f"{s['win_rate']:.1%}"   if s["win_rate"]  is not None else "n/a"
        mfe = f"{s['mean_mfe']:.4f}"   if s["mean_mfe"]  is not None else "n/a"
        mae = f"{s['mean_mae']:.4f}"   if s["mean_mae"]  is not None else "n/a"
        fm  = f"{s['mfe_mae']:.2f}"    if s["mfe_mae"]   is not None else "n/a"
        cf  = f"{s['mean_conf']:.3f}"  if s["mean_conf"] is not None else "n/a"
        print(f"{det:<{col_w}} {s['count']:>6} {s['with_outcome']:>10} {wr:>8} "
              f"{mfe:>9} {mae:>9} {fm:>8} {cf:>8}")

    print("-" * (col_w + 62))
    s = stats(all_records)
    wr  = f"{s['win_rate']:.1%}"   if s["win_rate"]  is not None else "n/a"
    mfe = f"{s['mean_mfe']:.4f}"   if s["mean_mfe"]  is not None else "n/a"
    mae = f"{s['mean_mae']:.4f}"   if s["mean_mae"]  is not None else "n/a"
    fm  = f"{s['mfe_mae']:.2f}"    if s["mfe_mae"]   is not None else "n/a"
    cf  = f"{s['mean_conf']:.3f}"  if s["mean_conf"] is not None else "n/a"
    print(f"{'TOTAL':<{col_w}} {s['count']:>6} {s['with_outcome']:>10} {wr:>8} "
          f"{mfe:>9} {mae:>9} {fm:>8} {cf:>8}")
    print()


def main() -> None:
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        appdata = os.environ.get("APPDATA", "")
        path = Path(appdata) / "flowsurface" / "shadow_events" / "strategy_outcomes.jsonl"

    if not path.exists():
        print(f"Outcomes file not found: {path}")
        print("Run the app and generate some signals first.")
        sys.exit(1)

    records = load_outcomes(path)
    print(f"Loaded {len(records)} records from {path}")
    analyze(records)


if __name__ == "__main__":
    main()
