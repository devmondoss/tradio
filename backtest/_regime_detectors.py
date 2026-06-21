"""
_regime_detectors.py — Detectores de régimen PROBADOS (tendencia vs rango), causales
====================================================================================
Reemplazan la columna 'regime' tosca (94% Chop). Basados en literatura (Kaufman, Wilder):
  • Efficiency Ratio (Kaufman): |Δneto|/Σ|Δ| ∈ [0,1]. Alto = tendencia eficiente.
  • ADX (Wilder): fuerza de tendencia. >25 = tendencia.
  • Choppiness Index: <38.2 = tendencia, >61.8 = rango.
Todos SHIFTED 1 barra (causal: el régimen se conoce ANTES de decidir la entrada).
"""
import numpy as np


def _wilder(x, n):
    out = np.full(len(x), np.nan)
    x = np.nan_to_num(x)
    if len(x) < n: return out
    out[n-1] = x[:n].mean()
    for i in range(n, len(x)):
        out[i] = (out[i-1]*(n-1) + x[i])/n
    return out


def efficiency_ratio(c, N=10):
    """Kaufman ER: cambio neto / suma de movimientos absolutos. ∈[0,1], alto=tendencia."""
    er = np.full(len(c), np.nan)
    absdiff = np.abs(np.diff(c, prepend=c[0]))
    for i in range(N, len(c)):
        vol = absdiff[i-N+1:i+1].sum()
        er[i] = abs(c[i]-c[i-N])/vol if vol > 0 else 0.0
    return er


def adx(h, l, c, n=14):
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h-l, np.maximum(np.abs(h-pc), np.abs(l-pc)))
    up = h - np.roll(h, 1); dn = np.roll(l, 1) - l
    up[0] = 0; dn[0] = 0
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    atr = _wilder(tr, n)
    pdi = 100*_wilder(plus_dm, n)/(atr+1e-9)
    mdi = 100*_wilder(minus_dm, n)/(atr+1e-9)
    dx = 100*np.abs(pdi-mdi)/(pdi+mdi+1e-9)
    return _wilder(dx, n)


def choppiness(h, l, c, n=14):
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h-l, np.maximum(np.abs(h-pc), np.abs(l-pc)))
    chop = np.full(len(c), np.nan)
    for i in range(n, len(c)):
        atrsum = tr[i-n+1:i+1].sum()
        rng = h[i-n+1:i+1].max() - l[i-n+1:i+1].min()
        if rng > 0 and atrsum > 0:
            chop[i] = 100*np.log10(atrsum/rng)/np.log10(n)
    return chop


def shift1(x):
    out = np.roll(x, 1); out[0] = np.nan
    return out
