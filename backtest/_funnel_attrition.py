"""
_funnel_attrition.py — ¿Dónde muere el volumen del short v2?
=============================================================
Cuenta cuántas barras sobreviven CADA gate, en orden, para identificar
el cuello de botella real. Objetivo: pasar de ~0.3 tpd a 2-4 tpd intradía.

No simula trades (no path-dependent); cuenta OPORTUNIDADES por barra.
Uso: python backtest/_funnel_attrition.py
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


def main():
    df = load()
    n0 = len(df)
    days = (df.ts_ms.max() - df.ts_ms.min()) / 86_400_000

    # contexto D1 EMA (igual que simulate)
    d1 = m.resamp(df, m.D1_MS); d1['ema20'] = m.ema(d1['close'].values, 20)
    d1_ctx = {int(r.ts_ms): float(r.ema20) for r in d1.itertuples()}
    d1_ts = (df.ts_ms // m.D1_MS) * m.D1_MS - m.D1_MS
    df['d1_ema'] = d1_ts.map(d1_ctx)

    # sesión
    hm = (df.ts_ms // 60_000) % 1440
    df['sess'] = np.select(
        [(hm>=7*60)&(hm<12*60), (hm>=12*60)&(hm<16*60), (hm>=16*60)&(hm<20*60)],
        ['london','overlap','ny'], default='')

    # niveles: ¿la barra toca VAH dentro de tolerancia?
    vah = df['vp_vah']
    near_vah = (vah > 0) & ((df['high'] - vah).abs() / vah <= m.LEVEL_TOL)

    gates = [
        ('0. total barras M1',          pd.Series(True, df.index)),
        ('1. en sesión (LON/OVL/NY)',   df.sess != ''),
        ('2. régimen D1 (close<=ema*.98)', df.close <= df.d1_ema*0.980),
        ('3. H1 BOS/ChoCH bear',        df.h1_bos_bear | df.h1_choch_bear),
        ('4. cerca de VAH (<=0.7%)',    near_vah),
        ('5. rechazo (wick sup 30-85%, close<=open)', None),  # calc abajo
        ('6. body_below_poc',           df.body_below_poc.astype(bool)),
        ('7. minus_ticks > plus_ticks', df.minus_ticks > df.plus_ticks),
        ('8. NOT fp_absorb_buy',        ~df.fp_absorb_buy.astype(bool)),
        ('9. NOT vp_lvn_below==False',  df.vp_lvn_below.astype(bool)),
    ]
    # rechazo
    rng = df.high - df.low
    wick_up = df.high - df[['close','open']].max(axis=1)
    rej = (rng > 0) & (wick_up/rng > 0.30) & (wick_up/rng < 0.85) & (df.close <= df.open)
    gates[5] = ('5. rechazo (wick sup 30-85%, close<=open)', rej)

    print(f'Dataset: {n0:,} barras  /  {days:.0f} días  ({days/30:.1f} meses)\n')
    print(f'{"gate":<46}{"sobreviven":>12}{"% del total":>12}{"% del prev":>12}{"trades/día":>12}')
    print('-'*94)
    mask = pd.Series(True, df.index)
    prev = n0
    for label, g in gates:
        if g is not None:
            mask = mask & g
    # recompute cumulative properly
    mask = pd.Series(True, df.index)
    prev = n0
    for label, g in gates:
        mask = mask & g
        cnt = int(mask.sum())
        pct_tot = cnt/n0*100
        pct_prev = cnt/prev*100 if prev else 0
        tpd = cnt/days
        print(f'{label:<46}{cnt:>12,}{pct_tot:>11.3f}%{pct_prev:>11.1f}%{tpd:>12.2f}')
        prev = cnt

    print('\n>>> El gate con menor "% del prev" es el cuello de botella principal.')
    print('>>> trades/día final = barras que pasan TODO (antes de stop válido / path).')


if __name__ == '__main__':
    main()
