#!/usr/bin/env python3
import json, subprocess, sys, datetime

r = subprocess.run([sys.executable, 'shorts_htf_backtest.py', '--days', '14'],
                   capture_output=True, text=True)
data = json.loads(r.stdout)
trades = data['trades']

print(f"n={len(trades)}  WR={data['wr_pct']}%  AvgR={data['avg_r']}R  Equity=${data['equity']}")
print()
print(f"{'#':>3} {'sym':>5} {'sig':<22} {'session':<16} {'hr':>3} {'stop%':>6} {'resultR':>8} {'reason':<18} {'dur':>5}")
print('-'*96)
for t in trades:
    hr  = datetime.datetime.utcfromtimestamp(t['ts']).hour
    sym = t['sym'].replace('USDT', '')
    win = '+' if t['resultR'] > 0 else ' '
    print(f"{t['idx']:>3} {sym:>5} {t['sig']:<22} {t['session']:<16} {hr:>2}h  {t['stopPct']:>5.2f}%  {win}{t['resultR']:>+7.3f}R  {t['reason']:<18} {t['durationMin']:>5}m")
