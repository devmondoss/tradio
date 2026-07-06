"""
backtest_setup_quality.py — H21 toques >=3 + fill bar quieta (vr<umbral)
=========================================================================
Palancas de calidad del setup, no del filtro de mercado.

H21 min_touches: actualmente >=2. Con >=3 el nivel es mas fuerte.
Fill bar quieta: vr en la barra de fill < umbral -> precio llego al nivel
                 sin agresividad -> setup mas limpio.

Uso: python -X utf8 backtest/backtest_setup_quality.py
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


# ── gen_h21 con min_touches parametrizable ───────────────────────────────────
def gen_h21_mt(K=15, tol=0.002, min_touches=2):
    def g(a, i):
        if i < K: return
        win = a.fp_poc[i-K:i]; win = win[np.isfinite(win)]
        if len(win) < 3: return
        lvl = np.median(win)
        touches = np.sum(np.abs(a.l[i-K:i] - lvl) / lvl <= tol)
        if touches >= min_touches and a.c[i] > a.c[i-1] and abs(a.l[i] - lvl) / lvl <= tol:
            stop = lvl - 0.6 * a.atr[i]
            tp1, tp2 = L2.struct_target(a, i, "long", lvl)
            if np.isfinite(tp2):
                return [("long", lvl, stop, tp1, tp2, f"H21t{min_touches}")]
    return g


def gen_h21_short_mt(K=15, tol=0.002, min_touches=2):
    def g(a, i):
        if i < K: return
        win = a.fp_poc[i-K:i]; win = win[np.isfinite(win)]
        if len(win) < 3: return
        lvl = np.median(win)
        touches = np.sum(np.abs(a.h[i-K:i] - lvl) / lvl <= tol)
        if touches >= min_touches and a.c[i] < a.c[i-1] and abs(a.h[i] - lvl) / lvl <= tol:
            stop = lvl + 0.6 * a.atr[i]
            tp1, tp2 = L2.struct_target(a, i, "short", lvl)
            if np.isfinite(tp2):
                return [("short", lvl, stop, tp1, tp2, f"H21st{min_touches}")]
    return g


# ── motor con vr filter parametrizable ───────────────────────────────────────
def run_ab(a, vr_arr, gens, m1, tf_min=15, trail_atr=4.0,
           volfilter=True, timeout_min=24*60, cooldown=6, max_day=2,
           margin=2.0, stop_floor_pct=0.15, min_range=0.5,
           vr_max=None):

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

                # ── PALANCA: fill bar quieta ──────────────────────────────────
                if vr_max is not None:
                    vr_i = vr_arr[i]
                    if np.isfinite(vr_i) and vr_i > vr_max: continue

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
                    oos=int(a.ts[i]) >= OOS_MS, kind=kind, vr=vr_arr[i],
                ))
                cool = i + cooldown; dcount[d] = dcount.get(d, 0) + 1; break

    return pd.DataFrame(trades)


def stats(df):
    if len(df) == 0:
        return dict(n=0, wr=0, avgR=0, oosA=0, n_oos=0, dd=0)
    o   = df[df.oos]
    cap = 500.0; peak = 500.0; dd = 0.0
    for r in df.sort_values("ts").r.values:
        cap += 5 * r; peak = max(peak, cap); dd = max(dd, (peak - cap) / peak)
    return dict(n=len(df), wr=100*(df.r>0).mean(), avgR=df.r.mean(),
                oosA=o.r.mean() if len(o) else 0,
                n_oos=len(o), dd=100*dd)


def row(label, s, base_oos=None):
    delta = f" ({s['oosA']-base_oos:+.3f})" if base_oos is not None else ""
    elim_n = ""
    return (f"  {label:<40} n={s['n']:>4} n_oos={s['n_oos']:>4} | "
            f"WR {s['wr']:4.1f}% | avgR {s['avgR']:+.3f} | "
            f"OOS {s['oosA']:+.3f}{delta} | DD {s['dd']:4.1f}%")


def run_symbol(sym):
    path = PARQUETS[sym]
    if not path.exists():
        print(f"  SKIP {sym}"); return None

    print(f"\n{'='*68}")
    print(f"  {sym}")
    print(f"{'='*68}")

    L2.M1 = path
    t  = L2.load2(TF, start_ms=0)
    a  = L2.A2(t)
    m1 = L2.load_m1_exit(start_ms=0)
    vr_arr = t["vr"].values.astype(float)

    # vr stats en barras de trading
    print(f"  vr: median={np.nanmedian(vr_arr):.2f}  p75={np.nanpercentile(vr_arr,75):.2f}  p90={np.nanpercentile(vr_arr,90):.2f}")

    # baseline (toques=2, sin vr filter)
    gens_2 = [L2.gen_h5(), gen_h21_mt(min_touches=2), gen_h21_short_mt(min_touches=2)]
    df_base = run_ab(a, vr_arr, gens_2, m1)
    s_base  = stats(df_base)
    base_oos = s_base["oosA"]
    print(row("BASELINE (toques=2, sin vr filter)", s_base))

    # ── PALANCA 1: H21 toques >=3 ────────────────────────────────────────────
    print(f"\n  --- H21 MIN TOUCHES ---")
    for mt in [2, 3, 4]:
        gens_mt = [L2.gen_h5(), gen_h21_mt(min_touches=mt), gen_h21_short_mt(min_touches=mt)]
        df = run_ab(a, vr_arr, gens_mt, m1)
        s  = stats(df)
        elim = s_base["n"] - s["n"]
        print(row(f"toques >= {mt} (elimina {elim}, {100*elim/max(s_base['n'],1):.0f}%)", s, base_oos))

    # ── PALANCA 2: FILL BAR QUIETA ───────────────────────────────────────────
    print(f"\n  --- FILL BAR QUIETA (vr_max) ---")
    for vr_thr in [None, 2.0, 1.5, 1.2, 1.0]:
        df = run_ab(a, vr_arr, gens_2, m1, vr_max=vr_thr)
        s  = stats(df)
        elim = s_base["n"] - s["n"]
        label = f"vr_max={'None (base)' if vr_thr is None else f'{vr_thr:.1f}'} (-{100*elim/max(s_base['n'],1):.0f}%n)"
        print(row(label, s, base_oos))

    # ── COMBINACION: toques=3 + vr_max ───────────────────────────────────────
    print(f"\n  --- COMBINACION toques=3 + vr_max ---")
    gens_3 = [L2.gen_h5(), gen_h21_mt(min_touches=3), gen_h21_short_mt(min_touches=3)]
    best_oos = base_oos; best_label = "baseline"
    for vr_thr in [None, 2.0, 1.5, 1.2]:
        df = run_ab(a, vr_arr, gens_3, m1, vr_max=vr_thr)
        s  = stats(df)
        elim = s_base["n"] - s["n"]
        label = f"toques=3 + vr<={'None' if vr_thr is None else vr_thr} (-{100*elim/max(s_base['n'],1):.0f}%n)"
        print(row(label, s, base_oos))
        if s["oosA"] > best_oos and s["n_oos"] >= 15:
            best_oos = s["oosA"]; best_label = label

    print(f"\n  Mejor para {sym}: '{best_label}' -> OOS {best_oos:+.3f}R")
    return {"base": s_base, "best_oos": best_oos, "best_label": best_label}


def main():
    results = {}
    for sym in ["BTC", "ETH", "SOL"]:
        r = run_symbol(sym)
        if r: results[sym] = r

    print(f"\n\n{'='*68}")
    print("  RESUMEN MULTIASSET")
    print(f"{'='*68}")
    for sym, r in results.items():
        b = r["base"]
        print(f"  {sym}: baseline OOS={b['oosA']:+.3f}  |  mejor={r['best_oos']:+.3f}  ({r['best_label'].strip()})")


if __name__ == "__main__":
    main()
