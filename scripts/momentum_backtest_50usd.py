"""
Backtest completo MomentumFlow v2 — cuenta de $50 con leverage x15.

Modelo de riesgo:
  - Capital inicial: $50
  - Leverage maximo: 15x  →  posicion maxima = capital * 15
  - Riesgo por trade: 2% del capital actual (compounding)
  - Position size = risk_usd / stop_pct  (limitado por leverage 15x)
  - P&L = result_r * risk_usd
  - Si capital cae a $0 o menos → cuenta quemada, se detiene
"""

import json
from collections import defaultdict
import datetime

CAPITAL_INIT  = 50.0
LEVERAGE      = 15.0
RISK_PCT      = 0.02       # 2% por trade
MIN_CAPITAL   = 1.0        # debajo de esto, cuenta muerta

def ts(ms):
    return datetime.datetime.utcfromtimestamp(ms / 1000).strftime("%d/%m %H:%M")

def sess_label(s):
    return {"London":"LDN","LondonNyOverlap":"OVR","NewYork":"NY","Asia":"ASI","OffHours":"OFF"}.get(s,s[:3])

# ── cargar trades ─────────────────────────────────────────────────────────────
with open("/tmp/momentum_v2_results.json") as f:
    trades = json.load(f)
trades.sort(key=lambda t: t["entry_ms"])

# ── simulacion ────────────────────────────────────────────────────────────────
capital   = CAPITAL_INIT
rows      = []
peak      = capital
max_dd    = 0.0
wins = losses = 0
total_r   = 0.0

for t in trades:
    if capital < MIN_CAPITAL:
        break

    risk_usd   = capital * RISK_PCT
    entry      = t["entry"]
    stop_dist  = abs(entry - t["stop"])
    stop_pct   = stop_dist / entry if entry > 0 else 0.001

    # posicion limitada por leverage
    position   = risk_usd / stop_pct if stop_pct > 0 else 0
    max_pos    = capital * LEVERAGE
    position   = min(position, max_pos)
    lev_used   = position / capital if capital > 0 else 0

    # recalcular risk_usd real si la posicion fue capada
    actual_risk = position * stop_pct

    result_r   = t["r"]
    pnl        = result_r * actual_risk

    capital    += pnl
    total_r    += result_r

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
        "n":        len(rows) + 1,
        "ts":       ts(t["entry_ms"]),
        "sym":      t["sym"],
        "dir":      t["dir"][0],
        "sess":     sess_label(t["session"]),
        "vr":       t["vr"],
        "dz":       t["dz"],
        "atr":      t["atr"],
        "entry":    entry,
        "stop":     t["stop"],
        "target":   t["target"],
        "reason":   t.get("reason","?")[:5],
        "r":        result_r,
        "risk_usd": round(actual_risk, 3),
        "pos_usd":  round(position, 2),
        "lev":      round(lev_used, 1),
        "pnl":      round(pnl, 3),
        "capital":  round(capital, 3),
    })

n_total = len(rows)
wr      = wins / n_total * 100 if n_total > 0 else 0
avg_r   = total_r / n_total if n_total > 0 else 0
profit  = capital - CAPITAL_INIT
roi_pct = profit / CAPITAL_INIT * 100

# ── imprimir tabla ────────────────────────────────────────────────────────────
print()
print("=" * 115)
print(f"  BACKTEST MOMENTUMFLOW v2 — Capital: ${CAPITAL_INIT}  Leverage: {int(LEVERAGE)}x  Riesgo: {int(RISK_PCT*100)}%/trade")
print("=" * 115)
print(f"  {'#':>3} {'Fecha':>10} {'Sym':>8} {'D':>1} {'Ses':>3} {'VR':>5} {'DZ':>5} "
      f"{'Entry':>10} {'Stop':>10} {'Reason':>5} {'R':>7} {'Risk$':>6} {'Pos$':>7} "
      f"{'Lev':>4} {'P&L':>8} {'Capital':>9}")
print("-" * 115)

for r in rows:
    win_mark = "+" if r["r"] > 0 else "-"
    print(f"  {r['n']:>3} {r['ts']:>10} {r['sym']:>8} {r['dir']:>1} {r['sess']:>3} "
          f"{r['vr']:>5.1f} {r['dz']:>+5.1f} "
          f"{r['entry']:>10.4f} {r['stop']:>10.4f} "
          f"{r['reason']:>5} {r['r']:>+7.3f} "
          f"${r['risk_usd']:>5.2f} ${r['pos_usd']:>6.1f} "
          f"{r['lev']:>4.1f}x "
          f"${r['pnl']:>+7.3f} ${r['capital']:>8.3f}")

print("=" * 115)
print()
print(f"  RESUMEN FINAL")
print(f"  {'─'*60}")
print(f"  Trades totales:     {n_total}")
print(f"  Ganadores:          {wins}  ({wr:.1f}%)")
print(f"  Perdedores:         {losses}")
print(f"  AvgR por trade:     {avg_r:+.3f}R")
print(f"  Total R:            {total_r:+.2f}R")
print(f"  {'─'*60}")
print(f"  Capital inicial:    ${CAPITAL_INIT:.2f}")
print(f"  Capital final:      ${capital:.2f}")
print(f"  Ganancia neta:      ${profit:+.2f}  ({roi_pct:+.1f}% ROI)")
print(f"  Max Drawdown:       {max_dd:.1f}%  (${peak - capital:.2f} desde el pico)")
print(f"  {'─'*60}")

# ── breakdown por sesion ──────────────────────────────────────────────────────
print()
print(f"  Por sesion (capital en riesgo):")
by_sess = defaultdict(list)
for r in rows: by_sess[r["sess"]].append(r)
for s in ["LDN","ASI","OVR","NY","OFF"]:
    lst = by_sess.get(s,[])
    if not lst: continue
    ww = sum(1 for x in lst if x["r"]>0)/len(lst)*100
    aa = sum(x["r"] for x in lst)/len(lst)
    pp = sum(x["pnl"] for x in lst)
    print(f"    {s}: n={len(lst):3d}  WR={ww:4.0f}%  AvgR={aa:>+6.3f}  P&L=${pp:>+7.3f}")

print()
print(f"  Por direccion:")
by_dir = defaultdict(list)
for r in rows: by_dir[r["dir"]].append(r)
for d in ["S","L"]:
    lst = by_dir.get(d,[])
    if not lst: continue
    ww = sum(1 for x in lst if x["r"]>0)/len(lst)*100
    aa = sum(x["r"] for x in lst)/len(lst)
    pp = sum(x["pnl"] for x in lst)
    print(f"    {'Short' if d=='S' else 'Long '}:  n={len(lst):3d}  WR={ww:4.0f}%  AvgR={aa:>+6.3f}  P&L=${pp:>+7.3f}")

# ── curva de capital simplificada ─────────────────────────────────────────────
print()
print(f"  CURVA DE CAPITAL (cada 20 trades):")
print(f"  {'Trade':>6}  {'Capital':>9}  {'vs Inicio':>10}  Barra visual")
for i in range(0, len(rows), 20):
    cap  = rows[i]["capital"]
    diff = cap - CAPITAL_INIT
    bar_len = int((cap / CAPITAL_INIT) * 20)
    bar  = "#" * min(bar_len, 50)
    print(f"  {rows[i]['n']:>6}  ${cap:>8.2f}  {diff:>+9.2f}   {bar}")
# ultimo
if rows:
    cap  = rows[-1]["capital"]
    diff = cap - CAPITAL_INIT
    bar_len = int((cap / CAPITAL_INIT) * 20)
    bar  = "#" * min(bar_len, 50)
    print(f"  {rows[-1]['n']:>6}  ${cap:>8.2f}  {diff:>+9.2f}   {bar}  <- FINAL")

print()
print(f"  NOTA: {n_total} trades en 4.4 dias = {n_total/4.4:.0f} trades/dia")
print(f"  Proyeccion mensual (22 dias, compounding diario no aplicado):")
daily_profit = profit / 4.4
print(f"    Ganancia/dia estimada: ${daily_profit:+.2f}")
print(f"    Proyeccion mes lineal: ${daily_profit*22:+.2f}  ({daily_profit*22/CAPITAL_INIT*100:+.0f}% ROI mensual)")
