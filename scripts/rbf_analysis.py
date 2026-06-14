"""
RBF Win/Loss analysis — bt_output.json
Discrimina features entre wins y losses para detectar patrones calibrables.
"""
import json, sys
from collections import defaultdict

with open(sys.argv[1], encoding='utf-16') as f:
    data = json.load(f)

trades = [t for t in data['trades'] if t['reason'] != 'DATA_END']
wins   = [t for t in trades if t['resultR'] > 0]
losses = [t for t in trades if t['resultR'] < 0]

n = len(trades)

def avg(lst, key):
    vals = [t[key] for t in lst if t.get(key) is not None]
    return sum(vals)/len(vals) if vals else None

def pct(lst): return len(lst)/n*100

def sep(title):
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)

def row(label, w_val, l_val, unit="", note=""):
    if w_val is None or l_val is None:
        return
    delta = w_val - l_val
    flag = " ★" if abs(delta) > abs(w_val) * 0.20 else ""
    note_str = f"  [{note}]" if note else ""
    print(f"  {label:25s}: W={w_val:>8.2f}{unit}  L={l_val:>8.2f}{unit}  Δ={delta:>+8.2f}{unit}{flag}{note_str}")

# ─── RESUMEN ───────────────────────────────────────────────────────────────────
sep("RESUMEN GLOBAL")
print(f"  n={n}  Wins={len(wins)} ({len(wins)/n*100:.0f}%)  Losses={len(losses)} ({len(losses)/n*100:.0f}%)")
print(f"  Total R = {sum(t['resultR'] for t in trades):+.2f}R")
print(f"  Avg R wins   = {avg(wins,'resultR'):+.3f}R")
print(f"  Avg R losses = {avg(losses,'resultR'):+.3f}R")

# ─── FEATURE DISCRIMINACION ────────────────────────────────────────────────────
sep("FEATURES — Wins vs Losses")
print("  (★ = diferencia >20% del valor de wins — señal fuerte)")
print()
row("VR en breakout",       avg(wins,'vr'),          avg(losses,'vr'),          "x")
row("Range %",              avg(wins,'rangePct'),     avg(losses,'rangePct'),    "%",  "menor = rango mas apretado")
row("Range bars",           avg(wins,'rangeBars'),    avg(losses,'rangeBars'),   "b")
row("CVD en rango",         avg(wins,'cvdInRange'),   avg(losses,'cvdInRange'),  "",   "mas negativo = mas presion vendedora")
row("CVD slope (abs)",      avg([t for t in wins   if t.get('cvdSlope')], 'cvdSlope'),
                            avg([t for t in losses if t.get('cvdSlope')], 'cvdSlope'), "")
row("OBI en entry",         avg(wins,'obi'),          avg(losses,'obi'),         "",   ">0 = bid pressure (absorcion)")
row("Price vs VWAP %",      avg(wins,'priceVsVwap'),  avg(losses,'priceVsVwap'), "%",  "neg = debajo VWAP (bear bias)")
row("Duracion (min)",       avg(wins,'durationMin'),  avg(losses,'durationMin'), "m",  "wins rapidas o lentas?")
row("Score confluencia",    avg(wins,'score'),        avg(losses,'score'),       "pts")

# ─── VR BUCKETS ────────────────────────────────────────────────────────────────
sep("VR BUCKETS (volumen del breakout)")
buckets = [(0, 2), (2, 3), (3, 5), (5, 99)]
labels  = ["<2x (pre)", "2-3x", "3-5x", ">5x"]
for (lo, hi), lab in zip(buckets, labels):
    subset = [t for t in trades if lo <= t.get('vr', 0) < hi]
    if not subset: continue
    sw = [t for t in subset if t['resultR'] > 0]
    tot = sum(t['resultR'] for t in subset)
    print(f"  {lab:12s}: n={len(subset):2d}  WR={len(sw)/len(subset)*100:.0f}%  TotR={tot:+.2f}R  AvgR={tot/len(subset):+.3f}R")

# ─── RANGE % BUCKETS ───────────────────────────────────────────────────────────
sep("RANGE % BUCKETS (apretado vs amplio)")
rbuckets = [(0, 0.25), (0.25, 0.40), (0.40, 0.50), (0.50, 1.0)]
rlabels  = ["<0.25%", "0.25-0.40%", "0.40-0.50%", ">0.50%"]
for (lo, hi), lab in zip(rbuckets, rlabels):
    subset = [t for t in trades if lo <= t.get('rangePct', 0) < hi]
    if not subset: continue
    sw = [t for t in subset if t['resultR'] > 0]
    tot = sum(t['resultR'] for t in subset)
    print(f"  {lab:14s}: n={len(subset):2d}  WR={len(sw)/len(subset)*100:.0f}%  TotR={tot:+.2f}R  AvgR={tot/len(subset):+.3f}R")

# ─── SCORE BREAKDOWN ───────────────────────────────────────────────────────────
sep("SCORE DE CONFLUENCIA")
for s in range(0, 7):
    subset = [t for t in trades if t.get('score') == s]
    if not subset: continue
    sw = [t for t in subset if t['resultR'] > 0]
    tot = sum(t['resultR'] for t in subset)
    note = " ← vetado en live" if s == 4 else ""
    print(f"  Score {s}: n={len(subset):2d}  WR={len(sw)/len(subset)*100:.0f}%  TotR={tot:+.2f}R  AvgR={tot/len(subset):+.3f}R{note}")

# ─── FLAGS DE CONFLUENCIA ──────────────────────────────────────────────────────
sep("FLAGS — frecuencia en Wins vs Losses")
all_flags = ['stacked_imbalance', 'absorption', 'lvn_thin', 'vwap_bias', 'oi_momentum', 'obi_trap']
print(f"  {'Flag':22s}  {'W%':>5}  {'L%':>5}  {'Δpp':>6}  {'Señal'}")
print("  " + "-"*55)
for f in all_flags:
    w_pct = sum(1 for t in wins   if f in t.get('confluenceFlags',[])) / max(len(wins),1) * 100
    l_pct = sum(1 for t in losses if f in t.get('confluenceFlags',[])) / max(len(losses),1) * 100
    delta = w_pct - l_pct
    signal = "★ WINS" if delta > 15 else ("★ LOSSES" if delta < -15 else "neutro")
    print(f"  {f:22s}  {w_pct:>5.0f}%  {l_pct:>5.0f}%  {delta:>+5.0f}pp  {signal}")

# ─── PRE vs POST ───────────────────────────────────────────────────────────────
sep("PRE-BREAKOUT vs POST-BREAKOUT")
for label, subset in [("Post (VR≥3)", [t for t in trades if not t.get('isPreBreakout')]),
                       ("Pre  (VR≥1.5)", [t for t in trades if t.get('isPreBreakout')])]:
    if not subset: continue
    sw  = [t for t in subset if t['resultR'] > 0]
    tot = sum(t['resultR'] for t in subset)
    vr  = avg(subset, 'vr')
    rng = avg(subset, 'rangePct')
    print(f"  {label}: n={len(subset):2d}  WR={len(sw)/len(subset)*100:.0f}%  TotR={tot:+.2f}R  AvgR={tot/len(subset):+.3f}R  avgVR={vr:.2f}x  avgRng={rng:.3f}%")

# ─── COULD HAVE DONE MORE ─────────────────────────────────────────────────────
sep("TRAILING STOPS — dejaron R sobre la mesa?")
trailing = [t for t in trades if t['reason'] == 'TRAILING_STOP']
trail_r  = [t['resultR'] for t in trailing]
target_r = [2.0 if not t.get('isPreBreakout') else 3.0 for t in trailing]
left_on  = [tgt - r for tgt, r in zip(target_r, trail_r)]
print(f"  n trailing = {len(trailing)}")
print(f"  Avg exit R = {sum(trail_r)/len(trail_r):+.3f}R  (target avg = {sum(target_r)/len(target_r):.1f}R)")
print(f"  R dejado/trade = {sum(left_on)/len(left_on):+.3f}R  ({sum(left_on):+.2f}R total)")
print(f"  Si todos llegaran a target: +{sum(target_r):.1f}R vs actual +{sum(trail_r):.2f}R")
print()
print(f"  {'#':>3} {'Sym':8} {'Ses':10} {'Exit R':>7} {'Target':>7} {'Dejado':>8} {'Flags'}")
print("  " + "-"*65)
for t in trailing:
    tgt  = 2.0 if not t.get('isPreBreakout') else 3.0
    left = tgt - t['resultR']
    ses  = {'LondonNyOverlap':'Overlap','NewYork':'NY'}.get(t['session'],t['session'])
    flags = ','.join(t.get('confluenceFlags',[])) or '—'
    print(f"  {t.get('idx',0):>3} {t['sym']:8} {ses:10} {t['resultR']:>+7.3f}R {tgt:>7.1f}R {left:>+8.3f}R  [{flags}]")

# ─── PEORES LOSSES ────────────────────────────────────────────────────────────
sep("STOP LOSSES — patrones de las perdidas")
stops = [t for t in trades if t['reason'] == 'STOP_LOSS']
print(f"  n stops = {len(stops)}")
print()
print(f"  {'#':>3} {'Sym':8} {'Ses':10} {'VR':>5} {'Rng%':>6} {'CVD':>8} {'OBI':>7} {'Score':>5} {'Dur':>5} {'Pre':>3}")
print("  " + "-"*75)
for t in stops:
    ses  = {'LondonNyOverlap':'Overlap','NewYork':'NY'}.get(t['session'],t['session'])
    pre  = 'PRE' if t.get('isPreBreakout') else '—'
    cvd  = t.get('cvdInRange') or 0
    obi  = t.get('obi') or 0
    dur  = t.get('durationMin') or 0
    print(f"  {t.get('idx',0):>3} {t['sym']:8} {ses:10} {t.get('vr',0):>5.2f} {t.get('rangePct',0):>6.3f} {cvd:>8.0f} {obi:>7.3f} {t.get('score',0):>5} {dur:>5}m {pre:>3}")

print()
print("  Avg stats stops:")
row("  VR",         avg(stops,'vr'),         avg(wins,'vr'),         "x",  "vs wins")
row("  Range %",    avg(stops,'rangePct'),    avg(wins,'rangePct'),   "%",  "vs wins")
row("  CVD rango",  avg(stops,'cvdInRange'),  avg(wins,'cvdInRange'), "",   "vs wins")
row("  OBI",        avg(stops,'obi'),         avg(wins,'obi'),        "",   "vs wins")
row("  Duracion",   avg(stops,'durationMin'), avg(wins,'durationMin'),"m",  "vs wins")
