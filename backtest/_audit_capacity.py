"""
_audit_capacity.py — CAPACIDAD real de la estrategia (el techo que le falta al compounding)
===========================================================================================
El compounding daba $500->$2.1M, pero ¿cuánto tamaño aguantan los niveles? Como maker, solo te
llenan hasta el volumen TAKER que cruza tu precio mientras tu límite reposa. Mido, por cada fill,
el volumen (BTC y $) que atraviesa el nivel en la ventana de la barra = tope de fill por trade.
Realista: estás al fondo de la cola -> capturas una FRACCIÓN (asumo 25%/50% como cotas).

Traduce a capital desplegable: notional/trade = risk_usd/stop% ; risk_usd = capital·1%.
=> capital_max ≈ capacidad_$ · stop% / 1%  (para que el notional quepa en la liquidez del nivel).
"""
import sys
from pathlib import Path
from datetime import datetime, timezone
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _audit_mirror import gen_h21_short
from _audit_markout import placements, RAW, TF


def cap_for_day(date_str, plc_day):
    f = RAW / f"{date_str}.parquet"
    if not f.exists(): return []
    df = pd.read_parquet(f, columns=["ts_ms", "price", "size"])
    ts = df.ts_ms.values.astype(np.int64); px = df.price.values.astype(float); sz = df["size"].values.astype(float)
    rows = []
    for p in plc_day.itertuples():
        lo = np.searchsorted(ts, p.bar_ts); hi = np.searchsorted(ts, p.bar_end)
        if hi <= lo: continue
        w_px = px[lo:hi]; w_sz = sz[lo:hi]
        # volumen que CRUZA el nivel (te llenaría como maker reposando ahí)
        mask = (w_px <= p.level) if p.side == "long" else (w_px >= p.level)
        btc = float(w_sz[mask].sum())
        if btc <= 0: continue
        rows.append(dict(reg=p.reg, kind=p.kind, btc=btc, usd=btc*p.level, stop_pct=None, level=p.level))
    return rows


def main():
    print("Generando placements + midiendo capacidad por fill (tape tick)...")
    t = L2.load2(TF, start_ms=L2.TICK_MS)
    a = L2.A2(t)
    plc = placements(a, [L2.gen_h5(), L2.gen_h21(), gen_h21_short()])
    plc["date"] = plc.bar_ts.apply(lambda ms: datetime.fromtimestamp(ms/1000, tz=timezone.utc).strftime("%Y-%m-%d"))

    rows = []
    dates = sorted(plc.date.unique())
    for n, d in enumerate(dates):
        rows += cap_for_day(d, plc[plc.date == d])
        if (n+1) % 90 == 0: print(f"  ...{n+1}/{len(dates)} días")
    cap = pd.DataFrame(rows)
    print(f"\n{len(cap)} fills con capacidad medida.\n")

    def q(s): return s.quantile([.1, .25, .5, .75, .9]).values

    print("=== Volumen que cruza el nivel por fill (capacidad bruta de fill) ===")
    for lbl, sub in [("TODO", cap), ("VOL-HIGH", cap[cap.reg == "high"]), ("VOL-LOW", cap[cap.reg == "low"])]:
        b = q(sub.btc); u = q(sub.usd)
        print(f"  {lbl:<9} BTC  p10={b[0]:.1f} p25={b[1]:.1f} med={b[2]:.1f} p75={b[3]:.1f} p90={b[4]:.1f}")
        print(f"  {'':<9} USD  med=${u[2]/1e3:.0f}k  p25=${u[1]/1e3:.0f}k  p75=${u[3]/1e3:.0f}k")

    # Capital desplegable: tu posición no debe pasarte de una fracción del volumen del nivel.
    # stop% mediano de la cartera ≈ 0.19%. notional = capital·1%/stop%.
    STOP_PCT = 0.0019
    print(f"\n=== Capital desplegable (stop mediano {STOP_PCT*100:.2f}%, riesgo 1%/trade) ===")
    print("    Regla: notional_trade = capital·0.01/stop%  debe caber en (fracción·capacidad_$ del nivel)")
    med_usd = cap.usd.median(); p25_usd = cap.usd.quantile(.25)
    for frac, name in [(0.50, "optimista (50% del flujo)"), (0.25, "realista (25%)"), (0.10, "conservador (10%)")]:
        # capital tal que notional = frac·capacidad ; notional = capital·0.01/STOP_PCT
        cap_med = frac*med_usd*STOP_PCT/0.01
        cap_p25 = frac*p25_usd*STOP_PCT/0.01
        print(f"  {name:<26} capital_max ≈ ${cap_med/1e3:.0f}k (nivel mediano) | ${cap_p25/1e3:.0f}k (p25, niveles flojos)")

    print("\n=== Lectura ===")
    print(f"  Capacidad mediana por fill: {cap.btc.median():.1f} BTC (${med_usd/1e3:.0f}k).")
    print(f"  El compounding a $2.1M es INALCANZABLE: requeriría ~${0.01*2.1e6/STOP_PCT/1e6:.1f}M de notional/trade")
    print(f"  vs ~${med_usd/1e3:.0f}k de liquidez en el nivel mediano. El techo realista está en")
    print(f"  decenas-cientos de miles de $, no millones. Ahí el compounding deja de ser exponencial.")


if __name__ == "__main__":
    main()
