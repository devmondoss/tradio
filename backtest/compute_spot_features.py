"""
compute_spot_features.py
------------------------
Enriquece btcusdt_m1/m5/m15.parquet con todos los features de microestructura
derivables desde OHLCV + trades + OBI — sin look-ahead, sin OI, sin funding.

Uso:
    python backtest/compute_spot_features.py --symbol BTCUSDT_M5 --tf m5

Features añadidos:
    Sesión / contexto temporal (ICT killzone timing)
        session         : Asia / London_KZ / London / NY_KZ / NewYork / OffHours
                          Asia=00:00-02:00  London_KZ=02:00-05:00  London=05:00-07:00
                          NY_KZ=07:00-10:00  NewYork=10:00-20:00  OffHours=20:00-24:00
        asian_high      : máximo sesión Asia (00:00-02:00 UTC) del mismo día
        asian_low       : mínimo sesión Asia del mismo día
        prev_day_high   : PDH — máximo del día anterior
        prev_day_low    : PDL — mínimo del día anterior

    Tendencia / estructura
        ema20           : EMA20 de cierres M1 (contexto de tendencia rápido)
        atr14           : ATR(14) Wilder en M1
        regime          : TrendUp / TrendDown / Expansion / Chop
        swing_high_50   : máximo en ventana 50 barras (mirar HACIA ATRÁS)
        swing_low_50    : mínimo en ventana 50 barras

    ICT levels
        equal_high      : high dentro de ±0.03% de swing_high_50 anterior
        equal_low       : low dentro de ±0.03% de swing_low_50 anterior
        sweep_confirmed : wick superó swing pero cierre volvió adentro
        vwap            : VWAP diario acumulado, reset 00:00 UTC

    Orderflow
        vr              : volume ratio vs media 50 barras
        dz              : delta z-score ventana 100 barras
        cvd_slope       : cvd[i] - cvd[i-5]
        stacked_imb     : Bullish / Bearish / None (3+ barras mismo sesgo delta)
        abs_ask         : absorción en ask (delta > 0 pero obi < -0.05)
        abs_bid         : absorción en bid (delta < 0 pero obi > 0.05)
        cvd_div         : divergencia CVD vs precio
        cvd_consec_neg  : barras consecutivas con delta < 0
        cvd_consec_pos  : barras consecutivas con delta > 0
        prev_bar_delta  : delta de la barra anterior
        bars_since_low_vr: barras desde la última compresión de volumen (vr < 0.7)
        vpin            : buy_vol / (buy_vol + sell_vol) ventana 50 barras
        big_trade_bearish: sell_vol > 2.5× vol_ma AND mecha superior relevante
        big_trade_bullish: buy_vol > 2.5× vol_ma AND mecha inferior relevante

    Orderbook (de obi10_min/max existentes)
        obi_range       : obi10_max - obi10_min (lucha DOM durante la barra)

    Volume Profile (ventana 300 barras)
        vp_poc          : Point of Control (precio con más volumen)
        vp_vah          : Value Area High (70% del volumen)
        vp_val          : Value Area Low
        vp_lvn_below    : LVN (zona vacía) más cercana por debajo del precio

    ICT / estructura HTF (requiere pasos anteriores)
        fib_ote         : en zona OTE 61.8-78.6% retroceso del swing 50-bar
        fib_ote_london  : en zona OTE 62-79% del swing Asian Low → London Sweep High
        london_sweep_h  : running max de high desde 02:00 UTC (London KZ, reset diario)
        ote_62          : nivel 62% del swing London (precio)
        ote_79          : nivel 79% del swing London (precio)
        ote_rejection   : wick entró en OTE pero body cerró bajo el 62% (rechazo duro)
        body_below_vwap : close < VWAP (cuerpo vela, no wick)
        body_below_poc  : close < POC
        weekly_high     : máximo rolling ~5 días (ajustado al timeframe)
        weekly_low      : mínimo rolling ~5 días
        near_weekly_high: |close - weekly_high| / close < 0.5%
        near_asian_high : |close - asian_high| / close < 0.3%
        near_pdh        : |close - prev_day_high| / close < 0.3%
        pdh_sweep       : high últimas 3 barras > PDH pero cierre < PDH
        equal_high_sweep: equal_high reciente + wick barrió swing + cerró debajo
        tight_range     : TR medio 5 barras < 0.3 × ATR14 (compresión activa)
        above_poc       : close > vp_poc
        val_near        : |close - vp_val| / close < 0.5%
        bearish_fvg_active: FVG bajista activo (sin rellenar, últimas 50 barras)
        near_bearish_fvg: precio dentro del 0.5% del FVG bajista más cercano
        near_bearish_ob : precio retestando el OB bajista más reciente

    H4 structure (requiere datos OHLCV; se computa resampling en memoria)
        h4_ema20        : EMA20 sobre barras H4 mapeada a barras base
        h4_bearish      : close < h4_ema20 (bias bajista en H4)
        h4_ob_zone      : precio dentro del Order Block bajista más reciente en H4
        h4_fvg_zone     : precio dentro de Fair Value Gap bajista activo en H4
        h4_bos_bear     : Break of Structure bajista confirmado en H4 (últimas 5 H4)
        displacement_bear: vela impulsiva bajista (body>65% rango, vr>1.5, delta<0)
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
SYMBOL_TO_PATH = {
    'BTCUSDT':    ROOT / 'data/bybit-spot/processed/btcusdt_m1.parquet',
    'BTCUSDT_M5': ROOT / 'data/bybit-spot/processed/btcusdt_m5.parquet',
    'BTCUSDT_M15':ROOT / 'data/bybit-spot/processed/btcusdt_m15.parquet',
    'ETHUSDT':    ROOT / 'data/bybit-spot-eth/processed/ethusdt_m1.parquet',
}

# Barras por periodo según timeframe base
TF_BARS = {
    'm1':  {'h4': 240, 'week': 7200,  'day': 1440, 'h1': 60},
    'm5':  {'h4': 48,  'week': 1440,  'day': 288,  'h1': 12},
    'm15': {'h4': 16,  'week': 480,   'day': 96,   'h1': 4},
}

D1_MS = 86_400_000
H1_MS =  3_600_000
H4_MS =  4 * H1_MS


# ── Helpers básicos ───────────────────────────────────────────────────────────

def ema(vals: np.ndarray, period: int) -> np.ndarray:
    k = 2 / (period + 1)
    out = np.empty(len(vals), dtype=np.float64)
    out[0] = vals[0]
    for i in range(1, len(vals)):
        out[i] = vals[i] * k + out[i - 1] * (1 - k)
    return out


def wilder_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(high)
    tr = np.empty(n)
    tr[0] = high[0] - low[0]
    prev_c = close[:-1]
    tr[1:] = np.maximum(high[1:] - low[1:],
             np.maximum(np.abs(high[1:] - prev_c),
                        np.abs(low[1:]  - prev_c)))
    out = np.empty(n)
    out[:period] = tr[:period].mean()
    k = 1 / period
    for i in range(period, n):
        out[i] = out[i - 1] * (1 - k) + tr[i] * k
    return out


# ── Sesión ────────────────────────────────────────────────────────────────────

def compute_session(ts_ms: np.ndarray) -> np.ndarray:
    """ICT killzone timing:
    Asia      00:00-02:00  acumulacion silenciosa
    London_KZ 02:00-05:00  manipulacion (sweep Asian High)
    London    05:00-07:00  continuacion London
    NY_KZ     07:00-10:00  distribucion / entrada ICT
    NewYork   10:00-20:00  sesion NY amplia
    OffHours  20:00-24:00  sin liquidez
    """
    hm  = (ts_ms // 60_000) % 1440   # minuto del dia UTC
    out = np.full(len(hm), 'OffHours', dtype=object)
    out[(hm >=  0 * 60) & (hm <  2 * 60)] = 'Asia'
    out[(hm >=  2 * 60) & (hm <  5 * 60)] = 'London_KZ'   # manipulacion
    out[(hm >=  5 * 60) & (hm <  7 * 60)] = 'London'
    out[(hm >=  7 * 60) & (hm < 10 * 60)] = 'NY_KZ'       # distribucion / entrada
    out[(hm >= 10 * 60) & (hm < 20 * 60)] = 'NewYork'
    return out


# ── Niveles diarios ───────────────────────────────────────────────────────────

def compute_daily_levels(df: pd.DataFrame) -> pd.DataFrame:
    """Asian high/low, PDH/PDL — sin look-ahead: siempre del día anterior o sesión ya cerrada."""
    ts  = df['ts_ms'].values
    day = ts // D1_MS
    hm  = (ts // 60_000) % 1440

    # ── Asian high/low: 00:00-02:00 UTC (ICT Asia session), propagado al resto del día ──
    # ICT: Asia session termina cuando arranca London KZ (02:00 UTC).
    # El sweep del Asian High ocurre durante London KZ (02:00-05:00).
    asian_mask = hm < 2 * 60
    n = len(df)
    asian_h = np.full(n, np.nan)
    asian_l = np.full(n, np.nan)

    # Itera una sola vez guardando el running max/min por día
    cur_day = -1
    run_h = run_l = np.nan
    # Primero construye el CIERRE de Asia por día (para el look-ahead del resto del día)
    asia_close_h: dict[int, float] = {}
    asia_close_l: dict[int, float] = {}
    for i in range(n):
        d = int(day[i])
        if d != cur_day:
            cur_day = d
            run_h = run_l = np.nan
        if asian_mask[i]:
            run_h = df['high'].iat[i] if np.isnan(run_h) else max(run_h, df['high'].iat[i])
            run_l = df['low'].iat[i]  if np.isnan(run_l) else min(run_l, df['low'].iat[i])
            asia_close_h[d] = run_h
            asia_close_l[d] = run_l

    # Propaga el cierre de Asia a todas las barras del mismo día
    cur_day = -1
    run_h = run_l = np.nan
    for i in range(n):
        d = int(day[i])
        if d != cur_day:
            cur_day = d
            # Al entrar a un nuevo día usamos el cierre de Asia de ese día (si ya terminó)
            # Para barras DENTRO de Asia: propagamos el running max/min en tiempo real
            run_h = asia_close_h.get(d, np.nan)
            run_l = asia_close_l.get(d, np.nan)
        if asian_mask[i]:
            # Durante Asia: valor incremental (sin look-ahead)
            if i == 0 or int(day[i - 1]) != d:
                run_h = df['high'].iat[i]
                run_l = df['low'].iat[i]
            else:
                run_h = max(run_h, df['high'].iat[i])
                run_l = min(run_l, df['low'].iat[i])
        asian_h[i] = run_h
        asian_l[i] = run_l

    df = df.copy()
    df['asian_high'] = asian_h
    df['asian_low']  = asian_l

    # ── PDH / PDL — máximo/mínimo del día ANTERIOR ───────────────────────────
    daily_h = df.groupby(day)['high'].max()
    daily_l = df.groupby(day)['low'].min()
    days_arr = np.array(sorted(daily_h.index))

    pdh_map = {d: daily_h[days_arr[i - 1]] for i, d in enumerate(days_arr) if i > 0}
    pdl_map = {d: daily_l[days_arr[i - 1]] for i, d in enumerate(days_arr) if i > 0}

    df['prev_day_high'] = pd.Series(day, dtype=int).map(pdh_map).values
    df['prev_day_low']  = pd.Series(day, dtype=int).map(pdl_map).values

    return df


# ── VWAP diario ───────────────────────────────────────────────────────────────

def compute_vwap(df: pd.DataFrame) -> np.ndarray:
    day  = df['ts_ms'].values // D1_MS
    tp   = (df['high'].values + df['low'].values + df['close'].values) / 3
    tpv  = tp * df['volume'].values

    vwap = np.empty(len(df))
    cum_tpv = cum_vol = 0.0
    prev_day = -1
    for i in range(len(df)):
        d = int(day[i])
        if d != prev_day:
            cum_tpv = cum_vol = 0.0
            prev_day = d
        cum_tpv += tpv[i]
        cum_vol  += df['volume'].iat[i]
        vwap[i]   = cum_tpv / cum_vol if cum_vol > 0 else df['close'].iat[i]
    return vwap


# ── Swings (mirada hacia atrás, sin look-ahead) ───────────────────────────────

def compute_swings(df: pd.DataFrame, window: int = 50) -> tuple[np.ndarray, np.ndarray]:
    sh = df['high'].rolling(window, min_periods=1).max().values
    sl = df['low'].rolling(window,  min_periods=1).min().values
    return sh, sl


# ── Equal high/low ────────────────────────────────────────────────────────────

def compute_equal_levels(df: pd.DataFrame, sh: np.ndarray, sl: np.ndarray,
                          thresh: float = 0.0003) -> tuple[np.ndarray, np.ndarray]:
    high  = df['high'].values
    low   = df['low'].values

    # equal_high: high actual dentro de ±thresh% del swing_high previo
    # shift(1) del swing para evitar look-ahead
    sh_prev = np.roll(sh, 1); sh_prev[0] = np.nan
    sl_prev = np.roll(sl, 1); sl_prev[0] = np.nan

    eq_h = np.abs(high - sh_prev) / (sh_prev + 1e-9) < thresh
    eq_l = np.abs(low  - sl_prev) / (sl_prev + 1e-9) < thresh
    return eq_h, eq_l


# ── Sweep confirmed ───────────────────────────────────────────────────────────

def compute_sweep(df: pd.DataFrame, sh: np.ndarray, sl: np.ndarray) -> np.ndarray:
    """Wick que supera el swing pero el cierre vuelve adentro del rango."""
    high  = df['high'].values
    low   = df['low'].values
    close = df['close'].values

    sh_prev = np.roll(sh, 1); sh_prev[0] = np.nan
    sl_prev = np.roll(sl, 1); sl_prev[0] = np.nan

    bear_sweep = (high > sh_prev) & (close < sh_prev)   # swept highs → short setup
    bull_sweep = (low  < sl_prev) & (close > sl_prev)   # swept lows  → long setup
    return bear_sweep | bull_sweep


# ── Regime (TrendUp / TrendDown / Expansion / Chop) ──────────────────────────

def compute_regime(df: pd.DataFrame, ema20: np.ndarray, atr14: np.ndarray) -> np.ndarray:
    close    = df['close'].values
    atr_ma   = pd.Series(atr14).rolling(20, min_periods=5).mean().values
    atr_pct  = atr14 / (close + 1e-9)
    atr_ratio = atr14 / (atr_ma + 1e-9)

    n = len(close)
    regime = np.full(n, 'Chop', dtype=object)

    # Expansion: ATR actual > 1.3× su media de 20 barras
    expansion = atr_ratio > 1.30

    # TrendUp/Down: precio en el mismo lado de EMA20 por 3+ barras consecutivas
    bull = close > ema20 * 1.002
    bear = close < ema20 * 0.998

    bull_streak = np.zeros(n, dtype=int)
    bear_streak = np.zeros(n, dtype=int)
    for i in range(1, n):
        bull_streak[i] = bull_streak[i - 1] + 1 if bull[i] else 0
        bear_streak[i] = bear_streak[i - 1] + 1 if bear[i] else 0

    regime[expansion]              = 'Expansion'
    regime[bull_streak >= 3]       = 'TrendUp'
    regime[bear_streak >= 3]       = 'TrendDown'
    # Expansion tiene prioridad sobre trend si ATR explota
    regime[expansion & bull_streak >= 3] = 'Expansion'
    regime[expansion & bear_streak >= 3] = 'Expansion'

    return regime


# ── VR / DZ / CVD slope ──────────────────────────────────────────────────────

def compute_orderflow_basic(df: pd.DataFrame) -> pd.DataFrame:
    vol   = df['volume'].values
    delta = df['delta'].values
    cvd   = df['cvd'].values if 'cvd' in df.columns else np.cumsum(delta)

    # VR
    vol_ma = pd.Series(vol).rolling(50, min_periods=10).mean().values
    vr = vol / (vol_ma + 1e-9)

    # DZ — delta z-score ventana 100
    d_s = pd.Series(delta)
    dz = ((d_s - d_s.rolling(100, min_periods=20).mean()) /
          (d_s.rolling(100, min_periods=20).std() + 1e-9)).values

    # CVD slope (diferencia en 5 barras)
    slope = np.empty(len(cvd)); slope[:5] = 0.0
    slope[5:] = cvd[5:] - cvd[:-5]

    df = df.copy()
    df['vr']        = vr
    df['dz']        = dz
    df['cvd_slope'] = slope
    return df


# ── Stacked imbalance ─────────────────────────────────────────────────────────

def compute_stacked_imb(df: pd.DataFrame, streak: int = 3) -> np.ndarray:
    """3+ barras consecutivas con mismo sesgo de delta → Bullish / Bearish / None."""
    delta = df['delta'].values
    n = len(delta)
    out = np.full(n, 'None', dtype=object)

    pos_streak = neg_streak = 0
    for i in range(n):
        if delta[i] > 0:
            pos_streak += 1; neg_streak = 0
        elif delta[i] < 0:
            neg_streak += 1; pos_streak = 0
        else:
            pos_streak = neg_streak = 0

        if pos_streak >= streak:
            out[i] = 'Bullish'
        elif neg_streak >= streak:
            out[i] = 'Bearish'

    return out


# ── Absorción ─────────────────────────────────────────────────────────────────

def compute_absorption(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """abs_ask: compradores activos pero OBI sigue negativo (sellers absorbiendo bids)."""
    delta = df['delta'].values
    obi   = df['obi5_mean'].values if 'obi5_mean' in df.columns else \
            df['obi10_mean'].values

    abs_ask = (delta > 0) & (obi < -0.05)   # buyers presentes, libro vendedor
    abs_bid = (delta < 0) & (obi > +0.05)   # sellers presentes, libro comprador
    return abs_ask, abs_bid


# ── CVD divergencia ───────────────────────────────────────────────────────────

def compute_cvd_div(df: pd.DataFrame) -> np.ndarray:
    close = df['close'].values
    slope = df['cvd_slope'].values if 'cvd_slope' in df.columns else \
            np.zeros(len(close))
    price_up = (close - np.roll(close, 3)) > 0
    cvd_down = slope < 0
    return price_up & cvd_down


# ── Multi-bar context ─────────────────────────────────────────────────────────

def compute_multibar_context(df: pd.DataFrame) -> pd.DataFrame:
    delta = df['delta'].values
    vr    = df['vr'].values if 'vr' in df.columns else np.ones(len(delta))
    n = len(delta)

    cvd_neg = np.zeros(n, dtype=np.int16)
    cvd_pos = np.zeros(n, dtype=np.int16)
    cnt_neg = cnt_pos = 0
    for i in range(n):
        cnt_neg = cnt_neg + 1 if delta[i] < 0 else 0
        cnt_pos = cnt_pos + 1 if delta[i] > 0 else 0
        cvd_neg[i] = min(cnt_neg, 32767)
        cvd_pos[i] = min(cnt_pos, 32767)

    prev_delta = np.roll(delta, 1); prev_delta[0] = 0.0

    # bars_since_low_vr: barras desde última compresión (vr < 0.7)
    low_vr_mask = vr < 0.70
    bars_since = np.full(n, 9999, dtype=np.int16)
    last_low = -9999
    for i in range(n):
        if low_vr_mask[i]:
            last_low = i
        bars_since[i] = min(i - last_low, 9999)

    df = df.copy()
    df['cvd_consec_neg']   = cvd_neg
    df['cvd_consec_pos']   = cvd_pos
    df['prev_bar_delta']   = prev_delta
    df['bars_since_low_vr'] = bars_since
    return df


# ── VPIN ─────────────────────────────────────────────────────────────────────

def compute_vpin(df: pd.DataFrame, window: int = 50) -> np.ndarray:
    """VPIN simplificado: buy_vol / total_vol en ventana rolling."""
    buy  = pd.Series(df['buy_vol'].values)
    sell = pd.Series(df['sell_vol'].values)
    roll_buy  = buy.rolling(window, min_periods=10).sum()
    roll_sell = sell.rolling(window, min_periods=10).sum()
    total = roll_buy + roll_sell
    return (roll_buy / total.replace(0, np.nan)).fillna(0.5).values


# ── Big trade ─────────────────────────────────────────────────────────────────

def compute_big_trade(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """
    big_trade_bearish: sell_vol > 2.5× vol_ma Y mecha superior relevante
                       (vendedores dominaron la parte alta de la vela)
    big_trade_bullish: buy_vol  > 2.5× vol_ma Y mecha inferior relevante
    """
    vol   = df['volume'].values
    sell  = df['sell_vol'].values
    buy   = df['buy_vol'].values
    high  = df['high'].values
    low   = df['low'].values
    op    = df['open'].values
    cl    = df['close'].values

    vol_ma = pd.Series(vol).rolling(20, min_periods=5).mean().values
    rng    = high - low + 1e-9

    upper_wick = high - np.maximum(op, cl)
    lower_wick = np.minimum(op, cl) - low

    big_sell = sell > 2.5 * vol_ma
    big_buy  = buy  > 2.5 * vol_ma

    upper_wick_pct = upper_wick / rng
    lower_wick_pct = lower_wick / rng

    bt_bear = big_sell & (upper_wick_pct > 0.30)   # sell grande + mecha superior
    bt_bull = big_buy  & (lower_wick_pct > 0.30)   # buy grande  + mecha inferior

    return bt_bear, bt_bull


# ── OBI range intrabar ────────────────────────────────────────────────────────

def compute_obi_range(df: pd.DataFrame) -> np.ndarray:
    obi_max = df['obi10_max'].values if 'obi10_max' in df.columns else np.zeros(len(df))
    obi_min = df['obi10_min'].values if 'obi10_min' in df.columns else np.zeros(len(df))
    return obi_max - obi_min


# ── Volume Profile rolling ────────────────────────────────────────────────────

def compute_volume_profile(df: pd.DataFrame, window: int = 300,
                            n_bins: int = 50) -> tuple[np.ndarray, ...]:
    """
    Para cada barra i, calcula el volume profile de las últimas `window` barras.
    Devuelve (poc, vah, val, lvn_below).
    """
    close  = df['close'].values
    volume = df['volume'].values
    n      = len(close)

    poc       = np.full(n, np.nan)
    vah       = np.full(n, np.nan)
    val       = np.full(n, np.nan)
    lvn_below = np.full(n, np.nan)

    for i in range(window, n):
        w_close = close[i - window: i]
        w_vol   = volume[i - window: i]

        lo, hi = w_close.min(), w_close.max()
        if hi - lo < 1e-6:
            continue

        bins = np.linspace(lo, hi, n_bins + 1)
        hist, _ = np.histogram(w_close, bins=bins, weights=w_vol)

        # POC
        poc_idx    = int(np.argmax(hist))
        poc[i]     = (bins[poc_idx] + bins[poc_idx + 1]) / 2

        # VAH / VAL — expandir desde POC hasta cubrir 70% del volumen
        total  = hist.sum()
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

        val[i] = (bins[lo_i]     + bins[lo_i + 1])     / 2
        vah[i] = (bins[hi_i]     + bins[hi_i + 1])     / 2

        # LVN below: nivel con menor volumen por debajo del precio actual
        curr   = close[i]
        below  = bins[1:] <= curr
        if below.any():
            h_below = hist.copy().astype(float)
            h_below[~below] = np.inf
            min_idx = int(np.argmin(h_below))
            if h_below[min_idx] < np.inf:
                lvn_below[i] = (bins[min_idx] + bins[min_idx + 1]) / 2

    return poc, vah, val, lvn_below


# ── ICT structure features (FVG, OTE, OB, sweeps HTF, weekly) ────────────────

def compute_ict_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula features ICT/estructurales sin look-ahead.
    Requiere: swing_high/low_50, equal_high, atr14, prev_day_high, asian_high,
              asian_low, vp_poc, vp_val, vwap (ya calculados en enrich()).
    """
    from collections import deque

    close = df['close'].values
    high  = df['high'].values
    low   = df['low'].values
    op    = df['open'].values
    sh    = df['swing_high_50'].values
    sl    = df['swing_low_50'].values
    atr14 = df['atr14'].values
    eq_h  = df['equal_high'].values.astype(bool)
    n     = len(close)

    ts_ms = df['ts_ms'].values
    day   = ts_ms // D1_MS
    hm    = (ts_ms // 60_000) % 1440   # minuto del día UTC

    # ── 1a. Fibonacci OTE genérico (61.8-78.6% del último swing 50-bar) ────────
    swing_range = sh - sl
    ote_top = sh - 0.618 * swing_range
    ote_bot = sh - 0.786 * swing_range
    fib_ote = (close >= ote_bot) & (close <= ote_top) & (swing_range > 0)

    # ── 1b. OTE London — swing correcto: Asian Low → London KZ Sweep High ───────
    # ICT Market Maker Sell: Asia (00:00-02:00) acumula → London KZ (02:00-05:00)
    # barre el Asian High (manipulacion) → precio retrocede al 62-79% de ese swing
    # → entry short en NY KZ (07:00-10:00) (distribucion)
    #
    # london_sweep_h: running max de high desde 02:00 UTC (inicio London KZ), reset diario
    # Condicion de sweep: london_sweep_h > asian_high del dia
    # OTE zone: [62%, 79%] del rango (london_sweep_h → asian_low)
    asian_h_arr = df['asian_high'].values
    asian_l_arr = df['asian_low'].values

    london_sweep_h   = np.full(n, np.nan)
    cur_day_ote      = -1
    run_lon_h        = np.nan

    for i in range(n):
        d = int(day[i])
        m = int(hm[i])
        if d != cur_day_ote:
            cur_day_ote = d
            run_lon_h   = np.nan
        if m >= 120:          # 02:00 UTC = inicio London KZ (manipulacion ICT)
            run_lon_h = high[i] if np.isnan(run_lon_h) else max(run_lon_h, high[i])
        london_sweep_h[i] = run_lon_h

    # Sweep válido: London superó el Asian High del día
    london_swept      = (london_sweep_h > asian_h_arr) & np.isfinite(asian_l_arr)
    ote_range_lon     = london_sweep_h - asian_l_arr          # swing total
    ote_62            = london_sweep_h - 0.618 * ote_range_lon  # 62% = nivel OTE top
    ote_79            = london_sweep_h - 0.786 * ote_range_lon  # 79% = nivel OTE bottom
    valid_range       = (ote_range_lon > 0) & london_swept

    fib_ote_london = valid_range & (close <= ote_62) & (close >= ote_79)

    # OTE rejection: wick entró en la zona premium pero BODY cerró por debajo del 62%
    # → shooting star con rechazo duro en la zona OTE
    wick_in_ote       = valid_range & (high >= ote_79)   # wick tocó o entró en OTE
    body_below_ote62  = close < ote_62                    # cuerpo cerró bajo el 62%
    ote_rejection     = wick_in_ote & body_below_ote62

    # body_close_below para niveles clave (close del cuerpo, no wick)
    vwap_arr = df['vwap'].values if 'vwap' in df.columns else close
    poc_arr  = df['vp_poc'].values if 'vp_poc' in df.columns else np.full(n, np.nan)
    body_below_vwap = close < vwap_arr                    # body cerró bajo VWAP
    body_below_poc  = close < poc_arr                     # body cerró bajo POC

    # ── 2. Weekly high / low (calculado en enrich() con periodo ajustado al tf) ─
    weekly_h  = pd.Series(high).rolling(500, min_periods=10).max().values  # placeholder
    weekly_l  = pd.Series(low).rolling(500,  min_periods=10).min().values
    near_wk_h = np.abs(close - weekly_h) / (close + 1e-9) < 0.005

    # ── 3. Near Asian High / PDH ──────────────────────────────────────────────
    asian_h  = df['asian_high'].values
    near_ah  = np.abs(close - asian_h) / (close + 1e-9) < 0.003
    pdh      = df['prev_day_high'].values
    near_pdh = np.abs(close - pdh) / (close + 1e-9) < 0.003

    # ── 4. PDH sweep: high últimas 3 barras > PDH, cierre < PDH ──────────────
    rolling_h3 = pd.Series(high).rolling(3, min_periods=1).max().values
    pdh_sweep  = (rolling_h3 > pdh) & (close < pdh)

    # ── 5. Equal high sweep: equal_high reciente + wick barrió swing high ─────
    eq_h_recent = pd.Series(eq_h.astype(np.float32)).rolling(5, min_periods=1).max().astype(bool).values
    sh_prev = np.roll(sh, 1); sh_prev[0] = np.nan
    equal_high_sweep = eq_h_recent & (high > sh_prev) & (close < sh_prev)

    # ── 6. Tight range: TR medio 5 barras < 0.3 × ATR14 (compresión activa) ──
    tr5 = pd.Series(high - low).rolling(5, min_periods=3).mean().values
    tight_range = tr5 < 0.30 * atr14

    # ── 7. Above POC / VAL near ───────────────────────────────────────────────
    poc = df['vp_poc'].values if 'vp_poc' in df.columns else np.full(n, np.nan)
    val = df['vp_val'].values if 'vp_val' in df.columns else np.full(n, np.nan)
    above_poc = close > poc
    val_near  = np.abs(close - val) / (close + 1e-9) < 0.005

    # ── 8. Bearish FVG (gap 3 velas sin rellenar, últimas 50 barras) ──────────
    # FVG en barra i: low[i-2] > high[i] → gap = [high[i], low[i-2]]
    bearish_fvg_active = np.zeros(n, dtype=bool)
    near_bearish_fvg   = np.zeros(n, dtype=bool)
    fvg_q: 'deque[tuple[float, float, int]]' = deque()

    for i in range(2, n):
        ft_new = low[i - 2]
        fb_new = high[i]
        if ft_new > fb_new:
            fvg_q.append((ft_new, fb_new, i))
        next_q: 'deque[tuple[float, float, int]]' = deque()
        for ft, fb, fa in fvg_q:
            if (i - fa) > 50 or (fb <= close[i] <= ft):
                continue
            next_q.append((ft, fb, fa))
        fvg_q = next_q
        for ft, fb, fa in fvg_q:
            if fb > close[i]:
                bearish_fvg_active[i] = True
                if (fb - close[i]) / (close[i] + 1e-9) < 0.005:
                    near_bearish_fvg[i] = True

    # ── 9. Bearish Order Block: vela verde + impulso bajista → retest ─────────
    # Confirmado 3 barras después: close[j+3] < open[j] y caída > 1.5×ATR
    near_bearish_ob = np.zeros(n, dtype=bool)
    ob_events: 'dict[int, tuple[float, float]]' = {}

    for j in range(n - 3):
        if close[j] > op[j]:
            drop = close[j] - close[j + 3]
            if drop > 1.5 * atr14[j] and close[j + 3] < op[j]:
                ob_events[j + 3] = (high[j], op[j])  # (ob_high, ob_low_body)

    ob_h = ob_l = np.nan
    for i in range(n):
        if i in ob_events:
            ob_h, ob_l = ob_events[i]
        if not np.isnan(ob_h):
            if close[i] < ob_l * 0.999:
                ob_h = ob_l = np.nan
            elif ob_l <= close[i] <= ob_h:
                near_bearish_ob[i] = True

    # ── 10. Displacement candle bajista ───────────────────────────────────────
    # Vela impulsiva bearish: cuerpo > 65% del rango, delta negativo, volumen alto
    rng_d        = high - low + 1e-9
    body_d       = np.abs(close - op)
    vr_d         = df['vr'].values if 'vr' in df.columns else np.ones(n)
    displacement_bear = (
        (close < op) &               # vela bajista
        (body_d / rng_d > 0.65) &    # cuerpo domina (poca mecha)
        (vr_d > 1.5)                  # volumen elevado
    )

    df = df.copy()
    df['fib_ote']            = fib_ote
    df['fib_ote_london']     = fib_ote_london     # OTE sobre swing Asian Low → London Sweep
    df['london_sweep_h']     = london_sweep_h     # London running high (numérico)
    df['ote_62']             = ote_62             # nivel 62% del swing London (numérico)
    df['ote_79']             = ote_79             # nivel 79% del swing London (numérico)
    df['ote_rejection']      = ote_rejection      # wick en OTE + body cerró bajo 62%
    df['body_below_vwap']    = body_below_vwap    # close < VWAP (cuerpo, no wick)
    df['body_below_poc']     = body_below_poc     # close < POC
    df['weekly_high']        = weekly_h
    df['weekly_low']         = weekly_l
    df['near_weekly_high']   = near_wk_h
    df['near_asian_high']    = near_ah
    df['near_pdh']           = near_pdh
    df['pdh_sweep']          = pdh_sweep
    df['equal_high_sweep']   = equal_high_sweep
    df['tight_range']        = tight_range
    df['above_poc']          = above_poc
    df['val_near']           = val_near
    df['bearish_fvg_active'] = bearish_fvg_active
    df['near_bearish_fvg']   = near_bearish_fvg
    df['near_bearish_ob']    = near_bearish_ob
    df['displacement_bear']  = displacement_bear
    return df


# ── H4 features (EMA20, OB, FVG, BoS) ───────────────────────────────────────

def compute_h4_features(df: pd.DataFrame, bars_per_h4: int) -> pd.DataFrame:
    """
    Resamplea en memoria a H4 y calcula:
      h4_ema20       : EMA20 sobre cierres H4, mapeada a barras base
      h4_bearish     : close_base < h4_ema20 (sesgo bajista H4)
      h4_ob_zone     : precio dentro del OB bajista H4 mas reciente activo
      h4_fvg_zone    : precio dentro de FVG bajista H4 activo
      h4_bos_bear    : BoS bajista confirmado en H4 en las ultimas 5 barras H4
      displacement_h4: desplazamiento bajista en H4 (body>65%, vr>1.5)
    """
    n       = len(df)
    ts      = df['ts_ms'].values
    close_b = df['close'].values

    # ── Agrupar en buckets H4 ─────────────────────────────────────────────────
    h4_bucket = (ts // H4_MS) * H4_MS   # timestamp del inicio del H4

    # Agregaciones H4
    df_tmp = df.copy()
    df_tmp['h4_ts'] = h4_bucket
    h4 = df_tmp.groupby('h4_ts', sort=True).agg(
        open  = ('open',  'first'),
        high  = ('high',  'max'),
        low   = ('low',   'min'),
        close = ('close', 'last'),
        volume= ('volume','sum'),
        delta = ('delta', 'sum') if 'delta' in df.columns else ('close', 'count'),
        vr    = ('vr',    'mean') if 'vr'    in df.columns else ('close', 'count'),
    ).reset_index().rename(columns={'h4_ts': 'ts_ms'})

    nh4 = len(h4)
    if nh4 < 5:
        for col in ('h4_ema20','h4_bearish','h4_ob_zone','h4_fvg_zone','h4_bos_bear'):
            df[col] = False if col != 'h4_ema20' else np.nan
        return df

    h4_close = h4['close'].values
    h4_high  = h4['high'].values
    h4_low   = h4['low'].values
    h4_open  = h4['open'].values
    h4_ts    = h4['ts_ms'].values

    # ── H4 EMA20 ──────────────────────────────────────────────────────────────
    h4_ema = ema(h4_close, 20)

    # ── H4 Bearish OB ─────────────────────────────────────────────────────────
    # Ultima vela H4 alcista (close > open) antes de impulso bajista (close[j+2] < open[j])
    h4_ob_hi = np.full(nh4, np.nan)
    h4_ob_lo = np.full(nh4, np.nan)
    cur_ob_hi = cur_ob_lo = np.nan

    for j in range(nh4 - 2):
        if h4_close[j] > h4_open[j]:               # vela H4 alcista
            drop = h4_close[j] - h4_close[j + 2]
            if drop > 0 and h4_close[j + 2] < h4_open[j]:  # impulso bajista tras ella
                cur_ob_hi = h4_high[j]
                cur_ob_lo = h4_open[j]
        h4_ob_hi[j + 2] = cur_ob_hi
        h4_ob_lo[j + 2] = cur_ob_lo

    # ── H4 FVG bajista ────────────────────────────────────────────────────────
    # FVG = low[i-2] > high[i] en H4 (vela i-1 crea gap, sin rellenar)
    h4_fvg_top = np.full(nh4, np.nan)
    h4_fvg_bot = np.full(nh4, np.nan)
    cur_fvg_t = cur_fvg_b = np.nan

    for j in range(2, nh4):
        ft = h4_low[j - 2]
        fb = h4_high[j]
        if ft > fb:                                 # gap bajista confirmado
            cur_fvg_t = ft
            cur_fvg_b = fb
        # invalidar si precio cierra dentro del FVG
        if not np.isnan(cur_fvg_t) and (cur_fvg_b <= h4_close[j] <= cur_fvg_t):
            cur_fvg_t = cur_fvg_b = np.nan
        h4_fvg_top[j] = cur_fvg_t
        h4_fvg_bot[j] = cur_fvg_b

    # ── H4 BoS bajista ────────────────────────────────────────────────────────
    # Swing low H4 = minimo en ventana 3 barras H4 hacia atras
    h4_swing_lo = pd.Series(h4_low).rolling(3, min_periods=2).min().values
    # BoS: cierre H4 por debajo del swing low anterior
    h4_swing_lo_prev = np.roll(h4_swing_lo, 1); h4_swing_lo_prev[0] = np.nan
    h4_bos = h4_close < h4_swing_lo_prev        # bool en espacio H4

    # ── Mapear de H4 a barras base (forward-fill) ──────────────────────────────
    # Para cada barra base, encontrar el H4 mas reciente completado
    h4_ts_arr = h4_ts

    # Indices: para cada barra base, indice del H4 bucket actual
    h4_idx_per_bar = np.searchsorted(h4_ts_arr, h4_bucket, side='left')
    h4_idx_per_bar = np.clip(h4_idx_per_bar, 0, nh4 - 1)

    base_h4_ema   = h4_ema[h4_idx_per_bar]
    base_ob_hi    = h4_ob_hi[h4_idx_per_bar]
    base_ob_lo    = h4_ob_lo[h4_idx_per_bar]
    base_fvg_top  = h4_fvg_top[h4_idx_per_bar]
    base_fvg_bot  = h4_fvg_bot[h4_idx_per_bar]

    # BoS reciente: True si hubo BoS en las ultimas 5 barras H4
    bos_recent = np.zeros(nh4, dtype=bool)
    for j in range(nh4):
        bos_recent[j] = np.any(h4_bos[max(0, j - 4): j + 1])
    base_bos = bos_recent[h4_idx_per_bar]

    # ── Flags en espacio base ─────────────────────────────────────────────────
    h4_bearish   = close_b < base_h4_ema
    h4_ob_zone   = (~np.isnan(base_ob_hi)) & (close_b <= base_ob_hi) & (close_b >= base_ob_lo)
    h4_fvg_zone  = (~np.isnan(base_fvg_top)) & (close_b <= base_fvg_top) & (close_b >= base_fvg_bot)

    df = df.copy()
    df['h4_ema20']    = base_h4_ema
    df['h4_bearish']  = h4_bearish
    df['h4_ob_zone']  = h4_ob_zone
    df['h4_fvg_zone'] = h4_fvg_zone
    df['h4_bos_bear'] = base_bos
    return df


# ── Pipeline principal ────────────────────────────────────────────────────────

def enrich(df: pd.DataFrame, verbose: bool = True, tf: str = 'm1') -> pd.DataFrame:
    n        = len(df)
    tf_cfg   = TF_BARS.get(tf, TF_BARS['m1'])
    bars_h4  = tf_cfg['h4']
    bars_week= tf_cfg['week']
    bars_day = tf_cfg['day']

    def log(msg):
        if verbose:
            print(msg, flush=True)

    log(f"Barras de entrada : {n:,}  (tf={tf}, bars_h4={bars_h4}, bars_week={bars_week})")

    # 1. Sesión
    log("  [1/15] Sesion (ICT killzones)...")
    df['session'] = compute_session(df['ts_ms'].values)

    # 2. Niveles diarios
    log("  [2/16] Niveles diarios (asian_high 00:00-02:00, PDH/PDL)...")
    df = compute_daily_levels(df)

    # 3. VWAP
    log("  [3/16] VWAP diario...")
    df['vwap'] = compute_vwap(df)

    # 4. EMA20 y ATR14
    log("  [4/16] EMA20 + ATR14...")
    cl = df['close'].values
    hi = df['high'].values
    lo = df['low'].values
    df['ema20'] = ema(cl, 20)
    df['atr14'] = wilder_atr(hi, lo, cl, 14)

    # 5. Swings 50 barras
    log("  [5/16] Swing high/low (50 barras)...")
    sh, sl = compute_swings(df, 50)
    df['swing_high_50'] = sh
    df['swing_low_50']  = sl

    # 6. Equal high/low
    log("  [6/16] Equal high/low...")
    eq_h, eq_l = compute_equal_levels(df, sh, sl)
    df['equal_high'] = eq_h
    df['equal_low']  = eq_l

    # 7. Sweep confirmed
    log("  [7/16] Sweep confirmed...")
    df['sweep_confirmed'] = compute_sweep(df, sh, sl)

    # 8. Regime
    log("  [8/16] Regime (TrendUp/Down/Expansion/Chop)...")
    df['regime'] = compute_regime(df, df['ema20'].values, df['atr14'].values)

    # 9. VR, DZ, CVD slope
    log("  [9/16] VR / DZ / CVD slope...")
    df = compute_orderflow_basic(df)

    # 10. Stacked imbalance
    log("  [10/16] Stacked imbalance...")
    df['stacked_imb'] = compute_stacked_imb(df)

    # 11. Absorción + CVD div
    log("  [11/16] Absorcion + CVD divergencia...")
    abs_ask, abs_bid = compute_absorption(df)
    df['abs_ask']  = abs_ask
    df['abs_bid']  = abs_bid
    df['cvd_div']  = compute_cvd_div(df)

    # 12. Multi-bar context
    log("  [12/16] Multi-bar context (cvd_consec, prev_delta, bars_since_low_vr)...")
    df = compute_multibar_context(df)

    # 13. VPIN + big_trade + OBI range + thin/wall
    log("  [13/16] VPIN / big_trade / OBI range / thin zones / walls...")
    df['vpin']            = compute_vpin(df)
    bt_bear, bt_bull      = compute_big_trade(df)
    df['big_trade_bearish'] = bt_bear
    df['big_trade_bullish'] = bt_bull
    df['obi_range']         = compute_obi_range(df)

    # thin_above/below: 24h lookback ajustado al timeframe
    ROLL = bars_day
    if 'near5_ask' in df.columns:
        df['thin_above'] = df['near5_ask'] < \
            df['near5_ask'].rolling(ROLL, min_periods=60).quantile(0.15)
        df['thin_below'] = df['near5_bid'] < \
            df['near5_bid'].rolling(ROLL, min_periods=60).quantile(0.15)
        df['ask_wall'] = df['max_ask5'] > \
            df['max_ask5'].rolling(ROLL, min_periods=60).quantile(0.90)
        df['bid_wall'] = df['max_bid5'] > \
            df['max_bid5'].rolling(ROLL, min_periods=60).quantile(0.90)
    else:
        log("    WARN: near5_ask no disponible")
        for col in ('thin_above', 'thin_below', 'ask_wall', 'bid_wall'):
            df[col] = False

    # 14. Volume Profile (el mas lento)
    log("  [14/16] Volume Profile (POC / VAH / VAL / LVN)...")
    poc, vah, val, lvn = compute_volume_profile(df)
    df['vp_poc']       = poc
    df['vp_vah']       = vah
    df['vp_val']       = val
    df['vp_lvn_below'] = lvn

    # 15. ICT features (OTE London KZ correcto, FVG, OB, displacement)
    log("  [15/16] ICT features (OTE London KZ 02:00, FVG, OB, displacement)...")
    df = compute_ict_features(df)

    # 16. H4 features (EMA20, OB, FVG, BoS)
    log("  [16/16] H4 features (EMA20, OB, FVG, BoS)...")
    df = compute_h4_features(df, bars_h4)

    # Weekly high/low ajustado al timeframe
    df['weekly_high'] = df['high'].rolling(bars_week, min_periods=60).max().values
    df['weekly_low']  = df['low'].rolling(bars_week,  min_periods=60).min().values
    df['near_weekly_high'] = (
        np.abs(df['close'].values - df['weekly_high'].values)
        / (df['close'].values + 1e-9) < 0.005
    )

    log(f"Features totales  : {len(df.columns)} columnas")
    return df


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbol', default='BTCUSDT', choices=list(SYMBOL_TO_PATH.keys()))
    parser.add_argument('--tf', default='m1', choices=list(TF_BARS.keys()),
                        help='Timeframe base: m1 (default), m5, m15')
    args = parser.parse_args()

    # Auto-detect tf desde symbol si no se especifica
    if args.tf == 'm1' and 'M5' in args.symbol:
        args.tf = 'm5'
    elif args.tf == 'm1' and 'M15' in args.symbol:
        args.tf = 'm15'

    path = SYMBOL_TO_PATH[args.symbol]
    if not path.exists():
        sys.exit(f"No existe: {path}")

    print(f"Leyendo {path.name}...")
    df = pd.read_parquet(path)
    df = df.sort_values('ts_ms').reset_index(drop=True)

    print(f"Calculando features para {args.symbol} ({len(df):,} barras, tf={args.tf})...")
    df = enrich(df, tf=args.tf)

    df.to_parquet(path, index=False, engine='pyarrow')

    first = pd.Timestamp(df['ts_ms'].iloc[0],  unit='ms', tz='UTC')
    last  = pd.Timestamp(df['ts_ms'].iloc[-1], unit='ms', tz='UTC')
    sz    = path.stat().st_size / 1e6
    print(f"\nGuardado: {path}")
    print(f"Rango   : {first.date()} -> {last.date()}")
    print(f"Columnas: {len(df.columns)}")
    print(f"Tamanio : {sz:.1f} MB")
    print()
    print("Columnas del dataset enriquecido:")
    for col in df.columns:
        print(f"  {col}")


if __name__ == '__main__':
    main()
