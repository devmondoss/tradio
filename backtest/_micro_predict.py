"""
_micro_predict.py — ¿La microestructura PREDICE el retorno intradía corto? (causal, alto-n)
============================================================================================
Premisa correcta del usuario: la microestructura es un edge de HORIZONTE CORTO. No se mide
con swing ni con 50 trades serializados. Se mide así: para cada señal de microestructura en
la barra T (causal), ¿cuál es el retorno FUTURO a 5/15/30/60 min? Promediado sobre TODAS las
instancias (miles) → escapa al cuello de muestra n≈50.

  signal en T (causal)  →  label = (close[T+h]/close[T] - 1) en bps   [h = 5,15,30,60 min]

Reporta, por señal, el EXCESO sobre el baseline de la sesión (mean fwd ret de todas las
barras de sesión), IS y OOS por separado. Una señal sirve si:
  - IS y OOS COINCIDEN en signo (no flip), y
  - |exceso OOS| supera el costo: RT futuros = 0.11% = 11 bps → necesita >~11 bps para ser
    tradeable en taker (o stop muy ajustado). Marca ✓ si coincide signo y |OOS| >= 6 bps.

Sin lookahead: la señal usa solo info de T; el retorno futuro es el LABEL (lo que se predice).
Thresholds de floats por cuantil IS (sin mirar OOS).

Uso: python backtest/_micro_predict.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'backtest'))
import mtf_v2 as m

HORIZONS = [5, 15, 30, 60]
OOS_MS = m.OOS_MS
RT_BPS = m.FEE_RT * 1e4   # 11 bps


def load():
    df = pd.read_parquet(m.DATA_M1).sort_values('ts_ms').reset_index(drop=True)
    for c in df.select_dtypes('object').columns: df[c] = df[c].fillna('')
    for c in df.select_dtypes('float').columns:  df[c] = df[c].fillna(0.0)
    for c in df.select_dtypes('bool').columns:    df[c] = df[c].fillna(False)
    return df


def main():
    df = load()
    ts = df['ts_ms'].values.astype(np.int64)
    close = df['close'].values.astype(float)
    high = df['high'].values.astype(float); low = df['low'].values.astype(float)
    n = len(close)
    is_mask_all = ts < OOS_MS

    # retornos futuros (bps) por horizonte
    fwd = {}
    for h in HORIZONS:
        f = np.full(n, np.nan)
        f[:n - h] = (close[h:] / close[:n - h] - 1.0) * 1e4
        fwd[h] = f

    # solo barras de sesión (ventana tradeable)
    hm = (ts // 60_000) % 1440
    sess = (hm >= 7 * 60) & (hm < 20 * 60)

    # baseline por sesión (IS y OOS) — el "drift" de fondo
    base = {}
    for h in HORIZONS:
        f = fwd[h]
        bi = np.nanmean(f[sess & is_mask_all]); bo = np.nanmean(f[sess & ~is_mask_all])
        base[h] = (bi, bo)

    # thresholds por cuantil IS
    def qIS(col, q): return float(df[is_mask_all][col].quantile(q))
    dz = df['dz'].values.astype(float)
    obi = df['obi10_mean'].values.astype(float)
    vpin = df['vpin'].values.astype(float)
    fpsd = df['fp_sell_dom'].values.astype(float)
    dz_hi, dz_lo = qIS('dz', 0.90), qIS('dz', 0.10)
    obi_hi, obi_lo = qIS('obi10_mean', 0.90), qIS('obi10_mean', 0.10)
    vpin_hi = qIS('vpin', 0.80)
    fpsd_hi = qIS('fp_sell_dom', 0.90)

    B = lambda c: df[c].values.astype(bool)
    stk = df['stacked_imb'].values
    vah = df['vp_vah'].values.astype(float); val = df['vp_val'].values.astype(float)
    res = (vah > 0) & (np.abs(high - vah) / np.where(vah > 0, vah, 1) <= m.LEVEL_TOL)
    sup = (val > 0) & (np.abs(low - val) / np.where(val > 0, val, 1) <= m.LEVEL_TOL)

    signals = {
        'abs_ask':              B('abs_ask'),
        'abs_bid':              B('abs_bid'),
        'cvd_div':              B('cvd_div'),
        'big_trade_bearish':    B('big_trade_bearish'),
        'big_trade_bullish':    B('big_trade_bullish'),
        'stacked Bearish':      (stk == 'Bearish'),
        'stacked Bullish':      (stk == 'Bullish'),
        'sweep_confirmed':      B('sweep_confirmed'),
        'displacement_bear':    B('displacement_bear'),
        'dz>=Q90 (buy fuerte)': dz >= dz_hi,
        'dz<=Q10 (sell fuerte)':dz <= dz_lo,
        'obi>=Q90 (bids)':      obi >= obi_hi,
        'obi<=Q10 (asks)':      obi <= obi_lo,
        'vpin>=Q80':            vpin >= vpin_hi,
        'cvd_consec_neg>=3':    df['cvd_consec_neg'].values >= 3,
        'cvd_consec_pos>=3':    df['cvd_consec_pos'].values >= 3,
        'fp_absorb_buy':        B('fp_absorb_buy'),
        'fp_absorb_sell':       B('fp_absorb_sell'),
        'fp_sell_dom>=Q90':     fpsd >= fpsd_hi,
        # condicionadas por localización
        'cvd_div & @Res':       B('cvd_div') & res,
        'big_bear & @Res':      B('big_trade_bearish') & res,
        'abs_ask & @Res':       B('abs_ask') & res,
        'abs_bid & @Sup':       B('abs_bid') & sup,
        'big_bull & @Sup':      B('big_trade_bullish') & sup,
    }

    print(f'Baseline sesión (drift de fondo, bps):')
    for h in HORIZONS:
        bi, bo = base[h]; print(f'   h{h:>2}m: IS {bi:+.2f}  OOS {bo:+.2f}', end='   ')
    print(f'\nCosto RT futuros ≈ {RT_BPS:.0f} bps. ✓ = IS/OOS mismo signo y |exceso OOS| ≥ 6 bps.\n')
    hdr = f'{"señal":<22} {"n_is":>6} {"n_oos":>5}'
    for h in HORIZONS: hdr += f' | h{h}m IS/OOS (exceso bps)'
    print(hdr); print('-' * len(hdr))

    for name, mask in signals.items():
        mk = mask & sess
        ni = int((mk & is_mask_all).sum()); no = int((mk & ~is_mask_all).sum())
        line = f'{name:<22} {ni:>6} {no:>5}'
        flag = ''
        for h in HORIZONS:
            f = fwd[h]; bi, bo = base[h]
            ei = np.nanmean(f[mk & is_mask_all]) - bi if ni > 0 else np.nan
            eo = np.nanmean(f[mk & ~is_mask_all]) - bo if no > 0 else np.nan
            line += f' | {ei:>+6.1f}/{eo:>+6.1f}'
            if h in (15, 30) and np.isfinite(ei) and np.isfinite(eo) and np.sign(ei) == np.sign(eo) and abs(eo) >= 6 and no >= 30:
                flag = ' ✓'
        print(line + flag)


if __name__ == '__main__':
    main()
