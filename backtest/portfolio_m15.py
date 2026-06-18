"""
portfolio_m15.py
----------------
Combina los top patrones M15 en un portfolio con cooldown.
Objetivo: >= 2 trades/dia con EV positivo.

Uso:
    python backtest/portfolio_m15.py
    python backtest/portfolio_m15.py --cooldown 4 --wr-min 35
"""

import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT    = Path(__file__).parent.parent
DATA    = ROOT / 'data/bybit-spot/processed/btcusdt_m15.parquet'

TARGET_R     = 2.5
MIN_STOP_PCT = 0.30
MAX_STOP_PCT = 0.75
FORWARD_MAX  = 80        # ~20h en M15
ATR_BUFF     = 0.30
H1_MS        = 3_600_000
D1_MS        = 86_400_000
BREAKEVEN_WR = 1 / (1 + TARGET_R)


# â”€â”€ helpers (copiad de mine_mtf_sequences) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def ema_np(vals, period):
    k   = 2 / (period + 1)
    out = np.empty(len(vals))
    out[0] = vals[0]
    for i in range(1, len(vals)):
        out[i] = vals[i] * k + out[i - 1] * (1 - k)
    return out


def wilder_atr_np(high, low, close, period=14):
    n  = len(high)
    tr = np.empty(n)
    tr[0] = high[0] - low[0]
    tr[1:] = np.maximum(high[1:] - low[1:],
              np.maximum(np.abs(high[1:] - close[:-1]),
                         np.abs(low[1:]  - close[:-1])))
    out = np.empty(n)
    out[:period] = tr[:period].mean()
    k = 1 / period
    for i in range(period, n):
        out[i] = out[i - 1] * (1 - k) + tr[i] * k
    return out


def compute_h1_stop(df):
    h1_bucket = (df['ts_ms'].values // H1_MS) * H1_MS
    df2 = df.copy(); df2['h1_ts'] = h1_bucket
    h1  = df2.groupby('h1_ts').agg(
        high =('high','max'), low=('low','min'), close=('close','last')
    ).reset_index().rename(columns={'h1_ts':'ts_ms'})
    h1['atr14'] = wilder_atr_np(h1['high'].values, h1['low'].values,
                                 h1['close'].values, period=14)
    h1_map    = {row.ts_ms: (row.high, row.atr14) for row in h1.itertuples()}
    n          = len(df)
    stop_price = np.full(n, np.nan)
    stop_pct   = np.full(n, np.nan)
    close      = df['close'].values
    h1_run_h   = {}
    for i in range(n):
        bkt = int(h1_bucket[i])
        h   = float(df['high'].iat[i])
        h1_run_h[bkt] = max(h1_run_h.get(bkt, h), h)
        data = h1_map.get(bkt)
        if data is None: continue
        _, atr = data
        sp  = h1_run_h[bkt] + ATR_BUFF * atr
        rk  = (sp - close[i]) / close[i] * 100
        stop_price[i] = sp
        stop_pct[i]   = rk
    return stop_price, stop_pct


def simulate_outcomes(df, stop_price, stop_pct):
    close      = df['close'].values
    high       = df['high'].values
    low        = df['low'].values
    ts_ms      = df['ts_ms'].values
    n          = len(df)
    valid_sess = df['session'].isin({'London','Overlap','NewYork'}).values \
                 if 'session' in df.columns else np.ones(n, dtype=bool)
    ema20      = df['ema20'].values if 'ema20' in df.columns else ema_np(close, 20)
    d1_ok      = close <= ema20 * 1.005
    valid_stop = np.isfinite(stop_pct) & (stop_pct >= MIN_STOP_PCT) & (stop_pct <= MAX_STOP_PCT)
    candidate_mask = valid_sess & d1_ok & valid_stop
    candidates     = np.where(candidate_mask)[0]
    print(f"  Candidatos validos: {len(candidates):,} / {n:,} barras")

    records = []
    t0 = time.time()
    for k, i in enumerate(candidates):
        sp   = stop_price[i]
        risk = sp - close[i]
        if risk <= 0: continue
        target = close[i] - TARGET_R * risk
        end    = min(i + FORWARD_MAX + 1, n)
        fwd_h  = high[i + 1: end]
        fwd_l  = low[i + 1:  end]
        if len(fwd_h) == 0: continue
        stop_hits = fwd_h >= sp
        tgt_hits  = fwd_l <= target
        stop_bar  = int(np.argmax(stop_hits)) if stop_hits.any() else FORWARD_MAX
        tgt_bar   = int(np.argmax(tgt_hits))  if tgt_hits.any()  else FORWARD_MAX
        if not stop_hits.any(): stop_bar = FORWARD_MAX
        if not tgt_hits.any():  tgt_bar  = FORWARD_MAX
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
    print(f"  Simulados: {len(records):,} trades en {time.time()-t0:.1f}s")
    return pd.DataFrame(records)


# â”€â”€ Condiciones para los 4 patrones del portfolio â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
    """
    Devuelve dict: nombre_patron -> array bool (barra entra en el patron).
    Patrones seleccionados del M15 mining (depth=2, min-n=30).
    """
    n    = len(df)
    def col(c, d=None):
        if c in df.columns: return df[c].values
        return np.full(n, d) if d is not None else None

    close   = df['close'].values
    high    = df['high'].values
    open_   = df['open'].values
    rng     = high - np.minimum(close, open_) + np.maximum(close, open_) - df['low'].values + 1e-9

    sess    = col('session', 'OffHours')
    simb    = col('stacked_imb', 'None')
    cvd_neg = col('cvd_consec_neg', np.zeros(n))
    nah_arr = col('near_asian_high', np.zeros(n)).astype(bool)
    pdh_arr = col('near_pdh',        np.zeros(n)).astype(bool)
    fib_ote = col('fib_ote',         np.zeros(n)).astype(bool)
    poc     = col('vp_poc', np.full(n, np.nan))
    vwap    = col('vwap', close)

    wick_up = high - np.maximum(close, open_)
    body    = np.abs(close - open_)
    rng_f   = high - df['low'].values + 1e-9
    is_shoot = (wick_up / rng_f > 0.45) & (body / rng_f < 0.40)

    stk_bear = simb == 'Bearish'

    # Patron 1: seq_asian_to_sess + stk_bear_1b
    # WR=43.8%  AvgR=0.456  0.6/dia
    # Historia: Asian High visitada recientemente â†’ London/NY abre â†’ stacked bear 15min atras
    p1 = (_recent(nah_arr, 3)
          & ((sess == 'London') | (sess == 'NewYork'))
          & _lag(stk_bear, 1))

    # Patron 2: near_pdh + rec_near_ah_5b
    # WR=38.5%  AvgR=0.311  1.1/dia
    # Historia: ahora cerca de PDH Y Asian High fue visitada en la ultima hora y cuarto
    p2 = (pdh_arr
          & _recent(nah_arr, 5))

    # Patron 3: fib_ote + sess_london
    # WR=35.6%  AvgR=0.226  1.3/dia
    # Historia: precio en zona OTE (50-bar swing) dentro de sesion London
    p3 = (fib_ote
          & (sess == 'London'))

    # Patron 4: near_ah_2b + cvd_mom_3neg
    # WR=39.7%  AvgR=0.337  0.9/dia
    # Historia: cercano al Asian High hace 30min Y CVD negativo 3+ barras consecutivas
    p4 = (_lag(nah_arr, 2)
          & (cvd_neg >= 3))

    # Patron 5 (bonus): near_ah_2b + stk_bear_1b
    # WR=42.5%  AvgR=0.419  0.7/dia  â€” distinto de P1 porque no exige sesion
    p5 = (_lag(nah_arr, 2)
          & _lag(stk_bear, 1))

    return {
        'P1_asian_sess_stk': p1,
        'P2_pdh_rec_ah':     p2,
        'P3_fib_ote_london': p3,
        'P4_ah2b_cvd_neg':   p4,
        'P5_ah2b_stk_bear':  p5,
    }


# â”€â”€ Portfolio con cooldown â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def apply_cooldown(signal_mask, cooldown_bars):
    """
    Dado un array bool de seÃ±ales, retorna array bool con cooldown:
    tras una seÃ±al activa, silencia las proximas `cooldown_bars` barras.
    """
    out  = np.zeros(len(signal_mask), dtype=bool)
    last = -9999
    for i in range(len(signal_mask)):
        if signal_mask[i] and (i - last) >= cooldown_bars:
            out[i] = True
            last   = i
    return out


def portfolio_stats(outcomes, combined_mask, label="PORTFOLIO"):
    idx     = outcomes['bar_idx'].values
    matched = combined_mask[idx]
    sub     = outcomes[matched]
    n_t     = len(sub)
    if n_t == 0:
        print(f"  {label}: 0 trades")
        return None
    wins  = (sub['result_r'] > 0).sum()
    wr    = wins / n_t * 100
    avg_r = sub['result_r'].mean()
    total = n_t * avg_r
    return {'n': n_t, 'wr': wr, 'avg_r': avg_r, 'total_r': total, 'sub': sub}


# â”€â”€ Main â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cooldown', type=int, default=4,
                        help='Barras de cooldown post-seÃ±al (default=4 = 1h en M15)')
    parser.add_argument('--wr-min',   type=float, default=35.0)
    parser.add_argument('--no-p5',    action='store_true',
                        help='Excluir P5 del portfolio (solapa con P1)')
    args = parser.parse_args()

    print(f"Portfolio M15  |  cooldown={args.cooldown} barras (~{args.cooldown*15}min)")
    print(f"Data: {DATA.name}")

    df = pd.read_parquet(DATA).sort_values('ts_ms').reset_index(drop=True)
    days_total = (df['ts_ms'].max() - df['ts_ms'].min()) / D1_MS
    print(f"  {len(df):,} barras M15  |  {days_total:.0f} dias")

    # H1 stop + simulacion
    print("\n[1/3] Calculando H1 stop...")
    stop_price, stop_pct = compute_h1_stop(df)

    print("\n[2/3] Simulando outcomes...")
    outcomes = simulate_outcomes(df, stop_price, stop_pct)

    n_tot = len(outcomes)
    wins_base = (outcomes['result_r'] > 0).sum()
    print(f"\n  BASE (sin filtros): n={n_tot:,}  "
          f"WR={wins_base/n_tot*100:.1f}%  AvgR={outcomes['result_r'].mean():.3f}  "
          f"Total_R={n_tot*outcomes['result_r'].mean():.0f}R  "
          f"({n_tot/days_total:.1f}/dia)")

    # Construir patrones
    print("\n[3/3] Evaluando patrones y portfolio...")
    patterns = build_patterns(df)

    if args.no_p5:
        patterns.pop('P5_ah2b_stk_bear', None)

    # â”€â”€ Stats por patron â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    print(f"\n{'â”€'*80}")
    print(f"  PATRONES INDIVIDUALES (sin cooldown)")
    print(f"{'â”€'*80}")
    print(f"  {'patron':<25}  {'n':>5}  {'/dia':>5}  {'WR%':>6}  {'AvgR':>7}  {'Total_R':>8}")
    print(f"{'â”€'*80}")

    for name, pmask in patterns.items():
        st = portfolio_stats(outcomes, pmask, name)
        if st:
            tpd = st['n'] / days_total
            flag = " <-- WR ok" if st['wr'] >= args.wr_min else ""
            print(f"  {name:<25}  {st['n']:>5}  {tpd:>5.1f}  "
                  f"{st['wr']:>6.1f}%  {st['avg_r']:>7.3f}  {st['total_r']:>8.1f}R{flag}")

    # â”€â”€ Overlap matrix â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    print(f"\n{'â”€'*80}")
    print("  OVERLAP ENTRE PATRONES (% de seÃ±ales de la fila que coinciden con la columna)")
    print(f"{'â”€'*80}")
    pnames = list(patterns.keys())
    idx_arr = outcomes['bar_idx'].values

    # Para calcular overlap sobre candidatos
    pat_on_candidates = {k: v[idx_arr] for k, v in patterns.items()}

    header = "  " + " " * 25
    for n2 in pnames:
        header += f"  {n2[:8]:>8}"
    print(header)
    for n1 in pnames:
        m1 = pat_on_candidates[n1]
        row = f"  {n1:<25}"
        for n2 in pnames:
            m2 = pat_on_candidates[n2]
            if m1.sum() == 0:
                row += f"  {'N/A':>8}"
            else:
                overlap = (m1 & m2).sum() / m1.sum() * 100
                row += f"  {overlap:>7.1f}%"
        print(row)

    # â”€â”€ Portfolio combinado â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    print(f"\n{'â”€'*80}")
    print(f"  PORTFOLIOS COMBINADOS (cooldown={args.cooldown} barras = {args.cooldown*15}min)")
    print(f"{'â”€'*80}")

    all_masks = list(patterns.values())

    # Portfolio A: todos los patrones
    combined_a = np.zeros(len(df), dtype=bool)
    for m in all_masks:
        combined_a |= m
    combined_a_cd = apply_cooldown(combined_a, args.cooldown)
    st_a = portfolio_stats(outcomes, combined_a_cd, "PORTFOLIO_A_TODOS")

    # Portfolio B: solo los 3 mas independientes (P1, P2, P3)
    combined_b = patterns['P1_asian_sess_stk'] | patterns['P2_pdh_rec_ah'] | patterns['P3_fib_ote_london']
    combined_b_cd = apply_cooldown(combined_b, args.cooldown)
    st_b = portfolio_stats(outcomes, combined_b_cd, "PORTFOLIO_B_P123")

    # Portfolio C: P1 + P4 (sin solapo: P1=bearish_imb lag, P4=CVD_mom)
    combined_c = patterns['P1_asian_sess_stk'] | patterns['P4_ah2b_cvd_neg']
    combined_c_cd = apply_cooldown(combined_c, args.cooldown)
    st_c = portfolio_stats(outcomes, combined_c_cd, "PORTFOLIO_C_P14")

    # Portfolio D: P1 + P2 + P4 (sin OTE)
    combined_d = (patterns['P1_asian_sess_stk']
                  | patterns['P2_pdh_rec_ah']
                  | patterns['P4_ah2b_cvd_neg'])
    combined_d_cd = apply_cooldown(combined_d, args.cooldown)
    st_d = portfolio_stats(outcomes, combined_d_cd, "PORTFOLIO_D_P124")

    # Mostrar todos los portfolios
    combos = [
        ("A: todos",       st_a),
        ("B: P1+P2+P3",    st_b),
        ("C: P1+P4",       st_c),
        ("D: P1+P2+P4",    st_d),
    ]
    print(f"  {'portfolio':<20}  {'n':>5}  {'/dia':>5}  {'WR%':>6}  {'AvgR':>7}  {'Total_R':>8}  {'EV_dia':>8}")
    print(f"  {'â”€'*70}")
    for label, st in combos:
        if st is None: continue
        tpd   = st['n'] / days_total
        ev_d  = tpd * st['avg_r']
        flag = " <--" if tpd >= 2.0 and st['wr'] >= args.wr_min else ""
        print(f"  {label:<20}  {st['n']:>5}  {tpd:>5.1f}  "
              f"{st['wr']:>6.1f}%  {st['avg_r']:>7.3f}  {st['total_r']:>8.1f}R  "
              f"{ev_d:>7.2f}R/d{flag}")

    # â”€â”€ Resumen del mejor portfolio â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    best = max(combos, key=lambda x: (x[1]['n']/days_total * x[1]['avg_r']) if x[1] else 0)
    print(f"\n{'â”€'*80}")
    print(f"  MEJOR: {best[0]}")
    st = best[1]
    tpd = st['n'] / days_total
    print(f"  Trades/dia: {tpd:.1f}")
    print(f"  WR        : {st['wr']:.1f}%  (breakeven={BREAKEVEN_WR*100:.1f}%)")
    print(f"  AvgR      : {st['avg_r']:+.3f}R")
    print(f"  EV/dia    : {tpd * st['avg_r']:.2f}R")
    print(f"  Total_R   : {st['total_r']:.0f}R en {days_total:.0f} dias")

    # Desglose por resultado
    sub = st['sub']
    for oc in ['target', 'stop', 'timeout']:
        n_oc = (sub['outcome'] == oc).sum()
        r_oc = sub.loc[sub['outcome'] == oc, 'result_r'].mean() if n_oc > 0 else 0
        print(f"    {oc:<10}: {n_oc:>4} ({n_oc/len(sub)*100:.1f}%)  AvgR={r_oc:+.3f}")

    # Distribucion mensual
    print(f"\n  Distribucion mensual:")
    sub2 = sub.copy()
    sub2['month'] = pd.to_datetime(sub2['ts_ms'], unit='ms', utc=True).dt.strftime('%Y-%m')
    m_days = 30  # approx
    for mo, grp in sub2.groupby('month'):
        wr_m = (grp['result_r'] > 0).sum() / len(grp) * 100
        avg_m = grp['result_r'].mean()
        tpd_m = len(grp) / m_days
        print(f"    {mo}  n={len(grp):>3}  {tpd_m:.1f}/d  WR={wr_m:.0f}%  AvgR={avg_m:+.3f}")


if __name__ == '__main__':
    main()

