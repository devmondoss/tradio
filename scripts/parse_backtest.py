import json, sys
from datetime import datetime, timezone
from collections import defaultdict

if len(sys.argv) > 1:
    with open(sys.argv[1], encoding='utf-16') as f:
        data = json.load(f)
else:
    data = json.load(sys.stdin)
trades = data['trades']
closed = [t for t in trades if t['reason'] != 'DATA_END']
wins   = [t for t in closed if t['resultR'] > 0]
pre    = [t for t in closed if t.get('isPreBreakout')]
post   = [t for t in closed if not t.get('isPreBreakout')]

ts_list = [t['tsMs'] for t in closed]
if ts_list:
    first_ms = min(ts_list)
    last_ms  = max(ts_list)
    first_dt = datetime.fromtimestamp(first_ms/1000, tz=timezone.utc)
    last_dt  = datetime.fromtimestamp(last_ms/1000, tz=timezone.utc)
    delta    = last_dt - first_dt
    days     = delta.days
    hours    = delta.seconds // 3600
    mins     = (delta.seconds % 3600) // 60
else:
    first_dt = last_dt = None
    days = hours = mins = 0

n   = len(closed)
wr  = len(wins)/n*100 if n else 0
tot = sum(t['resultR'] for t in closed)
eq  = data['equity']

print("=== BACKTEST RBF (backtest_script.py) ===")
if first_dt:
    print(f"  Datos desde:   {first_dt.strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"  Hasta:         {last_dt.strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"  Duracion:      {days} dias, {hours}h {mins}m")
print(f"  Micro start:   {data.get('micro_start','N/A')}")
print(f"  actual_days:   {data.get('actual_days','N/A'):.2f} dias" if isinstance(data.get('actual_days'), float) else f"  actual_days:   {data.get('actual_days','N/A')}")
print()
print(f"  Trades cerrados: {n}")
print(f"  Wins/Losses:     {len(wins)}/{n-len(wins)}")
print(f"  Win Rate:        {wr:.1f}%")
print(f"  Total R:         {tot:+.2f}R")
print(f"  Avg R/trade:     {tot/n:+.3f}R" if n else "  Avg R/trade:  N/A")
print(f"  Capital:         ${data.get('capital',500):.2f}")
print(f"  Risk USD:        ${data.get('risk_usd',10):.2f}")
print(f"  Equity final:    ${eq:.2f} ({(eq-data.get('capital',500))/data.get('capital',500)*100:+.1f}%)")
print()
if post:
    wpost = sum(1 for t in post if t['resultR']>0)
    print(f"  Post-breakout:  n={len(post)}  WR={wpost/len(post)*100:.0f}%  TotR={sum(t['resultR'] for t in post):+.2f}R")
if pre:
    wpre = sum(1 for t in pre if t['resultR']>0)
    print(f"  Pre-breakout:   n={len(pre)}   WR={wpre/len(pre)*100:.0f}%  TotR={sum(t['resultR'] for t in pre):+.2f}R")
print()

by_ses = defaultdict(list)
for t in closed: by_ses[t['session']].append(t)
print("  POR SESION:")
for ses in ['London','LondonNyOverlap','NewYork']:
    lst = by_ses.get(ses,[])
    if lst:
        w2 = sum(1 for t in lst if t['resultR']>0)
        tt = sum(t['resultR'] for t in lst)
        print(f"    {ses:20s}: n={len(lst):2d}  WR={w2/len(lst)*100:.0f}%  TotR={tt:+.2f}R  AvgR={tt/len(lst):+.3f}R")
print()

by_sym = defaultdict(list)
for t in closed: by_sym[t['sym']].append(t)
print("  POR SIMBOLO:")
for sym in sorted(by_sym):
    lst = by_sym[sym]
    w2  = sum(1 for t in lst if t['resultR']>0)
    tt  = sum(t['resultR'] for t in lst)
    print(f"    {sym:10s}: n={len(lst):2d}  WR={w2/len(lst)*100:.0f}%  TotR={tt:+.2f}R  AvgR={tt/len(lst):+.3f}R")
print()

by_reason = defaultdict(list)
for t in closed: by_reason[t['reason']].append(t)
print("  EXIT REASONS:")
for r,lst in sorted(by_reason.items(), key=lambda x:-len(x[1])):
    avg = sum(t['resultR'] for t in lst)/len(lst)
    print(f"    {r:20s}: n={len(lst):2d}  avg={avg:+.3f}R")
print()

print(f"  {'#':>3} {'Fecha':16} {'Sym':8} {'Ses':10} {'VR':>5} {'Rng%':>6} {'Pre':>3} {'Reason':20} {'R':>7}")
print("  "+"-"*85)
for t in closed:
    pre_flag = 'PRE' if t.get('isPreBreakout') else ''
    ses = {'LondonNyOverlap':'Overlap','NewYork':'NY'}.get(t['session'],t['session'])
    dt = datetime.fromtimestamp(t['tsMs']/1000, tz=timezone.utc).strftime('%m-%d %H:%M')
    print(f"  {t.get('idx',0):>3} {dt:16} {t['sym']:8} {ses:10} {t.get('vr',0):>5.2f} {t.get('rangePct',0):>6.3f} {pre_flag:>3} {t['reason']:20} {t['resultR']:>+7.3f}")
