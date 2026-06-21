"""
_edge_research2.py — Validación de los 2 hallazgos del paso 1:
  (A) veto vp_lvn_below==False
  (B) vpin: ¿ortogonal a tape (n_trades) o redundante?
Y aplicación al sistema real (re-corre simulate con el veto para medir impacto).
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'backtest'))
import mtf_v2 as m


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
    return td.join(feat, on='ts_ms', rsuffix='_bar'), nt_q50


def cell(sub):
    if len(sub) == 0: return (0, float('nan'), float('nan'), 0.0)
    return (len(sub), (sub.result_r>0).mean()*100, sub.result_r.mean(), sub.result_r.sum())

def line(label, td):
    i, o = td[~td.oos], td[td.oos]
    ni,wi,ai,ti = cell(i); no,wo,ao,to = cell(o)
    print(f'  {label:<34} | IS n={ni:>3} WR={wi:>5.1f}% AvgR={ai:>+.3f} TotR={ti:>+6.1f} '
          f'| OOS n={no:>3} WR={wo:>5.1f}% AvgR={ao:>+.3f} TotR={to:>+6.1f}')


def main():
    df = load()
    td, nt_q50 = entries(df)
    vpin_med = td[~td.oos].vpin.median()

    print('='*120); print('(A) VETO vp_lvn_below==False — impacto en el sistema'); print('='*120)
    line('v2 actual (todo)', td)
    line('v2 + veto lvn_below==False', td[td.vp_lvn_below.astype(bool)])

    print('\n'+'='*120); print('(B) ¿vpin ortogonal a tape (n_trades)?'); print('='*120)
    td = td.copy()
    td['tape']     = td.n_trades >= nt_q50
    td['vpin_hi']  = td.vpin >= vpin_med
    print(f'  corr(vpin, n_trades) = {td.vpin.corr(td.n_trades):.3f}')
    print(f'  P(vpin_hi | tape)    = {td[td.tape].vpin_hi.mean():.2f}   '
          f'P(vpin_hi | ~tape) = {td[~td.tape].vpin_hi.mean():.2f}')
    print('\n  vpin dentro de cada bucket de tape (¿separa adentro?):')
    for tp in (True, False):
        sub = td[td.tape == tp]
        print(f'  --- tape=={tp} (n={len(sub)}) ---')
        line(f'    vpin_hi', sub[sub.vpin_hi])
        line(f'    vpin_lo', sub[~sub.vpin_hi])

    print('\n  cvd_slope>0 dentro de vpin_hi (¿vpin separa donde cvd no?):')
    for vh in (True, False):
        sub = td[td.vpin_hi == vh]
        print(f'  --- vpin_hi=={vh} (n={len(sub)}) ---')
        line(f'    cvd>0', sub[sub.cvd_slope>0])
        line(f'    cvd<=0', sub[sub.cvd_slope<=0])

    print('\n'+'='*120); print('(C) combo veto lvn + vpin como tier extra'); print('='*120)
    base = td[td.vp_lvn_below.astype(bool)]
    line('veto lvn (base)', base)
    line('veto lvn + vpin_hi', base[base.vpin_hi])
    line('veto lvn + vpin_lo', base[~base.vpin_hi])


if __name__ == '__main__':
    main()
