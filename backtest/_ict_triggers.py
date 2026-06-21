"""
_ict_triggers.py — Menú de disparadores ICT/SMC sobre la MISMA confirmación de orderflow.
==========================================================================================
Objetivo: subir de 0.3 tpd a 2-4 tpd añadiendo disparadores paralelos (no aflojando el edge).

Diseño: la confirmación de orderflow es CONSTANTE (régimen D1 + H1 estructura + agresión
minus>plus + body_below_poc + veto fp_absorb_buy). Solo cambia el DISPARADOR (qué define
"estamos en una ubicación shorteable con reacción"). Exit/stop idénticos a mtf_v2.

Cada disparador se mide aislado (tpd + IS/OOS) y luego la UNIÓN (cualquiera dispara).
Uso: python backtest/_ict_triggers.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'backtest'))
import mtf_v2 as m


def load():
    df = pd.read_parquet(m.DATA_M1).sort_values('ts_ms').reset_index(drop=True)
    for c in df.select_dtypes('object').columns:  df[c] = df[c].fillna('')
    for c in df.select_dtypes('float').columns:   df[c] = df[c].fillna(0.0)
    for c in df.select_dtypes('bool').columns:     df[c] = df[c].fillna(False)
    return df


# ── disparadores (cada uno: row -> bool). Definen ubicación+reacción shorteable. ──
def trig_rejection_vah(row):
    ok, lbl = m.active_level(row)
    return ok and 'VAH' in lbl and m.rejection(row)

def trig_ote(row):
    # OTE: precio en zona fib 62-79% de un swing bajista + rechazo
    return bool(row.get('fib_ote', 0)) and m.rejection(row)

def trig_ob(row):
    # Order Block bajista: precio retesteando OB + rechazo
    return bool(row.get('near_bearish_ob', False)) and m.rejection(row)

def trig_fvg(row):
    # Fair Value Gap bajista cercano + rechazo (FVG muy común → exige rechazo)
    return bool(row.get('near_bearish_fvg', False)) and m.rejection(row)

def trig_sweep(row):
    # Liquidity sweep confirmado (sweep de high + reclaim) — disparador ICT puro
    return bool(row.get('sweep_confirmed', False))

def trig_eqh_sweep(row):
    # Sweep de equal highs (liquidez de stops sobre dobles techos)
    return bool(row.get('equal_high_sweep', False))

def trig_displacement(row):
    # Displacement bajista (vela de impulso institucional)
    return bool(row.get('displacement_bear', False))

TRIGGERS = {
    'rejection_VAH (actual)': trig_rejection_vah,
    'OTE (fib 62-79)':        trig_ote,
    'OrderBlock retest':      trig_ob,
    'FVG bajista':            trig_fvg,
    'Liquidity sweep':        trig_sweep,
    'EqualHigh sweep':        trig_eqh_sweep,
    'Displacement':           trig_displacement,
}


def simulate_trig(df, trigger, n_trades_q50, vpin_q50, use_h1=True, lvn_veto=True):
    h1 = m.resamp(df, m.H1_MS)
    h1['atr'] = m.atr14(h1['high'].values, h1['low'].values, h1['close'].values)
    h1_ctx = {int(r.ts_ms): (float(r.high), float(r.atr)) for r in h1.itertuples()}
    d1 = m.resamp(df, m.D1_MS); d1['ema20'] = m.ema(d1['close'].values, 20)
    d1_ctx = {int(r.ts_ms): float(r.ema20) for r in d1.itertuples()}

    rows = df.to_dict('records')
    trades = []
    in_t = False
    ep = sl = tp = dist = 0.0; t_start = t_entry = 0; t_sess = ''
    mfe = mae = 0.0; entry_risk = 0.0
    cap = m.CAPITAL; monthly_risk = m.CAPITAL * m.RISK_PCT; current_month = -1

    for i, row in enumerate(rows):
        ts = int(row['ts_ms'])
        if in_t:
            mfe = max(mfe, (ep - row['low'])/dist); mae = max(mae, (row['high']-ep)/dist)
            bars_el = i - t_start; reason = exit_px = None
            if row['high'] >= sl:   reason, exit_px = 'stop', sl
            elif row['low'] <= tp:  reason, exit_px = 'target', tp
            elif bars_el >= m.FORWARD: reason, exit_px = 'timeout', row['close']
            if reason:
                gross_r = (ep - exit_px)/dist; fee_r = m.FEE_RT*ep/dist
                pnl_r = gross_r - fee_r
                trades.append({'ts_ms': t_entry, 'result_r': pnl_r,
                               'pnl_usd': entry_risk*pnl_r, 'bars': bars_el,
                               'oos': t_entry >= m.OOS_MS})
                cap += entry_risk*pnl_r; in_t = False
            continue

        month = pd.Timestamp(ts, unit='ms', tz='UTC').month + pd.Timestamp(ts, unit='ms', tz='UTC').year*12
        if month != current_month:
            monthly_risk = cap * m.RISK_PCT; current_month = month

        sess = m.session(ts)
        if sess not in ('london', 'overlap', 'ny'): continue

        # ── Confirmación de orderflow CONSTANTE ──────────────────────────────
        d1_ema = d1_ctx.get((ts // m.D1_MS)*m.D1_MS - m.D1_MS)
        if d1_ema is None or row['close'] > d1_ema*0.980: continue
        if use_h1 and not (row.get('h1_bos_bear', False) or row.get('h1_choch_bear', False)): continue

        # ── DISPARADOR (lo único que cambia) ─────────────────────────────────
        if not trigger(row): continue

        if not row.get('body_below_poc', False): continue
        if float(row.get('minus_ticks') or 0) <= float(row.get('plus_ticks') or 0): continue
        if row.get('fp_absorb_buy', False): continue
        if lvn_veto and not row.get('vp_lvn_below', False): continue

        # sizing (vpin-aware, igual que v2 nuevo)
        nt = float(row.get('n_trades') or 0); cvd = float(row.get('cvd_slope') or 0)
        vpin = float(row.get('vpin') or 0); obi10 = float(row.get('obi10_mean') or 0)
        tape = nt >= n_trades_q50; vpin_hi = vpin >= vpin_q50
        if   tape and vpin_hi and cvd > 0: mult = 2.5
        elif tape and vpin_hi:             mult = 2.0
        elif cvd > 0 or tape or obi10 >= 0: mult = 1.5
        else:                              mult = 1.0
        entry_risk = monthly_risk * mult

        h1d = h1_ctx.get((ts // m.H1_MS)*m.H1_MS)
        if h1d is None: continue
        h1h, h1a = h1d
        sl_ = h1h + 0.40*h1a; d = sl_ - row['close']
        if d <= 0: continue
        sp = d/row['close']
        if not (m.MIN_STOP <= sp <= m.MAX_STOP): continue

        in_t = True; ep = row['close']; sl = sl_; dist = d
        tp = ep - m.TARGET_R*dist; t_start = i; t_entry = ts; t_sess = sess
        mfe = mae = 0.0

    return trades


def report(trades, label, days=533):
    is_t = [t for t in trades if not t['oos']]; oos_t = [t for t in trades if t['oos']]
    def s(ts):
        if not ts: return (0,0,0,0,0)
        n=len(ts); wr=sum(1 for t in ts if t['result_r']>0)/n*100
        return (n, wr, sum(t['result_r'] for t in ts)/n, sum(t['result_r'] for t in ts), n/days)
    ni,wi,ai,ti,_ = s(is_t); no,wo,ao,to,_ = s(oos_t)
    tpd = len(trades)/days
    print(f'  {label:<24} | IS n={ni:>4} WR={wi:>5.1f}% AvgR={ai:>+.3f} TotR={ti:>+6.1f} '
          f'| OOS n={no:>3} WR={wo:>5.1f}% AvgR={ao:>+.3f} TotR={to:>+5.1f} | {tpd:>4.2f} tpd')


def main():
    df = load()
    is_df = df[df['ts_ms'] < m.OOS_MS]
    nt_q50 = float(is_df['n_trades'].quantile(0.50)); vpin_q50 = float(is_df['vpin'].quantile(0.50))
    days = (df.ts_ms.max()-df.ts_ms.min())/86_400_000

    print(f'Días: {days:.0f}   n_trades Q50: {nt_q50:.0f}   vpin Q50: {vpin_q50:.3f}\n')
    print('='*128); print('DISPARADORES AISLADOS (misma confirmación orderflow, mismo exit)'); print('='*128)
    all_trades = {}
    for name, trig in TRIGGERS.items():
        t = simulate_trig(df, trig, nt_q50, vpin_q50)
        all_trades[name] = t
        report(t, name, days)

    print('\n' + '='*128)
    print('UNIÓN — cualquier disparador confirmado (1 posición a la vez, prioridad por orden)')
    print('='*128)
    def union_trigger(row):
        return any(trig(row) for trig in TRIGGERS.values())
    tu = simulate_trig(df, union_trigger, nt_q50, vpin_q50)
    report(tu, 'UNIÓN todos', days)

    # unión sin H1 gate (¿el H1 BOS/ChoCH limita demasiado?)
    tu2 = simulate_trig(df, union_trigger, nt_q50, vpin_q50, use_h1=False)
    report(tu2, 'UNIÓN sin gate H1', days)


if __name__ == '__main__':
    main()
