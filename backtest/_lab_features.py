"""
_lab_features.py — momentum de aproximación y tamaño de vela al tocar, por el lab.
==================================================================================
Ambos son FILTROS (gatean los trades del sistema A+B base). Causales: usan SOLO la
barra previa al fill (i-1) y anteriores — nada de la barra del fill (evita lookahead).

  • Tamaño de vela al llegar : range(i-1)/ATR. Chica = llegada ordenada; grande = pánico.
  • Momentum de aproximación : |c[i-1]-c[i-1-K]| / (K·ATR). Lento = exploración; rápido = caída libre.

Se barren varios umbrales (no una sola calibración). Regla dura: mejorar avgR OOS,
conservando muestra, en LOS 3 activos.
Uso: python backtest/_lab_features.py
"""
import sys; from pathlib import Path
import numpy as np
sys.path.insert(0, "backtest")
import featurelab as FL


def candle_size_filter(thr, big=False):
    """big=False: PASA llegada ORDENADA (vela <= thr·ATR). big=True: PASA PÁNICO (>= thr·ATR)."""
    def f(a, i, side, entry):
        if i < 1 or a.atr[i-1] <= 0: return False
        rng = (a.h[i-1] - a.l[i-1]) / a.atr[i-1]
        return rng >= thr if big else rng <= thr
    return f


def approach_vel_filter(thr, K=3, fast=False):
    """fast=False: PASA aproximación LENTA (vel <= thr). fast=True: PASA RÁPIDA (>= thr)."""
    def f(a, i, side, entry):
        if i < K+1 or a.atr[i-1] <= 0: return False
        vel = abs(a.c[i-1] - a.c[i-1-K]) / (K * a.atr[i-1])
        return vel >= thr if fast else vel <= thr
    return f


print("########## TAMAÑO DE VELA AL TOCAR ##########")
for thr in (0.8, 1.2):
    FL.filter_verdict(f"llegada ORDENADA (vela <= {thr}·ATR)", candle_size_filter(thr))
for thr in (1.5,):
    FL.filter_verdict(f"llegada PÁNICO (vela >= {thr}·ATR)", candle_size_filter(thr, big=True))

print("\n\n########## MOMENTUM DE APROXIMACIÓN (3 velas) ##########")
for thr in (0.5, 0.8):
    FL.filter_verdict(f"aproximación LENTA (vel <= {thr})", approach_vel_filter(thr))
for thr in (1.2,):
    FL.filter_verdict(f"aproximación RÁPIDA (vel >= {thr})", approach_vel_filter(thr, fast=True))
