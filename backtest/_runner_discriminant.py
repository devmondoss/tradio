"""
_runner_discriminant.py — ¿qué features en la ENTRADA predicen un runner (MFE>=3R)?
Para target adaptativo. Mide MFE-antes-del-stop por entrada y separa runners vs no.
Valida IS y OOS por separado (no inventar reglas que solo sirven OOS).
"""
import pandas as pd, numpy as np
import mtf_basics as M

df = pd.read_parquet(M.DATA_M1).sort_values('ts_ms').reset_index(drop=True)
for c in df.columns:
    if df[c].dtype == object: df[c] = df[c].fillna('')
    elif df[c].dtype == float: df[c] = df[c].fillna(0.0)

H1_MS = M.H1_MS
h1 = M.resamp(df, H1_MS)
h1['atr'] = M.atr14(h1['high'].values, h1['low'].values, h1['close'].values)
h1ctx = {int(r.ts_ms): (float(r.high), float(r.atr)) for r in h1.itertuples()}
rows = df.to_dict('records')
N = len(rows)

# features candidatos a evaluar (numericos + 1 categorico regime)
NUMF = ['vr', 'vpin', 'dz', 'cvd_slope', 'delta', 'obi10_mean', 'sell_vol', 'buy_vol',
        'spread_mean', 'obi_range', 'atr14']

def entry_at(i, row, ts):
    if M.session(ts) not in ('london', 'overlap', 'ny'): return None
    ok, lbl = M.active_level(row)
    if not ok or 'VAH' not in lbl: return None
    p = lbl.split('+')
    if len(p) >= 3 and 'PDH' in p and 'AH' in p: return None
    if 'PDH' in p and 'VAH' in p and len(p) == 2: return None
    if 'WH' in p and (ts // 3_600_000) % 24 == 15: return None
    if lbl == 'AH+VAH' and float(row.get('obi10_mean') or 0) < -0.15: return None
    if not M.rejection(row) or not M.flow_bearish(row): return None
    h1d = h1ctx.get((ts // H1_MS) * H1_MS)
    if h1d is None: return None
    h1h, h1a = h1d
    sl = h1h + 0.40 * h1a; c = float(row['close']); d = sl - c
    if d <= 0: return None
    if not (M.MIN_STOP <= d / c <= M.MAX_STOP): return None
    return c, sl, d, ts >= M.OOS_MS, lbl

# set real (1 a la vez con salida actual: 2.5R / no CVD / timeout 1200)
records = []
i = 0
while i < N:
    row = rows[i]; ts = int(row['ts_ms'])
    e = entry_at(i, row, ts)
    if e is None: i += 1; continue
    ep, sl, dist, oos, lbl = e
    tp = ep - 2.5 * dist
    # forward: MFE antes del stop (potencial real, sin cap del target), y cierre real
    mfe = 0.0; close_i = None; hit_stop_bar = None
    for j in range(i + 1, min(i + 1 + M.FORWARD, N)):
        r = rows[j]
        mfe = max(mfe, (ep - r['low']) / dist)
        if r['high'] >= sl and hit_stop_bar is None:
            hit_stop_bar = j
        # cierre real (para avanzar i): stop / target / timeout
        if close_i is None:
            if r['high'] >= sl: close_i = j
            elif r['low'] <= tp: close_i = j
            elif j - i >= M.FORWARD: close_i = j
        if hit_stop_bar is not None:
            break  # MFE ya no crece tras stop
    if close_i is None: close_i = min(i + M.FORWARD, N - 1)
    rec = {'oos': oos, 'mfe': mfe, 'runner': mfe >= 3.0, 'reached2_5': mfe >= 2.5,
           'regime': str(row.get('regime') or ''), 'level': lbl,
           'vah_dist': abs(row['high'] - row['vp_vah']) / row['vp_vah'] if row.get('vp_vah') else 0,
           'stop_pct': dist / ep, 'tight_range': bool(row.get('tight_range'))}
    for f in NUMF:
        rec[f] = float(row.get(f) or 0)
    records.append(rec)
    i = close_i + 1

R = pd.DataFrame(records)
print(f"Entradas: {len(R)}  | runners(MFE>=3R): {R['runner'].mean()*100:.1f}%  | IS {(~R['oos']).sum()} OOS {R['oos'].sum()}\n")

def discriminant(split_name, sub):
    run = sub[sub['runner']]; nor = sub[~sub['runner']]
    if len(run) < 10 or len(nor) < 10:
        print(f"  [{split_name}] muestra insuficiente"); return
    print(f"  [{split_name}] runners={len(run)} ({len(run)/len(sub)*100:.0f}%)  no-runners={len(nor)}")
    print(f"    {'feature':<12} {'runner_med':>11} {'norun_med':>11} {'separacion':>11}")
    diffs = []
    for f in NUMF + ['vah_dist', 'stop_pct']:
        rm, nm = run[f].median(), nor[f].median()
        pooled = sub[f].std() or 1
        sep = (rm - nm) / pooled  # cohen-ish
        diffs.append((abs(sep), f, rm, nm, sep))
    for _, f, rm, nm, sep in sorted(diffs, reverse=True):
        star = ' <<<' if abs(sep) > 0.25 else ''
        print(f"    {f:<12} {rm:>11.3f} {nm:>11.3f} {sep:>+11.2f}{star}")
    # regime
    print(f"    regime runner-rate:")
    for reg in sub['regime'].unique():
        s = sub[sub['regime'] == reg]
        if len(s) >= 15:
            print(f"      {reg:<12} n={len(s):>4} runner%={s['runner'].mean()*100:>5.1f}")

print("=== IS (Jun2025-Feb2026) ===")
discriminant('IS', R[~R['oos']])
print("\n=== OOS (Mar-May2026) ===")
discriminant('OOS', R[R['oos']])
