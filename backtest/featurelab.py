"""
featurelab.py — Laboratorio de features con protocolo FIJO y veredicto robusto.
===============================================================================
Problema que resuelve: cada feature se probaba con una gestión improvisada → el
resultado dependía de decisiones arbitrarias, no del feature. Acá TODO pasa por
el mismo protocolo y el mismo veredicto, así los resultados son comparables y el
harness deja de ser el confound.

Dos tipos de feature:
  • SEÑAL  (sweep, fvg, poc...): propone (side, lvl, stop, tp1, tp2). Se evalúa
    standalone con gestión A+B enrutada FIJA → signal_verdict().
  • FILTRO (frescura, confluencia, régimen...): gatea los trades del sistema base.
    Se evalúa partiendo los trades base en pasa/no-pasa → filter_verdict().

Regla dura (igual para ambos): una idea SOLO vale si es positiva en IS y OOS en
LOS 3 activos con muestra suficiente. Lo que brilla en OOS pero no en IS = régimen.

Uso desde otro script:
    import featurelab as FL
    FL.signal_verdict("sweep", make_sweep_gen)          # SEÑAL
    FL.filter_verdict("confluencia>=2", conf_filter)    # FILTRO
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _strategy_ab import run_system, stats
from _audit_mirror import gen_h21_short

ASSETS = {"BTCUSDT": "data/bybit-perp/processed/btcusdt_perp_m1.parquet",
          "ETHUSDT": "E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet",
          "SOLUSDT": "E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"}

# ── Protocolo FIJO (lo que NO se toca entre features) ─────────────────────────
STD = dict(mode="routed", max_day=4, cooldown=3, min_range=0.0, volfilter=True, tf_min=15)
MIN_N_OOS = 12          # muestra mínima OOS por activo para tomar en serio el signo
MIN_N_IS  = 25

_CACHE = {}
def load(sym):
    if sym not in _CACHE:
        L2.M1 = Path(ASSETS[sym]); t = L2.load2(15, start_ms=0)
        _CACHE[sym] = (L2.A2(t), L2.load_m1_exit(start_ms=0))
    return _CACHE[sym]

def base_gens():
    return [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]

_BASE = {}
def base_trades(sym, tf=15, **params):
    """Trades del sistema A+B base, cacheados por activo (para probar muchos filtros rápido)."""
    if sym not in _BASE:
        a, m1 = load(sym)
        p = {**STD, **params}; p.pop("tf_min", None)
        _BASE[sym] = run_system(a, base_gens(), m1, tf, **p)
    return _BASE[sym]

def _split(df):
    return df[~df.oos], df[df.oos]

def _avg(d): return d.r.mean() if len(d) else float("nan")
def _wr(d):  return 100*(d.r > 0).mean() if len(d) else float("nan")


def _verdict_table(name, per_asset):
    """per_asset: {sym: (is_n, is_avg, is_wr, oos_n, oos_avg, oos_wr)}. Devuelve PASS bool."""
    print(f"\n══ {name} ══")
    print(f"  {'activo':<8} {'IS n':>5} {'IS avgR':>8} {'IS WR':>6}   {'OOS n':>5} {'OOS avgR':>9} {'OOS WR':>7}   {'robusto?':>8}")
    ok_all = True
    for sym, (isn, isa, isw, on, oa, ow) in per_asset.items():
        ok = (isn >= MIN_N_IS and on >= MIN_N_OOS and isa > 0 and oa > 0)
        ok_all = ok_all and ok
        print(f"  {sym:<8} {isn:>5} {isa:>+8.3f} {isw:>5.0f}%   {on:>5} {oa:>+9.3f} {ow:>6.0f}%   {'SÍ' if ok else 'no':>8}")
    print(f"  → VEREDICTO: {'✅ ROBUSTO (positivo IS+OOS en los 3)' if ok_all else '❌ no generaliza'}")
    return ok_all


def signal_verdict(name, gen_factory, **override):
    """gen_factory: callable() -> gen(a,i) que devuelve [(side,lvl,stop,tp1,tp2,kind)].
    La gestión es FIJA (STD). Evalúa standalone en los 3 activos."""
    params = {**STD, **override}; tf = params.pop("tf_min")
    per = {}
    for sym in ASSETS:
        if not Path(ASSETS[sym]).exists(): continue
        a, m1 = load(sym)
        df = run_system(a, [gen_factory()], m1, tf, **params)
        i, o = _split(df)
        per[sym] = (len(i), _avg(i), _wr(i), len(o), _avg(o), _wr(o))
    return _verdict_table(f"SEÑAL · {name}", per)


def filter_verdict(name, filt, **override):
    """filt: callable(a, bar, side, entry) -> bool. Gatea los trades del sistema BASE.
    Compara base vs PASA-filtro vs DESCARTADO, por activo, IS/OOS."""
    params = {**STD, **override}; tf = params.pop("tf_min")
    per = {}
    print(f"\n══ FILTRO · {name} ══")
    print(f"  {'activo':<8} {'grupo':<9} {'IS n':>5} {'IS avgR':>8}   {'OOS n':>5} {'OOS avgR':>9} {'OOS WR':>7}")
    ok_all = True
    for sym in ASSETS:
        if not Path(ASSETS[sym]).exists(): continue
        a, m1 = load(sym)
        base = base_trades(sym, tf)
        mask = np.array([bool(filt(a, int(t.bar), t.side, float(t.entry))) for t in base.itertuples()])
        kept, drop = base[mask], base[~mask]
        bi, bo = _split(base); ki, ko = _split(kept); di, do = _split(drop)
        for lbl, (gi, go) in [("base", (bi, bo)), ("PASA", (ki, ko)), ("descarta", (di, do))]:
            print(f"  {sym:<8} {lbl:<9} {len(gi):>5} {_avg(gi):>+8.3f}   {len(go):>5} {_avg(go):>+9.3f} {_wr(go):>6.0f}%")
        # útil si: PASA mejora avgR OOS vs base y conserva muestra
        improves = (_avg(ko) > _avg(bo)) and (len(ko) >= MIN_N_OOS)
        ok_all = ok_all and improves
        per[sym] = improves
    print(f"  → VEREDICTO: {'✅ el filtro MEJORA en los 3 (sube avgR OOS, conserva n)' if ok_all else '❌ no mejora consistentemente'}")
    return ok_all


# ── Features de utilidad (confluencia, frescura) reusables ────────────────────
def confluence_filter(min_conf=2, tol=0.0015):
    """Cuenta cuántas fuentes de nivel coinciden con `entry` (±tol). Filtro causal."""
    def f(a, i, side, entry):
        srcs = [a.vp_vah[i], a.vp_val[i], a.vp_poc[i], a.swing_high_50[i], a.swing_low_50[i],
                a.prev_day_high[i], a.prev_day_low[i], a.fp_poc[i]]
        for nm in ("weekly_high", "weekly_low"):
            if hasattr(a, nm): srcs.append(getattr(a, nm)[i])
        c = sum(1 for s in srcs if np.isfinite(s) and abs(s-entry)/entry <= tol)
        return c >= min_conf
    return f
