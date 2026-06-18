"""
Compara target fijo 2.5R vs target dinamico min(2.5R, dist_to_VAL_in_R)
para los mejores patrones con is_shoot como trigger.

Logica short:
  entry     = close[i]
  stop      = H1_high + 0.30*ATR_H1   (arriba)
  risk      = stop - entry
  tgt_fixed = entry - 2.5 * risk
  tgt_val   = vp_val[i]                (abajo, area de valor inferior)
  tgt_dyn   = entry - min(2.5, dist_val_r) * risk
            = max(tgt_fixed, vp_val)   <- el que esta mas cerca al entry
"""
import sys
sys.path.insert(0, 'backtest')

import time
import numpy as np
import pandas as pd
from mine_mtf_sequences import (
    compute_h1_stop, build_sequence_conditions,
    TARGET_R, MIN_STOP_PCT, MAX_STOP_PCT, FORWARD_MAX,
    ema_np
)

DAYS = 351

# ── Carga ─────────────────────────────────────────────────────────────────────
print("Cargando datos...")
df = pd.read_parquet('data/bybit-spot/processed/btcusdt_m1.parquet').sort_values('ts_ms').reset_index(drop=True)
print(f"  {len(df):,} barras | {df.shape[1]} columnas")

print("H1 stop...")
stop_price, stop_pct = compute_h1_stop(df)

# ── Simulacion con target dinamico ────────────────────────────────────────────
print("Simulando con target fijo Y dinamico (VAL)...")

close  = df['close'].values
high   = df['high'].values
low    = df['low'].values
ts_ms  = df['ts_ms'].values
n      = len(df)

ema20      = df['ema20'].values if 'ema20' in df.columns else ema_np(close, 20)
d1_ok      = close <= ema20 * 1.005
valid_sess = df['session'].isin({'London', 'Overlap', 'NewYork'}).values \
             if 'session' in df.columns else np.ones(n, bool)
valid_stop = np.isfinite(stop_pct) & (stop_pct >= MIN_STOP_PCT) & (stop_pct <= MAX_STOP_PCT)
cand_mask  = valid_sess & d1_ok & valid_stop
candidates = np.where(cand_mask)[0]
print(f"  Candidatos: {len(candidates):,}")

# VAL array (puede tener NaN en barras sin VP calculado)
val_arr = df['vp_val'].values if 'vp_val' in df.columns else np.full(n, np.nan)
poc_arr = df['vp_poc'].values if 'vp_poc' in df.columns else np.full(n, np.nan)

records = []
t0 = time.time()
for k, i in enumerate(candidates):
    if k % 20000 == 0 and k > 0:
        eta = (time.time() - t0) / k * (len(candidates) - k)
        print(f"    {k:,}/{len(candidates):,}  ETA:{eta:.0f}s", end='\r')

    sp   = stop_price[i]
    risk = sp - close[i]
    if risk <= 0:
        continue

    tgt_fixed = close[i] - TARGET_R * risk

    # Target dinamico: min(2.5R, distancia al VAL)
    val = val_arr[i]
    if np.isfinite(val) and val < close[i]:
        dist_val_r = (close[i] - val) / risk   # cuantos R hasta VAL
        dyn_r      = min(TARGET_R, dist_val_r)
        tgt_dyn    = close[i] - dyn_r * risk
    else:
        dyn_r   = TARGET_R
        tgt_dyn = tgt_fixed

    end   = min(i + FORWARD_MAX + 1, n)
    fwd_h = high[i + 1: end]
    fwd_l = low[i + 1:  end]
    if len(fwd_h) == 0:
        continue

    stop_hits = fwd_h >= sp

    # Resultado fijo 2.5R
    tgt_hits_f = fwd_l <= tgt_fixed
    stop_bar   = int(np.argmax(stop_hits))   if stop_hits.any()   else FORWARD_MAX
    tgt_bar_f  = int(np.argmax(tgt_hits_f)) if tgt_hits_f.any()  else FORWARD_MAX
    if not stop_hits.any():   stop_bar  = FORWARD_MAX
    if not tgt_hits_f.any():  tgt_bar_f = FORWARD_MAX

    if tgt_bar_f < stop_bar:
        res_fixed, out_fixed = TARGET_R, 'target'
    elif stop_bar < FORWARD_MAX:
        res_fixed, out_fixed = -1.0, 'stop'
    else:
        last_c = close[min(i + FORWARD_MAX, n - 1)]
        res_fixed = (close[i] - last_c) / risk
        out_fixed = 'timeout'

    # Resultado dinamico VAL
    tgt_hits_d = fwd_l <= tgt_dyn
    tgt_bar_d  = int(np.argmax(tgt_hits_d)) if tgt_hits_d.any() else FORWARD_MAX
    if not tgt_hits_d.any(): tgt_bar_d = FORWARD_MAX

    if tgt_bar_d < stop_bar:
        res_dyn, out_dyn = dyn_r, 'target'
    elif stop_bar < FORWARD_MAX:
        res_dyn, out_dyn = -1.0, 'stop'
    else:
        last_c = close[min(i + FORWARD_MAX, n - 1)]
        res_dyn = (close[i] - last_c) / risk
        out_dyn = 'timeout'

    records.append({
        'bar_idx':    i,
        'ts_ms':      int(ts_ms[i]),
        'res_fixed':  round(float(res_fixed), 4),
        'out_fixed':  out_fixed,
        'res_dyn':    round(float(res_dyn),   4),
        'out_dyn':    out_dyn,
        'dyn_r':      round(float(dyn_r), 3),   # R target real usado
        'stop_pct':   round(float(stop_pct[i]), 4),
        'val':        round(float(val), 2) if np.isfinite(val) else np.nan,
        'poc':        round(float(poc_arr[i]), 2) if np.isfinite(poc_arr[i]) else np.nan,
    })

print(f"\n  Simulados: {len(records):,} trades en {time.time()-t0:.1f}s")
out = pd.DataFrame(records)

# ── Condiciones ───────────────────────────────────────────────────────────────
print("Construyendo condiciones...")
conds = build_sequence_conditions(df)

def _get(k):
    return conds.get(k, np.zeros(len(df), dtype=bool))

shoot = _get('is_shoot')
idx_arr = out['bar_idx'].values

# Patrones a comparar (los top del analisis anterior)
patterns = {
    'above_poc+london':               _get('above_poc')       & _get('sess_london'),
    'above_poc+london+bvwap':         _get('above_poc')       & _get('sess_london') & _get('below_vwap'),
    'rec_ah5+london+avwap':           _get('rec_near_ah_5b')  & _get('sess_london') & _get('above_vwap'),
    'rec_ah3+london+avwap':           _get('rec_near_ah_3b')  & _get('sess_london') & _get('above_vwap'),
    'near_ah+london+avwap':           _get('near_asian_high') & _get('sess_london') & _get('above_vwap'),
    'poc+lon+bvwap+obi_neg05':        _get('above_poc')       & _get('sess_london') & _get('below_vwap') & _get('obi_neg05'),
    'poc+lon+bvwap+rec_cvd_div':      _get('above_poc')       & _get('sess_london') & _get('below_vwap') & _get('rec_cvd_div_3b'),
    'rec_ah3+lon+cvd_mom_3neg':       _get('rec_near_ah_3b')  & _get('sess_london') & _get('cvd_mom_3neg'),
    'ah+lon+avwap+cvd_mom_3neg':      _get('near_asian_high') & _get('sess_london') & _get('above_vwap') & _get('cvd_mom_3neg'),
    'poc+lon+bvwap+cvd_div':          _get('above_poc')       & _get('sess_london') & _get('below_vwap') & _get('cvd_div'),
}

# ── Comparativa ───────────────────────────────────────────────────────────────
print()
print(f"{'Patron':<35} | {'n':>5} | {'/dia':>4} | {'WR_fix':>6} | {'AvgR_fix':>8} | {'WR_val':>6} | {'AvgR_val':>8} | {'Delta_R':>7} | {'dyn_R_mean':>10}")
print('-' * 120)

for pname, pmask in patterns.items():
    combo   = pmask & shoot
    matched = combo[idx_arr]
    sub     = out[matched]
    n       = len(sub)
    if n < 15:
        continue

    wr_f   = (sub['res_fixed'] > 0).sum() / n * 100
    avg_f  = sub['res_fixed'].mean()
    wr_v   = (sub['res_dyn']   > 0).sum() / n * 100
    avg_v  = sub['res_dyn'].mean()
    delta  = avg_v - avg_f
    tpd    = n / DAYS
    dyn_mean = sub['dyn_r'].mean()

    print(f"{pname:<35} | {n:>5} | {tpd:>4.1f} | {wr_f:>6.1f}% | {avg_f:>8.3f} | {wr_v:>6.1f}% | {avg_v:>8.3f} | {delta:>+7.3f} | {dyn_mean:>10.2f}R")

# ── Distribucion de dyn_r para entender cuando el VAL acorta el target ────────
print()
print("Distribucion del target dinamico (dyn_r) para poc+london+bvwap+shoot:")
poc_bvwap_shoot = (_get('above_poc') & _get('sess_london') & _get('below_vwap') & shoot)[idx_arr]
sub_ref = out[poc_bvwap_shoot]
if len(sub_ref) > 0:
    dr = sub_ref['dyn_r']
    print(f"  Min={dr.min():.2f}R  P25={dr.quantile(0.25):.2f}R  "
          f"Median={dr.median():.2f}R  P75={dr.quantile(0.75):.2f}R  Max={dr.max():.2f}R")
    below_2r  = (dr < 2.0).sum()
    at_full   = (dr >= 2.49).sum()
    print(f"  VAL acorta target (<2R): {below_2r} trades ({below_2r/len(dr)*100:.1f}%)")
    print(f"  Full 2.5R (VAL lejano):  {at_full} trades ({at_full/len(dr)*100:.1f}%)")
