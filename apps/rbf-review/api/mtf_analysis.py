#!/usr/bin/env python3
"""
MTF Shorts — Análisis de distribución
Corre el backtest y calcula:
1. WR / AvgR / n por patrón (sig)
2. Distribución de stop_pct en buckets
3. Performance por bucket de stop_pct
4. Distribución temporal (hora UTC) de señales y WR por hora
5. Performance por régimen de mercado (bear/neutral D1)
6. Análisis del CVD exit vs TP vs SL
"""
import json, subprocess, sys
from pathlib import Path
from collections import defaultdict

SCRIPT = Path(__file__).parent / 'shorts_mtf_backtest.py'

def run_backtest(days=14):
    r = subprocess.run(
        [sys.executable, str(SCRIPT), '--days', str(days)],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        print('ERROR:', r.stderr[:500])
        sys.exit(1)
    return json.loads(r.stdout)

def bucket_stop(pct):
    if pct < 0.5:  return '0.30–0.50%'
    if pct < 0.75: return '0.50–0.75%'
    if pct < 1.0:  return '0.75–1.00%'
    if pct < 1.5:  return '1.00–1.50%'
    return '1.50–2.50%'

def stats(trades):
    if not trades: return {'n':0,'wr':0,'avgR':0,'totalR':0,'wins_avgR':0,'loss_avgR':0}
    n    = len(trades)
    wins = [t for t in trades if t['resultR'] > 0]
    loss = [t for t in trades if t['resultR'] <= 0]
    tr   = sum(t['resultR'] for t in trades)
    return {
        'n':        n,
        'wr':       round(len(wins)/n*100, 1),
        'avgR':     round(tr/n, 3),
        'totalR':   round(tr, 2),
        'wins_avgR': round(sum(t['resultR'] for t in wins)/len(wins), 3) if wins else 0,
        'loss_avgR': round(sum(t['resultR'] for t in loss)/len(loss), 3) if loss else 0,
    }

def hr(ts_sec):
    import datetime
    return datetime.datetime.utcfromtimestamp(ts_sec).hour

def sep(title):
    print(f'\n{"="*60}')
    print(f'  {title}')
    print(f'{"="*60}')

def tbl(headers, rows, widths):
    fmt = '  ' + '  '.join(f'{{:<{w}}}' for w in widths)
    print(fmt.format(*headers))
    print('  ' + '  '.join('-'*w for w in widths))
    for row in rows:
        print(fmt.format(*[str(x) for x in row]))

def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    print(f'Corriendo backtest --days {days}...')
    data   = run_backtest(days)
    trades = data['trades']
    closed = [t for t in trades if not t['isOpen']]
    n      = data['n']
    print(f'  {n} trades  WR={data["wr_pct"]}%  AvgR={data["avg_r"]}R  Equity=${data["equity"]}')

    # ── 1. Por patrón (sig) ──────────────────────────────────────────────────
    sep('1. PERFORMANCE POR PATRÓN')
    by_sig = defaultdict(list)
    for t in closed:
        by_sig[t['sig']].append(t)
    rows = []
    for sig, ts in sorted(by_sig.items(), key=lambda x: -stats(x[1])['wr']):
        s = stats(ts)
        rows.append([sig, s['n'], f"{s['wr']}%", f"{s['avgR']:+.3f}R",
                     f"{s['totalR']:+.2f}R", f"{s['wins_avgR']:+.3f}", f"{s['loss_avgR']:+.3f}"])
    tbl(['Patrón','n','WR%','AvgR','TotalR','Win avg','Loss avg'],
        rows, [28,4,6,8,8,8,8])

    # ── 2. Por bucket de stop_pct ────────────────────────────────────────────
    sep('2. PERFORMANCE POR STOP_PCT BUCKET')
    by_stop = defaultdict(list)
    for t in closed:
        by_stop[bucket_stop(t['stopPct'])].append(t)
    order = ['0.30–0.50%','0.50–0.75%','0.75–1.00%','1.00–1.50%','1.50–2.50%']
    rows = []
    for b in order:
        ts = by_stop.get(b, [])
        if not ts: continue
        s = stats(ts)
        # RR efectivo = wins_avgR / abs(loss_avgR)
        rr = round(s['wins_avgR']/abs(s['loss_avgR']), 2) if s['loss_avgR'] != 0 else 0
        rows.append([b, s['n'], f"{s['wr']}%", f"{s['avgR']:+.3f}R",
                     f"{s['totalR']:+.2f}R", f"RR={rr}"])
    tbl(['Bucket','n','WR%','AvgR','TotalR','RR efectivo'],
        rows, [12,4,6,8,8,12])

    # ── 3. Distribución stop_pct ─────────────────────────────────────────────
    sep('3. DISTRIBUCIÓN STOP_PCT (percentiles)')
    spcts = sorted(t['stopPct'] for t in closed)
    nn = len(spcts)
    for pct in [10,25,50,75,90,95]:
        idx = int(nn * pct/100)
        print(f'  p{pct:2d}: {spcts[min(idx,nn-1)]:.3f}%')
    print(f'  mean: {sum(spcts)/nn:.3f}%')
    print(f'  min:  {spcts[0]:.3f}%   max: {spcts[-1]:.3f}%')

    # ── 4. Performance por hora UTC ──────────────────────────────────────────
    sep('4. PERFORMANCE POR HORA UTC (señal de entrada)')
    by_hr = defaultdict(list)
    for t in closed:
        by_hr[hr(t['ts'])].append(t)
    rows = []
    for h in sorted(by_hr.keys()):
        ts = by_hr[h]
        s  = stats(ts)
        session = 'London' if 7<=h<12 else 'NY' if 13<=h<17 else 'Overlap' if 12<=h<13 else 'Other'
        rows.append([f'{h:02d}:00', session, s['n'], f"{s['wr']}%", f"{s['avgR']:+.3f}R", f"{s['totalR']:+.2f}R"])
    tbl(['Hora UTC','Sesión','n','WR%','AvgR','TotalR'],
        rows, [8,8,4,6,8,8])

    # ── 5. Por símbolo + patrón top ──────────────────────────────────────────
    sep('5. TOP PATRONES POR SÍMBOLO (n>=3)')
    for sym in ['BTCUSDT','ETHUSDT','SOLUSDT']:
        sym_trades = [t for t in closed if t['sym']==sym]
        if not sym_trades: continue
        by_sig_sym = defaultdict(list)
        for t in sym_trades:
            by_sig_sym[t['sig']].append(t)
        s_all = stats(sym_trades)
        print(f'\n  {sym}  n={s_all["n"]}  WR={s_all["wr"]}%  AvgR={s_all["avgR"]:+.3f}R')
        rows = []
        for sig, ts in sorted(by_sig_sym.items(), key=lambda x: -stats(x[1])['totalR']):
            s = stats(ts)
            if s['n'] < 3: continue
            rows.append([f'  {sig}', s['n'], f"{s['wr']}%", f"{s['avgR']:+.3f}R", f"{s['totalR']:+.2f}R"])
        tbl(['  Patrón','n','WR%','AvgR','TotalR'], rows, [30,4,6,8,8])

    # ── 6. Análisis de exit reasons ──────────────────────────────────────────
    sep('6. ANÁLISIS DE EXIT REASONS')
    by_reason = defaultdict(list)
    for t in closed:
        by_reason[t['reason']].append(t)
    rows = []
    for reason, ts in sorted(by_reason.items(), key=lambda x: -len(x[1])):
        s = stats(ts)
        rows.append([reason, s['n'], f"{s['n']/len(closed)*100:.1f}%",
                     f"{s['avgR']:+.3f}R", f"{s['totalR']:+.2f}R",
                     f"{s['wins_avgR']:+.3f}", f"{s['loss_avgR']:+.3f}"])
    tbl(['Reason','n','%total','AvgR','TotalR','Win avg','Loss avg'],
        rows, [18,4,7,8,8,8,8])

    # ── 7. Duración de trades por resultado ──────────────────────────────────
    sep('7. DURACIÓN MEDIA (minutos) POR RESULTADO')
    wins_dur  = [t['durationMin'] for t in closed if t['resultR'] > 0]
    loss_dur  = [t['durationMin'] for t in closed if t['resultR'] <= 0]
    cvd_wins  = [t['durationMin'] for t in closed if t['reason']=='CVD_EXHAUSTION' and t['resultR']>0]
    tp_dur    = [t['durationMin'] for t in closed if t['reason']=='TAKE_PROFIT']
    sl_dur    = [t['durationMin'] for t in closed if t['reason']=='STOP_LOSS']
    def avg(lst): return round(sum(lst)/len(lst),1) if lst else 0
    print(f'  Wins  (todos)      avg={avg(wins_dur)} min  n={len(wins_dur)}')
    print(f'  Losses             avg={avg(loss_dur)} min  n={len(loss_dur)}')
    print(f'  TAKE_PROFIT        avg={avg(tp_dur)} min  n={len(tp_dur)}')
    print(f'  CVD_EXHAUSTION win avg={avg(cvd_wins)} min  n={len(cvd_wins)}')
    print(f'  STOP_LOSS          avg={avg(sl_dur)} min  n={len(sl_dur)}')

    # ── 8. Correlación stop_pct vs resultR ───────────────────────────────────
    sep('8. stop_pct vs RESULTADO — ¿más riesgo = peor resultado?')
    low  = [t for t in closed if t['stopPct'] < 0.75]
    mid  = [t for t in closed if 0.75 <= t['stopPct'] < 1.5]
    high = [t for t in closed if t['stopPct'] >= 1.5]
    for label, ts in [('stop < 0.75%', low), ('0.75–1.50%', mid), ('stop > 1.50%', high)]:
        s = stats(ts)
        print(f'  {label:<15} n={s["n"]:3d}  WR={s["wr"]:5.1f}%  AvgR={s["avgR"]:+.3f}R  TotalR={s["totalR"]:+.2f}R')

if __name__ == '__main__':
    main()
