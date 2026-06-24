"""
_smc_setups.py — Setups SMC probados UNO POR UNO, con AUTOPSIA mecánica.
========================================================================
Cada setup se prueba aislado (su entrada + gestión A+B enrutada, nuestra mejor
gestión) y se reporta el DESGLOSE DE SALIDAS (stop / breakeven / target / trail /
timeout) con avgR por cada una → así se ve POR QUÉ gana o pierde, no solo el número.

Setups:
  1. SWEEP            — barrido de un swing/PDH-PDL + rechazo (fade del barrido).
  2. FVG             — hueco de 3 velas sin rellenar (límite en el borde).
  3. SWEEP→FVG       — el SETUP REAL ICT: barrido de liquidez → desplazamiento que
                       deja un FVG → entrada en el retroceso al FVG. Secuencia, no
                       dos señales sueltas. El FVG SOLO cuenta si nació de un sweep.

Uso: python backtest/_smc_setups.py [BTCUSDT ...]
"""
import sys; from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, "backtest")
import _listas2 as L2
from _strategy_ab import run_system, stats
from _audit_mirror import gen_h21_short

PARQ = {"BTCUSDT": "data/bybit-perp/processed/btcusdt_perp_m1.parquet",
        "ETHUSDT": "E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet",
        "SOLUSDT": "E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"}


# ── 1. SWEEP (barrido + rechazo) ──────────────────────────────────────────────
def gen_sweep():
    def g(a, i):
        out = []; h, l, c, atr = a.h[i], a.l[i], a.c[i], a.atr[i]
        if atr <= 0: return out
        for lvl in (a.swing_low_50[i], a.prev_day_low[i]):
            if np.isfinite(lvl) and l < lvl and c > lvl and (lvl - l) > 0.1*atr:
                stop = l - 0.25*atr; tp1, tp2 = L2.struct_target(a, i, "long", c)
                if np.isfinite(tp2) and stop < c < tp2: out.append(("long", c, stop, tp1, tp2, "sweep")); break
        for lvl in (a.swing_high_50[i], a.prev_day_high[i]):
            if np.isfinite(lvl) and h > lvl and c < lvl and (h - lvl) > 0.1*atr:
                stop = h + 0.25*atr; tp1, tp2 = L2.struct_target(a, i, "short", c)
                if np.isfinite(tp2) and tp2 < c < stop: out.append(("short", c, stop, tp1, tp2, "sweep")); break
        return out
    return g


# ── 2. FVG (hueco de 3 velas sin rellenar) ────────────────────────────────────
def gen_fvg(W=50, buf=0.25, min_gap=0.1):
    def g(a, i):
        atr = a.atr[i]
        if atr <= 0 or i < 4: return []
        ref = a.c[i-1]
        for k in range(i-1, max(2, i-W)-1, -1):
            gb, gt = a.h[k-2], a.l[k]
            if gt - gb > min_gap*atr:
                seg = a.l[k+1:i]; entered = seg.size > 0 and np.nanmin(seg) <= gt
                if not entered and a.l[i] <= gt and gt < ref:
                    stop = gb - buf*atr; tp1, tp2 = L2.struct_target(a, i, "long", gt)
                    if np.isfinite(tp2) and stop < gt < tp2: return [("long", gt, stop, tp1, tp2, "fvg")]
            gt2, gb2 = a.l[k-2], a.h[k]
            if gt2 - gb2 > min_gap*atr:
                seg = a.h[k+1:i]; entered = seg.size > 0 and np.nanmax(seg) >= gb2
                if not entered and a.h[i] >= gb2 and gb2 > ref:
                    stop = gt2 + buf*atr; tp1, tp2 = L2.struct_target(a, i, "short", gb2)
                    if np.isfinite(tp2) and tp2 < gb2 < stop: return [("short", gb2, stop, tp1, tp2, "fvg")]
        return []
    return g


# ── 3. SWEEP→FVG (el setup REAL: barrido → desplazamiento → FVG) ──────────────
def gen_sweep_fvg(W=30, buf=0.25, min_gap=0.1):
    """FVG que SOLO vale si el desplazamiento que lo creó barrió liquidez justo antes.
    Bullish: el hueco (k-2,k-1,k) nace de un mínimo que tomó un swing_low/PDL en [k-3,k]."""
    def g(a, i):
        atr = a.atr[i]
        if atr <= 0 or i < 7: return []
        ref = a.c[i-1]
        for k in range(i-1, max(5, i-W)-1, -1):
            # bullish FVG
            gb, gt = a.h[k-2], a.l[k]
            if gt - gb > min_gap*atr:
                seg = a.l[k+1:i]; entered = seg.size > 0 and np.nanmin(seg) <= gt
                if not entered and a.l[i] <= gt and gt < ref:
                    win_lo = np.nanmin(a.l[k-3:k+1])               # mínimo del desplazamiento
                    liq = np.nanmax([a.swing_low_50[k], a.prev_day_low[k]])  # liquidez que debía tomarse
                    if np.isfinite(liq) and win_lo < liq:          # ¿barrió?
                        stop = win_lo - buf*atr; tp1, tp2 = L2.struct_target(a, i, "long", gt)
                        if np.isfinite(tp2) and stop < gt < tp2: return [("long", gt, stop, tp1, tp2, "swfvg")]
            # bearish FVG
            gt2, gb2 = a.l[k-2], a.h[k]
            if gt2 - gb2 > min_gap*atr:
                seg = a.h[k+1:i]; entered = seg.size > 0 and np.nanmax(seg) >= gb2
                if not entered and a.h[i] >= gb2 and gb2 > ref:
                    win_hi = np.nanmax(a.h[k-3:k+1])
                    liq = np.nanmin([a.swing_high_50[k], a.prev_day_high[k]])
                    if np.isfinite(liq) and win_hi > liq:
                        stop = win_hi + buf*atr; tp1, tp2 = L2.struct_target(a, i, "short", gb2)
                        if np.isfinite(tp2) and tp2 < gb2 < stop: return [("short", gb2, stop, tp1, tp2, "swfvg")]
        return []
    return g


def autopsy(name, df):
    """Desglose de salidas: POR QUÉ gana/pierde."""
    o = df[df.oos]
    if not len(o):
        print(f"  {name:<12} sin trades OOS"); return
    print(f"  {name:<12} n={len(o):>4}  WR {(o.r>0).mean()*100:4.0f}%  avgR {o.r.mean():+.3f}  netR {o.r.sum():+6.1f}")
    rc = o.reason.value_counts()
    parts = []
    for reason in ("target", "trail", "be", "stop", "timeout"):
        if reason in rc.index:
            sub = o[o.reason == reason]
            parts.append(f"{reason} {len(sub)*100//len(o):>2}% (avgR {sub.r.mean():+.2f})")
    print(f"               salidas: " + " · ".join(parts))


R = 5.0
for sym in (sys.argv[1:] or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]):
    if not Path(PARQ[sym]).exists(): print(f"SKIP {sym}"); continue
    L2.M1 = Path(PARQ[sym]); t = L2.load2(15, start_ms=0); a = L2.A2(t); m1 = L2.load_m1_exit(start_ms=0)
    base_gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]
    print(f"\n{'='*60}\n  {sym}   (gestión A+B enrutada en todos · OOS)\n{'='*60}")

    base = run_system(a, base_gens, m1, 15, mode="routed", max_day=4, cooldown=3)
    autopsy("A+B base", base)
    for nm, gen in [("SWEEP", gen_sweep()), ("FVG", gen_fvg()), ("SWEEP->FVG", gen_sweep_fvg())]:
        solo = run_system(a, [gen], m1, 15, mode="routed", max_day=4, cooldown=3)
        autopsy(nm, solo)
    # el combinado prometedor: base + sweep->fvg
    comb = run_system(a, base_gens + [gen_sweep_fvg()], m1, 15, mode="routed", max_day=4, cooldown=3)
    sc = stats(comb[comb.oos]); sb = stats(base[base.oos])
    print(f"  {'A+B+SWFVG':<12} n={sc['n']:>4}  WR {sc['wr']:4.0f}%  avgR {sc['oosA']:+.3f}  (Δ vs base {sc['oosA']-sb['oosA']:+.3f})")
