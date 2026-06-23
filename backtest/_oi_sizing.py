"""
_oi_sizing.py — Sizing diferencial por OI direction (BTC OOS).
En vez de filtrar (perder trades positivos), apostamos MAS cuando OI sube
y MENOS cuando OI baja. Frecuencia intacta, capital mejor asignado.
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _audit_mirror import gen_h21_short
from _strategy_ab import run_system
from _listas import OOS_MS

TF = 15
ROOT = Path(__file__).parent.parent
FILL_RATE   = 0.65
SLIP_DISC   = 0.15
SCALE2_MULT = 1.28
CAPITAL_0   = 500.0
BASE_RISK   = 0.01      # 1% base
EFF_FACTOR  = FILL_RATE * (1 - SLIP_DISC) * SCALE2_MULT   # 0.704

# ── Cargar trades OOS BTC ─────────────────────────────────────────────────────
L2.M1 = ROOT / "data/bybit-perp/processed/btcusdt_perp_m1.parquet"
t   = L2.load2(TF, start_ms=0)
a   = L2.A2(t)
m1  = L2.load_m1_exit(start_ms=0)
gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]
df  = run_system(a, gens, m1, TF, mode="routed", max_day=4, cooldown=3)
oos = df[df.oos].sort_values("ts").reset_index(drop=True)

# ── OI 5m ─────────────────────────────────────────────────────────────────────
oi = pd.read_parquet(ROOT / "data/bybit-perp/oi_5m.parquet").sort_values("ts_ms")
oi_ts  = oi.ts_ms.values.astype(np.int64)
oi_val = oi.open_interest.values

def oi_chg_pct(ts_ms, lb_ms=60*60_000):
    idx_now  = np.searchsorted(oi_ts, ts_ms, side="right") - 1
    idx_past = np.searchsorted(oi_ts, ts_ms - lb_ms, side="right") - 1
    if idx_now < 0 or idx_past < 0 or oi_val[idx_past] == 0:
        return 0.0
    return (oi_val[idx_now] - oi_val[idx_past]) / oi_val[idx_past] * 100

oos["oi_chg"] = oos.ts.apply(oi_chg_pct)

# ── Simulación compounding con sizing diferencial ─────────────────────────────
def sim(oi_up_mult, oi_down_mult, threshold=0.1, label=""):
    cap = CAPITAL_0; peak = cap; dd = 0.0; equity = [cap]
    for _, row in oos.iterrows():
        chg = row.oi_chg
        if chg > threshold:
            mult = oi_up_mult
        elif chg < -threshold:
            mult = oi_down_mult
        else:
            mult = 1.0
        risk_usd = cap * BASE_RISK * mult * EFF_FACTOR
        cap += risk_usd * row.r
        peak = max(peak, cap); dd = max(dd, (peak - cap) / peak)
        equity.append(cap)
    equity = np.array(equity)
    days = max(1, (oos.ts.iloc[-1] - oos.ts.iloc[0]) / 86_400_000)
    cagr = (equity[-1] / CAPITAL_0) ** (365 / days) - 1
    # Sharpe sobre R ponderados
    r_w = []
    for _, row in oos.iterrows():
        chg = row.oi_chg
        m = oi_up_mult if chg > threshold else (oi_down_mult if chg < -threshold else 1.0)
        r_w.append(row.r * m)
    r_w = np.array(r_w)
    sh = r_w.mean() / (r_w.std() + 1e-9) * np.sqrt(252 * len(r_w) / days)
    return dict(
        label     = label or f"up={oi_up_mult}x dn={oi_down_mult}x",
        final     = equity[-1],
        ret_pct   = (equity[-1] / CAPITAL_0 - 1) * 100,
        cagr_pct  = cagr * 100,
        dd_pct    = dd * 100,
        sharpe    = sh,
        n_up      = (oos.oi_chg > threshold).sum(),
        n_dn      = (oos.oi_chg < -threshold).sum(),
        n_flat    = ((oos.oi_chg >= -threshold) & (oos.oi_chg <= threshold)).sum(),
    )

configs = [
    (1.0, 1.0,  0.1, "baseline (flat 1x)"),
    (1.5, 0.5,  0.1, "up=1.5x  dn=0.5x"),
    (2.0, 0.5,  0.1, "up=2.0x  dn=0.5x"),
    (2.0, 0.25, 0.1, "up=2.0x  dn=0.25x"),
    (1.5, 0.0,  0.1, "up=1.5x  dn=skip"),
    (2.0, 0.0,  0.1, "up=2.0x  dn=skip"),
    (2.0, 0.5,  0.3, "up=2.0x  dn=0.5x  thr=0.3%"),
    (3.0, 0.25, 0.1, "up=3.0x  dn=0.25x"),
]

print("BTC OOS sizing diferencial por OI (1h change, threshold bps)")
print(f"Base: $500, 1%/trade, {len(oos)} trades, {(oos.ts.max()-oos.ts.min())/86_400_000:.0f}d")
print(f"OI dist: up={( oos.oi_chg> 0.1).sum()} flat={(oos.oi_chg.between(-0.1,0.1)).sum()} dn={(oos.oi_chg<-0.1).sum()}")
print()
print(f"  {'Config':<28} {'Final $':>9} {'Retorno':>8} {'DD%':>6} {'Sharpe':>7}  dist(up/flat/dn)")
print("  " + "-"*80)

results = []
for args in configs:
    r = sim(*args)
    results.append(r)
    print(f"  {r['label']:<28} ${r['final']:>8,.0f} {r['ret_pct']:>7.0f}% "
          f"{r['dd_pct']:>5.1f}% {r['sharpe']:>+7.1f}  "
          f"{r['n_up']}/{r['n_flat']}/{r['n_dn']}")

# ── Mejor config: desglose mensual ───────────────────────────────────────────
print()
best = [r for r in results if r['label'] != "baseline (flat 1x)"]
best.sort(key=lambda x: x['final'], reverse=True)
best_label = best[0]['label']
# Extraer up/dn mult del mejor
best_cfg = [c for c in configs if c[3] == best_label][0]
up_m, dn_m, thr, _ = best_cfg

print(f"Mejor config: {best_label}")
oos2 = oos.copy()
oos2["mult"] = oos2.oi_chg.apply(lambda x: up_m if x > thr else (dn_m if x < -thr else 1.0))
oos2["r_w"]  = oos2.r * oos2.mult
oos2["month"] = pd.to_datetime(oos2.ts, unit="ms").dt.to_period("M")

cap = CAPITAL_0
print(f"\n  Mensual (mejor config vs baseline):")
print(f"  {'Mes':<10} {'n':>4} {'avgR_w':>8} {'cap_mejor':>11} {'cap_base':>10}")
cap_base = CAPITAL_0
for m, g in oos2.groupby("month"):
    cap_b_start = cap_base
    cap_start   = cap
    for _, row in g.iterrows():
        cap_base += cap_base * BASE_RISK * EFF_FACTOR * row.r
        cap      += cap      * BASE_RISK * row.mult * EFF_FACTOR * row.r
    print(f"  {str(m):<10} {len(g):>4} {g.r_w.mean():>+8.3f} ${cap:>9,.0f} ${cap_base:>8,.0f}")
