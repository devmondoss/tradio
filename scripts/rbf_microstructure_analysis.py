#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
"""
Analisis de microestructura pre-entrada para todas las senales RBF.
Responde: por que la senal entra tarde y que datos podrian mejorar el timing.

Para cada trade:
  - Busca las 25 barras M1 previas a la entrada en *_bars (Supabase)
  - Mide el movimiento ya consumido (entry lag)
  - Analiza patrones de microestructura: bar_delta, cvd_slope, stacked_imb,
    absorption, regime, obi_l5, thin_above, vpin
  - Correlaciona con resultado (win/loss) y con calidad de entrada (% done)
"""
import csv, json, os, urllib.request, urllib.parse, statistics
from datetime import datetime, timezone
from collections import defaultdict

URL = 'https://ztdhvmcisjjyhbqlgkzm.supabase.co'
KEY = os.environ.get('SUPABASE_KEY', '')

LOOKBACK = 25   # barras M1 antes de la entrada

def fetch_all(table, params):
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

def fetch_pre_bars(table, ts_ms, n=25):
    """Trae las n barras anteriores a ts_ms de la tabla *_bars."""
    params = {
        'select': 'ts_ms,open,high,low,close,volume,bar_delta,cvd_slope,'
                  'obi_l5,dz,vr,stacked_imb,absorption,thin_above,thin_below,'
                  'bid_wall,ask_wall,vpin,regime,atr,oi_momentum',
        'ts_ms': f'lt.{ts_ms}',
        'order': 'ts_ms.desc',
        'limit': n,
    }
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        f'{URL}/rest/v1/{table}?{qs}',
        headers={'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
    )
    rows = json.loads(urllib.request.urlopen(req).read())
    return list(reversed(rows))  # cronologico

SYM_TABLE = {
    'BTCUSDT': 'btc_bars', 'ETHUSDT': 'eth_bars',
    'SOLUSDT': 'sol_bars', 'BNBUSDT': 'bnb_bars', 'XRPUSDT': 'xrp_bars',
}

def safe(v, default=0.0):
    return float(v) if v is not None else default

def analyze_pre(bars, entry, stop, target):
    """
    Dado un array de barras pre-entrada, calcula todas las metricas.
    Para Short: movimiento favorable es hacia abajo.
    """
    if not bars:
        return None

    risk    = abs(entry - stop)
    highs   = [b['high'] for b in bars]
    lows    = [b['low']  for b in bars]
    closes  = [safe(b.get('close')) for b in bars]

    pre_high = max(float(h) for h in highs)
    pre_low  = min(float(l) for l in lows)

    # Movimiento que ya ocurrio (Short: cayo de pre_high a entry)
    pre_move   = pre_high - entry
    post_move  = entry - target
    total_move = pre_high - target
    pct_done   = pre_move / total_move * 100 if total_move > 1e-6 else 0
    pre_move_r = pre_move / risk if risk > 1e-6 else 0

    # Microestructura agregada de las 25 barras previas
    deltas      = [safe(b.get('bar_delta'))  for b in bars]
    cvd_slopes  = [safe(b.get('cvd_slope'))  for b in bars]
    obis        = [safe(b.get('obi_l5'))     for b in bars]
    vpins       = [safe(b.get('vpin'))       for b in bars if b.get('vpin') is not None]

    cum_delta    = sum(deltas)
    avg_cvd      = statistics.mean(cvd_slopes) if cvd_slopes else 0
    last5_delta  = sum(deltas[-5:]) if len(deltas) >= 5 else sum(deltas)
    last5_cvd    = statistics.mean(cvd_slopes[-5:]) if len(cvd_slopes) >= 5 else avg_cvd
    avg_obi      = statistics.mean(obis) if obis else 0
    avg_vpin     = statistics.mean(vpins) if vpins else 0

    # Conteos de flags bearish
    stacked_bear = sum(1 for b in bars if b.get('stacked_imb') == 'Bearish')
    stacked_bull = sum(1 for b in bars if b.get('stacked_imb') == 'Bullish')
    absorption_n = sum(1 for b in bars if b.get('absorption') not in (None, 'None', ''))
    thin_above_n = sum(1 for b in bars if b.get('thin_above'))
    thin_below_n = sum(1 for b in bars if b.get('thin_below'))
    bid_wall_n   = sum(1 for b in bars if b.get('bid_wall'))
    ask_wall_n   = sum(1 for b in bars if b.get('ask_wall'))
    regimes      = [b.get('regime','') for b in bars if b.get('regime')]
    expansion_n  = sum(1 for r in regimes if r == 'Expansion')
    oi_mom_n     = sum(1 for b in bars if b.get('oi_momentum'))

    # Primera barra con stacked_imb Bearish (cuantas barras antes del entry)
    first_bear_bar = None
    for j, b in enumerate(bars):
        if b.get('stacked_imb') == 'Bearish':
            first_bear_bar = len(bars) - j  # barras antes del entry
            break

    # Primer bar_delta negativo grande (> 0.5 * avg abs delta)
    abs_deltas = [abs(d) for d in deltas if abs(d) > 0]
    delta_thresh = statistics.mean(abs_deltas) if abs_deltas else 1
    first_neg_delta_bar = None
    for j, b in enumerate(bars):
        if safe(b.get('bar_delta')) < -delta_thresh:
            first_neg_delta_bar = len(bars) - j
            break

    return {
        'pre_high': round(pre_high, 4),
        'pre_low':  round(pre_low,  4),
        'pre_move': round(pre_move, 4),
        'post_move': round(post_move, 4),
        'pct_done': round(pct_done, 1),
        'pre_move_r': round(pre_move_r, 2),
        'cum_delta': round(cum_delta, 1),
        'last5_delta': round(last5_delta, 1),
        'avg_cvd': round(avg_cvd, 2),
        'last5_cvd': round(last5_cvd, 2),
        'avg_obi': round(avg_obi, 4),
        'avg_vpin': round(avg_vpin, 4),
        'stacked_bear': stacked_bear,
        'stacked_bull': stacked_bull,
        'absorption_n': absorption_n,
        'thin_above_n': thin_above_n,
        'thin_below_n': thin_below_n,
        'ask_wall_n': ask_wall_n,
        'bid_wall_n': bid_wall_n,
        'expansion_n': expansion_n,
        'oi_mom_n': oi_mom_n,
        'first_bear_bar': first_bear_bar,
        'first_neg_delta_bar': first_neg_delta_bar,
        'n_bars': len(bars),
    }

def main():
    if not KEY:
        print("ERROR: SUPABASE_KEY no definida"); return

    print("Fetcheando todas las senales RBF (72)...")
    trades = fetch_all('rbf_signals', {
        'select': '*',
        'order': 'timestamp_ms.asc',
    })
    print(f"  {len(trades)} senales totales\n")

    rows_out = []
    no_bars  = 0

    for i, t in enumerate(trades, 1):
        sym      = t['symbol']
        entry    = float(t['entry_price'])
        stop     = float(t['stop_price'])
        target   = float(t['target_price'])
        ts       = int(t['timestamp_ms'])
        score    = t.get('confluence_score')
        ses      = t.get('session', '?')
        direction= t.get('direction', '?')
        result_r = t.get('result_r')
        status   = t.get('exit_reason') or t.get('status') or '?'
        dt       = datetime.fromtimestamp(ts/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')
        risk     = abs(entry - stop)

        table = SYM_TABLE.get(sym)
        if not table:
            no_bars += 1
            continue

        try:
            pre_bars = fetch_pre_bars(table, ts, LOOKBACK)
        except Exception as e:
            print(f"  #{i} {sym} ERROR: {e}")
            no_bars += 1
            continue

        if not pre_bars:
            no_bars += 1
            row = {
                'num': i, 'fecha': dt, 'symbol': sym, 'session': ses,
                'direction': direction, 'score': score, 'result_r': result_r,
                'status': status, 'entry': entry, 'stop': stop, 'target': target,
                'risk': round(risk,4), 'n_pre_bars': 0, 'pct_done': None,
                'pre_move_r': None, 'cum_delta': None, 'last5_delta': None,
                'avg_cvd': None, 'last5_cvd': None, 'avg_obi': None,
                'avg_vpin': None, 'stacked_bear': None, 'stacked_bull': None,
                'absorption_n': None, 'thin_above_n': None, 'expansion_n': None,
                'oi_mom_n': None, 'ask_wall_n': None,
                'first_bear_bar': None, 'first_neg_delta_bar': None, 'has_data': False,
            }
            rows_out.append(row)
            continue

        # Para Short: usa pre_high como referencia
        # Para Long: usa pre_low como referencia (inverso)
        if direction == 'Short':
            m = analyze_pre(pre_bars, entry, stop, target)
        else:
            # Long: invert (movement is upward)
            pre_low  = min(float(b['low'])  for b in pre_bars)
            pre_high = max(float(b['high']) for b in pre_bars)
            pre_move  = entry - pre_low
            post_move = target - entry
            total_move = target - pre_low
            pct_done = pre_move / total_move * 100 if total_move > 1e-6 else 0
            # simplified for Longs
            m = analyze_pre(pre_bars, entry, stop, target)  # same calc, different interpretation
            m['pct_done'] = round(pct_done, 1)
            m['pre_move_r'] = round(pre_move / risk, 2) if risk > 1e-6 else 0

        if not m:
            no_bars += 1
            continue

        row = {
            'num': i, 'fecha': dt, 'symbol': sym, 'session': ses,
            'direction': direction, 'score': score, 'result_r': result_r,
            'status': status, 'entry': entry, 'stop': stop, 'target': target,
            'risk': round(risk, 4), 'n_pre_bars': m['n_bars'],
            'pct_done': m['pct_done'], 'pre_move_r': m['pre_move_r'],
            'cum_delta': m['cum_delta'], 'last5_delta': m['last5_delta'],
            'avg_cvd': m['avg_cvd'], 'last5_cvd': m['last5_cvd'],
            'avg_obi': m['avg_obi'], 'avg_vpin': m['avg_vpin'],
            'stacked_bear': m['stacked_bear'], 'stacked_bull': m['stacked_bull'],
            'absorption_n': m['absorption_n'], 'thin_above_n': m['thin_above_n'],
            'expansion_n': m['expansion_n'], 'oi_mom_n': m['oi_mom_n'],
            'ask_wall_n': m['ask_wall_n'],
            'first_bear_bar': m['first_bear_bar'],
            'first_neg_delta_bar': m['first_neg_delta_bar'],
            'has_data': True,
        }
        rows_out.append(row)

    # CSV completo
    out_path = 'scripts/rbf_microstructure.csv'
    if rows_out:
        with open(out_path, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
            w.writeheader(); w.writerows(rows_out)
        print(f"CSV guardado -> {out_path}\n")

    # ── ANALISIS ──────────────────────────────────────────────────────────────
    with_data = [r for r in rows_out if r.get('has_data') and r['pct_done'] is not None]
    shorts    = [r for r in with_data if r['direction'] == 'Short']
    closed    = [r for r in shorts if r['result_r'] is not None]
    wins      = [r for r in closed if float(r['result_r']) > 0]
    losses    = [r for r in closed if float(r['result_r']) <= 0]

    print(f"{'='*70}")
    print(f"  TOTAL SENALES: {len(trades)}  |  CON DATOS: {len(with_data)}  |  SIN DATOS: {no_bars}")
    print(f"  Shorts con datos: {len(shorts)} (cerrados: {len(closed)}, wins: {len(wins)}, losses: {len(losses)})")

    if not shorts: print("  Sin datos suficientes"); return

    # 1. ENTRY LAG
    pcts = [r['pct_done'] for r in shorts if r['pct_done'] is not None]
    premr = [r['pre_move_r'] for r in shorts if r['pre_move_r'] is not None]
    print(f"\n  ── ENTRY LAG (25 barras pre-entrada) ──")
    print(f"  Avg % movimiento consumido: {statistics.mean(pcts):.1f}%  "
          f"(mediana {statistics.median(pcts):.1f}%)")
    print(f"  Avg pre-movimiento en R:    {statistics.mean(premr):+.2f}R")
    late  = sum(1 for p in pcts if p > 60)
    mid   = sum(1 for p in pcts if 40 < p <= 60)
    early = sum(1 for p in pcts if p <= 40)
    print(f"  TARDE (>60%): {late}  MEDIO (40-60%): {mid}  TEMPRANO (<40%): {early}")

    # 2. ENTRY LAG vs RESULTADO
    if wins and losses:
        win_pct  = statistics.mean(r['pct_done'] for r in wins  if r['pct_done'] is not None)
        loss_pct = statistics.mean(r['pct_done'] for r in losses if r['pct_done'] is not None)
        print(f"\n  ── ENTRY LAG vs RESULTADO ──")
        print(f"  Wins  avg % done: {win_pct:.1f}%   (entradas mas tempranas = mas margen)")
        print(f"  Losses avg % done: {loss_pct:.1f}%")

    # 3. MICROESTRUCTURA PRE-ENTRADA: WINS vs LOSSES
    def avg_field(rows, field):
        vals = [r[field] for r in rows if r.get(field) is not None]
        return statistics.mean(vals) if vals else None

    print(f"\n  ── MICROESTRUCTURA PRE-ENTRADA: WINS vs LOSSES ──")
    fields = [
        ('cum_delta',       'Delta acumulado 25 barras'),
        ('last5_delta',     'Delta ultimas 5 barras'),
        ('avg_cvd',         'CVD slope promedio'),
        ('last5_cvd',       'CVD slope ultimas 5'),
        ('avg_obi',         'OBI L5 promedio'),
        ('avg_vpin',        'VPIN promedio'),
        ('stacked_bear',    'Barras con stacked Bearish'),
        ('absorption_n',    'Barras con absorcion'),
        ('thin_above_n',    'Barras con thin_above'),
        ('expansion_n',     'Barras en regime Expansion'),
        ('ask_wall_n',      'Barras con ask_wall'),
        ('oi_mom_n',        'Barras con OI momentum'),
    ]
    print(f"  {'Metrica':30}  {'WINS':>10}  {'LOSSES':>10}  {'Diferencia':>12}")
    print(f"  {'-'*30}  {'-'*10}  {'-'*10}  {'-'*12}")
    correlations = []
    for field, label in fields:
        w_avg = avg_field(wins,   field)
        l_avg = avg_field(losses, field)
        if w_avg is None or l_avg is None: continue
        diff = w_avg - l_avg
        sign = '+' if diff > 0 else ''
        correlations.append((abs(diff), field, label, w_avg, l_avg, diff))
        print(f"  {label:30}  {w_avg:>10.3f}  {l_avg:>10.3f}  {sign}{diff:>11.3f}")

    # 4. SENALES MAS PREDICTIVAS (mayor diferencia wins vs losses)
    correlations.sort(reverse=True)
    print(f"\n  ── TOP SENALES PREDICTIVAS (mayor diferencia W vs L) ──")
    for _, field, label, wv, lv, diff in correlations[:6]:
        direction_txt = "mayor en WINS" if diff > 0 else "mayor en LOSSES"
        print(f"  {label}: {direction_txt} ({diff:+.3f})")

    # 5. PATRONES DE PRIMERA APARICION DE BEARS
    bear_bars = [r['first_bear_bar'] for r in shorts
                 if r.get('first_bear_bar') is not None]
    neg_delta_bars = [r['first_neg_delta_bar'] for r in shorts
                      if r.get('first_neg_delta_bar') is not None]
    if bear_bars:
        print(f"\n  ── CUANDO APARECE LA PRIMERA SENAL BEARISH ──")
        print(f"  Primer stacked_imb Bearish: avg {statistics.mean(bear_bars):.1f} barras antes de entrar")
        print(f"    -> Esto significa que el breakdown empieza ~{statistics.mean(bear_bars):.0f} barras antes del entry")
    if neg_delta_bars:
        print(f"  Primer delta negativo grande: avg {statistics.mean(neg_delta_bars):.1f} barras antes de entrar")

    # 6. DISTRIBUCION DE pct_done por sesion
    print(f"\n  ── ENTRY LAG POR SESION ──")
    by_ses = defaultdict(list)
    for r in shorts:
        if r['pct_done'] is not None:
            by_ses[r['session']].append(r)
    for ses in sorted(by_ses):
        rs = by_ses[ses]
        avg_p = statistics.mean(r['pct_done'] for r in rs)
        avg_r = statistics.mean(float(r['result_r']) for r in rs if r['result_r'] is not None) if any(r['result_r'] for r in rs) else 0
        print(f"  {ses:20}  n={len(rs):2d}  avg_pct_done={avg_p:.1f}%  avg_result={avg_r:+.2f}R")

    # 7. CORRELACION pct_done vs result_r
    paired = [(r['pct_done'], float(r['result_r']))
              for r in closed if r['pct_done'] is not None and r['result_r'] is not None]
    if len(paired) > 3:
        pcts_c = [p[0] for p in paired]
        rets_c = [p[1] for p in paired]
        n = len(paired)
        mean_p = statistics.mean(pcts_c); mean_r = statistics.mean(rets_c)
        cov = sum((p-mean_p)*(r-mean_r) for p,r in zip(pcts_c, rets_c)) / n
        std_p = statistics.stdev(pcts_c); std_r = statistics.stdev(rets_c)
        corr = cov / (std_p * std_r) if std_p * std_r > 0 else 0
        print(f"\n  ── CORRELACION pct_done vs result_r ──")
        print(f"  r = {corr:.3f}  (negativo = entrar mas tarde -> peor resultado)")
        print(f"  n = {n} trades cerrados con datos completos")

    # 8. RECOMENDACIONES
    print(f"\n  {'='*70}")
    print(f"  DIAGNOSTICO Y RECOMENDACIONES")
    print(f"  {'='*70}")

    if bear_bars:
        avg_lag = statistics.mean(bear_bars)
        print(f"\n  1. ENTRY LAG CONFIRMADO:")
        print(f"     Las senales bearish de microestructura aparecen ~{avg_lag:.0f} barras")
        print(f"     antes de que el detector dispare. La oportunidad lleva {avg_lag:.0f}min")
        print(f"     de ventaja sobre la entrada actual.")

    print(f"\n  2. SEÑALES QUE PODRIAN USARSE COMO TRIGGER ANTICIPADO:")
    for _, field, label, wv, lv, diff in correlations[:4]:
        if diff > 0 and field in ('stacked_bear','cum_delta','last5_delta','avg_cvd'):
            print(f"     * {label}: mayor en wins ({wv:.2f} vs {lv:.2f})")

    print(f"\n  3. DATOS QUE FALTAN O PODRIAN AGREGARSE:")
    print(f"     * Footprint/volume at price: donde se ejecutan las ordenes grandes")
    print(f"       (tenemos bar_delta pero no la distribucion por precio)")
    print(f"     * Velocidad del tape (n trades/min): urgencia de la presion vendedora")
    print(f"     * Deteccion de sweep de liquidez: cuando price limpia stops del rango")
    print(f"       antes del breakout real")
    print(f"     * Range tight bar count: cuantas barras estuvo en rango antes del break")
    print(f"       (ya tenemos el rango del RBF, pero no como feature en bars)")
    print(f"     * CVD divergence flag: precio sube pero CVD baja (early signal)")

    print(f"\n  4. ESTRATEGIA PARA ENTRADA ANTICIPADA:")
    print(f"     Si stacked_imb=Bearish + cum_delta < 0 + cvd_slope < 0 aparecen")
    print(f"     ANTES de que el detector confirme el breakout, se podria entrar")
    print(f"     con orden limitada en el borde del rango (no esperar confirmacion)")
    print(f"     Ganancia potencial: entrar ~{statistics.mean(premr):.1f}R antes del entry actual.")

if __name__ == '__main__':
    main()
