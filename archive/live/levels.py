"""
levels.py — Niveles de MAKER y FLOW (CONFIG FINAL: M15 · estructural · POC)
=============================================================================
MAKER (System A): fade puro en TODOS los regimenes.
  Provee liquidez maker en niveles de volumen. Parcial 50% en TP1, resto a target.

FLOW (System C): fade en CHOP, ATR trail en TENDENCIA.
  Mismos generadores de entrada, gestion adaptativa al regimen.

Paridad con backtest/liquidity_app_backtest.py:
  - 3 componentes POC: OB largo/corto + POC defendido largo + mirror corto
  - Naked POC aproximado desde OHLCV (sin ticks; fiel en entrada, aprox. en POC previo)
  - Target estructural = nivel mas lejano de liquidez real (VAH/swing/PDH/WH)
  - Filtro volatilidad (ATR > mediana movil)
  - min_tp1_rr = 2.3 (parcial solo si TP1 >= 2.3 x riesgo)

Salida: lista de ordenes limite candidatas, cada una con:
  side, kind, price, stop, tp1, tp, vol_regime, regime, atr, gestion
  donde gestion = "fade" para MAKER, y "fade"|"trail" para FLOW segun regimen.
"""
import numpy as np
import pandas as pd

BIN       = 5.0    # $ por nivel del volume profile
VA_BARS   = 96     # ventana area de valor (96x15min = 1 dia)
SWING     = 50     # lookback swing high/low
OB_WIN    = 15     # ventana order block (vela mayor rango)
MIN_RR    = 1.2
MIN_TP1_RR = 2.3   # parcial solo si TP1 >= 2.3 x riesgo
TRAIL_ATR  = 4.0   # multiplicador ATR para trailing stop (FLOW tendencia)
NAKED_TOL  = 75.0  # $75 tolerancia para "POC visitado"
NAKED_LOOKBACK = 10  # dias de lookback para naked POC approximado
FP_MIN_BARS = 20   # mínimo de barras de footprint real para activar VP por ticks


def _atr(h, l, c, n=14):
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    out = np.full(len(tr), np.nan)
    if len(tr) >= n:
        out[n - 1] = np.nanmean(tr[:n]); k = 1.0 / n
        for i in range(n, len(tr)):
            out[i] = out[i - 1] * (1 - k) + tr[i] * k
    return out


def _vp_bins(lvls: np.ndarray, vols: np.ndarray) -> dict:
    """POC / VAH / VAL desde arrays ya binados (lvls=precios, vols=volúmenes)."""
    poc = float(lvls[vols.argmax()])
    order = np.argsort(vols)[::-1]; tot = vols.sum(); cum = 0.0; sel = []
    for k in order:
        sel.append(k); cum += vols[k]
        if cum >= 0.70 * tot: break
    va = lvls[sel]
    return dict(poc=poc, vah=float(va.max()), val=float(va.min()))


def _vp(price, vol):
    """POC / VAH / VAL desde OHLCV (aproximación: volumen asignado al close)."""
    if len(price) == 0: return None
    lo  = np.floor(price.min() / BIN) * BIN
    idx = ((price - lo) // BIN).astype(int)
    vols = np.bincount(idx, weights=vol).astype(float)
    lvls = lo + np.arange(len(vols)) * BIN
    return _vp_bins(lvls, vols)


def _vp_from_fp(fp_bars: list) -> dict | None:
    """VP real desde footprint tick acumulado (más preciso que OHLCV).
    fp_bars: lista de dicts devueltos por FootprintAccumulator.on_bar_close()."""
    if not fp_bars:
        return None
    combined: dict[float, float] = {}
    for bar in fp_bars:
        for p, v in zip(bar["prices"], bar["total"]):
            combined[float(p)] = combined.get(float(p), 0.0) + float(v)
    if not combined:
        return None
    lvls = np.array(sorted(combined.keys()))
    vols = np.array([combined[p] for p in lvls])
    return _vp_bins(lvls, vols)


def _regime(c, atr_series, n_sma=50):
    """Chop o tendencia. True = tendencia (FLOW usara trail)."""
    if len(c) < n_sma + 1: return False
    sma  = float(np.mean(c[-n_sma:]))
    a    = float(atr_series[-1]) if np.isfinite(atr_series[-1]) else 0.0
    dist = abs(c[-1] - sma)
    return dist > 0.6 * a   # precio alejado > 0.6 ATR de la media -> tendencia


def _naked_poc_approx(df_m15, tol=NAKED_TOL, lookback=NAKED_LOOKBACK):
    """Naked POC aproximado desde OHLCV: POC de sesion previa no revisitado.
    Usa VP de cierre ponderado por volumen por dia. Sin datos de ticks -> aproximacion."""
    days = sorted(df_m15["date"].unique())
    if len(days) < 3: return []
    naked = []
    today = days[-1]
    for i in range(max(0, len(days) - 1 - lookback), len(days) - 1):
        d_past = days[i]
        sub    = df_m15[df_m15["date"] == d_past]
        if len(sub) < 4: continue
        va = _vp(sub.close.values, sub.volume.values)
        if va is None: continue
        poc = va["poc"]
        visited = False
        for j in range(i + 1, len(days) - 1):   # dias intermedios (sin incluir hoy)
            s2 = df_m15[df_m15["date"] == days[j]]
            if len(s2) == 0: continue
            if s2.high.max() >= poc - tol and s2.low.min() <= poc + tol:
                visited = True; break
        if not visited:
            naked.append(poc)
    return sorted(set(naked))


def compute_levels(m15: pd.DataFrame, system="maker", high_vol_only=False, vol_window=500,
                   fp_bars: list | None = None):
    """Calcula niveles de entrada para MAKER o FLOW.

    system:   "maker" = fade puro. "flow" = fade en chop, trail en tendencia.
    fp_bars:  historial de footprint real (FootprintAccumulator.bars()).
              Si se pasa y tiene >= FP_MIN_BARS barras, se usa VP por ticks
              para el value area y POC real del order block.
              Si es None o insuficiente, cae al cálculo OHLCV aproximado.
    Devuelve lista de dicts con: side, kind, price, stop, tp1, tp,
                                  vol_regime, regime, atr, gestion, fp_source.
    """
    if len(m15) < max(300, VA_BARS + SWING + 50): return []
    df = m15.copy()
    df["date"] = df.ts_ms // 86_400_000
    h, l, c, v = df.high.values, df.low.values, df.close.values, df.volume.values
    atr    = _atr(h, l, c); a = float(atr[-1]); price = float(c[-1])
    if not np.isfinite(a) or a <= 0: return []

    # Regimen de volatilidad (causal: sin la barra actual)
    hist     = atr[~np.isnan(atr)][-(vol_window + 1):-1]
    atr_med  = float(np.median(hist)) if len(hist) >= 50 else np.nan
    vol_regime = "high" if (np.isfinite(atr_med) and a > atr_med) else "low"
    if high_vol_only and vol_regime != "high": return []

    # Regimen de mercado (chop vs tendencia) para FLOW
    is_trend = _regime(c, atr) if system == "flow" else False
    regime   = "trend" if is_trend else "chop"

    # Niveles estructurales — VP real (ticks) si hay suficiente historial, OHLCV si no
    use_fp = fp_bars is not None and len(fp_bars) >= FP_MIN_BARS
    fp_source = "tick" if use_fp else "ohlcv"
    if use_fp:
        va = _vp_from_fp(fp_bars[-VA_BARS:])
    if not use_fp or va is None:
        va = _vp(c[-VA_BARS:], v[-VA_BARS:])
    if va is None: return []
    sh  = float(np.max(h[-SWING - 1:-1])); sl = float(np.min(l[-SWING - 1:-1]))
    days = sorted(df["date"].unique())
    prev = df[df["date"] == days[-2]] if len(days) >= 2 else df.iloc[0:0]
    pdh  = float(prev.high.max()) if len(prev) else np.nan
    pdl  = float(prev.low.min()) if len(prev) else np.nan
    wk   = df[df["date"].isin(days[-8:-1])] if len(days) >= 2 else df.iloc[0:0]
    wh   = float(wk.high.max()) if len(wk) else np.nan
    wl   = float(wk.low.min()) if len(wk) else np.nan

    def struct_target(side, entry):
        if side == "long":
            cands = [x for x in (va["vah"], sh, pdh, wh) if np.isfinite(x) and x > entry * 1.001]
            if not cands: return None, np.nan
            tp2 = max(cands); tp1 = min(cands)
        else:
            cands = [x for x in (va["val"], sl, pdl, wl) if np.isfinite(x) and x < entry * 0.999]
            if not cands: return None, np.nan
            tp2 = min(cands); tp1 = max(cands)
        return tp1, tp2

    out = []

    # POC del Order Block (vela de mayor rango reciente)
    win  = df.tail(OB_WIN)
    obi  = (win.high - win.low).values.argmax()
    obh, obl = float(win.high.values[obi]), float(win.low.values[obi])
    # POC real del OB si tenemos el footprint de esa barra específica
    ob_ts_ms = int(win.ts_ms.values[obi])
    ob_fp = None
    if fp_bars:
        ob_fp = next((b for b in reversed(fp_bars) if b["ts_ms"] == ob_ts_ms), None)
    obpoc = ob_fp["poc"] if ob_fp else (obh + obl) / 2.0
    for side, stop in (("long", obl - 0.25 * a), ("short", obh + 0.25 * a)):
        tp1, tp2 = struct_target(side, obpoc)
        if np.isfinite(tp2):
            out.append(dict(side=side, kind="poc_ob", price=obpoc, stop=stop, tp1=tp1, tp=tp2))

    # POC defendido (nodo de alto volumen tocado >= 2 veces por minimos recientes) -> long
    defended = va["poc"]
    touches  = int(np.sum(np.abs(l[-OB_WIN:] - defended) / defended <= 0.002))
    if touches >= 2:
        tp1, tp2 = struct_target("long", defended)
        if np.isfinite(tp2):
            out.append(dict(side="long", kind="poc_def", price=defended,
                            stop=defended - 0.6 * a, tp1=tp1, tp=tp2))

    # Mirror short (resistencia defendida >= 2 veces por maximos) -> short
    touches_hi = int(np.sum(np.abs(h[-OB_WIN:] - defended) / defended <= 0.002))
    if touches_hi >= 2:
        tp1, tp2 = struct_target("short", defended)
        if np.isfinite(tp2):
            out.append(dict(side="short", kind="poc_def_short", price=defended,
                            stop=defended + 0.6 * a, tp1=tp1, tp=tp2))

    # Naked POC aproximado (solo si hay suficiente historial)
    if len(days) >= NAKED_LOOKBACK + 2:
        for poc in _naked_poc_approx(df, tol=NAKED_TOL, lookback=NAKED_LOOKBACK):
            # Long: precio por encima del naked POC (soporte historico sin visitar)
            if price > poc * 1.001:
                tp1, tp2 = struct_target("long", poc)
                if np.isfinite(tp2):
                    out.append(dict(side="long", kind="naked_poc",
                                    price=poc, stop=poc - 0.6 * a, tp1=tp1, tp=tp2))
            # Short: precio por debajo del naked POC (resistencia historica sin visitar)
            elif price < poc * 0.999:
                tp1, tp2 = struct_target("short", poc)
                if np.isfinite(tp2):
                    out.append(dict(side="short", kind="naked_poc",
                                    price=poc, stop=poc + 0.6 * a, tp1=tp1, tp=tp2))

    # Filtros finales + etiquetas
    valid = []
    for o in out:
        risk = abs(o["price"] - o["stop"])
        if risk <= 0: continue
        if abs(o["tp"] - o["price"]) / risk < MIN_RR: continue
        if o["side"] == "long"  and not (o["price"] < price and o["stop"] < o["price"] < o["tp"]): continue
        if o["side"] == "short" and not (o["price"] > price and o["tp"] < o["price"] < o["stop"]): continue
        # tp1 debe estar a >= MIN_TP1_RR x riesgo para activar la parcial
        tp1_rr = abs(o["tp1"] - o["price"]) / risk if o["tp1"] else 0.0
        take_partial = tp1_rr >= MIN_TP1_RR
        # Gestion: MAKER siempre fade; FLOW fade en chop, trail en tendencia
        gestion = "trail" if (system == "flow" and is_trend) else "fade"
        o.update(vol_regime=vol_regime, regime=regime, atr=float(a),
                 gestion=gestion, take_partial=take_partial, fp_source=fp_source)
        valid.append(o)
    return valid
