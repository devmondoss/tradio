"""
_oi_funding_test.py — Test OI y funding rate como filtros de entrada (BTC).

Ideas del video:
  OI: buildup en nivel -> mas SL acumulados -> bounce mas fuerte
  Funding: negativo en longs (shorts pagan) = combustible alcista
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

# Cargar BTC
L2.M1 = ROOT / "data/bybit-perp/processed/btcusdt_perp_m1.parquet"
t   = L2.load2(TF, start_ms=0)
a   = L2.A2(t)
m1  = L2.load_m1_exit(start_ms=0)
gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]

df = run_system(a, gens, m1, TF, mode="routed", max_day=4, cooldown=3)
oos = df[df.oos].copy()
print(f"BTC OOS baseline: n={len(oos)}  avgR={oos.r.mean():+.3f}  WR={100*(oos.r>0).mean():.0f}%")

# ── Cargar OI (5m) ──────────────────────────────────────────────────────────
oi = pd.read_parquet(ROOT / "data/bybit-perp/oi_5m.parquet").sort_values("ts_ms").reset_index(drop=True)
oi_ts  = oi.ts_ms.values.astype(np.int64)
oi_val = oi.open_interest.values

def oi_at(ts_ms):
    idx = np.searchsorted(oi_ts, ts_ms, side="right") - 1
    return oi_val[idx] if 0 <= idx < len(oi_val) else np.nan

def oi_change_pct(ts_ms, lookback_ms=60*60_000):
    """% cambio OI en las ultimas lookback_ms milisegundas antes de ts."""
    now = oi_at(ts_ms)
    idx = np.searchsorted(oi_ts, ts_ms - lookback_ms, side="right") - 1
    past = oi_val[idx] if 0 <= idx < len(oi_val) else np.nan
    if np.isnan(past) or past == 0: return np.nan
    return (now - past) / past * 100

# ── Cargar Funding (8h) ──────────────────────────────────────────────────────
fr = pd.read_parquet(ROOT / "data/bybit-perp/funding.parquet").sort_values("ts_ms").reset_index(drop=True)
fr_ts  = fr.ts_ms.values.astype(np.int64)
fr_val = fr.funding_rate.values

def funding_at(ts_ms):
    idx = np.searchsorted(fr_ts, ts_ms, side="right") - 1
    return fr_val[idx] if 0 <= idx < len(fr_val) else np.nan

# ── Anotar cada trade OOS ────────────────────────────────────────────────────
oos["oi_now"]    = oos.ts.apply(oi_at)
oos["oi_chg_1h"] = oos.ts.apply(lambda t: oi_change_pct(t, 60*60_000))
oos["oi_chg_4h"] = oos.ts.apply(lambda t: oi_change_pct(t, 4*60*60_000))
oos["funding"]   = oos.ts.apply(funding_at)

# Funding favorable: neg = shorts pagan longs (bullish fuel)
# Para longs: funding < 0 -> favorable; para shorts: funding > 0 -> favorable
if "side" in oos.columns:
    oos["fund_fav"] = np.where(
        oos.side == "long",  oos.funding < 0,
        np.where(oos.side == "short", oos.funding > 0, False)
    )
else:
    # Sin side: usar funding negativo como proxy general (mercado bajo presión alcista)
    oos["fund_fav"] = oos.funding < 0

oos["fund_extreme"] = oos.funding.abs() > oos.funding.abs().quantile(0.75)

print(f"\nCobertura OI en OOS: {oos.oi_now.notna().mean():.0%}")
print(f"Cobertura funding en OOS: {oos.funding.notna().mean():.0%}")
print(f"OI cambio 1h: min={oos.oi_chg_1h.min():.2f}%  med={oos.oi_chg_1h.median():.2f}%  max={oos.oi_chg_1h.max():.2f}%")
print(f"Funding: neg={oos.fund_fav.mean():.0%} de trades  extremo={oos.fund_extreme.mean():.0%}")


def show(label, mask, base=oos):
    sub = base[mask]
    if len(sub) < 5:
        print(f"  {label:<40} n={len(sub):>3}  (insuficiente)")
        return
    print(f"  {label:<40} n={len(sub):>3}  avgR={sub.r.mean():>+.3f}  WR={100*(sub.r>0).mean():.0f}%  "
          f"vs_base={sub.r.mean()-base.r.mean():>+.3f}")


print(f"\n{'='*72}")
print(f"  FILTROS OI (cambio % en 1h antes de la entrada)")
print(f"{'='*72}")
show("baseline (todos)", pd.Series([True]*len(oos), index=oos.index))
show("OI subiendo >+0.1% 1h",  oos.oi_chg_1h >  0.1)
show("OI subiendo >+0.3% 1h",  oos.oi_chg_1h >  0.3)
show("OI estable (-0.1 a +0.1%)", oos.oi_chg_1h.between(-0.1, 0.1))
show("OI bajando <-0.1% 1h",   oos.oi_chg_1h < -0.1)
show("OI bajando <-0.3% 1h",   oos.oi_chg_1h < -0.3)

print(f"\n{'='*72}")
print(f"  FILTROS OI 4h")
print(f"{'='*72}")
show("OI subiendo >+0.5% 4h",  oos.oi_chg_4h >  0.5)
show("OI subiendo >+1.0% 4h",  oos.oi_chg_4h >  1.0)
show("OI bajando <-0.5% 4h",   oos.oi_chg_4h < -0.5)
show("OI estable 4h",           oos.oi_chg_4h.between(-0.5, 0.5))

print(f"\n{'='*72}")
print(f"  FILTROS FUNDING")
print(f"{'='*72}")
show("funding favorable (lado correcto)", oos.fund_fav)
show("funding desfavorable",             ~oos.fund_fav)
show("funding negativo (< 0)",           oos.funding < 0)
show("funding positivo (> 0)",           oos.funding > 0)
show("funding extremo (top 25%)",        oos.fund_extreme)
show("funding extremo + favorable",      oos.fund_extreme & oos.fund_fav)

print(f"\n{'='*72}")
print(f"  COMBINACIONES OI + FUNDING")
print(f"{'='*72}")
oi_up   = oos.oi_chg_1h > 0.1
oi_down = oos.oi_chg_1h < -0.1
show("OI sube + funding fav",   oi_up   & oos.fund_fav)
show("OI sube + funding desfav", oi_up  & ~oos.fund_fav)
show("OI baja + funding fav",   oi_down & oos.fund_fav)
show("OI baja + funding desfav", oi_down & ~oos.fund_fav)

# Por regimen
if "gestion" in oos.columns:
    print(f"\n{'='*72}")
    print(f"  POR GESTION — solo fade")
    print(f"{'='*72}")
    fade = oos[oos.gestion == "fade"]
    show("fade baseline", pd.Series([True]*len(fade), index=fade.index), base=fade)
    show("fade + OI subiendo >0.1%", fade.oi_chg_1h > 0.1, base=fade)
    show("fade + funding fav",       fade.fund_fav, base=fade)
    show("fade + OI up + fund fav",  (fade.oi_chg_1h > 0.1) & fade.fund_fav, base=fade)
    show("fade + OI down",           fade.oi_chg_1h < -0.1, base=fade)
    show("fade + funding desfav",    ~fade.fund_fav, base=fade)

print(f"\n  Distribucion funding en trades OOS:")
bins = [-0.02, -0.005, -0.001, 0, 0.001, 0.005, 0.02]
labels = ["<-0.5%","-0.5 a -0.1%","-0.1 a 0%","0 a +0.1%","+0.1 a +0.5%",">+0.5%"]
oos["fund_bin"] = pd.cut(oos.funding * 100, bins=bins, labels=labels)
for b, g in oos.groupby("fund_bin", observed=True):
    print(f"    {b:<18}  n={len(g):>3}  avgR={g.r.mean():>+.3f}  WR={100*(g.r>0).mean():.0f}%")
