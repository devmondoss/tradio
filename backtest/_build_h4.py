"""
_build_h4.py — Construye parquets H4 para ETH y SOL desde M1
=============================================================
Resamplea M1 → H4 y computa:
  - OHLCV + buy_vol + sell_vol + delta
  - ATR14 (True Range EWM span=14)
  - VP rolling (window=20 H4 bars ≈ 3.3 días, 50 bins) → vp_poc, vp_vah, vp_val
  - Swing high/low 50 barras H4
  - Prev day high/low, weekly high/low (heredados del último M1 de cada H4)
  - Regime: ATR > rolling_median(500)
  - fp_poc heredado del último M1 de cada H4 (NaN si vacío)
  - fp_absorb_buy / fp_absorb_sell → NaN (no disponible para ETH/SOL)

Salida:
  E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_h4.parquet
  E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_h4.parquet
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

CONFIGS = {
    "ETHUSDT": Path("E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet"),
    "SOLUSDT": Path("E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"),
}

VP_WINDOW  = 20    # barras H4 (≈ 3.3 días)
VP_BINS    = 50
ATR_PERIOD = 14
SWING_WIN  = 50
MED_WIN    = 500   # para regime


def compute_atr(high, low, close, period=14):
    prev_c = np.roll(close, 1); prev_c[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_c), np.abs(low - prev_c)))
    atr = pd.Series(tr).ewm(span=period, adjust=False).mean().values
    return atr


def compute_vp(close, volume, window=20, n_bins=50):
    n = len(close)
    poc = np.full(n, np.nan)
    vah = np.full(n, np.nan)
    val = np.full(n, np.nan)

    for i in range(window, n):
        wc = close[i - window: i]
        wv = volume[i - window: i]
        lo, hi = wc.min(), wc.max()
        if hi - lo < 1e-9: continue

        bins = np.linspace(lo, hi, n_bins + 1)
        hist, _ = np.histogram(wc, bins=bins, weights=wv)

        poc_idx   = int(np.argmax(hist))
        poc[i]    = (bins[poc_idx] + bins[poc_idx + 1]) / 2

        total = hist.sum()
        if total == 0: continue
        target = total * 0.70
        lo_i = hi_i = poc_idx
        acc = hist[poc_idx]
        while acc < target and (lo_i > 0 or hi_i < n_bins - 1):
            add_lo = hist[lo_i - 1] if lo_i > 0 else -1
            add_hi = hist[hi_i + 1] if hi_i < n_bins - 1 else -1
            if add_hi >= add_lo:
                hi_i += 1; acc += add_hi
            else:
                lo_i -= 1; acc += add_lo
        val[i] = (bins[lo_i] + bins[lo_i + 1]) / 2
        vah[i] = (bins[hi_i] + bins[hi_i + 1]) / 2

    return poc, vah, val


def build_h4(sym, m1_path):
    print(f"\n[{sym}] Cargando M1 de {m1_path} ...")
    m1 = pd.read_parquet(m1_path)
    m1['ts'] = pd.to_datetime(m1['ts_ms'], unit='ms', utc=True)
    m1 = m1.set_index('ts').sort_index()

    print(f"  M1 rows: {len(m1):,}  ({m1.index[0]} → {m1.index[-1]})")

    # ── 1. Resample a H4 ─────────────────────────────────────────────────────
    agg = {
        'open':           'first',
        'high':           'max',
        'low':            'min',
        'close':          'last',
        'volume':         'sum',
        'buy_vol':        'sum',
        'sell_vol':       'sum',
        'delta':          'sum',
        # heredar último valor del período
        'prev_day_high':  'last',
        'prev_day_low':   'last',
        'weekly_high':    'last',
        'weekly_low':     'last',
        'fp_poc':         'last',
    }
    # solo agregar columnas que existen
    agg = {k: v for k, v in agg.items() if k in m1.columns}
    h4 = m1.resample('4h').agg(agg).dropna(subset=['open'])

    print(f"  H4 bars: {len(h4):,}")

    h4 = h4.reset_index()
    h4['ts_ms'] = h4['ts'].astype(np.int64) // 1_000_000

    # ── 2. ATR14 ─────────────────────────────────────────────────────────────
    h4['atr14'] = compute_atr(h4['high'].values, h4['low'].values,
                               h4['close'].values, ATR_PERIOD)

    # ── 3. Volume Profile ────────────────────────────────────────────────────
    poc, vah, val = compute_vp(h4['close'].values, h4['volume'].values,
                                VP_WINDOW, VP_BINS)
    h4['vp_poc'] = poc
    h4['vp_vah'] = vah
    h4['vp_val'] = val

    # ── 4. Swing high/low 50 ────────────────────────────────────────────────
    h4['swing_high_50'] = h4['high'].rolling(SWING_WIN, min_periods=1).max().shift(1).values
    h4['swing_low_50']  = h4['low'].rolling(SWING_WIN, min_periods=1).min().shift(1).values

    # ── 5. Regime ────────────────────────────────────────────────────────────
    atr_med = pd.Series(h4['atr14'].values).rolling(MED_WIN, min_periods=50).median().shift(1).values
    h4['regime'] = np.where(h4['atr14'].values > atr_med, 'trend', 'chop')

    # ── 6. fp_absorb (NaN — no disponible) ──────────────────────────────────
    h4['fp_absorb_buy']  = np.nan
    h4['fp_absorb_sell'] = np.nan

    # ── 7. Seleccionar columnas finales ──────────────────────────────────────
    cols = [
        'ts_ms', 'open', 'high', 'low', 'close', 'volume',
        'buy_vol', 'sell_vol', 'delta',
        'atr14', 'regime',
        'vp_poc', 'vp_vah', 'vp_val',
        'swing_high_50', 'swing_low_50',
        'prev_day_high', 'prev_day_low',
        'weekly_high', 'weekly_low',
        'fp_poc', 'fp_absorb_buy', 'fp_absorb_sell',
    ]
    cols = [c for c in cols if c in h4.columns]
    h4 = h4[cols].copy()

    # ── 8. Guardar ───────────────────────────────────────────────────────────
    out = m1_path.parent / m1_path.name.replace('m1', 'h4')
    h4.to_parquet(out, index=False)
    size_mb = out.stat().st_size / 1e6
    print(f"  Guardado → {out}  ({size_mb:.1f} MB, {len(h4):,} barras)")
    print(f"  VP no-nulo: {h4['vp_poc'].notna().mean()*100:.1f}%")
    print(f"  ATR rango: {h4['atr14'].min():.4f} – {h4['atr14'].max():.4f}")
    return h4


if __name__ == "__main__":
    for sym, path in CONFIGS.items():
        if not path.exists():
            print(f"[{sym}] No encontrado: {path}")
            continue
        build_h4(sym, path)
    print("\nListo.")
