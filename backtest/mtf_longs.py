"""
mtf_longs.py — SISTEMA CANÓNICO DE LONGS v2
MTF Auction-Liquidity Orderflow — Bybit SPOT BTC

CAMBIO v2: Position sizing por score de calidad (no filtro por hora).
El score captura las CONDICIONES REALES del mercado:
  sell_vol, buy_vol, delta, vr — absorcion y flujo comprador.

DISCOVERY:
  - Score 4/4: 62.7% WR, AvgR +0.504 (condiciones optimas)
  - Score 1/4: 44.2% WR, AvgR +0.011 (setup debil — se size a 0.5%)
  - Las "horas malas" tienen WR bajo PORQUE tienen score bajo, no por la hora

REGLAS DE ENTRADA:
  Nivel: low dentro del 0.70% de VAL (requerido) + PDL/AL/WL
  Vela: wick inferior 30-85%, cierre alcista
  Flujo: OBI > +0.05 OR delta > 0
  Stop: H1_low - 0.40 * ATR_H1
  Target: Chop=1.5R, Expansion=3R, else=2R
  CVD exit: 3 velas CVD negativas consecutivas + OBI < -0.15 a >= 1R

FILTROS ESTRUCTURALES (sin hora):
  PDL+AL+VAL: BLOQUEADO (triple confluencia, WR 41.9%)
  bid_wall=True: BLOQUEADO (soporte falso — alguien muestra para atrapar)

POSITION SIZING (conservador, auditoria 2026-06-18):
  Score = suma de: sell_vol>=Q50, buy_vol>=Q50, delta>Q50, vr>Q50
  El score NO es monotono OOS — solo sc4 es robusto. El sizing no mejora el
  edge (AvgR/WR identicos), es solo apalancamiento. Unico boost defendible:
  Score 0-3: 1.00 * monthly_risk (2.0%, plano)
  Score 4/4: 1.50 * monthly_risk (3.0%, unico bucket robusto)

THRESHOLDS (calculados en IS Jun2025-Feb2026):
  sell_vol Q50 = 2.992
  buy_vol  Q50 = 4.055
  delta    Q50 = 0.806
  vr       Q50 = 0.940

RESULTADOS OOS (Mar-May 2026, 90 dias, IS thresholds):
  n=333  WR=52.6%  AvgR=+0.257  ~3.7 tpd  (edge identico en cualquier sizing)
  Capital con sc4=1.5x: $500 => ~$38,868  (plano 1x => ~$24,148; sc4=2x => ~$60,761)
"""
import pandas as pd
import numpy as np

CAPITAL   = 500.0
BASE_RISK = 0.02
FEE_RT    = 0.0020        # round-trip REAL Bybit spot basico (taker 0.1% x2). Antes 0.0007 (falso)
FORWARD   = 1200
MIN_STOP  = 0.003
MAX_STOP  = 0.0075
H1_MS     = 3_600_000
LEVEL_TOL = 0.007

OOS_MS = int(pd.Timestamp('2026-03-01', tz='UTC').value // 1_000_000)

# Thresholds IS (Jun2025-Feb2026)
SV_Q50  = 2.992   # sell_vol Q50
BV_Q50  = 4.055   # buy_vol  Q50
DLT_Q50 = 0.806   # delta    Q50
VR_Q50  = 0.940   # vr       Q50

# Sizing conservador (auditoria 2026-06-18): score NO monotono OOS (sc1 peor
# +0.032, sc4 mejor +0.425). El sizing no cambia el edge — solo la perilla de
# riesgo. Unico boost defendible: sc4 = 1.5x. Para flat puro usar [1,1,1,1,1].
BOOST_MULT = [1.00, 1.00, 1.00, 1.00, 1.50]  # score 0..4


def atr14(h, l, c):
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1))))
    tr[0] = h[0] - l[0]
    out = np.empty_like(tr)
    out[:14] = tr[:14].mean()
    k = 1 / 14
    for i in range(14, len(tr)):
        out[i] = out[i - 1] * (1 - k) + tr[i] * k
    return out


def resample_h1(df):
    df = df.copy()
    df['tf'] = (df['ts_ms'] // H1_MS) * H1_MS
    return (
        df.groupby('tf', sort=True)
        .agg(open=('open', 'first'), high=('high', 'max'),
             low=('low', 'min'), close=('close', 'last'))
        .reset_index()
        .rename(columns={'tf': 'ts_ms'})
    )


def session(ts_ms):
    hm = (ts_ms // 60_000) % 1440
    if  7 * 60 <= hm < 12 * 60: return 'london'
    if 12 * 60 <= hm < 16 * 60: return 'overlap'
    if 16 * 60 <= hm < 20 * 60: return 'ny'
    return ''


def quality_score(row):
    sv  = float(row.get('sell_vol') or 0)
    bv  = float(row.get('buy_vol')  or 0)
    dlt = float(row.get('delta')    or 0)
    vr  = float(row.get('vr')       or 0)
    return sum([sv >= SV_Q50, bv >= BV_Q50, dlt > DLT_Q50, vr > VR_Q50])


def run(df):
    h1 = resample_h1(df)
    h1['atr'] = atr14(h1['high'].values, h1['low'].values, h1['close'].values)
    h1_ctx = {int(r.ts_ms): (float(r.low), float(r.atr)) for r in h1.itertuples()}

    rows = df.to_dict('records')
    trades = []
    in_t = False
    ep = sl = tp = dist = 0.0
    t_start = t_entry = 0
    t_lbl = t_sess = ''
    cvd_streak = 0
    entry_risk = 0.0
    entry_score = 0
    mfe_r = mae_r = 0.0

    cap = CAPITAL
    monthly_risk = CAPITAL * BASE_RISK
    current_month = -1

    for i, row in enumerate(rows):
        ts = int(row['ts_ms'])

        if in_t:
            obi = float(row.get('obi10_mean') or 0)
            cs  = float(row.get('cvd_slope') or 0)
            cvd_streak = (cvd_streak + 1) if cs < 0 else 0
            cur_r = (row['close'] - ep) / dist
            mfe_r = max(mfe_r, (row['high'] - ep) / dist)
            mae_r = max(mae_r, (ep - row['low'])  / dist)
            reason = None; exit_px = 0.0

            if   row['low'] <= sl:                                  reason, exit_px = 'stop',     sl
            elif row['high'] >= tp:                                  reason, exit_px = 'target',   tp
            elif i - t_start >= FORWARD:                            reason, exit_px = 'timeout',  row['close']
            elif cvd_streak >= 3 and obi < -0.15 and cur_r >= 1.0: reason, exit_px = 'cvd_exit', row['close']

            if reason:
                gross_r = (exit_px - ep) / dist
                fee_r   = FEE_RT * ep / dist        # fee sobre NOTIONAL, no sobre riesgo
                pnl_r   = gross_r - fee_r           # result_r ya es NETO
                pnl_usd = entry_risk * pnl_r
                trades.append({
                    'ts_ms':    t_entry,
                    'result_r': round(pnl_r, 3),
                    'oos':      ts >= OOS_MS,
                    'level':    t_lbl,
                    'sess':     t_sess,
                    'reason':   reason,
                    'score':    entry_score,
                    'risk_mult': entry_risk / monthly_risk if monthly_risk > 0 else 1.0,
                    'mfe_r':    round(mfe_r, 3),
                    'mae_r':    round(mae_r, 3),
                })
                cap     += pnl_usd
                in_t     = False
                cvd_streak = 0
                mfe_r = mae_r = 0.0
            continue

        # ── Monthly rebalance ────────────────────────────────────────
        month = (pd.Timestamp(ts, unit='ms', tz='UTC').month
                 + pd.Timestamp(ts, unit='ms', tz='UTC').year * 12)
        if month != current_month:
            monthly_risk  = cap * BASE_RISK
            current_month = month

        # ── Session filter ───────────────────────────────────────────
        sess = session(ts)
        if not sess:
            continue

        # ── Level detection ──────────────────────────────────────────
        low = row['low']
        levels = []
        for key, col in [('PDL', 'prev_day_low'), ('AL', 'asian_low'),
                         ('WL', 'weekly_low'),    ('VAL', 'vp_val')]:
            v = row.get(col)
            if v and v > 0 and abs(low - v) / v <= LEVEL_TOL:
                levels.append(key)

        if not levels:
            continue
        lbl   = '+'.join(levels)
        parts = lbl.split('+')

        if 'VAL' not in lbl:
            continue

        # ── Structural block: PDL+AL+VAL ─────────────────────────────
        if len(parts) >= 3 and 'PDL' in parts and 'AL' in parts:
            continue

        # ── Bid wall block: fake support ─────────────────────────────
        if bool(row.get('bid_wall')):
            continue

        # ── Candle filter ─────────────────────────────────────────────
        c, o, h, l = float(row['close']), float(row['open']), float(row['high']), float(row['low'])
        rng = h - l
        if rng <= 0:
            continue
        wick_dn = (min(c, o) - l) / rng
        if not (0.30 < wick_dn < 0.85) or c < o:
            continue

        # ── Flow filter ───────────────────────────────────────────────
        obi   = float(row.get('obi10_mean') or 0)
        delta = float(row.get('delta') or 0)
        if obi <= 0.05 and delta <= 0:
            continue

        # ── H1 stop distance ─────────────────────────────────────────
        h1_ts = (ts // H1_MS) * H1_MS
        h1d = h1_ctx.get(h1_ts)
        if h1d is None:
            continue
        h1l, h1a = h1d
        sl_ = h1l - 0.40 * h1a
        dist_ = row['close'] - sl_
        if dist_ <= 0:
            continue
        if not (MIN_STOP <= dist_ / row['close'] <= MAX_STOP):
            continue

        # ── Dynamic target ────────────────────────────────────────────
        reg = str(row.get('regime') or '')
        tgt = 1.5 if reg == 'Chop' else (3.0 if reg == 'Expansion' else 2.0)

        # ── Quality score & position size ────────────────────────────
        sc   = quality_score(row)
        risk = monthly_risk * BOOST_MULT[sc]

        # ── Open trade ────────────────────────────────────────────────
        entry_score = sc
        in_t     = True
        ep       = row['close']
        sl       = sl_
        dist     = dist_
        tp       = ep + tgt * dist
        entry_risk = risk
        t_start  = i
        t_entry  = ts
        t_lbl    = lbl
        t_sess   = sess
        cvd_streak = 0

    return trades, cap


if __name__ == '__main__':
    from collections import defaultdict

    print('Cargando M1...')
    df = pd.read_parquet('data/bybit-spot/processed/btcusdt_m1.parquet').sort_values('ts_ms').reset_index(drop=True)
    for c in df.columns:
        if df[c].dtype == object:  df[c] = df[c].fillna('')
        elif df[c].dtype == float: df[c] = df[c].fillna(0.0)

    trades, final_cap = run(df)

    import pathlib
    pathlib.Path('exports').mkdir(exist_ok=True)
    pd.DataFrame(trades).to_csv('exports/longs_trades.csv', index=False)

    ist = [t for t in trades if not t['oos']]
    oot = [t for t in trades if t['oos']]

    def stats(ts):
        if not ts: return 0, 0.0, 0.0
        n = len(ts); w = sum(1 for t in ts if t['result_r'] > 0)
        return n, w / n * 100, sum(t['result_r'] for t in ts) / n

    ni, wi, ai = stats(ist)
    no, wo, ao = stats(oot)

    print(f'\nLONGS v2 (score sizing) — Capital final: ${final_cap:,.0f}')
    print(f'  IS  n={ni}  WR={wi:.1f}%  AvgR={ai:+.3f}  tpd={ni/260:.1f}')
    print(f'  OOS n={no}  WR={wo:.1f}%  AvgR={ao:+.3f}  tpd={no/90:.1f}')

    print('\n-- Por nivel OOS --')
    by_lbl = defaultdict(list)
    for t in oot: by_lbl[t['level']].append(t['result_r'])
    for lbl in sorted(by_lbl, key=lambda x: -len(by_lbl[x])):
        rs = by_lbl[lbl]; n = len(rs); w = sum(1 for r in rs if r > 0)
        flag = ' **' if w / n >= 0.60 else (' <<' if w / n < 0.44 else '')
        print(f'  {lbl:<25}  n={n:>4}  WR={w/n*100:>5.1f}%  AvgR={sum(rs)/n:>+.3f}{flag}')

    print('\n-- Por score OOS --')
    by_sc = defaultdict(list)
    for t in oot: by_sc[t.get('score',0)].append(t['result_r'])
    for sc in range(5):
        rs = by_sc.get(sc, [])
        if not rs: continue
        n = len(rs); w = sum(1 for r in rs if r > 0); avg = sum(rs) / n
        mult = BOOST_MULT[sc]
        flag = ' ***' if w/n >= 0.60 else (' <<' if w/n < 0.44 else '')
        print(f'  Score {sc}/4  n={n:>4}  WR={w/n*100:>5.1f}%  AvgR={avg:>+.3f}  size={mult:.0%}{flag}')

    print('\n-- Por sesion OOS --')
    by_sess = defaultdict(list)
    for t in oot: by_sess[t['sess']].append(t['result_r'])
    for sess in ['london', 'overlap', 'ny']:
        rs = by_sess.get(sess, [])
        if not rs: continue
        n = len(rs); w = sum(1 for r in rs if r > 0)
        print(f'  {sess:<10}  n={n:>3}  WR={w/n*100:>5.1f}%  AvgR={sum(rs)/n:>+.3f}')
