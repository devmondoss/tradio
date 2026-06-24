"""
_lab_candle_verify.py — ¿el filtro "vela grande al tocar" es MESETA o PICO (ruido)?
==================================================================================
Barre el umbral del tamaño de vela (i-1)/ATR y, por activo, reporta OOS avgR/n/WR
+ la cuota de netR del MEJOR trade (dependencia de home-runs). Si mejora sostenido
en un rango de umbrales y no vive de 1 trade → real. Si solo pica en uno → ruido.
"""
import sys; from pathlib import Path
import numpy as np
sys.path.insert(0, "backtest")
import featurelab as FL

THRS = [1.0, 1.2, 1.5, 1.8, 2.0, 2.5]

for sym in FL.ASSETS:
    if not Path(FL.ASSETS[sym]).exists(): continue
    a, _ = FL.load(sym); base = FL.base_trades(sym)
    rng = np.array([(a.h[int(t.bar)-1]-a.l[int(t.bar)-1])/a.atr[int(t.bar)-1]
                    if int(t.bar) >= 1 and a.atr[int(t.bar)-1] > 0 else 0.0 for t in base.itertuples()])
    base = base.assign(csz=rng)
    print(f"\n══ {sym} ══  (base OOS avgR {base[base.oos].r.mean():+.3f}, n {base.oos.sum()})")
    print(f"  {'thr':>4} {'IS n':>5} {'IS avgR':>8}   {'OOS n':>5} {'OOS avgR':>9} {'OOS WR':>7} {'top1 %netR':>11}")
    for thr in THRS:
        k = base[base.csz >= thr]; ki, ko = k[~k.oos], k[k.oos]
        if not len(ko): continue
        top = ko.r.max()/ko.r.sum()*100 if ko.r.sum() > 0 else float("nan")
        print(f"  {thr:>4} {len(ki):>5} {ki.r.mean():>+8.3f}   {len(ko):>5} {ko.r.mean():>+9.3f} "
              f"{100*(ko.r>0).mean():>6.0f}% {top:>10.0f}%")
