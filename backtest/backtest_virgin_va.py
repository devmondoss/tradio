"""
backtest_virgin_va.py — Virgin VAH/VAL generator
=================================================
Virgin VAH = VAH de un día anterior que el precio nunca volvió a tocar desde abajo.
Virgin VAL = VAL de un día anterior que el precio nunca volvió a tocar desde arriba.

Misma arquitectura que naked_poc: precomputo O(n×D), chequeo O(1) en el gen.
Señal solo cuando ESTA barra es la primera que toca el nivel.

Uso: python -X utf8 backtest/backtest_virgin_va.py
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


# ── Precomputo niveles diarios VAH/VAL ───────────────────────────────────────

def compute_daily_va(t, a):
    """
    Para cada día: extrae el VAH y VAL del cierre de la última barra del día.
    Usa las columnas vp_vah/vp_val del parquet (rolling VA sobre ventana 96 bars).
    Returns: day_end_idx, daily_vah, daily_val
    """
    day_arr   = a.day.astype(int)
    vah_arr   = t["vp_vah"].values.astype(float) if "vp_vah" in t.columns else np.full(a.n, np.nan)
    val_arr   = t["vp_val"].values.astype(float) if "vp_val" in t.columns else np.full(a.n, np.nan)

    days_unique   = np.unique(day_arr)
    day_end_idx   = {}
    daily_vah_map = {}
    daily_val_map = {}

    for d in days_unique:
        mask = np.where(day_arr == d)[0]
        if len(mask) == 0:
            continue
        last = int(mask[-1])
        day_end_idx[d]   = last
        daily_vah_map[d] = float(vah_arr[last])
        daily_val_map[d] = float(val_arr[last])

    return day_end_idx, daily_vah_map, daily_val_map


def precompute_revisit(level_map, day_end_idx, h_arr, l_arr, n):
    """
    Para cada día d con nivel lv:
    Encuentra el primer bar donde precio tocó lv tras el cierre de ese día.
    """
    revisit_idx = {}
    for d, lv in level_map.items():
        if not np.isfinite(lv) or lv <= 0 or d not in day_end_idx:
            revisit_idx[d] = 0
            continue
        start = day_end_idx[d] + 1
        if start >= n:
            revisit_idx[d] = n + 1
            continue
        mask = (l_arr[start:] <= lv) & (h_arr[start:] >= lv)
        nz   = np.where(mask)[0]
        revisit_idx[d] = int(start + nz[0]) if len(nz) > 0 else (n + 1)
    return revisit_idx


def make_gen_virgin_va(daily_vah_map, daily_val_map, day_end_idx,
                       rev_vah, rev_val, day_arr, N_days=10):
    """
    Señal cuando ESTA barra es la primera que toca un Virgin VAH o VAL.

    VAH resistencia → SHORT (precio retorna desde abajo, primer toque = resistencia)
    VAL soporte    → LONG  (precio retorna desde arriba, primer toque = soporte)
    """
    day_arr = day_arr.astype(int)

    def g(a, i):
        if i < 100:
            return None
        cur_day = day_arr[i]
        ref     = a.c[i - 1]
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
            # ── Virgin VAL → LONG (soporte desde arriba) ──────────────────
            val = daily_val_map.get(d)
            if val and np.isfinite(val) and rev_val.get(d, -1) == i:
                if val < ref:  # precio actual por encima del VAL
                    stop = val - 0.6 * a.atr[i]
                    tp1, tp2 = L2.struct_target(a, i, "long", val)
                    if np.isfinite(tp2):
                        results.append(("long", val, stop, tp1, tp2, "virgin_val"))

            # ── Virgin VAH → SHORT (resistencia desde abajo) ───────────────
            vah = daily_vah_map.get(d)
            if vah and np.isfinite(vah) and rev_vah.get(d, -1) == i:
                if vah > ref:  # precio actual por debajo del VAH
                    stop = vah + 0.6 * a.atr[i]
                    tp1, tp2 = L2.struct_target(a, i, "short", vah)
                    if np.isfinite(tp2):
                        results.append(("short", vah, stop, tp1, tp2, "virgin_vah"))

        return results if results else None

    return g


# ── Motor de backtest ────────────────────────────────────────────────────────

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
                        px = m1c[jj]; realized+=rem*(((px-entry) if side=="long" else (entry-px))/risk)
                    exit_s = MK if reason=="target" else TK
                    fee_r  = (MK+(MK*p1 if f1 else 0.0)+exit_s*rem)*entry/risk
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
    delta = f" ({s['oosA']-base_oos:+.3f})" if base_oos is not None else ""
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

    print("  Precomputando VAH/VAL diarios...", end=" ", flush=True)
    day_end_idx, daily_vah_map, daily_val_map = compute_daily_va(t, a)
    print(f"{len(day_end_idx)} días")

    rev_vah = precompute_revisit(daily_vah_map, day_end_idx, a.h, a.l, a.n)
    rev_val = precompute_revisit(daily_val_map, day_end_idx, a.h, a.l, a.n)

    gen_va = make_gen_virgin_va(daily_vah_map, daily_val_map, day_end_idx,
                                rev_vah, rev_val, a.day, N_days=10)
    gens_base = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]

    df_base = run_ab(a, gens_base, m1)
    s_base  = stats(df_base)
    base_oos = s_base["oosA"]
    print(row("BASELINE (H5+H21+H21s)", s_base))

    print(f"\n  --- VIRGIN VAH/VAL ---")
    df_va = run_ab(a, [gen_va], m1)
    s_va  = stats(df_va)
    print(row("Virgin VAH/VAL standalone", s_va))
    if len(df_va) > 0:
        is_va  = df_va[~df_va.oos]
        oos_va = df_va[df_va.oos]
        print(f"    IS:  n={len(is_va):>4} WR={100*(is_va.r>0).mean():4.1f}% avgR={is_va.r.mean():+.3f}")
        print(f"    OOS: n={len(oos_va):>4} WR={100*(oos_va.r>0).mean():4.1f}% avgR={oos_va.r.mean():+.3f}  gap={oos_va.r.mean()-is_va.r.mean():+.3f}R")
        for side, g in df_va.groupby("side"):
            print(f"    {side}: n={len(g):>4} WR={100*(g.r>0).mean():4.1f}% avgR={g.r.mean():+.3f}")
        for kind, g in df_va.groupby("kind"):
            print(f"    {kind}: n={len(g):>4} WR={100*(g.r>0).mean():4.1f}% avgR={g.r.mean():+.3f}")

    print(f"\n  --- BASELINE + VIRGIN VA ---")
    df_comb = run_ab(a, gens_base + [gen_va], m1)
    s_comb  = stats(df_comb)
    print(row("H5+H21+H21s+VirginVA", s_comb, base_oos))

    return {"base": s_base, "va": s_va, "combined": s_comb}


def main():
    results = {}
    for sym, path in PARQUETS.items():
        r = run_symbol(sym, path)
        if r: results[sym] = r

    print(f"\n\n{'='*72}\n  RESUMEN MULTIASSET — VIRGIN VAH/VAL\n{'='*72}")
    print(f"  {'Sym':<6} {'Baseline OOS':>13} {'VirginVA OOS':>13} {'Combined OOS':>13} {'Delta':>8}")
    print("  " + "-"*58)
    for sym, r in results.items():
        b=r["base"]; v=r["va"]; c=r["combined"]
        print(f"  {sym:<6} {b['oosA']:>+13.3f} {v['oosA']:>+13.3f} {c['oosA']:>+13.3f} {c['oosA']-b['oosA']:>+8.3f}")
    print()
    for sym, r in results.items():
        verdict="ADD" if r["va"]["oosA"]>0.1 and r["va"]["n_oos"]>=10 else "SKIP"
        print(f"  {sym}: {verdict}  (va n_oos={r['va']['n_oos']}  delta_combined={r['combined']['oosA']-r['base']['oosA']:+.3f}R)")

if __name__ == "__main__":
    main()
