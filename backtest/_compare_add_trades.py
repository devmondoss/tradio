"""
_compare_add_trades.py
Objetivo: agregar trades SIN bajar calidad del sistema actual ($38,545 BASE).

Candidatos:
  A) POC como nivel de resistencia adicional  (vp_poc en datos)
  B) London session re-habilitada             (08-12 UTC excluida hoy)
  C) London solo con WH en etiqueta           (high WR combo en London)
  D) PDL como resistencia (soporte roto)      (prev_day_low)
  E) POC + London
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
    if  8*60 <= hm < 12*60: return 'london'
    if 12*60 <= hm < 16*60: return 'overlap'
    if 16*60 <= hm < 20*60: return 'ny'
    return ''

def simulate(df, add_poc=False, add_pdl=False, london=False, london_wh_only=False):
    h1 = resamp(df, H1_MS)
    h1['atr'] = atr14(h1['high'].values, h1['low'].values, h1['close'].values)
    h1_ctx = {int(r.ts_ms): (float(r.high), float(r.atr)) for r in h1.itertuples()}

    rows = df.to_dict('records'); trades = []; in_t = False
    ep = sl = tp = dist = 0.0; t_start = t_entry = 0; t_lbl = ''; cvd_streak = 0; cap = CAPITAL

    for i, row in enumerate(rows):
        ts = int(row['ts_ms'])

        if in_t:
            obi = float(row.get('obi10_mean') or 0); cs = float(row.get('cvd_slope') or 0)
            cvd_streak = (cvd_streak+1) if cs > 0 else 0
            cur_r = (ep - row['close'])/dist; reason = None; exit_px = 0.0
            if row['high'] >= sl:   reason, exit_px = 'stop',    sl
            elif row['low'] <= tp:  reason, exit_px = 'target',  tp
            elif i - t_start >= FORWARD: reason, exit_px = 'timeout', row['close']
            elif cvd_streak >= 5 and obi > 0.15 and cur_r >= 1.0: reason, exit_px = 'cvd_exit', row['close']
            if reason:
                pnl_r = (ep - exit_px)/dist; risk_usd = cap*RISK_PCT
                pnl_usd = risk_usd*pnl_r - cap*RISK_PCT*FEE_RT
                trades.append({'ts_ms': t_entry, 'result_r': round(pnl_r,3), 'oos': ts>=OOS_MS,
                                'level': t_lbl, 'sess': session(t_entry)})
                cap += pnl_usd; in_t = False; cvd_streak = 0
            continue

        sess = session(ts)
        if not sess: continue

        # Sesiones habilitadas
        allowed = ['overlap', 'ny']
        if london or london_wh_only: allowed.append('london')
        if sess not in allowed: continue

        # ── Nivel ──────────────────────────────────────────────────────────────
        high = row['high']; levels = []
        for k, col in [('PDH','prev_day_high'), ('PDL','prev_day_low'),
                       ('AH','asian_high'), ('WH','weekly_high'),
                       ('POC','vp_poc'), ('VAH','vp_vah')]:
            if k == 'PDL' and not add_pdl: continue
            if k == 'POC' and not add_poc: continue
            v = row.get(col)
            if v and v > 0 and abs(high - v)/v <= LEVEL_TOL: levels.append(k)
        if not levels: continue

        lbl = '+'.join(levels)
        if 'VAH' not in lbl: continue   # VAH siempre requerido
        parts = lbl.split('+')

        # Bloqueos existentes
        if len(parts) >= 3 and 'PDH' in parts and 'AH' in parts: continue
        if 'PDH' in parts and 'VAH' in parts and len(parts) == 2: continue
        if 'WH' in parts and (ts//3_600_000)%24 == 15: continue
        if lbl == 'AH+VAH' and float(row.get('obi10_mean') or 0) < -0.15: continue

        # London: solo permitir si hay WH en el label
        if sess == 'london' and london_wh_only and 'WH' not in parts: continue

        # ── Rechazo ────────────────────────────────────────────────────────────
        c, o, h, l = float(row['close']), float(row['open']), float(row['high']), float(row['low'])
        rng = h - l
        if rng <= 0: continue
        wick_pct = (h - max(c,o))/rng
        if not (0.30 < wick_pct < 0.85) or c > o: continue

        # ── Flujo ──────────────────────────────────────────────────────────────
        obi = float(row.get('obi10_mean') or 0); delta = float(row.get('delta') or 0)
        if obi >= -0.05 and delta >= 0: continue

        # ── Stop H1 ────────────────────────────────────────────────────────────
        h1d = h1_ctx.get((ts//H1_MS)*H1_MS)
        if h1d is None: continue
        h1h, h1a = h1d; sl_ = h1h + 0.40*h1a; d = sl_ - row['close']
        if d <= 0: continue
        if not (MIN_STOP <= d/row['close'] <= MAX_STOP): continue

        in_t = True; ep = row['close']; sl = sl_; dist = d; tp = ep - TARGET_R*dist
        t_start = i; t_entry = ts; t_lbl = lbl; cvd_streak = 0

    return trades, cap

print('Cargando M1...')
df = pd.read_parquet('data/bybit-spot/processed/btcusdt_m1.parquet').sort_values('ts_ms').reset_index(drop=True)
for c in df.columns:
    if df[c].dtype == object: df[c] = df[c].fillna('')
    elif df[c].dtype == float: df[c] = df[c].fillna(0.0)

configs = [
    (False, False, False, False, 'BASE v3+AH'),
    (True,  False, False, False, '+ POC'),
    (False, True,  False, False, '+ PDL'),
    (True,  True,  False, False, '+ POC + PDL'),
    (False, False, True,  False, '+ London completo'),
    (False, False, False, True,  '+ London WH only'),
    (True,  False, False, True,  '+ POC + London WH'),
    (True,  True,  False, True,  '+ POC+PDL+London WH'),
]

print(f"\n{'Config':<26}  {'n_tot':>6}  {'WR_IS':>6}  {'WR_OOS':>6}  {'n_OOS':>5}  {'AvgR_OOS':>9}  {'tpd_OOS':>8}  {'Capital':>10}")
print('-'*105)

for poc, pdl, lon, lonwh, label in configs:
    trades, cap = simulate(df, add_poc=poc, add_pdl=pdl, london=lon, london_wh_only=lonwh)
    ist = [t for t in trades if not t['oos']]; oot = [t for t in trades if t['oos']]
    def s(ts):
        if not ts: return 0, 0.0, 0.0
        n = len(ts); w = sum(1 for t in ts if t['result_r'] > 0)
        return n, w/n*100, sum(t['result_r'] for t in ts)/n
    ni, wi, _ = s(ist); no, wo, ao = s(oot)
    delta_n = no - 204  # vs BASE OOS
    sign = '+' if delta_n >= 0 else ''
    print(f"{label:<26}  {ni+no:>6}  {wi:>5.1f}%  {wo:>5.1f}%  {no:>5} ({sign}{delta_n:+d})  {ao:>+.3f}      {no/90:>5.1f}    ${cap:>9,.0f}")

# Desglose de trades nuevos que agrega POC
print('\n-- Trades OOS nuevos con POC (lbl contiene POC) ---------------')
trades_poc, _ = simulate(df, add_poc=True)
oot_poc = [t for t in trades_poc if t['oos'] and 'POC' in t['level']]
by_lbl = defaultdict(list)
for t in oot_poc: by_lbl[t['level']].append(t['result_r'])
for lbl in sorted(by_lbl, key=lambda x: -len(by_lbl[x])):
    rs = by_lbl[lbl]; n = len(rs); w = sum(1 for r in rs if r > 0)
    print(f"  {lbl:<25}  n={n:>3}  WR={w/n*100:>5.1f}%  AvgR={sum(rs)/n:>+.3f}")

print('\n-- Trades OOS nuevos con London WH only -----------------------')
trades_lonwh, _ = simulate(df, london_wh_only=True)
oot_lonwh = [t for t in trades_lonwh if t['oos'] and t['sess'] == 'london']
by_lbl2 = defaultdict(list)
for t in oot_lonwh: by_lbl2[t['level']].append(t['result_r'])
for lbl in sorted(by_lbl2, key=lambda x: -len(by_lbl2[x])):
    rs = by_lbl2[lbl]; n = len(rs); w = sum(1 for r in rs if r > 0)
    print(f"  {lbl:<25}  n={n:>3}  WR={w/n*100:>5.1f}%  AvgR={sum(rs)/n:>+.3f}")
