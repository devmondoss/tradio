"""
_ict_ifvg.py — Inverse Fair Value Gap (IFVG) como señal standalone
====================================================================
Definición causal (sin look-ahead):
  FVG bullish en barra j: high[j-2] < low[j]  →  zona [high[j-2], low[j]]
  FVG bearish en barra j: low[j-2]  > high[j]  →  zona [high[j], low[j-2]]

  IFVG = FVG que fue LLENADO después de crearse:
    bull IFVG: close[k] < high[j-2] para algún k > j  → se llenó desde arriba
    bear IFVG: close[k] > low[j-2]  para algún k > j  → se llenó desde abajo

  Señal (maker limit):
    Bullish IFVG (retest de ex-zona bajista llenada):
      low[i] ≤ bear_top × (1+tol)  AND  close[i] > bear_top  → LONG en bear_top
    Bearish IFVG (retest de ex-zona alcista llenada):
      high[i] ≥ bull_bot × (1-tol)  AND  close[i] < bull_bot → SHORT en bull_bot

Protocolo: featurelab.signal_verdict (STD, regla dura IS+OOS en los 3 activos).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import numpy as np
import featurelab as FL
import _listas2 as L2

# ─────────────────────────────────────────────────────────────────────────────
# Precomputación de FVGs + fill (se ejecuta una vez por activo, en primera llamada)
# ─────────────────────────────────────────────────────────────────────────────

def _precompute(a, min_gap_atr=0.15, max_fill_bars=200):
    """Detecta FVGs y para cada uno el primer bar donde fue llenado.
    min_gap_atr: tamaño mínimo del gap como fracción del ATR (filtra micro-gaps).
    max_fill_bars: máximo bars a mirar hacia adelante para detectar fill.
    """
    n = a.n
    bull_bot  = np.full(n, np.nan)   # bottom del FVG bullish (high[j-2])
    bull_top  = np.full(n, np.nan)   # top    del FVG bullish (low[j])
    bear_bot  = np.full(n, np.nan)   # bottom del FVG bearish (high[j])
    bear_top  = np.full(n, np.nan)   # top    del FVG bearish (low[j-2])
    bull_fill = np.full(n, -1, dtype=np.int32)
    bear_fill = np.full(n, -1, dtype=np.int32)

    # Paso 1 — detectar FVGs
    for j in range(2, n):
        atr_j = a.atr[j] if a.atr[j] > 0 else 1.0
        gap_b = a.l[j]   - a.h[j-2]   # gap bullish (positivo si existe)
        gap_s = a.l[j-2] - a.h[j]     # gap bearish (positivo si existe)
        if gap_b > min_gap_atr * atr_j:
            bull_bot[j] = a.h[j-2]
            bull_top[j] = a.l[j]
        if gap_s > min_gap_atr * atr_j:
            bear_bot[j] = a.h[j]
            bear_top[j] = a.l[j-2]

    # Paso 2 — para cada FVG, hallar el primer fill (vectorizado por FVG)
    fvg_bull_idxs = np.where(np.isfinite(bull_bot))[0]
    fvg_bear_idxs = np.where(np.isfinite(bear_bot))[0]

    for j in fvg_bull_idxs:
        bot = bull_bot[j]
        end = min(j + 1 + max_fill_bars, n)
        future_c = a.c[j+1:end]
        where = np.where(future_c < bot)[0]
        if len(where): bull_fill[j] = j + 1 + int(where[0])

    for j in fvg_bear_idxs:
        top = bear_top[j]
        end = min(j + 1 + max_fill_bars, n)
        future_c = a.c[j+1:end]
        where = np.where(future_c > top)[0]
        if len(where): bear_fill[j] = j + 1 + int(where[0])

    return bull_bot, bull_top, bull_fill, bear_bot, bear_top, bear_fill

# ─────────────────────────────────────────────────────────────────────────────
# Generador IFVG
# ─────────────────────────────────────────────────────────────────────────────

def gen_ifvg(K=60, min_gap_atr=0.15, tol=0.0015):
    """
    K: ventana de barras hacia atrás donde buscar IFVGs activos (K×15min = horas)
    tol: tolerancia de toque al nivel (0.15% por defecto)
    """
    _state = {}   # lazy init por instancia de generador

    def g(a, i):
        if i < K + 3: return []

        # Precomputar una sola vez al primer llamado
        if 'done' not in _state:
            (bull_bot, bull_top, bull_fill,
             bear_bot, bear_top, bear_fill) = _precompute(a, min_gap_atr)
            _state['done']      = True
            _state['bull_bot']  = bull_bot
            _state['bull_top']  = bull_top
            _state['bull_fill'] = bull_fill
            _state['bear_bot']  = bear_bot
            _state['bear_top']  = bear_top
            _state['bear_fill'] = bear_fill

        bb  = _state['bull_bot'];  bt  = _state['bull_top'];  bf = _state['bull_fill']
        srb = _state['bear_bot'];  srt = _state['bear_top'];  sf = _state['bear_fill']

        out = []

        # ── Bearish IFVG (ex bull FVG llenado → resistencia) ─────────────────
        for j in range(max(2, i - K), i - 1):
            if not np.isfinite(bb[j]): continue
            if bf[j] < 0 or bf[j] >= i: continue   # no llenado aún o llenado en el futuro
            lvl = bb[j]          # resistencia = bottom del FVG original
            top = bt[j]          # techo del FVG original
            # Señal: high[i] toca el nivel desde abajo, close[i] cierra debajo
            if a.h[i] >= lvl * (1 - tol) and a.c[i] < lvl:
                stop = top + 0.25 * a.atr[i]
                tp1, tp2 = L2.struct_target(a, i, "short", lvl)
                if np.isfinite(tp2) and tp2 < lvl:
                    out.append(("short", lvl, stop, tp1, tp2, "IFVG_bear"))
                    break   # solo el primero más reciente

        # ── Bullish IFVG (ex bear FVG llenado → soporte) ─────────────────────
        for j in range(max(2, i - K), i - 1):
            if not np.isfinite(srt[j]): continue
            if sf[j] < 0 or sf[j] >= i: continue
            lvl = srt[j]         # soporte = top del FVG bajista original
            bot = srb[j]         # suelo del FVG original
            # Señal: low[i] toca el nivel desde arriba, close[i] cierra encima
            if a.l[i] <= lvl * (1 + tol) and a.c[i] > lvl:
                stop = bot - 0.25 * a.atr[i]
                tp1, tp2 = L2.struct_target(a, i, "long", lvl)
                if np.isfinite(tp2) and tp2 > lvl:
                    out.append(("long", lvl, stop, tp1, tp2, "IFVG_bull"))
                    break

        return out

    return g

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Testeando IFVG standalone (signal_verdict)...")
    r = FL.signal_verdict("IFVG standalone K=60", gen_ifvg)

    print("\nTesteando variantes de K y min_gap:")
    for k in [40, 60, 100]:
        FL.signal_verdict(f"IFVG K={k}", lambda k=k: gen_ifvg(K=k))

    print("\nTesteando min_gap_atr:")
    for mg in [0.10, 0.20, 0.30]:
        FL.signal_verdict(f"IFVG min_gap={mg}", lambda mg=mg: gen_ifvg(min_gap_atr=mg))
