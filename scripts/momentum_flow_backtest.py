"""
MomentumFlow Detector — simulacion barra por barra sobre datos M1.

Concepto: detectar barras con presion direccional intensa (no consolidacion)
y entrar en la DIRECCION de esa presion con stop = 1x ATR, target = 2x ATR.

Reglas de entrada LONG:
  - VR >= 3.0  (volumen 3x sobre media)
  - delta_atr > 1.0  (presion compradora neta > 1x ATR)
  - Confirmacion: stk_bullish OR (dz > 0.5 AND obi > 0.0)

Reglas de entrada SHORT:
  - VR >= 3.0
  - delta_atr < -1.0  (presion vendedora neta > 1x ATR)
  - Confirmacion: stk_bearish OR (dz < -0.5 AND obi < 0.0)

Stop:    1.0 x ATR (debajo del minimo de la barra para Long, encima del maximo para Short)
Target:  2.0 x ATR (RR 2:1)
Trailing: activa a 1.5R, trail = 0.5 x ATR
Time stop: bar 15 si en perdida
Cooldown: 30 barras entre seniales
"""

import json, datetime
from collections import defaultdict

# ── CONFIG ────────────────────────────────────────────────────────────────────
VR_MIN          = 3.0
DELTA_ATR_MIN   = 1.0    # |delta/atr| minimo
ATR_STOP_K      = 1.0    # stop = ATR_STOP_K * atr
TARGET_RR       = 2.0    # RR fijo 2:1
TRAIL_ACTIVATE  = 1.5    # R para activar trailing
TRAIL_ATR_K     = 0.5    # trailing distance
TIME_STOP_BARS  = 15     # cierra en perdida si lleva >= N barras
MAX_BARS        = 120    # timeout maximo
COOLDOWN_BARS   = 30     # entre seniales del mismo simbolo

def ts(ms):
    return datetime.datetime.utcfromtimestamp(ms / 1000).strftime("%m-%d %H:%M")

def session_label(s):
    return {"London": "LDN", "LondonNyOverlap": "OVR", "NewYork": "NY",
            "Asia": "ASI", "OffHours": "OFF"}.get(s, s[:3])

# ── DETECTOR ─────────────────────────────────────────────────────────────────
def detect_momentum(bar):
    """
    Devuelve 'Long', 'Short' o None segun la barra.
    """
    vr      = bar.get("vr") or 0.0
    delta   = bar.get("bar_delta") or 0.0
    atr     = bar.get("atr") or 0.0
    stk     = bar.get("stacked_imb") or "None"
    dz      = bar.get("dz") or 0.0
    obi     = bar.get("obi_l5") or 0.0

    if atr < 1e-9 or vr < VR_MIN:
        return None

    delta_atr = delta / atr

    # LONG: presion compradora intensa
    if delta_atr > DELTA_ATR_MIN:
        confirm = (stk == "Bullish") or (dz > 0.5 and obi > 0.0)
        if confirm:
            return "Long"

    # SHORT: presion vendedora intensa
    if delta_atr < -DELTA_ATR_MIN:
        confirm = (stk == "Bearish") or (dz < -0.5 and obi < 0.0)
        if confirm:
            return "Short"

    return None


def backtest_symbol(sym, bars):
    trades   = []
    n        = len(bars)
    cooldown = 0
    in_trade = None

    for i in range(20, n):
        bar = bars[i]
        atr = bar.get("atr") or 0.0
        h, l, c = bar["high"], bar["low"], bar["close"]

        # ── gestionar trade activo ────────────────────────────────────────────
        if in_trade is not None:
            t = in_trade
            t["bars_held"] += 1

            if t["dir"] == "Short":
                if l < t["best_ext"]: t["best_ext"] = l
                fav_r = (t["entry"] - t["best_ext"]) / t["risk"]
                if fav_r >= TRAIL_ACTIVATE and not t["trailing"]:
                    t["trailing"] = True
                if t["trailing"] and atr > 0:
                    new_stop = t["best_ext"] + TRAIL_ATR_K * atr
                    if new_stop < t["stop"]:
                        t["stop"] = new_stop
                stop_hit   = h >= t["stop"]
                target_hit = l <= t["target"]
            else:
                if h > t["best_ext"]: t["best_ext"] = h
                fav_r = (t["best_ext"] - t["entry"]) / t["risk"]
                if fav_r >= TRAIL_ACTIVATE and not t["trailing"]:
                    t["trailing"] = True
                if t["trailing"] and atr > 0:
                    new_stop = t["best_ext"] - TRAIL_ATR_K * atr
                    if new_stop > t["stop"]:
                        t["stop"] = new_stop
                stop_hit   = l <= t["stop"]
                target_hit = h >= t["target"]

            reason = exit_price = None
            if stop_hit:
                reason = "TRAIL" if t["trailing"] else "STOP"
                exit_price = t["stop"]
            elif target_hit:
                reason = "TARGET"
                exit_price = t["target"]
            elif t["bars_held"] >= TIME_STOP_BARS:
                pnl = (t["entry"] - c) if t["dir"] == "Short" else (c - t["entry"])
                if pnl < 0:
                    reason = "TIME"
                    exit_price = c
            elif t["bars_held"] >= MAX_BARS:
                reason = "TIMEOUT"
                exit_price = c

            if reason:
                pnl_raw = (t["entry"] - exit_price) if t["dir"] == "Short" else (exit_price - t["entry"])
                result_r = pnl_raw / t["risk"]
                t.update({"exit_ms": bar["ts_ms"], "exit_p": exit_price,
                          "r": round(result_r, 4), "reason": reason,
                          "exit_sess": bar.get("session", "?")})
                trades.append(t)
                in_trade = None
                cooldown = COOLDOWN_BARS
            continue

        if cooldown > 0:
            cooldown -= 1
            continue

        # ── detector ─────────────────────────────────────────────────────────
        if atr <= 0:
            continue
        direction = detect_momentum(bar)
        if direction is None:
            continue

        # Stop por el extremo de la barra + ATR_STOP_K
        if direction == "Short":
            stop_dist  = h - c + ATR_STOP_K * atr   # sobre el high de la barra
            stop_price = c + stop_dist
        else:
            stop_dist  = c - l + ATR_STOP_K * atr   # bajo el low de la barra
            stop_price = c - stop_dist

        risk = abs(c - stop_price)
        if risk < 1e-9:
            continue

        target_price = c - risk * TARGET_RR if direction == "Short" else c + risk * TARGET_RR

        in_trade = {
            "sym": sym, "dir": direction,
            "entry_ms": bar["ts_ms"], "entry": c,
            "stop": stop_price, "target": target_price, "risk": risk,
            "atr": round(atr, 4), "vr": bar.get("vr", 0),
            "delta_atr": round((bar.get("bar_delta") or 0) / atr, 2),
            "dz": round(bar.get("dz") or 0, 2),
            "obi": round(bar.get("obi_l5") or 0, 2),
            "stk": bar.get("stacked_imb") or "None",
            "session": bar.get("session", "OffHours"),
            "bars_held": 0, "trailing": False, "best_ext": c,
            "exit_ms": None, "exit_p": None, "r": None, "reason": None, "exit_sess": None,
        }

    return trades


# ── CARGAR DATOS ──────────────────────────────────────────────────────────────
with open("/tmp/all_bars_full.json") as f:
    raw = json.load(f)

all_trades = []
for sym, bars_raw in raw.items():
    bars = sorted(bars_raw if isinstance(bars_raw, list) else
                  [v for k, v in bars_raw.items() if k != "_sorted"],
                  key=lambda b: b["ts_ms"])
    t = backtest_symbol(sym, bars)
    all_trades.extend(t)
    print(f"{sym}: {len(bars)} barras -> {len(t)} trades")

closed = [t for t in all_trades if t["r"] is not None]
closed.sort(key=lambda t: t["entry_ms"])

print(f"\nTotal seniales: {len(all_trades)} | Cerrados: {len(closed)}")

if not closed:
    print("Sin trades cerrados")
    raise SystemExit


# ── ESTADISTICAS ──────────────────────────────────────────────────────────────
def stats(lst):
    if not lst: return (0, 0.0, 0.0, 0.0)
    wins  = sum(1 for t in lst if t["r"] > 0)
    total = sum(t["r"] for t in lst)
    return len(lst), wins / len(lst) * 100, total / len(lst), total

print("\n" + "=" * 80)
print("MOMENTUMFLOW BACKTEST — VR>=3 + delta_atr>1 + confirmacion")
print("=" * 80)

n, wr, avg_r, tot_r = stats(closed)
print(f"\nGLOBAL: n={n}  WR={wr:.1f}%  AvgR={avg_r:+.3f}  TotalR={tot_r:+.2f}")

print("\n--- Por simbolo ---")
by_sym = defaultdict(list)
for t in closed: by_sym[t["sym"]].append(t)
for sym, lst in sorted(by_sym.items(), key=lambda x: -sum(t["r"] for t in x[1])):
    nn, ww, aa, tt = stats(lst)
    print(f"  {sym:12s}: n={nn:3d}  WR={ww:4.0f}%  AvgR={aa:+.3f}  TotalR={tt:+.2f}")

print("\n--- Por direccion ---")
by_dir = defaultdict(list)
for t in closed: by_dir[t["dir"]].append(t)
for d, lst in sorted(by_dir.items()):
    nn, ww, aa, tt = stats(lst)
    print(f"  {d:8s}: n={nn:3d}  WR={ww:4.0f}%  AvgR={aa:+.3f}  TotalR={tt:+.2f}")

print("\n--- Por sesion de entrada ---")
by_sess = defaultdict(list)
for t in closed: by_sess[t["session"]].append(t)
for sess, lst in sorted(by_sess.items(), key=lambda x: -sum(t["r"] for t in x[1])):
    nn, ww, aa, tt = stats(lst)
    print(f"  {sess:22s}: n={nn:3d}  WR={ww:4.0f}%  AvgR={aa:+.3f}  TotalR={tt:+.2f}")

print("\n--- Por razon de salida ---")
by_reason = defaultdict(list)
for t in closed: by_reason[t["reason"]].append(t)
for r, lst in sorted(by_reason.items(), key=lambda x: -len(x[1])):
    nn, ww, aa, tt = stats(lst)
    print(f"  {r:12s}: n={nn:3d}  WR={ww:4.0f}%  AvgR={aa:+.3f}  TotalR={tt:+.2f}")

print("\n--- Por VR tier ---")
for lo, hi, label in [(3, 4, "3-4x"), (4, 5, "4-5x"), (5, 99, "5x+")]:
    subset = [t for t in closed if lo <= t["vr"] < hi]
    if subset:
        nn, ww, aa, tt = stats(subset)
        print(f"  VR {label}: n={nn:3d}  WR={ww:4.0f}%  AvgR={aa:+.3f}  TotalR={tt:+.2f}")

# ── COMPARACION CON RBF ───────────────────────────────────────────────────────
print("\n" + "=" * 80)
print("COMPARACION MomentumFlow vs RBF (backtest M1)")
print("=" * 80)
print(f"  MomentumFlow: n={n}  WR={wr:.1f}%  AvgR={avg_r:+.3f}  TotalR={tot_r:+.2f}")
print(f"  RBF (ref):    n=81   WR=42%    AvgR=+0.513   TotalR=+41.5")

days = (max(t["entry_ms"] for t in closed) - min(t["entry_ms"] for t in closed)) / 86400000
days = max(days, 1)
spd  = n / days
print(f"\n  Dias de datos: {days:.1f} | Seniales/dia: {spd:.1f}")
for cap, pct in [(500, 0.02), (1000, 0.02)]:
    r_per_usd = cap * pct
    monthly_r = spd * 22 * avg_r
    print(f"  ${cap} / {int(pct*100)}% riesgo: {monthly_r:+.1f}R = ${monthly_r*r_per_usd:+.0f}/mes")

# ── TABLA COMPLETA ────────────────────────────────────────────────────────────
print(f"\n{'='*100}")
print("TODOS LOS TRADES")
print(f"{'='*100}")
print(f"{'Entrada':>15} {'Sym':>8} {'D':>2} {'Ses':>3} {'VR':>5} {'dATR':>6} "
      f"{'DZ':>5} {'OBI':>5} {'Stk':>8} {'ATR':>7} {'Reason':>8} {'R':>8} {'Bars':>5}")
print("-" * 100)

for t in closed:
    print(f"{ts(t['entry_ms']):>15} {t['sym']:>8} {t['dir'][0]:>2} "
          f"{session_label(t['session']):>3} {t['vr']:>5.1f} {t['delta_atr']:>+6.2f} "
          f"{t['dz']:>+5.2f} {t['obi']:>+5.2f} {t['stk']:>8} {t['atr']:>7.2f} "
          f"{t['reason']:>8} {t['r']:>+8.4f} {t['bars_held']:>5}")

with open("/tmp/momentum_flow_results.json", "w") as f:
    json.dump(closed, f, indent=2, default=str)
print(f"\nGuardado: /tmp/momentum_flow_results.json")
