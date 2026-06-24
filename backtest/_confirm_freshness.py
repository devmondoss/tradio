"""
_confirm_freshness.py — PASO 1: ¿un nivel FRESCO rebota mejor que uno GASTADO?
=============================================================================
Para cada fill REAL del sistema A+B (misma entrada/gestión/fees que producción),
mide en las 48h PREVIAS al fill (causal, sin lookahead):
  • touches_48h        : nº de VISITAS distintas del precio al nivel
  • bars_since_touch   : frescura (barras desde la última visita; grande = fresco)
  • last_touch_held    : en la última visita, ¿el nivel aguantó o fue atravesado?
Luego correlaciona cada feature con el R del trade, separando IS / OOS.

El nivel del fill es `entry` (= lvl exacto del límite). Los toques se cuentan
con la MISMA tolerancia de toque de levels.py (0.2%). Nada se calcula con barras
>= bar del fill → estrictamente causal.

Correr:  python backtest/_confirm_freshness.py
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _audit_mirror import gen_h21_short
from _strategy_ab import run_system, stats, line, TF

WIN_H   = 48                      # ventana de historia del nivel (horas)
WIN     = WIN_H * 60 // TF        # barras M15 en 48h = 192
TOL     = 0.002                   # 0.2% — misma tolerancia de "toque" que levels.py


def level_history(a, i, lvl, side, win=WIN, tol=TOL):
    """Historia del nivel `lvl` en [i-win, i-1] (causal). Devuelve:
       n_visits, bars_since_last, last_held (True=aguantó / False=atravesado / None=sin toque)."""
    lo = lvl * (1 - tol); hi = lvl * (1 + tol)
    j0 = max(0, i - win)
    in_band = (a.l[j0:i] <= hi) & (a.h[j0:i] >= lo)   # barra "tocó" el nivel
    if not in_band.any():
        return 0, win, None
    # agrupar barras consecutivas in-band -> visitas distintas
    idx = np.where(in_band)[0]
    visits = np.split(idx, np.where(np.diff(idx) > 1)[0] + 1)
    n_visits = len(visits)
    last_visit = visits[-1]
    last_bar_local = last_visit[-1]            # última barra de la última visita
    last_bar = j0 + last_bar_local
    bars_since = i - last_bar
    # ¿aguantó? close del lado correcto del nivel al cerrar la última visita.
    # long (soporte): aguanta si cerró >= nivel. short (resistencia): aguanta si cerró <= nivel.
    c_last = a.c[last_bar]
    last_held = (c_last >= lvl) if side == "long" else (c_last <= lvl)
    return n_visits, bars_since, bool(last_held)


def grp(df, col, label):
    """avgR/WR/n por valor de `col`, IS y OOS."""
    print(f"\n  ── por {label} ──")
    print(f"  {'bucket':<14} {'n':>5} {'WR':>6} {'avgR':>8}   |   {'n_oos':>5} {'WR_oos':>7} {'avgR_oos':>9}")
    for v, g in df.groupby(col):
        o = g[g.oos]
        oos_wr = f"{100*(o.r>0).mean():5.1f}%" if len(o) else "   -- "
        oos_a  = f"{o.r.mean():+8.3f}" if len(o) else "      --"
        print(f"  {str(v):<14} {len(g):>5} {100*(g.r>0).mean():5.1f}% {g.r.mean():+8.3f}   |   "
              f"{len(o):>5} {oos_wr:>7} {oos_a:>9}")


def main():
    t = L2.load2(TF, start_ms=L2.TICK_MS); a = L2.A2(t)
    m1 = L2.load_m1_exit(start_ms=L2.TICK_MS)
    gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]
    span = (a.ts.max() - a.ts.min()) / 86_400_000

    df = run_system(a, gens, m1, TF, mode="routed")
    print(f"PASO 1 · frescura del nivel · M{TF} · {span:.0f}d · ventana historia {WIN_H}h ({WIN} barras)")
    line("A+B (baseline)", stats(df))

    # features causales por fill
    feats = [level_history(a, int(r.bar), float(r.entry), r.side) for r in df.itertuples()]
    df["touches"]      = [f[0] for f in feats]
    df["bars_since"]   = [f[1] for f in feats]
    df["last_held"]    = [f[2] for f in feats]

    # buckets
    df["touch_b"] = pd.cut(df.touches, [-1,0,1,2,1e9], labels=["0","1","2","3+"]).astype(str)
    df["fresh_b"] = pd.cut(df.bars_since, [-1, WIN//4, WIN//2, WIN], labels=["reciente","medio","fresco"]).astype(str)
    df["held_b"]  = df.last_held.map({True:"aguanto", False:"atraveso"}).fillna("sin_toque")

    grp(df, "touch_b", "nº de visitas en 48h (más = nivel gastado)")
    grp(df, "fresh_b", "frescura (barras desde última visita)")
    grp(df, "held_b",  "última visita: ¿aguantó o fue atravesado?")

    # correlaciones (Spearman robusto, todo y solo OOS)
    o = df[df.oos]
    def sp(x, y):
        from scipy.stats import spearmanr
        if len(x) < 5: return float("nan")
        return spearmanr(x, y).correlation
    try:
        print("\n  Spearman corr con R:")
        print(f"    touches    vs R : todo {sp(df.touches, df.r):+.3f}  | OOS {sp(o.touches, o.r):+.3f}")
        print(f"    bars_since vs R : todo {sp(df.bars_since, df.r):+.3f}  | OOS {sp(o.bars_since, o.r):+.3f}")
        held = df.dropna(subset=["last_held"])
        ho = held[held.oos]
        print(f"    last_held  vs R : todo {sp(held.last_held.astype(int), held.r):+.3f}  | OOS {sp(ho.last_held.astype(int), ho.r):+.3f}")
    except ImportError:
        print("\n  (scipy no disponible — omito Spearman; mirá las tablas de buckets)")


if __name__ == "__main__":
    main()
