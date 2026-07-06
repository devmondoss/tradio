"""
_scalp_chart.py — gráfico visual de los trades sc3 (config canónica) sobre el precio.
================================================================================
Por cada trade dibuja: entrada (▲long/▼short), línea de STOP (roja) y TARGET (verde)
extendidas hacia adelante, y el marcador coloreado por resultado (verde ganó / rojo perdió).
Genera PNGs en docs/scalp/charts/ + curva de equity.
Uso: python backtest/_scalp_chart.py [SYMBOL] [dias_ventana]
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
sys.path.insert(0, "backtest")
from _scalp import load, run_sc3, SC3, OOS_MS

OUT = Path("docs/scalp/charts"); OUT.mkdir(parents=True, exist_ok=True)
BAR_MS = 5*60_000


def chart(sym, s, df, days_win=21):
    t_end = s.ts.max(); t_lo = t_end - days_win*86_400_000
    mask = s.ts >= t_lo
    x = pd.to_datetime(s.ts[mask], unit="ms"); px = s.c[mask]
    tw = df[df.ts >= t_lo].copy()
    fig, ax = plt.subplots(figsize=(16, 7))
    ax.plot(x, px, lw=0.7, color="#555", zorder=1)
    fwd = 18*BAR_MS   # cuánto extender stop/target hacia adelante (18 barras M5 = 90 min)
    for r in tw.itertuples():
        x0 = pd.to_datetime(r.ts, unit="ms"); x1 = pd.to_datetime(r.ts + fwd, unit="ms")
        win = r.r > 0
        # zona target (verde) y stop (rojo)
        ax.hlines(r.tp2, x0, x1, color="#16a34a", lw=1.0, alpha=0.5, zorder=2)
        ax.hlines(r.stop, x0, x1, color="#dc2626", lw=1.0, alpha=0.5, zorder=2)
        ax.fill_between([x0, x1], r.entry, r.tp2, color="#16a34a", alpha=0.05, zorder=1)
        ax.fill_between([x0, x1], r.stop, r.entry, color="#dc2626", alpha=0.05, zorder=1)
        # marcador de entrada
        mk = "^" if r.side == "long" else "v"
        col = "#16a34a" if win else "#dc2626"
        ax.scatter(x0, r.entry, marker=mk, s=110, color=col, edgecolor="black", lw=0.6, zorder=4)
    n = len(tw); wr = 100*(tw.r > 0).mean() if n else 0
    ax.set_title(f"{sym} sc3 — últimos {days_win}d · {n} trades · WR {wr:.0f}% · "
                 f"▲long ▼short (verde=ganó/rojo=perdió) · banda verde=target rojo=stop")
    ax.grid(alpha=0.15); fig.tight_layout()
    p = OUT / f"{sym[:3].lower()}_trades_{days_win}d.png"
    fig.savefig(p, dpi=115); plt.close(fig)
    print(f"  → {p}")


def equity_chart(all_df):
    all_df = all_df.sort_values("ts").reset_index(drop=True)
    cap = 500.0; eq = []
    for r in all_df.r.values: cap += 5*r; eq.append(cap)
    x = pd.to_datetime(all_df.ts, unit="ms")
    fig, ax = plt.subplots(figsize=(16, 5))
    ax.plot(x, eq, lw=1.3, color="#2563eb")
    ax.axhline(500, color="#999", ls="--", lw=0.8)
    ox = pd.to_datetime(OOS_MS, unit="ms")
    ax.axvline(ox, color="#f59e0b", ls=":", lw=1.3); ax.text(ox, max(eq), " OOS", color="#f59e0b", va="top")
    ax.set_title(f"sc3 portfolio equity (FADE x3) — 500 USD riesgo fijo · {len(all_df)} trades · "
                 f"final {cap:,.0f} USD (+{100*(cap-500)/500:.0f}%)")
    ax.grid(alpha=0.15); fig.tight_layout()
    p = OUT / "equity_portfolio.png"; fig.savefig(p, dpi=115); plt.close(fig)
    print(f"  → {p}")


def main():
    syms = [sys.argv[1]] if len(sys.argv) > 1 else list(SC3)
    win = int(sys.argv[2]) if len(sys.argv) > 2 else 21
    print("FRECUENCIA (config canónica corregida, FADE x3):")
    allt = []
    cache = {}
    for sym in syms:
        df, st = run_sc3(sym); s = load(sym, 5); cache[sym] = (s, df)
        span = (s.ts.max()-s.ts.min())/86_400_000
        print(f"  {sym[:3]}: {st['n']} trades · {st['n']/span:.1f}/día · WR {st['wr']:.0f}% · "
              f"OOS {st['oosA']:+.2f} · DD {st['dd']:.0f}%")
        df = df.copy(); df["sym"] = sym[:3]; allt.append(df)
    print("Gráficos:")
    for sym in syms: chart(sym, *cache[sym], win)
    if len(syms) > 1: equity_chart(pd.concat(allt, ignore_index=True))


if __name__ == "__main__":
    main()
