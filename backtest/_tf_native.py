"""
_tf_native.py — MTF shorts NATIVO en M1/M5/M15 (barras, rechazo, stop, target todo en el TF)
=============================================================================================
A diferencia de _tf_explorer (que detectaba en M5/M15 pero usaba stop M1), acá TODO es nativo del
timeframe: la vela de rechazo, el swing del stop y el timeout se calculan en M1/M5/M15. El stop se
deja ANCHO (hasta 2%) — que es la ventaja real de subir de TF (menos fee-killed). Exit causal, fee real.

Estrategia (shorts MTF): D1 régimen bajista + H1 BOS/ChoCH bear + disparador (rejection@VAH | OB | FVG |
displacement | sweep) + body_below_poc + minus>plus ticks + veto fp_absorb_buy + veto vp_lvn_below.
Stop = swing high ~1h + 0.40·ATR_TF | target 2.8R | timeout 4h | fee 0.11% | IS/OOS 2026-03-01.

Uso: python backtest/_tf_native.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'backtest'))
import mtf_v2 as m

TARGET_R = 2.8
FEE_RT = m.FEE_RT
TOL = m.LEVEL_TOL
OOS_MS = m.OOS_MS
MIN_STOP, MAX_STOP = 0.0015, 0.020   # stop ANCHO permitido (ventaja del TF mayor)
CAP0, RISK = 500.0, 0.02


def atr(h, l, c, n=14):
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1))))
    tr[0] = h[0] - l[0]
    o = np.empty_like(tr); o[:n] = np.nanmean(tr[:n]); k = 1 / n
    for i in range(n, len(tr)): o[i] = o[i-1]*(1-k) + tr[i]*k
    return o


def resample_tf(df, tf_min):
    """M1 → TF: OHLC exacto, ticks/n_trades sum, eventos 'any', niveles/estructura 'last', obi mean."""
    f = tf_min * 60_000
    g = (df['ts_ms'].values // f) * f
    base = pd.DataFrame({'ts_ms': g, 'o': df['open'].values, 'h': df['high'].values,
                         'l': df['low'].values, 'c': df['close'].values})
    agg = base.groupby('ts_ms').agg(open=('o','first'), high=('h','max'), low=('l','min'), close=('c','last'))
    last = ['vp_vah','asian_high','prev_day_high','weekly_high','vp_lvn_below','vp_poc',
            'h1_bos_bear','h1_choch_bear','cvd_slope','obi10_mean']
    anyc = ['near_bearish_ob','near_bearish_fvg','displacement_bear','sweep_confirmed','fp_absorb_buy']
    summ = ['plus_ticks','minus_ticks','n_trades']
    out = agg.copy()
    gb = pd.DataFrame({'g': g})
    for c in last:  out[c] = pd.Series(df[c].values).groupby(g).last().values
    for c in anyc:  out[c] = pd.Series(df[c].values.astype(bool)).groupby(g).any().values
    for c in summ:  out[c] = pd.Series(df[c].values.astype(float)).groupby(g).sum().values
    return out.reset_index()


def run_tf(df, tf_min, d1_ema_map):
    t = resample_tf(df, tf_min)
    ts = t['ts_ms'].values.astype(np.int64)
    o, h, l, c = t['open'].values, t['high'].values, t['low'].values, t['close'].values
    n = len(t)
    a = atr(h, l, c)
    K = max(2, 60 // tf_min)                     # ~1h de swing
    swing_hi = pd.Series(h).rolling(K, min_periods=1).max().values
    fwd_bars = max(1, 240 // tf_min)             # timeout 4h
    # gates
    rng = h - l
    with np.errstate(all='ignore'):
        wu = (h - np.maximum(c, o)) / np.where(rng > 0, rng, np.nan)
    rej = (rng > 0) & (wu > 0.30) & (wu < 0.85) & (c <= o)
    def near(col):
        v = t[col].values; return (v > 0) & (np.abs(h - v) / np.where(v > 0, v, 1) <= TOL)
    trig = ((near('vp_vah') & rej) | (t['near_bearish_ob'].values & rej) |
            (t['near_bearish_fvg'].values & rej) | t['displacement_bear'].values |
            t['sweep_confirmed'].values)
    d1 = np.array([d1_ema_map.get((x // 86_400_000)*86_400_000 - 86_400_000, np.nan) for x in ts])
    d1_bear = np.isfinite(d1) & (c <= d1 * 0.980)
    h1s = t['h1_bos_bear'].values.astype(bool) | t['h1_choch_bear'].values.astype(bool)
    poc = t['vp_poc'].values
    body_below = (poc > 0) & (np.minimum(o, c) < poc)
    aggr = t['minus_ticks'].values > t['plus_ticks'].values
    elig = d1_bear & h1s & trig & body_below & aggr & ~t['fp_absorb_buy'].values.astype(bool) & (t['vp_lvn_below'].values > 0)
    hm = (ts // 60_000) % 1440; sess = (hm >= 7*60) & (hm < 20*60)
    elig = elig & sess

    cand = np.where(elig)[0]; trades = []; next_ok = 0
    cap = CAP0; monthly = CAP0*RISK; cur_mo = -1; peak = cap; dd = 0
    for i in range(n):
        mo = pd.Timestamp(ts[i], unit='ms', tz='UTC').month + pd.Timestamp(ts[i], unit='ms', tz='UTC').year*12
        if mo != cur_mo: monthly = cap*RISK; cur_mo = mo
        if not elig[i] or i < next_ok or i+1 >= n: continue
        ep = c[i]; st = swing_hi[i] + 0.40*a[i]; dist = st - ep
        if dist <= 0: continue
        sp = dist/ep
        if not (MIN_STOP <= sp <= MAX_STOP): continue
        tp = ep - TARGET_R*dist
        reason=None; ex=None; xi=min(i+fwd_bars, n-1)
        for j in range(i+1, min(i+1+fwd_bars, n)):
            if h[j] >= st: reason,ex,xi='stop',st,j; break
            if l[j] <= tp: reason,ex,xi='tp',tp,j; break
        if reason is None: ex=c[xi]
        r = (ep-ex)/dist - FEE_RT*ep/dist
        pnl = monthly*r; cap += pnl
        peak=max(peak,cap); dd=max(dd,(peak-cap)/peak)
        trades.append({'r':r,'oos':int(ts[i])>=OOS_MS,'sp':sp}); next_ok = xi + max(1,15//tf_min)
    return trades, cap, dd


def stat(tr):
    it=[x for x in tr if not x['oos']]; ot=[x for x in tr if x['oos']]
    def s(x):
        if not x: return (0,0,0)
        return (len(x), 100*sum(1 for z in x if z['r']>0)/len(x), float(np.mean([z['r'] for z in x])))
    return s(it), s(ot)


def main():
    print("Cargando M1 causal...")
    df = pd.read_parquet(m.DATA_M1).sort_values('ts_ms').reset_index(drop=True)
    for c in df.select_dtypes('object').columns: df[c]=df[c].fillna('')
    for c in df.select_dtypes('float').columns: df[c]=df[c].fillna(0.0)
    for c in df.select_dtypes('bool').columns: df[c]=df[c].fillna(False)
    d1 = m.resamp(df, 86_400_000); d1['e']=m.ema(d1['close'].values,20)
    d1m = dict(zip(d1['ts_ms'].astype(int), d1['e'].astype(float)))
    days=(df.ts_ms.max()-df.ts_ms.min())/86_400_000
    print(f"Barras M1: {len(df):,} | días {days:.0f} | stop {MIN_STOP*100:.2f}-{MAX_STOP*100:.1f}% | target {TARGET_R}R\n")
    print(f'{"TF":<5} | IS n/WR/AvgR        | OOS n/WR/AvgR       | stop% med | $500→ | MaxDD')
    print('-'*92)
    for tf in (1,5,15):
        tr,cap,dd = run_tf(df, tf, d1m)
        (ni,wi,ai),(no,wo,ao)=stat(tr)
        spmed = np.median([x['sp'] for x in tr])*100 if tr else 0
        print(f'M{tf:<4} | {ni:>4}/{wi:>4.0f}/{ai:>+.3f} | {no:>4}/{wo:>4.0f}/{ao:>+.3f} | {spmed:>6.2f}% | ${cap:>8,.0f} | {dd*100:>4.0f}%')


if __name__ == '__main__':
    main()
