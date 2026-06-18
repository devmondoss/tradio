"""
mine_spot_patterns.py
---------------------
Minería de patrones para BTC SPOT shorts.

Metodología:
  1. Precomputa H1 stop/target para cada barra (sin look-ahead).
  2. Simula forward hasta 1200 barras -> result_r por barra candidata.
  3. Testa miles de combinaciones de features como condiciones de entrada.
  4. Ordena por WR × AvgR (equity proxy) filtrando n >= MIN_N.

Sin cooldown en la minería (fase exploratoria) — se aplica en los finalistas.

Uso:
    python backtest/mine_spot_patterns.py
    python backtest/mine_spot_patterns.py --min-n 15 --top 50 --out exports/mining.csv
    python backtest/mine_spot_patterns.py --depth 3      # triples (más lento)
"""

import argparse
import json
import sys
import time
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

ROOT      = Path(__file__).parent.parent
DATA_FILE = ROOT / 'data/bybit-spot/processed/btcusdt_m1.parquet'
EXPORTS   = ROOT / 'exports'

# ── Parámetros del sistema (spec congelada) ────────────────────────────────────
TARGET_R     = 2.5
MIN_STOP_PCT = 0.30
MAX_STOP_PCT = 0.75
FORWARD_MAX  = 1200
ATR_BUFF     = 0.30   # stop = H1_high + 0.30 × ATR_H1
MIN_N        = 20     # mínimo trades para considerar un patrón
H1_MS        = 3_600_000
D1_MS        = 86_400_000


# ── EMA / ATR helpers ─────────────────────────────────────────────────────────

def ema_np(vals: np.ndarray, period: int) -> np.ndarray:
    k = 2 / (period + 1)
    out = np.empty(len(vals))
    out[0] = vals[0]
    for i in range(1, len(vals)):
        out[i] = vals[i] * k + out[i - 1] * (1 - k)
    return out


def wilder_atr_np(high, low, close, period=14):
    n   = len(high)
    tr  = np.empty(n)
    tr[0] = high[0] - low[0]
    tr[1:] = np.maximum(high[1:] - low[1:],
              np.maximum(np.abs(high[1:] - close[:-1]),
                         np.abs(low[1:]  - close[:-1])))
    out = np.empty(n)
    out[:period] = tr[:period].mean()
    k = 1 / period
    for i in range(period, n):
        out[i] = out[i - 1] * (1 - k) + tr[i] * k
    return out


# ── H1 stop precomputation ────────────────────────────────────────────────────

def compute_h1_stop(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """
    Para cada barra M1, calcula stop y stop_pct usando la estructura H1.
    H1_high = máximo de todas las M1 del bucket H1 (sin look-ahead: vemos el H1 en curso).
    H1_ATR  = ATR(14) Wilder sobre H1 resampled.

    Retorna (stop_price, stop_pct) — nan donde el H1 no tiene datos suficientes.
    """
    h1_bucket = (df['ts_ms'].values // H1_MS) * H1_MS

    # Resample a H1
    df2 = df.copy()
    df2['h1_ts'] = h1_bucket
    h1 = df2.groupby('h1_ts').agg(
        high  =('high',  'max'),
        low   =('low',   'min'),
        close =('close', 'last'),
    ).reset_index().rename(columns={'h1_ts': 'ts_ms'})

    h1['atr14'] = wilder_atr_np(h1['high'].values, h1['low'].values,
                                 h1['close'].values, period=14)
    h1_map = {row.ts_ms: (row.high, row.atr14) for row in h1.itertuples()}

    n          = len(df)
    stop_price = np.full(n, np.nan)
    stop_pct   = np.full(n, np.nan)
    close      = df['close'].values
    ts_ms      = df['ts_ms'].values

    # Rolling H1_high en curso (sin look-ahead): max de M1s del mismo bucket H1 hasta aquí
    h1_running_high: dict[int, float] = {}

    for i in range(n):
        bkt = int(h1_bucket[i])
        h   = float(df['high'].iat[i])
        h1_running_high[bkt] = max(h1_running_high.get(bkt, h), h)

        h1_data = h1_map.get(bkt)
        if h1_data is None:
            continue
        _, atr = h1_data

        sp = h1_running_high[bkt] + ATR_BUFF * atr
        rk = (sp - close[i]) / close[i] * 100
        stop_price[i] = sp
        stop_pct[i]   = rk

    return stop_price, stop_pct


# ── Forward simulation ─────────────────────────────────────────────────────────

def simulate_outcomes(df: pd.DataFrame, stop_price: np.ndarray,
                      stop_pct: np.ndarray) -> pd.DataFrame:
    """
    Para cada barra con stop_pct en rango válido, simula el trade hacia adelante.
    Retorna DataFrame con columnas: [bar_idx, result_r, outcome, stop_pct].
    Sin cooldown — la minería lo ignora para maximizar la muestra.
    """
    close  = df['close'].values
    high   = df['high'].values
    low    = df['low'].values
    ts_ms  = df['ts_ms'].values
    n      = len(df)

    # Filtro base: stop_pct válido y sesión permitida
    valid_sess = df['session'].isin({'London', 'Overlap', 'NewYork'}).values \
                 if 'session' in df.columns else np.ones(n, dtype=bool)

    # Filtro D1: no bull fuerte (precio > EMA20 × 1.005 -> bloqueado)
    if 'ema20' in df.columns:
        ema20 = df['ema20'].values
        d1_not_bull = close <= ema20 * 1.005
    else:
        ema20 = ema_np(close, 20)
        d1_not_bull = close <= ema20 * 1.005

    valid_stop = (
        np.isfinite(stop_pct) &
        (stop_pct >= MIN_STOP_PCT) &
        (stop_pct <= MAX_STOP_PCT)
    )
    candidate_mask = valid_sess & d1_not_bull & valid_stop

    candidates = np.where(candidate_mask)[0]
    print(f"  Candidatos válidos: {len(candidates):,} / {n:,} barras")

    records = []
    t0 = time.time()
    for k, i in enumerate(candidates):
        if k % 20000 == 0 and k > 0:
            elapsed = time.time() - t0
            eta = elapsed / k * (len(candidates) - k)
            print(f"    {k:,}/{len(candidates):,}  ETA: {eta:.0f}s", end='\r')

        sp     = stop_price[i]
        risk   = sp - close[i]
        if risk <= 0:
            continue
        target = close[i] - TARGET_R * risk

        end   = min(i + FORWARD_MAX + 1, n)
        fwd_h = high[i + 1: end]
        fwd_l = low[i + 1:  end]

        if len(fwd_h) == 0:
            continue

        # Primer bar donde high >= stop
        stop_hits = fwd_h >= sp
        tgt_hits  = fwd_l <= target

        stop_bar = int(np.argmax(stop_hits)) if stop_hits.any() else FORWARD_MAX
        tgt_bar  = int(np.argmax(tgt_hits))  if tgt_hits.any()  else FORWARD_MAX

        if not stop_hits.any(): stop_bar = FORWARD_MAX
        if not tgt_hits.any():  tgt_bar  = FORWARD_MAX

        if tgt_bar < stop_bar:
            res_r   = TARGET_R
            outcome = 'target'
        elif stop_bar < FORWARD_MAX:
            res_r   = -1.0
            outcome = 'stop'
        else:
            # Timeout: resultado al precio de cierre de la última barra
            last_close = close[min(i + FORWARD_MAX, n - 1)]
            res_r   = (close[i] - last_close) / risk
            outcome = 'timeout'

        records.append({
            'bar_idx':   i,
            'ts_ms':     int(ts_ms[i]),
            'result_r':  round(res_r, 4),
            'outcome':   outcome,
            'stop_pct':  round(float(stop_pct[i]), 4),
        })

    print(f"\n  Simulados: {len(records):,} trades en {time.time()-t0:.1f}s")
    return pd.DataFrame(records)


# ── Definición de condiciones ──────────────────────────────────────────────────

def build_conditions(df: pd.DataFrame) -> dict[str, np.ndarray]:
    """
    Devuelve dict nombre->array bool (True=condición activa en esa barra).
    """
    conds: dict[str, np.ndarray] = {}
    n = len(df)

    def col(name, default=None):
        if name in df.columns:
            return df[name].values
        if default is not None:
            return np.full(n, default)
        return None

    # ── Patrones de vela ──────────────────────────────────────────────────────
    rng  = df['high'].values - df['low'].values + 1e-9
    body = np.abs(df['close'].values - df['open'].values)
    wick_up = df['high'].values - np.maximum(df['close'].values, df['open'].values)
    wick_dn = np.minimum(df['close'].values, df['open'].values) - df['low'].values

    conds['is_shoot']  = (wick_up / rng > 0.45) & (body / rng < 0.40)
    conds['is_hammer'] = (wick_dn / rng > 0.45) & (body / rng < 0.40)

    # ── Sesiones ──────────────────────────────────────────────────────────────
    sess = col('session', 'OffHours')
    conds['sess_london']  = sess == 'London'
    conds['sess_overlap'] = sess == 'Overlap'
    conds['sess_ny']      = sess == 'NewYork'

    # ── Regime ────────────────────────────────────────────────────────────────
    reg = col('regime', 'Chop')
    conds['reg_trendup']      = reg == 'TrendUp'
    conds['reg_trenddown']    = reg == 'TrendDown'
    conds['reg_expansion']    = reg == 'Expansion'
    conds['reg_not_trenddown'] = reg != 'TrendDown'

    # ── Orderflow booleans ────────────────────────────────────────────────────
    for feat in ('abs_ask', 'abs_bid', 'cvd_div', 'sweep_confirmed',
                 'equal_high', 'equal_low', 'big_trade_bearish', 'big_trade_bullish',
                 'thin_above', 'thin_below', 'bid_wall', 'ask_wall'):
        v = col(feat, False)
        if v is not None:
            conds[feat] = v.astype(bool)

    # ── Stacked imbalance ─────────────────────────────────────────────────────
    simb = col('stacked_imb', 'None')
    conds['stacked_bear'] = simb == 'Bearish'
    conds['stacked_bull'] = simb == 'Bullish'

    # ── OBI thresholds ────────────────────────────────────────────────────────
    obi = col('obi5_mean', col('obi10_mean', np.zeros(n)))
    for thr in [0.05, 0.10, 0.15, 0.20]:
        conds[f'obi_neg{int(thr*100):02d}'] = obi < -thr
        conds[f'obi_pos{int(thr*100):02d}'] = obi >  thr

    obi_range = col('obi_range', np.zeros(n))
    conds['obi_range_wide'] = obi_range > 0.25

    # ── Volume ratio ─────────────────────────────────────────────────────────
    vr = col('vr', np.ones(n))
    for thr in [1.2, 1.5, 2.0, 3.0]:
        conds[f'vr_gt{str(thr).replace(".","p")}'] = vr > thr

    # ── Delta z-score ─────────────────────────────────────────────────────────
    dz = col('dz', np.zeros(n))
    conds['dz_buyers']    = dz >  0.5   # compradores activos (ICT: buen short)
    conds['dz_buyers_1']  = dz >  1.0
    conds['dz_sellers']   = dz < -0.5
    conds['dz_sellers_1'] = dz < -1.0

    # ── VPIN ─────────────────────────────────────────────────────────────────
    vpin = col('vpin', np.full(n, 0.5))
    conds['vpin_toxic']  = vpin > 0.60
    conds['vpin_neutral'] = (vpin >= 0.40) & (vpin <= 0.60)

    # ── CVD context ───────────────────────────────────────────────────────────
    cvd_neg = col('cvd_consec_neg', np.zeros(n))
    cvd_pos = col('cvd_consec_pos', np.zeros(n))
    conds['cvd_mom_3neg'] = cvd_neg >= 3
    conds['cvd_mom_5neg'] = cvd_neg >= 5
    conds['cvd_pos_pre']  = cvd_pos >= 2   # compradores previos (ICT: liquidity hunt)
    conds['cvd_pos3_pre'] = cvd_pos >= 3

    prev_d = col('prev_bar_delta', np.zeros(n))
    conds['prev_bear_bar'] = prev_d < 0
    conds['prev_bull_bar'] = prev_d > 0

    # ── Volume compression / expansion ───────────────────────────────────────
    bslvr = col('bars_since_low_vr', np.full(n, 9999))
    conds['post_compression'] = (bslvr >= 1) & (bslvr <= 5)
    conds['open_momentum']    = bslvr > 15

    # ── Spread ────────────────────────────────────────────────────────────────
    spread = col('spread_mean', np.zeros(n))
    conds['spread_tight'] = spread < 1.5
    conds['spread_wide']  = spread > 2.5

    # ── VP levels ─────────────────────────────────────────────────────────────
    poc = col('vp_poc')
    val = col('vp_val')
    cl  = df['close'].values
    if poc is not None:
        conds['below_poc'] = cl < poc
        conds['above_poc'] = cl > poc
    if val is not None:
        conds['val_near'] = np.abs(cl - val) / (cl + 1e-9) < 0.005

    # ── ICT structure ─────────────────────────────────────────────────────────
    pdh = col('prev_day_high')
    vwap = col('vwap')
    if pdh is not None:
        conds['near_pdh'] = np.abs(cl - pdh) / (cl + 1e-9) < 0.003
    if vwap is not None:
        conds['above_vwap'] = cl > vwap
        conds['below_vwap'] = cl < vwap

    # ── Big trade ─────────────────────────────────────────────────────────────
    bt_bear = col('big_trade_bearish', False)
    if bt_bear is not None:
        conds['big_trade_bearish'] = bt_bear.astype(bool)

    # ── ICT structure features (FVG, OTE, OB, sweeps HTF, weekly) ─────────────
    for feat in ('fib_ote', 'near_weekly_high', 'near_asian_high',
                 'pdh_sweep', 'equal_high_sweep', 'tight_range',
                 'bearish_fvg_active', 'near_bearish_fvg', 'near_bearish_ob'):
        v = col(feat, False)
        if v is not None:
            conds[feat] = v.astype(bool)

    return conds


# ── Estadísticas de un patrón ─────────────────────────────────────────────────

def pattern_stats(outcomes: pd.DataFrame, mask: np.ndarray,
                  label: str) -> dict | None:
    """
    Dado un mask booleano sobre los índices de outcomes, calcula estadísticas.
    """
    idx = outcomes['bar_idx'].values
    matched = mask[idx]
    sub = outcomes[matched]

    n = len(sub)
    if n < MIN_N:
        return None

    wins  = (sub['result_r'] > 0).sum()
    wr    = wins / n * 100
    avg_r = sub['result_r'].mean()
    tot_r = sub['result_r'].sum()

    return {
        'pattern': label,
        'n':       n,
        'wins':    int(wins),
        'wr':      round(wr, 1),
        'avg_r':   round(avg_r, 3),
        'total_r': round(tot_r, 2),
        'score':   round(wr * avg_r, 2),  # proxy de equity
    }


# ── Mining ────────────────────────────────────────────────────────────────────

def mine(outcomes: pd.DataFrame, conds: dict[str, np.ndarray],
         depth: int = 2, top_singles: int = 30) -> list[dict]:
    """
    Profundidad 1: prueba cada condición individual.
    Profundidad 2: prueba pares (best singles × todas las condiciones).
    Profundidad 3: triples (best pairs × todas las condiciones).
    """
    results = []
    cond_names = list(conds.keys())
    n_conds = len(cond_names)

    # ── Depth 1: singles ──────────────────────────────────────────────────────
    print(f"  Mining depth=1: {n_conds} condiciones...")
    singles = []
    for name in cond_names:
        mask  = conds[name]
        stats = pattern_stats(outcomes, mask, name)
        if stats:
            results.append(stats)
            singles.append((name, mask, stats['score']))

    singles.sort(key=lambda x: -x[2])
    print(f"    -> {len(singles)} condiciones con n>={MIN_N}")

    if depth < 2:
        return results

    # ── Depth 2: pares (top singles × todas) ─────────────────────────────────
    top_s = singles[:top_singles]
    total_pairs = len(top_s) * n_conds
    print(f"  Mining depth=2: {total_pairs:,} pares...")
    pairs = []
    for i, (n1, m1, _) in enumerate(top_s):
        for n2, m2 in [(x, conds[x]) for x in cond_names if x != n1]:
            mask  = m1 & m2
            label = f"{n1} + {n2}"
            stats = pattern_stats(outcomes, mask, label)
            if stats:
                results.append(stats)
                pairs.append((label, mask, stats['score']))

    pairs.sort(key=lambda x: -x[2])
    print(f"    -> {len(pairs)} pares con n>={MIN_N}")

    if depth < 3:
        return results

    # ── Depth 3: triples (top pairs × top singles) ───────────────────────────
    top_p = pairs[:20]
    total_triples = len(top_p) * n_conds
    print(f"  Mining depth=3: {total_triples:,} triples...")
    for n12, m12, _ in top_p:
        for n3, m3 in [(x, conds[x]) for x in cond_names]:
            if n3 in n12:
                continue
            mask  = m12 & m3
            label = f"{n12} + {n3}"
            stats = pattern_stats(outcomes, mask, label)
            if stats:
                results.append(stats)

    return results


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    global MIN_N
    parser = argparse.ArgumentParser()
    parser.add_argument('--min-n',  type=int, default=MIN_N,
                        help='Mínimo n de trades para reportar un patrón')
    parser.add_argument('--top',    type=int, default=50,
                        help='Cuántos patrones mostrar en consola')
    parser.add_argument('--depth',  type=int, default=2, choices=[1, 2, 3],
                        help='Profundidad: 1=singles, 2=pares, 3=triples')
    parser.add_argument('--days',   type=int, default=0,
                        help='Limitar a últimos N días (0 = todos)')
    parser.add_argument('--out',    default='',
                        help='Guardar CSV en este path')
    args = parser.parse_args()
    MIN_N = args.min_n

    if not DATA_FILE.exists():
        sys.exit(f"No existe {DATA_FILE}")

    print(f"Cargando {DATA_FILE.name}...")
    df = pd.read_parquet(DATA_FILE).sort_values('ts_ms').reset_index(drop=True)

    if args.days > 0:
        cutoff = df['ts_ms'].max() - args.days * D1_MS
        df = df[df['ts_ms'] >= cutoff].reset_index(drop=True)
        print(f"  Limitado a últimos {args.days} días: {len(df):,} barras")

    print(f"  {len(df):,} barras  |  {len(df.columns)} columnas")

    # ── Precomputa stop H1 ───────────────────────────────────────────────────
    print("\n[1/3] Calculando stop H1 por barra...")
    t0 = time.time()
    stop_price, stop_pct = compute_h1_stop(df)
    print(f"  Listo en {time.time()-t0:.1f}s")

    # ── Simula outcomes ───────────────────────────────────────────────────────
    print("\n[2/3] Simulando outcomes (forward 1200 barras)...")
    t0 = time.time()
    outcomes = simulate_outcomes(df, stop_price, stop_pct)

    if outcomes.empty:
        sys.exit("No hay candidatos válidos.")

    n_tot  = len(outcomes)
    wins   = (outcomes['result_r'] > 0).sum()
    avg_r  = outcomes['result_r'].mean()
    print(f"\n  BASE (sin filtro de patrón): n={n_tot:,}  WR={wins/n_tot*100:.1f}%  AvgR={avg_r:.3f}")
    print(f"  Simulación total: {time.time()-t0:.1f}s")

    # ── Construye condiciones ─────────────────────────────────────────────────
    print("\n[3/3] Mining de patrones...")
    conds   = build_conditions(df)
    n_conds = len(conds)
    print(f"  {n_conds} condiciones definidas")

    t0 = time.time()
    results = mine(outcomes, conds, depth=args.depth)
    print(f"  Mining completado en {time.time()-t0:.1f}s")
    print(f"  Total patrones con n>={MIN_N}: {len(results):,}")

    # ── Resultados ────────────────────────────────────────────────────────────
    df_res = pd.DataFrame(results).drop_duplicates('pattern')
    df_res = df_res.sort_values('score', ascending=False).reset_index(drop=True)

    print(f"\n{'-'*80}")
    print(f"  TOP {args.top} PATRONES  (score = WR x AvgR)")
    print(f"{'-'*80}")
    print(f"  {'#':>3}  {'WR%':>6}  {'AvgR':>7}  {'n':>5}  {'score':>7}  patron")
    print(f"{'-'*80}")
    for i, row in df_res.head(args.top).iterrows():
        print(f"  {i+1:>3}  {row['wr']:>6.1f}%  {row['avg_r']:>7.3f}  "
              f"{row['n']:>5}  {row['score']:>7.2f}  {row['pattern']}")

    # ── Guardar ───────────────────────────────────────────────────────────────
    EXPORTS.mkdir(exist_ok=True)
    out_path = Path(args.out) if args.out else EXPORTS / 'spot_patterns_mining.csv'
    df_res.to_csv(out_path, index=False)
    print(f"\nGuardado: {out_path}  ({len(df_res):,} patrones)")

    # Stats de finalistas
    print(f"\n  Patrones con WR>55% y AvgR>0.30 y n>={MIN_N}:")
    finalists = df_res[(df_res['wr'] > 55) & (df_res['avg_r'] > 0.30)]
    print(f"  -> {len(finalists)} candidatos")
    if not finalists.empty:
        print(finalists[['pattern','n','wr','avg_r','score']].head(20).to_string(index=False))


if __name__ == '__main__':
    main()
