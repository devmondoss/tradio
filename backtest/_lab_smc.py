"""
_lab_smc.py — features SMC/volumen/microestructura NO usados, por el lab (regla dura).
=====================================================================================
Features ya calculados que la estrategia liquidity NO toca:
  • premium/discount (proxy vp_poc) → ataca el BIAS: long sólo en descuento, short en premium
  • fib_ote      → ¿entrada en zona OTE mejora?
  • muros de volumen (bid/ask_wall) → microestructura: ¿muro a favor del nivel?
  • vacíos (thin_above/below)       → microestructura: ¿espacio libre hacia el target?
Todos como FILTRO sobre A+B base, regla dura: mejorar avgR OOS en los 3 activos.
"""
import sys; import numpy as np
sys.path.insert(0, "backtest")
import featurelab as FL


def premdisc():
    def f(a, i, side, entry):
        poc = a.vp_poc[i]
        if not np.isfinite(poc): return True
        return entry < poc if side == "long" else entry > poc   # long en descuento / short en premium
    return f

def fib_ote_f():
    def f(a, i, side, entry):
        return bool(getattr(a, "fib_ote")[i])
    return f

def wall_favor():
    def f(a, i, side, entry):
        return bool(getattr(a, "bid_wall")[i]) if side == "long" else bool(getattr(a, "ask_wall")[i])
    return f

def thin_room():
    def f(a, i, side, entry):
        return bool(getattr(a, "thin_above")[i]) if side == "long" else bool(getattr(a, "thin_below")[i])
    return f


print("########## FEATURES NO USADOS — por el lab (regla dura) ##########")
FL.filter_verdict("premium/discount (bias)", premdisc())
FL.filter_verdict("fib_ote (zona OTE)", fib_ote_f())
FL.filter_verdict("muro de volumen a favor", wall_favor())
FL.filter_verdict("vacío hacia el target (thin)", thin_room())
