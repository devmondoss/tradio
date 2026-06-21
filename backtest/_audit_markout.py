"""
_audit_markout.py — Análisis de MARKOUT post-fill (calidad de fills maker, selección adversa)
=============================================================================================
La métrica de mesa de market-making: tras un fill límite en el nivel, ¿a dónde va el precio a
+1/5/30/60/300s? Markout favorable = el mercado te respeta. Negativo = te llenaron justo antes de
que te atropellaran (selección adversa). Se mide con el TAPE tick-a-tick real (raw_trades, 365d),
SEPARADO por régimen de volatilidad (la pregunta clave: ¿en VOL-HIGH los fills se vuelven tóxicos?).

NO simula salida ni PnL — aísla la calidad del fill, que es justo lo que el backtest no puede ver.
Fill price = nivel (límite maker). markout_bps = signo·(precio_futuro − nivel)/nivel·1e4
  signo = +1 long (favorable si el precio sube), −1 short (favorable si baja).
"""
import sys, glob
from pathlib import Path
from datetime import datetime, timezone
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _audit_mirror import gen_h21_short

ROOT = Path(__file__).parent.parent
RAW = ROOT / "data/bybit-perp/raw_trades"
HORIZONS = [1, 5, 30, 60, 300]   # segundos
TF = 15


def placements(a, gens):
    """Todas las órdenes límite maker que la estrategia colocaría y que el TF tocaría (fillables),
    etiquetadas por régimen de vol. Sin cap/cooldown: maximiza la muestra para juzgar CALIDAD de fill."""
    atr_med = pd.Series(a.atr).rolling(500, min_periods=50).median().shift(1).values
    bar_ms = TF*60_000
    out = []
    for g in gens:
        for i in range(60, a.n-1):
            if a.atr[i] <= 0: continue
            reg = "high" if (np.isfinite(atr_med[i]) and a.atr[i] > atr_med[i]) else "low"
            for side, lvl, stop, tp1, tp2, kind in (g(a, i) or []):
                if not np.isfinite([lvl, stop, tp2]).all(): continue
                ref = a.c[i-1]
                if side == "long" and not (lvl < ref): continue
                if side == "short" and not (lvl > ref): continue
                # el TF toca el nivel (≈ se llena) — con el margen de selección adversa 2bps del backtest
                if side == "long" and not (a.l[i] <= lvl - 2/1e4*lvl): continue
                if side == "short" and not (a.h[i] >= lvl + 2/1e4*lvl): continue
                risk = abs(lvl-stop)
                if risk <= 0 or abs(tp2-lvl)/risk < 1.2: continue
                if side == "long" and not (stop < lvl < tp2): continue
                if side == "short" and not (tp2 < lvl < stop): continue
                out.append(dict(bar_ts=int(a.ts[i]), bar_end=int(a.ts[i])+bar_ms,
                                level=float(lvl), side=side, reg=reg, kind=kind))
    return pd.DataFrame(out)


def markout_for_day(date_str, plc_day):
    """Para los placements de un día: encuentra el tick del fill y mide markout a cada horizonte."""
    f = RAW / f"{date_str}.parquet"
    if not f.exists(): return []
    df = pd.read_parquet(f, columns=["ts_ms", "price"])
    ts = df.ts_ms.values.astype(np.int64); px = df.price.values.astype(float)
    rows = []
    for p in plc_day.itertuples():
        lo = np.searchsorted(ts, p.bar_ts); hi = np.searchsorted(ts, p.bar_end)
        if hi <= lo: continue
        w_ts = ts[lo:hi]; w_px = px[lo:hi]
        # primer tick que cruza el nivel = fill
        cross = (w_px <= p.level) if p.side == "long" else (w_px >= p.level)
        k = np.argmax(cross)
        if not cross[k]: continue
        fill_ts = w_ts[k]; sign = 1.0 if p.side == "long" else -1.0
        rec = dict(reg=p.reg, kind=p.kind, side=p.side, fill_ts=int(fill_ts))
        for h in HORIZONS:
            j = np.searchsorted(ts, fill_ts + h*1000)
            if j >= len(ts): rec[f"mo{h}"] = np.nan; continue
            rec[f"mo{h}"] = sign*(px[j]-p.level)/p.level*1e4
        rows.append(rec)
    return rows


def report(df, label):
    if len(df) == 0: print(f"{label}: SIN FILLS"); return
    cols = [f"mo{h}" for h in HORIZONS]
    means = df[cols].mean()
    meds = df[cols].median()
    winrate = (df["mo30"] > 0).mean()*100  # % de fills con markout favorable a 30s
    print(f"{label:<30} n={len(df):>5} | markout medio (bps): " +
          "  ".join(f"{h}s={means[f'mo{h}']:+.2f}" for h in HORIZONS) +
          f" | %fav@30s={winrate:.0f}%")


def main():
    print(f"Cargando M{TF} + generando placements (h5 + h21 + mirror)...")
    t = L2.load2(TF, start_ms=L2.TICK_MS)
    a = L2.A2(t)
    plc = placements(a, [L2.gen_h5(), L2.gen_h21(), gen_h21_short()])
    plc["date"] = plc.bar_ts.apply(lambda ms: datetime.fromtimestamp(ms/1000, tz=timezone.utc).strftime("%Y-%m-%d"))
    print(f"  {len(plc)} placements fillables  (VOL-HIGH {int((plc.reg=='high').sum())} / VOL-LOW {int((plc.reg=='low').sum())})")

    all_rows = []
    dates = sorted(plc.date.unique())
    for n, d in enumerate(dates):
        all_rows += markout_for_day(d, plc[plc.date == d])
        if (n+1) % 60 == 0: print(f"  ...{n+1}/{len(dates)} días procesados")
    mo = pd.DataFrame(all_rows)
    print(f"\n{len(mo)} fills con markout medido.\n")
    print("(markout >0 = favorable; el spread típico BTC perp ≈ 0.5-1 bp, fee maker 2bps/lado)\n")

    print("=== POR RÉGIMEN DE VOLATILIDAD (la pregunta clave) ===")
    report(mo, "TODO")
    report(mo[mo.reg == "high"], "VOL-HIGH")
    report(mo[mo.reg == "low"], "VOL-LOW")

    print("\n=== POR COMPONENTE (×régimen) ===")
    for k in ("H5", "H21", "H21s"):
        for reg in ("high", "low"):
            report(mo[(mo.kind == k) & (mo.reg == reg)], f"{k} · VOL-{reg.upper()}")

    print("\n=== POR LADO ===")
    report(mo[mo.side == "long"], "LONG")
    report(mo[mo.side == "short"], "SHORT")


if __name__ == "__main__":
    main()
