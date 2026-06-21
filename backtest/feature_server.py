"""
feature_server.py — Sidecar de features (UNA fuente de verdad para backtest y live).
=====================================================================================
Costura Python↔Rust: el monitor Rust (motor) computa la BARRA BASE cada minuto
(OHLCV + primitivos de orderflow/OB + footprint) y la manda por stdin como JSON.
Este sidecar la appendea a un buffer rolling, corre `compute_features.enrich()` (la
MISMA función del backtest) + resample H1 para el stop, y devuelve por stdout el
`MtfSpotBarContext` enriquecido (mismo schema que mtf_system_parity).

Garantía: live usa EXACTAMENTE las definiciones del backtest → cero divergencia.
Cambiar una feature = editar compute_features.py en un solo lugar.

Protocolo (JSON-lines, request/response síncrono, lockstep):
  IN  (Rust→PY): {ts_ms, open, high, low, close, <primitivos base>}   1 línea por barra
  OUT (PY→Rust): {ts_ms, open, high, low, close, h1_high, h1_low, h1_atr,
                  <features del contexto>}                            1 línea por barra
  Línea de control: '{"cmd":"ping"}' → '{"pong":true}'.

Warmup: precarga la cola del parquet para que las features rolling (D1/H1/H4, weekly,
vpin-50, FVG-50, volume profile) tengan historia desde el primer minuto live.

Uso:
  python backtest/feature_server.py [--warmup 15000]
  (en vivo lo lanza el monitor Rust; para test se le pipea NDJSON de barras base)
"""
from __future__ import annotations
import argparse, json, math, sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'backtest'))
import mtf_v2 as m
import compute_spot_features as cf

# Schema de salida = MtfSpotBarContext (idéntico a mtf_system_parity.py)
FLOAT_COLS = ['obi10_mean', 'delta', 'minus_ticks', 'plus_ticks', 'n_trades', 'vpin']
BOOL_COLS = ['body_below_poc', 'fp_absorb_buy', 'fp_absorb_sell',
             'near_bearish_fvg', 'near_bearish_ob', 'displacement_bear',
             'sweep_confirmed', 'vp_lvn_below',
             'h1_bos_bear', 'h1_choch_bear', 'h1_bos_bull', 'h4_bos_bear']
OPT_COLS = ['cvd_slope', 'vp_vah', 'vp_val', 'vp_poc', 'prev_day_high', 'prev_day_low',
            'asian_high', 'asian_low', 'weekly_high', 'weekly_low']


def _clean(v):
    """0/NaN/inf → None (igual semántica que el harness de paridad)."""
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if (not math.isfinite(v) or v == 0.0) else v


def context_from_last_row(buf: pd.DataFrame) -> dict:
    """Corre enrich() + H1 resample sobre el buffer y devuelve el contexto de la última barra."""
    enr = cf.enrich(buf.copy(), verbose=False)

    # H1 high/low/atr del bucket actual (igual que mtf_system_parity: m.resamp + atr14)
    h1 = m.resamp(enr, m.H1_MS)
    h1['atr'] = m.atr14(h1['high'].values, h1['low'].values, h1['close'].values)
    h1c = {int(r.ts_ms): (float(r.high), float(r.low), float(r.atr)) for r in h1.itertuples()}

    row = enr.iloc[-1]
    ts = int(row['ts_ms'])
    hh, hl, ha = h1c.get((ts // m.H1_MS) * m.H1_MS, (None, None, None))

    out = {'ts_ms': ts, 'open': float(row['open']), 'high': float(row['high']),
           'low': float(row['low']), 'close': float(row['close']),
           'h1_high': hh, 'h1_low': hl, 'h1_atr': ha}
    for c in FLOAT_COLS:
        out[c] = float(row[c]) if c in enr.columns and pd.notna(row[c]) else 0.0
    for c in BOOL_COLS:
        out[c] = bool(row[c]) if c in enr.columns and pd.notna(row[c]) else False
    for c in OPT_COLS:
        out[c] = _clean(row[c]) if c in enr.columns else None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--warmup', type=int, default=15000,
                    help='barras de historia precargadas (default ~10 días M1)')
    args = ap.parse_args()

    # Warmup: cola del parquet (todas las columnas base; enrich recomputa derivadas).
    df = pd.read_parquet(m.DATA_M1).sort_values('ts_ms').reset_index(drop=True)
    for c in df.select_dtypes('object').columns: df[c] = df[c].fillna('')
    buf = df.tail(args.warmup).reset_index(drop=True)
    base_cols = list(buf.columns)

    sys.stderr.write(f'[feature_server] listo. warmup={len(buf)} barras, {len(base_cols)} cols base\n')
    sys.stderr.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(json.dumps({'error': 'bad_json'}) + '\n'); sys.stdout.flush(); continue

        if msg.get('cmd') == 'ping':
            sys.stdout.write(json.dumps({'pong': True}) + '\n'); sys.stdout.flush(); continue

        # Appendea la barra base. Columnas faltantes → NaN (enrich las recomputa si son derivadas).
        new_row = {c: msg.get(c, np.nan) for c in base_cols}
        new_row['ts_ms'] = int(msg['ts_ms'])
        buf = pd.concat([buf, pd.DataFrame([new_row])], ignore_index=True)
        if len(buf) > args.warmup:
            buf = buf.iloc[-args.warmup:].reset_index(drop=True)

        try:
            ctx = context_from_last_row(buf)
            sys.stdout.write(json.dumps(ctx, separators=(',', ':')) + '\n')
        except Exception as e:  # nunca tumbar el server por una barra mala
            sys.stdout.write(json.dumps({'error': str(e), 'ts_ms': msg.get('ts_ms')}) + '\n')
        sys.stdout.flush()


if __name__ == '__main__':
    main()
