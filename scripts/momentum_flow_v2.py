"""
MomentumFlow v2 — corregido tras analisis v1.

Problemas encontrados en v1:
  1. delta_atr = bar_delta / atr tiene unidades inconsistentes entre simbolos
     (bar_delta en unidades base, ATR en USDT), lo que daba delta_atr=416795 en SOLUSDT.
     Fix: usar dz (z-score del delta ya normalizado internamente).
  2. Stop = bar_extreme + ATR era demasiado ancho (efectivamente 1.5-2x ATR).
     Fix: stop simple = close +/- ATR_STOP_K * atr, identico a RBF.
  3. WR 28.6% < 33% necesario para 2:1. Necesitamos mejores filtros.

Logica v2:
  LONG:
    - VR >= 3.0   (volumen 3x sobre media)
    - dz > 2.0    (delta z-score: presion de compra estadisticamente extrema)
    - Confirmacion: stk_bullish OR (obi > 0.2 AND vpin >= 0.6)

  SHORT:
    - VR >= 3.0
    - dz < -2.0
    - Confirmacion: stk_bearish OR (obi < -0.2 AND vpin >= 0.6)

  Stop:    ATR_STOP_K * atr desde el close (no extremo de barra)
  Target:  TARGET_RR * stop_dist
  Trailing: activa a 1.5R, trail = 0.5 * atr
  Time stop: bar 15 en perdida

Grid search por ATR_STOP_K [0.5, 0.7, 1.0, 1.3] y TARGET_RR [1.5, 2.0, 2.5].
"""

import json, datetime
from collections import defaultdict

COOLDOWN_BARS   = 30
TIME_STOP_BARS  = 15
TRAIL_ACTIVATE  = 1.5
TRAIL_ATR_K     = 0.5
MAX_BARS        = 120

VR_MIN     = 3.0
DZ_MIN     = 2.0    # |dz| minimo


def ts(ms):
    return datetime.datetime.utcfromtimestamp(ms / 1000).strftime("%m-%d %H:%M")

def session_label(s):
    return {"London": "LDN", "LondonNyOverlap": "OVR", "NewYork": "NY",
            "Asia": "ASI", "OffHours": "OFF"}.get(s, s[:3])


def detect_momentum_v2(bar):
    vr   = bar.get("vr") or 0.0
    dz   = bar.get("dz") or 0.0
    stk  = bar.get("stacked_imb") or "None"
    obi  = bar.get("obi_l5") or 0.0
    vpin = bar.get("vpin") or 0.0
    atr  = bar.get("atr") or 0.0

    if atr < 1e-9 or vr < VR_MIN:
        return None

    if dz > DZ_MIN:
        confirm = (stk == "Bullish") or (obi > 0.2 and vpin >= 0.6)
        if confirm:
            return "Long"

    if dz < -DZ_MIN:
        confirm = (stk == "Bearish") or (obi < -0.2 and vpin >= 0.6)
        if confirm:
            return "Short"

    return None


def backtest_symbol(sym, bars, atr_stop_k, target_rr):
    trades   = []
    n        = len(bars)
    cooldown = 0
    in_trade = None

    for i in range(20, n):
        bar = bars[i]
        atr = bar.get("atr") or 0.0
        h, l, c = bar["high"], bar["low"], bar["close"]

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
                pnl_raw  = (t["entry"] - exit_price) if t["dir"] == "Short" else (exit_price - t["entry"])
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

        if atr <= 0:
            continue
        direction = detect_momentum_v2(bar)
        if direction is None:
            continue

        stop_dist    = atr_stop_k * atr
        stop_price   = c - stop_dist if direction == "Long" else c + stop_dist
        target_price = c + stop_dist * target_rr if direction == "Long" else c - stop_dist * target_rr
        risk         = stop_dist

        in_trade = {
            "sym": sym, "dir": direction,
            "entry_ms": bar["ts_ms"], "entry": c,
            "stop": stop_price, "target": target_price, "risk": risk,
            "atr": round(atr, 4), "vr": round(bar.get("vr") or 0, 2),
            "dz": round(bar.get("dz") or 0, 2),
            "obi": round(bar.get("obi_l5") or 0, 2),
            "stk": bar.get("stacked_imb") or "None",
            "session": bar.get("session", "OffHours"),
            "bars_held": 0, "trailing": False, "best_ext": c,
            "exit_ms": None, "exit_p": None, "r": None,
            "reason": None, "exit_sess": None,
        }

    return trades


# ── CARGAR DATOS ──────────────────────────────────────────────────────────────
with open("/tmp/all_bars_full.json") as f:
    raw = json.load(f)

all_bars = {}
for sym, bars_raw in raw.items():
    bars = sorted(bars_raw if isinstance(bars_raw, list) else
                  [v for k, v in bars_raw.items() if k != "_sorted"],
                  key=lambda b: b["ts_ms"])
    all_bars[sym] = bars
    print(f"{sym}: {len(bars)} barras")


# ── GRID SEARCH ───────────────────────────────────────────────────────────────
ATR_STOP_VALUES = [0.5, 0.7, 1.0, 1.3]
TARGET_RR_VALUES = [1.5, 2.0, 2.5, 3.0]

print("\n" + "=" * 80)
print("GRID SEARCH: ATR_STOP_K vs TARGET_RR")
print(f"{'ATR_k':>6} {'RR':>4} {'n':>5} {'WR%':>6} {'AvgR':>8} {'TotalR':>9}")
print("-" * 50)

best_config = None
best_total  = -999

for atr_k in ATR_STOP_VALUES:
    for rr in TARGET_RR_VALUES:
        trades = []
        for sym, bars in all_bars.items():
            trades.extend(backtest_symbol(sym, bars, atr_k, rr))
        closed = [t for t in trades if t["r"] is not None]
        if not closed:
            continue
        wins   = sum(1 for t in closed if t["r"] > 0)
        wr     = wins / len(closed) * 100
        avg_r  = sum(t["r"] for t in closed) / len(closed)
        tot_r  = sum(t["r"] for t in closed)
        print(f"{atr_k:>6.1f} {rr:>4.1f} {len(closed):>5} {wr:>6.1f}% {avg_r:>+8.3f} {tot_r:>+9.2f}")
        if tot_r > best_total:
            best_total  = tot_r
            best_config = (atr_k, rr, closed)

print(f"\nMejor config: ATR_k={best_config[0]}, RR={best_config[1]}, TotalR={best_total:+.2f}")


# ── ANALISIS DETALLADO DE LA MEJOR CONFIG ─────────────────────────────────────
atr_k_opt, rr_opt, closed = best_config
print("\n" + "=" * 80)
print(f"ANALISIS DETALLADO — ATR_STOP={atr_k_opt}x, RR={rr_opt}:1")
print("=" * 80)

def stats(lst):
    if not lst: return (0, 0.0, 0.0, 0.0)
    wins  = sum(1 for t in lst if t["r"] > 0)
    total = sum(t["r"] for t in lst)
    return len(lst), wins / len(lst) * 100, total / len(lst), total

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

print("\n--- Por sesion ---")
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

print("\n--- Por DZ tier ---")
for lo, hi, label in [(2, 3, "2-3 sigma"), (3, 4, "3-4 sigma"), (4, 99, "4+ sigma")]:
    subset = [t for t in closed if lo <= abs(t["dz"]) < hi]
    if subset:
        nn, ww, aa, tt = stats(subset)
        print(f"  DZ {label}: n={nn:3d}  WR={ww:4.0f}%  AvgR={aa:+.3f}  TotalR={tt:+.2f}")

print("\n--- Por VR tier ---")
for lo, hi, label in [(3, 4, "3-4x"), (4, 5, "4-5x"), (5, 99, "5x+")]:
    subset = [t for t in closed if lo <= t["vr"] < hi]
    if subset:
        nn, ww, aa, tt = stats(subset)
        print(f"  VR {label}: n={nn:3d}  WR={ww:4.0f}%  AvgR={aa:+.3f}  TotalR={tt:+.2f}")

# ── COMPARACION ───────────────────────────────────────────────────────────────
days = (max(t["entry_ms"] for t in closed) - min(t["entry_ms"] for t in closed)) / 86400000
days = max(days, 1)
spd  = n / days

print(f"\n{'='*80}")
print("COMPARACION CON RBF")
print(f"{'='*80}")
print(f"  MomentumFlow v2 (opt): n={n}   WR={wr:.1f}%  AvgR={avg_r:+.3f}  TotalR={tot_r:+.2f}")
print(f"  RBF (backtest M1):     n=81   WR=42%    AvgR=+0.513  TotalR=+41.5")
print(f"  RBF (live 37 trades):  n=37   WR=35%    AvgR=-0.046  TotalR=-1.71")
print()
print(f"  Seniales/dia MomentumFlow: {spd:.1f}   vs RBF: ~3/dia")
print(f"  Dias de datos: {days:.1f}")
for cap, pct in [(500, 0.02), (1000, 0.02)]:
    r_usd    = cap * pct
    monthly  = spd * 22 * avg_r
    print(f"  ${cap} / {int(pct*100)}% riesgo (${r_usd:.0f}/trade): "
          f"{monthly:+.1f}R = ${monthly*r_usd:+.0f}/mes")

# ── SHORT vs LONG: breakdown DIR x SESION ─────────────────────────────────────
print(f"\n{'='*80}")
print("BREAKDOWN DIRECCION x SESION")
print(f"{'='*80}")
from itertools import product
for d in ["Long", "Short"]:
    print(f"\n  {d}:")
    for sess in ["LondonNyOverlap", "NewYork", "London", "Asia", "OffHours"]:
        sub = [t for t in closed if t["dir"] == d and t["session"] == sess]
        if len(sub) >= 3:
            nn, ww, aa, tt = stats(sub)
            print(f"    {sess:22s}: n={nn:3d}  WR={ww:4.0f}%  AvgR={aa:+.3f}")

with open("/tmp/momentum_v2_results.json", "w") as f:
    import json
    json.dump(closed, f, indent=2, default=str)
print(f"\nGuardado: /tmp/momentum_v2_results.json")
