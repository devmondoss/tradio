"""
_sr_equilibrio.py — Equilibrio global: v2_h1_ifvg + S/R en 3 configs
=====================================================================
Métrica objetivo: N trades, WR, avgR, RR, PnL desde $500 (riesgo $5/trade)
Regla dura: OOS > 0 en los 3 activos.

Config probadas:
  0. v2 base (H1+dist+IFVG) — referencia
  1. v2 + filtro S/R contexto (solo entra si VP coincide con S/R)
  2. v2 + S/R como entradas adicionales (weekly + round virgin)
  3. Combinado: v2 con contexto S/R + entradas adicionales S/R
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import numpy as np
import pandas as pd
import featurelab as FL
import _listas2 as L2
from _audit_mirror import gen_h21_short
from _ict_ifvg import gen_ifvg
from _strategy_ab import run_system, stats
from _listas import OOS_MS

# ── Params Rust ───────────────────────────────────────────────────────────────
PARAMS = dict(
    trail_atr=6.0, stop_scale=0.8, mode='routed',
    volfilter=True, cooldown=6, max_day=2, min_range=0.0,
    use_partial=True, p1_frac=0.5, stop_floor_pct=0.15,
    tp2_cap_r=0.0, timeout_min=24*60,
)

OOS_DAYS = 109   # días OOS para annualizar

# ── v2 generators (H1 slope + dist>0.5ATR + IFVG) ────────────────────────────
def f_h1(a, bar, side, entry):
    if bar < 4: return True
    return (a.c[bar] > a.c[bar-4]) if side == 'long' else (a.c[bar] < a.c[bar-4])

def f_dist(a, bar, side, entry):
    return abs(float(a.c[bar]) - float(entry)) >= 0.5 * float(a.atr[bar])

def filt(a, i, s, e): return f_h1(a,i,s,e) and f_dist(a,i,s,e)

def wrap_v2(gens):
    out = []
    for g in gens:
        def make(g_=g):
            def w(a, i):
                r = g_(a, i)
                if not r: return r
                return [t for t in r if filt(a, i, t[0], t[1])] or None
            return w
        out.append(make())
    return out

def v2_gens():
    return wrap_v2([L2.gen_h5(), L2.gen_h21(), gen_h21_short(), gen_ifvg()])

# ── S/R helpers ───────────────────────────────────────────────────────────────
ROUND_CONFIG = {"BTCUSDT":[1000,5000], "ETHUSDT":[100,500], "SOLUSDT":[10,50]}
TOUCH_TOL = 0.002
STOP_FRAC = 0.5
ENTRY_ABOVE = 0.001

def near_sr(a, i, sym, tol):
    p = float(a.c[i])
    for col in ['prev_day_high','prev_day_low','weekly_high','weekly_low']:
        arr = getattr(a, col, None)
        if arr is not None and abs(p - float(arr[i]))/p < tol: return True
    for m in ROUND_CONFIG.get(sym, [1000]):
        nr = round(p/m)*m
        if nr > 0 and abs(p - nr)/p < tol: return True
    return False

def wrap_sr_ctx(gens, sym, tol):
    out = []
    for g in gens:
        def make(g_=g):
            def w(a, i):
                if not near_sr(a, i, sym, tol): return None
                return g_(a, i)
            return w
        out.append(make())
    return out

def sr_struct_target(side, lvl, a, i):
    candidates = []
    for col in ['vp_vah','vp_val','swing_high_50','swing_low_50',
                'prev_day_high','prev_day_low','weekly_high','weekly_low']:
        arr = getattr(a, col, None)
        if arr is None: continue
        v = float(arr[i])
        if not np.isfinite(v) or v <= 0: continue
        if side == "long"  and v > lvl * 1.002: candidates.append(v)
        if side == "short" and v < lvl * 0.998: candidates.append(v)
    if not candidates: return None, None
    tp2 = min(candidates) if side == "long" else max(candidates)
    risk = STOP_FRAC * a.atr[i]
    if risk <= 0 or abs(tp2 - lvl)/risk < 1.2: return None, None
    tp1s = [v for v in candidates if v != tp2 and (v < tp2 if side=="long" else v > tp2)]
    tp1 = (min(tp1s) if side=="long" else max(tp1s)) if tp1s else None
    return tp1, tp2

def is_virgin(a, i, lvl, lookback=20):
    for j in range(max(0, i-lookback), i):
        if abs(a.l[j]-lvl)/lvl <= TOUCH_TOL or abs(a.h[j]-lvl)/lvl <= TOUCH_TOL:
            return False
    return True

def sr_signal(a, i, side, lvl, kind, virgin_req=False):
    if not np.isfinite(lvl) or lvl <= 0: return []
    if side == "long":
        if not (a.l[i] <= lvl*(1+TOUCH_TOL)): return []
        if a.c[i] <= lvl: return []
    else:
        if not (a.h[i] >= lvl*(1-TOUCH_TOL)): return []
        if a.c[i] >= lvl: return []
    if virgin_req and not is_virgin(a, i, lvl): return []
    stop = lvl - STOP_FRAC*a.atr[i] if side=="long" else lvl + STOP_FRAC*a.atr[i]
    tp1, tp2 = sr_struct_target(side, lvl, a, i)
    if tp2 is None: return []
    return [(side, lvl, stop, tp1, tp2, kind)]

def gen_weekly_hl():
    def g(a, i):
        wh = getattr(a,'weekly_high',None); wl = getattr(a,'weekly_low',None)
        sigs = []
        if wl is not None:
            sigs += sr_signal(a, i, "long",  float(wl[i])*(1+ENTRY_ABOVE), "wl")
        if wh is not None:
            sigs += sr_signal(a, i, "short", float(wh[i])*(1-ENTRY_ABOVE), "wh")
        return sigs or None
    return g

def gen_round(sym, virgin=True):
    mults = ROUND_CONFIG.get(sym, [1000])
    def g(a, i):
        p = float(a.c[i]); sigs = []
        for m in mults:
            nr = round(p/m)*m
            if nr <= 0: continue
            side = "long" if p > nr else "short"
            sigs += sr_signal(a, i, side, nr, f"rnd{m}", virgin)
        return sigs or None
    return g

# ── PnL desde $500, riesgo $5/trade ──────────────────────────────────────────
def pnl_500(df, risk_usd=5.0, start=500.0):
    cap = start; peak = start; dd = 0.0
    for r in df.sort_values("ts").r.values:
        cap += risk_usd * r
        peak = max(peak, cap)
        dd   = max(dd, (peak-cap)/peak)
    return cap, 100*dd

# ── Runner completo ───────────────────────────────────────────────────────────
def run_full(label, sym, gens):
    a, m1 = FL.load(sym)
    df = run_system(a, gens, m1, tf_min=15, **PARAMS)
    if len(df) == 0:
        return None, None
    s  = stats(df)
    oo = df[df.oos]
    n_oos   = len(oo)
    avgr_oos = oo.r.mean() if n_oos > 0 else 0
    wr_oos   = 100*(oo.r>0).mean() if n_oos > 0 else 0
    wins = oo[oo.r>0]; loss = oo[oo.r<=0]
    avgw = wins.r.mean() if len(wins) else 0
    avgl = loss.r.mean() if len(loss) else 0
    rr   = avgw/abs(avgl) if avgl else 0
    pnl, dd = pnl_500(df)
    return dict(
        n=s['n'], n_oos=n_oos,
        avgR=avgr_oos, wr=wr_oos,
        rr=rr, net_r=oo.r.sum(),
        pnl=pnl, dd=dd,
    ), df

def print_suite(label, res_by_sym, base=None):
    print(f"\n{'='*72}")
    print(f"  {label}")
    print(f"  {'sym':<8} {'n':>5} {'n_oos':>6} {'avgR':>7} {'WR':>5} {'RR':>5} {'net_R':>7} {'PnL$':>7} {'DD':>5}")
    all_ok = True
    for sym, r in res_by_sym.items():
        if r is None:
            print(f"  {sym:<8} n=0"); all_ok=False; continue
        if r['avgR'] <= 0: all_ok = False
        b = base.get(sym) if base else None
        d = f"  d_avgR={r['avgR']-b['avgR']:>+.3f}" if b else ""
        print(f"  {sym:<8} {r['n']:>5} {r['n_oos']:>6} {r['avgR']:>+7.3f} "
              f"{r['wr']:>4.0f}% {r['rr']:>4.2f}x {r['net_r']:>+7.0f}R "
              f"${r['pnl']:>6.0f}  {r['dd']:>4.1f}%{d}")
    vals = [r['avgR'] for r in res_by_sym.values() if r and r['avgR']]
    port = sum(vals)/len(vals) if vals else 0
    print(f"  {'Portfolio OOS avgR':>20}: {port:>+.3f}  {'PASA' if all_ok else 'FALLA'}")
    return all_ok, port

SYMS = [s for s in FL.ASSETS if Path(FL.ASSETS[s]).exists()]

# ── 0. BASE v2_h1_ifvg ────────────────────────────────────────────────────────
base_res = {}
for sym in SYMS:
    r, _ = run_full("base", sym, v2_gens())
    base_res[sym] = r
ok0, p0 = print_suite("0. BASE v2_h1_ifvg (H1+dist+IFVG)", base_res)

# ── 1. v2 + contexto S/R 0.3% ────────────────────────────────────────────────
ctx_res = {}
for sym in SYMS:
    gens = wrap_sr_ctx(v2_gens(), sym, 0.003)
    r, _ = run_full("ctx", sym, gens)
    ctx_res[sym] = r
ok1, p1 = print_suite("1. v2 + CONTEXTO S/R (tol 0.3%)", ctx_res, base_res)

# ── 2. v2 + entradas S/R adicionales ─────────────────────────────────────────
add_res = {}
for sym in SYMS:
    gens = v2_gens() + [gen_weekly_hl(), gen_round(sym)]
    r, _ = run_full("add", sym, gens)
    add_res[sym] = r
ok2, p2 = print_suite("2. v2 + Weekly H/L + Round virgin", add_res, base_res)

# ── 3. Combinado: v2 con contexto S/R + entradas adicionales ─────────────────
combo_res = {}
for sym in SYMS:
    v2 = wrap_sr_ctx(v2_gens(), sym, 0.005)   # contexto un poco más suelto
    sr = [gen_weekly_hl(), gen_round(sym)]
    r, _ = run_full("combo", sym, v2 + sr)
    combo_res[sym] = r
ok3, p3 = print_suite("3. COMBO: v2 con ctx 0.5% + Weekly + Round", combo_res, base_res)

# ── Resumen global ────────────────────────────────────────────────────────────
print(f"\n{'='*72}")
print("RESUMEN GLOBAL")
print(f"  {'Config':<42} {'Port OOS':>9}  {'WR':>5}  {'PnL BTC':>8}  Regla")

for label, res, ok, port in [
    ("0. BASE v2_h1_ifvg",                    base_res,  ok0, p0),
    ("1. v2 + CONTEXTO 0.3%",                 ctx_res,   ok1, p1),
    ("2. v2 + Weekly + Round virgin",          add_res,   ok2, p2),
    ("3. COMBO ctx 0.5% + Weekly + Round",     combo_res, ok3, p3),
]:
    btc = res.get("BTCUSDT")
    pnl_btc = f"${btc['pnl']:.0f}" if btc else "n/a"
    wr_avg  = sum(r['wr'] for r in res.values() if r) / len([r for r in res.values() if r])
    print(f"  {label:<42} {port:>+9.3f}  {wr_avg:>4.0f}%  {pnl_btc:>8}  {'PASA' if ok else 'FALLA'}")
