"""
backtest_levers.py — Bloque 1: CVD Slope + Timeout + Fill Margin en BTC/ETH/SOL
=================================================================================
Testa las 3 palancas pendientes de mayor impacto esperado:
  1. CVD Slope alineado (corr +0.24 en BTC)
  2. Timeout: 6h / 8h / 12h / 24h
  3. Fill margin: 2 / 4 / 6 / 8 bps

Cada combinacion se corre con los mismos params en los 3 activos.
Si el delta es positivo en los 3 -> filtro real, no overfit BTC.

Uso: python -X utf8 backtest/backtest_levers.py
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


# ── motor con todas las palancas parametrizables ──────────────────────────────
def run_ab(a, cvd_slope_arr, gens, m1, tf_min=15, trail_atr=4.0,
           volfilter=True, timeout_min=24*60, cooldown=6, max_day=2,
           margin=2.0, stop_floor_pct=0.15, min_range=0.5,
           cvd_filter=False):

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

                # ── PALANCA 1: CVD slope alineado ────────────────────────────
                if cvd_filter:
                    cs = cvd_slope_arr[i]
                    if not np.isfinite(cs): continue
                    if side == "long"  and cs <= 0: continue
                    if side == "short" and cs >= 0: continue

                entry = lvl; atr0 = a.atr[i]
                if stop_floor_pct > 0:
                    mr = stop_floor_pct / 100.0 * entry
                    if abs(entry - stop) < mr:
                        stop = entry - mr if side == "long" else entry + mr
                risk = abs(entry - stop)
                if risk <= 0 or abs(tp2 - entry) / risk < 1.2: continue
                if min_range > 0 and tp1 is not None and 100 * abs(tp1 - entry) / entry < min_range: continue

                chop_here = str(a.reg[i]).lower() in ("chop","range","balance","consolidation")
                use_fade  = chop_here
                j0   = np.searchsorted(m1ts, a.ts[i] + bar_ms)
                jend = np.searchsorted(m1ts, a.ts[i] + bar_ms + timeout_min * 60_000)
                res  = None

                if use_fade:
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


def row(label, s, base_oos=None):
    delta = f" ({s['oosA']-base_oos:+.3f})" if base_oos is not None else ""
    return (f"  {label:<35} n={s['n']:>4} n_oos={s['n_oos']:>4} | "
            f"WR {s['wr']:4.1f}% | avgR {s['avgR']:+.3f} | "
            f"OOS {s['oosA']:+.3f}{delta} | DD {s['dd']:4.1f}%")


def load_symbol(sym):
    path = PARQUETS[sym]
    L2.M1 = path
    t   = L2.load2(TF, start_ms=0)
    a   = L2.A2(t)
    m1  = L2.load_m1_exit(start_ms=0)
    cvd = t["cvd_slope"].values.astype(float)
    return a, cvd, m1


def run_symbol(sym):
    print(f"\n{'='*68}")
    print(f"  {sym}")
    print(f"{'='*68}")

    a, cvd, m1 = load_symbol(sym)
    gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]

    # baseline
    df_base = run_ab(a, cvd, gens, m1, margin=2.0, timeout_min=24*60, cvd_filter=False)
    s_base  = stats(df_base)
    print(row("BASELINE (margin=2bps, timeout=24h)", s_base))
    base_oos = s_base["oosA"]

    # ── PALANCA 1: CVD slope ─────────────────────────────────────────────────
    print(f"\n  --- CVD SLOPE FILTER ---")
    df_cvd = run_ab(a, cvd, gens, m1, margin=2.0, timeout_min=24*60, cvd_filter=True)
    s_cvd  = stats(df_cvd)
    elim   = s_base["n"] - s_cvd["n"]
    print(row(f"CVD alineado (elimina {elim} trades, {100*elim/max(s_base['n'],1):.0f}%)", s_cvd, base_oos))

    # ── PALANCA 2: TIMEOUT ───────────────────────────────────────────────────
    print(f"\n  --- TIMEOUT ---")
    for h in [6, 8, 12, 24]:
        df_t = run_ab(a, cvd, gens, m1, margin=2.0, timeout_min=h*60, cvd_filter=False)
        s_t  = stats(df_t)
        print(row(f"timeout {h:>2}h", s_t, base_oos))

    # ── PALANCA 3: FILL MARGIN ───────────────────────────────────────────────
    print(f"\n  --- FILL MARGIN ---")
    for bps in [2, 4, 6, 8, 10]:
        df_m = run_ab(a, cvd, gens, m1, margin=float(bps), timeout_min=24*60, cvd_filter=False)
        s_m  = stats(df_m)
        elim = s_base["n"] - s_m["n"]
        print(row(f"margin {bps}bps (elimina {elim}, {100*elim/max(s_base['n'],1):.0f}%)", s_m, base_oos))

    # ── COMBINACION MEJOR ────────────────────────────────────────────────────
    print(f"\n  --- MEJORES COMBINACIONES ---")
    combos = [
        ("CVD + timeout 8h  + margin 2bps", True,  8*60, 2.0),
        ("CVD + timeout 12h + margin 2bps", True, 12*60, 2.0),
        ("CVD + timeout 8h  + margin 4bps", True,  8*60, 4.0),
        ("CVD + timeout 12h + margin 4bps", True, 12*60, 4.0),
        ("     timeout 8h  + margin 4bps",  False, 8*60, 4.0),
        ("     timeout 12h + margin 4bps",  False,12*60, 4.0),
    ]
    best_oos = base_oos; best_label = "baseline"
    for label, cvdf, tmin, mg in combos:
        df_c = run_ab(a, cvd, gens, m1, margin=mg, timeout_min=tmin, cvd_filter=cvdf)
        s_c  = stats(df_c)
        elim = s_base["n"] - s_c["n"]
        print(row(f"{label} (-{100*elim/max(s_base['n'],1):.0f}%n)", s_c, base_oos))
        if s_c["oosA"] > best_oos and s_c["n_oos"] >= 20:
            best_oos = s_c["oosA"]; best_label = label

    print(f"\n  Mejor OOS para {sym}: '{best_label}' -> {best_oos:+.3f}R")
    return {"base": s_base, "cvd": s_cvd}


def main():
    results = {}
    for sym in ["BTC", "ETH", "SOL"]:
        r = run_symbol(sym)
        results[sym] = r

    # resumen final
    print(f"\n\n{'='*68}")
    print("  RESUMEN MULTIASSET — generaliza el CVD filter?")
    print(f"{'='*68}")
    print(f"  {'Sym':<6} {'Baseline OOS':>13} {'CVD OOS':>10} {'Delta':>8} {'n base':>8} {'n cvd':>8} {'% eliminado':>12}")
    print("  " + "-"*65)
    for sym, r in results.items():
        b = r["base"]; c = r["cvd"]
        elim_pct = 100 * (b["n"] - c["n"]) / max(b["n"], 1)
        print(f"  {sym:<6} {b['oosA']:>+13.3f} {c['oosA']:>+10.3f} "
              f"{c['oosA']-b['oosA']:>+8.3f} {b['n']:>8} {c['n']:>8} {elim_pct:>11.1f}%")

    generaliza = all(results[s]["cvd"]["oosA"] > results[s]["base"]["oosA"] for s in results)
    print(f"\n  CVD filter generaliza en los 3 activos: {'SI' if generaliza else 'NO — solo BTC'}")


if __name__ == "__main__":
    main()
