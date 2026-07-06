"""
_confluence_test.py — big_trade y H1 BoS como confluencia en los 3 activos
============================================================================
fp_absorb descartado: solo existe en BTC (tick data).
Candidatos con cobertura 3/3: big_trade (3-5%), h1_bos (29-31%), h1_choch (14-33%)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import numpy as np
import pandas as pd
import featurelab as FL
import _listas2 as L2
from _audit_mirror import gen_h21_short
from _strategy_ab import run_system, stats

BASE = dict(
    trail_atr=6.0, stop_scale=0.8, mode="routed", volfilter=True,
    tp2_cap_r=0.0, use_partial=True, p1_frac=0.5,
    timeout_min=24*60, stop_floor_pct=0.15, min_range=0.5,
    cooldown=6, max_day=2,
)

def wrap(gens, cond_long, cond_short):
    out = []
    for g in gens:
        def make(g_=g):
            def w(a, i):
                sigs = g_(a, i)
                if not sigs: return sigs
                r = []
                for s in sigs:
                    if s[0] == "long"  and not cond_long(a, i):  continue
                    if s[0] == "short" and not cond_short(a, i): continue
                    r.append(s)
                return r
            return w
        out.append(make())
    return out

def get(a, col):
    arr = getattr(a, col, None)
    return np.zeros(a.n) if arr is None else np.array(arr, dtype=float)

def run_test(label, cond_long, cond_short, base_res):
    rows = []
    for sym in FL.ASSETS:
        if not Path(FL.ASSETS[sym]).exists(): continue
        a, m1 = FL.load(sym)
        gens = wrap([L2.gen_h5(), L2.gen_h21(), gen_h21_short()], cond_long, cond_short)
        try:
            df = run_system(a, gens, m1, tf_min=15, **BASE)
            if len(df) == 0:
                rows.append((sym, 0, "?", 0.0, 0.0, 0.0, 0.0, False))
                continue
            s = stats(df)
            oos_n = int(df["oos"].sum()) if "oos" in df.columns else "?"
            b = base_res[sym]
            dr = s['oosA'] - b['oosA']
            ok = s['oosA'] > 0 and dr >= -0.05
            rows.append((sym, s['n'], oos_n, s['avgR'], s['oosA'], s['wr'], s['dd'], ok))
        except Exception as e:
            rows.append((sym, 0, "?", 0.0, 0.0, 0.0, 0.0, False))

    all_pass = all(r[7] for r in rows)
    print(f"\n{label}  ->  {'PASA' if all_pass else 'FALLA'}")
    print(f"  {'sym':<8} {'n':>5} {'n_oos':>6} {'IS_R':>7} {'OOS_R':>8} {'WR':>5} {'DD':>5} {'delta':>7} {'ok':>4}")
    for sym, n, oos_n, isr, oosr, wr, dd, ok in rows:
        b  = base_res.get(sym, {})
        dr = oosr - b.get('oosA', 0)
        print(f"  {sym:<8} {n:>5} {str(oos_n):>6} {isr:>+7.3f} {oosr:>+8.3f} "
              f"{wr:>4.0f}% {dd:>4.1f}% {dr:>+7.3f} {'V' if ok else 'X':>4}")
    return all_pass

# ── Base ──────────────────────────────────────────────────────────────────────
print("=" * 72)
print("BASE  (trail=6, routed, sin confluencia)")
print(f"  {'sym':<8} {'n':>5} {'IS_R':>7} {'OOS_R':>8} {'WR':>5} {'DD':>5}")
base_res = {}
for sym in FL.ASSETS:
    if not Path(FL.ASSETS[sym]).exists(): continue
    a, m1 = FL.load(sym)
    df = run_system(a, [L2.gen_h5(), L2.gen_h21(), gen_h21_short()], m1, tf_min=15, **BASE)
    s  = stats(df)
    base_res[sym] = s
    print(f"  {sym:<8} {s['n']:>5} {s['avgR']:>+7.3f} {s['oosA']:>+8.3f} {s['wr']:>4.0f}% {s['dd']:>4.1f}%")
avg_b = sum(v['oosA'] for v in base_res.values()) / len(base_res)
print(f"  {'Portfolio':>8}       {'':>7} {avg_b:>+8.3f}")

# ── TEST 1: big_trade (ventana 3 barras = 45 min) ─────────────────────────────
print("\n" + "=" * 72)
print("TEST 1: big_trade — trade institucional en las ultimas 3 barras (45 min)")
def bt_l(a, i): return bool(get(a, 'big_trade_bullish')[max(0,i-2):i+1].any())
def bt_s(a, i): return bool(get(a, 'big_trade_bearish')[max(0,i-2):i+1].any())
r1 = run_test("big_trade w=3", bt_l, bt_s, base_res)

# ── TEST 2: H1 BoS alineado (ventana 8 barras = 2h) ──────────────────────────
print("\n" + "=" * 72)
print("TEST 2: H1 BoS — estructura rota en las ultimas 2h, ahora retroceso al nivel")
def bos_l(a, i): return bool(get(a, 'h1_bos_bull')[max(0,i-7):i+1].any())
def bos_s(a, i): return bool(get(a, 'h1_bos_bear')[max(0,i-7):i+1].any())
r2 = run_test("H1 BoS w=8", bos_l, bos_s, base_res)

# ── TEST 3: H1 CHoCH (cambio de caracter — mas fuerte que BoS) ───────────────
print("\n" + "=" * 72)
print("TEST 3: H1 CHoCH — cambio de caracter en las ultimas 2h (senal de reversal)")
def choch_l(a, i): return bool(get(a, 'h1_choch_bull')[max(0,i-7):i+1].any())
def choch_s(a, i): return bool(get(a, 'h1_choch_bear')[max(0,i-7):i+1].any())
r3 = run_test("H1 CHoCH w=8", choch_l, choch_s, base_res)

# ── TEST 4: big_trade + H1 BoS combinado ─────────────────────────────────────
print("\n" + "=" * 72)
print("TEST 4: big_trade OR H1 BoS  (cualquiera de los dos = confluencia suficiente)")
def combo_l(a, i): return bt_l(a, i) or bos_l(a, i)
def combo_s(a, i): return bt_s(a, i) or bos_s(a, i)
r4 = run_test("big_trade OR H1 BoS", combo_l, combo_s, base_res)

# ── Resumen ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 72)
print("RESUMEN")
for label, ok in [("1. big_trade w=3", r1), ("2. H1 BoS w=8", r2),
                   ("3. H1 CHoCH w=8", r3), ("4. big_trade OR BoS", r4)]:
    print(f"  {label:<22} {'PASA' if ok else 'FALLA'}")
