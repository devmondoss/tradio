#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Convierte los CSVs de backtest a JSON para el frontend React.
Output: apps/trade-lab/public/data/backtest.json

Uso:
    python scripts/gen_backtest_json.py
"""
import csv, json, os, sys

def safe_float(v):
    try: return float(v)
    except: return None

def safe_int(v):
    try: return int(v)
    except: return None

def load_csv(path):
    if not os.path.exists(path):
        print(f"  [skip] {path} no existe")
        return []
    with open(path, encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    print(f"  [ok]   {path} — {len(rows)} filas")
    return rows

# ── RBF ───────────────────────────────────────────────────────────────────────
def parse_rbf(rows):
    out = []
    for r in rows:
        result_r = safe_float(r.get('sim_r') or r.get('result_r') or r.get('orig_r'))
        if result_r is None:
            continue
        out.append({
            'fecha':     r.get('fecha', ''),
            'symbol':    r.get('symbol', ''),
            'direction': r.get('direction', 'Short'),
            'session':   r.get('session', ''),
            'score':     safe_int(r.get('score')),
            'result_r':  result_r,
            'reason':    r.get('reason_sim') or r.get('reason_orig') or r.get('reason', ''),
        })
    return out

# ── AMD ───────────────────────────────────────────────────────────────────────
# Asia excluida live: "Asia: 0 wins, 3 stops → EXCLUIDA" (strategy.toml)
AMD_EXCLUDED_SESSIONS = {'Asia'}

def parse_amd(rows):
    out = []
    skipped = 0
    for r in rows:
        result_r = safe_float(r.get('result_r'))
        if result_r is None:
            continue
        session = r.get('session', '')
        if session in AMD_EXCLUDED_SESSIONS:
            skipped += 1
            continue
        ts = safe_int(r.get('timestamp_ms', 0)) or 0
        from datetime import datetime, timezone
        fecha = datetime.fromtimestamp(ts/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M') if ts else r.get('fecha','')
        out.append({
            'fecha':     fecha,
            'symbol':    r.get('symbol', 'BTCUSDT'),
            'direction': r.get('direction', ''),
            'session':   session,
            'result_r':  result_r,
            'reason':    r.get('exit', ''),
            'vr_spike':  safe_float(r.get('vr_at_spike')),
            'rr':        safe_float(r.get('rr')),
        })
    if skipped:
        print(f"    [filtro] AMD: {skipped} trades Asia excluidos (Asia excluida en config live)")
    return out

# ── BE ────────────────────────────────────────────────────────────────────────
# XRP desactivado live: WR=54%, avg=-0.025R (buyer_exhaustion.toml: [XRPUSDT] enabled=false)
BE_EXCLUDED_SYMBOLS = {'XRPUSDT'}

def parse_be(rows):
    out = []
    skipped = 0
    for r in rows:
        result_r = safe_float(r.get('result_r'))
        if result_r is None:
            continue
        symbol = r.get('symbol', '')
        if symbol in BE_EXCLUDED_SYMBOLS:
            skipped += 1
            continue
        out.append({
            'fecha':         r.get('fecha', ''),
            'symbol':        symbol,
            'direction':     'Short',
            'session':       r.get('session', ''),
            'result_r':      result_r,
            'reason':        r.get('exit_reason', ''),
            'vr':            safe_float(r.get('vr_at_breakout')),
            'cvd_flip':      safe_float(r.get('cvd_flip_ratio')),
            'close_loc':     safe_float(r.get('close_loc')),
            'rr':            safe_float(r.get('rr')),
            'pnl_usd':       safe_float(r.get('pnl_usd')),
            'balance_after': safe_float(r.get('balance_after')),
        })
    if skipped:
        print(f"    [filtro] BE: {skipped} trades XRPUSDT excluidos (desactivado en config live)")
    return out

def main():
    print("Generando backtest.json...")

    rbf_rows = load_csv('scripts/rbf_backtest_results.csv')
    # Fallback al de PnL real si el backtest no tiene 'result_r'
    if rbf_rows and 'result_r' not in rbf_rows[0] and 'sim_r' not in rbf_rows[0]:
        rbf_rows = load_csv('scripts/rbf_pnl_450.csv')

    amd_rows = load_csv('scripts/amd_backtest_results.csv')
    be_rows  = load_csv('scripts/be_backtest_results.csv')

    rbf_out = parse_rbf(rbf_rows)
    amd_out = parse_amd(amd_rows)
    be_out  = parse_be(be_rows)

    data = {
        'generated': __import__('datetime').datetime.now().isoformat(),
        'rbf': rbf_out,
        'amd': amd_out,
        'be':  be_out,
        'meta': {
            'rbf': {'source': 'rbf_backtest_results.csv (39 trades revisados)', 'n': len(rbf_out)},
            'amd': {'source': 'BTCUSDT 163d · sin Asia (excluida live)', 'n': len(amd_out)},
            'be':  {'source': '163d BASE+MICRO · sin XRP (excluido live)', 'n': len(be_out)},
        }
    }

    out_path = 'apps/trade-lab/public/data/backtest.json'
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))

    print(f"\nGuardado en {out_path}")
    print(f"  RBF: {len(data['rbf'])} trades")
    print(f"  AMD: {len(data['amd'])} trades (Asia excluida)")
    print(f"  BE:  {len(data['be'])} trades (XRP excluido)")

if __name__ == '__main__':
    main()
