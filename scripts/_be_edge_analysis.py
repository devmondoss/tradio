"""Per-symbol edge discovery for BE — 730d backtest + 7 features nuevas."""
import sys, time, itertools, math
sys.path.insert(0, 'apps/rbf-review/api')
import be_backtest_script as bt
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

bt.SESSIONS_OK    = {'LondonNyOverlap'}   # solo produccion
bt.TIME_STOP_BARS = 9999

RANGE_W = bt.RANGE_WINDOW  # 8 barras

def linslope(vals):
    """Pendiente lineal simple sobre índice 0..n-1."""
    n = len(vals)
    if n < 2: return 0.0
    xm = (n - 1) / 2.0
    ym = sum(vals) / n
    cov = sum((i - xm) * (v - ym) for i, v in enumerate(vals))
    var = sum((i - xm) ** 2 for i in range(n))
    return cov / var if var > 0 else 0.0

def detect_all(sym, bars):
    trades, last_sig = [], -bt.COOLDOWN_BARS
    n = len(bars)
    for i in range(bt.COOLDOWN_BARS, n - 40):
        b = bars[i]
        ses = b.get('session') or ''
        if ses not in bt.SESSIONS_OK: continue
        if b.get('bar_delta') is None: continue
        if i - last_sig < bt.COOLDOWN_BARS: continue
        win = bars[i - RANGE_W : i]
        if len(win) < RANGE_W: continue
        hi  = max(x['high'] for x in win)
        lo  = min(x['low']  for x in win)
        rng = hi - lo
        rp  = rng / b['close'] * 100.0
        if rp < bt.RANGE_MIN_PCT or rp > bt.RANGE_MAX_PCT: continue
        deltas = [x.get('bar_delta') for x in win]
        if any(d is None for d in deltas): continue
        range_cvd = sum(deltas)
        if range_cvd <= 0: continue
        pre_cvd = sum(deltas[-bt.PRE_CVD_BARS:])
        if pre_cvd >= 0: continue
        flip = abs(pre_cvd) / range_cvd
        if flip < bt.CVD_FLIP_RATIO_MIN: continue
        close = b['close']
        if close >= lo: continue
        vol_hist = [bars[j]['volume'] for j in range(max(0, i - bt.VR_WINDOW), i)]
        avg_vol  = sum(vol_hist) / len(vol_hist) if vol_hist else 1.0
        vr       = b['volume'] / avg_vol if avg_vol > 0 else 0.0
        if vr < bt.VR_MIN: continue
        if (b.get('bar_delta') or 0) >= 0: continue
        bh, bl, bo = b['high'], b['low'], b['open']
        bar_rng = bh - bl
        if bar_rng > 0:
            if (close - bl)          / bar_rng > bt.CLOSE_LOC_MAX: continue
            if abs(close - bo)       / bar_rng < bt.BEAR_BODY_MIN: continue
            if (bh - max(bo, close)) / bar_rng > bt.UPPER_WICK_MAX: continue
        stop_p = max(hi, b['high'])
        risk   = stop_p - close
        if risk < 1e-8: continue
        target = close - 2 * risk
        if b['low'] <= target: continue
        hour = datetime.fromtimestamp(b['ts_ms'] / 1000, tz=timezone.utc).hour

        # ── F1: posicion del rango vs high/low 50 barras previas ─────────────
        lb = bars[max(0, i - RANGE_W - 50) : i - RANGE_W]
        if len(lb) >= 10:
            lb_hi = max(x['high'] for x in lb)
            lb_lo = min(x['low']  for x in lb)
            span  = lb_hi - lb_lo
            pos   = min(max((hi - lb_lo) / span if span > 1e-10 else 0.5, 0.0), 1.0)
        else:
            pos = 0.5
        posbin = 'top' if pos > 0.70 else ('mid' if pos > 0.40 else 'bot')

        # ── F2: pendiente del precio 20 barras antes del rango ───────────────
        ps = bars[max(0, i - RANGE_W - 20) : i - RANGE_W]
        if len(ps) >= 5:
            pre_slp = (ps[-1]['close'] - ps[0]['close']) / ps[0]['close'] * 100
        else:
            pre_slp = 0.0
        slopebin = 'up' if pre_slp > 0.20 else ('dn' if pre_slp < -0.20 else 'flat')

        # ── F3: compresion — avg bar size relativa al range total ─────────────
        total_br = sum(x['high'] - x['low'] for x in win)
        avg_br   = total_br / RANGE_W
        comp_r   = avg_br / rng if rng > 1e-10 else 0.5
        compbin  = 'tight' if comp_r < 0.30 else ('normal' if comp_r < 0.50 else 'choppy')

        # ── F4: bull_bias — % barras cerrando en mitad superior del rango ────
        rng_mid = (hi + lo) / 2.0
        bull_b  = sum(1 for x in win if x['close'] > rng_mid) / RANGE_W
        biasbin = 'bull' if bull_b > 0.60 else ('mid' if bull_b > 0.40 else 'bear')

        # ── F5: cvd_slope — tendencia del CVD acumulado durante el rango ─────
        cum_d    = list(itertools.accumulate(deltas))
        avg_dabs = sum(abs(d) for d in deltas) / RANGE_W
        cvds_n   = linslope(cum_d) / (avg_dabs + 1e-10)
        cvsbin   = 'falling' if cvds_n < -0.3 else ('flat' if cvds_n < 0.3 else 'rising')

        # ── F6: cvd_mag_z — magnitud del CVD del rango vs historico ──────────
        hd = [bars[j].get('bar_delta') for j in range(max(0, i - RANGE_W - 100), i - RANGE_W)]
        hd = [d for d in hd if d is not None]
        avg_abs_h = sum(abs(d) for d in hd) / len(hd) if hd else 1.0
        cvd_z     = range_cvd / (avg_abs_h * math.sqrt(RANGE_W) + 1e-10)
        cvzbin    = 'low' if cvd_z < 0.8 else ('mid' if cvd_z < 1.8 else 'high')

        # ── F7: pre_cvd_trend — direccion del flow 20 barras antes del rango ─
        pd = [x.get('bar_delta') for x in bars[max(0, i - RANGE_W - 20) : i - RANGE_W]]
        pd = [d for d in pd if d is not None]
        pctbin = 'pos' if (sum(pd) > 0 if pd else False) else 'neg'

        sim = bars[i + 1 : i + 1 + 350]
        r, reason, dur, exit_ms = bt.simulate(sim, close, stop_p, target, ses)

        trades.append({
            'resultR': round(r, 4),
            'win':     1 if r > 0 else 0,
            # Features originales
            'session': ses,
            'hour':    hour,
            'hbin':    'AM' if hour < 12 else ('TRANS' if hour < 14 else 'PM'),
            'flip':    round(flip, 3),
            'fbin':    'lo' if flip < 0.6 else ('mid' if flip < 0.85 else ('hi' if flip < 1.0 else 'OVER')),
            'vr':      round(vr, 2),
            'vrbin':   'base' if vr < 3.5 else ('mid' if vr < 5.5 else ('hi' if vr < 9 else 'spike')),
            'ext':     round((lo - close) / rng, 3),
            'ebin':    'tight' if (lo - close) / rng < 0.25 else ('mid' if (lo - close) / rng < 0.55 else 'wide'),
            'rng':     round(rp, 4),
            'rbin':    'xs' if rp < 0.10 else ('sm' if rp < 0.16 else ('md' if rp < 0.25 else 'lg')),
            'reason':  reason,
            # Features nuevas
            'posbin':   posbin,
            'slopebin': slopebin,
            'compbin':  compbin,
            'biasbin':  biasbin,
            'cvsbin':   cvsbin,
            'cvzbin':   cvzbin,
            'pctbin':   pctbin,
        })
        last_sig = i
    return trades

# ── Cargar datos ──────────────────────────────────────────────────────────────

start_ms = int((time.time() - 730 * 86400) * 1000)
sb_first = bt.sb_earliest_ms()

sym_trades = {}
def proc(item):
    sym, tbl = item
    return sym, detect_all(sym, bt.load_bars(sym, tbl, start_ms, sb_first))

with ThreadPoolExecutor(max_workers=5) as ex:
    for res in as_completed({ex.submit(proc, item): item for item in bt.TABLES.items()}):
        sym, trades = res.result()
        sym_trades[sym] = trades

# ── Stats ─────────────────────────────────────────────────────────────────────

RISK         = 10.0
OLD_FEATURES = ['session', 'hbin', 'fbin', 'vrbin', 'ebin', 'rbin']
NEW_FEATURES = ['posbin', 'slopebin', 'compbin', 'biasbin', 'cvsbin', 'cvzbin', 'pctbin']
ALL_FEATURES = OLD_FEATURES + NEW_FEATURES

def stats(g):
    if not g: return None, None, None, None
    wr  = sum(t['win'] for t in g) / len(g)
    ar  = sum(t['resultR'] for t in g) / len(g)
    ev  = wr * 2 - (1 - wr)
    pnl = sum(t['resultR'] for t in g) * RISK
    return wr * 100, ar, ev, pnl

for sym in ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT']:
    trades = sym_trades.get(sym, [])
    wr0, ar0, ev0, pnl0 = stats(trades)
    print()
    print('=' * 72)
    s = sym.replace('USDT', '')
    print(f"  {s}   n={len(trades)}  WR={wr0:.0f}%  EV={ev0:+.3f}  PnL=${pnl0:+.1f}")
    print('=' * 72)

    singles = []
    for feat in ALL_FEATURES:
        for v in sorted(set(t[feat] for t in trades)):
            g = [t for t in trades if t[feat] == v]
            if len(g) < 5: continue
            wr_, ar_, ev_, pnl_ = stats(g)
            singles.append((ev_, feat, v, len(g), wr_, pnl_))
    singles.sort(reverse=True)

    pos_old = [(e,f,v,n,w,p) for e,f,v,n,w,p in singles if e > 0.10 and f in OLD_FEATURES]
    pos_new = [(e,f,v,n,w,p) for e,f,v,n,w,p in singles if e > 0.10 and f in NEW_FEATURES]
    neg_all = sorted([(e,f,v,n,w,p) for e,f,v,n,w,p in singles if e < -0.30])

    if pos_old:
        print("  EDGE features originales (EV>0.10):")
        for ev_, feat, v, n, wr_, pnl_ in pos_old[:5]:
            print(f"    {feat}={v:<10s}  n={n:2d}  WR={wr_:3.0f}%  EV={ev_:+.3f}  PnL=${pnl_:+.1f}")

    if pos_new:
        print("  EDGE features NUEVAS (EV>0.10)  *** ")
        for ev_, feat, v, n, wr_, pnl_ in pos_new[:7]:
            print(f"    {feat}={v:<10s}  n={n:2d}  WR={wr_:3.0f}%  EV={ev_:+.3f}  PnL=${pnl_:+.1f}")
    else:
        print("  (ninguna feature nueva con EV>0.10)")

    if neg_all:
        print("  DESTRUCCION (EV<-0.30):")
        for ev_, feat, v, n, wr_, pnl_ in neg_all[:4]:
            tag = '***' if feat in NEW_FEATURES else ''
            print(f"    {feat}={v:<10s}  n={n:2d}  WR={wr_:3.0f}%  EV={ev_:+.3f}  PnL=${pnl_:+.1f} {tag}")

    # Combos 2-features que incluyan al menos una feature nueva
    combos = []
    for f1, f2 in itertools.combinations(ALL_FEATURES, 2):
        if f1 not in NEW_FEATURES and f2 not in NEW_FEATURES:
            continue  # solo combos con al menos 1 nueva
        for v1, v2 in set((t[f1], t[f2]) for t in trades):
            g = [t for t in trades if t[f1] == v1 and t[f2] == v2]
            if len(g) < 5: continue
            wr_, ar_, ev_, pnl_ = stats(g)
            if ev_ > 0.30:
                combos.append((ev_, f1, v1, f2, v2, len(g), wr_, pnl_))
    combos.sort(reverse=True)
    if combos:
        print("  COMBOS 2-feat con feature nueva (EV>0.30):")
        for ev_, f1, v1, f2, v2, n, wr_, pnl_ in combos[:6]:
            print(f"    {f1}={v1} & {f2}={v2:<10s}  n={n:2d}  WR={wr_:3.0f}%  EV={ev_:+.3f}  PnL=${pnl_:+.1f}")

# ── Resumen global: ranking de features por impacto ──────────────────────────

print()
print('=' * 72)
print("  RANKING GLOBAL — diferencial EV(mejor bin - peor bin) por feature")
print('=' * 72)
all_trades = [t for ts in sym_trades.values() for t in ts]
feat_impact = []
for feat in ALL_FEATURES:
    evs = []
    for v in set(t[feat] for t in all_trades):
        g = [t for t in all_trades if t[feat] == v]
        if len(g) < 8: continue
        _, _, ev_, _ = stats(g)
        evs.append((ev_, v, len(g)))
    if len(evs) >= 2:
        evs.sort(reverse=True)
        impact = evs[0][0] - evs[-1][0]
        tag = ' ***' if feat in NEW_FEATURES else ''
        feat_impact.append((impact, feat, evs[0][1], evs[0][0], evs[-1][1], evs[-1][0]))

feat_impact.sort(reverse=True)
for imp, feat, best_v, best_ev, worst_v, worst_ev in feat_impact:
    tag = ' ***' if feat in NEW_FEATURES else '    '
    print(f"  {tag} {feat:<12s}  spread={imp:+.3f}  best={best_v}({best_ev:+.3f})  worst={worst_v}({worst_ev:+.3f})")

print()
print("  *** = feature nueva")
print('=' * 72)
