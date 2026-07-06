"""
audit_orderflow_filters.py — 3 hipotesis de mejora por orderflow
=================================================================
A. fp_absorb_buy/sell en barra de fill -> WR sube?
B. sweep_confirmed antes de H21 entry -> avgR sube?
C. sesion NY/London vs Asia en VP levels

Uso: python -X utf8 backtest/audit_orderflow_filters.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _listas import OOS_MS
from _audit_mirror import gen_h21_short

ROOT = Path(__file__).parent.parent
TF   = 15
MK, TK = 0.0002 / 2, 0.00055 / 2


def load():
    t = L2.load2(TF)
    # solo columnas que NO carga load2() ya
    extra = pd.read_parquet(
        ROOT / "data/bybit-perp/processed/btcusdt_perp_m15.parquet",
        columns=["ts_ms", "sweep_confirmed", "session", "stacked_imb"]
    ).sort_values("ts_ms").reset_index(drop=True)
    t = t.merge(extra, on="ts_ms", how="left")
    return t


def run_tagged(a, t, gens, m1, tf_min=15, trail_atr=4.0,
               volfilter=True, timeout_min=24*60,
               cooldown=6, max_day=2, margin=2.0,
               stop_floor_pct=0.15, min_range=0.5):

    m1ts, m1h, m1l, m1c = m1
    bar_ms = tf_min * 60_000
    atr_med = pd.Series(a.atr).rolling(500, min_periods=50).median().shift(1).values

    # arrays de los tags extra
    absorb_buy  = np.nan_to_num(t["fp_absorb_buy"].values.astype(float)).astype(bool)
    absorb_sell = np.nan_to_num(t["fp_absorb_sell"].values.astype(float)).astype(bool)
    sweep       = np.nan_to_num(t["sweep_confirmed"].values.astype(float)).astype(bool)
    session_arr = t["session"].values
    stacked_arr = t["stacked_imb"].values
    bos_bull    = np.nan_to_num(t["h1_bos_bull"].values.astype(float)).astype(bool)
    bos_bear    = np.nan_to_num(t["h1_bos_bear"].values.astype(float)).astype(bool)

    trades = []
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

                # ── TAGS ─────────────────────────────────────────────────────
                # A. absorcion en barra de fill (i) o barra previa (i-1)
                if side == "long":
                    absorb_ok = absorb_buy[i] or (i > 0 and absorb_buy[i-1])
                else:
                    absorb_ok = absorb_sell[i] or (i > 0 and absorb_sell[i-1])

                # B. sweep en ventana previa (ultimas 3 barras antes de fill)
                sweep_ok = bool(np.any(sweep[max(0, i-3):i+1]))

                # C. sesion
                sess = str(session_arr[i])
                sess_group = (
                    "NY"     if sess in ("NewYork", "NY_KZ") else
                    "London" if sess in ("London",  "London_KZ") else
                    "Asia"   if sess == "Asia" else
                    "OffHours"
                )

                # D. stacked imbalance alineado
                stk = str(stacked_arr[i])
                stacked_ok = (
                    (side == "long"  and stk == "Bullish") or
                    (side == "short" and stk == "Bearish")
                )

                # E. H1 BOS alineado (en ventana 3 barras)
                bos_ok = bool(
                    np.any(bos_bull[max(0,i-3):i+1]) if side == "long" else
                    np.any(bos_bear[max(0,i-3):i+1])
                )

                # ── routing ───────────────────────────────────────────────────
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
                    res = realized - fee_r; gestion = "fade"
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
                    gestion = "trail"

                trades.append(dict(
                    ts=int(a.ts[i]), side=side, r=res, gestion=gestion,
                    oos=int(a.ts[i]) >= OOS_MS, kind=kind,
                    absorb=absorb_ok,
                    sweep=sweep_ok,
                    session=sess_group,
                    stacked=stacked_ok,
                    bos=bos_ok,
                ))
                cool = i + cooldown; dcount[d] = dcount.get(d, 0) + 1; break

    return pd.DataFrame(trades)


def blk(df, label, indent=2):
    sp = " " * indent
    if len(df) == 0:
        print(f"{sp}{label:<35} n=0"); return
    oos = df[df.oos]
    wr  = 100 * (df.r > 0).mean()
    oavg = oos.r.mean() if len(oos) else 0
    print(f"{sp}{label:<35} n={len(df):>4}  WR={wr:4.1f}%  avgR={df.r.mean():+.3f}"
          f"  OOS_avgR={oavg:+.3f} (n={len(oos)})")


def main():
    print("Cargando M15 + tags...")
    t  = load()
    a  = L2.A2(t)
    m1 = L2.load_m1_exit()
    gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]

    print("Corriendo backtest taggeado...")
    df = run_tagged(a, t, gens, m1)
    oos = df[df.oos]
    print(f"Total: {len(df)}  OOS: {len(oos)}\n")

    # baseline
    blk(df,  "BASELINE (todo)")
    blk(oos, "BASELINE OOS")

    # ── A. ABSORCION ──────────────────────────────────────────────────────────
    print("\n=== A. ABSORCION (fp_absorb en barra de fill) ===")
    blk(df[df.absorb],       "Con absorcion")
    blk(df[~df.absorb],      "Sin absorcion")
    blk(df[df.absorb & df.oos],  "Con absorcion OOS")
    blk(df[~df.absorb & df.oos], "Sin absorcion OOS")
    pct = 100 * df.absorb.mean()
    print(f"  Porcentaje trades con absorcion: {pct:.1f}%")

    # A2. absorcion + direccion por side
    print("  Breakdown por side:")
    for s in ["long", "short"]:
        dfs = df[df.side == s]
        blk(dfs[dfs.absorb],  f"    {s} + absorcion", indent=4)
        blk(dfs[~dfs.absorb], f"    {s} sin absorcion", indent=4)

    # ── B. SWEEP CONFIRMADO ───────────────────────────────────────────────────
    print("\n=== B. SWEEP CONFIRMADO (ultimas 3 barras antes de fill) ===")
    blk(df[df.sweep],        "Con sweep")
    blk(df[~df.sweep],       "Sin sweep")
    blk(df[df.sweep & df.oos],  "Con sweep OOS")
    blk(df[~df.sweep & df.oos], "Sin sweep OOS")
    print(f"  Porcentaje trades con sweep: {100*df.sweep.mean():.1f}%")

    # ── C. SESION ─────────────────────────────────────────────────────────────
    print("\n=== C. SESION DE ENTRADA ===")
    for sess in ["NY", "London", "Asia", "OffHours"]:
        blk(df[df.session == sess],               f"{sess}")
        blk(df[(df.session == sess) & df.oos],    f"{sess} OOS")

    # ── D. STACKED IMBALANCE ─────────────────────────────────────────────────
    print("\n=== D. STACKED IMBALANCE ALINEADO ===")
    blk(df[df.stacked],       "Stacked alineado")
    blk(df[~df.stacked],      "Stacked NO alineado")
    blk(df[df.stacked & df.oos],  "Stacked alineado OOS")
    blk(df[~df.stacked & df.oos], "Stacked NO alineado OOS")
    print(f"  Porcentaje con stacked alineado: {100*df.stacked.mean():.1f}%")

    # ── E. H1 BOS ALINEADO ───────────────────────────────────────────────────
    print("\n=== E. H1 BOS ALINEADO ===")
    blk(df[df.bos],       "Con H1 BOS")
    blk(df[~df.bos],      "Sin H1 BOS")
    blk(df[df.bos & df.oos],  "Con H1 BOS OOS")
    blk(df[~df.bos & df.oos], "Sin H1 BOS OOS")
    print(f"  Porcentaje con H1 BOS: {100*df.bos.mean():.1f}%")

    # ── COMBINACIONES PROMETEDORAS ────────────────────────────────────────────
    print("\n=== COMBINACIONES ===")
    combos = [
        ("absorb + sweep",         df.absorb & df.sweep),
        ("absorb + stacked",       df.absorb & df.stacked),
        ("absorb + bos",           df.absorb & df.bos),
        ("sweep + stacked",        df.sweep & df.stacked),
        ("NY + absorb",            (df.session=="NY") & df.absorb),
        ("NY + sweep",             (df.session=="NY") & df.sweep),
        ("London + absorb",        (df.session=="London") & df.absorb),
        ("absorb + sweep + stack", df.absorb & df.sweep & df.stacked),
    ]
    for label, mask in combos:
        sub = df[mask]
        sub_oos = sub[sub.oos]
        if len(sub) < 5: continue
        blk(sub,     label)
        blk(sub_oos, f"{label} OOS")

    # ── MEJOR FILTRO: impacto en n y avgR ────────────────────────────────────
    print("\n=== RESUMEN: mejora vs baseline ===")
    base_oos = oos.r.mean()
    print(f"  Baseline OOS avgR = {base_oos:+.3f}  (n={len(oos)})")
    candidates = [
        ("absorb",              df.absorb),
        ("sweep",               df.sweep),
        ("stacked",             df.stacked),
        ("bos",                 df.bos),
        ("NY session",          df.session=="NY"),
        ("London session",      df.session=="London"),
        ("absorb+sweep",        df.absorb & df.sweep),
        ("absorb+stacked",      df.absorb & df.stacked),
        ("sweep+stacked",       df.sweep & df.stacked),
    ]
    for label, mask in candidates:
        sub_oos = df[mask & df.oos]
        if len(sub_oos) < 10: continue
        delta = sub_oos.r.mean() - base_oos
        pct_n = 100 * mask.mean()
        print(f"  {label:<25}  OOS_avgR={sub_oos.r.mean():+.3f}  "
              f"delta={delta:+.3f}  n_oos={len(sub_oos)}  "
              f"trades_usados={pct_n:.0f}%")

    print("\nListo.")


if __name__ == "__main__":
    main()
