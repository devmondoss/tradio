"""
_strategy_ab.py — SISTEMA A+B: una entrada (límite en nivel), exit ENRUTADO por régimen
=======================================================================================
Misma entrada que A (maker en el nivel de volumen). La GESTIÓN se elige por el régimen en la barra:
  • Chop/Range     → FADE (parcial 50% en tp1 → breakeven → target estructural)  [gana en rangos]
  • Trend/Expansion→ TRAILING stop (dejar correr la continuación)                 [gana en tendencias]
Fees honestos: maker entrada; fade = taker en stop/BE/timeout; trailing = taker en la salida.
Compara A-sola (todo fade) vs B-sola (todo trailing) vs A+B (enrutado). 365 días.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _listas import FEE_MAKER, FEE_TAKER, OOS_MS
from _audit_missed import zigzag
from _audit_mirror import gen_h21_short

TF = 15
MK, TK = FEE_MAKER/2, FEE_TAKER/2


def is_chop(reg):
    return str(reg).lower() in ("chop", "range", "balance", "consolidation")


def run_system(a, gens, m1, tf_min, mode="routed", trail_atr=4.0, volfilter=True,
               timeout_min=24*60, cooldown=6, max_day=2, margin=2.0, stop_floor_pct=0.15, min_range=0.5,
               chop_mask=None, tp2_cap_r=0.0, atr_mult=1.0, atr_win=500, p1_frac=0.5):
    """mode: 'fade' (todo A), 'trail' (todo B), 'routed' (por régimen).
    chop_mask: array bool por barra (True=fade/rango). Si None, usa la columna 'regime' (tosca).
    tp2_cap_r: si >0 limita tp2 a entry ± tp2_cap_r*risk (0=sin cap, usa target estructural).
    atr_mult: filtro ATR exige atr > atr_mult*mediana. atr_win: ventana de la mediana.
    p1_frac: fracción cerrada en TP1 (parcial)."""
    m1ts, m1h, m1l, m1c = m1; bar_ms = tf_min*60_000
    atr_med = pd.Series(a.atr).rolling(atr_win, min_periods=50).median().shift(1).values
    trades = []
    for g in gens:
        cool = 0; dcount = {}
        for i in range(60, a.n-1):
            if i < cool or a.atr[i] <= 0: continue
            if volfilter and not (np.isfinite(atr_med[i]) and a.atr[i] > atr_mult * atr_med[i]): continue
            d = int(a.day[i])
            if dcount.get(d, 0) >= max_day: continue
            for side, lvl, stop, tp1, tp2, kind in (g(a, i) or []):
                if not np.isfinite([lvl, stop, tp2]).all(): continue
                ref = a.c[i-1]
                if side == "long" and not (lvl < ref): continue
                if side == "short" and not (lvl > ref): continue
                if side == "long" and not (a.l[i] <= lvl - margin/1e4*lvl): continue
                if side == "short" and not (a.h[i] >= lvl + margin/1e4*lvl): continue
                entry = lvl; atr0 = a.atr[i]
                if stop_floor_pct > 0:
                    mr = stop_floor_pct/100.0*entry
                    if abs(entry-stop) < mr: stop = entry-mr if side == "long" else entry+mr
                risk = abs(entry-stop)
                if risk <= 0 or abs(tp2-entry)/risk < 1.2: continue
                # Cap tp2 a un múltiplo fijo del riesgo (0 = sin cap)
                if tp2_cap_r > 0:
                    cap = entry + tp2_cap_r*risk if side == "long" else entry - tp2_cap_r*risk
                    if (side == "long" and tp2 > cap) or (side == "short" and tp2 < cap):
                        tp2 = cap
                        if tp1 is not None and not (min(entry,tp2) < tp1 < max(entry,tp2)):
                            tp1 = None
                # ¿qué gestión? (routed = por régimen; fade/trail = forzado)
                chop_here = chop_mask[i] if chop_mask is not None else is_chop(a.reg[i])
                use_fade = (mode == "fade") or (mode == "routed" and chop_here)
                j0 = np.searchsorted(m1ts, a.ts[i]+bar_ms)
                jend = np.searchsorted(m1ts, a.ts[i]+bar_ms+timeout_min*60_000)
                res = None
                if use_fade:
                    # rango mínimo solo para fades (no fadear migajas); trends se montan igual
                    if min_range > 0 and tp1 is not None and 100*abs(tp1-entry)/entry < min_range: continue
                    cur = stop; realized = 0.0; rem = 1.0; f1 = False; p1 = p1_frac if tp1 else 0.0; reason = "timeout"
                    for j in range(j0, min(jend, len(m1ts))):
                        if side == "long":
                            if m1l[j] <= cur: realized += rem*((cur-entry)/risk); reason = "be" if f1 else "stop"; break
                            if not f1 and tp1 and m1h[j] >= tp1: realized += p1*((tp1-entry)/risk); rem -= p1; f1 = True; cur = entry
                            if m1h[j] >= tp2: realized += rem*((tp2-entry)/risk); reason = "target"; break
                        else:
                            if m1h[j] >= cur: realized += rem*((entry-cur)/risk); reason = "be" if f1 else "stop"; break
                            if not f1 and tp1 and m1l[j] <= tp1: realized += p1*((entry-tp1)/risk); rem -= p1; f1 = True; cur = entry
                            if m1l[j] <= tp2: realized += rem*((entry-tp2)/risk); reason = "target"; break
                    else:
                        jj = min(jend, len(m1ts))-1
                        if jj <= j0: continue
                        px = m1c[jj]; realized += rem*(((px-entry) if side == "long" else (entry-px))/risk)
                    exit_side = MK if reason == "target" else TK
                    fee_r = (MK*1.0 + (MK*p1 if f1 else 0.0) + exit_side*rem)*entry/risk
                    res = realized - fee_r; gestion = "fade"
                else:
                    fee_r = (MK + TK)*entry/risk; best = entry; trail = stop
                    for j in range(j0, min(jend, len(m1ts))):
                        if side == "long":
                            best = max(best, m1h[j]); trail = max(trail, best-trail_atr*atr0)
                            if m1l[j] <= trail: res = (trail-entry)/risk - fee_r; break
                        else:
                            best = min(best, m1l[j]); trail = min(trail, best+trail_atr*atr0)
                            if m1h[j] >= trail: res = (entry-trail)/risk - fee_r; break
                    if res is None:
                        jj = min(jend, len(m1ts))-1
                        if jj <= j0: continue
                        px = m1c[jj]; res = ((px-entry) if side == "long" else (entry-px))/risk - fee_r
                    gestion = "trail"; reason = "trail"
                trades.append(dict(ts=int(a.ts[i]), bar=i, side=side, r=res, gestion=gestion,
                                   reason=reason, entry=entry, stop=stop, risk=risk,
                                   hold_min=int(j - j0), oos=int(a.ts[i]) >= OOS_MS))
                cool = i+cooldown; dcount[d] = dcount.get(d, 0)+1; break
    return pd.DataFrame(trades)


def stats(df):
    o = df[df.oos]; cap = 500.0; peak = 500.0; dd = 0.0
    for r in df.sort_values("ts").r.values:
        cap += 5*r; peak = max(peak, cap); dd = max(dd, (peak-cap)/peak)
    sh = df.r.mean()/(df.r.std()+1e-9)*np.sqrt(len(df))
    return dict(n=len(df), wr=100*(df.r > 0).mean(), avgR=df.r.mean(), netR=df.r.sum(),
                oosA=o.r.mean() if len(o) else 0, oosN=o.r.sum() if len(o) else 0,
                maxR=df.r.max(), dd=100*dd, sharpe=sh)


def line(lbl, s):
    print(f"{lbl:<22} n={s['n']:>4} | WR {s['wr']:4.1f}% | avgR {s['avgR']:+.3f} | netR {s['netR']:+6.1f} "
          f"| OOS avgR {s['oosA']:+.3f} netR {s['oosN']:+6.1f} | maxR {s['maxR']:+5.1f} | DD {s['dd']:4.1f}% | Sh {s['sharpe']:+.1f}")


def main():
    t = L2.load2(TF, start_ms=L2.TICK_MS); a = L2.A2(t)
    m1 = L2.load_m1_exit(start_ms=L2.TICK_MS)
    span = (a.ts.max()-a.ts.min())/86_400_000
    gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]
    print(f"SISTEMA A+B · M{TF} · {span:.0f}d · entrada maker en nivel · exit por régimen\n")
    print("régimen del dataset:", dict(pd.Series(a.reg).value_counts()))
    print()

    fade = run_system(a, gens, m1, TF, mode="fade")
    trail = run_system(a, gens, m1, TF, mode="trail")
    routed = run_system(a, gens, m1, TF, mode="routed")
    line("A sola (todo fade)", stats(fade))
    line("B sola (todo trail)", stats(trail))
    line("A+B (enrutado)", stats(routed))

    print(f"\n  reparto enrutado: {dict(routed.gestion.value_counts())}")
    print(f"  fades  -> avgR {routed[routed.gestion=='fade'].r.mean():+.3f} (n={(routed.gestion=='fade').sum()})")
    print(f"  trails -> avgR {routed[routed.gestion=='trail'].r.mean():+.3f} (n={(routed.gestion=='trail').sum()})")

    piv = zigzag(a.c, 2.0); moves = []
    for k in range(len(piv)-1):
        i0, p0, _ = piv[k]; i1, p1, _ = piv[k+1]
        if abs(p1-p0)/p0*100 >= 2.0 and i0 >= 60: moves.append(dict(start=i0, side='long' if p1 > p0 else 'short'))
    caught = sum(1 for mv in moves if len(routed[(routed.bar >= mv['start']-1) & (routed.bar <= mv['start']+6)
                & (routed.side == mv['side']) & (routed.r > 2)]) > 0)
    print(f"\n  Movimientos grandes capturados (R>2) por A+B: {caught}/{len(moves)} ({100*caught/len(moves):.0f}%)  (A sola: 1%)")


if __name__ == "__main__":
    main()
