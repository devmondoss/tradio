"""
audit_vp_quality.py — Dos hipótesis de mejora del edge VP
==========================================================
1. LIQUIDATION FILTER: ¿Los nodos POC construidos durante cascadas de liquidación
   son menos predictivos? (vr>2.5 AND max_trade>p95 = "barra sucia")

2. FUNDING FILTER: ¿El funding rate extremo degrada las entradas fade en la misma
   dirección que la presión del funding?

Resultado esperado: si POC limpio > POC sucio → nuevo filtro sin reoptimizar.

Uso: python backtest/audit_vp_quality.py
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
TF = 15

# ── thresholds ────────────────────────────────────────────────────────────────
LIQ_VR       = 2.5     # vr > esto = spike de volumen
LIQ_MT_PCT   = 95      # max_trade > percentil N = print grande
FUND_EXTREME = 0.0001  # |funding_rate| > 10 bps = extremo


# ── carga datos ───────────────────────────────────────────────────────────────
def load():
    t = L2.load2(TF)
    # añadir max_trade_p95 como umbral dinámico causal (rolling 500 barras)
    t["mt_p95"] = t["max_trade"].rolling(500, min_periods=50).quantile(0.95).shift(1)
    t["dirty"] = (t["vr"] > LIQ_VR) & (t["max_trade"] > t["mt_p95"])
    return t


def load_funding():
    f = pd.read_parquet(ROOT / "data/bybit-perp/funding.parquet").sort_values("ts_ms")
    return f


def merge_funding(t, f):
    """Forward-fill funding cada 8h al M15."""
    t2 = t.copy()
    t2["funding_rate"] = np.nan
    fi = 0
    for idx in range(len(t2)):
        ts = t2.ts_ms.iloc[idx]
        while fi + 1 < len(f) and f.ts_ms.iloc[fi + 1] <= ts:
            fi += 1
        if f.ts_ms.iloc[fi] <= ts:
            t2.at[t2.index[idx], "funding_rate"] = f.funding_rate.iloc[fi]
    return t2


# ── runner con tags ───────────────────────────────────────────────────────────
def run_tagged(a, dirty_arr, funding_arr, gens, m1, tf_min=15,
               trail_atr=4.0, volfilter=True, timeout_min=24*60,
               cooldown=6, max_day=2, margin=2.0, stop_floor_pct=0.15, min_range=0.5, K=15):

    from _listas2 import struct_target
    m1ts, m1h, m1l, m1c = m1
    bar_ms = tf_min * 60_000
    MK, TK = 0.0002 / 2, 0.00055 / 2

    atr_med = pd.Series(a.atr).rolling(500, min_periods=50).median().shift(1).values
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
                if side == "long" and not (lvl < ref): continue
                if side == "short" and not (lvl > ref): continue
                if side == "long" and not (a.l[i] <= lvl - margin / 1e4 * lvl): continue
                if side == "short" and not (a.h[i] >= lvl + margin / 1e4 * lvl): continue

                entry = lvl; atr0 = a.atr[i]
                if stop_floor_pct > 0:
                    mr = stop_floor_pct / 100.0 * entry
                    if abs(entry - stop) < mr:
                        stop = entry - mr if side == "long" else entry + mr
                risk = abs(entry - stop)
                if risk <= 0 or abs(tp2 - entry) / risk < 1.2: continue
                if min_range > 0 and tp1 is not None and 100 * abs(tp1 - entry) / entry < min_range: continue

                # ── tag LIQUIDACIÓN: fracción de barras sucias en ventana K ──
                w_start = max(0, i - K)
                dirty_window = dirty_arr[w_start:i]
                dirty_ratio = dirty_window.mean() if len(dirty_window) > 0 else 0.0

                # ── tag FUNDING ───────────────────────────────────────────────
                fr = funding_arr[i] if i < len(funding_arr) else np.nan
                fund_tag = "neutral"
                if np.isfinite(fr):
                    if fr > FUND_EXTREME:
                        fund_tag = "extreme_long"   # longs pagan → presión vendedora
                    elif fr < -FUND_EXTREME:
                        fund_tag = "extreme_short"  # shorts pagan → presión compradora

                # ── chop/trend para routing ───────────────────────────────────
                chop_here = str(a.reg[i]).lower() in ("chop", "range", "balance", "consolidation")
                use_fade = chop_here

                j0 = np.searchsorted(m1ts, a.ts[i] + bar_ms)
                jend = np.searchsorted(m1ts, a.ts[i] + bar_ms + timeout_min * 60_000)
                res = None

                if use_fade:
                    cur = stop; realized = 0.0; rem = 1.0; f1 = False
                    p1 = 0.5 if tp1 else 0.0; reason = "timeout"
                    for j in range(j0, min(jend, len(m1ts))):
                        if side == "long":
                            if m1l[j] <= cur: realized += rem * ((cur - entry) / risk); reason = "be" if f1 else "stop"; break
                            if not f1 and tp1 and m1h[j] >= tp1: realized += p1 * ((tp1 - entry) / risk); rem -= p1; f1 = True; cur = entry
                            if m1h[j] >= tp2: realized += rem * ((tp2 - entry) / risk); reason = "target"; break
                        else:
                            if m1h[j] >= cur: realized += rem * ((entry - cur) / risk); reason = "be" if f1 else "stop"; break
                            if not f1 and tp1 and m1l[j] <= tp1: realized += p1 * ((entry - tp1) / risk); rem -= p1; f1 = True; cur = entry
                            if m1l[j] <= tp2: realized += rem * ((entry - tp2) / risk); reason = "target"; break
                    else:
                        jj = min(jend, len(m1ts)) - 1
                        if jj <= j0: continue
                        px = m1c[jj]; realized += rem * (((px - entry) if side == "long" else (entry - px)) / risk)
                    exit_side = MK if reason == "target" else TK
                    fee_r = (MK * 1.0 + (MK * p1 if f1 else 0.0) + exit_side * rem) * entry / risk
                    res = realized - fee_r; gestion = "fade"
                else:
                    fee_r = (MK + TK) * entry / risk; best = entry; trail = stop
                    for j in range(j0, min(jend, len(m1ts))):
                        if side == "long":
                            best = max(best, m1h[j]); trail = max(trail, best - trail_atr * atr0)
                            if m1l[j] <= trail: res = (trail - entry) / risk - fee_r; break
                        else:
                            best = min(best, m1l[j]); trail = min(trail, best + trail_atr * atr0)
                            if m1h[j] >= trail: res = (entry - trail) / risk - fee_r; break
                    if res is None:
                        jj = min(jend, len(m1ts)) - 1
                        if jj <= j0: continue
                        px = m1c[jj]; res = ((px - entry) if side == "long" else (entry - px)) / risk - fee_r
                    gestion = "trail"

                trades.append(dict(
                    ts=int(a.ts[i]), side=side, r=res, gestion=gestion,
                    oos=int(a.ts[i]) >= OOS_MS, kind=kind,
                    dirty_ratio=dirty_ratio,
                    dirty=(dirty_ratio >= 0.20),   # >=20% de barras sucias = POC sucio
                    fund_tag=fund_tag,
                    funding_rate=fr,
                ))
                cool = i + cooldown; dcount[d] = dcount.get(d, 0) + 1; break

    return pd.DataFrame(trades)


# ── reporte ───────────────────────────────────────────────────────────────────
def blk(df, label):
    if len(df) == 0:
        print(f"  {label:<30} n=0")
        return
    oos = df[df.oos]
    wr  = 100 * (df.r > 0).mean()
    print(f"  {label:<30} n={len(df):>4}  WR={wr:4.1f}%  avgR={df.r.mean():+.3f}"
          f"  OOS_avgR={oos.r.mean() if len(oos) else 0:+.3f} (n={len(oos)})")


def main():
    print("Cargando M15...")
    t = load()
    a = L2.A2(t)
    dirty_arr   = t["dirty"].values.astype(float)
    funding_raw = load_funding()
    t2 = merge_funding(t, funding_raw)
    funding_arr = t2["funding_rate"].values

    m1  = L2.load_m1_exit()
    gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]

    print("Corriendo backtest con tags...")
    df = run_tagged(a, dirty_arr, funding_arr, gens, m1)

    total = len(df)
    print(f"\nTotal trades: {total}  OOS: {df.oos.sum()}")

    # ── 1. LIQUIDATION FILTER ─────────────────────────────────────────────────
    print("\n═══ 1. POC QUALITY — liquidation filter ═══")
    print(f"  dirty_ratio stats: mean={df.dirty_ratio.mean():.2f}  p50={df.dirty_ratio.median():.2f}  p75={df.dirty_ratio.quantile(0.75):.2f}")
    print(f"  POC limpio (<20% barras sucias): {(~df.dirty).sum()}  |  POC sucio (>=20%): {df.dirty.sum()}")
    blk(df[~df.dirty], "POC LIMPIO (all)")
    blk(df[df.dirty],  "POC SUCIO  (all)")
    blk(df[~df.dirty & df.oos], "POC LIMPIO OOS")
    blk(df[df.dirty  & df.oos], "POC SUCIO  OOS")

    # breakdown por umbral
    print("\n  Barrido de umbrales dirty_ratio:")
    for thr in [0.10, 0.15, 0.20, 0.25, 0.30]:
        clean = df[df.dirty_ratio < thr]
        dirty = df[df.dirty_ratio >= thr]
        oos_c = clean[clean.oos].r.mean() if len(clean[clean.oos]) else 0
        oos_d = dirty[dirty.oos].r.mean() if len(dirty[dirty.oos]) else 0
        print(f"    thr={thr:.2f}  limpio n={len(clean)} OOS_avgR={oos_c:+.3f} | sucio n={len(dirty)} OOS_avgR={oos_d:+.3f}")

    # ── 2. FUNDING FILTER ─────────────────────────────────────────────────────
    print("\n═══ 2. FUNDING RATE FILTER ═══")
    print(f"  funding stats: mean={np.nanmean(funding_arr)*10000:.2f}bps  p95={np.nanpercentile(funding_arr[np.isfinite(funding_arr)], 95)*10000:.2f}bps")
    blk(df[df.fund_tag == "neutral"],        "FUNDING neutral")
    blk(df[df.fund_tag == "extreme_long"],   "FUNDING extreme long  (longs pagan)")
    blk(df[df.fund_tag == "extreme_short"],  "FUNDING extreme short (shorts pagan)")

    # hipótesis específica: long fade con funding extremo long = malo?
    print("\n  Hipótesis: fade LONG con funding extreme_long (presión vendedora)")
    blk(df[(df.side=="long")  & (df.gestion=="fade") & (df.fund_tag=="extreme_long")],  "  long fade + fund_ext_long")
    blk(df[(df.side=="long")  & (df.gestion=="fade") & (df.fund_tag=="neutral")],        "  long fade + fund neutral")
    blk(df[(df.side=="short") & (df.gestion=="fade") & (df.fund_tag=="extreme_short")],  "  short fade + fund_ext_short")
    blk(df[(df.side=="short") & (df.gestion=="fade") & (df.fund_tag=="neutral")],         "  short fade + fund neutral")

    # ── 3. COMBINADO ─────────────────────────────────────────────────────────
    print("\n═══ 3. COMBINADO: POC limpio + funding neutral ═══")
    good = ~df.dirty & (df.fund_tag == "neutral")
    blk(df[good],              "Limpio + neutral (all)")
    blk(df[good & df.oos],     "Limpio + neutral (OOS)")
    blk(df[~good],             "Resto (all)")
    blk(df[~good & df.oos],    "Resto (OOS)")
    print(f"\n  Trades filtrados: {(~good).sum()} de {total} ({100*(~good).mean():.1f}% eliminados)")

    print("\n✓ listo")


if __name__ == "__main__":
    main()
