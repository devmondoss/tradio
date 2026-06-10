import json, math, datetime
from collections import defaultdict

with open('/tmp/momentum_v2_results.json') as f:
    trades = json.load(f)
trades.sort(key=lambda t: t['entry_ms'])

def ts(ms):
    return datetime.datetime.fromtimestamp(ms/1000, datetime.timezone.utc).strftime('%d/%m %H:%M')

total_r = sum(t['r'] for t in trades)
n = len(trades)

print()
print('PROBLEMA 1: UN SOLO TRADE DISTORSIONA TODO EL RESULTADO')
print('-'*65)
top10 = sorted(trades, key=lambda t: -t['r'])[:10]
for t in top10:
    print(f'  {ts(t["entry_ms"])} {t["sym"]:8s} {t["dir"]:5s} {t["session"]:18s} '
          f'dz={t["dz"]:+.1f}  R={t["r"]:+.3f}')
top10_r = sum(t['r'] for t in top10)
rest_r  = total_r - top10_r
print(f'\n  Total R: {total_r:+.2f}R')
print(f'  Top 10 trades: {top10_r:+.2f}R  ({top10_r/total_r*100:.0f}% del total!)')
print(f'  Los otros 256: {rest_r:+.2f}R  ({rest_r/total_r*100:.0f}% del total)')
print(f'\n  Sin los top 10: AvgR = {rest_r/256:+.4f}R  (casi cero)')

print()
print('PROBLEMA 2: COMISIONES DESTRUYEN LA ESTRATEGIA (stop 0.5xATR)')
print('-'*65)
TAKER = 0.0004
print(f'  Binance Futures taker fee: {TAKER*100:.2f}% por lado = {TAKER*2*100:.2f}% round trip')
print()
data = [
    ('BTCUSDT',  50.0,  61000.0),
    ('ETHUSDT',   2.0,   1600.0),
    ('BNBUSDT',   0.5,    590.0),
    ('SOLUSDT',   0.10,    64.0),
    ('XRPUSDT', 0.005,     1.13),
]
print(f'  {"Sym":8s}  {"Stop dist":>10}  {"Stop%":>7}  {"Risk/750$":>9}  {"Fee RT/750$":>12}  {"Fee/Risk":>9}  Viable?')
for sym, atr, entry in data:
    stop_d   = 0.5 * atr
    stop_pct = stop_d / entry
    pos      = 750.0
    risk     = pos * stop_pct
    fee_rt   = pos * TAKER * 2
    ratio    = fee_rt / risk
    viable   = 'OK' if ratio < 0.15 else ('LIMITE' if ratio < 0.5 else 'IMPOSIBLE')
    print(f'  {sym:8s}  {stop_d:>10.4f}  {stop_pct*100:>6.3f}%  ${risk:>8.3f}  ${fee_rt:>11.3f}  {ratio:>8.1f}x  {viable}')

print()
print('  Con stop 0.5xATR, para BTC cada trade cuesta en fees 2x mas que el riesgo.')
print('  La estrategia es MATEMATICAMENTE IMPOSIBLE a este stop size con comisiones reales.')

print()
print('PROBLEMA 3: SOBREAJUSTE (overfitting sobre 4.4 dias)')
print('-'*65)
edge  = 0.117
std_r = 1.0
z95   = 1.96
n_min = (z95 * std_r / edge) ** 2
print(f'  Periodo de datos: 4.4 dias')
print(f'  Trades en backtest: {n}')
print(f'  AvgR del backtest: {edge:+.3f}R')
print()
print(f'  Para confirmar un edge de {edge:+.3f}R con 95% confianza necesitas:')
print(f'  n >= {n_min:.0f} trades OUT-OF-SAMPLE (datos no vistos)')
print(f'  Tenemos: {n} trades, pero los parametros fueron ajustados sobre esos mismos datos.')
print(f'  Eso es data snooping -> el edge real probablemente sea menor.')
print()
print('  Analogia: si tiramos una moneda 266 veces y ajustamos parametros')
print('  para que salga cara, parecera que la moneda esta trucada.')

print()
print('PROBLEMA 4: CORRELACION ENTRE SIMBOLOS')
print('-'*65)
print('  El backtest asume 5 posiciones independientes.')
print('  En realidad: cuando BTC cae 1%, ETH/BNB/SOL caen casi igual.')
print('  5 Shorts en la misma sesion = una sola apuesta con 5x el riesgo.')
print()
by_ts = defaultdict(list)
for t in trades:
    by_ts[t['entry_ms']].append(t)
simultaneous = {k: v for k, v in by_ts.items() if len(v) > 1}
print(f'  Trades simulados a la vez en exactamente el mismo timestamp: {len(simultaneous)} grupos')
total_sim = sum(len(v) for v in simultaneous.values())
print(f'  Trades en esos grupos: {total_sim} de {n} ({total_sim/n*100:.0f}%)')
print(f'  Esos son apostando el mismo movimiento de mercado N veces.')

print()
print('ESTIMACION REALISTA DEL EDGE')
print('='*65)
print()
print('  Setup mas prometedor: Short + London + DZ >= 4')
ldn_short_dz4 = [t for t in trades if t['dir']=='Short' and t['session']=='London' and abs(t['dz'])>=4]
n_ls = len(ldn_short_dz4)
avg_ls = sum(t['r'] for t in ldn_short_dz4)/n_ls if n_ls else 0
print(f'  Trades en backtest: {n_ls} (en 4.4 dias = {n_ls/4.4:.1f}/dia)')
print(f'  AvgR backtest: {avg_ls:+.3f}R')
print()
print('  Factores de descuento aplicados:')
disc_overfit = 0.5
disc_fees    = 0.3
disc_slip    = 0.1
disc_total   = 1.0 - disc_overfit - disc_fees - disc_slip
avg_real     = avg_ls * disc_total
print(f'    Overfitting (4.4 dias, params fitted):  -{int(disc_overfit*100)}%')
print(f'    Comisiones + spread (stop 1.0xATR):     -{int(disc_fees*100)}%')
print(f'    Slippage en ejecucion M1:               -{int(disc_slip*100)}%')
print(f'    AvgR realista estimado: {avg_ls:+.3f} x {disc_total:.1f} = {avg_real:+.3f}R')
print()
spd_real = n_ls / 4.4
r_mes = spd_real * 22 * avg_real
print(f'  Con {spd_real:.1f} trades/dia, 22 dias/mes:')
for risk_usd in [0.5, 1.0, 2.0]:
    cap_equiv = risk_usd / 0.02
    print(f'    Riesgo ${risk_usd:.1f}/trade (capital ~${cap_equiv:.0f}): {r_mes:+.1f}R = ${r_mes*risk_usd:+.2f}/mes')

print()
print('  CONCLUSION HONESTA:')
print('  - La proyeccion de $1,010/mes con $50 es ficticia')
print('  - El edge Short+London+DZ4 PARECE REAL pero necesita validacion live')
print('  - Con 30 dias de datos reales y un estimado conservador:')
risk_conserv = 1.0
r_conserv    = spd_real * 22 * avg_real
print(f'    Expectativa mensual: ${r_conserv*risk_conserv:+.1f} con $1/trade de riesgo')
print(f'    Con $50 capital y 2% riesgo: ${r_conserv*1:+.1f}/mes (no $1010)')
print()
print('  LO QUE SI PODEMOS DECIR CON CONFIANZA:')
print('  1. Short en London tiene edge consistente (RBF y MF v2 coinciden)')
print('  2. DZ > 4sigma filtra trades de mejor calidad')
print('  3. El trailing stop > target fijo para este tipo de entrada')
print('  4. Necesitamos 60+ dias de datos para una validacion real')
