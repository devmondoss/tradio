"""
audit_sol_gap.py — Diagnostico del gap IS/OOS de SOL (+1.64 IS vs +0.93 OOS)
==============================================================================
Hipotesis:
  1. Detector de regimen inestable en SOL (chop IS=84.8% vs OOS=88.8%)
  2. Trades trail peor en OOS que IS
  3. IS captura un periodo de mayor volatilidad/tendencia que no se repite OOS
  4. Generadores H5/H21/H21s se comportan distinto IS vs OOS

Uso: python -X utf8 backtest/audit_sol_gap.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _listas import OOS_MS, FEE_MAKER, FEE_TAKER
from _audit_mirror import gen_h21_short

ROOT    = Path(__file__).parent.parent
TF      = 15
MK, TK  = FEE_MAKER / 2, FEE_TAKER / 2
SOL_M1  = Path("E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet")
BTC_M1  = ROOT / "data/bybit-perp/processed/btcusdt_perp_m1.parquet"


def run_tagged(a, gens, m1, tf_min=15, trail_atr=4.0,
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
                gestion   = "fade" if chop_here else "trail"
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

                # walk-forward quarter
                qtr = pd.Timestamp(a.ts[i], unit="ms").quarter
                yr  = pd.Timestamp(a.ts[i], unit="ms").year

                trades.append(dict(
                    ts=int(a.ts[i]), side=side, r=res, gestion=gestion,
                    oos=int(a.ts[i]) >= OOS_MS, kind=kind,
                    atr_pct=100 * atr0 / entry,
                    regime=a.reg[i],
                    period=f"{yr}Q{qtr}",
                ))
                cool = i + cooldown; dcount[d] = dcount.get(d, 0) + 1; break

    return pd.DataFrame(trades)


def blk(label, df, indent=2):
    sp = " " * indent
    if len(df) == 0:
        print(f"{sp}{label:<40} n=0"); return
    wr = 100 * (df.r > 0).mean()
    print(f"{sp}{label:<40} n={len(df):>4}  WR={wr:4.1f}%  avgR={df.r.mean():+.3f}")


def main():
    results = {}
    for sym, path in [("BTC", BTC_M1), ("SOL", SOL_M1)]:
        print(f"\n{'='*65}")
        print(f"  {sym}")
        print(f"{'='*65}")

        L2.M1 = path
        t  = L2.load2(TF, start_ms=0)
        a  = L2.A2(t)
        m1 = L2.load_m1_exit(start_ms=0)
        gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]

        df = run_tagged(a, gens, m1)
        IS  = df[~df.oos]
        OOS = df[df.oos]

        print(f"  Total: {len(df)}  IS: {len(IS)}  OOS: {len(OOS)}")
        print(f"  IS  avgR={IS.r.mean():+.3f}  OOS avgR={OOS.r.mean():+.3f}  gap={OOS.r.mean()-IS.r.mean():+.3f}R")

        # ── 1. GAP POR GESTION ───────────────────────────────────────────────
        print(f"\n  --- GAP por gestion (fade vs trail) ---")
        for g in ["fade", "trail"]:
            is_g  = IS[IS.gestion == g]
            oos_g = OOS[OOS.gestion == g]
            if len(is_g) == 0: continue
            gap = (oos_g.r.mean() if len(oos_g) else 0) - is_g.r.mean()
            print(f"  {g:<8} IS n={len(is_g):>4} avgR={is_g.r.mean():+.3f} | "
                  f"OOS n={len(oos_g):>4} avgR={oos_g.r.mean() if len(oos_g) else 0:+.3f} | "
                  f"gap={gap:+.3f}")

        # ── 2. GAP POR GENERADOR ─────────────────────────────────────────────
        print(f"\n  --- GAP por generador ---")
        for k in df.kind.unique():
            is_k  = IS[IS.kind == k]
            oos_k = OOS[OOS.kind == k]
            gap = (oos_k.r.mean() if len(oos_k) else 0) - is_k.r.mean()
            print(f"  {k:<8} IS n={len(is_k):>4} avgR={is_k.r.mean():+.3f} | "
                  f"OOS n={len(oos_k):>4} avgR={oos_k.r.mean() if len(oos_k) else 0:+.3f} | "
                  f"gap={gap:+.3f}")

        # ── 3. WALK-FORWARD TRIMESTRAL ───────────────────────────────────────
        print(f"\n  --- Walk-forward trimestral ---")
        for p in sorted(df.period.unique()):
            sub = df[df.period == p]
            tag = " [OOS]" if sub.oos.any() else ""
            print(f"  {p}{tag:<8} n={len(sub):>4}  avgR={sub.r.mean():+.3f}  WR={100*(sub.r>0).mean():4.1f}%")

        # ── 4. ATR pct IS vs OOS ─────────────────────────────────────────────
        print(f"\n  --- ATR% del precio IS vs OOS ---")
        print(f"  IS  ATR%: median={IS.atr_pct.median():.3f}%  mean={IS.atr_pct.mean():.3f}%")
        print(f"  OOS ATR%: median={OOS.atr_pct.median():.3f}%  mean={OOS.atr_pct.mean():.3f}%")

        # ── 5. REGIMEN en trades IS vs OOS ───────────────────────────────────
        print(f"\n  --- Regimen en los trades ---")
        is_chop  = (IS.gestion == "fade").mean()
        oos_chop = (OOS.gestion == "fade").mean()
        print(f"  % trades fade: IS={is_chop:.1%}  OOS={oos_chop:.1%}")
        print(f"  % trades trail: IS={1-is_chop:.1%}  OOS={1-oos_chop:.1%}")

        results[sym] = df

    # ── COMPARATIVA BTC vs SOL gap ───────────────────────────────────────────
    print(f"\n\n{'='*65}")
    print("  COMPARATIVA: donde esta el gap real")
    print(f"{'='*65}")
    for sym, df in results.items():
        IS  = df[~df.oos]
        OOS = df[df.oos]
        fade_is   = IS[IS.gestion=="fade"].r.mean()   if len(IS[IS.gestion=="fade"])   else 0
        fade_oos  = OOS[OOS.gestion=="fade"].r.mean()  if len(OOS[OOS.gestion=="fade"])  else 0
        trail_is  = IS[IS.gestion=="trail"].r.mean()  if len(IS[IS.gestion=="trail"])  else 0
        trail_oos = OOS[OOS.gestion=="trail"].r.mean() if len(OOS[OOS.gestion=="trail"]) else 0
        print(f"  {sym}: fade  IS={fade_is:+.3f} OOS={fade_oos:+.3f} gap={fade_oos-fade_is:+.3f}")
        print(f"  {sym}: trail IS={trail_is:+.3f} OOS={trail_oos:+.3f} gap={trail_oos-trail_is:+.3f}")


if __name__ == "__main__":
    main()
