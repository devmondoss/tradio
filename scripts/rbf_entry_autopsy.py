#!/usr/bin/env python3
"""
RBF Entry Autopsy — análisis barra a barra de cada trade live.

Por cada trade cerrado:
  - MAE / MFE (max adverse / favorable excursion en R)
  - Velocidad (bars hasta primer 1R en favor, bars hasta stop)
  - CVD post-entry: ¿siguió bajando o revirtió?
  - Estructura encima del stop: ¿había un nivel que barrer?
  - Pre-entry momentum: ¿cuántos de las 5 barras previas eran bajistas?
  - Tipo de muerte: inmediata vs lenta vs casi-ganó

Output: tabla detallada por trade + resumen por categoría.
"""
import json, urllib.request, urllib.parse, sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone

ROOT = Path(__file__).parent.parent
env = {}
for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip().strip('"').strip("'")

URL = env['SUPABASE_URL']
KEY = env['SUPABASE_KEY']
HEADERS = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

TABLES = {
    'BTCUSDT': 'btc_bars', 'ETHUSDT': 'eth_bars', 'BNBUSDT': 'bnb_bars',
    'SOLUSDT': 'sol_bars', 'XRPUSDT': 'xrp_bars',
}

def sb_get(path, params):
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f'{URL}/rest/v1/{path}?{qs}', headers=HEADERS)
    return json.loads(urllib.request.urlopen(req, timeout=20).read())

def fetch_trades():
    rows = sb_get('rbf_signals', {
        'select': 'id,symbol,direction,entry_price,stop_price,target_price,result_r,exit_reason,bars_held,confluence_score,session,timestamp_ms,closed_at,is_pre_breakout,veto_reason',
        'result_r': 'not.is.null',
        'order': 'timestamp_ms.asc',
        'limit': '500'
    })
    return [r for r in rows if r.get('direction') == 'Short']

def fetch_bars(symbol, ts_ms, n_before=60, n_after=90):
    """Trae n_before barras antes y n_after después del trade."""
    table = TABLES.get(symbol)
    if not table:
        return [], []

    # Barras antes
    before = sb_get(table, {
        'select': 'ts_ms,open,high,low,close,bar_delta,vr,atr,session,cvd_slope,obi_l5,vwap,regime',
        'ts_ms': f'lt.{ts_ms}',
        'order': 'ts_ms.desc',
        'limit': str(n_before)
    })
    before = list(reversed(before))

    # Barras después (incluyendo la barra de entrada)
    after = sb_get(table, {
        'select': 'ts_ms,open,high,low,close,bar_delta,vr,atr,session,cvd_slope,obi_l5,vwap,regime',
        'ts_ms': f'gte.{ts_ms}',
        'order': 'ts_ms.asc',
        'limit': str(n_after)
    })
    return before, after

def mae_mfe(entry, stop, after_bars):
    """Calcula MAE y MFE en R para un Short.
    Empieza desde barra 1 (no la de entrada) para evitar inflación por rango de la barra 0."""
    risk = abs(stop - entry)
    if risk < 1e-8:
        return 0.0, 0.0, 0, 0
    mae_r = 0.0
    mfe_r = 0.0
    bar_to_1r = None
    bar_to_mae = 0
    # barra 0 = barra de entrada (el close es el entry); su high puede inflar MAE artificialmente
    for i, b in enumerate(after_bars[1:], start=1):
        h, l = b['high'], b['low']
        favor_r  = (entry - l) / risk    # cuánto bajó (favorable Short)
        advers_r = (h - entry) / risk    # cuánto subió (adverso Short)
        if favor_r > mfe_r:
            mfe_r = favor_r
        if advers_r > mae_r:
            mae_r = advers_r
            bar_to_mae = i
        if bar_to_1r is None and favor_r >= 1.0:
            bar_to_1r = i
    return mfe_r, mae_r, bar_to_1r, bar_to_mae

def pre_entry_momentum(before_bars, n=5):
    """% de barras bajistas en las N anteriores al entry."""
    if not before_bars:
        return 0.0, 0.0
    window = before_bars[-n:]
    bearish = sum(1 for b in window if b['close'] < b['open'])
    cvd_sum = sum((b.get('bar_delta') or 0) for b in window)
    return bearish / len(window), cvd_sum

def cvd_post_entry(after_bars, n=10):
    """CVD acumulado en las N barras post-entry."""
    window = after_bars[:n]
    return sum((b.get('bar_delta') or 0) for b in window)

def classify_death(t, mfe_r, mae_r, bars_held):
    """Cómo murió el trade."""
    reason = t.get('exit_reason') or ''
    result = t.get('result_r') or 0
    if result > 0:
        return 'WIN'
    if reason == 'STOP_LOSS' and bars_held and bars_held <= 5:
        return 'FAST_STOP'       # stop inmediato (≤5 bars) — entrada mala
    if reason == 'STOP_LOSS' and mfe_r >= 1.0:
        return 'GAVE_BACK'       # llegó a 1R+ pero no activó trail, luego stop
    if reason == 'TIME_STOP' and result > -0.3:
        return 'FLAT_EXIT'       # time stop cerca de entry — mercado lateral
    if reason == 'TIME_STOP' and result <= -0.3:
        return 'SLOW_FADE'       # se fue lento en contra
    if reason == 'TRAILING_STOP' and result > 0:
        return 'WIN'
    if reason == 'TRAILING_STOP' and result < 0:
        return 'TRAIL_STOP_LOSS' # rarísimo
    return 'OTHER'

def fmt_ts(ms):
    if not ms: return ''
    return datetime.fromtimestamp(ms/1000, tz=timezone.utc).strftime('%m-%d %H:%M')

def main():
    print('Fetching trades...', flush=True)
    trades = fetch_trades()
    print(f'  {len(trades)} trades Short con resultado\n', flush=True)

    results = []
    death_types = defaultdict(list)
    score_categories = defaultdict(list)

    for i, t in enumerate(trades):
        sym       = t['symbol']
        entry     = t['entry_price']
        stop      = t['stop_price']
        target    = t['target_price']
        result_r  = t['result_r']
        score     = t['confluence_score']
        session   = t['session'] or ''
        ts_ms     = t['timestamp_ms']
        bars_held = t.get('bars_held') or 0
        is_pre    = t.get('is_pre_breakout') or False

        print(f'  [{i+1}/{len(trades)}] {sym} {fmt_ts(ts_ms)} score={score} r={result_r:+.2f}', flush=True)

        before, after = fetch_bars(sym, ts_ms)
        if not after:
            print(f'    !! sin barras after, skip')
            continue

        # Limitar after al tiempo real del trade usando closed_at
        closed_at = t.get('closed_at')
        if closed_at:
            import re
            # closed_at es ISO 8601; convertir a ms
            try:
                from datetime import datetime, timezone
                ca = closed_at.replace('Z', '+00:00')
                closed_ms = int(datetime.fromisoformat(ca).timestamp() * 1000)
                after = [b for b in after if b['ts_ms'] <= closed_ms]
            except Exception:
                pass
        if not after:
            after = [before[-1]] if before else []
            continue

        mfe_r, mae_r, bar_to_1r, bar_to_mae = mae_mfe(entry, stop, after)
        bear_pct, pre_cvd = pre_entry_momentum(before, n=5)
        post_cvd_10 = cvd_post_entry(after, n=10)
        post_cvd_5  = cvd_post_entry(after, n=5)
        death       = classify_death(t, mfe_r, mae_r, bars_held)

        # ¿Precio bajó primero 0.5R+ y luego volvió a entry? → reversión post-favorable
        first_low = min((b['low'] for b in after[1:8]), default=entry)
        went_favorable = (entry - first_low) / abs(stop - entry) >= 0.5 if abs(stop - entry) > 1e-8 else False
        came_back      = any(b['high'] >= entry * 0.9998 for b in after[3:15]) if went_favorable else False
        pullback = went_favorable and came_back

        # Velocidad de ganadores: ¿en cuántas barras llegó a 1R?
        # Velocidad de perdedores: ¿cuándo fue el max drawdown?

        row = {
            'sym': sym, 'ts': fmt_ts(ts_ms), 'session': session[:3],
            'score': score, 'result_r': result_r,
            'exit_reason': (t.get('exit_reason') or '')[:8],
            'bars_held': bars_held,
            'mfe_r': round(mfe_r, 2), 'mae_r': round(mae_r, 2),
            'bar_to_1r': bar_to_1r, 'bar_to_mae': bar_to_mae,
            'bear_pct_pre': round(bear_pct, 2),
            'pre_cvd_5b': round(pre_cvd, 0),
            'post_cvd_5b': round(post_cvd_5, 0),
            'post_cvd_10b': round(post_cvd_10, 0),
            'pullback': pullback,
            'death': death,
            'is_pre': is_pre,
        }
        results.append(row)
        death_types[death].append(row)
        score_categories[score].append(row)

    print('\n' + '='*110)
    print(f'{"#":>3} {"Sym":>7} {"Date":>11} {"Ses":>3} {"Sc":>2} {"R":>6} {"Exit":>8} {"Bars":>4} '
          f'{"MFE":>5} {"MAE":>5} {"1R@":>4} {"MAE@":>5} '
          f'{"Bear%":>5} {"PreCVD":>7} {"PostCVD5":>9} {"Pullbk":>6} {"Death"}')
    print('-'*110)
    for i, r in enumerate(results):
        win_mark = 'W' if r['result_r'] > 0 else 'L'
        sc_str   = str(r['score']) if r['score'] is not None else '-'
        pb_str   = 'SI' if r['pullback'] else 'NO'
        print(f'{i+1:>3} {r["sym"]:>7} {r["ts"]:>11} {r["session"]:>3} {sc_str:>2} '
              f'{r["result_r"]:>+6.2f} {r["exit_reason"]:>8} {r["bars_held"]:>4} '
              f'{r["mfe_r"]:>5.2f} {r["mae_r"]:>5.2f} '
              f'{str(r["bar_to_1r"] or "-"):>4} {r["bar_to_mae"]:>5} '
              f'{r["bear_pct_pre"]:>5.0%} {r["pre_cvd_5b"]:>+7.0f} {r["post_cvd_5b"]:>+9.0f} '
              f'{pb_str:>6} {win_mark} {r["death"]}')

    print('\n' + '='*80)
    print('ANÁLISIS POR TIPO DE MUERTE')
    print('-'*80)
    for death_type in ['WIN', 'FAST_STOP', 'GAVE_BACK', 'FLAT_EXIT', 'SLOW_FADE', 'TRAIL_STOP_LOSS', 'OTHER']:
        group = death_types.get(death_type, [])
        if not group: continue
        avg_r    = sum(r['result_r'] for r in group) / len(group)
        avg_mfe  = sum(r['mfe_r'] for r in group) / len(group)
        avg_mae  = sum(r['mae_r'] for r in group) / len(group)
        avg_bars = sum(r['bars_held'] for r in group) / len(group)
        avg_bear = sum(r['bear_pct_pre'] for r in group) / len(group)
        avg_post = sum(r['post_cvd_5b'] for r in group) / len(group)
        pb_pct   = sum(1 for r in group if r['pullback']) / len(group)
        print(f'{death_type:>18} | n={len(group):2d} | avg_R={avg_r:+.3f} | '
              f'MFE={avg_mfe:.2f}R | MAE={avg_mae:.2f}R | bars={avg_bars:.1f} | '
              f'bear_pre={avg_bear:.0%} | postCVD5={avg_post:+.0f} | pullback={pb_pct:.0%}')

    print('\n' + '='*80)
    print('PATRONES DISCRIMINANTES — WINNERS vs LOSERS')
    print('-'*80)
    winners = [r for r in results if r['result_r'] > 0]
    losers  = [r for r in results if r['result_r'] <= 0]

    def avg(lst, key):
        vals = [x[key] for x in lst if x[key] is not None]
        return sum(vals)/len(vals) if vals else 0

    metrics = [
        ('MFE (R)', 'mfe_r'),
        ('MAE (R)', 'mae_r'),
        ('Bars held', 'bars_held'),
        ('Bear% pre-entry', 'bear_pct_pre'),
        ('Pre-CVD 5b', 'pre_cvd_5b'),
        ('Post-CVD 5b', 'post_cvd_5b'),
        ('Post-CVD 10b', 'post_cvd_10b'),
    ]
    print(f'{"Métrica":>22} | {"Winners":>10} | {"Losers":>10} | Diferencia')
    print('-'*70)
    for label, key in metrics:
        w = avg(winners, key)
        l = avg(losers, key)
        print(f'{label:>22} | {w:>10.3f} | {l:>10.3f} | {w-l:+.3f}')

    # Pullback rate
    w_pb = sum(1 for r in winners if r['pullback']) / len(winners) if winners else 0
    l_pb = sum(1 for r in losers  if r['pullback']) / len(losers)  if losers  else 0
    print(f'{"Pullback rate":>22} | {w_pb:>10.1%} | {l_pb:>10.1%} | {w_pb-l_pb:+.1%}')

    print('\n' + '='*80)
    print('FAST_STOP AUTOPSY — entradas que se revirtieron inmediatamente')
    print('-'*80)
    fast = death_types.get('FAST_STOP', [])
    for r in fast:
        print(f'  {r["sym"]:>7} {r["ts"]} ses={r["session"]} score={r["score"]} '
              f'bear_pre={r["bear_pct_pre"]:.0%} pre_cvd={r["pre_cvd_5b"]:+.0f} '
              f'post_cvd5={r["post_cvd_5b"]:+.0f} bars={r["bars_held"]}')

    print('\n' + '='*80)
    print('GAVE_BACK AUTOPSY — llegaron a 1R+ pero no salieron bien')
    print('-'*80)
    gave_back = death_types.get('GAVE_BACK', [])
    for r in gave_back:
        print(f'  {r["sym"]:>7} {r["ts"]} MFE={r["mfe_r"]:.2f}R MAE={r["mae_r"]:.2f}R '
              f'1R@bar={r["bar_to_1r"]} bars_held={r["bars_held"]} ses={r["session"]}')

    print('\n' + '='*80)
    print('SESSIONS — WR y avg_R por sesión')
    print('-'*80)
    sessions = defaultdict(list)
    for r in results:
        sessions[r['session']].append(r)
    for ses, group in sorted(sessions.items()):
        wins = sum(1 for r in group if r['result_r'] > 0)
        avg_r = sum(r['result_r'] for r in group) / len(group)
        print(f'  {ses:>3} | n={len(group):2d} | WR={wins/len(group):.0%} | avg_R={avg_r:+.3f}')

    print('\n' + '='*80)
    print('PRE-CVD GATE ANALYSIS — ¿Pre-CVD positivo = fakeout?')
    print('-'*80)
    pos_cvd  = [r for r in results if r['pre_cvd_5b'] > 0]
    neg_cvd  = [r for r in results if r['pre_cvd_5b'] <= 0]
    def group_stats(group, label):
        if not group: return
        wins = sum(1 for r in group if r['result_r'] > 0)
        avg_r = sum(r['result_r'] for r in group) / len(group)
        avg_mae = sum(r['mae_r'] for r in group) / len(group)
        print(f'  {label:>20} | n={len(group):2d} | WR={wins/len(group):.0%} | avg_R={avg_r:+.3f} | avg_MAE={avg_mae:.2f}R')
    group_stats(pos_cvd,  'PreCVD > 0 (compradores)')
    group_stats(neg_cvd,  'PreCVD <= 0 (vendedores)')
    # gate >+200 vs resto
    pos200 = [r for r in results if r['pre_cvd_5b'] > 200]
    neg200 = [r for r in results if r['pre_cvd_5b'] <= 200]
    group_stats(pos200, 'PreCVD > +200')
    group_stats(neg200, 'PreCVD <= +200')

    print('\n' + '='*80)
    print('MAE BUCKETS — entradas por MAE real')
    print('-'*80)
    mae_buckets = {'MAE<0.5R': [], '0.5-1R': [], '1-2R': [], '>2R': []}
    for r in results:
        m = r['mae_r']
        if   m < 0.5:  mae_buckets['MAE<0.5R'].append(r)
        elif m < 1.0:  mae_buckets['0.5-1R'].append(r)
        elif m < 2.0:  mae_buckets['1-2R'].append(r)
        else:          mae_buckets['>2R'].append(r)
    for label, group in mae_buckets.items():
        if not group: continue
        wins = sum(1 for r in group if r['result_r'] > 0)
        avg_r = sum(r['result_r'] for r in group) / len(group)
        print(f'  {label:>10} | n={len(group):2d} | WR={wins/len(group):.0%} | avg_R={avg_r:+.3f}')

    print('\n' + '='*80)
    print('SCORE vs DEATH TYPE')
    print('-'*80)
    for score in sorted(score_categories, key=lambda x: (x is None, x)):
        group = score_categories[score]
        deaths = defaultdict(int)
        for r in group: deaths[r['death']] += 1
        total_r = sum(r['result_r'] for r in group)
        wins = sum(1 for r in group if r['result_r'] > 0)
        print(f'  Score={score} n={len(group):2d} WR={wins/len(group):.0%} total_R={total_r:+.2f} | ' +
              ' '.join(f'{k}:{v}' for k, v in sorted(deaths.items())))

    # Guardar JSON para análisis posterior
    out = ROOT / 'scripts' / '_entry_autopsy.json'
    with open(out, 'w') as f:
        json.dump(results, f, indent=2)
    print(f'\nDatos guardados en {out}')

if __name__ == '__main__':
    main()
