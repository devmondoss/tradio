"""
_fabio_continuation.py — modelo de CONTINUACIÓN estilo Fabio Valentini (Direction → Location → Aggression).
================================================================================
Distinto de liquidity (fade en niveles VP) y de sc3 (absorción fade a target estructural):
acá se opera A FAVOR de la tendencia HTF, entrando en el pullback, dejando correr con trailing.

  Direction  : sesgo HTF ESTRICTO — H1 EMA20 Y H4 EMA20 alineados (AND, no OR). Filtro previo
               obligatorio, no solo confirmatorio (a diferencia de sc3 h1_or_h4_or_vr3).
  Location   : pullback DENTRO de la tendencia a value (vp_poc/vp_val/vp_vah) o ema20 —
               no niveles estructurales extremos (PDH/PDL/weekly/swing) que sc3 sí usa.
  Aggression : absorción/delta a favor de la tendencia en el pullback (footprint fp_delta si
               existe, si no delta base) + filtro de volumen (vr).
  Management : TRAIL — deja correr la continuación (a diferencia del target fijo de sc3/fade).

Uso: python backtest/_fabio_continuation.py [--tf 15] [--symbols BTCUSDT ETHUSDT SOLUSDT]
"""
import sys
import argparse
from pathlib import Path
import numpy as np, pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parent))
import _scalp as SC
from _scalp import struct_tp, run_setup, stats, ASSETS


def attach_htf(s, symbol, tf):
    """Adjunta h1_bearish/h4_bearish (booleanos EMA20 HTF) al frame S ya cargado por SC.load()."""
    cols = ["ts_ms", "h1_bearish", "h4_bearish"]
    df = pq.read_table(ASSETS[symbol]["m1"], columns=cols).to_pandas().sort_values("ts_ms").reset_index(drop=True)
    if tf != 1:
        step = tf * 60_000
        g = (df.ts_ms // step) * step
        df = (df.groupby(g)
                .agg(ts_ms=("ts_ms", "first"), h1_bearish=("h1_bearish", "last"), h4_bearish=("h4_bearish", "last"))
                .reset_index(drop=True))
    df["h1_bearish"] = pd.to_numeric(df["h1_bearish"], errors="coerce").fillna(0).astype(bool)
    df["h4_bearish"] = pd.to_numeric(df["h4_bearish"], errors="coerce").fillna(0).astype(bool)
    m = dict(zip(df.ts_ms.values, zip(df.h1_bearish.values, df.h4_bearish.values)))
    default = (False, False)
    s.h1_bearish = np.array([m.get(t, default)[0] for t in s.ts])
    s.h4_bearish = np.array([m.get(t, default)[1] for t in s.ts])
    return s


def gen_continuation(vr_thr=1.5, stop_atr=0.5, tol_atr=0.5, delta_min=0.05):
    LOC_L = ("vp_val", "vp_poc", "ema20")
    LOC_S = ("vp_vah", "vp_poc", "ema20")

    def g(s, i):
        out = []
        bull = (not s.h1_bearish[i]) and (not s.h4_bearish[i])   # Direction: HTF estricto AND
        bear = s.h1_bearish[i] and s.h4_bearish[i]
        if not (bull or bear):
            return out
        if s.vr[i] < vr_thr:                                    # Aggression: convicción de volumen
            return out
        atr = s.atr[i]
        fpd = getattr(s, "fp_delta", None)
        if bull:
            for key in LOC_L:                                   # Location: pullback a value/ema
                lvl = getattr(s, key)[i]
                if not np.isfinite(lvl) or lvl >= s.c[i]:
                    continue
                if abs(s.l[i] - lvl) <= tol_atr * atr and s.c[i] > lvl:
                    ok = (fpd[i] >= delta_min * s.volume[i]) if fpd is not None else (s.delta[i] >= delta_min * s.volume[i])
                    if not ok:
                        continue
                    stop = lvl - stop_atr * atr
                    tp1, tp2 = struct_tp(s, i, "long", lvl)
                    if np.isfinite(tp2):
                        out.append(("long", lvl, stop, tp1, tp2, "fabio")); break
        if bear:
            for key in LOC_S:
                lvl = getattr(s, key)[i]
                if not np.isfinite(lvl) or lvl <= s.c[i]:
                    continue
                if abs(s.h[i] - lvl) <= tol_atr * atr and s.c[i] < lvl:
                    ok = (fpd[i] <= -delta_min * s.volume[i]) if fpd is not None else (s.delta[i] <= -delta_min * s.volume[i])
                    if not ok:
                        continue
                    stop = lvl + stop_atr * atr
                    tp1, tp2 = struct_tp(s, i, "short", lvl)
                    if np.isfinite(tp2):
                        out.append(("short", lvl, stop, tp1, tp2, "fabio")); break
        return out
    return g


def run(symbol, tf=15, vr_thr=1.5, trail_atr=4.0):
    s = SC.load(symbol, tf)
    attach_htf(s, symbol, tf)
    m1 = SC.load_m1_exit(symbol)
    g = gen_continuation(vr_thr=vr_thr)
    df = run_setup(s, g, m1, tf, entry_mode="maker", mgmt="trail", trail_atr=trail_atr,
                   timeout_min=24 * 60, cooldown=6, max_day=4)
    return df, stats(df)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", type=int, default=15)
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    ap.add_argument("--trail_atr", type=float, default=4.0)
    args = ap.parse_args()

    print(f"Fabio continuation · Direction(H1+H4 AND) + Location(pullback value/ema) + Aggression(delta) · TF=M{args.tf} · trail={args.trail_atr}\n")
    print(f"{'symbol':<10} {'n':>5} {'IS avgR':>9} {'OOS avgR':>9} {'WR':>6} {'DD%':>6} {'Sh':>6} {'/d':>5}")
    worst_oos = 99
    for sym in args.symbols:
        df, st = run(sym, tf=args.tf, trail_atr=args.trail_atr)
        worst_oos = min(worst_oos, st["oosA"] if st["n_oos"] > 0 else -99)
        print(f"{sym:<10} {st['n']:>5} {st['isA']:>+9.3f} {st['oosA']:>+9.3f} {st['wr']:>5.0f}% {st['dd']:>5.1f}% {st['sharpe']:>+5.1f} {st['npd']:>5.1f}")
    print(f"\npeorOOS (regla dura, positivo en los 3 = pasa): {worst_oos:+.3f}")


if __name__ == "__main__":
    main()
