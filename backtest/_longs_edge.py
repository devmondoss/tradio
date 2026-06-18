"""
_longs_edge.py — busca edge no capturado en las SALIDAS de longs (volumen intacto).
Mide el MFE real de cada entrada (hasta donde llego a favor antes de tocar stop),
desacoplado del target actual. Hipotesis en IS, validacion en OOS.
"""
import pandas as pd, numpy as np
from collections import defaultdict
import mtf_longs as L

df = pd.read_parquet(L.__dict__.get('DATA','') or 'data/bybit-spot/processed/btcusdt_m1.parquet') \
        .sort_values('ts_ms').reset_index(drop=True)
for c in df.columns:
    if df[c].dtype == object: df[c] = df[c].fillna('')
    elif df[c].dtype == float: df[c] = df[c].fillna(0.0)

# ── replicar deteccion de entradas (identica a mtf_longs.run) ───────────────
h1 = L.resample_h1(df)
h1['atr'] = L.atr14(h1['high'].values, h1['low'].values, h1['close'].values)
h1_ctx = {int(r.ts_ms): (float(r.low), float(r.atr)) for r in h1.itertuples()}
rows = df.to_dict('records')
H1_MS = L.H1_MS

def is_entry(i, row, ts):
    sess = L.session(ts)
    if not sess: return None
    low = row['low']; levels = []
    for key, col in [('PDL','prev_day_low'),('AL','asian_low'),('WL','weekly_low'),('VAL','vp_val')]:
        v = row.get(col)
        if v and v > 0 and abs(low - v)/v <= L.LEVEL_TOL: levels.append(key)
    if not levels: return None
    lbl = '+'.join(levels); parts = lbl.split('+')
    if 'VAL' not in lbl: return None
    if len(parts) >= 3 and 'PDL' in parts and 'AL' in parts: return None
    if bool(row.get('bid_wall')): return None
    c,o,h,l = float(row['close']),float(row['open']),float(row['high']),float(row['low'])
    rng = h-l
    if rng <= 0: return None
    wick_dn = (min(c,o)-l)/rng
    if not (0.30 < wick_dn < 0.85) or c < o: return None
    obi = float(row.get('obi10_mean') or 0); delta = float(row.get('delta') or 0)
    if obi <= 0.05 and delta <= 0: return None
    h1d = h1_ctx.get((ts//H1_MS)*H1_MS)
    if h1d is None: return None
    h1l, h1a = h1d
    sl = h1l - 0.40*h1a; dist = c - sl
    if dist <= 0: return None
    if not (L.MIN_STOP <= dist/c <= L.MAX_STOP): return None
    return {'i':i,'ep':c,'sl':sl,'dist':dist,'oos':ts>=L.OOS_MS,'lbl':lbl,'sess':sess,
            'score':L.quality_score(row),'regime':str(row.get('regime') or '')}

# respetar 1-trade-a-la-vez con la salida ACTUAL para fijar el set real de entradas
def forward_profile(e):
    """Camina FORWARD barras desde la entrada; mide MFE/MAE y milestones antes del stop."""
    i, ep, sl, dist = e['i'], e['ep'], e['sl'], e['dist']
    mfe = mae = 0.0; stop_bar = None; milestones = {}
    cvd_streak = 0
    for j in range(i+1, min(i+1+L.FORWARD, len(rows))):
        r = rows[j]
        hi_r = (r['high']-ep)/dist; lo_r = (ep-r['low'])/dist  # lo_r adverso
        mfe = max(mfe, hi_r); mae = max(mae, lo_r)
        for m in (0.5,1.0,1.5,2.0,2.5,3.0):
            if m not in milestones and mfe >= m: milestones[m] = j-i
        if r['low'] <= sl and stop_bar is None:
            stop_bar = j-i
            break
    return mfe, mae, stop_bar, milestones

# fijar set real de entradas (1 a la vez con salida actual)
taken = []
in_t = False; free_at = -1
for i, row in enumerate(rows):
    ts = int(row['ts_ms'])
    if i < free_at: continue
    e = is_entry(i, row, ts)
    if e is None: continue
    # determinar bar de cierre con salida ACTUAL (stop/target/cvd/forward)
    ep,sl,dist = e['ep'],e['sl'],e['dist']
    reg = e['regime']; tgt = 1.5 if reg=='Chop' else (3.0 if reg=='Expansion' else 2.0)
    tp = ep + tgt*dist; cvd_streak=0; close_bar=None
    for j in range(i+1, min(i+1+L.FORWARD, len(rows))):
        r = rows[j]; obi=float(r.get('obi10_mean') or 0); cs=float(r.get('cvd_slope') or 0)
        cvd_streak = (cvd_streak+1) if cs<0 else 0
        cur_r = (r['close']-ep)/dist
        if r['low']<=sl: close_bar=j; break
        if r['high']>=tp: close_bar=j; break
        if cvd_streak>=3 and obi<-0.15 and cur_r>=1.0: close_bar=j; break
    if close_bar is None: close_bar = min(i+L.FORWARD, len(rows)-1)
    mfe,mae,stop_bar,ms = forward_profile(e)
    e.update({'mfe':mfe,'mae':mae,'stop_bar':stop_bar,'ms':ms})
    taken.append(e)
    free_at = close_bar+1

print(f'Entradas totales: {len(taken)}  (IS {sum(1 for e in taken if not e["oos"])} / OOS {sum(1 for e in taken if e["oos"])})')

# ── analisis MFE: cuanto R deja en la mesa el set actual ─────────────────────
def analyze(tag, ev):
    n = len(ev)
    print(f'\n=== {tag} (n={n}) — distribucion de MFE real (techo antes de stop) ===')
    for lo,hi in [(0,0.5),(0.5,1.0),(1.0,1.5),(1.5,2.0),(2.0,2.5),(2.5,3.0),(3.0,99)]:
        k = [e for e in ev if lo <= e['mfe'] < hi]
        if k: print(f'  MFE {lo:.1f}-{hi:.1f}R: {len(k):>4} ({len(k)/n*100:>4.1f}%)')
    print('  -- % de trades que ALCANZARON cada R (antes de stop) --')
    for m in (0.5,1.0,1.5,2.0,2.5,3.0):
        reached = sum(1 for e in ev if m in e['ms'])
        print(f'    >= {m}R: {reached:>4} ({reached/n*100:>4.1f}%)')

def sim_target(ev, target):
    """AvgR/WR si usaramos un target FIJO 'target' (stop -1R) sobre el mismo set."""
    rs = []
    for e in ev:
        hit_t = target in e['ms']
        t_bar = e['ms'].get(target); s_bar = e['stop_bar']
        if hit_t and (s_bar is None or t_bar <= s_bar): rs.append(target - L.FEE_RT/(e['dist']/e['ep'])*0)  # fee approx omitida
        elif s_bar is not None: rs.append(-1.0)
        else: rs.append(0.0)  # ni target ni stop en ventana -> ~breakeven (raro)
    n=len(rs); w=sum(1 for r in rs if r>0)
    return n, w/n*100, sum(rs)/n

is_e = [e for e in taken if not e['oos']]; oos_e = [e for e in taken if e['oos']]
analyze('IS', is_e)
analyze('OOS', oos_e)

# ── politicas de SALIDA con fee REAL (0.07% notional = FEE_RT*ep/dist en R) ──
def fee_r(e):
    return L.FEE_RT * e['ep'] / e['dist']   # ~0.09-0.23 R

def walk_policy(e, policy, target=2.0, be_at=None, partial_at=None, runner_tgt=3.0):
    """Devuelve gross_r realizado de UNA entrada bajo la politica dada."""
    i, ep, sl0, dist = e['i'], e['ep'], e['sl'], e['dist']
    sl = sl0; cvd_streak = 0; got_partial = False; realized = 0.0
    for j in range(i+1, min(i+1+L.FORWARD, len(rows))):
        r = rows[j]; hi_r = (r['high']-ep)/dist; lo_r_px = r['low']
        # mover a BE
        if be_at is not None and hi_r >= be_at and sl < ep:
            sl = ep
        # parcial
        if partial_at is not None and not got_partial and hi_r >= partial_at:
            realized += 0.5 * partial_at; got_partial = True; sl = ep
        # stop
        if lo_r_px <= sl:
            stop_r = (sl-ep)/dist
            return realized + (0.5 if got_partial else 1.0)*stop_r
        # target
        tg = runner_tgt if (partial_at and got_partial) else target
        if (r['high']-ep)/dist >= tg:
            return realized + (0.5 if got_partial else 1.0)*tg
    # fin de ventana: cerrar a mercado
    rc = (rows[min(i+L.FORWARD,len(rows)-1)]['close']-ep)/dist
    return realized + (0.5 if got_partial else 1.0)*rc

def eval_policy(ev, **kw):
    rs = [walk_policy(e, None, **kw) - fee_r(e) for e in ev]
    n=len(rs); w=sum(1 for r in rs if r>0)
    return w/n*100, sum(rs)/n

policies = {
  'target 1.5R':        dict(target=1.5),
  'target 2.0R':        dict(target=2.0),
  'target 3.0R':        dict(target=3.0),
  'BE@1R + tgt 2R':     dict(target=2.0, be_at=1.0),
  'BE@1R + tgt 3R':     dict(target=3.0, be_at=1.0),
  'BE@1.5R + tgt 3R':   dict(target=3.0, be_at=1.5),
  'partial 1R+run 3R':  dict(target=2.0, partial_at=1.0, runner_tgt=3.0),
}
print('\n=== Politicas de salida — fee REAL — volumen intacto (n_IS=804 n_OOS=333) ===')
print(f'{"politica":<20} | {"IS WR":>6} {"IS AvgR":>8} | {"OOS WR":>6} {"OOS AvgR":>8}')
for name,kw in policies.items():
    wi,ai = eval_policy(is_e, **kw)
    wo,ao = eval_policy(oos_e, **kw)
    print(f'{name:<20} | {wi:>5.1f}% {ai:>+8.3f} | {wo:>5.1f}% {ao:>+8.3f}')
print(f'\nfee_real promedio: IS {sum(fee_r(e) for e in is_e)/len(is_e):.3f}R  OOS {sum(fee_r(e) for e in oos_e)/len(oos_e):.3f}R')
