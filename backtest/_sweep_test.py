"""
_sweep_test.py — Concepto SMC "Entrada por Barrido de Liquidez".
El wick perfora un mínimo/máximo clave (barre stops) y el cierre lo rechaza
(reversión) → entrada TAKER en la dirección opuesta. Stop bajo el wick, target
estructural. ¿Agrega avgR sobre el sistema A+B, o el poc_ob ya lo captura?
"""
import sys; from pathlib import Path
import numpy as np
sys.path.insert(0, "backtest")
import _listas2 as L2
from _strategy_ab import run_system, stats
from _audit_mirror import gen_h21_short

PARQ = {"BTCUSDT": "data/bybit-perp/processed/btcusdt_perp_m1.parquet",
        "ETHUSDT": "E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet",
        "SOLUSDT": "E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"}

def gen_sweep():
    def g(a, i):
        out = []
        h, l, c = a.h[i], a.l[i], a.c[i]; atr = a.atr[i]
        if atr <= 0: return out
        sl, sh = a.swing_low_50[i], a.swing_high_50[i]
        pdl, pdh = a.prev_day_low[i], a.prev_day_high[i]
        # SWEEP LOW → LONG: wick perfora un mínimo, cierre lo recupera (rechazo)
        for lvl in (sl, pdl):
            if np.isfinite(lvl) and l < lvl and c > lvl and (lvl - l) > 0.1*atr:
                entry = c; stop = l - 0.25*atr
                tp1, tp2 = L2.struct_target(a, i, "long", entry)
                if np.isfinite(tp2) and stop < entry < tp2:
                    out.append(("long", entry, stop, tp1, tp2, "sweep")); break
        # SWEEP HIGH → SHORT
        for lvl in (sh, pdh):
            if np.isfinite(lvl) and h > lvl and c < lvl and (h - lvl) > 0.1*atr:
                entry = c; stop = h + 0.25*atr
                tp1, tp2 = L2.struct_target(a, i, "short", entry)
                if np.isfinite(tp2) and tp2 < entry < stop:
                    out.append(("short", entry, stop, tp1, tp2, "sweep")); break
        return out
    return g

for sym in (sys.argv[1:] or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]):
    if not Path(PARQ[sym]).exists(): print(f"SKIP {sym}"); continue
    L2.M1 = Path(PARQ[sym]); t = L2.load2(15, start_ms=0); a = L2.A2(t); m1 = L2.load_m1_exit(start_ms=0)
    # A+B base
    base = run_system(a, [L2.gen_h5(), L2.gen_h21(), gen_h21_short()], m1, 15, mode="routed", max_day=4, cooldown=3)
    sb = stats(base[base.oos])
    # SWEEP solo (entrada taker, salida M1 fade)
    sw = L2.run_level_m1exit(a, gen_sweep(), m1, timeout_min=24*60, mode="taker", tf_min=15,
                             cooldown=3, max_day=4, min_rr=1.2)
    import pandas as pd
    swdf = pd.DataFrame(sw)
    swo = swdf[swdf.oos] if len(swdf) else swdf
    # A+B + sweep combinado
    comb = run_system(a, [L2.gen_h5(), L2.gen_h21(), gen_h21_short(), gen_sweep()], m1, 15, mode="routed", max_day=4, cooldown=3)
    sc = stats(comb[comb.oos])
    R = 5.0
    print(f"\n{'='*56}\n  {sym}\n{'='*56}")
    print(f"  A+B base:        avgR {sb['oosA']:+.3f}  net$ {sb['oosN']*R:+.0f}  n {sb['n']}")
    if len(swo):
        print(f"  SWEEP solo:      avgR {swo.r.mean():+.3f}  net$ {swo.r.sum()*R:+.0f}  WR {(swo.r>0).mean()*100:.0f}%  n {len(swo)}")
    else:
        print("  SWEEP solo:      sin trades")
    print(f"  A+B + SWEEP:     avgR {sc['oosA']:+.3f}  net$ {sc['oosN']*R:+.0f}  n {sc['n']}   (Δ avgR {sc['oosA']-sb['oosA']:+.3f})")
