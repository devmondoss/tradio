"""
_audit_edge.py — Re-auditoría del edge de liquidez (duda "no cuadra")
=====================================================================
Tres preguntas sobre la config CONGELADA (gen_h5 + gen_h21, M15, volfilter, salida M1):
  Q1. ¿Cómo de minúsculos son los stops REALMENTE? (distribución de stopPct por componente)
  Q2. ¿Los home-runs se concentran en los trades de stop chico? (¿es artefacto stop-chico × RR?)
  Q3. Robustez: ¿sobrevive el edge a (a) un PISO de stop y (b) fills maker más pesimistas?

No cambia el motor. Reutiliza run()/gens del app backtest, con hooks para:
  - stop_floor_pct: fuerza riesgo mínimo = floor% del precio (aleja el stop si quedó muy pegado)
  - fill_margin_bps: cuántos bps DEBE penetrar el precio el nivel para asumir fill maker
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2

FEE_MAKER = L2.FEE_MAKER
FEE_TAKER = L2.FEE_TAKER
OOS_MS = L2.OOS_MS

def run_audit(a, gens, m1, tf_min, timeout_min=24*60, volfilter=True,
              margin=2.0, stop_floor_pct=0.0, honest_fee=False, entry_offset_bps=0.0):
    """Clon de liquidity_app_backtest.run() con stop_floor_pct y fill margin parametrizables.
    honest_fee: cobra TAKER (5.5bps/lado) en salidas a mercado (stop/breakeven/timeout),
    MAKER (2bps/lado) en entrada y salidas por límite (tp1/target). El motor base cobra maker a todo."""
    m1ts, m1h, m1l, m1c = m1; bar_ms = tf_min*60_000
    atr_med = pd.Series(a.atr).rolling(500, min_periods=50).median().shift(1).values
    trades = []
    for g in gens:
        cool = 0; dcount = {}
        for i in range(60, a.n-1):
            if i < cool or a.atr[i] <= 0: continue
            if volfilter and not (np.isfinite(atr_med[i]) and a.atr[i] > atr_med[i]): continue
            d = int(a.day[i])
            if dcount.get(d, 0) >= 2: continue
            for side, lvl, stop, tp1, tp2, kind in (g(a, i) or []):
                if not np.isfinite([lvl, stop, tp2]).all(): continue
                # offset de profundidad: colocar el límite MÁS adentro (mejor entrada, menos fills)
                place = lvl*(1 - entry_offset_bps/1e4) if side == "long" else lvl*(1 + entry_offset_bps/1e4)
                ref = a.c[i-1]
                if side == "long" and not (place < ref): continue
                if side == "short" and not (place > ref): continue
                if side == "long" and not (a.l[i] <= place - margin/1e4*place): continue
                if side == "short" and not (a.h[i] >= place + margin/1e4*place): continue
                entry = place
                # --- PISO DE STOP: si el stop quedó más cerca que floor%, alejarlo a floor% ---
                if stop_floor_pct > 0:
                    min_risk = stop_floor_pct/100.0 * entry
                    if abs(entry-stop) < min_risk:
                        stop = entry - min_risk if side == "long" else entry + min_risk
                risk = abs(entry-stop)
                if risk <= 0: continue
                if side == "long" and not (stop < entry < tp2): continue
                if side == "short" and not (tp2 < entry < stop): continue
                if abs(tp2-entry)/risk < 1.2: continue
                fee_r = FEE_MAKER*entry/risk; exit_px = None; reason = "timeout"
                cur_stop = stop; realized = 0.0; rem = 1.0; filled1 = False; p1 = 0.5 if tp1 else 0.0
                j0 = np.searchsorted(m1ts, a.ts[i]+bar_ms)
                jend = np.searchsorted(m1ts, a.ts[i]+bar_ms+timeout_min*60_000)
                exit_ts = None
                for j in range(j0, min(jend, len(m1ts))):
                    if side == "long":
                        if m1l[j] <= cur_stop:
                            realized += rem*((cur_stop-entry)/risk); reason = ("breakeven" if filled1 else "stop"); exit_ts = m1ts[j]; break
                        if not filled1 and tp1 and m1h[j] >= tp1:
                            realized += p1*((tp1-entry)/risk); rem -= p1; filled1 = True; cur_stop = entry
                        if m1h[j] >= tp2:
                            realized += rem*((tp2-entry)/risk); reason = "target"; exit_px = tp2; exit_ts = m1ts[j]; break
                    else:
                        if m1h[j] >= cur_stop:
                            realized += rem*((entry-cur_stop)/risk); reason = ("breakeven" if filled1 else "stop"); exit_ts = m1ts[j]; break
                        if not filled1 and tp1 and m1l[j] <= tp1:
                            realized += p1*((entry-tp1)/risk); rem -= p1; filled1 = True; cur_stop = entry
                        if m1l[j] <= tp2:
                            realized += rem*((entry-tp2)/risk); reason = "target"; exit_px = tp2; exit_ts = m1ts[j]; break
                else:
                    jj = min(jend, len(m1ts))-1
                    if jj <= j0: continue
                    px = m1c[jj]; realized += rem*(((px-entry) if side == "long" else (entry-px))/risk); exit_ts = m1ts[jj]
                if honest_fee:
                    mk = FEE_MAKER/2.0; tk = FEE_TAKER/2.0   # por lado
                    exit_side = mk if reason == "target" else tk   # stop/breakeven/timeout = mercado = taker
                    fee_frac = mk*1.0 + (mk*p1 if filled1 else 0.0) + exit_side*rem  # entrada + tp1 + salida final
                    fee_r = fee_frac*entry/risk
                r = realized - fee_r
                trades.append(dict(ts=int(a.ts[i]), side=side, r=r, kind=kind, reason=reason,
                                   stopPct=100*risk/entry, entry=entry, risk=risk,
                                   exit_ts=int(exit_ts) if exit_ts else int(a.ts[i]),
                                   oos=int(a.ts[i]) >= OOS_MS))
                cool = i+6; dcount[d] = dcount.get(d, 0)+1; break
    return pd.DataFrame(trades)


def summ(df, label):
    if len(df) == 0:
        print(f"{label:<34} SIN TRADES"); return
    o = df[df.oos]
    wr = 100*(df.r > 0).mean(); wro = 100*(o.r > 0).mean() if len(o) else float('nan')
    print(f"{label:<34} n={len(df):>4} (OOS {len(o):>3}) | WR {wr:4.1f}% | avgR {df.r.mean():+.3f} "
          f"| netR {df.r.sum():+6.1f} | OOS avgR {o.r.mean() if len(o) else 0:+.3f} netR {o.r.sum() if len(o) else 0:+6.1f}")


def main():
    tf = 15
    t = L2.load2(tf, start_ms=L2.TICK_MS)
    a = L2.A2(t)
    m1 = L2.load_m1_exit(start_ms=L2.TICK_MS)
    span = (a.ts.max()-a.ts.min())/86_400_000
    print(f"TF=M{tf} span={span:.0f}d  OOS>=2026-03-01  (gen_h5 + gen_h21, volfilter ON)\n")

    # ===== BASE =====
    base = run_audit(a, [L2.gen_h5(), L2.gen_h21()], m1, tf)
    print("=== BASELINE (config congelada) ===")
    summ(base, "BASE")
    summ(base[base.kind == "H5"], "  H5 (POC order-block)")
    summ(base[base.kind == "H21"], "  H21 (POC defendido)")

    # ===== Q1. Distribución de stops =====
    print("\n=== Q1. Distribución de stopPct (% del precio) ===")
    for lbl, sub in [("TODO", base), ("H5", base[base.kind == "H5"]), ("H21", base[base.kind == "H21"])]:
        if len(sub) == 0: continue
        q = sub.stopPct.quantile([.05, .25, .5, .75, .95]).values
        print(f"  {lbl:<6} p5={q[0]:.3f}%  p25={q[1]:.3f}%  med={q[2]:.3f}%  p75={q[3]:.3f}%  p95={q[4]:.3f}%  "
              f"min={sub.stopPct.min():.3f}%  max={sub.stopPct.max():.3f}%")
    tiny = base[base.stopPct < 0.10]
    print(f"  Trades con stop < 0.10%: {len(tiny)}/{len(base)} ({100*len(tiny)/len(base):.0f}%)  "
          f"avgR={tiny.r.mean():+.3f}  netR={tiny.r.sum():+.1f}")

    # ===== Q2. ¿Home-runs = stops chicos? =====
    print("\n=== Q2. Concentración: ¿los home-runs vienen de stops minúsculos? ===")
    b = base.sort_values("r", ascending=False)
    top5 = b.head(5)
    print(f"  Top-5 trades por R: netR={top5.r.sum():+.1f} ({100*top5.r.sum()/base.r.sum():.0f}% del netR total)")
    print("    " + " | ".join(f"{row.kind} R={row.r:+.1f} stop={row.stopPct:.3f}%" for _, row in top5.iterrows()))
    print(f"  avgR SIN top-5: {b.iloc[5:].r.mean():+.3f}  (vs BASE {base.r.mean():+.3f})")
    # correlación: ¿R alto correlaciona con stop chico?
    corr = np.corrcoef(base.stopPct, base.r)[0, 1]
    print(f"  corr(stopPct, R) = {corr:+.3f}  (negativo fuerte = el edge VIENE del stop chico)")
    # winners grandes: stop medio de los R>3 vs el resto
    big = base[base.r > 3]; rest = base[base.r <= 3]
    if len(big):
        print(f"  R>3R: n={len(big)}  stop medio={big.stopPct.mean():.3f}%   |   R<=3R: stop medio={rest.stopPct.mean():.3f}%")

    # ===== Q3a. Piso de stop =====
    print("\n=== Q3a. ROBUSTEZ — piso de stop (aleja stops minúsculos) ===")
    summ(base, "floor 0.00% (BASE)")
    for floor in [0.10, 0.15, 0.20, 0.30, 0.50]:
        df = run_audit(a, [L2.gen_h5(), L2.gen_h21()], m1, tf, stop_floor_pct=floor)
        summ(df, f"floor {floor:.2f}%")

    # ===== Q3b. Fills pesimistas =====
    print("\n=== Q3b. ROBUSTEZ — fill maker pesimista (bps que el precio DEBE penetrar el nivel) ===")
    for mg in [2.0, 5.0, 10.0, 20.0]:
        df = run_audit(a, [L2.gen_h5(), L2.gen_h21()], m1, tf, margin=mg)
        summ(df, f"fill margin {mg:.0f} bps")

    # ===== Q3c. Combinado (piso 0.20% + fill 10bps) =====
    print("\n=== Q3c. COMBINADO realista (piso 0.20% + fill 10 bps) ===")
    df = run_audit(a, [L2.gen_h5(), L2.gen_h21()], m1, tf, margin=10.0, stop_floor_pct=0.20)
    summ(df, "piso 0.20% + fill 10bps")


if __name__ == "__main__":
    main()
