"""
RBF Optimal Trade Replay
Para cada trade cerrado con barras M1 disponibles:
1. Replay barra a barra con entry/stop/target actual
2. Simula variantes de exit (TP múltiples, trailing, tiempo, parciales)
3. Encuentra la configuracion optima
4. Proyecta PnL con esa config
"""

import json, datetime, sys
from collections import defaultdict

# ── datos ────────────────────────────────────────────────────────────────────
with open("/tmp/rbf_signals.json") as f:
    rbf_all = json.load(f)
with open("/tmp/all_bars.json") as f:
    all_bars_raw = json.load(f)

# indexar barras por ts_ms para acceso O(1)
all_bars = {}
for sym, bars in all_bars_raw.items():
    all_bars[sym] = {b["ts_ms"]: b for b in bars}
    all_bars[sym]["_sorted"] = sorted(b["ts_ms"] for b in bars)

# solo trades cerrados con barras disponibles
BAR_STARTS = {
    "BTCUSDT": 1780676700000,
    "ETHUSDT": 1780762260000,
    "BNBUSDT": 1780762260000,
    "SOLUSDT": 1780762260000,
    "XRPUSDT": 1781051520000,
}
rbf_closed = [t for t in rbf_all if t.get("result_r") is not None]
trades_with_bars = [
    t for t in rbf_closed
    if t["timestamp_ms"] >= BAR_STARTS.get(t["symbol"], 9e18)
]

def ts(ms):
    return datetime.datetime.utcfromtimestamp(ms / 1000).strftime("%m-%d %H:%M")

# ── replay engine ─────────────────────────────────────────────────────────────
def get_bars_after(sym, from_ms, n=120):
    """Devuelve las n barras M1 desde from_ms (inclusivo)."""
    sorted_ts = all_bars[sym]["_sorted"]
    idx = None
    for i, t in enumerate(sorted_ts):
        if t >= from_ms:
            idx = i
            break
    if idx is None:
        return []
    keys = sorted_ts[idx: idx + n]
    return [all_bars[sym][k] for k in keys if k in all_bars[sym]]


def replay(bars, entry, stop, direction, tp_mult, trail_after=None,
           partial_at=None, max_bars=90):
    """
    Simula un trade barra a barra.
    tp_mult: multiplicador sobre el riesgo (1R = |entry-stop|)
    trail_after: activa trailing stop despues de alcanzar este R (None = sin trailing)
    partial_at: cierra el 50% en este R, trailing el resto (None = sin parciales)
    max_bars: tiempo maximo en barras

    Devuelve R realizado (promedio ponderado si hay parcial).
    """
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0

    target = entry + tp_mult * risk if direction == "Long" else entry - tp_mult * risk
    current_stop = stop
    bars_held = 0
    partial_done = False
    partial_r = 0.0
    trail_activated = False

    for bar in bars[:max_bars]:
        lo = bar["low"]
        hi = bar["high"]
        bars_held += 1

        if direction == "Long":
            # stop hit?
            if lo <= current_stop:
                exit_r = (current_stop - entry) / risk
                if partial_done:
                    return 0.5 * partial_r + 0.5 * exit_r
                return exit_r

            # partial exit?
            if partial_at and not partial_done and hi >= entry + partial_at * risk:
                partial_r = partial_at
                partial_done = True
                # mover stop a breakeven
                current_stop = max(current_stop, entry)

            # trailing?
            if trail_after and (trail_activated or hi >= entry + trail_after * risk):
                trail_activated = True
                atr = bar.get("atr") or risk
                new_stop = hi - atr * 0.5
                current_stop = max(current_stop, new_stop)

            # target hit?
            if hi >= target:
                if partial_done:
                    return 0.5 * partial_r + 0.5 * tp_mult
                return tp_mult

        else:  # Short
            if hi >= current_stop:
                exit_r = (entry - current_stop) / risk
                if partial_done:
                    return 0.5 * partial_r + 0.5 * exit_r
                return exit_r

            if partial_at and not partial_done and lo <= entry - partial_at * risk:
                partial_r = partial_at
                partial_done = True
                current_stop = min(current_stop, entry)

            if trail_after and (trail_activated or lo <= entry - trail_after * risk):
                trail_activated = True
                atr = bar.get("atr") or risk
                new_stop = lo + atr * 0.5
                current_stop = min(current_stop, new_stop)

            if lo <= target:
                if partial_done:
                    return 0.5 * partial_r + 0.5 * tp_mult
                return tp_mult

    # timeout — salir al close de la ultima barra
    if bars:
        last_close = bars[min(bars_held, len(bars)) - 1]["close"]
        exit_r = (last_close - entry) / risk if direction == "Long" else (entry - last_close) / risk
        if partial_done:
            return 0.5 * partial_r + 0.5 * exit_r
        return exit_r
    return 0.0


# ── variantes a simular ───────────────────────────────────────────────────────
VARIANTS = {
    # nombre: (tp_mult, trail_after, partial_at, max_bars)
    "Actual_2R":        (2.0, None, None, 90),
    "TP_1.5R":          (1.5, None, None, 90),
    "TP_2.5R":          (2.5, None, None, 90),
    "TP_3R":            (3.0, None, None, 90),
    "Trail_1R":         (4.0, 1.0, None, 90),   # trailing desde 1R, TP maximo 4R
    "Trail_1.5R":       (4.0, 1.5, None, 90),
    "Partial50_1R":     (2.5, None, 1.0, 90),   # 50% en 1R, resto a 2.5R
    "Partial50_Trail":  (4.0, 1.5, 1.0, 90),   # 50% en 1R, trail el resto
    "Time30":           (2.0, None, None, 30),   # max 30 min
    "Time60":           (2.0, None, None, 60),
}

# ── ejecutar replay ───────────────────────────────────────────────────────────
results = []  # lista de dicts {trade_info, variant_name, r_result}
variant_totals = defaultdict(list)

print(f"{'='*80}")
print(f"RBF OPTIMAL REPLAY — {len(trades_with_bars)} trades con barras M1")
print(f"{'='*80}\n")

for t in trades_with_bars:
    sym   = t["symbol"]
    ts_ms = t["timestamp_ms"]
    entry = t["entry_price"]
    stop  = t["stop_price"]
    tgt   = t["target_price"]
    dirn  = t["direction"]
    actual_r = t["result_r"]

    bars = get_bars_after(sym, ts_ms, n=120)
    if not bars:
        continue

    row = {
        "ts":     ts(ts_ms),
        "symbol": sym,
        "dir":    dirn,
        "session": t.get("session", "?"),
        "actual_r": actual_r,
    }

    best_r = actual_r
    best_v = "Actual_2R"

    for v_name, (tp_m, trail, partial, max_b) in VARIANTS.items():
        r = replay(bars, entry, stop, dirn, tp_m, trail_after=trail,
                   partial_at=partial, max_bars=max_b)
        r = round(r, 4)
        row[v_name] = r
        variant_totals[v_name].append(r)
        if r > best_r:
            best_r = r
            best_v = v_name

    row["best_variant"] = best_v
    row["best_r"] = best_r
    row["gain_vs_actual"] = round(best_r - actual_r, 4)
    results.append(row)

# ── tabla por trade ───────────────────────────────────────────────────────────
print(f"{'Trade':<18} {'Dir':<6} {'Sesion':<20} {'Actual':>7} {'Best':>7} {'Variante':<18} {'Ganancia':>8}")
print("-" * 90)
for r in results:
    print(f"{r['ts']:<18} {r['dir']:<6} {r['session']:<20} {r['actual_r']:>+7.2f} "
          f"{r['best_r']:>+7.2f} {r['best_variant']:<18} {r['gain_vs_actual']:>+8.2f}")

# ── resumen por variante ──────────────────────────────────────────────────────
print(f"\n{'='*80}")
print(f"RESUMEN POR VARIANTE (n={len(results)} trades)")
print(f"{'='*80}")
print(f"{'Variante':<20} {'n_win':>6} {'WR%':>6} {'AvgR':>8} {'TotalR':>8} {'Score':>8}")
print("-" * 60)

n = len(results)
variant_stats = {}
for v_name, rs in variant_totals.items():
    if len(rs) != n:
        continue
    wins = sum(1 for r in rs if r > 0)
    wr   = wins / n
    avg  = sum(rs) / n
    tot  = sum(rs)
    score = wr * avg * (n ** 0.5)  # penaliza variantes raras
    variant_stats[v_name] = (wins, wr, avg, tot, score)

for v_name, (wins, wr, avg, tot, score) in sorted(variant_stats.items(), key=lambda x: -x[1][4]):
    print(f"{v_name:<20} {wins:>6} {wr*100:>6.1f} {avg:>+8.3f} {tot:>+8.2f} {score:>+8.3f}")

# ── best variant detallado ────────────────────────────────────────────────────
best_v = max(variant_stats, key=lambda v: variant_stats[v][4])
wins, wr, avg, tot, score = variant_stats[best_v]

print(f"\n{'='*80}")
print(f"VARIANTE OPTIMA: {best_v}")
print(f"{'='*80}")
print(f"  WR:     {wr*100:.1f}%")
print(f"  AvgR:   {avg:+.3f}")
print(f"  TotalR: {tot:+.2f}  (vs actual {sum(r['actual_r'] for r in results):+.2f})")

actual_tot = sum(r["actual_r"] for r in results)
print(f"  Mejora: {tot - actual_tot:+.2f}R sobre los mismos {n} trades")

# ── proyeccion mensual ────────────────────────────────────────────────────────
# 37 trades en ~8 dias = ~4.6/dia
# con filtro VR>=3x esperamos ~40% menos señales
TOTAL_DAYS = 8
signals_per_day_actual = len(rbf_closed) / TOTAL_DAYS
signals_per_day_filtered = signals_per_day_actual * 0.60  # VR>=3 filtra ~40%

print(f"\n{'='*80}")
print(f"PROYECCION MENSUAL (22 dias operativos)")
print(f"{'='*80}")
print(f"  Señales/dia actual:   {signals_per_day_actual:.1f}")
print(f"  Señales/dia con VR>=3: {signals_per_day_filtered:.1f}  (-40% por filtro)")

for cap_usd, risk_pct in [(500, 0.02), (1000, 0.02), (2000, 0.01)]:
    risk_usd = cap_usd * risk_pct
    monthly_r_actual = signals_per_day_actual * 22 * (sum(r['actual_r'] for r in results)/n)
    monthly_r_opt    = signals_per_day_filtered * 22 * avg
    pnl_actual = monthly_r_actual * risk_usd
    pnl_opt    = monthly_r_opt * risk_usd
    print(f"\n  Capital ${cap_usd:,} riesgo {risk_pct*100:.0f}% (${risk_usd:.0f}/trade):")
    print(f"    Actual ({variant_stats['Actual_2R'][2]:+.3f}R/trade): {monthly_r_actual:+.1f}R = ${pnl_actual:+.0f}/mes")
    print(f"    Optimo ({avg:+.3f}R/trade):                          {monthly_r_opt:+.1f}R = ${pnl_opt:+.0f}/mes")

# guardar JSON para el reviewer
with open("/tmp/rbf_optimal_results.json", "w") as f:
    json.dump({"trades": results, "variant_stats": {k: list(v) for k,v in variant_stats.items()}, "best_variant": best_v}, f, indent=2)
print(f"\nResultados guardados en tmp/rbf_optimal_results.json")
