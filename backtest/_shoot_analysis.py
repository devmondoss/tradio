"""
Analisis de is_shoot como trigger obligatorio.
Busca: CONTEXTO (estructura) + ORDERFLOW (presion) + is_shoot (trigger M1)
"""
import sys
sys.path.insert(0, 'backtest')

import numpy as np
import pandas as pd
from pathlib import Path

# Importar funciones del pipeline de mining
from mine_mtf_sequences import (
    compute_h1_stop, simulate_outcomes, build_sequence_conditions
)

ROOT = Path('.')
DAYS = 351

print("Cargando datos...")
df = pd.read_parquet('data/bybit-spot/processed/btcusdt_m1.parquet').sort_values('ts_ms').reset_index(drop=True)
print(f"  {len(df):,} barras | {df.shape[1]} columnas")

print("H1 stop...")
stop_price, stop_pct = compute_h1_stop(df)

print("Simulando outcomes...")
outcomes = simulate_outcomes(df, stop_price, stop_pct)
n_tot = len(outcomes)
idx_arr = outcomes['bar_idx'].values

wr_base = (outcomes['result_r'] > 0).sum() / n_tot * 100
print(f"\nBASE: n={n_tot:,}  WR={wr_base:.1f}%\n")

# Construir condiciones secuenciales
conds = build_sequence_conditions(df)

# ── 0. is_shoot solo ──────────────────────────────────────────────────────────
shoot = conds['is_shoot']
shoot_matched = shoot[idx_arr]
sub0 = outcomes[shoot_matched]
n0 = len(sub0)
wr0 = (sub0['result_r'] > 0).sum() / n0 * 100
avg0 = sub0['result_r'].mean()
print(f"is_shoot (solo):  n={n0:,}  {n0/DAYS:.1f}/dia  WR={wr0:.1f}%  AvgR={avg0:.3f}  Total={n0*avg0:.0f}R")
print()

def _get(k):
    if k in conds:
        return conds[k]
    if k in df.columns:
        return df[k].values.astype(bool)
    return np.zeros(len(df), dtype=bool)

# ── 1. Contextos estructurales + shoot ───────────────────────────────────────
structural = {
    'above_poc+london':           _get('above_poc')       & _get('sess_london'),
    'above_poc+london+bvwap':     _get('above_poc')       & _get('sess_london') & _get('below_vwap'),
    'above_poc+london+avwap':     _get('above_poc')       & _get('sess_london') & _get('above_vwap'),
    'near_ah+london':             _get('near_asian_high') & _get('sess_london'),
    'near_ah+london+avwap':       _get('near_asian_high') & _get('sess_london') & _get('above_vwap'),
    'rec_ah5+london':             _get('rec_near_ah_5b')  & _get('sess_london'),
    'rec_ah5+london+avwap':       _get('rec_near_ah_5b')  & _get('sess_london') & _get('above_vwap'),
    'rec_ah3+london+avwap':       _get('rec_near_ah_3b')  & _get('sess_london') & _get('above_vwap'),
    'near_pdh+trenddown':         _get('near_pdh')        & _get('reg_trenddown'),
    'near_pdh+trenddown+ny':      _get('near_pdh')        & _get('reg_trenddown') & _get('sess_ny'),
    'stk_bear+london':            _get('stacked_bear')    & _get('sess_london'),
    'stk_bear+above_poc':         _get('stacked_bear')    & _get('above_poc'),
    'equal_high+london':          _get('equal_high')      & _get('sess_london'),
    'near_weekly_high+london':    _get('near_weekly_high')& _get('sess_london'),
    # ── Nuevos OTE London ──────────────────────────────────────────────────────
    'fib_ote_london':             _get('fib_ote_london'),
    'ote_rejection':              _get('ote_rejection'),
    'ote_rej+london':             _get('ote_rejection')   & _get('sess_london'),
    'fib_ote_lon+london':         _get('fib_ote_london')  & _get('sess_london'),
    'fib_ote_lon+bvwap':          _get('fib_ote_london')  & _get('body_below_vwap'),
    'ote_rej+bvwap':              _get('ote_rejection')   & _get('body_below_vwap'),
    'fib_ote_lon+poc':            _get('fib_ote_london')  & _get('above_poc'),
    'ote_rej+poc':                _get('ote_rejection')   & _get('above_poc'),
}

# ── 2. Orderflow signals ──────────────────────────────────────────────────────
orderflow = {
    'cvd_div':       _get('cvd_div'),
    'abs_ask':       _get('abs_ask'),
    'cvd_mom_3neg':  _get('cvd_mom_3neg'),
    'cvd_mom_5neg':  _get('cvd_mom_5neg'),
    'obi_neg05':     _get('obi_neg05'),
    'obi_neg10':     _get('obi_neg10'),
    'stk_bear':      _get('stacked_bear'),
    'dz_sell':       _get('dz_sellers'),
    'vpin_toxic':    _get('vpin_toxic'),
    'rec_cvd_div':   _get('rec_cvd_div_3b'),
    'rec_abs_ask':   _get('rec_abs_ask_3b'),
    'rec_bt_bear':   _get('rec_bt_bear_3b'),
}

results = []

# Estructura + shoot  (incluyendo nuevos contextos OTE)
for sname, smask in structural.items():
    combo = smask & shoot
    matched = combo[idx_arr]
    sub = outcomes[matched]
    n = len(sub)
    if n < 10:
        continue
    wr = (sub['result_r'] > 0).sum() / n * 100
    avgr = sub['result_r'].mean()
    tpd = n / DAYS
    total_r = n * avgr
    results.append((f"{sname} + shoot", n, tpd, wr, avgr, total_r))

# Estructura + orderflow + shoot
best_structs = [
    ('poc+lon+bvwap',  _get('above_poc')       & _get('sess_london') & _get('below_vwap')),
    ('poc+london',     _get('above_poc')        & _get('sess_london')),
    ('ah+london',      _get('near_asian_high')  & _get('sess_london')),
    ('rec_ah5+lon',    _get('rec_near_ah_5b')   & _get('sess_london')),
    ('rec_ah3+lon',    _get('rec_near_ah_3b')   & _get('sess_london')),
    ('pdh+tdown',      _get('near_pdh')         & _get('reg_trenddown')),
    ('ah+lon+avwap',   _get('near_asian_high')  & _get('sess_london') & _get('above_vwap')),
    # Nuevos OTE London como estructura base
    ('ote_london',     _get('fib_ote_london')),
    ('ote_rej',        _get('ote_rejection')),
    ('ote_rej+london', _get('ote_rejection')    & _get('sess_london')),
    ('ote_lon+bvwap',  _get('fib_ote_london')   & _get('body_below_vwap')),
    ('ote_rej+bvwap',  _get('ote_rejection')    & _get('body_below_vwap')),
    ('ote_rej+poc',    _get('ote_rejection')    & _get('above_poc')),
]

for sname, smask in best_structs:
    for ofname, ofmask in orderflow.items():
        combo = smask & ofmask & shoot
        matched = combo[idx_arr]
        sub = outcomes[matched]
        n = len(sub)
        if n < 12:
            continue
        wr = (sub['result_r'] > 0).sum() / n * 100
        avgr = sub['result_r'].mean()
        tpd = n / DAYS
        total_r = n * avgr
        results.append((f"{sname} + {ofname} + shoot", n, tpd, wr, avgr, total_r))

results.sort(key=lambda x: -x[5])

print(f"{'Patron':<50} | {'n':>5} | {'/dia':>5} | {'WR%':>6} | {'AvgR':>6} | {'TotalR':>7}")
print('-' * 95)
for r in results[:50]:
    print(f"{r[0]:<50} | {r[1]:>5} | {r[2]:>5.1f} | {r[3]:>6.1f}% | {r[4]:>6.3f} | {r[5]:>7.0f}R")

# ── OTE London explícito (aparezca donde aparezca en el ranking) ───────────────
print()
print('=== OTE LONDON — resultados completos ===')
print(f"{'Patron':<55} | {'n':>5} | {'/dia':>5} | {'WR%':>6} | {'AvgR':>6} | {'TotalR':>7}")
print('-' * 100)
ote_results = [r for r in results if 'ote' in r[0].lower()]
if ote_results:
    for r in sorted(ote_results, key=lambda x: -x[5]):
        print(f"{r[0]:<55} | {r[1]:>5} | {r[2]:>5.1f} | {r[3]:>6.1f}% | {r[4]:>6.3f} | {r[5]:>7.0f}R")
else:
    print("  (sin resultados OTE — calculando directamente)")
    for tag, smask in [
        ('fib_ote_london + shoot',             _get('fib_ote_london') & shoot),
        ('ote_rejection + shoot',              _get('ote_rejection')  & shoot),
        ('ote_rejection + london + shoot',     _get('ote_rejection')  & _get('sess_london') & shoot),
        ('ote_rejection + bvwap + shoot',      _get('ote_rejection')  & _get('body_below_vwap') & shoot),
        ('fib_ote_london + bvwap + shoot',     _get('fib_ote_london') & _get('body_below_vwap') & shoot),
        ('fib_ote_london + poc + shoot',       _get('fib_ote_london') & _get('above_poc') & shoot),
        ('ote_rej + cvd_mom_3neg + shoot',     _get('ote_rejection')  & _get('cvd_mom_3neg') & shoot),
        ('ote_rej + obi_neg05 + shoot',        _get('ote_rejection')  & _get('obi_neg05')    & shoot),
        ('ote_rej + stk_bear + shoot',         _get('ote_rejection')  & _get('stacked_bear') & shoot),
    ]:
        matched = smask[idx_arr]
        sub = outcomes[matched]
        nn = len(sub)
        if nn < 5:
            print(f"  {tag:<55}: n={nn} (insuf.)")
            continue
        wr   = (sub['result_r'] > 0).sum() / nn * 100
        avgr = sub['result_r'].mean()
        tpd  = nn / DAYS
        print(f"  {tag:<55}: n={nn:>4}  {tpd:.1f}/d  WR={wr:.1f}%  AvgR={avgr:.3f}  Total={nn*avgr:.0f}R")
