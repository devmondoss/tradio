"""
_lab_heatmap.py — re-test EXHAUSTIVO del heatmap/libro como filtro (varias opciones).
====================================================================================
El primer test fue una sola config cruda. Acá: OBI a favor/en contra a varios umbrales,
muro a favor, muro adelante (target), vacío hacia target, combos. Todos como FILTRO
sobre A+B base, regla dura (mejorar avgR OOS en los 3 activos).
"""
import sys; import numpy as np
sys.path.insert(0, "backtest")
import featurelab as FL

def obi_favor(thr):   # libro ACOMPAÑA: long con presión compradora / short con vendedora
    def f(a,i,side,entry):
        o=getattr(a,'obi5_mean')[i]
        return (o>thr) if side=='long' else (o<-thr)
    return f
def obi_against(thr): # CONTRARIAN/absorción: compramos donde hay presión vendedora (y viceversa)
    def f(a,i,side,entry):
        o=getattr(a,'obi5_mean')[i]
        return (o<-thr) if side=='long' else (o>thr)
    return f
def wall_favor():     # muro de soporte/resistencia en NUESTRO lado
    def f(a,i,side,entry):
        return bool(getattr(a,'bid_wall')[i]) if side=='long' else bool(getattr(a,'ask_wall')[i])
    return f
def wall_ahead():     # muro ADELANTE (target): resistencia arriba para long / soporte abajo para short
    def f(a,i,side,entry):
        return bool(getattr(a,'ask_wall')[i]) if side=='long' else bool(getattr(a,'bid_wall')[i])
    return f
def thin_room():      # vacío hacia el target (espacio para correr)
    def f(a,i,side,entry):
        return bool(getattr(a,'thin_above')[i]) if side=='long' else bool(getattr(a,'thin_below')[i])
    return f
def obi_favor_and_wall(thr):  # combo: libro a favor Y muro de soporte
    g1=obi_favor(thr); g2=wall_favor()
    def f(a,i,side,entry): return g1(a,i,side,entry) and g2(a,i,side,entry)
    return f

print("########## HEATMAP/LIBRO — re-test con varias opciones (regla dura) ##########")
for thr in (0.1,0.2,0.3): FL.filter_verdict(f"OBI a favor >{thr}", obi_favor(thr))
for thr in (0.1,0.2):     FL.filter_verdict(f"OBI en contra >{thr} (absorción)", obi_against(thr))
FL.filter_verdict("muro a favor (soporte)", wall_favor())
FL.filter_verdict("muro adelante (target)", wall_ahead())
FL.filter_verdict("vacío hacia target", thin_room())
FL.filter_verdict("OBI>0.2 + muro soporte", obi_favor_and_wall(0.2))
