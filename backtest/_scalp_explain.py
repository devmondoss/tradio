"""
_scalp_explain.py — diseca UN trade sc3 capa por capa + gráfico anotado.
Elige un long ganador limpio con confluencia y absorción fuerte, imprime cada capa
(nivel VP, footprint, disparador, filtros, entrada, gestión, salida) y dibuja el zoom.
Uso: python backtest/_scalp_explain.py [SYMBOL]
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
sys.path.insert(0, "backtest")
import _scalp as SC
from _scalp_more import gen_sc3x, ALLK_L, ALLK_S, LONG_LV, SHORT_LV

OUT = Path("docs/scalp/charts"); OUT.mkdir(parents=True, exist_ok=True)


def main():
    sym = sys.argv[1] if len(sys.argv) > 1 else "SOLUSDT"
    df, _ = SC.run_sc3(sym); s = SC.load(sym, 5)
    idx = {int(t): k for k, t in enumerate(s.ts)}
    c = SC.SC3[sym]
    # buscar un long ganador con confluencia>=2 y vr alto, que cerró en target
    best = None
    for r in df.itertuples():
        if r.side != "long" or r.r < 1.3: continue   # R alto = llegó al target
        i = idx.get(int(r.ts));
        if i is None or i < 30: continue
        allk = set(list(LONG_LV.values())+list(SHORT_LV.values()))
        conf = sum(1 for k in allk if np.isfinite(getattr(s, k)[i]) and abs(getattr(s, k)[i]-r.entry) <= c["tol_atr"]*s.atr[i])
        if conf >= 2 and s.vr[i] >= 2.0:
            best = (r, i, conf); break
    if best is None:
        print("no encontré ejemplo limpio"); return
    r, i, conf = best
    fpd = s.fp_delta[i]; vol = s.fp_vol[i]
    target = r.tp2; stopPct = 100*r.risk/r.entry; tp1 = r.entry + 0.5*(target-r.entry)
    # qué nivel VP es la entrada
    lvtype = min(((k, abs(getattr(s, LONG_LV[k])[i]-r.entry)) for k in LONG_LV
                  if np.isfinite(getattr(s, LONG_LV[k])[i])), key=lambda x: x[1])[0]
    NAME = {"val":"VAL (Value Area Low)","poc":"POC","pdl":"PDH/PDL día previo","wl":"Weekly Low","swl":"Swing Low"}

    print("="*70)
    print(f"  TRADE EJEMPLO — {sym[:3]} LONG  ({pd.to_datetime(r.ts,unit='ms',utc=True):%Y-%m-%d %H:%M} UTC)")
    print("="*70)
    print(f"\n  CAPA 1 · NIVEL (dónde): entrada en {NAME.get(lvtype,lvtype)} = {r.entry:.4f}")
    print(f"           confluencia = {conf} niveles juntos en la zona (calidad alta)")
    print(f"\n  CAPA 2 · FOOTPRINT (qué pasó en el flujo):")
    print(f"           volumen de la barra (vr) = {s.vr[i]:.2f}× la media → actividad alta")
    print(f"           delta footprint = {fpd:+.1f}  ({100*fpd/vol:+.0f}% del volumen) → venta agresora")
    print(f"\n  CAPA 3 · DISPARADOR (absorción): venta agresora fuerte PERO el precio aguantó")
    print(f"           low de la barra {s.l[i]:.4f} tocó el nivel y cerró arriba ({s.c[i]:.4f})")
    print(f"           → los vendedores fueron ABSORBIDOS → fade long")
    print(f"\n  CAPA 4 · FILTROS: ATR {s.atr[i]:.4f} > mediana(500) ✓ · ≤3/día ✓ · RR ✓")
    print(f"\n  CAPA 5 · ENTRADA (maker): límite post-only en {r.entry:.4f} (pasivo, +rebate)")
    print(f"\n  CAPA 6 · GESTIÓN (fade): stop {r.stop:.4f} (−{stopPct:.2f}%) ·"
          f" parcial 50% a medio camino → BE · target {target:.4f}")
    print(f"\n  CAPA 7 · SALIDA: TARGET (R alto) → resultado {r.r:+.2f}R  (${r.r*5:+.2f} con $5 riesgo)")
    print("="*70)

    # gráfico anotado: zoom ~5h alrededor
    w = 30  # barras M5 a cada lado
    lo, hi = max(0, i-w), min(s.n, i+w*2)
    x = pd.to_datetime(s.ts[lo:hi], unit="ms")
    fig, ax = plt.subplots(figsize=(15, 7))
    # velas simples (high-low + open-close)
    for k in range(lo, hi):
        xk = pd.to_datetime(s.ts[k], unit="ms")
        up = s.c[k] >= s.o[k]; col = "#16a34a" if up else "#dc2626"
        ax.plot([xk, xk], [s.l[k], s.h[k]], color=col, lw=0.7, alpha=0.6)
        ax.plot([xk, xk], [s.o[k], s.c[k]], color=col, lw=2.4, alpha=0.85)
    xe = pd.to_datetime(r.ts, unit="ms")
    ax.axhline(r.entry, color="#2563eb", ls="-", lw=1.2, alpha=0.7)
    ax.axhline(r.stop, color="#dc2626", ls="--", lw=1.2, alpha=0.7)
    ax.axhline(target, color="#16a34a", ls="--", lw=1.2, alpha=0.7)
    ax.axhline(tp1, color="#16a34a", ls=":", lw=1.0, alpha=0.5)
    ax.scatter(xe, r.entry, marker="^", s=200, color="#2563eb", edgecolor="black", lw=0.8, zorder=5)
    xt = x.max()
    ax.text(xt, r.entry, f"  ENTRADA maker {r.entry:.3f}", color="#2563eb", va="center", fontsize=9, fontweight="bold")
    ax.text(xt, r.stop, f"  STOP {r.stop:.3f}", color="#dc2626", va="center", fontsize=9)
    ax.text(xt, target, f"  TARGET {target:.3f} (+{r.r:.1f}R)", color="#16a34a", va="center", fontsize=9, fontweight="bold")
    ax.text(xt, tp1, f"  TP1 parcial 50%", color="#16a34a", va="center", fontsize=8, alpha=0.7)
    ax.set_title(f"{sym[:3]} sc3 — anatomía de UN trade · {NAME.get(lvtype,lvtype)} · vr {s.vr[i]:.1f}× · "
                 f"absorción venta → target {r.r:+.1f}R")
    ax.grid(alpha=0.15); fig.autofmt_xdate(); fig.tight_layout()
    p = OUT / f"{sym[:3].lower()}_anatomia_trade.png"
    fig.savefig(p, dpi=120); plt.close(fig)
    print(f"\n  gráfico anotado → {p}")


if __name__ == "__main__":
    main()
