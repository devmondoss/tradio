"""
_lookahead_impact.py — Autopsia: por qué el edge colapsó al quitar el lookahead H1/H4.
=======================================================================================
Corre mtf_system sobre los DOS parquets (lookahead.bak vs causal) y disecciona:
  1. Métricas shorts/longs en cada uno.
  2. Diff de conjuntos de trades por ts: comunes / solo-lookahead / solo-causal (nuevos).
  3. Performance de los trades NUEVOS que aparecen en causal (los que el gate con
     lookahead filtraba) — la hipótesis: son los malos, y por eso bajan las métricas.
  4. Cómo cambió el gate de estructura: % de barras con h1_bos_bear/bull True.

Uso: python backtest/_lookahead_impact.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'backtest'))
import mtf_v2 as m
import mtf_system as s

CAUSAL = m.DATA_M1
LOOKAHEAD = Path(str(m.DATA_M1) + '.lookahead.bak')


def load(path):
    df = pd.read_parquet(path).sort_values('ts_ms').reset_index(drop=True)
    for c in df.select_dtypes('object').columns: df[c] = df[c].fillna('')
    for c in df.select_dtypes('float').columns:  df[c] = df[c].fillna(0.0)
    for c in df.select_dtypes('bool').columns:    df[c] = df[c].fillna(False)
    return df


def metrics(trades, side=None):
    t = [x for x in trades if side is None or x['side'] == side]
    if not t: return '(0)'
    n = len(t); wr = sum(1 for x in t if x['result_r'] > 0) / n * 100
    avg = sum(x['result_r'] for x in t) / n
    return f'n={n:>4} WR={wr:4.1f}% AvgR={avg:+.3f} TotR={sum(x["result_r"] for x in t):+6.1f}'


def main():
    print('Cargando ambos parquets...')
    dfc = load(CAUSAL)
    dfl = load(LOOKAHEAD)

    print('Corriendo mtf_system en cada uno...')
    tc, _, _ = s.run(dfc)
    tl, _, _ = s.run(dfl)

    print('\n' + '='*90)
    print('1. MÉTRICAS POR PARQUET (17 meses)')
    print('='*90)
    for lbl, t in [('LOOKAHEAD', tl), ('CAUSAL', tc)]:
        print(f'\n  [{lbl}]')
        print(f'    TOTAL  {metrics(t)}')
        print(f'    shorts {metrics(t, "short")}')
        print(f'    longs  {metrics(t, "long")}')

    # 2. Diff de conjuntos por (ts, side)
    def keyset(t): return {(x['ts_ms'], x['side']): x for x in t}
    kc, kl = keyset(tc), keyset(tl)
    common = set(kc) & set(kl)
    only_c = set(kc) - set(kl)   # NUEVOS en causal (el gate lookahead los filtraba)
    only_l = set(kl) - set(kc)   # solo en lookahead

    print('\n' + '='*90)
    print('2. DIFF DE CONJUNTOS DE TRADES (por ts+side)')
    print('='*90)
    print(f'  comunes:        {len(common)}')
    print(f'  solo-causal:    {len(only_c)}  (NUEVOS — el gate con lookahead los excluía)')
    print(f'  solo-lookahead: {len(only_l)}  (el lookahead los tomaba, causal no)')

    print('\n' + '='*90)
    print('3. PERFORMANCE POR CLASE (¿los nuevos de causal son los malos?)')
    print('='*90)
    common_c = [kc[k] for k in common]
    new_c    = [kc[k] for k in only_c]
    lost_l   = [kl[k] for k in only_l]
    print(f'  comunes (en causal):   {metrics(common_c)}')
    print(f'  NUEVOS solo-causal:    {metrics(new_c)}   <-- los que el lookahead vetaba')
    print(f'  perdidos solo-lookah:  {metrics(lost_l)}')
    for side in ('short', 'long'):
        print(f'    [{side}] nuevos: {metrics(new_c, side)} | comunes: {metrics(common_c, side)}')

    # 4. Gate de estructura: % barras True
    print('\n' + '='*90)
    print('4. CAMBIO DEL GATE DE ESTRUCTURA (% barras True)')
    print('='*90)
    for col in ('h1_bos_bear', 'h1_choch_bear', 'h1_bos_bull', 'h4_bos_bear'):
        pl = dfl[col].mean()*100 if col in dfl else float('nan')
        pc = dfc[col].mean()*100 if col in dfc else float('nan')
        print(f'  {col:<16} lookahead={pl:5.1f}%  causal={pc:5.1f}%  (delta {pc-pl:+.1f}pp)')


if __name__ == '__main__':
    main()
