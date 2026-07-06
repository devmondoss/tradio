"""
_sr_levels_test.py — S/R estructurales como niveles de entrada directa
=======================================================================
Genera y testea niveles que YA TENEMOS en parquet pero no usamos como entrada:
  1. PDH/PDL  — previous day H/L (virgin 71% del tiempo)
  2. Weekly H/L — rolling 8 días (virgin 88% del tiempo)
  3. Round numbers — BTC $1k/$5k, ETH $100/$500, SOL $10/$50
  4. Monthly H/L  — max/min del mes anterior (calculado on-the-fly)

Ademas: flag virgin (0 toques en lookback) para cada nivel.
Regla dura: OOS avgR > 0 en los 3 activos.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import numpy as np
import pandas as pd
import featurelab as FL
import _listas2 as L2
from _strategy_ab import run_system, stats
from _listas import OOS_MS

# ── Parametros identicos al binario Rust ─────────────────────────────────────
PARAMS = dict(
    trail_atr=6.0, stop_scale=0.8, mode="routed", volfilter=True,
    cooldown=6, max_day=2, min_range=0.0,
    use_partial=True, p1_frac=0.5, stop_floor_pct=0.15,
    tp2_cap_r=0.0, timeout_min=24*60,
)

TOUCH_TOL  = 0.002   # 0.2% tolerancia de toque (igual que Rust)
ENTRY_TOL  = 0.002   # precio debe llegar dentro del 0.2% del nivel
STOP_FRAC  = 0.5     # stop = nivel ± 0.5*ATR
MIN_RR     = 1.2
# run_system requiere l[i] <= lvl - margin*lvl/1e4 (margin=2.0 default = 0.02%)
# Para niveles donde el precio llega DESDE ARRIBA (rolling S/R), subimos lvl 0.1%
# para que el test de penetración del motor pueda dispararse.
ENTRY_ABOVE_OFFSET = 0.001   # 0.1% por encima del nivel S/R estructural

# ── Helpers ───────────────────────────────────────────────────────────────────

def get(a, col):
    arr = getattr(a, col, None)
    return None if arr is None else np.array(arr, dtype=float)

def struct_target(side, lvl, a, i):
    """Target estructural: nivel más cercano en dirección del trade."""
    candidates = []
    for col in ['vp_vah','vp_val','swing_high_50','swing_low_50',
                'prev_day_high','prev_day_low','weekly_high','weekly_low']:
        arr = get(a, col)
        if arr is None: continue
        v = float(arr[i])
        if not np.isfinite(v) or v <= 0: continue
        if side == "long"  and v > lvl * 1.002: candidates.append(v)
        if side == "short" and v < lvl * 0.998: candidates.append(v)
    if not candidates: return None, None
    tp2 = min(candidates) if side == "long" else max(candidates)
    risk = abs(lvl - (lvl - STOP_FRAC*a.atr[i] if side=="long" else lvl + STOP_FRAC*a.atr[i]))
    if risk <= 0 or abs(tp2 - lvl) / risk < MIN_RR: return None, None
    tp1_cands = [v for v in candidates if v != tp2 and
                 (v < tp2 if side=="long" else v > tp2)]
    tp1 = (min(tp1_cands) if side=="long" else max(tp1_cands)) if tp1_cands else None
    return tp1, tp2

def is_virgin(a, i, lvl, lookback=20):
    """True si el nivel no fue tocado en las ultimas `lookback` barras."""
    start = max(0, i - lookback)
    for j in range(start, i):
        if abs(a.l[j] - lvl) / lvl <= TOUCH_TOL: return False
        if abs(a.h[j] - lvl) / lvl <= TOUCH_TOL: return False
    return True

def touches(a, i, lvl, lookback=20):
    """Cuenta toques del nivel en las ultimas N barras."""
    n = 0
    for j in range(max(0, i-lookback), i):
        if abs(a.l[j] - lvl) / lvl <= TOUCH_TOL or abs(a.h[j] - lvl) / lvl <= TOUCH_TOL:
            n += 1
    return n

def signal(a, i, side, lvl, kind, virgin_req=False, max_touches=None):
    """Construye señal si el precio toca el nivel en barra i."""
    if not np.isfinite(lvl) or lvl <= 0: return []
    # Verificar que la barra toca el nivel
    if side == "long":
        if not (a.l[i] <= lvl * (1 + ENTRY_TOL)): return []
        if a.c[i] <= lvl: return []   # cierre debe ser sobre el nivel
    else:
        if not (a.h[i] >= lvl * (1 - ENTRY_TOL)): return []
        if a.c[i] >= lvl: return []   # cierre debe ser bajo el nivel
    # Virgin / touch filters
    if virgin_req and not is_virgin(a, i, lvl): return []
    if max_touches is not None and touches(a, i, lvl) > max_touches: return []
    stop = lvl - STOP_FRAC * a.atr[i] if side == "long" else lvl + STOP_FRAC * a.atr[i]
    tp1, tp2 = struct_target(side, lvl, a, i)
    if tp2 is None: return []
    return [(side, lvl, stop, tp1, tp2, kind)]

# ── Monthly H/L (calculado on-the-fly) ───────────────────────────────────────

def compute_monthly_hl(a):
    """Prev month H/L en cada barra M15."""
    ts = pd.to_datetime(a.ts * 1_000_000, utc=True)
    h_s = pd.Series(a.h, index=ts)
    l_s = pd.Series(a.l, index=ts)
    mh = h_s.resample("MS").max().shift(1)   # prev month high
    ml = l_s.resample("MS").min().shift(1)   # prev month low
    pmh = mh.reindex(ts, method="ffill").values
    pml = ml.reindex(ts, method="ffill").values
    return pmh, pml

# ── Round numbers ─────────────────────────────────────────────────────────────

ROUND_CONFIG = {
    "BTCUSDT": [1000, 5000],
    "ETHUSDT": [100, 500],
    "SOLUSDT": [10, 50],
}

def nearest_round(price, mult):
    return round(price / mult) * mult

# ── Generadores ───────────────────────────────────────────────────────────────

def gen_pdh_pdl(virgin=False, max_t=None):
    def g(a, i):
        pdh = get(a, 'prev_day_high')
        pdl = get(a, 'prev_day_low')
        sigs = []
        if pdl is not None:
            sigs += signal(a, i, "long",  float(pdl[i]), "pdl_entry", virgin, max_t)
        if pdh is not None:
            sigs += signal(a, i, "short", float(pdh[i]), "pdh_entry", virgin, max_t)
        return sigs or None
    return g

def gen_weekly_hl(virgin=False, max_t=None):
    def g(a, i):
        wh = get(a, 'weekly_high')
        wl = get(a, 'weekly_low')
        sigs = []
        if wl is not None:
            # Entrada 0.1% sobre el weekly_low para que run_system pueda verificar penetración
            lvl = float(wl[i]) * (1 + ENTRY_ABOVE_OFFSET)
            sigs += signal(a, i, "long",  lvl, "wl_entry", virgin, max_t)
        if wh is not None:
            # Entrada 0.1% bajo el weekly_high
            lvl = float(wh[i]) * (1 - ENTRY_ABOVE_OFFSET)
            sigs += signal(a, i, "short", lvl, "wh_entry", virgin, max_t)
        return sigs or None
    return g

def gen_monthly_hl(virgin=False, max_t=None):
    _cache = {}
    def g(a, i):
        if 'pmh' not in _cache:
            pmh, pml = compute_monthly_hl(a)
            _cache['pmh'] = pmh; _cache['pml'] = pml
        pmh = _cache['pmh']; pml = _cache['pml']
        sigs = []
        sigs += signal(a, i, "long",  float(pml[i]), "monthly_l", virgin, max_t)
        sigs += signal(a, i, "short", float(pmh[i]), "monthly_h", virgin, max_t)
        return sigs or None
    return g

def gen_round_numbers(sym, virgin=False, max_t=None):
    mults = ROUND_CONFIG.get(sym, [1000])
    def g(a, i):
        p = float(a.c[i])
        sigs = []
        for mult in mults:
            nr = nearest_round(p, mult)
            if nr <= 0: continue
            # Long en round desde arriba, short desde abajo
            if p > nr:
                sigs += signal(a, i, "long",  nr, f"round_{mult}", virgin, max_t)
            else:
                sigs += signal(a, i, "short", nr, f"round_{mult}", virgin, max_t)
        return sigs or None
    return g

# ── Runner ────────────────────────────────────────────────────────────────────

def run_test(label, gen_fn, virgin=False, max_t=None):
    print(f"\n{'='*66}")
    print(f"{label}  {'[virgin]' if virgin else ''}  {'[max_touches='+str(max_t)+']' if max_t is not None else ''}")
    print(f"  {'sym':<8} {'n':>5} {'n_oos':>6} {'IS_R':>7} {'OOS_R':>8} {'WR':>5} {'DD':>5}")
    oos_vals = []; ok = True
    for sym in FL.ASSETS:
        if not Path(FL.ASSETS[sym]).exists(): continue
        a, m1 = FL.load(sym)
        gens = [gen_fn(sym, virgin, max_t)]
        try:
            df = run_system(a, gens, m1, tf_min=15, **PARAMS)
            if len(df) == 0:
                print(f"  {sym:<8}   n=0  (sin señales)")
                oos_vals.append(0); ok = False; continue
            s = stats(df)
            oos = df[df.oos]
            oos_n = len(oos)
            oos_r = oos.r.mean() if oos_n > 0 else 0
            oos_vals.append(oos_r)
            if oos_r <= 0: ok = False
            print(f"  {sym:<8} {s['n']:>5} {oos_n:>6} {s['avgR']:>+7.3f} {oos_r:>+8.3f} "
                  f"{100*(oos.r>0).mean() if oos_n>0 else 0:>4.0f}% {s['dd']:>4.1f}%")
        except Exception as e:
            print(f"  {sym:<8} ERROR: {e}"); ok = False; oos_vals.append(0)
    port = sum(oos_vals)/len(oos_vals) if oos_vals else 0
    print(f"  {'Portfolio':>8}                   {port:>+8.3f}   {'PASA' if ok else 'FALLA'}")
    return ok, port

# Wrapper para que gen_fn reciba (sym, virgin, max_t)
def wrap_gen(gen_class, sym_agnostic=True):
    if sym_agnostic:
        return lambda sym, v, mt: gen_class(v, mt)
    else:
        return lambda sym, v, mt: gen_class(sym, v, mt)

# ── Tests ─────────────────────────────────────────────────────────────────────

print("=" * 66)
print("S/R ESTRUCTURALES COMO NIVEL DE ENTRADA DIRECTA")
print("Base: H1 slope OFF (nivel puro) | stop=0.5ATR | target estructural")
print("=" * 66)

results = {}

# 1. PDH/PDL
results['pdh_pdl']         = run_test("1. PDH/PDL — todos los toques",   wrap_gen(gen_pdh_pdl))
results['pdh_pdl_virgin']  = run_test("1b. PDH/PDL — solo virgin",       wrap_gen(gen_pdh_pdl), virgin=True)
results['pdh_pdl_1t']      = run_test("1c. PDH/PDL — max 1 toque",       wrap_gen(gen_pdh_pdl), max_t=1)

# 2. Weekly H/L
results['weekly']          = run_test("2. Weekly H/L — todos",           wrap_gen(gen_weekly_hl))
results['weekly_virgin']   = run_test("2b. Weekly H/L — solo virgin",    wrap_gen(gen_weekly_hl), virgin=True)

# 3. Monthly H/L
results['monthly']         = run_test("3. Monthly H/L — todos",          wrap_gen(gen_monthly_hl))

# 4. Round numbers
results['round']           = run_test("4. Round numbers",                wrap_gen(gen_round_numbers, sym_agnostic=False))
results['round_virgin']    = run_test("4b. Round numbers — solo virgin", wrap_gen(gen_round_numbers, sym_agnostic=False), virgin=True)

# ── Resumen ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
print("RESUMEN")
print(f"  {'Test':<35} {'Port OOS':>9}  Regla")
for k, (ok, oos) in sorted(results.items(), key=lambda x: -x[1][1]):
    print(f"  {k:<35} {oos:>+9.3f}  {'PASA' if ok else 'FALLA'}")
