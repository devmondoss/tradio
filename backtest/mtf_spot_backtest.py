"""
mtf_spot_backtest.py
--------------------
MTF Spot Shorts v4 - Bybit BTCUSDT local parquet.

Implements docs/MTF_SPOT_SHORTS_SPEC.md:
  SIGNAL = LEVEL + REJECTION + FLOW

Active rules:
  - BTCUSDT spot only.
  - Sessions: Overlap and New York. London is excluded.
  - Level: high near VAH/PDH/AH/WH within 0.70%; VAH is required.
  - Block PDH+AH+VAH confluence and PDH+VAH false confluence.
  - Block WH confluence trades during 15:00-15:59 UTC.
  - Block AH+VAH trades when OBI < -0.15.
  - Rejection: upper wick 30%-85% and bearish close.
  - Flow: obi10_mean < -0.05 OR delta < 0.
  - Stop: current H1 high + 0.40 * ATR14_H1.
  - Stop range: 0.30%-0.75%.
  - Target: 2.0R.
  - CVD exit: 5 positive CVD-slope bars + OBI > 0.15 and profit >= 1R.
  - One open trade at a time, no cooldown.

Usage:
    python backtest/mtf_spot_backtest.py --json
    python backtest/mtf_spot_backtest.py --days 90 --json
    python backtest/mtf_spot_backtest.py --info
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
DATA_PATH = ROOT / "data/bybit-spot/processed/btcusdt_m1.parquet"
EXPORTS_DIR = ROOT / "exports"

SYMBOL = "BTCUSDT"
CAPITAL_INIT = 500.0
RISK_PCT = 0.02
FEE_RT = 0.0007
TARGET_R = 2.0
MIN_STOP = 0.0030
MAX_STOP = 0.0075
LEVEL_TOL = 0.007
ATR_MULT = 0.40
H1_MS = 3_600_000
D1_MS = 86_400_000
OOS_MS = int(pd.Timestamp("2026-03-01", tz="UTC").value // 1_000_000)


def atr14(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    tr = np.maximum(
        high - low,
        np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))),
    )
    tr[0] = high[0] - low[0]
    out = np.empty_like(tr)
    out[:14] = tr[:14].mean()
    k = 1 / 14
    for i in range(14, len(tr)):
        out[i] = out[i - 1] * (1 - k) + tr[i] * k
    return out


def resample_ohlc(df: pd.DataFrame, freq_ms: int) -> pd.DataFrame:
    df = df.copy()
    df["tf"] = (df["ts_ms"] // freq_ms) * freq_ms
    return (
        df.groupby("tf", sort=True)
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
        )
        .reset_index()
        .rename(columns={"tf": "ts_ms"})
    )


def session_of(ts_ms: int) -> str:
    hm = (ts_ms // 60_000) % 1440
    if 7 * 60 <= hm < 12 * 60:
        return "london"
    if 12 * 60 <= hm < 16 * 60:
        return "overlap"
    if 16 * 60 <= hm < 20 * 60:
        return "ny"
    return ""


def active_level(row: dict) -> tuple[bool, str]:
    high = float(row["high"])
    levels: list[str] = []

    checks = (
        ("PDH", row.get("prev_day_high")),
        ("AH", row.get("asian_high")),
        ("WH", row.get("weekly_high")),
        ("VAH", row.get("vp_vah")),
    )
    for name, level in checks:
        level = float(level or 0.0)
        if level > 0 and abs(high - level) / level <= LEVEL_TOL:
            levels.append(name)

    if not levels:
        return False, ""
    return True, "+".join(levels)


def rejection(row: dict) -> tuple[bool, float]:
    close = float(row["close"])
    open_ = float(row["open"])
    high = float(row["high"])
    low = float(row["low"])
    rng = high - low
    if rng <= 0:
        return False, 0.0
    wick_up = high - max(close, open_)
    wick_pct = wick_up / rng
    return (0.30 < wick_pct < 0.85 and close <= open_), wick_pct


def flow_bearish(row: dict) -> bool:
    obi = float(row.get("obi10_mean") or 0.0)
    delta = float(row.get("delta") or 0.0)
    return obi < -0.05 or delta < 0


def build_trade(
    idx: int,
    entry_ts: int,
    exit_ts: int,
    entry: float,
    stop: float,
    target: float,
    exit_price: float,
    reason: str,
    bars: int,
    session: str,
    level: str,
    wick_pct: float,
    obi: float,
    delta: float,
    cvd_slope: float,
    mfe_r: float,
    mae_r: float,
    capital_before: float,
) -> dict:
    risk = stop - entry
    gross_r = (entry - exit_price) / risk if risk > 0 else 0.0
    risk_usd = capital_before * RISK_PCT
    pnl_usd = risk_usd * gross_r - risk_usd * FEE_RT
    equity = capital_before + pnl_usd
    stop_pct = risk / entry * 100
    evidence = [
        f"level:{level}",
        f"wick:{wick_pct:.2f}",
        f"obi:{obi:.3f}",
        f"delta:{delta:.1f}",
    ]
    return {
        "idx": idx,
        "id": str(entry_ts),
        "sym": SYMBOL,
        "dir": "Short",
        "session": session,
        "score": None,
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "target": round(target, 2),
        "exit": round(exit_price, 2),
        "resultR": round(gross_r, 3),
        "pnlUsd": round(pnl_usd, 2),
        "riskUsd": round(risk_usd, 2),
        "stopPct": round(stop_pct, 3),
        "equity": round(equity, 2),
        "reason": reason,
        "tsMs": entry_ts,
        "ts": entry_ts // 1000,
        "closedAt": pd.Timestamp(exit_ts, unit="ms", tz="UTC").isoformat(),
        "regime": "spot-v1",
        "sessionPhase": session,
        "evidence": evidence,
        "confluenceFlags": [level],
        "vetoReason": "",
        "cvdInRange": None,
        "vr": None,
        "priceVsVwap": None,
        "funding": None,
        "cvdSlope": round(cvd_slope, 4),
        "obi": round(obi, 4),
        "dz": None,
        "rangePct": None,
        "rangeBars": None,
        "rangeTouch": None,
        "durationMin": bars,
        "isOpen": False,
        "htf": {
            "level": level,
            "wickPct": round(wick_pct, 3),
            "mfeR": round(mfe_r, 3),
            "maeR": round(mae_r, 3),
        },
    }


def simulate(df: pd.DataFrame) -> tuple[list[dict], float]:
    h1 = resample_ohlc(df, H1_MS)
    h1["atr"] = atr14(h1["high"].values, h1["low"].values, h1["close"].values)
    h1_ctx = {int(r.ts_ms): (float(r.high), float(r.atr)) for r in h1.itertuples()}

    rows = df.to_dict("records")
    trades: list[dict] = []
    capital = CAPITAL_INIT
    in_trade = False

    entry = stop = target = risk = 0.0
    start_i = entry_ts = 0
    trade_session = trade_level = ""
    trade_wick = trade_obi = trade_delta = trade_cvd = 0.0
    mfe_r = mae_r = 0.0
    cvd_pos_streak = 0

    for i, row in enumerate(rows):
        ts = int(row["ts_ms"])

        if in_trade:
            high = float(row["high"])
            low = float(row["low"])
            close = float(row["close"])
            mfe_r = max(mfe_r, (entry - low) / risk)
            mae_r = max(mae_r, (high - entry) / risk)
            bars = i - start_i

            cvd_now = float(row.get("cvd_slope") or 0.0)
            obi_now = float(row.get("obi10_mean") or 0.0)
            cvd_pos_streak = cvd_pos_streak + 1 if cvd_now > 0 else 0
            cur_r = (entry - close) / risk

            reason = ""
            exit_price = 0.0
            if high >= stop:
                reason, exit_price = "stop", stop
            elif low <= target:
                reason, exit_price = "target", target
            elif cvd_pos_streak >= 5 and obi_now > 0.15 and cur_r >= 1.0:
                reason, exit_price = "cvd_exit", close

            if reason:
                trade = build_trade(
                    len(trades) + 1,
                    entry_ts,
                    ts,
                    entry,
                    stop,
                    target,
                    exit_price,
                    reason,
                    bars,
                    trade_session,
                    trade_level,
                    trade_wick,
                    trade_obi,
                    trade_delta,
                    trade_cvd,
                    mfe_r,
                    mae_r,
                    capital,
                )
                capital += trade["pnlUsd"]
                trades.append(trade)
                in_trade = False
                cvd_pos_streak = 0
            continue

        session = session_of(ts)
        if session not in ("overlap", "ny"):
            continue

        ok_level, level = active_level(row)
        if not ok_level or "VAH" not in level:
            continue
        parts = level.split("+")
        if len(parts) >= 3 and "PDH" in parts and "AH" in parts:
            continue
        if "PDH" in parts and "VAH" in parts and len(parts) == 2:
            continue
        if "WH" in parts and (ts // H1_MS) % 24 == 15:
            continue
        if level == "AH+VAH" and float(row.get("obi10_mean") or 0.0) < -0.15:
            continue

        ok_rejection, wick_pct = rejection(row)
        if not ok_rejection:
            continue
        if not flow_bearish(row):
            continue

        h1_data = h1_ctx.get((ts // H1_MS) * H1_MS)
        if h1_data is None:
            continue
        h1_high, h1_atr = h1_data
        entry_ = float(row["close"])
        stop_ = h1_high + ATR_MULT * h1_atr
        risk_ = stop_ - entry_
        if risk_ <= 0:
            continue
        stop_frac = risk_ / entry_
        if not (MIN_STOP <= stop_frac <= MAX_STOP):
            continue

        in_trade = True
        entry = entry_
        stop = stop_
        risk = risk_
        target = entry - TARGET_R * risk
        start_i = i
        entry_ts = ts
        trade_session = session
        trade_level = level
        trade_wick = wick_pct
        trade_obi = float(row.get("obi10_mean") or 0.0)
        trade_delta = float(row.get("delta") or 0.0)
        trade_cvd = float(row.get("cvd_slope") or 0.0)
        mfe_r = mae_r = 0.0
        cvd_pos_streak = 0

    return trades, capital


def summarize(trades: list[dict], df: pd.DataFrame, capital_final: float) -> dict:
    closed = [t for t in trades if not t["isOpen"]]
    n = len(closed)
    wins = sum(1 for t in closed if (t["resultR"] or 0) > 0)
    total_r = sum(t["resultR"] or 0 for t in closed)
    start_ms = int(df["ts_ms"].min())
    end_ms = int(df["ts_ms"].max())
    actual_days = round((end_ms - start_ms) / D1_MS)

    oos = [t for t in closed if t["tsMs"] >= OOS_MS]
    oos_wins = sum(1 for t in oos if (t["resultR"] or 0) > 0)
    oos_total_r = sum(t["resultR"] or 0 for t in oos)

    by_reason = defaultdict(int)
    by_session = defaultdict(int)
    by_level = defaultdict(int)
    for t in closed:
        by_reason[t["reason"]] += 1
        by_session[t["session"]] += 1
        by_level[t["htf"]["level"]] += 1

    return {
        "trades": trades,
        "n": n,
        "wins": wins,
        "total_r": round(total_r, 2),
        "avg_r": round(total_r / n, 3) if n else 0.0,
        "wr_pct": round(wins / n * 100, 1) if n else 0.0,
        "equity": round(capital_final, 2),
        "actual_days": actual_days,
        "symbol": SYMBOL,
        "n_shorts": n,
        "n_longs": 0,
        "longs_enabled": False,
        "strategy": "mtf_spot_shorts_v4",
        "oos": {
            "n": len(oos),
            "wins": oos_wins,
            "wr_pct": round(oos_wins / len(oos) * 100, 1) if oos else 0.0,
            "total_r": round(oos_total_r, 2),
            "avg_r": round(oos_total_r / len(oos), 3) if oos else 0.0,
        },
        "breakdown": {
            "reason": dict(sorted(by_reason.items())),
            "session": dict(sorted(by_session.items())),
            "level": dict(sorted(by_level.items())),
        },
    }


def load_data(days: int) -> pd.DataFrame:
    df = pd.read_parquet(DATA_PATH).sort_values("ts_ms").reset_index(drop=True)
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].fillna("")
        elif pd.api.types.is_numeric_dtype(df[col]):
            df[col] = df[col].fillna(0.0)
    for col in ("obi10_mean", "delta", "cvd_slope", "vp_vah", "prev_day_high", "asian_high", "weekly_high"):
        if col not in df.columns:
            df[col] = 0.0
    if days > 0:
        cutoff = int(df["ts_ms"].max()) - days * D1_MS
        df = df[df["ts_ms"] >= cutoff].reset_index(drop=True)
    return df


def print_report(result: dict) -> None:
    print("MTF Spot Shorts v4 - BTCUSDT")
    print(f"n={result['n']} WR={result['wr_pct']}% AvgR={result['avg_r']} TotalR={result['total_r']}R")
    print(f"Equity: ${CAPITAL_INIT:.0f} -> ${result['equity']:.0f}")
    oos = result["oos"]
    print(f"OOS: n={oos['n']} WR={oos['wr_pct']}% AvgR={oos['avg_r']} TotalR={oos['total_r']}R")
    print("Breakdown:", json.dumps(result["breakdown"], ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=SYMBOL)
    parser.add_argument("--days", type=int, default=0, help="0 = all available data")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--info", action="store_true")
    args = parser.parse_args()

    if args.symbol != SYMBOL:
        msg = "MTF Spot Shorts v4 is calibrated only for BTCUSDT."
        if args.json or args.info:
            print(json.dumps({"error": msg}))
            return
        sys.exit(msg)

    if not DATA_PATH.exists():
        msg = f"Missing local parquet: {DATA_PATH}"
        if args.json or args.info:
            print(json.dumps({"error": msg}))
            return
        sys.exit(msg)

    if args.info:
        df_meta = pd.read_parquet(DATA_PATH, columns=["ts_ms"])
        start_ms = int(df_meta["ts_ms"].min())
        end_ms = int(df_meta["ts_ms"].max())
        avail_days = round((end_ms - start_ms) / D1_MS)
        start_lbl = pd.Timestamp(start_ms, unit="ms", tz="UTC").strftime("%d %b %Y")
        print(
            json.dumps(
                {
                    "available_days": avail_days,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "start_label": start_lbl,
                    "symbol": SYMBOL,
                    "strategy": "mtf_spot_shorts_v4",
                }
            )
        )
        return

    df = load_data(args.days)
    trades, capital_final = simulate(df)
    result = summarize(trades, df, capital_final)

    EXPORTS_DIR.mkdir(exist_ok=True)
    out_file = EXPORTS_DIR / "mtf_btcusdt_backtest.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(result, f)
    pd.DataFrame(trades).to_csv(EXPORTS_DIR / "mtf_spot_v4_trades.csv", index=False)

    if args.json:
        print(json.dumps(result))
    else:
        print_report(result)


if __name__ == "__main__":
    main()
