"""
Bar Feature Mining — descubrimiento de patrones en barras M1.

Para cada barra calculamos:
  - Features del momento (vr, dz, cvd_slope, obi, delta, vpin, stacked_imb, etc.)
  - Features derivados (body_ratio, wick_up, wick_down, vol_z, bar_dir, etc.)
  - Forward return a N=5,10,15,30 barras (en ATR para normalizar entre pares)

Luego:
  1. Correlacion Spearman de cada feature vs forward return
  2. Top combinaciones de features (reglas AND simples)
  3. Las mejores 'zonas' de entrada que el mercado nos muestra
"""

import json, math
from collections import defaultdict

with open("/tmp/all_bars_full.json") as f:
    raw = json.load(f)

HORIZONS = [5, 10, 15, 30]   # barras hacia adelante
MIN_ATR   = 1e-6

# ── 1. Construir dataset flat con features + labels ───────────────────────────

def compute_features(bar):
    o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]
    rng = h - l
    body = abs(c - o)
    body_ratio    = body / rng if rng > 1e-9 else 0.5
    wick_up       = (h - max(o, c)) / rng if rng > 1e-9 else 0.0
    wick_down     = (min(o, c) - l) / rng if rng > 1e-9 else 0.0
    bar_dir       = 1 if c > o else (-1 if c < o else 0)
    bar_delta     = bar.get("bar_delta") or 0.0
    cvd_slope     = bar.get("cvd_slope") or 0.0
    obi           = bar.get("obi_l5") or 0.0
    dz            = bar.get("dz") or 0.0
    vr            = bar.get("vr") or 0.0
    vpin          = bar.get("vpin") or 0.0
    atr           = bar.get("atr") or 0.0
    thin_above    = 1 if bar.get("thin_above") else 0
    thin_below    = 1 if bar.get("thin_below") else 0
    stk_raw       = bar.get("stacked_imb") or "None"
    stk_bearish   = 1 if stk_raw == "Bearish" else 0
    stk_bullish   = 1 if stk_raw == "Bullish" else 0
    abs_raw       = bar.get("absorption") or "None"
    abs_ask       = 1 if abs_raw == "Ask" else 0
    abs_bid       = 1 if abs_raw == "Bid" else 0
    regime_raw    = bar.get("regime") or ""
    is_expansion  = 1 if "Expansion" in regime_raw else 0
    session_raw   = bar.get("session") or "OffHours"
    is_london     = 1 if session_raw == "London" else 0
    is_overlap    = 1 if session_raw == "LondonNyOverlap" else 0
    is_ny         = 1 if session_raw == "NewYork" else 0
    is_asia       = 1 if session_raw == "Asia" else 0
    # delta normalizado por ATR
    delta_atr     = bar_delta / atr if atr > MIN_ATR else 0.0
    # cvd_slope normalizado por ATR
    cvd_atr       = cvd_slope / atr if atr > MIN_ATR else 0.0

    return {
        "body_ratio":   body_ratio,
        "wick_up":      wick_up,
        "wick_down":    wick_down,
        "bar_dir":      bar_dir,
        "delta_atr":    delta_atr,
        "cvd_atr":      cvd_atr,
        "obi":          obi,
        "dz":           dz,
        "vr":           vr,
        "vpin":         vpin,
        "thin_above":   thin_above,
        "thin_below":   thin_below,
        "stk_bearish":  stk_bearish,
        "stk_bullish":  stk_bullish,
        "abs_ask":      abs_ask,
        "abs_bid":      abs_bid,
        "is_expansion": is_expansion,
        "is_london":    is_london,
        "is_overlap":   is_overlap,
        "is_ny":        is_ny,
        "is_asia":      is_asia,
        "atr":          atr,
        "close":        c,
        "sym":          bar.get("symbol") or bar.get("sym") or "?",
        "session":      session_raw,
        "ts_ms":        bar["ts_ms"],
    }

all_rows = []
for sym, bars_list in raw.items():
    bars = sorted(bars_list, key=lambda b: b["ts_ms"])
    for i, bar in enumerate(bars):
        feat = compute_features(bar)
        feat["sym"] = sym
        atr_i = feat["atr"]
        if atr_i < MIN_ATR:
            continue
        # forward returns en unidades de ATR (signed: positivo = sube)
        for h in HORIZONS:
            if i + h < len(bars):
                future_close = bars[i + h]["close"]
                ret_atr = (future_close - feat["close"]) / atr_i
            else:
                ret_atr = None
            feat[f"fwd_{h}"] = ret_atr
        all_rows.append(feat)

print(f"Dataset: {len(all_rows)} barras con features")

# ── 2. Correlacion Spearman feature vs forward return ─────────────────────────

def spearman_corr(xs, ys):
    """Correlacion Spearman sin numpy."""
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None and not math.isnan(x) and not math.isnan(y)]
    if len(pairs) < 30:
        return 0.0, 0
    n = len(pairs)
    def rank(lst):
        sorted_i = sorted(range(len(lst)), key=lambda i: lst[i])
        r = [0.0] * len(lst)
        for rank_val, idx in enumerate(sorted_i):
            r[idx] = rank_val + 1
        return r
    xs_v = [p[0] for p in pairs]
    ys_v = [p[1] for p in pairs]
    rx = rank(xs_v)
    ry = rank(ys_v)
    mx = sum(rx) / n; my = sum(ry) / n
    num = sum((rx[i]-mx)*(ry[i]-my) for i in range(n))
    dx  = math.sqrt(sum((rx[i]-mx)**2 for i in range(n)))
    dy  = math.sqrt(sum((ry[i]-my)**2 for i in range(n)))
    if dx < 1e-9 or dy < 1e-9:
        return 0.0, n
    return num / (dx * dy), n

FEATURES = [
    "body_ratio","wick_up","wick_down","bar_dir",
    "delta_atr","cvd_atr","obi","dz","vr","vpin",
    "thin_above","thin_below","stk_bearish","stk_bullish",
    "abs_ask","abs_bid","is_expansion",
    "is_london","is_overlap","is_ny","is_asia",
]

print("\n" + "=" * 90)
print("CORRELACION SPEARMAN: features vs retorno futuro (en ATR)")
print("=" * 90)
print(f"{'Feature':<18} {'fwd_5':>8} {'fwd_10':>8} {'fwd_15':>8} {'fwd_30':>8}")
print("-" * 50)

corrs = {}
for feat in FEATURES:
    row = {}
    for h in HORIZONS:
        xs = [r[feat] for r in all_rows]
        ys = [r[f"fwd_{h}"] for r in all_rows]
        rho, n2 = spearman_corr(xs, ys)
        row[h] = rho
    corrs[feat] = row
    stars = lambda r: "**" if abs(r) > 0.08 else ("*" if abs(r) > 0.05 else "  ")
    print(f"{feat:<18} {row[5]:>+7.4f}{stars(row[5])} {row[10]:>+7.4f}{stars(row[10])} "
          f"{row[15]:>+7.4f}{stars(row[15])} {row[30]:>+7.4f}{stars(row[30])}")

# ── 3. Analisis de setups combinados ─────────────────────────────────────────

print("\n" + "=" * 90)
print("SETUPS COMBINADOS — condiciones AND sobre los features mas correlacionados")
print("(retorno promedio en ATR a fwd_15, solo rows con n>=20)")
print("=" * 90)

def avg(lst): return sum(lst)/len(lst) if lst else 0.0
def wr(lst): return 100*sum(1 for x in lst if x > 0)/len(lst) if lst else 0.0

SETUPS_SHORT = [
    ("VR_HIGH + BEAR_DELTA",
     lambda r: r["vr"] >= 3.0 and r["delta_atr"] < -1.0),
    ("VR_HIGH + BEAR_DELTA + STK_BEARISH",
     lambda r: r["vr"] >= 3.0 and r["delta_atr"] < -1.0 and r["stk_bearish"] == 1),
    ("VR_HIGH + NEG_CVD + DZ_NEG",
     lambda r: r["vr"] >= 3.0 and r["cvd_atr"] < -1.0 and r["dz"] < -0.5),
    ("VR>=4 + BEAR_DELTA + LONDON",
     lambda r: r["vr"] >= 4.0 and r["delta_atr"] < -1.0 and r["is_london"] == 1),
    ("VR>=3 + OBI_NEG + DZ_NEG",
     lambda r: r["vr"] >= 3.0 and r["obi"] < -0.3 and r["dz"] < -0.5),
    ("VR>=3 + BEAR_DELTA + THIN_BELOW",
     lambda r: r["vr"] >= 3.0 and r["delta_atr"] < -1.0 and r["thin_below"] == 1),
    ("STK_BEARISH + VR>=2 + DZ_NEG",
     lambda r: r["stk_bearish"] == 1 and r["vr"] >= 2.0 and r["dz"] < -0.5),
    ("HIGH_VPIN + BEAR_DELTA",
     lambda r: r["vpin"] >= 0.6 and r["delta_atr"] < -0.5),
    ("EXPANSION + VR>=3 + NEG_DELTA",
     lambda r: r["is_expansion"] == 1 and r["vr"] >= 3.0 and r["delta_atr"] < -0.5),
    ("VR>=5 + cualquier sesion",
     lambda r: r["vr"] >= 5.0),
    ("ABSORCION_ASK (vendedores absorbidos = reversal bajista)",
     lambda r: r["abs_ask"] == 1),
    ("DZ < -1.5 (extreme selling)",
     lambda r: r["dz"] < -1.5),
    ("VR>=3 + WICK_UP > 0.5 (rechazo arriba)",
     lambda r: r["vr"] >= 3.0 and r["wick_up"] > 0.5),
    ("OBI < -0.4 + CVD neg + London",
     lambda r: r["obi"] < -0.4 and r["cvd_atr"] < 0 and r["is_london"] == 1),
]

SETUPS_LONG = [
    ("VR_HIGH + BULL_DELTA",
     lambda r: r["vr"] >= 3.0 and r["delta_atr"] > 1.0),
    ("STK_BULLISH + VR>=2 + DZ_POS",
     lambda r: r["stk_bullish"] == 1 and r["vr"] >= 2.0 and r["dz"] > 0.5),
    ("ABSORCION_BID (compradores absorbidos = reversal alcista)",
     lambda r: r["abs_bid"] == 1),
    ("DZ > 1.5 + OBI_POS",
     lambda r: r["dz"] > 1.5 and r["obi"] > 0.3),
    ("VR>=3 + WICK_DOWN > 0.5 (rechazo abajo)",
     lambda r: r["vr"] >= 3.0 and r["wick_down"] > 0.5),
]

print(f"\n{'Setup':<45} {'n':>5} {'WR%':>6} {'Avg_fwd15':>10} {'Total_ATR':>10}")
print("-" * 80)
print("  --- SHORTS (retorno negativo = ganancia) ---")
for name, cond in SETUPS_SHORT:
    subset = [r for r in all_rows if r["fwd_15"] is not None and cond(r)]
    if len(subset) < 10:
        continue
    rets = [-r["fwd_15"] for r in subset]   # negativo porque Short
    print(f"  {name:<45} {len(subset):>5} {wr(rets):>6.0f}% {avg(rets):>+10.3f} {sum(rets):>+10.2f}")

print("\n  --- LONGS (retorno positivo = ganancia) ---")
for name, cond in SETUPS_LONG:
    subset = [r for r in all_rows if r["fwd_15"] is not None and cond(r)]
    if len(subset) < 10:
        continue
    rets = [r["fwd_15"] for r in subset]
    print(f"  {name:<45} {len(subset):>5} {wr(rets):>6.0f}% {avg(rets):>+10.3f} {sum(rets):>+10.2f}")

# ── 4. La sesion mas predictiva ───────────────────────────────────────────────
print("\n" + "=" * 90)
print("SESION vs RETORNO FUTURO (fwd_15 en ATR, Short = invertido)")
print("=" * 90)
for sess in ["London", "LondonNyOverlap", "NewYork", "Asia", "OffHours"]:
    sub = [r for r in all_rows if r["session"] == sess and r["fwd_15"] is not None]
    if not sub: continue
    rets_short = [-r["fwd_15"] for r in sub]
    rets_long  = [ r["fwd_15"] for r in sub]
    print(f"  {sess:<22}: n={len(sub):4d}  Short_avg={avg(rets_short):+.3f}ATR  Long_avg={avg(rets_long):+.3f}ATR")

# ── 5. Distribucion de fwd_15 para entender el potencial real ─────────────────
print("\n" + "=" * 90)
print("DISTRIBUCION DEL RETORNO A 15 BARRAS (en ATR)")
print("= cuanto se mueve el mercado en 15 minutos tipicamente")
print("=" * 90)
all_fwd15 = [abs(r["fwd_15"]) for r in all_rows if r["fwd_15"] is not None]
all_fwd15.sort()
n_f = len(all_fwd15)
print(f"  n={n_f}")
print(f"  Mediana:  {all_fwd15[n_f//2]:.3f} ATR")
print(f"  P75:      {all_fwd15[int(n_f*0.75)]:.3f} ATR")
print(f"  P90:      {all_fwd15[int(n_f*0.90)]:.3f} ATR")
print(f"  P95:      {all_fwd15[int(n_f*0.95)]:.3f} ATR")
print(f"  P99:      {all_fwd15[int(n_f*0.99)]:.3f} ATR")
print(f"  >1 ATR:   {100*sum(1 for x in all_fwd15 if x>1)/n_f:.1f}% de las barras")
print(f"  >2 ATR:   {100*sum(1 for x in all_fwd15 if x>2)/n_f:.1f}%")
print(f"  >3 ATR:   {100*sum(1 for x in all_fwd15 if x>3)/n_f:.1f}%")

print("\nGuardado. Analisis completo.")
