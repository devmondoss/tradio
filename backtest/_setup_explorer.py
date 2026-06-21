"""
_setup_explorer.py — Exploración amplia de setups ICT + orderflow (CAUSAL, IS/OOS)
====================================================================================
Objetivo: ir más allá de la única tesis explorada hasta ahora ("fade del bull-trap en
resistencia con confirmación orderflow"). Probar TODAS las familias de setup razonables
sobre los mismos datos causales, con el MISMO motor de salida, y rankear por ROBUSTEZ
(IS≈OOS, ambos positivos), no por el mejor número aislado.

Diseño experimental (lo único que cambia entre setups es el DISPARADOR de entrada):
  Exit/stop/fee/timeout IDÉNTICOS para todos:
    - stop = H1 CERRADA previa ± 0.40·ATR_H1  (causal: bucket H1 anterior, nunca el en-curso)
    - target 2.8R | timeout 240 barras (4h) | fee 0.11% RT | stop_pct ∈ [0.30%, 0.75%]
    - 1 posición por dirección, cooldown 15 barras
  Cada setup se corre en 2 configs:
    [raw] disparador + sesión + stop          (señal cruda, sin régimen ni confirmación)
    [full] + régimen D1 alineado + confirmación orderflow (body/POC + ticks + veto absorb)

ADVERTENCIA: 1 símbolo, n_OOS chico → multiple-comparisons. Nada que sobreviva con n_OOS<20
es concluyente. El objetivo es DESCARTAR familias muertas y señalar candidatos a profundizar.

Uso: python backtest/_setup_explorer.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'backtest'))
import mtf_v2 as m

TARGET_R = 2.8
FWD      = 240
COOLDOWN = 15
FEE_RT   = m.FEE_RT
TOL      = m.LEVEL_TOL
MIN_STOP, MAX_STOP = m.MIN_STOP, m.MAX_STOP
H1_MS, D1_MS = m.H1_MS, m.D1_MS
OOS_MS = m.OOS_MS


def load():
    df = pd.read_parquet(m.DATA_M1).sort_values('ts_ms').reset_index(drop=True)
    for c in df.select_dtypes('object').columns: df[c] = df[c].fillna('')
    for c in df.select_dtypes('float').columns:  df[c] = df[c].fillna(0.0)
    for c in df.select_dtypes('bool').columns:    df[c] = df[c].fillna(False)
    return df


def build_context(df):
    """Arrays causales: stop short/long (H1 cerrada previa) y régimen D1 (cerrado previo)."""
    h1 = m.resamp(df, H1_MS)
    h1['atr'] = m.atr14(h1['high'].values, h1['low'].values, h1['close'].values)
    h1_high = dict(zip(h1['ts_ms'].astype(int), h1['high'].astype(float)))
    h1_low  = dict(zip(h1['ts_ms'].astype(int), h1['low'].astype(float)))
    h1_atr  = dict(zip(h1['ts_ms'].astype(int), h1['atr'].astype(float)))
    d1 = m.resamp(df, D1_MS); d1['ema20'] = m.ema(d1['close'].values, 20)
    d1_ema = dict(zip(d1['ts_ms'].astype(int), d1['ema20'].astype(float)))

    ts = df['ts_ms'].values.astype(np.int64)
    h1_prev = (ts // H1_MS) * H1_MS - H1_MS
    d1_prev = (ts // D1_MS) * D1_MS - D1_MS
    nan = float('nan')
    sh = np.array([h1_high.get(int(b), nan) for b in h1_prev])
    sl = np.array([h1_low.get(int(b),  nan) for b in h1_prev])
    sa = np.array([h1_atr.get(int(b),  nan) for b in h1_prev])
    de = np.array([d1_ema.get(int(b),  nan) for b in d1_prev])
    stop_short = sh + 0.40 * sa
    stop_long  = sl - 0.40 * sa
    return stop_short, stop_long, de


def sim(side, entry_mask, close, high, low, ts, stop_arr, d1_ema, regime_ok=None, of_ok=None):
    """Motor único. Entra en barras de entry_mask (& regime & of si se pasan); exit estándar."""
    mask = entry_mask.copy()
    if regime_ok is not None: mask &= regime_ok
    if of_ok is not None:     mask &= of_ok
    cand = np.where(mask)[0]
    n = len(close)
    trades = []
    next_ok = 0
    for ci in cand:
        if ci < next_ok or ci + 1 >= n: continue
        ep = close[ci]; st = stop_arr[ci]
        if not np.isfinite(st): continue
        if side == 'short':
            dist = st - ep
        else:
            dist = ep - st
        if dist <= 0: continue
        sp = dist / ep
        if not (MIN_STOP <= sp <= MAX_STOP): continue
        tp = ep - TARGET_R * dist if side == 'short' else ep + TARGET_R * dist
        # forward scan
        reason = None; ex = None; exit_i = min(ci + FWD, n - 1)
        for j in range(ci + 1, min(ci + 1 + FWD, n)):
            if side == 'short':
                if high[j] >= st: reason, ex, exit_i = 'stop', st, j; break
                if low[j]  <= tp: reason, ex, exit_i = 'target', tp, j; break
            else:
                if low[j]  <= st: reason, ex, exit_i = 'stop', st, j; break
                if high[j] >= tp: reason, ex, exit_i = 'target', tp, j; break
        if reason is None:
            ex = close[exit_i]; reason = 'timeout'
        gross = (ep - ex) / dist if side == 'short' else (ex - ep) / dist
        r = gross - FEE_RT * ep / dist
        trades.append({'ts': int(ts[ci]), 'r': r, 'oos': int(ts[ci]) >= OOS_MS, 'bars': exit_i - ci})
        next_ok = exit_i + COOLDOWN
    return trades


def stat(trades):
    is_t = [t for t in trades if not t['oos']]; oo = [t for t in trades if t['oos']]
    def s(ts):
        if not ts: return (0, 0.0, 0.0)
        n = len(ts); wr = 100 * sum(1 for t in ts if t['r'] > 0) / n
        return (n, wr, sum(t['r'] for t in ts) / n)
    return s(is_t), s(oo)


def verdict(isr, oor):
    ni, wi, ai = isr; no, wo, ao = oor
    if no < 12 or ni < 20:                 return '· muestra chica'
    if ai <= 0 or ao <= 0:                 return '✗ negativo/flip'
    if ao < 0.35 * ai:                     return '⚠ colapsa OOS'
    if ai >= 0.12 and ao >= 0.12:          return '✅ candidato'
    return '~ marginal'


def main():
    df = load()
    days = (df.ts_ms.max() - df.ts_ms.min()) / 86_400_000
    stop_short, stop_long, d1_ema = build_context(df)

    close = df['close'].values.astype(float); high = df['high'].values.astype(float)
    low = df['low'].values.astype(float); op = df['open'].values.astype(float)
    ts = df['ts_ms'].values.astype(np.int64)
    rng = high - low
    with np.errstate(divide='ignore', invalid='ignore'):
        wick_up = (high - np.maximum(close, op)) / np.where(rng > 0, rng, np.nan)
        wick_lo = (np.minimum(close, op) - low) / np.where(rng > 0, rng, np.nan)
    rej_s = (rng > 0) & (wick_up > 0.30) & (wick_up < 0.85) & (close <= op)
    rej_l = (rng > 0) & (wick_lo > 0.30) & (wick_lo < 0.85) & (close >= op)

    def col(c): return df[c].values
    def near(level_col):
        lv = df[level_col].values.astype(float)
        return (lv > 0) & (np.abs(high - lv) / np.where(lv > 0, lv, 1) <= TOL)
    def near_lo(level_col):
        lv = df[level_col].values.astype(float)
        return (lv > 0) & (np.abs(low - lv) / np.where(lv > 0, lv, 1) <= TOL)

    res = near('vp_vah') | near('asian_high') | near('prev_day_high') | near('weekly_high')
    sup = near_lo('vp_val') | near_lo('asian_low') | near_lo('prev_day_low') | near_lo('weekly_low')

    # sesiones
    hm = (ts // 60_000) % 1440
    sess_all = (hm >= 7 * 60) & (hm < 20 * 60)
    sess_pm  = (hm >= 12 * 60) & (hm < 20 * 60)   # overlap+ny (longs)

    # régimen D1 (cerrado previo)
    d1_bear = np.isfinite(d1_ema) & (close <= d1_ema * 0.980)
    ratio = np.where(np.isfinite(d1_ema) & (d1_ema > 0), close / d1_ema, np.nan)
    d1_bull = (ratio >= 1.000) & (ratio <= 1.030)

    # confirmación orderflow
    poc = df['vp_poc'].values.astype(float)
    pt = df['plus_ticks'].values.astype(float); mt = df['minus_ticks'].values.astype(float)
    of_short = col('body_below_poc').astype(bool) & (mt > pt) & ~col('fp_absorb_buy').astype(bool)
    body_top = np.minimum(op, close)
    of_long = ((poc <= 0) | (body_top <= poc)) & (pt > mt) & ~col('fp_absorb_sell').astype(bool)

    B = lambda c: col(c).astype(bool)
    stk = df['stacked_imb'].values
    vr = df['vr'].values.astype(float); dz = df['dz'].values.astype(float)
    vr_is_q = float(df[df.ts_ms < OOS_MS]['vr'].quantile(0.80))
    dz_is_q = float(df[df.ts_ms < OOS_MS]['dz'].abs().quantile(0.80))

    # ── CATÁLOGO: (nombre, side, mask, familia) ──────────────────────────────────
    setups = [
        # FADE en resistencia (tesis actual) / soporte (espejo)
        ('rej@VAH',            'short', near('vp_vah') & rej_s,                 'fade'),
        ('rej@anyRes',         'short', res & rej_s,                           'fade'),
        ('OB retest+rej',      'short', B('near_bearish_ob') & rej_s,          'fade'),
        ('FVG+rej',            'short', B('near_bearish_fvg') & rej_s,         'fade'),
        ('OTE fib+rej',        'short', B('fib_ote') & rej_s,                  'fade'),
        ('rej@VAL',            'long',  near_lo('vp_val') & rej_l,             'fade'),
        ('rej@anySup',         'long',  sup & rej_l,                           'fade'),
        # SWEEP primario (barre liquidez y revierte)
        ('LiqSweep',           'short', B('sweep_confirmed'),                  'sweep'),
        ('EqualHigh sweep',    'short', B('equal_high_sweep'),                 'sweep'),
        ('PDH sweep',          'short', B('pdh_sweep'),                        'sweep'),
        ('Sweep+rej@Res',      'short', B('sweep_confirmed') & res & rej_s,    'sweep'),
        # CONTINUACIÓN / breakout (operar CON la tendencia)
        ('Displacement bear',  'short', B('displacement_bear'),               'cont'),
        ('H1 BOS bear+rej',    'short', B('h1_bos_bear') & rej_s,             'cont'),
        ('H1 BOS bull+rej',    'long',  B('h1_bos_bull') & rej_l,             'cont'),
        ('Thin below+rej',     'short', B('thin_below') & rej_s,              'cont'),
        # ORDERFLOW puro (con localización en nivel)
        ('AbsAsk@Res',         'short', B('abs_ask') & res,                    'oflow'),
        ('AbsBid@Sup',         'long',  B('abs_bid') & sup,                    'oflow'),
        ('CVDdiv@Res',         'short', B('cvd_div') & res,                    'oflow'),
        ('BigTradeBear@Res',   'short', B('big_trade_bearish') & res,         'oflow'),
        ('BigTradeBull@Sup',   'long',  B('big_trade_bullish') & sup,         'oflow'),
        ('StackedImbBear+rej', 'short', (stk == 'Bearish') & rej_s,           'oflow'),
        ('StackedImbBull+rej', 'long',  (stk == 'Bullish') & rej_l,           'oflow'),
        # EXHAUSTION (pico volumen + delta extremo + rechazo en nivel)
        ('Exhaust short@Res',  'short', res & rej_s & (vr >= vr_is_q) & (np.abs(dz) >= dz_is_q), 'exhaust'),
        ('Exhaust long@Sup',   'long',  sup & rej_l & (vr >= vr_is_q) & (np.abs(dz) >= dz_is_q), 'exhaust'),
    ]

    print(f'Días: {days:.0f}   OOS desde 2026-03-01   vr_Q80(IS)={vr_is_q:.2f}  |dz|_Q80(IS)={dz_is_q:.2f}')
    print(f'Exit común: stop H1 prev ±0.40ATR | target {TARGET_R}R | timeout {FWD} | fee {FEE_RT*100:.2f}% | stop 0.30-0.75%')
    hdr = f'{"setup":<20} {"cfg":<5} | {"IS n":>5} {"WR":>5} {"AvgR":>7} | {"OOS n":>5} {"WR":>5} {"AvgR":>7} | {"tpd":>4} | veredicto'
    last_fam = None
    for name, side, mask, fam in setups:
        if fam != last_fam:
            print('\n' + '=' * len(hdr)); print(f'## FAMILIA: {fam}'); print('=' * len(hdr)); print(hdr); last_fam = fam
        stp = stop_short if side == 'short' else stop_long
        sess = sess_all if side == 'short' else sess_pm
        reg = d1_bear if side == 'short' else d1_bull
        of = of_short if side == 'short' else of_long
        for cfg, rok, ook in (('raw', None, None), ('full', reg, of)):
            tr = sim(side, mask & sess, close, high, low, ts, stp, d1_ema, regime_ok=rok, of_ok=ook)
            (ni, wi, ai), (no, wo, ao) = stat(tr)
            tpd = len(tr) / days
            v = verdict((ni, wi, ai), (no, wo, ao))
            print(f'{name:<20} {cfg:<5} | {ni:>5} {wi:>4.0f}% {ai:>+7.3f} | {no:>5} {wo:>4.0f}% {ao:>+7.3f} | {tpd:>4.2f} | {v}')


if __name__ == '__main__':
    main()
