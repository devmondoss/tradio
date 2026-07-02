"""
_rules_sweep.py — ¿Qué reglas con NUESTROS features se adaptan a crypto perp?
=============================================================================
Sweep de filtros bar-level sobre v3 prod (fade + H1 + dist>0.5ATR + IFVG, ss=0.8).
Regla dura: un filtro solo vale si mejora avgR IS **y** OOS en LOS 3 símbolos vs v3,
manteniendo n razonable (>=40% de los trades OOS).

EXCLUIDOS (cementerio, no repetir): obi*, vpin, cvd_slope, session/killzones,
sweep/FVG/frescura/confluencia (equal_h/l), ICT (choch/bos/ote), funding/OI, POC solo.

CANDIDATOS (nunca testeados en liquidity M15):
  vr_hi        VR >= 1.5 en la barra de señal (convicción SC3, ¿transfiere a M15?)
  vr_lo        VR < 1.0 (llegada tranquila al nivel)
  absorb       delta de la barra CONTRA el lado del trade (absorción: compran contra
               nuestra resistencia short / venden contra nuestro soporte long)
  delta_con    delta A FAVOR del lado (continuación hacia el nivel)
  mom4         momentum 4 barras hacia el nivel (>= 1 ATR) — del fill model: P(fill) 72%
  mom4_soft    momentum 4 barras hacia el nivel (>= 0.5 ATR)
  atr_up       vol expandiendo: ATR > ATR hace 16 barras (4h)
  atr_dn       vol decayendo (la semana mala del paper fue esto)
  weekday      solo lun-vie (calendario crypto-nativo, no sesión intradía)
  weekend      solo sáb-dom
  big_trade    big_trade_bullish/bearish en las últimas 2 barras (ballena en el nivel)
  no_lvn_path  sin LVN entre entry y el lado del target (proxy: vp_lvn_below no
               interpuesto para longs)

Uso: python backtest/_rules_sweep.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import numpy as np, pandas as pd
from _v3_parity import load, make_filter, wrap, SYMS
from _strategy_ab import run_system
from _audit_mirror import gen_h21_short
from _ict_ifvg import gen_ifvg
import _listas2 as L2


# ── filtros candidatos: f(a, i, side, entry) -> bool ────────────────────────
def _mom_atr(a, i, side, nbars):
    """retorno de las últimas nbars en ATRs, con signo 'hacia el nivel'.
    long: nivel debajo → aproximación = precio cayendo (retorno negativo)."""
    if i < nbars or a.atr[i] <= 0: return 0.0
    ret = a.c[i] - a.c[i - nbars]
    return (-ret if side == "long" else ret) / a.atr[i]


CANDS = {
    "vr_hi":      lambda a, i, s, e: np.nan_to_num(a.vr[i]) >= 1.5,
    "vr_lo":      lambda a, i, s, e: np.nan_to_num(a.vr[i]) < 1.0,
    "absorb":     lambda a, i, s, e: (a.delta[i] > 0) if s == "short" else (a.delta[i] < 0),
    "delta_con":  lambda a, i, s, e: (a.delta[i] < 0) if s == "short" else (a.delta[i] > 0),
    "mom4":       lambda a, i, s, e: _mom_atr(a, i, s, 4) >= 1.0,
    "mom4_soft":  lambda a, i, s, e: _mom_atr(a, i, s, 4) >= 0.5,
    "atr_up":     lambda a, i, s, e: i >= 16 and a.atr[i] > a.atr[i - 16],
    "atr_dn":     lambda a, i, s, e: i >= 16 and a.atr[i] <= a.atr[i - 16],
    "weekday":    lambda a, i, s, e: pd.Timestamp(int(a.ts[i]), unit="ms").dayofweek < 5,
    "weekend":    lambda a, i, s, e: pd.Timestamp(int(a.ts[i]), unit="ms").dayofweek >= 5,
    "big_trade":  lambda a, i, s, e: bool(a.big_trade_bullish[i] or a.big_trade_bearish[i]
                                          or (i > 0 and (a.big_trade_bullish[i-1] or a.big_trade_bearish[i-1]))),
    "no_lvn_path": lambda a, i, s, e: not (np.isfinite(a.vp_lvn_below[i])
                                           and (min(e, a.c[i]) < a.vp_lvn_below[i] < max(e, a.c[i]))),
}


def run_v3(sym, extra=None):
    a, m1 = load(sym)
    gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short(), gen_ifvg()]
    base_f = make_filter(True, True)  # H1 + dist (v3)
    if extra is None:
        f = base_f
    else:
        def f(a_, i_, s_, e_):
            return base_f(a_, i_, s_, e_) and extra(a_, i_, s_, e_)
    return run_system(a, wrap(gens, f), m1, 15, mode="fade", stop_scale=0.8,
                      max_day=4, cooldown=3, min_range=0.0)


def metrics(df):
    if df is None or len(df) == 0:
        return dict(isA=np.nan, oosA=np.nan, oosN=0.0, n_oos=0, n=0)
    o = df[df.oos]; i = df[~df.oos]
    return dict(isA=i.r.mean() if len(i) else np.nan, oosA=o.r.mean() if len(o) else np.nan,
                oosN=o.r.sum(), n_oos=len(o), n=len(df))


if __name__ == "__main__":
    base = {}
    print("== baseline v3 ==")
    for sym in SYMS:
        base[sym] = metrics(run_v3(sym))
        b = base[sym]
        print(f"  {sym}: IS {b['isA']:+.3f}  OOS {b['oosA']:+.3f}  (netR {b['oosN']:+.0f}, n_oos={b['n_oos']})")

    print(f"\n{'filtro':>12} | " + " | ".join(f"{s[:3]} IS/OOS (nOOS)" for s in SYMS) + " | veredicto")
    for name, f in CANDS.items():
        res = {s: metrics(run_v3(s, f)) for s in SYMS}
        hard = all(np.isfinite(res[s]["isA"]) and np.isfinite(res[s]["oosA"])
                   and res[s]["isA"] > base[s]["isA"] and res[s]["oosA"] > base[s]["oosA"]
                   and res[s]["n_oos"] >= 0.4 * base[s]["n_oos"] for s in SYMS)
        pos = all(np.isfinite(res[s]["oosA"]) and res[s]["oosA"] > 0 and res[s]["isA"] > 0 for s in SYMS)
        cells = " | ".join(f"{res[s]['isA']:+.2f}/{res[s]['oosA']:+.2f} ({res[s]['n_oos']})" for s in SYMS)
        tag = "PASA REGLA DURA" if hard else ("positivo" if pos else "muerto")
        print(f"{name:>12} | {cells} | {tag}")
