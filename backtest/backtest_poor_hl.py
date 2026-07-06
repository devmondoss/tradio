"""
backtest_poor_hl.py — Poor High/Low generator
==============================================
Poor High = sesión cerró dentro del X% superior de su rango (sin wick arriba).
           → mercado no terminó la subasta → continuación alcista esperada.
Poor Low  = sesión cerró dentro del X% inferior de su rango (sin wick abajo).
           → continuación bajista esperada.

Implementación: cuando la barra actual rompe el extremo de la sesión anterior
que hizo poor high/low → entrada con trail (continuación, no fade).

Variante A: solo trail
Variante B: routed (chop→fade en el nivel, trend→trail)

Uso: python -X utf8 backtest/backtest_poor_hl.py
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


# ── Precomputo: Poor High/Low diario ─────────────────────────────────────────

def compute_daily_stats(a, t):
    """
    Para cada día: calcula high, low, close, range y si es poor high/low.
    Returns: daily_info dict[day_id -> {high, low, close, poor_h, poor_l, end_idx}]
    """
    day_arr = a.day.astype(int)
    days = np.unique(day_arr)
    info = {}
    for d in days:
        mask = np.where(day_arr == d)[0]
        if len(mask) < 4:
            continue
        h = a.h[mask].max()
        l = a.l[mask].min()
        c = a.c[mask[-1]]  # close de la última barra del día
        rng = h - l
        if rng <= 0:
            continue
        # Poor high: close en el top X% del rango
        # Poor low:  close en el bottom X% del rango
        info[d] = dict(
            high=h, low=l, close=c, rng=rng,
            end_idx=int(mask[-1]),
            poor_h=(h - c) / rng,   # pequeño = poor high
            poor_l=(c - l) / rng,   # pequeño = poor low
        )
    return info


def make_gen_poor_hl(daily_info, day_arr, poor_tol=0.10, N_days=3):
    """
    Genera señales cuando el precio rompe el extremo de una sesión poor.

    poor_tol: close dentro del 10% del rango desde el extremo → poor high/low
    N_days:   mirar hasta N días atrás

    Señal long:  break por encima del high de una sesión poor-high anterior
    Señal short: break por debajo del low de una sesión poor-low anterior
    """
    day_arr = day_arr.astype(int)

    def g(a, i):
        if i < 100:
            return None
        cur_day = day_arr[i]
        ref = a.c[i - 1]
        results = []

        past_days = []
        seen = set()
        for j in range(i - 1, max(0, i - 700), -1):
            d = int(day_arr[j])
            if d == cur_day:
                continue
            if d not in seen:
                seen.add(d)
                past_days.append(d)
            if len(past_days) >= N_days:
                break

        for d in past_days:
            di = daily_info.get(d)
            if di is None:
                continue

            # ── POOR HIGH → long break del high anterior ──────────────────
            if di["poor_h"] <= poor_tol:
                lvl = di["high"]
                # precio actual justo debajo y la barra rompe arriba
                if ref < lvl and a.h[i] >= lvl:
                    stop = lvl - a.atr[i]  # stop 1ATR debajo del nivel
                    tp1, tp2 = L2.struct_target(a, i, "long", lvl)
                    if np.isfinite(tp2):
                        results.append(("long", lvl, stop, tp1, tp2, "poor_high"))

            # ── POOR LOW → short break del low anterior ───────────────────
            if di["poor_l"] <= poor_tol:
                lvl = di["low"]
                if ref > lvl and a.l[i] <= lvl:
                    stop = lvl + a.atr[i]
                    tp1, tp2 = L2.struct_target(a, i, "short", lvl)
                    if np.isfinite(tp2):
                        results.append(("short", lvl, stop, tp1, tp2, "poor_low"))

        return results if results else None

    return g


# ── Motor de backtest ────────────────────────────────────────────────────────

def run_ab(a, gens, m1, tf_min=15, trail_atr=4.0,
           volfilter=True, timeout_min=24*60, cooldown=6, max_day=2,
           margin=2.0, stop_floor_pct=0.15, min_range=0.5,
           force_trail=False):

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
                # Poor H/L: rompe el nivel = barra toca el extremo (distinto a fade)
                if "poor" in kind:
                    if side == "long"  and not (a.h[i] >= lvl): continue
                    if side == "short" and not (a.l[i] <= lvl): continue
                else:
                    if side == "long"  and not (a.l[i] <= lvl - margin / 1e4 * lvl): continue
                    if side == "short" and not (a.h[i] >= lvl + margin / 1e4 * lvl): continue
                entry = lvl; atr0 = a.atr[i]
                if stop_floor_pct > 0:
                    mr = stop_floor_pct / 100.0 * entry
                    if abs(entry - stop) < mr:
                        stop = entry - mr if side == "long" else entry + mr
                risk = abs(entry - stop)
                if risk <= 0 or abs(tp2 - entry) / risk < 1.2: continue
                # Poor H/L es un setup de breakout — gestión trail siempre
                chop_here = False if (force_trail or "poor" in kind) else \
                            str(a.reg[i]).lower() in ("chop","range","balance","consolidation")
                j0   = np.searchsorted(m1ts, a.ts[i] + bar_ms)
                jend = np.searchsorted(m1ts, a.ts[i] + bar_ms + timeout_min * 60_000)
                res  = None
                if chop_here:
                    cur = stop; realized = 0.0; rem = 1.0; f1 = False
                    p1 = 0.5 if tp1 else 0.0; reason = "timeout"
                    for j in range(j0, min(jend, len(m1ts))):
                        if side == "long":
                            if m1l[j]<=cur: realized+=rem*((cur-entry)/risk); reason="be" if f1 else "stop"; break
                            if not f1 and tp1 and m1h[j]>=tp1: realized+=p1*((tp1-entry)/risk); rem-=p1; f1=True; cur=entry
                            if m1h[j]>=tp2: realized+=rem*((tp2-entry)/risk); reason="target"; break
                        else:
                            if m1h[j]>=cur: realized+=rem*((entry-cur)/risk); reason="be" if f1 else "stop"; break
                            if not f1 and tp1 and m1l[j]<=tp1: realized+=p1*((entry-tp1)/risk); rem-=p1; f1=True; cur=entry
                            if m1l[j]<=tp2: realized+=rem*((entry-tp2)/risk); reason="target"; break
                    else:
                        jj=min(jend,len(m1ts))-1
                        if jj<=j0: continue
                        px=m1c[jj]; realized+=rem*(((px-entry) if side=="long" else (entry-px))/risk)
                    exit_s=MK if reason=="target" else TK
                    fee_r=(MK+(MK*p1 if f1 else 0.0)+exit_s*rem)*entry/risk
                    res=realized-fee_r
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
                trades.append(dict(ts=int(a.ts[i]),side=side,r=res,oos=int(a.ts[i])>=OOS_MS,kind=kind))
                cool=i+cooldown; dcount[d]=dcount.get(d,0)+1; break
    return pd.DataFrame(trades)


def stats(df):
    if len(df)==0: return dict(n=0,wr=0,avgR=0,oosA=0,n_oos=0,dd=0,sharpe=0)
    o=df[df.oos]; cap=500.0; peak=500.0; dd=0.0
    for r in df.sort_values("ts").r.values:
        cap+=5*r; peak=max(peak,cap); dd=max(dd,(peak-cap)/peak)
    sh=df.r.mean()/(df.r.std()+1e-9)*np.sqrt(len(df))
    return dict(n=len(df),wr=100*(df.r>0).mean(),avgR=df.r.mean(),
                oosA=o.r.mean() if len(o) else 0,n_oos=len(o),dd=100*dd,sharpe=sh)


def row(label, s, base_oos=None):
    delta=f" ({s['oosA']-base_oos:+.3f})" if base_oos is not None else ""
    return (f"  {label:<42} n={s['n']:>4} n_oos={s['n_oos']:>4} | "
            f"WR {s['wr']:4.1f}% | avgR {s['avgR']:+.3f} | OOS {s['oosA']:+.3f}{delta} | DD {s['dd']:4.1f}%")


def run_symbol(sym, path):
    if not path.exists():
        print(f"  {sym}: SKIP"); return None
    print(f"\n{'='*72}\n  {sym}\n{'='*72}")

    L2.M1 = path
    t  = L2.load2(TF, start_ms=0)
    a  = L2.A2(t)
    m1 = L2.load_m1_exit(start_ms=0)

    print("  Precomputando daily stats...", end=" ", flush=True)
    daily_info = compute_daily_stats(a, t)
    # distribución de poor_h/poor_l ratio
    ph = [v["poor_h"] for v in daily_info.values()]
    pl = [v["poor_l"] for v in daily_info.values()]
    print(f"{len(daily_info)} días")
    ph_arr = np.array(ph); pl_arr = np.array(pl)
    print(f"  poor_h ratio: median={np.median(ph_arr):.2f}  %<0.10={100*(ph_arr<0.10).mean():.1f}%  %<0.15={100*(ph_arr<0.15).mean():.1f}%")
    print(f"  poor_l ratio: median={np.median(pl_arr):.2f}  %<0.10={100*(pl_arr<0.10).mean():.1f}%  %<0.15={100*(pl_arr<0.15).mean():.1f}%")

    gens_base = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]
    df_base   = run_ab(a, gens_base, m1)
    s_base    = stats(df_base)
    base_oos  = s_base["oosA"]
    print(row("BASELINE (H5+H21+H21s)", s_base))

    print(f"\n  --- POOR HIGH/LOW (tolerancia 10%) — trail siempre ---")
    for tol in [0.05, 0.10, 0.15, 0.20]:
        gen_ph = make_gen_poor_hl(daily_info, a.day, poor_tol=tol, N_days=3)
        df_ph  = run_ab(a, [gen_ph], m1, force_trail=True)
        s_ph   = stats(df_ph)
        ph_days = sum(1 for v in daily_info.values() if v["poor_h"]<=tol or v["poor_l"]<=tol)
        print(row(f"poor_tol={tol:.2f} ({ph_days}d calific.)", s_ph))

    print(f"\n  --- COMBINACION MEJOR POOR + BASELINE ---")
    best_delta = 0.0; best_cfg = "baseline"
    for tol in [0.05, 0.10, 0.15]:
        gen_ph   = make_gen_poor_hl(daily_info, a.day, poor_tol=tol, N_days=3)
        df_comb  = run_ab(a, gens_base + [gen_ph], m1)
        s_comb   = stats(df_comb)
        delta    = s_comb["oosA"] - base_oos
        print(row(f"H5+H21+H21s + poor_tol={tol:.2f}", s_comb, base_oos))
        if delta > best_delta and s_comb["n_oos"] >= 20:
            best_delta = delta; best_cfg = f"tol={tol}"

    print(f"\n  Mejor combinación {sym}: {best_cfg}  delta={best_delta:+.3f}R")
    # standalone con mejor tol
    gen_best = make_gen_poor_hl(daily_info, a.day, poor_tol=0.10, N_days=3)
    df_best  = run_ab(a, [gen_best], m1, force_trail=True)
    s_best   = stats(df_best)
    if len(df_best) > 0:
        is_b  = df_best[~df_best.oos]
        oos_b = df_best[df_best.oos]
        print(f"  Standalone poor (tol=0.10):")
        print(f"    IS:  n={len(is_b):>4} WR={100*(is_b.r>0).mean():4.1f}% avgR={is_b.r.mean():+.3f}")
        print(f"    OOS: n={len(oos_b):>4} WR={100*(oos_b.r>0).mean():4.1f}% avgR={oos_b.r.mean():+.3f}  gap={oos_b.r.mean()-is_b.r.mean():+.3f}R")
        for k, g in df_best.groupby("kind"):
            print(f"    {k}: n={len(g):>4} WR={100*(g.r>0).mean():4.1f}% avgR={g.r.mean():+.3f}")

    gen_comb = make_gen_poor_hl(daily_info, a.day, poor_tol=0.10, N_days=3)
    df_comb  = run_ab(a, gens_base + [gen_comb], m1)
    s_comb   = stats(df_comb)
    return {"base": s_base, "poor": s_best, "combined": s_comb}


def main():
    results = {}
    for sym, path in PARQUETS.items():
        r = run_symbol(sym, path)
        if r: results[sym] = r

    print(f"\n\n{'='*72}\n  RESUMEN MULTIASSET — POOR HIGH/LOW\n{'='*72}")
    print(f"  {'Sym':<6} {'Baseline OOS':>13} {'Poor OOS':>10} {'Combined OOS':>13} {'Delta':>8}")
    print("  " + "-"*55)
    for sym, r in results.items():
        b=r["base"]; p=r["poor"]; c=r["combined"]
        print(f"  {sym:<6} {b['oosA']:>+13.3f} {p['oosA']:>+10.3f} {c['oosA']:>+13.3f} {c['oosA']-b['oosA']:>+8.3f}")
    print()
    for sym, r in results.items():
        verdict="ADD" if r["combined"]["oosA"]>r["base"]["oosA"]+0.05 and r["poor"]["n_oos"]>=10 else "SKIP"
        print(f"  {sym}: {verdict}  (poor n_oos={r['poor']['n_oos']}  delta={r['combined']['oosA']-r['base']['oosA']:+.3f}R)")

if __name__ == "__main__":
    main()
