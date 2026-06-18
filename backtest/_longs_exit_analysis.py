import pandas as pd, numpy as np

df = pd.read_csv('exports/longs_trades.csv')
oos = df[df['oos'] == True].copy()

print('=== DISTRIBUCION MFE OOS ===')
print(f'Total trades: {len(oos)}')
for thr in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]:
    n = (oos['mfe_r'] >= thr).sum()
    print(f'  MFE >= {thr}R: {n} ({n/len(oos)*100:.1f}%)')

print()
print('=== TRADES STRANDED (MFE>=1R pero terminaron en stop) ===')
stranded = oos[(oos['mfe_r'] >= 1.0) & (oos['reason'] == 'stop')]
print(f'n={len(stranded)} ({len(stranded)/len(oos)*100:.1f}% del total OOS)')
print(f'MFE promedio stranded: {stranded["mfe_r"].mean():.2f}R')

print()
print('=== MFE POR SCORE OOS ===')
print(f'{"Score":<8} {"n":>4} {"WR":>6} {"mfe_avg":>8} {"mfe_med":>8} {"mfe>=2R":>8} {"tgt_hit":>8} {"stranded":>9}')
print('-'*72)
for sc in range(5):
    s = oos[oos['score'] == sc]
    if len(s) == 0: continue
    wr = (s['result_r'] > 0).mean() * 100
    strnd = s[(s['mfe_r'] >= 1.0) & (s['reason'] == 'stop')]
    tgt = (s['reason'] == 'target').sum()
    mfe2 = (s['mfe_r'] >= 2.0).sum()
    print(f'Score {sc}/4  {len(s):>4}  {wr:>5.1f}%  {s["mfe_r"].mean():>7.2f}R  {s["mfe_r"].median():>7.2f}R  {mfe2:>5}({mfe2/len(s)*100:.0f}%)  {tgt:>5}({tgt/len(s)*100:.0f}%)  {len(strnd):>5}({len(strnd)/len(s)*100:.0f}%)')

print()
print('=== SIMULACION: target optimo por score ===')
print('(mfe_r >= target => asumimos target hit)')
print()

def sim(trades, tgt):
    return [tgt if t['mfe_r'] >= tgt else t['result_r'] for _, t in trades.iterrows()]

print(f'{"Score":<8}', end='')
for tgt in [1.0, 1.5, 2.0, 2.5, 3.0]:
    print(f'  tgt={tgt}R', end='')
print()
print('-'*65)

best_targets = {}
for sc in range(5):
    s = oos[oos['score'] == sc]
    if len(s) == 0: continue
    print(f'Score {sc}/4 ', end='')
    best_avg = -99; best_tgt = 2.0
    for tgt in [1.0, 1.5, 2.0, 2.5, 3.0]:
        rs = sim(s, tgt)
        avg = np.mean(rs)
        wr = sum(1 for r in rs if r > 0) / len(rs) * 100
        print(f'  {avg:>+.3f}({wr:.0f}%)', end='')
        if avg > best_avg:
            best_avg = avg; best_tgt = tgt
    best_targets[sc] = best_tgt
    print(f'  <- mejor: {best_tgt}R')

print()
print('=== TARGETS OPTIMOS DETECTADOS ===')
for sc, tgt in best_targets.items():
    s = oos[oos['score'] == sc]
    current_avg = s['result_r'].mean()
    new_rs = sim(s, tgt)
    new_avg = np.mean(new_rs)
    delta = new_avg - current_avg
    print(f'Score {sc}: actual AvgR={current_avg:+.3f} => con target {tgt}R: {new_avg:+.3f}  (delta {delta:+.3f}R x {len(s)} trades)')

print()
print('=== POR SESION ===')
for sess in ['london', 'overlap', 'ny']:
    s = oos[oos['sess'] == sess]
    if len(s) == 0: continue
    strnd = s[(s['mfe_r'] >= 1.0) & (s['reason'] == 'stop')]
    wr = (s['result_r'] > 0).mean() * 100
    print(f'\n{sess} (n={len(s)}, WR={wr:.1f}%, mfe_avg={s["mfe_r"].mean():.2f}R, stranded={len(strnd)}):')
    for tgt in [1.0, 1.5, 2.0, 3.0]:
        rs = sim(s, tgt)
        avg = np.mean(rs); total = sum(rs)
        print(f'  target {tgt}R: AvgR={avg:+.3f}  TotalR={total:+.1f}R')

print()
print('=== POR NIVEL ===')
for lv in oos['level'].value_counts().head(5).index:
    s = oos[oos['level'] == lv]
    if len(s) < 8: continue
    wr = (s['result_r'] > 0).mean() * 100
    print(f'\n{lv} (n={len(s)}, WR={wr:.1f}%, mfe_avg={s["mfe_r"].mean():.2f}R):')
    for tgt in [1.0, 1.5, 2.0, 3.0]:
        rs = sim(s, tgt)
        avg = np.mean(rs); total = sum(rs)
        print(f'  target {tgt}R: AvgR={avg:+.3f}  TotalR={total:+.1f}R')
