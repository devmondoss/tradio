"""
_edge_research.py — Análisis de features no usadas sobre las entradas v2 (shorts).
====================================================================================
NO modifica el sistema. Toma las entradas reales de mtf_v2.simulate(mode='v2'),
les hace join del contexto completo de la barra de entry, y para cada feature
candidata mide el split ganadores/perdedores IS vs OOS.

Criterio de "sirve": mejora AvgR en IS *y* OOS (misma dirección) reteniendo volumen,
o aísla limpiamente un subconjunto perdedor para vetar.

Uso: python backtest/_edge_research.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'backtest'))
import mtf_v2 as m

pd.set_option('display.width', 200)


def load():
    df = pd.read_parquet(m.DATA_M1).sort_values('ts_ms').reset_index(drop=True)
    for c in df.select_dtypes('object').columns:  df[c] = df[c].fillna('')
    for c in df.select_dtypes('float').columns:   df[c] = df[c].fillna(0.0)
    for c in df.select_dtypes('bool').columns:     df[c] = df[c].fillna(False)
    return df


def entries(df):
    nt_q50 = float(df[df['ts_ms'] < m.OOS_MS]['n_trades'].quantile(0.50))
    trades, _ = m.simulate(df, mode='v2', n_trades_q50=nt_q50)
    td = pd.DataFrame(trades)
    feat = df.set_index('ts_ms')
    td = td.join(feat, on='ts_ms', rsuffix='_bar')
    return td


def cell(sub):
    if len(sub) == 0: return (0, float('nan'), float('nan'), 0.0)
    n = len(sub); wr = (sub['result_r'] > 0).mean() * 100
    return (n, wr, sub['result_r'].mean(), sub['result_r'].sum())


def line(label, sub_is, sub_oos):
    ni, wi, ai, ti = cell(sub_is)
    no, wo, ao, to = cell(sub_oos)
    print(f'  {label:<38} | IS n={ni:>3} WR={wi:>5.1f}% AvgR={ai:>+.3f} TotR={ti:>+6.1f} '
          f'| OOS n={no:>3} WR={wo:>5.1f}% AvgR={ao:>+.3f} TotR={to:>+6.1f}')


def split_bool(td, col):
    is_t, oos_t = td[~td['oos']], td[td['oos']]
    if col not in td.columns:
        print(f'  (falta {col})'); return
    print(f'\n[{col}]')
    line('all', is_t, oos_t)
    for val in (True, False):
        mi = is_t[is_t[col].astype(bool) == val]
        mo = oos_t[oos_t[col].astype(bool) == val]
        line(f'  =={val}', mi, mo)


def split_num(td, col, qs=(0.50,)):
    is_t, oos_t = td[~td['oos']], td[td['oos']]
    if col not in td.columns:
        print(f'  (falta {col})'); return
    print(f'\n[{col}]')
    line('all', is_t, oos_t)
    for q in qs:
        # threshold se fija EN IS (sin usar OOS) para no hacer leak
        thr = is_t[col].quantile(q)
        line(f'  >= Q{int(q*100)}(IS)={thr:.3g}', is_t[is_t[col] >= thr], oos_t[oos_t[col] >= thr])
        line(f'  <  Q{int(q*100)}(IS)={thr:.3g}', is_t[is_t[col] <  thr], oos_t[oos_t[col] <  thr])


def main():
    df = load()
    td = entries(df)
    print(f'Entradas v2 shorts: IS={(~td.oos).sum()}  OOS={td.oos.sum()}')
    print(f'AvgR base: IS={td[~td.oos].result_r.mean():+.3f}  OOS={td[td.oos].result_r.mean():+.3f}')

    print('\n' + '='*120)
    print('PASO 4 — ATRAPADA / RESULTADO (footprint)')
    print('='*120)
    for c in ('fp_result_sell', 'fp_sell_dom', 'fp_unfinished_hi', 'cvd_div', 'big_trade_bearish'):
        (split_bool if td[c].dtype == bool else split_num)(td, c)

    print('\n' + '='*120)
    print('PASO 5 — LIQUIDEZ PASIVA (order book)')
    print('='*120)
    for c in ('bid_wall', 'ask_wall', 'thin_below'):
        split_bool(td, c)

    print('\n' + '='*120)
    print('PASO 6 — TAPE / TOXICIDAD')
    print('='*120)
    for c in ('vr', 'vpin', 'dz'):
        split_num(td, c)

    print('\n' + '='*120)
    print('PASO 7 — ESPACIO LIMPIO AL TARGET')
    print('='*120)
    for c in ('vp_lvn_below',):
        split_bool(td, c)
    for c in ('obi20_mean',):
        split_num(td, c, qs=(0.50,))

    print('\n' + '='*120)
    print('COMBO worklog: fp_result_sell==True & cvd_slope>0')
    print('='*120)
    combo = td[(td['fp_result_sell'].astype(bool)) & (td['cvd_slope'] > 0)]
    line('combo', combo[~combo.oos], combo[combo.oos])


if __name__ == '__main__':
    main()
