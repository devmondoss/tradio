"""
Analisis profundo de exit quality y potencial de mejora.
Pregunta clave: donde se pierde R y como recuperarlo.
"""
import pandas as pd, numpy as np

df = pd.read_csv('exports/basics_trades.csv')
oos = df[df['oos'] == True].copy()

# ── 1. MAE de winners: dip antes de ir al target ──────────────────────────────
print('=== MAE DE WINNERS (cuanto retroceden antes de ganar) ===')
wins = oos[oos['result_r'] > 0]
print(f'Winners n={len(wins)}')
for thr in [0.1, 0.2, 0.3, 0.5, 0.7]:
    n = (wins['mae_r'] > thr).sum()
    print(f'  Winners con MAE > {thr}R: {n} ({n/len(wins)*100:.1f}%)')
print(f'  MAE promedio winners: {wins["mae_r"].mean():.3f}R')
print()
print('=> Si pusieramos BE trail en 1R, perderíamos los winners que tienen MAE>~0.05R después de 1R')
print('   (no podemos medirlo exactamente sin barra-a-barra, pero MAE total es proxy)')

# ── 2. Score x target: cuanto se mueve cada score group ──────────────────────
print()
print('=== PROFUNDIDAD DEL MOVIMIENTO POR SCORE ===')
print(f'{"Score":<8} {"n":>4} {"WR":>6} {"mfe_avg":>8} {"mfe_med":>8} {"mfe>=2R":>8} {"target_hit":>10} {"cvd_exit":>9}')
print('-'*70)
for sc in range(5):
    s = oos[oos['score'] == sc]
    if len(s) == 0: continue
    wr = (s['result_r'] > 0).mean() * 100
    mfe_avg = s['mfe_r'].mean()
    mfe_med = s['mfe_r'].median()
    mfe2 = (s['mfe_r'] >= 2.0).sum()
    tgt_hit = (s['reason'] == 'target').sum()
    cvd_ex = (s['reason'] == 'cvd_exit').sum()
    print(f'Score {sc}/4  {len(s):>4}  {wr:>5.1f}%  {mfe_avg:>7.2f}R  {mfe_med:>7.2f}R  {mfe2:>7}({mfe2/len(s)*100:.0f}%)  {tgt_hit:>7}({tgt_hit/len(s)*100:.0f}%)  {cvd_ex:>7}({cvd_ex/len(s)*100:.0f}%)')

# ── 3. Objetivo optimo por score: si hubieramos usado 1R/1.5R/2R/3R ──────────
print()
print('=== SIMULACION: resultado si target fuera X por cada score ===')
print('(usando mfe_r como proxy — si mfe>=target asumimos target hit, sino result_r real)')
print()

def sim_target(trades, target_r):
    """Simula resultado si el target fuera target_r (usando MFE como proxy)"""
    results = []
    for _, t in trades.iterrows():
        if t['mfe_r'] >= target_r:
            results.append(target_r)  # hubiera alcanzado target
        else:
            results.append(t['result_r'])  # mismo resultado
    return results

print(f'{"Score":<8}', end='')
for tgt in [1.0, 1.5, 2.0, 2.5, 3.0]:
    print(f'  tgt={tgt}R', end='')
print()
print('-'*70)

for sc in range(5):
    s = oos[oos['score'] == sc]
    if len(s) == 0: continue
    print(f'Score {sc}/4 ', end='')
    for tgt in [1.0, 1.5, 2.0, 2.5, 3.0]:
        rs = sim_target(s, tgt)
        avg = np.mean(rs)
        wr = sum(1 for r in rs if r > 0) / len(rs) * 100
        print(f'  {avg:>+.3f}({wr:.0f}%)', end='')
    print()

# ── 4. Trailing stop simulation: mover a BE en 1R ─────────────────────────────
print()
print('=== SIMULACION BE TRAIL @ 1R ===')
print('Si movemos stop a BE cuando MFE >= 1R:')
print('  - Trades que llegaron a 1R MFE pero stopping: resultado = 0R (en vez de -1R)')
print('  - Riesgo: winners que dip<0 despues de 1R y se cortan en BE')
print()

# trades que alcanzaron 1R MFE
reached_1r = oos[oos['mfe_r'] >= 1.0]
# de esos, cuantos terminaron siendo stops (es decir, retrocedieron desde >1R a stop)
stranded = reached_1r[reached_1r['reason'] == 'stop']
# cuantos winners que alcanzaron 1R MFE (estos son "seguros" - no se afectan)
winners_past_1r = reached_1r[reached_1r['result_r'] > 0]

print(f'Trades que alcanzaron 1R MFE: {len(reached_1r)}')
print(f'  De esos, terminaron en stop (stranded):  {len(stranded)} -> se convierten en 0R')
print(f'  De esos, terminaron en win (safe):       {len(winners_past_1r)}')
print(f'  De esos, terminaron en cvd_exit:         {(reached_1r["reason"]=="cvd_exit").sum()}')
print()

# Cuanto vale convertir stranded a 0R?
# En $ depende del score y monthly_risk, pero en R: cada stranded = -1R -> 0R = +1R
r_gain = len(stranded) * 1.0  # cada stranded pasa de -1R a 0R
print(f'Ganancia en R pura: +{r_gain:.0f}R ({len(stranded)} x 1R)')

# ── 5. Por score: donde mas duele el stranding ────────────────────────────────
print()
print('=== STRANDING POR SCORE (donde mas duele) ===')
BOOST = [0.20, 0.50, 1.00, 1.50, 2.00]
for sc in range(5):
    s = oos[oos['score'] == sc]
    strnd = s[(s['mfe_r'] >= 1.0) & (s['reason'] == 'stop')]
    if len(strnd) == 0: continue
    # En terminos de capital: cada stranded = -2R de riesgo (entry_risk * -1)
    # el entry_risk = monthly_risk * BOOST[sc], pero monthly_risk crece
    # Como proxy: n_stranded * BOOST[sc] como peso relativo
    peso = len(strnd) * BOOST[sc]
    print(f'Score {sc}: {len(strnd)} stranded  x{BOOST[sc]:.2f} size  = peso relativo {peso:.2f}')

# ── 6. Session x target: cual sesion necesita target diferente ────────────────
print()
print('=== SESION x TARGET SIMULACION ===')
for sess in ['london', 'overlap', 'ny']:
    s = oos[oos['session'] == sess]
    if len(s) == 0: continue
    print(f'\n{sess} (n={len(s)}, WR={((s["result_r"]>0).mean()*100):.1f}%):')
    for tgt in [1.0, 1.5, 2.0, 3.0]:
        rs = sim_target(s, tgt)
        avg = np.mean(rs)
        wr = sum(1 for r in rs if r > 0) / len(rs) * 100
        total = sum(rs)
        print(f'  target {tgt}R: AvgR={avg:>+.3f}  WR={wr:.0f}%  TotalR={total:>+.1f}R')

# ── 7. Nivel x target ─────────────────────────────────────────────────────────
print()
print('=== NIVEL x TARGET SIMULACION ===')
for lv in ['WH+VAH', 'AH+VAH', 'VAH']:
    s = oos[oos['level'] == lv]
    if len(s) < 10: continue
    print(f'\n{lv} (n={len(s)}, WR={((s["result_r"]>0).mean()*100):.1f}%):')
    for tgt in [1.0, 1.5, 2.0, 3.0]:
        rs = sim_target(s, tgt)
        avg = np.mean(rs)
        total = sum(rs)
        print(f'  target {tgt}R: AvgR={avg:>+.3f}  TotalR={total:>+.1f}R')
