"""
backtest_footprint_full.py — filtro bar_delta_at_fill en los 3 activos
======================================================================
BTC: tick data exacto (raw_trades/)
ETH/SOL: M1 delta del bar que coincide con el inicio de la barra M15 señal
         (fill ocurre en primer ~4% de ticks ≈ primeros 36s → M1 es proxy directo)

Configs per-asset (igual que backtest_per_asset_config.py):
  BTC: H5+H21+H21s, margin=2bps, timeout=24h
  ETH: H5+H21+H21s, margin=6bps, timeout=24h
  SOL: H21+H21s (sin H5), margin=2bps, timeout=6h

Uso: python -X utf8 backtest/backtest_footprint_full.py
"""
import sys, warnings
from pathlib import Path
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _listas import OOS_MS, FEE_MAKER, FEE_TAKER
from _audit_mirror import gen_h21_short

ROOT     = Path(__file__).parent.parent
BAR_MS   = 15 * 60_000
MK, TK   = FEE_MAKER / 2, FEE_TAKER / 2
TICK_DIR = ROOT / "data/bybit-perp/raw_trades"

ASSETS = {
    "BTC": dict(
        m1_path  = ROOT / "data/bybit-perp/processed/btcusdt_perp_m1.parquet",
        margin   = 2.0,
        timeout  = 24 * 60,
        use_h5   = True,
        tick_dir = TICK_DIR,
    ),
    "ETH": dict(
        m1_path  = Path("E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet"),
        margin   = 6.0,
        timeout  = 24 * 60,
        use_h5   = True,
    ),
    "SOL": dict(
        m1_path  = Path("E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"),
        margin   = 2.0,
        timeout  = 6 * 60,
        use_h5   = False,
    ),
}

# ── Motor de backtest ─────────────────────────────────────────────────────────

def run_ab(a, gens, m1, timeout_min=24*60, trail_atr=4.0, cooldown=6,
           max_day=2, margin=2.0, stop_floor_pct=0.15, min_range=0.5):
    m1ts, m1h, m1l, m1c = m1
    bar_ms  = BAR_MS
    atr_med = pd.Series(a.atr).rolling(500, min_periods=50).median().shift(1).values
    trades  = []
    for g in gens:
        cool = 0; dcount = {}
        for i in range(60, a.n - 1):
            if i < cool or a.atr[i] <= 0: continue
            if not (np.isfinite(atr_med[i]) and a.atr[i] > atr_med[i]): continue
            d = int(a.day[i])
            if dcount.get(d, 0) >= max_day: continue
            for side, lvl, stop, tp1, tp2, kind in (g(a, i) or []):
                if not np.isfinite([lvl, stop, tp2]).all(): continue
                ref = a.c[i - 1]
                if side == "long"  and not (lvl < ref): continue
                if side == "short" and not (lvl > ref): continue
                mf = margin / 1e4
                if side == "long"  and not (a.l[i] <= lvl * (1.0 - mf)): continue
                if side == "short" and not (a.h[i] >= lvl * (1.0 + mf)): continue
                entry = lvl; atr0 = a.atr[i]
                if stop_floor_pct > 0:
                    mr = stop_floor_pct / 100.0 * entry
                    if abs(entry - stop) < mr:
                        stop = entry - mr if side == "long" else entry + mr
                risk = abs(entry - stop)
                if risk <= 0 or abs(tp2 - entry) / risk < 1.2: continue
                if tp1 is not None and 100 * abs(tp1 - entry) / entry < min_range: continue
                chop = str(a.reg[i]).lower() in ("chop","range","balance","consolidation")
                j0   = np.searchsorted(m1ts, a.ts[i] + bar_ms)
                jend = np.searchsorted(m1ts, a.ts[i] + bar_ms + timeout_min * 60_000)
                res  = None
                if chop:
                    cur = stop; realized = 0.0; rem = 1.0; f1 = False
                    p1 = 0.5 if tp1 else 0.0; reason = "timeout"
                    for j in range(j0, min(jend, len(m1ts))):
                        if side == "long":
                            if m1l[j] <= cur: realized += rem*((cur-entry)/risk); reason="be" if f1 else "stop"; break
                            if not f1 and tp1 and m1h[j]>=tp1: realized+=p1*((tp1-entry)/risk); rem-=p1; f1=True; cur=entry
                            if m1h[j]>=tp2: realized+=rem*((tp2-entry)/risk); reason="target"; break
                        else:
                            if m1h[j]>=cur: realized+=rem*((entry-cur)/risk); reason="be" if f1 else "stop"; break
                            if not f1 and tp1 and m1l[j]<=tp1: realized+=p1*((entry-tp1)/risk); rem-=p1; f1=True; cur=entry
                            if m1l[j]<=tp2: realized+=rem*((entry-tp2)/risk); reason="target"; break
                    else:
                        jj = min(jend, len(m1ts)) - 1
                        if jj <= j0: continue
                        px = m1c[jj]; realized += rem*(((px-entry) if side=="long" else (entry-px))/risk)
                    exit_s = MK if reason=="target" else TK
                    fee_r  = (MK + (MK*p1 if f1 else 0.0) + exit_s*rem) * entry / risk
                    res = realized - fee_r
                else:
                    fee_r=(MK+TK)*entry/risk; best=entry; trail=stop
                    for j in range(j0, min(jend, len(m1ts))):
                        if side=="long":
                            best=max(best,m1h[j]); trail=max(trail,best-trail_atr*atr0)
                            if m1l[j]<=trail: res=(trail-entry)/risk-fee_r; break
                        else:
                            best=min(best,m1l[j]); trail=min(trail,best+trail_atr*atr0)
                            if m1h[j]>=trail: res=(entry-trail)/risk-fee_r; break
                    if res is None:
                        jj=min(jend,len(m1ts))-1
                        if jj<=j0: continue
                        px=m1c[jj]; res=((px-entry) if side=="long" else (entry-px))/risk-fee_r
                trades.append(dict(bar_ts=int(a.ts[i]), side=side, lvl=lvl, atr=atr0,
                                   r=res, oos=int(a.ts[i])>=OOS_MS, kind=kind,
                                   regime="chop" if chop else "trend"))
                cool=i+cooldown; dcount[d]=dcount.get(d,0)+1; break
    return pd.DataFrame(trades)


# ── Delta desde ticks BTC ─────────────────────────────────────────────────────

_tick_cache: dict = {}

def load_ticks(date_str):
    if date_str not in _tick_cache:
        p = TICK_DIR / f"{date_str}.parquet"
        if p.exists():
            df = pq.read_table(p, columns=["ts_ms","price","size","side"]).to_pandas()
            df["size"] = df["size"].astype(float)
            df["is_buy"] = df["side"] == "Buy"
            _tick_cache[date_str] = df
        else:
            _tick_cache[date_str] = pd.DataFrame()
    return _tick_cache[date_str]


def tick_delta_at_fill(bar_ts_ms, level, side, atr, margin):
    import datetime
    bar_end = bar_ts_ms + BAR_MS
    d0 = datetime.datetime.fromtimestamp(bar_ts_ms/1000, tz=datetime.timezone.utc).strftime("%Y-%m-%d")
    d1 = datetime.datetime.fromtimestamp(bar_end/1000,   tz=datetime.timezone.utc).strftime("%Y-%m-%d")
    t0 = load_ticks(d0)
    ticks = t0 if d0 == d1 else pd.concat([t0, load_ticks(d1)], ignore_index=True)
    if len(ticks) == 0:
        return np.nan, np.nan

    mask = (ticks.ts_ms >= bar_ts_ms) & (ticks.ts_ms < bar_end)
    bt = ticks[mask].reset_index(drop=True)
    if len(bt) == 0:
        return np.nan, np.nan

    mf = margin / 1e4
    px = bt["price"].values
    if side == "long":
        fill_mask = px <= level * (1.0 - mf)
    else:
        fill_mask = px >= level * (1.0 + mf)
    fill_idx = int(np.argmax(fill_mask)) if fill_mask.any() else len(bt) - 1
    sub = bt.iloc[:fill_idx + 1]
    delta = sub.loc[sub.is_buy, "size"].sum() - sub.loc[~sub.is_buy, "size"].sum()
    return float(delta), float(delta / atr) if atr > 0 else np.nan


# ── Delta desde M1 ────────────────────────────────────────────────────────────

def build_m1_delta_index(m1_path):
    """Carga ts_ms → delta del M1 parquet como índice rápido."""
    df = pq.read_table(m1_path, columns=["ts_ms","delta"]).to_pandas()
    df = df.set_index("ts_ms")
    return df["delta"]


# ── Stats ─────────────────────────────────────────────────────────────────────

def stats(df):
    if len(df) == 0: return dict(n=0, wr=0, avgR=0, oosA=0, n_oos=0, dd=0)
    o = df[df.oos]
    cap=500.0; peak=500.0; dd=0.0
    for r in df.sort_values("bar_ts").r.values:
        cap+=5*r; peak=max(peak,cap); dd=max(dd,(peak-cap)/peak)
    return dict(n=len(df), wr=100*(df.r>0).mean(), avgR=df.r.mean(),
                oosA=o.r.mean() if len(o) else 0, n_oos=len(o), dd=100*dd)


def row(label, s, base_oos=None):
    d = f" ({s['oosA']-base_oos:+.3f})" if base_oos is not None else ""
    return (f"  {label:<40} n={s['n']:>4} n_oos={s['n_oos']:>4} | "
            f"WR {s['wr']:4.1f}% | avgR {s['avgR']:+.3f} | OOS {s['oosA']:+.3f}{d} | DD {s['dd']:4.1f}%")


# ── Por activo ────────────────────────────────────────────────────────────────

def run_symbol(sym, cfg):
    print(f"\n{'='*68}\n  {sym}\n{'='*68}")
    m1_path = cfg["m1_path"]
    if not m1_path.exists():
        print(f"  SKIP — {m1_path} no existe"); return None

    L2.M1 = m1_path
    t  = L2.load2(15, start_ms=0)
    a  = L2.A2(t)
    m1 = L2.load_m1_exit(start_ms=0)

    gens = []
    if cfg["use_h5"]: gens.append(L2.gen_h5())
    gens += [L2.gen_h21(), gen_h21_short()]

    df = run_ab(a, gens, m1, timeout_min=cfg["timeout"], margin=cfg["margin"])
    s_base = stats(df)
    print(row("BASELINE (per-asset config)", s_base))

    # ── Añadir delta ──────────────────────────────────────────────────────────
    use_ticks = sym == "BTC" and TICK_DIR.exists()

    if use_ticks:
        print(f"  Computando tick delta para {len(df)} trades BTC...", flush=True)
        deltas, deltas_norm = [], []
        for i, r in enumerate(df.itertuples(), 1):
            if i % 100 == 0: print(f"    {i}/{len(df)}...", flush=True)
            d, dn = tick_delta_at_fill(r.bar_ts, r.lvl, r.side, r.atr, cfg["margin"])
            deltas.append(d); deltas_norm.append(dn)
        df["bar_delta"]      = deltas
        df["bar_delta_norm"] = deltas_norm
        df = df.dropna(subset=["bar_delta"])
        print(f"  Tick delta: {len(df)} trades con datos")
    else:
        # M1 delta lookup
        print(f"  Cargando M1 delta index...", flush=True)
        m1_delta = build_m1_delta_index(m1_path)
        tick_start_ms = int(pd.Timestamp("2025-06-19").timestamp() * 1000)
        # fill bar_delta desde M1
        def get_m1d(ts):
            # buscar el M1 bar más cercano (≤ ts)
            idx = m1_delta.index.searchsorted(ts, side="right") - 1
            if idx < 0 or idx >= len(m1_delta): return np.nan
            return float(m1_delta.iloc[idx])
        df["bar_delta"] = df["bar_ts"].apply(get_m1d)
        df["bar_delta_norm"] = df["bar_delta"] / df["atr"]
        df = df.dropna(subset=["bar_delta"])
        print(f"  M1 delta: {len(df)} trades con datos")

    # ── Filtro adverso ────────────────────────────────────────────────────────
    df["delta_favor"] = ((df.side == "long")  & (df.bar_delta > 0)) | \
                        ((df.side == "short") & (df.bar_delta < 0))

    s_adv = stats(df[~df.delta_favor])
    s_fav = stats(df[df.delta_favor])
    s_all = stats(df)  # baseline en el mismo subconjunto con delta disponible

    print(row("Baseline (subconjunto con delta)", s_all))
    print(row("  → ADVERSO only (filtro ON)",     s_adv, s_all["oosA"]))
    print(row("  → FAVORABLE only (skip)",        s_fav, s_all["oosA"]))

    n_adv = (~df.delta_favor).sum()
    n_fav = df.delta_favor.sum()
    print(f"  Split: {n_adv} adverso ({100*n_adv/len(df):.0f}%)  |  {n_fav} favorable ({100*n_fav/len(df):.0f}%)")

    # ── Quintiles delta_norm ──────────────────────────────────────────────────
    print("\n  Quintiles delta_norm:")
    df["q"] = pd.qcut(df.bar_delta_norm, 5, labels=["Q1(−−)","Q2(−)","Q3","Q4(+)","Q5(++)"])
    for q, g in df.groupby("q", observed=True):
        oos_g = g[g.oos]
        oos_r = oos_g.r.mean() if len(oos_g) else np.nan
        print(f"    {str(q):<10} n={len(g):>4}  avgR={g.r.mean():+.4f}  OOS={oos_r:+.4f}")

    corr = df[["bar_delta_norm","r"]].corr().iloc[0,1]
    print(f"\n  Pearson(delta_norm, result_r) = {corr:+.4f}")

    return dict(base=s_base, all_delta=s_all, adverso=s_adv, favorable=s_fav)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    results = {}
    for sym, cfg in ASSETS.items():
        r = run_symbol(sym, cfg)
        if r: results[sym] = r

    print(f"\n\n{'='*68}")
    print("  RESUMEN MULTIASSET — FILTRO DELTA ADVERSO")
    print(f"{'='*68}")
    print(f"  {'Sym':<5} {'Base OOS':>9} {'All(delta)':>11} {'Adverso OOS':>12} {'Delta':>8} {'N kept%':>9}")
    print("  " + "-"*58)
    for sym, r in results.items():
        b = r["base"]; a = r["all_delta"]; adv = r["adverso"]
        n_pct = 100 * adv["n"] / a["n"] if a["n"] else 0
        print(f"  {sym:<5} {b['oosA']:>+9.3f} {a['oosA']:>+11.3f} {adv['oosA']:>+12.3f} {adv['oosA']-a['oosA']:>+8.3f} {n_pct:>8.0f}%")

    print()
    total_delta = sum(r["adverso"]["oosA"] - r["all_delta"]["oosA"] for r in results.values())
    print(f"  Ganancia total OOS con filtro adverso: {total_delta:+.3f}R")


if __name__ == "__main__":
    main()
