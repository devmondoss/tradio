#!/usr/bin/env python3
"""
MTF Shorts — Comparación de experimentos de orderflow

Corre el backtest con 4 variantes y compara WR, AvgR, n, equity vs baseline v2.

Uso:
  python mtf_experiments.py [--days N]

Experimentos:
  none       — baseline v2 (patrones mineados + filtros calibrados)
  obi_strict — gate global: obi_fast < -0.15 (flujo vendedor más fuerte)
  cvd_session — gate por sesión: London CVD < -0.20 / NY CVD < -0.15
  delta_div  — gate global: cvd_divergence == 'BearishAbsorption'
"""
import json, subprocess, sys, time
from pathlib import Path
from collections import defaultdict

SCRIPT = Path(__file__).parent / 'mtf_shorts_backtest.py'

EXPERIMENTS = ['none', 'obi_strict', 'cvd_session', 'delta_div',
               'vwap_bias', 'secondary_str', 'three_layer', 'vwap_weak',
               'regime_filter', 'regime_vwap_combined']
LABELS = {
    'none':                  'Baseline v2',
    'obi_strict':            'EXP1: OBI < -0.15',
    'cvd_session':           'EXP2: CVD/sesión',
    'delta_div':             'EXP3: Delta Div',
    'vwap_bias':             'EXP4: VWAP Macro',
    'secondary_str':         'EXP5: Secundario',
    'three_layer':           'EXP6: 3 Capas',
    'vwap_weak':             'EXP7: VWAP Selectivo',
    'regime_filter':         'EXP8: Regime Filter',
    'regime_vwap_combined':  'EXP9: Regime+VWAP',
}
DESCRIPTIONS = {
    'none':                  'Patrones mineados v2, sin filtros adicionales',
    'obi_strict':            'Requiere obi_fast < -0.15 en TODOS los patrones',
    'cvd_session':           'London: cvd_slope < -0.20 / NY: cvd_slope < -0.15',
    'delta_div':             'Requiere cvd_divergence == BearishAbsorption',
    'vwap_bias':             'Macro sesión: entrar short solo si close < VWAP (equilibrio bajista)',
    'secondary_str':         'Secundario: requiere stacked_imb == Bearish (estructura H1 débil)',
    'three_layer':           '3 capas: Macro(D1+VWAP) + Secundario(stacked/exp+delta<0) + Micro(M1)',
    'vwap_weak':             'VWAP solo en patrones WR<55%: btc:shoot+london, bnb:oi+ny, sol:eq+london+exp, xrp:oi+ny',
    'regime_filter':         'Bloquear TrendDown (WR=44.4% worst regime); mantener TrendUp+Exp+Chop',
    'regime_vwap_combined':  'Regime filter + EXP7 VWAP selectivo: dos capas combinadas',
}

def run_experiment(exp, days):
    t0 = time.time()
    r = subprocess.run(
        [sys.executable, str(SCRIPT), '--days', str(days), '--experiment', exp],
        capture_output=True, text=True
    )
    elapsed = time.time() - t0
    if r.returncode != 0:
        print(f'  ERROR [{exp}]: {r.stderr[:200]}')
        return None
    data = json.loads(r.stdout)
    data['_elapsed'] = round(elapsed, 1)
    return data

def stats_by_sig(trades):
    by = defaultdict(list)
    for t in trades:
        by[t['sig']].append(t)
    out = {}
    for sig, ts in by.items():
        wins = [t for t in ts if t['resultR'] > 0]
        out[sig] = {
            'n':    len(ts),
            'wr':   round(len(wins)/len(ts)*100, 1),
            'avgR': round(sum(t['resultR'] for t in ts)/len(ts), 3),
        }
    return out

def stats_by_reason(trades):
    by = defaultdict(list)
    for t in trades:
        by[t['reason']].append(t)
    return {r: {'n': len(ts), 'pct': round(len(ts)/len(trades)*100, 1)}
            for r, ts in by.items()} if trades else {}

def sep(title):
    print(f'\n{"="*70}')
    print(f'  {title}')
    print(f'{"="*70}')

def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    print(f'\nMTF Experiments — {days} días')
    print('Descripción de cada experimento:')
    for exp in EXPERIMENTS:
        print(f'  [{exp:<12}] {DESCRIPTIONS[exp]}')

    print('\nCorriendo backtests...')
    results = {}
    for exp in EXPERIMENTS:
        print(f'  {exp}...', end=' ', flush=True)
        results[exp] = run_experiment(exp, days)
        d = results[exp]
        if d:
            print(f'n={d["n"]} WR={d["wr_pct"]}% AvgR={d["avg_r"]}R  ({d["_elapsed"]}s)')
        else:
            print('FAILED')

    base = results['none']
    if not base:
        print('ERROR: baseline falló, no se puede comparar')
        sys.exit(1)

    # ── Tabla principal ──────────────────────────────────────────────────────
    sep('RESUMEN COMPARATIVO')
    hdr = f'  {"Experimento":<22} {"n":>4} {"WR%":>6} {"AvgR":>8} {"TotalR":>8} {"Equity":>8}  {"dn":>4} {"dWR":>6} {"dAvgR":>8}'
    print(hdr)
    print('  ' + '-'*90)

    for exp in EXPERIMENTS:
        d = results[exp]
        if not d:
            print(f'  {LABELS[exp]:<22}  FAILED')
            continue
        n      = d['n']
        wr     = d['wr_pct']
        avgr   = d['avg_r']
        totalr = d['total_r']
        equity = d['equity']

        if exp == 'none':
            delta = ''
        else:
            dn    = n - base['n']
            dwr   = wr - base['wr_pct']
            davgr = avgr - base['avg_r']
            delta = f'  {dn:>+4}  {dwr:>+5.1f}pp  {davgr:>+7.3f}R'

        print(f'  {LABELS[exp]:<22} {n:>4} {wr:>5.1f}%  {avgr:>+7.3f}R  {totalr:>+7.2f}R  ${equity:>7.0f}{delta}')

    # ── Detalle por patrón para cada experimento ─────────────────────────────
    sep('BREAKDOWN POR PATRÓN')
    all_sigs = set()
    for d in results.values():
        if d:
            for t in d['trades']:
                all_sigs.add(t['sig'])
    all_sigs = sorted(all_sigs)

    hdr2 = f'  {"Patrón":<28}' + ''.join(f'  {LABELS[e][:14]:>16}' for e in EXPERIMENTS)
    print(hdr2)
    print('  ' + '-'*80)

    for sig in all_sigs:
        row = f'  {sig:<28}'
        for exp in EXPERIMENTS:
            d = results[exp]
            if not d:
                row += f'  {"—":>16}'
                continue
            by = stats_by_sig(d['trades'])
            if sig in by:
                s = by[sig]
                row += f'  n={s["n"]} WR={s["wr"]}%{s["avgR"]:>+.2f}R'
            else:
                row += f'  {"(filtrado)":>16}'
        print(row)

    # ── Exit reasons por experimento ─────────────────────────────────────────
    sep('EXIT REASONS')
    for exp in EXPERIMENTS:
        d = results[exp]
        if not d or not d['trades']:
            continue
        reasons = stats_by_reason(d['trades'])
        parts = ', '.join(f'{r}={v["n"]}({v["pct"]}%)' for r, v in
                          sorted(reasons.items(), key=lambda x: -x[1]['n']))
        print(f'  {LABELS[exp]:<22}: {parts}')

    # ── Veredicto ────────────────────────────────────────────────────────────
    sep('VEREDICTO')
    print('  Criterio A (filtros EXP1-3): WR >=+3pp Y n>=60% del baseline')
    print('  Criterio B (3 capas EXP4-6): WR >=+5pp Y AvgR >=+0.10R (menor n aceptable)')
    print()
    for exp in EXPERIMENTS[1:]:
        d = results[exp]
        if not d: continue
        dwr   = d['wr_pct'] - base['wr_pct']
        davgr = d['avg_r']  - base['avg_r']
        n_ret = d['n'] / base['n']
        is_layer = exp in ('vwap_bias', 'secondary_str', 'three_layer', 'vwap_weak',
                           'regime_filter', 'regime_vwap_combined')
        if is_layer:
            if dwr >= 5 and davgr >= 0.10 and n_ret >= 0.30:
                verdict = '[OK] CANDIDATO 3-capas'
            elif dwr >= 3 and davgr >= 0.05:
                verdict = '[~] MEJORA LEVE — explorar combinaciones'
            elif dwr >= 0 and n_ret >= 0.50:
                verdict = '[~] NEUTRAL — no suma ni resta'
            else:
                verdict = '[X] No mejora o n demasiado bajo'
        else:
            if dwr >= 3 and n_ret >= 0.60:
                verdict = '[OK] CANDIDATO para walk-forward'
            elif dwr >= 0 and davgr >= 0.05:
                verdict = '[~] NEUTRAL/LEVE mejora'
            else:
                verdict = '[X] No mejora o reduce n excesivamente'
        print(f'  {LABELS[exp]:<24}: WR {dwr:>+.1f}pp  AvgR {davgr:>+.3f}R  n={d["n"]}({n_ret:.0%})  -> {verdict}')

    print()

if __name__ == '__main__':
    main()
