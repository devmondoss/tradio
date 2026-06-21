"""
_strategy_b.py — PROTOTIPO de la estrategia B · MOMENTUM / Continuación de ruptura
=================================================================================
Opuesta a A (fader). B NO predice: reacciona a un movimiento que YA arrancó y lo monta con
trailing stop (pérdidas chicas en falsos arranques, ganancias grandes en tendencias reales).

ENTRADA (taker, agresiva): el cierre M15 ROMPE el máx/mín de las últimas N barras + expansión de
volumen (vr) + delta a favor. STOP inicial = 1·ATR. SALIDA = trailing stop (best ± k·ATR), M1.
Fee TAKER 11bps RT (momentum persigue, no es maker).

Mide: edge (WR/avgR/netR, IS/OOS) + cuántos de los movimientos grandes (zigzag ≥2%) captura.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _listas import FEE_TAKER, OOS_MS
from _audit_missed import zigzag

TF = 15


def gen_breakout(N=20, vr_min=1.5, atr_stop=1.0):
    """Ruptura del rango de N barras + volumen expandido + delta a favor."""
    def g(a, i):
        if i < N: return []
        ph = np.max(a.h[i-N:i]); pl = np.min(a.l[i-N:i])
        out = []
        if a.c[i] > ph and a.vr[i] >= vr_min and a.delta[i] > 0:
            out.append(("long", a.c[i], a.c[i]-atr_stop*a.atr[i], "breakout"))
        elif a.c[i] < pl and a.vr[i] >= vr_min and a.delta[i] < 0:
            out.append(("short", a.c[i], a.c[i]+atr_stop*a.atr[i], "breakout"))
        return out
    return g


def run_trailing(a, gen, m1, tf_min, trail_atr=2.0, timeout_min=24*60, cooldown=6, max_day=3):
    m1ts, m1h, m1l, m1c = m1; bar_ms = tf_min*60_000; fee = FEE_TAKER
    trades = []; cool = 0; dcount = {}
    for i in range(60, a.n-1):
        if i < cool or a.atr[i] <= 0: continue
        d = int(a.day[i])
        if dcount.get(d, 0) >= max_day: continue
        for side, entry, stop, kind in (gen(a, i) or []):
            risk = abs(entry-stop); atr0 = a.atr[i]
            if risk <= 0: continue
            fee_r = fee*entry/risk
            j0 = np.searchsorted(m1ts, a.ts[i]+bar_ms)
            jend = np.searchsorted(m1ts, a.ts[i]+bar_ms+timeout_min*60_000)
            best = entry; trail = stop; res = None
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
            trades.append(dict(ts=int(a.ts[i]), bar=i, side=side, r=res, oos=int(a.ts[i]) >= OOS_MS))
            cool = i+cooldown; dcount[d] = dcount.get(d, 0)+1
    return pd.DataFrame(trades)


def summ(df, lbl):
    if len(df) == 0: print(f"{lbl}: SIN TRADES"); return
    o = df[df.oos]
    big = (df.r > 3).sum()
    print(f"{lbl:<30} n={len(df):>4} | WR {100*(df.r>0).mean():4.1f}% | avgR {df.r.mean():+.3f} "
          f"| netR {df.r.sum():+6.1f} | OOS avgR {o.r.mean() if len(o) else 0:+.3f} | maxR {df.r.max():+.1f} | R>3: {big}")


def main():
    t = L2.load2(TF, start_ms=L2.TICK_MS); a = L2.A2(t)
    m1 = L2.load_m1_exit(start_ms=L2.TICK_MS)
    span = (a.ts.max()-a.ts.min())/86_400_000
    print(f"B · MOMENTUM (ruptura + trailing) · M{TF} · {span:.0f}d · fee TAKER 11bps\n")

    print("=== Barrido de config (N rango, vr mín, trailing ATR) ===")
    best_df = None; best_net = -1e9; best_cfg = None
    for N in [10, 20, 40]:
        for vr in [1.5, 2.0]:
            for trail in [1.5, 2.5]:
                df = run_trailing(a, gen_breakout(N=N, vr_min=vr), m1, TF, trail_atr=trail)
                summ(df, f"N={N} vr={vr} trail={trail}")
                if len(df) and df.r.sum() > best_net:
                    best_net = df.r.sum(); best_df = df; best_cfg = (N, vr, trail)

    print(f"\n=== Mejor config: N={best_cfg[0]} vr={best_cfg[1]} trail={best_cfg[2]} ===")
    summ(best_df, "MEJOR B")
    # distribución (¿skew positivo? = pocas grandes pagan muchas chicas)
    r = best_df.r.values
    print(f"  perfil R: pérdidas {100*(r<0).mean():.0f}% (media {r[r<0].mean():+.2f}) | "
          f"ganancias {100*(r>0).mean():.0f}% (media {r[r>0].mean():+.2f}) | skew={pd.Series(r).skew():+.2f}")

    # ¿cuántos de los movimientos grandes captura B?
    piv = zigzag(a.c, 2.0); moves = []
    for k in range(len(piv)-1):
        i0, p0, _ = piv[k]; i1, p1, _ = piv[k+1]
        if abs(p1-p0)/p0*100 >= 2.0 and i0 >= 60:
            moves.append(dict(start=i0, end=i1, side='long' if p1 > p0 else 'short'))
    caught = 0
    for mv in moves:
        tr = best_df[(best_df.bar >= mv['start']-2) & (best_df.bar <= mv['start']+8) & (best_df.side == mv['side'])]
        if len(tr) and (tr.r > 0).any(): caught += 1
    print(f"\n  Movimientos grandes (≥2%) capturados por B: {caught}/{len(moves)} ({100*caught/len(moves):.0f}%)")
    print(f"  (recordatorio: A capturaba completos solo el 1%)")


if __name__ == "__main__":
    main()
