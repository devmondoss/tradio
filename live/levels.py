"""
levels.py — Niveles de la estrategia de liquidez (CONFIG FINAL: M15 · estructural · POC)
=========================================================================================
Paridad con el backtest final (backtest/liquidity_app_backtest.py + _listas2.py):
  • 2 componentes POC: POC del order-block (largo/corto) + POC defendido (largo)
  • TARGET ESTRUCTURAL = siguiente nivel de liquidez real (VAH/VAL/swing/PDH-PDL), no múltiplo fijo
  • Filtro de VOLATILIDAD (ATR > mediana móvil) — etiqueta vol_regime; high_vol_only para despliegue
  • Stop estructural (OB high/low ±0.25·ATR ; POC defendido −0.6·ATR) · min_RR 1.2

Entrada: DataFrame de velas del TF de decisión (M15), columnas ts_ms/open/high/low/close/volume.
Salida: lista de órdenes límite candidatas AHORA, cada una con tp1 (parcial POC) y tp2 (estructural).
NOTA: en vivo no hay footprint/tick → fp_poc se aproxima con el VP por bins de cierre y el OB con la
vela de mayor rango reciente. El nivel de ENTRADA (lo que decide el fill ratio) es fiel; el target/stop
son la mejor aproximación con klines.
"""
import numpy as np
import pandas as pd

BIN = 5.0          # $ por nivel del volume profile
VA_BARS = 96       # ventana para el área de valor (96×M15 ≈ 1 día)
SWING = 50         # lookback swing high/low
OB_WIN = 15        # ventana para detectar el order block (vela de mayor rango)
MIN_RR = 1.2

def _atr(h, l, c, n=14):
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h-l, np.maximum(np.abs(h-pc), np.abs(l-pc)))
    out = np.full(len(tr), np.nan)
    if len(tr) >= n:
        out[n-1] = np.nanmean(tr[:n]); k = 1.0/n
        for i in range(n, len(tr)): out[i] = out[i-1]*(1-k)+tr[i]*k
    return out

def _vp(price, vol):
    """POC / VAH / VAL (área de valor 70%) por bins de precio ponderados por volumen."""
    if len(price) == 0: return None
    lo = np.floor(price.min()/BIN)*BIN
    idx = ((price-lo)//BIN).astype(int)
    vol = np.bincount(idx, weights=vol)
    levels = lo + np.arange(len(vol))*BIN
    poc = levels[vol.argmax()]
    order = np.argsort(vol)[::-1]; tot = vol.sum(); cum = 0; sel = []
    for k in order:
        sel.append(k); cum += vol[k]
        if cum >= 0.70*tot: break
    va = levels[sel]
    return dict(poc=float(poc), vah=float(va.max()), val=float(va.min()))

def compute_levels(m15: pd.DataFrame, high_vol_only=False, vol_window=500):
    if len(m15) < max(300, VA_BARS+SWING): return []
    df = m15.copy()
    df["date"] = df.ts_ms // 86_400_000
    h, l, c, v = df.high.values, df.low.values, df.close.values, df.volume.values
    atr = _atr(h, l, c); a = atr[-1]; price = c[-1]
    if not np.isfinite(a) or a <= 0: return []

    # --- régimen de volatilidad (causal: mediana sin la barra actual) ---
    hist = atr[~np.isnan(atr)][-(vol_window+1):-1]
    atr_med = float(np.median(hist)) if len(hist) >= 50 else np.nan
    vol_regime = "high" if (np.isfinite(atr_med) and a > atr_med) else "low"
    if high_vol_only and vol_regime != "high": return []

    # --- niveles estructurales (área de valor reciente, swing, día previo) ---
    va = _vp(c[-VA_BARS:], v[-VA_BARS:])
    if va is None: return []
    sh = float(np.max(h[-SWING-1:-1])); sl = float(np.min(l[-SWING-1:-1]))
    days = sorted(df.date.unique())
    prev = df[df.date == days[-2]] if len(days) >= 2 else df.iloc[0:0]
    pdh = float(prev.high.max()) if len(prev) else np.nan
    pdl = float(prev.low.min()) if len(prev) else np.nan

    def struct_target(side, entry):
        if side == "long":
            cands = [x for x in (va["vah"], sh, pdh) if np.isfinite(x) and x > entry*1.001]
            tp2 = min(cands) if cands else np.nan
            tp1 = va["poc"] if (entry < va["poc"] < tp2) else None
        else:
            cands = [x for x in (va["val"], sl, pdl) if np.isfinite(x) and x < entry*0.999]
            tp2 = max(cands) if cands else np.nan
            tp1 = va["poc"] if (tp2 < va["poc"] < entry) else None
        return tp1, tp2

    out = []
    # --- POC del Order Block (vela de mayor rango reciente) ---
    win = df.tail(OB_WIN)
    obi = (win.high - win.low).values.argmax()
    obh, obl = float(win.high.values[obi]), float(win.low.values[obi])
    obpoc = (obh + obl) / 2.0
    for side, stop in (("long", obl-0.25*a), ("short", obh+0.25*a)):
        tp1, tp2 = struct_target(side, obpoc)
        if np.isfinite(tp2):
            out.append(dict(side=side, kind="poc_orderblock", price=obpoc, stop=stop, tp1=tp1, tp=tp2))
    # --- POC defendido (nodo de alto volumen tocado por mínimos recientes) ---
    defended = va["poc"]
    touches = int(np.sum(np.abs(l[-OB_WIN:] - defended)/defended <= 0.002))
    if touches >= 2:
        tp1, tp2 = struct_target("long", defended)
        if np.isfinite(tp2):
            out.append(dict(side="long", kind="poc_defendido", price=defended,
                            stop=defended-0.6*a, tp1=tp1, tp=tp2))

    # --- filtros finales: lado correcto del mercado + min_RR + etiqueta de régimen ---
    valid = []
    for o in out:
        risk = abs(o["price"] - o["stop"])
        if risk <= 0 or abs(o["tp"] - o["price"])/risk < MIN_RR: continue
        if o["side"] == "long" and not (o["price"] < price and o["stop"] < o["price"] < o["tp"]): continue
        if o["side"] == "short" and not (o["price"] > price and o["tp"] < o["price"] < o["stop"]): continue
        o["vol_regime"] = vol_regime
        valid.append(o)
    return valid
