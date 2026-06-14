"""
RBF Entry Science — Analisis profundo de calidad de entrada
Para cada trade con barras M1:
  - MFE / MAE en R
  - Capture efficiency
  - Entry slippage vs breakout bar
  - Optimal entry (pullback al borde del rango)
  - Stop sizing vs ATR
  - Correlaciones: que predice MFE?
"""

import json, datetime, math
from collections import defaultdict

# ── datos ─────────────────────────────────────────────────────────────────────
with open("/tmp/rbf_signals.json") as f:
    rbf_all = json.load(f)
with open("/tmp/all_bars.json") as f:
    all_bars_raw = json.load(f)

all_bars = {}
for sym, bars in all_bars_raw.items():
    sorted_bars = sorted(bars, key=lambda b: b["ts_ms"])
    all_bars[sym] = {"_sorted": [b["ts_ms"] for b in sorted_bars]}
    for b in sorted_bars:
        all_bars[sym][b["ts_ms"]] = b

BAR_STARTS = {
    "BTCUSDT": 1780676700000,
    "ETHUSDT": 1780762260000,
    "BNBUSDT": 1780762260000,
    "SOLUSDT": 1780762260000,
    "XRPUSDT": 1781051520000,
}
rbf_closed = [t for t in rbf_all if t.get("result_r") is not None]
trades = [
    t for t in rbf_closed
    if t["timestamp_ms"] >= BAR_STARTS.get(t["symbol"], 9e18)
]


def get_bars_after(sym, from_ms, n=120):
    sorted_ts = all_bars[sym]["_sorted"]
    idx = next((i for i, t in enumerate(sorted_ts) if t >= from_ms), None)
    if idx is None:
        return []
    return [all_bars[sym][k] for k in sorted_ts[idx: idx + n]]


def ts(ms):
    return datetime.datetime.utcfromtimestamp(ms / 1000).strftime("%m-%d %H:%M")


# ── analisis por trade ────────────────────────────────────────────────────────
analysis = []

for t in trades:
    sym    = t["symbol"]
    entry  = t["entry_price"]
    stop   = t["stop_price"]
    dirn   = t["direction"]
    r_high = t["range_high"]
    r_low  = t["range_low"]
    actual_r = t["result_r"]
    risk   = abs(entry - stop)

    bars = get_bars_after(sym, t["timestamp_ms"], n=120)
    if not bars or risk <= 0:
        continue

    # ── 1. barra de breakout ──────────────────────────────────────────────────
    bo_bar = bars[0]
    bo_close = bo_bar["close"]
    bo_high  = bo_bar["high"]
    bo_low   = bo_bar["low"]
    atr      = bo_bar.get("atr") or risk

    # ── 2. entry slippage ─────────────────────────────────────────────────────
    # diferencia entre el close del breakout bar y donde realmente entramos
    if dirn == "Long":
        slippage_r = (entry - bo_close) / risk   # positivo = entramos MAS CARO que el close
        # mejor entrada posible = lo mas bajo de los primeros 3 bares (pullback)
        optimal_entry = min(b["low"] for b in bars[:3])
        optimal_entry = max(optimal_entry, r_low)  # no puede ser dentro del rango
        optimal_risk  = abs(optimal_entry - stop)
    else:
        slippage_r = (bo_close - entry) / risk   # positivo = entramos MAS BARATO que el close
        optimal_entry = max(b["high"] for b in bars[:3])
        optimal_entry = min(optimal_entry, r_high)
        optimal_risk  = abs(optimal_entry - stop)

    # ── 3. MFE y MAE (en R original) ─────────────────────────────────────────
    mfe_price = entry  # lo maximo favorable
    mae_price = entry  # lo maximo adverso
    max_fav = 0.0
    max_adv = 0.0
    bars_to_mfe = 0

    for i, bar in enumerate(bars[:120]):
        if dirn == "Long":
            fav = (bar["high"] - entry) / risk
            adv = (entry - bar["low"])  / risk
        else:
            fav = (entry - bar["low"])  / risk
            adv = (bar["high"] - entry) / risk

        if fav > max_fav:
            max_fav = fav
            bars_to_mfe = i + 1

        if adv > max_adv:
            max_adv = adv

        # si habria hecho stop out, MAE no puede crecer mas
        if max_adv >= 1.0:
            break

    # ── 4. capture efficiency ─────────────────────────────────────────────────
    capture = actual_r / max_fav if max_fav > 0 else 0.0

    # ── 5. stop calibration vs ATR ────────────────────────────────────────────
    stop_as_atr = risk / atr  # 1.0 = stop es exactamente 1 ATR

    # ── 6. optimal entry R ganado ─────────────────────────────────────────────
    # si hubieramos entrado en optimal_entry con el mismo stop
    # el riesgo cambia pero el movimiento disponible es mayor
    if optimal_risk > 0 and max_fav > 0:
        # max favorable en precio
        if dirn == "Long":
            mfe_price_abs = entry + max_fav * risk
            optimal_mfe_r = (mfe_price_abs - optimal_entry) / optimal_risk
        else:
            mfe_price_abs = entry - max_fav * risk
            optimal_mfe_r = (optimal_entry - mfe_price_abs) / optimal_risk
    else:
        optimal_mfe_r = max_fav

    entry_improvement = optimal_entry - entry if dirn == "Long" else entry - optimal_entry
    entry_improvement_r = entry_improvement / risk  # R ganado por mejor entrada

    row = {
        "ts":              ts(t["timestamp_ms"]),
        "symbol":          sym,
        "dir":             dirn,
        "session":         t.get("session", "?"),
        "actual_r":        round(actual_r, 3),
        "mfe_r":           round(max_fav, 3),
        "mae_r":           round(max_adv, 3),
        "capture_pct":     round(capture * 100, 1),
        "bars_to_mfe":     bars_to_mfe,
        "slippage_r":      round(slippage_r, 3),
        "stop_as_atr":     round(stop_as_atr, 3),
        "optimal_entry_gain_r": round(entry_improvement_r, 3),
        "optimal_mfe_r":   round(optimal_mfe_r, 3),
        "vr":              t.get("vr_at_breakout"),
        "score":           t.get("confluence_score"),
        "macro":           t.get("macro_regime"),
        "range_pct":       t.get("range_pct"),
    }
    analysis.append(row)


# ── TABLA DETALLADA ───────────────────────────────────────────────────────────
print("=" * 110)
print("RBF ENTRY SCIENCE — Analisis de calidad de entrada")
print("=" * 110)
print(f"{'Trade':<16} {'D':<2} {'Sesion':<20} {'ActR':>6} {'MFE':>6} {'MAE':>6} "
      f"{'Cap%':>6} {'Slip':>6} {'StpATR':>7} {'BstEnt':>7} {'VR':>5}")
print("-" * 110)

for r in analysis:
    cap_str = f"{r['capture_pct']:.0f}%" if r['mfe_r'] > 0 else "N/A"
    print(f"{r['ts']:<16} {r['dir'][0]:<2} {r['session']:<20} "
          f"{r['actual_r']:>+6.2f} {r['mfe_r']:>6.2f} {r['mae_r']:>6.2f} "
          f"{cap_str:>6} {r['slippage_r']:>+6.3f} {r['stop_as_atr']:>7.3f} "
          f"{r['optimal_entry_gain_r']:>+7.3f} {str(r['vr'] or 'N/A'):>5}")


# ── ESTADISTICAS AGREGADAS ────────────────────────────────────────────────────
n = len(analysis)
print(f"\n{'=' * 80}")
print(f"ESTADISTICAS AGREGADAS (n={n})")
print(f"{'=' * 80}")

def avg(lst): return sum(lst) / len(lst) if lst else 0
def pct(lst, fn): return 100 * sum(1 for x in lst if fn(x)) / len(lst) if lst else 0

mfes    = [r["mfe_r"] for r in analysis]
maes    = [r["mae_r"] for r in analysis]
caps    = [r["capture_pct"] for r in analysis if r["mfe_r"] > 0]
slips   = [r["slippage_r"] for r in analysis]
stpatrs = [r["stop_as_atr"] for r in analysis]
opt_ent = [r["optimal_entry_gain_r"] for r in analysis]

print(f"\n--- MFE (maximo movimiento favorable disponible) ---")
print(f"  Promedio:   {avg(mfes):+.3f}R")
print(f"  > 2R:       {pct(mfes, lambda x: x >= 2.0):.0f}% de los trades")
print(f"  > 1.5R:     {pct(mfes, lambda x: x >= 1.5):.0f}%")
print(f"  > 1R:       {pct(mfes, lambda x: x >= 1.0):.0f}%")
print(f"  < 1R:       {pct(mfes, lambda x: x < 1.0):.0f}%  <- el trade nunca tuvo chance de 2R")

print(f"\n--- MAE (maximo movimiento adverso antes de recuperar) ---")
print(f"  Promedio:   {avg(maes):+.3f}R")
print(f"  MAE < 0.5R: {pct(maes, lambda x: x < 0.5):.0f}%  (stops apretados ok)")
print(f"  MAE 0.5-1R: {pct(maes, lambda x: 0.5 <= x < 1.0):.0f}%")
print(f"  MAE >= 1R:  {pct(maes, lambda x: x >= 1.0):.0f}%  (ya hizo stop out)")

print(f"\n--- CAPTURE EFFICIENCY ---")
print(f"  Promedio:   {avg(caps):.1f}% del MFE capturado")
print(f"  < 25%:      {pct(caps, lambda x: x < 25):.0f}%  <- grave, casi no capturamos nada")
print(f"  25-75%:     {pct(caps, lambda x: 25 <= x < 75):.0f}%")
print(f"  > 75%:      {pct(caps, lambda x: x >= 75):.0f}%  <- bien, capturamos la mayoria")

print(f"\n--- ENTRY SLIPPAGE ---")
print(f"  Promedio:   {avg(slips):+.3f}R")
print(f"  Positivo (entramos peor que close del breakout): {pct(slips, lambda x: x > 0.05):.0f}%")
print(f"  Neutro (-0.05 a +0.05R):                         {pct(slips, lambda x: abs(x) <= 0.05):.0f}%")
print(f"  Negativo (entramos mejor):                        {pct(slips, lambda x: x < -0.05):.0f}%")

print(f"\n--- STOP SIZING vs ATR ---")
print(f"  Promedio stop:  {avg(stpatrs):.3f}x ATR")
print(f"  < 0.5 ATR:      {pct(stpatrs, lambda x: x < 0.5):.0f}%  <- stop muy apretado")
print(f"  0.5-1.0 ATR:    {pct(stpatrs, lambda x: 0.5 <= x < 1.0):.0f}%  <- zona ideal")
print(f"  > 1.0 ATR:      {pct(stpatrs, lambda x: x >= 1.0):.0f}%  <- stop ancho")

print(f"\n--- MEJORA POR ENTRADA OPTIMA (pullback al borde del rango) ---")
print(f"  Mejora promedio: {avg(opt_ent):+.3f}R por trade")
print(f"  > +0.2R mejora: {pct(opt_ent, lambda x: x > 0.2):.0f}% de los trades")
print(f"  Total R ganado si hubieramos esperado pullback: {sum(opt_ent):+.2f}R")


# ── SEPARAR WINS VS LOSSES EN CADA METRICA ────────────────────────────────────
print(f"\n{'=' * 80}")
print("WINS vs LOSSES — metricas comparadas")
print(f"{'=' * 80}")

wins   = [r for r in analysis if r["actual_r"] > 0]
losses = [r for r in analysis if r["actual_r"] <= 0]

metrics = [
    ("mfe_r",           "MFE"),
    ("mae_r",           "MAE"),
    ("capture_pct",     "Capture%"),
    ("slippage_r",      "Slippage"),
    ("stop_as_atr",     "Stop/ATR"),
    ("optimal_entry_gain_r", "Opt.Entry gain"),
    ("bars_to_mfe",     "Bars to MFE"),
]

print(f"{'Metrica':<22} {'Wins avg':>10} {'Losses avg':>12} {'Delta':>10}")
print("-" * 56)
for field, label in metrics:
    w_vals = [r[field] for r in wins   if r[field] is not None]
    l_vals = [r[field] for r in losses if r[field] is not None]
    w_avg  = avg(w_vals)
    l_avg  = avg(l_vals)
    print(f"{label:<22} {w_avg:>+10.3f} {l_avg:>+12.3f} {w_avg-l_avg:>+10.3f}")


# ── LOS 5 PEORES TRADES: DONDE FALLAMOS ──────────────────────────────────────
print(f"\n{'=' * 80}")
print("LOS 5 PEORES TRADES — diagnostico")
print(f"{'=' * 80}")

worst = sorted(analysis, key=lambda r: r["actual_r"])[:5]
for r in worst:
    print(f"\n  {r['ts']} {r['symbol']} {r['dir']} {r['session']}")
    print(f"    Resultado: {r['actual_r']:+.2f}R")
    print(f"    MFE: {r['mfe_r']:.2f}R  MAE: {r['mae_r']:.2f}R  Capture: {r['capture_pct']:.0f}%")
    print(f"    Slippage: {r['slippage_r']:+.3f}R  Stop/ATR: {r['stop_as_atr']:.3f}")
    print(f"    Mejora por entrada optima: {r['optimal_entry_gain_r']:+.3f}R")
    if r["mae_r"] >= 1.0:
        print(f"    DIAGNOSTICO: El precio fue directo al stop — no habia estructura favorable")
    elif r["mfe_r"] < 1.0:
        print(f"    DIAGNOSTICO: MFE < 1R — el trade nunca tuvo potencial real de llegar a 2R")
    elif r["capture_pct"] < 25:
        print(f"    DIAGNOSTICO: Habia movimiento ({r['mfe_r']:.1f}R) pero no lo capturamos — problema de salida")

# ── CORRELACION SIMPLE: VR vs MFE ────────────────────────────────────────────
print(f"\n{'=' * 80}")
print("VR vs MFE — correlacion")
print(f"{'=' * 80}")
vr_mfe = [(r["vr"], r["mfe_r"]) for r in analysis if r["vr"] is not None]
if vr_mfe:
    bins = [(2,3,"2-3x"),(3,4,"3-4x"),(4,99,"4x+")]
    for lo,hi,label in bins:
        subset = [mfe for vr,mfe in vr_mfe if lo <= vr < hi]
        if subset:
            print(f"  VR {label}: n={len(subset)} MFE_avg={avg(subset):.3f}R  MFE>2R={pct(subset, lambda x: x>=2):.0f}%")

# ── CORRELACION: SESSION vs MFE ───────────────────────────────────────────────
print(f"\n--- Session vs MFE ---")
by_sess = defaultdict(list)
for r in analysis:
    by_sess[r["session"]].append(r["mfe_r"])
for sess, mfes_s in sorted(by_sess.items()):
    wins_s = sum(1 for m in mfes_s if m >= 1.5)
    print(f"  {sess:<22}: n={len(mfes_s)} MFE_avg={avg(mfes_s):.3f}R  MFE>=1.5R={wins_s}/{len(mfes_s)}")

# ── CORRELACION: DIRECTION vs MFE ────────────────────────────────────────────
print(f"\n--- Direction vs MFE ---")
by_dir = defaultdict(list)
for r in analysis:
    by_dir[r["dir"]].append(r["mfe_r"])
for d, mfes_d in sorted(by_dir.items()):
    print(f"  {d:<8}: n={len(mfes_d)} MFE_avg={avg(mfes_d):.3f}R  MFE>=2R={pct(mfes_d, lambda x: x>=2):.0f}%")

# ── TRADES CON MFE ALTO PERO CAPTURE BAJO ────────────────────────────────────
print(f"\n{'=' * 80}")
print("TRADES CON MFE ALTO PERO MAL CAPTURADOS — oportunidades perdidas")
print(f"{'=' * 80}")
missed = [r for r in analysis if r["mfe_r"] >= 1.5 and r["capture_pct"] < 50]
for r in sorted(missed, key=lambda x: -x["mfe_r"]):
    print(f"  {r['ts']} {r['symbol']} {r['dir']} {r['session']}: "
          f"MFE={r['mfe_r']:.2f}R capturado={r['actual_r']:+.2f}R ({r['capture_pct']:.0f}%)")


# ── RESUMEN EJECUTIVO ─────────────────────────────────────────────────────────
print(f"\n{'=' * 80}")
print("RESUMEN EJECUTIVO — PROBLEMAS Y SOLUCIONES")
print(f"{'=' * 80}")

mfe_lt1 = sum(1 for r in analysis if r["mfe_r"] < 1.0)
mfe_lt1_pct = mfe_lt1 / n * 100

mae_direct = sum(1 for r in analysis if r["mae_r"] >= 1.0 and r["mfe_r"] < 0.5)
mae_direct_pct = mae_direct / n * 100

cap_low = sum(1 for r in analysis if r["mfe_r"] >= 1.5 and r["capture_pct"] < 50)
cap_low_pct = cap_low / n * 100

slip_bad = sum(1 for r in analysis if r["slippage_r"] > 0.1)
slip_bad_pct = slip_bad / n * 100

stp_tight = sum(1 for r in analysis if r["stop_as_atr"] < 0.5)
stp_tight_pct = stp_tight / n * 100

print(f"""
  PROBLEMA 1 — Trades sin potencial real (MFE < 1R): {mfe_lt1}/{n} = {mfe_lt1_pct:.0f}%
    -> El mercado nunca se mueve suficiente para nuestro TP de 2R
    -> Solucion: filtrar con VR>=3 (ya hecho) + exigir rango pct < 0.30%

  PROBLEMA 2 — Stops demasiado {'ajustados' if stp_tight_pct > 30 else 'anchos'} vs ATR: {stp_tight}/{n} = {stp_tight_pct:.0f}%
    -> Stop promedio = {avg(stpatrs):.2f}x ATR
    -> Zona ideal = 0.5-0.8x ATR para M1
    -> Solucion: calibrar stop = 0.6 * ATR_at_breakout

  PROBLEMA 3 — Potencial disponible no capturado: {cap_low}/{n} = {cap_low_pct:.0f}% de trades con MFE>=1.5R
    -> Habia movimiento pero salimos muy pronto o revertio
    -> Solucion: trailing desde 1.5R (ya validado)

  PROBLEMA 4 — Slippage de entrada: {slip_bad}/{n} = {slip_bad_pct:.0f}% entraron >0.1R peor que el breakout
    -> Promedio slippage = {avg(slips):+.3f}R
    -> Solucion: limit order en borde del rango en vez de market al breakout

  GANANCIA POTENCIAL POR MEJOR ENTRADA: {sum(opt_ent):+.2f}R total ({avg(opt_ent):+.3f}R/trade)
    -> Esperando pullback al borde del rango en los primeros 3 bares
""")

# guardar
with open("/tmp/rbf_entry_analysis.json", "w") as f:
    json.dump(analysis, f, indent=2)
print("Guardado en /tmp/rbf_entry_analysis.json")
