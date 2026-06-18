"""
_feature_audit.py
Ver todas las columnas disponibles en M1, sus tipos y distribuciones basicas.
Objetivo: identificar features candidatas para reemplazar bloqueos por hora.
"""
import pandas as pd, numpy as np

df = pd.read_parquet('data/bybit-spot/processed/btcusdt_m1.parquet')
print(f'Shape: {df.shape}')
print(f'\n{"Col":<35} {"dtype":<10} {"non-null%":>9} {"nunique":>8} {"sample_vals"}')
print('-'*100)
for col in sorted(df.columns):
    nn = df[col].notna().mean()*100
    nu = df[col].nunique()
    if df[col].dtype in [float, 'float64']:
        nz = (df[col].fillna(0)!=0).mean()*100
        mn = df[col].dropna().mean()
        mx = df[col].dropna().max()
        mi = df[col].dropna().min()
        print(f'{col:<35} {str(df[col].dtype):<10} {nn:>8.1f}% {nu:>8}  non-zero={nz:.0f}%  mean={mn:.3g}  [{mi:.3g},{mx:.3g}]')
    elif df[col].dtype == object or df[col].dtype.name == 'category':
        samples = str(df[col].dropna().unique()[:5].tolist())
        print(f'{col:<35} {str(df[col].dtype):<10} {nn:>8.1f}% {nu:>8}  {samples}')
    else:
        print(f'{col:<35} {str(df[col].dtype):<10} {nn:>8.1f}% {nu:>8}')
