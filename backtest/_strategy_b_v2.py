"""
_strategy_b_v2.py — B corregida: ENTRADA de A (límite en el nivel) + gestión de B (trailing stop)
================================================================================================
B-breakout falló (perseguir rupturas = falsos breaks). Pista: los movimientos grandes EMPIEZAN en
pivotes, y la entrada de A (maker en el nivel) te mete justo ahí. El problema era la SALIDA (migaja).
Aquí: misma entrada que A, pero exit = TRAILING STOP (dejar correr). Fee: maker entrada + taker salida.
Compara contra A-actual y mide cuántos de los 269 movimientos grandes captura.
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


def run_a_entry_trailing(a, gens, m1, tf_min, trail_atr=2.0, volfilter=True,
                         timeout_min=24*60, cooldown=6, max_day=2, margin=2.0, stop_floor_pct=0.15):
    """Entrada como A (límite maker en el nivel) + SALIDA trailing stop (ride). maker in / taker out."""
    m1ts, m1h, m1l, m1c = m1; bar_ms = tf_min*60_000
    atr_med = pd.Series(a.atr).rolling(500, min_periods=50).median().shift(1).values
    mk, tk = FEE_MAKER/2, FEE_TAKER/2
    trades = []
    for g in gens:
        cool = 0; dcount = {}
        for i in range(60, a.n-1):
            if i < cool or a.atr[i] <= 0: continue
            if volfilter and not (np.isfinite(atr_med[i]) and a.atr[i] > atr_med[i]): continue
            d = int(a.day[i])
            if dcount.get(d, 0) >= max_day: continue
            for side, lvl, stop, tp1, tp2, kind in (g(a, i) or []):
                if not np.isfinite([lvl, stop]).all(): continue
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
                if risk <= 0: continue
                fee_r = (mk + tk)*entry/risk
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
                cool = i+cooldown; dcount[d] = dcount.get(d, 0)+1; break
    return pd.DataFrame(trades)


def summ(df, lbl):
    o = df[df.oos]
    print(f"{lbl:<34} n={len(df):>4} | WR {100*(df.r>0).mean():4.1f}% | avgR {df.r.mean():+.3f} "
          f"| netR {df.r.sum():+6.1f} | OOS avgR {o.r.mean() if len(o) else 0:+.3f} | maxR {df.r.max():+.1f}")


def main():
    t = L2.load2(TF, start_ms=L2.TICK_MS); a = L2.A2(t)
    m1 = L2.load_m1_exit(start_ms=L2.TICK_MS)
    gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]
    print(f"B-v2: entrada de A (maker en nivel) + trailing stop · M{TF}\n")

    print("=== Trailing ATR (dejar correr lo que A entra bien) ===")
    best = None; bn = -1e9
    for tr in [1.5, 2.0, 3.0, 4.0]:
        df = run_a_entry_trailing(a, gens, m1, TF, trail_atr=tr)
        summ(df, f"A-entry + trailing {tr}·ATR")
        if df.r.sum() > bn: bn = df.r.sum(); best = df; bt = tr

    # capturas de movimientos grandes
    piv = zigzag(a.c, 2.0); moves = []
    for k in range(len(piv)-1):
        i0, p0, _ = piv[k]; i1, p1, _ = piv[k+1]
        if abs(p1-p0)/p0*100 >= 2.0 and i0 >= 60:
            moves.append(dict(start=i0, side='long' if p1 > p0 else 'short'))
    caught = sum(1 for mv in moves if len(best[(best.bar >= mv['start']-1) & (best.bar <= mv['start']+6)
                & (best.side == mv['side']) & (best.r > 2)]) > 0)
    print(f"\n  Mejor trailing = {bt}·ATR")
    print(f"  Movimientos grandes capturados (R>2): {caught}/{len(moves)} ({100*caught/len(moves):.0f}%)")
    print(f"  (A-actual con migaja capturaba completos: 1%)")
    r = best.r.values
    print(f"  perfil R: pérdidas {100*(r<0).mean():.0f}% (media {r[r<0].mean():+.2f}) | "
          f"ganancias {100*(r>0).mean():.0f}% (media {r[r>0].mean():+.2f}) | maxR {r.max():+.1f}")


if __name__ == "__main__":
    main()
