"""
_fill_analysis.py — ¿Qué son los trades que no llenan?
=======================================================
1. Simula fill real: señal en bar i, orden en lvl, espera M1 data hasta K barras.
   → fill_rate real vs backtest (asumido 100%).
2. Para los NO-llenados: ¿a dónde fue el precio? ¿ganamos algo perdiendo esos fills?
3. Sweep de "lifetime de la orden" (1..12 M15 bars): fill_rate vs avgR.
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


def run_fill_model(a, gens, m1, order_lifetime_bars=1,
                   trail_atr=6.0, stop_scale=0.8,
                   cooldown=6, max_day=2, margin=2.0, stop_floor_pct=0.15,
                   atr_win=500, use_partial=True, p1_frac=0.5):
    """
    Simula fill real: la orden espera hasta `order_lifetime_bars` barras M15.
    Si M1 toca el nivel dentro de ese tiempo → fill. Si no → no fill (se registra).
    """
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
                # fill condition M15 (señal mínima: barra tocó el área)
                if side == "long"  and not (a.l[i] <= lvl - margin / 1e4 * lvl): continue
                if side == "short" and not (a.h[i] >= lvl + margin / 1e4 * lvl): continue

                entry = lvl; atr0 = float(a.atr[i])
                stop = stop_gen
                if stop_scale != 1.0:
                    stop = lvl - stop_scale * (lvl - stop_gen) if side == "long" else lvl + stop_scale * (stop_gen - lvl)
                if stop_floor_pct > 0:
                    mr = stop_floor_pct / 100.0 * entry
                    if abs(entry - stop) < mr:
                        stop = entry - mr if side == "long" else entry + mr
                risk = abs(entry - stop)
                if risk <= 0 or abs(tp2 - entry) / risk < 1.2: continue

                # ── FILL SIMULATION ──────────────────────────────────────────────
                # Buscar en M1 si el precio vuelve al nivel dentro de lifetime barras
                j_start = np.searchsorted(m1ts, a.ts[i] + bar_ms)          # siguiente M15
                j_deadline = np.searchsorted(m1ts, a.ts[i] + (order_lifetime_bars + 1) * bar_ms)

                fill_j = None
                for j in range(j_start, min(j_deadline, len(m1ts))):
                    if side == "long"  and m1l[j] <= entry:
                        fill_j = j; break
                    if side == "short" and m1h[j] >= entry:
                        fill_j = j; break

                filled = fill_j is not None

                if not filled:
                    # Registrar no-fill: ¿a dónde fue el precio en las próximas 4h?
                    j_check = np.searchsorted(m1ts, a.ts[i] + 4 * 60 * 60_000)
                    if j_check < len(m1ts) and j_check > j_start:
                        future_low  = float(m1l[j_start:j_check].min())
                        future_high = float(m1h[j_start:j_check].max())
                        if side == "long":
                            # Si hubiera esperado más, ¿habría llenado y ganado?
                            would_fill  = future_low  <= entry
                            would_tp    = future_high >= tp2
                            price_move  = (m1c[j_check - 1] - entry) / risk  # cuánto R se movió
                        else:
                            would_fill  = future_high >= entry
                            would_tp    = future_low  <= tp2
                            price_move  = (entry - m1c[j_check - 1]) / risk
                        trades.append(dict(
                            ts=int(a.ts[i]), side=side, filled=False, r=None,
                            would_fill=would_fill, would_tp=would_tp,
                            price_move_r=round(price_move, 3),
                            oos=int(a.ts[i]) >= OOS_MS
                        ))
                    cool = i + cooldown; dcount[d] = dcount.get(d, 0) + 1; break

                # ── TRADE (llenado) ──────────────────────────────────────────────
                j0   = fill_j
                jend = np.searchsorted(m1ts, m1ts[fill_j] + timeout_ms)
                chop = str(a.reg[i]).lower() in ("chop", "range", "balance")
                use_fade = chop

                if tp2_cap_r := 0.0:  # no cap en este análisis
                    pass

                res = None
                if use_fade:
                    cur = stop; realized = 0.0; rem = 1.0; f1 = False
                    p1 = p1_frac if (tp1 and use_partial) else 0.0; reason = "timeout"
                    for j in range(j0 + 1, min(jend, len(m1ts))):
                        if side == "long":
                            if m1l[j] <= cur: realized += rem * ((cur - entry) / risk); reason = "stop"; break
                            if use_partial and not f1 and tp1 and m1h[j] >= tp1:
                                realized += p1 * ((tp1 - entry) / risk); rem -= p1; f1 = True; cur = entry
                            if m1h[j] >= tp2: realized += rem * ((tp2 - entry) / risk); reason = "target"; break
                        else:
                            if m1h[j] >= cur: realized += rem * ((entry - cur) / risk); reason = "stop"; break
                            if use_partial and not f1 and tp1 and m1l[j] <= tp1:
                                realized += p1 * ((entry - tp1) / risk); rem -= p1; f1 = True; cur = entry
                            if m1l[j] <= tp2: realized += rem * ((entry - tp2) / risk); reason = "target"; break
                    else:
                        jj = min(jend, len(m1ts)) - 1
                        if jj <= j0: continue
                        px = m1c[jj]; realized += rem * (((px - entry) if side == "long" else (entry - px)) / risk)
                    exit_side = MK if reason == "target" else TK
                    fee_r = (MK + (MK * p1 if f1 else 0) + exit_side * rem) * entry / risk
                    res = realized - fee_r
                else:
                    fee_r = (MK + TK) * entry / risk; best = entry; trail = stop
                    for j in range(j0 + 1, min(jend, len(m1ts))):
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

                trades.append(dict(
                    ts=int(a.ts[i]), side=side, filled=True, r=res,
                    would_fill=True, would_tp=None, price_move_r=None,
                    oos=int(a.ts[i]) >= OOS_MS
                ))
                cool = i + cooldown; dcount[d] = dcount.get(d, 0) + 1; break
    return pd.DataFrame(trades)


if __name__ == "__main__":
    GENS = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]

    # ── 1. Análisis de no-fills ───────────────────────────────────────────────
    print("=" * 70)
    print("ANÁLISIS DE NO-FILLS (lifetime=1 barra M15)")
    print("¿A dónde fue el precio en las próximas 4h para trades que no llenaron?")
    print("=" * 70)

    for sym in FL.ASSETS:
        if not Path(FL.ASSETS[sym]).exists(): continue
        a, m1 = FL.load(sym)
        df = run_fill_model(a, GENS, m1, order_lifetime_bars=1)
        filled   = df[df.filled]
        nofill   = df[~df.filled]

        fill_rate = 100 * len(filled) / len(df) if len(df) else 0
        print(f"\n{sym}: {len(df)} señales → {len(filled)} fills ({fill_rate:.0f}%),  {len(nofill)} no-fills")

        if len(filled):
            o_f = filled[filled.oos]
            print(f"  Fills OOS:    n={len(o_f)}  avgR={o_f.r.mean():+.3f}  WR={100*(o_f.r>0).mean():.0f}%")

        if len(nofill):
            nf_oos = nofill[nofill.oos]
            wf  = 100 * nf_oos.would_fill.mean()
            wtp = 100 * nf_oos.would_tp.mean()
            avg_move = nf_oos.price_move_r.mean()
            print(f"  No-fills OOS: n={len(nf_oos)}")
            print(f"    → {wf:.0f}% habrían llenado si orden vivía 4h más")
            print(f"    → {wtp:.0f}% habrían tocado el TP después")
            print(f"    → precio se movió {avg_move:+.2f}R en 4h (positivo=a favor)")

    # ── 2. Sweep lifetime de la orden ────────────────────────────────────────
    print("\n" + "=" * 70)
    print("SWEEP: lifetime de la orden (cuántas barras M15 vive la orden)")
    print("=" * 70)
    print(f"{'lifetime':>9} | {'sym':<8} {'signals':>8} {'fills':>6} {'fill%':>6} {'OOS_avgR':>9}")
    print("-" * 60)

    for lifetime in [1, 2, 4, 8, 16]:
        for sym in FL.ASSETS:
            if not Path(FL.ASSETS[sym]).exists(): continue
            a, m1 = FL.load(sym)
            df = run_fill_model(a, GENS, m1, order_lifetime_bars=lifetime)
            filled = df[df.filled]
            o = filled[filled.oos]
            fill_pct = 100 * len(filled) / len(df) if len(df) else 0
            avg_r = o.r.mean() if len(o) else 0
            print(f"{lifetime:>9} | {sym:<8} {len(df):>8} {len(filled):>6} {fill_pct:>5.0f}%  {avg_r:>+9.3f}")
        print()
