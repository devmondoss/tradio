"""
_early_deep.py — Afina la señal temprana: qué ventana (30/45/60/90 min) y qué
umbral de corte maximiza el edge, en los 3 activos.

REGLA REAL evaluada: si a los W minutos el trade va peor que THR (en R),
se CIERRA ahí mismo (resultado = THR), no se elimina. Así el cálculo es honesto.
"""
import sys; from pathlib import Path
sys.path.insert(0, "backtest")
import numpy as np, pandas as pd
import _listas2 as L2
from _strategy_ab import run_system
from _audit_mirror import gen_h21_short

PARQUETS = {"BTCUSDT": "data/bybit-perp/processed/btcusdt_perp_m1.parquet",
            "ETHUSDT": "E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet",
            "SOLUSDT": "E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"}
WINS = [30, 45, 60, 90]
THRS = [-0.3, -0.5, -0.7]

def load(sym):
    L2.M1 = Path(PARQUETS[sym])
    t = L2.load2(15, start_ms=0); a = L2.A2(t)
    m1 = L2.load_m1_exit(start_ms=0)
    df = run_system(a, [L2.gen_h5(), L2.gen_h21(), gen_h21_short()], m1, 15,
                    mode="routed", max_day=4, cooldown=3)
    df = df[df.oos].reset_index(drop=True)
    m1ts, m1h, m1l, _ = m1
    bar_ms = 15 * 60_000
    for W in WINS:
        rcol = []
        for _, x in df.iterrows():
            j0 = np.searchsorted(m1ts, x.ts + bar_ms); je = min(j0 + W, len(m1ts))
            if je <= j0: rcol.append(0.0); continue
            e, risk, lng = x.entry, x.risk, x.side == "long"
            # peor excursión en la ventana (MAE en R) — es lo que dispararía el corte
            mae = (m1l[j0:je].min() - e) / risk if lng else (e - m1h[j0:je].max()) / risk
            rcol.append(mae)
        df[f"mae{W}"] = rcol
    return df

def eval_rule(df, W, thr):
    """Cierra a 'thr' los trades cuya MAE a los W min ya tocó thr; deja correr el resto."""
    hit = df[f"mae{W}"] <= thr
    r_eff = np.where(hit, thr, df.r)   # cortados → resultado = thr
    return r_eff.mean(), r_eff.sum(), int(hit.sum())

for sym in (sys.argv[1:] or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]):
    if not Path(PARQUETS[sym]).exists():
        print(f"SKIP {sym} (parquet no disponible)"); continue
    df = load(sym)
    base_avg, base_net, n = df.r.mean(), df.r.sum(), len(df)
    print(f"\n{'='*60}\n  {sym}  baseline avgR {base_avg:+.3f}  net$ {base_net*5:+.0f}  n {n}\n{'='*60}")
    print(f"  {'ventana':>8} {'umbral':>7} {'avgR':>7} {'Δ avgR':>8} {'net$':>7} {'cortados':>9}")
    best = None
    for W in WINS:
        for thr in THRS:
            avg, net, cut = eval_rule(df, W, thr)
            mark = ""
            if best is None or avg > best[0]: best = (avg, W, thr); mark = ""
            print(f"  {W:>5}min {thr:>+7.1f} {avg:>+7.3f} {avg-base_avg:>+8.3f} {net*5:>+7.0f} {cut:>4}/{n}")
    print(f"  → mejor: {best[1]}min @ {best[2]:+.1f}R  →  avgR {best[0]:+.3f}  (base {base_avg:+.3f})")
