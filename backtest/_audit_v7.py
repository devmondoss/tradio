"""
_audit_v7.py — auditoria walk-forward del score sizing + target override (shorts v7)
Reutiliza helpers de mtf_basics; reimplementa simulate con toggles para aislar
la contribucion de cada decision y detectar overfitting a OOS.
"""
import numpy as np
import pandas as pd
from collections import defaultdict

import mtf_basics as M


def simulate_param(df, score_sizing=True, target_override=True):
    h1 = M.resamp(df, M.H1_MS)
    h1['atr'] = M.atr14(h1['high'].values, h1['low'].values, h1['close'].values)
    h1_ctx = {int(r.ts_ms): (float(r.high), float(r.atr)) for r in h1.itertuples()}

    rows = df.to_dict('records')
    trades = []
    in_t = False
    ep = sl = tp = dist = 0.0
    t_start = t_entry = 0
    t_lbl = t_sess = ''
    cvd_streak = 0
    entry_risk = 0.0
    entry_score = 0
    cap = M.CAPITAL
    monthly_risk = M.CAPITAL * M.RISK_PCT
    current_month = -1

    for i, row in enumerate(rows):
        ts = int(row['ts_ms'])
        if in_t:
            obi_now = float(row.get('obi10_mean') or 0)
            cs = float(row.get('cvd_slope') or 0)
            cvd_streak = (cvd_streak + 1) if cs > 0 else 0
            cur_r = (ep - row['close']) / dist
            reason = None; exit_px = 0.0
            if row['high'] >= sl:
                reason, exit_px = 'stop', sl
            elif row['low'] <= tp:
                reason, exit_px = 'target', tp
            elif i - t_start >= M.FORWARD:
                reason, exit_px = 'timeout', row['close']
            elif cvd_streak >= 3 and obi_now > 0.15 and cur_r >= 1.0:
                reason, exit_px = 'cvd_exit', row['close']
            if reason:
                pnl_r = (ep - exit_px) / dist
                pnl_usd = entry_risk * pnl_r - entry_risk * M.FEE_RT
                trades.append({'ts_ms': t_entry, 'result_r': round(pnl_r, 3),
                               'pnl_usd': round(pnl_usd, 2), 'oos': ts >= M.OOS_MS,
                               'level': t_lbl, 'session': t_sess, 'score': entry_score,
                               'date': pd.Timestamp(t_entry, unit='ms', tz='UTC').strftime('%Y-%m')})
                cap += pnl_usd
                in_t = False; cvd_streak = 0
            continue

        month = pd.Timestamp(ts, unit='ms', tz='UTC').month + pd.Timestamp(ts, unit='ms', tz='UTC').year * 12
        if month != current_month:
            monthly_risk = cap * M.RISK_PCT
            current_month = month

        sess = M.session(ts)
        if sess not in ('london', 'overlap', 'ny'):
            continue
        ok_level, lbl = M.active_level(row)
        if not ok_level or 'VAH' not in lbl:
            continue
        parts = lbl.split('+')
        if len(parts) >= 3 and 'PDH' in parts and 'AH' in parts:
            continue
        if 'PDH' in parts and 'VAH' in parts and len(parts) == 2:
            continue
        if 'WH' in parts and (ts // 3_600_000) % 24 == 15:
            continue
        if lbl == 'AH+VAH' and float(row.get('obi10_mean') or 0) < -0.15:
            continue
        if not M.rejection(row) or not M.flow_bearish(row):
            continue
        h1d = h1_ctx.get((ts // M.H1_MS) * M.H1_MS)
        if h1d is None:
            continue
        h1h, h1a = h1d
        sl_ = h1h + 0.40 * h1a
        d = sl_ - row['close']
        if d <= 0:
            continue
        sp = d / row['close']
        if not (M.MIN_STOP <= sp <= M.MAX_STOP):
            continue

        reg = str(row.get('regime') or '')
        tgt = 1.5 if reg == 'Chop' else (3.0 if reg == 'Expansion' else M.TARGET_R)
        sc = M.quality_score(row)
        entry_score = sc
        entry_risk = monthly_risk * (M.SCORE_MULT[sc] if score_sizing else 1.0)
        if target_override:
            if sc == 1: tgt = 1.5
            elif sc == 3: tgt = min(tgt, 1.5)

        in_t = True; ep = row['close']; sl = sl_; dist = d
        tp = ep - tgt * dist; t_start = i; t_entry = ts
        t_lbl = lbl; t_sess = sess; cvd_streak = 0

    return trades, cap


def split_stats(trades, tag):
    for lab, oos in [('IS ', False), ('OOS', True)]:
        ts = [t for t in trades if t['oos'] == oos]
        if not ts: continue
        n = len(ts); w = sum(1 for t in ts if t['result_r'] > 0)
        tot = sum(t['result_r'] for t in ts)
        print(f'  [{tag}] {lab}  n={n:>4}  WR={w/n*100:>5.1f}%  AvgR={tot/n:>+.3f}  TotalR={tot:>6.1f}')


def main():
    df = pd.read_parquet(M.DATA_M1).sort_values('ts_ms').reset_index(drop=True)
    for c in df.columns:
        if df[c].dtype == object: df[c] = df[c].fillna('')
        elif df[c].dtype == float: df[c] = df[c].fillna(0.0)

    print('=== TEST 1: contribucion de cada decision (capital final) ===')
    for ss, to, name in [(True, True, 'v7 full (sizing+override)'),
                         (True, False, 'sizing, SIN override'),
                         (False, True, 'flat 2%, CON override'),
                         (False, False, 'flat 2%, SIN override (base)')]:
        tr, cap = simulate_param(df, score_sizing=ss, target_override=to)
        print(f'\n{name}:  capital ${cap:,.0f}')
        split_stats(tr, name[:12])

    # TEST 2: verificar que thresholds hardcoded == Q50 de entry bars SOLO-IS (no leakage)
    print('\n=== TEST 2: leakage de thresholds (Q50 IS vs hardcoded) ===')
    tr, _ = simulate_param(df, score_sizing=False, target_override=False)
    # reconstruir entry rows IS para medir Q50 real
    is_ts = set(t['ts_ms'] for t in tr if not t['oos'])
    is_rows = df[df['ts_ms'].isin(is_ts)]
    for col, hard in [('sell_vol', M.SCORE_SV_Q50), ('buy_vol', M.SCORE_BV_Q50), ('vr', M.SCORE_VR_Q50)]:
        q50_is = is_rows[col].astype(float).quantile(0.50)
        q50_full = df[df['ts_ms'].isin(set(t['ts_ms'] for t in tr))][col].astype(float).quantile(0.50)
        print(f'  {col:<10} hardcoded={hard:>7.3f}  Q50_IS_entries={q50_is:>7.3f}  Q50_full_entries={q50_full:>7.3f}')

    # TEST 3: monotonicidad del score IS vs OOS
    print('\n=== TEST 3: score IS vs OOS (AvgR debe ser monotono si el score mide calidad) ===')
    tr, _ = simulate_param(df, score_sizing=True, target_override=True)
    for oos in [False, True]:
        lab = 'OOS' if oos else 'IS '
        by = defaultdict(list)
        for t in tr:
            if t['oos'] == oos: by[t['score']].append(t['result_r'])
        line = f'  {lab}: '
        for sc in range(5):
            rs = by.get(sc, [])
            if rs:
                line += f'sc{sc}={sum(rs)/len(rs):+.2f}(n{len(rs)}) '
        print(line)


if __name__ == '__main__':
    main()
