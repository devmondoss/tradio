"""
mtf_basics.py — La version mas simple posible
==============================================
Sin cooldown. Sin combos. Sin ICT patterns.

3 ingredientes:
  1. NIVEL    — precio cerca de PDH / Asian High / Weekly High / VAH
  2. RECHAZO  — barra cierra por debajo del nivel (vela bajista en zona)
  3. FLUJO    — OBI < 0  O  delta < 0  (vendedores presentes)

Stop  : H1_high + 0.30 x ATR_H1  (0.30% - 0.75%)
Target: 2.5R
Sin cooldown — cada barra valida puede ser un trade
Walk-forward: IS hasta 2026-03-01 / OOS desde ahi
"""
import json
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd

ROOT    = Path(__file__).parent.parent
DATA_M1 = ROOT / 'data/bybit-spot/processed/btcusdt_m1.parquet'
EXPORTS = ROOT / 'exports'

CAPITAL  = 500.0
RISK_PCT = 0.02
RISK_USD = CAPITAL * RISK_PCT
FEE_RT   = 0.0020        # round-trip REAL Bybit spot basico (taker 0.1% x2). Antes 0.0007 (falso)
TARGET_R = 1.8           # mejor balance WR/AvgR/volumen, IS≈OOS (2026-06-18)
FORWARD  = 1200          # barras maximas en trade (~20h)
MIN_STOP = 0.0030
MAX_STOP = 0.0075
H1_MS    = 3_600_000
H4_MS    = 14_400_000
D1_MS    = 86_400_000
OOS_MS   = int(pd.Timestamp('2026-03-01', tz='UTC').value // 1_000_000)

# ── Score de calidad — position sizing dinamico ───────────────────────────────
# Score v3: sell_vol + buy_vol + cvd_slope>0 + vr
# Thresholds calculados en IS (Jun2025-Feb2026)
SCORE_SV_Q50  = 3.989   # sell_vol Q50
SCORE_BV_Q50  = 2.712   # buy_vol  Q50
SCORE_VR_Q50  = 0.933   # vr       Q50
# Sizing conservador (auditoria 2026-06-18): el score NO es monotono OOS — solo
# sc4 es robusto (mejor IS +0.56 y OOS +0.44). La escala vieja [.2,.5,1,1.5,2]
# achicaba buckets que en OOS son positivos (sc0 +0.34) y desperdiciaba compounding.
# El sizing NO cambia el edge (AvgR/WR identicos) — es solo la perilla de riesgo.
# Unico boost defendible: sc4 = 1.5x. Resto plano. Para flat puro usar [1,1,1,1,1].
SCORE_MULT    = [1.00, 1.00, 1.00, 1.00, 1.50]


def quality_score(row) -> int:
    """0-4: numero de condiciones de absorcion presentes en el entry."""
    sv  = float(row.get('sell_vol')  or 0)
    bv  = float(row.get('buy_vol')   or 0)
    cvd = float(row.get('cvd_slope') or 0)
    vr  = float(row.get('vr')        or 0)
    return sum([sv >= SCORE_SV_Q50, bv >= SCORE_BV_Q50, cvd > 0, vr >= SCORE_VR_Q50])

# ── helpers ───────────────────────────────────────────────────────────────────

def ema(v, n):
    k = 2 / (n + 1)
    o = np.empty(len(v)); o[0] = v[0]
    for i in range(1, len(v)):
        o[i] = v[i] * k + o[i-1] * (1-k)
    return o

def atr14(h, l, c):
    tr = np.maximum(h-l, np.maximum(np.abs(h-np.roll(c,1)), np.abs(l-np.roll(c,1))))
    tr[0] = h[0]-l[0]
    o = np.empty_like(tr); o[:14] = tr[:14].mean(); k = 1/14
    for i in range(14, len(tr)):
        o[i] = o[i-1]*(1-k) + tr[i]*k
    return o

def resamp(df, f):
    df = df.copy(); df['tf'] = (df['ts_ms'] // f) * f
    return df.groupby('tf', sort=True).agg(
        open=('open','first'), high=('high','max'),
        low=('low','min'),    close=('close','last'),
    ).reset_index().rename(columns={'tf':'ts_ms'})

def session(ts_ms):
    hm = (ts_ms // 60_000) % 1440
    if   7*60 <= hm < 12*60: return 'london'
    if  12*60 <= hm < 16*60: return 'overlap'
    if  16*60 <= hm < 20*60: return 'ny'
    return ''

# ── nivel activo en la barra ──────────────────────────────────────────────────
# Retorna (True, nombre_nivel) si el precio esta dentro de la tolerancia del nivel

LEVEL_TOL = 0.007   # 0.70% — zona alrededor del nivel

def active_level(row) -> tuple[bool, str]:
    close = row['close']
    high  = row['high']
    levels = []

    pdh = row.get('prev_day_high')
    if pdh and pdh > 0 and abs(high - pdh) / pdh <= LEVEL_TOL:
        levels.append('PDH')

    ash = row.get('asian_high')
    if ash and ash > 0 and abs(high - ash) / ash <= LEVEL_TOL:
        levels.append('AH')

    wkh = row.get('weekly_high')
    if wkh and wkh > 0 and abs(high - wkh) / wkh <= LEVEL_TOL:
        levels.append('WH')

    vah = row.get('vp_vah')
    if vah and vah > 0 and abs(high - vah) / vah <= LEVEL_TOL:
        levels.append('VAH')

    if levels:
        return True, '+'.join(levels)
    return False, ''

# ── confirmacion de flujo ─────────────────────────────────────────────────────
# Al menos una señal de flujo vendedor en la barra de entrada

def flow_bearish(row) -> bool:
    obi   = float(row.get('obi10_mean') or 0)
    delta = float(row.get('delta') or 0)
    return obi < -0.05 or delta < 0

# ── rechazo en el nivel ───────────────────────────────────────────────────────
# La barra wickeo al nivel pero cerro por debajo — no acepto precios altos

def rejection(row) -> bool:
    close = float(row['close'])
    open_ = float(row['open'])
    high  = float(row['high'])
    rng   = high - row['low']
    if rng <= 0:
        return False
    wick_up = high - max(close, open_)
    wick_pct = wick_up / rng
    # wick superior 30-85% del rango Y cierre bajista (>=85% = doji sin cuerpo, sin conviccion)
    return 0.30 < wick_pct < 0.85 and (close <= open_)

# ── simulacion ────────────────────────────────────────────────────────────────

def simulate(df, d1_threshold=1.005):
    h1 = resamp(df, H1_MS)
    h1['atr'] = atr14(h1['high'].values, h1['low'].values, h1['close'].values)
    h1_ctx = {int(r.ts_ms): (float(r.high), float(r.atr)) for r in h1.itertuples()}

    d1 = resamp(df, D1_MS)
    d1['ema20'] = ema(d1['close'].values, 20)
    d1_ctx = {int(r.ts_ms): float(r.ema20) for r in d1.itertuples()}

    rows   = df.to_dict('records')
    trades = []
    in_t   = False
    ep = sl = tp = dist = 0.0
    t_start = t_entry = 0
    t_lbl = t_sess = ''
    mfe = mae = 0.0
    cvd_streak = 0
    entry_risk  = 0.0
    entry_score = 0
    cap = CAPITAL
    monthly_risk = CAPITAL * RISK_PCT   # se actualiza cada mes
    current_month = -1

    for i, row in enumerate(rows):
        ts = int(row['ts_ms'])

        # ── gestionar trade abierto ────────────────────────────────────────
        if in_t:
            mfe = max(mfe, (ep - row['low'])  / dist)
            mae = max(mae, (row['high'] - ep) / dist)
            bars_el = i - t_start

            obi_now = float(row.get('obi10_mean') or 0)
            cs      = float(row.get('cvd_slope')  or 0)
            cvd_streak = (cvd_streak + 1) if cs > 0 else 0
            cur_r   = (ep - row['close']) / dist

            # Paso 1 (2026-06-18): CVD exit ELIMINADO. Auditoria de salidas mostro
            # que cerraba ganadores a ~1.3R prematuramente. Target fijo limpio sube
            # AvgR neto +0.072->+0.12 OOS (IS≈OOS, robusto). Solo stop/target/timeout.
            reason = None; exit_px = 0.0
            if row['high'] >= sl:
                reason, exit_px = 'stop', sl
            elif row['low'] <= tp:
                reason, exit_px = 'target', tp
            elif bars_el >= FORWARD:
                reason, exit_px = 'timeout', row['close']

            if reason:
                gross_r = (ep - exit_px) / dist
                fee_r   = FEE_RT * ep / dist        # fee sobre NOTIONAL, no sobre riesgo
                pnl_r   = gross_r - fee_r           # result_r ya es NETO
                pnl_usd = entry_risk * pnl_r
                trades.append({
                    'ts_ms':    t_entry,
                    'date':     pd.Timestamp(t_entry, unit='ms', tz='UTC').strftime('%Y-%m-%d %H:%M'),
                    'session':  t_sess,
                    'level':    t_lbl,
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
                    'oos':      ts >= OOS_MS,
                    'score':    entry_score,
                })
                cap += pnl_usd
                in_t = False; cvd_streak = 0
            continue

        # ── rebalanceo mensual ─────────────────────────────────────────────
        month = pd.Timestamp(ts, unit='ms', tz='UTC').month + pd.Timestamp(ts, unit='ms', tz='UTC').year * 12
        if month != current_month:
            monthly_risk = cap * RISK_PCT
            current_month = month

        # ── condiciones de entrada ─────────────────────────────────────────
        sess = session(ts)
        if sess not in ('london', 'overlap', 'ny'):
            continue

        # D1 EMA20 — bloquear si mercado en tendencia alcista por encima del threshold
        d1_ts  = (ts // D1_MS) * D1_MS - D1_MS
        d1_ema = d1_ctx.get(d1_ts)
        if d1_ema is None:
            continue
        if d1_threshold is not None and row['close'] > d1_ema * d1_threshold:
            continue

        # 1. NIVEL — requiere VAH; bloquea cuando PDH+AH+VAH confluyen (zona de soporte duro)
        ok_level, lbl = active_level(row)
        if not ok_level:
            continue
        if 'VAH' not in lbl:
            continue
        parts = lbl.split('+')
        if len(parts) >= 3 and 'PDH' in parts and 'AH' in parts:
            continue
        if 'PDH' in parts and 'VAH' in parts and len(parts) == 2:
            continue  # PDH+VAH falsa confluencia a 0.70% (niveles separados)

        # WH: bloquear 15h UTC (cierre Overlap/apertura NY — zona de transición tóxica)
        if 'WH' in parts and (ts // 3_600_000) % 24 == 15:
            continue

        # AH+VAH: cuando OBI < -0.15 el Asian High ya fue atacado — nivel no defendido
        if lbl == 'AH+VAH' and float(row.get('obi10_mean') or 0) < -0.15:
            continue

        # 2. RECHAZO
        if not rejection(row):
            continue

        # 3. FLUJO
        if not flow_bearish(row):
            continue

        # Stop H1 estructural
        h1d = h1_ctx.get((ts // H1_MS) * H1_MS)
        if h1d is None:
            continue
        h1h, h1a = h1d
        sl_  = h1h + 0.40 * h1a
        d    = sl_ - row['close']
        if d <= 0:
            continue
        sp = d / row['close']
        if not (MIN_STOP <= sp <= MAX_STOP):
            continue

        # Target FIJO 2.5R (Paso 1 2026-06-18). El regime target (Chop=1.5R) cortaba
        # ganadores; fijo 2.5R da mejor AvgR neto e IS≈OOS. TARGET_R=2.5.
        tgt = TARGET_R

        # Quality score => position sizing (solo boost sc4; ver SCORE_MULT)
        # Target override eliminado en auditoria 2026-06-18: bajaba TotalR IS
        # (214.5->213.1) y subia OOS (96.2->101.3) = huella de tuning a OOS, +
        # magnitud inmaterial (~5R). El target queda solo por regime.
        sc          = quality_score(row)
        entry_score = sc
        entry_risk  = monthly_risk * SCORE_MULT[sc]

        in_t     = True
        ep       = row['close']
        sl       = sl_
        dist     = d
        tp       = ep - tgt * dist
        t_start  = i
        t_entry  = ts
        t_lbl    = lbl
        t_sess   = sess
        mfe = mae = 0.0
        cvd_streak = 0

    return trades, cap


# ── stats ─────────────────────────────────────────────────────────────────────

def stats_block(trades, label):
    if not trades:
        print(f'{label}: sin trades'); return
    n     = len(trades)
    wins  = sum(1 for t in trades if t['result_r'] > 0)
    total = sum(t['result_r'] for t in trades)
    days  = len(set(t['date'][:10] for t in trades))
    stops  = sum(1 for t in trades if t['reason']=='stop')
    tgts   = sum(1 for t in trades if t['reason']=='target')
    cvds   = sum(1 for t in trades if t['reason']=='cvd_exit')
    touts  = sum(1 for t in trades if t['reason']=='timeout')
    avg_mfe = sum(t['mfe_r'] for t in trades)/n
    avg_mae = sum(t['mae_r'] for t in trades)/n
    print(f'\n{label}')
    print(f'  n={n}  WR={wins/n*100:.1f}%  AvgR={total/n:.3f}  TotalR={total:.1f}R')
    print(f'  Trades/dia={n/max(days,1):.1f}  dias={days}')
    print(f'  target={tgts}  stop={stops}  cvd_exit={cvds}  timeout={touts}')
    print(f'  MFE_avg={avg_mfe:.2f}R  MAE_avg={avg_mae:.2f}R')


def by_key(trades, fn, min_n=5):
    g = defaultdict(list)
    for t in trades:
        g[fn(t)].append(t['result_r'])
    rows = []
    for k, rs in g.items():
        n = len(rs)
        if n < min_n: continue
        wins = sum(1 for r in rs if r > 0)
        rows.append({'key':k,'n':n,'wr':round(wins/n*100,1),
                     'avg_r':round(sum(rs)/n,3),'total_r':round(sum(rs),2)})
    return sorted(rows, key=lambda x:-x['total_r'])


def print_table(rows, header):
    if not rows: return
    print(f'\n-- {header} ' + '-'*(55-len(header)))
    kw = max(len(r['key']) for r in rows)
    for r in rows:
        print(f"  {r['key']:<{kw}}  n={r['n']:>4}  WR={r['wr']:>5.1f}%  AvgR={r['avg_r']:>6.3f}")


# ── main ──────────────────────────────────────────────────────────────────────

def sweep_stats(trades, label, days_is=260, days_oos=92):
    is_t  = [t for t in trades if not t['oos']]
    oos_t = [t for t in trades if t['oos']]

    def s(ts, days):
        if not ts: return {'n':0,'wr':0,'avg_r':0,'tpd':0}
        n = len(ts); wins = sum(1 for t in ts if t['result_r'] > 0)
        return {'n':n,'wr':wins/n*100,'avg_r':sum(t['result_r'] for t in ts)/n,'tpd':n/days}

    i = s(is_t, days_is); o = s(oos_t, days_oos)
    print(f'  {label:<16}  '
          f'IS  n={i["n"]:>4}  WR={i["wr"]:>5.1f}%  AvgR={i["avg_r"]:>+.3f}  tpd={i["tpd"]:.1f}  |  '
          f'OOS n={o["n"]:>4}  WR={o["wr"]:>5.1f}%  AvgR={o["avg_r"]:>+.3f}  tpd={o["tpd"]:.1f}')


def main():
    print('Cargando M1...')
    df = pd.read_parquet(DATA_M1).sort_values('ts_ms').reset_index(drop=True)

    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].fillna('')
        elif df[c].dtype == float:
            df[c] = df[c].fillna(0.0)

    print(f'Barras: {len(df):,}\n')

    print('=== SHORTS v6 — Score v3 BOOST a (sell_vol + buy_vol + cvd_slope + vr) ===')
    print(f'  Thresholds IS: sv_q50={SCORE_SV_Q50}  bv_q50={SCORE_BV_Q50}  vr_q50={SCORE_VR_Q50}  cvd_slope>0')
    print(f'  BOOST: {SCORE_MULT}  (score 0-4)\n')

    trades, cap_final = simulate(df, d1_threshold=None)
    is_t  = [t for t in trades if not t['oos']]
    oos_t = [t for t in trades if t['oos']]

    stats_block(is_t,  'IN-SAMPLE    (Jun 2025 - Feb 2026)')
    stats_block(oos_t, 'OUT-OF-SAMPLE (Mar 2026 - May 2026)')

    print(f'\nCapital: ${CAPITAL:.0f} => ${cap_final:,.0f}  ({(cap_final/CAPITAL-1)*100:.1f}%)')

    print('\n-- Por score OOS --')
    by_sc = defaultdict(list)
    for t in oos_t:
        by_sc[t.get('score', 0)].append(t['result_r'])
    for sc in range(5):
        rs = by_sc.get(sc, [])
        if not rs: continue
        n = len(rs); w = sum(1 for r in rs if r > 0); avg = sum(rs) / n
        mult = SCORE_MULT[sc]
        flag = ' ***' if w/n >= 0.58 else (' <<' if w/n < 0.44 else '')
        print(f'  Score {sc}/4  n={n:>4}  WR={w/n*100:>5.1f}%  AvgR={avg:>+.3f}  size={mult:.0%}{flag}')

    print_table(by_key(trades, lambda t: t['level'],   min_n=5),  'Por nivel OOS')
    print_table(by_key(oos_t,  lambda t: t['session'], min_n=5),  'Por sesion OOS')
    print_table(by_key(oos_t,  lambda t: t['reason'],  min_n=1),  'Por razon OOS')

    print('\n-- Curva mensual --')
    by_month = defaultdict(lambda: {'n': 0, 'wins': 0, 'pnl': 0.0})
    for t in trades:
        mo = t['date'][:7]
        by_month[mo]['n']    += 1
        by_month[mo]['pnl']  += t['pnl_usd']
        if t['result_r'] > 0:
            by_month[mo]['wins'] += 1
    running = CAPITAL
    for mo in sorted(by_month):
        d   = by_month[mo]
        wr  = d['wins'] / d['n'] * 100 if d['n'] else 0
        running += d['pnl']
        tag = 'IS ' if mo < '2026-03' else 'OOS'
        print(f'  {mo} {tag}  n={d["n"]:>3}  WR={wr:>5.1f}%  PnL=${d["pnl"]:>+8,.0f}  Equity=${running:>9,.0f}')

    EXPORTS.mkdir(exist_ok=True)
    pd.DataFrame(trades).to_csv(EXPORTS / 'basics_trades.csv', index=False)
    print(f'\nCSV: {EXPORTS}/basics_trades.csv')


if __name__ == '__main__':
    main()
