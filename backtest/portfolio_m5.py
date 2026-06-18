"""
portfolio_m5.py
---------------
Combina top patrones M5 en portfolio con cooldown.
Objetivo: >= 2 trades/dia con EV positivo.

Uso:
    python backtest/portfolio_m5.py
    python backtest/portfolio_m5.py --cooldown 12
"""
import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT    = Path(__file__).parent.parent
DATA    = ROOT / 'data/bybit-spot/processed/btcusdt_m5.parquet'

TARGET_R     = 2.5
MIN_STOP_PCT = 0.30
MAX_STOP_PCT = 0.75
FORWARD_MAX  = 240
ATR_BUFF     = 0.30
H1_MS        = 3_600_000
D1_MS        = 86_400_000
BREAKEVEN_WR = 1 / (1 + TARGET_R)


def ema_np(vals, period):
    k = 2 / (period + 1)
    out = np.empty(len(vals))
    out[0] = vals[0]
    for i in range(1, len(vals)):
        out[i] = vals[i] * k + out[i - 1] * (1 - k)
    return out


def wilder_atr_np(high, low, close, period=14):
    n = len(high)
    tr = np.empty(n)
    tr[0] = high[0] - low[0]
    tr[1:] = np.maximum(high[1:] - low[1:],
              np.maximum(np.abs(high[1:] - close[:-1]),
                         np.abs(low[1:] - close[:-1])))
    out = np.empty(n)
    out[:period] = tr[:period].mean()
    k = 1 / period
    for i in range(period, n):
        out[i] = out[i - 1] * (1 - k) + tr[i] * k
    return out


def compute_h1_stop(df):
    h1_bucket = (df['ts_ms'].values // H1_MS) * H1_MS
    df2 = df.copy()
    df2['h1_ts'] = h1_bucket
    h1 = df2.groupby('h1_ts').agg(
        high=('high', 'max'), low=('low', 'min'), close=('close', 'last')
    ).reset_index().rename(columns={'h1_ts': 'ts_ms'})
    h1['atr14'] = wilder_atr_np(h1['high'].values, h1['low'].values,
                                 h1['close'].values, period=14)
    h1_map = {row.ts_ms: (row.high, row.atr14) for row in h1.itertuples()}
    n = len(df)
    stop_price = np.full(n, np.nan)
    stop_pct = np.full(n, np.nan)
    close = df['close'].values
    h1_run_h = {}
    for i in range(n):
        bkt = int(h1_bucket[i])
        h = float(df['high'].iat[i])
        h1_run_h[bkt] = max(h1_run_h.get(bkt, h), h)
        data = h1_map.get(bkt)
        if data is None:
            continue
        _, atr = data
        sp = h1_run_h[bkt] + ATR_BUFF * atr
        rk = (sp - close[i]) / close[i] * 100
        stop_price[i] = sp
        stop_pct[i] = rk
    return stop_price, stop_pct


def simulate_outcomes(df, stop_price, stop_pct):
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    ts_ms = df['ts_ms'].values
    n = len(df)
    valid_sess = df['session'].isin({'London', 'Overlap', 'NewYork'}).values \
                 if 'session' in df.columns else np.ones(n, dtype=bool)
    ema20 = df['ema20'].values if 'ema20' in df.columns else ema_np(close, 20)
    d1_ok = close <= ema20 * 1.005
    valid_stop = np.isfinite(stop_pct) & (stop_pct >= MIN_STOP_PCT) & (stop_pct <= MAX_STOP_PCT)
    candidate_mask = valid_sess & d1_ok & valid_stop
    candidates = np.where(candidate_mask)[0]
    print(f"  Candidatos validos: {len(candidates):,} / {n:,}")

    records = []
    t0 = time.time()
    for k, i in enumerate(candidates):
        if k % 5000 == 0 and k > 0:
            print(f"    {k:,}/{len(candidates):,}", end='\r')
        sp = stop_price[i]
        risk = sp - close[i]
        if risk <= 0:
            continue
        target = close[i] - TARGET_R * risk
        end = min(i + FORWARD_MAX + 1, n)
        fwd_h = high[i + 1: end]
        fwd_l = low[i + 1: end]
        if len(fwd_h) == 0:
            continue
        stop_hits = fwd_h >= sp
        tgt_hits = fwd_l <= target
        stop_bar = int(np.argmax(stop_hits)) if stop_hits.any() else FORWARD_MAX
        tgt_bar = int(np.argmax(tgt_hits)) if tgt_hits.any() else FORWARD_MAX
        if not stop_hits.any(): stop_bar = FORWARD_MAX
        if not tgt_hits.any(): tgt_bar = FORWARD_MAX
        if tgt_bar < stop_bar:
            res_r, outcome = TARGET_R, 'target'
        elif stop_bar < FORWARD_MAX:
            res_r, outcome = -1.0, 'stop'
        else:
            last_close = close[min(i + FORWARD_MAX, n - 1)]
            res_r = (close[i] - last_close) / risk
            outcome = 'timeout'
        records.append({'bar_idx': i, 'ts_ms': int(ts_ms[i]),
                        'result_r': round(res_r, 4), 'outcome': outcome,
                        'stop_pct': round(float(stop_pct[i]), 4)})
    print(f"\n  Simulados: {len(records):,} trades en {time.time() - t0:.1f}s")
    return pd.DataFrame(records)


def _lag(arr, k):
    result = np.roll(arr.astype(np.float32), k)
    result[:k] = 0.0
    return result.astype(bool)


def _recent(arr, window):
    return (pd.Series(arr.astype(np.float32))
              .rolling(window, min_periods=1)
              .max()
              .astype(bool)
              .values)


def build_patterns(df):
    n = len(df)

    def col(c, d=None):
        if c in df.columns:
            return df[c].values
        return np.full(n, d) if d is not None else None

    close = df['close'].values
    high = df['high'].values
    open_ = df['open'].values
    sess = col('session', 'OffHours')
    simb = col('stacked_imb', 'None')
    cvd_neg = col('cvd_consec_neg', np.zeros(n))
    cvd_div = col('cvd_div', np.zeros(n)).astype(bool)
    nah_arr = col('near_asian_high', np.zeros(n)).astype(bool)
    pdh_arr = col('near_pdh', np.zeros(n)).astype(bool)
    fib_ote = col('fib_ote', np.zeros(n)).astype(bool)
    poc = col('vp_poc', np.full(n, np.nan))

    stk_bear = simb == 'Bearish'

    below_poc = np.zeros(n, dtype=bool)
    if poc is not None:
        with np.errstate(invalid='ignore'):
            below_poc = close < poc

    # ── Patrones del M5 mining ──────────────────────────────────────────────

    # P1: below_poc + rec_near_ah_5b
    # WR=40.4%  AvgR=0.347  3.2/dia  [NUEVO CAMPEON]
    # Historia: cerro bajo el POC (vendedores dominan) Y estuvo cerca del Asian High en los ultimos 25min
    p1 = below_poc & _recent(nah_arr, 5)

    # P2: seq_asian_to_sess + stk_bear_1b
    # WR=41.0%  AvgR=0.372  1.7/dia  [campeon de M15, funciona en M5]
    # Historia: Asian High visitada recientemente -> London/NY -> stacked bear 5min atras
    p2 = (_recent(nah_arr, 3)
          & ((sess == 'London') | (sess == 'NewYork'))
          & _lag(stk_bear, 1))

    # P3: stk_bear_3b + near_ah_2b
    # WR=43.1%  AvgR=0.447  1.8/dia  [mejor WR+AvgR combinado]
    # Historia: stacked bear hace 15min Y cerca del Asian High hace 10min
    p3 = _lag(stk_bear, 3) & _lag(nah_arr, 2)

    # P4: near_ah_3b + stk_bear_1b
    # WR=40.2%  AvgR=0.362  2.2/dia  [cumple 2/dia solo]
    # Historia: cerca del Asian High hace 15min Y stacked bear hace 5min
    p4 = _lag(nah_arr, 3) & _lag(stk_bear, 1)

    # P5: rec_cvd_div_3b + rec_near_ah_5b
    # WR=37.8%  AvgR=0.286  4.3/dia  [mas frecuente, menor calidad]
    # Historia: CVD divergencia bajista en los ultimos 15min Y cerca Asian High en los ultimos 25min
    p5 = _recent(cvd_div, 3) & _recent(nah_arr, 5)

    # P6: below_poc + near_ah_3b
    # WR=42.1%  AvgR=0.394  2.3/dia  [variante de P1 mas estricta]
    # Historia: cerro bajo POC Y exactamente 15min atras estaba en Asian High
    p6 = below_poc & _lag(nah_arr, 3)

    return {
        'P1_bpoc_rec_ah5':    p1,
        'P2_asian_sess_stk':  p2,
        'P3_stk3b_ah2b':      p3,
        'P4_ah3b_stk1b':      p4,
        'P5_cvddiv_ah5':      p5,
        'P6_bpoc_ah3b':       p6,
    }


def apply_cooldown(signal_mask, cooldown_bars):
    out = np.zeros(len(signal_mask), dtype=bool)
    last = -9999
    for i in range(len(signal_mask)):
        if signal_mask[i] and (i - last) >= cooldown_bars:
            out[i] = True
            last = i
    return out


def stats(outcomes, mask, label=""):
    idx = outcomes['bar_idx'].values
    sub = outcomes[mask[idx]]
    n_t = len(sub)
    if n_t == 0:
        return None
    wins = (sub['result_r'] > 0).sum()
    wr = wins / n_t * 100
    avg_r = sub['result_r'].mean()
    return {'label': label, 'n': n_t, 'wr': wr,
            'avg_r': avg_r, 'total_r': n_t * avg_r, 'sub': sub}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cooldown', type=int, default=12,
                        help='Barras cooldown (default=12 = 1h en M5)')
    args = parser.parse_args()

    print(f"Portfolio M5  |  cooldown={args.cooldown} barras ({args.cooldown * 5}min = {args.cooldown * 5 // 60}h{args.cooldown * 5 % 60}min)")

    df = pd.read_parquet(DATA).sort_values('ts_ms').reset_index(drop=True)
    days_total = (df['ts_ms'].max() - df['ts_ms'].min()) / D1_MS
    print(f"  {len(df):,} barras M5  |  {days_total:.0f} dias")

    print("\n[1/3] H1 stop...")
    stop_price, stop_pct = compute_h1_stop(df)

    print("\n[2/3] Simulando outcomes...")
    outcomes = simulate_outcomes(df, stop_price, stop_pct)

    n_b = len(outcomes)
    wr_b = (outcomes['result_r'] > 0).sum() / n_b * 100
    avr_b = outcomes['result_r'].mean()
    print(f"\n  BASE: n={n_b:,}  WR={wr_b:.1f}%  AvgR={avr_b:.3f}  "
          f"({n_b / days_total:.1f}/dia)  Total_R={n_b * avr_b:.0f}R")
    print(f"  Breakeven WR = {BREAKEVEN_WR * 100:.1f}%")

    print("\n[3/3] Patrones y portfolios...")
    patterns = build_patterns(df)

    # ── Stats individuales ─────────────────────────────────────────────────────
    sep = "-" * 78
    print(f"\n{sep}")
    print("  PATRONES INDIVIDUALES (sin cooldown)")
    print(sep)
    print(f"  {'patron':<22}  {'n':>5}  {'/dia':>5}  {'WR%':>6}  {'AvgR':>7}  {'Total_R':>8}")
    print(sep)

    pat_stats = {}
    for name, pmask in patterns.items():
        st = stats(outcomes, pmask, name)
        pat_stats[name] = st
        if st:
            tpd = st['n'] / days_total
            ok = " <--" if tpd >= 2.0 and st['wr'] >= 38 else ""
            print(f"  {name:<22}  {st['n']:>5}  {tpd:>5.1f}  "
                  f"{st['wr']:>6.1f}%  {st['avg_r']:>7.3f}  {st['total_r']:>8.1f}R{ok}")

    # ── Overlap ────────────────────────────────────────────────────────────────
    print(f"\n{sep}")
    print("  OVERLAP (% de señales fila que coinciden con columna)")
    print(sep)
    pnames = list(patterns.keys())
    idx_arr = outcomes['bar_idx'].values
    poc_cands = {k: v[idx_arr] for k, v in patterns.items()}

    hdr = f"  {'':22}"
    for n2 in pnames:
        hdr += f"  {n2[:7]:>7}"
    print(hdr)
    for n1 in pnames:
        m1 = poc_cands[n1]
        row = f"  {n1:<22}"
        for n2 in pnames:
            m2 = poc_cands[n2]
            ov = (m1 & m2).sum() / max(m1.sum(), 1) * 100
            row += f"  {ov:>6.0f}%"
        print(row)

    # ── Portfolios combinados ─────────────────────────────────────────────────
    print(f"\n{sep}")
    print(f"  PORTFOLIOS COMBINADOS  (cooldown={args.cooldown} barras = {args.cooldown * 5}min)")
    print(sep)

    combos = {
        "A: P1+P2+P3 (calidad)":    ['P1_bpoc_rec_ah5', 'P2_asian_sess_stk', 'P3_stk3b_ah2b'],
        "B: P1+P4+P2 (frecuencia)": ['P1_bpoc_rec_ah5', 'P4_ah3b_stk1b',    'P2_asian_sess_stk'],
        "C: P1+P6+P2 (poc)":        ['P1_bpoc_rec_ah5', 'P6_bpoc_ah3b',      'P2_asian_sess_stk'],
        "D: P1+P2+P4+P3 (todo)":    ['P1_bpoc_rec_ah5', 'P2_asian_sess_stk', 'P4_ah3b_stk1b',   'P3_stk3b_ah2b'],
        "E: P1+P5 (cvd_div)":       ['P1_bpoc_rec_ah5', 'P5_cvddiv_ah5'],
        "F: P2+P3+P4 (sin poc)":    ['P2_asian_sess_stk', 'P3_stk3b_ah2b',   'P4_ah3b_stk1b'],
    }

    print(f"  {'portfolio':<30}  {'n':>5}  {'/dia':>5}  {'WR%':>6}  {'AvgR':>7}  {'Total_R':>8}  {'EV/dia':>7}")
    print(f"  {'-' * 72}")

    best_ev = 0
    best_name = ""
    best_st = None

    for label, pat_list in combos.items():
        combined = np.zeros(len(df), dtype=bool)
        for p in pat_list:
            if p in patterns:
                combined |= patterns[p]
        combined_cd = apply_cooldown(combined, args.cooldown)
        st = stats(outcomes, combined_cd, label)
        if st is None:
            continue
        tpd = st['n'] / days_total
        ev_d = tpd * st['avg_r']
        ok = " <--" if tpd >= 2.0 and st['wr'] >= 38.0 else ""
        print(f"  {label:<30}  {st['n']:>5}  {tpd:>5.1f}  "
              f"{st['wr']:>6.1f}%  {st['avg_r']:>7.3f}  "
              f"{st['total_r']:>8.1f}R  {ev_d:>6.2f}R{ok}")
        if ev_d > best_ev:
            best_ev = ev_d
            best_name = label
            best_st = st

    # ── Resumen del mejor ─────────────────────────────────────────────────────
    if best_st:
        print(f"\n{sep}")
        print(f"  MEJOR PORTFOLIO: {best_name}")
        tpd = best_st['n'] / days_total
        print(f"  Trades/dia  : {tpd:.1f}")
        print(f"  WR          : {best_st['wr']:.1f}%  (breakeven={BREAKEVEN_WR * 100:.1f}%)")
        print(f"  AvgR        : {best_st['avg_r']:+.3f}R")
        print(f"  EV/dia      : {best_ev:.2f}R")
        print(f"  Total_R     : {best_st['total_r']:.0f}R en {days_total:.0f} dias")

        sub = best_st['sub']
        print(f"\n  Breakdown por resultado:")
        for oc in ['target', 'stop', 'timeout']:
            n_oc = (sub['outcome'] == oc).sum()
            if n_oc == 0:
                continue
            r_oc = sub.loc[sub['outcome'] == oc, 'result_r'].mean()
            print(f"    {oc:<10}: {n_oc:>4}  ({n_oc / len(sub) * 100:.1f}%)  AvgR={r_oc:+.3f}")

        print(f"\n  Distribucion mensual:")
        sub2 = sub.copy()
        sub2['month'] = pd.to_datetime(sub2['ts_ms'], unit='ms', utc=True).dt.strftime('%Y-%m')
        for mo, grp in sub2.groupby('month'):
            wr_m = (grp['result_r'] > 0).sum() / len(grp) * 100
            avg_m = grp['result_r'].mean()
            tpd_m = len(grp) / 30
            print(f"    {mo}  n={len(grp):>3}  {tpd_m:.1f}/d  WR={wr_m:.0f}%  AvgR={avg_m:+.3f}")


if __name__ == '__main__':
    main()
