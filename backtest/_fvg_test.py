"""
_fvg_test.py — Fair Value Gap como 4º componente de nivel del sistema A+B.
=========================================================================
Tesis: un FVG (hueco de 3 velas por impulso) SIN rellenar es, como un POC, un
precio al que el mercado tiende a volver y reaccionar. Lo probamos como ENTRADA
límite maker (mismo motor/gestión A+B enrutada), NO como filtro.

FVG alcista: low[k] > high[k-2]  → hueco (soporte). Buy-limit en el TECHO del
  hueco (low[k]); stop bajo el hueco (high[k-2] - buf·ATR); target estructural.
FVG bajista: high[k] < low[k-2]  → espejo (resistencia). Sell-limit en el SUELO
  del hueco (high[k]); stop sobre el hueco.
Exige: hueco NO tocado desde que se formó (primer test = fresco) y el más reciente.

¿Agrega avgR sobre A+B, o el poc_ob/defendido ya captura esas zonas?
Uso: python backtest/_fvg_test.py [BTCUSDT ...]
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


def gen_fvg(W=50, buf=0.25, min_gap=0.1):
    """W: ventana atrás para buscar FVG. buf: colchón de stop en ATR.
    min_gap: tamaño mínimo del hueco en fracción de ATR (filtra ruido)."""
    def g(a, i):
        atr = a.atr[i]
        if atr <= 0 or i < 4: return []
        ref = a.c[i-1]
        lo = max(2, i - W)
        for k in range(i-1, lo-1, -1):
            # ── FVG alcista: low[k] > high[k-2] (hueco de impulso alcista = soporte)
            gb, gt = a.h[k-2], a.l[k]          # gb=suelo, gt=techo del hueco
            if gt - gb > min_gap*atr:
                seg = a.l[k+1:i]               # barras posteriores a la formación
                entered = seg.size > 0 and np.nanmin(seg) <= gt
                if not entered and a.l[i] <= gt and gt < ref:
                    stop = gb - buf*atr
                    tp1, tp2 = L2.struct_target(a, i, "long", gt)
                    if np.isfinite(tp2) and stop < gt < tp2:
                        return [("long", gt, stop, tp1, tp2, "fvg")]
            # ── FVG bajista: high[k] < low[k-2] (hueco de impulso bajista = resistencia)
            gt2, gb2 = a.l[k-2], a.h[k]        # gt2=techo, gb2=suelo del hueco
            if gt2 - gb2 > min_gap*atr:
                seg = a.h[k+1:i]
                entered = seg.size > 0 and np.nanmax(seg) >= gb2
                if not entered and a.h[i] >= gb2 and gb2 > ref:
                    stop = gt2 + buf*atr
                    tp1, tp2 = L2.struct_target(a, i, "short", gb2)
                    if np.isfinite(tp2) and tp2 < gb2 < stop:
                        return [("short", gb2, stop, tp1, tp2, "fvg")]
        return []
    return g


R = 5.0
for sym in (sys.argv[1:] or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]):
    if not Path(PARQ[sym]).exists(): print(f"SKIP {sym}"); continue
    L2.M1 = Path(PARQ[sym]); t = L2.load2(15, start_ms=0); a = L2.A2(t); m1 = L2.load_m1_exit(start_ms=0)
    base_gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]

    base = run_system(a, base_gens, m1, 15, mode="routed", max_day=4, cooldown=3)
    sb = stats(base[base.oos])

    fvg_only = run_system(a, [gen_fvg()], m1, 15, mode="routed", max_day=4, cooldown=3)
    fo = fvg_only[fvg_only.oos]

    comb = run_system(a, base_gens + [gen_fvg()], m1, 15, mode="routed", max_day=4, cooldown=3)
    sc = stats(comb[comb.oos])

    print(f"\n{'='*56}\n  {sym}\n{'='*56}")
    print(f"  A+B base:        avgR {sb['oosA']:+.3f}  net$ {sb['oosN']*R:+.0f}  WR {sb['wr']:.0f}%  n {sb['n']}")
    if len(fo):
        print(f"  FVG solo:        avgR {fo.r.mean():+.3f}  net$ {fo.r.sum()*R:+.0f}  WR {(fo.r>0).mean()*100:.0f}%  n {len(fo)}"
              f"  (fade {(fo.gestion=='fade').sum()}/trail {(fo.gestion=='trail').sum()})")
    else:
        print("  FVG solo:        sin trades")
    print(f"  A+B + FVG:       avgR {sc['oosA']:+.3f}  net$ {sc['oosN']*R:+.0f}  WR {sc['wr']:.0f}%  n {sc['n']}   (Δ avgR {sc['oosA']-sb['oosA']:+.3f})")
