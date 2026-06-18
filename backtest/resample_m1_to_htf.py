"""
resample_m1_to_htf.py
---------------------
Resamplea btcusdt_m1.parquet a M5 y M15.

Columnas raw que se agregan correctamente:
  OHLCV:         open=first, high=max, low=min, close=last, volume=sum
  Trades:        buy_vol/sell_vol/delta = sum | cvd = last (acumulado)
  OBI:           obi*_mean = mean | obi10_min = min | obi10_max = max
  Spread/mid:    spread_mean = mean | mid_open = first | mid_close = last
  Snapshots:     n_snapshots = sum
  Orderbook liq: near5_ask/bid = mean | max_ask5/bid5 = max

Las columnas de features enriquecidos (session, ema20, regime, etc.) se descartan
y se recomputan con compute_spot_features.py sobre el nuevo timeframe.
"""
import sys
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ROOT     = Path(__file__).parent.parent
SRC      = ROOT / 'data/bybit-spot/processed/btcusdt_m1.parquet'
OUT_M5   = ROOT / 'data/bybit-spot/processed/btcusdt_m5.parquet'
OUT_M15  = ROOT / 'data/bybit-spot/processed/btcusdt_m15.parquet'

# Columnas raw a mantener (el resto son features derivados — se recalculan)
RAW_COLS = [
    'ts_ms',
    'open', 'high', 'low', 'close', 'volume',
    'buy_vol', 'sell_vol', 'delta', 'cvd',
    'obi5_mean', 'obi10_mean', 'obi20_mean', 'obi10_min', 'obi10_max',
    'spread_mean', 'mid_open', 'mid_close', 'n_snapshots',
    'near5_ask', 'near5_bid', 'max_ask5', 'max_bid5',
]

# Reglas de agregación por columna
AGG = {
    'open':        'first',
    'high':        'max',
    'low':         'min',
    'close':       'last',
    'volume':      'sum',
    'buy_vol':     'sum',
    'sell_vol':    'sum',
    'delta':       'sum',
    'cvd':         'last',    # CVD es acumulado — last del periodo
    'obi5_mean':   'mean',
    'obi10_mean':  'mean',
    'obi20_mean':  'mean',
    'obi10_min':   'min',
    'obi10_max':   'max',
    'spread_mean': 'mean',
    'mid_open':    'first',
    'mid_close':   'last',
    'n_snapshots': 'sum',
    'near5_ask':   'mean',
    'near5_bid':   'mean',
    'max_ask5':    'max',
    'max_bid5':    'max',
}


def resample(df_raw: pd.DataFrame, tf_minutes: int) -> pd.DataFrame:
    """Resamplea df_raw al timeframe indicado (en minutos)."""
    # Usar ts_ms como índice datetime para resample
    df = df_raw.copy()
    df.index = pd.to_datetime(df['ts_ms'], unit='ms', utc=True)

    rule = f'{tf_minutes}min'
    agg_cols = {c: AGG[c] for c in df.columns if c in AGG and c != 'ts_ms'}

    resampled = df.resample(rule, closed='left', label='left').agg(agg_cols)

    # Eliminar barras sin volumen (huecos en datos)
    resampled = resampled[resampled['volume'] > 0].copy()

    # Reconstruir ts_ms desde el índice
    resampled['ts_ms'] = resampled.index.astype(np.int64) // 1_000_000

    # Ordenar columnas con ts_ms primero
    cols = ['ts_ms'] + [c for c in resampled.columns if c != 'ts_ms']
    return resampled[cols].reset_index(drop=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tf', choices=['m5', 'm15', 'both'], default='both',
                        help='Timeframe a generar')
    args = parser.parse_args()

    print(f"Leyendo {SRC.name}...")
    df_full = pd.read_parquet(SRC).sort_values('ts_ms').reset_index(drop=True)
    print(f"  {len(df_full):,} barras M1  |  {df_full.shape[1]} columnas totales")

    # Filtrar solo columnas raw existentes
    raw_available = [c for c in RAW_COLS if c in df_full.columns]
    missing = [c for c in RAW_COLS if c not in df_full.columns]
    if missing:
        print(f"  WARN: columnas no disponibles (se omiten): {missing}")

    df_raw = df_full[raw_available].copy()
    print(f"  Columnas raw seleccionadas: {len(raw_available)}")

    targets = []
    if args.tf in ('m5',  'both'): targets.append((5,  OUT_M5))
    if args.tf in ('m15', 'both'): targets.append((15, OUT_M15))

    for tf_min, out_path in targets:
        print(f"\nResampling M1 -> M{tf_min}...")
        df_htf = resample(df_raw, tf_min)

        # Estadísticas
        first = pd.Timestamp(df_htf['ts_ms'].iloc[0],  unit='ms', tz='UTC')
        last  = pd.Timestamp(df_htf['ts_ms'].iloc[-1], unit='ms', tz='UTC')
        days  = (last - first).days
        expected = days * 24 * 60 // tf_min
        fill_pct = len(df_htf) / expected * 100

        print(f"  Barras M{tf_min}: {len(df_htf):,}  "
              f"(esperadas ~{expected:,}, fill={fill_pct:.1f}%)")
        print(f"  Rango: {first.date()} -> {last.date()}  ({days} dias)")

        df_htf.to_parquet(out_path, index=False)
        size_mb = out_path.stat().st_size / 1e6
        print(f"  Guardado: {out_path.name}  ({size_mb:.1f} MB)")

    print("\nListo. Siguiente paso:")
    print("  python backtest/compute_spot_features.py --symbol BTCUSDT_M5")
    print("  python backtest/compute_spot_features.py --symbol BTCUSDT_M15")


if __name__ == '__main__':
    main()
