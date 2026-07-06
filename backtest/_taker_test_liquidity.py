"""
_taker_test_liquidity.py — taker vs maker en los mismos niveles VP POC
=======================================================================
Compara 4 variantes de entrada en los mismos niveles (gen_h21 + gen_h21_short + gen_h5):

  MAKER:       límite en VP POC nivel exacto — base de referencia
  TAKER-ANY:   cierre de la barra de toque (sin confirmación extra)
  TAKER-CONF:  ídem + close en mitad correcta del rango (wick confirmado)
               long → close > (high+low)/2, short → close < (high+low)/2
  TAKER-DIST:  TAKER-CONF pero solo si además dist(close,lvl) > 0.3 ATR
               (rechazo con rebote visible, no solo rasguño del nivel)

Exit: M1 barra-a-barra, fade con parcial TP1 50% → BE → target estructural.
ATR volfilter: atr > median(500) en ambos modos.
Regla dura (OOS avgR > 0 en los 3 activos, y bate MAKER).

Observación sobre fees:
  Maker  = 0.04% round-trip (rebate entrada + taker salida)
  Taker  = 0.11% round-trip
  Delta  ≈ 0.07% por trade ≈ 0.17R en BTC con risk=0.5%×ATR
  Solo el fee cuesta 0.17R por trade al taker. Necesita compensar con mejor entry.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import numpy as np, pandas as pd
import featurelab as FL
import _listas2 as L2
from _audit_mirror import gen_h21_short

COOLDOWN = 3
MAX_DAY  = 4
TF_MIN   = 15
TIMEOUT  = 24 * 60   # minutos

# ─────────────────────────────────────────────────────────────────────────────
# Generators y filtros
# ─────────────────────────────────────────────────────────────────────────────

def combined_gen():
    """Los 3 generadores validados en un solo gen (run_level_m1exit acepta uno)."""
    g21  = L2.gen_h21()
    g21s = gen_h21_short()
    g5   = L2.gen_h5()
    def g(a, i):
        out = []
        for fn in (g21, g21s, g5):
            r = fn(a, i)
            if r: out.extend(r)
        return out
    return g

def atrvol_filt(atr_med):
    """ATR > mediana(500) causal — igual a volfilter de run_system."""
    def f(a, i, side):
        return np.isfinite(atr_med[i]) and a.atr[i] > atr_med[i]
    return f

def taker_confirm_filt():
    """Close en la mitad correcta del rango de la vela = wick de rechazo confirmado."""
    def f(a, i, side):
        mid = (a.h[i] + a.l[i]) / 2.0
        return a.c[i] > mid if side == "long" else a.c[i] < mid
    return f

def taker_dist_filt(min_atr=0.3):
    """Close a más de min_atr×ATR del nivel (rechazo visible, no solo rasguño)."""
    def f(a, i, side):
        # El nivel es vp_poc (proxy del lvl de la señal)
        poc = a.vp_poc[i]
        if not np.isfinite(poc): return True
        dist = abs(float(a.c[i]) - poc)
        return dist >= min_atr * float(a.atr[i])
    return f

def combo_filt(*filts):
    def f(a, i, side):
        return all(fn(a, i, side) for fn in filts)
    return f

# ─────────────────────────────────────────────────────────────────────────────
# Runner por activo
# ─────────────────────────────────────────────────────────────────────────────

def run_for_sym(sym):
    a, m1 = FL.load(sym)
    atr_med = pd.Series(a.atr).rolling(500, min_periods=50).median().shift(1).values
    avol    = atrvol_filt(atr_med)
    tconf   = taker_confirm_filt()
    tdist   = taker_dist_filt(0.3)

    gen = combined_gen()

    variants = [
        ("MAKER",       "maker", avol),
        ("TAKER-ANY",   "taker", avol),
        ("TAKER-CONF",  "taker", combo_filt(avol, tconf)),
        ("TAKER-DIST",  "taker", combo_filt(avol, tconf, tdist)),
    ]

    results = {}
    for label, mode, filt in variants:
        trades = L2.run_level_m1exit(
            a, gen, m1, TIMEOUT, mode, TF_MIN,
            cooldown=COOLDOWN, max_day=MAX_DAY, filt=filt
        )
        df = pd.DataFrame(trades) if trades else pd.DataFrame()
        if df.empty or "oos" not in df.columns:
            results[label] = dict(n_is=0, avgr_is=float("nan"), n_oos=0,
                                  avgr_oos=float("nan"), wr_oos=float("nan"))
            continue
        is_  = df[~df.oos]
        oos  = df[df.oos]
        results[label] = dict(
            n_is=len(is_),
            avgr_is=is_.r.mean() if len(is_) else float("nan"),
            n_oos=len(oos),
            avgr_oos=oos.r.mean() if len(oos) else float("nan"),
            wr_oos=100*(oos.r > 0).mean() if len(oos) else float("nan"),
        )
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ALL = {}
    for sym in FL.ASSETS:
        if not Path(FL.ASSETS[sym]).exists(): continue
        print(f"\n{'─'*60}")
        print(f"  {sym}")
        print(f"{'─'*60}")
        ALL[sym] = run_for_sym(sym)
        hdr = f"  {'variante':<14} {'IS n':>5} {'IS avgR':>8}   {'OOS n':>5} {'OOS avgR':>9} {'OOS WR':>7}"
        print(hdr)
        for label, r in ALL[sym].items():
            marker = " ←BASE" if label == "MAKER" else ""
            print(f"  {label:<14} {r['n_is']:>5} {r['avgr_is']:>+8.3f}   "
                  f"{r['n_oos']:>5} {r['avgr_oos']:>+9.3f} {r['wr_oos']:>6.1f}%{marker}")

    print(f"\n{'='*60}")
    print("VEREDICTO — ¿taker bate maker en los 3 activos?")
    print(f"{'='*60}")
    syms = list(ALL.keys())
    for label in ["TAKER-ANY", "TAKER-CONF", "TAKER-DIST"]:
        oos_vals    = [ALL[s][label]["avgr_oos"] for s in syms]
        maker_vals  = [ALL[s]["MAKER"]["avgr_oos"] for s in syms]
        oos_pos     = all(v > 0       for v in oos_vals if np.isfinite(v))
        beats_maker = all(ALL[s][label]["avgr_oos"] > ALL[s]["MAKER"]["avgr_oos"]
                          for s in syms if np.isfinite(ALL[s][label]["avgr_oos"]))
        print(f"  {label:<14}  OOS>0 en los 3: {'✅' if oos_pos else '❌'}   "
              f"Bate MAKER: {'✅' if beats_maker else '❌'}")
        for s in syms:
            m = ALL[s]["MAKER"]["avgr_oos"]
            t = ALL[s][label]["avgr_oos"]
            delta = t - m
            print(f"    {s}: MAKER={m:+.3f}  {label}={t:+.3f}  Δ={delta:+.3f}")
