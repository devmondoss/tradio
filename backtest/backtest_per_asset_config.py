"""
backtest_per_asset_config.py — Config optima por activo basada en hallazgos
============================================================================
BTC: H5+H21+H21s, margin=2bps, timeout=24h  (baseline solido)
ETH: H5+H21+H21s, margin=6bps, timeout=24h  (fill mas profundo ayuda)
SOL: H21+H21s (sin H5 — sobreajuste IS), margin=2bps, timeout=6h

Comparacion: config universal vs config por activo.

Uso: python -X utf8 backtest/backtest_per_asset_config.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _listas import OOS_MS, FEE_MAKER, FEE_TAKER
from _audit_mirror import gen_h21_short

ROOT = Path(__file__).parent.parent
TF   = 15
MK, TK = FEE_MAKER / 2, FEE_TAKER / 2

PARQUETS = {
    "BTC": ROOT / "data/bybit-perp/processed/btcusdt_perp_m1.parquet",
    "ETH": Path("E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet"),
    "SOL": Path("E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"),
}

# config universal (baseline actual)
UNIVERSAL = {
    "BTC": dict(gens_keys=["H5","H21","H21s"], margin=2.0, timeout_h=24),
    "ETH": dict(gens_keys=["H5","H21","H21s"], margin=2.0, timeout_h=24),
    "SOL": dict(gens_keys=["H5","H21","H21s"], margin=2.0, timeout_h=24),
}

# config por activo — basada en hallazgos empiricos
PER_ASSET = {
    "BTC": dict(gens_keys=["H5","H21","H21s"], margin=2.0, timeout_h=24),
    "ETH": dict(gens_keys=["H5","H21","H21s"], margin=6.0, timeout_h=24),
    "SOL": dict(gens_keys=["H21","H21s"],       margin=2.0, timeout_h=6),
}


def make_gens(keys):
    mapping = {
        "H5":   L2.gen_h5(),
        "H21":  L2.gen_h21(),
        "H21s": gen_h21_short(),
    }
    return [mapping[k] for k in keys]


def run_ab(a, gens, m1, tf_min=15, trail_atr=4.0,
           volfilter=True, timeout_min=24*60, cooldown=6, max_day=2,
           margin=2.0, stop_floor_pct=0.15, min_range=0.5):

    m1ts, m1h, m1l, m1c = m1
    bar_ms  = tf_min * 60_000
    atr_med = pd.Series(a.atr).rolling(500, min_periods=50).median().shift(1).values
    trades  = []

    for g in gens:
        cool = 0; dcount = {}
        for i in range(60, a.n - 1):
            if i < cool or a.atr[i] <= 0: continue
            if volfilter and not (np.isfinite(atr_med[i]) and a.atr[i] > atr_med[i]): continue
            d = int(a.day[i])
            if dcount.get(d, 0) >= max_day: continue

            for side, lvl, stop, tp1, tp2, kind in (g(a, i) or []):
                if not np.isfinite([lvl, stop, tp2]).all(): continue
                ref = a.c[i - 1]
                if side == "long"  and not (lvl < ref): continue
                if side == "short" and not (lvl > ref): continue
                if side == "long"  and not (a.l[i] <= lvl - margin / 1e4 * lvl): continue
                if side == "short" and not (a.h[i] >= lvl + margin / 1e4 * lvl): continue

                entry = lvl; atr0 = a.atr[i]
                if stop_floor_pct > 0:
                    mr = stop_floor_pct / 100.0 * entry
                    if abs(entry - stop) < mr:
                        stop = entry - mr if side == "long" else entry + mr
                risk = abs(entry - stop)
                if risk <= 0 or abs(tp2 - entry) / risk < 1.2: continue
                if min_range > 0 and tp1 is not None and 100 * abs(tp1 - entry) / entry < min_range: continue

                chop_here = str(a.reg[i]).lower() in ("chop","range","balance","consolidation")
                j0   = np.searchsorted(m1ts, a.ts[i] + bar_ms)
                jend = np.searchsorted(m1ts, a.ts[i] + bar_ms + timeout_min * 60_000)
                res  = None

                if chop_here:
                    cur = stop; realized = 0.0; rem = 1.0; f1 = False
                    p1 = 0.5 if tp1 else 0.0; reason = "timeout"
                    for j in range(j0, min(jend, len(m1ts))):
                        if side == "long":
                            if m1l[j] <= cur:
                                realized += rem * ((cur - entry) / risk)
                                reason = "be" if f1 else "stop"; break
                            if not f1 and tp1 and m1h[j] >= tp1:
                                realized += p1 * ((tp1 - entry) / risk)
                                rem -= p1; f1 = True; cur = entry
                            if m1h[j] >= tp2:
                                realized += rem * ((tp2 - entry) / risk)
                                reason = "target"; break
                        else:
                            if m1h[j] >= cur:
                                realized += rem * ((entry - cur) / risk)
                                reason = "be" if f1 else "stop"; break
                            if not f1 and tp1 and m1l[j] <= tp1:
                                realized += p1 * ((entry - tp1) / risk)
                                rem -= p1; f1 = True; cur = entry
                            if m1l[j] <= tp2:
                                realized += rem * ((entry - tp2) / risk)
                                reason = "target"; break
                    else:
                        jj = min(jend, len(m1ts)) - 1
                        if jj <= j0: continue
                        px = m1c[jj]
                        realized += rem * (((px - entry) if side == "long" else (entry - px)) / risk)
                    exit_s = MK if reason == "target" else TK
                    fee_r  = (MK + (MK * p1 if f1 else 0.0) + exit_s * rem) * entry / risk
                    res = realized - fee_r
                else:
                    fee_r = (MK + TK) * entry / risk
                    best = entry; trail = stop
                    for j in range(j0, min(jend, len(m1ts))):
                        if side == "long":
                            best  = max(best,  m1h[j])
                            trail = max(trail, best - trail_atr * atr0)
                            if m1l[j] <= trail:
                                res = (trail - entry) / risk - fee_r; break
                        else:
                            best  = min(best,  m1l[j])
                            trail = min(trail, best + trail_atr * atr0)
                            if m1h[j] >= trail:
                                res = (entry - trail) / risk - fee_r; break
                    if res is None:
                        jj = min(jend, len(m1ts)) - 1
                        if jj <= j0: continue
                        px  = m1c[jj]
                        res = ((px - entry) if side == "long" else (entry - px)) / risk - fee_r

                trades.append(dict(
                    ts=int(a.ts[i]), side=side, r=res,
                    oos=int(a.ts[i]) >= OOS_MS, kind=kind,
                ))
                cool = i + cooldown; dcount[d] = dcount.get(d, 0) + 1; break

    return pd.DataFrame(trades)


def stats(df):
    if len(df) == 0:
        return dict(n=0, wr=0, avgR=0, oosA=0, n_oos=0, dd=0, sharpe=0)
    o   = df[df.oos]
    cap = 500.0; peak = 500.0; dd = 0.0
    for r in df.sort_values("ts").r.values:
        cap += 5 * r; peak = max(peak, cap); dd = max(dd, (peak - cap) / peak)
    sh = df.r.mean() / (df.r.std() + 1e-9) * np.sqrt(len(df))
    return dict(n=len(df), wr=100*(df.r>0).mean(), avgR=df.r.mean(),
                oosA=o.r.mean() if len(o) else 0,
                n_oos=len(o), dd=100*dd, sharpe=sh)


def main():
    print(f"{'='*72}")
    print("  CONFIG UNIVERSAL vs PER-ASSET")
    print(f"{'='*72}")
    print(f"  {'Sym':<6} {'Config':<30} {'n':>5} {'n_oos':>6} | {'WR':>5} | {'avgR':>7} | {'OOS':>7} | {'DD':>5} | {'Sh':>5}")
    print("  " + "-"*72)

    summary = {}

    for sym in ["BTC", "ETH", "SOL"]:
        path = PARQUETS[sym]
        if not path.exists():
            print(f"  {sym}: SKIP"); continue

        L2.M1 = path
        t  = L2.load2(TF, start_ms=0)
        a  = L2.A2(t)
        m1 = L2.load_m1_exit(start_ms=0)

        results = {}
        for cfg_name, configs in [("Universal", UNIVERSAL), ("Per-asset", PER_ASSET)]:
            cfg  = configs[sym]
            gens = make_gens(cfg["gens_keys"])
            df   = run_ab(a, gens, m1,
                          margin=cfg["margin"],
                          timeout_min=cfg["timeout_h"] * 60)
            s    = stats(df)
            results[cfg_name] = s

            cfg_desc = f"{'+'.join(cfg['gens_keys'])} mg={cfg['margin']:.0f}bps t={cfg['timeout_h']}h"
            print(f"  {sym:<6} {cfg_desc:<30} {s['n']:>5} {s['n_oos']:>6} | "
                  f"{s['wr']:>4.1f}% | {s['avgR']:>+7.3f} | {s['oosA']:>+7.3f} | "
                  f"{s['dd']:>4.1f}% | {s['sharpe']:>+5.1f}")

        delta = results["Per-asset"]["oosA"] - results["Universal"]["oosA"]
        print(f"  {sym:<6} {'=> delta OOS per-asset vs universal':<30} {'':>5} {'':>6}   {'':>5}   {'':>7}   {delta:>+7.3f}")
        print()
        summary[sym] = results

    # resumen final comparativo
    print(f"{'='*72}")
    print("  RESUMEN FINAL")
    print(f"{'='*72}")
    print(f"  {'Sym':<6} {'Universal OOS':>14} {'Per-asset OOS':>14} {'Delta':>8} {'n_oos univ':>11} {'n_oos pa':>9}")
    print("  " + "-"*65)
    total_u_oos = 0; total_pa_oos = 0
    for sym, res in summary.items():
        u  = res["Universal"]
        pa = res["Per-asset"]
        delta = pa["oosA"] - u["oosA"]
        mark = " <-- mejor" if delta > 0 else ""
        print(f"  {sym:<6} {u['oosA']:>+14.3f} {pa['oosA']:>+14.3f} {delta:>+8.3f} {u['n_oos']:>11} {pa['n_oos']:>9}{mark}")
        total_u_oos  += u["oosA"]
        total_pa_oos += pa["oosA"]

    print(f"\n  Suma OOS avgR universal:  {total_u_oos:+.3f}")
    print(f"  Suma OOS avgR per-asset:  {total_pa_oos:+.3f}")
    print(f"  Ganancia total:           {total_pa_oos-total_u_oos:+.3f}R")

    print(f"\n  Configuraciones finales por activo:")
    for sym, cfg in PER_ASSET.items():
        print(f"  {sym}: gens={'+'.join(cfg['gens_keys'])}  margin={cfg['margin']:.0f}bps  timeout={cfg['timeout_h']}h")


if __name__ == "__main__":
    main()
