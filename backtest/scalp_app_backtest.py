"""
scalp_app_backtest.py — Backtest de SCALP sc3 (absorción VP) para la app de review (trade-lab).
=================================================================================================
Emite los trades de sc3 en el shape `Trade` que espera apps/trade-lab (entry/stop/target/exit/
tsMs/dir/resultR/reason...) para verlos en el chart. Config canónica per-asset (_scalp.SC3):
niveles ampliados (VP+PDH/PDL/weekly/swing) + gestión FADE (parcial 50%→BE→target rr_cap).

Uso (lo invoca vite.config):
  python backtest/scalp_app_backtest.py --info --symbol BTCUSDT
  python backtest/scalp_app_backtest.py --days 180 --json --symbol BTCUSDT
"""
import argparse, json, sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import _scalp as SC
from _scalp_more import gen_sc3x, ALLK_L, ALLK_S

CAP0, RISK = 500.0, 0.01
FEE_MK, FEE_TK = SC.FEE_MK, SC.FEE_TK


def run(symbol, tf=5, timeout_min=240, max_day=3, cooldown=6, stop_floor_pct=0.15, atr_win=500):
    """Genera y simula sc3 (config canónica) trackeando exit_px/exit_ts/reason para la visual."""
    c = SC.SC3[symbol]
    s = SC.load(symbol, tf); m1ts, m1h, m1l, m1c = SC.load_m1_exit(symbol)
    gen = gen_sc3x(vr_thr=c["vr_thr"], stop_atr=c["stop_atr"], tol_atr=c["tol_atr"],
                   rr_cap=c["rr_cap"], longk=ALLK_L, shortk=ALLK_S, confluence=c.get("confluence", 0))
    bar_ms = tf*60_000
    atr_med = pd.Series(s.atr).rolling(atr_win, min_periods=50).median().shift(1).values
    trades = []; cool = 0; dcount = {}
    for i in range(60, s.n-1):
        if i < cool or s.atr[i] <= 0: continue
        if not (np.isfinite(atr_med[i]) and s.atr[i] > atr_med[i]): continue
        d = int(s.day[i])
        if dcount.get(d, 0) >= max_day: continue
        for side, lvl, stop, tp1, tp2, tag in (gen(s, i) or []):
            if not np.isfinite([lvl, stop, tp2]).all(): continue
            ref = s.c[i-1]
            if side == "long" and not (lvl < ref): continue
            if side == "short" and not (lvl > ref): continue
            mf = 2.0/1e4
            if side == "long" and not (s.l[i] <= lvl*(1-mf)): continue
            if side == "short" and not (s.h[i] >= lvl*(1+mf)): continue
            entry = lvl
            if stop_floor_pct > 0:
                mr = stop_floor_pct/100.0*entry
                if abs(entry-stop) < mr: stop = entry-mr if side == "long" else entry+mr
            risk = abs(entry-stop)
            if risk <= 0: continue
            if side == "long" and not (stop < entry < tp2): continue
            if side == "short" and not (tp2 < entry < stop): continue
            if abs(tp2-entry)/risk < 1.2: continue
            j0 = np.searchsorted(m1ts, s.ts[i]+bar_ms); jend = min(np.searchsorted(m1ts, s.ts[i]+bar_ms+timeout_min*60_000), len(m1ts))
            if jend <= j0: continue
            # FADE: parcial 50% en tp1 → BE → tp2
            cur = stop; realized = 0.0; rem = 1.0; f1 = False
            p1 = 0.5 if (tp1 is not None and np.isfinite(tp1)) else 0.0
            exit_px = None; exit_ts = None; reason = "timeout"
            for j in range(j0, jend):
                if side == "long":
                    if m1l[j] <= cur: realized += rem*((cur-entry)/risk); exit_px = cur; reason = "breakeven" if f1 else "stop"; exit_ts = m1ts[j]; break
                    if not f1 and p1 and m1h[j] >= tp1: realized += p1*((tp1-entry)/risk); rem -= p1; f1 = True; cur = entry
                    if m1h[j] >= tp2: realized += rem*((tp2-entry)/risk); exit_px = tp2; reason = "target"; exit_ts = m1ts[j]; break
                else:
                    if m1h[j] >= cur: realized += rem*((entry-cur)/risk); exit_px = cur; reason = "breakeven" if f1 else "stop"; exit_ts = m1ts[j]; break
                    if not f1 and p1 and m1l[j] <= tp1: realized += p1*((entry-tp1)/risk); rem -= p1; f1 = True; cur = entry
                    if m1l[j] <= tp2: realized += rem*((entry-tp2)/risk); exit_px = tp2; reason = "target"; exit_ts = m1ts[j]; break
            if exit_px is None:
                jj = jend-1; px = m1c[jj]
                realized += rem*(((px-entry) if side == "long" else (entry-px))/risk); exit_px = px; exit_ts = m1ts[jj]
            exit_s = FEE_MK if reason == "target" else FEE_TK
            r = realized - (FEE_MK + (FEE_MK*p1 if f1 else 0.0) + exit_s*rem)*entry/risk
            trades.append(dict(tsMs=int(s.ts[i]), dir=("Long" if side == "long" else "Short"),
                               entry=float(entry), stop=float(stop), target=float(tp2),
                               tp1=(float(tp1) if (tp1 is not None and np.isfinite(tp1)) else None),
                               exit=float(exit_px), resultR=float(r), reason=reason, kind=tag,
                               closedAt=int(exit_ts), stopPct=float(100*risk/entry),
                               regime=str(s.reg[i]), vr=(float(s.vr[i]) if np.isfinite(s.vr[i]) else None)))
            cool = i+cooldown; dcount[d] = dcount.get(d, 0)+1; break
    return sorted(trades, key=lambda t: t["tsMs"])


def to_trade_json(raw, i, sym):
    risk_usd = CAP0*RISK
    return {
        "idx": i+1, "id": f"sc3-{i+1}", "sym": sym, "dir": raw["dir"], "session": "",
        "score": None, "entry": raw["entry"], "stop": raw["stop"], "target": raw["target"],
        "tp1": raw.get("tp1"), "targetName": "estructural(rr_cap)",
        "exit": raw["exit"], "resultR": round(raw["resultR"], 4), "pnlUsd": round(raw["resultR"]*risk_usd, 2),
        "riskUsd": risk_usd, "stopPct": round(raw["stopPct"], 3), "equity": 0.0,
        "reason": raw["reason"], "tsMs": raw["tsMs"], "ts": raw["tsMs"]//1000,
        "closedAt": pd.to_datetime(raw["closedAt"], unit="ms", utc=True).isoformat(),
        "regime": raw["regime"], "gestion": "fade", "sessionPhase": "", "evidence": [raw["kind"]],
        "confluenceFlags": [raw["kind"], "fade", "absorción VP"], "vetoReason": "",
        "cvdInRange": None, "vr": raw.get("vr"), "priceVsVwap": None, "funding": None,
        "cvdSlope": None, "obi": None, "dz": None, "rangePct": None, "rangeBars": None,
        "rangeTouch": None, "durationMin": None, "isOpen": False, "mscore": None, "absorb": True,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=540)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--info", action="store_true")
    ap.add_argument("--symbol", default="BTCUSDT")
    args = ap.parse_args()
    sym = args.symbol.upper()
    if sym not in SC.SC3:
        print(json.dumps({"error": f"símbolo no soportado: {sym}"})); sys.exit(1)
    if sym != "BTCUSDT" and not SC.ASSETS[sym]["m1"].exists():
        print(json.dumps({"error": f"parquet no encontrado: {SC.ASSETS[sym]['m1']}"})); sys.exit(1)

    s = SC.load(sym, 5)
    tmin, tmax = int(s.ts.min()), int(s.ts.max())
    if args.info:
        avail = int((tmax-tmin)/86_400_000)
        lbl = pd.to_datetime(tmin, unit="ms", utc=True).strftime("%d %b %Y")
        print(json.dumps({"available_days": avail, "start_label": lbl})); return

    raws = run(sym)
    if args.days and args.days > 0:
        cutoff = tmax - args.days*86_400_000
        raws = [r for r in raws if r["tsMs"] >= cutoff]
    trades = [to_trade_json(r, i, sym) for i, r in enumerate(raws)]
    eq = CAP0
    for tr in trades: eq += tr["pnlUsd"]; tr["equity"] = round(eq, 2)
    closed = [tr for tr in trades if not tr["isOpen"]]
    wins = sum(1 for tr in closed if tr["resultR"] > 0)
    totalR = sum(tr["resultR"] for tr in closed)
    actual = int((tmax - (raws[0]["tsMs"] if raws else tmin))/86_400_000)
    print(json.dumps({
        "trades": trades, "n": len(closed), "wins": wins,
        "total_r": round(totalR, 2), "avg_r": round(totalR/len(closed), 3) if closed else 0,
        "wr_pct": round(100*wins/len(closed), 1) if closed else 0,
        "equity": round(eq, 2), "actual_days": actual, "micro_start": None, "longs_enabled": True,
    }))


if __name__ == "__main__":
    main()
