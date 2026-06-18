"""
ict_setup_test.py
-----------------
Prueba el setup ICT completo sin mining:

  H4 bearish (h4_bearish)
  + Session London_KZ o NY_KZ
  + London barrio Asian High (fib_ote_london implica esto)
  + Precio en OTE 62-79% (fib_ote_london)
  + Body cerro bajo 62% (ote_rejection)
  + Shooting star (is_shoot)
  + Orderflow: stacked_bear | displacement_bear | cvd_div | obi_neg

Stop: H1 structural (H1_high + 0.30xATR_H1)
Target: 2.5R

Uso:
    python backtest/ict_setup_test.py
    python backtest/ict_setup_test.py --tf m15
"""
import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent

TF_CFG = {
    'm5':  (ROOT / 'data/bybit-spot/processed/btcusdt_m5.parquet',  240),
    'm15': (ROOT / 'data/bybit-spot/processed/btcusdt_m15.parquet',  80),
}

TARGET_R     = 2.5
MIN_STOP_PCT = 0.30
MAX_STOP_PCT = 0.75
ATR_BUFF     = 0.30
H1_MS        = 3_600_000
H4_MS        = 4 * H1_MS
D1_MS        = 86_400_000
BREAKEVEN_WR = 1 / (1 + TARGET_R)


def ema_np(vals, period):
    k = 2 / (period + 1)
    out = np.empty(len(vals))
    out[0] = vals[0]
    for i in range(1, len(vals)):
        out[i] = vals[i] * k + out[i - 1] * (1 - k)
    return out


def wilder_atr_np(high, low, close, period=14):
    n = len(high)
    tr = np.empty(n)
    tr[0] = high[0] - low[0]
    tr[1:] = np.maximum(high[1:] - low[1:],
              np.maximum(np.abs(high[1:] - close[:-1]),
                         np.abs(low[1:] - close[:-1])))
    out = np.empty(n)
    out[:period] = tr[:period].mean()
    k = 1 / period
    for i in range(period, n):
        out[i] = out[i - 1] * (1 - k) + tr[i] * k
    return out


def compute_h1_stop(df):
    h1_bucket = (df['ts_ms'].values // H1_MS) * H1_MS
    df2 = df.copy(); df2['h1_ts'] = h1_bucket
    h1 = df2.groupby('h1_ts').agg(
        high=('high','max'), low=('low','min'), close=('close','last')
    ).reset_index().rename(columns={'h1_ts': 'ts_ms'})
    h1['atr14'] = wilder_atr_np(h1['high'].values, h1['low'].values, h1['close'].values)
    h1_map = {row.ts_ms: (row.high, row.atr14) for row in h1.itertuples()}
    n = len(df); stop_price = np.full(n, np.nan); stop_pct = np.full(n, np.nan)
    close = df['close'].values; h1_run_h = {}
    for i in range(n):
        bkt = int(h1_bucket[i])
        h = float(df['high'].iat[i])
        h1_run_h[bkt] = max(h1_run_h.get(bkt, h), h)
        data = h1_map.get(bkt)
        if data is None: continue
        _, atr = data
        sp = h1_run_h[bkt] + ATR_BUFF * atr
        stop_price[i] = sp
        stop_pct[i] = (sp - close[i]) / close[i] * 100
    return stop_price, stop_pct


def simulate(df, mask, stop_price, stop_pct, forward_max):
    """Simula trades para las barras donde mask==True y stop valido."""
    close = df['close'].values
    high  = df['high'].values
    low   = df['low'].values
    ts    = df['ts_ms'].values
    n     = len(df)

    valid = mask & np.isfinite(stop_pct) & (stop_pct >= MIN_STOP_PCT) & (stop_pct <= MAX_STOP_PCT)
    idxs  = np.where(valid)[0]

    records = []
    for i in idxs:
        sp   = stop_price[i]
        risk = sp - close[i]
        if risk <= 0: continue
        target = close[i] - TARGET_R * risk
        end    = min(i + forward_max + 1, n)
        fwd_h  = high[i + 1: end]
        fwd_l  = low[i + 1: end]
        if len(fwd_h) == 0: continue
        sh = fwd_h >= sp; tl = fwd_l <= target
        sb = int(np.argmax(sh)) if sh.any() else forward_max
        tb = int(np.argmax(tl)) if tl.any() else forward_max
        if not sh.any(): sb = forward_max
        if not tl.any(): tb = forward_max
        if tb < sb:
            res_r, oc = TARGET_R, 'target'
        elif sb < forward_max:
            res_r, oc = -1.0, 'stop'
        else:
            res_r = (close[i] - close[min(i + forward_max, n - 1)]) / risk
            oc = 'timeout'
        records.append({'bar_idx': i, 'ts_ms': int(ts[i]),
                        'result_r': round(res_r, 4), 'outcome': oc,
                        'stop_pct': round(float(stop_pct[i]), 4)})
    return pd.DataFrame(records)


def show(label, trades, days_total):
    if len(trades) == 0:
        print(f"  {label:<45}  n=0  (sin trades)")
        return
    n    = len(trades)
    wr   = (trades['result_r'] > 0).sum() / n * 100
    avgr = trades['result_r'].mean()
    tpd  = n / days_total
    totalr = n * avgr
    ok = " <--" if wr >= 45 and tpd >= 0.3 else ""
    print(f"  {label:<45}  n={n:>4}  {tpd:>5.2f}/d  WR={wr:>5.1f}%  "
          f"AvgR={avgr:>+6.3f}  Total={totalr:>+6.1f}R{ok}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tf', choices=['m5', 'm15'], default='m5')
    args = parser.parse_args()

    data_path, forward_max = TF_CFG[args.tf]
    print(f"ICT Setup Test  |  tf={args.tf}  forward={forward_max} barras")

    df = pd.read_parquet(data_path).sort_values('ts_ms').reset_index(drop=True)
    days_total = (df['ts_ms'].max() - df['ts_ms'].min()) / D1_MS
    n = len(df)
    print(f"  {n:,} barras  |  {days_total:.0f} dias")

    # ── Verificar features disponibles ────────────────────────────────────────
    needed = ['session', 'fib_ote_london', 'ote_rejection', 'h4_bearish',
              'stacked_imb', 'displacement_bear', 'cvd_div', 'obi5_mean']
    missing = [c for c in needed if c not in df.columns]
    if missing:
        print(f"  WARN: features faltantes: {missing}")
        print("  Recorre compute_spot_features.py --symbol BTCUSDT_M5 --tf m5 primero")

    # ── Calcular stop H1 ──────────────────────────────────────────────────────
    print("Calculando H1 stop...")
    stop_price, stop_pct = compute_h1_stop(df)

    # ── Construir condiciones ─────────────────────────────────────────────────
    close = df['close'].values
    high  = df['high'].values
    op    = df['open'].values
    rng   = high - df['low'].values + 1e-9
    body  = np.abs(close - op)
    wick_up = high - np.maximum(close, op)

    sess = df['session'].values if 'session' in df.columns else np.full(n, 'OffHours')
    in_kz     = (sess == 'London_KZ') | (sess == 'NY_KZ')
    in_london = (sess == 'London_KZ') | (sess == 'London')
    in_ny     = (sess == 'NY_KZ')     | (sess == 'NewYork')

    # H4 bearish filter (reemplaza D1 EMA20)
    h4_bear = df['h4_bearish'].values.astype(bool) if 'h4_bearish' in df.columns \
              else (close <= ema_np(close, 20) * 1.005)

    # ICT condiciones de estructura
    ote    = df['fib_ote_london'].values.astype(bool) if 'fib_ote_london' in df.columns \
             else np.zeros(n, dtype=bool)
    ote_rej= df['ote_rejection'].values.astype(bool) if 'ote_rejection' in df.columns \
             else np.zeros(n, dtype=bool)

    # Shooting star
    is_shoot = (wick_up / rng > 0.45) & (body / rng < 0.40)

    # Orderflow
    simb      = df['stacked_imb'].values if 'stacked_imb' in df.columns else np.full(n, 'None')
    stk_bear  = simb == 'Bearish'
    disp_bear = df['displacement_bear'].values.astype(bool) if 'displacement_bear' in df.columns \
                else np.zeros(n, dtype=bool)
    cvd_div   = df['cvd_div'].values.astype(bool) if 'cvd_div' in df.columns \
                else np.zeros(n, dtype=bool)
    obi       = df['obi5_mean'].values if 'obi5_mean' in df.columns else np.zeros(n)
    obi_neg   = obi < -0.05

    h4_ob     = df['h4_ob_zone'].values.astype(bool) if 'h4_ob_zone' in df.columns \
                else np.zeros(n, dtype=bool)
    h4_fvg    = df['h4_fvg_zone'].values.astype(bool) if 'h4_fvg_zone' in df.columns \
                else np.zeros(n, dtype=bool)
    h4_bos    = df['h4_bos_bear'].values.astype(bool) if 'h4_bos_bear' in df.columns \
                else np.zeros(n, dtype=bool)

    of_any    = stk_bear | disp_bear | cvd_div | obi_neg  # cualquier orderflow
    of_strong = stk_bear | disp_bear                       # orderflow fuerte

    # ── Frecuencia de cada condicion ──────────────────────────────────────────
    sep = "-" * 72
    print(f"\n{sep}")
    print("  FRECUENCIA DE CONDICIONES CLAVE (sobre total de barras)")
    print(sep)
    for label, mask in [
        ("in_kz (London_KZ + NY_KZ)",   in_kz),
        ("h4_bearish",                   h4_bear),
        ("fib_ote_london",               ote),
        ("ote_rejection",                ote_rej),
        ("is_shoot",                     is_shoot),
        ("stacked_bear",                 stk_bear),
        ("displacement_bear",            disp_bear),
        ("cvd_div",                      cvd_div),
        ("obi_neg",                      obi_neg),
        ("h4_ob_zone",                   h4_ob),
        ("h4_fvg_zone",                  h4_fvg),
        ("h4_bos_bear",                  h4_bos),
        ("london_swept (ote valido)",    ote | ote_rej),
    ]:
        cnt = mask.sum()
        pct = cnt / n * 100
        tpd = cnt / days_total
        print(f"  {label:<40} {cnt:>6} ({pct:>5.1f}%)  {tpd:.1f}/dia")

    # ── Simular setups ────────────────────────────────────────────────────────
    print(f"\n{sep}")
    print("  RESULTADOS POR SETUP (H1 stop, 2.5R target)")
    print(sep)
    print(f"  {'setup':<45}  {'n':>4}  {'/dia':>5}  {'WR%':>6}  {'AvgR':>7}  {'Total':>7}")
    print(sep)

    t0 = time.time()

    setups = [
        # Baseline: solo killzone + H4 bearish
        ("BASE: kz + h4_bear",
         in_kz & h4_bear),

        # Estructura ICT core
        ("OTE: kz + h4 + ote_london",
         in_kz & h4_bear & ote),

        ("OTE_REJ: kz + h4 + ote_rej",
         in_kz & h4_bear & ote_rej),

        # OTE + trigger de vela
        ("OTE + shoot",
         in_kz & h4_bear & ote & is_shoot),

        ("OTE_REJ + shoot",
         in_kz & h4_bear & ote_rej & is_shoot),

        # OTE + orderflow
        ("OTE + stk_bear",
         in_kz & h4_bear & ote & stk_bear),

        ("OTE + of_any",
         in_kz & h4_bear & ote & of_any),

        ("OTE_REJ + of_any",
         in_kz & h4_bear & ote_rej & of_any),

        # Setup completo ICT
        ("FULL: kz+h4+ote+ote_rej+shoot+stk",
         in_kz & h4_bear & ote & ote_rej & is_shoot & stk_bear),

        ("FULL: kz+h4+ote+ote_rej+shoot+of_any",
         in_kz & h4_bear & ote & ote_rej & is_shoot & of_any),

        ("FULL: kz+h4+ote_rej+shoot+of_any",
         in_kz & h4_bear & ote_rej & is_shoot & of_any),

        # Variantes con H4 confluencia
        ("OTE + h4_ob",
         in_kz & h4_bear & ote & h4_ob),

        ("OTE_REJ + h4_ob + of_any",
         in_kz & h4_bear & ote_rej & h4_ob & of_any),

        ("OTE + h4_fvg + of_any",
         in_kz & h4_bear & ote & h4_fvg & of_any),

        # Sin requerir OTE (solo KZ + H4 + confluencia)
        ("kz + h4 + shoot + stk_bear",
         in_kz & h4_bear & is_shoot & stk_bear),

        ("kz + h4 + shoot + of_strong",
         in_kz & h4_bear & is_shoot & of_strong),

        ("kz + h4 + shoot + of_any",
         in_kz & h4_bear & is_shoot & of_any),

        # Por killzone especifica
        ("London_KZ only + h4 + ote",
         (sess == 'London_KZ') & h4_bear & ote),

        ("NY_KZ only + h4 + ote",
         (sess == 'NY_KZ') & h4_bear & ote),

        ("London_KZ + h4 + ote_rej + of_any",
         (sess == 'London_KZ') & h4_bear & ote_rej & of_any),

        ("NY_KZ + h4 + ote_rej + of_any",
         (sess == 'NY_KZ') & h4_bear & ote_rej & of_any),
    ]

    for label, mask in setups:
        trades = simulate(df, mask, stop_price, stop_pct, forward_max)
        show(label, trades, days_total)

    print(f"\n  Tiempo total: {time.time() - t0:.1f}s")
    print(f"  Breakeven WR = {BREAKEVEN_WR * 100:.1f}%")

    # ── Mejor setup — detalle mensual ─────────────────────────────────────────
    # Evaluar el setup completo en detalle
    best_mask = in_kz & h4_bear & ote_rej & is_shoot & of_any
    best_trades = simulate(df, best_mask, stop_price, stop_pct, forward_max)

    if len(best_trades) > 0:
        print(f"\n{sep}")
        print("  DETALLE: FULL ICT (kz+h4+ote_rej+shoot+of_any)")
        print(sep)
        n_t  = len(best_trades)
        wr   = (best_trades['result_r'] > 0).sum() / n_t * 100
        avgr = best_trades['result_r'].mean()
        print(f"  Trades: {n_t}  |  WR: {wr:.1f}%  |  AvgR: {avgr:+.3f}  "
              f"|  Total: {n_t*avgr:.1f}R  |  {n_t/days_total:.2f}/dia")

        best_trades['month'] = pd.to_datetime(
            best_trades['ts_ms'], unit='ms', utc=True
        ).dt.strftime('%Y-%m')

        print(f"\n  {'mes':<8}  {'n':>4}  {'WR%':>5}  {'AvgR':>7}  {'Total_R':>8}")
        for mo, grp in best_trades.groupby('month'):
            wr_m  = (grp['result_r'] > 0).sum() / len(grp) * 100
            avgr_m = grp['result_r'].mean()
            print(f"  {mo}  {len(grp):>4}  {wr_m:>5.0f}%  {avgr_m:>+7.3f}  "
                  f"{len(grp)*avgr_m:>+8.1f}R")

        for oc in ['target', 'stop', 'timeout']:
            sub = best_trades[best_trades['outcome'] == oc]
            if len(sub):
                print(f"  {oc:<10}: {len(sub)} ({len(sub)/n_t*100:.0f}%)")


if __name__ == '__main__':
    main()
