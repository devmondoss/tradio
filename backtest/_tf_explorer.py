"""
_tf_explorer.py — ¿el edge vive en M5/M15 en vez de M1?
========================================================
Hipótesis del usuario: M1 es demasiado ruido. Con barras más anchas (M5/M15) el rechazo
estructural es más limpio y el stop relativo más amplio (menos fee-sensible).

Diseño (aísla SOLO el timeframe de entrada; todo lo demás constante y causal):
  - Se detecta el setup en barras resampleadas M1/M5/M15 (la barra se cierra ANTES de entrar).
  - Niveles/estructura TF-independientes (VAH, PDH, AH, WH, D1 régimen, H1 BOS) se llevan
    al cierre de la barra (último valor M1 del grupo = lo conocido en ese cierre).
  - Rechazo (wick), body_below_poc y agresión (ticks) se RE-computan en la nueva resolución.
  - Eventos M1 (FVG/OB/sweep/displacement/absorb) se agregan como "ocurrió en la ventana".
  - EXIT idéntico y causal: stop = max high 60m (swing 1h) + 0.40·ATR_H1_prev, target 2.8R,
    fee 0.11%, timeout 4h. El scan de stop/target se hace SOBRE M1 (granularidad correcta).

Uso: python backtest/_tf_explorer.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'backtest'))
import mtf_v2 as m

TARGET_R, FWD_MIN, COOLDOWN_MIN = 2.8, 240, 15
FEE_RT, TOL = m.FEE_RT, m.LEVEL_TOL
MIN_STOP, MAX_STOP = m.MIN_STOP, m.MAX_STOP
H1_MS, D1_MS, OOS_MS = m.H1_MS, m.D1_MS, m.OOS_MS


def load():
    df = pd.read_parquet(m.DATA_M1).sort_values('ts_ms').reset_index(drop=True)
    for c in df.select_dtypes('object').columns: df[c] = df[c].fillna('')
    for c in df.select_dtypes('float').columns:  df[c] = df[c].fillna(0.0)
    for c in df.select_dtypes('bool').columns:    df[c] = df[c].fillna(False)
    return df


def sim_short(entry_idx, close, high, low, ts, stop_short):
    """entry_idx: índices M1 (cierre de la barra TF) ordenados. Exit scan en M1."""
    n = len(close); trades = []; next_ok = 0
    for ci in entry_idx:
        if ci < next_ok or ci + 1 >= n: continue
        ep = close[ci]; st = stop_short[ci]
        if not np.isfinite(st): continue
        dist = st - ep
        if dist <= 0: continue
        sp = dist / ep
        if not (MIN_STOP <= sp <= MAX_STOP): continue
        tp = ep - TARGET_R * dist
        reason = None; ex = None; exit_i = min(ci + FWD_MIN, n - 1)
        for j in range(ci + 1, min(ci + 1 + FWD_MIN, n)):
            if high[j] >= st: reason, ex, exit_i = 's', st, j; break
            if low[j] <= tp:  reason, ex, exit_i = 't', tp, j; break
        if reason is None: ex = close[exit_i]
        r = (ep - ex) / dist - FEE_RT * ep / dist
        trades.append({'r': r, 'oos': int(ts[ci]) >= OOS_MS})
        next_ok = exit_i + COOLDOWN_MIN
    return trades


def sim_long(entry_idx, close, high, low, ts, stop_long):
    n = len(close); trades = []; next_ok = 0
    for ci in entry_idx:
        if ci < next_ok or ci + 1 >= n: continue
        ep = close[ci]; st = stop_long[ci]
        if not np.isfinite(st): continue
        dist = ep - st
        if dist <= 0: continue
        sp = dist / ep
        if not (MIN_STOP <= sp <= MAX_STOP): continue
        tp = ep + TARGET_R * dist
        reason = None; ex = None; exit_i = min(ci + FWD_MIN, n - 1)
        for j in range(ci + 1, min(ci + 1 + FWD_MIN, n)):
            if low[j] <= st:  reason, ex, exit_i = 's', st, j; break
            if high[j] >= tp: reason, ex, exit_i = 't', tp, j; break
        if reason is None: ex = close[exit_i]
        r = (ex - ep) / dist - FEE_RT * ep / dist
        trades.append({'r': r, 'oos': int(ts[ci]) >= OOS_MS})
        next_ok = exit_i + COOLDOWN_MIN
    return trades


def stat(tr):
    it = [t['r'] for t in tr if not t['oos']]; ot = [t['r'] for t in tr if t['oos']]
    def s(x):
        if not x: return (0, 0.0, 0.0)
        return (len(x), 100 * sum(1 for r in x if r > 0) / len(x), float(np.mean(x)))
    return s(it), s(ot)


def verdict(isr, oor):
    ni, wi, ai = isr; no, wo, ao = oor
    if no < 12 or ni < 20:        return '· muestra chica'
    if ai <= 0 or ao <= 0:        return '✗ negativo/flip'
    if ao < 0.35 * ai:            return '⚠ colapsa OOS'
    if ai >= 0.12 and ao >= 0.12: return '✅ candidato'
    return '~ marginal'


def main():
    df = load()
    days = (df.ts_ms.max() - df.ts_ms.min()) / 86_400_000
    ts = df['ts_ms'].values.astype(np.int64)
    close = df['close'].values.astype(float); high = df['high'].values.astype(float)
    low = df['low'].values.astype(float)

    # ── EXIT context (M1, causal) ─────────────────────────────────────────────
    h1 = m.resamp(df, H1_MS); h1['atr'] = m.atr14(h1['high'].values, h1['low'].values, h1['close'].values)
    ha = dict(zip(h1['ts_ms'].astype(int), h1['atr'].astype(float)))
    patr = np.array([ha.get(int(b), np.nan) for b in (ts // H1_MS) * H1_MS - H1_MS])
    roll_hi = pd.Series(high).rolling(60, min_periods=10).max().values
    roll_lo = pd.Series(low).rolling(60, min_periods=10).min().values
    stop_short = roll_hi + 0.40 * patr
    stop_long  = roll_lo - 0.40 * patr

    d1 = m.resamp(df, D1_MS); d1['e'] = m.ema(d1['close'].values, 20)
    de_ = dict(zip(d1['ts_ms'].astype(int), d1['e'].astype(float)))
    de = np.array([de_.get(int(b), np.nan) for b in (ts // D1_MS) * D1_MS - D1_MS])
    d1_bear = np.isfinite(de) & (close <= de * 0.980)
    ratio = np.where(np.isfinite(de) & (de > 0), close / de, np.nan)
    d1_bull = (ratio >= 1.000) & (ratio <= 1.030)

    # arrays M1 fuente para resample manual
    o1 = df['open'].values.astype(float)
    cols_carry = ['vp_vah', 'vp_val', 'vp_poc', 'asian_high', 'asian_low', 'prev_day_high',
                  'prev_day_low', 'weekly_high', 'weekly_low']
    cols_bool_last = ['h1_bos_bear', 'h1_choch_bear', 'h1_bos_bull']
    cols_event_any = ['near_bearish_fvg', 'near_bearish_ob', 'sweep_confirmed', 'displacement_bear',
                      'fp_absorb_buy', 'fp_absorb_sell', 'abs_ask', 'abs_bid']
    cols_sum = ['plus_ticks', 'minus_ticks']

    def run_tf(tf_min):
        if tf_min == 1:
            idx_last = np.arange(len(df))  # cada barra M1
            oo, hh, ll, cc = o1, high, low, close
            carry = {c: df[c].values.astype(float) for c in cols_carry}
            blast = {c: df[c].values.astype(bool) for c in cols_bool_last}
            evany = {c: df[c].values.astype(bool) for c in cols_event_any}
            ssum = {c: df[c].values.astype(float) for c in cols_sum}
        else:
            f = tf_min * 60_000
            grp = (ts // f)
            g = pd.DataFrame({'g': grp})
            idx_last = g.groupby('g').apply(lambda x: x.index[-1]).values  # último M1 idx por grupo
            # OHLC del grupo
            tmp = pd.DataFrame({'g': grp, 'o': o1, 'h': high, 'l': low, 'c': close})
            agg = tmp.groupby('g').agg(o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last'))
            oo, hh, ll, cc = agg['o'].values, agg['h'].values, agg['l'].values, agg['c'].values
            carry = {c: df[c].values[idx_last].astype(float) for c in cols_carry}     # último conocido
            blast = {c: df[c].values[idx_last].astype(bool) for c in cols_bool_last}
            evany = {}
            for c in cols_event_any:
                evany[c] = tmp.assign(v=df[c].values.astype(bool)).groupby('g')['v'].any().values \
                    if False else (pd.Series(df[c].values.astype(bool)).groupby(grp).any().values)
            ssum = {c: pd.Series(df[c].values.astype(float)).groupby(grp).sum().values for c in cols_sum}

        rng = hh - ll
        with np.errstate(all='ignore'):
            wu = (hh - np.maximum(cc, oo)) / np.where(rng > 0, rng, np.nan)
            wl = (np.minimum(cc, oo) - ll) / np.where(rng > 0, rng, np.nan)
        rej_s = (rng > 0) & (wu > 0.30) & (wu < 0.85) & (cc <= oo)
        rej_l = (rng > 0) & (wl > 0.30) & (wl < 0.85) & (cc >= oo)

        def nearH(col):
            lv = carry[col]; return (lv > 0) & (np.abs(hh - lv) / np.where(lv > 0, lv, 1) <= TOL)
        def nearL(col):
            lv = carry[col]; return (lv > 0) & (np.abs(ll - lv) / np.where(lv > 0, lv, 1) <= TOL)
        res = nearH('vp_vah') | nearH('asian_high') | nearH('prev_day_high') | nearH('weekly_high')
        sup = nearL('vp_val') | nearL('asian_low') | nearL('prev_day_low') | nearL('weekly_low')

        # régimen / OF en el idx_last (lo conocido al cierre de la barra TF)
        dbear = d1_bear[idx_last]; dbull = d1_bull[idx_last]
        body_below_poc = (carry['vp_poc'] > 0) & (np.minimum(oo, cc) < carry['vp_poc'])  # cierre bajo POC
        of_s = body_below_poc & (ssum['minus_ticks'] > ssum['plus_ticks']) & ~evany['fp_absorb_buy']
        body_top = np.minimum(oo, cc)
        of_l = ((carry['vp_poc'] <= 0) | (body_top <= carry['vp_poc'])) & \
               (ssum['plus_ticks'] > ssum['minus_ticks']) & ~evany['fp_absorb_sell']
        h1s = blast['h1_bos_bear'] | blast['h1_choch_bear']

        tsg = ts[idx_last]
        hm = (tsg // 60_000) % 1440
        sess = (hm >= 7 * 60) & (hm < 20 * 60); sess_pm = (hm >= 12 * 60) & (hm < 20 * 60)

        setups = {
            'rej@VAH':        ('short', nearH('vp_vah') & rej_s),
            'rej@anyRes':     ('short', res & rej_s),
            'FVG+rej':        ('short', evany['near_bearish_fvg'] & rej_s),
            'OB+rej':         ('short', evany['near_bearish_ob'] & rej_s),
            'Sweep':          ('short', evany['sweep_confirmed']),
            'Displacement':   ('short', evany['displacement_bear']),
            'AbsAsk@Res':     ('short', evany['abs_ask'] & res),
            'rej@VAL':        ('long',  nearL('vp_val') & rej_l),
            'rej@anySup':     ('long',  sup & rej_l),
        }
        out = {}
        for name, (side, mask) in setups.items():
            sm = sess if side == 'short' else sess_pm
            reg = dbear if side == 'short' else dbull
            ofx = of_s if side == 'short' else of_l
            for cfg in ('raw', 'full'):
                mm = mask & sm
                if cfg == 'full': mm = mm & reg & ofx & (h1s if side == 'short' else dbull)
                ei = idx_last[mm]
                tr = sim_short(ei, close, high, low, ts, stop_short) if side == 'short' \
                    else sim_long(ei, close, high, low, ts, stop_long)
                out[(name, cfg)] = (side, stat(tr), len(tr))
        return out

    print(f'Días: {days:.0f}  | exit causal: swing-1h + 0.40·ATR_H1 | target {TARGET_R}R | fee {FEE_RT*100:.2f}% | timeout {FWD_MIN}m')
    results = {tf: run_tf(tf) for tf in (1, 5, 15)}

    names = list(results[1].keys())
    hdr = f'{"setup":<14} {"cfg":<5} {"side":<6}'
    for tf in (1, 5, 15): hdr += f' | M{tf}: IS(n/WR/AvgR)      OOS(n/WR/AvgR)        verdict'
    print('\n' + '-' * 40)
    for (name, cfg) in names:
        line = f'{name:<14} {cfg:<5} {results[1][(name,cfg)][0]:<6}'
        for tf in (1, 5, 15):
            side, ((ni, wi, ai), (no, wo, ao)), nt = results[tf][(name, cfg)]
            v = verdict((ni, wi, ai), (no, wo, ao))
            line += f' | {ni:>4}/{wi:>2.0f}/{ai:>+.2f} {no:>3}/{wo:>2.0f}/{ao:>+.2f} {v:<16}'
        print(line)


if __name__ == '__main__':
    main()
