"""
mtf_v2_backtest.py
------------------
MTF Auction-Liquidity Orderflow v2 — Bybit SPOT BTC
Jerarquia estricta (nunca al reves):

  D1/H4 macro  →  Regime  →  Session
  →  M15 Zone  →  M5 Setup  →  M1 Trigger  →  H1 Stop

No dispara sin zona M15.
No dispara sin setup M5.
M1 solo reduce timing, no es la senal madre.

Walk-forward:
  In-sample:       2025-06-15 -> 2026-02-28
  Out-of-sample:   2026-03-01 -> 2026-05-31

Uso:
  python backtest/mtf_v2_backtest.py
  python backtest/mtf_v2_backtest.py --top 20
  python backtest/mtf_v2_backtest.py --no-trigger   # solo zona+setup, sin filtro M1
  python backtest/mtf_v2_backtest.py --oos-only
"""
import argparse, json
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd

ROOT    = Path(__file__).parent.parent
DATA_M1  = ROOT / 'data/bybit-spot/processed/btcusdt_m1.parquet'
DATA_M5  = ROOT / 'data/bybit-spot/processed/btcusdt_m5.parquet'
DATA_M15 = ROOT / 'data/bybit-spot/processed/btcusdt_m15.parquet'
EXPORTS  = ROOT / 'exports'

# Parametros de riesgo (iguales al spec)
CAPITAL   = 500.0
RISK_PCT  = 0.02
RISK_USD  = CAPITAL * RISK_PCT   # $10 fijo
FEE_RT    = 0.0007
TARGET_R  = 2.5
COOLDOWN  = 30
FORWARD   = 1200   # barras M1 (~20h)
MIN_STOP  = 0.0030
MAX_STOP  = 0.0075

M5_MS  =  5 * 60_000
M15_MS = 15 * 60_000
H1_MS  = 60 * 60_000
D1_MS  = 86_400_000

OOS_START = pd.Timestamp('2026-03-01', tz='UTC')
OOS_START_MS = int(OOS_START.value // 1_000_000)


# ── Helpers numericos ─────────────────────────────────────────────────────────

def ema(vals: np.ndarray, n: int) -> np.ndarray:
    k = 2 / (n + 1)
    out = np.empty(len(vals))
    out[0] = vals[0]
    for i in range(1, len(vals)):
        out[i] = vals[i] * k + out[i-1] * (1-k)
    return out


def atr14(h: np.ndarray, l: np.ndarray, c: np.ndarray) -> np.ndarray:
    tr = np.maximum(h - l,
         np.maximum(np.abs(h - np.roll(c, 1)),
                    np.abs(l - np.roll(c, 1))))
    tr[0] = h[0] - l[0]
    out = np.empty_like(tr)
    out[:14] = tr[:14].mean()
    k = 1 / 14
    for i in range(14, len(tr)):
        out[i] = out[i-1] * (1-k) + tr[i] * k
    return out


def resample_ohlc(df: pd.DataFrame, freq_ms: int) -> pd.DataFrame:
    df = df.copy()
    df['tf'] = (df['ts_ms'] // freq_ms) * freq_ms
    agg = df.groupby('tf', sort=True).agg(
        open=('open', 'first'), high=('high', 'max'),
        low=('low', 'min'),    close=('close', 'last'),
    ).reset_index().rename(columns={'tf': 'ts_ms'})
    return agg


# ── Sesion UTC ────────────────────────────────────────────────────────────────

def get_session(ts_ms: int) -> str:
    hm = (ts_ms // 60_000) % 1440
    if   7*60 <= hm < 12*60: return 'london'
    if  12*60 <= hm < 16*60: return 'overlap'
    if  16*60 <= hm < 20*60: return 'ny'
    return ''


# ── Zona M15 (Short) ──────────────────────────────────────────────────────────
# Cada feature puntua +1. Necesitamos zone_score >= 1 para operar.
# Se usa el bar M15 COMPLETADO anterior (sin lookahead).

ZONE_MAP = [
    ('near_weekly_high', 'wkly_h'),
    ('near_asian_high',  'asian_h'),
    ('near_pdh',         'pdh'),
    ('near_bearish_fvg', 'fvg'),
    ('near_bearish_ob',  'ob'),
    ('equal_high',       'eq_h'),
    ('fib_ote',          'ote'),
    ('above_poc',        'poc'),
    ('val_near',         'val'),     # VAL actua como resistencia en short
]

def zone_of(row: dict) -> tuple[int, str]:
    active = [lbl for feat, lbl in ZONE_MAP if row.get(feat, False)]
    return len(active), '+'.join(active)


# ── Setup M5 (Short) ──────────────────────────────────────────────────────────
# Se usa el bar M5 COMPLETADO anterior (sin lookahead).

def setup_of(row: dict) -> str:
    parts = []
    stk = str(row.get('stacked_imb', '') or '')
    obi = float(row.get('obi10_mean', 0) or 0)

    # Sweep de liquidez (mas fuerte)
    if row.get('pdh_sweep') or row.get('equal_high_sweep') or row.get('sweep_confirmed'):
        parts.append('sweep')

    # Absorption ask + estructura bajista
    if row.get('abs_ask') and stk == 'Bearish':
        parts.append('ask+stk')
    elif row.get('abs_ask') and obi < -0.08:
        parts.append('ask+obi')

    # Big trade comprador atrapado con CVD divergente
    if row.get('big_trade_bearish') and row.get('cvd_div'):
        parts.append('bt+div')
    elif row.get('big_trade_bearish') and stk == 'Bearish':
        parts.append('bt+stk')

    # Desplazamiento bajista (M5)
    if row.get('displacement_bear'):
        parts.append('displ')

    # OTE rejection (retroceso Fibonacci con rechazo)
    if row.get('ote_rejection'):
        parts.append('ote_rej')

    # CVD divergencia + stacked
    if row.get('cvd_div') and stk == 'Bearish':
        parts.append('cvd+stk')

    return '+'.join(parts)


# ── Trigger M1 ────────────────────────────────────────────────────────────────
# M1 solo ajusta timing dentro del setup M5 ya confirmado.

def trigger_of(row: dict) -> str:
    rng = row['high'] - row['low']
    if rng > 0:
        wick_up = row['high'] - max(row['close'], row['open'])
        body    = abs(row['close'] - row['open'])
        is_ss   = (wick_up / rng >= 0.45) and (body / rng <= 0.35)
    else:
        is_ss = False

    obi     = float(row.get('obi10_mean', 0) or 0)
    abs_ask = bool(row.get('abs_ask', False))
    bt_bear = bool(row.get('big_trade_bearish', False))
    cvd_neg = float(row.get('cvd_slope', 0) or 0) < 0
    dz_neg  = float(row.get('dz', 0) or 0) < -0.5

    # Orden de prioridad: mas confirmaciones primero
    if is_ss and abs_ask and obi < -0.05:
        return 'ss+ask+obi'
    if bt_bear and abs_ask and obi < -0.05:
        return 'bt+ask+obi'
    if is_ss and obi < -0.05:
        return 'ss+obi'
    if abs_ask and obi < -0.08:
        return 'ask+obi'
    if bt_bear and cvd_neg and obi < 0:
        return 'bt+cvd+obi'
    if dz_neg and obi < -0.05:
        return 'dz+obi'
    if is_ss and cvd_neg:
        return 'ss+cvd'
    if abs_ask and cvd_neg:
        return 'ask+cvd'
    if bt_bear and cvd_neg:
        return 'bt+cvd'
    return ''


# ── Simulacion principal ───────────────────────────────────────────────────────

def simulate(df_m1: pd.DataFrame, df_m5: pd.DataFrame, df_m15: pd.DataFrame,
             require_trigger: bool = True,
             require_h4_bos:  bool = False,
             block_above_poc: bool = True,
             block_bid_wall:  bool = True,
             block_stk_bull:  bool = True) -> list[dict]:

    # H1 context: high y ATR para el stop estructural
    h1 = resample_ohlc(df_m1, H1_MS)
    h1['atr'] = atr14(h1['high'].values, h1['low'].values, h1['close'].values)
    h1_ctx = {int(r.ts_ms): (float(r.high), float(r.atr)) for r in h1.itertuples()}

    # D1 EMA20 para filtro macro
    d1 = resample_ohlc(df_m1, D1_MS)
    d1['ema20'] = ema(d1['close'].values, 20)
    d1_ctx = {int(r.ts_ms): float(r.ema20) for r in d1.itertuples()}

    # Dicts de lookup por timestamp — O(1) por barra
    m5_ctx  = {int(r.ts_ms): r._asdict() for r in df_m5.itertuples()}
    m15_ctx = {int(r.ts_ms): r._asdict() for r in df_m15.itertuples()}

    rows   = df_m1.to_dict('records')
    trades : list[dict] = []
    last_i = -999
    in_t   = False

    ep = sl = tp = dist = 0.0
    t_start = t_entry = 0
    t_zone = t_setup = t_trig = t_sess = ''
    t_h4_bear = False
    mfe = mae = 0.0
    cap = CAPITAL
    cvd_pos_streak = 0

    for i, row in enumerate(rows):
        ts = int(row['ts_ms'])

        # ── Gestion del trade abierto ──────────────────────────────────────
        if in_t:
            lo, hi = row['low'], row['high']
            mfe = max(mfe, (ep - lo) / dist)    # short: favorable = precio baja
            mae = max(mae, (hi - ep) / dist)    # short: adverso  = precio sube

            bars_el = i - t_start
            obi_now = float(row.get('obi10_mean', 0) or 0)
            cvd_slope_now = float(row.get('cvd_slope', 0) or 0)

            # CVD exhaustion counter: n barras consecutivas con flujo comprador
            cvd_pos_streak = (cvd_pos_streak + 1) if cvd_slope_now > 0 else 0
            cur_r = (ep - row['close']) / dist

            reason = None; exit_px = 0.0
            if hi >= sl:
                reason, exit_px = 'stop', sl
            elif lo <= tp:
                reason, exit_px = 'target', tp
            elif bars_el >= FORWARD:
                reason, exit_px = 'timeout', row['close']
            elif cvd_pos_streak >= 5 and obi_now > 0.15 and cur_r >= 1.0:
                reason, exit_px = 'cvd_exit', row['close']

            if reason:
                pnl_r   = (ep - exit_px) / dist
                pnl_usd = RISK_USD * pnl_r - cap * RISK_PCT * FEE_RT
                oos     = ts >= OOS_START_MS
                trades.append({
                    'ts_ms':    t_entry,
                    'date':     pd.Timestamp(t_entry, unit='ms', tz='UTC').strftime('%Y-%m-%d %H:%M'),
                    'session':  t_sess,
                    'h4_bear':  t_h4_bear,
                    'zone':     t_zone,
                    'setup':    t_setup,
                    'trigger':  t_trig,
                    'combo':    f'{t_zone}|{t_setup}|{t_trig}',
                    'entry':    round(ep, 2),
                    'stop':     round(sl, 2),
                    'target':   round(tp, 2),
                    'exit':     round(exit_px, 2),
                    'stop_pct': round(dist/ep*100, 3),
                    'reason':   reason,
                    'result_r': round(pnl_r, 3),
                    'pnl_usd':  round(pnl_usd, 2),
                    'bars':     bars_el,
                    'mfe_r':    round(mfe, 3),
                    'mae_r':    round(mae, 3),
                    'equity':   round(cap + pnl_usd, 2),
                    'oos':      oos,
                })
                cap += pnl_usd
                in_t = False
                last_i = i
                cvd_pos_streak = 0
            continue

        # ── Cooldown ───────────────────────────────────────────────────────
        if i - last_i < COOLDOWN:
            continue

        # ── Filtro sesion ──────────────────────────────────────────────────
        sess = get_session(ts)
        if not sess:
            continue

        # ── Filtro D1 macro: bloquear bull claro ──────────────────────────
        d1_ts = (ts // D1_MS) * D1_MS - D1_MS
        d1_ema20 = d1_ctx.get(d1_ts)
        if d1_ema20 is None:
            continue
        if row['close'] > d1_ema20 * 1.005:   # strong bull → no shorts
            continue

        # ── Filtro regime: no TrendDown (perseguir caida = sin edge) ──────
        regime = str(row.get('regime', '') or '')
        if regime == 'TrendDown':
            continue

        # ── Zona M15 (bar completado anterior sin lookahead) ──────────────
        m15_ts = (ts // M15_MS) * M15_MS - M15_MS
        m15_row = m15_ctx.get(m15_ts)
        if m15_row is None:
            continue

        z_score, z_lbl = zone_of(m15_row)
        if z_score < 1:
            continue

        # ── Filtros de calidad M15 ─────────────────────────────────────────
        # Precio encima del POC → no estamos en zona premium/resistencia real
        if block_above_poc and m15_row.get('above_poc', False):
            continue
        # Bid wall debajo → soporte activo, la caida tiene freno inmediato
        if block_bid_wall and m15_row.get('bid_wall', False):
            continue

        h4_bear = bool(m15_row.get('h4_bearish', False))

        # ── Setup M5 (bar completado anterior sin lookahead) ──────────────
        m5_ts = (ts // M5_MS) * M5_MS - M5_MS
        m5_row = m5_ctx.get(m5_ts)
        if m5_row is None:
            continue

        # ── Filtros de calidad M5 ──────────────────────────────────────────
        # Stacked bullish → momentum comprador activo en M5, no shortear
        if block_stk_bull and str(m5_row.get('stacked_imb', '') or '') == 'Bullish':
            continue
        # H4 Break of Structure bajista requerido (el filtro mas potente)
        if require_h4_bos and not m5_row.get('h4_bos_bear', False):
            continue

        s_lbl = setup_of(m5_row)
        if not s_lbl:
            continue

        # ── Trigger M1 ────────────────────────────────────────────────────
        if require_trigger:
            t_lbl = trigger_of(row)
            if not t_lbl:
                continue
        else:
            t_lbl = 'no_filter'

        # ── Stop estructural H1 ───────────────────────────────────────────
        h1_ts = (ts // H1_MS) * H1_MS
        h1d   = h1_ctx.get(h1_ts)
        if h1d is None:
            continue
        h1_high, h1_atr = h1d
        sl_  = h1_high + 0.30 * h1_atr
        d    = sl_ - row['close']
        if d <= 0:
            continue
        stop_pct = d / row['close']
        if not (MIN_STOP <= stop_pct <= MAX_STOP):
            continue

        # ── Abrir trade ───────────────────────────────────────────────────
        in_t   = True
        ep     = row['close']
        sl     = sl_
        dist   = d
        tp     = ep - TARGET_R * dist
        t_start  = i
        t_entry  = ts
        t_zone   = z_lbl
        t_setup  = s_lbl
        t_trig   = t_lbl
        t_sess   = sess
        t_h4_bear = h4_bear
        mfe = mae = 0.0
        cvd_pos_streak = 0

    # Cierre forzado EOD si quedo abierto
    if in_t and rows:
        r = rows[-1]
        exit_px = r['close']
        pnl_r   = (ep - exit_px) / dist
        pnl_usd = RISK_USD * pnl_r - cap * RISK_PCT * FEE_RT
        trades.append({
            'ts_ms': t_entry,
            'date':  pd.Timestamp(t_entry, unit='ms', tz='UTC').strftime('%Y-%m-%d %H:%M'),
            'session': t_sess, 'h4_bear': t_h4_bear,
            'zone': t_zone, 'setup': t_setup, 'trigger': t_trig,
            'combo': f'{t_zone}|{t_setup}|{t_trig}',
            'entry': round(ep,2), 'stop': round(sl,2), 'target': round(tp,2),
            'exit': round(exit_px,2), 'stop_pct': round(dist/ep*100,3),
            'reason': 'eod', 'result_r': round(pnl_r,3), 'pnl_usd': round(pnl_usd,2),
            'bars': len(rows)-1-t_start, 'mfe_r': round(mfe,3), 'mae_r': round(mae,3),
            'equity': round(cap+pnl_usd,2), 'oos': int(r['ts_ms']) >= OOS_START_MS,
        })

    return trades


# ── Estadisticas ──────────────────────────────────────────────────────────────

def group_stats(trades: list[dict], key_fn, min_n: int = 1) -> list[dict]:
    g: dict[str, list] = defaultdict(list)
    for t in trades:
        g[key_fn(t)].append(t['result_r'])
    rows = []
    for k, rs in g.items():
        n    = len(rs)
        if n < min_n:
            continue
        wins = sum(1 for r in rs if r > 0)
        rows.append({
            'key':     k,
            'n':       n,
            'wr':      round(wins/n*100, 1),
            'avg_r':   round(sum(rs)/n, 3),
            'total_r': round(sum(rs), 2),
            'mfe':     None,
            'mae':     None,
        })
    return sorted(rows, key=lambda x: -x['total_r'])


def summary_block(trades: list[dict], label: str):
    if not trades:
        print(f'\n{label}: 0 trades')
        return
    n      = len(trades)
    wins   = sum(1 for t in trades if t['result_r'] > 0)
    total_r = sum(t['result_r'] for t in trades)
    avg_mfe = sum(t['mfe_r'] for t in trades) / n
    avg_mae = sum(t['mae_r'] for t in trades) / n
    stops   = sum(1 for t in trades if t['reason'] == 'stop')
    tgts    = sum(1 for t in trades if t['reason'] == 'target')
    cvds    = sum(1 for t in trades if t['reason'] == 'cvd_exit')
    touts   = sum(1 for t in trades if t['reason'] == 'timeout')
    dates   = set(t['date'][:10] for t in trades)
    days    = len(dates)
    tpd     = round(n / max(days, 1), 1)
    print(f'\n{label}')
    print(f'  n={n}  WR={wins/n*100:.1f}%  AvgR={total_r/n:.3f}  TotalR={total_r:.1f}R')
    print(f'  Trades/dia={tpd}  dias={days}')
    print(f'  Salidas: target={tgts}  stop={stops}  cvd_exit={cvds}  timeout={touts}')
    print(f'  MFE_avg={avg_mfe:.2f}R  MAE_avg={avg_mae:.2f}R')


def print_table(rows: list[dict], key_col: str, top: int = 15, min_n: int = 3):
    filtered = [r for r in rows if r['n'] >= min_n][:top]
    if not filtered:
        return
    kw = max(len(r['key']) for r in filtered)
    print(f"  {'Clave':<{kw}}  {'n':>5}  {'WR':>6}  {'AvgR':>7}  {'TotalR':>8}")
    print(f"  {'-'*kw}  -----  ------  -------  --------")
    for r in filtered:
        print(f"  {r['key']:<{kw}}  {r['n']:>5}  {r['wr']:>5.1f}%  {r['avg_r']:>7.3f}  {r['total_r']:>8.2f}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--top',          type=int, default=15)
    ap.add_argument('--min-n',        type=int, default=5)
    ap.add_argument('--no-trigger',   action='store_true', help='sin filtro M1')
    ap.add_argument('--h4-bos',       action='store_true', help='requerir H4 BoS bajista')
    ap.add_argument('--no-poc-block', action='store_true', help='no bloquear above_poc')
    ap.add_argument('--no-wall-block',action='store_true', help='no bloquear bid_wall')
    ap.add_argument('--no-stk-block', action='store_true', help='no bloquear stk_bull_m5')
    ap.add_argument('--oos-only',     action='store_true')
    ap.add_argument('--save-csv',     action='store_true')
    args = ap.parse_args()

    print('Cargando parquets...')
    df_m1  = pd.read_parquet(DATA_M1).sort_values('ts_ms').reset_index(drop=True)
    df_m5  = pd.read_parquet(DATA_M5).sort_values('ts_ms').reset_index(drop=True)
    df_m15 = pd.read_parquet(DATA_M15).sort_values('ts_ms').reset_index(drop=True)

    # Normalizar NaN en columnas relevantes
    for df in [df_m1, df_m5, df_m15]:
        for c in df.columns:
            if df[c].dtype == object:
                df[c] = df[c].fillna('')
            elif c in ('stacked_imb',):
                pass
            elif df[c].dtype == float:
                df[c] = df[c].fillna(0.0)
            elif str(df[c].dtype) == 'bool':
                df[c] = df[c].fillna(False)

    print(f'M1: {len(df_m1):,} barras | M5: {len(df_m5):,} | M15: {len(df_m15):,}')

    trades = simulate(
        df_m1, df_m5, df_m15,
        require_trigger = not args.no_trigger,
        require_h4_bos  = args.h4_bos,
        block_above_poc = not args.no_poc_block,
        block_bid_wall  = not args.no_wall_block,
        block_stk_bull  = not args.no_stk_block,
    )
    closed = [t for t in trades if t['reason'] != 'eod']

    is_t   = [t for t in closed if not t['oos']]
    oos_t  = [t for t in closed if t['oos']]

    show = oos_t if args.oos_only else closed

    # Bloques de resumen
    summary_block(is_t,  'IN-SAMPLE    (Jun 2025 -> Feb 2026)')
    summary_block(oos_t, 'OUT-OF-SAMPLE (Mar 2026 -> May 2026)')

    if not args.oos_only:
        subset = closed
    else:
        subset = oos_t

    if not subset:
        print('\nSin trades para mostrar.')
        return

    print('\n' + '='*70)
    print(f'ANALISIS POR DIMENSION (n>={args.min_n})')
    print('='*70)

    print('\n-- Zona M15 -------------------------------------------------------')
    print_table(group_stats(subset, lambda t: t['zone'], args.min_n), 'zone', args.top)

    print('\n-- Setup M5 -------------------------------------------------------')
    print_table(group_stats(subset, lambda t: t['setup'], args.min_n), 'setup', args.top)

    print('\n-- Trigger M1 -----------------------------------------------------')
    print_table(group_stats(subset, lambda t: t['trigger'], args.min_n), 'trigger', args.top)

    print('\n-- Sesion ---------------------------------------------------------')
    print_table(group_stats(subset, lambda t: t['session'], 1), 'session', args.top)

    print('\n-- H4 contexto ----------------------------------------------------')
    print_table(group_stats(subset, lambda t: str(t['h4_bear']), 1), 'h4_bear', args.top)

    print('\n-- Top combos (zona|setup|trigger) --------------------------------')
    print_table(group_stats(subset, lambda t: t['combo'], args.min_n), 'combo', args.top, args.min_n)

    # Salida de razon de cierre
    print('\n-- Por razon de cierre --------------------------------------------')
    print_table(group_stats(subset, lambda t: t['reason'], 1), 'reason', 10)

    # Capital final
    cap_final = CAPITAL + sum(t['pnl_usd'] for t in closed)
    print(f'\nCapital final: ${CAPITAL:.0f} -> ${cap_final:.0f}  ({(cap_final/CAPITAL-1)*100:.1f}%)')

    # Guardar
    EXPORTS.mkdir(exist_ok=True)
    if args.save_csv:
        df_out = pd.DataFrame(trades)
        p = EXPORTS / 'mtf_v2_trades.csv'
        df_out.to_csv(p, index=False)
        print(f'CSV guardado: {p}')

    stats = {
        'by_zone':    group_stats(closed, lambda t: t['zone']),
        'by_setup':   group_stats(closed, lambda t: t['setup']),
        'by_trigger': group_stats(closed, lambda t: t['trigger']),
        'by_combo':   group_stats(closed, lambda t: t['combo'], args.min_n),
        'by_session': group_stats(closed, lambda t: t['session']),
        'is':  {'n': len(is_t),  'wr': round(sum(1 for t in is_t  if t['result_r']>0)/max(len(is_t),1)*100,1),
                'avg_r': round(sum(t['result_r'] for t in is_t)/max(len(is_t),1),3)},
        'oos': {'n': len(oos_t), 'wr': round(sum(1 for t in oos_t if t['result_r']>0)/max(len(oos_t),1)*100,1),
                'avg_r': round(sum(t['result_r'] for t in oos_t)/max(len(oos_t),1),3)},
    }
    p_json = EXPORTS / 'mtf_v2_stats.json'
    with open(p_json, 'w') as f:
        json.dump(stats, f, indent=2)
    print(f'Stats JSON: {p_json}')


if __name__ == '__main__':
    main()
