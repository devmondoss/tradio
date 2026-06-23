"""
_metrics_final.py — Estado final de las metricas para los 3 activos.
Usa run_system() original (sin reimplementar), max_day=4 cool=3.
Scaled entry: factor +28% validado sobre PnL (no cambia n ni avgR base).
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _listas import OOS_MS
from _audit_mirror import gen_h21_short
from _strategy_ab import run_system, stats

TF = 15
FILL_RATE   = 0.65
SLIP_DISC   = 0.15
CAPITAL     = 500.0
RISK_USD    = CAPITAL * 0.01   # $5/trade
EFF_RISK    = RISK_USD * FILL_RATE * (1 - SLIP_DISC)   # $2.76 efectivo
SCALE2_MULT = 1.28             # +28% PnL validado en _entry_improve.py

PARQUETS = {
    "BTCUSDT": Path(__file__).parent.parent / "data/bybit-perp/processed/btcusdt_perp_m1.parquet",
    "ETHUSDT": Path("E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet"),
    "SOLUSDT": Path("E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"),
}


def pnl_year(df, scale2=True):
    o = df[df.oos]
    if len(o) == 0:
        return 0.0
    days_oos = max(1, (o.ts.max() - o.ts.min()) / 86_400_000)
    pnl = EFF_RISK * o.r.sum() * (365 / days_oos)
    return pnl * (SCALE2_MULT if scale2 else 1.0)


def run_asset(sym, path):
    L2.M1 = path
    t  = L2.load2(TF, start_ms=0)
    a  = L2.A2(t)
    m1 = L2.load_m1_exit(start_ms=0)
    gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]

    tp2_cap = 2.25 if sym == "SOLUSDT" else 0.0
    df = run_system(a, gens, m1, TF, mode="routed", max_day=4, cooldown=3, tp2_cap_r=tp2_cap)
    s  = stats(df)

    o = df[df.oos]
    i = df[~df.oos]

    fade_o  = o[o.gestion == "fade"]  if "gestion" in o.columns else pd.DataFrame()
    trail_o = o[o.gestion == "trail"] if "gestion" in o.columns else pd.DataFrame()

    days_oos = max(1, (o.ts.max() - o.ts.min()) / 86_400_000) if len(o) > 1 else 1
    days_is  = max(1, (i.ts.max() - i.ts.min()) / 86_400_000) if len(i) > 1 else 1

    # DD real sobre equity OOS
    cap = CAPITAL; peak = CAPITAL; dd = 0.0
    for r in o.sort_values("ts").r.values:
        cap += EFF_RISK * r * SCALE2_MULT
        peak = max(peak, cap)
        dd = max(dd, (peak - cap) / peak)

    sharpe = o.r.mean() / (o.r.std() + 1e-9) * np.sqrt(252 * (len(o) / days_oos))

    return {
        "n_is":        len(i),
        "n_oos":       len(o),
        "tpd_is":      len(i) / days_is,
        "tpd_oos":     len(o) / days_oos,
        "avgR_is":     i.r.mean() if len(i) else 0,
        "avgR_oos":    s["oosA"],
        "wr_oos":      s["wr"],
        "dd_pct":      100 * dd,
        "sharpe":      sharpe,
        "pnl_yr":      pnl_year(df),
        "pnl_no_s2":   pnl_year(df, scale2=False),
        "fade_n":      len(fade_o),
        "fade_avgR":   fade_o.r.mean() if len(fade_o) else 0,
        "fade_wr":     100 * (fade_o.r > 0).mean() if len(fade_o) else 0,
        "trail_n":     len(trail_o),
        "trail_avgR":  trail_o.r.mean() if len(trail_o) else 0,
        "trail_wr":    100 * (trail_o.r > 0).mean() if len(trail_o) else 0,
        "gap":         s["oosA"] - (i.r.mean() if len(i) else 0),
    }


print("=" * 74)
print("  METRICAS FINALES — A+B enrutado | max_day=4 cool=3 | scaled +28%")
print("  $500 x10 | fill 65% | slippage 15% | OOS split rigido")
print("=" * 74)

results = {}
for sym, path in PARQUETS.items():
    if not path.exists():
        print(f"\n  {sym}: parquet no encontrado")
        continue
    print(f"\n  Cargando {sym}...", end=" ", flush=True)
    m = run_asset(sym, path)
    results[sym] = m
    print("OK")

    print(f"\n  {'='*40}")
    print(f"  {sym}")
    print(f"  {'='*40}")
    print(f"  {'Metrica':<26} {'IS':>9} {'OOS':>9}")
    print(f"  {'-'*46}")
    print(f"  {'n trades':<26} {m['n_is']:>9} {m['n_oos']:>9}")
    print(f"  {'t/dia':<26} {m['tpd_is']:>9.2f} {m['tpd_oos']:>9.2f}")
    print(f"  {'avgR':<26} {m['avgR_is']:>+9.3f} {m['avgR_oos']:>+9.3f}")
    print(f"  {'gap IS->OOS':<26} {'':>9} {m['gap']:>+9.3f}")
    print(f"  {'WR':<26} {'':>9} {m['wr_oos']:>8.1f}%")
    print(f"  {'DD% (capital real)':<26} {'':>9} {m['dd_pct']:>8.1f}%")
    print(f"  {'Sharpe (anualizado)':<26} {'':>9} {m['sharpe']:>+9.1f}")
    print(f"  {'PnL/ano sin scaled':<26} {'':>9} ${m['pnl_no_s2']:>7,.0f}")
    print(f"  {'PnL/ano + scaled 28%':<26} {'':>9} ${m['pnl_yr']:>7,.0f}")
    print(f"  -- fade (OOS) --")
    print(f"  {'  n / avgR / WR':<26} {m['fade_n']:>9} {m['fade_avgR']:>+9.3f} {m['fade_wr']:>5.1f}%")
    print(f"  -- trail (OOS) --")
    print(f"  {'  n / avgR / WR':<26} {m['trail_n']:>9} {m['trail_avgR']:>+9.3f} {m['trail_wr']:>5.1f}%")

if results:
    print(f"\n\n{'='*74}")
    print("  CONSOLIDADO 3 ACTIVOS")
    print(f"{'='*74}")
    print(f"  {'Simbolo':<10} {'avgR_oos':>9} {'WR':>6} {'DD%':>6} {'Sharpe':>8} {'t/dia':>6} {'PnL/ano':>9}")
    print(f"  {'-'*58}")
    total = 0
    for sym, m in results.items():
        print(f"  {sym:<10} {m['avgR_oos']:>+9.3f} {m['wr_oos']:>5.1f}% {m['dd_pct']:>5.1f}% {m['sharpe']:>+8.1f} {m['tpd_oos']:>6.2f} ${m['pnl_yr']:>7,.0f}")
        total += m['pnl_yr']
    print(f"  {'-'*58}")
    print(f"  {'TOTAL':<10} {'':>9} {'':>6} {'':>6} {'':>8} {'':>6} ${total:>7,.0f}/ano")
    print()
    print("  Scaling ($500 base):")
    for mult, cap, lev in [(1,500,10),(2,1000,10),(4,2000,10),(10,5000,5),(20,10000,3)]:
        print(f"    ${cap:>6} x{lev}  ->  ${total*mult:>9,.0f}/ano")
