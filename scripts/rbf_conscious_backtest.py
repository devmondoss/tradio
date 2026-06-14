#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
"""
RBF Conscious Backtest — Jun 6-11 2026
Examina cada potencial setup barra por barra, evalua microestructura
pre-trade y post-trade, y decide donde HABRIA valido la pena entrar.

Output: scripts/_conscious_bt.csv (detalle por setup)
        + resumen en consola por par/sesion/score
"""
import json, csv, urllib.request, urllib.parse, datetime, statistics
from collections import defaultdict

# ── Config Supabase ────────────────────────────────────────────────────────────
URL = "https://ztdhvmcisjjyhbqlgkzm.supabase.co"
KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inp0ZGh2bWNpc2pqeWhicWxna3ptIiwicm9sZSI6"
    "InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3ODk0MTc1MiwiZXhwIjoyMDk0NTE3NzUyfQ"
    ".sqMh9Jcxrxyg-ZBYWPaNN8DB9kf-KkC7ARPLucItN1Y"
)
TABLES = {
    "BTCUSDT": "btc_bars",
    "ETHUSDT": "eth_bars",
    "BNBUSDT": "bnb_bars",
    "SOLUSDT": "sol_bars",
    "XRPUSDT": "xrp_bars",
}
COLS = ("ts_ms,open,high,low,close,volume,bar_delta,cvd_slope,obi_l5,"
        "dz,vr,stacked_imb,absorption,thin_above,thin_below,"
        "bid_wall,ask_wall,vpin,regime,atr,oi_momentum,session")

# Jun 6 00:00 UTC  →  Jun 12 00:00 UTC
TS_FROM = 1780704000000   # 2026-06-06 00:00 UTC
TS_TO   = 1781222400000   # 2026-06-12 00:00 UTC

# ── Parametros RBF ────────────────────────────────────────────────────────────
RANGE_WINDOWS   = [15, 20, 30, 45, 60]
RANGE_MIN_PCT   = 0.08
RANGE_MAX_PCT   = 0.55
VR_MIN          = 3.0
MIN_RANGE_ATR   = 1.5
COOLDOWN        = 60
SESSIONS_OK     = {"London", "LondonNyOverlap", "NewYork"}
PRE_BARS        = 25   # barras de contexto pre-breakout
POST_BARS       = 60   # barras de seguimiento post-entrada

# ── Fetch ─────────────────────────────────────────────────────────────────────
def fetch(table, sym):
    bars, offset, page = [], 0, 1000
    hdrs = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}
    while True:
        params = [
            ("select", COLS),
            ("order", "ts_ms.asc"),
            ("ts_ms", f"gte.{TS_FROM}"),
            ("ts_ms", f"lte.{TS_TO}"),
            ("offset", str(offset)),
            ("limit", str(page)),
        ]
        qs = urllib.parse.urlencode(params)
        req = urllib.request.Request(f"{URL}/rest/v1/{table}?{qs}", headers=hdrs)
        try:
            resp = urllib.request.urlopen(req)
            batch = json.loads(resp.read())
        except Exception as e:
            print(f"  ERROR {sym}: {e}")
            # intenta sin filtro de fecha para diagnosticar
            qs2 = urllib.parse.urlencode([("select","ts_ms"),("order","ts_ms.desc"),("limit","1")])
            try:
                r2 = urllib.request.urlopen(urllib.request.Request(f"{URL}/rest/v1/{table}?{qs2}", headers=hdrs))
                print(f"  Ultima barra en DB: {json.loads(r2.read())}")
            except Exception as e2:
                print(f"  No se pudo diagnosticar: {e2}")
            break
        if not isinstance(batch, list):
            print(f"  Respuesta inesperada {sym}: {batch}")
            break
        for b in batch:
            b["symbol"] = sym
        bars.extend(batch)
        print(f"  {sym}: {len(bars)} barras...", end="\r")
        if len(batch) < page: break
        offset += page
    print(f"  {sym}: {len(bars)} barras          ")
    return bars

# ── Helpers ───────────────────────────────────────────────────────────────────
def sf(v, d=0.0):
    try: return float(v) if v not in (None, '', 'None') else d
    except: return d

def ts_str(ms):
    return datetime.datetime.utcfromtimestamp(ms/1000).strftime("%m-%d %H:%M")

def session_label(s):
    return {"London":"LDN","LondonNyOverlap":"OVR","NewYork":"NY"}.get(s, s[:3] if s else "???")

def vr_calc(bars_vol, cur_vol, window=50):
    if len(bars_vol) < 5: return 0.0
    hist = list(bars_vol)[-window:]
    mean = sum(hist) / len(hist)
    return cur_vol / mean if mean > 0 else 0.0

def dz_calc(delta_hist, cur_delta):
    h = list(delta_hist)
    if len(h) < 5: return 0.0
    mean = sum(h) / len(h)
    std  = (sum((x-mean)**2 for x in h) / len(h)) ** 0.5
    return (cur_delta - mean) / std if std > 1e-8 else 0.0

# ── Evaluacion consciente de microestructura ──────────────────────────────────
def score_setup(pre_bars, brk_bar, direction):
    """
    Evalua un setup barra a barra y retorna (score 0-10, dict de metricas).

    CRITERIOS (Short):
    + Range limpio:     expansion_n <= 2 en las 25 barras pre                 [0-2]
    + Presion vendedora: cum_delta < 0 fuertemente                            [0-2]
    + Pre-CVD limpio:   last5_delta < 0 (no compradores activos al final)     [0-1]
    + Breakout genuino: dz_dir en 0.5-3.0 (no extremo)                       [0-1]
    + Volumen real:     VR en barra de breakout                               [0-2]
    + OBI alineado:     obi_l5 < 0 para Short                                [0-1]
    + Sesion correcta:  London < Overlap < NY (ponderado)                     [0-1]
    """
    sign = -1 if direction == "Short" else 1  # Short: moves son hacia abajo

    # Metricas pre-entrada
    deltas     = [sf(b.get("bar_delta")) for b in pre_bars]
    cum_delta  = sum(deltas)
    last5      = sum(deltas[-5:]) if len(deltas) >= 5 else sum(deltas)
    obis       = [sf(b.get("obi_l5")) for b in pre_bars]
    avg_obi    = statistics.mean(obis) if obis else 0.0
    vpins      = [sf(b.get("vpin")) for b in pre_bars if b.get("vpin") not in (None,"")]
    avg_vpin   = statistics.mean(vpins) if vpins else 0.0
    regimes    = [b.get("regime","") for b in pre_bars]
    expansion_n = sum(1 for r in regimes if r == "Expansion")
    stacked_bear = sum(1 for b in pre_bars if b.get("stacked_imb") == "Bearish")
    stacked_bull = sum(1 for b in pre_bars if b.get("stacked_imb") == "Bullish")
    oi_mom_n   = sum(1 for b in pre_bars if b.get("oi_momentum"))

    # Metricas de la barra de breakout
    vr_brk   = sf(brk_bar.get("vr"))
    dz_brk   = sf(brk_bar.get("dz"))
    obi_brk  = sf(brk_bar.get("obi_l5"))
    vpin_brk = sf(brk_bar.get("vpin"))

    # dz en direccion del trade (Short: queremos dz negativo => dz_dir positivo con sign=-1)
    dz_dir = sign * (-dz_brk)

    # ── Scoring ───────────────────────────────────────────────────────────────
    score = 0.0
    flags = []

    # [0-2] Rango limpio (sin expansion reciente)
    if expansion_n == 0:
        score += 2; flags.append("rango_limpio")
    elif expansion_n <= 2:
        score += 1; flags.append("rango_ok")

    # [0-2] Presion en direccion correcta
    if direction == "Short":
        if cum_delta < -200:
            score += 2; flags.append("presion_vendedora_fuerte")
        elif cum_delta < 0:
            score += 1; flags.append("presion_vendedora_leve")
        elif cum_delta > 200:
            flags.append("COMPRADORES_DOMINAN")  # malo
    else:
        if cum_delta > 200:
            score += 2; flags.append("presion_compradora_fuerte")
        elif cum_delta > 0:
            score += 1; flags.append("presion_compradora_leve")

    # [0-1] Pre-CVD limpio (ultimas 5 barras no van contra el trade)
    if direction == "Short" and last5 < 0:
        score += 1; flags.append("pre_cvd_ok")
    elif direction == "Long" and last5 > 0:
        score += 1; flags.append("pre_cvd_ok")
    elif (direction == "Short" and last5 > 100) or (direction == "Long" and last5 < -100):
        flags.append("PRE_CVD_CONTRA")  # rojo

    # [0-1] dz en rango util (0.5-3.0 en direccion)
    if 0.5 <= dz_dir <= 3.0:
        score += 1; flags.append(f"dz_ok({dz_dir:.1f})")
    elif dz_dir > 3.0:
        flags.append(f"DZ_EXTREMO({dz_dir:.1f})")

    # [0-2] Volumen relativo en breakout
    if vr_brk >= 5.0:
        score += 2; flags.append(f"vr_fuerte({vr_brk:.1f}x)")
    elif vr_brk >= 3.0:
        score += 1; flags.append(f"vr_ok({vr_brk:.1f}x)")
    else:
        flags.append(f"vr_bajo({vr_brk:.1f}x)")

    # [0-1] OBI alineado en breakout
    if (direction == "Short" and obi_brk < -0.05) or (direction == "Long" and obi_brk > 0.05):
        score += 1; flags.append("obi_alineado")

    # VETO: VPIN toxico
    if vpin_brk > 0.65:
        score = max(0, score - 2); flags.append("VPIN_TOXICO")

    # VETO: stacked en direccion contraria
    if direction == "Short" and stacked_bull > stacked_bear and stacked_bull >= 5:
        score = max(0, score - 1); flags.append("STACKED_CONTRA")

    return round(score, 1), {
        "cum_delta": round(cum_delta, 0),
        "last5_delta": round(last5, 0),
        "avg_obi": round(avg_obi, 3),
        "avg_vpin": round(avg_vpin, 3),
        "expansion_n": expansion_n,
        "stacked_bear": stacked_bear,
        "stacked_bull": stacked_bull,
        "oi_mom_n": oi_mom_n,
        "vr_brk": round(vr_brk, 2),
        "dz_dir": round(dz_dir, 2),
        "obi_brk": round(obi_brk, 3),
        "flags": "|".join(flags),
    }

def simulate_outcome(bars, entry_idx, direction, stop, target, max_bars=60):
    """Simula el outcome del trade post-entrada."""
    entry = bars[entry_idx]["close"]
    best  = entry
    for j in range(1, min(max_bars, len(bars) - entry_idx)):
        b = bars[entry_idx + j]
        h, l = b["high"], b["low"]
        if direction == "Short":
            best = min(best, l)
            if h >= stop:
                result_r = (entry - stop) / abs(entry - stop) * -1 if abs(entry - stop) > 0 else -1
                return round((entry - stop) / (stop - entry) * -1, 3), j, "STOP"
            if l <= target:
                return 2.0, j, "TARGET"
        else:
            best = max(best, h)
            if l <= stop:
                return -1.0, j, "STOP"
            if h >= target:
                return 2.0, j, "TARGET"
    # Sin resolucion en max_bars
    last = bars[min(entry_idx + max_bars, len(bars)-1)]["close"]
    pnl = (entry - last) if direction == "Short" else (last - entry)
    risk = abs(entry - stop)
    return round(pnl / risk, 3) if risk > 0 else 0.0, max_bars, "TIMEOUT"

# ── Main ──────────────────────────────────────────────────────────────────────
print("Descargando barras Jun 6-11 desde Supabase...")
all_bars = {}
for sym, table in TABLES.items():
    bars = fetch(table, sym)
    if bars:
        all_bars[sym] = bars

print(f"\nTotal pares cargados: {len(all_bars)}")
for sym, bars in all_bars.items():
    if bars:
        t0 = ts_str(bars[0]["ts_ms"])
        t1 = ts_str(bars[-1]["ts_ms"])
        print(f"  {sym}: {len(bars)} barras  [{t0} → {t1}]")

print("\nEscaneando setups...")
all_setups = []

for sym, bars in all_bars.items():
    n = len(bars)
    cooldown = 0
    vol_hist   = []
    delta_hist = []

    for i in range(60, n - POST_BARS):
        bar = bars[i]
        vol   = sf(bar.get("volume"))
        delta = sf(bar.get("bar_delta"))
        sess  = bar.get("session", "")
        close = sf(bar.get("close"))
        atr   = sf(bar.get("atr"))

        vol_hist.append(vol)
        delta_hist.append(delta)
        if len(vol_hist) > 50: vol_hist.pop(0)
        if len(delta_hist) > 50: delta_hist.pop(0)

        if cooldown > 0:
            cooldown -= 1
            continue
        if sess not in SESSIONS_OK:
            continue

        vr = vr_calc(vol_hist, vol)

        for rw in RANGE_WINDOWS:
            if i < rw + 1: continue
            window = bars[i - rw: i]

            hi  = max(sf(b["high"])  for b in window)
            lo  = min(sf(b["low"])   for b in window)
            rng = hi - lo
            rng_pct = rng / close * 100 if close > 0 else 0

            if rng_pct < RANGE_MIN_PCT or rng_pct > RANGE_MAX_PCT:
                continue
            if atr > 0 and rng < MIN_RANGE_ATR * atr:
                continue

            breaks_down = close < lo
            breaks_up   = close > hi
            if not breaks_down and not breaks_up:
                continue

            # Requiere VR minimo (mas permisivo que live para ver todos los setups)
            if vr < 2.0:
                continue

            direction = "Short" if breaks_down else "Long"

            # CVD en rango
            cvd_rng = sum(sf(b.get("bar_delta")) for b in window)
            if direction == "Short" and cvd_rng >= 0: continue
            if direction == "Long"  and cvd_rng <= 0: continue

            # Pre-CVD gate (filtro probado)
            pre5 = sum(sf(b.get("bar_delta")) for b in window[-5:])
            pre_cvd_ok = (direction == "Short" and pre5 <= 0) or (direction == "Long" and pre5 >= 0)

            # Extension del breakout
            ext_pct = ((lo - close) / lo * 100 if breaks_down else (close - hi) / hi * 100)

            # Stop y target
            stop   = hi if breaks_down else lo
            risk   = abs(close - stop)
            if risk < 1e-6: continue
            target = close - 2 * risk if breaks_down else close + 2 * risk
            rr     = 2.0

            # Pre-bars para contexto
            pre_start = max(0, i - PRE_BARS)
            pre_bars_data = bars[pre_start:i]

            # Score consciente
            score, metrics = score_setup(pre_bars_data, bar, direction)

            # Outcome real
            result_r, bars_held, reason = simulate_outcome(
                bars, i, direction, stop, target
            )

            # dz en direccion
            dz_raw = sf(bar.get("dz"))
            dz_dir = (-dz_raw) if direction == "Short" else dz_raw

            setup = {
                "sym": sym,
                "ts": ts_str(bar["ts_ms"]),
                "ts_ms": bar["ts_ms"],
                "session": sess,
                "direction": direction,
                "close": round(close, 4),
                "range_bars": rw,
                "range_pct": round(rng_pct, 3),
                "cvd_rng": round(cvd_rng, 0),
                "vr": round(vr, 2),
                "ext_pct": round(ext_pct, 3),
                "pre_cvd_ok": pre_cvd_ok,
                "score": score,
                "result_r": result_r,
                "bars_held": bars_held,
                "reason": reason,
                **metrics,
            }
            all_setups.append(setup)

            cooldown = COOLDOWN
            break  # un setup por barra

print(f"  Total setups detectados: {len(all_setups)}")

# ── Guardar CSV ───────────────────────────────────────────────────────────────
out_csv = "scripts/_conscious_bt.csv"
if all_setups:
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(all_setups[0].keys()))
        w.writeheader()
        w.writerows(all_setups)
    print(f"  CSV guardado: {out_csv}")

# ── Analisis por score ────────────────────────────────────────────────────────
print("\n" + "="*80)
print("ANALISIS CONSCIENTE RBF — Jun 6-11 2026")
print("="*80)

def stats(lst):
    if not lst: return 0, 0, 0, 0
    wins = sum(1 for t in lst if t["result_r"] > 0)
    tot  = sum(t["result_r"] for t in lst)
    return len(lst), wins/len(lst)*100, tot/len(lst), tot

# Shorts vs Longs
for d in ["Short", "Long"]:
    lst = [s for s in all_setups if s["direction"] == d]
    n, wr, avg, tot = stats(lst)
    if n: print(f"\n{d}: n={n}  WR={wr:.0f}%  AvgR={avg:+.3f}  TotalR={tot:+.2f}")

# Por score
print("\n--- Por score de calidad ---")
from collections import defaultdict
by_score = defaultdict(list)
for s in all_setups:
    bucket = int(s["score"])
    by_score[bucket].append(s)
for sc in sorted(by_score):
    lst = by_score[sc]
    n, wr, avg, tot = stats(lst)
    bar_str = "#" * min(n, 20)
    print(f"  Score {sc}: n={n:>3}  WR={wr:4.0f}%  AvgR={avg:+.3f}  TotalR={tot:+.2f}  {bar_str}")

# Por sesion (solo Shorts que es lo relevante)
print("\n--- Por sesion (Shorts) ---")
by_sess = defaultdict(list)
for s in all_setups:
    if s["direction"] == "Short":
        by_sess[s["session"]].append(s)
for sess in ["London", "LondonNyOverlap", "NewYork"]:
    lst = by_sess[sess]
    n, wr, avg, tot = stats(lst)
    if n: print(f"  {sess:<22}: n={n:>3}  WR={wr:4.0f}%  AvgR={avg:+.3f}  TotalR={tot:+.2f}")

# Por par (Shorts)
print("\n--- Por simbolo (Shorts) ---")
by_sym = defaultdict(list)
for s in all_setups:
    if s["direction"] == "Short":
        by_sym[s["sym"]].append(s)
for sym in sorted(by_sym):
    lst = by_sym[sym]
    n, wr, avg, tot = stats(lst)
    print(f"  {sym:<10}: n={n:>3}  WR={wr:4.0f}%  AvgR={avg:+.3f}  TotalR={tot:+.2f}")

# Setups de alta calidad (score >= 6) — los mas interesantes
print("\n--- SETUPS SCORE >= 6 (alta calidad) ---")
hi_q = [s for s in all_setups if s["score"] >= 6 and s["direction"] == "Short"]
hi_q.sort(key=lambda x: x["score"], reverse=True)
print(f"  {'Fecha':>12}  {'Sym':<8}  {'Ses':>3}  {'Sc':>4}  {'VR':>5}  {'ext%':>6}  "
      f"{'cumD':>8}  {'pre5':>8}  {'expN':>4}  {'Result':>8}  Flags")
print(f"  {'-'*110}")
for s in hi_q:
    sess = session_label(s["session"])
    flags_short = s["flags"][:50] + "..." if len(s["flags"]) > 50 else s["flags"]
    pre_ok = "ok" if s["pre_cvd_ok"] else "XX"
    print(f"  {s['ts']:>12}  {s['sym']:<8}  {sess:>3}  {s['score']:>4}  "
          f"{s['vr']:>5.1f}  {s['ext_pct']:>6.3f}  "
          f"{s['cum_delta']:>8.0f}  {s['last5_delta']:>8.0f}  {s['expansion_n']:>4}  "
          f"{s['result_r']:>+8.3f}  {flags_short}")

# Pre-CVD gate: cuanto filtra y que evita
print("\n--- PRE-CVD gate: impacto sobre todos los Shorts ---")
shorts = [s for s in all_setups if s["direction"] == "Short"]
pasa  = [s for s in shorts if s["pre_cvd_ok"]]
filtr = [s for s in shorts if not s["pre_cvd_ok"]]
n1, wr1, avg1, tot1 = stats(pasa)
n2, wr2, avg2, tot2 = stats(filtr)
print(f"  Pasan pre-CVD gate : n={n1:>3}  WR={wr1:4.0f}%  AvgR={avg1:+.3f}  TotalR={tot1:+.2f}")
print(f"  Filtrados          : n={n2:>3}  WR={wr2:4.0f}%  AvgR={avg2:+.3f}  TotalR={tot2:+.2f}")

# Extension gate: cuanto filtra
print("\n--- Extension gate (ext > 0.1%) ---")
ext_pasa  = [s for s in shorts if s["ext_pct"] >= 0.1]
ext_filtr = [s for s in shorts if s["ext_pct"] < 0.1]
n1, wr1, avg1, tot1 = stats(ext_pasa)
n2, wr2, avg2, tot2 = stats(ext_filtr)
print(f"  ext >= 0.1%: n={n1:>3}  WR={wr1:4.0f}%  AvgR={avg1:+.3f}  TotalR={tot1:+.2f}")
print(f"  ext <  0.1%: n={n2:>3}  WR={wr2:4.0f}%  AvgR={avg2:+.3f}  TotalR={tot2:+.2f}")

# VSWAP: cuanto filtra (usando price_vs_vwap que no tenemos en bars — skip)
# Expansion gate
print("\n--- Expansion gate ---")
for max_exp in [0, 1, 2, 3]:
    lst = [s for s in shorts if s["expansion_n"] <= max_exp]
    n, wr, avg, tot = stats(lst)
    print(f"  expansion_n <= {max_exp}: n={n:>3}  WR={wr:4.0f}%  AvgR={avg:+.3f}  TotalR={tot:+.2f}")

# Surpresas: perdedores con score alto y ganadores con score bajo
print("\n--- SORPRESAS ---")
print("  Perdedores con score alto (>= 6):")
for s in [x for x in hi_q if x["result_r"] < 0]:
    print(f"    {s['ts']} {s['sym']:8} score={s['score']}  R={s['result_r']:+.2f}  {s['flags'][:60]}")

print("  Ganadores con score bajo (<= 3):")
lo_winners = [s for s in shorts if s["score"] <= 3 and s["result_r"] > 0]
for s in sorted(lo_winners, key=lambda x: x["result_r"], reverse=True)[:10]:
    print(f"    {s['ts']} {s['sym']:8} score={s['score']}  R={s['result_r']:+.2f}  {s['flags'][:60]}")

print("\nListo.")
