"""
_entry_offset_sweep.py — ¿Mejorar fills subiendo el limit de compra?
=====================================================================
Para long: entry = lvl + offset*ATR  (orden más arriba → fill más fácil)
Para short: entry = lvl - offset*ATR (orden más abajo → fill más fácil)
Stop se queda anclado al nivel original → risk aumenta → R comprime.

Fill condition M15 también se actualiza: a.l[i] <= entry (más fácil de llenar).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import numpy as np
import pandas as pd
import featurelab as FL
import _listas2 as L2
from _listas import FEE_MAKER, FEE_TAKER, OOS_MS
from _audit_mirror import gen_h21_short

TF = 15
MK, TK = FEE_MAKER / 2, FEE_TAKER / 2
OFFSETS = [0.0, 0.05, 0.10, 0.15, 0.20, 0.30]


def run_with_offset(a, gens, m1, offset_atr,
                    trail_atr=6.0, stop_scale=0.8, mode="routed",
                    cooldown=6, max_day=2, margin=2.0, stop_floor_pct=0.15,
                    atr_win=500, tp2_cap_r=0.0, use_partial=True, p1_frac=0.5):
    m1ts, m1h, m1l, m1c = m1
    bar_ms = TF * 60_000
    timeout_ms = 24 * 60 * 60_000
    atr_med = pd.Series(a.atr).rolling(atr_win, min_periods=50).median().shift(1).values
    trades = []
    for g in gens:
        cool = 0; dcount = {}
        for i in range(60, a.n - 1):
            if i < cool or a.atr[i] <= 0: continue
            if not (np.isfinite(atr_med[i]) and a.atr[i] > atr_med[i]): continue
            d = int(a.day[i])
            if dcount.get(d, 0) >= max_day: continue
            for side, lvl, stop_gen, tp1, tp2, kind in (g(a, i) or []):
                if not np.isfinite([lvl, stop_gen, tp2]).all(): continue
                ref = a.c[i - 1]
                if side == "long"  and not (lvl < ref): continue
                if side == "short" and not (lvl > ref): continue

                atr0 = float(a.atr[i])
                # Entry con offset
                if side == "long":
                    entry = lvl + offset_atr * atr0
                else:
                    entry = lvl - offset_atr * atr0

                # Fill condition M15: barra actual toca el entry
                if side == "long"  and not (a.l[i] <= entry - margin / 1e4 * lvl): continue
                if side == "short" and not (a.h[i] >= entry + margin / 1e4 * lvl): continue

                # Stop anclado al nivel original (no al entry desplazado)
                stop = stop_gen
                if stop_scale != 1.0:
                    stop = lvl - stop_scale * (lvl - stop_gen) if side == "long" else lvl + stop_scale * (stop_gen - lvl)
                if stop_floor_pct > 0:
                    mr = stop_floor_pct / 100.0 * lvl
                    if abs(entry - stop) < mr:
                        stop = entry - mr if side == "long" else entry + mr

                risk = abs(entry - stop)
                if risk <= 0 or abs(tp2 - entry) / risk < 1.0: continue

                if tp2_cap_r > 0:
                    cap = entry + tp2_cap_r * risk if side == "long" else entry - tp2_cap_r * risk
                    if (side == "long" and tp2 > cap) or (side == "short" and tp2 < cap):
                        tp2 = cap
                        if tp1 is not None and not (min(entry, tp2) < tp1 < max(entry, tp2)):
                            tp1 = None

                chop_here = str(a.reg[i]).lower() in ("chop", "range", "balance", "consolidation")
                use_fade = (mode == "fade") or (mode == "routed" and chop_here)

                j0   = np.searchsorted(m1ts, a.ts[i] + bar_ms)
                jend = np.searchsorted(m1ts, a.ts[i] + bar_ms + timeout_ms)
                res = None

                if use_fade:
                    cur = stop; realized = 0.0; rem = 1.0; f1 = False
                    p1 = p1_frac if (tp1 and use_partial) else 0.0; reason = "timeout"
                    for j in range(j0, min(jend, len(m1ts))):
                        if side == "long":
                            if m1l[j] <= cur:
                                realized += rem * ((cur - entry) / risk); reason = "stop"; break
                            if use_partial and not f1 and tp1 and m1h[j] >= tp1:
                                realized += p1 * ((tp1 - entry) / risk); rem -= p1; f1 = True; cur = entry
                            if m1h[j] >= tp2:
                                realized += rem * ((tp2 - entry) / risk); reason = "target"; break
                        else:
                            if m1h[j] >= cur:
                                realized += rem * ((entry - cur) / risk); reason = "stop"; break
                            if use_partial and not f1 and tp1 and m1l[j] <= tp1:
                                realized += p1 * ((entry - tp1) / risk); rem -= p1; f1 = True; cur = entry
                            if m1l[j] <= tp2:
                                realized += rem * ((entry - tp2) / risk); reason = "target"; break
                    else:
                        jj = min(jend, len(m1ts)) - 1
                        if jj <= j0: continue
                        px = m1c[jj]; realized += rem * (((px - entry) if side == "long" else (entry - px)) / risk)
                    exit_side = MK if reason == "target" else TK
                    fee_r = (MK * 1.0 + (MK * p1 if f1 else 0.0) + exit_side * rem) * entry / risk
                    res = realized - fee_r
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

                trades.append(dict(ts=int(a.ts[i]), side=side, r=res,
                                   entry=entry, stop=stop, risk=risk,
                                   oos=int(a.ts[i]) >= OOS_MS))
                cool = i + cooldown; dcount[d] = dcount.get(d, 0) + 1; break
    return pd.DataFrame(trades)


def stats(df):
    if len(df) == 0:
        return dict(n=0, n_oos=0, avgR=0, oosR=0, wr=0, oos_wr=0, dd=0)
    o = df[df.oos]
    cap = 500.0; peak = 500.0; dd = 0.0
    for r in df.sort_values("ts").r.values:
        cap += 5 * r; peak = max(peak, cap); dd = max(dd, (peak - cap) / peak)
    return dict(
        n=len(df), n_oos=len(o),
        avgR=df.r.mean(),
        oosR=o.r.mean() if len(o) else 0,
        wr=100 * (df.r > 0).mean(),
        oos_wr=100 * (o.r > 0).mean() if len(o) else 0,
        dd=100 * dd,
    )


if __name__ == "__main__":
    GENS = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]

    print(f"{'offset':>8} | {'sym':<8} {'n':>5} {'n_oos':>5} {'IS_avgR':>8} {'OOS_avgR':>9} {'WR':>5} {'OOS_WR':>6} {'DD':>5}")
    print("-" * 75)

    summary = {}
    for offset in OFFSETS:
        row_oos = []
        for sym in FL.ASSETS:
            if not Path(FL.ASSETS[sym]).exists():
                continue
            a, m1 = FL.load(sym)
            df = run_with_offset(a, GENS, m1, offset_atr=offset)
            s = stats(df)
            marker = " ←" if offset == 0.0 else ""
            print(f"{offset:>8.2f} | {sym:<8} {s['n']:>5} {s['n_oos']:>5} "
                  f"{s['avgR']:>+8.3f} {s['oosR']:>+9.3f} "
                  f"{s['wr']:>4.0f}% {s['oos_wr']:>5.0f}% {s['dd']:>4.1f}%{marker}")
            row_oos.append(s['oosR'])
        avg_oos = sum(row_oos) / len(row_oos) if row_oos else 0
        summary[offset] = avg_oos
        print(f"{'':>8} | {'PORTFOLIO avg OOS':>20}  {avg_oos:>+9.3f}")
        print()

    print("\n── Resumen OOS por offset ──")
    base = summary.get(0.0, 1e-9)
    for off, v in summary.items():
        bar = "█" * int(max(0, v) * 10)
        print(f"  offset={off:.2f}  OOS={v:+.3f}  ({100*(v-base)/abs(base):+.1f}% vs base)  {bar}")
