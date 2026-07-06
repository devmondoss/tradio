"""
_crt.py — CRT (Candle Range Theory) backtest en M5
====================================================
Modelo AMD sobre vela H1: el rango H1 anterior define [h1_high, h1_low].
Un sweep M5 del extremo + cierre de vuelta dentro = setup CRT.
Entry maker limitado al extremo barrido. Target estructural o extremo opuesto H1.

Filtros:
  ATR > mediana(500)     [en run_setup, siempre activo]
  VR >= vr_min           en la barra del sweep (conviccion)
  delta_confirm          delta adverso al sweep (absorbcion real)
  htf_filter             H1 EMA20 OR H4 EMA20 alineado OR vr>3 (igual que SC3)
  rr_min >= 1.2

Targets:
  struct:    VAL/VAH, swing, PDL/PDH, weekly (mismo que liquidity+SC3)
  h1_opp:    extremo opuesto del rango H1 anchor

Uso:
  python backtest/_crt.py
  python backtest/_crt.py --symbol BTCUSDT
"""
import sys, warnings, argparse
from pathlib import Path
import numpy as np, pandas as pd
import pyarrow.parquet as pq

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _scalp import load, load_m1_exit, run_setup, stats, OOS_MS, ASSETS, _load_htf

ROOT = Path(__file__).parent.parent


# ─────────────────────────────────────────────────────────────────────────────
# H1 OHLC loader (para rango CRT)
# ─────────────────────────────────────────────────────────────────────────────

def _load_h1_ohlc(symbol):
    """Devuelve (ts_ms, high, low) de barras H1. Uso: searchsorted para lookup causal."""
    if symbol == "BTCUSDT":
        h = pq.read_table(
            ROOT / "data/bybit-perp/processed/btcusdt_perp_h1.parquet",
            columns=["ts_ms", "high", "low"]
        ).to_pandas().sort_values("ts_ms").reset_index(drop=True)
    else:
        m1_path = ASSETS[symbol]["m1"]
        m1 = pq.read_table(m1_path, columns=["ts_ms", "open", "high", "low", "close"]).to_pandas()
        m1.index = pd.to_datetime(m1.ts_ms, unit="ms", utc=True)
        r = m1.resample("60min").agg({"high": "max", "low": "min"}).dropna()
        r["ts_ms"] = r.index.astype(np.int64) // 1_000_000
        h = r.reset_index(drop=True)[["ts_ms", "high", "low"]]
    return (h.ts_ms.values.astype(np.int64),
            h.high.values.astype(float),
            h.low.values.astype(float))


# ─────────────────────────────────────────────────────────────────────────────
# Generador CRT
# ─────────────────────────────────────────────────────────────────────────────

def gen_crt(symbol, vr_min=1.5, delta_confirm=False, stop_buf=0.3,
            rr_min=1.2, use_h1_target=False, htf_filter=False,
            min_sweep_atr=0.0, min_range_atr=0.0, vp_confluence=False,
            delta_sym=None, filter_50_vp=False, fvg_entry=False):
    """
    Factory de generador CRT para M5.

    Mejoras base:
      min_sweep_atr:  wick mas alla del nivel H1 >= X*ATR
      min_range_atr:  rango H1 >= X*ATR
      vp_confluence:  nivel H1 coincide con VP level
      delta_sym:      lista de simbolos donde aplicar delta_confirm

    Mejoras video (A y B):
      filter_50_vp:   si POC/VAL esta cerca del 50% del rango H1, omite el trade
                      (el precio probable no llega al extremo opuesto)
      fvg_entry:      entra en la barra de CONFIRMACION (i-1 fue sweep, i confirma)
                      en vez de en la barra del sweep mismo — stop en wick de i-1

    signal: (side, entry, stop, tp1, tp2, tag)
    """
    h1_ts, h1_h, h1_l = _load_h1_ohlc(symbol)

    if htf_filter:
        h1f_ts, h1f_c, h1f_e = _load_htf(symbol, 60)
        h4f_ts, h4f_c, h4f_e = _load_htf(symbol, 240)
    else:
        h1f_ts = h1f_c = h1f_e = None
        h4f_ts = h4f_c = h4f_e = None

    use_delta = delta_confirm and (delta_sym is None or symbol in delta_sym)
    _state = {}

    def _h1_for(j):
        """H1 high/low causal para barra j (prev H1 cerrada)."""
        idx = int(np.searchsorted(h1_ts, int(_state['ts'][j]), "right")) - 1
        if idx < 1:
            return np.nan, np.nan
        return float(h1_h[idx - 1]), float(h1_l[idx - 1])

    def _make_signal(s, i, side, entry, sweep_wick, h1_high, h1_low):
        """Construye la tupla de señal aplicando filtros 50%VP y target."""
        atr = float(s.atr[i])
        rng = h1_high - h1_low

        # A: filtro 50% VP — si hay POC/VAL cerca del 50% del rango, omitir
        if filter_50_vp and rng > 0:
            mid = (h1_high + h1_low) / 2
            tol_50 = 0.30 * rng  # dentro del 30% del punto medio = "en el medio"
            if side == "short":
                vp_near = [s.vp_poc[i], s.vp_val[i]]
            else:
                vp_near = [s.vp_poc[i], s.vp_vah[i]]
            vp_near = [v for v in vp_near if np.isfinite(v)]
            if any(abs(v - mid) <= tol_50 for v in vp_near):
                return None  # precio probable no llega al extremo opuesto

        stop = sweep_wick + stop_buf * atr if side == "short" else sweep_wick - stop_buf * atr
        if use_h1_target:
            tp2 = h1_low  if side == "short" else h1_high
            tp1 = mid = (h1_high + h1_low) / 2
        else:
            tp1, tp2 = L2.struct_target(s, i, side, entry)

        if not np.isfinite(tp2):
            return None
        if side == "short" and not (tp2 < entry < stop):
            return None
        if side == "long"  and not (stop < entry < tp2):
            return None
        risk = abs(stop - entry)
        if risk <= 0 or abs(tp2 - entry) / risk < rr_min:
            return None
        tp1_safe = tp1 if (np.isfinite(tp1) if tp1 is not None else False) else tp2
        return (side, entry, stop, tp1_safe, tp2,
                "CRT_bear" if side == "short" else "CRT_bull")

    def g(s, i):
        if i < 61:
            return []

        # Precomputa timestamps una vez (para _h1_for)
        if 'ts' not in _state:
            _state['ts'] = s.ts

        atr = float(s.atr[i])
        vr  = float(s.vr[i])
        dlt = float(s.delta[i])

        out = []

        # ── Qué barra usamos para detectar el sweep ──────────────────────
        # fvg_entry=False → detectar en barra i (entrada en el sweep mismo)
        # fvg_entry=True  → detectar en barra i-1, confirmar en i (retest/confirmación)
        bars = [(i, i)] if not fvg_entry else [(i - 1, i)]

        for sweep_bar, entry_bar in bars:
            if sweep_bar < 1:
                continue

            h1_high, h1_low = _h1_for(sweep_bar)
            if not (np.isfinite(h1_high) and np.isfinite(h1_low) and h1_high > h1_low):
                continue
            if min_range_atr > 0 and (h1_high - h1_low) < min_range_atr * atr:
                continue

            sw_hi = float(s.h[sweep_bar])
            sw_lo = float(s.l[sweep_bar])
            sw_cl = float(s.c[sweep_bar])
            sw_vr = float(s.vr[sweep_bar])
            sw_dl = float(s.delta[sweep_bar])

            if sw_vr < vr_min:
                continue

            # VP confluence: el nivel H1 debe estar cerca de VP
            if vp_confluence:
                tol_vp = 0.5 * atr

            # ── BEARISH CRT ───────────────────────────────────────────────
            sweep_bear = sw_hi > h1_high and sw_cl <= h1_high
            if sweep_bear:
                if min_sweep_atr > 0 and (sw_hi - h1_high) < min_sweep_atr * atr:
                    sweep_bear = False
                if use_delta and sw_dl >= 0:
                    sweep_bear = False
                if vp_confluence and sweep_bear:
                    vp_lvls = [s.vp_poc[entry_bar], s.vp_vah[entry_bar],
                               s.prev_day_high[entry_bar], s.weekly_high[entry_bar]]
                    if not any(abs(h1_high - v) <= tol_vp
                               for v in vp_lvls if np.isfinite(v)):
                        sweep_bear = False

            if sweep_bear:
                # Para fvg_entry: la barra de confirmación debe seguir bajando
                if fvg_entry and float(s.c[entry_bar]) > h1_high:
                    pass
                else:
                    sig = _make_signal(s, entry_bar, "short", h1_high,
                                       sw_hi, h1_high, h1_low)
                    if sig:
                        out.append(sig)

            # ── BULLISH CRT ───────────────────────────────────────────────
            sweep_bull = sw_lo < h1_low and sw_cl >= h1_low
            if sweep_bull:
                if min_sweep_atr > 0 and (h1_low - sw_lo) < min_sweep_atr * atr:
                    sweep_bull = False
                if use_delta and sw_dl <= 0:
                    sweep_bull = False
                if vp_confluence and sweep_bull:
                    vp_lvls = [s.vp_poc[entry_bar], s.vp_val[entry_bar],
                               s.prev_day_low[entry_bar], s.weekly_low[entry_bar]]
                    if not any(abs(h1_low - v) <= tol_vp
                               for v in vp_lvls if np.isfinite(v)):
                        sweep_bull = False

            if sweep_bull:
                if fvg_entry and float(s.c[entry_bar]) < h1_low:
                    pass
                else:
                    sig = _make_signal(s, entry_bar, "long", h1_low,
                                       sw_lo, h1_high, h1_low)
                    if sig:
                        out.append(sig)

        if not out:
            return []

        # ── HTF filter (H1 EMA20 OR H4 EMA20 OR vr>3) ───────────────────
        if htf_filter:
            ts_i = int(s.ts[i])
            i1 = int(np.searchsorted(h1f_ts, ts_i, "right")) - 1
            i4 = int(np.searchsorted(h4f_ts, ts_i, "right")) - 1
            if i1 < 0 or i4 < 0:
                return []
            h1_bull = h1f_c[i1] > h1f_e[i1]
            h4_bull = h4f_c[i4] > h4f_e[i4]
            filtered = []
            for sig in out:
                side   = sig[0]
                vr_now = float(s.vr[i])
                h1_ok  = (h1_bull if side == "long" else not h1_bull)
                h4_ok  = (h4_bull if side == "long" else not h4_bull)
                if h1_ok or h4_ok or vr_now > 3:
                    filtered.append(sig)
            return filtered

        return out

    return g


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

def run_crt(symbol, tf=5, vr_min=1.5, delta_confirm=False, stop_buf=0.3,
            rr_min=1.2, use_h1_target=False, htf_filter=False,
            min_sweep_atr=0.0, min_range_atr=0.0, vp_confluence=False,
            delta_sym=None, filter_50_vp=False, fvg_entry=False,
            mgmt="fade", trail_atr=4.0, timeout_min=240):
    s  = load(symbol, tf)
    m1 = load_m1_exit(symbol)
    g  = gen_crt(symbol, vr_min=vr_min, delta_confirm=delta_confirm,
                 stop_buf=stop_buf, rr_min=rr_min,
                 use_h1_target=use_h1_target, htf_filter=htf_filter,
                 min_sweep_atr=min_sweep_atr, min_range_atr=min_range_atr,
                 vp_confluence=vp_confluence, delta_sym=delta_sym,
                 filter_50_vp=filter_50_vp, fvg_entry=fvg_entry)
    df = run_setup(s, g, m1, tf,
                   entry_mode="maker", mgmt=mgmt, trail_atr=trail_atr,
                   timeout_min=timeout_min, max_day=3, cooldown=6,
                   atr_filter=True, atr_win=500)
    return df, stats(df)


def _row(label, st):
    return (f"  {label:<42} "
            f"n={st['n']:>4} ({st['n_oos']:>3} oos)  "
            f"IS={st['isA']:>+6.3f}  OOS={st['oosA']:>+6.3f}  "
            f"WR={st['wr']:>4.0f}%  DD={st['dd']:>4.1f}%  "
            f"npd={st['npd']:.2f}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

VARIANTS = [
    ("base           vr>=1.5 struct",   dict()),
    ("base           vr>=2.0 struct",   dict(vr_min=2.0)),
    ("base           vr>=3.0 struct",   dict(vr_min=3.0)),
    ("+delta         vr>=1.5 struct",   dict(delta_confirm=True)),
    ("+delta         vr>=2.0 struct",   dict(delta_confirm=True, vr_min=2.0)),
    ("h1_target      vr>=1.5",          dict(use_h1_target=True)),
    ("h1_target      vr>=1.5 +delta",   dict(use_h1_target=True, delta_confirm=True)),
    ("+htf           vr>=1.5 struct",   dict(htf_filter=True)),
    ("+htf+delta     vr>=1.5 struct",   dict(htf_filter=True, delta_confirm=True)),
    ("+htf           vr>=1.5 h1_tgt",   dict(htf_filter=True, use_h1_target=True)),
]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default=None, help="BTCUSDT|ETHUSDT|SOLUSDT (default: todos)")
    ap.add_argument("--tf", type=int, default=5)
    args = ap.parse_args()

    symbols = [args.symbol] if args.symbol else ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

    print("=" * 80)
    print("CRT — Candle Range Theory  (M5 sweep de rango H1)")
    print(f"OOS desde {pd.Timestamp(OOS_MS, unit='ms', tz='UTC').date()}")
    print("=" * 80)

    for sym in symbols:
        print(f"\n--- {sym} ---")
        for label, kw in VARIANTS:
            try:
                df, st = run_crt(sym, tf=args.tf, **kw)
                print(_row(label, st))
            except Exception as e:
                print(f"  {label:<42} ERROR: {e}")
    print()
