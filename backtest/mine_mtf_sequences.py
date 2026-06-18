"""
mine_mtf_sequences.py
---------------------
Mineria de PATRONES MULTI-BARRA para BTC SPOT shorts.
Respeta todos los features del spec MTF (MTF_STRATEGY_RULES.md).

Diferencias vs mine_spot_patterns.py:
  - Condiciones de LAG: feature verdadero N barras atras (setup -> trigger)
  - Condiciones RECENT: feature verdadero en cualquiera de las ultimas N barras
  - Detectores de SECUENCIA compuesta (compression->burst, eqh->sweep->entry, etc.)
  - Score = n x avg_r  (R total ganado = maximiza PnL)
  - Umbral: WR > 35% AND avg_r > 0.10 AND n >= MIN_N
  - Objetivo: 2-5 trades/dia con EV positivo

Uso:
    python backtest/mine_mtf_sequences.py
    python backtest/mine_mtf_sequences.py --min-n 100 --top 80 --depth 2
    python backtest/mine_mtf_sequences.py --min-n 50  --top 100 --depth 3
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT    = Path(__file__).parent.parent
EXPORTS = ROOT / 'exports'

# Configuración por timeframe: (archivo, FORWARD_MAX en barras ~20h, cooldown barras ~30min)
TF_CONFIG = {
    'm1':  (ROOT / 'data/bybit-spot/processed/btcusdt_m1.parquet',   1200, 30),
    'm5':  (ROOT / 'data/bybit-spot/processed/btcusdt_m5.parquet',    240,  6),
    'm15': (ROOT / 'data/bybit-spot/processed/btcusdt_m15.parquet',    80,  2),
}
DATA_FILE = TF_CONFIG['m1'][0]  # default, se sobreescribe en main()

TARGET_R     = 2.5
MIN_STOP_PCT = 0.30
MAX_STOP_PCT = 0.75
FORWARD_MAX  = 1200   # se ajusta según --tf en main()
ATR_BUFF     = 0.30
MIN_N        = 100
H1_MS        = 3_600_000
D1_MS        = 86_400_000
BREAKEVEN_WR = 1 / (1 + TARGET_R)   # 28.57% con 2.5R


# ── helpers reutilizados del pipeline principal ───────────────────────────────

def ema_np(vals, period):
    k = 2 / (period + 1)
    out = np.empty(len(vals))
    out[0] = vals[0]
    for i in range(1, len(vals)):
        out[i] = vals[i] * k + out[i - 1] * (1 - k)
    return out


def wilder_atr_np(high, low, close, period=14):
    n  = len(high)
    tr = np.empty(n)
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


def compute_h1_stop(df):
    h1_bucket = (df['ts_ms'].values // H1_MS) * H1_MS
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
    h1_run_h   = {}

    for i in range(n):
        bkt = int(h1_bucket[i])
        h   = float(df['high'].iat[i])
        h1_run_h[bkt] = max(h1_run_h.get(bkt, h), h)
        data = h1_map.get(bkt)
        if data is None:
            continue
        _, atr = data
        sp = h1_run_h[bkt] + ATR_BUFF * atr
        rk = (sp - close[i]) / close[i] * 100
        stop_price[i] = sp
        stop_pct[i]   = rk

    return stop_price, stop_pct


def simulate_outcomes(df, stop_price, stop_pct):
    close   = df['close'].values
    high    = df['high'].values
    low     = df['low'].values
    ts_ms   = df['ts_ms'].values
    n       = len(df)

    valid_sess = df['session'].isin({'London', 'Overlap', 'NewYork'}).values \
                 if 'session' in df.columns else np.ones(n, dtype=bool)

    ema20 = df['ema20'].values if 'ema20' in df.columns else ema_np(close, 20)
    d1_ok = close <= ema20 * 1.005

    valid_stop = (
        np.isfinite(stop_pct) &
        (stop_pct >= MIN_STOP_PCT) &
        (stop_pct <= MAX_STOP_PCT)
    )
    candidate_mask = valid_sess & d1_ok & valid_stop
    candidates = np.where(candidate_mask)[0]
    print(f"  Candidatos validos: {len(candidates):,} / {n:,} barras")

    records = []
    t0 = time.time()
    for k, i in enumerate(candidates):
        if k % 20000 == 0 and k > 0:
            elapsed = time.time() - t0
            eta     = elapsed / k * (len(candidates) - k)
            print(f"    {k:,}/{len(candidates):,}  ETA:{eta:.0f}s", end='\r')

        sp   = stop_price[i]
        risk = sp - close[i]
        if risk <= 0:
            continue
        target = close[i] - TARGET_R * risk

        end   = min(i + FORWARD_MAX + 1, n)
        fwd_h = high[i + 1: end]
        fwd_l = low[i + 1:  end]
        if len(fwd_h) == 0:
            continue

        stop_hits = fwd_h >= sp
        tgt_hits  = fwd_l <= target
        stop_bar  = int(np.argmax(stop_hits)) if stop_hits.any() else FORWARD_MAX
        tgt_bar   = int(np.argmax(tgt_hits))  if tgt_hits.any()  else FORWARD_MAX
        if not stop_hits.any(): stop_bar = FORWARD_MAX
        if not tgt_hits.any():  tgt_bar  = FORWARD_MAX

        if tgt_bar < stop_bar:
            res_r, outcome = TARGET_R, 'target'
        elif stop_bar < FORWARD_MAX:
            res_r, outcome = -1.0, 'stop'
        else:
            last_close = close[min(i + FORWARD_MAX, n - 1)]
            res_r = (close[i] - last_close) / risk
            outcome = 'timeout'

        records.append({
            'bar_idx':  i,
            'ts_ms':    int(ts_ms[i]),
            'result_r': round(res_r, 4),
            'outcome':  outcome,
            'stop_pct': round(float(stop_pct[i]), 4),
        })

    print(f"\n  Simulados: {len(records):,} trades en {time.time()-t0:.1f}s")
    return pd.DataFrame(records)


# ── Construccion de condiciones MULTI-BARRA ────────────────────────────────────

def _lag(arr: np.ndarray, k: int) -> np.ndarray:
    """Feature verdadero k barras atras (sin look-ahead)."""
    result = np.roll(arr.astype(np.float32), k)
    result[:k] = 0.0
    return result.astype(bool)


def _recent(arr: np.ndarray, window: int) -> np.ndarray:
    """Feature verdadero en cualquiera de las ultimas `window` barras."""
    return (pd.Series(arr.astype(np.float32))
              .rolling(window, min_periods=1)
              .max()
              .astype(bool)
              .values)


def build_sequence_conditions(df: pd.DataFrame) -> dict[str, np.ndarray]:
    """
    Devuelve dict nombre->array bool.
    Incluye condiciones de barra actual, lags, rolling-recent y detectores
    de secuencia compuesta derivados del spec MTF.
    """
    conds: dict[str, np.ndarray] = {}
    n = len(df)

    def col(name, default=None):
        if name in df.columns:
            v = df[name].values
            return v
        return np.full(n, default) if default is not None else None

    close   = df['close'].values
    high    = df['high'].values
    low     = df['low'].values
    rng     = high - low + 1e-9
    body    = np.abs(close - df['open'].values)
    wick_up = high - np.maximum(close, df['open'].values)
    wick_dn = np.minimum(close, df['open'].values) - low

    sess     = col('session', 'OffHours')
    reg      = col('regime',  'Chop')
    simb     = col('stacked_imb', 'None')
    obi      = col('obi5_mean', col('obi10_mean', np.zeros(n)))
    vr_arr   = col('vr', np.ones(n))
    dz_arr   = col('dz', np.zeros(n))
    vpin_arr = col('vpin', np.full(n, 0.5))
    cvd_neg  = col('cvd_consec_neg', np.zeros(n))
    cvd_pos  = col('cvd_consec_pos', np.zeros(n))
    bslvr    = col('bars_since_low_vr', np.full(n, 9999))
    vwap     = col('vwap', close)
    poc      = col('vp_poc', np.full(n, np.nan))
    val_poc  = col('vp_val', np.full(n, np.nan))

    # ── A. Condiciones de barra actual ────────────────────────────────────────
    is_shoot = (wick_up / rng > 0.45) & (body / rng < 0.40)
    is_hammer= (wick_dn / rng > 0.45) & (body / rng < 0.40)
    conds['is_shoot']  = is_shoot
    conds['is_hammer'] = is_hammer

    conds['sess_london']  = sess == 'London'
    conds['sess_overlap'] = sess == 'Overlap'
    conds['sess_ny']      = sess == 'NewYork'

    conds['reg_trendup']       = reg == 'TrendUp'
    conds['reg_trenddown']     = reg == 'TrendDown'
    conds['reg_expansion']     = reg == 'Expansion'
    conds['reg_not_trenddown'] = reg != 'TrendDown'

    conds['stacked_bear'] = simb == 'Bearish'
    conds['stacked_bull'] = simb == 'Bullish'

    for feat in ('abs_ask', 'abs_bid', 'cvd_div', 'sweep_confirmed',
                 'equal_high', 'equal_low', 'big_trade_bearish', 'big_trade_bullish',
                 'thin_below', 'bid_wall', 'ask_wall',
                 'fib_ote', 'near_weekly_high', 'near_asian_high', 'near_pdh',
                 'pdh_sweep', 'equal_high_sweep', 'tight_range',
                 'bearish_fvg_active', 'near_bearish_fvg', 'near_bearish_ob'):
        v = col(feat, False)
        if v is not None:
            conds[feat] = v.astype(bool)

    for thr in [0.05, 0.10, 0.15, 0.20]:
        conds[f'obi_neg{int(thr*100):02d}'] = obi < -thr
        conds[f'obi_pos{int(thr*100):02d}'] = obi >  thr

    for thr in [1.2, 1.5, 2.0, 3.0]:
        conds[f'vr_gt{str(thr).replace(".","p")}'] = vr_arr > thr

    conds['dz_buyers']    = dz_arr > 0.5
    conds['dz_sellers']   = dz_arr < -0.5
    conds['vpin_toxic']   = vpin_arr > 0.60
    conds['cvd_mom_3neg'] = cvd_neg >= 3
    conds['cvd_mom_5neg'] = cvd_neg >= 5
    conds['cvd_pos_3']    = cvd_pos >= 3
    conds['cvd_pos_5']    = cvd_pos >= 5
    conds['post_compression'] = (bslvr >= 1) & (bslvr <= 5)

    with np.errstate(invalid='ignore'):
        if poc is not None:
            conds['above_poc']  = close > poc
            conds['below_poc']  = close < poc
        if val_poc is not None:
            conds['val_near']   = np.abs(close - val_poc) / (close + 1e-9) < 0.005
        conds['above_vwap'] = close > vwap
        conds['below_vwap'] = close < vwap

    pdh  = col('prev_day_high', np.full(n, np.nan))
    if pdh is not None:
        conds['near_pdh'] = np.abs(close - pdh) / (close + 1e-9) < 0.003

    # ── B. Lag conditions: setup ocurrio N barras atras ───────────────────────
    # Captura la secuencia setup[i-k] -> trigger[i]
    _setup_feats = {
        'eq_high':   col('equal_high',         np.zeros(n)).astype(bool),
        'sweep':     col('sweep_confirmed',     np.zeros(n)).astype(bool),
        'near_ob':   col('near_bearish_ob',     np.zeros(n)).astype(bool),
        'abs_ask':   col('abs_ask',             np.zeros(n)).astype(bool),
        'abs_bid':   col('abs_bid',             np.zeros(n)).astype(bool),
        'cvd_div':   col('cvd_div',             np.zeros(n)).astype(bool),
        'stk_bear':  (simb == 'Bearish'),
        'tight':     col('tight_range',         np.zeros(n)).astype(bool),
        'near_ah':   col('near_asian_high',     np.zeros(n)).astype(bool),
        'near_fvg':  col('near_bearish_fvg',    np.zeros(n)).astype(bool),
        'near_pdh':  col('near_pdh',            np.zeros(n)).astype(bool),
        'fvg_act':   col('bearish_fvg_active',  np.zeros(n)).astype(bool),
        'bt_bear':   col('big_trade_bearish',   np.zeros(n)).astype(bool),
        'eq_h_sw':   col('equal_high_sweep',    np.zeros(n)).astype(bool),
    }
    for name, arr in _setup_feats.items():
        for k in [1, 2, 3]:
            conds[f'{name}_{k}b'] = _lag(arr, k)

    # ── C. Rolling "recent" conditions ────────────────────────────────────────
    # Captura si algo ocurrio en cualquiera de las ultimas N barras
    _recent_specs = [
        ('rec_eq_h_3b',    'equal_high',        3),
        ('rec_eq_h_5b',    'equal_high',        5),
        ('rec_eq_h_10b',   'equal_high',       10),
        ('rec_sweep_3b',   'sweep_confirmed',   3),
        ('rec_sweep_5b',   'sweep_confirmed',   5),
        ('rec_sweep_10b',  'sweep_confirmed',  10),
        ('rec_comp_5b',    'tight_range',       5),
        ('rec_comp_10b',   'tight_range',      10),
        ('rec_near_ob_5b', 'near_bearish_ob',   5),
        ('rec_near_ob_10b','near_bearish_ob',  10),
        ('rec_fvg_10b',    'bearish_fvg_active',10),
        ('rec_fvg_20b',    'bearish_fvg_active',20),
        ('rec_cvd_div_3b', 'cvd_div',           3),
        ('rec_near_ah_3b', 'near_asian_high',   3),
        ('rec_near_ah_5b', 'near_asian_high',   5),
        ('rec_near_pdh_5b','near_pdh',          5),
        ('rec_near_fvg_5b','near_bearish_fvg',  5),
        ('rec_abs_ask_3b', 'abs_ask',           3),
        ('rec_bt_bear_3b', 'big_trade_bearish', 3),
        ('rec_eq_sw_5b',   'equal_high_sweep',  5),
    ]
    for cname, feat, window in _recent_specs:
        arr = col(feat, np.zeros(n)).astype(bool)
        conds[cname] = _recent(arr, window)

    # ── D. Detectores de secuencia compuesta (MTF spec) ──────────────────────
    # Cada detector captura un TIPO de secuencia temporal documentada

    eq_h_arr  = col('equal_high',         np.zeros(n)).astype(bool)
    sw_arr    = col('sweep_confirmed',     np.zeros(n)).astype(bool)
    tight_arr = col('tight_range',         np.zeros(n)).astype(bool)
    ob_arr    = col('near_bearish_ob',     np.zeros(n)).astype(bool)
    fvg_arr   = col('bearish_fvg_active',  np.zeros(n)).astype(bool)
    nfvg_arr  = col('near_bearish_fvg',    np.zeros(n)).astype(bool)
    nah_arr   = col('near_asian_high',     np.zeros(n)).astype(bool)
    abs_a_arr = col('abs_ask',             np.zeros(n)).astype(bool)
    cdiv_arr  = col('cvd_div',             np.zeros(n)).astype(bool)
    eqsw_arr  = col('equal_high_sweep',    np.zeros(n)).astype(bool)

    # "Equal High formo → precio barrío → entrada"  (ICT liquidity hunt)
    conds['seq_eqh_then_sweep']  = _recent(eq_h_arr, 10) & _recent(sw_arr, 5)

    # "Compresion de volumen → explosion"  (AMD: accumulation -> markup)
    conds['seq_comp_burst']      = _recent(tight_arr, 5) & (vr_arr > 1.5)

    # "Compradores agotados → divergencia CVD"  (distribution ICT)
    conds['seq_bull_trap']       = (_lag(cvd_pos >= 3, 1)) & cdiv_arr

    # "Equal high activo → absorcion ask"  (setup + trigger)
    conds['seq_eqh_abs_ask']     = _recent(eq_h_arr, 5) & abs_a_arr

    # "Order Block activo → stacked bear"
    conds['seq_ob_then_bear']    = _recent(ob_arr, 5) & (simb == 'Bearish')

    # "FVG bajista activo → retest → reversal"
    conds['seq_fvg_retest']      = _recent(fvg_arr, 20) & nfvg_arr

    # "Asian High tocada en las ultimas 3h → apertura London/NY"
    conds['seq_asian_to_sess']   = _recent(nah_arr, 3) & ((sess == 'London') | (sess == 'NewYork'))

    # "Sweep reciente → entrada limpia en OB"
    conds['seq_sweep_ob']        = _recent(sw_arr, 5) & ob_arr

    # "Shooting star DESPUES de equal high"  (patron ICT clasico)
    conds['seq_eqh_then_shoot']  = _recent(eq_h_arr, 5) & is_shoot

    # "Equal high sweep → presion bajista continua"
    conds['seq_eqhsw_bear']      = _recent(eqsw_arr, 3) & (simb == 'Bearish')

    # "Equal high → FVG bajista arriba → absorcion"
    conds['seq_eqh_fvg_abs']     = _recent(eq_h_arr, 5) & _recent(fvg_arr, 10) & abs_a_arr

    # "Compresion → OBI negativo → entrada"  (liquidez seca encima)
    conds['seq_comp_obi_neg']    = _recent(tight_arr, 5) & (obi < -0.05)

    # "PDH cerca → sweep → entrada"
    near_pdh_arr = col('near_pdh', np.zeros(n)).astype(bool)
    conds['seq_pdh_sweep']       = _recent(near_pdh_arr, 5) & _recent(sw_arr, 3)

    # "CVD negativo consecutivo → absorcion bid"  (soporte sin compradores)
    abs_b_arr = col('abs_bid', np.zeros(n)).astype(bool)
    conds['seq_cvd_neg_abs_bid'] = (cvd_neg >= 3) & abs_b_arr

    return conds


# ── Estadisticas de un patron ─────────────────────────────────────────────────

def pattern_stats(outcomes: pd.DataFrame, mask: np.ndarray, label: str) -> dict | None:
    idx     = outcomes['bar_idx'].values
    matched = mask[idx]
    sub     = outcomes[matched]
    n_t     = len(sub)
    if n_t < MIN_N:
        return None
    wins  = (sub['result_r'] > 0).sum()
    wr    = wins / n_t * 100
    avg_r = sub['result_r'].mean()
    return {
        'pattern': label,
        'n':       n_t,
        'wins':    int(wins),
        'wr':      round(wr, 1),
        'avg_r':   round(avg_r, 3),
        'total_r': round(n_t * avg_r, 1),   # <<< metrica principal
        'score':   round(wr * avg_r, 2),     # referencia legacy
    }


# ── Mining ────────────────────────────────────────────────────────────────────

def mine(outcomes: pd.DataFrame, conds: dict[str, np.ndarray],
         depth: int = 2, top_singles: int = 40,
         anchor_pairs: list[str] | None = None,
         exclude_keys: list[str] | None = None) -> list[dict]:
    """
    anchor_pairs: lista de labels de pares para usar como base en depth=3
                  (anula la seleccion automatica por top total_r)
    exclude_keys: tokens cuya presencia en TODOS los componentes excluye el par
                  del pool de anclas en depth=3 (ej. ['fvg'] para filtrar FVG-puro)
    """
    results    = []
    cond_names = list(conds.keys())
    n_conds    = len(cond_names)

    print(f"  Mining depth=1: {n_conds} condiciones...")
    singles = []
    for name in cond_names:
        mask  = conds[name]
        stats = pattern_stats(outcomes, mask, name)
        if stats:
            results.append(stats)
            singles.append((name, mask, stats['total_r']))

    singles.sort(key=lambda x: -x[2])
    print(f"    -> {len(singles)} con n>={MIN_N}")

    if depth < 2:
        return results

    top_s        = singles[:top_singles]
    total_pairs  = len(top_s) * n_conds
    print(f"  Mining depth=2: {total_pairs:,} pares...")
    pairs = []
    pair_by_label: dict[str, tuple] = {}
    for n1, m1, _ in top_s:
        for n2 in cond_names:
            if n2 == n1:
                continue
            m2    = conds[n2]
            mask  = m1 & m2
            label = f"{n1} + {n2}"
            stats = pattern_stats(outcomes, mask, label)
            if stats:
                results.append(stats)
                pairs.append((label, mask, stats['total_r']))
                pair_by_label[label] = (label, mask, stats['total_r'])

    pairs.sort(key=lambda x: -x[2])
    print(f"    -> {len(pairs)} con n>={MIN_N}")

    if depth < 3:
        return results

    # Seleccion de anclas para depth=3
    if anchor_pairs:
        # Usar anclas especificadas manualmente
        top_p = []
        for lbl in anchor_pairs:
            if lbl in pair_by_label:
                top_p.append(pair_by_label[lbl])
            else:
                # Buscar coincidencia parcial
                matches = [(l, m, s) for l, m, s in pairs if lbl in l]
                top_p.extend(matches[:3])
        top_p = list({t[0]: t for t in top_p}.values())  # dedup por label
        print(f"  Anclas manuales: {len(top_p)} pares")
    else:
        # Auto: top pares filtrados de condiciones dominantes (FVG puro)
        _excl = exclude_keys or []
        def is_dominated(label):
            if not _excl:
                return False
            parts = label.split(' + ')
            return all(any(k in p for k in _excl) for p in parts)
        clean_pairs = [(l, m, s) for l, m, s in pairs if not is_dominated(l)]
        top_p = clean_pairs[:20]
        print(f"  Anclas auto (excl {_excl}): top {len(top_p)} pares limpios")

    total_triples = len(top_p) * n_conds
    print(f"  Mining depth=3: {total_triples:,} triples...")
    for n12, m12, _ in top_p:
        for n3 in cond_names:
            if n3 in n12:
                continue
            m3    = conds[n3]
            mask  = m12 & m3
            label = f"{n12} + {n3}"
            stats = pattern_stats(outcomes, mask, label)
            if stats:
                results.append(stats)

    return results


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    global MIN_N, DATA_FILE, FORWARD_MAX
    parser = argparse.ArgumentParser()
    parser.add_argument('--tf',     choices=['m1', 'm5', 'm15'], default='m1',
                        help='Timeframe del dataset (default m1)')
    parser.add_argument('--min-n',  type=int, default=MIN_N)
    parser.add_argument('--top',    type=int, default=80)
    parser.add_argument('--depth',  type=int, default=2, choices=[1, 2, 3])
    parser.add_argument('--days',   type=int, default=0)
    parser.add_argument('--wr-min', type=float, default=35.0,
                        help='WR minimo para finalistas (default 35%%)')
    parser.add_argument('--exclude', default='fvg,rec_fvg,rec_near_fvg,seq_fvg,near_fvg,fvg_act',
                        help='Tokens excluidos de anclas depth=3 (separados por coma)')
    parser.add_argument('--anchor', default='',
                        help='Labels de pares ancla para depth=3 (separados por |)')
    parser.add_argument('--out',    default='')
    args  = parser.parse_args()
    MIN_N = args.min_n

    # Ajustar DATA_FILE y FORWARD_MAX según timeframe
    DATA_FILE, FORWARD_MAX, _ = TF_CONFIG[args.tf]
    print(f"Timeframe: {args.tf.upper()}  |  FORWARD_MAX={FORWARD_MAX} barras (~20h)")

    if not DATA_FILE.exists():
        sys.exit(f"No existe {DATA_FILE}  — corre resample_m1_to_htf.py primero")

    print(f"Cargando {DATA_FILE.name}...")
    df = pd.read_parquet(DATA_FILE).sort_values('ts_ms').reset_index(drop=True)

    if args.days > 0:
        cutoff = df['ts_ms'].max() - args.days * D1_MS
        df     = df[df['ts_ms'] >= cutoff].reset_index(drop=True)
        print(f"  Limitado a ultimos {args.days} dias: {len(df):,} barras")

    days_total = (df['ts_ms'].max() - df['ts_ms'].min()) / D1_MS
    print(f"  {len(df):,} barras  |  {len(df.columns)} columnas  |  {days_total:.0f} dias")
    print(f"  Breakeven WR = {BREAKEVEN_WR*100:.1f}%  |  Target = {TARGET_R}R  |  MIN_N = {MIN_N}")

    # ── H1 stop ───────────────────────────────────────────────────────────────
    print("\n[1/3] H1 stop por barra...")
    t0 = time.time()
    stop_price, stop_pct = compute_h1_stop(df)
    print(f"  Listo en {time.time()-t0:.1f}s")

    # ── Simulacion ────────────────────────────────────────────────────────────
    print("\n[2/3] Simulando outcomes (forward 1200 barras)...")
    t0       = time.time()
    outcomes = simulate_outcomes(df, stop_price, stop_pct)
    if outcomes.empty:
        sys.exit("No hay candidatos validos.")

    n_tot = len(outcomes)
    wins  = (outcomes['result_r'] > 0).sum()
    avg_r = outcomes['result_r'].mean()
    print(f"\n  BASE: n={n_tot:,}  WR={wins/n_tot*100:.1f}%  AvgR={avg_r:.3f}  "
          f"Total_R={n_tot*avg_r:.0f}R")
    print(f"  Simulacion: {time.time()-t0:.1f}s")

    # ── Mining secuencias ─────────────────────────────────────────────────────
    print("\n[3/3] Mining de secuencias multi-barra...")
    conds   = build_sequence_conditions(df)
    n_conds = len(conds)
    print(f"  {n_conds} condiciones (actuales + lags + recent + secuencias)")

    excl_keys    = [k.strip() for k in args.exclude.split(',') if k.strip()]
    anchor_pairs = [a.strip() for a in args.anchor.split('|') if a.strip()] if args.anchor else None

    t0      = time.time()
    results = mine(outcomes, conds, depth=args.depth,
                   anchor_pairs=anchor_pairs, exclude_keys=excl_keys)
    print(f"  Mining completado en {time.time()-t0:.1f}s")
    print(f"  Total patrones con n>={MIN_N}: {len(results):,}")

    # ── Resultados ────────────────────────────────────────────────────────────
    df_res = (pd.DataFrame(results)
                .drop_duplicates('pattern')
                .sort_values('total_r', ascending=False)   # <<< ordenar por Total_R
                .reset_index(drop=True))

    print(f"\n{'-'*90}")
    print(f"  TOP {args.top} PATRONES  (ordenados por Total_R = n x AvgR)")
    print(f"  Breakeven WR = {BREAKEVEN_WR*100:.1f}%")
    print(f"{'-'*90}")
    print(f"  {'#':>3}  {'n':>5}  {'/dia':>5}  {'WR%':>6}  {'AvgR':>7}  {'Total_R':>8}  patron")
    print(f"{'-'*90}")
    for i, row in df_res.head(args.top).iterrows():
        trades_per_day = row['n'] / days_total
        print(f"  {i+1:>3}  {row['n']:>5}  {trades_per_day:>5.1f}  "
              f"{row['wr']:>6.1f}%  {row['avg_r']:>7.3f}  {row['total_r']:>8.1f}  "
              f"{row['pattern']}")

    # ── Guardar ───────────────────────────────────────────────────────────────
    EXPORTS.mkdir(exist_ok=True)
    out_path = Path(args.out) if args.out else EXPORTS / 'spot_sequences_mining.csv'
    df_res.to_csv(out_path, index=False)
    print(f"\nGuardado: {out_path}  ({len(df_res):,} patrones)")

    # ── Finalistas ────────────────────────────────────────────────────────────
    finalists = df_res[
        (df_res['wr']    > args.wr_min) &
        (df_res['avg_r'] > 0.10) &
        (df_res['n']     >= MIN_N)
    ].copy()
    finalists['trades_per_day'] = finalists['n'] / days_total

    print(f"\n  Finalistas (WR>{args.wr_min:.0f}%, AvgR>0.10, n>={MIN_N}):")
    print(f"  -> {len(finalists)} candidatos")
    if not finalists.empty:
        seen = set()
        for _, row in finalists.iterrows():
            key = frozenset(row['pattern'].split(' + '))
            if key in seen:
                continue
            seen.add(key)
            print(f"  WR={row['wr']:.1f}%  AvgR={row['avg_r']:+.3f}  "
                  f"n={row['n']}  {row['trades_per_day']:.1f}/dia  "
                  f"Total={row['total_r']:.0f}R  |  {row['pattern']}")


if __name__ == '__main__':
    main()
