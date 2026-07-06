"""
_ict.py — Modelo ICT/SMC del checklist (HTF level + liquidity sweep + CISD + FVG en discount + RR).
=====================================================================================================
Setup LONG (short = espejo):
  1. HTF Key Level   : nivel de liquidez sell-side (PDL/swing_low/weekly_low/VAL/equal_low/asian_low).
  2. Liquidity Sweep : una vela barre por DEBAJO del nivel (penetra >= pen_atr*ATR) y CIERRA de vuelta
                       arriba (grab de stops).
  3. CISD            : "change in state of delivery" — el precio CIERRA por encima del OPEN de la
                       primera vela del tramo bajista que llevó al low (shift de entrega alcista).
                       Opcional: confirmación con delta>0 del footprint (orderflow real).
  4. FVG en discount : gap alcista de 3 velas (low[x+2] > high[x]) del tramo de displacement,
                       ubicado en la mitad inferior del dealing range (precio "barato").
  5. Good RR         : entry LÍMITE-MAKER dentro del FVG, stop bajo el sweep low, target estructural
                       (siguiente liquidez) — se exige RR >= min_rr.

Contrato: gen(a,i) -> [(side, lvl, stop, tp1, tp2, tag)] — compatible con _listas2.run_level_m1exit
y con el harness Nautilus (_nautilus_real.emit_signals). Detección 100% causal (usa barras <= i-1,
fill maker en barra i). Todos los umbrales son params con DECIMALES para barrer calibraciones.

Uso:  python backtest/_ict.py [BTCUSDT|ETHUSDT|SOLUSDT] [--tf 15] [--mode maker]
"""
import argparse
from pathlib import Path
import numpy as np, pandas as pd
import sys
sys.path.insert(0, str(Path(__file__).parent))
from _listas import OOS_MS, TICK_MS, atr as _atr, FEE_MAKER, FEE_TAKER
import _listas2 as L2
from _listas2 import struct_target, run_level_m1exit

ROOT = Path(__file__).parent.parent
PARQ = {"BTCUSDT": str(ROOT / "data/bybit-perp/processed/btcusdt_perp_m1.parquet"),
        "ETHUSDT": "E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet",
        "SOLUSDT": "E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"}

_COLS = ["ts_ms", "open", "high", "low", "close", "volume", "delta", "regime",
         "vp_poc", "vp_vah", "vp_val", "prev_day_high", "prev_day_low",
         "weekly_high", "weekly_low", "asian_high", "asian_low",
         "swing_high_50", "swing_low_50", "equal_high", "equal_low"]


def load_ict(path, tf_min=15, start_ms=TICK_MS):
    """Carga portable (BTC/ETH/SOL) y agrega al TF pedido. Causal."""
    df = pd.read_parquet(path, columns=_COLS).sort_values("ts_ms").reset_index(drop=True)
    df = df[df.ts_ms >= start_ms].reset_index(drop=True)
    if tf_min != 1:
        g = (df.ts_ms // (tf_min * 60_000)) * (tf_min * 60_000)
        last = ["close", "regime", "vp_poc", "vp_vah", "vp_val", "prev_day_high", "prev_day_low",
                "weekly_high", "weekly_low", "asian_high", "asian_low",
                "swing_high_50", "swing_low_50", "equal_high", "equal_low"]
        agg = {"ts_ms": ("ts_ms", "first"), "open": ("open", "first"), "high": ("high", "max"),
               "low": ("low", "min"), "volume": ("volume", "sum"), "delta": ("delta", "sum")}
        for c in last:
            agg[c] = (c, "last")
        df = df.groupby(g).agg(**agg).reset_index(drop=True)
    df["atr"] = _atr(df.high.values, df.low.values, df.close.values)
    # sesgo HTF causal: SMA larga (proxy de tendencia mayor) + EMA media
    bars_day = max(1, 1440 // tf_min)
    df["htf_sma"] = df.close.rolling(bars_day, min_periods=20).mean().shift(1)
    df["htf_sma_slow"] = df.close.rolling(bars_day * 4, min_periods=40).mean().shift(1)
    df["hour"] = ((df.ts_ms // 3_600_000) % 24).astype(int)
    return df


class A:
    """Vista de arrays (causal). Igual idea que _listas2.A2 pero lean y portable."""
    def __init__(s, t):
        s.ts = t.ts_ms.values.astype(np.int64)
        s.o = t.open.values.astype(float); s.h = t.high.values.astype(float)
        s.l = t.low.values.astype(float);  s.c = t.close.values.astype(float)
        s.atr = t.atr.values.astype(float); s.delta = t.delta.values.astype(float)
        s.n = len(s.ts); s.day = (s.ts // 86_400_000)
        s.reg = t.regime.astype(str).values
        s.htf_sma = t.htf_sma.values.astype(float)
        s.htf_sma_slow = t.htf_sma_slow.values.astype(float)
        s.hour = t.hour.values.astype(int)
        for c in ["vp_poc", "vp_vah", "vp_val", "prev_day_high", "prev_day_low",
                  "weekly_high", "weekly_low", "asian_high", "asian_low",
                  "swing_high_50", "swing_low_50", "equal_high", "equal_low"]:
            setattr(s, c, t[c].values.astype(float))


# ----------------------------------------------------------- defaults de calibración
DEFAULTS = dict(
    sweep_win=10,       # barras hacia atrás para buscar el sweep
    pen_atr=0.10,       # penetración mínima bajo el nivel (en ATR)
    leg_max=6,          # largo máximo del tramo bajista para el open del CISD
    cisd_win=6,         # barras tras el sweep para que ocurra el CISD
    use_delta=False,    # exigir delta>0 (long) en la vela CISD (footprint orderflow)
    fvg_min_atr=0.15,   # tamaño mínimo del FVG / ATR (fuerza del displacement)
    fvg_entry=0.50,     # 0=tope del gap (shallow), 1=base del gap (deep, mejor precio)
    discount=True,      # entry debe estar en la mitad inferior (long) del dealing range
    ote_lo=0.0,         # banda OTE de retroceso (0 desactiva)
    ote_hi=1.0,
    stop_buf_atr=0.25,  # stop = sweep_low - buf*ATR
    min_rr=1.5,         # RR mínimo al target lejano
    rr_target=2.0,      # target fallback (si no hay target estructural)
    max_age=8,          # barras que la orden límite "descansa" tras el CISD
    target_mode="struct",  # struct | range (liquidez opuesta) | rr (múltiplo fijo)
)


def _levels(a, i, side):
    if side == "long":
        keys = ["prev_day_low", "swing_low_50", "weekly_low", "vp_val", "equal_low", "asian_low"]
    else:
        keys = ["prev_day_high", "swing_high_50", "weekly_high", "vp_vah", "equal_high", "asian_high"]
    return [getattr(a, k)[i] for k in keys if np.isfinite(getattr(a, k)[i])]


def _detect(a, i, side, P):
    """Devuelve (side, entry, stop, tp1, tp2, tag) o None. Sólo usa barras <= i-1."""
    if i < P["sweep_win"] + P["cisd_win"] + 4 or a.atr[i] <= 0:
        return None
    atr_i = a.atr[i]
    lo_b = max(1, i - P["sweep_win"] - P["cisd_win"])

    # 1+2) buscar el sweep más reciente (barra s en [lo_b, i-1]).
    # Se testea CADA nivel individualmente (simétrico long/short): basta que UNA
    # bolsa de liquidez sea barrida (mecha más allá + cierre de vuelta).
    best = None
    pen = P["pen_atr"] * atr_i
    for s in range(i - 1, lo_b - 1, -1):
        for L in _levels(a, s, side):
            if side == "long" and a.l[s] < L - pen and a.c[s] > L:
                best = (s, a.l[s]); break
            if side == "short" and a.h[s] > L + pen and a.c[s] < L:
                best = (s, a.h[s]); break
        if best is not None:
            break
    if best is None:
        return None
    s, sweep_px = best

    # 3) CISD: cierre más allá del open de la 1ª vela del tramo contra-tendencia
    if side == "long":
        opens = [a.o[k] for k in range(max(0, s - P["leg_max"]), s + 1) if a.c[k] < a.o[k]]
        leg_open = max(opens) if opens else a.h[s]
    else:
        opens = [a.o[k] for k in range(max(0, s - P["leg_max"]), s + 1) if a.c[k] > a.o[k]]
        leg_open = min(opens) if opens else a.l[s]
    m = None
    for k in range(s + 1, min(s + 1 + P["cisd_win"], i)):
        if side == "long":
            ok = a.c[k] > leg_open and (a.delta[k] > 0 if P["use_delta"] else True)
        else:
            ok = a.c[k] < leg_open and (a.delta[k] < 0 if P["use_delta"] else True)
        if ok:
            m = k
            break
    if m is None or (i - m) > P["max_age"]:
        return None

    # 4) FVG del displacement (triple x,x+1,x+2 con x+1 >= m-1, x+2 <= i-1)
    gap = None
    for x in range(max(s, m - 2), i - 2):
        x2 = x + 2
        if x2 > i - 1:
            break
        if x + 1 < m - 1:
            continue
        if side == "long" and a.l[x2] > a.h[x]:
            sz = a.l[x2] - a.h[x]
            if sz >= P["fvg_min_atr"] * atr_i:
                gap = (a.h[x], a.l[x2]); break
        if side == "short" and a.h[x2] < a.l[x]:
            sz = a.l[x] - a.h[x2]
            if sz >= P["fvg_min_atr"] * atr_i:
                gap = (a.h[x2], a.l[x]); break
    if gap is None:
        return None
    g_lo, g_hi = gap  # g_lo < g_hi siempre

    # dealing range del tramo (sweep -> high/low del impulso)
    rng_hi = float(np.max(a.h[s:i]))
    rng_lo = float(np.min(a.l[s:i]))
    if rng_hi <= rng_lo:
        return None
    eq = (rng_hi + rng_lo) / 2.0

    # entry dentro del FVG: fvg_entry=0 -> tope (fácil fill), 1 -> base (mejor precio)
    if side == "long":
        entry = g_hi - P["fvg_entry"] * (g_hi - g_lo)
        if P["discount"] and entry > eq:
            return None
        retr = (rng_hi - entry) / (rng_hi - rng_lo)
    else:
        entry = g_lo + P["fvg_entry"] * (g_hi - g_lo)
        if P["discount"] and entry < eq:
            return None
        retr = (entry - rng_lo) / (rng_hi - rng_lo)
    if not (P["ote_lo"] <= retr <= P["ote_hi"]):
        return None

    # 5) stop bajo/sobre el sweep (con stop floor anti risk~0) + target por modo
    floor = P.get("stop_floor_pct", 0.15) / 100.0 * entry
    tm = P.get("target_mode", "struct")  # struct | range | rr
    if side == "long":
        stop = sweep_px - P["stop_buf_atr"] * atr_i
        if entry - stop < floor:
            stop = entry - floor
        if not (stop < entry):
            return None
        risk = entry - stop
        if tm == "struct":
            tp1, tp2 = struct_target(a, i, "long", entry)
        elif tm == "range":   # liquidez opuesta = el high que formó el tramo (draw on liquidity)
            tp2 = rng_hi if rng_hi > entry * 1.001 else np.nan
            tp1 = entry + 0.5 * (tp2 - entry) if np.isfinite(tp2) else np.nan
        else:                 # rr fijo
            tp2 = entry + P["rr_target"] * risk; tp1 = entry + 1.0 * risk
        if not np.isfinite(tp2):
            tp2 = entry + P["rr_target"] * risk; tp1 = entry + 1.0 * risk
        rr = (tp2 - entry) / risk
    else:
        stop = sweep_px + P["stop_buf_atr"] * atr_i
        if stop - entry < floor:
            stop = entry + floor
        if not (stop > entry):
            return None
        risk = stop - entry
        if tm == "struct":
            tp1, tp2 = struct_target(a, i, "short", entry)
        elif tm == "range":
            tp2 = rng_lo if rng_lo < entry * 0.999 else np.nan
            tp1 = entry - 0.5 * (entry - tp2) if np.isfinite(tp2) else np.nan
        else:
            tp2 = entry - P["rr_target"] * risk; tp1 = entry - 1.0 * risk
        if not np.isfinite(tp2):
            tp2 = entry - P["rr_target"] * risk; tp1 = entry - 1.0 * risk
        rr = (entry - tp2) / risk
    if rr < P["min_rr"]:
        return None
    return (side, float(entry), float(stop), (float(tp1) if tp1 is not None and np.isfinite(tp1) else None),
            float(tp2), "ICT")


def gen_ict(sides=("long", "short"), **kw):
    """Factory del generador ICT. kw sobreescribe DEFAULTS."""
    P = dict(DEFAULTS); P.update(kw)

    def g(a, i):
        out = []
        for side in sides:
            r = _detect(a, i, side, P)
            if r is not None:
                out.append(r)
        return out
    return g


# ----------------------------------------------------------- stats / sweep helpers
def stats(trades):
    if not trades:
        return dict(n=0)
    df = pd.DataFrame(trades)
    r = df.r.values
    do = df[df.oos]
    eq = np.cumsum(r); peak = np.maximum.accumulate(eq); dd = float(np.max(peak - eq)) if len(eq) else 0.0
    sh = float(r.mean() / (r.std() + 1e-9) * np.sqrt(len(r))) if len(r) > 1 else 0.0
    return dict(n=len(df), avgR=float(r.mean()), wr=float(100 * (r > 0).mean()),
                netR=float(r.sum()), dd=dd, sharpe=sh,
                oosN=int(len(do)), oosA=float(do.r.mean()) if len(do) else float("nan"),
                oosWR=float(100 * (do.r > 0).mean()) if len(do) else float("nan"),
                isA=float(df[~df.oos].r.mean()) if (~df.oos).any() else float("nan"))


def load_symbol(symbol, tf_min=15):
    """Carga (a, m1) una sola vez para reusar en barridos."""
    L2.M1 = PARQ[symbol]
    a = A(load_ict(PARQ[symbol], tf_min))
    m1 = L2.load_m1_exit()
    return a, m1


def run_on(a, m1, P, tf_min=15, mode="maker", timeout_min=480, sides=("long", "short")):
    """Corre un config sobre datos ya cargados (no relee parquet)."""
    gen = gen_ict(sides=sides, **P)
    tr = run_level_m1exit(a, gen, m1, timeout_min, mode, tf_min,
                          cooldown=P.get("cooldown", 6), max_day=P.get("max_day", 3),
                          min_rr=P.get("min_rr", 1.5))
    return stats(tr), tr


def _is_chop(reg):
    return str(reg).lower() in ("chop", "range", "balance", "consolidation")


def _bias_ok(a, i, side, mode):
    """Sesgo HTF causal. 'sma': precio vs SMA diaria. 'slope': SMA sube/baja. None: sin gate."""
    if not mode:
        return True
    sma = a.htf_sma[i]
    if not np.isfinite(sma):
        return False
    if mode == "sma":
        return a.c[i - 1] > sma if side == "long" else a.c[i - 1] < sma
    if mode == "slope":
        ss = a.htf_sma_slow[i]
        if not np.isfinite(ss):
            return False
        return sma > ss if side == "long" else sma < ss
    if mode == "both":
        ss = a.htf_sma_slow[i]
        if not np.isfinite(ss):
            return False
        return (a.c[i - 1] > sma and sma > ss) if side == "long" else (a.c[i - 1] < sma and sma < ss)
    return True


def run_fast(a, m1, P, tf_min=15, mode="maker", timeout_min=480, sides=("long", "short"),
             volfilter=True, atr_mult=1.0, atr_win=500, trail_atr=6.0, route=True,
             cooldown=6, max_day=3, fill_margin_bps=2.0, bias=None, kz=None):
    """Runner FIEL a la metodología del proyecto: filtro ATR (palanca #1) + routing
    fade(parcial→BE→tp2) en chop / trail(ATR×k) en tendencia (palanca #2), salida M1,
    fill maker post-only. Causal. Devuelve trades con ts/oos para split IS/OOS."""
    m1ts, m1h, m1l, m1c = m1
    maker = (mode == "maker"); fee = FEE_MAKER if maker else FEE_TAKER
    atr_med = pd.Series(a.atr).rolling(atr_win, min_periods=50).median().shift(1).values
    gen = gen_ict(sides=sides, **P)
    trades = []; cool = 0; dcount = {}; bar_ms = tf_min * 60_000
    for i in range(60, a.n - 1):
        if i < cool or a.atr[i] <= 0:
            continue
        if volfilter and not (np.isfinite(atr_med[i]) and a.atr[i] > atr_mult * atr_med[i]):
            continue
        d = int(a.day[i])
        if dcount.get(d, 0) >= max_day:
            continue
        if kz is not None and not (kz[0] <= a.hour[i] < kz[1]):
            continue
        for side, entry, stop, tp1, tp2, tag in (gen(a, i) or []):
            if not np.isfinite([entry, stop, tp2]).all():
                continue
            if not _bias_ok(a, i, side, bias):
                continue
            if maker:
                ref = a.c[i - 1]
                if side == "long" and not (entry < ref):
                    continue
                if side == "short" and not (entry > ref):
                    continue
                if side == "long" and not (a.l[i] <= entry - fill_margin_bps / 1e4 * entry):
                    continue
                if side == "short" and not (a.h[i] >= entry + fill_margin_bps / 1e4 * entry):
                    continue
            else:
                entry = a.c[i]
                tp1, tp2 = struct_target(a, i, side, entry)
                if not (tp2 is not None and np.isfinite(tp2)):
                    continue
            risk = abs(entry - stop)
            if risk <= 0:
                continue
            if side == "long" and not (stop < entry < tp2):
                continue
            if side == "short" and not (tp2 < entry < stop):
                continue
            gestion = "fade" if (route and _is_chop(a.reg[i])) else ("trail" if route else "fade")
            j0 = np.searchsorted(m1ts, a.ts[i] + bar_ms)
            jend = min(np.searchsorted(m1ts, a.ts[i] + bar_ms + timeout_min * 60_000), len(m1ts))
            if jend <= j0:
                continue
            res = None
            if gestion == "trail":
                fee_r = (FEE_MAKER + FEE_TAKER) * entry / risk
                best = entry; trail = stop; atrv = a.atr[i]
                for j in range(j0, jend):
                    if side == "long":
                        best = max(best, m1h[j]); trail = max(trail, best - trail_atr * atrv)
                        if m1l[j] <= trail:
                            res = (trail - entry) / risk - fee_r; break
                    else:
                        best = min(best, m1l[j]); trail = min(trail, best + trail_atr * atrv)
                        if m1h[j] >= trail:
                            res = (entry - trail) / risk - fee_r; break
                if res is None:
                    px = m1c[jend - 1]
                    res = (((px - entry) if side == "long" else (entry - px)) / risk
                           - (FEE_MAKER + FEE_TAKER) * entry / risk)
            else:  # fade: parcial 50% tp1 → BE → tp2
                fee_r = fee * entry / risk; cur_stop = stop; realized = 0.0; rem = 1.0; filled1 = False
                p1 = 0.5 if (tp1 is not None and np.isfinite(tp1)) else 0.0
                for j in range(j0, jend):
                    if side == "long":
                        if m1l[j] <= cur_stop:
                            realized += rem * ((cur_stop - entry) / risk); res = realized - fee_r; break
                        if (not filled1) and p1 and m1h[j] >= tp1:
                            realized += p1 * ((tp1 - entry) / risk); rem -= p1; filled1 = True; cur_stop = entry
                        if m1h[j] >= tp2:
                            realized += rem * ((tp2 - entry) / risk); res = realized - fee_r; break
                    else:
                        if m1h[j] >= cur_stop:
                            realized += rem * ((entry - cur_stop) / risk); res = realized - fee_r; break
                        if (not filled1) and p1 and m1l[j] <= tp1:
                            realized += p1 * ((entry - tp1) / risk); rem -= p1; filled1 = True; cur_stop = entry
                        if m1l[j] <= tp2:
                            realized += rem * ((entry - tp2) / risk); res = realized - fee_r; break
                if res is None:
                    px = m1c[jend - 1]
                    realized += rem * (((px - entry) if side == "long" else (entry - px)) / risk)
                    res = realized - fee_r
            trades.append({"ts": int(a.ts[i]), "side": side, "r": res,
                           "oos": int(a.ts[i]) >= OOS_MS, "gestion": gestion, "tag": tag})
            cool = i + cooldown; dcount[d] = dcount.get(d, 0) + 1
            break
    return trades


def run_cfg(symbol, P, tf_min=15, mode="maker", timeout_min=480, sides=("long", "short")):
    a, m1 = load_symbol(symbol, tf_min)
    return run_on(a, m1, P, tf_min, mode, timeout_min, sides)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", nargs="?", default="BTCUSDT")
    ap.add_argument("--tf", type=int, default=15)
    ap.add_argument("--mode", default="maker")
    ap.add_argument("--timeout", type=int, default=480)
    args = ap.parse_args()
    st, tr = run_cfg(args.symbol, dict(DEFAULTS), args.tf, args.mode, args.timeout)
    print(f"== ICT {args.symbol} M{args.tf} [{args.mode}] defaults ==")
    if st["n"] == 0:
        print("  SIN TRADES"); return
    print(f"  n={st['n']} avgR={st['avgR']:+.3f} WR={st['wr']:.0f}% netR={st['netR']:+.1f} "
          f"DD={st['dd']:.1f}R Sh={st['sharpe']:+.1f}")
    print(f"  IS avgR={st['isA']:+.3f} | OOS n={st['oosN']} avgR={st['oosA']:+.3f} WR={st['oosWR']:.0f}%")


if __name__ == "__main__":
    main()
