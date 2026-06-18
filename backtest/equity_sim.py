"""
equity_sim.py
-------------
Simula curva de equity con compounding desde $500.
Usa los mismos patrones y cooldown del portfolio_m5.py (Portfolio A).

Uso:
    python backtest/equity_sim.py
    python backtest/equity_sim.py --risk 2 --start 500
"""
import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
DATA = ROOT / 'data/bybit-spot/processed/btcusdt_m5.parquet'

TARGET_R     = 2.5
MIN_STOP_PCT = 0.30
MAX_STOP_PCT = 0.75
FORWARD_MAX  = 240
ATR_BUFF     = 0.30
H1_MS        = 3_600_000
D1_MS        = 86_400_000


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

    records = []
    for i in candidates:
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
                        'result_r': round(res_r, 4), 'outcome': outcome})
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


def build_portfolio_mask(df):
    n = len(df)

    def col(c, d=None):
        if c in df.columns:
            return df[c].values
        return np.full(n, d) if d is not None else None

    close = df['close'].values
    sess = col('session', 'OffHours')
    simb = col('stacked_imb', 'None')
    cvd_neg = col('cvd_consec_neg', np.zeros(n))
    nah_arr = col('near_asian_high', np.zeros(n)).astype(bool)
    poc = col('vp_poc', np.full(n, np.nan))
    stk_bear = simb == 'Bearish'

    below_poc = np.zeros(n, dtype=bool)
    if poc is not None:
        with np.errstate(invalid='ignore'):
            below_poc = close < poc

    p1 = below_poc & _recent(nah_arr, 5)
    p2 = (_recent(nah_arr, 3) & ((sess == 'London') | (sess == 'NewYork')) & _lag(stk_bear, 1))
    p3 = _lag(stk_bear, 3) & _lag(nah_arr, 2)
    p4 = _lag(nah_arr, 3) & _lag(stk_bear, 1)

    return p1 | p2 | p3 | p4   # Portfolio D


def apply_cooldown(signal_mask, cooldown_bars):
    out = np.zeros(len(signal_mask), dtype=bool)
    last = -9999
    for i in range(len(signal_mask)):
        if signal_mask[i] and (i - last) >= cooldown_bars:
            out[i] = True
            last = i
    return out


def run_equity(trades_df, start_capital, risk_pct):
    """Simula curva de equity con compounding."""
    capital = start_capital
    risk_frac = risk_pct / 100.0
    peak = start_capital
    max_dd = 0.0
    rows = []

    for _, row in trades_df.iterrows():
        risk_dollar = capital * risk_frac
        pnl = row['result_r'] * risk_dollar
        capital += pnl
        if capital > peak:
            peak = capital
        dd = (peak - capital) / peak * 100
        if dd > max_dd:
            max_dd = dd
        rows.append({
            'ts_ms':    row['ts_ms'],
            'result_r': row['result_r'],
            'outcome':  row['outcome'],
            'risk_$':   round(risk_dollar, 2),
            'pnl_$':    round(pnl, 2),
            'capital':  round(capital, 2),
            'drawdown': round(dd, 2),
        })

    return pd.DataFrame(rows), max_dd


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start',    type=float, default=500.0)
    parser.add_argument('--risk',     type=float, default=2.0,
                        help='Porcentaje del capital arriesgado por trade (default=2%%)')
    parser.add_argument('--cooldown', type=int,   default=4)
    args = parser.parse_args()

    print(f"Equity Sim  |  start=${args.start:.0f}  risk={args.risk}%/trade  cooldown={args.cooldown}x5min")

    df = pd.read_parquet(DATA).sort_values('ts_ms').reset_index(drop=True)
    days_total = (df['ts_ms'].max() - df['ts_ms'].min()) / D1_MS

    print("Calculando stop H1...")
    stop_price, stop_pct = compute_h1_stop(df)

    print("Simulando outcomes...")
    outcomes = simulate_outcomes(df, stop_price, stop_pct)

    print("Aplicando portfolio y cooldown...")
    port_mask = build_portfolio_mask(df)
    port_cd   = apply_cooldown(port_mask, args.cooldown)

    idx_arr = outcomes['bar_idx'].values
    matched = port_cd[idx_arr]
    trades  = outcomes[matched].copy().sort_values('ts_ms').reset_index(drop=True)

    print(f"  {len(trades)} trades en {days_total:.0f} dias ({len(trades)/days_total:.1f}/dia)")

    equity, max_dd = run_equity(trades, args.start, args.risk)

    final   = equity['capital'].iloc[-1]
    total_r = trades['result_r'].sum()
    wr      = (trades['result_r'] > 0).sum() / len(trades) * 100
    avg_r   = trades['result_r'].mean()

    sep = "-" * 52
    print(f"\n{sep}")
    print(f"  RESULTADO TOTAL ({days_total:.0f} dias = ~{days_total/30:.1f} meses)")
    print(sep)
    print(f"  Capital inicial : ${args.start:>10.2f}")
    print(f"  Capital final   : ${final:>10.2f}")
    print(f"  PnL total       : ${final - args.start:>+10.2f}  ({(final/args.start - 1)*100:+.1f}%)")
    print(f"  Total R ganado  : {total_r:>+.1f}R")
    print(f"  Max Drawdown    : {max_dd:.1f}%")
    print(f"  WR              : {wr:.1f}%")
    print(f"  AvgR/trade      : {avg_r:+.3f}R")
    print(f"  Trades totales  : {len(trades)}")

    # Por outcome
    for oc in ['target', 'stop', 'timeout']:
        sub = equity[equity['outcome'] == oc]
        if len(sub) == 0:
            continue
        pnl_oc = sub['pnl_$'].sum()
        print(f"    {oc:<10}: {len(sub):>4} trades  PnL=${pnl_oc:>+8.2f}")

    # Mensual
    print(f"\n{sep}")
    print("  MES-A-MES")
    print(sep)
    print(f"  {'mes':<8}  {'trades':>6}  {'/dia':>5}  {'WR%':>5}  {'PnL($)':>9}  {'balance':>10}  {'DD%':>5}")
    print(f"  {'-'*52}")

    equity['month'] = pd.to_datetime(equity['ts_ms'], unit='ms', utc=True).dt.strftime('%Y-%m')
    month_start = args.start

    for mo, grp in equity.groupby('month'):
        n_mo   = len(grp)
        wr_mo  = (grp['result_r'] > 0).sum() / n_mo * 100
        pnl_mo = grp['pnl_$'].sum()
        bal_mo = grp['capital'].iloc[-1]
        dd_mo  = grp['drawdown'].max()
        tpd_mo = n_mo / 30
        sign   = "+" if pnl_mo >= 0 else ""
        print(f"  {mo}  {n_mo:>6}  {tpd_mo:>5.1f}  {wr_mo:>5.0f}%  "
              f"${pnl_mo:>+8.2f}  ${bal_mo:>9.2f}  {dd_mo:>4.1f}%")
        month_start = bal_mo

    # Worst streak
    results = equity['result_r'].values
    max_loss_streak = 0
    cur_streak = 0
    for r in results:
        if r < 0:
            cur_streak += 1
            max_loss_streak = max(max_loss_streak, cur_streak)
        else:
            cur_streak = 0

    print(f"\n  Racha perdedora maxima : {max_loss_streak} trades consecutivos")
    print(f"  Max Drawdown           : {max_dd:.1f}%  (${args.start * max_dd/100:.2f} sobre capital inicial)")


if __name__ == '__main__':
    main()
