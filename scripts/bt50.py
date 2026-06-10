import json, datetime
from collections import defaultdict

CAPITAL_INIT = 50.0
LEVERAGE     = 15.0
RISK_PCT     = 0.02

def ts(ms):
    return datetime.datetime.fromtimestamp(ms/1000, datetime.timezone.utc).strftime('%d/%m %H:%M')

def sl(s):
    return {'London':'LDN','LondonNyOverlap':'OVR','NewYork':'NY','Asia':'ASI','OffHours':'OFF'}.get(s, s[:3])

with open('/tmp/momentum_v2_results.json') as f:
    trades = json.load(f)
trades.sort(key=lambda t: t['entry_ms'])

capital = CAPITAL_INIT
rows    = []
peak    = capital
max_dd  = 0.0
wins    = 0
losses  = 0
total_r = 0.0

for t in trades:
    if capital < 1.0:
        break
    risk_usd  = capital * RISK_PCT
    entry     = t['entry']
    stop_dist = abs(entry - t['stop'])
    stop_pct  = stop_dist / entry if entry > 0 else 0.001
    position  = min(risk_usd / stop_pct, capital * LEVERAGE) if stop_pct > 0 else 0
    act_risk  = position * stop_pct
    lev_used  = position / capital if capital > 0 else 0
    result_r  = t['r']
    pnl       = result_r * act_risk
    capital  += pnl
    total_r  += result_r
    if capital > peak:
        peak = capital
    dd = (peak - capital) / peak * 100
    if dd > max_dd:
        max_dd = dd
    if result_r > 0:
        wins += 1
    else:
        losses += 1
    rows.append({
        'n':    len(rows) + 1,
        'ts':   ts(t['entry_ms']),
        'sym':  t['sym'],
        'dir':  t['dir'][0],
        'sess': sl(t['session']),
        'vr':   t['vr'],
        'dz':   t['dz'],
        'entry': entry,
        'stop':  t['stop'],
        'rsn':  (t.get('reason') or '?')[:5],
        'r':    result_r,
        'risk': round(act_risk, 2),
        'pos':  round(position, 0),
        'lev':  round(lev_used, 1),
        'pnl':  round(pnl, 2),
        'cap':  round(capital, 2),
    })

n_total  = len(rows)
wr       = wins / n_total * 100
avg_r    = total_r / n_total
profit   = capital - CAPITAL_INIT
roi      = profit / CAPITAL_INIT * 100

SEP = '=' * 112
SEP2 = '-' * 112

print(SEP)
print('  BACKTEST COMPLETO MomentumFlow v2  |  Capital: $50  |  Leverage: 15x  |  Riesgo: 2pct/trade (compounding)')
print(SEP)
hdr = (f'{"#":>3} {"Fecha":>10} {"Sym":>8} {"D":>1} {"Ses":>3} {"VR":>4} {"DZ":>5}  '
       f'{"Entry":>10} {"Stop":>10}  {"Exit":>5} {"R":>6}  {"Risk$":>5} {"Pos$":>7} {"Lev":>5}  '
       f'{"P&L":>7}  {"Capital":>9}')
print(hdr)
print(SEP2)

for r in rows:
    win = '+' if r['r'] > 0 else ' '
    line = (f'{r["n"]:>3} {r["ts"]:>10} {r["sym"]:>8} {r["dir"]:>1} {r["sess"]:>3} '
            f'{r["vr"]:>4.1f} {r["dz"]:>+5.1f}  '
            f'{r["entry"]:>10.3f} {r["stop"]:>10.3f}  '
            f'{r["rsn"]:>5} {r["r"]:>+6.3f}  '
            f'${r["risk"]:>4.2f} ${r["pos"]:>6.0f} {r["lev"]:>4.1f}x  '
            f'${r["pnl"]:>+6.2f}  ${r["cap"]:>8.2f}')
    print(line)

print(SEP)
print()
print('RESUMEN')
print(f'  Trades totales : {n_total}')
print(f'  Ganadores      : {wins}  ({wr:.1f}%)')
print(f'  Perdedores     : {losses}')
print(f'  AvgR por trade : {avg_r:+.3f}R')
print(f'  Total R        : {total_r:+.2f}R')
print()
print(f'  Capital inicio : $50.00')
print(f'  Capital final  : ${capital:.2f}')
print(f'  Ganancia neta  : ${profit:+.2f}  ({roi:+.1f}% ROI en 4.4 dias)')
print(f'  Max Drawdown   : {max_dd:.1f}%  (pico ${peak:.2f})')
print()

print('Por sesion:')
by_sess = defaultdict(list)
for r in rows:
    by_sess[r['sess']].append(r)
for s in ['LDN', 'ASI', 'OVR', 'NY', 'OFF']:
    lst = by_sess.get(s, [])
    if not lst:
        continue
    ww = sum(1 for x in lst if x['r'] > 0) / len(lst) * 100
    aa = sum(x['r'] for x in lst) / len(lst)
    pp = sum(x['pnl'] for x in lst)
    print(f'  {s}: n={len(lst):3d}  WR={ww:4.0f}%  AvgR={aa:>+6.3f}  P&L=${pp:>+8.2f}')

print()
print('Por direccion:')
by_dir = defaultdict(list)
for r in rows:
    by_dir[r['dir']].append(r)
for d in ['S', 'L']:
    lst = by_dir.get(d, [])
    if not lst:
        continue
    ww = sum(1 for x in lst if x['r'] > 0) / len(lst) * 100
    aa = sum(x['r'] for x in lst) / len(lst)
    pp = sum(x['pnl'] for x in lst)
    name = 'Short' if d == 'S' else 'Long '
    print(f'  {name}: n={len(lst):3d}  WR={ww:4.0f}%  AvgR={aa:>+6.3f}  P&L=${pp:>+8.2f}')

print()
print('CURVA DE CAPITAL (cada 20 trades):')
print(f'  {"Trade":>5}  {"Fecha":>10}  {"Capital":>9}  {"Ganancia":>9}  Visual (cada # = x0.07 del capital inicial)')
seen = set()
checkpoints = list(range(0, len(rows), 20)) + [len(rows) - 1]
for i in checkpoints:
    if i in seen:
        continue
    seen.add(i)
    c   = rows[i]['cap']
    d   = c - CAPITAL_INIT
    bar = '#' * int(c / CAPITAL_INIT * 15)
    tag = ' <- FINAL' if i == len(rows) - 1 else ''
    print(f'  {rows[i]["n"]:>5}  {rows[i]["ts"]:>10}  ${c:>8.2f}  {d:>+8.2f}   {bar}{tag}')

print()
daily = profit / 4.4
print('PROYECCION (capital fijo $50, sin compounding extra):')
print(f'  Ganancia/dia media  : ${daily:+.2f}')
print(f'  Mes (22 dias)       : ${daily * 22:+.2f}  ({daily * 22 / CAPITAL_INIT * 100:+.0f}% ROI mensual)')
print(f'  Trimestre (66 dias) : ${daily * 66:+.2f}')
print()
print('ADVERTENCIA: 4.4 dias de datos. Proyecciones asumen condiciones similares de mercado.')
print('Con mas datos el AvgR se normalizara hacia el valor real de largo plazo.')
