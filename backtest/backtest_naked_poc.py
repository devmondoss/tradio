"""
backtest_naked_poc.py — Naked POC generator
============================================
Naked POC = POC diario que el precio no ha revisitado desde que se formó.
El mercado tiende a volver a rellenarlo → entrada límite en el nivel.

Long:  precio actual > naked_poc → soporte potencial desde abajo
Short: precio actual < naked_poc → resistencia desde arriba

Precomputo O(n×D) one-time, luego chequeo O(1) en el generador por barra.

Uso: python -X utf8 backtest/backtest_naked_poc.py
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


# ── Precomputación de POCs diarios ───────────────────────────────────────────

def compute_daily_poc_map(a, t):
    """
    Para cada día en el dataset: calcula el POC del perfil de volumen diario.
    Usa el bar M15 con mayor volumen como proxy del POC diario.
    Fallback: VWAP del día si fp_poc no está disponible.

    Returns:
        daily_poc_map: dict[day_id -> poc_price]
        day_end_idx:   dict[day_id -> last_bar_idx_in_that_day]
    """
    day_arr   = a.day.astype(int)
    fp_poc    = a.fp_poc
    vol_arr   = t["volume"].values.astype(float) if "volume" in t.columns else np.ones(a.n)
    close_arr = a.c

    days_unique = np.unique(day_arr)
    daily_poc_map = {}
    day_end_idx   = {}

    for d in days_unique:
        mask = np.where(day_arr == d)[0]
        if len(mask) == 0:
            continue
        day_end_idx[d] = int(mask[-1])

        # Bar con más volumen en ese día → su fp_poc es el POC diario
        max_vol_pos = mask[np.argmax(vol_arr[mask])]
        poc = fp_poc[max_vol_pos]

        if not np.isfinite(poc) or poc <= 0:
            # Fallback: VWAP del día
            vols = vol_arr[mask]
            total_vol = vols.sum()
            poc = (close_arr[mask] * vols).sum() / total_vol if total_vol > 0 else np.nan

        daily_poc_map[d] = poc

    return daily_poc_map, day_end_idx


def precompute_revisit_idx(daily_poc_map, day_end_idx, h_arr, l_arr, n):
    """
    Para cada día d con POC poc_d:
    Encuentra el primer bar después del día d donde precio tocó el POC.
    O n+1 si nunca fue revisitado.

    Returns:
        revisit_idx: dict[day_id -> first_bar_where_revisited_or_n+1]
    """
    revisit_idx = {}
    for d, poc in daily_poc_map.items():
        if not np.isfinite(poc) or poc <= 0 or d not in day_end_idx:
            revisit_idx[d] = 0  # inválido
            continue

        start = day_end_idx[d] + 1
        if start >= n:
            revisit_idx[d] = n + 1  # nunca (fin del dataset)
            continue

        mask = (l_arr[start:] <= poc) & (h_arr[start:] >= poc)
        nz   = np.where(mask)[0]
        revisit_idx[d] = int(start + nz[0]) if len(nz) > 0 else (n + 1)

    return revisit_idx


def make_gen_naked_poc(daily_poc_map, day_end_idx, revisit_idx, day_arr,
                       N_days=10, tol=0.003):
    """
    Generador de señales naked POC.

    N_days:  mirar hasta N días atrás buscando naked POCs.
    tol:     0.3% — la barra debe tocar el nivel dentro de esta tolerancia.
    """
    day_arr = day_arr.astype(int)

    def g(a, i):
        if i < 100:
            return None

        cur_day = day_arr[i]
        ref     = a.c[i - 1]
        results = []

        # Recoger los últimos N_days únicos, excluyendo el día actual
        past_days = []
        seen_set  = set()
        for j in range(i - 1, max(0, i - 700), -1):
            d = int(day_arr[j])
            if d == cur_day:
                continue
            if d not in seen_set:
                seen_set.add(d)
                past_days.append(d)
            if len(past_days) >= N_days:
                break

        for d in past_days:
            poc = daily_poc_map.get(d)
            if poc is None or not np.isfinite(poc):
                continue

            # Señal solo cuando ESTA barra es la primera que toca el naked POC.
            # revisit_idx[d] == i  →  primer retorno al POC = señal de entrada
            # revisit_idx[d] < i   →  ya fue revisitado antes, ya no es naked
            # revisit_idx[d] > i   →  precio aún lejos, no hay toque
            rev = revisit_idx.get(d, -1)
            if rev != i:
                continue

            # Long: naked POC debajo, precio regresó desde arriba
            if poc < ref:
                stop = poc - 0.6 * a.atr[i]
                tp1, tp2 = L2.struct_target(a, i, "long", poc)
                if np.isfinite(tp2):
                    results.append(("long", poc, stop, tp1, tp2, "naked_poc"))

            # Short: naked POC arriba, precio regresó desde abajo
            elif poc > ref:
                stop = poc + 0.6 * a.atr[i]
                tp1, tp2 = L2.struct_target(a, i, "short", poc)
                if np.isfinite(tp2):
                    results.append(("short", poc, stop, tp1, tp2, "naked_poc"))

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
            if i < cool or a.atr[i] <= 0:
                continue
            if volfilter and not (np.isfinite(atr_med[i]) and a.atr[i] > atr_med[i]):
                continue
            d = int(a.day[i])
            if dcount.get(d, 0) >= max_day:
                continue

            for side, lvl, stop, tp1, tp2, kind in (g(a, i) or []):
                if not np.isfinite([lvl, stop, tp2]).all():
                    continue
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
                if risk <= 0 or abs(tp2 - entry) / risk < 1.2:
                    continue
                if min_range > 0 and tp1 is not None and 100 * abs(tp1 - entry) / entry < min_range:
                    continue

                chop_here = str(a.reg[i]).lower() in ("chop", "range", "balance", "consolidation")
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


def stats(df, label=""):
    if len(df) == 0:
        return dict(n=0, wr=0, avgR=0, oosA=0, n_oos=0, dd=0, sharpe=0)
    o   = df[df.oos]
    cap = 500.0; peak = 500.0; dd = 0.0
    for r in df.sort_values("ts").r.values:
        cap += 5 * r; peak = max(peak, cap); dd = max(dd, (peak - cap) / peak)
    sh = df.r.mean() / (df.r.std() + 1e-9) * np.sqrt(len(df))
    return dict(n=len(df), wr=100*(df.r > 0).mean(), avgR=df.r.mean(),
                oosA=o.r.mean() if len(o) else 0,
                n_oos=len(o), dd=100*dd, sharpe=sh)


def row(label, s, base_oos=None):
    delta = f" ({s['oosA']-base_oos:+.3f})" if base_oos is not None else ""
    return (f"  {label:<42} n={s['n']:>4} n_oos={s['n_oos']:>4} | "
            f"WR {s['wr']:4.1f}% | avgR {s['avgR']:+.3f} | "
            f"OOS {s['oosA']:+.3f}{delta} | DD {s['dd']:4.1f}%")


def run_symbol(sym, path):
    if not path.exists():
        print(f"  {sym}: SKIP ({path})")
        return None

    print(f"\n{'='*72}")
    print(f"  {sym}")
    print(f"{'='*72}")

    L2.M1 = path
    t  = L2.load2(TF, start_ms=0)
    a  = L2.A2(t)
    m1 = L2.load_m1_exit(start_ms=0)

    # Precomputación naked POC
    print("  Precomputando daily POCs...", end=" ", flush=True)
    daily_poc_map, day_end_idx = compute_daily_poc_map(a, t)
    print(f"{len(daily_poc_map)} días")

    print("  Calculando revisit index...", end=" ", flush=True)
    revisit_idx = precompute_revisit_idx(
        daily_poc_map, day_end_idx,
        a.h, a.l, a.n
    )
    naked_count = sum(1 for v in revisit_idx.values() if v > a.n)
    print(f"({naked_count} POCs aún naked al final del dataset)")

    # Generadores baseline
    gens_baseline = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]

    # Generador naked POC
    gen_naked = make_gen_naked_poc(
        daily_poc_map, day_end_idx, revisit_idx,
        a.day, N_days=10, tol=0.003
    )

    # ── BASELINE ────────────────────────────────────────────────────────────
    df_base = run_ab(a, gens_baseline, m1)
    s_base  = stats(df_base)
    base_oos = s_base["oosA"]
    print(row("BASELINE (H5+H21+H21s)", s_base))

    # ── NAKED POC standalone ─────────────────────────────────────────────────
    print(f"\n  --- NAKED POC ---")
    # Diagnóstico: cuántos niveles genera el gen antes del fill filter
    gen_diag = make_gen_naked_poc(daily_poc_map, day_end_idx, revisit_idx, a.day, N_days=10, tol=0.003)
    n_signals = sum(len(gen_diag(a, i) or []) for i in range(100, a.n - 1))
    print(f"  Señales raw del generador (antes fill filter): {n_signals}")

    df_nk = run_ab(a, [gen_naked], m1)
    s_nk  = stats(df_nk)
    print(row("Naked POC standalone", s_nk))

    # Breakdown IS/OOS
    if len(df_nk) > 0:
        is_nk  = df_nk[~df_nk.oos]
        oos_nk = df_nk[df_nk.oos]
        print(f"    IS: n={len(is_nk):>4} WR={100*(is_nk.r>0).mean():4.1f}% avgR={is_nk.r.mean():+.3f}")
        print(f"   OOS: n={len(oos_nk):>4} WR={100*(oos_nk.r>0).mean():4.1f}% avgR={oos_nk.r.mean():+.3f}  gap={oos_nk.r.mean()-is_nk.r.mean():+.3f}R")
        side_counts = df_nk.groupby("side").agg(n=("r","count"), avgR=("r","mean"), wr=("r", lambda x: 100*(x>0).mean()))
        for side, row_d in side_counts.iterrows():
            print(f"    {side}: n={int(row_d.n):>4} WR={row_d.wr:4.1f}% avgR={row_d.avgR:+.3f}")

    # ── COMBINACION: baseline + naked POC ────────────────────────────────────
    print(f"\n  --- BASELINE + NAKED POC ---")
    gens_combined = gens_baseline + [gen_naked]
    df_comb = run_ab(a, gens_combined, m1)
    s_comb  = stats(df_comb)
    delta_oos = s_comb["oosA"] - base_oos
    print(row(f"H5+H21+H21s+NakedPOC", s_comb, base_oos))
    print(f"    delta OOS vs baseline: {delta_oos:+.3f}R  (n +{s_comb['n']-s_base['n']})")

    # ── SENSIBILIDAD N_days ───────────────────────────────────────────────────
    print(f"\n  --- NAKED POC standalone — sensibilidad N_days ---")
    for nd in [5, 10, 15, 20]:
        gen_nd = make_gen_naked_poc(
            daily_poc_map, day_end_idx, revisit_idx,
            a.day, N_days=nd, tol=0.003
        )
        df_nd = run_ab(a, [gen_nd], m1)
        s_nd  = stats(df_nd)
        print(row(f"N_days={nd:>2}", s_nd, s_nk["oosA"] if len(df_nk) > 0 else None))

    return {"base": s_base, "naked": s_nk, "combined": s_comb}


def main():
    results = {}
    for sym, path in PARQUETS.items():
        r = run_symbol(sym, path)
        if r:
            results[sym] = r

    print(f"\n\n{'='*72}")
    print("  RESUMEN MULTIASSET")
    print(f"{'='*72}")
    print(f"  {'Sym':<6} {'Baseline OOS':>13} {'Naked OOS':>10} {'Combined OOS':>13} {'Delta comb':>11}")
    print("  " + "-"*60)
    for sym, r in results.items():
        b  = r["base"]
        nk = r["naked"]
        co = r["combined"]
        print(f"  {sym:<6} {b['oosA']:>+13.3f} {nk['oosA']:>+10.3f} "
              f"{co['oosA']:>+13.3f} {co['oosA']-b['oosA']:>+11.3f}")

    print(f"\n  Veredicto:")
    for sym, r in results.items():
        delta = r["combined"]["oosA"] - r["base"]["oosA"]
        verdict = "ADD" if delta > 0.05 and r["naked"]["n_oos"] >= 10 else "SKIP"
        print(f"  {sym}: {verdict}  (naked n_oos={r['naked']['n_oos']}  delta_combined={delta:+.3f}R)")


if __name__ == "__main__":
    main()
