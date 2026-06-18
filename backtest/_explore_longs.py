"""
_explore_longs.py
Espejo de shorts: rebotes alcistas en soporte.
Misma logica 3 ingredientes pero invertida.

NIVEL   — low dentro del 0.70% de VAL / PDL / AL / WL (VAL requerido)
RECHAZO — wick inferior 30-85% + cierre alcista
FLUJO   — obi10_mean > +0.05 OR delta > 0
Stop    — H1_low - 0.40 x ATR_H1, rango 0.30%-0.75%
Target  — 2R (ajustable)
"""
import pandas as pd, numpy as np
from collections import defaultdict

CAPITAL = 500.0; RISK_PCT = 0.02; FEE_RT = 0.0007
TARGET_R = 2.0; FORWARD = 1200; MIN_STOP = 0.003; MAX_STOP = 0.0075
H1_MS = 3_600_000
OOS_MS = int(pd.Timestamp('2026-03-01', tz='UTC').value // 1_000_000)
LEVEL_TOL = 0.007

def atr14(h, l, c):
    tr = np.maximum(h-l, np.maximum(np.abs(h-np.roll(c,1)), np.abs(l-np.roll(c,1)))); tr[0]=h[0]-l[0]
    o = np.empty_like(tr); o[:14] = tr[:14].mean(); k = 1/14
    for i in range(14, len(tr)): o[i] = o[i-1]*(1-k) + tr[i]*k
    return o

def resamp(df, f):
    df = df.copy(); df['tf'] = (df['ts_ms']//f)*f
    return df.groupby('tf', sort=True).agg(
        open=('open','first'), high=('high','max'),
        low=('low','min'), close=('close','last')
    ).reset_index().rename(columns={'tf':'ts_ms'})

def session(ts):
    hm = (ts//60_000) % 1440
    if  7*60 <= hm < 12*60: return 'london'
    if 12*60 <= hm < 16*60: return 'overlap'
    if 16*60 <= hm < 20*60: return 'ny'
    return ''

def simulate(df):
    h1 = resamp(df, H1_MS)
    h1['atr'] = atr14(h1['high'].values, h1['low'].values, h1['close'].values)
    # Para longs necesitamos el LOW de H1 como referencia del stop
    h1_ctx = {int(r.ts_ms): (float(r.low), float(r.atr)) for r in h1.itertuples()}

    rows = df.to_dict('records'); trades = []; in_t = False
    ep = sl = tp = dist = 0.0; t_start = t_entry = 0; t_lbl = t_sess = ''
    cvd_streak = 0; cap = CAPITAL; monthly_risk = CAPITAL * RISK_PCT; current_month = -1

    for i, row in enumerate(rows):
        ts = int(row['ts_ms'])

        if in_t:
            obi = float(row.get('obi10_mean') or 0); cs = float(row.get('cvd_slope') or 0)
            # CVD exit longs: CVD negativo sostenido = vendedores tomando control
            cvd_streak = (cvd_streak+1) if cs < 0 else 0
            cur_r = (row['close'] - ep)/dist; reason = None; exit_px = 0.0
            if row['low'] <= sl:         reason, exit_px = 'stop',    sl
            elif row['high'] >= tp:      reason, exit_px = 'target',  tp
            elif i - t_start >= FORWARD: reason, exit_px = 'timeout', row['close']
            elif cvd_streak >= 5 and obi < -0.15 and cur_r >= 1.0:
                reason, exit_px = 'cvd_exit', row['close']
            if reason:
                pnl_r = (exit_px - ep)/dist
                pnl_usd = monthly_risk * pnl_r - monthly_risk * FEE_RT
                trades.append({
                    'ts_ms': t_entry, 'result_r': round(pnl_r,3),
                    'oos': ts >= OOS_MS, 'level': t_lbl, 'sess': t_sess,
                    'reason': reason
                })
                cap += pnl_usd; in_t = False; cvd_streak = 0
            continue

        month = pd.Timestamp(ts, unit='ms', tz='UTC').month + pd.Timestamp(ts, unit='ms', tz='UTC').year * 12
        if month != current_month:
            monthly_risk = cap * RISK_PCT; current_month = month

        sess = session(ts)
        if not sess: continue

        # ── NIVEL — usamos LOW de la barra vs niveles de soporte ───────────────
        low = row['low']; levels = []
        for k, col in [('PDL','prev_day_low'),('AL','asian_low'),('WL','weekly_low'),('VAL','vp_val')]:
            v = row.get(col)
            if v and v > 0 and abs(low - v)/v <= LEVEL_TOL: levels.append(k)
        if not levels: continue
        lbl = '+'.join(levels)
        if 'VAL' not in lbl: continue   # VAL requerido (espejo de VAH)
        parts = lbl.split('+')

        # Bloqueos espejo de shorts
        if len(parts) >= 3 and 'PDL' in parts and 'AL' in parts: continue  # PDL+AL+VAL
        if 'PDL' in parts and 'VAL' in parts and len(parts) == 2: continue  # falsa confluencia
        if 'WL' in parts and (ts//3_600_000)%24 == 15: continue            # WL tóxico 15h
        # AL+VAL cuando OBI > +0.15: Asian Low ya atacado agresivamente (espejo de AH+VAH OBI<-0.15)
        if lbl == 'AL+VAL' and float(row.get('obi10_mean') or 0) > 0.15: continue

        # ── RECHAZO — wick inferior 30-85% + cierre alcista ───────────────────
        c, o, h, l = float(row['close']), float(row['open']), float(row['high']), float(row['low'])
        rng = h - l
        if rng <= 0: continue
        wick_dn = min(c, o) - l
        wick_pct = wick_dn / rng
        if not (0.30 < wick_pct < 0.85) or c < o: continue  # doji bloqueado, necesita cierre alcista

        # ── FLUJO — presión compradora ─────────────────────────────────────────
        obi = float(row.get('obi10_mean') or 0); delta = float(row.get('delta') or 0)
        if obi <= 0.05 and delta <= 0: continue  # espejo de shorts flow

        # ── STOP H1 estructural ────────────────────────────────────────────────
        h1d = h1_ctx.get((ts//H1_MS)*H1_MS)
        if h1d is None: continue
        h1l, h1a = h1d; sl_ = h1l - 0.40*h1a; d = row['close'] - sl_
        if d <= 0: continue
        sp = d/row['close']
        if not (MIN_STOP <= sp <= MAX_STOP): continue

        in_t = True; ep = row['close']; sl = sl_; dist = d; tp = ep + TARGET_R*dist
        t_start = i; t_entry = ts; t_lbl = lbl; t_sess = sess; cvd_streak = 0

    return trades, cap

print('Cargando M1...')
df = pd.read_parquet('data/bybit-spot/processed/btcusdt_m1.parquet').sort_values('ts_ms').reset_index(drop=True)
for c in df.columns:
    if df[c].dtype == object: df[c] = df[c].fillna('')
    elif df[c].dtype == float: df[c] = df[c].fillna(0.0)

trades, cap = simulate(df)
ist  = [t for t in trades if not t['oos']]
oot  = [t for t in trades if t['oos']]

def stats(ts, label, days):
    if not ts: print(f'{label}: sin trades'); return
    n = len(ts); w = sum(1 for t in ts if t['result_r'] > 0)
    avgr = sum(t['result_r'] for t in ts)/n
    print(f'\n{label}')
    print(f'  n={n}  WR={w/n*100:.1f}%  AvgR={avgr:+.3f}  TotalR={avgr*n:.1f}R  tpd={n/days:.1f}')
    stops  = sum(1 for t in ts if t['reason']=='stop')
    tgts   = sum(1 for t in ts if t['reason']=='target')
    cvds   = sum(1 for t in ts if t['reason']=='cvd_exit')
    touts  = sum(1 for t in ts if t['reason']=='timeout')
    print(f'  target={tgts}  stop={stops}  cvd_exit={cvds}  timeout={touts}')

stats(ist,  'IN-SAMPLE    (Jun 2025 – Feb 2026)', 260)
stats(oot,  'OUT-OF-SAMPLE (Mar 2026 – May 2026)', 90)

print(f'\nCapital: ${CAPITAL:.0f} -> ${cap:,.0f}  ({(cap/CAPITAL-1)*100:.1f}%)')

print('\n-- Por nivel OOS -------------------------------------------')
by_lbl = defaultdict(list)
for t in oot: by_lbl[t['level']].append(t['result_r'])
for lbl in sorted(by_lbl, key=lambda x: -len(by_lbl[x])):
    rs = by_lbl[lbl]; n = len(rs); w = sum(1 for r in rs if r > 0)
    print(f'  {lbl:<22}  n={n:>3}  WR={w/n*100:>5.1f}%  AvgR={sum(rs)/n:>+.3f}')

print('\n-- Por sesion OOS ------------------------------------------')
by_sess = defaultdict(list)
for t in oot: by_sess[t['sess']].append(t['result_r'])
for sess in ['london','overlap','ny']:
    rs = by_sess[sess]; n = len(rs)
    if n < 5: continue
    w = sum(1 for r in rs if r > 0)
    print(f'  {sess:<10}  n={n:>3}  WR={w/n*100:>5.1f}%  AvgR={sum(rs)/n:>+.3f}')

print('\n-- Comparativa shorts vs longs OOS -------------------------')
print(f'  {"Sistema":<10}  {"n_OOS":>6}  {"WR":>6}  {"AvgR":>8}  {"tpd":>5}')
print(f'  {"Shorts":<10}  {301:>6}  {"49.5%":>6}  {"+0.338":>8}  {"3.6":>5}')
n_l = len(oot); w_l = sum(1 for t in oot if t['result_r']>0)
ar_l = sum(t['result_r'] for t in oot)/n_l if oot else 0
print(f'  {"Longs":<10}  {n_l:>6}  {w_l/n_l*100:>5.1f}%  {ar_l:>+.3f}     {n_l/90:>4.1f}')
print(f'  {"COMBINADO":<10}  {301+n_l:>6}  {"~?":>6}  {"~?":>8}  {(301+n_l)/90:>4.1f}')
