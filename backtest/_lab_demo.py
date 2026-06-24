"""
_lab_demo.py — valida el laboratorio con controles y prueba confluencia (Paso 2).
=================================================================================
Control de calidad del lab:
  • H5 solo  → debe dar ✅ (sabemos que el order-block POC tiene edge)
  • sweep    → debe dar ❌ (ya probado muerto)
Si el lab acierta ambos, confiamos en su veredicto para lo nuevo.

Luego: confluencia (≥2 y ≥3 fuentes de nivel coinciden) como FILTRO del sistema base.
Uso: python backtest/_lab_demo.py
"""
import sys; from pathlib import Path
import numpy as np
sys.path.insert(0, "backtest")
import featurelab as FL
import _listas2 as L2


def make_sweep():
    def g(a, i):
        out = []; h, l, c, atr = a.h[i], a.l[i], a.c[i], a.atr[i]
        if atr <= 0: return out
        for lvl in (a.swing_low_50[i], a.prev_day_low[i]):
            if np.isfinite(lvl) and l < lvl and c > lvl and (lvl-l) > 0.1*atr:
                stop = l-0.25*atr; tp1, tp2 = L2.struct_target(a, i, "long", c)
                if np.isfinite(tp2) and stop < c < tp2: out.append(("long", c, stop, tp1, tp2, "sweep")); break
        for lvl in (a.swing_high_50[i], a.prev_day_high[i]):
            if np.isfinite(lvl) and h > lvl and c < lvl and (h-lvl) > 0.1*atr:
                stop = h+0.25*atr; tp1, tp2 = L2.struct_target(a, i, "short", c)
                if np.isfinite(tp2) and tp2 < c < stop: out.append(("short", c, stop, tp1, tp2, "sweep")); break
        return out
    return g


print("########## CONTROL DE CALIDAD DEL LABORATORIO ##########")
FL.signal_verdict("H5 order-block (control BUENO)", L2.gen_h5)
FL.signal_verdict("sweep (control MALO)", make_sweep)

print("\n\n########## FEATURE NUEVO: CONFLUENCIA (Paso 2) ##########")
FL.filter_verdict("confluencia >=2 fuentes", FL.confluence_filter(2))
FL.filter_verdict("confluencia >=3 fuentes", FL.confluence_filter(3))
