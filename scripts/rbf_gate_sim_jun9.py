#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simula los gates actuales de RBF sobre los 19 trades live del 9-jun-2026.
Datos: rbf_microstructure.csv (ya generado, no requiere Supabase).

Gates aplicados (estado actual 2026-06-11):
  G1  allow_long = false
  G2  pre-CVD gate: last5_delta > 0 para Short → filtrado
  G3  London CVD gate: session==London && cum_delta > 200 para Short → filtrado
  G4  expansion gate: expansion_n > 1 (si está activo)

Nota: vswap_gate y breakout_ext_gate requieren datos de VWAP y breakout_extension
que no están en el CSV de microestructura — no se aplican aquí.
"""
import csv, os

CSV = os.path.join(os.path.dirname(__file__), 'rbf_microstructure.csv')

DATE_FILTER = '2026-06-09'

def safe_float(v, default=0.0):
    try:
        return float(v) if v not in (None, '', 'None') else default
    except ValueError:
        return default

def safe_int(v, default=0):
    try:
        return int(float(v)) if v not in (None, '', 'None') else default
    except ValueError:
        return default

def load_jun9_trades():
    trades = []
    with open(CSV, encoding='utf-8') as f:
        for row in csv.DictReader(f):
            if not row['fecha'].startswith(DATE_FILTER):
                continue
            trades.append({
                'num':          int(row['num']),
                'fecha':        row['fecha'],
                'symbol':       row['symbol'],
                'session':      row['session'],
                'direction':    row['direction'],
                'score':        row['score'],
                'result_r':     safe_float(row['result_r']),
                'status':       row['status'],
                'cum_delta':    safe_float(row['cum_delta']),
                'last5_delta':  safe_float(row['last5_delta']),
                'expansion_n':  safe_int(row['expansion_n']),
                'oi_mom_n':     safe_int(row['oi_mom_n']),
                'has_data':     row['has_data'] == 'True',
            })
    return trades

def apply_gates(trade, expansion_gate=False, expansion_max=1):
    direction  = trade['direction']
    session    = trade['session']
    cum_delta  = trade['cum_delta']
    last5      = trade['last5_delta']
    exp_n      = trade['expansion_n']

    # G1: Longs desactivados
    if direction == 'Long':
        return 'G1_long_disabled'

    # G2: pre-CVD gate (últimas 5 barras del rango)
    # Compradores activos justo antes del breakout → breakdown falso
    if direction == 'Short' and last5 > 0.0:
        return 'G2_pre_cvd_bullish'

    # G3: London CVD gate
    # cum_delta > 200 en rango durante London = fakeout probable
    if session == 'London' and cum_delta > 200.0:
        return 'G3_london_cvd_bullish'

    # G4: expansion gate (opcional — expansion_max_bars no confirmado en TOML)
    if expansion_gate and exp_n > expansion_max:
        return f'G4_expansion_{exp_n}'

    return None  # PASA todos los gates

def fmt_r(r):
    return f'{r:+.2f}R'

def main():
    trades = load_jun9_trades()
    if not trades:
        print(f"No hay trades del {DATE_FILTER} en {CSV}")
        return

    print(f"{'='*80}")
    print(f"SIMULACIÓN DE GATES ACTUALES — {DATE_FILTER}  ({len(trades)} trades live)")
    print(f"{'='*80}")
    print(f"\n{'#':>3}  {'Fecha':>14}  {'Sym':<8}  {'Dir':>5}  {'Ses':>18}  {'Sc':>3}  "
          f"{'Real':>7}  {'Razón Filtro'}")
    print(f"{'-'*90}")

    passed_base = []   # sin expansion gate
    filtered_base = []

    for t in trades:
        blocked = apply_gates(t, expansion_gate=False)
        mark = '  ' if blocked is None else 'XX'
        print(f"{mark}{t['num']:>2}  {t['fecha']:>14}  {t['symbol']:<8}  "
              f"{t['direction']:>5}  {t['session']:>18}  {t['score']:>3}  "
              f"{fmt_r(t['result_r']):>7}  {blocked or 'PASA'}")
        if blocked:
            filtered_base.append((t, blocked))
        else:
            passed_base.append(t)

    print(f"\n{'='*80}")
    print("RESUMEN (sin expansion gate — estado más conservador):")
    print(f"{'='*80}")
    _print_stats("Trades originales (19 live)", trades)
    _print_stats("PASAN gates actuales", passed_base)
    _print_stats("FILTRADOS", [t for t, _ in filtered_base])

    print(f"\n{'-'*50}")
    print("Desglose de filtros:")
    from collections import Counter
    for reason, cnt in Counter(r for _, r in filtered_base).most_common():
        affected = [t for t, bl in filtered_base if bl == reason]
        pnl = sum(t['result_r'] for t in affected)
        print(f"  {reason:<28}: n={cnt:>2}  PnL_evitado={fmt_r(pnl)}")

    print(f"\n{'='*80}")
    print("RESUMEN (con expansion gate <=1 - si esta activo en config):")
    print(f"{'='*80}")
    passed_exp = [t for t in trades if apply_gates(t, expansion_gate=True) is None]
    filtered_exp = [(t, apply_gates(t, expansion_gate=True)) for t in trades
                    if apply_gates(t, expansion_gate=True) is not None]
    _print_stats("PASAN con expansion gate", passed_exp)

def _print_stats(label, trades):
    if not trades:
        print(f"  {label}: n=0")
        return
    wins = [t for t in trades if t['result_r'] > 0]
    losses = [t for t in trades if t['result_r'] <= 0]
    total_r = sum(t['result_r'] for t in trades)
    wr = len(wins) / len(trades) * 100 if trades else 0
    print(f"  {label:<40}: n={len(trades):>2}  WR={wr:4.0f}%  "
          f"TotalR={fmt_r(total_r)}  AvgR={fmt_r(total_r/len(trades))}")

if __name__ == '__main__':
    main()
