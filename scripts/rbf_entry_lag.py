#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
"""
Analiza cuanto movimiento ya habia ocurrido ANTES de que la senal RBF entrara.
Para cada Short: mira 25 barras M1 previas a la entrada y calcula:
  - pre_high:  maximo de las 25 barras previas (techo del movimiento Short)
  - pre_move:  pre_high - entry_price  (cuanto ya habia caido antes de la entrada)
  - post_move: entry_price - target     (cuanto queda hasta el target)
  - pct_done:  pre_move / (pre_move + post_move)  => % del movimiento total ya consumido
"""
import csv, json, os, urllib.request, urllib.parse
from datetime import datetime, timezone

URL = 'https://ztdhvmcisjjyhbqlgkzm.supabase.co'
KEY = os.environ.get('SUPABASE_KEY', '')

LOOKBACK = 25   # barras M1 a mirar antes de la entrada

def fetch(table, params):
    rows, lim, off = [], 1000, 0
    while True:
        qs = urllib.parse.urlencode({**params, 'limit': lim, 'offset': off})
        req = urllib.request.Request(
            f'{URL}/rest/v1/{table}?{qs}',
            headers={'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
        )
        chunk = json.loads(urllib.request.urlopen(req).read())
        rows.extend(chunk)
        if len(chunk) < lim: break
        off += lim
    return rows

def load_m1(path, ts_col='ts_ms'):
    bars = {}
    if not os.path.exists(path): return bars
    with open(path, encoding='utf-8', errors='replace') as f:
        for row in csv.DictReader(f):
            ts = row.get(ts_col)
            if not ts: continue
            try:
                bars[int(ts)] = {
                    'open':  float(row['open']),
                    'high':  float(row['high']),
                    'low':   float(row['low']),
                    'close': float(row['close']),
                }
            except (KeyError, ValueError):
                pass
    return bars

def get_pre_bars(ts_ms, bars_sorted, bars_dict, n=25):
    """Retorna las n barras M1 inmediatamente anteriores a ts_ms."""
    lo, hi, idx = 0, len(bars_sorted)-1, len(bars_sorted)
    while lo <= hi:
        mid = (lo+hi)//2
        if bars_sorted[mid] >= ts_ms: idx=mid; hi=mid-1
        else: lo=mid+1
    # idx = primera barra >= entry; las previas son idx-1 ... idx-n
    pre = []
    for i in range(max(0, idx-n), idx):
        b = bars_dict.get(bars_sorted[i])
        if b: pre.append((bars_sorted[i], b))
    return pre

def main():
    if not KEY:
        print("ERROR: SUPABASE_KEY no definida"); return

    print("Cargando barras M1...")
    btc = load_m1('dataset/btcusdt_90d_m1.csv', 'ts_ms')
    btc.update(load_m1('dataset/btc_bars_full.csv'))
    eth = load_m1('dataset/eth_bars_full.csv')
    sol = load_m1('dataset/sol_bars_full.csv')
    bnb = load_m1('dataset/bnb_bars_full.csv')

    sym_data = {
        'BTCUSDT': btc, 'ETHUSDT': eth,
        'SOLUSDT': sol, 'BNBUSDT': bnb,
    }
    sym_sorted = {s: sorted(d.keys()) for s, d in sym_data.items()}

    trades = fetch('rbf_signals', {
        'select': '*', 'result_r': 'not.is.null',
        'direction': 'eq.Short', 'order': 'timestamp_ms.asc',
    })

    rows_out = []
    print(f"\n{'#':>3}  {'Fecha':16}  {'Sym':8}  {'Sc':2}  {'Ses':15}  "
          f"{'Res':>6}  {'PreMv':>6}  {'PostMv':>6}  {'%Done':>6}  "
          f"{'PreMv_R':>7}  {'MaxPot_R':>8}  Nota")
    print('-'*110)

    for i, t in enumerate(trades, 1):
        sym    = t['symbol']
        entry  = float(t['entry_price'])
        stop   = float(t['stop_price'])
        target = float(t['target_price'])
        ts     = int(t['timestamp_ms'])
        score  = t.get('confluence_score','?')
        ses    = t.get('session','?')
        res_r  = float(t.get('result_r') or 0)
        dt     = datetime.fromtimestamp(ts/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')

        risk = abs(entry - stop)

        bars_d = sym_data.get(sym)
        bars_s = sym_sorted.get(sym)
        if not bars_d or not bars_s:
            print(f"{i:>3}  {dt}  {sym:8}  {str(score):2}  {ses:15}  {res_r:>+5.2f}  -- SIN DATOS --")
            continue

        pre = get_pre_bars(ts, bars_s, bars_d, LOOKBACK)
        if not pre:
            print(f"{i:>3}  {dt}  {sym:8}  {str(score):2}  {ses:15}  {res_r:>+5.2f}  -- SIN PRE --")
            continue

        # Para Short: el movimiento favorable es hacia abajo
        # pre_high = maximo de las 25 barras previas (de donde viene el precio antes de caer)
        pre_high  = max(b['high'] for _, b in pre)
        pre_low   = min(b['low']  for _, b in pre)
        pre_close_last = pre[-1][1]['close']  # close de la barra justo antes de la entrada

        # Movimiento que ya ocurrio antes de la entrada (Short: cayo de pre_high a entry)
        pre_move  = pre_high - entry          # puntos que ya cayeron antes de entrar
        # Movimiento que queda desde entry hasta target
        post_move = entry - target            # puntos restantes al target
        # Total teorico del movimiento
        total_move = pre_high - target
        pct_done = pre_move / total_move * 100 if total_move > 1e-6 else 0

        # En terminos de R (normalizado al riesgo del trade)
        pre_move_r  = pre_move  / risk   # cuantas R ya se movio antes de la senal
        max_pot_r   = total_move / risk  # maximo potencial desde el inicio del movimiento

        # Barra previa mas cercana: cuanto se movio la ultima barra antes de entrar
        last_bar_move = pre[-1][1]['high'] - pre[-1][1]['low']

        # Nota
        if pct_done > 60:
            nota = "TARDE (>60%)"
        elif pct_done > 40:
            nota = "MEDIO (40-60%)"
        else:
            nota = "temprano (<40%)"

        rows_out.append({
            'num': i, 'fecha': dt, 'sym': sym, 'score': score, 'session': ses,
            'result_r': res_r, 'entry': entry, 'stop': stop, 'target': target,
            'risk': round(risk,2), 'pre_high': round(pre_high,2),
            'pre_move': round(pre_move,2), 'post_move': round(post_move,2),
            'pct_done': round(pct_done,1), 'pre_move_r': round(pre_move_r,2),
            'max_pot_r': round(max_pot_r,2), 'nota': nota,
        })

        print(f"{i:>3}  {dt}  {sym:8}  {str(score):2}  {ses:15}  "
              f"{res_r:>+5.2f}R  "
              f"{pre_move:>7.1f}  {post_move:>7.1f}  {pct_done:>5.1f}%  "
              f"{pre_move_r:>+6.2f}R  {max_pot_r:>7.2f}R  {nota}")

    # CSV
    out = 'scripts/rbf_entry_lag.csv'
    if rows_out:
        with open(out, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
            w.writeheader(); w.writerows(rows_out)
        print(f"\nCSV guardado -> {out}")

    # Resumen
    if rows_out:
        avg_pct  = sum(r['pct_done']    for r in rows_out) / len(rows_out)
        avg_pre  = sum(r['pre_move_r']  for r in rows_out) / len(rows_out)
        avg_max  = sum(r['max_pot_r']   for r in rows_out) / len(rows_out)
        avg_post = sum(r['post_move']/r['risk'] for r in rows_out) / len(rows_out)

        late   = sum(1 for r in rows_out if r['pct_done'] > 60)
        mid    = sum(1 for r in rows_out if 40 < r['pct_done'] <= 60)
        early  = sum(1 for r in rows_out if r['pct_done'] <= 40)

        print(f"\n{'='*70}")
        print(f"  ANALISIS DE ENTRADA (n={len(rows_out)} trades con datos)")
        print(f"  Avg % movimiento consumido antes de entrar: {avg_pct:.1f}%")
        print(f"  Avg pre-movimiento en R:                    {avg_pre:+.2f}R")
        print(f"  Avg potencial maximo desde inicio:          {avg_max:.2f}R")
        print(f"  Avg R restante al entrar (hasta target):    {avg_post:.2f}R")
        print(f"\n  Entradas TARDE  (>60% consumido): {late:2d}")
        print(f"  Entradas MEDIO  (40-60%):          {mid:2d}")
        print(f"  Entradas TEMPRANO (<40%):          {early:2d}")
        print(f"\n  -> Si entraramos en el inicio del movimiento,")
        print(f"     el target potencial promedio seria {avg_max:.2f}R (vs {avg_post:.2f}R actual).")

if __name__ == '__main__':
    main()
