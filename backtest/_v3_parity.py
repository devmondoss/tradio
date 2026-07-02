"""
_v3_parity.py — Backtest EXACTO de lo deployado en prod ("v3_fade_only") + ablation + optimización
===================================================================================================
v3 prod = niveles VP + IFVG + filtros H1 slope + dist(close,nivel)>0.5 ATR + gestión FADE
          (parcial 50% tp1 -> BE -> target estructural), stop_scale=0.8.

Nunca se validó como combo completo:
  - combo IFVG+H1+dist se validó con ROUTED trail=6 (OOS +1.97/+1.91/+1.87)
  - fade-only se validó SIN filtros (OOS +1.24/+1.46/+0.76)

Tareas:
  t1: v3 replicado vs fade-base vs v2-routed
  t2: ablation de filtros bajo fade (h1/dist/ifvg solos y pares)
  t3: grid gestión fade (p1_frac, timeout, stop_scale, min_range) one-at-a-time
  t4: slippage stress (slip_bps 2/5)

Uso: python backtest/_v3_parity.py t1 t2   |   python backtest/_v3_parity.py t3 t4 --h1 --dist --ifvg
"""
import sys, argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import numpy as np, pandas as pd
import _listas2 as L2
from _strategy_ab import run_system, stats
from _audit_mirror import gen_h21_short
from _ict_ifvg import gen_ifvg

ROOT = Path(__file__).parent.parent
ASSETS = {"BTCUSDT": ROOT / "data/bybit-perp/processed/btcusdt_perp_m1.parquet",
          "ETHUSDT": Path("E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet"),
          "SOLUSDT": Path("E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet")}
SYMS = [s for s in ASSETS if ASSETS[s].exists()]
TF = 15
STD = dict(max_day=4, cooldown=3, min_range=0.0, volfilter=True)  # protocolo featurelab

_CACHE = {}
def load(sym):
    if sym not in _CACHE:
        L2.M1 = ASSETS[sym]
        t = L2.load2(TF, start_ms=0)
        _CACHE[sym] = (L2.A2(t), L2.load_m1_exit(start_ms=0))
    return _CACHE[sym]

# ── Filtros v2 (idénticos a _parity_h1_ifvg.py / Rust levels.rs) ─────────────
def f_h1(a, bar, side, entry):
    if bar < 4: return True
    return (float(a.c[bar]) > float(a.c[bar-4])) if side == 'long' else (float(a.c[bar]) < float(a.c[bar-4]))

def f_dist(a, bar, side, entry):
    return abs(float(a.c[bar]) - float(entry)) >= 0.5 * float(a.atr[bar])

def make_filter(use_h1, use_dist):
    if not use_h1 and not use_dist: return None
    def f(a, i, s, e):
        if use_h1 and not f_h1(a, i, s, e): return False
        if use_dist and not f_dist(a, i, s, e): return False
        return True
    return f

def wrap(gens, f):
    out = []
    for gen in gens:
        def make(g, fn):
            def w(a, i):
                r = g(a, i)
                if not r: return r
                return [t for t in r if fn(a, i, t[0], t[1])] or None
            return w
        out.append(make(gen, f))
    return out

def run_cfg(sym, mode='fade', use_h1=False, use_dist=False, use_ifvg=False, **params):
    a, m1 = load(sym)
    gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]
    if use_ifvg: gens.append(gen_ifvg())
    filt = make_filter(use_h1, use_dist)
    if filt: gens = wrap(gens, filt)
    p = {**STD, **params}
    return run_system(a, gens, m1, TF, mode=mode, **p)

def row(df):
    if len(df) == 0:
        return dict(n_is=0, avgR_is=0, wr_is=0, n_oos=0, avgR_oos=0, wr_oos=0, netR_oos=0, dd=0)
    i, o = df[~df.oos], df[df.oos]
    s = stats(df)
    return dict(n_is=len(i), avgR_is=i.r.mean() if len(i) else 0, wr_is=100*(i.r > 0).mean() if len(i) else 0,
                n_oos=len(o), avgR_oos=o.r.mean() if len(o) else 0, wr_oos=100*(o.r > 0).mean() if len(o) else 0,
                netR_oos=o.r.sum() if len(o) else 0, dd=s['dd'])

def show(label, per):
    """per: {sym: row}. Imprime tabla compacta + veredicto regla dura."""
    print(f"\n── {label} ──")
    print(f"  {'sym':<8} {'IS n':>5} {'IS avgR':>8} {'IS WR':>6}   {'OOS n':>5} {'OOS avgR':>9} {'OOS WR':>7} {'OOS netR':>9} {'DD%':>5}")
    ok = True
    for sym, r in per.items():
        flag = "" if (r['avgR_is'] > 0 and r['avgR_oos'] > 0) else "  <FALLA"
        if flag: ok = False
        print(f"  {sym:<8} {r['n_is']:>5} {r['avgR_is']:>+8.3f} {r['wr_is']:>5.0f}%   {r['n_oos']:>5} {r['avgR_oos']:>+9.3f} {r['wr_oos']:>6.0f}% {r['netR_oos']:>+9.1f} {r['dd']:>5.1f}{flag}")
    print(f"  REGLA DURA: {'PASA' if ok else 'FALLA'}")
    return ok

def eval_cfg(label, **kw):
    per = {sym: row(run_cfg(sym, **kw)) for sym in SYMS}
    show(label, per)
    return per

# ─────────────────────────────────────────────────────────────────────────────
def t1():
    print("=" * 90)
    print("T1 — v3 REPLICADO vs FADE-BASE vs V2-ROUTED (protocolo STD cd=3/mx=4, min_range=0)")
    print("=" * 90)
    eval_cfg("fade-base (sin filtros, sin IFVG, stop_scale=1.0)", mode='fade', stop_scale=1.0)
    eval_cfg("v3 replicado: fade + H1 + dist>0.5ATR + IFVG + stop_scale=0.8",
             mode='fade', use_h1=True, use_dist=True, use_ifvg=True, stop_scale=0.8)
    eval_cfg("v3 replicado (params Rust: cooldown=6, max_day=2)",
             mode='fade', use_h1=True, use_dist=True, use_ifvg=True, stop_scale=0.8,
             cooldown=6, max_day=2)
    eval_cfg("v2 routed trail=6 + H1 + dist + IFVG + stop_scale=0.8 (referencia)",
             mode='routed', trail_atr=6.0, use_h1=True, use_dist=True, use_ifvg=True, stop_scale=0.8)

def t2():
    print("\n" + "=" * 90)
    print("T2 — ABLATION de filtros bajo FADE (stop_scale=0.8 fijo)")
    print("=" * 90)
    combos = [
        ("fade ss0.8 · sin nada",      dict()),
        ("fade ss0.8 · solo H1",       dict(use_h1=True)),
        ("fade ss0.8 · solo dist>0.5", dict(use_dist=True)),
        ("fade ss0.8 · solo IFVG",     dict(use_ifvg=True)),
        ("fade ss0.8 · H1+dist",       dict(use_h1=True, use_dist=True)),
        ("fade ss0.8 · H1+IFVG",       dict(use_h1=True, use_ifvg=True)),
        ("fade ss0.8 · dist+IFVG",     dict(use_dist=True, use_ifvg=True)),
        ("fade ss0.8 · H1+dist+IFVG (=v3)", dict(use_h1=True, use_dist=True, use_ifvg=True)),
    ]
    for label, kw in combos:
        eval_cfg(label, mode='fade', stop_scale=0.8, **kw)

def t3(combo):
    print("\n" + "=" * 90)
    print(f"T3 — GRID gestión fade (one-at-a-time) sobre combo {combo}")
    print("=" * 90)
    base = dict(mode='fade', stop_scale=0.8, p1_frac=0.5, timeout_min=24*60, min_range=0.0, **combo)
    eval_cfg("BASELINE grid (p1=0.5, to=24h, ss=0.8, mr=0.0)", **base)
    for p1 in [0.3, 0.7]:
        eval_cfg(f"p1_frac={p1}", **{**base, 'p1_frac': p1})
    for to in [12*60, 48*60]:
        eval_cfg(f"timeout={to//60}h", **{**base, 'timeout_min': to})
    for ss in [0.7, 1.0]:
        eval_cfg(f"stop_scale={ss}", **{**base, 'stop_scale': ss})
    for mr in [0.3, 0.5, 0.8]:
        eval_cfg(f"min_range={mr}", **{**base, 'min_range': mr})

def t4(combo, winner):
    print("\n" + "=" * 90)
    print(f"T4 — SLIPPAGE STRESS sobre config ganadora {winner}")
    print("=" * 90)
    base = dict(mode='fade', **combo, **winner)
    eval_cfg("slip_bps=0", **base)
    eval_cfg("slip_bps=2", **{**base, 'slip_bps': 2.0})
    eval_cfg("slip_bps=5", **{**base, 'slip_bps': 5.0})

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks", nargs="+", choices=["t1", "t2", "t3", "t4"])
    ap.add_argument("--h1", action="store_true")
    ap.add_argument("--dist", action="store_true")
    ap.add_argument("--ifvg", action="store_true")
    ap.add_argument("--stop_scale", type=float, default=0.8)
    ap.add_argument("--p1_frac", type=float, default=0.5)
    ap.add_argument("--timeout_min", type=int, default=24*60)
    ap.add_argument("--min_range", type=float, default=0.0)
    args = ap.parse_args()
    combo = dict(use_h1=args.h1, use_dist=args.dist, use_ifvg=args.ifvg)
    winner = dict(stop_scale=args.stop_scale, p1_frac=args.p1_frac,
                  timeout_min=args.timeout_min, min_range=args.min_range)
    for t in args.tasks:
        if t == "t1": t1()
        elif t == "t2": t2()
        elif t == "t3": t3(combo)
        elif t == "t4": t4(combo, winner)
    print("\nDONE")
