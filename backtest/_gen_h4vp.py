"""
_gen_h4vp.py — Generador de señales en niveles VP de H4
==========================================================
Lógica: igual que gen_h21 pero en temporalidad H4.
  - VP POC de H4 defendido (≥2 toques en ventana K barras H4)
  - Señal en barra M15 cuando precio toca el nivel H4 VP POC
  - Stop y targets calculados en M15 (mismo executor que el resto)

Testeo via featurelab.signal_verdict (regla dura IS+OOS en los 3 activos).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import numpy as np
import pandas as pd
import featurelab as FL
import _listas2 as L2

H4_PATHS = {
    'BTCUSDT': Path('data/bybit-perp/processed/btcusdt_perp_h4.parquet'),
    'ETHUSDT': Path('E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_h4.parquet'),
    'SOLUSDT': Path('E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_h4.parquet'),
}


def _infer_sym(a):
    p = float(np.nanmedian(a.c[:100]))
    if p > 10_000: return 'BTCUSDT'
    elif p > 500:  return 'ETHUSDT'
    else:          return 'SOLUSDT'


def _load_h4(sym):
    path = H4_PATHS[sym]
    if not path.exists():
        raise FileNotFoundError(f"H4 parquet no encontrado: {path}")
    df = pd.read_parquet(path)
    return df['ts_ms'].values, df['vp_poc'].values, df['vp_vah'].values, df['vp_val'].values


def gen_h4vp(K=10, tol=0.004):
    """
    K: ventana H4 para contar toques (K=10 → 40h, ~1.7 días)
    tol: tolerancia precio al nivel (0.4% — H4 tiene barras más grandes)
    """
    _state = {}

    def g(a, i):
        if i < 4: return []

        if 'done' not in _state:
            sym = _infer_sym(a)
            ts_h4, poc_h4, vah_h4, val_h4 = _load_h4(sym)
            # Para cada barra M15 (índice i), pre-calcular el índice H4 correspondiente
            # ts de A2 son ms desde epoch
            m15_ts = a.ts  # array de ms
            # índice H4 para cada barra M15: el H4 bar más reciente
            h4_idx = np.searchsorted(ts_h4, m15_ts, side='right') - 1
            h4_idx = np.clip(h4_idx, 0, len(ts_h4) - 1)
            _state.update({
                'done': True, 'sym': sym,
                'ts_h4': ts_h4, 'poc_h4': poc_h4,
                'vah_h4': vah_h4, 'val_h4': val_h4,
                'h4_idx': h4_idx,
            })

        h4_idx = _state['h4_idx']
        poc_h4 = _state['poc_h4']
        vah_h4 = _state['vah_h4']
        val_h4 = _state['val_h4']

        j = int(h4_idx[i])    # índice H4 del bar M15 actual
        if j < K + 1: return []

        out = []

        # ── Buscar nivel H4 defendido en ventana K barras H4 ──────────────────
        # Usamos el POC del H4 bar actual como nivel candidato
        lvl = poc_h4[j]
        if not np.isfinite(lvl): return []

        # Contar toques (low near lvl) en las últimas K barras H4
        window_poc = poc_h4[j - K: j]
        window_poc = window_poc[np.isfinite(window_poc)]
        if len(window_poc) < 3: return []
        lvl_win = np.median(window_poc)

        # Toques en los lows de H4 — necesitamos los lows H4
        # Los sacamos indirectamente: buscamos barras M15 dentro de cada H4
        # Simplificación: usar a.l en ventana M15 correspondiente (~16 barras M15 = 1 H4)
        m15_per_h4 = 16
        m15_start = max(0, i - K * m15_per_h4)
        l_window = a.l[m15_start:i]
        touches_low  = np.sum(np.abs(l_window - lvl_win) / lvl_win <= tol)
        touches_high = np.sum(np.abs(a.h[m15_start:i] - lvl_win) / lvl_win <= tol)

        # ── LONG: nivel H4 POC defendido como soporte ─────────────────────────
        if (touches_low >= 2
                and a.c[i] > a.c[i-1]
                and abs(a.l[i] - lvl_win) / lvl_win <= tol):
            stop = lvl_win - 0.6 * a.atr[i]
            tp1, tp2 = L2.struct_target(a, i, "long", lvl_win)
            if np.isfinite(tp2) and tp2 > lvl_win:
                out.append(("long", lvl_win, stop, tp1, tp2, "H4VP_long"))

        # ── SHORT: nivel H4 POC como resistencia ──────────────────────────────
        if (touches_high >= 2
                and a.c[i] < a.c[i-1]
                and abs(a.h[i] - lvl_win) / lvl_win <= tol):
            stop = lvl_win + 0.6 * a.atr[i]
            tp1, tp2 = L2.struct_target(a, i, "short", lvl_win)
            if np.isfinite(tp2) and tp2 < lvl_win:
                out.append(("short", lvl_win, stop, tp1, tp2, "H4VP_short"))

        return out

    return g


if __name__ == "__main__":
    print("=== H4 VP como señal standalone ===\n")
    FL.signal_verdict("H4 VP POC K=10", gen_h4vp)
    FL.signal_verdict("H4 VP POC K=15", lambda: gen_h4vp(K=15))
    FL.signal_verdict("H4 VP POC K=20", lambda: gen_h4vp(K=20))
    FL.signal_verdict("H4 VP POC tol=0.003", lambda: gen_h4vp(tol=0.003))
    FL.signal_verdict("H4 VP POC tol=0.006", lambda: gen_h4vp(tol=0.006))
