import os, json, urllib.request
from pathlib import Path
import pandas as pd
import numpy as np

for line in Path('.env').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip())

url = os.environ['SUPABASE_URL']
key = os.environ['SUPABASE_KEY']

pages = []
for offset in range(0, 5000, 1000):
    req = urllib.request.Request(
        f'{url}/rest/v1/btc_bars?select=*&order=ts_ms.asc&limit=1000&offset={offset}&symbol=eq.BTCUSDT',
        headers={'apikey': key, 'Authorization': f'Bearer {key}', 'Accept': 'application/json'}
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        page = json.loads(r.read())
    if not page: break
    pages.extend(page)
    if len(page) < 1000: break

df = pd.DataFrame(pages)
df['dt'] = pd.to_datetime(df['ts_ms'], unit='ms', utc=True)
df = df.sort_values('ts_ms').reset_index(drop=True)

ACCUM_MIN=15; ACCUM_MAX=60; RNG_MIN=0.08; RNG_MAX=0.55
VR_MIN=2.0; STOP_PCT=0.0025; TGT_L=0.0045; TGT_S=0.0050; COOLDOWN=30
RBF_SESS={'London','LondonNyOverlap'}

def gv(row, col):
    v = row.get(col)
    if v is None: return None
    if isinstance(v, float) and np.isnan(v): return None
    return v

signals = []
last_sig = -COOLDOWN

for i in range(ACCUM_MIN, len(df)-5):
    if i - last_sig < COOLDOWN:
        continue
    row = df.iloc[i]
    if row['session'] not in RBF_SESS:
        continue

    best = None
    for nb in range(ACCUM_MIN, min(ACCUM_MAX, i)+1):
        w = df.iloc[i-nb:i]
        rh, rl = w['high'].max(), w['low'].min()
        rng = (rh-rl)/row['close']*100
        if RNG_MIN <= rng <= RNG_MAX:
            best = (rh, rl, rng, nb, w)
            break

    if not best:
        continue
    rh, rl, rng, nb, accum_w = best
    if not (row['close'] > rh or row['close'] < rl):
        continue
    if pd.isna(row['vr']) or row['vr'] < VR_MIN:
        continue
    vpin_val = gv(row, 'vpin')
    if vpin_val is not None and vpin_val > 0.65:
        continue

    direction = 'Long' if row['close'] > rh else 'Short'

    cvd     = gv(row, 'cvd_slope')
    dz      = gv(row, 'dz')
    obi     = gv(row, 'obi_l5')
    liq     = gv(row, 'liq_ratio')
    bar_d   = gv(row, 'bar_delta') or 0.0
    stk     = str(row.get('stacked_imb') or 'None')
    abso    = str(row.get('absorption') or 'None')
    thin_b  = bool(row.get('thin_below', False))
    thin_a  = bool(row.get('thin_above', False))
    bid_w   = bool(row.get('bid_wall', False))
    ask_w   = bool(row.get('ask_wall', False))
    oi_m    = bool(row.get('oi_momentum', False))
    regime  = str(row.get('regime') or '?')

    if direction == 'Long':
        cvd_aligned  = (cvd > 15) if cvd is not None else None
        obi_aligned  = (obi > 0.08) if obi is not None else None
        delta_aligned= bar_d > 0
        stk_aligned  = stk in ['Bullish', 'FBG-Bullish']
        abso_aligned = abso == 'Bid'
        thin_favor   = thin_a
        wall_against = ask_w
    else:
        cvd_aligned  = (cvd < -15) if cvd is not None else None
        obi_aligned  = (obi < -0.08) if obi is not None else None
        delta_aligned= bar_d < 0
        stk_aligned  = stk in ['Bearish', 'FBG-Bearish']
        abso_aligned = abso == 'Ask'
        thin_favor   = thin_b
        wall_against = bid_w

    cvd_abs   = abs(cvd) if cvd is not None else None
    delta_abs = abs(bar_d)

    cvd_sum_range   = float(accum_w['cvd_slope'].dropna().sum())
    obi_mean_range  = float(accum_w['obi_l5'].dropna().mean()) if accum_w['obi_l5'].notna().any() else 0.0
    vr_mean_range   = float(accum_w['vr'].dropna().mean())
    vpin_mean_range = float(accum_w['vpin'].dropna().mean()) if accum_w['vpin'].notna().any() else 0.0
    high_vr_range   = int((accum_w['vr'] > 1.5).sum())

    # CVD acumulado en rango alineado con direccion del breakout
    cvd_range_aligned = (cvd_sum_range > 0) if direction == 'Long' else (cvd_sum_range < 0)

    entry  = row['close']
    stop   = entry*(1+STOP_PCT) if direction=='Short' else entry*(1-STOP_PCT)
    target = entry*(1-TGT_S)   if direction=='Short' else entry*(1+TGT_L)
    rr     = abs(target-entry)/abs(stop-entry)

    outcome = 'OPEN'; res_r = None
    for j in range(i+1, min(i+61, len(df))):
        f = df.iloc[j]
        if direction == 'Short':
            if f['low']  <= target: outcome = 'TARGET'; res_r = rr;  break
            if f['high'] >= stop:   outcome = 'STOP';   res_r = -1.; break
        else:
            if f['high'] >= target: outcome = 'TARGET'; res_r = rr;  break
            if f['low']  <= stop:   outcome = 'STOP';   res_r = -1.; break

    signals.append({
        'dt': str(row['dt'])[:16], 'dir': direction, 'session': row['session'],
        'vr': row['vr'], 'vpin': vpin_val, 'cvd': cvd, 'cvd_abs': cvd_abs,
        'cvd_aligned': cvd_aligned, 'delta_aligned': delta_aligned, 'delta_abs': delta_abs,
        'obi': obi, 'obi_aligned': obi_aligned, 'dz': dz,
        'stk_aligned': stk_aligned, 'abso_aligned': abso_aligned,
        'thin_favor': thin_favor, 'wall_against': wall_against,
        'oi_ok': oi_m, 'regime': regime, 'liq': liq,
        'cvd_sum_range': cvd_sum_range, 'cvd_range_aligned': cvd_range_aligned,
        'obi_mean_range': obi_mean_range, 'vr_mean_range': vr_mean_range,
        'vpin_mean_range': vpin_mean_range, 'high_vr_range': high_vr_range,
        'nb': nb, 'rng_pct': rng,
        'outcome': outcome, 'R': res_r,
    })
    last_sig = i

s = pd.DataFrame(signals)
cl = s[s['outcome'] != 'OPEN'].copy()
cl['win'] = cl['outcome'] == 'TARGET'

print(f"Total cerradas: {len(cl)} | W={cl['win'].sum()} L={(~cl['win']).sum()}")
print()

print('--- NUMERICAS (direction-normalized) ---')
for col in ['vr', 'vpin', 'cvd_abs', 'delta_abs', 'obi', 'dz', 'liq']:
    sub = cl[cl[col].notna()]
    if len(sub) == 0: continue
    wm = sub[sub['win']][col].mean()
    lm = sub[~sub['win']][col].mean()
    nw = sub['win'].sum(); nl = (~sub['win']).sum()
    diff = wm - lm
    mark = ' <<<<' if abs(diff) > max(abs(lm)*0.25, 0.3) and nw >= 3 else ''
    print(f'{col:18s}  W={wm:+7.2f}(n={int(nw)})  L={lm:+7.2f}(n={int(nl)})  diff={diff:+.2f}{mark}')

print()
print('--- BOOLEANS (% True) ---')
bool_cols = ['cvd_aligned','delta_aligned','obi_aligned','stk_aligned',
             'abso_aligned','thin_favor','wall_against','oi_ok','cvd_range_aligned']
for col in bool_cols:
    sub = cl[cl[col].notna()].copy()
    sub[col] = sub[col].astype(bool)
    wr = sub[sub['win']][col].mean()
    lr = sub[~sub['win']][col].mean()
    diff = wr - lr
    mark = ' <<<<' if abs(diff) > 0.15 else ''
    print(f'{col:24s}  W={wr:.0%}  L={lr:.0%}  diff={diff:+.0%}{mark}')

print()
print('--- RANGO DE ACUMULACION ---')
for col in ['cvd_sum_range', 'obi_mean_range', 'vr_mean_range', 'vpin_mean_range', 'high_vr_range', 'nb', 'rng_pct']:
    wm = cl[cl['win']][col].mean()
    lm = cl[~cl['win']][col].mean()
    diff = wm - lm
    mark = ' <<<<' if abs(diff) > 0.5 else ''
    print(f'{col:22s}  W={wm:+7.2f}  L={lm:+7.2f}  diff={diff:+.2f}{mark}')

print()
print('--- COMBINACIONES ---')

def test(name, fn, df=cl):
    try:
        mask = df.apply(fn, axis=1)
        sub = df[mask]
        if len(sub) < 3: return
        wr = sub['win'].mean()*100
        ar = sub['R'].mean()
        print(f'{name:42s}  n={len(sub):2d}  WR={wr:.0f}%  avgR={ar:+.2f}')
    except Exception as e:
        pass

test('vr 2-5',                      lambda r: 2<=r['vr']<=5)
test('vr 2-5 + cvd_aligned',        lambda r: 2<=r['vr']<=5 and r['cvd_aligned'])
test('vr 2-5 + delta_aligned',      lambda r: 2<=r['vr']<=5 and r['delta_aligned'])
test('cvd_aligned + delta_aligned',  lambda r: r['cvd_aligned'] and r['delta_aligned'])
test('cvd_aligned + obi_aligned',    lambda r: r['cvd_aligned'] and bool(r['obi_aligned']))
test('delta + NO wall_against',      lambda r: r['delta_aligned'] and not r['wall_against'])
test('stk_aligned + cvd_aligned',    lambda r: r['stk_aligned'] and r['cvd_aligned'])
test('NO wall + cvd + delta',        lambda r: not r['wall_against'] and r['cvd_aligned'] and r['delta_aligned'])
test('Expansion + cvd_aligned',      lambda r: r['regime']=='Expansion' and r['cvd_aligned'])
test('London + cvd_aligned',         lambda r: r['session']=='London' and r['cvd_aligned'])
test('low vr_range(<0.9) + cvd',     lambda r: r['vr_mean_range']<0.9 and r['cvd_aligned'])
test('low high_vr_range(<=3) + cvd', lambda r: r['high_vr_range']<=3 and r['cvd_aligned'])
test('cvd_range_aligned + cvd_ok',   lambda r: r['cvd_range_aligned'] and r['cvd_aligned'])
test('cvd_range_aligned alone',      lambda r: r['cvd_range_aligned'])
test('stk + delta + NO wall',        lambda r: r['stk_aligned'] and r['delta_aligned'] and not r['wall_against'])
test('vr 2-4 + cvd + delta',         lambda r: 2<=r['vr']<=4 and r['cvd_aligned'] and r['delta_aligned'])
test('abso_aligned + cvd_aligned',   lambda r: r['abso_aligned'] and r['cvd_aligned'])
test('thin_favor + cvd_aligned',     lambda r: r['thin_favor'] and r['cvd_aligned'])
test('oi_ok + cvd_aligned',          lambda r: r['oi_ok'] and r['cvd_aligned'])
