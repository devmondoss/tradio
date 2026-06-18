"""Distribución mensual de trades usando el mismo stop H1 que el mining."""
import time
import numpy as np
import pandas as pd
import sys
sys.path.insert(0, 'backtest')
from mine_spot_patterns import compute_h1_stop, simulate_outcomes, build_conditions

df = pd.read_parquet(
    'data/bybit-spot/processed/btcusdt_m1.parquet'
).sort_values('ts_ms').reset_index(drop=True)

first = pd.Timestamp(df['ts_ms'].iloc[0],  unit='ms', tz='UTC')
last  = pd.Timestamp(df['ts_ms'].iloc[-1], unit='ms', tz='UTC')
print(f"Dataset: {len(df):,} barras | {first.date()} -> {last.date()} ({(last-first).days} dias)\n")

print("Calculando H1 stop...")
stop_price, stop_pct = compute_h1_stop(df)

print("Simulando outcomes...")
outcomes = simulate_outcomes(df, stop_price, stop_pct)
n_tot = len(outcomes)
wins  = (outcomes['result_r'] > 0).sum()
print(f"BASE: n={n_tot:,}  WR={wins/n_tot*100:.1f}%\n")

conds = build_conditions(df)

# Patterns de interes
patterns = {
    'eq_high+bid_wall+val_near':      conds['equal_high'] & conds['bid_wall'] & conds['val_near'],
    'eq_high+bid_wall+below_vwap':    conds['equal_high'] & conds['bid_wall'] & conds['below_vwap'],
    'eq_high+bid_wall+stacked_bear':  conds['equal_high'] & conds['bid_wall'] & conds['stacked_bear'],
    'eq_high+bid_wall+below_poc':     conds['equal_high'] & conds['bid_wall'] & conds['below_poc'],
    'eq_high+near_ob+sess_london':    conds['equal_high'] & conds['near_bearish_ob'] & conds['sess_london'],
    'near_pdh+trenddown+sess_ny':     conds['near_pdh'] & conds['reg_trenddown'] & conds['sess_ny'],
    'abs_bid+dz_buyers':              conds['abs_bid'] & conds['dz_buyers'],
    'cvd5neg+trenddown+sweep':        conds['cvd_mom_5neg'] & conds['reg_trenddown'] & conds['sweep_confirmed'],
    'bt_bull+ask_wall+post_comp':     conds['big_trade_bullish'] & conds['ask_wall'] & conds['post_compression'],
}

ts       = pd.to_datetime(df['ts_ms'], unit='ms', utc=True)
out_ts   = pd.to_datetime(outcomes['ts_ms'], unit='ms', utc=True)
out_month = out_ts.dt.to_period('M')
idx_arr  = outcomes['bar_idx'].values
res_arr  = outcomes['result_r'].values

all_months = sorted(set(out_month))

print(f"{'Patron':<38} | tot | /mes | WR%  | AvgR")
print('-' * 72)
for name, mask in patterns.items():
    matched = mask[idx_arr]
    sub = outcomes[matched]
    total = len(sub)
    if total == 0:
        print(f"{name:<38} |   0 |  --- |  --- |  ---")
        continue
    wins_p  = (sub['result_r'] > 0).sum()
    wr      = wins_p / total * 100
    avg_r   = sub['result_r'].mean()
    sub_month = out_month[matched]
    by_month  = sub_month.value_counts().sort_index()
    active    = (by_month > 0).sum()
    avg_per   = total / max(active, 1)
    print(f"{name:<38} | {total:>3} | {avg_per:>4.1f} | {wr:>4.1f}% | {avg_r:>+.3f}")
    detail = '  '.join(f"{str(m)[2:]}:{v}" for m, v in sorted(by_month.items()))
    print(f"   [{detail}]")

print(f"\nTotal candidatos base: {n_tot:,} / {len(df):,} ({n_tot/len(df)*100:.1f}%)")
months_range = (last - first).days / 30
print(f"Periodo: {months_range:.1f} meses  ({first.date()} -> {last.date()})")
