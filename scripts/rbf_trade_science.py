#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
"""
Análisis profundo de trades RBF.
Por cada trade cerrado en Supabase:
  - Descarga 90 barras antes y 60 después del entry
  - Responde: por qué ganó/perdió, entrada buena/mala, oportunidad anticipada
  - Agrega patrones de ganadores vs perdedores
"""
import json, os, urllib.request, urllib.parse, math
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

ROOT = Path(__file__).parent.parent
_env = {}
for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        _env[k.strip()] = v.strip().strip('"').strip("'")

URL = _env.get('SUPABASE_URL', '')
KEY = _env.get('SUPABASE_KEY', '')

SYM_TABLES = {
    'BTCUSDT': 'btc_bars', 'ETHUSDT': 'eth_bars',
    'BNBUSDT': 'bnb_bars', 'SOLUSDT': 'sol_bars', 'XRPUSDT': 'xrp_bars',
}

def sb(table, params):
    rows, lim, off = [], 1000, 0
    while True:
        qs = urllib.parse.urlencode({**params, 'limit': lim, 'offset': off})
        req = urllib.request.Request(
            f'{URL}/rest/v1/{table}?{qs}',
            headers={'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
        )
        chunk = json.loads(urllib.request.urlopen(req, timeout=30).read())
        rows.extend(chunk)
        if len(chunk) < lim: break
        off += lim
    return rows

def fetch_bars_around(sym, ts_ms, pre=90, post=60):
    table = SYM_TABLES.get(sym)
    if not table: return [], []
    start = ts_ms - pre * 60_000
    end   = ts_ms + post * 60_000
    rows = sb(table, {
        'select': 'ts_ms,open,high,low,close,vr,atr,bar_delta,obi_l5,cvd_slope,dz,vwap,session,regime',
        'ts_ms': f'gte.{start}',
        'order': 'ts_ms.asc',
    })
    rows = [r for r in rows if r['ts_ms'] <= end]
    entry_idx = next((i for i, r in enumerate(rows) if r['ts_ms'] >= ts_ms), None)
    if entry_idx is None:
        return rows, []
    return rows[:entry_idx+1], rows[entry_idx+1:]

def cvd_trend_quality(window):
    """
    Qué tan consistente fue la presión vendedora durante el rango.
    Retorna: (pct_neg_bars, cvd_sum, monotonicity_score)
    """
    deltas = [r.get('bar_delta') or 0 for r in window]
    if not deltas: return 0, 0, 0
    pct_neg = sum(1 for d in deltas if d < 0) / len(deltas)
    cvd_sum = sum(deltas)
    # Monotonicity: cuántas barras el CVD acumulado seguía bajando
    cum = 0; drops = 0
    for d in deltas:
        cum += d
        if d < 0: drops += 1
    mono = drops / len(deltas)
    return pct_neg, cvd_sum, mono

def find_early_entry(pre_bars, entry_price, stop_price, range_lo, range_hi):
    """
    ¿Existía una entrada anticipada (pre-breakout)?
    Busca barras donde VR >= 1.5 y close estuvo cerca de range_lo antes del breakout real.
    Retorna cuántas barras antes + el VR que tenía.
    """
    candidates = []
    for i, b in enumerate(pre_bars):
        vr = b.get('vr') or 0
        cl = b.get('close') or 0
        # Zona pre-breakout: close dentro del 0.1% por encima de range_low
        if vr >= 1.5 and cl <= range_lo * 1.001 and cl > range_lo * 0.995:
            bars_before = len(pre_bars) - i
            candidates.append({'bars_before': bars_before, 'vr': vr, 'close': cl, 'ts': b['ts_ms']})
    return candidates[-3:] if candidates else []  # las 3 más recientes

def post_entry_momentum(post_bars, entry, stop, is_short=True):
    """
    Analiza los primeros N bars post-entrada.
    - first_bar_favorable: la primera barra fue en la dirección correcta
    - max_favorable_r: máximo R alcanzado antes del primer retroceso
    - bars_to_1R: cuántas barras tardó en llegar a 1R (o None si no llegó)
    """
    risk = abs(entry - stop)
    if risk < 1e-10: return {}
    first_favorable = None
    max_r = 0; bars_to_1r = None
    best = entry
    for i, b in enumerate(post_bars[:60]):
        h, l, c = b.get('high',0), b.get('low',0), b.get('close',0)
        if is_short:
            if l < best: best = l
            fav_r = (entry - best) / risk
        else:
            if h > best: best = h
            fav_r = (best - entry) / risk
        if fav_r > max_r: max_r = fav_r
        if bars_to_1r is None and fav_r >= 1.0:
            bars_to_1r = i + 1
        if first_favorable is None:
            if is_short: first_favorable = c < entry
            else:        first_favorable = c > entry
    return {
        'first_bar_ok':  first_favorable,
        'max_r':         round(max_r, 3),
        'bars_to_1r':    bars_to_1r,
    }

def breakout_quality(entry_bar, range_lo, range_hi):
    """
    Calidad del breakout: extensión, VR, DZ, OBI.
    """
    cl   = entry_bar.get('close') or 0
    lo   = entry_bar.get('low')   or cl
    vr   = entry_bar.get('vr')    or 0
    dz   = entry_bar.get('dz')    or 0
    obi  = entry_bar.get('obi_l5') or 0
    ext  = (range_lo - cl) / range_lo if range_lo > 0 else 0
    # Penetración: qué % del rango rompió
    rng  = range_hi - range_lo
    pen  = (range_lo - cl) / rng if rng > 0 else 0
    return {
        'ext_pct':   round(ext * 100, 3),
        'pen_ratio': round(pen, 3),
        'vr':        round(vr, 2),
        'dz':        round(dz, 2),
        'obi':       round(obi, 3),
    }

# ── Main ──────────────────────────────────────────────────────────────────────

print("Cargando trades cerrados de Supabase...")
trades = sb('rbf_signals', {
    'select': '*',
    'result_r': 'not.is.null',
    'order': 'timestamp_ms.asc',
})
print(f"  {len(trades)} trades\n")

results = []
for i, t in enumerate(trades):
    sym    = t['symbol']
    ts     = int(t['timestamp_ms'])
    entry  = float(t.get('entry_price') or 0)
    stop   = float(t.get('stop_price')  or 0)
    target = float(t.get('target_price') or 0)
    r      = float(t.get('result_r')    or 0)
    reason = t.get('exit_reason') or '?'
    ses    = t.get('session')     or '?'
    score  = t.get('confluence_score')
    is_win = r > 0
    is_short = t.get('direction','Short') == 'Short'

    print(f"  [{i+1:02d}/{len(trades)}] {sym} {ses} {r:+.2f}R {reason}...", end=' ', flush=True)
    pre, post = fetch_bars_around(sym, ts, pre=90, post=60)
    if not pre:
        print("SIN DATOS")
        continue

    entry_bar = pre[-1]

    # Estimar range_lo/range_hi del rango de consolidación
    # Usar 15-60 barras previas a la entrada
    windows = [pre[-rw-1:-1] for rw in [15, 20, 30, 45, 60] if rw < len(pre)]
    best_window = None
    for win in windows:
        if not win: continue
        hi = max(b['high'] for b in win)
        lo = min(b['low']  for b in win)
        rng_pct = (hi - lo) / lo * 100 if lo > 0 else 0
        if 0.08 <= rng_pct <= 0.55:
            best_window = win
            break

    if best_window:
        range_hi = max(b['high'] for b in best_window)
        range_lo = min(b['low']  for b in best_window)
    else:
        range_hi = stop  # fallback
        range_lo = entry * 1.001

    pct_neg, cvd_sum, mono = cvd_trend_quality(best_window or pre[-30:-1])
    early    = find_early_entry(pre[:-1], entry, stop, range_lo, range_hi)
    momentum = post_entry_momentum(post, entry, stop, is_short)
    bq       = breakout_quality(entry_bar, range_lo, range_hi)

    # Expansion bars en 25 previas (señal de mercado en tendencia fuerte)
    exp_window = pre[-26:-1]
    exp_bars   = sum(1 for b in exp_window if b.get('regime') == 'Expansion')

    # Velocidad del trade: llegó a 1R en los primeros 5 bars?
    fast_win = momentum.get('bars_to_1r') is not None and momentum['bars_to_1r'] <= 5

    # ¿Pudo entrar antes?
    could_early = bool(early)
    earliest_bars_before = early[0]['bars_before'] if early else None

    rec = {
        'idx':     i+1,
        'sym':     sym,
        'ses':     ses,
        'r':       r,
        'reason':  reason,
        'is_win':  is_win,
        'score':   score,

        # Calidad del rango
        'pct_neg_bars': round(pct_neg * 100, 1),
        'cvd_sum':      round(cvd_sum, 0),
        'cvd_mono':     round(mono * 100, 1),

        # Calidad del breakout
        'ext_pct':    bq['ext_pct'],
        'pen_ratio':  bq['pen_ratio'],
        'vr':         bq['vr'],
        'dz':         bq['dz'],
        'obi':        bq['obi'],

        # Expansion previa
        'exp_bars': exp_bars,

        # Momentum post-entrada
        'first_bar_ok': momentum.get('first_bar_ok'),
        'max_r':        momentum.get('max_r', 0),
        'bars_to_1r':   momentum.get('bars_to_1r'),
        'fast_win':     fast_win,

        # Entrada anticipada
        'could_early':          could_early,
        'earliest_bars_before': earliest_bars_before,
    }
    results.append(rec)
    tag = 'WIN' if is_win else 'LOSS'
    print(f"{tag} cvd={cvd_sum:+.0f} ext={bq['ext_pct']:.2f}% vr={bq['vr']:.1f}x exp={exp_bars} early={'SI' if could_early else 'NO'}")

# ── Análisis agregado ─────────────────────────────────────────────────────────
print('\n' + '='*72)
print('  ANALISIS PROFUNDO RBF')
print('='*72)

wins  = [r for r in results if r['is_win']]
losses= [r for r in results if not r['is_win']]

def avg(lst, key):
    vals = [x[key] for x in lst if x.get(key) is not None]
    return round(sum(vals)/len(vals), 2) if vals else None

def pct(lst, key, check=lambda v: v):
    vals = [x for x in lst if x.get(key) is not None]
    return round(sum(1 for x in vals if check(x[key])) / len(vals) * 100, 1) if vals else None

print(f"\n  GANADORES n={len(wins)}  PERDEDORES n={len(losses)}\n")

# 1. Calidad del rango
print("  1. CALIDAD DEL RANGO (CVD durante consolidación)")
print(f"  {'Métrica':<28} {'Ganadores':>12} {'Perdedores':>12}")
print(f"  {'-'*28} {'-'*12} {'-'*12}")
for key, label in [('pct_neg_bars','% barras cvd neg'), ('cvd_sum','CVD total rango'), ('cvd_mono','% consistency')]:
    w = avg(wins, key); l = avg(losses, key)
    print(f"  {label:<28} {str(w):>12} {str(l):>12}")

# 2. Calidad del breakout
print(f"\n  2. CALIDAD DEL BREAKOUT (barra de entrada)")
print(f"  {'Métrica':<28} {'Ganadores':>12} {'Perdedores':>12}")
print(f"  {'-'*28} {'-'*12} {'-'*12}")
for key, label in [('ext_pct','Extensión % más allá'), ('pen_ratio','Penetración ratio'), ('vr','VR en breakout'), ('dz','DZ')]:
    w = avg(wins, key); l = avg(losses, key)
    print(f"  {label:<28} {str(w):>12} {str(l):>12}")

# 3. Expansion previa
print(f"\n  3. EXPANSION BARS PREVIAS (últimas 25 barras)")
print(f"  {'Categoría':<28} {'WR%':>8} {'n':>6}")
for exp_max in [0, 1, 2, 3, 5, 10]:
    subset = [r for r in results if r['exp_bars'] <= exp_max]
    if not subset: continue
    wr = sum(1 for r in subset if r['is_win']) / len(subset) * 100
    print(f"  exp_bars <= {exp_max:<17} {wr:>7.1f}% {len(subset):>6}")

# 4. Momentum post-entrada
print(f"\n  4. MOMENTUM POST-ENTRADA")
print(f"  {'Métrica':<35} {'Ganadores':>10} {'Perdedores':>10}")
print(f"  {'-'*35} {'-'*10} {'-'*10}")
w_fb  = pct(wins,   'first_bar_ok', lambda v: v is True)
l_fb  = pct(losses, 'first_bar_ok', lambda v: v is True)
w_mr  = avg(wins,   'max_r')
l_mr  = avg(losses, 'max_r')
w_fw  = pct(wins,   'fast_win', lambda v: v)
l_fw  = pct(losses, 'fast_win', lambda v: v)
w_b1r = avg(wins,   'bars_to_1r')
l_b1r = avg(losses, 'bars_to_1r')
print(f"  {'1a barra favorable %':<35} {str(w_fb)+'%':>10} {str(l_fb)+'%':>10}")
print(f"  {'Max R alcanzado antes de salir':<35} {str(w_mr)+'R':>10} {str(l_mr)+'R':>10}")
print(f"  {'Llegan a 1R en <=5 bars %':<35} {str(w_fw)+'%':>10} {str(l_fw)+'%':>10}")
print(f"  {'Barras promedio hasta 1R':<35} {str(w_b1r):>10} {str(l_b1r):>10}")

# 5. Entradas anticipadas
early_yes = [r for r in results if r['could_early']]
early_wins = [r for r in early_yes if r['is_win']]
print(f"\n  5. OPORTUNIDADES DE ENTRADA ANTICIPADA")
print(f"  Trades con señal pre-breakout disponible: {len(early_yes)}/{len(results)}")
if early_yes:
    wr_early = len(early_wins)/len(early_yes)*100
    avg_bars = sum(r['earliest_bars_before'] for r in early_yes if r['earliest_bars_before']) / len(early_yes)
    print(f"  WR de esos trades (post entry): {wr_early:.1f}%")
    print(f"  Promedio bars antes del breakout real: {avg_bars:.1f}")
    # Simulación: si hubieras entrado N bars antes en el borde del rango
    print(f"  (Entrada pre-breakout habría capturado el move completo)")

# 6. Por qué fallan los perdedores
print(f"\n  6. ANATOMIA DE LOS PERDEDORES")
by_reason = defaultdict(list)
for r in losses: by_reason[r['reason']].append(r)
for rsn, grp in sorted(by_reason.items(), key=lambda x: -len(x[1])):
    avg_exp = avg(grp, 'exp_bars')
    avg_ext = avg(grp, 'ext_pct')
    avg_vr  = avg(grp, 'vr')
    avg_fb  = pct(grp, 'first_bar_ok', lambda v: v is True)
    print(f"  {rsn:<20} n={len(grp):2d}  exp_avg={avg_exp}  ext={avg_ext}%  vr={avg_vr}x  1a_bar_ok={avg_fb}%")

# 7. Lo más accionable: diferenciadores claros
print(f"\n  7. DIFERENCIADORES CLAROS (qué separa ganadores de perdedores)")
checks = [
    ('ext_pct >= 0.15',   lambda r: r['ext_pct'] >= 0.15),
    ('vr >= 5.0',         lambda r: r['vr'] >= 5.0),
    ('first_bar_ok=True', lambda r: r['first_bar_ok'] is True),
    ('exp_bars <= 2',     lambda r: r['exp_bars'] <= 2),
    ('cvd_sum < -500',    lambda r: r['cvd_sum'] < -500),
    ('pct_neg >= 60%',    lambda r: r['pct_neg_bars'] >= 60),
]
print(f"  {'Filtro':<28} {'WR%':>7} {'n':>5} {'AvgR':>7}")
print(f"  {'-'*28} {'-'*7} {'-'*5} {'-'*7}")
for label, fn in checks:
    subset = [r for r in results if fn(r)]
    if not subset: continue
    wr   = sum(1 for r in subset if r['is_win']) / len(subset) * 100
    ar   = sum(r['r'] for r in subset) / len(subset)
    print(f"  {label:<28} {wr:>6.1f}% {len(subset):>5} {ar:>+6.2f}R")

# 8. Score vs WR
print(f"\n  8. CONFLUENCE SCORE vs WR")
for sc in [1, 2, 3, 4, 5, 6, 7]:
    grp = [r for r in results if r.get('score') == sc]
    if not grp: continue
    wr = sum(1 for r in grp if r['is_win']) / len(grp) * 100
    ar = sum(r['r'] for r in grp) / len(grp)
    print(f"  score={sc}  n={len(grp):2d}  WR={wr:5.1f}%  avgR={ar:+.3f}")

# 9. Por sesión: avg_r y momentum
print(f"\n  9. POR SESION: momentum post-entrada")
by_ses = defaultdict(list)
for r in results: by_ses[r['ses']].append(r)
for ses, grp in sorted(by_ses.items()):
    wr  = sum(1 for r in grp if r['is_win']) / len(grp) * 100
    ar  = sum(r['r'] for r in grp) / len(grp)
    fb  = pct(grp, 'first_bar_ok', lambda v: v is True)
    print(f"  {ses:<20} n={len(grp):2d}  WR={wr:5.1f}%  avgR={ar:+.3f}  1a_bar_ok={fb}%")

print('\n' + '='*72 + '\n')
