import csv, math
from collections import defaultdict

rows = []
with open("scripts/rbf_microstructure.csv") as f:
    reader = csv.DictReader(f)
    for r in reader:
        if r["has_data"] != "True":
            continue
        def f2(k, _r=r):
            v = _r.get(k, "")
            try: return float(v) if v else None
            except: return None
        def i2(k, _r=r):
            v = _r.get(k, "")
            try: return int(float(v)) if v else None
            except: return None
        rows.append({
            "sym": r["symbol"], "dir": r["direction"], "ses": r["session"],
            "score": r["score"], "result_r": f2("result_r"), "status": r["status"],
            "entry": f2("entry"), "stop": f2("stop"), "target": f2("target"), "risk": f2("risk"),
            "pct_done": f2("pct_done"), "pre_move_r": f2("pre_move_r"),
            "cum_delta": f2("cum_delta"), "last5_delta": f2("last5_delta"),
            "avg_cvd": f2("avg_cvd"), "last5_cvd": f2("last5_cvd"),
            "avg_obi": f2("avg_obi"), "avg_vpin": f2("avg_vpin"),
            "stacked_bear": i2("stacked_bear"), "stacked_bull": i2("stacked_bull"),
            "absorption_n": i2("absorption_n"), "thin_above_n": i2("thin_above_n"),
            "expansion_n": i2("expansion_n"), "oi_mom_n": i2("oi_mom_n"),
            "ask_wall_n": i2("ask_wall_n"),
            "first_bear_bar": i2("first_bear_bar"), "first_neg_delta_bar": i2("first_neg_delta_bar"),
        })

def avg(lst):
    lst = [x for x in lst if x is not None]
    return sum(lst)/len(lst) if lst else 0

# ─── A) CALIBRACION POR SIMBOLO ──────────────────────────────────────────────
print("=" * 70)
print("A) CALIBRACION POR SIMBOLO")
print("=" * 70)

sym_data = defaultdict(list)
for r in rows:
    sym_data[r["sym"]].append(r)

for sym, data in sorted(sym_data.items()):
    n = len(data)
    wins = [r for r in data if r["result_r"] and r["result_r"] > 0]
    losses = [r for r in data if r["result_r"] and r["result_r"] <= 0]
    risks = [r["risk"] for r in data if r["risk"]]
    risk_mean = avg(risks)
    prices = [r["entry"] for r in data if r["entry"]]
    price_mean = avg(prices)
    risk_pct = (risk_mean / price_mean * 100) if price_mean else 0
    pnl = sum(r["result_r"] for r in data if r["result_r"])
    wr = len(wins)/n*100 if n else 0

    print(f"\n  {sym}  n={n}  WR={wr:.0f}%  PnL={pnl:+.1f}R")
    print(f"    Risk abs (avg): {risk_mean:.4f}  = {risk_pct:.4f}% del precio")
    print(f"    Cum delta  wins: {avg([r['cum_delta'] for r in wins]):+.0f}  |  losses: {avg([r['cum_delta'] for r in losses]):+.0f}")
    print(f"    OBI        wins: {avg([r['avg_obi'] for r in wins]):+.3f}  |  losses: {avg([r['avg_obi'] for r in losses]):+.3f}")
    print(f"    expansion  wins: {avg([r['expansion_n'] for r in wins]):.1f} bars  |  losses: {avg([r['expansion_n'] for r in losses]):.1f} bars")
    print(f"    oi_mom     wins: {avg([r['oi_mom_n'] for r in wins]):.1f} bars  |  losses: {avg([r['oi_mom_n'] for r in losses]):.1f} bars")

# ─── B) ENTRADA ANTICIPADA ────────────────────────────────────────────────────
print("\n\n" + "=" * 70)
print("B) SENALES DE ENTRADA ANTICIPADA - Shorts")
print("=" * 70)

shorts = [r for r in rows if r["dir"] == "Short"]
wins_s = [r for r in shorts if r["result_r"] and r["result_r"] > 0]
losses_s = [r for r in shorts if r["result_r"] and r["result_r"] <= 0]

print(f"\n  n={len(shorts)}  wins={len(wins_s)}  losses={len(losses_s)}")

metrics = [
    ("cum_delta",    "Delta acumulado 25b  "),
    ("last5_delta",  "Delta ultimas 5b     "),
    ("avg_cvd",      "CVD slope promedio   "),
    ("last5_cvd",    "CVD slope L5         "),
    ("avg_obi",      "OBI promedio         "),
    ("stacked_bear", "Barras stacked_bear  "),
    ("expansion_n",  "Barras en expansion  "),
    ("oi_mom_n",     "Barras OI momentum   "),
    ("ask_wall_n",   "Barras ask_wall      "),
]

print(f"\n  {'Metrica':<24} {'WINS':>12} {'LOSSES':>12} {'DIFF':>10}")
print(f"  {'-'*60}")
for key, label in metrics:
    w = avg([r[key] for r in wins_s])
    l = avg([r[key] for r in losses_s])
    diff = w - l
    marker = " KEY" if abs(diff) > max(abs(w), abs(l), 1) * 0.25 else ""
    print(f"  {label} {w:>12.2f} {l:>12.2f} {diff:>+10.2f}{marker}")

# Threshold para cum_delta
print(f"\n  DISTRIBUCION cum_delta en Shorts:")
print(f"  Wins:   {sorted([r['cum_delta'] for r in wins_s if r['cum_delta'] is not None])}")
print(f"  Losses: {sorted([r['cum_delta'] for r in losses_s if r['cum_delta'] is not None])}")

print(f"\n  CONDICION PROPUESTA para entrada anticipada:")
print(f"  stacked_bear >= 1 barra (aparece avg {avg([r['first_bear_bar'] for r in shorts if r['first_bear_bar']]): .0f}/25 barras antes)")
bear_w = avg([r['first_bear_bar'] for r in wins_s if r['first_bear_bar']])
bear_l = avg([r['first_bear_bar'] for r in losses_s if r['first_bear_bar']])
print(f"  Primer stacked_bear - wins: barra {bear_w:.0f}/25  |  losses: barra {bear_l:.0f}/25")
print(f"  (barra 25 = hace 25min, barra 1 = 1min antes del entry actual)")

# Expansion filter
print(f"\n  FILTRO EXPANSION:")
for threshold in [3, 4, 5, 6]:
    passed = [r for r in shorts if r["expansion_n"] is not None and r["expansion_n"] <= threshold]
    if passed:
        w2 = [r for r in passed if r["result_r"] and r["result_r"] > 0]
        pnl2 = sum(r["result_r"] for r in passed if r["result_r"])
        print(f"  expansion_n <= {threshold}: n={len(passed)}  WR={len(w2)/len(passed)*100:.0f}%  PnL={pnl2:+.1f}R")

# OI momentum filter
print(f"\n  FILTRO OI MOMENTUM:")
for threshold in [3, 4, 5, 6, 8]:
    passed = [r for r in shorts if r["oi_mom_n"] is not None and r["oi_mom_n"] <= threshold]
    if passed:
        w2 = [r for r in passed if r["result_r"] and r["result_r"] > 0]
        pnl2 = sum(r["result_r"] for r in passed if r["result_r"])
        print(f"  oi_mom_n <= {threshold}: n={len(passed)}  WR={len(w2)/len(passed)*100:.0f}%  PnL={pnl2:+.1f}R")

# ─── C) TRAILING CALIBRACION ─────────────────────────────────────────────────
print("\n\n" + "=" * 70)
print("C) TRAILING STOP CALIBRACION")
print("=" * 70)

for exit_reason in ["TARGET", "TRAILING_STOP", "STOP", "TIME_STOP"]:
    sub = [r for r in rows if r["status"] == exit_reason]
    if sub:
        r_vals = [r["result_r"] for r in sub if r["result_r"] is not None]
        print(f"  {exit_reason:<15} n={len(sub):2d}  avg={sum(r_vals)/len(r_vals):+.2f}R")

print(f"\n  TRAILING por direccion y simbolo:")
trailing = [r for r in rows if r["status"] == "TRAILING_STOP"]
target_r = [r for r in rows if r["status"] == "TARGET"]
print(f"  {'Sym+Dir':<20} {'TRAIL n':>8} {'TRAIL avg':>10} {'TARGET n':>8} {'TARGET avg':>10} {'Diferencia':>10}")
print(f"  {'-'*70}")
for sym in ["BTCUSDT","SOLUSDT","BNBUSDT","ETHUSDT"]:
    for direction in ["Short","Long"]:
        t = [r for r in trailing if r["sym"]==sym and r["dir"]==direction]
        tgt = [r for r in target_r if r["sym"]==sym and r["dir"]==direction]
        if t or tgt:
            t_avg = avg([r["result_r"] for r in t]) if t else 0
            tgt_avg = avg([r["result_r"] for r in tgt]) if tgt else 0
            print(f"  {sym+' '+direction:<20} {len(t):>8} {t_avg:>+10.2f} {len(tgt):>8} {tgt_avg:>+10.2f} {tgt_avg-t_avg:>+10.2f}")

print(f"\n  CADA trade TRAILING (para ver patron):")
for r in sorted(trailing, key=lambda x: x["result_r"] if x["result_r"] else 0, reverse=True):
    print(f"  {r['sym']:<10} {r['dir']:<6} {r['ses']:<22} score={r['score']:<4} -> {r['result_r']:+.2f}R")

# Optimal trailing - test different TRAIL_ACTIVATE_R values
# For trades that hit trailing: they got capped. What if target was just let run?
print(f"\n  SIMULACION: que pasaria si TARGET fijo (sin trailing) en Shorts:")
shorts_trail = [r for r in trailing if r["dir"] == "Short"]
if shorts_trail:
    lost_r = sum(2.0 - r["result_r"] for r in shorts_trail)
    print(f"  Shorts trailing: n={len(shorts_trail)}  R cedido vs TARGET 2R = {lost_r:+.2f}R")

print(f"\n  SIMULACION: que pasaria si TARGET fijo (sin trailing) en Longs:")
longs_trail = [r for r in trailing if r["dir"] == "Long"]
if longs_trail:
    good = [(r["sym"], round(r["result_r"],2)) for r in longs_trail if r["result_r"] and r["result_r"] > 0]
    print(f"  Longs trailing que terminaron bien (>1R): {good}")
    print(f"  Cuantos hubieran llegado a target?: desconocido (no tenemos post-data)")
