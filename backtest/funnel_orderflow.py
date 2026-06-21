"""
funnel_orderflow.py — Barrido SISTEMATICO del embudo de orderflow sobre el short.
==================================================================================
Baseline ya validado (2026-06-18): regimen (D1 EMA thr=1.000) + agresion (minus>plus).
Aqui probamos, EN ORDEN, los pasos del embudo que FALTAN, con features que YA tenemos
en el dataset perp (105 cols). Cada feature se prueba como gate adicional, IS/OOS.

Embudo:
  0. Regimen        -> D1 EMA            [YA: gran salto]
  1-2. Valor/Nivel  -> vp_vah, above_poc, val_near, near_pdh, body_below_poc/vwap
  3. Agresion       -> minus>plus ticks  [YA: segundo salto]
  4. Atrapada/result-> fp_result_sell, fp_sell_dom, fp_delta_top, cvd_div
  5. Veto liquidez  -> bid_wall(veto), ask_wall(a favor), thin_below
  6. Tape acelera   -> n_trades, vpin, vr, dz
  7. Espacio limpio -> thin_below, vp_lvn_below, obi10_mean

Uso: python backtest/funnel_orderflow.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'backtest'))
import mtf_basics as mb

DATASET = ROOT / 'data/bybit-perp/processed/btcusdt_perp_m1.parquet'
mb.FEE_RT = 0.0011   # futuros perp round-trip real


def run_baseline():
    df = pd.read_parquet(DATASET).sort_values('ts_ms').reset_index(drop=True)
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].fillna('')
        elif df[c].dtype == float:
            df[c] = df[c].fillna(0.0)
    print(f'Dataset perp: {len(df):,} barras, {len(df.columns)} cols')

    # baseline = regimen D1 EMA thr=1.000
    trades, _ = mb.simulate(df, d1_threshold=1.000)
    td = pd.DataFrame(trades)
    print(f'Regimen solo: {len(td)} trades')

    # join features de entrada por ts_ms
    feat = df.set_index('ts_ms')
    td = td.join(feat, on='ts_ms', rsuffix='_bar')
    return td


def stats(sub):
    if len(sub) == 0:
        return (0, 0, 0, 0)
    n = len(sub); wr = (sub['result_r'] > 0).mean() * 100
    avg = sub['result_r'].mean(); tot = sub['result_r'].sum()
    return (n, wr, avg, tot)


def report(td, label):
    is_t = td[~td['oos']]; oos_t = td[td['oos']]
    ni, wi, ai, ti = stats(is_t)
    no, wo, ao, to = stats(oos_t)
    tot = ti + to
    print(f'  {label:<34} | IS n={ni:>4} WR={wi:>4.0f}% AvgR={ai:>+.3f} '
          f'| OOS n={no:>4} WR={wo:>4.0f}% AvgR={ao:>+.3f} | TotR={tot:>+5.0f}')
    return tot


def gate_numeric(td, col, label, mode='high'):
    """mode='high' mantiene >= mediana; 'low' mantiene <= mediana."""
    if col not in td.columns:
        print(f'  (falta {col})'); return
    med = td[col].median()
    keep = td[td[col] >= med] if mode == 'high' else td[td[col] <= med]
    report(keep, f'{label} ({mode} med={med:.3g})')


def gate_bool(td, col, label, want=True):
    if col not in td.columns:
        print(f'  (falta {col})'); return
    keep = td[td[col].astype(bool) == want]
    report(keep, f'{label} (={want})')


def main():
    td = run_baseline()

    print('\n=== BASELINE ===')
    report(td, 'regimen solo')
    # paso 3 agresion (ya validado)
    agg = td[td['minus_ticks'] > td['plus_ticks']]
    report(agg, '+ agresion (minus>plus) [BASE+]')

    # de aqui en adelante todo se mide SOBRE base+ (regimen+agresion)
    base = agg
    print('\n=== PASO 1-2: VALOR / NIVEL (sobre base+) ===')
    gate_bool(base, 'above_poc', 'above_poc (precio sobre POC)', True)
    gate_bool(base, 'val_near', 'val_near', False)            # lejos del VAL = espacio
    gate_bool(base, 'body_below_poc', 'body_below_poc')
    gate_bool(base, 'body_below_vwap', 'body_below_vwap')
    gate_bool(base, 'near_pdh', 'near_pdh')
    gate_bool(base, 'near_weekly_high', 'near_weekly_high')

    print('\n=== PASO 4: ATRAPADA / RESULTADO (sobre base+) ===')
    gate_bool(base, 'fp_result_sell', 'fp_result_sell (venta con result)')
    gate_bool(base, 'fp_absorb_buy', 'NOT fp_absorb_buy', False)
    gate_numeric(base, 'fp_sell_dom', 'fp_sell_dom alto', 'high')
    gate_numeric(base, 'fp_delta_top', 'fp_delta_top bajo (venta en techo)', 'low')
    gate_bool(base, 'cvd_div', 'cvd_div')

    print('\n=== PASO 5: VETO LIQUIDEZ PASIVA (sobre base+) ===')
    gate_bool(base, 'bid_wall', 'NOT bid_wall (sin soporte abajo)', False)
    gate_bool(base, 'ask_wall', 'ask_wall (resistencia arriba a favor)', True)
    gate_bool(base, 'thin_below', 'thin_below (poco soporte abajo)', True)

    print('\n=== PASO 6: TAPE ACELERA (sobre base+) ===')
    gate_numeric(base, 'n_trades', 'n_trades alto (intensidad)', 'high')
    gate_numeric(base, 'vpin', 'vpin alto (toxicidad)', 'high')
    gate_numeric(base, 'vr', 'vr alto', 'high')
    gate_numeric(base, 'dz', 'dz bajo (delta z negativo)', 'low')

    print('\n=== PASO 7: ESPACIO LIMPIO HASTA TARGET (sobre base+) ===')
    gate_bool(base, 'thin_below', 'thin_below', True)
    gate_bool(base, 'vp_lvn_below', 'vp_lvn_below (void abajo)')
    gate_numeric(base, 'obi10_mean', 'obi10 muy negativo', 'low')
    gate_numeric(base, 'obi20_mean', 'obi20 muy negativo', 'low')


if __name__ == '__main__':
    main()
