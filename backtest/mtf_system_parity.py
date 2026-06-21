"""
mtf_system_parity.py — Paridad Python (mtf_system.py directions) ↔ Rust (mtf_spot_detector).
=============================================================================================
Genera NDJSON de contexto desde el dataset perp (todos los campos que el detector lee,
incluidas las features ICT/orderflow nuevas + H1 high/low/atr resampleados), corre el
detector Rust (bin mtf_spot_parity, modo 'both') y compara entrada/salida/stop/target/R.

NOTA: sizing_mult NO se compara (no es campo de paridad). El q50 vpin/n_trades difiere
(Python: quantile IS fijo; Rust: rolling) pero no afecta entry/exit/R.

Uso: python backtest/mtf_system_parity.py            # dataset completo
     python backtest/mtf_system_parity.py --days 120 # últimos N días
"""
from __future__ import annotations
import argparse, json, math, subprocess, sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'backtest'))
import mtf_v2 as m
import mtf_system as sysmod

EXPORTS = ROOT / 'exports' / 'parity'

BOOL_COLS = ['body_below_poc', 'fp_absorb_buy', 'fp_absorb_sell',
             'near_bearish_fvg', 'near_bearish_ob', 'displacement_bear',
             'sweep_confirmed', 'vp_lvn_below',
             # estructura H1/H4 precomputada (parquet) → Option<bool> en Rust
             'h1_bos_bear', 'h1_choch_bear', 'h1_bos_bull', 'h4_bos_bear']
FLOAT_COLS = ['obi10_mean', 'delta', 'minus_ticks', 'plus_ticks', 'n_trades', 'vpin']
OPT_COLS = ['cvd_slope', 'vp_vah', 'vp_val', 'vp_poc', 'prev_day_high', 'prev_day_low',
            'asian_high', 'asian_low', 'weekly_high', 'weekly_low']


def clean(v: Any) -> float | None:
    if v is None: return None
    try: v = float(v)
    except (TypeError, ValueError): return None
    if not math.isfinite(v) or v == 0.0: return None
    return v


def load(days: int) -> pd.DataFrame:
    df = pd.read_parquet(m.DATA_M1).sort_values('ts_ms').reset_index(drop=True)
    for c in df.select_dtypes('object').columns: df[c] = df[c].fillna('')
    for c in df.select_dtypes('float').columns:  df[c] = df[c].fillna(0.0)
    for c in df.select_dtypes('bool').columns:    df[c] = df[c].fillna(False)
    if days > 0:
        cutoff = df.ts_ms.max() - days * 86_400_000
        df = df[df.ts_ms >= cutoff].reset_index(drop=True)
    return df


def context_rows(df: pd.DataFrame) -> list[dict]:
    h1 = m.resamp(df, m.H1_MS); h1['atr'] = m.atr14(h1['high'].values, h1['low'].values, h1['close'].values)
    h1c = {int(r.ts_ms): (float(r.high), float(r.low), float(r.atr)) for r in h1.itertuples()}
    rows = []
    for r in df.to_dict('records'):
        ts = int(r['ts_ms'])
        hh, hl, ha = h1c.get((ts // m.H1_MS) * m.H1_MS, (None, None, None))
        row = {'ts_ms': ts, 'open': float(r['open']), 'high': float(r['high']),
               'low': float(r['low']), 'close': float(r['close']),
               'h1_high': hh, 'h1_low': hl, 'h1_atr': ha}
        for c in FLOAT_COLS: row[c] = float(r.get(c) or 0.0)
        for c in BOOL_COLS:  row[c] = bool(r.get(c))
        for c in OPT_COLS:   row[c] = clean(r.get(c))
        rows.append(row)
    return rows


def expected_trades(df: pd.DataFrame) -> list[dict]:
    closed, _cap, _dd = sysmod.run(df)
    out = []
    for t in closed:
        out.append({
            'ts_ms': int(t['ts_ms']),
            'direction': 'Short' if t['side'] == 'short' else 'Long',
            'strategy': f"mtf_directions_{t['side']}",
            'session': t['session'],
            'level': t['level'],
            'entry': round(float(t['entry']), 3),
            'stop': round(float(t['stop']), 3),
            'target': round(float(t['target']), 3),
            'exit_price': round(float(t['exit_price']), 3),
            'reason': t['reason'],
            'gross_r': round(float(t['gross_r']), 3),
            'duration_bars': int(t['bars']),
        })
    return out


def write_ctx(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, separators=(',', ':')) + '\n')


def run_rust(ctx_path: Path) -> list[dict]:
    cmd = ['cargo', '+1.95.0-x86_64-pc-windows-gnu', 'run', '--release', '--quiet',
           '-p', 'monitor', '--bin', 'mtf_spot_parity', '--', 'both', str(ctx_path)]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Rust runner failed\n{proc.stdout}\n{proc.stderr}")
    return json.loads(proc.stdout)


def close_enough(a, b, tol):
    try: return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError): return a == b


def compare(expected: list[dict], actual: list[dict]) -> dict:
    fields = [('ts_ms', 0), ('direction', 0), ('strategy', 0), ('session', 0), ('level', 0),
              ('entry', 0.02), ('stop', 0.05), ('target', 0.05), ('exit_price', 0.05),
              ('reason', 0), ('gross_r', 0.002), ('duration_bars', 0)]
    mm = []
    if len(expected) != len(actual):
        mm.append({'kind': 'count', 'expected': len(expected), 'actual': len(actual)})
    for i, (e, a) in enumerate(zip(expected, actual), 1):
        for field, tol in fields:
            ok = e.get(field) == a.get(field) if tol == 0 else close_enough(e.get(field), a.get(field), tol)
            if not ok:
                mm.append({'trade': i, 'field': field, 'expected': e.get(field), 'actual': a.get(field)})
                break
        if len(mm) >= 25: break
    return {'expected_n': len(expected), 'actual_n': len(actual), 'ok': not mm, 'mismatches': mm}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=0)
    ap.add_argument('--keep', action='store_true')
    ap.add_argument('--reuse-ctx', action='store_true', help='reusar ndjson existente (no regenerar)')
    args = ap.parse_args()

    df = load(args.days)
    print(f'Barras: {len(df):,}')
    ctx_path = EXPORTS / 'mtf_system_contexts.ndjson'
    if not (args.reuse_ctx and ctx_path.exists()):
        write_ctx(ctx_path, context_rows(df))
    expected = expected_trades(df)
    actual = run_rust(ctx_path)
    (EXPORTS / 'expected.json').write_text(json.dumps(expected, indent=1))
    (EXPORTS / 'actual.json').write_text(json.dumps(actual, indent=1))
    result = compare(expected, actual)
    print(f"expected_n={result['expected_n']} actual_n={result['actual_n']} ok={result['ok']}")
    print(json.dumps(result['mismatches'][:3], indent=2))
    if result['ok'] and not args.keep:
        ctx_path.unlink(missing_ok=True)
    sys.exit(0 if result['ok'] else 1)


if __name__ == '__main__':
    main()
