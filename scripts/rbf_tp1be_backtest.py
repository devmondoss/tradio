#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
"""
Backtest: compara exit logic ORIGINAL vs TP1->BE en los Shorts de rbf_signals.
Usa barras M1 locales para simular barra-a-barra.
"""
import csv, json, os, urllib.request, urllib.parse
from datetime import datetime, timezone

URL = 'https://ztdhvmcisjjyhbqlgkzm.supabase.co'
KEY = os.environ.get('SUPABASE_KEY', '')

def fetch(table, params):
    rows, lim, off = [], 1000, 0
    while True:
        qs = urllib.parse.urlencode({**params, 'limit': lim, 'offset': off})
        req = urllib.request.Request(f'{URL}/rest/v1/{table}?{qs}',
            headers={'apikey': KEY, 'Authorization': f'Bearer {KEY}'})
        chunk = json.loads(urllib.request.urlopen(req).read())
        rows.extend(chunk)
        if len(chunk) < lim: break
        off += lim
    return rows

def load_bars(path, ts_col='ts_ms'):
    bars = {}
    with open(path, encoding='utf-8', errors='replace') as f:
        for row in csv.DictReader(f):
            bars[int(row[ts_col])] = {
                'high': float(row['high']),
                'low':  float(row['low']),
                'close': float(row['close'])
            }
    return bars

def simulate_tp1(entry, stop, target, entry_ms, bars):
    """
    Simula la lógica TP1 → SL al precio de TP1 (no a breakeven).
    TP1 = 1.5R: cuando el precio llega a 1.5R de beneficio,
    el SL se mueve al precio de TP1, garantizando mínimo +1.5R.
    Trailing arranca a 2.0R (después de TP1) para movimientos extendidos.
    """
    TRAIL_R   = 2.0   # trailing arranca después de TP1
    TRAIL_K   = 1.2
    TP1_R     = 1.5   # TP1 en 1.5R (no 1.0R)
    TIME_BARS = 30
    risk = abs(entry - stop)
    if risk < 1e-10:
        return None, 'ZERO_RISK', 0
    atr_proxy = risk * 2.5

    stop_cur = stop
    best     = entry
    trailing = False
    tp1_hit  = False
    tp1_price = entry - TP1_R * risk   # para Short: precio 1.5R por debajo de entry
    bars_held = 0
    ts_list  = bars['_ts']

    # bisect manual
    lo, hi, idx = 0, len(ts_list) - 1, len(ts_list)
    while lo <= hi:
        mid = (lo + hi) // 2
        if ts_list[mid] >= entry_ms:
            idx = mid; hi = mid - 1
        else:
            lo = mid + 1

    if idx >= len(ts_list):
        return None, 'NO_DATA', 0

    for ts in ts_list[idx:idx + 500]:
        bar = bars.get(ts)
        if not bar: continue
        h, l, c = bar['high'], bar['low'], bar['close']
        bars_held += 1

        # Short: extremo favorable = low
        if l < best: best = l
        fav_r = (entry - best) / risk

        # TP1 a 1.5R: SL se mueve al precio de TP1 (no a entry/BE)
        if fav_r >= TP1_R and not tp1_hit:
            tp1_hit  = True
            stop_cur = tp1_price   # bloquea +1.5R mínimo

        # Trailing a 2.0R (tras TP1)
        if fav_r >= TRAIL_R and not trailing:
            trailing = True
        if trailing and atr_proxy > 0:
            ts_lvl = best + TRAIL_K * atr_proxy
            if ts_lvl < stop_cur:
                stop_cur = ts_lvl

        stop_hit   = h >= stop_cur
        target_hit = l <= target

        if stop_hit and target_hit:
            # misma barra: si target también tocó, asumimos TARGET
            return (entry - target) / risk, 'TARGET', bars_held
        elif target_hit:
            return (entry - target) / risk, 'TARGET', bars_held
        elif stop_hit:
            r_exit = (entry - stop_cur) / risk
            if trailing:
                reason = 'TRAILING'
            elif tp1_hit:
                reason = 'TP1_STOP'
            else:
                reason = 'STOP'
            return r_exit, reason, bars_held

        if bars_held >= TIME_BARS and (entry - c) / risk < 0:
            return (entry - c) / risk, 'TIME_STOP', bars_held

    return None, 'TIMEOUT', bars_held

def main():
    print("Cargando barras M1 (TP1=1.5R → SL al precio TP1, trailing a 2.0R)...")
    btc = load_bars('dataset/btcusdt_90d_m1.csv', ts_col='ts_ms')
    btc.update(load_bars('dataset/btc_bars_full.csv'))
    eth = load_bars('dataset/eth_bars_full.csv')
    sol = load_bars('dataset/sol_bars_full.csv')
    bnb = load_bars('dataset/bnb_bars_full.csv')

    sym_bars = {'BTCUSDT': btc, 'ETHUSDT': eth, 'SOLUSDT': sol, 'BNBUSDT': bnb}
    for sym, d in sym_bars.items():
        d['_ts'] = sorted(k for k in d if k != '_ts')
        print(f"  {sym}: {len(d['_ts'])} barras")

    trades = fetch('rbf_signals', {
        'select': '*', 'result_r': 'not.is.null',
        'direction': 'eq.Short', 'order': 'timestamp_ms.asc'
    })
    print(f"\nShorts en Supabase: {len(trades)}\n")

    print(f"{'#':>3}  {'Fecha':16}  {'Sym':8}  {'Sc':3}  {'ORIG':>8}  {'TP1_15R':>8}  {'Razon':13}  D")
    print('-' * 75)

    orig_tot = new_tot = 0
    orig_wins = new_wins = 0
    no_data = 0
    detail = []

    for i, t in enumerate(trades, 1):
        sym    = t['symbol']
        orig_r = t.get('result_r') or 0
        entry  = t['entry_price']
        stop   = t['stop_price']
        target = t['target_price']
        ts     = t['timestamp_ms']
        score  = t.get('confluence_score')
        dt     = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime('%m-%d %H:%M')

        bars = sym_bars.get(sym)
        if bars:
            new_r, reason, _ = simulate_tp1(entry, stop, target, ts, bars)
        else:
            new_r, reason = None, 'SYM_ND'

        if new_r is None:
            new_r = orig_r
            no_data += 1
            reason = f'ND({reason})'

        d = new_r - orig_r
        marker = '+' if d > 0.05 else ('-' if d < -0.05 else '=')

        print(f"{i:>3}  {dt}  {sym:8}  {str(score):3}  {orig_r:+7.2f}R  {new_r:+7.2f}R  {reason:13}  {marker}")
        orig_tot += orig_r; new_tot += new_r
        if orig_r > 0: orig_wins += 1
        if new_r  > 0: new_wins  += 1
        detail.append({'sym': sym, 'sc': score, 'orig': orig_r, 'new': new_r, 'reason': reason, 'nd': 'ND' in reason})

    n = len(trades)
    print('\n' + '=' * 75)
    print(f"  ORIGINAL       n={n:2d}  WR={orig_wins/n*100:5.1f}%  TotalR={orig_tot:+7.2f}R  AvgR={orig_tot/n:+.3f}R")
    print(f"  TP1→1.5R+SL    n={n:2d}  WR={new_wins/n*100:5.1f}%  TotalR={new_tot:+7.2f}R  AvgR={new_tot/n:+.3f}R")
    print(f"  Mejora total: {new_tot - orig_tot:+.2f}R  ({no_data} trades sin barras locales)")

    # Solo los que tienen datos
    ok = [d for d in detail if not d['nd']]
    if ok:
        ot = sum(d['orig'] for d in ok); nt = sum(d['new'] for d in ok)
        ow = sum(1 for d in ok if d['orig'] > 0); nw = sum(1 for d in ok if d['new'] > 0)
        print(f"\n  CON DATOS COMPLETOS (n={len(ok)})")
        print(f"  ORIGINAL       WR={ow/len(ok)*100:5.1f}%  TotalR={ot:+.2f}R  AvgR={ot/len(ok):+.3f}R")
        print(f"  TP1→1.5R+SL    WR={nw/len(ok)*100:5.1f}%  TotalR={nt:+.2f}R  AvgR={nt/len(ok):+.3f}R")
        print(f"  Mejora:   {nt - ot:+.2f}R  (+{(nt-ot)/len(ok)*100:.1f}% en avg/trade)")

    # Trades que salieron por TP1_STOP (cerraron a +1.5R por rebote al TP1 level)
    tp1_stops  = [d for d in detail if d['reason'] == 'TP1_STOP' and not d['nd']]
    stops_orig = [d for d in detail if d['orig'] <= -0.9 and not d['nd']]
    stops_salvados = [d for d in stops_orig if d['new'] >= 1.4]   # -1R → +1.5R
    stops_mejor    = [d for d in stops_orig if d['new'] > d['orig'] + 0.05]
    print(f"\n  TP1_STOP (salieron a +1.5R por rebote): {len(tp1_stops)}")
    print(f"  STOPS originales (-1R): {len(stops_orig)}")
    print(f"  -> Salvados a +1.5R:    {len(stops_salvados)}")
    print(f"  -> Mejorados cualquier: {len(stops_mejor)}")

if __name__ == '__main__':
    main()
