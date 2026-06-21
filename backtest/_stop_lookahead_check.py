"""
_stop_lookahead_check.py — ¿el stop H1 del motor canónico es un SEGUNDO lookahead?
====================================================================================
El motor (mtf_system.py) coloca el stop short en el HIGH TOTAL del bucket H1 en-curso:
    sl = h1_full_bucket_high + 0.40*ATR     # incluye minutos futuros de la hora
En el minuto T, el high final de la hora (T+1..T+59) NO se conoce → lookahead.

Comparamos el MISMO setup (rejection@VAH + confirmación orderflow + régimen D1, shorts)
bajo 3 modelos de stop:
  A) full-bucket H1 high  (canónico = LOOKAHEAD)
  B) prev-closed H1 high  (causal, conservador)
  C) rolling-60-bar high  (causal, swing reciente ~1h)  ← el honesto correcto
ATR: A usa ATR del bucket en-curso (lookahead); B/C usan ATR del bucket H1 cerrado previo.

Uso: python backtest/_stop_lookahead_check.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'backtest'))
import mtf_v2 as m

TARGET_R, FWD, COOLDOWN = 2.8, 240, 15
FEE_RT, TOL = m.FEE_RT, m.LEVEL_TOL
MIN_STOP, MAX_STOP = m.MIN_STOP, m.MAX_STOP
H1_MS, D1_MS, OOS_MS = m.H1_MS, m.D1_MS, m.OOS_MS


def sim(entry, close, high, low, ts, stop_short):
    cand = np.where(entry)[0]; n = len(close); trades = []; next_ok = 0
    for ci in cand:
        if ci < next_ok or ci + 1 >= n: continue
        ep = close[ci]; st = stop_short[ci]
        if not np.isfinite(st): continue
        dist = st - ep
        if dist <= 0: continue
        sp = dist / ep
        if not (MIN_STOP <= sp <= MAX_STOP): continue
        tp = ep - TARGET_R * dist
        reason = None; ex = None; exit_i = min(ci + FWD, n - 1)
        for j in range(ci + 1, min(ci + 1 + FWD, n)):
            if high[j] >= st: reason, ex, exit_i = 'stop', st, j; break
            if low[j] <= tp:  reason, ex, exit_i = 'target', tp, j; break
        if reason is None: ex = close[exit_i]
        r = (ep - ex) / dist - FEE_RT * ep / dist
        trades.append({'r': r, 'oos': int(ts[ci]) >= OOS_MS})
        next_ok = exit_i + COOLDOWN
    return trades


def rep(trades, label):
    is_t = [t for t in trades if not t['oos']]; oo = [t for t in trades if t['oos']]
    def s(x):
        if not x: return (0, 0.0, 0.0, 0.0)
        nn = len(x); wr = 100 * sum(1 for t in x if t['r'] > 0) / nn
        tot = sum(t['r'] for t in x)
        return (nn, wr, tot / nn, tot)
    ni, wi, ai, ti = s(is_t); no, wo, ao, to = s(oo)
    print(f'  {label:<28} | IS n={ni:>4} WR={wi:>4.0f}% AvgR={ai:>+.3f} TotR={ti:>+6.1f} '
          f'| OOS n={no:>3} WR={wo:>4.0f}% AvgR={ao:>+.3f} TotR={to:>+5.1f}')


def main():
    df = pd.read_parquet(m.DATA_M1).sort_values('ts_ms').reset_index(drop=True)
    for c in df.select_dtypes('object').columns: df[c] = df[c].fillna('')
    for c in df.select_dtypes('float').columns:  df[c] = df[c].fillna(0.0)
    for c in df.select_dtypes('bool').columns:    df[c] = df[c].fillna(False)

    ts = df['ts_ms'].values.astype(np.int64)
    close = df['close'].values.astype(float); high = df['high'].values.astype(float)
    low = df['low'].values.astype(float); op = df['open'].values.astype(float)

    # H1 buckets
    h1 = m.resamp(df, H1_MS); h1['atr'] = m.atr14(h1['high'].values, h1['low'].values, h1['close'].values)
    h1_high = dict(zip(h1['ts_ms'].astype(int), h1['high'].astype(float)))
    h1_atr  = dict(zip(h1['ts_ms'].astype(int), h1['atr'].astype(float)))
    cur_b = (ts // H1_MS) * H1_MS; prev_b = cur_b - H1_MS
    full_high = np.array([h1_high.get(int(b), np.nan) for b in cur_b])     # LOOKAHEAD
    full_atr  = np.array([h1_atr.get(int(b),  np.nan) for b in cur_b])     # LOOKAHEAD
    prev_high = np.array([h1_high.get(int(b), np.nan) for b in prev_b])    # causal
    prev_atr  = np.array([h1_atr.get(int(b),  np.nan) for b in prev_b])    # causal
    roll_high = pd.Series(high).rolling(60, min_periods=10).max().values   # causal ~1h

    stopA = full_high + 0.40 * full_atr
    stopB = prev_high + 0.40 * prev_atr
    stopC = roll_high + 0.40 * prev_atr

    # D1 régimen causal (bucket previo)
    d1 = m.resamp(df, D1_MS); d1['ema20'] = m.ema(d1['close'].values, 20)
    d1_ema = dict(zip(d1['ts_ms'].astype(int), d1['ema20'].astype(float)))
    d1_prev = (ts // D1_MS) * D1_MS - D1_MS
    de = np.array([d1_ema.get(int(b), np.nan) for b in d1_prev])
    d1_bear = np.isfinite(de) & (close <= de * 0.980)

    # setup: rejection@VAH + confirmación OF + régimen + H1 estructura + sesión
    rng = high - low
    with np.errstate(divide='ignore', invalid='ignore'):
        wick_up = (high - np.maximum(close, op)) / np.where(rng > 0, rng, np.nan)
    rej = (rng > 0) & (wick_up > 0.30) & (wick_up < 0.85) & (close <= op)
    vah = df['vp_vah'].values.astype(float)
    near_vah = (vah > 0) & (np.abs(high - vah) / np.where(vah > 0, vah, 1) <= TOL)
    hm = (ts // 60_000) % 1440; sess = (hm >= 7 * 60) & (hm < 20 * 60)
    pt = df['plus_ticks'].values.astype(float); mt = df['minus_ticks'].values.astype(float)
    of = df['body_below_poc'].values.astype(bool) & (mt > pt) & ~df['fp_absorb_buy'].values.astype(bool)
    h1s = df['h1_bos_bear'].values.astype(bool) | df['h1_choch_bear'].values.astype(bool)

    entry = near_vah & rej & sess & d1_bear & of & h1s

    print(f'Entradas que pasan gates: {entry.sum()}  (IS+OOS)')
    print('Setup: rejection@VAH + régimen D1 + H1 estructura + confirmación OF (shorts)\n')
    rep(sim(entry, close, high, low, ts, stopA), 'A) H1 full-bucket  [LOOKAHEAD]')
    rep(sim(entry, close, high, low, ts, stopB), 'B) H1 prev-closed  [causal]')
    rep(sim(entry, close, high, low, ts, stopC), 'C) rolling-60 high [causal]')


if __name__ == '__main__':
    main()
