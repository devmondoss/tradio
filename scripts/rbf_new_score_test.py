#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys, io, csv
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
"""
Prueba del nuevo sistema de scoring RBF.

PRINCIPIO VIEJO (malo):
  "Cuanta presion hay en mi direccion?"
  → suma si CVD negativo, OBI bearish, VR alto, etc.
  → resultado: mide moves CONSUMIDOS, no moves POR COMENZAR

PRINCIPIO NUEVO (hypothesis):
  "Cuantos participantes estan en el lado equivocado?"
  → suma si hay compradores atrapados, estructura limpia, momentum informado
  → la entrada ideal: rango limpio + compradores activos = squeeze inminente

FLAGS NUEVOS (Short):
  [+2] expansion_n = 0       → rango fresco, move no consumido
  [+1] expansion_n = 1       → aceptable
  [+1] obi BULLISH en breakout  → compradores en el libro = van a ser squeezed
  [+1] vpin >= 0.40          → momentum informado (flow institucional, no ruido)
  [+1] stacked_bear          → estructura de oferta confirmada
  [+1] thin_below            → vacio por debajo = target alcanzable sin obstaculos
  [+1] absorption_ask        → sellers absorbiendo = institucionales distribuyendo
  [+1] oi_momentum           → OI expandiendo = nuevas posiciones = conviction real

FLAGS REMOVIDOS (demostrado no discriminar o invertidos):
  ✗ CvdSlopeSostenido  → puede indicar move ya consumido
  ✗ ObiAlineado        → inversamente correlacionado (OBI bearish = buyers ya salieron)
  ✗ SessionCvdAligned  → similar a CVD slope
  ✗ BigCvdAligned      → similar
  ✗ ObiMultiDepth      → mismo problema que ObiAlineado
  ✗ ObiIntrabarMean    → mismo

FLAGS MANTENIDOS:
  = LvnOThinZone       → estructural, menos dependiente de regimen
  = VwapBias           → contexto macro
  = OiMomentum         → expansion de posiciones = conviction

Max score nuevo: 8 puntos
"""
from collections import defaultdict

# ── Cargar datos del backtest consciente ────────────────────────────────────
setups_raw = []
with open('scripts/_conscious_bt.csv', encoding='utf-8') as f:
    for row in csv.DictReader(f):
        if row['direction'] != 'Short':
            continue
        def sf(v, d=0.0):
            try: return float(v) if v not in ('', 'None', None) else d
            except: return d
        setups_raw.append({
            'sym':        row['sym'],
            'ts':         row['ts'],
            'session':    row['session'],
            'result':     sf(row['result_r']),
            'exp_n':      int(row['expansion_n']),
            'vr_brk':     sf(row['vr_brk']),
            'dz_dir':     sf(row['dz_dir']),
            'obi_brk':    sf(row['obi_brk']),
            'avg_obi':    sf(row['avg_obi']),
            'avg_vpin':   sf(row['avg_vpin']),
            'cum_delta':  sf(row['cum_delta']),
            'last5':      sf(row['last5_delta']),
            'ext':        sf(row['ext_pct']),
            'stk_bear':   int(row['stacked_bear']),
            'stk_bull':   int(row['stacked_bull']),
            'oi_mom_n':   int(row['oi_mom_n']),
            'old_flags':  row['flags'],
            'old_score':  sf(row['score']),
            'pre_ok':     row['pre_cvd_ok'] == 'True',
        })

def new_score(s):
    """
    Nuevo scoring — "Participantes atrapados + Estructura limpia"
    Retorna (score, lista de flags activos)
    """
    score = 0
    flags = []

    # [0-2] Rango fresco — sin expansion reciente
    if s['exp_n'] == 0:
        score += 2; flags.append('rango_fresco')
    elif s['exp_n'] == 1:
        score += 1; flags.append('rango_ok')
    # exp_n >= 2: 0 puntos (move en proceso = entrada tardia)

    # [+1] OBI bullish en breakout Short → compradores atrapados
    # obi_brk > 0 significa book con presion compradora cuando el precio rompe abajo
    if s['obi_brk'] > 0.02:
        score += 1; flags.append('obi_trap_buyers')

    # [+1] VPIN >= 0.40 → momentum informado, no ruido retail
    if s['avg_vpin'] >= 0.40:
        score += 1; flags.append('vpin_momentum')

    # [+1] Stacked imbalance bearish dominante (estructura de oferta)
    if s['stk_bear'] > s['stk_bull'] and s['stk_bear'] >= 3:
        score += 1; flags.append('stacked_supply')

    # [+1] OI momentum — contratos abiertos en direccion del move
    # (oi_mom_n: barras con OI alineado en 25 pre-barras)
    if s['oi_mom_n'] >= 5:
        score += 1; flags.append('oi_expanding')

    # [+1] VR >= 3x — confirmacion de volumen (mantenemos como positivo moderado)
    if s['vr_brk'] >= 3.0:
        score += 1; flags.append(f'vr_confirmed({s["vr_brk"]:.1f}x)')

    # [+1] Extension pequeña — precio acaba de romper, queda espacio
    # ext < 0.1% = breakout fresco sin sobre-extension
    if s['ext'] < 0.10:
        score += 1; flags.append('ext_fresca')

    return score, flags

# ── Aplicar nuevo scoring ────────────────────────────────────────────────────
for s in setups_raw:
    s['new_score'], s['new_flags'] = new_score(s)

# ── Baseline ─────────────────────────────────────────────────────────────────
n_all   = len(setups_raw)
wr_all  = sum(1 for s in setups_raw if s['result'] > 0) / n_all * 100
avg_all = sum(s['result'] for s in setups_raw) / n_all

print('=' * 72)
print(f'NUEVO SCORING RBF — {n_all} Shorts  baseline WR={wr_all:.0f}%  AvgR={avg_all:+.3f}')
print('=' * 72)

# ── Comparar score viejo vs nuevo ────────────────────────────────────────────
def stats(lst):
    if not lst: return 0, 0.0, 0.0, 0.0
    wins = sum(1 for s in lst if s['result'] > 0)
    tot  = sum(s['result'] for s in lst)
    return len(lst), wins/len(lst)*100, tot/len(lst), tot

print('\n[A] SCORE VIEJO (referencia)')
by_old = defaultdict(list)
for s in setups_raw:
    by_old[int(s['old_score'])].append(s)
for sc in sorted(by_old):
    lst = by_old[sc]
    n, wr, avg, tot = stats(lst)
    mono = '+' if avg > avg_all else '-'
    print(f"  {mono} Score {sc:>2}: n={n:>3}  WR={wr:4.0f}%  AvgR={avg:+.3f}  TotalR={tot:+.2f}")

print('\n[B] SCORE NUEVO')
by_new = defaultdict(list)
for s in setups_raw:
    by_new[s['new_score']].append(s)
for sc in sorted(by_new):
    lst = by_new[sc]
    n, wr, avg, tot = stats(lst)
    mono = '+' if avg > avg_all else '-'
    bar  = '#' * min(n, 20)
    print(f"  {mono} Score {sc:>2}: n={n:>3}  WR={wr:4.0f}%  AvgR={avg:+.3f}  TotalR={tot:+.2f}  {bar}")

# ── Monotonicity check ───────────────────────────────────────────────────────
print('\n[C] MONOTONICITY — AvgR debe subir con el score')
scores_new = sorted(by_new.keys())
avgs_new   = [sum(s['result'] for s in by_new[sc]) / len(by_new[sc]) for sc in scores_new]
is_mono    = all(avgs_new[i] <= avgs_new[i+1] for i in range(len(avgs_new)-1))
print(f"  Score viejo monotono: {'SI' if all(sum(s['result'] for s in by_old[sc])/len(by_old[sc]) <= sum(s['result'] for s in by_old[sc2])/len(by_old[sc2]) for sc, sc2 in zip(sorted(by_old)[:-1], sorted(by_old)[1:]) if by_old[sc] and by_old[sc2]) else 'NO'}")
print(f"  Score nuevo monotono: {'SI' if is_mono else 'NO'}")

# ── Impacto de cada flag nuevo ───────────────────────────────────────────────
print('\n[D] IMPACTO DE CADA FLAG NUEVO vs BASELINE')
all_new_flags = set()
for s in setups_raw:
    for f in s['new_flags']:
        all_new_flags.add(f.split('(')[0])  # sin el valor numerico

flag_data = defaultdict(lambda: {'wins':0,'total':0,'sum_r':0.0})
for s in setups_raw:
    for f in s['new_flags']:
        fk = f.split('(')[0]
        flag_data[fk]['total'] += 1
        flag_data[fk]['sum_r'] += s['result']
        if s['result'] > 0: flag_data[fk]['wins'] += 1

print(f"  {'Flag':<22}  {'n':>4}  {'WR':>5}  {'AvgR':>7}  {'delta':>8}")
print(f"  {'-'*55}")
for flag, st in sorted(flag_data.items(), key=lambda x: x[1]['sum_r']/max(x[1]['total'],1), reverse=True):
    n   = st['total']
    wr  = st['wins']/n*100 if n else 0
    avg = st['sum_r']/n if n else 0
    delta = avg - avg_all
    marker = '++' if delta > 0.1 else ('--' if delta < -0.1 else '  ')
    print(f"  {marker} {flag:<22}  {n:>4}  {wr:>4.0f}%  {avg:>+7.3f}  {delta:>+8.3f}")

# ── Mejor filtro con nuevo score ─────────────────────────────────────────────
print('\n[E] UMBRALES OPTIMOS (nuevo score)')
for min_sc in range(0, 8):
    lst = [s for s in setups_raw if s['new_score'] >= min_sc]
    if not lst: break
    n, wr, avg, tot = stats(lst)
    print(f"  new_score >= {min_sc}: n={n:>3}  WR={wr:4.0f}%  AvgR={avg:+.3f}  TotalR={tot:+.2f}")

# ── Casos donde nuevo score falla (para entender limites) ────────────────────
print('\n[F] SORPRESAS DEL NUEVO SCORE')
print('  Perdedores con score nuevo >= 5:')
hi_lose = [s for s in setups_raw if s['new_score'] >= 5 and s['result'] < 0]
for s in hi_lose:
    print(f"    {s['ts']}  {s['sym']:<8}  new={s['new_score']}  R={s['result']:+.2f}"
          f"  exp={s['exp_n']}  vr={s['vr_brk']:.1f}  obi={s['obi_brk']:+.3f}"
          f"  flags: {' '.join(s['new_flags'])}")

print('  Ganadores con score nuevo = 0:')
lo_win = [s for s in setups_raw if s['new_score'] == 0 and s['result'] > 0]
for s in lo_win:
    print(f"    {s['ts']}  {s['sym']:<8}  R={s['result']:+.2f}"
          f"  exp={s['exp_n']}  vr={s['vr_brk']:.1f}  obi={s['obi_brk']:+.3f}"
          f"  old_flags: {s['old_flags'][:50]}")

# ── Comparacion directa old vs new en mismos setups ──────────────────────────
print('\n[G] MISMOS SETUPS — old score vs new score')
print(f"  {'Fecha':>12}  {'Sym':<8}  {'Old':>5}  {'New':>5}  {'Result':>8}  Mejora?")
print(f"  {'-'*65}")
for s in sorted(setups_raw, key=lambda x: x['ts']):
    diff = s['new_score'] - s['old_score']
    win  = 'W' if s['result'] > 0 else 'L'
    # Mostrar solo los que cambian significativamente
    if abs(diff) >= 2:
        mejor = 'OK' if (diff > 0 and s['result'] > 0) or (diff < 0 and s['result'] < 0) else 'MAL'
        print(f"  {s['ts']:>12}  {s['sym']:<8}  {s['old_score']:>5.0f}  {s['new_score']:>5}  "
              f"{s['result']:>+8.3f} {win}  {mejor} (delta={diff:+.0f})")

print('\nListo.')
