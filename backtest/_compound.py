"""
_compound.py — Simulación con compounding real sobre los 3 activos.
1% riesgo por trade sobre capital corriente (no fijo $5).
Trades ordenados cronológicamente, 3 servicios simultáneos.
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _audit_mirror import gen_h21_short
from _strategy_ab import run_system

PARQUETS = {
    "BTCUSDT": Path(__file__).parent.parent / "data/bybit-perp/processed/btcusdt_perp_m1.parquet",
    "ETHUSDT": Path("E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet"),
    "SOLUSDT": Path("E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"),
}
TF          = 15
RISK_PCT    = 0.01       # 1% del capital corriente
FILL_RATE   = 0.65
SLIP_DISC   = 0.15
SCALE2_MULT = 1.28       # scaled entry +28%
CAPITAL_0   = 500.0      # capital inicial total del portafolio

# Factor efectivo por R ganado: fill * (1-slip) * scaled
EFF_FACTOR = FILL_RATE * (1 - SLIP_DISC) * SCALE2_MULT   # 0.704

# --- Recopilar todos los trades OOS de los 3 activos ---
all_oos = []

for sym, path in PARQUETS.items():
    if not path.exists():
        print(f"  SKIP {sym}")
        continue
    L2.M1 = path
    t   = L2.load2(TF, start_ms=0)
    a   = L2.A2(t)
    m1  = L2.load_m1_exit(start_ms=0)
    gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]
    tp2_cap = 2.25 if sym == "SOLUSDT" else 0.0
    df = run_system(a, gens, m1, TF, mode="routed", max_day=4, cooldown=3, tp2_cap_r=tp2_cap)
    oos = df[df.oos].copy()
    oos["sym"] = sym
    all_oos.append(oos)
    print(f"  {sym}: {len(oos)} trades OOS  avgR {oos.r.mean():+.3f}")

all_oos = pd.concat(all_oos).sort_values("ts").reset_index(drop=True)

# Ventana OOS: del primer al último trade
oos_start = pd.Timestamp(all_oos.ts.min(), unit="ms").date()
oos_end   = pd.Timestamp(all_oos.ts.max(), unit="ms").date()
days_oos  = max(1, (all_oos.ts.max() - all_oos.ts.min()) / 86_400_000)
print(f"\nOOS window: {oos_start} -> {oos_end}  ({days_oos:.0f}d)  {len(all_oos)} trades")

# --- Simulación compounding ---
# Cada trade usa RISK_PCT * capital_actual * EFF_FACTOR (ajuste fill/slip/scaled)
cap = CAPITAL_0
peak = cap
dd_peak = 0.0
equity = [cap]
monthly = {}

for _, row in all_oos.iterrows():
    risk_usd = cap * RISK_PCT * EFF_FACTOR
    cap += risk_usd * row.r
    peak = max(peak, cap)
    dd_peak = max(dd_peak, (peak - cap) / peak)
    equity.append(cap)
    m = pd.Timestamp(row.ts, unit="ms").to_period("M")
    monthly.setdefault(m, []).append(cap)

equity = np.array(equity)
final = equity[-1]
total_return = (final - CAPITAL_0) / CAPITAL_0 * 100
cagr = (final / CAPITAL_0) ** (365 / days_oos) - 1

# Sharpe sobre retornos diarios
eq_daily = pd.Series(equity).resample("D",
    on=pd.Series(pd.date_range(start=oos_start, periods=len(equity), freq="1min"))
).last().dropna() if False else None
# Simple: Sharpe sobre R por trade
r_series = all_oos.r.values
sharpe = r_series.mean() / (r_series.std() + 1e-9) * np.sqrt(252 * len(r_series) / days_oos)

print(f"\n{'='*56}")
print(f"  COMPOUNDING — $500 inicio, 1%/trade, 3 activos")
print(f"{'='*56}")
print(f"  Capital inicial    ${CAPITAL_0:>10,.2f}")
print(f"  Capital final      ${final:>10,.2f}")
print(f"  Retorno total      {total_return:>9.1f}%")
print(f"  CAGR               {100*cagr:>9.1f}%")
print(f"  DD max             {100*dd_peak:>9.1f}%")
print(f"  Sharpe             {sharpe:>+9.1f}")
print(f"  Trades OOS         {len(all_oos):>10}")
print(f"  t/dia promedio     {len(all_oos)/days_oos:>10.2f}")

# Evolución a distintos horizontes
print(f"\n  Proyeccion a distintos capitales iniciales (CAGR={100*cagr:.0f}%):")
for c0 in [500, 1_000, 2_000, 5_000, 10_000, 25_000]:
    c1 = c0 * (1 + cagr)
    c2 = c0 * (1 + cagr)**2
    c3 = c0 * (1 + cagr)**3
    print(f"    ${c0:>7,}  ->  1a ${c1:>9,.0f}  2a ${c2:>10,.0f}  3a ${c3:>11,.0f}")

# Mensual breakdown
print(f"\n  Evolucion mensual:")
months = sorted(monthly.keys())
prev = CAPITAL_0
for m in months:
    caps = monthly[m]
    end_cap = caps[-1]
    pct = (end_cap - prev) / prev * 100
    bar = "+" * int(abs(pct) / 5) if pct > 0 else "-" * int(abs(pct) / 5)
    print(f"    {m}  ${end_cap:>9,.0f}  {pct:>+7.1f}%  {bar}")
    prev = end_cap
