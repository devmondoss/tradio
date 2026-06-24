"""
_early_signal.py — ¿El comportamiento en los primeros 30/60 min predice el
resultado final? Hipótesis del usuario: los ganadores grandes (8h+) arrancan
yendo a favor temprano; los perdedores arrancan en contra.

Para cada trade mide MFE/MAE en R en los primeros 30 y 60 min (en M1) y cruza
con el R final.  Si separa → es señal accionable (no tautológica).
"""
import sys; from pathlib import Path
sys.path.insert(0, "backtest")
import numpy as np, pandas as pd
import _listas2 as L2
from _strategy_ab import run_system
from _audit_mirror import gen_h21_short

def run(sym, path):
    L2.M1 = Path(path)
    t = L2.load2(15, start_ms=0); a = L2.A2(t)
    m1ts, m1h, m1l, m1c = L2.load_m1_exit(start_ms=0)
    df = run_system(a, [L2.gen_h5(), L2.gen_h21(), gen_h21_short()], (m1ts, m1h, m1l, m1c),
                    15, mode="routed", max_day=4, cooldown=3)
    df = df[df.oos].reset_index(drop=True)
    bar_ms = 15 * 60_000

    # excursión temprana en R
    for W in (30, 60):
        mfe, mae, rnow = [], [], []
        for _, t_ in df.iterrows():
            j0 = np.searchsorted(m1ts, t_.ts + bar_ms)
            jend = min(j0 + W, len(m1ts))
            if jend <= j0: mfe.append(0); mae.append(0); rnow.append(0); continue
            hi = m1h[j0:jend]; lo = m1l[j0:jend]; cl = m1c[jend-1]
            e, risk, long = t_.entry, t_.risk, t_.side == "long"
            if long:
                f = (hi.max() - e) / risk; ad = (lo.min() - e) / risk; rn = (cl - e) / risk
            else:
                f = (e - lo.min()) / risk; ad = (e - hi.max()) / risk; rn = (e - cl) / risk
            mfe.append(f); mae.append(ad); rnow.append(rn)
        df[f"mfe{W}"] = mfe; df[f"mae{W}"] = mae; df[f"r{W}"] = rnow

    print(f"\n{'='*64}\n  {sym}  (OOS, n={len(df)})\n{'='*64}")
    # correlaciones con R final
    for c in ["mfe30", "mae30", "r30", "mfe60", "mae60", "r60"]:
        print(f"  corr({c:>6}, R_final) = {np.corrcoef(df[c], df.r)[0,1]:+.3f}")

    # ¿filtrar por R no realizado a los 60 min separa?
    print("\n  -- avgR final según cómo venía a los 60 min --")
    for lab, mask in [
        ("r60 > +0.5R (ya ganando)", df.r60 > 0.5),
        ("r60 0 a +0.5R (plano+)",   (df.r60 >= 0) & (df.r60 <= 0.5)),
        ("r60 -0.5 a 0 (plano-)",    (df.r60 >= -0.5) & (df.r60 < 0)),
        ("r60 < -0.5R (ya perdiendo)", df.r60 < -0.5),
    ]:
        g = df[mask]
        if len(g): print(f"    {lab:<28} n={len(g):>4} avgR_final {g.r.mean():>+6.2f} WR {(g.r>0).mean()*100:>3.0f}% net$ {g.r.sum()*5:>+6.0f}")

    # regla accionable: cortar los que a los 60 min están < -0.5R
    keep = df[df.r60 >= -0.5]; cut = df[df.r60 < -0.5]
    print(f"\n  REGLA: cortar a los 60min si R<-0.5  (evita {len(cut)} trades)")
    print(f"    SIN regla (todos): avgR {df.r.mean():+.2f}  net$ {df.r.sum()*5:+.0f}  n {len(df)}")
    print(f"    CON regla (keep):  avgR {keep.r.mean():+.2f}  net$ {keep.r.sum()*5:+.0f}  n {len(keep)}")
    print(f"    lo cortado:        avgR {cut.r.mean():+.2f}  net$ {cut.r.sum()*5:+.0f}  n {len(cut)}")
    return df

if __name__ == "__main__":
    P = {"BTCUSDT": "data/bybit-perp/processed/btcusdt_perp_m1.parquet",
         "ETHUSDT": "E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet",
         "SOLUSDT": "E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"}
    for s in (sys.argv[1:] or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]):
        run(s, P[s])
