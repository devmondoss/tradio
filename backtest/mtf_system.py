"""
mtf_system.py — SISTEMA CANÓNICO (modelo 'directions', validado 2026-06-19)
============================================================================
Estado del arte del MTF sobre futuros bybit-perp. Reemplaza al short-único de v2.

Qué consolida (todo validado IS/OOS, ver worklog 2026-06-19):
  • Unión de disparadores ICT para shorts: rejection@VAH + FVG + OrderBlock +
    Displacement + LiquiditySweep. (OTE y EqualHighSweep EXCLUIDOS: flip OOS.)
    Nota: la unión es no-separable (slot-competition); quitar triggers la empeora.
  • Longs espejo (rechazo en VAL/AL/PDL/WL, régimen alcista, H1 bull, vetos).
  • Concurrencia por dirección: 1 short + 1 long. NUNCA se solapan (regímenes D1
    opuestos), así que es volumen sin riesgo correlacionado. Capital COMPARTIDO.
  • Confirmación orderflow constante: D1 EMA20 + H1 estructura + body_below_poc +
    minus/plus_ticks + veto fp_absorb + veto vp_lvn_below.
  • Sizing vpin-aware: tape & vpin_hi es la firma institucional robusta OOS.
  • Salida: target 2.8R, timeout 4h (240m), stop H1 ± 0.40·ATR (0.30–0.75%).

⚠️ 2026-06-19 sesión 2 — LOS NÚMEROS DE ABAJO ($74.9K) ERAN LOOKAHEAD. NO son reales.
  La estructura H1/H4 tenía lookahead intra-hora (corregido en compute_spot_features).
  Honesto causal: ~$3.3K, OOS combinado +0.126R, longs negativos. SIN edge desplegable en BTC.
  Config actual revertido a baseline honesto (unión, sin gates fp overfit, sin vpin, LONGS_ENABLED=False).
  Ver docs/mtf/MTF_WORKLOG_2026-06-19_lookahead.md para el arco completo.

Resultado walk-forward [INVÁLIDO — lookahead] (533 días, fee perp 0.11%):
  IS  n≈309  WR≈54.7%  AvgR≈+0.502   |   OOS n≈82  WR≈53.7%  AvgR≈+0.412   |   $500→$74.9k

Uso: python backtest/mtf_system.py
"""
import sys
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'backtest'))
import mtf_v2 as m   # helpers: ema, atr14, resamp, session, active_level(_long), rejection(_long)

EXPORTS = ROOT / 'exports'

TARGET_R = 2.8
FWD      = 240          # timeout 4h — la cola swing >4h aporta poco (sweep 2026-06-19)
COOLDOWN = 15           # barras (min) entre entradas de la misma dirección (anti-stack)
MAX_OPEN_PER_SIDE = 1   # modelo 'directions'
LONGS_ENABLED = False   # longs negativos sobre datos causales (-0.104R) → solo shorts


# ── disparadores ICT shorts (unión validada) ───────────────────────────────────

def _short_trigger(row) -> str:
    """Devuelve el nombre del disparador que activa, o '' si ninguno."""
    ok, lbl = m.active_level(row)
    if ok and 'VAH' in lbl and m.rejection(row):    return 'rejection_VAH'
    if row.get('near_bearish_ob', False) and m.rejection(row):  return 'OrderBlock'
    if row.get('near_bearish_fvg', False) and m.rejection(row): return 'FVG'
    if row.get('displacement_bear', False):         return 'Displacement'
    if row.get('sweep_confirmed', False):           return 'LiquiditySweep'
    # Sobre datos CAUSALES (2026-06-19) la "fusión ICT" no es claramente robusta (FVG IS≈0,
    # Displacement/Sweep flipean OOS), pero la unión completa da OOS ~+0.16R vs rejection-solo
    # ~-0.06R por dinámica de slots. NINGÚN config supera nivel de ruido. Ver worklog.
    return ''


# ── elegibilidad (confirmación orderflow constante) ─────────────────────────────

def _elig_short(row, d1_ema):
    if d1_ema is None or row['close'] > d1_ema * 0.980: return None
    if not (row.get('h1_bos_bear', False) or row.get('h1_choch_bear', False)): return None
    trig = _short_trigger(row)
    if not trig: return None
    if not row.get('body_below_poc', False): return None
    if float(row.get('minus_ticks') or 0) <= float(row.get('plus_ticks') or 0): return None
    if row.get('fp_absorb_buy', False): return None
    if not row.get('vp_lvn_below', False): return None
    # NOTA 2026-06-19: probamos gates fp_result_sell + veto ask_wall. Daban +0.44R OOS
    # PERO solo sobre el motor mtf_v2 (timeout 20h, level-blocks) — en ESTE motor (directions,
    # timeout 4h) daban OOS NEGATIVO. Era overfit a un harness distinto. Descartados.
    return trig

def _elig_long(row, d1_ema):
    if d1_ema is None: return None
    ratio = row['close'] / d1_ema
    if not (1.000 <= ratio <= 1.030): return None
    if row.get('h4_bos_bear', False): return None
    if not row.get('h1_bos_bull', False): return None
    ok, lbl = m.active_level_long(row)
    parts = lbl.split('+')
    if not ok or not any(p in parts for p in ('VAL', 'AL', 'PDL', 'WL')): return None
    if len(parts) >= 3 and 'PDL' in parts and 'AL' in parts: return None
    if not m.rejection_long(row): return None
    poc = float(row.get('vp_poc') or 0)
    if poc > 0 and min(float(row['open']), float(row['close'])) > poc: return None
    if float(row.get('plus_ticks') or 0) <= float(row.get('minus_ticks') or 0): return None
    if row.get('fp_absorb_sell', False): return None
    return lbl


def _sizing(row, nt_q50, side):
    # vpin ELIMINADO del sizing 2026-06-19: era artefacto del lookahead (no discrimina causal).
    nt = float(row.get('n_trades') or 0); cvd = float(row.get('cvd_slope') or 0)
    obi = float(row.get('obi10_mean') or 0)
    tape = nt >= nt_q50; cvd_ok = cvd > 0
    if cvd_ok and tape: return 2.0
    if side == 'short':
        return 1.5 if (cvd_ok or tape or obi >= 0) else 1.0
    return 1.5 if (cvd_ok or obi > 0) else 1.0


# ── motor 'directions' (capital compartido, no overlap) ─────────────────────────

def run(df):
    is_df = df[df['ts_ms'] < m.OOS_MS]
    nt_q50 = float(is_df['n_trades'].quantile(0.50))

    h1 = m.resamp(df, m.H1_MS); h1['atr'] = m.atr14(h1['high'].values, h1['low'].values, h1['close'].values)
    h1hi = {int(r.ts_ms): (float(r.high), float(r.atr)) for r in h1.itertuples()}
    h1lo = {int(r.ts_ms): (float(r.low),  float(r.atr)) for r in h1.itertuples()}
    d1 = m.resamp(df, m.D1_MS); d1['ema20'] = m.ema(d1['close'].values, 20)
    d1c = {int(r.ts_ms): float(r.ema20) for r in d1.itertuples()}

    rows = df.to_dict('records')
    openp = []; closed = []
    cap = m.CAPITAL; monthly_risk = cap * m.RISK_PCT; cur_month = -1
    last_entry = {'short': -10**9, 'long': -10**9}
    peak = cap; maxdd = 0.0

    for i, row in enumerate(rows):
        ts = int(row['ts_ms'])

        # exits
        keep = []
        for p in openp:
            bars_el = i - p['t_start']; reason = ex = None
            if p['side'] == 'short':
                if   row['high'] >= p['sl']: reason, ex = 'stop', p['sl']
                elif row['low']  <= p['tp']: reason, ex = 'target', p['tp']
                elif bars_el >= FWD:         reason, ex = 'timeout', row['close']
                if reason: gross = (p['ep'] - ex) / p['dist']
            else:
                if   row['low']  <= p['sl']: reason, ex = 'stop', p['sl']
                elif row['high'] >= p['tp']: reason, ex = 'target', p['tp']
                elif bars_el >= FWD:         reason, ex = 'timeout', row['close']
                if reason: gross = (ex - p['ep']) / p['dist']
            if reason:
                r = gross - m.FEE_RT * p['ep'] / p['dist']
                pnl = p['risk'] * r; cap += pnl
                closed.append({'ts_ms': p['t_entry'],
                               'date': pd.Timestamp(p['t_entry'], unit='ms', tz='UTC').strftime('%Y-%m-%d %H:%M'),
                               'side': p['side'], 'trigger': p['trigger'], 'level': p['level'],
                               'session': p['session'],
                               'entry': round(p['ep'], 3), 'stop': round(p['sl'], 3),
                               'target': round(p['tp'], 3), 'exit_price': round(ex, 3),
                               'gross_r': round(gross, 3),
                               'result_r': round(r, 3), 'pnl_usd': round(pnl, 2),
                               'reason': reason, 'bars': bars_el, 'oos': p['t_entry'] >= m.OOS_MS,
                               'equity': round(cap, 2)})
                peak = max(peak, cap); maxdd = max(maxdd, (peak - cap) / peak)
            else:
                keep.append(p)
        openp = keep

        month = pd.Timestamp(ts, unit='ms', tz='UTC').month + pd.Timestamp(ts, unit='ms', tz='UTC').year * 12
        if month != cur_month: monthly_risk = cap * m.RISK_PCT; cur_month = month

        sess = m.session(ts)
        if sess == '': continue
        d1_ema = d1c.get((ts // m.D1_MS) * m.D1_MS - m.D1_MS)

        for side in ('short', 'long'):
            if side == 'long' and not LONGS_ENABLED: continue
            if side == 'long' and sess not in ('overlap', 'ny'): continue
            if i - last_entry[side] < COOLDOWN: continue
            if sum(1 for p in openp if p['side'] == side) >= MAX_OPEN_PER_SIDE: continue

            if side == 'short':
                trig = _elig_short(row, d1_ema)
                if trig is None: continue
                h1d = h1hi.get((ts // m.H1_MS) * m.H1_MS)
                if h1d is None: continue
                h1h, h1a = h1d; sl = h1h + 0.40 * h1a; dist = sl - row['close']
                level = trig
            else:
                trig = _elig_long(row, d1_ema)
                if trig is None: continue
                h1d = h1lo.get((ts // m.H1_MS) * m.H1_MS)
                if h1d is None: continue
                h1l, h1a = h1d; sl = h1l - 0.40 * h1a; dist = row['close'] - sl
                level = trig; trig = 'rejection_' + trig

            if dist <= 0: continue
            sp = dist / row['close']
            if not (m.MIN_STOP <= sp <= m.MAX_STOP): continue

            mult = _sizing(row, nt_q50, side)
            ep = row['close']
            tp = ep - TARGET_R * dist if side == 'short' else ep + TARGET_R * dist
            openp.append({'side': side, 'trigger': trig, 'level': level, 'session': sess,
                          'ep': ep, 'sl': sl, 'tp': tp, 'dist': dist, 'risk': monthly_risk * mult,
                          't_start': i, 't_entry': ts})
            last_entry[side] = i

    return closed, cap, maxdd


# ── reporting ───────────────────────────────────────────────────────────────────

def _stat(ts):
    if not ts: return (0, 0.0, 0.0, 0.0)
    n = len(ts); wr = sum(1 for t in ts if t['result_r'] > 0) / n * 100
    return (n, wr, sum(t['result_r'] for t in ts) / n, sum(t['result_r'] for t in ts))

def report(closed, label, days):
    is_t = [t for t in closed if not t['oos']]; oos_t = [t for t in closed if t['oos']]
    ni, wi, ai, ti = _stat(is_t); no, wo, ao, to = _stat(oos_t)
    print(f'  {label:<16} | IS n={ni:>4} WR={wi:>5.1f}% AvgR={ai:>+.3f} TotR={ti:>+6.1f} '
          f'| OOS n={no:>3} WR={wo:>5.1f}% AvgR={ao:>+.3f} TotR={to:>+5.1f} | {len(closed)/days:.2f} tpd')


def main():
    print('Cargando dataset perp M1...')
    df = pd.read_parquet(m.DATA_M1).sort_values('ts_ms').reset_index(drop=True)
    for c in df.select_dtypes('object').columns: df[c] = df[c].fillna('')
    for c in df.select_dtypes('float').columns:  df[c] = df[c].fillna(0.0)
    for c in df.select_dtypes('bool').columns:    df[c] = df[c].fillna(False)
    days = (df.ts_ms.max() - df.ts_ms.min()) / 86_400_000
    print(f'Barras: {len(df):,}  Días: {days:.0f}\n')

    closed, cap, maxdd = run(df)

    print('=' * 120)
    print('MTF SISTEMA — modelo directions (longs + shorts, ICT union, vpin sizing)')
    print('=' * 120)
    report(closed, 'COMBINADO', days)
    report([t for t in closed if t['side'] == 'short'], 'shorts', days)
    report([t for t in closed if t['side'] == 'long'],  'longs', days)
    print(f'\n  Capital $500 -> ${cap:,.0f}   MaxDD {maxdd*100:.1f}%')

    print('\n--- shorts: por disparador (OOS) ---')
    by = defaultdict(list)
    for t in closed:
        if t['side'] == 'short' and t['oos']: by[t['trigger']].append(t['result_r'])
    for k, rs in sorted(by.items(), key=lambda x: -len(x[1])):
        n = len(rs); w = sum(1 for r in rs if r > 0)
        print(f'    {k:<16} n={n:>3}  WR={w/n*100:>5.1f}%  AvgR={sum(rs)/n:>+.3f}')

    print('\n--- curva mensual (combinado) ---')
    bymo = defaultdict(lambda: {'n': 0, 'w': 0, 'pnl': 0.0})
    for t in closed:
        mo = t['date'][:7]; bymo[mo]['n'] += 1; bymo[mo]['pnl'] += t['pnl_usd']
        if t['result_r'] > 0: bymo[mo]['w'] += 1
    run_eq = m.CAPITAL
    for mo in sorted(bymo):
        d = bymo[mo]; run_eq += d['pnl']; tag = 'IS ' if mo < '2026-03' else 'OOS'
        print(f'    {mo} {tag}  n={d["n"]:>3}  WR={d["w"]/d["n"]*100:>5.1f}%  '
              f'PnL=${d["pnl"]:>+8,.0f}  Eq=${run_eq:>9,.0f}')

    EXPORTS.mkdir(exist_ok=True)
    pd.DataFrame(closed).to_csv(EXPORTS / 'mtf_system_trades.csv', index=False)
    print(f'\nCSV: exports/mtf_system_trades.csv')


if __name__ == '__main__':
    main()
