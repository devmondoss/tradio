"""
VR deep dive — cómo combina con otros features de orderflow.
Objetivo: entender qué acompaña a VR 3-5x (mejor bucket) y por qué VR>5x pierde.
"""
import json, sys, math
from collections import defaultdict

with open(sys.argv[1], encoding='utf-16') as f:
    data = json.load(f)

trades = [t for t in data['trades'] if t['reason'] != 'DATA_END']
wins   = [t for t in trades if t['resultR'] > 0]
losses = [t for t in trades if t['resultR'] < 0]

def avg(lst, key):
    vals = [t[key] for t in lst if t.get(key) is not None]
    return sum(vals)/len(vals) if vals else 0.0

def sep(title):
    print()
    print("=" * 62)
    print(f"  {title}")
    print("=" * 62)

# Buckets VR
def vr_bucket(t):
    v = t.get('vr', 0)
    if v < 2:   return "<2x"
    if v < 3:   return "2-3x"
    if v < 5:   return "3-5x"
    return ">5x"

buckets = {"<2x": [], "2-3x": [], "3-5x": [], ">5x": []}
for t in trades:
    buckets[vr_bucket(t)].append(t)

# ─── 1. POR QUE VR>5x PIERDE ──────────────────────────────────────────────────
sep("VR >5x vs 3-5x — comparacion directa")
b35  = buckets["3-5x"]
bhi  = buckets[">5x"]
b35w = [t for t in b35 if t['resultR'] > 0]
b35l = [t for t in b35 if t['resultR'] < 0]
bhiw = [t for t in bhi if t['resultR'] > 0]
bhil = [t for t in bhi if t['resultR'] < 0]

print(f"\n  {'Feature':25s} {'3-5x W':>9} {'3-5x L':>9} {'  ':2} {'>5x W':>9} {'>5x L':>9}")
print("  " + "-"*65)
for key, label, fmt in [
    ('vr',           'VR',              '{:>9.2f}'),
    ('rangePct',     'Range %',         '{:>9.3f}'),
    ('cvdInRange',   'CVD en rango',    '{:>9.0f}'),
    ('obi',          'OBI',             '{:>9.3f}'),
    ('priceVsVwap',  'Precio vs VWAP%', '{:>9.2f}'),
    ('durationMin',  'Duracion (min)',   '{:>9.0f}'),
    ('score',        'Score',           '{:>9.1f}'),
]:
    v35w = avg(b35w, key); v35l = avg(b35l, key)
    vhiw = avg(bhiw, key); vhil = avg(bhil, key)
    def f(v): return fmt.format(v)
    print(f"  {label:25s} {f(v35w):>9} {f(v35l):>9}  | {f(vhiw):>9} {f(vhil):>9}")

print(f"\n  3-5x: {len(b35w)}W/{len(b35l)}L (WR={len(b35w)/len(b35)*100:.0f}%)   >5x: {len(bhiw)}W/{len(bhil)}L (WR={len(bhiw)/len(bhi)*100:.0f}%)")

# ─── 2. VR x CVD — combinacion cruzada ────────────────────────────────────────
sep("VR x CVD en rango — combinacion cruzada")
print("  CVD normalizado por simbolo (Z-score aproximado por rango de valores)")
print()

# Normalizar CVD por símbolo (simple: dividir por |max CVD del símbolo|)
# Así BTC y SOL son comparables
sym_max_cvd = defaultdict(float)
for t in trades:
    v = abs(t.get('cvdInRange') or 0)
    sym_max_cvd[t['sym']] = max(sym_max_cvd[t['sym']], v)

def norm_cvd(t):
    raw = t.get('cvdInRange') or 0
    mx  = sym_max_cvd.get(t['sym'], 1) or 1
    return raw / mx  # -1 a 0 para shorts (más negativo = más presión)

# Ahora cruzar VR bucket x CVD strength
print(f"  {'VR bucket':12} {'CVD norm avg W':>15} {'CVD norm avg L':>15} {'Diferencia':>12}")
print("  " + "-"*58)
for label, subset in buckets.items():
    sw = [t for t in subset if t['resultR'] > 0]
    sl = [t for t in subset if t['resultR'] < 0]
    if not subset: continue
    aw = sum(norm_cvd(t) for t in sw)/len(sw) if sw else 0
    al = sum(norm_cvd(t) for t in sl)/len(sl) if sl else 0
    delta = aw - al
    flag = " ★" if abs(delta) > 0.1 else ""
    print(f"  {label:12} {aw:>15.3f} {al:>15.3f} {delta:>+12.3f}{flag}")

# ─── 3. COMBINACION VR 3-5x + CVD FUERTE ─────────────────────────────────────
sep("VR 3-5x con CVD fuerte vs debil")
print("  CVD 'fuerte' = normalizado < -0.30 (presion vendedora intensa)")
print()

for label, subset in buckets.items():
    if not subset: continue
    strong_cvd = [t for t in subset if norm_cvd(t) < -0.30]
    weak_cvd   = [t for t in subset if norm_cvd(t) >= -0.30]
    if strong_cvd:
        sw = [t for t in strong_cvd if t['resultR'] > 0]
        tot = sum(t['resultR'] for t in strong_cvd)
        print(f"  {label} + CVD fuerte: n={len(strong_cvd):2d}  WR={len(sw)/len(strong_cvd)*100:.0f}%  TotR={tot:+.2f}R  AvgR={tot/len(strong_cvd):+.3f}R")
    if weak_cvd:
        sw = [t for t in weak_cvd if t['resultR'] > 0]
        tot = sum(t['resultR'] for t in weak_cvd)
        print(f"  {label} + CVD debil:  n={len(weak_cvd):2d}  WR={len(sw)/len(weak_cvd)*100:.0f}%  TotR={tot:+.2f}R  AvgR={tot/len(weak_cvd):+.3f}R")
    print()

# ─── 4. VR x OBI ──────────────────────────────────────────────────────────────
sep("VR x OBI — absorcion en entry")
print("  OBI > 0 = presion compradora (bids) en entry — absorcion para short")
print("  OBI < 0 = presion vendedora (asks) en entry")
print()

for label, subset in buckets.items():
    if not subset: continue
    pos_obi = [t for t in subset if (t.get('obi') or 0) > 0]
    neg_obi = [t for t in subset if (t.get('obi') or 0) <= 0]
    parts = []
    if pos_obi:
        sw = [t for t in pos_obi if t['resultR'] > 0]
        tot = sum(t['resultR'] for t in pos_obi)
        parts.append(f"OBI>0: n={len(pos_obi)} WR={len(sw)/len(pos_obi)*100:.0f}% AvgR={tot/len(pos_obi):+.3f}R")
    if neg_obi:
        sw = [t for t in neg_obi if t['resultR'] > 0]
        tot = sum(t['resultR'] for t in neg_obi)
        parts.append(f"OBI<0: n={len(neg_obi)} WR={len(sw)/len(neg_obi)*100:.0f}% AvgR={tot/len(neg_obi):+.3f}R")
    print(f"  {label:8}: {' | '.join(parts)}")

# ─── 5. VR x FLAGS ────────────────────────────────────────────────────────────
sep("VR x FLAGS de confluencia (en bucket 3-5x y >5x)")
flags_all = ['stacked_imbalance', 'lvn_thin', 'vwap_bias', 'obi_trap']
for label in ["3-5x", ">5x"]:
    subset = buckets[label]
    sw = [t for t in subset if t['resultR'] > 0]
    sl = [t for t in subset if t['resultR'] < 0]
    print(f"\n  {label} (n={len(subset)}, WR={len(sw)/len(subset)*100:.0f}%)")
    for f in flags_all:
        w_pct = sum(1 for t in sw if f in t.get('confluenceFlags',[])) / max(len(sw),1) * 100
        l_pct = sum(1 for t in sl if f in t.get('confluenceFlags',[])) / max(len(sl),1) * 100
        delta = w_pct - l_pct
        note = "WINS" if delta > 20 else ("LOSSES" if delta < -20 else "neutro")
        print(f"    {f:22s}: W={w_pct:.0f}%  L={l_pct:.0f}%  delta={delta:+.0f}pp  ({note})")

# ─── 6. HIPOTESIS: VR>5 = FAKEOUT — el CVD confirma? ─────────────────────────
sep("Hipotesis VR>5x = breakout exhausto")
print("  Si VR>5x es un fakeout, esperariamos CVD debil (no acompano el volumen)")
print()
for t in sorted(bhi, key=lambda x: x['resultR']):
    nc = norm_cvd(t)
    flag = "LOSS" if t['resultR'] < 0 else "WIN"
    cvd_note = "CVD debil" if nc > -0.20 else ("CVD medio" if nc > -0.50 else "CVD fuerte")
    print(f"  [{flag}] #{t.get('idx',0):2d} {t['sym']:8} VR={t.get('vr',0):.1f}x  CVDnorm={nc:+.3f} ({cvd_note:12})  OBI={t.get('obi',0):+.3f}  R={t['resultR']:+.2f}")

# ─── 7. BEST COMBO SIGNAL ─────────────────────────────────────────────────────
sep("Combinacion optima: VR 3-5x + Score >= 2")
combo = [t for t in buckets["3-5x"] if t.get('score', 0) >= 2]
other = [t for t in trades if t not in combo]
cw = [t for t in combo if t['resultR'] > 0]
ow = [t for t in other  if t['resultR'] > 0]
ct = sum(t['resultR'] for t in combo)
ot = sum(t['resultR'] for t in other)
print(f"  VR 3-5x + Score>=2: n={len(combo):2d}  WR={len(cw)/max(len(combo),1)*100:.0f}%  TotR={ct:+.2f}R  AvgR={ct/max(len(combo),1):+.3f}R")
print(f"  Resto del sistema:   n={len(other):2d}  WR={len(ow)/max(len(other),1)*100:.0f}%  TotR={ot:+.2f}R  AvgR={ot/max(len(other),1):+.3f}R")
print()

# Detalle trades del combo
print(f"  Trades VR 3-5x + Score>=2:")
print(f"  {'#':>3} {'Sym':8} {'Ses':8} {'VR':>5} {'Score':>5} {'CVDn':>7} {'OBI':>7} {'Flags'}")
print("  " + "-"*72)
for t in combo:
    ses = {'LondonNyOverlap':'Overlap','NewYork':'NY'}.get(t['session'],t['session'])
    nc  = norm_cvd(t)
    flags = ','.join([f[:4] for f in t.get('confluenceFlags',[])]) or '-'
    res   = f"+{t['resultR']:.2f}" if t['resultR']>0 else f"{t['resultR']:.2f}"
    print(f"  {t.get('idx',0):>3} {t['sym']:8} {ses:8} {t.get('vr',0):>5.1f} {t.get('score',0):>5} {nc:>+7.3f} {t.get('obi',0):>+7.3f}  [{flags}] -> {res}R")
