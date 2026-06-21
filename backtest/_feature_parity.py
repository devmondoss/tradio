"""
_feature_parity.py — Verifica que el sidecar (enrich sobre ventana rolling) reproduce
las columnas del parquet (enrich sobre dataset completo).
=====================================================================================
Testea feature_server.context_from_last_row() con warmup controlable: para cada barra
de test, arma la ventana [i-W : i] y compara el contexto emitido contra los valores
reales del parquet en la barra i. Reporta mismatches por campo.

Objetivo: encontrar el warmup mínimo que hace live == backtest (sobre todo la D1 EMA
del régimen, que se siembra al inicio de la ventana).

Uso: python backtest/_feature_parity.py --warmup 15000 --test 300
"""
from __future__ import annotations
import argparse, math, sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'backtest'))
import mtf_v2 as m
import feature_server as fs

# Tolerancias por campo (precios: absoluta; ratios: relativa pequeña)
PRICE_FIELDS = ['vp_vah', 'vp_val', 'vp_poc', 'prev_day_high', 'prev_day_low',
                'asian_high', 'asian_low', 'weekly_high', 'weekly_low', 'h1_high', 'h1_low']
NUM_FIELDS = ['obi10_mean', 'delta', 'minus_ticks', 'plus_ticks', 'n_trades', 'vpin', 'cvd_slope', 'h1_atr']
BOOL_FIELDS = ['body_below_poc', 'fp_absorb_buy', 'fp_absorb_sell', 'near_bearish_fvg',
               'near_bearish_ob', 'displacement_bear', 'sweep_confirmed', 'vp_lvn_below',
               'h1_bos_bear', 'h1_choch_bear', 'h1_bos_bull', 'h4_bos_bear']


def approx(a, b, price=False):
    if a is None and b is None:
        return True
    if a is None or b is None:
        # parquet 0/NaN ~ None del contexto
        pv = b if a is None else a
        try:
            return (not math.isfinite(float(pv))) or float(pv) == 0.0
        except (TypeError, ValueError):
            return False
    a, b = float(a), float(b)
    tol = 0.05 if price else max(1e-6, abs(b) * 0.01 + 0.005)
    return abs(a - b) <= tol


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--warmup', type=int, default=15000)
    ap.add_argument('--test', type=int, default=300)
    args = ap.parse_args()

    df = pd.read_parquet(m.DATA_M1).sort_values('ts_ms').reset_index(drop=True)
    for c in df.select_dtypes('object').columns: df[c] = df[c].fillna('')
    n = len(df)
    start = n - args.test
    print(f'Parquet {n:,} barras | test bars [{start}:{n}] | warmup={args.warmup}')

    mismatches = {}   # field -> count
    checked = 0
    for i in range(start, n):
        lo = max(0, i - args.warmup)
        buf = df.iloc[lo:i + 1].reset_index(drop=True)
        ctx = fs.context_from_last_row(buf)
        truth = df.iloc[i]
        checked += 1
        for f in NUM_FIELDS + PRICE_FIELDS:
            if f in ('h1_high', 'h1_low', 'h1_atr'):
                continue  # no están en el parquet (se validan aparte; deterministas de OHLC)
            if f not in df.columns:
                continue
            if not approx(ctx.get(f), truth[f], price=(f in PRICE_FIELDS)):
                mismatches[f] = mismatches.get(f, 0) + 1
        for f in BOOL_FIELDS:
            if f not in df.columns:
                continue
            cv = bool(ctx.get(f))
            tv = bool(truth[f]) if pd.notna(truth[f]) else False
            if cv != tv:
                mismatches[f] = mismatches.get(f, 0) + 1

    print(f'\nBarras comparadas: {checked}')
    if not mismatches:
        print('✓ PARIDAD DE FEATURES PERFECTA — live (rolling enrich) == backtest (full enrich)')
        return
    print('Mismatches por campo (de', checked, 'barras):')
    for f, c in sorted(mismatches.items(), key=lambda x: -x[1]):
        print(f'  {f:<20} {c:>4}  ({c/checked*100:.1f}%)')


if __name__ == '__main__':
    main()
