import pandas as pd, numpy as np

df = pd.read_csv('exports/basics_trades.csv')
oos = df[df['oos'] == True].copy()

print('=== DISTRIBUCION MFE OOS ===')
print(f'Total trades: {len(oos)}')
for thr in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]:
    n = (oos['mfe_r'] >= thr).sum()
    print(f'  MFE >= {thr}R: {n} ({n/len(oos)*100:.1f}%)')

print()
print('=== TRADES QUE LLEGARON A >= 1R MFE PERO TERMINARON EN STOP ===')
stranded = oos[(oos['mfe_r'] >= 1.0) & (oos['reason'] == 'stop')]
normal_stops = oos[(oos['mfe_r'] < 1.0) & (oos['reason'] == 'stop')]
print(f'Stranded (llegaron 1R, luego stop): n={len(stranded)} ({len(stranded)/len(oos)*100:.1f}% del total)')
print(f'Normal stops (nunca llegaron a 1R): n={len(normal_stops)}')
print(f'MFE promedio de stranded: {stranded["mfe_r"].mean():.2f}R')
print(f'PnL destruido (vs 0R breakeven): ${(-stranded["pnl_usd"].sum()):.0f}')
print(f'Si stranded fueran 0R en vez de -1R: +${(-stranded["pnl_usd"].sum()):.0f} adicionales')

print()
print('=== MFE PROMEDIO POR SCORE OOS ===')
for sc in range(5):
    s = oos[oos['score'] == sc]
    if len(s) == 0: continue
    wins = s[s['result_r'] > 0]
    stops_all = s[s['reason'] == 'stop']
    strnd = s[(s['mfe_r'] >= 1.0) & (s['reason'] == 'stop')]
    wr = (s['result_r'] > 0).mean() * 100
    print(f'Score {sc}/4  n={len(s):>3}  WR={wr:>5.1f}%  mfe_avg={s["mfe_r"].mean():.2f}R  '
          f'reaches_1R={( s["mfe_r"]>=1.0).sum():>3}({(s["mfe_r"]>=1.0).mean()*100:.0f}%)  '
          f'stranded={len(strnd)}')

print()
print('=== POR NIVEL OOS ===')
for lv in oos['level'].value_counts().head(6).index:
    s = oos[oos['level'] == lv]
    strnd = s[(s['mfe_r'] >= 1.0) & (s['reason'] == 'stop')]
    wr = (s['result_r'] > 0).mean() * 100
    mfe = s['mfe_r'].mean()
    pct_strnd = len(strnd)/len(s)*100
    print(f'{lv:<22} n={len(s):>3}  WR={wr:>5.1f}%  mfe_avg={mfe:.2f}R  stranded={len(strnd):>2}({pct_strnd:.0f}%)')

print()
print('=== POR SESION OOS ===')
for sess in ['london', 'overlap', 'ny']:
    s = oos[oos['session'] == sess]
    if len(s) == 0: continue
    strnd = s[(s['mfe_r'] >= 1.0) & (s['reason'] == 'stop')]
    wr = (s['result_r'] > 0).mean() * 100
    mfe = s['mfe_r'].mean()
    print(f'{sess:<10} n={len(s):>3}  WR={wr:>5.1f}%  mfe_avg={mfe:.2f}R  stranded={len(strnd):>2}({len(strnd)/len(s)*100:.0f}%)')

print()
print('=== ANALISIS EXIT QUALITY: resultado_r vs mfe_r ===')
# Efficiency = result_r / mfe_r (cuanto del MFE capturamos)
oos2 = oos[(oos['mfe_r'] > 0)].copy()
oos2['efficiency'] = oos2['result_r'] / oos2['mfe_r']
wins_oos = oos2[oos2['result_r'] > 0]
print(f'Eficiencia promedio (result/MFE):')
print(f'  Todos:     {oos2["efficiency"].mean():.3f}')
print(f'  Winners:   {wins_oos["efficiency"].mean():.3f}')
print(f'  Por score:')
for sc in range(5):
    s = oos2[oos2['score'] == sc]
    if len(s) < 5: continue
    print(f'    Score {sc}: {s["efficiency"].mean():.3f}  (mfe_avg={s["mfe_r"].mean():.2f}R)')

print()
print('=== TRAILING: si moviese stop a BE @ 1R, cuanto ganamos? ===')
# Simulacion simple: trades stranded se convierten en 0R en vez de -1R
stranded_loss = stranded['pnl_usd'].sum()
# Con BE trail: esos trades dan 0R (fee break even ~ -fee)
be_gain = -stranded_loss  # recuperamos la perdida
print(f'PnL actual de {len(stranded)} stranded: ${stranded_loss:.0f}')
print(f'Con BE trail: esos trades = ~$0 (fee only)')
print(f'Mejora bruta: +${be_gain:.0f} sobre capital final')

# Y si el trail fuera mas agresivo (1R trail a 0.5R por ejemplo)?
# Trades que llegaron a 1.5R pero retrocedieron a menos de 0.5R
retrace_1r = oos[(oos['mfe_r'] >= 1.5) & (oos['result_r'] < 0.5)]
print(f'\nTrades con MFE>=1.5R que terminaron con <0.5R: n={len(retrace_1r)}')
if len(retrace_1r) > 0:
    print(f'  result_r promedio: {retrace_1r["result_r"].mean():.3f}R')
    print(f'  MFE promedio: {retrace_1r["mfe_r"].mean():.2f}R')
    by_reason = retrace_1r['reason'].value_counts()
    print(f'  Por razon: {dict(by_reason)}')
