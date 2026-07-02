"""
_portfolio_fade.py — Backtest de PORTFOLIO combinado (3 símbolos, timeline única, fade-only)
============================================================================================
Lo que el backtest por símbolo nunca midió:
  1. DD real de la cartera con los 3 corriendo a la vez (riesgo $5/trade fijo)
  2. Correlación de entradas/resultados entre símbolos (¿entran juntos y pierden juntos?)
  3. Concurrencia: cuántas posiciones abiertas simultáneas y si conviene cap global
Ventana: intersección de los 3 datasets (ETH/SOL arrancan 2025-06-21).
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

ROOT = Path(__file__).parent.parent
PARQUETS = {
    "BTCUSDT": ROOT / "data/bybit-perp/processed/btcusdt_perp_m1.parquet",
    "ETHUSDT": Path("E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet"),
    "SOLUSDT": Path("E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"),
}
TF = 15
RISK_USD = 5.0
CAP0 = 500.0


def load_trades():
    import _listas2 as L2
    from _strategy_ab import run_system
    from _audit_mirror import gen_h21_short
    out = []
    for sym, p in PARQUETS.items():
        L2.M1 = p
        t = L2.load2(TF, start_ms=0); a = L2.A2(t); m1 = L2.load_m1_exit(start_ms=0)
        gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]
        df = run_system(a, gens, m1, TF, mode="fade", max_day=4, cooldown=3)
        df["symbol"] = sym
        out.append(df)
        print(f"  {sym}: {len(df)} trades")
    df = pd.concat(out, ignore_index=True)
    # ventana común
    t0 = max(g.ts.min() for _, g in df.groupby("symbol"))
    t1 = min(g.ts.max() for _, g in df.groupby("symbol"))
    df = df[(df.ts >= t0) & (df.ts <= t1)].sort_values("ts").reset_index(drop=True)
    df["dt"] = pd.to_datetime(df.ts, unit="ms")
    df["close_ts"] = df.ts + df.hold_min * 60_000
    print(f"  ventana común: {df.dt.min().date()} -> {df.dt.max().date()}  n={len(df)}")
    return df


def equity_dd(df):
    cap = CAP0; peak = CAP0; dd = 0.0; dd_at = None
    for r in df.sort_values("close_ts").r.values:
        cap += RISK_USD * r
        peak = max(peak, cap)
        d = (peak - cap) / peak
        if d > dd: dd = d
    return cap, dd


def main():
    print("== cargando y corriendo fade-only por símbolo ==")
    df = load_trades()

    print("\n== 1. DD portfolio vs por símbolo ==")
    for sym, g in df.groupby("symbol"):
        cap, dd = equity_dd(g)
        print(f"  {sym}: netR {g.r.sum():+8.1f}  avgR {g.r.mean():+.3f}  DD {dd*100:5.2f}%  n={len(g)}")
    cap, dd = equity_dd(df)
    print(f"  PORTFOLIO: netR {df.r.sum():+8.1f}  avgR {df.r.mean():+.3f}  DD {dd*100:5.2f}%  n={len(df)}  (${CAP0:.0f} -> ${cap:,.0f})")

    print("\n== 2. correlación diaria de R entre símbolos ==")
    daily = df.groupby([df.dt.dt.date, "symbol"]).r.sum().unstack(fill_value=0.0)
    print(daily.corr().round(3).to_string())
    port_daily = daily.sum(axis=1)
    print(f"  peor día portfolio: {port_daily.min():+.1f}R ({port_daily.idxmin()})  |  p1: {port_daily.quantile(0.01):+.1f}R")
    print(f"  días negativos: {(port_daily<0).mean()*100:.0f}%")

    print("\n== 3. concurrencia (posiciones abiertas simultáneas) ==")
    events = []
    for _, t in df.iterrows():
        events.append((t.ts, 1)); events.append((t.close_ts, -1))
    ev = pd.DataFrame(events, columns=["ts", "d"]).sort_values("ts")
    ev["open"] = ev.d.cumsum()
    dist = ev.groupby("open").size()
    print(f"  max simultáneas: {ev.open.max()}")
    # R del trade condicionado a cuántas posiciones había abiertas al entrar
    opens_at_entry = []
    ev_ts = ev.ts.values; ev_open = ev.open.values
    for ts in df.ts.values:
        i = np.searchsorted(ev_ts, ts, side="left") - 1
        opens_at_entry.append(ev_open[i] if i >= 0 else 0)
    df["open_at_entry"] = opens_at_entry
    g = df.groupby(pd.cut(df.open_at_entry, [-1, 0, 1, 2, 99], labels=["0", "1", "2", "3+"]), observed=True)
    print(g.r.agg(n="size", avgR="mean", sumR="sum").round(3).to_string())

    print("\n== 4. entradas simultáneas multi-símbolo (misma barra M15) ==")
    df["bar15"] = df.ts // (15 * 60_000)
    sim = df.groupby("bar15").agg(nsym=("symbol", "nunique"), sumR=("r", "sum"), n=("r", "size"))
    g = sim.groupby("nsym").agg(bars=("n", "size"), trades=("n", "sum"), avgR_bar=("sumR", "mean"), sumR=("sumR", "sum"))
    print(g.round(2).to_string())
    multi = df[df.bar15.isin(sim[sim.nsym >= 2].index)]
    solo = df[df.bar15.isin(sim[sim.nsym == 1].index)]
    print(f"  avgR trades en barra multi-símbolo: {multi.r.mean():+.3f} (n={len(multi)})  vs solo: {solo.r.mean():+.3f} (n={len(solo)})")

    print("\n== 5. cap de concurrencia global (skip si ya hay K abiertas) ==")
    for K in [1, 2, 3, 4]:
        open_until = []  # close_ts de posiciones abiertas
        kept = []
        for _, t in df.sort_values("ts").iterrows():
            open_until = [c for c in open_until if c > t.ts]
            if len(open_until) < K:
                kept.append(t.r); open_until.append(t.close_ts)
        kept = pd.Series(kept)
        # DD del capado
        cap_ = CAP0; peak = CAP0; ddk = 0.0
        for r in kept.values:
            cap_ += RISK_USD * r; peak = max(peak, cap_); ddk = max(ddk, (peak - cap_) / peak)
        print(f"  K={K}: n={len(kept):5d}  netR {kept.sum():+8.1f}  avgR {kept.mean():+.3f}  DD {ddk*100:5.2f}%")
    print(f"  sin cap: n={len(df):5d}  netR {df.r.sum():+8.1f}  avgR {df.r.mean():+.3f}  DD {dd*100:5.2f}%")


if __name__ == "__main__":
    main()
