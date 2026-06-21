"""
_swing_explorer.py — ¿el edge vive en la GEOMETRÍA swing (no en la señal ni el TF)?
====================================================================================
Las 3 sesiones previas mostraron: ni el disparador (24 setups) ni el TF de entrada
(M1/M5/M15) rescatan el edge. El baseline NULO dio OOS ~−0.04 (breakeven) mientras los
setups dieron OOS −0.15 a −0.26 → el cuello es la GEOMETRÍA: stop ≤0.75% + target 2.8R
fijo + fee fuerza WR~35% que las fees rematan.

Este test sube el HORIZONTE DE RIESGO (no el TF de la señal):
  STOP (causal, ancho):
    - swing4h : max high 4h (rolling 240) + 0.5·ATR_H1   (estructural; puede salir tight)
    - fix1.5% : entry × (1 ± 1.5%)                         (ancho limpio, decoupla geometría)
  TARGET:
    - 2.8R    : fijo (control)
    - struct  : nivel estructural más cercano (VAL/PDL/WL/AL/LVN abajo; espejo arriba),
                con veto rr_too_low (RR<1.2) y rr_capado a 8
  timeout 2 días (2880 M1) | fee 0.11% | exit scan en M1 (toques correctos)
  Confirmación completa (régimen D1 + H1 estructura + OF) — la cruda ya estaba muerta.

Uso: python backtest/_swing_explorer.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'backtest'))
import mtf_v2 as m

FWD_MIN, COOLDOWN_MIN = 2880, 30   # 2 días, cooldown 30m
FEE_RT, TOL = m.FEE_RT, m.LEVEL_TOL
H1_MS, D1_MS, OOS_MS = m.H1_MS, m.D1_MS, m.OOS_MS
SWING_W = 240          # 4h
MIN_SW, MAX_SW = 0.005, 0.040   # rango stop swing
FIX_STOP = 0.015       # 1.5%
MIN_RR, MAX_RR = 1.2, 8.0
MIN_TGT_GAP = 0.003    # target al menos 0.3% lejos


def load():
    df = pd.read_parquet(m.DATA_M1).sort_values('ts_ms').reset_index(drop=True)
    for c in df.select_dtypes('object').columns: df[c] = df[c].fillna('')
    for c in df.select_dtypes('float').columns:  df[c] = df[c].fillna(0.0)
    for c in df.select_dtypes('bool').columns:    df[c] = df[c].fillna(False)
    return df


def sim(side, entry_idx, close, high, low, ts, stop_arr, tgt_arr):
    """tgt_arr: precio de target (si NaN o inválido → trade se descarta)."""
    n = len(close); trades = []; next_ok = 0
    for ci in entry_idx:
        if ci < next_ok or ci + 1 >= n: continue
        ep = close[ci]; st = stop_arr[ci]; tg = tgt_arr[ci]
        if not (np.isfinite(st) and np.isfinite(tg)): continue
        risk = (st - ep) if side == 'short' else (ep - st)
        if risk <= 0: continue
        rr = ((ep - tg) if side == 'short' else (tg - ep)) / risk
        if not (MIN_RR <= rr <= MAX_RR): continue
        reason = None; ex = None; exit_i = min(ci + FWD_MIN, n - 1)
        for j in range(ci + 1, min(ci + 1 + FWD_MIN, n)):
            if side == 'short':
                if high[j] >= st: reason, ex, exit_i = 's', st, j; break
                if low[j] <= tg:  reason, ex, exit_i = 't', tg, j; break
            else:
                if low[j] <= st:  reason, ex, exit_i = 's', st, j; break
                if high[j] >= tg: reason, ex, exit_i = 't', tg, j; break
        if reason is None: ex = close[exit_i]
        gross = (ep - ex) / risk if side == 'short' else (ex - ep) / risk
        r = gross - FEE_RT * ep / risk
        trades.append({'r': r, 'rr': rr, 'oos': int(ts[ci]) >= OOS_MS, 'bars': exit_i - ci})
        next_ok = exit_i + COOLDOWN_MIN
    return trades


def stat(tr):
    it = [t for t in tr if not t['oos']]; ot = [t for t in tr if t['oos']]
    def s(x):
        if not x: return (0, 0.0, 0.0, 0.0, 0.0)
        n = len(x); wr = 100 * sum(1 for t in x if t['r'] > 0) / n
        return (n, wr, float(np.mean([t['r'] for t in x])), float(np.mean([t['rr'] for t in x])),
                float(np.mean([t['bars'] for t in x])) / 60)
    return s(it), s(ot)


def verdict(isr, oor):
    ni, wi, ai = isr[:3]; no, wo, ao = oor[:3]
    if no < 12 or ni < 20:        return '· chica'
    if ai <= 0 or ao <= 0:        return '✗ neg/flip'
    if ao < 0.35 * ai:            return '⚠ colapsa'
    if ai >= 0.10 and ao >= 0.10: return '✅ candidato'
    return '~ marginal'


def main():
    df = load()
    days = (df.ts_ms.max() - df.ts_ms.min()) / 86_400_000
    ts = df['ts_ms'].values.astype(np.int64)
    close = df['close'].values.astype(float); high = df['high'].values.astype(float)
    low = df['low'].values.astype(float); op = df['open'].values.astype(float)

    h1 = m.resamp(df, H1_MS); h1['atr'] = m.atr14(h1['high'].values, h1['low'].values, h1['close'].values)
    ha = dict(zip(h1['ts_ms'].astype(int), h1['atr'].astype(float)))
    patr = np.array([ha.get(int(b), np.nan) for b in (ts // H1_MS) * H1_MS - H1_MS])
    swing_hi = pd.Series(high).rolling(SWING_W, min_periods=30).max().values
    swing_lo = pd.Series(low).rolling(SWING_W, min_periods=30).min().values

    # STOP models
    stop_sw_s = swing_hi + 0.5 * patr
    stop_sw_l = swing_lo - 0.5 * patr
    stop_fx_s = close * (1 + FIX_STOP)
    stop_fx_l = close * (1 - FIX_STOP)
    # validar swing stop dentro de rango (fijo siempre válido)
    sp_sw_s = (stop_sw_s - close) / close
    sp_sw_l = (close - stop_sw_l) / close
    stop_sw_s = np.where((sp_sw_s >= MIN_SW) & (sp_sw_s <= MAX_SW), stop_sw_s, np.nan)
    stop_sw_l = np.where((sp_sw_l >= MIN_SW) & (sp_sw_l <= MAX_SW), stop_sw_l, np.nan)

    # TARGET estructural (nivel más cercano abajo p/ short, arriba p/ long)
    sup = np.vstack([df[c].values.astype(float) for c in
                     ['vp_val', 'prev_day_low', 'weekly_low', 'asian_low', 'vp_lvn_below']])
    rez = np.vstack([df[c].values.astype(float) for c in
                     ['vp_vah', 'prev_day_high', 'weekly_high', 'asian_high']])
    thr_s = close * (1 - MIN_TGT_GAP); thr_l = close * (1 + MIN_TGT_GAP)
    sup_below = np.where((sup > 0) & (sup < thr_s), sup, -np.inf)
    tgt_struct_s = np.max(sup_below, axis=0)              # el soporte más alto que aún está abajo
    tgt_struct_s = np.where(np.isfinite(tgt_struct_s), tgt_struct_s, np.nan)
    rez_above = np.where((rez > 0) & (rez > thr_l), rez, np.inf)
    tgt_struct_l = np.min(rez_above, axis=0)
    tgt_struct_l = np.where(np.isfinite(tgt_struct_l), tgt_struct_l, np.nan)

    # régimen / OF / estructura
    d1 = m.resamp(df, D1_MS); d1['e'] = m.ema(d1['close'].values, 20)
    de_ = dict(zip(d1['ts_ms'].astype(int), d1['e'].astype(float)))
    de = np.array([de_.get(int(b), np.nan) for b in (ts // D1_MS) * D1_MS - D1_MS])
    d1_bear = np.isfinite(de) & (close <= de * 0.980)
    ratio = np.where(np.isfinite(de) & (de > 0), close / de, np.nan)
    d1_bull = (ratio >= 1.000) & (ratio <= 1.030)
    pt = df['plus_ticks'].values.astype(float); mt = df['minus_ticks'].values.astype(float)
    poc = df['vp_poc'].values.astype(float)
    of_s = (poc > 0) & (np.minimum(op, close) < poc) & (mt > pt) & ~df['fp_absorb_buy'].values.astype(bool)
    of_l = ((poc <= 0) | (np.minimum(op, close) <= poc)) & (pt > mt) & ~df['fp_absorb_sell'].values.astype(bool)
    h1s = df['h1_bos_bear'].values.astype(bool) | df['h1_choch_bear'].values.astype(bool)

    rng = high - low
    with np.errstate(all='ignore'):
        wu = (high - np.maximum(close, op)) / np.where(rng > 0, rng, np.nan)
        wl = (np.minimum(close, op) - low) / np.where(rng > 0, rng, np.nan)
    rej_s = (rng > 0) & (wu > 0.30) & (wu < 0.85) & (close <= op)
    rej_l = (rng > 0) & (wl > 0.30) & (wl < 0.85) & (close >= op)

    def nearH(c):
        lv = df[c].values.astype(float); return (lv > 0) & (np.abs(high - lv) / np.where(lv > 0, lv, 1) <= TOL)
    def nearL(c):
        lv = df[c].values.astype(float); return (lv > 0) & (np.abs(low - lv) / np.where(lv > 0, lv, 1) <= TOL)
    res = nearH('vp_vah') | nearH('asian_high') | nearH('prev_day_high') | nearH('weekly_high')
    sup_lvl = nearL('vp_val') | nearL('asian_low') | nearL('prev_day_low') | nearL('weekly_low')

    hm = (ts // 60_000) % 1440; sess = (hm >= 7 * 60) & (hm < 20 * 60); sess_pm = (hm >= 12 * 60) & (hm < 20 * 60)

    setups = {
        'rej@VAH':     ('short', nearH('vp_vah') & rej_s),
        'rej@anyRes':  ('short', res & rej_s),
        'Sweep':       ('short', df['sweep_confirmed'].values.astype(bool)),
        'Displacement':('short', df['displacement_bear'].values.astype(bool)),
        'AbsAsk@Res':  ('short', df['abs_ask'].values.astype(bool) & res),
        'rej@VAL':     ('long',  nearL('vp_val') & rej_l),
    }

    nan = np.full(len(close), np.nan)
    fix_tgt_s = np.where(np.isfinite(stop_fx_s), close - 2.8 * (stop_fx_s - close), np.nan)  # placeholder no usado
    def tgt28(side, stp):
        return np.where(np.isfinite(stp), (close - 2.8 * (stp - close)) if side == 'short'
                        else (close + 2.8 * (close - stp)), np.nan)

    combos = [
        ('swing4h+2.8R', 'sw', '28'),
        ('fix1.5%+2.8R', 'fx', '28'),
        ('fix1.5%+struct', 'fx', 'st'),
        ('swing4h+struct', 'sw', 'st'),
    ]

    print(f'Días: {days:.0f} | timeout {FWD_MIN//1440}d | fee {FEE_RT*100:.2f}% | swing stop {MIN_SW*100:.1f}-{MAX_SW*100:.1f}%, fijo {FIX_STOP*100:.1f}% | RR {MIN_RR}-{MAX_RR}')
    print(f'{"setup":<13} {"geometría":<16} | IS  n/WR/AvgR/RR/hrs        | OOS n/WR/AvgR/RR/hrs       | veredicto')
    print('-' * 110)

    def run(side, mask, sk, tk):
        stp = (stop_sw_s if side == 'short' else stop_sw_l) if sk == 'sw' else (stop_fx_s if side == 'short' else stop_fx_l)
        if tk == '28':
            tgt = tgt28(side, stp)
        else:
            tgt = tgt_struct_s if side == 'short' else tgt_struct_l
        sm = sess if side == 'short' else sess_pm
        reg = d1_bear if side == 'short' else d1_bull
        ofx = of_s if side == 'short' else of_l
        struct = (h1s if side == 'short' else d1_bull)
        full = mask & sm & reg & ofx & struct
        ei = np.where(full)[0]
        return sim(side, ei, close, high, low, ts, stp, tgt)

    for name, (side, mask) in setups.items():
        for label, sk, tk in combos:
            tr = run(side, mask, sk, tk)
            (ni, wi, ai, ri, hi), (no, wo, ao, ro, ho) = stat(tr)
            v = verdict((ni, wi, ai), (no, wo, ao))
            print(f'{name:<13} {label:<16} | {ni:>4}/{wi:>2.0f}/{ai:>+.2f}/{ri:>3.1f}/{hi:>4.0f}h '
                  f'| {no:>3}/{wo:>2.0f}/{ao:>+.2f}/{ro:>3.1f}/{ho:>4.0f}h | {v}')
        print()

    # NULL recalibrado para geometría swing (fix1.5% + 2.8R, entradas régimen sin señal)
    nullm = sess & d1_bear & (np.arange(len(close)) % 60 == 0)
    tr = sim('short', np.where(nullm)[0], close, high, low, ts, stop_fx_s, tgt28('short', stop_fx_s))
    (ni, wi, ai, ri, hi), (no, wo, ao, ro, ho) = stat(tr)
    print(f'{"NULL":<13} {"fix1.5%+2.8R":<16} | {ni:>4}/{wi:>2.0f}/{ai:>+.2f}/{ri:>3.1f}/{hi:>4.0f}h '
          f'| {no:>3}/{wo:>2.0f}/{ao:>+.2f}/{ro:>3.1f}/{ho:>4.0f}h | baseline')


if __name__ == '__main__':
    main()
