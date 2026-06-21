import sys, pandas as pd, numpy as np, importlib
sys.path.insert(0, 'backtest')
import mtf_v2 as mv
importlib.reload(mv)

df = pd.read_parquet('data/bybit-perp/processed/btcusdt_perp_m1.parquet').sort_values('ts_ms').reset_index(drop=True)
for c in df.select_dtypes('object').columns: df[c] = df[c].fillna('')
for c in df.select_dtypes('float').columns:  df[c] = df[c].fillna(0.0)
for c in ['body_below_poc','fp_absorb_buy','fp_absorb_sell','h1_bos_bear','h1_choch_bear',
          'h1_ob_bear','h1_fvg_bear','h1_bos_bull','h1_ob_bull','fp_result_sell','fp_unfinished_hi']:
    if c in df.columns: df[c] = df[c].astype(bool)

nt_q50 = float(df[df['ts_ms'] < mv.OOS_MS]['n_trades'].quantile(0.50))
OOS = mv.OOS_MS

d1r = mv.resamp(df, mv.D1_MS); d1r['ema20'] = mv.ema(d1r['close'].values, 20)
h1r = mv.resamp(df, mv.H1_MS); h1r['atr'] = mv.atr14(h1r['high'].values, h1r['low'].values, h1r['close'].values)
d1_ctx = {int(r.ts_ms): float(r.ema20) for r in d1r.itertuples()}
h1_ctx = {int(r.ts_ms): (float(r.high), float(r.atr)) for r in h1r.itertuples()}

FEE   = 0.0011
TARGET_R = mv.TARGET_R
FORWARD  = mv.FORWARD
MIN_STOP = mv.MIN_STOP
MAX_STOP = mv.MAX_STOP
LEVEL_TOL = mv.LEVEL_TOL


def run_sim(df, allow_pdh_vah=True, allow_ah=True, vah_tol=0.007, cvd_mode='size'):
    """
    cvd_mode:
      'none'       - sin CVD (n_trades_q50 sizing original)
      'size'       - CVD>0 sube multiplicador
      'gate'       - CVD>0 requerido
    """
    rows = df.to_dict('records')
    trades = []
    in_t = False
    ep = sl = tp = dist = t_risk = t_cvd = 0.0
    t_entry = bars = 0
    mfe = mae = 0.0
    cap = 500.0
    monthly_risk = cap * 0.01
    cur_month = -1

    for i, row in enumerate(rows):
        ts = int(row['ts_ms'])
        month = pd.Timestamp(ts, unit='ms').month + pd.Timestamp(ts, unit='ms').year * 12
        if month != cur_month:
            monthly_risk = cap * 0.01
            cur_month = month

        if in_t:
            mfe = max(mfe, (ep - row['low']) / dist)
            mae = max(mae, (row['high'] - ep) / dist)
            reason = exit_px = None
            if row['high'] >= sl:
                reason, exit_px = 'stop', sl
            elif row['low'] <= tp:
                reason, exit_px = 'target', tp
            elif i - bars >= FORWARD:
                reason, exit_px = 'timeout', row['close']
            if reason:
                gr  = (ep - exit_px) / dist
                net = gr - FEE * ep / dist
                cap += t_risk * net
                trades.append({
                    'ts_ms':      t_entry,
                    'result_r':   round(net, 3),
                    'oos':        t_entry >= OOS,
                    'risk_mult':  round(t_risk / monthly_risk, 2),
                    'cvd_entry':  t_cvd,
                })
                in_t = False
            continue

        hm = (ts // 60_000) % 1440
        if not (7 * 60 <= hm < 20 * 60):
            continue

        d1_ts = (ts // mv.D1_MS) * mv.D1_MS - mv.D1_MS
        d1e = d1_ctx.get(d1_ts)
        if not d1e:
            continue
        if row['close'] > d1e * 0.980:
            continue

        if not (row.get('h1_bos_bear', False) or row.get('h1_choch_bear', False)):
            continue

        hi  = row['high']
        lo  = row['low']
        cl  = row['close']
        op  = row['open']
        cvd = float(row.get('cvd_slope') or 0)

        vah = float(row.get('vp_vah')         or 0)
        pdh = float(row.get('prev_day_high')   or 0)
        ah  = float(row.get('asian_high')      or 0)
        wh  = float(row.get('weekly_high')     or 0)

        hits = []
        if vah > 0 and abs(hi - vah) / vah <= vah_tol:    hits.append('VAH')
        if pdh > 0 and abs(hi - pdh) / pdh <= LEVEL_TOL:  hits.append('PDH')
        if ah  > 0 and abs(hi - ah)  / ah  <= LEVEL_TOL:  hits.append('AH')
        if wh  > 0 and abs(hi - wh)  / wh  <= LEVEL_TOL:  hits.append('WH')
        if not hits:
            continue

        lbl   = '+'.join(hits)
        parts = lbl.split('+')

        # Nivel valido
        has_vah = 'VAH' in parts
        has_pdh = 'PDH' in parts
        has_ah  = 'AH'  in parts
        if not has_vah:
            if not (allow_pdh_vah and has_pdh):
                if not (allow_ah and has_ah):
                    continue
        # PDH+VAH antes excluido — ahora se permite si allow_pdh_vah
        if not allow_pdh_vah and has_pdh and has_vah and len(parts) == 2:
            continue
        # Triple PDH+AH+X -> skip (demasiado noise)
        if len(parts) >= 3 and has_pdh and has_ah:
            continue
        if 'WH' in parts and (ts // 3_600_000) % 24 == 15:
            continue

        # Wick bajista M1
        rng = hi - lo
        if rng <= 0:
            continue
        wu = hi - max(op, cl)
        if not (0.30 < wu / rng < 0.85 and cl <= op):
            continue

        # Footprint gates
        if not row.get('body_below_poc', False):
            continue
        mt = float(row.get('minus_ticks') or 0)
        pt = float(row.get('plus_ticks')  or 0)
        if mt <= pt:
            continue
        if cvd_mode == 'gate' and cvd <= 0:
            continue
        if row.get('fp_absorb_buy', False):
            continue

        # Stop H1
        h1d = h1_ctx.get((ts // mv.H1_MS) * mv.H1_MS)
        if not h1d:
            continue
        h1h, h1a = h1d
        stp = h1h + 0.40 * h1a
        d   = stp - cl
        if d <= 0:
            continue
        if not (MIN_STOP <= d / cl <= MAX_STOP):
            continue

        # Sizing por CVD + tape
        nt = float(row.get('n_trades') or 0)
        tape = nt >= nt_q50
        if cvd_mode == 'size':
            if cvd > 0 and tape: mult = 2.0
            elif cvd > 0 or tape: mult = 1.5
            else: mult = 1.0
        else:
            mult = 1.5 if tape else 1.0

        in_t    = True
        ep      = cl
        sl      = stp
        tp      = cl - TARGET_R * d
        dist    = d
        t_entry = ts
        bars    = i
        mfe     = mae = 0.0
        t_risk  = monthly_risk * mult
        t_cvd   = cvd

    return trades


def st(trades, label, show_cvd=False):
    td = trades if isinstance(trades, pd.DataFrame) else pd.DataFrame(trades)
    if len(td) == 0:
        print(f'  {label}: 0 trades')
        return
    is_t  = td[~td['oos']]
    oos_t = td[td['oos']]

    def s(x):
        r = x['result_r'].values
        if not len(r):
            return 'n=  0  ---'
        w = r[r > 0]; l = r[r < 0]
        pf = w.sum() / abs(l.sum()) if len(l) else 99
        return f'n={len(x):>3}  WR={100*(r>0).mean():>5.1f}%  AvgR={r.mean():>+.3f}  TotR={r.sum():>+6.1f}  PF={pf:.2f}'

    extra = ''
    if show_cvd and 'risk_mult' in td.columns:
        m = is_t['risk_mult'].mean() if len(is_t) else 0
        pct_hi = (is_t['cvd_entry'] > 0).mean() * 100 if len(is_t) else 0
        extra = f'  AvgMult={m:.2f}x  CVD>0={pct_hi:.0f}%IS'
    print(f'  {label:<52}  IS: {s(is_t)}  OOS: {s(oos_t)}{extra}')


# ============================================================
print('=== REFERENCIA v2 ===')
t_ref, _ = mv.simulate(df, mode='v2', n_trades_q50=nt_q50)
st(pd.DataFrame(t_ref), 'v2 original (VAH, tol=0.7%)')

print()
print('=== A2 + VARIANTES CVD ===')
t_a2      = run_sim(df, cvd_mode='none')
t_a2_size = run_sim(df, cvd_mode='size')
t_a2_gate = run_sim(df, cvd_mode='gate')
st(t_a2,      'A2 (PDH+VAH, AH)  sin CVD sizing')
st(t_a2_size, 'A2 (PDH+VAH, AH)  + CVD sizing (1.5/2x)', show_cvd=True)
st(t_a2_gate, 'A2 (PDH+VAH, AH)  + CVD gate duro')

print()
print('=== v2 PURO + SOLO CVD SIZING (sin A2) ===')
t_v2_cvd = run_sim(df, allow_pdh_vah=False, allow_ah=False, cvd_mode='size')
st(t_v2_cvd, 'v2 puro + CVD sizing', show_cvd=True)

print()
print('=== IMPACTO REAL DEL CVD SIZING en v2 puro ===')
td_v2_cvd = pd.DataFrame(t_v2_cvd)
is_hi = td_v2_cvd[~td_v2_cvd['oos'] & (td_v2_cvd['cvd_entry'] > 0)]
is_lo = td_v2_cvd[~td_v2_cvd['oos'] & (td_v2_cvd['cvd_entry'] <= 0)]
r_hi = is_hi['result_r'].values; r_lo = is_lo['result_r'].values
print(f'  IS CVD>0  (2x si tape, 1.5x sinon): n={len(is_hi)}  WR={100*(r_hi>0).mean():.1f}%  AvgR={r_hi.mean():>+.3f}  WtdR={r_hi.mean()*1.75:>+.3f}')
print(f'  IS CVD<=0 (1.5x si tape, 1.0x):     n={len(is_lo)}  WR={100*(r_lo>0).mean():.1f}%  AvgR={r_lo.mean():>+.3f}  WtdR={r_lo.mean()*1.1:>+.3f}')

print()
print('=== CAPITAL FINAL: v2 vs A2+CVD vs v2+CVD (1% compound) ===')
for label, trades in [('v2 sin CVD sizing', list(t_ref)),
                       ('v2 + CVD sizing',   t_v2_cvd),
                       ('A2 + CVD sizing',   t_a2_size)]:
    td = pd.DataFrame(trades)
    cap = 500.0
    for _, row in td.iterrows():
        mult = row.get('risk_mult', 1.0)
        mr   = cap * 0.01
        cap += mr * mult * row['result_r']
    print(f'  {label:<36}  cap final: ${cap:>8,.0f}  ({(cap/500-1)*100:>+.0f}%)')

print()
print('=== ORDERFLOW: AUDITORIA DE EXPLOTACION ===')
# Todas las columnas relevantes y cuanto las usamos
features = {
    'USADOS como gate':       ['body_below_poc','minus_ticks','plus_ticks','fp_absorb_buy'],
    'USADOS como estructura': ['h1_bos_bear','h1_choch_bear','h4_bos_bear','h1_bos_bull','h1_choch_bull'],
    'USADOS como sizing':     ['n_trades'],
    'NUEVO - CVD sizing':     ['cvd_slope'],
    'DISPONIBLES no usados':  ['fp_result_sell','fp_stack_sell','fp_stack_buy','fp_sell_imb',
                                'fp_buy_imb','fp_sell_dom','fp_delta_top','fp_delta_bot',
                                'fp_unfinished_hi','fp_unfinished_lo','obi10_mean','delta',
                                'h1_ob_bear','h1_fvg_bear','h1_ob_bull','h4_bos_bear'],
}

print()
for cat, cols in features.items():
    print(f'  [{cat}]')
    for c in cols:
        if c not in df.columns:
            print(f'    {c:<28} (no en dataset)')
            continue
        if df[c].dtype == bool or df[c].nunique() <= 2:
            pct = df[c].astype(bool).mean() * 100
            print(f'    {c:<28} bool  true={pct:>5.1f}%')
        else:
            pct_pos = (df[c] > 0).mean() * 100
            mn = df[c].mean()
            print(f'    {c:<28} float >0={pct_pos:>5.1f}%  mean={mn:>+.3f}')
    print()
